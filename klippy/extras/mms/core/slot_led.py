# Support for MMS SLOT led
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from ..adapters import printer_adapter
from ..hardware.led import MMSLedEffect, MMSLedEvent


class SlotLED:
    def __init__(self, mms_slot):
        self.mms_slot = mms_slot
        # SLOT meta
        self.slot_num = mms_slot.get_num()

        # Logger
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=False)

        # Current LED brightness
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
