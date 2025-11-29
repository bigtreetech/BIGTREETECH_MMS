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


# Global instance for singleton
buttons_adapter = ButtonsAdapter()
