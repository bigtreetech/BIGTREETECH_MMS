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
        self.log_info_silent = mms_logger.create_log_info()
        self.log_warning_silent = mms_logger.create_log_warning()

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
        mms_slot = self.mms.get_slot(slot_num)
        return mms_slot.entry_is_set() and mms_slot.entry.is_released()

    def _entry_is_triggered(self, slot_num):
        mms_slot = self.mms.get_slot(slot_num)
        return mms_slot.entry_is_set() and mms_slot.entry.is_triggered()

    def _gate_is_released(self, slot_num):
        return self.mms.get_slot(slot_num).gate.is_released()

    def _outlet_is_triggered(self, slot_num):
        return self.mms.get_slot(slot_num).outlet.is_triggered()

    def _filament_still_in_toolhead(self, slot_num):
        return self._entry_is_triggered(slot_num) \
            or self._outlet_is_triggered(slot_num)

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

        # if not success:
        #     return False
        # # Unload by drive is success, raise stop signal of retract
        # self._stop_extruder_retract(slot_num)
        # # Final unload to gate
        # return self.mms_delivery.mms_unload(slot_num)

    def _handle_unload_task_end(self, result):
        self._task_end = True
        # result is None means _swap_task() raise an error(maybe retry failed)
        self._task_success = True if result not in (None, False) else False

    def _extruder_retract(self, slot_num):
        self._extruder_retract_end = False

        for i in range(self.retract_times):
            if self._extruder_retract_end:
                self.log_info_silent(
                    f"slot[{slot_num}] extruder retract finish at round:{i}")
                # Reset
                self._extruder_retract_end = False
                return

            self.log_info_silent(
                f"slot[{slot_num}] extruder retract count: {i}")
            extruder_adapter.retract(
                self.retract_distance, self.retract_speed
            )

        self.log_warning_silent(
            f"slot[{slot_num}] extruder retract end without signal...")

    def _stop_extruder_retract(self, slot_num):
        self.log_info_silent(f"slot[{slot_num}] receive stop"
                             f" extruder retract signal")
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
        self.log_info(f"slot[{slot_num}] waiting...")
        while not self._task_end:
            self.pause(0.2)
        return self._task_success

    # ---- MMS Buffer control ----
    def _pause_mms_buffer(self, slot_num):
        mms_slot = self.mms.get_slot(slot_num)
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
    def _standard_eject_old(self, slot_num):
        mms_slot = self.mms.get_slot(slot_num)

        # Extra check toolhead
        if not toolhead_adapter.is_homed():
            raise EjectFailedError(
                "toolhead is not homed", mms_slot
            )

        log_prefix = f"slot[{slot_num}] standard eject"
        self.log_info(f"{log_prefix} begin")

        # Phase I: Heat extruder
        extruder_adapter.heat_to_min_temp()

        # Phase II: Extruder retract a little bit to decrease nozzle remain
        self.mms_purge.apply_retraction_compensation(slot_num)

        # Phase III: Apply pressure pulse cleaning
        if self.mms_cut.is_enable():
            # Park to cutter init point
            self.mms_cut.cut_init()
        self.mms_purge.pressure_pulse_cleaning(slot_num)

        # Phase IV: Cut&Unload or ColdPull
        if self.mms_cut.is_enable():
            # First cut
            if not self.mms_cut.mms_cut():
                raise EjectFailedError(
                    f"{log_prefix} cut failed", mms_slot
                )
        # else:
        #     # cold_pull() would print log itself
        #     self.mms_purge.cold_pull(slot_num)

        # Async unload
        if not self.wait_unload(slot_num):
            raise EjectFailedError(
                f"{log_prefix} async unload failed", mms_slot
            )

        # Check outlet/Entry again
        # If any of them is still triggered, Cut&Unload/ColdPull failed
        if self._outlet_is_triggered(slot_num) \
            or self._entry_is_triggered(slot_num):
            raise EjectFailedError(
                f"{log_prefix} exit toolhead failed", mms_slot
            )

        # Phase V: Finally unload to gate
        if not self.mms_delivery.mms_unload(slot_num):
            raise EjectFailedError(
                f"{log_prefix} unload to gate release failed", mms_slot
            )

        self.log_info(f"{log_prefix} finish")

    def mms_eject_old(self, check_entry=True, skip_slot_num=None):
        if self.custom_before:
            self.log_info(
                f"MMS execute macro before EJECT: {self.custom_before}")
            gcode_adapter.run_command(self.custom_before)

        if self.is_running():
            self.log_warning("another eject is running, return")
            return False

        loading_slots = self.mms.get_loading_slots()
        self.log_info(f"eject begin with loading slots: {loading_slots}")

        with self._eject_is_running():
            for slot_num in loading_slots:
                if skip_slot_num is not None and slot_num == skip_slot_num:
                    continue

                log_prefix = f"slot[{slot_num}] eject"
                self.log_info(f"{log_prefix} begin")

                try:
                    # Make sure mms_buffer is idle
                    self._pause_mms_buffer(slot_num)

                    if check_entry and self._entry_is_released(slot_num):
                        # Only prepare need, apply and return
                        if self._prepare_only(slot_num):
                            # Prepare success
                            self.log_info(
                                f"{log_prefix} finish, mms_prepare only")
                            continue
                        else:
                            # Continue with standard eject if failed
                            self.log_warning(
                                f"{log_prefix} continue with standard method")

                    # Check gate state again
                    if self._gate_is_released(slot_num):
                        self.log_warning(
                            f"{log_prefix} skip as gate is released")
                        self.mms.log_status()
                        continue

                    # Apply standard eject
                    self._standard_eject(slot_num)

                except EjectFailedError as e:
                    self.log_warning(e)
                    return False
                except Exception as e:
                    self.log_error(f"{log_prefix} error: {e}")
                    return False

                self.log_info(f"{log_prefix} finish")

        self.log_info(f"eject finish with loading slots: {loading_slots}")

        if self.custom_after:
            self.log_info(
                f"MMS execute macro after EJECT: {self.custom_after}")
            gcode_adapter.run_command(self.custom_after)

        return True

    def _prepare_only(self, slot_num):
        log_prefix = f"slot[{slot_num}] eject with entry is released"
        self.log_info(f"{log_prefix} begin")

        result = self.mms_delivery.mms_prepare(slot_num)
        if result:
            self.log_info(f"{log_prefix} finish")
        else:
            self.log_warning(f"{log_prefix} failed")

        return result

    def _standard_eject(self, check_entry):
        loading_slots = self.mms.get_loading_slots()
        if not loading_slots:
            self.log_info("standard eject skip, no loading slots")
            self.mms.log_status()
            return True

        self.log_info(
            "standard eject begin, "
            f"loading slots: {loading_slots}"
        )

        with self._eject_is_running():
            for slot_num in loading_slots:
                # Make sure all mms_buffer are idle
                self._pause_mms_buffer(slot_num)

            if check_entry and self._entry_is_released(loading_slots[0]):
                # Only mms_prepare need, apply and return
                for slot_num in loading_slots:
                    self.log_info(f"slot[{slot_num}] eject with prepare only")
                    self._prepare_only(slot_num)

            # Check again and continue if any slots still loading
            loading_slots = self.mms.get_loading_slots()
            if not loading_slots:
                self.log_info("standard eject finish")
                return True
            self.log_info(
                "standard eject continue, "
                f"loading slots: {loading_slots}"
            )

            # Check toolhead, should be homed
            if not toolhead_adapter.is_homed():
                raise EjectFailedError(
                    "toolhead is not homed",
                    self.mms.get_slot(loading_slots[0])
                )

            if self.mms_cut.is_enable():
                # Park to cutter init point
                self.mms_cut.cut_init()

            # Heat extruder
            extruder_adapter.heat_to_min_temp()

            # Apply purge clean steps
            for slot_num in loading_slots:
                # Extruder retract a little bit to decrease nozzle remain
                self.mms_purge.apply_retraction_compensation(slot_num)
                # Apply pressure pulse cleaning
                self.mms_purge.pressure_pulse_cleaning(slot_num)

            if self.mms_cut.is_enable():
                # Cut
                if not self.mms_cut.mms_cut():
                    raise EjectFailedError(
                        f"slot[{loading_slots[0]}] eject cut failed",
                        self.mms.get_slot(loading_slots[0])
                    )

            # Async unload slowly
            for slot_num in loading_slots:
                if not self.wait_unload(slot_num):
                    raise EjectFailedError(
                        f"slot[{slot_num}] eject async unload failed",
                        self.mms.get_slot(slot_num)
                    )

            # Check outlet/Entry again
            # If any of them is still triggered, Cut&Unload/ColdPull failed
            for slot_num in loading_slots:
                if self._filament_still_in_toolhead(slot_num):
                    raise EjectFailedError(
                        f"slot[{slot_num}] eject exit toolhead failed",
                        self.mms.get_slot(slot_num)
                    )

            # Finally unload to gate
            for slot_num in loading_slots:
                if not self.mms_delivery.mms_unload(slot_num):
                    raise EjectFailedError(
                        f"slot[{slot_num}] eject unload to gate release failed",
                        self.mms.get_slot(slot_num)
                    )

        self.log_info("standard eject finish")

    def mms_eject(self, check_entry=True):
        if self.custom_before:
            self.log_info(
                "MMS execute macro before EJECT:\n"
                f"{self.custom_before}"
            )
            gcode_adapter.run_command(self.custom_before)

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

        if self.custom_after:
            self.log_info(
                "MMS execute macro after EJECT:\n"
                f"{self.custom_after}"
            )
            gcode_adapter.run_command(self.custom_after)

        return True

    # ---- GCode ----
    @log_time_cost("log_info_silent")
    def cmd_MMS_EJECT(self, gcmd):
        if not self.mms.cmd_can_exec():
            self.log_warning("MMS_EJECT can not execute now")
            return False

        with toolhead_adapter.snapshot():
            with toolhead_adapter.safe_z_raise(self.z_raise):
                self.mms_eject()


def load_config(config):
    return MMSEject(config)
