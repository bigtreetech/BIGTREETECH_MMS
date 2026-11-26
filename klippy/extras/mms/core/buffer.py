# Support for MMS Buffer
#
#   +----------------------------------------------+
# ==|= Gate =|                                  |  |
# ==|= Gate =|                                  |  |
#   |        += Runout =\/\/\/\/\/\/\/= Outlet =|==| Extruder
# ==|= Gate =|          |<= Spring =>|         /   | --> Extrude
# ==|= Gate =|      Relax <--    --> Compress /    | <-- Retract
#   +----------------------------------------+
#
# Copyright (C) 2025-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import math
from contextlib import contextmanager
from dataclasses import dataclass, fields

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    motion_queuing_adapter,
    pause_resume_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.exceptions import DeliveryTerminateSignal, MMSException
from ..core.task import PeriodicTask


@dataclass(frozen=True)
class BufferConfig:
    """
    Spring of Buffer

    Max relaxation
        /\/\/\/\/\/\/\/\/\/\/
    Max compression
        ||||||
             |<-- Stroke -->|
      max_compression   min_compression
    """
    # Spring's stroke between max compression and max relaxation, in mm
    spring_stroke: float = 20.0
    # Diameter of filament loaded in buffer, in mm
    filament_diameter: float = 1.75

    # Target percentage of buffer, in % rate
    target_percentage: float = 50.0

    # Feed configs
    # Minimum feed volume, in mm^3
    min_deliver_volume: float = 2.0

    # Spring stroke measure config
    # Max distance of Spring stroke measure, in mm
    max_measure_distance: float = 30.0
    min_measure_distance: float = 5.0
    # Distance of per step, in mm
    measure_speed: float = 5.0
    measure_accel: float = 5.0

    # Period of Extruder monitor task, in second
    monitor_period: float = 0.2

    # sprint_speed: float = 50 # mm/s
    # sprint_accel: float = 150 # mm/s^2

    # ---- Calculate ----
    # Cross section of filament, in mm^2
    filament_cross_section: float = math.pi * (
        (filament_diameter * 0.5) ** 2
    )
    # The total capacity of buffer
    buffer_capacity: float = filament_cross_section * spring_stroke
    buffer_empty: float = 0.0

    # Buffer volume configs
    max_volume: float = buffer_capacity
    min_volume: float = buffer_empty
    target_volume: float = (max_volume - min_volume) * target_percentage / 100

    # max_distance: float = spring_stroke * target_percentage / 100
    # min_distance: float = 2.0


