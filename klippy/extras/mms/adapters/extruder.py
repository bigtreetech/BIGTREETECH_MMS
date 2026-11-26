# Adapter of printer's Extruder
#
# Copyright (C) 2025-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import chelper
from gcode import CommandError
from toolhead import LookAheadQueue, Move

from .base import BaseAdapter
from .gcode import gcode_adapter
from .gcode_move import gcode_move_adapter as gm_adp
from .motion_report import motion_report_adapter as mr_adp
from .motion_queuing import motion_queuing_adapter
from .printer import printer_adapter
from .toolhead import toolhead_adapter


class ExtruderTrajectory:
    """Local signed E curves; never retained across native flush callbacks."""
    def __init__(self, moves, max_e_accel):
        self._moves = moves
        # Coordinated printing can exceed the E-only acceleration setting.
        # Retain that setting for cruise/idle reserve correction as well.
        self.acceleration = max(
            max_e_accel, max((abs(move[3]) for move in moves), default=0.))

    def interval(self, start_time, end_time):
        """Return net distance, forward distance and signed velocity bounds."""
        if end_time <= start_time:
            return 0., 0., 0., 0.
        net = positive = covered = 0.
        lower, upper = float('inf'), -float('inf')
        for start, duration, velocity, accel in self._moves:
            if start >= end_time:
                break
            if start + duration <= start_time:
                continue
            begin = max(0., start_time - start)
            end = min(duration, end_time - start)
            distance = (end - begin) * (velocity + .5 * accel * (end + begin))
            net += distance
            positive += max(0., distance)
            first, last = velocity + accel * begin, velocity + accel * end
            lower, upper = min(lower, first, last), max(upper, first, last)
            covered += end - begin
        if not covered:
            return 0., 0., 0., 0.
        if covered < end_time - start_time - 1.e-9:
            lower, upper = min(lower, 0.), max(upper, 0.)
        return net, positive, lower, upper


class ExtruderAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        # Delta for min_extrude_temp
        self.temp_delta = 5

        # At least safe_get() once to wake up _setup_logger()
        # However now no safe_get() would be called, so lazy setup
        self._log_info = None
        self._log_error = None
        self._logger_is_set = False

    def _lazy_setup_logger(self):
        mms_logger = printer_adapter.get_mms_logger()
        self._log_info = mms_logger.create_log_info(console_output=False)
        self._log_error = mms_logger.create_log_error(console_output=False)

    def log_info(self, msg):
        if not self._logger_is_set:
            self._lazy_setup_logger()
            self._logger_is_set = True
        self._log_info(msg)

    def log_error(self, msg):
        if not self._logger_is_set:
            self._lazy_setup_logger()
            self._logger_is_set = True
        self._log_error(msg)

    def _get_extruder(self):
        return toolhead_adapter.get_extruder()

    # ---- Extruder Heaters ----
    def get_min_temp(self):
        min_temp = toolhead_adapter.get_extruder_heater().min_extrude_temp
        return min_temp + self.temp_delta

    def get_current_temp(self):
        return toolhead_adapter.get_extruder_heater_temp()

    def get_target_temp(self):
        return toolhead_adapter.get_extruder_heater_target_temp()

    def set_temperature(self, temp, wait=True):
        toolhead_adapter.set_extruder_temperature(temp, wait)

    def heat_to_min_temp(self):
        min_temp = self.get_min_temp()
        if self.get_current_temp() < min_temp:
            msg = (
                f"extruder[{self.get_extruder_name()}] wait until "
                f"heated to {min_temp}C"
            )
            gcode_adapter.respond_echo(msg)
            self.set_temperature(min_temp)

    def is_hot_enough(self):
        if not self.get_extruder_status().get("can_extrude"):
            msg = f"extruder[{self.get_extruder_name()}] is not hot enough"
            gcode_adapter.respond_error(msg)
            self.log_error(msg)
            return False
        return True

    # ---- Extrude/Retract ----
    def _position_stepper(self, extruder):
        # An extruder may use its built-in motor or an external
        # [extruder_stepper]. Read the motor actually following this trapq.
        candidates = [extruder.extruder_stepper]
        candidates.extend(obj.extruder_stepper for name, obj in
                          self.printer.lookup_objects('extruder_stepper'))
        for candidate in candidates:
            if candidate is not None and \
                    candidate.stepper.get_trapq() is extruder.get_trapq():
                return candidate.stepper
        raise CommandError("MMS extrusion has no synchronized extruder stepper")

    def get_executed_position(self, print_time):
        """Physical E steps at a shared MCU time, including pressure advance."""
        stepper = self._position_stepper(self._get_extruder())
        return (stepper.get_past_mcu_position(print_time)
                * stepper.get_step_dist())

    def get_generated_position(self):
        """Physical steps generated so far, including uncompressed steps."""
        stepper = self._position_stepper(self._get_extruder())
        return stepper.get_mcu_position() * stepper.get_step_dist()

    def get_planned_motion(self, start_time):
        """Copy queued E curves once; the caller owns this no-yield snapshot."""
        ffi_main, ffi = chelper.get_ffi()
        data = ffi_main.new('struct pull_move[64]')
        extruder = self._get_extruder()
        trapq = extruder.get_trapq()
        end_time = float('inf')
        moves = []
        while end_time > start_time:
            count = ffi.trapq_extract_old(
                trapq, data, len(data), start_time, end_time)
            for i in range(count):
                move = data[i]
                moves.append((move.print_time, move.move_t,
                              move.x_r * move.start_v, move.x_r * move.accel))
            if count < len(data):
                break
            end_time = data[count - 1].print_time
        moves.reverse()
        return ExtruderTrajectory(moves, extruder.max_e_accel)

    def _drip_move(self, distance, speed, check, stop_when,
                   on_segment_accepted):
        # One native E trajectory, submitted in short time windows. Multiple
        # G1 E moves would each stop at their junction, even without M400.
        toolhead = toolhead_adapter._get_toolhead()
        extruder = self._get_extruder()
        mq = toolhead.motion_queuing
        start_pos = toolhead.get_position()
        end_pos = list(start_pos)
        end_pos[3] += distance
        if end_pos[3] == start_pos[3]:
            # Settle an unrepresentable remainder without queuing a zero move.
            if on_segment_accepted is not None:
                on_segment_accepted(distance)
            return distance, False
        # Plan the requested relative E distance, so endpoint subtraction
        # cannot round a full-size segment above the native extrusion limit.
        # Bind the physical origin before native checking and submission.
        relative_start = list(start_pos)
        relative_start[3] = 0.
        relative_end = list(relative_start)
        relative_end[3] = distance
        move = Move(toolhead, relative_start, relative_end, speed / 60.)
        move.start_pos, move.end_pos = tuple(start_pos), tuple(end_pos)
        extruder.check_move(move, 3)
        position_stepper = self._position_stepper(extruder)
        lookahead = LookAheadQueue()
        lookahead.add_move(move)
        lookahead.flush()

        class Completion:
            error = None
            stopped = False

            def test(inner):
                if inner.stopped:
                    return True
                try:
                    toolhead_adapter.check_motion()
                    if not extruder.heater.can_extrude:
                        raise CommandError(
                            "MMS extrusion stopped: below minimum temperature")
                    if check is not None:
                        check()
                    inner.stopped = bool(stop_when and stop_when())
                except Exception as error:
                    # Do not throw through core's drip loop: it must restore
                    # its flush timer before the caller reports the failure.
                    inner.error = error
                    inner.stopped = True
                return inner.stopped

            def wait(inner, waketime):
                self.reactor.pause(waketime)

        completion = Completion()
        if completion.test():
            if completion.error is not None:
                raise completion.error
            return 0., True
        start_time = toolhead.get_last_move_time()
        end_time = start_time + move.accel_t + move.cruise_t + move.decel_t
        trapq = extruder.get_trapq()
        actual = 0.
        completed = False
        try:
            extruder.process_move(start_time, move, 3)
            motion_queuing_adapter.drip_update_time(
                start_time, end_time, completion)
            completed = not completion.stopped
        finally:
            # Include core's final step-generation window, not wall time or
            # the moment Entry changed. Already generated steps must drain.
            stop_time = max(start_time, mq.last_step_gen_time)
            if completed:
                end_e = end_pos[3]
            elif stop_time <= start_time:
                end_e = start_pos[3]
            else:
                end_e = position_stepper.get_commanded_position()
            # A completed trajectory consumes its requested distance. Endpoint
            # subtraction can leave a rounding remainder that becomes a zero
            # length move on the next iteration. Interrupted moves still use
            # the generated-step frontier for partial-distance accounting.
            actual = distance if completed else end_e - start_pos[3]
            # This also trims cancelled future trapq history. Wiping alone
            # would leave the old planned endpoint visible to motion reports.
            ffi_main, ffi_lib = chelper.get_ffi()
            ffi_lib.trapq_set_position(trapq, stop_time, end_e, 0., 0.)
            toolhead._advance_move_time(stop_time)
            extruder.last_position = end_e
            toolhead.set_extruder(extruder, end_e)
            gm_adp._get_gcode_move().reset_last_position()
            try:
                if actual and on_segment_accepted is not None:
                    on_segment_accepted(actual)
            finally:
                toolhead_adapter.wait_moves(check=False)
        if completion.error is not None:
            raise completion.error
        return actual, completion.stopped

    def _move(self, distance, speed, wait=True, on_accepted=None,
              check=None, on_segment_accepted=None, stop_when=None):
        # Only split at the configured native distance limit. Each allowed
        # move is continuous; the drip loop provides cancellation checkpoints.
        if speed <= 0.:
            raise CommandError("MMS extrusion speed must be positive")
        extruder = self._get_extruder()
        limit = extruder.max_e_dist
        if limit <= 0.:
            raise CommandError(
                "MMS extrusion requires a positive extrusion limit"
            )
        toolhead_adapter.check_motion()
        remaining = abs(distance)
        direction = 1. if distance >= 0. else -1.
        stopped = False
        with gm_adp.filament_move_state():
            # Retain synchronous ownership until all submitted E/drive steps
            # finish, including when a legacy caller supplies wait=False.
            toolhead_adapter.wait_moves()
            while remaining > 0.:
                toolhead_adapter.check_motion()
                if check is not None:
                    check()
                if stop_when is not None and stop_when():
                    break
                amount = min(remaining, limit)
                signed = direction * amount
                actual, stopped = self._drip_move(
                    signed, speed, check, stop_when, on_segment_accepted)
                remaining = max(0., remaining - abs(actual))
                toolhead_adapter.check_motion()
                if check is not None:
                    check()
                if stopped:
                    break
            if not remaining and not stopped and on_accepted is not None:
                on_accepted()
        return direction * (abs(distance) - remaining)

    def move_filament(self, distance, speed, on_accepted=None,
                      check=None, on_segment_accepted=None, stop_when=None):
        # Move physical mm; submitted motion is accounted on wait failure.
        return self._move(
            distance, speed, on_accepted=on_accepted, check=check,
            on_segment_accepted=on_segment_accepted, stop_when=stop_when)

    def extrude(self, distance, speed, wait=True, on_accepted=None,
                check=None, on_segment_accepted=None, stop_when=None):
        # Return submitted E distance. stop_when ends normally; faults raise
        # after truncating the trajectory and draining submitted steps.
        self.log_info(f"mms extrude {distance} mm at {speed} mm/min")
        return self._move(abs(distance), speed, wait, on_accepted=on_accepted,
                          check=check, on_segment_accepted=on_segment_accepted,
                          stop_when=stop_when)

    def retract(self, distance, speed, wait=True, check=None, stop_when=None,
                on_segment_accepted=None):
        self.log_info(f"mms retract {distance} mm at {speed} mm/min")
        return self._move(-abs(distance), speed, wait, check=check,
                          stop_when=stop_when,
                          on_segment_accepted=on_segment_accepted)

    def _drip_action(
        self, action, speed, drip_distance, drip_times, exit_condition
    ):
        # Sum distance
        dist_total = 0

        for i in range(int(drip_times)):
            action(distance=drip_distance, speed=speed)
            dist_total += drip_distance
            # Condition achieved, exit
            if exit_condition():
                return True, dist_total
            # Pause before next drip
            self.pause(0.2)

        # Finally false if condition is not achieved
        return False, dist_total

    def drip_extrude(
        self, speed, drip_distance, drip_times, exit_condition
    ):
        return self._drip_action(
            self.extrude, speed, drip_distance, drip_times, exit_condition
        )

    def drip_retract(
        self, speed, drip_distance, drip_times, exit_condition
    ):
        return self._drip_action(
            self.retract, speed, drip_distance, drip_times, exit_condition
        )

    # ---- Extruder Status/Config ----
    def get_extruder_status(self):
        """
        {
            # Extruder heater status
            'temperature': round(smoothed_temp, 2),
            'target': target_temp,
            'power': last_pwm_value,
            'can_extrude': False,

            # Extruder status
            'pressure_advance': self.pressure_advance,
            'smooth_time': self.pressure_advance_smooth_time,
            'motion_queue': self.motion_queue,
        }
        """
        return self._get_extruder().get_status(self.reactor.monotonic())

    def get_extruder_name(self):
        return self._get_extruder().get_name()

    def get_extruder_filament_area(self):
        return self._get_extruder().filament_area

    def get_position(self):
        # Executed extruder move
        # return mr_adp.get_extruder_position()

        # Command(Current/Coming) extruder move
        # Param["position"] is the absolute position of extruder
        # no matter absolute_extrude is True or False
        position = gm_adp.get_toolhead_position().get("e")
        return position

    def get_speed(self):
        # The speed of gcode move is in mm/min
        gm_speed = gm_adp.get_current_status().get("speed")
        # Transform speed from mm/min to mm/s
        return self.transform_speed(gm_speed)

    def get_position_speed(self):
        position = gm_adp.get_toolhead_position().get("e")
        speed = gm_adp.get_current_status().get("speed")
        return position, self.transform_speed(speed)

    def get_velocity(self):
        return mr_adp.get_extruder_velocity()

    def get_flowrate(self):
        f_area = self.get_extruder_filament_area()
        e_velocity = mr_adp.get_extruder_velocity()
        return f_area * e_velocity

    def is_extruding(self):
        return mr_adp.get_extruder_velocity() > 0

    def is_retracting(self):
        return mr_adp.get_extruder_velocity() < 0

    # The 'current' type may get from trapq directly,
    # not from motion_report cache,
    # which may increase system burden
    def get_extruder_pos_vel(self):
        pos, velocity = mr_adp.get_extruder_trapq(
            self.get_extruder_name(), self.reactor.monotonic())
        return velocity

    def get_current_extruder_flowrate(self):
        f_area = self.get_extruder_filament_area()
        e_velocity = self.get_current_extruder_velocity()
        return f_area * e_velocity

    def is_current_extruding(self):
        return self.get_current_extruder_velocity() > 0

    def is_current_retracting(self):
        return self.get_current_extruder_velocity() < 0

    def get_current_extruder_move(self):
        move_dct = mr_adp.get_extruder_move(
            self.get_extruder_name(), self.reactor.monotonic())
        return move_dct

    def transform_speed(self, extruder_speed):
        # Transform speed from mm/min to mm/s
        # 'extruder_speed' in [mm/min]
        # transform to [mm/s]
        return abs(extruder_speed) / 60.


# Global instance for singleton
extruder_adapter = ExtruderAdapter()
