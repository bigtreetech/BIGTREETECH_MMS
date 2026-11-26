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
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager
from dataclasses import dataclass, field

from .config import PrinterConfig
from .exceptions import MMSException
from .filament_metadata import normalize_filament
from .slot_led import SlotLED
from .slot_meta import SlotMeta
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
from ..adapters import (
    moonraker_adapter,
    printer_adapter
)


@dataclass(frozen=True)
class PrinterSlotConfig(PrinterConfig):
    """ Configuration values in mms-slot.cfg """
    # Pins
    selector: str = ""
    inlet: str = ""
    gate: str = ""
    # LED
    led_name: str = ""
    chip_index: list = field(default_factory=list)
    brightness: float = 0.5
    # Autoload
    autoload_enable: int = 1
    # RFID
    rfid_name: str = ""
    rfid_enable: int = 0
    rfid_detect_duration: float = 50  # seconds
    rfid_read_duration: float = 4  # seconds
    # Endless spool
    endless_with_slot: int = 0    # slot number
    # Selector refine calibration
    selector_calibrate_distance: float = 2.0  # mm


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

        # Extend support
        self._is_extended = False
        self._extend_num = None
        # Delivery support
        self._can_update_deliver_distance = False
        # Withdrawn short tails may still cover Inlet. Runtime-only state.
        self.cleanup_required = False
        self._last_empty_state = None

        self._is_ready = False

        # Initialize Pins
        self._initialize_pins()

        # Register connect handler to printer
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

    def _handle_klippy_connect(self):
        self._initialize_loggers()
        self._initialize_led()
        self._initialize_rfid()
        self._initialize_meta()

    def _initialize_meta(self):
        self.meta = SlotMeta()
        # self.meta.pin_selector = self.slot_config.selector
        # self.meta.pin_inlet = self.slot_config.inlet
        # self.meta.pin_gate = self.slot_config.gate

        # File operations in SlotMeta log through the slot logger
        self.meta.log_error = self.log_error

        self.meta.num = self.num

        self.meta.mms_buffer_index = self.mms_buffer.get_index()
        self.meta.mms_selector_index = self.mms_selector.get_index()
        self.meta.mms_drive_index = self.mms_drive.get_index()

        self.meta.mms_buffer = self.mms_buffer
        self.meta.mms_selector = self.mms_selector
        self.meta.mms_drive = self.mms_drive

        self.meta.is_extended = self._is_extended
        self.meta.extend_num = self._extend_num

        self.meta.selector = self.selector
        self.meta.inlet = self.inlet
        self.meta.gate = self.gate
        self.meta.outlet = self.outlet
        self.meta.buffer_runout = self.buffer_runout
        self.meta.entry = self.entry

        cfg_path = printer_adapter.get_klippy_configfile()
        self.meta.set_cfg_path(cfg_path)

    def _handle_klippy_ready(self):
        self.reactor.register_timer(
            callback=self._init_led_notify,
            waketime=self.reactor.monotonic()+self.led_notify_delay
        )
        # Register led effect deactivate callback
        printer_adapter.register_mms_stepper_running(
            handler=self._handle_mms_stepper_running)

        self._last_empty_state = self.is_empty()
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

    def _init_led_notify(self, eventtime):
        self.slot_led.notify()
        return self.reactor.NEVER

    # ---- MMS Setup ----
    def set_mms_buffer(self, mms_buffer):
        # MMS Buffer
        self.mms_buffer = mms_buffer
        self.mms_buffer.register_slot_num(self.num)

    def set_buffer_runout(self, buffer_runout):
        self.buffer_runout.set_pin_obj(buffer_runout)

    def set_outlet(self, outlet):
        self.outlet.set_pin_obj(outlet)

    def set_entry(self, entry):
        self.entry.set_pin_obj(entry)

    def set_mms_selector(self, mms_selector):
        # MMS Stepper
        self.mms_selector = mms_selector
        # Setup mcu_stepper for mcu_endstop
        self.selector.set_stepper(self.mms_selector)

    def set_mms_drive(self, mms_drive):
        # MMS Stepper
        self.mms_drive = mms_drive
        # Setup mcu_stepper for mcu_endstop
        self.inlet.set_stepper(self.mms_drive)
        self.gate.set_stepper(self.mms_drive)
        self.buffer_runout.set_stepper(self.mms_drive)
        self.outlet.set_stepper(self.mms_drive)
        self.entry.set_stepper(self.mms_drive)

    # ---- Get properties ----
    def get_num(self):
        return self.num

    def autoload_is_enabled(self):
        return bool(self.slot_config.autoload_enable)

    # def get_rfid_status(self):
    #     return self.slot_rfid.get_status()

    def get_mms_selector(self):
        return self.mms_selector

    def get_mms_drive(self):
        return self.mms_drive

    def get_mms_buffer(self):
        return self.mms_buffer

    def is_extended(self):
        return self._is_extended

    def get_extend_num(self):
        return self._extend_num

    def get_endless_with_slot(self):
        return self.slot_config.endless_with_slot

    def get_selector_calibrate_distance(self):
        return self.slot_config.selector_calibrate_distance

    # ---- RFID support ----
    # def rfid_is_enabled(self):
    #     return self.slot_config.rfid_enable

    # def rfid_is_detecting(self):
    #     return self.slot_rfid.is_detecting() or \
    #         self.slot_rfid.mms_rfid.is_detecting()

    # ---- MMS support ----
    def mark_is_extended(self, extend_num):
        self._is_extended = True
        self._extend_num = extend_num

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
    def get_deliver_distance(self, pin_type, forward):
        return self.meta.get_deliver_distance(pin_type, forward)

    def set_deliver_distance(self, pin_type, forward, distance):
        if self._can_update_deliver_distance:
            self.meta.set_deliver_distance(pin_type, forward, distance)
            return True
        return False

    @contextmanager
    def update_deliver_distance(self, replace=False):
        previous = self.meta.deliver_vector
        if replace:
            self.meta.deliver_vector = {}
        self._can_update_deliver_distance = True
        try:
            yield
        except BaseException:
            self.meta.deliver_vector = previous
            raise
        finally:
            self._can_update_deliver_distance = False

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
        if slot_pin is None:
            # Not pin is waiting
            self.log_warning(f"slot[{self.num}] is not homing")
            return

        # Stop homing
        if slot_pin.break_endstop_homing():
            self.log_info_s(
                f"slot[{self.num}] '{slot_pin.get_mms_name()}' "
                "homing is stopped"
            )

    # ---- MMS Stepper support ----
    def complete_selector_moving(self):
        if self.mms_selector and self.mms_selector.is_running():
            self.mms_selector.complete_manual_home()

    def complete_drive_moving(self):
        if self.mms_drive and self.mms_drive.is_running():
            self.mms_drive.complete_manual_home()

    # ---- MMS Exceptions support ----
    def handle_mms_exception_raised(self, exception):
        self.log_info_s(f"slot[{self.num}] receive exception: {exception}")

        # Activate LED effect
        self.slot_led.activate_blinking()

    def _handle_mms_stepper_running(self):
        self.slot_led.deactivate_blinking()
        # Remove handler
        # printer_adapter.register_mms_stepper_running(handler=None)

    # ---- Log ----
    def format_pins_status(self):
        info = f"slot[{self.num}] "
        info += f"selector={1 if self.selector.is_triggered() else 0} "
        info += f"inlet={1 if self.inlet.is_triggered() else 0} "
        info += f"gate={1 if self.gate.is_triggered() else 0} "
        info += f"runout={1 if self.buffer_runout.is_triggered() else 0} "
        info += f"outlet={1 if self.outlet.is_triggered() else 0} "
        if self.entry.is_set():
            info += f"entry={1 if self.entry.is_triggered() else 0} "
        info += f"cleanup_required={int(self.cleanup_required)} "
        info += "\n"
        return info

    def format_deliver_distance(self):
        info = f"slot[{self.num}] "

        f_pin = self.pin_type.entry \
            if self.entry.is_set() else self.pin_type.outlet
        b_pin = self.pin_type.gate

        # Deliver forward to trigger Entry
        f_dist = self.meta.get_deliver_distance(f_pin, forward=True)
        # Deliver backward to release Entry
        b_dist = self.meta.get_deliver_distance(b_pin, forward=False)

        info += f"{b_pin} to {f_pin} "
        info += f"↓={f_dist or 0:.2f}mm/↑={b_dist or 0:.2f}mm"
        info += "\n"
        return info

    # ---- Status ----
    def get_status(self, eventtime=None):
        if not self._is_ready:
            return {}
        return dict(self.meta.report(), cleanup_required=self.cleanup_required,
                    is_empty=self.is_empty())

    def mark_tail_withdrawn(self):
        """Observe local sensors only after the withdrawal has drained."""
        try:
            self.inlet.refresh_state()
            self.gate.refresh_state()
        finally:
            # Earlier runout/retraction edges cannot count as manual removal.
            # Keep the exclusion if a final sensor query fails.
            self.cleanup_required = True
        self.update_cleanup_state()
        if self.cleanup_required:
            self.log_info(
                f"slot[{self.num}] withdrawn remnant needs removal; "
                "clear Inlet and Gate before inserting new filament")

    def update_cleanup_state(self):
        if (self.cleanup_required and self.inlet.is_released()
                and self.gate.is_released()):
            self.cleanup_required = False

    def check_load_allowed(self):
        if self.cleanup_required:
            raise MMSException(
                f"slot[{self.num}] needs remnant removal; clear Inlet and "
                "Gate, then insert new filament and retry or RESUME", self)

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

    def is_new_insert(self):
        return self.inlet.is_new_triggered()

    def selector_is_triggered(self):
        return self.selector.is_triggered()

    def entry_is_set(self):
        return self.entry.is_set()

    def entry_is_triggered(self):
        return self.entry.is_set() and self.entry.is_triggered()

    def is_empty(self):
        # Inlet and Gate belong to one slot. Entry is shared, so its state
        # may retain an accepted tail only for the slot that owns that tail.
        if not (self.inlet.is_released() and self.gate.is_released()):
            return False
        if printer_adapter.get_mms_charge().get_charged_slot() != self.num:
            return True
        # Without Entry, neither upstream switch can prove that the
        # accepted tail has cleared the toolhead.
        if not self.entry_is_set():
            return False
        return self.entry.is_released()

    def update_filament_presence(self, *, pin_changed=False):
        empty = self.is_empty()
        was_empty = self._last_empty_state
        self._last_empty_state = empty
        if empty and was_empty is False:
            self.update_filament_metadata({}, replace=True)
        # A Gate or Inlet edge can change the status color within an
        # occupied slot. A downstream tail can clear without either edge.
        if pin_changed or empty != was_empty:
            self.slot_led.notify()

    # ---- Slot Meta support ----
    def update_filament_metadata(self, patch, replace=False):
        self.meta.update_filament(patch, replace)
        moonraker_adapter.changed(self.num, patch, replace)

    def apply_rfid_metadata(self, tag):
        patch = {
            "vendor_name": tag.get("filament_manufacturer"),
            "name": tag.get("filament_type_detailed") or tag.get("color_name_a"),
            "material": tag.get("filament_material_type"),
            "color": tag.get("color_code"),
            "bed_temp": (tag.get("bed_temperature")
                         or tag.get("bed_temerature_max")
                         or tag.get("bed_temerature_min")),
            "nozzle_temp": (tag.get("printing_temperature_min")
                            or tag.get("printing_temperature_max")),
            "filament_id": tag.get("filament_id"),
        }
        self.update_filament_metadata(normalize_filament(
            {key: value for key, value in patch.items()
             if value is not None}))

    def truncate_meta(self):
        self.update_filament_metadata({}, replace=True)
        self.meta.truncate_deliver_distance()
        self.log_info_s(
            f"slot[{self.num}] filament/distance meta are truncated")


def load_config(config):
    return MMSSlot(config)
