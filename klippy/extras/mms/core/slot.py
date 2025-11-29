# Support for MMS SLOT
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field, fields

from ..adapters import idle_timeout_adapter, printer_adapter
from ..hardware.button import (
    MMSButtonGate,
    MMSButtonInlet,
    MMSButtonSelector
)
from ..hardware.led import MMSLedEffect, MMSLedEvent


@dataclass(frozen=True)
class SlotConfig:
    # Must be first line, printer_config is the param of loading object
    printer_config: object

    skip_configs = ["printer_config",]
    # ==== configuration values in *.cfg, must set default  ====
    selector: str = ""
    inlet: str = ""
    gate: str = ""

    led_name: str = ""
    chip_index: list = field(default_factory=list)
    brightness: float = 0.5

    autoload_enable: int = 1

    rfid_name: str = ""
    rfid_enable: int = 0
    rfid_detect_duration: float = 50  # seconds
    rfid_read_duration: float = 4  # seconds

    def __post_init__(self):
        type_method_map = {
            str: "get",
            int: "getint",
            float: "getfloat",
            list: "getintlist",
        }

        for field_info in fields(self):
            field_name = field_info.name
            field_type = field_info.type

            if field_name in self.skip_configs:
                continue

            # Default type is str
            get_method = type_method_map.get(field_type, "get")
            config_value = getattr(self.printer_config, get_method)(field_name)
            object.__setattr__(self, field_name, config_value)


@dataclass(frozen=True)
class SlotPinConfig:
    break_delay: float = 0.5 # Seconds


@dataclass(frozen=True)
class PinType:
    selector: str = "selector"
    inlet: str = "inlet"
    gate: str = "gate"
    outlet: str = "outlet"
    entry: str = "entry"
    buffer_runout: str = "buffer_runout"


@dataclass(frozen=True)
class PinState:
    triggered: str = "triggered"
    released: str = "released"


