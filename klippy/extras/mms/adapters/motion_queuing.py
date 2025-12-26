# Adapter of printer's motion_queuing
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter
from .force_move import force_move_adapter


class MotionQueuingAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "motion_queuing"

    def _get_motion_queuing(self):
        return self.safe_get(self._obj_name)

    # ---- Base trapq control ----
    def allocate_trapq(self):
        return self._get_motion_queuing().\
            allocate_trapq()

    def lookup_trapq_append(self):
        return self._get_motion_queuing().\
            lookup_trapq_append()

    def wipe_trapq(self, trapq):
        # Finalize exists moves in queue to stop stepper
        return self._get_motion_queuing().\
            wipe_trapq(trapq)

    def expire_trapq(self, trapq, print_time):
        # Expire any moves older than print_time from the trapq
        return self._get_motion_queuing().\
            trapq_finalize_moves(trapq, print_time, 0.)

    def note_mcu_movequeue_activity(self, end_print_time):
        return self._get_motion_queuing().\
            note_mcu_movequeue_activity(end_print_time)

    def flush_all_steps(self):
        return self._get_motion_queuing().\
            flush_all_steps()

    def drip_update_time(self, start_time, end_time, drip_completion):
        return self._get_motion_queuing().\
            drip_update_time(start_time, end_time, drip_completion)

    # ---- Advanced trapq control ----
    def setup_trapq(self, trapq, print_time, distance, speed, accel):
        """Configure trap queue with movement parameters"""
        # Calculate move params with distance
        axis_r, accel_t, cruise_t, cruise_v = force_move_adapter.\
            calc_move_time(distance, speed, accel)
        # accel_t plus twice because accel_t is equal to decel_t
        end_print_time = print_time+accel_t+cruise_t+accel_t

        # Wipe old movement in trapq
        self.wipe_trapq(trapq)

        # Append params to trapq
        trapq_append = self.lookup_trapq_append()
        trapq_append(
            trapq,          # trapq
            print_time,     # print_time

            accel_t,        # accel_t
            cruise_t,       # cruise_t
            accel_t,        # decel_t

            0.,             # start_pos_x
            0.,             # start_pos_y
            0.,             # start_pos_z

            axis_r,         # axes_r_x
            0.,             # axes_r_y
            0.,             # axes_r_z

            0.,             # start_v
            cruise_v,       # cruise_v
            accel           # accel
        )
        return end_print_time

    def process_trapq(self, trapq, end_print_time):
        # Calculate time and update timer
        self.note_mcu_movequeue_activity(end_print_time)
        # Flush to generate steps and wait movement finish
        # self.flush_all_steps()
        # Wipe old movement in trapq
        # self.wipe_trapq(trapq)

    def move(self, trapq, print_time, distance, speed, accel):
        end_print_time = self.setup_trapq(
            trapq, print_time, distance, speed, accel)
        self.process_trapq(trapq, end_print_time)
        return end_print_time


# Global instance for singleton
motion_queuing_adapter = MotionQueuingAdapter()
