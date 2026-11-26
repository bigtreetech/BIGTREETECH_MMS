# Adapter of printer's buttons
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter


class ButtonsAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "buttons"

    def _get_buttons(self):
        return self.safe_get(self._obj_name)

    def register_buttons(self, mcu_pin, callback):
        self._get_buttons().register_buttons([mcu_pin], callback)

    def register_sensor(
        self, config, pin, callback, analog_range=None, pullup=None
    ):
        # Keep ADC sampling and debounce inside Klipper across core versions.
        buttons = self.printer.load_object(config, "buttons")
        if analog_range is None:
            buttons.register_debounce_button(pin, callback, config)
        else:
            buttons.register_debounce_adc_button(
                pin, *analog_range, pullup, callback, config)


# Global instance for singleton
buttons_adapter = ButtonsAdapter()
