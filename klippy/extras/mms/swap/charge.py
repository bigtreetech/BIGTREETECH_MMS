# Support for MMS Charge
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
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
from ..core.exceptions import ChargeFailedError
from ..core.logger import log_time_cost
from ..core.task import AsyncTask


@dataclass(frozen=True)
class PrinterChargeConfig(PrinterConfig):
    # Z-axis lift distance during filament charging operations
    # Unit: mm
    z_raise: float = 1.0

    # Filament Extrusion Settings
    # Distance the extruder extrude filament during charging operation.
    # This is also the second phase of
    # standard filament swap operations (e.g., 'T*' command).
    # Total extrusion = extrude_distance × extrude_times
    # Unit: mm
    extrude_distance: float = 2.0
    # Number of extrusion cycles performed
    extrude_times: int = 5
    # Extruder extrusion speed
    # Unit: mm/min
    extrude_speed: float = 300.0

    # Extruder extrude distance per drip
    # Unit: mm
    drip_extrude_distance: float = 1.0
    # Extra distance of extruder drip extrude
    # Unit: mm
    drip_extra_distance: float = 10.0

    # Filament Unload Settings (for failed charge attempts)
    # If filament is not properly loaded, unload before retry
    # Unit: mm
    distance_unload: float = 120.0

    # Custom Macro
    custom_before: OptionalField = "MMS_CHARGE_CUSTOM_BEFORE"
    custom_after: OptionalField = "MMS_CHARGE_CUSTOM_AFTER"


