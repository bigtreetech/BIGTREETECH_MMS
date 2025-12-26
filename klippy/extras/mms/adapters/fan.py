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
        self._speed = None

    def _get_fan(self):
        return self.safe_get(self._obj_name)

    def get_status(self):
        return self._get_fan().get_status(self.reactor.monotonic())

    def get_speed(self):
        return self.get_status().get("speed", 0)

    def set_speed(self, speed):
        # M106 S{fan * 255}
        self._get_fan().cmd_M106(
            gcode_adapter.easy_gcmd(
                command = "M106",
                params = {"S":speed * 255}
            )
        )

    def pause(self):
        if self._speed is None:
            self._speed = self.get_speed()
            # M106 S0
            self.set_speed(0)
            return True
        return False

    def resume(self):
        if self._speed is not None:
            self.set_speed(self._speed)
            self._speed = None
            return True
        return False


# Global instance for singleton
fan_adapter = FanAdapter()
