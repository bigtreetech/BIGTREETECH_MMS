# Adapter of printer's GCodeMove
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager

from .base import BaseAdapter
from .gcode import gcode_adapter


class GCodeMoveAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "gcode_move"

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

    # ---- Native motion state ----
    def save_state(self, name):
        self._get_gcode_move().cmd_SAVE_GCODE_STATE(
            gcode_adapter.easy_gcmd("SAVE_GCODE_STATE", {"NAME": name}))

    def restore_state(self, name):
        self._get_gcode_move().cmd_RESTORE_GCODE_STATE(
            gcode_adapter.easy_gcmd(
                "RESTORE_GCODE_STATE", {"NAME": name, "MOVE": 0}))

    def normalize_motion(self):
        # Use Klipper's handlers so old/new extrusion-mode fields stay private.
        move = self._get_gcode_move()
        position = self.get_toolhead_position()
        move.cmd_G90(gcode_adapter.easy_gcmd("G90"))
        move.cmd_M83(gcode_adapter.easy_gcmd("M83"))
        move.cmd_M220(gcode_adapter.easy_gcmd("M220", {"S": 100.}))
        move.cmd_M221(gcode_adapter.easy_gcmd("M221", {"S": 100.}))
        move.cmd_G92(gcode_adapter.easy_gcmd(
            "G92", {axis.upper(): position[axis] for axis in ("x", "y", "z")}))

    @contextmanager
    def filament_move_state(self):
        # Native save/restore handles both old and new extrusion-mode fields
        # and preserves logical E without moving back the physical filament.
        move = self._get_gcode_move()
        name = "__MMS_FILAMENT_MOVE"
        move.cmd_SAVE_GCODE_STATE(gcode_adapter.easy_gcmd(
            "SAVE_GCODE_STATE", {"NAME": name}))
        try:
            move.cmd_M83(gcode_adapter.easy_gcmd("M83"))
            move.cmd_M221(gcode_adapter.easy_gcmd("M221", {"S": 100.}))
            move.cmd_M220(gcode_adapter.easy_gcmd("M220", {"S": 100.}))
            yield
        finally:
            move.cmd_RESTORE_GCODE_STATE(gcode_adapter.easy_gcmd(
                "RESTORE_GCODE_STATE", {"NAME": name, "MOVE": 0}))

    def g1(self, params):
        move = self._get_gcode_move()
        try:
            return move.cmd_G1(
                gcode_adapter.easy_gcmd(command="G1", params=params)
            )
        except Exception:
            # Locally caught native errors bypass gcode:command_error. Match
            # core's reset so a retry cannot include the rejected E/XYZ delta.
            move.reset_last_position()
            raise


# Global instance for singleton
gcode_move_adapter = GCodeMoveAdapter()