class MMSSlot:
    """
    A class to represent a Multi-Material System (MMS) SLOT.
    """
    def __init__(self, config):
        # The configuration section for the current slot, e.g., [mms slot 0]
        self.reactor = printer_adapter.get_reactor()

        self.name = config.get_name()
        self.num = int(self.name.split()[-1])
        self.led_notify_delay = 2.5 + 0.2*self.num

        self.slot_config = SlotConfig(config)
        self.pin_type = PinType()
        self.pin_state = PinState()

        # LED, init after klippy is connected
        self.slot_led = None
        # RFID, init after klippy is connected
        self.slot_rfid = None

        # Initialize Pins
        self._initialize_pins()

        # Register connect handler to printer
        printer_adapter.register_mms_initialized(
            self._handle_mms_initialized)
        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _initialize_pins(self):
        self.selector = SlotPinSelector(self, self.slot_config.selector)
        self.inlet = SlotPinInlet(self, self.slot_config.inlet)
        self.gate = SlotPinGate(self, self.slot_config.gate)
        # Register mcu_pin by MMS
        self.outlet = SlotPinOutlet(self, None)
        # Register mcu_pin by MMS
        self.buffer_runout = SlotPinBufferRunout(self, None)
        # Register mcu_pin by MMS
        self.entry = SlotPinEntry(self, None)

        self.slot_pin_map = {
            self.pin_type.selector: self.selector,
            self.pin_type.inlet: self.inlet,
            self.pin_type.gate: self.gate,
            self.pin_type.outlet: self.outlet,
            self.pin_type.buffer_runout: self.buffer_runout,
            self.pin_type.entry: self.entry,
        }

    def _handle_mms_initialized(self, mms):
        # Initialize from MMS
        assert mms, "MMS not found"
        self.mms = mms

        # MMS Buffer
        self.mms_buffer = self.mms.get_mms_buffer(self.num)

        # MMS Stepper
        self.mms_selector = self.mms.get_selector(self.num)
        self.mms_drive = self.mms.get_drive(self.num)

        # Setup mcu_stepper for mcu_endstop
        self.selector.set_stepper(self.mms_selector)
        self.inlet.set_stepper(self.mms_drive)
        self.gate.set_stepper(self.mms_drive)

        self.buffer_runout.set_pin_obj(
            self.mms.get_buffer_runout(self.num))
        self.buffer_runout.set_stepper(self.mms_drive)

        self.outlet.set_pin_obj(self.mms.get_outlet(self.num))
        self.outlet.set_stepper(self.mms_drive)

        entry = self.mms.get_entry()
        if entry:
            self.entry.set_pin_obj(entry)
            self.entry.set_stepper(self.mms_drive)

    def _handle_klippy_connect(self):
        self._initialize_loggers()
        self._initialize_led()
        self._initialize_rfid()

    def _handle_klippy_ready(self):
        self.reactor.register_timer(
            callback=self._init_led_notify,
            waketime=self.reactor.monotonic()+self.led_notify_delay
        )
        # Register led effect deactivate callback
        printer_adapter.register_mms_stepper_running(
            handler=self._handler_mms_stepper_running)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_silent = mms_logger.create_log_info(console_output=False)

    def _initialize_led(self):
        self.slot_led = SlotLED(self)
        self.slot_led.set_brightness(self.slot_config.brightness)

    def _initialize_rfid(self):
        self.slot_rfid = SlotRFID(self)
        self.slot_rfid.setup(
            self.slot_config.rfid_name,
            self.slot_config.rfid_enable,
            self.slot_config.rfid_detect_duration,
            self.slot_config.rfid_read_duration
        )

    def _init_led_notify(self, eventtime):
        self.slot_led.notify()
        return self.reactor.NEVER

    # ---- Get properties ----
    def get_num(self):
        return self.num

    def autoload_is_enable(self):
        return self.slot_config.autoload_enable

    def get_rfid_status(self):
        return self.slot_rfid.get_status()

    def get_mms_selector(self):
        return self.mms_selector

    def get_mms_drive(self):
        return self.mms_drive

    def get_mms_buffer(self):
        return self.mms_buffer

    # ---- MMS support ----
    def find_waiting(self, mcu_pin, pin_type, pin_state):
        slot_pin = self.slot_pin_map.get(pin_type, None)
        if slot_pin and slot_pin.is_waiting():
            if pin_state == self.pin_state.triggered:
                return slot_pin.trigger(mcu_pin)
            elif pin_state == self.pin_state.released:
                return slot_pin.release(mcu_pin)
        return False

    # ---- MMS LED support ----
    def get_led_name(self):
        return self.slot_config.led_name

    def get_chip_index(self):
        # Function config.getintlist() return a tuple, trans to list
        return [i for i in self.slot_config.chip_index]

    def get_pins_state(self):
        dct = {
            "selector": self.selector.get_state(),
            "inlet": self.inlet.get_state(),
            "gate": self.gate.get_state(),
            "outlet": None,
            "entry": None,
        }

        if self.outlet.is_set():
           dct["outlet"] = self.outlet.get_state()

        if self.entry.is_set():
           dct["entry"] = self.entry.get_state()

        return dct

    # ---- MMS Delivery support ----
    def get_wait_func(self, pin_type):
        slot_pin = self.slot_pin_map.get(pin_type, None)
        return slot_pin.wait_callback if slot_pin else None

    def check_pin(self, pin_type, trigger):
        slot_pin = self.slot_pin_map.get(pin_type, None)
        if not slot_pin:
            return None
        return slot_pin.is_triggered() if trigger else slot_pin.is_released()

    def format_endstop_pair(self, pin_type):
        slot_pin = self.slot_pin_map.get(pin_type, None)
        return [
            (slot_pin.get_endstop(), slot_pin.get_mcu_pin()),
        ] if slot_pin else []

    def format_endstop_pairs(self, pin_type_lst):
        pair_lst = []
        for pin_type in pin_type_lst:
            slot_pin = self.slot_pin_map.get(pin_type, None)
            if slot_pin:
                pair_lst.append(
                    (slot_pin.get_endstop(), slot_pin.get_mcu_pin())
                )
        return pair_lst

    def get_waiting_pin(self):
        for slot_pin in self.slot_pin_map.values():
            if slot_pin.is_waiting():
                return slot_pin
        return None

    def stop_homing(self, slot_pin=None):
        slot_pin = slot_pin or self.get_waiting_pin()
        if slot_pin:
            success = slot_pin.break_endstop_homing()
            if success:
                self.log_info(
                    f"slot[{self.num}] "
                    f"'{slot_pin.get_mms_name()}' homing stop"
                )
                return

        # Not pin is waiting
        self.log_warning(f"slot[{self.num}] no homing is waiting")

    # ---- MMS Stepper support ----
    def complete_selector_moving(self):
        if self.mms_selector and self.mms_selector.is_running():
            self.mms_selector.complete_manual_home()

    def complete_drive_moving(self):
        if self.mms_drive and self.mms_drive.is_running():
            self.mms_drive.complete_manual_home()

    def terminate_stepper_moving(self):
        if self.mms_selector and self.mms_selector.is_running():
            self.mms_selector.terminate_manual_home()

        if self.mms_drive and self.mms_drive.is_running():
            self.mms_drive.terminate_manual_home()

    # ---- MMS Exceptions support ----
    def handle_mms_exception_raised(self, exception):
        self.log_info_silent(f"slot[{self.num}] receive exception: {exception}")

        # Activate LED effect
        self.slot_led.activate_blinking()
        # Register deactivate callback
        # idle_timeout_adapter.register_busy_callback(
        #     callback=self.slot_led.deactivate_blinking)

        # e_type = type(exception)
        # if e_type is DeliveryFailedError:
        #     self._handle_delivery_triggered(exception)
        # elif e_type is DeliveryPreconditionError:
        #     self._handle_precondition_error(exception)
        # elif e_type is DeliveryReadyError:
        #     self._handle_ready_error(exception)
        # else:
        #     self._handle_generic_mms_exception(exception)

    def _handler_mms_stepper_running(self):
        self.slot_led.deactivate_blinking()
        # Remove handler
        # printer_adapter.register_mms_stepper_running(handler=None)

    # ---- Pin Status ----
    def format_pins_status(self):
        info = f"slot[{self.num}] "
        info += f"selector={1 if self.selector.is_triggered() else 0} "
        info += f"inlet={1 if self.inlet.is_triggered() else 0} "
        info += f"gate={1 if self.gate.is_triggered() else 0} "
        info += f"runout={1 if self.buffer_runout.is_triggered() else 0} "
        info += f"outlet={1 if self.outlet.is_triggered() else 0} "
        if self.entry.is_set():
            info += f"entry={1 if self.entry.is_triggered() else 0} "
        info += "\n"
        return info

    # Inlet is triggered
    def is_ready(self):
        """Check if inlet is triggered"""
        return self.inlet.is_triggered()

    # Inlet/gate both triggered
    def is_loading(self):
        """Check if both inlet and gate are triggered"""
        return self.inlet.is_triggered() and self.gate.is_triggered()

    # Inlet/gate/outlet/entry all triggered
    def is_fully_loaded(self):
        lst = [
            self.inlet.is_triggered(),
            self.gate.is_triggered(),
            self.outlet.is_triggered()
        ]
        if self.entry.is_set():
            lst.append(self.entry.is_triggered())
        return all(lst)

    # Inlet/gate/outlet/entry all released
    def is_empty(self):
        lst = [
            self.inlet.is_released(),
            self.gate.is_released(),
            self.outlet.is_released()
        ]
        if self.entry.is_set():
            lst.append(self.entry.is_released())
        return all(lst)

    def is_new_insert(self):
        return self.inlet.is_new_triggered()

    def selector_is_triggered(self):
        return self.selector.is_triggered()

    def entry_is_set(self):
        return self.entry.is_set()


