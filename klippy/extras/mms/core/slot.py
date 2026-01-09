# Support for MMS SLOT
#
#                     +---------------------+
#                     | Toolhead            |
#                     |     +---------+     |
#                     |     |  Entry  |     |
#                     +-----+---+ +---+-----+
#                               | |
#                           +---+ +---+
#                           |  Outlet |
#                           |    |    |-Buffer
#                           |  Runout |
#                           +---+ +---+
#     +------------------------/   \------------------------+
#    /            \                                           \
#   +------------+ +------------+ +------------+ +------------+
#   | +--------+ | | +--------+ | | +--------+ | | +--------+ |
#   | |  Gate  | | | |  Gate  | | | |  Gate  | | | |  Gate  | |  +----------+
#   | +--------+ | | +--------+ | | +--------+ | | +--------+ |  | MMS RFID |
#   | +--------+ | | +--------+ | | +--------+ | | +--------+ |  +----++----+
#   | |Selector| | | |Selector| | | |Selector| | | |Selector| |       ||
#   | +--------+ | | +--------+ | | +--------+ | | +--------+ |  +----++----+
#   | +--------+ | | +--------+ | | +--------+ | | +--------+ |--| SlotRFID |
#   | | Inlet  | | | | Inlet  | | | | Inlet  | | | | Inlet  | |  +----------+
#   | +--------+ | | +--------+ | | +--------+ | | +--------+ |
#   |            | |            | |            | |            |  +---------+
#   |   SLOT 0   | |   SLOT 1   | |   SLOT 2   | |   SLOT 3   |--| SlotLED |
#   +------------+ +------------+ +------------+ +------------+  +----++---+
#                                                                     ||
#                                                                +----++---+
#                                                                | MMS LED |
#                                                                +---------+
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass, field

from .config import OptionalField, PrinterConfig
from .slot_led import SlotLED
from .slot_pin import (
    PinType,
    PinState,
    SlotPinBufferRunout,
    SlotPinEntry,
    SlotPinGate,
    SlotPinInlet,
    SlotPinOutlet,
    SlotPinSelector
)
from .slot_rfid import SlotRFID
from ..adapters import printer_adapter


@dataclass(frozen=True)
class PrinterSlotConfig(PrinterConfig):
    """ Configuration values in mms-slot.cfg """
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

    # The optional slot_num configured for substitute
    # Substitute current slot with another one
    substitute_with: OptionalField = ""


class MMSSlot:
    """
    A class to represent a Multi-Material System (MMS) SLOT.
    The configuration section for the current slot, e.g., [mms slot 0]
    """
    def __init__(self, config):
        p_slot_config = PrinterSlotConfig(config)
        self.slot_config = p_slot_config.gen_packaged_config()

        self.reactor = printer_adapter.get_reactor()

        self.name = config.get_name()
        self.num = int(self.name.split()[-1])
        self.led_notify_delay = 2.5 + 0.2*self.num

        self.pin_type = PinType()
        self.pin_state = PinState()

        # LED, init after klippy is connected
        self.slot_led = None
        # RFID, init after klippy is connected
        self.slot_rfid = None
        # Slot number of self substitute with
        self.substitute_with = None

        self._is_ready = False

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
        self.mms_buffer.register_slot_num(self.num)

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

        self._initialize_substitute()

        self._is_ready = True

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_s = mms_logger.create_log_info(console_output=False)

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

    def _initialize_substitute(self):
        config_slot_num = self.slot_config.substitute_with
        if config_slot_num is None \
            or not config_slot_num.isdigit():
            return
        slot_num = int(config_slot_num)
        if not self.mms.slot_is_available(slot_num) \
            or slot_num == self.num:
            return
        self.substitute_with = slot_num

    def _init_led_notify(self, eventtime):
        self.slot_led.notify()
        return self.reactor.NEVER

    def get_status(self, eventtime=None):
        if not self._is_ready:
            return {}

        return {
            "rfid" : self.slot_rfid.get_status(),
        }

    # ---- Get properties ----
    def get_num(self):
        return self.num

    def autoload_is_enabled(self):
        return bool(self.slot_config.autoload_enable)

    def get_rfid_status(self):
        return self.slot_rfid.get_status()

    def get_mms_selector(self):
        return self.mms_selector

    def get_mms_drive(self):
        return self.mms_drive

    def get_mms_buffer(self):
        return self.mms_buffer

    def get_substitute_with(self):
        return self.substitute_with

    # ---- MMS support ----
    def get_mms_slot_pin(self, pin_type):
        return self.slot_pin_map.get(pin_type, None)

    def find_waiting(self, mcu_pin, pin_type, pin_state):
        slot_pin = self.get_mms_slot_pin(pin_type)
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
        slot_pin = self.get_mms_slot_pin(pin_type)
        return slot_pin.wait_callback if slot_pin else None

    def check_pin(self, pin_type, trigger):
        slot_pin = self.get_mms_slot_pin(pin_type)
        if not slot_pin:
            return None
        return slot_pin.is_triggered() if trigger else slot_pin.is_released()

    def format_endstop_pair(self, pin_type):
        slot_pin = self.get_mms_slot_pin(pin_type)
        return [
            (slot_pin.get_endstop(), slot_pin.get_mcu_pin()),
        ] if slot_pin else []

    def format_endstop_pairs(self, pin_type_lst):
        pair_lst = []
        for pin_type in pin_type_lst:
            slot_pin = self.get_mms_slot_pin(pin_type)
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
                self.log_info_s(
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
        self.log_info_s(f"slot[{self.num}] receive exception: {exception}")

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

    # Inlet and gate and (outlet or entry) all triggered
    def is_full(self):
        lst = [
            self.inlet.is_triggered(),
            self.gate.is_triggered()
        ]
        if self.entry.is_set():
            lst.append(self.entry.is_triggered())
        else:
            lst.append(self.outlet.is_triggered())
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

    def entry_is_triggered(self):
        return self.entry.is_set() and self.entry.is_triggered()


def load_config(config):
    return MMSSlot(config)
