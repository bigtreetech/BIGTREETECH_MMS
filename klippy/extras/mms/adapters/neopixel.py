# Adapter of printer's Neopixel
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter
from .printer import printer_adapter


class NeopixelAdapter(BaseAdapter):
    def __init__(self, led_name):
        super().__init__()
        self.led_name = led_name

    def _setup_logger(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_error = mms_logger.create_log_error(console_output=True)

    def _get_neopixel(self):
        return self.safe_get(self.led_name)

    def get_color_data(self):
        # neopixel.get_status() return a dict:
        # {'color_data': [(red, green, blue, white)] * led_count}
        # "led_count" is the "chain_count" set in config
        return self._get_neopixel().get_status().get("color_data")

    def update_leds(self, color_data):
        # Attempt to transmit the updated LED colors
        try:
            self._get_neopixel().update_leds(
                led_state=color_data, print_time=None)
        except Exception as e:
            # self.log_error(f"mms neopixel update error: {e}")
            pass


class NeopixelDispatch:
    def __init__(self):
        # key: led_name
        # val: NeopixelAdapter()
        self.np_adapter_dct = {}

    def get_adapter(self, led_name):
        if led_name in self.np_adapter_dct:
            return self.np_adapter_dct.get(led_name)

        neopixel_adapter = NeopixelAdapter(led_name)
        self.np_adapter_dct[led_name] = neopixel_adapter
        return neopixel_adapter


# Global instance for singleton
neopixel_dispatch = NeopixelDispatch()