##########################################
# SLOT Pins
##########################################
def check_ready(method):
    def wrapper(self, *args, **kwargs):
        if not self._is_ready:
            return
        return method(self, *args, **kwargs)
    return wrapper


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

    def stop_waiting(self):
        """Exit waiting state immediately"""
        self._waiting = False

    # ---- Common log ----
    def _can_log(self):
        """Condition for allowing state logging"""
        return True

    def _log_state(self, state):
        """Log pin state changes"""
        self.log_info(f"slot[{self.slot_num}] '{self.pin_type}' is {state}")

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

        if self.mms_slot.autoload_is_enable():
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


##########################################
# SLOT LED
##########################################
class SlotLED:
    def __init__(self, mms_slot):
        self.mms_slot = mms_slot
        # SLOT meta
        self.slot_num = mms_slot.get_num()

        # Logger
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)

        self.brightness = None
        # Current LED effect
        self.led_effect = None

        self.mms_led_effect = MMSLedEffect()
        self.mms_led_event = MMSLedEvent()

    def set_brightness(self, brightness):
        self.brightness = brightness

    # ---- Commands ----
    def _effect_playing(self):
        return self.led_effect is not None

    def _rfid_led_keep(self):
        if not self.mms_slot.is_empty() \
            and not self.mms_slot.is_new_insert() \
            and self.mms_slot.slot_rfid \
            and self.mms_slot.slot_rfid.has_tag_read():
            # Slot has RFID tag read, don't update LED
            return True
        return False

    def notify(self):
        if self._effect_playing() or self._rfid_led_keep():
            return

        printer_adapter.send_event(
            self.mms_led_event.slot_change_brightness,
            self.slot_num,
            self.brightness
        )
        printer_adapter.send_event(
            self.mms_led_event.slot_notify,
            self.slot_num
        )

    def change_color(self, color):
        printer_adapter.send_event(
            self.mms_led_event.slot_change_color,
            self.slot_num,
            color
        )
        self.log_info(f"slot[{self.slot_num}] new led color: {color}")

    # ---- LED Effects ----
    def _activate(self, effect_name, effect_event, reverse=False):
        if self.led_effect is None:
            printer_adapter.send_event(effect_event, self.slot_num, reverse)
            self.led_effect = effect_name

    def _deactivate(self, effect_name, effect_event):
        if self.led_effect == effect_name:
            printer_adapter.send_event(effect_event, self.slot_num)
            self.led_effect = None
            # Recover
            self.notify()

    def deactivate_led_effect(self):
        if self.led_effect:
            event = self.mms_led_event.get_effect_event(
                self.led_effect, enable=False)
            self._deactivate(self.led_effect, event)

    def activate_marquee(self, reverse=False):
        effect = self.mms_led_effect.marquee
        event = self.mms_led_event.get_effect_event(effect)
        self._activate(effect, event, reverse)

    def deactivate_marquee(self):
        effect = self.mms_led_effect.marquee
        event = self.mms_led_event.get_effect_event(effect, enable=False)
        self._deactivate(effect, event)

    def activate_breathing(self):
        effect = self.mms_led_effect.breathing
        event = self.mms_led_event.get_effect_event(effect)
        self._activate(effect, event)

    def deactivate_breathing(self):
        effect = self.mms_led_effect.breathing
        event = self.mms_led_event.get_effect_event(effect, enable=False)
        self._deactivate(effect, event)

    def activate_rainbow(self, reverse=False):
        effect = self.mms_led_effect.rainbow
        event = self.mms_led_event.get_effect_event(effect)
        self._activate(effect, event, reverse)

    def deactivate_rainbow(self):
        effect = self.mms_led_effect.rainbow
        event = self.mms_led_event.get_effect_event(effect, enable=False)
        self._deactivate(effect, event)

    def activate_blinking(self):
        effect = self.mms_led_effect.blinking
        event = self.mms_led_event.get_effect_event(effect)
        self._activate(effect, event)

    def deactivate_blinking(self):
        effect = self.mms_led_effect.blinking
        event = self.mms_led_event.get_effect_event(effect, enable=False)
        self._deactivate(effect, event)


