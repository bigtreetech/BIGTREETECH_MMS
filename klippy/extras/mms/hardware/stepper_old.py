# Support for MMS Stepper
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import chelper
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass

from gcode import CommandError
from ...homing import HomingMove

from ..adapters import (
    force_move_adapter as force_move_a,
    manual_stepper_dispatch,
    printer_adapter,
    stepper_enable_adapter as stepper_enable_a,
    toolhead_adapter as toolhead_a
)


# ------------------------------
# Configuration and Constants
# ------------------------------
@dataclass(frozen=True)
class StepperConfig:
    # Delay for waiting to flush print_time, in seconds
    wait_delay: float = 0.05
    # Period for manual move pause block loop, in seconds
    pause_period: float = 0.05
    # Interval for manual move print_time advancement, in seconds
    interval_time: float = 1.0
    # Delay before reset stepcompress in soft_stop(), in seconds
    # soft_stop_delay: float = 0.15

    # Default segment of drip_move
    drip_segment: float = 0.5
    # Selector drip_segment can be more lower
    selector_drip_segment: float = 0.2
    drive_drip_segment: float = 0.2


@dataclass(frozen=True)
class MoveType:
    DRIP_MOVE: str = "drip_move"
    MANUAL_MOVE: str = "manual_move"
    MANUAL_HOME: str = "manual_home"


@dataclass(frozen=True)
class MoveStatus:
    READY: str = "ready"
    MOVING: str = "moving"
    # Completed by pin triggered/released
    COMPLETED: str = "completed"
    # Terminated by Commands
    TERMINATED: str = "terminated"
    # Move end without completed or terminated
    EXPIRED: str = "expired"
    # Error raised by GCode, such as CommandError
    ERROR: str = "error"


