# Support for MMS Eject
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager
from dataclasses import dataclass, fields

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.config import OptionalField, PrinterConfig
from ..core.exceptions import EjectFailedError
from ..core.logger import log_time_cost
from ..core.task import AsyncTask


@dataclass(frozen=True)
class PrinterEjectConfig(PrinterConfig):
    # Z-axis lift distance during eject operations
    # Unit: mm
    z_raise: float = 1.0

    # Filament Retraction Settings
    # Distance the extruder retracts during eject operation
    # This is also the first phase of filament swap operations
    # (e.g., 'T*' command)
    # Total retraction = retract_distance × retract_times
    # Unit: mm
    retract_distance: float = 10.0
    # Number of retraction cycles performed
    retract_times: int = 100
    # Extruder retraction speed
    # Unit: mm/min
    retract_speed: float = 1200.0

    # Filament Unload Settings
    # Drive stepper speed for filament unloading
    # This pushes filament out of the toolhead
    # Unit: mm/s
    drive_speed: float = 20.0
    # Drive stepper acceleration for filament unloading
    # Unit: mm/s²
    drive_accel: float = 20.0
    # Total filament unload distance
    # Unit: mm
    distance_unload: float = 120.0

    # Custom Macro
    custom_before: OptionalField = "MMS_EJECT_CUSTOM_BEFORE"
    custom_after: OptionalField = "MMS_EJECT_CUSTOM_AFTER"