class Buffer:
    _loading_filament = False

    def __init__(self, target_percentage=50., max_velocity=80.,
                 max_accel=120.):
        if not 0. < target_percentage < 100.:
            raise ValueError(
                "buffer target percentage must be between 0 and 100")
        self.target_percentage = target_percentage
        self.max_velocity = max_velocity
        self.max_accel = max_accel
        buffer_config = BufferConfig()
        for field in fields(buffer_config):
            key = field.name
            # Don't cover
            if not hasattr(self, key):
                val = getattr(buffer_config, key)
                setattr(self, key, val)
        self.target_volume = ((self.max_volume - self.min_volume)
                              * self.target_percentage / 100.)

        # Index of MMS Buffer
        self._index = None
        # Sensor setting
        self._sensor_full = None
        self._sensor_runout = None
        # The slot_nums binding to current MMS Buffer
        self._binding_slot_nums = []

        # None means the spring has not reached a measured reference yet.
        self._volume = None
        # Executed E/drive step counters at one shared print time.
        self._last_positions = None
        # Calibrated release edge, distinct from the low trigger position.
        self._low_release_volume = None
        # Monitor periodic task
        self._p_task = None

        # Flags
        # Spring stroke measure flag
        self._stroke_is_measured = False
        # Inlet triggered flag
        self._low_warned = False
        self._idle_refill_blocked = False
        self._low_attempt_start = None
        self._low_diagnostic = None
        # One temporarily claimed source, not a second loaded-slot authority.
        self._feed_slot = self._feed_drive = None
        self._feed_origin = 0.
        self._feed_join = None
        self._tail_release = None

        # Printer status
        self._is_ready = False
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_loggers()
        self._initialize_task()
        self._is_ready = True

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()
        self.mms_resume = self.mms.get_mms_resume()
        self.mms_fil_detection = self.mms.get_mms_filament_detection()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)

        self.log_info_s = mms_logger.create_log_info(console_output=False)
        self.log_warning_s = mms_logger.create_log_warning(
            console_output=False)

    def _initialize_task(self):
        self._p_task = PeriodicTask()
        self._p_task.set_period(self.monitor_period)

    # ---- SLOTs manager ----
    def register_slot_num(self, slot_num):
        if slot_num not in self._binding_slot_nums:
            self._binding_slot_nums.append(slot_num)

    def has_gate_triggering(self):
        for slot_num in self._binding_slot_nums:
            mms_slot = self.mms.get_mms_slot(slot_num)
            if mms_slot.gate.is_triggered():
                return True
        return False

    def has_slot(self, slot_num):
        return slot_num in self._binding_slot_nums

    # ---- Monitor ----
    def _positions(self, slot_num, print_time=None):
        drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        if print_time is None:
            print_time = drive.get_mcu().estimated_print_time(
                self._p_task.reactor.monotonic())
        return (print_time,
                extruder_adapter.get_executed_position(print_time),
                drive.get_executed_position(print_time))

    def _update_motion(self, slot_num, after_side_move=False):
        current = self._positions(slot_num)
        previous = self._last_positions
        self._last_positions = current
        if previous is None:
            return 0.
        elapsed = current[0] - previous[0]
        if elapsed <= 0.:
            return 0.
        e_moved = current[1] - previous[1]
        # While a side slot was selected, only E consumed this reserve.
        delivered = 0. if after_side_move else current[2] - previous[2]
        if self._volume is not None:
            self._set_volume(
                self._volume + delivered * self.filament_cross_section
                - e_moved * extruder_adapter.get_extruder_filament_area())
        return e_moved / elapsed


    def _monitor(self):
        if not self.is_activating():
            return
        generation = self.mms_resume.get_status()["generation"]
        try:
            slot_num = self._feed_slot
            if not self.mms_resume.can_drive_filament(slot_num):
                self.request_tail_release(slot_num)
                return
            self.mms_fil_detection.check(slot_num)
            e_speed = self._update_motion(slot_num)
            self._check_sensors()
            horizon = motion_queuing_adapter.get_last_step_gen_time()
            self._check_low_feedback(horizon, e_speed)
            self._log_low_supply()
            # Idle stock correction uses the same native flush callback as E.
            # Do not keep an idle clock running once the motor has settled.
            dispatch = self._feed_drive.get_dispatch("buffer_feed")
            if ((self._stroke_is_measured and self._volume is not None
                 and not self._idle_refill_blocked
                 and (self._volume < self.target_volume
                      - self.min_deliver_volume
                      or self._volume > self.max_volume))
                    or abs(dispatch.velocity) > 1.e-6):
                now = self._last_positions[0]
                motion_queuing_adapter.note_mcu_movequeue_activity(
                    now + self.monitor_period)
        except DeliveryTerminateSignal:
            return
        except Exception as e:
            self.log_error(f"buffer[{self._index}] control failed: {e}")
            self.pause_after_failure(generation, str(e))

    def tail_release_pending(self, slot_num):
        request = (slot_num, self.mms_resume.get_status()["generation"])
        return self._tail_release == request

    def request_tail_release(self, slot_num):
        generation = self.mms_resume.get_status()["generation"]
        request = (slot_num, generation)
        if self._tail_release == request:
            return
        slot = self.mms.get_mms_slot(slot_num)
        if (self._feed_slot != slot_num and not slot.selector_is_triggered()
                and slot.get_mms_selector().get_focus_slot() != slot_num):
            return

        def current():
            return (self._tail_release is request
                    and self.mms_resume.get_status()["generation"]
                    == generation
                    and self._feed_slot in (None, slot_num))

        def release(eventtime):
            try:
                gcode = printer_adapter.get_printer().lookup_object("gcode")
                while current():
                    with gcode.get_mutex():
                        if not current():
                            return
                        owner = self.mms_delivery.async_task_sp
                        if not owner.is_running():
                            if not print_stats_adapter.is_printing():
                                self.mms_delivery.release_tail(slot_num)
                                return
                            # Claim the delivery owner under the lock. The
                            # physical drain and release run outside it.
                            completion = self.mms_delivery.start_print_tail_release(
                                slot_num)
                            break
                        completion = owner.completion
                    # WAIT=0 delivery can outlive its G-code lock.
                    completion.wait()
                else:
                    return
                result = completion.wait()
                if isinstance(result, Exception):
                    raise result
            except DeliveryTerminateSignal:
                return
            except Exception as e:
                self.log_error(f"buffer tail release failed: {e}")
                self.pause_after_failure(generation, str(e))
            finally:
                if self._tail_release is request:
                    self._tail_release = None

        self._tail_release = request
        printer_adapter.get_reactor().register_callback(release)

    def _queue_feed(self, flush_time, step_gen_time):
        # Native assert_no_pause callback: no waiting, G-code or toolhead edits.
        if not self.is_activating():
            return
        generation = self.mms_resume.get_status()["generation"]
        try:
            following_tail = not self.mms_resume.can_drive_filament(
                self._feed_slot)
            if following_tail and not (
                    self.mms_resume.is_extruder_tail(self._feed_slot)
                    and self.mms_resume.has_tail_grip(self._feed_slot)):
                # A free tail or a new strand in Inlet cannot drive this E.
                self._feed_drive.append_buffer_feed(
                    step_gen_time, lambda *args: (0., 0.))
                return
            e_speed = self._update_motion(self._feed_slot)
            self._check_sensors()
            motion = extruder_adapter.get_planned_motion(
                self._last_positions[0])
            if not following_tail:
                self._check_low_feedback(step_gen_time, e_speed, motion)
            self._feed_drive.append_buffer_feed(
                step_gen_time,
                lambda *args: self._feed_velocity(
                    *args, motion, following_tail))
        except Exception as e:
            self.log_error(f"buffer[{self._index}] queue failed: {e}")
            self.pause_after_failure(generation, str(e))

    def _check_low_feedback(self, horizon, e_speed, motion=None):
        if not self.is_empty():
            return
        now = self._last_positions
        positive = (e_speed > 0.
                    or extruder_adapter.get_generated_position() > now[1])
        if not positive:
            if motion is None:
                motion = extruder_adapter.get_planned_motion(now[0])
            positive = motion.interval(
                now[0], max(now[0], horizon) + self.monitor_period)[1] > 0.
        if positive:
            self._idle_refill_blocked = False
        if self._low_attempt_start is None:
            self._low_attempt_start = now[2]
        if now[2] - self._low_attempt_start < self.max_measure_distance:
            return
        if not self._low_warned:
            self.log_warning(
                "buffer remains low after refill; E demand keeps feeding, "
                "idle retries wait for new E demand")
            self._low_warned = True
            self._log_feedback("refill still low")
        if not positive:
            self._idle_refill_blocked = True

    def _log_low_supply(self):
        # Monitor-only diagnostics: no extra work in native step generation.
        # Executed motor steps describe scheduling, not actual filament travel.
        if not self.is_empty():
            return
        current = self._last_positions
        previous = self._low_diagnostic
        if previous is None:
            self._low_diagnostic = current
            return
        elapsed = current[0] - previous[0]
        if elapsed < 5.:
            return
        self._low_diagnostic = current
        dispatch = self._feed_drive.get_dispatch("buffer_feed")
        self._log_feedback(
            f"low supply slot={self._feed_slot} "
            f"elapsed={elapsed:.3f}s "
            f"E_delta={current[1] - previous[1]:.3f}mm "
            f"MMS_delta={current[2] - previous[2]:.3f}mm "
            f"active={int(self.is_activating())} "
            f"running={int(self._feed_drive.is_running())} "
            f"stopping={int(dispatch.stopping)} "
            f"idle_blocked={int(self._idle_refill_blocked)} "
            f"print_time={current[0]:.3f} "
            f"queued_until={dispatch.print_time:.3f}")

    def _feed_velocity(self, print_time, position, current, duration, motion,
                       following_tail=False):
        now = self._last_positions
        area = self.filament_cross_section
        ratio = extruder_adapter.get_extruder_filament_area() / area
        # Follow E demand within this feeder's mechanical acceleration.
        # Use the same bound for trajectory generation and braking room.
        accel = min(motion.acceleration * ratio, self.max_accel)
        demand = motion.interval(print_time, print_time + duration)[0]
        demand *= ratio / duration
        # Exhausting idle refill cannot suppress planned reverse-E relief.
        # Keep the idle block until feedback or positive E clears it.
        if not following_tail and self._idle_refill_blocked and demand >= 0.:
            return 0., accel
        if (following_tail or not self._stroke_is_measured
                or self._volume is None):
            # An unreferenced feed or a clamped tail can track forward and
            # reverse E. Only a live supply can add stock at the low switch.
            target = demand
            if not following_tail and target > 0. and self.is_empty():
                target += self.measure_speed
            if self.is_full():
                target = min(0., target)
            target = max(-self.max_velocity, min(target, self.max_velocity))
            return target, accel
        base = self._volume
        low = self.is_empty()
        if low:
            # A held low switch requires refill even after lost motor steps.
            # Its release calibration describes a different physical edge.
            base = self.min_volume
        elif self.is_full():
            base = max(base, self.max_volume)
        planned = motion.interval(now[0], print_time)[0]
        reserve = (base / area + self._feed_origin + position - now[2]
                   - planned * ratio)
        correction = (self.target_volume / area - reserve) / self.monitor_period
        target = demand + correction
        if low and self._has_low_release_reference():
            # The release edge bounds possible stock while low is held. Use
            # that upper bound only for braking room, never to cancel refill.
            reserve += (self._low_release_volume - self.min_volume) / area
        return_time = duration + motion_queuing_adapter.get_scan_delay()
        if accel:
            return_time += max(0., -demand) / accel
        distance, positive, _, _ = motion.interval(
            print_time, print_time + return_time)
        returned = positive - distance
        near_full = (reserve + returned * ratio
                     >= (self.max_volume - self.min_deliver_volume) / area)
        if (self._volume <= self.max_volume
                and (returned <= 0. or not near_full)):
            # Let the spring absorb short retracts and retain quiet high stock.
            # Start a real reverse early enough to absorb its acceleration.
            target = max(0., target)
        # E and feeder trajectories share this future time. Look ahead far
        # enough to brake before E stops; a steady E rate is not itself extra
        # buffer filling and must not inherit the load/unload default speed.
        window = duration + motion_queuing_adapter.get_scan_delay()
        brake_time = abs(current) / accel if accel else 0.
        stop_time = print_time + window + brake_time + duration
        distance, positive, low_e, high_e = motion.interval(
            print_time, stop_time)
        if target >= 0.:
            room = self.max_volume / area - reserve
            room -= (positive - distance) * ratio
            following = max(0., low_e * ratio)
        else:
            room = reserve - self.min_volume / area - positive * ratio
            following = max(0., -high_e * ratio)
        # A later E stop must not discard consumption guaranteed during the
        # committed window. Credit only its minimum same-direction speed;
        # no later consumption may hide an earlier buffer overflow.
        _, _, near_low, near_high = motion.interval(
            print_time, print_time + window)
        near_following = max(0., (near_low if target >= 0. else -near_high)
                             * ratio)
        room += max(0., near_following - following) * window
        room = max(0., room)
        if accel:
            extra = 2. * room / (
                math.sqrt(window * window + 2. * room / accel) + window)
        else:
            extra = room / window
        limit = min(following + extra, self.max_velocity)
        target = max(-limit, min(limit, target))
        if self.is_full():
            # E steps may continue during a jam. Wait for actual full release
            # before adding forward feed; the native queue retains braking.
            target = min(0., target)
        return target, accel

    def pause_after_failure(self, generation, reason):
        # Klipper clears this timestamp when the job ends. An idle manual
        # extrusion has its own valid fault even after a previous print.
        print_start = print_stats_adapter.get_print_start_time()

        def current():
            return (
                self.mms_resume.get_status()["generation"] == generation
                and not self.mms.printer_is_shutdown()
                and print_stats_adapter.get_print_start_time() == print_start
            )

        if not current():
            return
        # Publish failure before yielding for the G-code mutex: an ordinary
        # PAUSE may already be draining E and must not rearm this failed feed.
        was_active = self.is_activating()
        had_failure = bool(self.mms_resume.get_status()["reason"])
        self.mms_resume.note_failure(reason)
        # Failed stop cleanup is repaired by the next explicit retry. Once
        # paused, do not recursively schedule parking/release/stop again.
        if (not was_active and had_failure
                and pause_resume_adapter.is_paused()):
            return
        if was_active:
            try:
                self.deactivate_monitor(wait=False)
            except Exception as error:
                self.log_error(
                    f"buffer fault stop failed: {error}; continue pause")

        # Foreground MMS operations check this scope during E/XY and waits.
        # Their existing failure path owns stopping and parking the task.
        in_operation = toolhead_adapter.fail_motion(reason)
        if in_operation and (
                self.mms_resume.is_resuming()
                or printer_adapter.get_mms_swap().get_status()[
                    "slot_num_to"] is not None):
            return

        def pause(eventtime):
            if not current():
                return
            mms_pause = self.mms.get_mms_pause()
            with mms_pause.background_pause(current) as owned:
                if not owned:
                    return
                self.mms_resume.capture_return_state()
                pause_generation = self.mms_resume.get_status()["generation"]
                try:
                    mms_pause.mms_pause(force=True, generation=pause_generation)
                except Exception as error:
                    self.log_error(f"buffer fault parking failed: {error}")
                if (pause_generation == self.mms_resume.get_status()[
                        "generation"] and pause_resume_adapter.is_paused()):
                    try:
                        self.mms_resume.release_for_manual()
                    except Exception as error:
                        self.log_error(
                            f"buffer fault selector release failed: {error}")

        # Leave this buffer callback before PAUSE joins its feed monitor.
        printer_adapter.get_reactor().register_callback(pause)

    def _set_volume(self, new_volume):
        self._volume = new_volume

    def _log_feedback(self, event):
        if not self._is_ready:
            return
        velocity = None
        if self._feed_drive is not None:
            velocity = self._feed_drive.get_dispatch("buffer_feed").velocity
        self.log_info_s(
            f"buffer[{self._index}] {event}: "
            f"low={int(self.is_empty())} full={int(self.is_full())} "
            f"volume={self._volume} target={self.target_volume} mm^3 "
            f"low_release={self._low_release_volume} mm^3 "
            f"queued_velocity={velocity} mm/s")

    def get_feed_slot(self):
        return self._feed_slot if self.is_activating() else None

    def activate_monitor(self, slot_num=None, allow_unreferenced=False,
                         after_side_move=False):
        if not self._p_task or self._p_task.is_running():
            if self.is_activating() and slot_num not in (None, self._feed_slot):
                raise MMSException("buffer already feeds another slot")
            return
        if ((self._volume is None and not allow_unreferenced)
                or self._feed_drive is not None):
            return
        if slot_num is None:
            slot_num = self.mms_resume.identify_source_slot()
        if (slot_num not in self._binding_slot_nums
                or not self.mms_delivery.is_exclusively_selected(slot_num)):
            return
        if not self.mms_resume.can_drive_filament(slot_num):
            return
        self._feed_slot = slot_num
        self._feed_drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        try:
            self._update_motion(slot_num, after_side_move=after_side_move)
            self._feed_origin = self._feed_drive.get_generated_position()
            self._feed_drive.start_buffer_feed(slot_num)
            self.mms_fil_detection.activate(slot_num)
            if not self._p_task.schedule(self._monitor):
                raise MMSException("buffer observation could not be scheduled")
            if not self._p_task.start():
                raise MMSException("buffer observation could not start")
            motion_queuing_adapter.register_feed_callback(self._queue_feed)
            self._log_feedback("monitor activated")
        except Exception as e:
            self.log_error(f"buffer[{self._index}] activate failed: {e}")
            try:
                # Revoke now; the command/fault owner must stop input before
                # waiting for accepted motor steps to drain.
                self.deactivate_monitor(wait=False)
            except Exception as stop_error:
                self.log_error(
                    f"buffer activation cleanup failed: {stop_error}")
            raise


    def deactivate_monitor(self, wait=True, deadline=None, *,
                           release_tail=False):
        if not self._p_task:
            return True
        # A confirmed handoff can be needed even without a feed monitor.
        # A temporary grip loss only belongs to this particular monitor.
        pending = self._tail_release
        if release_tail or (pending and
                            not self.mms_resume.is_extruder_tail(pending[0])):
            self._tail_release = None
        self._p_task.stop()
        self._low_diagnostic = None
        motion_queuing_adapter.unregister_feed_callback(self._queue_feed)
        drive, slot_num = self._feed_drive, self._feed_slot
        if slot_num is not None:
            self.mms_fil_detection.deactivate(slot_num)
        if drive is not None:
            drive.stop_buffer_feed(wait=False)
        if not wait:
            if drive is not None:
                generation = self.mms_resume.get_status()["generation"]
                def finish(eventtime):
                    # A synchronous caller may already be draining this stop,
                    # or a newer feed may have started after that join.
                    if (self._feed_drive is not drive or self.is_activating()
                            or self._feed_join is not None):
                        return
                    try:
                        self.deactivate_monitor()
                    except Exception as e:
                        self.log_error(f"buffer stop failed: {e}")
                        self.pause_after_failure(generation, str(e))
                printer_adapter.get_reactor().register_callback(finish)
            return drive is None
        if self._feed_join is not None:
            result = (self._feed_join.wait() if deadline is None
                      else self._feed_join.wait(deadline))
            if isinstance(result, Exception):
                raise result
            if result is None:
                raise MMSException("buffer stop is still pending")
            return True
        completion = self._p_task.reactor.completion()
        self._feed_join = completion
        try:
            if self._p_task.is_running():
                if deadline is None:
                    deadline = (self._p_task.reactor.monotonic()
                                + self.mms_delivery.d_config.
                                wait_mms_stepper_timeout)
                if not self._p_task.wait_stopped(deadline):
                    raise MMSException("buffer observer is still exiting")
            try:
                if drive is not None:
                    drive.stop_buffer_feed()
            finally:
                if drive is None or not drive.is_running():
                    try:
                        if drive is not None:
                            self._update_motion(slot_num)
                    finally:
                        self._feed_drive = self._feed_slot = None
            completion.complete(True)
        except Exception as e:
            completion.complete(e)
            raise
        finally:
            self._feed_join = None
        self.log_info_s(f"buffer[{self._index}] monitor deactivated")
        return True

    # ---- Feed & Retract ----

    def _find_slot(self, no_log=False):
        slot_num = self.mms_resume.get_exhausted_slot()
        if slot_num is None or not self.mms_resume.is_tail_available(slot_num):
            slot_num = self.mms.get_current_slot(no_log=no_log)
        if slot_num is None and not no_log:
            self.log_warning(
                f"buffer[{self._index}] no available slot found")
        return slot_num







    # ---- Control ----
    def fill(self, slot_num):
        return self._position_at_limit(slot_num, full=True)

    def clear(self, slot_num):
        if not self._position_at_limit(slot_num, full=False):
            return False
        # Keep the requested low endpoint until E needs material again.
        self._idle_refill_blocked = True
        return True

    def _position_at_limit(self, slot_num, full):
        at_limit = self.is_full if full else self.is_empty
        self._refresh_limits(slot_num)
        if at_limit():
            return True
        if not self._stroke_is_measured and not self.measure_stroke(slot_num):
            return False
        if not at_limit():
            self.invalidate_reference()
            for _ in self.mms_delivery.buffer_references(slot_num, full=full):
                self._refresh_limits(slot_num)
                if at_limit():
                    self._set_volume(
                        self.max_volume if full else self.min_volume)
                    break
            self.log_info_s(
                f"slot[{slot_num}] buffer[{self._index}] reached limit: "
                f"low={int(self.is_empty())} full={int(self.is_full())}")
        return True

    def halfway(self, slot_num):
        if not self._stroke_is_measured and not self.measure_stroke(slot_num):
            return False

        try:
            self.invalidate_reference()
            config = self.mms_delivery.pd_config
            for _ in self._working_references(slot_num, force=True):
                self.mms_delivery.move_backward(
                    slot_num, self.spring_stroke * .5,
                    config.sprint_speed, config.sprint_accel)
                self._refresh_limits(slot_num)
                if not (self.is_full() or self.is_empty()):
                    break
            self._last_positions = None
            self._set_volume(self.max_volume * .5)
            self.log_info_s(
                f"slot[{slot_num}] buffer[{self._index}] "
                "halfway success"
            )
            return True

        except DeliveryTerminateSignal:
            self.log_error(
                f"slot[{slot_num}] buffer[{self._index}] "
                "halfway is terminated"
            )
            return False
        except Exception as e:
            self.log_error(
                f"slot[{slot_num}] buffer[{self._index}] "
                f"halfway error: {e}"
            )
            return False

    def measure_stroke(self, slot_num, force=False):
        if self._stroke_is_measured and not force:
            return True

        try:
            self._check_sensors()
            self.invalidate_reference()
            self.log_info_s(
                f"slot[{slot_num}] buffer[{self._index}] "
                "measure stroke begin"
            )

            old_stroke = self.spring_stroke
            self._stroke_is_measured = False
            self._low_release_volume = None
            measured_stroke = self.mms_delivery.measure_buffer(
                slot_num, self.max_measure_distance,
                self.measure_speed, self.measure_accel)

            # Validate against the complete bounded search, including retries.
            # Motor travel remains an estimate; live switches bound feeding.
            search_distance = (self.max_measure_distance
                               * (1 + self.mms_delivery.retry_times))
            if not (self.min_measure_distance <= measured_stroke
                    <= search_distance):
                raise MMSException(
                    f"buffer measured stroke {measured_stroke} mm is outside "
                    f"{self.min_measure_distance}.."
                    f"{search_distance} mm; retry measurement")
            # Homing just established the low trigger point. Measure only
            # to its release edge, then prepare working stock from that edge.
            released = self.mms_delivery.locate_buffer(
                slot_num, self.mms_delivery.pin_type.buffer_runout, True,
                self.max_measure_distance,
                self.measure_speed, self.measure_accel, trigger=False)
            toolhead_adapter.check_motion()
            self._check_sensors()
            if self.is_empty() or not 0. < released < measured_stroke:
                raise MMSException(
                    f"buffer low release {released} mm is inconsistent with "
                    f"stroke {measured_stroke} mm; retry measurement")
            self.spring_stroke = measured_stroke
            self.buffer_capacity = self.filament_cross_section * \
                    self.spring_stroke
            self.max_volume = self.buffer_capacity
            self.target_volume = (self.max_volume - self.min_volume) * \
                self.target_percentage / 100

            self._last_positions = self._positions(slot_num)
            self._low_release_volume = released * self.filament_cross_section
            self._set_volume(self._low_release_volume)
            self._stroke_is_measured = True

            self.log_info_s(
                f"buffer[{self._index}] spring stroke is measured, "
                f"update from {old_stroke} mm to {self.spring_stroke} mm; "
                f"low release={released} mm")
            return True

        except DeliveryTerminateSignal:
            self.log_error(
                f"slot[{slot_num}] buffer[{self._index}] "
                "measure is terminated"
            )
        except Exception as e:
            self.log_error(
                f"slot[{slot_num}] buffer[{self._index}] "
                f"measure stroke error: {e}"
            )
        return False

    def invalidate_reference(self):
        self._low_attempt_start = None
        self._low_warned = False
        self._idle_refill_blocked = False
        self._volume = None
        self._last_positions = None

    def establish_reference(self, slot_num, force=False):
        """Reuse a working reserve or seek a calibrated edge by feeding."""
        next(self._working_references(slot_num, force))

    def _working_references(self, slot_num, force=False):
        self._check_sensors()
        if (not force and self._volume is not None and not self.is_empty()
                and self.min_volume < self._volume <= self.max_volume):
            yield
        toolhead_adapter.check_motion()
        slot = self.mms.get_mms_slot(slot_num)
        if not slot.is_ready():
            raise MMSException(f"slot[{slot_num}] Inlet is empty", slot)
        if self.is_empty():
            self._last_positions = self._positions(slot_num)
            self._set_volume(self.min_volume)
        allow_low = (not force and self._stroke_is_measured
                     and self._has_low_release_reference())
        references = self.mms_delivery.buffer_references(
            slot_num, allow_low_release=allow_low)
        for _, pin in references:
            toolhead_adapter.check_motion()
            self._check_sensors()
            if self.is_full():
                self._last_positions = None
                self._set_volume(self.max_volume)
            elif pin == self.mms_delivery.pin_type.buffer_runout \
                    and not self.is_empty():
                self._last_positions = self._positions(slot_num)
                self._set_volume(self._low_release_volume)
            else:
                continue
            yield

    def prepare_feed(self, slot_num, loading=False):
        """Establish normal supply once, then retain it across E operations."""
        toolhead_adapter.check_motion()
        if not self.is_activating():
            if loading:
                # Loading and manual E can start before print preparation.
                self._refresh_limits(slot_num)
                self.mms_delivery.release_tail(slot_num)
                self.activate_monitor(slot_num, allow_unreferenced=True)
            else:
                self.prepare_for_print(slot_num)
                self.activate_monitor(slot_num)
        self.check_feed(slot_num)

    @contextmanager
    def loading_feed(self, slot_num):
        # E can turn before the gears grip. Use the live limits throughout
        # this move, even if a previous filament calibrated the spring.
        self.invalidate_reference()
        self._loading_filament = True
        try:
            self.prepare_feed(slot_num, loading=True)
            yield
        finally:
            try:
                self.deactivate_monitor()
            finally:
                self._loading_filament = False

    def check_feed(self, slot_num):
        toolhead_adapter.check_motion()
        if self.mms_resume.can_drive_filament(slot_num) and (
                not self.is_activating() or self._feed_slot != slot_num):
            raise MMSException(f"slot[{slot_num}] buffer feeding stopped")

    def prepare_for_print(self, slot_num):
        """Establish reserve and retract headroom before returning to print."""
        self.deactivate_monitor()
        toolhead_adapter.wait_moves()
        toolhead_adapter.check_motion()
        if (not self.mms_resume.is_tail_available(slot_num)
                and self.mms_resume.can_drive_filament(slot_num)):
            try:
                self.mms_delivery.select_slot(slot_num)
                toolhead_adapter.check_motion()
                self._update_motion(slot_num)
                self._refresh_limits(slot_num)
                if (not self._stroke_is_measured
                        and not self.measure_stroke(slot_num)):
                    raise MMSException(
                        f"slot[{slot_num}] buffer measurement failed")
                for _ in self._working_references(slot_num):
                    self._move_to_target(slot_num)
                    if not self.is_empty():
                        break
                self.log_info_s(
                    f"slot[{slot_num}] buffer[{self._index}] reserve ready: "
                    f"full={int(self.is_full())} volume={self._volume}")
            except Exception:
                if not self.mms_resume.is_tail_available(slot_num):
                    raise
        toolhead_adapter.check_motion()
        if (self._volume is None and self.mms_resume.is_tail_available(slot_num)
                and self.mms_resume.can_drive_filament(slot_num)):
            # The spool ended before a new buffer reference could be obtained.
            self.mms_delivery.unselect(slot_nums=[slot_num])
            toolhead_adapter.check_motion()
        self.mms_delivery.release_tail(slot_num)

    def _move_to_target(self, slot_num):
        full = self.is_full()
        reserve = self.max_volume if full else self._volume
        distance = (self.target_volume - reserve) / self.filament_cross_section
        if not full and distance <= 0.:
            return
        config = self.mms_delivery.pd_config
        move = (self.mms_delivery.move_forward if distance >= 0.
                else self.mms_delivery.move_backward)
        move(slot_num, abs(distance), config.sprint_speed, config.sprint_accel)
        toolhead_adapter.check_motion()
        self._last_positions = None
        self._refresh_limits(slot_num)
        if self.is_full():
            self._set_volume(self.max_volume)
        elif self.is_empty():
            self._set_volume(self.min_volume)
        else:
            self._set_volume(self.target_volume)

    # ---- Pins trigger/release handlers and check ----
    def _anchor(self, volume, sensor):
        # Startup switch events do not authorize a new feed path. Explicit
        # LOAD/calibration establishes the first reference after restart.
        if self._volume is None:
            return
        slot_num = self._feed_slot
        if slot_num is None and self._is_ready:
            slot_num = self._find_slot(no_log=True)
        if (slot_num in self._binding_slot_nums
                and self._last_positions is not None):
            now = self._positions(slot_num)
            # Sensor edge and queued button delivery may be separated in time.
            eventtime = sensor._state_eventtime
            drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
            edge_time = drive.get_mcu().estimated_print_time(eventtime)
            edge = self._positions(slot_num, edge_time)
            volume += ((now[2] - edge[2]) * self.filament_cross_section
                       - (now[1] - edge[1])
                       * extruder_adapter.get_extruder_filament_area())
            self._last_positions = now
        self._set_volume(volume)

    def _handle_full(self, mcu_pin):
        self._anchor(self.max_volume, self._sensor_full)
        self._log_feedback("full triggered")

    def _handle_runout(self, mcu_pin):
        self._anchor(self.min_volume, self._sensor_runout)
        self._log_feedback("low triggered")
        task = self._p_task
        if (task is not None and task.running
                and task._execution_greenlet is None):
            # The controller owns fault decisions. Wake an idle monitor;
            # an entered feed callback already checks each native window.
            task.reactor.update_timer(task.timer, task.reactor.NOW)

    def _has_low_release_reference(self):
        return (self._low_release_volume is not None
                and self.min_volume < self._low_release_volume
                < self.max_volume)

    def _handle_low_release(self, mcu_pin):
        if self._volume is None or self._last_positions is None:
            return
        if self._has_low_release_reference():
            self._anchor(self._low_release_volume, self._sensor_runout)
        self._log_feedback("low released")

    def _refresh_limits(self, slot_num):
        slot = self.mms.get_mms_slot(slot_num)
        for sensor in (slot.buffer_runout, slot.outlet):
            sensor.refresh_state()
            toolhead_adapter.check_motion()
        self._check_sensors()

    def _check_sensors(self):
        if not self.is_empty():
            self._low_warned = False
            self._idle_refill_blocked = False
            self._low_attempt_start = None
            self._low_diagnostic = None
        if self.is_full() and self.is_empty():
            raise MMSException("buffer full and empty signals are both active")
        # A held switch describes a region, not a new position measurement.
        # Once referenced, keep every executed displacement until the next
        # real edge instead of erasing motion while a switch remains pressed.
        if self._volume is None and not self._loading_filament:
            if self.is_full():
                self._set_volume(self.max_volume)
            elif self.is_empty():
                self._set_volume(self.min_volume)

    def set_sensor_full(self, mms_button):
        if self._sensor_full is mms_button:
            # Already set
            return
        self._sensor_full = mms_button
        self._sensor_full.register_trigger_callback(self._handle_full)

    def set_sensor_runout(self, mms_button):
        if self._sensor_runout is mms_button:
            # Already set
            return
        self._sensor_runout = mms_button
        self._sensor_runout.register_trigger_callback(self._handle_runout)
        self._sensor_runout.register_release_callback(self._handle_low_release)

    def set_index(self, index):
        self._index = index

    # ---- Status ----
    def is_activating(self):
        # A stopped callback may still own motion while unwinding; that is
        # busy for joining/restarting, but not enabled for print acceptance.
        return self._p_task is not None and self._p_task.running

    def is_full(self):
        full = self._sensor_full and self._sensor_full.is_triggered()
        return True if full else False

    def is_empty(self):
        runout = self._sensor_runout and self._sensor_runout.is_triggered()
        return True if runout else False

    def get_spring_stroke(self):
        return self.spring_stroke

    def get_volume(self):
        return self._volume

    def get_volume_percentage(self):
        if self._volume is None:
            return None
        return round(
            self._volume/(self.max_volume-self.min_volume), 4
        ) * 100

    def get_index(self):
        return self._index

    def get_status(self):
        return {
            "index" : self._index,

            "volume" : self._volume,
            "pct" : self.get_volume_percentage(),

            "is_activating" : self.is_activating(),
            # Retain the status key; boundary updates are never frozen now.
            "is_freezing" : False,
            "stroke_is_measured" : self._stroke_is_measured,
            "low_release_volume": self._low_release_volume,
            "low_warning": self._low_warned,
            "idle_refill_blocked": self._idle_refill_blocked,

            # Static config
            "spring_stroke" : self.spring_stroke,
            "filament_diameter" : self.filament_diameter,
            "target_percentage" : self.target_percentage,

            "filament_cross_section" : self.filament_cross_section,
            "max_volume" : self.max_volume,
            "min_volume" : self.min_volume,
            "target_volume" : self.target_volume,
            "min_deliver_volume" : self.min_deliver_volume,

            "measure_speed" : self.measure_speed,
            "measure_accel" : self.measure_accel,

            "monitor_period" : self.monitor_period,
        }

    def stroke_is_measured(self):
        return self._stroke_is_measured


