# Support for MMS Stepper
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass

from gcode import CommandError
from mcu import error as MCUError
from ...homing import HomingMove

from ..adapters import (
    force_move_adapter as force_move_a,
    gcode_adapter,
    manual_stepper_dispatch as ms_dispatch,
    motion_queuing_adapter as motion_queuing_a,
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
    # Interval for manual move print_time advancement, in seconds
    interval_time_selector: float = 1.0
    interval_time_drive: float = 1.0


@dataclass(frozen=True)
class MoveType:
    MANUAL_MOVE: str = "manual_move"
    MANUAL_HOME: str = "manual_home"
    DRIP_MOVE: str = "drip_move"
    BUFFER_FEED: str = "buffer_feed"
    EXTRUDER_SYNC: str = "extruder_sync"
    EXTRUDER_MOVE: str = "extruder_move"


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
        self.trapq = motion_queuing_a.allocate_trapq()

        self.s_config = StepperConfig()
        self.move_type = None

        self.steps_moved = 0
        self.distance_moved = 0.0

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_loggers()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        # self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)

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

    def _update_tracking(self):
        """Update movement tracking variables"""
        # self.steps_moved += self.stepper.get_step() - self.steps_moved
        # self.distance_moved += (self.stepper.get_position()
        #                         - self.distance_moved)

        # Now distance_moved can not calculate by get_stepper_position()
        # because stepper_position would be reset inside homing_move()
        # and the result may always be the params "distance"
        # So calculate by step_dist * steps_moved
        self.steps_moved = self.stepper.get_step() - self.steps_moved
        self.distance_moved = self.stepper.get_step_dist() * self.steps_moved
        self.stepper.reset_position()

    def terminate(self):
        return

    # ---- Trapq control ----
    def _replace_trapq(self):
        # Return origin trap_queue
        return self.stepper.set_trapq(self.trapq)

    def _recover_trapq(self, prev_trapq):
        # Recover preview trapq
        self.stepper.set_trapq(prev_trapq)

    # ---- Reactor waiting ----
    def _wait_for_completion(self, end_print_time):
        # Drain accepted steps before releasing the motor, even after cancel.
        mcu = self.stepper.get_mcu()
        while not printer_adapter.is_shutdown():
            eventtime = self.reactor.monotonic()
            remaining = end_print_time - mcu.estimated_print_time(eventtime)
            if remaining <= 0.:
                break
            self.reactor.pause(eventtime + min(remaining, 0.1))
        toolhead_a.check_motion(require_owner=False)

    # ---- Get status ----
    def get_steps_moved(self):
        return self.steps_moved or 0

    def get_distance_moved(self):
        return self.distance_moved or 0


class ManualMoveDispatch(MoveDispatch):
    def __init__(self, stepper):
        super().__init__(stepper)
        self.move_type = MoveType.MANUAL_MOVE

    def execute(self, print_time, distance, speed, accel, background=False):
        # Save original trapq and replace with ours
        prev_trapq = self._replace_trapq()
        end_print_time = None
        completed = False
        try:
            self._prepare_tracking()
            end_print_time = motion_queuing_a.setup_trapq(
                self.trapq, print_time, distance, abs(speed), abs(accel))
            if background:
                # Klipper generates this selector trajectory in its usual
                # windows alongside the print. Keep it attached until drain.
                motion_queuing_a.note_mcu_movequeue_activity(end_print_time)
            else:
                motion_queuing_a.process_trapq(self.trapq, end_print_time)
            completed = True
            return end_print_time
        except Exception as e:
            self.log_error(f"{self.move_type} error: {str(e)}")
            raise
        finally:
            try:
                stop_time = end_print_time
                if end_print_time is not None and not completed:
                    stop_time = min(end_print_time, max(
                        print_time, motion_queuing_a.get_last_step_gen_time()))
                try:
                    if not completed:
                        # Setup/flush can fail with an unsubmitted queue tail.
                        motion_queuing_a.wipe_trapq(self.trapq)
                finally:
                    if stop_time is not None:
                        # Keep ownership until every accepted step has drained.
                        self._wait_for_completion(stop_time)
                        if (background and completed
                                and motion_queuing_a.get_last_step_gen_time()
                                < stop_time):
                            raise CommandError(
                                "selector steps were not generated in time")
            finally:
                try:
                    if background and completed:
                        motion_queuing_a.wipe_trapq(self.trapq)
                finally:
                    self._recover_trapq(prev_trapq)
                    if end_print_time is not None:
                        self._update_tracking()


class ManualHomeDispatch(MoveDispatch):
    def __init__(self, stepper):
        super().__init__(stepper)
        self.move_type = MoveType.MANUAL_HOME
        self.mcu_pin_lst = []

    def execute(self, distance, speed, accel, trigger, endstop_pair_lst):
        # endstop_pair_lst: [(muc_pin, endstop_name)]
        self.mcu_pin_lst = [tup[1] for tup in endstop_pair_lst]
        self._prepare_tracking()

        try:
            ms_adapter = ms_dispatch.get_adapter(
                self.stepper.get_name())
            ms_adapter.set_home_accel(abs(accel))

            # Homing move
            hmove = HomingMove(
                printer = printer_adapter.get_printer(),
                endstops = endstop_pair_lst,
                toolhead = ms_adapter.get_manual_stepper()
            )
            # Return triggered_pos => [distance, 0., 0., 0.]
            motion_queuing_a.run_native_motion(
                hmove.homing_move,
                movepos = [distance, 0., 0., 0.],
                speed = abs(speed),
                triggered = trigger,
                check_triggered = False
            )
            # If no move, the triggered endstop_name
            # in endstop_pair_lst=>[(muc_pin, endstop_name)]
            # would be return.
            # Else endstop_name would be None.
            endstop_name = hmove.check_no_movement()
            return endstop_name

        except (CommandError, MCUError) as e:
            # Raise CommandError or MCUError
            self.log_error(f"{self.move_type} error: {str(e)}")
            raise
        except Exception as e:
            self.log_error(f"{self.move_type} error: {str(e)}")
            raise
        finally:
            self._update_tracking()
            # Truncate mcu_pin list
            self.mcu_pin_lst = []

    def is_destination(self, mcu_pin):
        return mcu_pin in self.mcu_pin_lst


class DripMoveDispatch(MoveDispatch):
    def __init__(self, stepper):
        super().__init__(stepper)
        self.move_type = MoveType.DRIP_MOVE
        self._drip_completion = None

    def execute(self, print_time, distance, speed, accel,
                check=None, stop_when=None):
        prev_trapq = self._replace_trapq()
        error = None
        end_print_time = None
        dispatch = self

        class Completion:
            def test(inner):
                nonlocal error
                try:
                    if check is not None:
                        check()
                    if stop_when is not None and stop_when():
                        dispatch.terminate()
                except Exception as e:
                    error = e
                    dispatch.terminate()
                return dispatch._drip_completion.test()

            def wait(inner, waketime):
                return dispatch._drip_completion.wait(waketime)

        try:
            self._prepare_tracking()
            self._drip_completion = self.reactor.completion()
            completion = (Completion() if check is not None
                          or stop_when is not None else self._drip_completion)
            end_print_time = motion_queuing_a.setup_trapq(
                self.trapq, print_time, distance, abs(speed), abs(accel))
            motion_queuing_a.drip_update_time(
                print_time, end_print_time, completion)
        except Exception as e:
            self.log_error(f"{self.move_type} error: {str(e)}")
            raise
        finally:
            try:
                stop_time = None
                if end_print_time is not None:
                    stop_time = min(end_print_time, max(
                        print_time, motion_queuing_a.get_last_step_gen_time()))
                try:
                    motion_queuing_a.wipe_trapq(self.trapq)
                finally:
                    if stop_time is not None:
                        self._wait_for_completion(stop_time)
            finally:
                self._drip_completion = None
                self._recover_trapq(prev_trapq)
                if end_print_time is not None:
                    self._update_tracking()
        if error is not None:
            raise error
        return stop_time

    def terminate(self, result=True):
        if self._drip_completion:
            self._drip_completion.complete(result)


# ------------------------------
# Core Stepper Class
# ------------------------------
class BufferMoveDispatch(MoveDispatch):
    """Own only the feeder queue; append before native step generation."""
    def start(self, print_time):
        self.move_type = MoveType.BUFFER_FEED
        self._prepare_tracking()
        self.previous_trapq = self._replace_trapq()
        self.attached = True
        self.print_time = print_time
        self.position = self.velocity = 0.
        self.stopping = False
        self.stop_time = None

    def append(self, step_gen_time, velocity):
        now = self.stepper._cal_print_time()
        if self.print_time < now:
            # An idle interval is stationary, not a backlog to regenerate.
            self.print_time = motion_queuing_a.calc_step_gen_restart(now)
            self.velocity = 0.
        # This Cartesian feeder needs no PA/shaper pre-scan. A tail beyond the
        # native frontier could remain ungenerated while the flush timer sleeps.
        end = step_gen_time
        while not self.stopping and self.print_time < end - 1.e-9:
            duration = min(.05, end - self.print_time)
            target, accel = velocity(
                self.print_time, self.position, self.velocity, duration)
            self.print_time, self.position, self.velocity = (
                motion_queuing_a.append_velocity(
                    self.trapq, self.print_time, self.position, self.velocity,
                    target, accel, duration=duration))
            self.stepper._forward = self.velocity >= 0.
        self.stepper._end_print_time = min(self.print_time, step_gen_time)

    def stop(self):
        # A failed wipe is retryable; a successful cut keeps its exact time.
        # No core flush/wait here: this can run inside assert_no_pause.
        self.stopping = True
        if self.stop_time is None:
            end = min(self.print_time,
                      motion_queuing_a.get_last_step_gen_time())
            motion_queuing_a.wipe_trapq(self.trapq)
            self.stop_time = end
            self.stepper._end_print_time = end

    def finish(self):
        self.stop()
        try:
            self._wait_for_completion(self.stepper._end_print_time)
        finally:
            # Cancellation after the physical drain must not skip cleanup.
            # A failed queue restore retains ownership for a repaired retry.
            self._recover_trapq(self.previous_trapq)
            self.attached = False
            self._update_tracking()


class MMSStepper:
    """Main stepper motor control class"""
    def __init__(self, name):
        self.name = name
        self.reactor = printer_adapter.get_reactor()

        # Configuration
        self.s_config = StepperConfig()

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
        self._mcu_stepper = None
        self._mcu = None

        # Initialize movement dispatch with self
        self.move_dispatch_dct = {
            MoveType.MANUAL_MOVE: ManualMoveDispatch(self),
            MoveType.MANUAL_HOME: ManualHomeDispatch(self),
            MoveType.DRIP_MOVE: DripMoveDispatch(self),
            MoveType.BUFFER_FEED: BufferMoveDispatch(self),
        }

        # Register connect handler to printer
        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)

    def _handle_klippy_connect(self):
        self._initialize_loggers()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=False)
        self.log_warning = mms_logger.create_log_warning(console_output=False)
        # self.log_error = mms_logger.create_log_error()

    def is_init(self):
        # Init status, never run before
        return self.move_status == MoveStatus.READY

    def pause(self, period_seconds):
        self.reactor.pause(
            self.reactor.monotonic() + period_seconds
        )

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

    def get_rotation_distance(self):
        return self.get_mcu_stepper().get_rotation_distance()[0]

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
            "is_running" : self.is_running(),
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

    def get_mms_name(self):
        return self.mms_name

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
        return 0 if not dispatch else dispatch.get_steps_moved()

    def get_distance_moved(self):
        dispatch = self.get_dispatch()
        return 0 if not dispatch else dispatch.get_distance_moved()

    def get_position(self):
        # Return current position
        return self.get_mcu_stepper().get_commanded_position() or 0

    def get_step(self):
        # Return current MCU steps
        return self.get_mcu_stepper().get_mcu_position() or 0

    def reset_position(self):
        # Reset position to avoid "Stepcompress error"
        self.get_mcu_stepper().set_position((0., 0., 0.))
        # Notice: ManualStepper may save ManualStepper.commanded_pos
        # itself, which use to calculate distance in homing.
        # So update position not only with MCU_Stepper but alse ManualStepper.
        ms_adapter = ms_dispatch.get_adapter(self.name)
        ms_adapter.reset_position()

    def set_trapq(self, trapq):
        return self.get_mcu_stepper().set_trapq(trapq)

    def move_is_completed(self, move_status=None):
        move_status = move_status or self.move_status
        return move_status == MoveStatus.COMPLETED

    def move_is_terminated(self, move_status=None):
        move_status = move_status or self.move_status
        return move_status == MoveStatus.TERMINATED

    def move_is_error(self, move_status=None):
        move_status = move_status or self.move_status
        return move_status == MoveStatus.ERROR

    def is_homing_to(self, mcu_pin):
        mt_home = MoveType.MANUAL_HOME
        dispatch = self.get_dispatch(mt_home)
        return self.move_status is MoveStatus.MOVING \
            and self.move_type is mt_home \
            and dispatch.is_destination(mcu_pin)

    def _log_review(self, move_type, distance):
        distance_m = self.get_distance_moved()
        gap = distance_m - distance
        if abs(int(gap)) > 0:
            self.log_warning("\n"
                "##########\n"
                f"{move_type} review:\n"
                f"step_dist : {self.get_step_dist():.4f} mm\n"
                f"steps : {self.get_steps_moved()}\n"
                f"request : {distance:.2f} mm\n"
                f"moved: {distance_m:.2f} mm\n"
                f"moved-request = {gap:.2f} mm\n"
                "##########"
            )

    # ---- Control ----
    def is_running(self):
        return self._is_running

    def _set_is_running(self, move_type, forward=None, slot_num=None,
                        print_time=None):
        if self.is_running():
            msg = f"[{self.mms_name}] is still running ({self.move_type})"
            if self.move_type == MoveType.EXTRUDER_SYNC \
                and self.move_status == MoveStatus.ERROR:
                slot = self._focus_slot
                msg += ("; sync cleanup pending; retry "
                        "MMS_SLOT_EXTRUDER_UNSYNC SLOT="
                        f"{slot if slot is not None else '<slot>'}")
            raise CommandError(msg)

        self._is_running = True
        self.move_type = move_type
        if forward is not None:
            self._forward = forward
        if slot_num is not None:
            self._focus_slot = slot_num
        try:
            self._reset_move_status(MoveStatus.MOVING)
            printer_adapter.notify_mms_stepper_running()
            if print_time is None:
                self.enable()
            else:
                self.enable(print_time)
        except Exception:
            self.move_status = MoveStatus.ERROR
            self._free_is_running()
            raise

    def _free_is_running(self):
        if not self.is_running():
            return

        self._is_running = False
        if self.move_status == MoveStatus.MOVING:
            # Not completed/terminated/error, mark as expired.
            self._update_move_status(MoveStatus.EXPIRED)
        printer_adapter.notify_mms_stepper_idle()

    def handle_is_not_running(self):
        if self.move_type == MoveType.BUFFER_FEED:
            # An armed supply queue may intentionally remain stationary.
            return
        # Unchanged commanded steps do not prove that the motion owner exited.
        self.log_warning(
            f"[{self.mms_name}] no step change observed; "
            f"still owned by {self.move_type}")

    def _cal_enable_print_time(self):
        # Warning, don't adde interval with print_time here
        cal_pt = self._cal_print_time(add_interval=False)
        th_pt = toolhead_a.get_print_time()
        # self.log_info(
        #     f"[{self.mms_name}] print_time:{cal_pt:.2f}, "
        #     f"toolhead print_time:{th_pt:.2f}"
        # )
        return max(cal_pt, th_pt)

    def enable(self, print_time=None):
        if not stepper_enable_a.is_motor_enabled(self.name):
            pt = (self._cal_enable_print_time() if print_time is None
                  else print_time)
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
    def _stepper_is_running(self, move_type, forward=None, slot_num=None,
                            print_time=None):
        self._set_is_running(move_type, forward, slot_num, print_time)
        try:
            yield
        except Exception:
            self.move_status = MoveStatus.ERROR
            raise
        finally:
            self._free_is_running()

    def _cal_print_time(self, add_interval=False):
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
            print_time += self.interval_time
        return print_time

    def _adjust_print_time(self):
        print_time = self._cal_print_time(add_interval=True)
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
            print_time = self._cal_print_time(add_interval=True)
        # A different motor or E may have advanced native generation while
        # this owner waited. Never append before that committed frontier.
        return max(print_time, motion_queuing_a.calc_step_gen_restart(
            self._cal_print_time()))

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
    # -- Manual Move --
    def manual_move(self, distance, speed, accel, slot_num=None, *,
                    background=False, check=None):
        if not distance:
            return

        mv_type = MoveType.MANUAL_MOVE
        # The active Klipper queue provides the scheduling lead for this
        # move. Avoid the standalone interval and a toolhead lookahead flush.
        start_time = None
        if background:
            restart = motion_queuing_a.calc_step_gen_restart(
                self._cal_print_time())
            start_time = max(restart, self._end_print_time)
        with self._stepper_is_running(
            mv_type, forward=distance >= 0, slot_num=slot_num,
            print_time=start_time
        ):
            try:
                print_time = (self._adjust_print_time()
                              if start_time is None else start_time)
                toolhead_a.check_motion(require_owner=False)
                if check is not None:
                    check()
                dispatch = self.get_dispatch(mv_type)
                self._end_print_time = dispatch.execute(
                    print_time, distance, speed, accel, background=background
                )
            finally:
                self._log_review(mv_type, distance)

    # -- Manual Home --
    def manual_home(
        self, distance, speed, accel, forward, trigger, endstop_pair_lst,
        slot_num=None, check_endstop=None
    ):
        if not distance:
            return

        mv_type = MoveType.MANUAL_HOME
        distance = abs(distance) if forward else -abs(distance)
        with self._stepper_is_running(
            mv_type, forward=forward, slot_num=slot_num
        ):
            try:
                self._can_calibrate = True
                self._sync_print_time()
                toolhead_a.check_motion(require_owner=False)
                dispatch = self.get_dispatch(mv_type)

                endstop_name = dispatch.execute(
                    distance, speed, accel, trigger, endstop_pair_lst
                )
                if check_endstop is not None:
                    # Native homing has drained its movement. Read the target
                    # before releasing this owner; button edges alone may be
                    # delayed, or may describe the opposite transition.
                    reached = check_endstop()
                    if not self.move_is_terminated():
                        if reached:
                            self.complete_manual_home()
                        else:
                            self.move_status = MoveStatus.MOVING
                elif (endstop_name is not None
                      and self.get_distance_moved() == 0):
                    self.complete_manual_home()
                if self.move_is_completed() and self.get_distance_moved() == 0:
                    self._can_calibrate = False
            except (CommandError, MCUError) as e:
                gcode_adapter.respond_error(e)
                raise
            finally:
                self.log_status()

        return self.move_status

    def complete_manual_home(self):
        if not self.is_running():
            self.log_warning(
                f"[{self.mms_name}] is not running, "
                "complete failed"
            )
            return
        self._update_move_status(MoveStatus.COMPLETED)

    def can_calibrate(self):
        return self._can_calibrate

    # -- Drip Move --
    def drip_move(self, distance, speed, accel, slot_num=None,
                  check=None, stop_when=None):
        if not distance:
            return
        mv_type = MoveType.DRIP_MOVE
        with self._stepper_is_running(
                mv_type, forward=distance >= 0, slot_num=slot_num):
            print_time = self._adjust_print_time()
            toolhead_a.check_motion(require_owner=False)
            dispatch = self.get_dispatch(mv_type)
            options = {}
            if check is not None or stop_when is not None:
                options = dict(check=check, stop_when=stop_when)
            self._end_print_time = dispatch.execute(
                print_time, distance, speed, accel, **options)

    def get_executed_position(self, print_time):
        stepper = self.get_mcu_stepper()
        return (stepper.get_past_mcu_position(print_time)
                * stepper.get_step_dist())

    def get_generated_position(self):
        stepper = self.get_mcu_stepper()
        return stepper.get_mcu_position() * stepper.get_step_dist()

    # -- Terminate --
    def terminate_moving(self):
        if self.is_running() and self.move_type == MoveType.BUFFER_FEED:
            self.get_dispatch(MoveType.BUFFER_FEED).stop()
            return
        if not self.is_running() \
            or self.move_type not in (
                MoveType.MANUAL_HOME,
                MoveType.DRIP_MOVE
            ):
            return
        self.log_info(f"[{self.mms_name}] terminate {self.move_type}")
        self.get_dispatch(self.move_type).terminate()
        self._update_move_status(MoveStatus.TERMINATED)