class MMSEject:
    def __init__(self, config):
        p_eject_config = PrinterEjectConfig(config)
        for field in fields(p_eject_config):
            key = field.name
            # Don't cover
            if not p_eject_config.should_skip(key) \
                and not hasattr(self, key):
                val = getattr(p_eject_config, key)
                setattr(self, key, val)

        self.reactor = printer_adapter.get_reactor()

        # State tracking
        self._is_running = False
        # Task state
        self._task_end = False
        self._task_success = False
        # Extruder state
        self._extruder_retract_end = False

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    # ---- Initial ----
    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()
        self.mms_cut = printer_adapter.get_mms_cut()
        self.mms_purge = printer_adapter.get_mms_purge()

    def _initialize_gcode(self):
        commands = [
            ("MMS_EJECT", self.cmd_MMS_EJECT),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        # Log would not output to console
        self.log_info_s = mms_logger.create_log_info(console_output=False)
        self.log_warning_s = mms_logger.create_log_warning(console_output=False)

    # ---- Status ----
    def is_running(self):
        return self._is_running

    @contextmanager
    def _eject_is_running(self):
        self._is_running = True
        try:
            yield
        finally:
            self._is_running = False

    def _entry_is_released(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        return mms_slot.entry_is_set() and mms_slot.entry.is_released()

    def _filament_still_in_toolhead(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        return mms_slot.entry_is_triggered() or mms_slot.outlet.is_triggered()

    # ---- Control ----
    def pause(self, period_seconds):
        self.reactor.pause(self.reactor.monotonic() + period_seconds)

    # ---- Async task ----
    def _init_task_state(self):
        self._task_end = False
        self._task_success = False

    def _async_unload(self, slot_num):
        # Setup and start drive unload in background
        func = self._unload_task
        params = {"slot_num" : slot_num}
        callback = self._handle_unload_task_end
        async_task = AsyncTask()
        try:
            is_ready = async_task.setup(func, params, callback)
            if is_ready:
                async_task.start()
        except Exception as e:
            self.log_error(f"slot[{slot_num}] async unload error: {e}")
            return

        # Wait selector if re-select
        self.mms_delivery.wait_mms_selector(slot_num)
        # Start extruder retract
        self._extruder_retract(slot_num)

    def _unload_task(self, slot_num):
        # Slow unload by drive
        success = self.mms_delivery.mms_move(
            slot_num = slot_num,
            distance = -abs(self.distance_unload),
            speed = self.drive_speed,
            accel = self.drive_accel
        )
        if success:
            # Raise stop signal for Extruder retract
            self._stop_extruder_retract(slot_num)
            return True
        return False

    def _handle_unload_task_end(self, result):
        self._task_end = True
        # result is None means _swap_task() raise an error(maybe retry failed)
        self._task_success = True if result not in (None, False) else False

    def _extruder_retract(self, slot_num):
        self._extruder_retract_end = False

        for i in range(self.retract_times):
            if self._extruder_retract_end:
                self.log_info_s(
                    f"slot[{slot_num}] extruder retract finish at round:{i}")
                # Reset
                self._extruder_retract_end = False
                return

            self.log_info_s(
                f"slot[{slot_num}] extruder retract count: {i}")
            extruder_adapter.retract(
                self.retract_distance, self.retract_speed
            )

        self.log_warning_s(
            f"slot[{slot_num}] extruder retract end without signal...")

    def _stop_extruder_retract(self, slot_num):
        self.log_info_s(
            f"slot[{slot_num}] receive stop extruder retract signal")
        self._extruder_retract_end = True
        # Wait a while to let extruder finish retract
        for _ in range(10):
            self.pause(0.2)
            if not self._extruder_retract_end:
                # Reset finish
                return

    def wait_unload(self, slot_num):
        """Perform unloading operation for slot."""
        self._init_task_state()
        # Async unload filament and Extruder retract
        self._async_unload(slot_num)

        # Wait until async swap is finished
        # No matter success or not, callback will run and flag will set
        # So no need to handle timeout
        self.log_info_s(f"slot[{slot_num}] waiting...")
        while not self._task_end:
            self.pause(0.2)
        return self._task_success

    # ---- MMS Buffer control ----
    def _pause_mms_buffer(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_buffer = mms_slot.get_mms_buffer()
        # Deactivate buffer monitor before
        mms_buffer.deactivate_monitor()

        # Make sure all mms steppers are idle
        # Timeout with default config in mms_delivery
        is_idle = self.mms_delivery.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            raise EjectFailedError(
                f"slot[{slot_num}] wait selector or drive stepper idle timeout",
                mms_slot
            )
        return mms_buffer

    # ---- Eject ----
    def _prepare_only(self, slot_num):
        log_prefix = f"slot[{slot_num}] eject with entry is released"
        self.log_info_s(f"{log_prefix} begin")

        result = self.mms_delivery.mms_prepare(slot_num)
        if result:
            self.log_info_s(f"{log_prefix} finish")
        else:
            self.log_warning(f"{log_prefix} failed")

        return result

    def find_eject_slots(self):
        """
        Sort loading slots based on priority and selection rules.
        Rules:
        1. All loading_slots elements are preserved in the result
        2. Only elements present in both lists are considered from
           selecting_slots
        3. Result order: priority elements (in selecting_slots order) followed
           by remaining elements (in loading_slots original order)
        """
        # Loading slots:
        #   pin:gate is triggered
        loading_slots = self.mms.get_loading_slots()
        # Selecting slots:
        #   pin:selector is triggered
        #   or stepper:selector is focusing
        selecting_slots = self.mms.get_selecting_slots()

        # Sort
        loading_slots.sort()
        selecting_slots.sort()

        priority = [s for s in selecting_slots if s in loading_slots]
        # If no priority slots, loading_slots will be return
        remaining = [s for s in loading_slots if s not in priority]

        # Sorted slot list following the priority rules
        return priority+remaining

    def _standard_eject(self, check_entry):
        eject_slots = self.find_eject_slots()
        if not eject_slots:
            self.log_info_s("standard eject skip, no loading slots")
            self.mms.log_status()
            return True

        self.log_info_s(
            "standard eject begin, "
            f"loading slots: {eject_slots}"
        )

        with self._eject_is_running():
            for slot_num in eject_slots:
                # Make sure all mms_buffer are idle
                self._pause_mms_buffer(slot_num)

            if check_entry and self._entry_is_released(eject_slots[0]):
                # Only mms_prepare need, apply and return
                for slot_num in eject_slots:
                    self.log_info_s(f"slot[{slot_num}] eject with prepare only")
                    self._prepare_only(slot_num)

            # Check again and continue if any slots still loading
            eject_slots = self.find_eject_slots()
            if not eject_slots:
                self.log_info_s("standard eject finish")
                return True
            self.log_info_s(
                "standard eject continue, "
                f"loading slots: {eject_slots}"
            )

            # Check toolhead, should be homed
            if not toolhead_adapter.is_homed():
                raise EjectFailedError(
                    "toolhead is not homed",
                    self.mms.get_mms_slot(eject_slots[0])
                )

            # Heat extruder
            extruder_adapter.heat_to_min_temp()

            # Purge clean steps would be skip
            # if multiple-eject_slots exists
            if len(eject_slots) == 1:
                # Apply purge clean steps
                slot_num = eject_slots[0]
                # Extruder retract a little bit to decrease nozzle remain
                self.mms_purge.apply_retraction_compensation(slot_num)
                # Apply pressure pulse cleaning
                self.mms_purge.pressure_pulse_cleaning(slot_num)

            if self.mms_cut.is_enabled():
                # Park to cutter init point
                self.mms_cut.cut_init()
                # Do Cut
                if not self.mms_cut.mms_cut():
                    raise EjectFailedError(
                        f"slot[{eject_slots[0]}] eject cut failed",
                        self.mms.get_mms_slot(eject_slots[0])
                    )

            if self.mms_purge.is_enabled():
                # Park to tray point
                self.mms_purge.move_to_tray()

            # Async unload slowly
            for slot_num in eject_slots:
                if not self.wait_unload(slot_num):
                    raise EjectFailedError(
                        f"slot[{slot_num}] eject async unload failed",
                        self.mms.get_mms_slot(slot_num)
                    )

            # Check outlet/Entry again
            # If any of them is still triggered, Cut&Unload/ColdPull failed
            for slot_num in eject_slots:
                if self._filament_still_in_toolhead(slot_num):
                    mms_slot = self.mms.get_mms_slot(slot_num)
                    pin = "entry" if mms_slot.entry_is_triggered() else "outlet"
                    msg = f"slot[{slot_num}] eject exit toolhead failed" \
                          f", '{pin}' is still triggering"
                    raise EjectFailedError(msg, mms_slot)

            # Finally unload to gate
            for slot_num in eject_slots:
                if not self.mms_delivery.mms_unload(slot_num):
                    raise EjectFailedError(
                        f"slot[{slot_num}] eject unload to gate release failed",
                        self.mms.get_mms_slot(slot_num)
                    )

        self.log_info_s("standard eject finish")

    def _exec_custom_macro(self, macro, position):
        if macro:
            self.log_info(
                f"MMS execute macro {position} EJECT:\n"
                f"{macro}"
            )
            gcode_adapter.run_command(macro)

    def mms_eject(self, check_entry=True):
        self._exec_custom_macro(self.custom_before, "before")

        if self.is_running():
            self.log_warning("another eject is running, return")
            return False

        try:
            self._standard_eject(check_entry)
        except EjectFailedError as e:
            self.log_warning(e)
            return False
        except Exception as e:
            self.log_error(f"eject error: {e}")
            return False

        self._exec_custom_macro(self.custom_after, "after")
        return True

    # ---- GCode ----
    @log_time_cost("log_info_s")
    def cmd_MMS_EJECT(self, gcmd):
        with toolhead_adapter.snapshot():
            with toolhead_adapter.safe_z_raise(self.z_raise):
                self.mms_eject()


def load_config(config):
    return MMSEject(config)
