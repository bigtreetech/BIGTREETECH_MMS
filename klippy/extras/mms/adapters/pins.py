# Adapter of printer's pins
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass

from .base import BaseAdapter


# Enum pin_type from klippy/pins.py
@dataclass(frozen=True)
class PinType:
    endstop: str = "endstop"
    digital_out: str = "digital_out"
    pwm: str = "pwm"
    adc: str = "adc"


class PinsAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "pins"
        self.pin_type = PinType()

    def _get_pins(self):
        return self.safe_get(self._obj_name)

    def allow_multi_use_pin(self, mcu_pin):
        pin_desc = mcu_pin[1:] if mcu_pin.startswith("!") else mcu_pin
        self._get_pins().allow_multi_use_pin(pin_desc)
        # self._get_pins().allow_multi_use_pin(mcu_pin)

    def get_pin_params(self, mcu_pin):
        # self._get_pins().parse_pin(mcu_pin, can_invert=True, can_pullup=True)
        return self._get_pins().lookup_pin(mcu_pin, can_invert=True)

    def get_mcu(self, mcu_pin):
        return self.get_pin_params(mcu_pin)["chip"]

    def setup_mcu_endstop(self, mcu_pin):
        mcu_endstop = self._get_pins().setup_pin(self.pin_type.endstop, mcu_pin)
        return mcu_endstop

    def setup_adc(self, mcu_pin):
        mcu_adc = self._get_pins().setup_pin(self.pin_type.adc, mcu_pin)
        return mcu_adc


# Global instance for singleton
pins_adapter = PinsAdapter()