class MMSSelector(MMSStepper):
    def __init__(self, name):
        super().__init__(name)
        self.mms_name = "stepper selector"
        self.interval_time = self.s_config.interval_time_selector

    def get_unselect_distance(self):
        sel_pins_cnt = 4
        benchmark_distance = self.get_rotation_distance() \
            // sel_pins_cnt // 2
        return benchmark_distance


class MMSDrive(MMSStepper):
    def __init__(self, name):
        super().__init__(name)
        self.mms_name = "stepper drive"
        self.interval_time = self.s_config.interval_time_drive

        self._org_trapq = None
        self._borrowed_feed_generation = None
        self._is_syncing = False

    def start_buffer_feed(self, slot_num):
        now = self._cal_print_time()
        start = motion_queuing_a.calc_step_gen_restart(now)
        self._set_is_running(MoveType.BUFFER_FEED, slot_num=slot_num,
                             print_time=start)
        try:
            self.get_dispatch(MoveType.BUFFER_FEED).start(start)
        except Exception:
            self.move_status = MoveStatus.ERROR
            self._free_is_running()
            raise

    def append_buffer_feed(self, step_gen_time, velocity):
        self.get_dispatch(MoveType.BUFFER_FEED).append(step_gen_time, velocity)

    def stop_buffer_feed(self, wait=True):
        if self.move_type != MoveType.BUFFER_FEED or not self.is_running():
            return True
        dispatch = self.get_dispatch(MoveType.BUFFER_FEED)
        dispatch.stop()
        if not wait:
            return False
        try:
            dispatch.finish()
        except Exception:
            self.move_status = MoveStatus.ERROR
            raise
        finally:
            if not dispatch.attached:
                self._free_is_running()
        return True

    def sync_to_extruder(self, slot_num=None, feed_generation=None):
        # Explicit SYNC lasts until UNSYNC or the exhausted strand is released.
        if (self._is_syncing and self.move_type == MoveType.EXTRUDER_SYNC
                and self.move_status != MoveStatus.ERROR):
            return
        self._set_is_running(MoveType.EXTRUDER_MOVE, slot_num=slot_num)
        self._borrowed_feed_generation = feed_generation
        try:
            self._sync_to_extruder()
            self.move_type = MoveType.EXTRUDER_SYNC
        except Exception:
            self.move_status = MoveStatus.ERROR
            self._finish_extruder_sync()
            raise

    def _sync_to_extruder(self):
        toolhead_a.flush_step_generation()
        extruder = toolhead_a.get_extruder()
        mcu_stepper = self.get_mcu_stepper()
        mcu_stepper.set_position([extruder.last_position, 0., 0.])
        self._org_trapq = mcu_stepper.set_trapq(extruder.get_trapq())
        self._is_syncing = True
        motion_queuing_a.check_step_generation_scan_windows()

    def unsync_to_extruder(self):
        # A manual command must not detach an active charge/eject/purge scope.
        if self.is_running() and self.move_type != MoveType.EXTRUDER_SYNC:
            raise CommandError(
                f"[{self.mms_name}] is still running ({self.move_type})")
        if self._is_syncing:
            self.move_type = MoveType.EXTRUDER_MOVE
            self._finish_extruder_sync()
            generation = self._borrowed_feed_generation
            self._borrowed_feed_generation = None
            return generation

    def _finish_extruder_sync(self):
        try:
            if self._is_syncing:
                # A fault can follow accepted E before its normal wait. Keep
                # drive ownership until that physical segment has drained;
                # cancellation must not bypass this cleanup wait.
                toolhead_a.wait_moves(check=False)
                toolhead_a.flush_step_generation()
                self.get_mcu_stepper().set_trapq(self._org_trapq)
                self._is_syncing = False
                motion_queuing_a.check_step_generation_scan_windows()
        except Exception as e:
            self.move_status = MoveStatus.ERROR
            if self._is_syncing:
                slot = self._focus_slot
                raise CommandError(
                    f"[{self.mms_name}] sync cleanup failed; drive remains "
                    "synchronized; correct the error and retry "
                    "MMS_SLOT_EXTRUDER_UNSYNC SLOT="
                    f"{slot if slot is not None else '<slot>'}: {e}") from e
            raise
        finally:
            if self._is_syncing:
                # Cleanup did not detach the trapq. Keep ownership, but let an
                # explicit UNSYNC retry after this failed scope has unwound.
                self.move_type = MoveType.EXTRUDER_SYNC
            else:
                self._free_is_running()

    @contextmanager
    def sync_with_extruder(self):
        self._set_is_running(MoveType.EXTRUDER_MOVE)
        self._borrowed_feed_generation = None
        try:
            self._sync_to_extruder()
            yield
        except Exception:
            self.move_status = MoveStatus.ERROR
            raise
        finally:
            self._finish_extruder_sync()
