# Adapter of printer's GCodeMove
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter
from .gcode import gcode_adapter


class GCodeMoveAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "gcode_move"

        self._absolute_extrude = None
        self._absolute_coordinates = None

    def _get_gcode_move(self):
        return self.safe_get(self._obj_name)

    # ---- Status ----
    def get_current_status(self):
        """
        Example:
        {
            'speed_factor': 1.0,
            'speed': 1500.0,
            'extrude_factor': 1.0,
            'absolute_coordinates': True,
            'absolute_extrude': True,
            'homing_origin': Coord(x=0.0, y=0.0, z=0.0, e=0.0),
            'position': Coord(x=0.0, y=0.0, z=0.0, e=0.0),
            'gcode_position': Coord(x=0.0, y=0.0, z=0.0, e=0.0)
        }

        Coord() is collections.namedtuple
            get("position")._asdict().get("e")
        """
        return self._get_gcode_move().get_status(self.reactor.monotonic())

    def get_toolhead_position(self):
        # Param["position"] is the absolute position
        pos = self.get_current_status().get("position")
        return {
            "x" : pos.x,
            "y" : pos.y,
            "z" : pos.z,
            "e" : pos.e
        }

    # ---- Move meta ----
    # G91
    def disable_absolute_coordinates(self):
        self._get_gcode_move().absolute_coord = False

    # G90
    def enable_absolute_coordinates(self):
        self._get_gcode_move().absolute_coord = True

    # M83
    def disable_absolute_extrude(self):
        self._get_gcode_move().absolute_extrude = False

    # M82
    def enable_absolute_extrude(self):
        self._get_gcode_move().absolute_extrude = True

    def save_absolute_extrude(self):
        status = self._get_gcode_move().get_status(self.reactor.monotonic())
        self._absolute_extrude = status.get("absolute_extrude")

    def restore_absolute_extrude(self):
        if self._absolute_extrude is not None:
            self._get_gcode_move().absolute_extrude = self._absolute_extrude
            self._absolute_extrude = None

    def save_absolute_coordinates(self):
        status = self._get_gcode_move().get_status(self.reactor.monotonic())
        self._absolute_coordinates = status.get("absolute_coordinates")

    def restore_absolute_coordinates(self):
        if self._absolute_coordinates is not None:
            self._get_gcode_move().absolute_coord = self._absolute_coordinates
            self._absolute_coordinates = None

    def pause_move_absolute(self):
        self.save_absolute_extrude()
        self.save_absolute_coordinates()

        self.disable_absolute_extrude()
        self.disable_absolute_coordinates()

    def resume_move_absolute(self):
        # if not self.absolute_coordinates:
        #     self.disable_absolute_coordinates()
        # if self.absolute_extrude:
        #     self.enable_absolute_extrude()
        self.restore_absolute_coordinates()
        self.restore_absolute_extrude()

    def g1(self, params):
        return self._get_gcode_move().cmd_G1(
            gcode_adapter.easy_gcmd(params)
        )


# Global instance for singleton
gcode_move_adapter = GCodeMoveAdapter()
