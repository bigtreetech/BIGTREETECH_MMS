# Support for MMS Filament Detection,
# which original module named "Filament Fracture"
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager
from dataclasses import dataclass

from ..adapters import printer_adapter
from ..core.exceptions import FilamentDetectionFailedError
from ..core.slot_pin import PinType
from ..hardware.stepper import MoveType


@dataclass(frozen=True)
class MMSFilamentDetectionConfig:
    log_flag: str = "==X=="


class MMSFilamentDetection:
    def __init__(self):
        fd_config = MMSFilamentDetectionConfig()
        self.log_flag = fd_config.log_flag

        self.pin_type = PinType()

        # Status
        self._enable = False
        self._enable_config = False
        self._activating = False
        self._activating_slot_num = None
        self._monitor_depth = 0
        # A release belongs to the complete outer delivery, not one retry.
        # Reinserting filament cannot erase a gap already created in its path.
        self._fault = None

        # Klippy event handler
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        # self._initialize_gcode()
        self._initialize_loggers()
        self._register()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_resume = self.mms.get_mms_resume()
        self.mms_endless_spool = self.mms.get_mms_endless_spool()
        self._enable = self.mms.filament_detection_is_enabled()
        self._enable_config = self._enable

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        # self.log_warning = mms_logger.create_log_warning(console_output=True)

        self.log_info_s = mms_logger.create_log_info(console_output=False)
        self.log_warning_s = mms_logger.create_log_warning(
            console_output=False)
        self.log_error_s = mms_logger.create_log_error(console_output=False)

    # ---- Status ----
    def is_enabled(self):
        # Dynamic print-state callbacks affect the next operation, not a
        # delivery scope that has already started (or latched a fault).
        return self._enable or self._monitor_depth > 0

    def is_activating(self):
        return self._activating

    # ---- Register ----
    def _register(self):
        if not self._enable:
            return
        for mms_slot in self.mms.get_mms_slots():
            slot_num = mms_slot.get_num()
            mms_slot.inlet.add_release_callback(
                callback=self._handle_inlet_is_released,
                params={"slot_num":slot_num}
            )
            mms_slot.gate.add_release_callback(
                callback=self._handle_gate_is_released,
                params={"slot_num":slot_num}
            )
            if mms_slot.entry.is_set():
                mms_slot.entry.add_release_callback(
                    callback=self._handle_entry_is_released,
                    params={"slot_num":slot_num})

    # ---- Handlers ----
    def _record_fault(self, slot_num, pin_type):
        reason = f"{pin_type} released during delivery"
        if self._fault is None:
            self._fault = (slot_num, reason)
            self.log_info(f"slot[{slot_num}] filament detection: "
                          f"{reason} {self.log_flag}")
        recovery = self.mms_resume.get_status()
        if recovery["pending"] and recovery["physical_slot"] == slot_num:
            self.mms_resume.require_filament_clearance(
                slot_num, reason, runout=(self._fault == (slot_num, reason)
                                         and pin_type == self.pin_type.inlet))

    def check(self, slot_num):
        """Raise in the delivery caller, never in the sensor callback."""
        if not self.mms_resume.is_current_attempt():
            raise FilamentDetectionFailedError("MMS recovery was cancelled")
        if not self.is_enabled():
            return
        mms_slot = self.mms.get_mms_slot(slot_num)
        reason = (self._fault[1] if self._fault is not None
                  and self._fault[0] == slot_num else None)
        if mms_slot.inlet.is_set() and mms_slot.inlet.is_released():
            self.mms_resume.handle_inlet_runout(slot_num, during_delivery=True)
        if ((mms_slot.inlet.is_set() and mms_slot.inlet.is_released()
             or self.mms_resume.get_exhausted_slot() == slot_num)
                and not self.mms_resume.is_tail_available(slot_num)):
            self._record_fault(slot_num, self.pin_type.inlet)
            reason = f"{self.pin_type.inlet} released during delivery"
        if reason is not None:
            raise FilamentDetectionFailedError(
                f"slot[{slot_num}] {reason}; "
                "clear residual filament and refill the original inlet "
                "before retrying", mms_slot)

    def _common_handler(self, slot_num, pin_type):
        if not self.is_enabled() \
            or not self._activating \
            or slot_num != self._activating_slot_num:
            return

        if self.mms_resume.is_tail_available(slot_num):
            return
        self._record_fault(slot_num, pin_type)
        try:
            # Button events run outside the G-code command's greenlet. Only
            # stop its drive here; the caller's next check owns the exception,
            # pause and recovery. Never run parking, selection or heater G-code.
            mms_slot = self.mms.get_mms_slot(slot_num)
            mms_drive = mms_slot.get_mms_drive()
            if not mms_drive.is_running():
                return
            if mms_drive.move_type == MoveType.MANUAL_HOME:
                slot_pin = mms_slot.get_waiting_pin()
                if slot_pin is not None \
                    and slot_pin.get_pin_type() != self.pin_type.selector:
                    mms_slot.stop_homing(slot_pin)
            elif mms_drive.move_type in (MoveType.DRIP_MOVE,
                                         MoveType.BUFFER_FEED):
                mms_drive.terminate_moving()
        except Exception as e:
            self.log_error_s(f"slot[{slot_num}] filament stop error: {e}")

    def _handle_inlet_is_released(self, slot_num, inlet_edge=True):
        if self._enable_config:
            # The recovery owner records loaded-swap edges through finishing
            # as well as ordinary Inlet runout. It filters maintenance and
            # schedules ordinary PAUSE outside this sensor callback.
            self.mms_resume.handle_inlet_runout(
                slot_num, during_delivery=(self._activating
                    and slot_num == self._activating_slot_num),
                inlet_edge=inlet_edge)
        self._common_handler(slot_num, self.pin_type.inlet)

    def _handle_gate_is_released(self, slot_num):
        self.mms_resume.handle_swap_path_release(slot_num, self.pin_type.gate)
        # Outer charge/purge scopes include legitimate retractions, including
        # synchronized E. Keep Gate's original forward-drive scope; only
        # Inlet monitoring spans the complete operation.
        drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        if (not drive.is_running()
                or drive.move_type not in (MoveType.MANUAL_MOVE,
                                          MoveType.MANUAL_HOME,
                                          MoveType.DRIP_MOVE)
                or not drive.get_status()["forward"]):
            return
        self._common_handler(slot_num, self.pin_type.gate)

    def _handle_entry_is_released(self, slot_num):
        # Entry is optional and may be shared by slots. The recovery owner
        # accepts only its loaded physical target; no motion runs here.
        self.mms_resume.handle_swap_path_release(slot_num, self.pin_type.entry)

    def force_handle_inlet_is_released(self, slot_num):
        if not self.is_enabled():
            return
        # Preserve an enclosing delivery's identity when the buffer calls us.
        if self._monitor_depth:
            self._handle_inlet_is_released(slot_num, inlet_edge=False)
            return
        self.activate(slot_num)
        try:
            self._handle_inlet_is_released(slot_num, inlet_edge=False)
        finally:
            self.deactivate()

    # ---- Control ----
    def enable(self):
        self._enable = True
        self.log_info_s("MMS filament detection is enabled")

    def disable(self):
        self._enable = False
        self.deactivate()
        self.log_info_s("MMS filament detection is disabled")

    def recover(self):
        if self._enable is not self._enable_config:
            if self._enable_config:
                self.enable()
            else:
                self.disable()

    def activate(self, slot_num, configured=False):
        enabled = self._enable_config if configured else self._enable
        if not enabled and not self._monitor_depth:
            return
        if self._monitor_depth:
            if self._activating_slot_num != slot_num:
                raise FilamentDetectionFailedError(
                    f"cannot monitor slot[{slot_num}] while "
                    f"slot[{self._activating_slot_num}] delivery is monitored")
            return
        self._fault = None
        self._activating = True
        self._activating_slot_num = slot_num
        self.log_info_s(
            f"slot[{slot_num}] filament detection is activated")

    def deactivate(self, slot_num=None):
        if (self._monitor_depth or slot_num is not None
                and slot_num != self._activating_slot_num):
            return
        msg_num = self._activating_slot_num \
            if self._activating_slot_num is not None else '*'
        self._activating = False
        self._activating_slot_num = None
        self.log_info_s(
            f"slot[{msg_num}] filament detection is deactivated")

    @contextmanager
    def monitor(self, slot_num, configured=False):
        # Explicit charge/purge uses the configured policy even after endless
        # disabled detection for normal printing. Nested delivery inherits it.
        # Do not overwrite the dynamic policy: observer callbacks may update
        # it while this scope yields, and their value must survive our exit.
        enabled = self._enable_config if configured else self._enable
        if not enabled and not self._monitor_depth:
            yield
            return
        self.activate(slot_num, configured)
        self._monitor_depth += 1
        try:
            self.check(slot_num)
            yield
            self.check(slot_num)
        finally:
            self._monitor_depth -= 1
            if not self._monitor_depth:
                # Normal purge keeps its Buffer owner after this scope exits.
                # Preserve its detection and any fault until that owner stops.
                buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()
                if not buffer.is_activating():
                    self.deactivate()
