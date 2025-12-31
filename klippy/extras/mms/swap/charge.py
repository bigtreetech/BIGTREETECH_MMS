# Support for MMS Charge
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
from ..core.exceptions import ChargeFailedError
from ..core.logger import log_time_cost


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

    def _initialize_gcode(self):
        commands = [
            ("MMS_CHARGE", self.cmd_MMS_CHARGE),
            ("MMS_SIMPLE_CHARGE", self.cmd_MMS_SIMPLE_CHARGE),
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

    # ---- Control ----
    def pause(self, period_seconds):
        self.reactor.pause(self.reactor.monotonic() + period_seconds)

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
            raise ChargeFailedError(
                f"slot[{slot_num}] wait selector or drive stepper idle timeout",
                mms_slot
            )
        return mms_buffer

    # ---- Charge ----
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

        for i in range(self.extrude_times):
            extruder_adapter.extrude(
                self.extrude_distance,
                self.extrude_speed
            )

            if mms_slot.outlet.is_released():
                dist = self.extrude_distance * (i+1)
                self.log_info_s(
                    f"slot[{slot_num}] outlet is released, "
                    f"extrude: {dist} mm"
                )
                return True

            self.pause(0.2)

        dist = self.extrude_distance * self.extrude_times
        self.log_warning(
            f"slot[{slot_num}] outlet is not released after "
            f"total extrude: {dist} mm"
        )
        return False

    def _extrude_to_trigger_runout(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)

        if mms_slot.buffer_runout.is_triggered():
            self.log_warning(
                f"slot[{slot_num}] buffer_runout is already triggered")
            return False

        self.log_info_s(
            f"slot[{slot_num}] extrude to trigger buffer_runout begin")

        for i in range(self.extrude_times):
            extruder_adapter.extrude(
                self.extrude_distance,
                self.extrude_speed
            )

            if mms_slot.buffer_runout.is_triggered():
                dist = self.extrude_distance * (i+1)
                self.log_info_s(
                    f"slot[{slot_num}] buffer_runout is triggered, "
                    f"extrude: {dist} mm"
                )
                return True

            self.pause(0.2)

        dist = self.extrude_distance * self.extrude_times
        self.log_warning(
            f"slot[{slot_num}] buffer_runout is not triggered after "
            f"total extrude: {dist} mm"
        )
        return False

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

    def _exec_custom_macro(self, macro, position):
        if macro:
            self.log_info(
                f"MMS execute macro {position} CHARGE:\n"
                f"{macro}"
            )
            gcode_adapter.run_command(macro)

    def mms_charge(self, slot_num):
        self._exec_custom_macro(self.custom_before, "before")

        if not self._safety_checks(slot_num):
            return False

        log_prefix = f"slot[{slot_num}] charge"
        self.log_info_s(f"{log_prefix} begin")

        # Load before extruder check,
        # make sure Outlet or Entry is triggered
        if not self.mms_delivery.mms_load(slot_num):
            self.log_warning(f"{log_prefix} load prepare failed")
            return False

        with self._charge_is_running():
            try:
                # Make sure mms_buffer is idle
                self._pause_mms_buffer(slot_num)

                retry_times = self.mms.get_retry_times()
                success = False
                # Retry loop
                for i in range(retry_times):
                    success = self._standard_charge(slot_num)
                    if success:
                        break
                    self.log_info(f"{log_prefix} retry {i+1}/{retry_times} ...")

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

        self.log_info_s(f"{log_prefix} finish")
        self._exec_custom_macro(self.custom_after, "after")
        return True

    def mms_simple_charge(self, slot_num):
        if slot_num is None:
            self.log_warning("current slot is None, return")
            return False
        if self.is_running():
            self.log_warning("another charge is running, return")
            return False

        log_prefix = f"slot[{slot_num}] simple charge"
        self.log_info_s(f"{log_prefix} begin")

        # Load before extruder check,
        # make sure Outlet or Entry is triggered
        if not self.mms_delivery.mms_load(slot_num):
            self.log_warning(f"{log_prefix} load prepare failed")
            return False

        with self._charge_is_running():
            try:
                # Make sure mms_buffer is idle
                self._pause_mms_buffer(slot_num)

                retry_times = self.mms.get_retry_times()
                success = False
                # Retry loop
                for i in range(retry_times):
                    success = self._standard_charge(slot_num)
                    if success:
                        break
                    self.log_info(f"{log_prefix} retry {i+1}/{retry_times} ...")

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

        self.log_info_s(f"{log_prefix} finish")

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
    def cmd_MMS_SIMPLE_CHARGE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            self.log_warning(
                "slot is not available, MMS_SIMPLE_CHARGE failed"
            )
            return

        self.mms_simple_charge(slot_num)


def load_config(config):
    return MMSCharge(config)
