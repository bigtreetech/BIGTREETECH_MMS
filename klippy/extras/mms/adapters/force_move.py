# Adapter of printer's force_move
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from ...force_move import calc_move_time

from .base import BaseAdapter


class ForceMoveAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "force_move"

    def _get_force_move(self):
        return self.safe_get(self._obj_name)

    def get_mcu_stepper(self, stepper_name):
        return self._get_force_move().lookup_stepper(stepper_name)

    def calc_move_time(self, distance, speed, accel):
        return calc_move_time(distance, speed, accel)


# Global instance for singleton
force_move_adapter = ForceMoveAdapter()