class BufferCommand:
    def __init__(self):
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()

    def _initialize_gcode(self):
        commands = [
            ("MMS_BUFFER_ACTIVATE", self.cmd_MMS_BUFFER_ACTIVATE),
            ("MMS_BUFFER_DEACTIVATE", self.cmd_MMS_BUFFER_DEACTIVATE),
            ("MMS_BUFFER_MEASURE", self.cmd_MMS_BUFFER_MEASURE),

            ("MMS_BUFFER_FILL", self.cmd_MMS_BUFFER_FILL),
            ("MMS_BUFFER_CLEAR", self.cmd_MMS_BUFFER_CLEAR),
            ("MMS_BUFFER_HALFWAY", self.cmd_MMS_BUFFER_HALFWAY),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)

    def find_mms_buffer(self, extend_num):
        mms_extend = self.mms.get_mms_extend(extend_num)
        if mms_extend:
            return mms_extend.get_mms_buffer()

        mms_buffers = self.mms.get_mms_buffers()
        if extend_num == 0 and mms_buffers:
            return mms_buffers[0]

        return None

    # ---- GCode commands ----
    def cmd_MMS_BUFFER_ACTIVATE(self, gcmd):
        self.mms.check_maintenance(gcmd)
        extend_num = gcmd.get_int("EXTEND", minval=0, default=0)
        mms_buffer = self.find_mms_buffer(extend_num)
        if not mms_buffer:
            raise gcmd.error(f"MMS buffer EXTEND={extend_num} was not found")
        try:
            slot_num = mms_buffer._find_slot(no_log=True)
            if slot_num is None or not mms_buffer.has_slot(slot_num):
                raise gcmd.error("MMS buffer has no active source slot")
            self._check_source(slot_num)
            resume = self.mms.get_mms_resume()
            generation = resume.get_status()["generation"]

            def check():
                if resume.get_status()["generation"] != generation:
                    raise MMSException("MMS buffer activation was cancelled")
                self._check_source(slot_num)

            with toolhead_adapter.motion_scope(check=check):
                mms_buffer.prepare_feed(slot_num)
                if not mms_buffer.is_activating():
                    raise gcmd.error("MMS buffer monitor did not start")
        except Exception as e:
            raise gcmd.error(str(e))

    def cmd_MMS_BUFFER_DEACTIVATE(self, gcmd):
        self.mms.check_maintenance(gcmd)
        extend_num = gcmd.get_int("EXTEND", minval=0, default=0)
        mms_buffer = self.find_mms_buffer(extend_num)
        if not mms_buffer:
            raise gcmd.error(f"MMS buffer EXTEND={extend_num} was not found")
        try:
            mms_buffer.deactivate_monitor()
        except Exception as e:
            raise gcmd.error(str(e))

    def _check_source(self, slot_num):
        resume = self.mms.get_mms_resume()
        owners = [resume.get_exhausted_slot(),
                  self.mms.mms_charge.get_charged_slot()]
        owners.extend(b.get_feed_slot() for b in self.mms.get_mms_buffers())
        for owner in owners:
            if owner not in (None, slot_num):
                raise MMSException(f"slot[{owner}] owns the loaded path")
        resume.check_loaded_source(slot_num)
        if not resume.can_drive_filament(slot_num):
            raise MMSException(
                f"slot[{slot_num}] filament is beyond the MMS drive; "
                "buffer maintenance requires a controllable source")

    def _maintenance_buffer(self, gcmd, operation, **kwargs):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")
        extend_num = gcmd.get_int("EXTEND", minval=0, default=0)
        buffer = self.find_mms_buffer(extend_num)
        if not buffer or not buffer.has_slot(slot_num):
            raise gcmd.error(
                f"slot[{slot_num}] does not belong to "
                f"MMS buffer EXTEND={extend_num}"
            )
        if (operation == "measure_stroke" and not kwargs.get("force")
                and buffer.stroke_is_measured()):
            return slot_num, buffer

        try:
            self._check_source(slot_num)
            delivery = buffer.mms_delivery
            resume = self.mms.get_mms_resume()
            generation = resume.get_status()["generation"]

            def maintain_buffer():
                toolhead_adapter.wait_moves()
                delivery._check_task_cancelled()
                feeding = buffer.get_feed_slot() == slot_num
                buffer.deactivate_monitor()
                delivery._check_task_cancelled()
                # Both waits yield; recheck the path before any positioning.
                self._check_source(slot_num)
                success = False
                try:
                    if not getattr(buffer, operation)(slot_num, **kwargs):
                        return False
                    delivery._check_task_cancelled()
                    if feeding:
                        self._check_source(slot_num)
                        buffer.activate_monitor(slot_num)
                        buffer.check_feed(slot_num)
                    success = True
                    return True
                finally:
                    if not success:
                        buffer.invalidate_reference()

            # Keep one owner across sensor queries and local retry waits.
            # This handoff retains the reference established by the operation.
            delivery.deliver_task(
                maintain_buffer, wait=True, manage_feeds=False,
                stop_when=lambda: resume.get_status()["generation"]
                != generation)
        except Exception as e:
            raise gcmd.error(str(e))
        return slot_num, buffer

    def cmd_MMS_BUFFER_MEASURE(self, gcmd):
        force = bool(gcmd.get_int("FORCE", 0))
        slot, buffer = self._maintenance_buffer(
            gcmd, "measure_stroke", force=force)
        self.log_info(
            f"slot[{slot}] buffer spring stroke: "
            f"{buffer.get_spring_stroke()} mm"
        )

    def cmd_MMS_BUFFER_FILL(self, gcmd):
        self._maintenance_buffer(gcmd, "fill")

    def cmd_MMS_BUFFER_CLEAR(self, gcmd):
        self._maintenance_buffer(gcmd, "clear")

    def cmd_MMS_BUFFER_HALFWAY(self, gcmd):
        self._maintenance_buffer(gcmd, "halfway")
