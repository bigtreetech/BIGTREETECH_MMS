# Support for MMS Button
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from collections import deque
from dataclasses import dataclass

from ..adapters import (
    buttons_adapter,
    pins_adapter,
    printer_adapter,
    query_endstops_adapter,
)


@dataclass(frozen=True)
class ButtonState:
    trigger: int = 1
    release: int = 0


class MMSButton:
    def __init__(self, mcu_pin):
        self.mcu_pin = mcu_pin
        self.mms_name = None
        self._parse_mcu_pin()
        self._initialize_state()
        self._initialize_hardware()
        self._initialize_loggers()

    # ==== Initialization ====
    def _parse_mcu_pin(self):
        """Parse and validate MCU pin configuration"""
        # Process pin format (e.g., "buffer:PA4")
        # mcu_pin -> "!buffer:PA4"
        self.invert = self.mcu_pin.startswith("!")
        # chip_pin -> "buffer:PA4"
        chip_pin = self.mcu_pin.lstrip("!")
        # chip_name -> "buffer"
        self.chip_name = chip_pin.split(':')[0]
        # pin -> "PA4"
        self.pin = chip_pin.split(':')[-1]

    def _initialize_state(self):
        """Initialize state tracking variables"""
        self.state_trigger = ButtonState().trigger
        self.state_release = ButtonState().release

        # Current state
        # Init state for button, default is released
        self.state = self.state_release
        # Previous state
        self.state_prev = None
        # self.last_trigger_at = 0
        # self.last_release_at = 0

        self.trigger_callbacks = deque()
        self.release_callbacks = deque()

    def _initialize_hardware(self):
        """Configure MCU Buttons hardware interface"""
        pins_adapter.allow_multi_use_pin(self.mcu_pin)
        self.mcu = pins_adapter.get_mcu(self.mcu_pin)
        self.mcu_endstop = pins_adapter.setup_mcu_endstop(self.mcu_pin)

        buttons_adapter.register_buttons(
            self.mcu_pin, self.handle_state_updated)

        query_endstops_adapter.register_endstop(
            self.mcu_endstop, self.mcu_pin)

    def handle_state_updated(self, eventtime, state):
        # self.log_info(f"{self.mcu_pin} state:{state}"
        #               f" state_t:{self.state_trigger}")
        if state == self.state_trigger:
            self.trigger()
        else:
            self.release()

    def _initialize_loggers(self):
        return
        # mms_logger = printer_adapter.get_mms_logger()
        # self.log_info = mms_logger.create_log_info(console_output=False)
        # self.log_warning = mms_logger.create_log_warning()

    # ==== Configuration ====
    def register_trigger_callback(self, callback, params=None):
        # self.trigger_callbacks.appendleft((callback, params))
        self.trigger_callbacks.append((callback, params))

    def unregister_trigger_callback(self, callback):
        self.trigger_callbacks = deque(
            tup for tup in self.trigger_callbacks
            if tup[0] != callback)

    def register_release_callback(self, callback, params=None):
        # self.release_callbacks.appendleft((callback, params))
        self.release_callbacks.append((callback, params))

    def unregister_release_callback(self, callback):
        self.release_callbacks = deque(
            tup for tup in self.release_callbacks
            if tup[0] != callback)

    def set_stepper(self, mcu_stepper):
        if self.mcu_endstop:
            self.mcu_endstop.add_stepper(mcu_stepper)

    def get_endstop(self):
        return self.mcu_endstop

    def get_mcu_pin(self):
        return self.mcu_pin

    # ==== State Transition Methods ====
    def _update_state(self, new_state):
        """Update state with history tracking"""
        self.state_prev = self.state
        self.state = new_state

    def trigger(self):
        """Handle trigger state transition"""
        self._update_state(self.state_trigger)
        # self.last_trigger_at = time.time()
        if self.is_new_triggered() and self.trigger_callbacks:
            for cb,p in self.trigger_callbacks:
                if p:
                    cb(**p)
                else:
                    cb(self.mcu_pin)

    def release(self):
        """Handle release state transition"""
        self._update_state(self.state_release)
        # self.last_release_at = time.time()
        if self.is_new_release() and self.release_callbacks:
            for cb,p in self.release_callbacks:
                if p:
                    cb(**p)
                else:
                    cb(self.mcu_pin)

    # ==== State Query Methods ====
    def is_triggered(self):
        """Check if in triggered state"""
        return self.state == self.state_trigger

    def is_released(self):
        """Check if in released state"""
        return self.state == self.state_release

    def has_changed(self):
        """
        Detect state change from previous value
                    is_triggered    has_changed
        New Trigger     True           True
        New Release     False          True
        """
        return self.state_prev != self.state

    def is_new_triggered(self):
        """Check for new trigger event"""
        return self.is_triggered() and self.has_changed()

    def is_new_release(self):
        """Check for new release event"""
        return self.is_released() and self.has_changed()

    # ==== Status Reporting ====
    def get_state(self):
        # return self.state
        return 1 if self.is_triggered() else 0

    # def format_status(self):
    #     return f"{self.mms_name} pin={self.mcu_pin} state={self.state}"

    def get_mms_name(self):
        return self.mms_name


class MMSButtonSelector(MMSButton):
    def __init__(self, mcu_pin):
        super().__init__(mcu_pin)
        self.mms_name = "selector"


class MMSButtonInlet(MMSButton):
    def __init__(self, mcu_pin):
        super().__init__(mcu_pin)
        self.mms_name = "inlet"


class MMSButtonGate(MMSButton):
    def __init__(self, mcu_pin):
        super().__init__(mcu_pin)
        self.mms_name = "gate"


class MMSButtonOutlet(MMSButton):
    def __init__(self, mcu_pin):
        super().__init__(mcu_pin)
        self.mms_name = "outlet"

    # def format_status(self):
    #     return (f"slot[*] {self.mms_name} pin={self.mcu_pin}"
    #             f" state={self.state}")


class MMSButtonEntry(MMSButton):
    def __init__(self, mcu_pin):
        super().__init__(mcu_pin)
        self.mms_name = "entry"

    # def format_status(self):
    #     return (f"slot[*] {self.mms_name} pin={self.mcu_pin}"
    #             f" state={self.state}")


class MMSButtonBufferRunout(MMSButton):
    def __init__(self, mcu_pin):
        super().__init__(mcu_pin)
        self.mms_name = "buffer_runout"

    def get_callback_register(self):
        return self.set_release_callback
