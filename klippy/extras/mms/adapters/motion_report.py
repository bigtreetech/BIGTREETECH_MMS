# Adapter of printer's motion_report
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import chelper

from .base import BaseAdapter


class MotionReportAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "motion_report"

    def _get_motion_report(self):
        return self.safe_get(self._obj_name)

    def get_current_status(self):
        """
        {
            # toolhead position interpolated to the current time
            # Coord is a collections.namedtuple('Coord', ('x', 'y', 'z', 'e'))
              from gcode.py
            'live_position': Coord(x=0.0, y=0.0, z=0.0, e=0.0),
            # toolhead velocity (in mm/s) at the current time
            'live_velocity': 0.0,
            # extruder velocity (in mm/s) at the current time
            'live_extruder_velocity': 0.0,

            'steppers': [
                'extruder',
                'manual_stepper drive_stepper',
                'manual_stepper selector_stepper',
                'stepper_x', 'stepper_y',
                'stepper_z', 'stepper_z1', 'stepper_z2', 'stepper_z3'
            ],
            'trapq': ['extruder', 'toolhead']
        }

        Notice:
            The motion_report refresh data in STATUS_REFRESH_TIME period,
            which default is 0.250 second.
        """
        return self._get_motion_report().get_status(self.reactor.monotonic())

    def get_extruder_position(self):
        # Default is 0
        return self.get_current_status().get("live_position").e

    def get_extruder_velocity(self):
        # Default is None, change to 0
        return self.get_current_status().get("live_extruder_velocity") or 0

    def get_trapq(self):
        return self._get_motion_report().trapqs

    def get_steppers(self):
        return self._get_motion_report().steppers

    def get_extruder_trapq(self, extruder_name):
        assert extruder_name in self.get_current_status().get("trapq"), \
            f"extruder:'{extruder_name}'"\
            f" not found in motion_report:{self.get_current_status()}"

        dump_trapq = self.get_trapq().get(extruder_name)
        return dump_trapq

    def get_extruder_pos_vel(self, extruder_name, print_time):
        dump_trapq = self.get_extruder_trapq(extruder_name)

        # 'pos' is a tuple like: (10.000000000000002, 0.0, 0.0)
        # For extruder, it just need the pos[0]
        # Would return None, None if no trapq data
        pos, velocity = dump_trapq.get_trapq_position(print_time)
        p = pos[0] if pos else None
        v = velocity or 0
        return p, v

    def get_extruder_move(self, extruder_name, print_time):
        """
        klippy/extras/motion_report.py
        DumpTrapQ.get_trapq_position()
        """
        dump_trapq = self.get_extruder_trapq(extruder_name)

        ffi_main, ffi_lib = chelper.get_ffi()
        data = ffi_main.new("struct pull_move[1]")
        count = ffi_lib.trapq_extract_old(
            dump_trapq.trapq,   # trapq
            data,               # pull_move
            1,                  # max
            # 3,                  # max
            0.,                 # start_time
            print_time          # end_time
        )

        if not count:
            return {}

        move = data[0]

        moved_time = max(
            0, min(move.move_t, print_time-move.print_time)
        )
        moved_distance = (
            move.start_v + 0.5 * move.accel * moved_time
        ) * moved_time

        current_position = (
            move.start_x + move.x_r * moved_distance,
            move.start_y + move.y_r * moved_distance,
            move.start_z + move.z_r * moved_distance
        )
        current_velocity = move.start_v + move.accel * moved_time

        move_dct = {
            "move_time": move.move_t,
            "accel": move.accel,
            "direction": (move.x_r, move.y_r, move.z_r),

            "start_print_time": move.print_time,
            "start_position": (move.start_x, move.start_y, move.start_z),
            "start_velocity": move.start_v,

            "moved_time": moved_time,
            "moved_distance": moved_distance,
            "current_position": current_position,
            "current_velocity": current_velocity,
        }
        return move_dct

    def get_extruder_step_queue(self, extruder_name, start_clock, end_clock):
        assert extruder_name in self.get_current_status().get("steppers"), \
            f"extruder:'{extruder_name}'"\
            f" not found in motion_report:{self.get_current_status()}"

        dump_stepper = self.get_steppers().get(extruder_name)

        # 'count' is equal to or less than 128
        data, count = dump_stepper.get_step_queue(start_clock, end_clock)
        return data if not data else {}


# Global instance for singleton
motion_report_adapter = MotionReportAdapter()
