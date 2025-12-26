# Support for MMS SLOT Pins
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass

from ..adapters import printer_adapter
from ..hardware.button import (
    MMSButtonGate,
    MMSButtonInlet,
    MMSButtonSelector
)


def check_ready(method):
    def wrapper(self, *args, **kwargs):
        if not self._is_ready:
            return
        return method(self, *args, **kwargs)
    return wrapper


@dataclass(frozen=True)
class SlotPinConfig:
    break_delay: float = 0.1 # Seconds


@dataclass(frozen=True)
class PinType:
    selector: str = "selector"
    inlet: str = "inlet"
    gate: str = "gate"
    gate_invert: str = "gate_invert"
    outlet: str = "outlet"
    entry: str = "entry"
    buffer_runout: str = "buffer_runout"


@dataclass(frozen=True)
class PinState:
    triggered: str = "triggered"
    released: str = "released"


class BaseSlotPin(ABC):
    """Base class for all slot pin handlers"""
    def __init__(self, mms_slot, mcu_pin, pin_type):
        # Common initialization for all pin types
        self.mms_slot = mms_slot
        self.mcu_pin = mcu_pin
        self.pin_type = pin_type
        self.pin_state = PinState()
        # SLOT meta
        self.slot_num = mms_slot.get_num()

        # Register MMS pin_obj later
        self.pin_obj = None
        # Status
        self._is_ready = False
        self._waiting = False

        # Pin-specific setup
        self._setup_pin()

        # Klippy event handler
        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_connect(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    def _handle_klippy_ready(self):
        self._is_ready = True

    @abstractmethod
    def _setup_pin(self):
        """Pin-specific initialization (implemented in subclasses)"""
        pass

    @abstractmethod
    def trigger(self, mcu_pin):
        """Handle trigger events"""
        pass

    @abstractmethod
    def release(self, mcu_pin):
        """Handle release events"""
        pass

    # ---- Waiting func ----
    @contextmanager
    def wait_callback(self):
        """Context manager for temporary callback waiting state"""
        self._waiting = True
        try:
            yield
        finally:
            self._waiting = False

    def is_waiting(self):
        """Check if currently in waiting state"""
        return self._waiting

    def start_waiting(self):
        """Setup waiting state"""
        self._waiting = True

    def stop_waiting(self):
        """Exit waiting state immediately"""
        self._waiting = False

    # ---- Common log ----
    def _can_log(self):
        """Condition for allowing state logging"""
        return True

    def _log_state(self, state, silent=True):
        """Log pin state changes"""
        if silent:
            self.log_info_s(
                f"slot[{self.slot_num}] '{self.pin_type}' is {state}")
        else:
            self.log_info(
                f"slot[{self.slot_num}] '{self.pin_type}' is {state}")

    # ---- State delegates ----
    def is_triggered(self):
        return self.pin_obj.is_triggered() if self.pin_obj else False

    def is_released(self):
        return self.pin_obj.is_released() if self.pin_obj else False

    def is_new_triggered(self):
        return self.pin_obj.is_new_triggered() if self.pin_obj else False

    def is_set(self):
        return self.pin_obj is not None

    def get_state(self):
        return self.pin_obj.get_state() if self.pin_obj else None

    def get_endstop(self):
        return self.pin_obj.get_endstop() if self.pin_obj else None

    def get_mcu_pin(self):
        return self.pin_obj.get_mcu_pin() if self.pin_obj else self.mcu_pin

    def get_mms_name(self):
        return self.pin_obj.get_mms_name()

    # ---- Callbacks ----
    def add_trigger_callback(self, callback, params=None):
        self.pin_obj.register_trigger_callback(callback, params)

    def remove_trigger_callback(self, callback):
        self.pin_obj.unregister_trigger_callback(callback)

    def add_release_callback(self, callback, params=None):
        self.pin_obj.register_release_callback(callback, params)

    def remove_release_callback(self, callback):
        self.pin_obj.unregister_release_callback(callback)

    @contextmanager
    def monitor_release(self, condition, callback, params):
        is_added = False
        if condition():
            self.add_release_callback(callback, params)
            is_added = True
        try:
            yield
        finally:
            if is_added:
                self.remove_release_callback(callback)

    # ---- Steppers ----
    def set_stepper(self, mms_stepper):
        # Register mcu_stepper to mcu_endstop, for moving:manual_home
        self.pin_obj.set_stepper(mms_stepper.get_mcu_stepper())

    def break_endstop_homing(self):
        if not self._waiting:
            return False

        mcu_endstop = self.get_endstop()
        if not mcu_endstop:
            return False

        # Get mcu objects
        mcu_dispatch = mcu_endstop._dispatch
        mcu_trsync = mcu_dispatch._trsyncs[0]

        # Send trsync_trigger command
        mcu_trsync._trsync_trigger_cmd.send([
            mcu_trsync._oid,
            mcu_trsync.REASON_HOST_REQUEST
        ])
        # ret = mcu_trsync._trsync_query_cmd.send([
        #     mcu_trsync._oid,
        #     mcu_trsync.REASON_HOST_REQUEST
        # ])
        # self.log_info(f"trsync query return:{ret}")

        # Update mms_stepper status to terminated before pause
        self.mms_slot.terminate_stepper_moving()
        # End up waiting
        self.stop_waiting()

        # Wait a while for stepper's step count cleaning
        reactor = printer_adapter.get_reactor()
        reactor.pause(reactor.monotonic() + SlotPinConfig.break_delay)

        # Teardown dispatch
        mcu_dispatch.stop()

        return True


class SlotPinSelector(BaseSlotPin):
    def __init__(self, mms_slot, mcu_pin):
        super().__init__(mms_slot, mcu_pin, PinType().selector)

    def _setup_pin(self):
        self.pin_obj = MMSButtonSelector(self.mcu_pin)
        self.pin_obj.register_trigger_callback(self.trigger)
        self.pin_obj.register_release_callback(self.release)

        self.mms_selector = None

    def _can_log(self):
        return self._is_selecting() or self.is_waiting()

    @check_ready
    def trigger(self, mcu_pin):
        if self._can_log():
            self._log_state(self.pin_state.triggered)

        if self.is_waiting():
            self.mms_slot.complete_selector_moving()
            self.stop_waiting()

        # Initial startup update status
        self._init_focus()

    @check_ready
    def release(self, mcu_pin):
        if self._can_log():
            self._log_state(self.pin_state.released)

    # ---- Custom ----
    def set_stepper(self, mms_selector):
        self.mms_selector = mms_selector
        # Register mcu_stepper to mcu_endstop, for moving:manual_home
        self.pin_obj.set_stepper(mms_selector.get_mcu_stepper())

    def _is_selecting(self):
        return self.mms_selector \
            and self.mms_selector.get_focus_slot() == self.slot_num

    def _init_focus(self):
        if self.mms_selector and self.mms_selector.is_init():
            self.mms_selector.update_focus_slot(self.slot_num)
            self._log_state(self.pin_state.triggered)


class SlotPinInlet(BaseSlotPin):
    def __init__(self, mms_slot, mcu_pin):
        super().__init__(mms_slot, mcu_pin, PinType().inlet)

    def _setup_pin(self):
        self.pin_obj = MMSButtonInlet(self.mcu_pin)
        self.pin_obj.register_trigger_callback(self.trigger)
        self.pin_obj.register_release_callback(self.release)

    @check_ready
    def trigger(self, mcu_pin):
        self._log_state(self.pin_state.triggered)
        self.mms_slot.slot_led.notify()

        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()

        if self.mms_slot.autoload_is_enabled():
            self._autoload()

    @check_ready
    def release(self, mcu_pin):
        self._log_state(self.pin_state.released)
        self.mms_slot.slot_led.notify()

        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()

    def _autoload(self):
        mms_autoload = printer_adapter.get_mms_autoload()
        if mms_autoload.is_enabled():
            mms_autoload.execute(self.slot_num)


class SlotPinGate(BaseSlotPin):
    def __init__(self, mms_slot, mcu_pin):
        super().__init__(mms_slot, mcu_pin, PinType().gate)

    def _setup_pin(self):
        self.pin_obj = MMSButtonGate(self.mcu_pin)
        self.pin_obj.register_trigger_callback(self.trigger)
        self.pin_obj.register_release_callback(self.release)

    @check_ready
    def trigger(self, mcu_pin):
        self._log_state(self.pin_state.triggered)
        self.mms_slot.slot_led.notify()
        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()

    @check_ready
    def release(self, mcu_pin):
        self._log_state(self.pin_state.released)
        self.mms_slot.slot_led.notify()
        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()


class SlotPinGateInvert(BaseSlotPin):
    def __init__(self, mms_slot, mcu_pin):
        mcu_pin_inv = mcu_pin.lstrip("!") \
            if mcu_pin.startswith("!") \
            else "!"+mcu_pin
        super().__init__(mms_slot, mcu_pin_inv, PinType().gate_invert)

    def _setup_pin(self):
        self.pin_obj = MMSButtonGate(self.mcu_pin)
        self.pin_obj.register_trigger_callback(self.trigger)
        self.pin_obj.register_release_callback(self.release)

    @check_ready
    def trigger(self, mcu_pin):
        self._log_state(self.pin_state.triggered)
        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()
            return True
        return False

    @check_ready
    def release(self, mcu_pin):
        self._log_state(self.pin_state.released)
        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()
            return True
        return False


class SlotPinOutlet(BaseSlotPin):
    def __init__(self, mms_slot, mcu_pin):
        super().__init__(mms_slot, mcu_pin, PinType().outlet)

    def _setup_pin(self):
        # No immediate initialization
        pass

    def set_pin_obj(self, pin_obj):
        self.pin_obj = pin_obj
        self.mcu_pin = pin_obj.get_mcu_pin()

    @check_ready
    def trigger(self, mcu_pin):
        self._log_state(self.pin_state.triggered)
        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()
            return True
        return False

    @check_ready
    def release(self, mcu_pin):
        self._log_state(self.pin_state.released)
        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()
            return True
        return False


class SlotPinEntry(BaseSlotPin):
    def __init__(self, mms_slot, mcu_pin):
        super().__init__(mms_slot, mcu_pin, PinType().entry)

    def _setup_pin(self):
        # No immediate initialization
        pass

    def set_pin_obj(self, pin_obj):
        self.pin_obj = pin_obj
        self.mcu_pin = pin_obj.get_mcu_pin()

    @check_ready
    def trigger(self, mcu_pin):
        self._log_state(self.pin_state.triggered)
        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()
            return True
        return False

    @check_ready
    def release(self, mcu_pin):
        self._log_state(self.pin_state.released)
        if self.is_waiting():
            # self.mms_slot.complete_drive_moving()
            self.stop_waiting()
            return True
        return False


class SlotPinBufferRunout(BaseSlotPin):
    def __init__(self, mms_slot, mcu_pin):
        super().__init__(mms_slot, mcu_pin, PinType().buffer_runout)

    def _setup_pin(self):
        # No immediate initialization
        pass

    def set_pin_obj(self, pin_obj):
        self.pin_obj = pin_obj
        self.mcu_pin = pin_obj.get_mcu_pin()

    @check_ready
    def trigger(self, mcu_pin):
        self._log_state(self.pin_state.triggered)
        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()
            return True
        return False

    @check_ready
    def release(self, mcu_pin):
        self._log_state(self.pin_state.released)
        if self.is_waiting():
            self.mms_slot.complete_drive_moving()
            self.stop_waiting()
            return True
        return False
