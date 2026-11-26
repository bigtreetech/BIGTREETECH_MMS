# Adapter of printer's fan
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter
from .gcode import gcode_adapter


class FanAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "fan"

        self._speed_factor = 255
        self._speed_command = "M106"
        # self._turnoff_command = "M106 S0"
        self._turnoff_command = "M107"
        self._turnmax_command = "M106 S255"

        self._speed = None

    def _get_fan(self):
        # return self.safe_get(self._obj_name)
        return self.risk_get(self._obj_name)

    def get_status(self):
        fan = self._get_fan()
        return None if fan is None \
            else fan.get_status(self.reactor.monotonic())

    def get_speed(self):
        status = self.get_status()
        return None if status is None else status.get("speed", None)

    def set_speed(self, speed):
        speed = max(0, min(1, speed))

        # The object style
        # self._get_fan().cmd_M106(
        #     gcode_adapter.easy_gcmd(
        #         command = self._speed_command,
        #         params = {"S":speed * self._speed_factor}
        #     )
        # )

        # Command style: M106 S{speed * 255}
        gcode_adapter.run_command(
            f"{self._speed_command} S{speed * self._speed_factor}", strict=True
        )

    def turn_off(self):
        # M106 S0
        # self.set_speed(0)
        # M107
        gcode_adapter.run_command(self._turnoff_command)

    def turn_max(self):
        # M106 S255
        # self.set_speed(1)
        gcode_adapter.run_command(self._turnmax_command)

    def pause(self):
        if self._speed is None:
            self._speed = self.get_speed()
            self.turn_off()
            return True
        return False

    def resume(self):
        if self._speed is not None:
            self.set_speed(self._speed)
            self._speed = None
            return True
        # Default turn max
        self.turn_max()
        return False


# Global instance for singleton
fan_adapter = FanAdapter()
