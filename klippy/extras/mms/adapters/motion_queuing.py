# Adapter of printer's motion_queuing
#
# Copyright (C) 2025-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from gcode import CommandError

from ...motion_queuing import (
    DRIP_SEGMENT_TIME,
)

from .base import BaseAdapter
from .force_move import force_move_adapter


class MotionQueuingAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "motion_queuing"

    def _get_motion_queuing(self):
        return self.safe_get(self._obj_name)

    def run_native_motion(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except CommandError as error:
            # Older cores expose this fatal C failure as a plain command error.
            # Match only the native message; sensor/hook errors stay retryable.
            if str(error) == "Internal error in stepcompress":
                printer = self.printer
                if not printer.is_shutdown():
                    printer.invoke_shutdown(str(error))
            raise

    def register_feed_callback(self, callback):
        # Run after toolhead has added E moves, before native step generation.
        self._get_motion_queuing().register_flush_callback(callback)

    def unregister_feed_callback(self, callback):
        self._get_motion_queuing().unregister_flush_callback(callback)

    def get_scan_delay(self):
        return self._get_motion_queuing().get_kin_flush_delay()

    # ---- Base trapq control ----
    def allocate_trapq(self):
        return self._get_motion_queuing().allocate_trapq()

    def lookup_trapq_append(self):
        return self._get_motion_queuing().lookup_trapq_append()

    def wipe_trapq(self, trapq):
        # Finalize exists moves in queue to stop stepper
        return self._get_motion_queuing().wipe_trapq(trapq)

    def expire_trapq(self, trapq, print_time):
        # Expire any moves older than print_time from the trapq
        return self._get_motion_queuing().\
            trapq_finalize_moves(trapq, print_time, 0.)

    def note_mcu_movequeue_activity(self, end_print_time):
        return self._get_motion_queuing().\
            note_mcu_movequeue_activity(end_print_time)

    def flush_all_steps(self):
        return self.run_native_motion(
            self._get_motion_queuing().flush_all_steps)

    def drip_update_time(self, start_time, end_time, drip_completion):
        mq = self._get_motion_queuing()
        try:
            self.run_native_motion(
                mq.drip_update_time, start_time, end_time, drip_completion)
        finally:
            # Core's exception path may leave this invocation registered.
            if start_time in mq.drip_start_times:
                mq.drip_start_times.remove(start_time)
                if not self.printer.is_shutdown():
                    self.reactor.update_timer(mq.flush_timer, self.reactor.NOW)

    def get_last_step_gen_time(self):
        return self._get_motion_queuing().last_step_gen_time

    def calc_step_gen_restart(self, print_time):
        return self._get_motion_queuing().calc_step_gen_restart(print_time)

    def check_step_generation_scan_windows(self):
        return self._get_motion_queuing().check_step_generation_scan_windows()

    # ---- Advanced trapq control ----
    def append_velocity(self, trapq, print_time, position, initial, target,
                        accel, duration=None):
        if duration is None:
            duration = DRIP_SEGMENT_TIME
        delta = target - initial
        ramp = min(duration, abs(delta) / accel) if accel else 0.
        signed_accel = (accel if delta >= 0. else -accel) if ramp else 0.
        final = initial + signed_accel * ramp if ramp else target
        self.lookup_trapq_append()(
            trapq, print_time, ramp, duration - ramp, 0.,
            position, 0., 0., 1., 0., 0.,
            initial if ramp else final, final, signed_accel)
        position += ((initial + final) * .5 * ramp
                     + final * (duration - ramp))
        return print_time + duration, position, final

    def setup_trapq(self, trapq, print_time, distance, speed, accel):
        """Configure trap queue with movement parameters"""
        # Calculate move params with distance
        axis_r, accel_t, cruise_t, cruise_v = force_move_adapter.\
            calc_move_time(distance or 0, abs(speed), abs(accel))

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

        # accel_t plus twice because accel_t is equal to decel_t
        end_print_time = print_time + accel_t + cruise_t + accel_t
        return end_print_time

    def process_trapq(self, trapq, end_print_time):
        # Calculate time and update timer
        self.note_mcu_movequeue_activity(end_print_time)
        # Generate and submit steps; this does not wait for MCU execution.
        self.flush_all_steps()
        # Wipe old movement in trapq
        self.wipe_trapq(trapq)


# Global instance for singleton
motion_queuing_adapter = MotionQueuingAdapter()