# ------------------------------
# Movement Dispatch
# ------------------------------
class MoveDispatch:
    """Base class for all movement dispatches"""
    def __init__(self, stepper):
        self.stepper = stepper
        self.reactor = printer_adapter.get_reactor()

        ffi_main, self.ffi_lib = chelper.get_ffi()
        self.trapq = ffi_main.gc(
            self.ffi_lib.trapq_alloc(),
            self.ffi_lib.trapq_free
        )

        self.s_config = StepperConfig()
        self.move_type = None

        self.steps_moved = 0
        self.distance_moved = 0.0
        self._completion = None

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_loggers()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info()
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()

    def execute(self, *args, **kwargs):
        """Execute the movement"""
        raise NotImplementedError

    # ---- Flow control ----
    def _prepare_tracking(self):
        """Reset movement tracking variables"""
        self.stepper.reset_position()
        self.steps_moved = self.stepper.get_step()
        # self.distance_moved = self.stepper.get_position()
        self.distance_moved = 0
        # self.drip_count = 0

    def _update_tracking(self):
        """Update movement tracking variables"""
        # self.steps_moved += self.stepper.get_step() - self.steps_moved
        # self.distance_moved += (self.stepper.get_position()
        #                         - self.distance_moved)
        # self.drip_count += 1

        # Now distance_moved can not calculate by get_stepper_position()
        # because stepper_position would be reset inside homing_move()
        # and the result may always be the params "distance"
        # So calculate by step_dist * steps_moved
        self.steps_moved = self.stepper.get_step() - self.steps_moved
        self.distance_moved = self.stepper.get_step_dist() * self.steps_moved

    def terminate(self):
        return

    # ---- Trapq control ----
    def _setup_trapq(self, print_time, distance, speed, accel):
        """Configure trap queue with movement parameters"""
        # Calculate move params with distance
        axis_r, accel_t, cruise_t, cruise_v = force_move_a.calc_move_time(
            distance, speed, accel)

        # accel_t plus twice because accel_t is equal to decel_t
        end_print_time = print_time + accel_t + cruise_t + accel_t

        try:
            # Append params to trapq
            self.ffi_lib.trapq_append(
                self.trapq,     # trapq
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
        except Exception as e:
            self.log_error(f"{self.move_type} setup trapq error: {e}")

    def _replace_trapq(self):
        # Return origin trap_queue
        return self.stepper.set_trapq(self.trapq)

    def _recover_trapq(self, prev_trapq):
        # Recover preview trapq
        self.stepper.set_trapq(prev_trapq)

    def _finalize_trapq(self, print_time):
        # Expire any moves older than drip_print_time from the trapq
        self.ffi_lib.trapq_finalize_moves(self.trapq, print_time, 0.)

    def _finalize_move(self):
        # Finalize exists moves in queue to stop stepper
        self._finalize_trapq(self.reactor.NEVER)

    # ---- Reactor waiting ----
    def _query_current(self):
        return self.reactor.monotonic()

    def _wait(self, delay):
        end_time = self._query_current() + delay
        self._completion = self.reactor.completion()

        while 1:
            # Complete
            result = self._completion.wait(end_time)
            if result is not None:
                break
            # Timeout
            if self._query_current() > end_time:
                self._completion.complete(False)
                break

        self._completion = None
        duration = self._query_current() + delay - end_time
        return duration

    def _complete_waiting(self, result=False):
        if self._completion:
            self.reactor.async_complete(
                completion = self._completion,
                result = result
            )


class DripMoveDispatch(MoveDispatch):
    def __init__(self, stepper):
        super().__init__(stepper)
        self.move_type = MoveType.DRIP_MOVE
        self.drip_segment = self.stepper.get_drip_segment()

        # Flags
        self._executing = False
        self._complete = False
        self._terminate = False

    def execute(self, print_time, distance, speed, accel):
        # Save original trapq and replace with ours
        prev_trapq = self._replace_trapq()

        self._prepare_tracking()
        try:
            drip_print_time = print_time + self.drip_segment
            # Calculate the final print_time of full distance
            end_print_time = self._setup_trapq(
                print_time, distance, speed, accel)

            self._executing = True
            # Execute movement in segments
            while drip_print_time < end_print_time:
                self._process_drip_step(drip_print_time, self.drip_segment)
                self._update_tracking()
                # Terminated
                if self._terminate or self._complete:
                    break
                # Increase print_time for next loop
                drip_print_time += self.drip_segment

            # Finalize movement
            self._finalize_move()
            # Return exactly final print_time
            return drip_print_time

        except Exception as e:
            self.log_error(f"{self.move_type} error: {str(e)}")
        finally:
            self._executing = False
            self._complete = False
            self._terminate = False
            self._recover_trapq(prev_trapq)
            self._update_tracking()
            self.stepper.reset_position()
            self._complete_waiting(result=True)

    def _process_drip_step(self, print_time, delay):
        """Process a single drip segment"""
        # Generate steps with print_time
        self.stepper.generate_steps(print_time)
        # Expire any moves older than print_time from the trapq
        self._finalize_trapq(print_time)
        # Execute steps by stepper's MCU
        self.stepper.execute_steps(print_time)
        # Wait till moves are done
        # self.stepper.pause(delay)
        # Async wait
        self._wait(delay)

    def terminate(self):
        # Call stepper.soft_stop outside
        if self._executing:
            self._terminate = True
            self._complete_waiting(result=False)

    def complete(self):
        if self._executing:
            self._complete = True
            self._complete_waiting(result=True)


class ManualMoveDispatch(MoveDispatch):
    def __init__(self, stepper):
        super().__init__(stepper)
        self.move_type = MoveType.MANUAL_MOVE

    def execute(self, print_time, distance, speed, accel):
        # Save original trapq and replace with ours
        prev_trapq = self._replace_trapq()

        self._prepare_tracking()
        try:
            # Calculate the final print_time of full distance
            end_print_time = self._setup_trapq(
                print_time, distance, speed, accel)
            delay = end_print_time - print_time + self.s_config.pause_period

            # Generate steps with print_time
            self.stepper.generate_steps(end_print_time)
            # Finalize movement
            self._finalize_move()
            # Execute steps by stepper's MCU
            self.stepper.execute_steps(end_print_time)
            # Wait until print_time is reach to the end
            # Which also means stepper move is done
            # Blocking wait
            # self.stepper.pause(delay)
            # Async wait
            # duration = self._wait(delay)
            self._wait(delay)

            # self.log_info(f"delay:{delay} duration:{duration}")
            # return print_time + duration
            return end_print_time

        except Exception as e:
            self.log_error(f"{self.move_type} error: {str(e)}")
        finally:
            self._recover_trapq(prev_trapq)
            self._update_tracking()
            self.stepper.reset_position()
            self._complete_waiting(result=True)

    def terminate(self, result=False):
        self._complete_waiting(result)


class ManualHomeDispatch(MoveDispatch):
    def __init__(self, stepper):
        super().__init__(stepper)
        self.move_type = MoveType.MANUAL_HOME

    def execute(self, distance, speed, accel, trigger, endstop_pair_lst):
        # # Move until triggered: True, released: False
        # triggered = True if distance >= 0 else False

        try:
            self._prepare_tracking()

            ms_adapter = manual_stepper_dispatch.get_adapter(
                self.stepper.get_name())
            ms_adapter.set_home_accel(accel)

            # Homing move
            hmove = HomingMove(
                printer = printer_adapter.get_printer(),
                endstops = endstop_pair_lst,
                toolhead = ms_adapter.get_manual_stepper()
            )
            # Return triggered_pos => [distance, 0., 0., 0.]
            hmove.homing_move(
                movepos = [distance, 0., 0., 0.],
                speed = speed,
                triggered = trigger,
                check_triggered = False
            )
            # If no move, the triggered endstop_name
            # in endstop_pair_lst=>[(muc_pin, endstop_name)]
            # would be return.
            # Else endstop_name would be None.
            endstop_name = hmove.check_no_movement()
            return endstop_name

        except Exception as e:
            self.log_error(f"{self.move_type} error: {str(e)}")
            # Raise CommandError again
            if isinstance(e, CommandError):
                raise
        finally:
            self._update_tracking()
            self.stepper.reset_position()


# ------------------------------
# Core Stepper Class
# ------------------------------
class MMSStepper:
    """Main stepper motor control class"""
    def __init__(self, name):
        self.name = name
        self.reactor = printer_adapter.get_reactor()

        # Configuration
        self.s_config = StepperConfig()
        self.drip_segment = self.s_config.drip_segment

        # State management
        self.mms_name = None
        self._index = None
        self._focus_slot = None
        self._is_running = False
        self._forward = True
        self._end_print_time = 0
        self._can_calibrate = True

        # Move status
        self.move_type = ""
        self._reset_move_status(MoveStatus.READY)

        # Setup and register mcu objects
        self._mcu_stepper = force_move_a.get_mcu_stepper(self.name)
        self._mcu = self.get_mcu_stepper().get_mcu()

        # Initialize movement dispatch with self
        self.move_dispatch_dct = {
            # MoveType.DRIP_MOVE: DripMoveDispatch(self),
            MoveType.MANUAL_MOVE: ManualMoveDispatch(self),
            MoveType.MANUAL_HOME: ManualHomeDispatch(self),
        }

        # Register connect handler to printer
        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)

    def _handle_klippy_connect(self):
        self._initialize_loggers()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info()
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()

    def is_running(self):
        return self._is_running

    def is_init(self):
        # Init status, never run before
        return self.move_status == MoveStatus.READY

    def pause(self, period_seconds):
        self.reactor.pause(self.reactor.monotonic() + period_seconds)

    # ---- Printer Objects ----
    def get_mcu(self):
        if not self._mcu:
            self._mcu = self.get_mcu_stepper().get_mcu()
        return self._mcu

    def get_mcu_stepper(self):
        # Get stepper.MCU_stepper
        if not self._mcu_stepper:
            self._mcu_stepper = force_move_a.get_mcu_stepper(self.name)
        return self._mcu_stepper

    def get_mcu_stepper_status(self):
        mcu_stepper = self.get_mcu_stepper()
        return {
            "name" : mcu_stepper.get_name(),
            # "units_in_radians" : mcu_stepper.units_in_radians(),
            # "step_pulse_duration" : mcu_stepper.get_pulse_duration()[0],
            # "step_both_edge" : mcu_stepper.get_pulse_duration()[1],

            "oid" : mcu_stepper.get_oid(),
            "step_dist" : mcu_stepper.get_step_dist(),
            "rotation_distance" : mcu_stepper.get_rotation_distance()[0],
            "steps_per_rotation" : mcu_stepper.get_rotation_distance()[1],

            # "invert_dir" : mcu_stepper.get_dir_inverted()[0],
            # "orig_invert_dir" : mcu_stepper.get_dir_inverted()[1],

            "commanded_position" : mcu_stepper.get_commanded_position(),
            "mcu_position" : mcu_stepper.get_mcu_position(),
            # "past_mcu_position" : mcu_stepper.get_past_mcu_position(
            #     self._end_print_time),

            # "mcu" : mcu_stepper.get_mcu(),
            # "stepper_kinematics" : mcu_stepper.get_stepper_kinematics(),
            # "trapq" : mcu_stepper.get_trapq(),
        }

    def get_step_dist(self):
        return self.get_mcu_stepper().get_step_dist()

    def get_mcu_status(self):
        mcu = self.get_mcu()
        eventtime = self.reactor.monotonic()
        clocksync = mcu._clocksync
        clock_adj = clocksync.clock_adj
        clock_est = clocksync.clock_est
        clock = clocksync.get_clock(eventtime)
        print_time = mcu.clock_to_print_time(clock)
        # clock_re = mcu.print_time_to_clock(print_time)

        return {
            "name" : mcu.get_name(),
            "freq" : mcu._mcu_freq,
            "oid_count" : mcu._oid_count,
            # "flush_callbacks" : mcu._flush_callbacks,
            # "stepqueues" : mcu._stepqueues,
            # "status_info" : mcu.get_status(),

            "eventtime" : eventtime,
            "adjusted_offset" : clock_adj[0],
            "adjusted_freq" : clock_adj[1],
            "clock_est(sample_time, clock, freq)" : clock_est,
            "clock" : clock,
            "print_time" : print_time,
        }

    # ---- Stepper Status ----
    def get_status(self):
        return {
            "index" : self._index,
            # Klipper config section name of stepper
            "name" : self.name,
            # MMS name of stepper
            "mms_name" : self.mms_name,
            # The focusing slot of stepper
            "focus_slot" : self._focus_slot,
            "is_running" : self._is_running,
            "forward" : self._forward,

            # Current move statement
            "move_type" : self.move_type,
            "move_status" : self.move_status,

            # Distance per step
            "step_dist" : round(self.get_step_dist(), 4),
            # The steps stepper current/last moved
            "steps_moved" : self.get_steps_moved(),
            # The distinces stepper current/last moved
            "distance_moved" : round(self.get_distance_moved(), 4),
        }

    def log_status(self):
        self.log_info(json.dumps(self.get_status(), indent=4))

    def get_name(self):
        return self.name

    def set_index(self, index):
        self._index = index

    def get_index(self):
        return self._index

    def update_focus_slot(self, slot):
        self._focus_slot = slot

    def get_focus_slot(self):
        return self._focus_slot

    def get_dispatch(self, move_type=None):
        mv_type = move_type or self.move_type
        return self.move_dispatch_dct.get(mv_type, None)

    def get_steps_moved(self):
        dispatch = self.get_dispatch()
        return dispatch.steps_moved if dispatch else 0

    def get_distance_moved(self):
        dispatch = self.get_dispatch()
        return dispatch.distance_moved if dispatch else 0

    def get_position(self):
        # Return current position
        return self.get_mcu_stepper().get_commanded_position()

    def get_step(self):
        # Return current MCU steps
        return self.get_mcu_stepper().get_mcu_position()

    def reset_position(self):
        # Set position to avoid "Stepcompress error"
        self.get_mcu_stepper().set_position((0., 0., 0.))
        return self.get_position()

    def set_trapq(self, trapq):
        return self.get_mcu_stepper().set_trapq(trapq)

    def get_drip_segment(self):
        return self.drip_segment

    def move_is_completed(self, move_status=None):
        move_status = move_status or self.move_status
        return move_status == MoveStatus.COMPLETED

    def move_is_terminated(self, move_status=None):
        move_status = move_status or self.move_status
        return move_status == MoveStatus.TERMINATED

    def move_is_error(self, move_status=None):
        move_status = move_status or self.move_status
        return move_status == MoveStatus.ERROR

    # ---- Control ----
    def _cal_enable_print_time(self):
        cal_pt = self._cal_print_time(add_interval=False)
        th_pt = toolhead_a.get_print_time()
        # self.log_info(
        #     f"[{self.mms_name}] print_time:{cal_pt:.2f}, "
        #     f"toolhead print_time:{th_pt:.2f}"
        # )
        return max(cal_pt, th_pt)

    def enable(self):
        if not stepper_enable_a.is_motor_enabled(self.name):
            pt = self._cal_enable_print_time()
            res = stepper_enable_a.enable(self.name, pt)
            if res:
                self.log_info(f"[{self.mms_name}] enable at"
                              f" print_time:{pt:.2f}")

    def disable(self):
        if stepper_enable_a.is_motor_enabled(self.name):
            pt = self._cal_enable_print_time()
            res = stepper_enable_a.disable(self.name, pt)
            if res:
                self.log_info(f"[{self.mms_name}] disable at"
                              f" print_time:{pt:.2f}")

    def _reset_move_status(self, move_type):
        self.move_status = move_type
        self.move_end_at = None
        self.move_end_steps = 0

    def _update_move_status(self, move_type):
        self.move_status = move_type
        self.move_end_at = time.time()
        self.move_end_steps = self.get_steps_moved()

    @contextmanager
    def _stepper_is_running(self, move_type):
        """
        Context manager to set the stepper motor running state.
        This method sets the `_is_running` attribute to True when the context
        is entered, and sets it back to False when the context is exited.
        Usage:
            with self._stepper_is_running():
                # Code to execute while the stepper is running
        Yields:
            None
        """
        if self._is_running:
            self.log_warning(f"[{self.mms_name}] is still running,"
                             f" move skip...")
            # Even confident is true, still yield to make sure generator return
            # yield at least once
            yield False
            return

        self.move_type = move_type
        self._is_running = True
        self._reset_move_status(MoveStatus.MOVING)
        printer_adapter.notify_mms_stepper_running()
        # Force enable stepper before run
        self.enable()
        try:
            yield True
        finally:
            self._is_running = False
            if self.move_status == MoveStatus.MOVING:
                # Not completed or terminated, mark as expired
                self._update_move_status(MoveStatus.EXPIRED)
            printer_adapter.notify_mms_stepper_idle()

    def _cal_print_time(self, add_interval=True):
        """
        Calculate the estimated print time.
        This method retrieves the estimated print time from the MCU
        and adds the configured interval time for the stepper.
        Returns:
            float: The final estimated print time.
        """
        print_time = self.get_mcu().estimated_print_time(
            self.reactor.monotonic())
        if add_interval:
            print_time += self.s_config.interval_time
        return print_time

    def _adjust_print_time(self):
        print_time = self._cal_print_time()
        if print_time < self._end_print_time:
            # Last round is not done yet
            # Wait to avoid "Stepcompress error"
            wait_time = (self._end_print_time - print_time
                         + self.s_config.wait_delay)
            self.log_info(f"[{self.mms_name}] {self.move_type}"
                          f" wait:{wait_time:.2f}...")
            # Wait to flush print_time
            self.pause(wait_time)
            # Calculate new print_time
            print_time = self._cal_print_time()
        return print_time

    def generate_steps(self, print_time):
        # Generate steps with print_time
        self.get_mcu_stepper().generate_steps(print_time)

    def execute_steps(self, print_time):
        # Execute steps by stepper's MCU
        self.get_mcu().flush_moves(print_time, 0.)

    def _sync_print_time(self):
        toolhead_pt = toolhead_a.get_print_time()
        gap = self._end_print_time - toolhead_pt

        if gap < 0:
            self.log_info(f"[{self.mms_name}] flush:{abs(gap):.2f}...")
            self._end_print_time = toolhead_pt
        elif gap > 0:
            self.log_info(f"toolhead dwell:{gap:.2f}...")
            toolhead_a.dwell(gap)

    # ---- Public Movement Methods ----
    # -- Drip Move --
    def drip_move(self, distance, speed, accel, forward=True):
        mv_type = MoveType.DRIP_MOVE
        distance = abs(distance) if forward else -abs(distance)
        self._forward = forward

        with self._stepper_is_running(mv_type) as can_run:
            if can_run:
                print_time = self._adjust_print_time()
                dispatch = self.get_dispatch(mv_type)
                self._end_print_time = dispatch.execute(
                    print_time, distance, speed, accel)

    def complete_drip_move(self):
        if not self._is_running:
            self.log_warning(
                f"[{self.mms_name}] is not running, complete failed")
            return

        try:
            dispatch = self.get_dispatch(MoveType.DRIP_MOVE)
            dispatch.complete()
            self._update_move_status(MoveStatus.COMPLETED)
        except Exception as e:
            self.log_error(f"[{self.mms_name}] complete error: {e}")

    def terminate_drip_move(self):
        if not self._is_running:
            self.log_warning(
                f"[{self.mms_name}] is not running, terminate failed")
            return

        try:
            dispatch = self.get_dispatch(MoveType.DRIP_MOVE)
            dispatch.terminate()
            self._update_move_status(MoveStatus.TERMINATED)
        except Exception as e:
            self.log_error(f"[{self.mms_name}] terminate error: {e}")

    # -- Manual Move --
    def manual_move(self, distance, speed, accel):
        mv_type = MoveType.MANUAL_MOVE
        self._forward = True if distance >= 0 else False

        with self._stepper_is_running(mv_type) as can_run:
            if can_run:
                print_time = self._adjust_print_time()
                dispatch = self.get_dispatch(mv_type)
                self._end_print_time = dispatch.execute(
                    print_time, distance, speed, accel)
                # self.log_status()

    def terminate_manual_move(self):
        if not self._is_running:
            self.log_warning(
                f"[{self.mms_name}] is not running, terminate failed")
            return

        # self._soft_stop()
        self.get_dispatch(MoveType.MANUAL_MOVE).terminate()
        self._update_move_status(MoveStatus.TERMINATED)

    # -- Manual Home --
    def manual_home(
        self, distance, speed, accel, forward, trigger, endstop_pair_lst
    ):
        mv_type = MoveType.MANUAL_HOME
        distance = abs(distance) if forward else -abs(distance)
        self._forward = forward

        with self._stepper_is_running(mv_type) as can_run:
            if can_run:
                self._can_calibrate = True
                self._sync_print_time()
                dispatch = self.get_dispatch(mv_type)

                try:
                    endstop_name = dispatch.execute(
                        distance, speed, accel, trigger, endstop_pair_lst)

                    if endstop_name is not None \
                        and self.get_distance_moved() == 0:
                        # Endstop is triggered without moving
                        self.complete_manual_home()
                        self._can_calibrate = False
                except CommandError as e:
                    self.move_status = MoveStatus.ERROR
                    printer_adapter.emergency_stop(e)
                    raise

        self.log_status()
        return self.move_status

    def complete_manual_home(self):
        if not self._is_running:
            self.log_warning(
                f"[{self.mms_name}] is not running, complete failed")
            return
        self._update_move_status(MoveStatus.COMPLETED)

    def terminate_manual_home(self):
        if not self._is_running:
            self.log_warning(
                f"[{self.mms_name}] is not running, "
                "terminate failed...")
            return
        self._update_move_status(MoveStatus.TERMINATED)

    def can_calibrate(self):
        return self._can_calibrate

    # ---- Terminate ----
    def terminate(self):
        return
        # if not self._is_running:
        #     self.log_warning(
        #         f"[{self.mms_name}] is not running, terminate failed")
        #     return

        # if self.move_type == MoveType.MANUAL_MOVE:
        #     self.terminate_manual_move()
        # elif self.move_type == MoveType.MANUAL_HOME:
        #     self.terminate_manual_home()
        # elif self.move_type == MoveType.DRIP_MOVE:
        #     self.terminate_drip_move()


class MMSSelector(MMSStepper):
    def __init__(self, name):
        super().__init__(name)
        self.drip_segment = self.s_config.selector_drip_segment
        self.mms_name = "Selector"


class MMSDrive(MMSStepper):
    def __init__(self, name):
        super().__init__(name)
        self.drip_segment = self.s_config.drive_drip_segment
        self.mms_name = "Drive"