class MMSCharge:
    def __init__(self, config):
        p_charge_config = PrinterChargeConfig(config)
        for field in fields(p_charge_config):
            key = field.name
            # Don't cover
            if not p_charge_config.should_skip(key) \
                and not hasattr(self, key):
                val = getattr(p_charge_config, key)
                setattr(self, key, val)

        self.reactor = printer_adapter.get_reactor()

        # State tracking
        self._is_running = False
        self._charging_slot_num = None
        # Task state
        # self._task_end = False
        # self._task_success = False
        # Extruder state
        self._drip_extrude_end = False

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
        self.mms_filament_fracture = self.mms.get_mms_filament_fracture()

    def _initialize_gcode(self):
        commands = [
            ("MMS_CHARGE", self.cmd_MMS_CHARGE),
            ("MMS_CAREFUL_CHARGE", self.cmd_MMS_CAREFUL_CHARGE),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        # Log would not output to console
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    # ---- Status ----
    def is_running(self):
        return self._is_running

    @contextmanager
    def _charge_is_running(self):
        self._is_running = True
        try:
            yield
        finally:
            self._is_running = False

    def get_charging_slot(self):
        return self._charging_slot_num

    def teardown(self):
        self._is_running = False
        self._charging_slot_num = None

    # ---- Control ----
    def pause(self, period_seconds):
        self.reactor.pause(self.reactor.monotonic() + period_seconds)

    def _exec_custom_macro(self, macro, position):
        if macro:
            self.log_info(
                f"MMS execute macro {position} CHARGE:\n"
                f"{macro}"
            )
            gcode_adapter.run_command(macro)

    def _pause_mms_buffer(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_buffer = mms_slot.get_mms_buffer()
        # Deactivate buffer monitor before
        mms_buffer.deactivate_monitor()

        # Make sure all mms steppers are idle
        # Timeout with default config in mms_delivery
        is_idle = self.mms_delivery.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            raise ChargeFailedError(
                f"slot[{slot_num}] wait selector or drive stepper idle timeout",
                mms_slot
            )
        return mms_buffer

    # ---- Async task ----
    # def _init_task_state(self):
    #     self._task_end = False
    #     self._task_success = False

    # def _handle_task_end(self, result):
    #     self._task_end = True
    #     # result is None means task raise an error(maybe retry failed)
    #     self._task_success = True if result not in (None, False) else False

    def _async_careful_load(self, slot_num, distance):
        # Setup and start careful load in background
        func = self._careful_load
        params = {"slot_num":slot_num, "distance":distance}
        # callback = self._handle_task_end
        callback = None
        async_task = AsyncTask()
        try:
            # self._init_task_state()
            if async_task.setup(func, params, callback):
                async_task.start()
        except Exception as e:
            self.log_error(f"slot[{slot_num}] async careful load error: {e}")
            return False
        return True

    def _careful_load(self, slot_num, distance):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        pin_type = mms_slot.outlet.get_pin_type()
        wait_func = mms_slot.get_wait_func(pin_type)
        endstop_pair_lst = mms_slot.format_endstop_pair(pin_type)
        # Calculate speed
        speed = self.extrude_speed / 60

        # # No retry loop
        with wait_func():
            with self.mms_filament_fracture.monitor_while_homing(slot_num):
                mms_drive.update_focus_slot(slot_num)
                mms_drive.manual_home(
                    distance = abs(distance),
                    speed = speed, accel = speed,
                    forward = True, trigger = True,
                    endstop_pair_lst = endstop_pair_lst,
                )
                # if mms_drive.move_is_completed(result):
                # if mms_slot.outlet.is_released():
                self._drip_extrude_end = True
                    # return True
        # return False

    # ---- Extruder control ----
    def _drip_extrude(
        self, speed, drip_distance, drip_times, exit_condition
    ):
        # Sum distance of extruded
        dist_extruded = 0

        for i in range(int(drip_times)):
            extruder_adapter.extrude(drip_distance, speed)
            dist_extruded += drip_distance
            # Condition achieved, exit
            if exit_condition():
                return True, dist_extruded
            # Pause before next drip
            self.pause(0.2)

        # Finally false if condition is not achieved
        return False, dist_extruded

    def _extrude_to_release_outlet(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if mms_slot.outlet.is_released():
            self.log_warning(
                f"slot[{slot_num}] outlet is already released"
            )
            return False

        self.log_info_s(
            f"slot[{slot_num}] extrude to release outlet begin"
        )
        result, dist_extruded = self._drip_extrude(
            speed = self.extrude_speed,
            drip_distance = self.extrude_distance,
            drip_times = self.extrude_times,
            exit_condition = mms_slot.outlet.is_released
        )

        msg = "released" if result else "not released"
        self.log_info_s(
            f"slot[{slot_num}] outlet is {msg}, "
            f"extrude: {dist_extruded} mm"
        )
        return result

    def _extrude_to_trigger_runout(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if mms_slot.buffer_runout.is_triggered():
            self.log_warning(
                f"slot[{slot_num}] buffer_runout is already triggered"
            )
            return False

        self.log_info_s(
            f"slot[{slot_num}] extrude to trigger buffer_runout begin"
        )
        result, dist_extruded = self._drip_extrude(
            speed = self.extrude_speed,
            drip_distance = self.extrude_distance,
            drip_times = self.extrude_times,
            exit_condition = mms_slot.outlet.is_triggered
        )

        msg = "triggered" if result else "not triggered"
        self.log_info_s(
            f"slot[{slot_num}] buffer_runout is {msg}, "
            f"extrude: {dist_extruded} mm"
        )
        return result

    def _careful_extrude(self, slot_num, distance_total):
        mms_slot = self.mms.get_mms_slot(slot_num)

        # Exit condition func
        def exit_drip():
            return self._drip_extrude_end \
                or mms_slot.outlet.is_triggered()

        # Check
        if mms_slot.outlet.is_triggered():
            self.log_warning(
                f"slot[{slot_num}] careful extrude failed, "
                "outlet is already triggered"
            )
            return False

        # Init flag
        self._drip_extrude_end = False
        result, dist_extruded = self._drip_extrude(
            speed = self.extrude_speed,
            drip_distance = self.drip_extrude_distance,
            drip_times = int(distance_total // self.drip_extrude_distance),
            exit_condition = exit_drip
        )
        # Reset flag
        self._drip_extrude_end = False

        self.log_info_s(
            f"slot[{slot_num}] exit careful extrude, "
            f"extruded {dist_extruded} mm"
        )
        return result

    # ---- Charge ----
    def _careful_charge(self, slot_num):
        log_prefix = f"slot[{slot_num}] careful charge"
        self.log_info_s(f"{log_prefix} begin")

        mms_slot = self.mms.get_mms_slot(slot_num)

        # Prepare MMS_Buffer
        mms_buffer = mms_slot.get_mms_buffer()
        if not mms_buffer.clear(slot_num):
            raise ChargeFailedError(
                f"{log_prefix} clear buffer failed", mms_slot
            )

        # Calculate the total distance should be careful deliver
        distance_total = mms_buffer.get_spring_stroke() + \
            self.drip_extra_distance
        self.log_info_s(
            f"{log_prefix} total distance: {distance_total} mm"
        )

        # Load task(async)
        start = self._async_careful_load(slot_num, distance_total)
        if not start:
            # raise ChargeFailedError(f"{log_prefix} failed", mms_slot)
            return False

        # Extruder task(block)
        self._careful_extrude(slot_num, distance_total)

        # Stop running load task
        slot_pin = mms_slot.get_waiting_pin()
        if slot_pin and slot_pin is mms_slot.outlet:
            mms_slot.stop_homing(slot_pin)

        # Judge result with state of pins
        # or mms_slot.buffer_runout.is_triggered() ?
        result = False if mms_slot.outlet.is_triggered() else True
        self.log_info_s(f"{log_prefix} finish, result is '{result}'")
        return result

    def _standard_charge(self, slot_num):
        log_prefix = f"slot[{slot_num}] standard charge"
        self.log_info_s(f"{log_prefix} begin")
        mms_slot = self.mms.get_mms_slot(slot_num)

        # Prepare mms_buffer
        mms_buffer = mms_slot.get_mms_buffer()
        # Buffer full means Outlet is triggered
        if not mms_buffer.fill(slot_num):
            raise ChargeFailedError(
                f"{log_prefix} fill buffer failed",
                mms_slot
            )

        # Check filament is properly loaded into extruder
        if not self._extrude_to_release_outlet(slot_num):
            # Not properly loaded, simple unload
            extruder_adapter.retract(
                self.distance_unload,
                self.extrude_speed,
                wait=False
            )
            # Raise DeliveryFailedError if fail
            self.mms_delivery.unload_to_gate(slot_num)
            return False

        self.log_info_s(f"{log_prefix} finish")
        return True

    def _standard_charge_new(self, slot_num):
        log_prefix = f"slot[{slot_num}] standard charge"
        self.log_info_s(f"{log_prefix} begin")

        # Prepare buffer_runout pin
        # Raise DeliveryFailedError if fail
        # How about DeliveryTerminateSignal?
        self.mms_delivery.unload_until_buffer_runout_trigger(slot_num)
        self.mms_delivery.load_until_buffer_runout_release(slot_num)

        # Check filament is properly loaded into extruder
        if not self._extrude_to_trigger_runout(slot_num):
            # Not properly loaded, simple unload
            extruder_adapter.retract(
                self.distance_unload,
                self.extrude_speed,
                wait=False
            )
            # Raise DeliveryFailedError if fail
            self.mms_delivery.unload_to_gate(slot_num)
            return False

        self.log_info_s(f"{log_prefix} finish")
        return True

    def _safety_checks(self, slot_num):
        if slot_num is None:
            self.log_warning("current slot is None, return")
            return False

        if self.is_running():
            self.log_warning("another charge is running, return")
            return False

        # Check extruder
        if not extruder_adapter.is_hot_enough():
            return False

        return True

    def mms_charge(self, slot_num):
        self._exec_custom_macro(self.custom_before, "before")

        if not self._safety_checks(slot_num):
            return False

        log_prefix = f"slot[{slot_num}] charge"
        self.log_info_s(f"{log_prefix} begin")

        # Load to make sure Outlet or Entry is triggered
        if not self.mms_delivery.mms_load(slot_num):
            self.log_warning(f"{log_prefix} load prepare failed")
            return False

        with self._charge_is_running():
            try:
                # Make sure mms_buffer is idle
                self._pause_mms_buffer(slot_num)

                # Careful charge
                success = self._careful_charge(slot_num)

                # Standard charge if not success
                if not success:
                    retry_times = self.mms.get_retry_times()
                    # Retry loop
                    for i in range(retry_times):
                        success = self._standard_charge(slot_num)
                        if success:
                            break
                        self.log_info(
                            f"{log_prefix} retry {i+1}/{retry_times} ..."
                        )

                    # Retry end
                    if not success:
                        raise ChargeFailedError(
                            f"{log_prefix} failed after all retries",
                            self.mms.get_mms_slot(slot_num)
                        )

            except ChargeFailedError as e:
                self.log_warning(e)
                return False
            except Exception as e:
                # May receive DeliveryFailedError
                self.log_error(f"{log_prefix} error: {e}")
                return False

        self._charging_slot_num = slot_num
        self.log_info_s(f"{log_prefix} finish")
        self._exec_custom_macro(self.custom_after, "after")
        return True

    # ---- GCode ----
    @log_time_cost("log_info_s")
    def cmd_MMS_CHARGE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            self.log_warning("slot is not available, MMS_CHARGE failed")
            return

        for mms_slot in self.mms.get_mms_slots():
            if mms_slot.gate.is_triggered():
                self.log_warning(
                    f"slot[{slot_num}] can not charge"
                    f" when any gate is triggered")
                return

        with toolhead_adapter.snapshot():
            with toolhead_adapter.safe_z_raise(self.z_raise):
                self.mms_charge(slot_num)

    @log_time_cost("log_info_s")
    def cmd_MMS_CAREFUL_CHARGE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            self.log_warning(
                "slot is not available, MMS_CAREFUL_CHARGE failed"
            )
            return

        for mms_slot in self.mms.get_mms_slots():
            if mms_slot.gate.is_triggered():
                self.log_warning(
                    f"slot[{slot_num}] can not careful charge"
                    f" when any gate is triggered")
                return

        log_prefix = f"slot[{slot_num}] careful charge"

        with toolhead_adapter.snapshot():
            if not self._safety_checks(slot_num):
                return False

            # Load before make sure Outlet or Entry is triggered
            if not self.mms_delivery.mms_load(slot_num):
                self.log_warning(f"{log_prefix} load prepare failed")
                return False

            with self._charge_is_running():
                try:
                    # Make sure mms_buffer is idle
                    self._pause_mms_buffer(slot_num)

                    success = self._careful_charge(slot_num)
                    msg = "success" if success else "failed"
                    self.log_info(f"{log_prefix} {msg}")

                except ChargeFailedError as e:
                    self.log_warning(e)
                    self.log_info(f"{log_prefix} failed")
                except Exception as e:
                    # May receive DeliveryFailedError
                    self.log_error(f"{log_prefix} error: {e}")


def load_config(config):
    return MMSCharge(config)