##########################################
# SLOT RFID
##########################################
class SlotRFID:
    def __init__(self, mms_slot):
        self.mms_slot = mms_slot
        # SLOT meta
        self.slot_num = mms_slot.get_num()
        # Logger
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)

        # Setup later
        self.name = None
        self.enable = None
        self.detect_duration = None
        self.read_duration = None

        self.mms_rfid = None

        # Status
        self._is_detecting = False
        self._is_reading = False

        self.detect_begin_at = None
        self.detect_end_at = None
        self.read_begin_at = None
        self.read_end_at = None

        # Tag data
        self._init_tag()

    def _init_tag(self):
        self.tag_data = None
        self.tag_uid = None
        self.tag_color = None

    def setup(self, name, enable, detect_duration, read_duration):
        self.name = name
        self.enable = enable
        self.detect_duration = detect_duration
        self.read_duration = read_duration

        self.mms_rfid = printer_adapter.get_obj(name)
        self.mms_delivery = printer_adapter.get_mms_delivery()

    def get_status(self):
        return {
            "name": self.name,

            # "detecting": self._is_detecting,
            # "detect_duration": self.detect_duration,
            # "detect_begin_at": self.detect_begin_at,
            # "detect_end_at": self.detect_end_at,

            # "reading": self._is_reading,
            # "read_duration": self.read_duration,
            # "read_begin_at": self.read_begin_at,
            # "read_end_at": self.read_end_at,

            "tag_uid": self.tag_uid,
            "tag_color": self.tag_color,
        }

    def has_tag_read(self):
        return self.tag_color is not None

    # ---- Detect ----
    def rfid_detect_begin(self):
        if self._is_detecting:
            self.log_info(f"slot[{self.slot_num}] RFID is detecting")
            return

        self._is_detecting = True
        self.detect_begin_at = time.time()
        self.mms_rfid.detect_begin(callback=self.handle_detected)
        self.log_info(f"slot[{self.slot_num}] RFID detect begin")

    def rfid_detect_end(self):
        if not self._is_detecting:
            self.log_info(f"slot[{self.slot_num}] RFID is not detecting")
            return

        self.mms_rfid.detect_end()
        self._is_detecting = False
        self.detect_end_at = time.time()
        self.log_info(f"slot[{self.slot_num}] RFID detect end")

    def handle_detected(self, data):
        if data:
            self.rfid_detect_end()
            self.log_info(f"slot[{self.slot_num}] RFID detect: {data}")

            self.tag_uid = data
            self.mms_delivery.mms_stop(self.slot_num)
            self.rfid_read_begin()

        elif time.time()-self.detect_begin_at > self.detect_duration:
            self.rfid_detect_end()
            self.log_info(f"slot[{self.slot_num}] RFID detect timeout")

    # ---- Read ----
    def rfid_read_begin(self):
        if self._is_reading:
            self.log_info(f"slot[{self.slot_num}] RFID is reading")
            return

        # Truncate existing RFID Tag data
        if self.has_tag_read():
            self._init_tag()

        self._is_reading = True
        self.read_begin_at = time.time()
        self.mms_rfid.read_begin(callback=self.handle_read)
        self.log_info(f"slot[{self.slot_num}] RFID read begin")

        # Activate LED effect
        self.mms_slot.slot_led.activate_marquee()

    def rfid_read_end(self):
        if not self._is_reading:
            self.log_info(f"slot[{self.slot_num}] RFID is not reading")
            return

        self.mms_rfid.read_end()
        self._is_reading = False
        self.read_end_at = time.time()
        self.log_info(f"slot[{self.slot_num}] RFID read end")

        # Deactivate LED effect
        self.mms_slot.slot_led.deactivate_marquee()

    def handle_read(self, data):
        if data:
            self.rfid_read_end()
            self.log_info(f"slot[{self.slot_num}] RFID read: {data}")

            try:
                self.tag_data = json.loads(data)
                self.tag_color = self.tag_data.get("color_code")
                # Alter LED color
                self.mms_slot.slot_led.change_color(self.tag_color)
            except Exception as e:
                self.log_error(
                    f"slot[{self.slot_num}] RFID read tag data error: {e}")

            # Continue delivery
            self.mms_delivery.mms_prepare(self.slot_num)

        elif time.time()-self.read_begin_at > self.read_duration:
            self.rfid_read_end()
            self.log_info(f"slot[{self.slot_num}] RFID read timeout")

            # Continue delivery
            self.mms_delivery.mms_prepare(self.slot_num)

    # ---- Utils ----
    @contextmanager
    def execute(self):
        if self.enable:
            self.rfid_detect_begin()
        try:
            yield
        finally:
            if self.enable:
                if self._is_detecting:
                    self.rfid_detect_end()
                if self._is_reading:
                    self.rfid_read_end()


def load_config(config):
    return MMSSlot(config)
