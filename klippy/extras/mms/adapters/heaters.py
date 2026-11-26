# Adapter of printer's Heaters
#
# Copyright (C) 2025-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter


class HeatersAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "heaters"

    def _get_heaters(self):
        return self.safe_get(self._obj_name)

    def find_heater(self, heater_name):
        return self._get_heaters().lookup_heater(heater_name)

    def set_temperature(self, heater, temp, wait=True, check=None):
        if check is None:
            self._get_heaters().set_temperature(heater, temp, wait)
            return
        check()
        self._get_heaters().set_temperature(heater, temp, False)
        if not wait or not temp:
            return
        if self.printer.get_start_args().get("debugoutput") is not None:
            return
        # Public Heater.check_busy works on both supported Klipper versions.
        # Keep the owning MMS operation cancellable without patching Klipper's
        # global heater wait or adding a second temperature controller.
        while True:
            check()
            now = self.reactor.monotonic()
            if not heater.check_busy(now):
                return
            self.reactor.pause(now + 0.25)

    def turn_off_all_heaters(self):
        self._get_heaters().turn_off_all_heaters()


# Global instance for singleton
heaters_adapter = HeatersAdapter()
