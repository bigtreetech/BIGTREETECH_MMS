# Support for MMS Delivery
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass

from gcode import CommandError

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    pause_resume_adapter,
    print_stats_adapter,
    printer_adapter,
    query_endstops_adapter,
    toolhead_adapter
)
from ..core.config import PrinterConfig
from ..core.exceptions import (
    MMSException,
    DeliveryFailedError,
    DeliveryPreconditionError,
    DeliveryReadyError,
    DeliveryTerminateSignal,
)
from ..core.logger import log_time_cost
from ..core.slot_pin import PinType
from ..core.task import AsyncTask
from ..hardware.stepper import MoveType


@dataclass(frozen=True)
class DeliveryConfig:
    # Retry period of delivery, in seconds
    retry_period: float = 0.5

    # Wait Toolhead
    wait_toolhead_interval: float = 0.5 # seconds
    wait_toolhead_timeout: float = 60 # seconds

    # Wait MMS Steppers
    wait_mms_stepper_interval: float = 0.2 # seconds
    wait_mms_stepper_timeout: float = 5 # seconds

    gentle_homing_distance: float = 50    # mm

    # For bowden calibration
    walk_speed: float = 50 # mm/s
    walk_accel: float = 150 # mm/s^2


@dataclass(frozen=True)
class PrinterDeliveryConfig(PrinterConfig):
    # Speed/Accel of Stepper:Selector
    speed_selector: float = 150 # mm/s
    accel_selector: float = 150 # mm/s

    # Speed/Accel of Stepper:Drive
    speed_drive: float = 120 # mm/s
    accel_drive: float = 50 # mm/s

    # Sprint speed/Accel of Stepper:Drive
    sprint_speed: float = 40 # mm/s
    sprint_accel: float = 120 # mm/s^2

    # The distance stepper move before endstop is triggered, in mm
    bowden_distance: float = 1000

    # The distance stepper retrace after unload to gate, in mm
    safety_retract_distance: float = 50

    # Full-slot rounds for MMS_SLOTS_LOOP and MMS_SLOTS_CHECK_LOOP
    slots_loop_times: int = 200

    # RFID checks in the first N check rounds; -1=all, 0=none
    slots_rfid_check_loops: int = -1

    # Multi-MCU homing timeout for MMS endstops only (seconds).
    custom_trsync_timeout: float = 0.05

    def get_type_handlers(self):
        handlers = super().get_type_handlers()

        def get_int(config, name):
            if name == "slots_rfid_check_loops":
                return config.getint(name, -1, minval=-1)
            return config.getint(name)

        def get_float(config, name):
            if name in ("speed_selector", "speed_drive", "sprint_speed"):
                return config.getfloat(name, above=0.)
            if name == "custom_trsync_timeout":
                value = config.getfloat(name, above=0.)
                if not 0. < value < float("inf"):
                    raise config.error(f"{name} must be positive and finite")
                return value
            return config.getfloat(name)

        handlers[int] = get_int
        handlers[float] = get_float
        return handlers


class MMSDelivery:
    def __init__(self, config):
        self.reactor = printer_adapter.get_reactor()

        # Delivery config
        pd_config = PrinterDeliveryConfig(config)
        self.pd_config = pd_config.gen_packaged_config()
        self.d_config = DeliveryConfig()

        # Pins
        self.pin_type = PinType()
        self._stopping = False
        self._print_tail_release = None

        # Register before klippy:connect so a UI macro can rename this command
        # regardless of the order in which the config sections were loaded.
        gcode_adapter.register(
            "MMS_LOAD_SLOT", self.cmd_MMS_LOAD_SLOT,
            "Safely eject the old path and load SLOT_NUM=n to entry/outlet.")
        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)
        printer_adapter.register_klippy_ready(self._handle_klippy_ready)

    # ---- Initialization ----
    def _handle_klippy_connect(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

        endstops = {pin.get_endstop() for slot in self.mms.get_mms_slots()
                    for pin in slot.slot_pin_map.values() if pin.is_set()}
        for endstop in endstops:
            query_endstops_adapter.set_homing_timeout(
                endstop, self.pd_config.custom_trsync_timeout)

    def _handle_klippy_ready(self):
        # READY cannot yield. Reserve the command stream before the first E
        # or LOAD, then do the hardware work after all READY handlers return.
        gcode = printer_adapter.get_printer().lookup_object("gcode")
        mutex = gcode.get_mutex()
        if mutex.test():
            self.log_warning("MMS startup selector release skipped: G-code is "
                             "already active; check MMS_STATUS and use "
                             "MMS_UNSELECT WAIT=1 if still clamped")
            return

        def release(eventtime):
            try:
                if not self.mms.printer_is_shutdown():
                    self.deliver_task(self._release_detected_selectors,
                                      {"startup": True}, wait=True,
                                      manage_feeds=False)
            except Exception as e:
                self.log_error(f"MMS startup selector release failed: {e}; "
                               "check the selector before moving filament, "
                               "then retry MMS_UNSELECT WAIT=1")
            finally:
                mutex.unlock()

        self.reactor.register_callback(release)
        mutex.lock()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_pause = self.mms.get_mms_pause()
        self.mms_fil_detection = self.mms.get_mms_filament_detection()
        # Configuration parameters
        self.retry_times = self.mms.get_retry_times()
        # Singleton async task
        self.async_task_sp = AsyncTask()

    def _initialize_gcode(self):
        commands = [
            # Core operations
            (
                "MMS_LOAD",
                self.cmd_MMS_LOAD,
                "Homing filament until entry/outlet is triggered."
            ),
            (
                "MMS_UNLOAD",
                self.cmd_MMS_UNLOAD,
                "Homing filament until gate is released."
            ),
            (
                "MMS_POP",
                self.cmd_MMS_POP,
                "Homing filament until inlet is released."
            ),
            (
                "MMS_PREPARE",
                self.cmd_MMS_PREPARE,
                "Make sure filament is ready to load."
            ),
            (
                "MMS_MOVE",
                self.cmd_MMS_MOVE,
                "Move filament in target slot: MMS_MOVE SLOT=n DISTANCE=a."
            ),
            ("MMS_DRIP_MOVE", self.cmd_MMS_DRIP_MOVE),

            # Selection controls
            (
                "MMS_SELECT",
                self.cmd_MMS_SELECT,
                "Select the target slot: MMS_SELECT SLOT=n"
            ),
            (
                "MMS_UNSELECT",
                self.cmd_MMS_UNSELECT,
                "Unselect current slot."
            ),

            # Stop commands
            ("MMS_STOP", self.cmd_MMS_STOP),
            # Diagnostic commands
            (
                "MMS_SLOTS_CHECK",
                self.cmd_MMS_SLOTS_CHECK,
                "Check slots' pins, for DEV only."
            ),
            (
                "MMS_SLOTS_CHECK_LOOP",
                self.cmd_MMS_SLOTS_CHECK_LOOP,
                "Loop checking slots' pins, for DEV only."
            ),
            (
                "MMS_SLOTS_WALK",
                self.cmd_MMS_SLOTS_WALK,
                "Deliver all ready slots like walking through."
            ),
            (
                "MMS_SLOTS_LOOP",
                self.cmd_MMS_SLOTS_LOOP,
                "SLOTs walk looping."
            ),
            # Calibration
            (
                "MMS_BOWDEN_CALIBRATION",
                self.cmd_MMS_BOWDEN_CALIBRATION,
                "Calibrate bowden distance of slots."
            ),
            # Command aliases
            ("MMS999", self.cmd_MMS_STOP),
            (
                "MMS9",
                self.cmd_MMS_SLOTS_WALK,
                "Deliver all ready slots like walking through."
            ),
            (
                "MMS8",
                self.cmd_MMS_SLOTS_LOOP,
                "SLOTs walk looping."
            ),
            (
                "MMS7",
                self.cmd_MMS_BOWDEN_CALIBRATION,
                "Calibrate bowden distance of slots."
            ),
            (
                "MMS09",
                self.cmd_MMS_SLOTS_CHECK,
                "Check slots' pins, for DEV only."
            ),
            (
                "MMS08",
                self.cmd_MMS_SLOTS_CHECK_LOOP,
                "Loop checking slots' pins, for DEV only."
            ),

            # Extruder sync
            ("MMS_SLOT_EXTRUDER_SYNC", self.cmd_MMS_SLOT_EXTRUDER_SYNC),
            ("MMS_SLOT_EXTRUDER_UNSYNC", self.cmd_MMS_SLOT_EXTRUDER_UNSYNC),

            # For KlipperScreen
            ("MMS_SELECT_U", self.cmd_MMS_SELECT_U),
            ("MMS_LOAD_U", self.cmd_MMS_LOAD_U),
            ("MMS_POP_U", self.cmd_MMS_POP_U),
            ("MMS_PREPARE_U", self.cmd_MMS_PREPARE_U),
            # Test
            ("MMS_D_TEST", self.cmd_MMS_D_TEST),
            (
                "MMS_TEST_SELECTOR_MEASURE",
                self.cmd_MMS_TEST_SELECTOR_MEASURE
            ),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        # All loggers in MMS Delivery will print to console
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)

        self.log_info_s = mms_logger.create_log_info(console_output=False)
        self.log_warning_s = mms_logger.create_log_warning(console_output=False)

    # ---- Control ----
    def is_stopping(self):
        return self._stopping

    def _get_autoload(self):
        # Autoload is optional; inspecting task ownership must not load it.
        return printer_adapter.printer.lookup_object(
            printer_adapter.mms_module.mms_autoload, None)

    def _check_task_cancelled(self):
        toolhead_adapter.check_motion(require_owner=False)
        autoload = self._get_autoload()
        if self.async_task_sp.current_is_cancelled() \
                or (autoload is not None and autoload.current_is_cancelled()) \
                or not self.mms.get_mms_resume().is_current_attempt():
            raise DeliveryTerminateSignal()

    def pause(self, period_seconds):
        self._check_task_cancelled()
        self.reactor.pause(self.reactor.monotonic() + period_seconds)
        self._check_task_cancelled()

    def _can_deliver(self):
        if self.mms.printer_is_shutdown():
            self.log_warning("printer is shutdown")
            return False
        return True

    def _wait_mms_stepper(
        self, slot_num, mms_stepper, interval=None, timeout=None
    ):
        interval = abs(interval or self.d_config.wait_mms_stepper_interval)
        timeout = abs(timeout or self.d_config.wait_mms_stepper_timeout)

        log_prefix = f"slot[{slot_num}] {mms_stepper.get_name()} wait idle"
        # self.log_info_s(log_prefix)

        begin_at = self.reactor.monotonic()
        # Wait until timeout or idle
        while mms_stepper.is_running():
            if mms_stepper.move_type == MoveType.EXTRUDER_SYNC:
                self.log_warning(
                    f"slot[{slot_num}] drive remains synchronized; run "
                    f"MMS_SLOT_EXTRUDER_UNSYNC SLOT={slot_num} before retrying")
                return False
            # First wait
            self.pause(interval)

            # Calculate elapsed time
            elapsed_time = self.reactor.monotonic() - begin_at
            if elapsed_time > timeout:
                # Timeout, return
                self.log_warning(
                    f"{log_prefix} timeout after {elapsed_time:.2f}s")
                return False

        # Idle
        elapsed_time = self.reactor.monotonic() - begin_at
        if elapsed_time > 0.1:
            self.log_info_s(f"{log_prefix} reached in {elapsed_time:.2f}s")
        return True

    def wait_mms_selector(self, slot_num, interval=None, timeout=None):
        mms_selector = self.mms.get_mms_slot(slot_num).get_mms_selector()
        return self._wait_mms_stepper(
            slot_num, mms_selector, interval, timeout)

    def wait_mms_drive(self, slot_num, interval=None, timeout=None):
        mms_drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        return self._wait_mms_stepper(
            slot_num, mms_drive, interval, timeout)

    def wait_mms_selector_and_drive(
        self, slot_num, interval=None, timeout=None
    ):
        selector_idle = self.wait_mms_selector(slot_num, interval, timeout)
        drive_idle = self.wait_mms_drive(slot_num, interval, timeout)
        mms_selector = self.mms.get_mms_slot(slot_num).get_mms_selector()
        mms_drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        return (selector_idle and drive_idle
                and not (mms_selector.is_running() or mms_drive.is_running()))

    def pause_feeds(self, slot_nums):
        """Stop callbacks, then join each shared buffer and motor once."""
        self.wait_for_print_tail_release()
        buffers, motors = {}, {}
        for num in slot_nums:
            slot = self.mms.get_mms_slot(num)
            buffer = slot.get_mms_buffer()
            buffers.setdefault(id(buffer), buffer)
            for motor in (slot.get_mms_selector(), slot.get_mms_drive()):
                motors.setdefault(id(motor), (motor, num))
        for buffer in buffers.values():
            buffer.deactivate_monitor(wait=False)
        for buffer in buffers.values():
            buffer.deactivate_monitor()
        for motor, num in motors.values():
            if not self._wait_mms_stepper(num, motor):
                raise DeliveryFailedError(
                    f"slot[{num}] feed handoff did not finish",
                    self.mms.get_mms_slot(num))

    @contextmanager
    def maintenance_feeds(self, side_slot=None):
        """Hand off feeding; a side-slot operation can preserve its source."""
        buffers = self.mms.get_mms_buffers()
        keeper = None
        toolhead_adapter.wait_moves()
        if side_slot is not None:
            resume = self.mms.get_mms_resume()
            for buffer in buffers:
                source = buffer.get_feed_slot()
                if (source is not None and source != side_slot
                        and self.is_exclusively_selected(source)):
                    keeper, keep_slot = buffer, source
                    generation = resume.get_status()["generation"]
                    accepted = self.mms.mms_charge.get_charged_slot()
                    break
        self.pause_feeds(self.mms.get_slot_nums())
        for buffer in buffers:
            if buffer is not keeper:
                buffer.invalidate_reference()

        def restore():
            if keeper is None:
                return
            slot = self.mms.get_mms_slot(keep_slot)
            def current():
                self._check_task_cancelled()
                return (generation == resume.get_status()["generation"]
                        and accepted == self.mms.mms_charge.get_charged_slot()
                        and resume.get_exhausted_slot() is None
                        and slot.is_loading()
                        and (not slot.entry_is_set()
                             or slot.entry.is_triggered()))
            if (current() and not self.mms.mms_selector_is_running()
                    and not self.mms.mms_drive_is_running()):
                self.mms.mms_eject.release_other_selectors(keep_slot)
                if current():
                    self.select_slot(keep_slot)
                if current():
                    keeper.activate_monitor(keep_slot, allow_unreferenced=True,
                                            after_side_move=True)
                    keeper.check_feed(keep_slot)

        try:
            yield
        except DeliveryTerminateSignal:
            raise
        except Exception:
            try:
                restore()
            except Exception as e:
                self.log_error(f"side-slot feed restoration failed: {e}")
            raise
        else:
            restore()
        finally:
            for buffer in buffers:
                if not buffer.is_activating():
                    buffer.invalidate_reference()

    def wait_toolhead(self, interval=None, timeout=None, should_stop=None):
        interval = interval or self.d_config.wait_toolhead_interval
        timeout = timeout or self.d_config.wait_toolhead_timeout

        # Block waiting for toolhead to complete pause movement operations
        begin_at = self.reactor.monotonic()
        while toolhead_adapter.is_busy():
            if should_stop is not None and should_stop():
                return False
            self.pause(interval)
            # Handle timeout scenario if toolhead
            # doesn't complete within allocated time
            if self.reactor.monotonic() - begin_at > timeout:
                return False
        return should_stop is None or not should_stop()

    # ---- Core Operations ----
    # -- Select --
    def _led_effect_activate(self, slot_num_lst, led_reverse=False):
        for slot_num in slot_num_lst:
            if slot_num is None:
                continue
            mms_slot = self.mms.get_mms_slot(slot_num)
            mms_slot.slot_led.activate_rainbow(led_reverse)

    def _led_effect_deactivate(self, slot_num_lst):
        for slot_num in slot_num_lst:
            if slot_num is None:
                continue
            mms_slot = self.mms.get_mms_slot(slot_num)
            mms_slot.slot_led.deactivate_rainbow()

    def _selector_refine_calibration(self, slot_num, factor=None):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_selector = mms_slot.get_mms_selector()

        if mms_selector.can_calibrate():
            # Move static distance
            factor = 1 if factor is None else factor
            dist = mms_slot.get_selector_calibrate_distance() * factor
            self.log_info_s(
                f"selector refine calibration distance: {dist} mm")
            self._check_task_cancelled()
            mms_selector.manual_move(
                distance = dist,
                speed = self.pd_config.speed_selector,
                accel = self.pd_config.accel_selector,
            )
            self._check_task_cancelled()

    def _check_home_endstop(self, slot_num, pin_type, trigger):
        self._check_task_cancelled()
        slot = self.mms.get_mms_slot(slot_num)
        slot.get_mms_slot_pin(pin_type).refresh_state()
        self._check_task_cancelled()
        return slot.check_pin(pin_type, trigger)

    def _selector_deliver_to(self, slot_num, reverse=False):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_selector = mms_slot.get_mms_selector()

        if not self._can_deliver():
            focus_slot = mms_selector.get_focus_slot()
            slot_num_lst = [focus_slot, slot_num] \
                if focus_slot is not None else [slot_num]
            self._led_effect_deactivate(slot_num_lst)
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        pin_type = self.pin_type.selector
        wait = mms_slot.get_wait_func(pin_type)

        with wait():
            self._check_task_cancelled()
            result = mms_selector.manual_home(
                distance = self.pd_config.bowden_distance,
                speed = self.pd_config.speed_selector,
                accel = self.pd_config.accel_selector,
                forward = not reverse,
                trigger = True,
                endstop_pair_lst = mms_slot.format_endstop_pair(pin_type),
                check_endstop=lambda: self._check_home_endstop(
                    slot_num, pin_type, True),
            )
            self._check_task_cancelled()
            return result

    def select_slot(self, slot_num, reverse=False):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_selector = mms_slot.get_mms_selector()

        # Already selecting
        if mms_slot.selector_is_triggered():
            mms_selector.enable()
            mms_selector.update_focus_slot(slot_num)
            # self.log_info_s(f"slot[{slot_num}] is already selected, skip...")
            return

        msg = (f"slot[{slot_num}] selector move until"
               f" '{self.pin_type.selector}' trigger")
        focus_slot = mms_selector.get_focus_slot()
        slot_num_lst = [focus_slot, slot_num] \
            if focus_slot is not None else [slot_num]

        # Activate LED effect
        led_reverse = (focus_slot is not None) and (focus_slot > slot_num)
        self._led_effect_activate(slot_num_lst, led_reverse)

        distance_moved = 0
        is_completed = False

        for i in range(self.retry_times):
            self.log_info_s(msg)
            result = self._selector_deliver_to(slot_num, reverse)

            distance_moved += mms_selector.get_distance_moved()
            msg_dist = f"{msg} total distances moved:{distance_moved:.3f}"

            if mms_selector.move_is_terminated():
                self._led_effect_deactivate(slot_num_lst)
                self.log_info_s(f"{msg} is terminated")
                self.log_info_s(msg_dist)
                raise DeliveryTerminateSignal()

            is_completed = mms_selector.move_is_completed(result)
            if is_completed:
                # Mark slot is focusing only if selector pin is triggered
                mms_selector.update_focus_slot(slot_num)
                self.log_info_s(msg_dist)
                break

            if i + 1 < self.retry_times:
                self.log_info(
                    f"{msg} retry {i+1}/{self.retry_times-1} ...")
                self.pause(self.d_config.retry_period)

        # Try overtravel detect and recover after selector is triggered
        if is_completed:
            self._selector_refine_calibration(slot_num)
        # Deactivate LED effect before exception raise
        self._led_effect_deactivate(slot_num_lst)

        if not is_completed:
            raise DeliveryFailedError(
                f"{msg} failed after full movement", mms_slot)

    # -- Deliver params --
    def _parse_distance(self, mms_slot, distance, pin_type, forward):
        if distance is not None:
            return distance, False

        distance = mms_slot.get_deliver_distance(pin_type, forward)
        if distance:
            return distance, True

        return self.pd_config.bowden_distance, False

    def _limit_drive_speed(self, speed):
        if speed is None:
            # Direct return
            return self.pd_config.speed_drive

        # Limit value
        # limited = min(max(speed, 0.0), self.pd_config.speed_drive)
        limited = max(speed, 0.0)
        if limited != speed:
            self.log_warning(
                f"speed {speed:.2f}mm/s limit to {limited:.2f}mm/s")
        return limited

    def _limit_drive_accel(self, accel):
        if accel is None:
            # Direct return
            return self.pd_config.accel_drive

        # Limit value
        # limited = min(max(accel, 0.0), self.pd_config.accel_drive)
        limited = max(accel, 0.0)
        if limited != accel:
            self.log_warning(
                f"accel {accel:.2f}mm/s^2 limit to {limited:.2f}mm/s^2")
        return limited

    # -- Deliver --
    def _deliver_distance(self, slot_num, distance, speed=None, accel=None):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if distance > 0.:
            mms_slot.check_load_allowed()
        if not self._can_deliver():
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        msg = f"slot[{slot_num}] deliver {distance:.2f} mm"

        # Wait until mms_selector/mms_drive idle
        is_idle = self.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            raise DeliveryFailedError(
                f"{msg} wait selector or drive stepper idle timeout", mms_slot)

        speed = self._limit_drive_speed(speed)
        accel = self._limit_drive_accel(accel)

        self.log_info_s(f"{msg} begin")
        self.log_info_s(
            "\n"
            f"slot[{slot_num}] deliver:\n"
            f"distance: {distance:.2f} mm\n"
            f"speed: {speed:.2f} mm/s\n"
            f"accel: {accel:.2f} mm/s^2"
        )

        # Apply select
        self.select_slot(slot_num)
        # Apply move
        mms_drive = mms_slot.get_mms_drive()
        context = (
            self.mms_fil_detection.monitor(slot_num)
            if distance>0 else nullcontext()
        )
        with context:
            self._check_task_cancelled()
            mms_drive.manual_move(distance, speed, accel, slot_num=slot_num)
            self._check_task_cancelled()

        self.log_info_s(f"{msg} finish")

    def _drip_deliver_distance(
        self, slot_num, distance,
        speed=None, accel=None
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if distance > 0.:
            mms_slot.check_load_allowed()
        if not self._can_deliver():
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        msg = f"slot[{slot_num}] drip deliver {distance:.2f} mm"

        # Wait until mms_selector/mms_drive idle
        is_idle = self.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            raise DeliveryFailedError(
                f"{msg} wait selector or drive stepper idle timeout", mms_slot)

        speed = self._limit_drive_speed(speed)
        accel = self._limit_drive_accel(accel)

        self.log_info_s(f"{msg} begin")
        self.log_info_s(
            "\n"
            f"slot[{slot_num}] drip deliver:\n"
            f"distance: {distance:.2f} mm\n"
            f"speed: {speed:.2f} mm/s\n"
            f"accel: {accel:.2f} mm/s^2"
        )

        # Apply select
        self.select_slot(slot_num)
        # Apply drive move
        mms_drive = mms_slot.get_mms_drive()
        # If deliver forward, enable monitoring
        # Else disable with Null context manager
        context = (
            self.mms_fil_detection.monitor(slot_num)
            if distance>0 else nullcontext()
        )
        with context:
            self._check_task_cancelled()
            mms_drive.drip_move(distance, speed, accel, slot_num=slot_num)
            self._check_task_cancelled()

        self.log_info_s(f"{msg} finish")

    # -- Deliver to --
    def _drive_deliver_to(
        self, slot_num, pin_type, forward, trigger, distance, speed, accel
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if forward:
            mms_slot.check_load_allowed()
        if not self._can_deliver():
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        mms_drive = mms_slot.get_mms_drive()
        wait = mms_slot.get_wait_func(pin_type)

        with wait():
            # If deliver forward, enable monitoring
            # Else disable with Null context manager
            context = (
                self.mms_fil_detection.monitor(slot_num)
                if forward else nullcontext()
            )
            with context:
                self._check_task_cancelled()
                # Revoke acceptance only when the path is about to move.
                if forward and trigger and pin_type == self.pin_type.entry:
                    printer_adapter.get_mms_charge().invalidate_loaded_slot()
                elif not forward and not trigger and pin_type in (
                        self.pin_type.inlet, self.pin_type.gate):
                    printer_adapter.get_mms_charge().invalidate_loaded_slot(
                        slot_num)
                result = mms_drive.manual_home(
                    distance=distance, speed=speed, accel=accel,
                    forward=forward, trigger=trigger,
                    endstop_pair_lst=mms_slot.format_endstop_pair(pin_type),
                    slot_num=slot_num,
                    check_endstop=lambda: self._check_home_endstop(
                        slot_num, pin_type, trigger),
                )
                self._check_task_cancelled()
                # A reverse buffer move may belong to a monitored charge or
                # purge. Do not enter another retry after its inlet failed.
                if self.mms_fil_detection.is_activating():
                    self.mms_fil_detection.check(slot_num)
                return result

    def _deliver_to(
        self, slot_num, pin_type, forward, trigger,
        distance=None, speed=None, accel=None
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)

        # Parse deliver parasms
        distance, from_meta = self._parse_distance(
            mms_slot, distance, pin_type, forward)
        speed = self._limit_drive_speed(speed)
        accel = self._limit_drive_accel(accel)

        self.log_info_s(
            f"\nslot[{slot_num}] deliver\n"
            f"distance: {distance} mm\n"
            f"speed: {speed} mm/s\n"
            f"accel: {accel} mm/s^2"
        )

        if from_meta and \
            pin_type in (self.pin_type.outlet, self.pin_type.entry) and \
            forward and trigger:
            return self.deliver_confidently(
                slot_num, pin_type, forward, trigger,
                distance, speed, accel
            )
        else:
            return self.deliver_with_retry(
                slot_num, pin_type, forward, trigger,
                distance, speed, accel
            )
        # return self.deliver_with_retry(
        #     slot_num, pin_type, forward, trigger,
        #     distance, speed, accel
        # )

    def deliver_confidently(
        self, slot_num, pin_type, forward, trigger,
        distance, speed, accel
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        msg = self._format_deliver_msg(
            slot_num, pin_type, forward, trigger)

        # Check destination pin state
        if self._check_home_endstop(slot_num, pin_type, trigger):
            self.log_info_s(f"{msg} is already done, skip...")
            return True

        # Wait until mms_selector/mms_drive idle
        is_idle = self.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            raise DeliveryFailedError(
                f"{msg} wait selector or drive stepper idle timeout", mms_slot)

        # Filament run out?
        self.log_info_s(
            f"slof[{slot_num}] deliver confidently: {distance:.2f}mm")
        # self._deliver_distance(slot_num, distance, speed, accel)
        # sprint_dist = self.pd_config.safety_retract_distance
        # distance_moved = distance

        # Apply select
        self.select_slot(slot_num)

        # Apply manual homing
        spd = self._limit_drive_speed(speed)
        acc = self._limit_drive_accel(accel)
        self.log_info_s(
            "\n"
            f"slot[{slot_num}] deliver to:\n"
            f"distance: {distance:.2f} mm\n"
            f"speed: {spd:.2f} mm/s\n"
            f"accel: {acc:.2f} mm/s^2"
        )
        self._drive_deliver_to(
            slot_num, pin_type, forward, trigger,
            distance, spd, acc
        )
        distance_moved = mms_drive.get_distance_moved()

        # Check destination pin state
        if mms_slot.check_pin(pin_type, trigger):
            self.log_info_s(
                f"{msg} complete, moved: {distance_moved:.2f} mm")
            return True

        # Gentle homing adds retries after the initial measured-distance pass.
        self.log_info_s(f"{msg} gentle homing")
        for i in range(self.retry_times):
            self.log_info(f"{msg} gentle retry {i+1}/{self.retry_times} ...")
            if i:
                self.pause(self.d_config.retry_period)
            result = self._drive_deliver_to(
                slot_num, pin_type, forward, trigger,
                distance,
                self.pd_config.sprint_speed,
                self.pd_config.sprint_accel
            )
            distance_moved += mms_drive.get_distance_moved()

            if mms_drive.move_is_completed(result):
                if i>0:
                    self.log_info(
                        f"{msg} complete, moved: {distance_moved:.2f} mm")
                return True

        # Finally not return, raise exception
        raise DeliveryFailedError(
            f"{msg} gentle failed after full movement", mms_slot)

    def deliver_with_retry(
        self, slot_num, pin_type, forward, trigger,
        distance, speed, accel
    ):
        msg = self._format_deliver_msg(slot_num, pin_type, forward, trigger)
        mms_slot = self.mms.get_mms_slot(slot_num)
        distance_moved_sum = 0

        for i in range(self.retry_times):
            success, distance_moved = self.deliver_once(
                slot_num, pin_type, forward, trigger,
                distance, speed, accel
            )
            distance_moved_sum += distance_moved

            if success:
                # Only update not 0
                if distance_moved_sum:
                    # Save distance
                    is_set = mms_slot.set_deliver_distance(
                        pin_type, forward, distance_moved_sum)
                    if is_set:
                        self.log_info_s(
                            f"{msg} distance saved: "
                            f"{distance_moved_sum:.2f} mm"
                        )
                # Log if retried
                if i>0:
                    self.log_info(
                        f"{msg} complete, moved: {distance_moved_sum:.2f} mm")
                return bool(distance_moved_sum)

            if i + 1 < self.retry_times:
                self.log_info(
                    f"{msg} retry {i+1}/{self.retry_times-1} ...")
                self.pause(self.d_config.retry_period)

        # Failed after all retry
        # Reset distance_moved to 0
        # is_set = mms_slot.set_deliver_distance(pin_type, forward, distance=0)
        # if is_set:
        #     self.log_info_s(f"{msg} reset deliver distance to 0 mm")
        # Finally not return, raise exception
        raise DeliveryFailedError(
            f"{msg} failed after full movement", mms_slot)

    def deliver_once(
        self, slot_num, pin_type, forward, trigger,
        distance, speed, accel
    ):
        # Prepare
        msg = self._format_deliver_msg(slot_num, pin_type, forward, trigger)
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()

        # Check destination pin state
        if self._check_home_endstop(slot_num, pin_type, trigger):
            self.log_info_s(f"{msg} is already done, skip...")
            self.mms.log_status()
            return True, 0

        # Wait until mms_selector/mms_drive idle
        if not self.wait_mms_selector_and_drive(slot_num):
            raise DeliveryFailedError(
                f"{msg} wait selector/drive stepper idle timeout", mms_slot)

        # Apply select
        self.select_slot(slot_num)
        # Apply drive
        self.log_info_s(msg)
        result = self._drive_deliver_to(
            slot_num, pin_type, forward, trigger, distance, speed, accel)

        # Get distance moved
        distance_moved = mms_drive.get_distance_moved()

        # Check terminated
        if mms_drive.move_is_terminated():
            self.log_info_s(
                f"{msg} is terminated, moved: {distance_moved:.2f} mm")
            raise DeliveryTerminateSignal()

        # Check complated
        if mms_drive.move_is_completed(result):
            self.log_info_s(
                f"{msg} is completed, moved: {distance_moved:.2f} mm")
            return True, distance_moved

        return False, distance_moved

    def _format_deliver_msg(self, slot_num, pin_type, forward, trigger):
        # Format log message
        f_dec = 'forward' if forward else 'backward'
        t_dec = 'trigger' if trigger else 'release'
        return f"slot[{slot_num}] deliver {f_dec} until '{pin_type}' {t_dec}"

    # ---- Atomic functions ----
    # Always use try-except with these functions
    def move_forward(self, slot_num, distance, speed=None, accel=None):
        self._deliver_distance(slot_num, abs(distance), speed, accel)

    def move_backward(self, slot_num, distance, speed=None, accel=None):
        self._deliver_distance(slot_num, -abs(distance), speed, accel)

    def drip_move_forward(self, slot_num, distance, speed=None, accel=None):
        self._drip_deliver_distance(slot_num, abs(distance), speed, accel)

    def drip_move_backward(self, slot_num, distance, speed=None, accel=None):
        self._drip_deliver_distance(slot_num, -abs(distance), speed, accel)

    def _load_to_release(
        self, slot_num, pin_type,
        distance=None, speed=None, accel=None
    ):
        self.log_info_s(f"slot[{slot_num}] load to release: '{pin_type}'")
        return self._deliver_to(
            slot_num, pin_type,
            forward=True, trigger=False,
            distance=distance, speed=speed, accel=accel
        )

    def _load_to_trigger(
        self, slot_num, pin_type,
        distance=None, speed=None, accel=None
    ):
        self.log_info_s(f"slot[{slot_num}] load to trigger: '{pin_type}'")
        return self._deliver_to(
            slot_num, pin_type,
            forward=True, trigger=True,
            distance=distance, speed=speed, accel=accel
        )

    def _unload_to_release(
        self, slot_num, pin_type,
        distance=None, speed=None, accel=None
    ):
        self.log_info_s(f"slot[{slot_num}] unload to release: '{pin_type}'")
        return self._deliver_to(
            slot_num, pin_type,
            forward=False, trigger=False,
            distance=distance, speed=speed, accel=accel
        )

    def _unload_to_trigger(
        self, slot_num, pin_type,
        distance=None, speed=None, accel=None
    ):
        self.log_info_s(f"slot[{slot_num}] unload to trigger: '{pin_type}'")
        return self._deliver_to(
            slot_num, pin_type,
            forward=False, trigger=True,
            distance=distance, speed=speed, accel=accel
        )

    def _check_slot_is_ready(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if mms_slot.is_ready():
            return
        msg = f"slot[{slot_num}] is not ready, please check Inlet"
        self.log_warning(msg)
        raise DeliveryReadyError(msg, mms_slot)

    def _safety_retract(self, slot_num):
        self.move_backward(
            slot_num,
            self.pd_config.safety_retract_distance,
            self.pd_config.sprint_speed,
            self.pd_config.sprint_accel
        )

    def load_to_gate(self, slot_num):
        self._check_slot_is_ready(slot_num)
        self._load_to_trigger(slot_num, self.pin_type.gate)

    def load_to_outlet(
        self, slot_num,
        distance=None, speed=None, accel=None
    ):
        self._check_slot_is_ready(slot_num)
        self._load_to_trigger(
            slot_num, self.pin_type.outlet,
            distance, speed, accel
        )

    def load_to_entry(
        self, slot_num,
        distance=None, speed=None, accel=None
    ):
        self._check_slot_is_ready(slot_num)
        self._load_to_trigger(
            slot_num, self.pin_type.entry,
            distance, speed, accel
        )

    def load_until_buffer_runout_release(
        self, slot_num, distance=None, speed=None, accel=None
    ):
        self._check_slot_is_ready(slot_num)
        self._load_to_release(
            slot_num, self.pin_type.buffer_runout,
            distance, speed, accel
        )

    def unload_to_outlet(self, slot_num):
        self._check_slot_is_ready(slot_num)
        self._unload_to_release(slot_num, self.pin_type.outlet)

    def unload_until_buffer_runout_trigger(self, slot_num, distance=None):
        self._check_slot_is_ready(slot_num)
        self._unload_to_trigger(
            slot_num,
            self.pin_type.buffer_runout,
            distance,
            self.pd_config.sprint_speed,
            self.pd_config.sprint_accel
        )

    def unload_to_gate(self, slot_num, need_check=True):
        if need_check:
            self._check_slot_is_ready(slot_num)
        res = self._unload_to_release(slot_num, self.pin_type.gate)
        # Only unload safety distance
        # after unload homing move is not skipped
        if res:
            self._safety_retract(slot_num)

    def unload_to_inlet(self, slot_num):
        self._check_slot_is_ready(slot_num)
        self._unload_to_release(slot_num, self.pin_type.inlet)

    def unload_loading_slots(self, skip_slot=None):
        loading_slots = self.mms.get_loading_slots()
        if not loading_slots:
            self.log_info_s("no loading slots, unload skip...")
            return

        for slot_num in loading_slots:
            if skip_slot is not None and slot_num == skip_slot:
                self.log_info_s(f"slot[{slot_num}] is loading, unload skip...")
                continue
            self.unload_to_gate(slot_num)

    def autoload_to_gate(self, slot_num, should_stop=None):
        def check_cancelled():
            if should_stop is not None and should_stop():
                raise DeliveryTerminateSignal()

        check_cancelled()
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        p_type = self.pin_type.gate

        # Wait until mms_selector/mms_drive idle
        if not self.wait_mms_selector_and_drive(slot_num):
            msg = "wait mms selector/drive stepper idle timeout"
            gcode_adapter.respond_error(msg)
            self.log_warning(msg)
            return

        # Reverse select once
        check_cancelled()
        self.select_slot(slot_num, reverse=True)
        check_cancelled()

        distance = mms_slot.get_deliver_distance(p_type, True) \
            or self.pd_config.bowden_distance
        speed = self.pd_config.sprint_speed
        accel = self.pd_config.sprint_accel

        distance_moved_sum = 0
        success = False
        # Force autoload with retry
        for i in range(self.retry_times):
            check_cancelled()
            self.log_info_s(
                f"\nslot[{slot_num}] deliver\n"
                f"distance: {distance} mm\n"
                f"speed: {speed} mm/s\n"
                f"accel: {accel} mm/s^2"
            )
            # Apply drive
            result = self._drive_deliver_to(
                slot_num, p_type,
                forward=True, trigger=True,
                distance=distance, speed=speed, accel=accel
            )
            check_cancelled()
            distance_moved_sum += mms_drive.get_distance_moved()

            # Check terminated
            if mms_drive.move_is_terminated():
                raise DeliveryTerminateSignal()
            # Check complated
            if mms_drive.move_is_completed(result):
                success = True
                break

            if i + 1 < self.retry_times:
                self.log_info_s(
                    f"slot[{slot_num}] autoload failed, "
                    f"retry {i+1}/{self.retry_times-1} ...")
                self.pause(self.d_config.retry_period)

        # Failed after all retry
        check_cancelled()
        if not success:
            with mms_slot.update_deliver_distance():
                # Reset distance_moved to 0
                mms_slot.set_deliver_distance(
                    p_type, forward=True, distance=0)

            msg = f"slot[{slot_num}] autoload failed after full movement"
            gcode_adapter.respond_error(msg)
            self.log_warning_s(msg)
            raise DeliveryFailedError(msg, mms_slot)

        # Only update not 0
        if distance_moved_sum:
            with mms_slot.update_deliver_distance():
                # Save distance
                is_set = mms_slot.set_deliver_distance(
                    p_type, forward=True, distance=distance_moved_sum)
                if is_set:
                    self.log_info_s(
                        f"slot[{slot_num}] autoload distance saved: "
                        f"{distance_moved_sum:.2f} mm"
                    )

        # Deliver backward without re-select
        check_cancelled()
        safe_dist = -abs(self.pd_config.safety_retract_distance)
        self._check_task_cancelled()
        mms_drive.manual_move(
            safe_dist,
            self.pd_config.sprint_speed,
            self.pd_config.sprint_accel
        )
        self._check_task_cancelled()
        self.log_info_s(
            f"slot[{slot_num}] deliver {safe_dist:.2f} mm finish")

        # Re-calibrate backward with twice factor
        check_cancelled()
        self._selector_refine_calibration(slot_num, factor=-2)
        if mms_slot.selector.is_triggered():
            mms_selector = mms_slot.get_mms_selector()
            mms_selector.update_focus_slot(slot_num)

    def preload_to_gate(self, slot_num):
        # Pre-load don't need to check Inlet
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_slot.check_load_allowed()
        p_type = self.pin_type.gate

        # Reverse select
        self.select_slot(slot_num, reverse=True)

        # Use default distance to prevent using slot meta
        moved = self._load_to_trigger(
            slot_num, p_type, speed=self.pd_config.sprint_speed,
            accel=self.pd_config.sprint_accel)
        if not moved:
            return
        # Deliver backward without re-select
        safe_dist = -abs(self.pd_config.safety_retract_distance)
        mms_drive = mms_slot.get_mms_drive()
        self._check_task_cancelled()
        mms_drive.manual_move(
            safe_dist,
            self.pd_config.sprint_speed,
            self.pd_config.sprint_accel
        )
        self._check_task_cancelled()
        self.log_info_s(
            f"slot[{slot_num}] deliver {safe_dist:.2f} mm finish")

        # Re-calibrate backward with twice factor
        self._selector_refine_calibration(slot_num, factor=-2)
        if mms_slot.selector.is_triggered():
            mms_selector = mms_slot.get_mms_selector()
            mms_selector.update_focus_slot(slot_num)

    def is_exclusively_selected(self, slot_num):
        slot = self.mms.get_mms_slot(slot_num)
        return (self.mms.get_selecting_slots() == [slot_num]
                and slot.selector_is_triggered()
                and slot.get_mms_selector().get_focus_slot() == slot_num)

    def _refresh_selectors(self, slots, expect_released=False):
        for slot in slots:
            self._check_task_cancelled()
            try:
                slot.selector.refresh_state()
            except Exception as e:
                raise CommandError(
                    f"slot[{slot.get_num()}] selector query failed: {e}"
                ) from e
            if expect_released and slot.selector.is_triggered():
                raise DeliveryFailedError(
                    f"slot[{slot.get_num()}] selector is still triggered "
                    "after release; check the release mechanism", slot)

    def _release_detected_selectors(self, startup=False):
        slots = self.mms.get_mms_slots()
        self._refresh_selectors(slots)
        if startup:
            # An early button edge can leave a focus after the switch opens.
            # Only a physically clamped selector needs a startup stroke.
            for selector in self.mms.get_mms_selectors():
                focus = selector.get_focus_slot()
                if (focus is not None and
                        self.mms.get_mms_slot(focus).selector.is_released()):
                    selector.update_focus_slot(None)
        if self.unselect():
            # Opening the original switch alone would miss a selector that
            # came to rest on a neighbouring slot during the release stroke.
            self._refresh_selectors(slots, expect_released=True)
        return True

    def unselect(self, keep_selector=None, *, slot_nums=None,
                 background=False):
        groups = {}
        if slot_nums is None:
            slot_nums = self.mms.get_selecting_slots()
        for slot_num in slot_nums:
            mms_slot = self.mms.get_mms_slot(slot_num)
            mms_selector = mms_slot.get_mms_selector()
            if mms_selector is not keep_selector:
                groups.setdefault(id(mms_selector), []).append(slot_num)
        # Each physical selector gets one release stroke, including when a
        # software focus and an active switch identify different slots.
        for group in groups.values():
            first_slot = self.mms.get_mms_slot(group[0])
            mms_selector = first_slot.get_mms_selector()
            self.log_info_s(f"unselect slots {group} begin")
            self._check_task_cancelled()
            options = (dict(background=True, check=self._check_task_cancelled)
                       if background else {})
            mms_selector.manual_move(
                distance=mms_selector.get_unselect_distance(),
                speed=self.pd_config.sprint_speed,
                accel=self.pd_config.sprint_accel,
                **options,
            )
            self._check_task_cancelled()
            if background:
                # Query the actual switches after the release stroke. Do
                # not wait for a cached edge with the old filament clamped.
                self._refresh_selectors(
                    (s for s in self.mms.get_mms_slots()
                     if s.get_mms_selector() is mms_selector),
                    expect_released=True)
                mms_selector.update_focus_slot(None)
                self.log_info_s(f"unselect slots {group} finished")
                continue
            # The fixed release travel is queued ahead of MCU time. Wait for
            # the existing button callback instead of accepting a stale state
            # or stopping that travel as soon as the switch opens.
            deadline = (
                self.reactor.monotonic()
                + self.d_config.wait_mms_stepper_timeout
            )
            for slot_num in group:
                mms_slot = self.mms.get_mms_slot(slot_num)
                while not mms_slot.selector.is_released():
                    self._check_task_cancelled()
                    remaining = deadline - self.reactor.monotonic()
                    if remaining <= 0.:
                        raise DeliveryFailedError(
                            f"slot[{slot_num}] unselect failed: selector is "
                            "still triggered; check the release mechanism "
                            "and retry",
                            mms_slot,
                        )
                    self.pause(
                        min(self.d_config.wait_mms_stepper_interval, remaining)
                    )
            self._check_task_cancelled()
            mms_selector.update_focus_slot(None)
            self.log_info_s(f"unselect slots {group} finished")
        return bool(groups)

    # ---- MMS Buffer support ----
    def locate_buffer(self, slot_num, pin_type, forward,
                      distance, speed, accel, trigger=True):
        """Seek a spring limit locally; never learn this as Bowden length."""
        attempts = self._buffer_limit_attempts(
            slot_num, pin_type, forward, distance, speed, accel, trigger)
        moved, _ = next(attempts)
        return moved

    def buffer_references(self, slot_num, *, full=True, allow_low_release=False,
                          distance=None):
        """Offer stopped references within a single local search budget."""
        buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()
        pin_type = self.pin_type.outlet if full else self.pin_type.buffer_runout
        return self._buffer_limit_attempts(
            slot_num, pin_type, full,
            buffer.max_measure_distance if distance is None else distance,
            self.pd_config.sprint_speed, self.pd_config.sprint_accel, True,
            allow_low_release)

    def _buffer_limit_attempts(self, slot_num, pin_type, forward, distance,
                               speed, accel, trigger, allow_low_release=False):
        slot = self.mms.get_mms_slot(slot_num)
        buffer = slot.get_mms_buffer()
        moved_total = 0.
        attempts = 1 + self.retry_times
        resume = self.mms.get_mms_resume()

        def check_source():
            if not resume.is_tail_available(slot_num):
                self._check_slot_is_ready(slot_num)
                return
            if not resume.can_drive_filament(slot_num, forward=forward):
                raise DeliveryReadyError(
                    f"slot[{slot_num}] tail is beyond the MMS drive", slot)

        def stop_reference(mcu_pin):
            # Only forward positioning can feed a new piece into an old tail.
            # Stop in the callback; the foreground owner handles recovery.
            if not resume.can_drive_filament(slot_num):
                slot.stop_homing()

        for attempt in range(attempts):
            check_source()
            if allow_low_release:
                # Each stopped pass uses the current spring position.
                trigger = buffer.is_full() or not buffer.is_empty()
                pin_type = (self.pin_type.outlet if trigger else
                            self.pin_type.buffer_runout)
            destination = "trigger" if trigger else "release"
            if attempt:
                speed, accel = buffer.measure_speed, buffer.measure_accel
            if forward:
                slot.gate.add_release_callback(stop_reference)
                slot.inlet.add_trigger_callback(stop_reference)
            try:
                success, moved = self.deliver_once(
                    slot_num, pin_type, forward, trigger,
                    distance, speed, accel)
                moved_total += abs(moved)
                check_source()
            finally:
                if forward:
                    slot.gate.remove_release_callback(stop_reference)
                    slot.inlet.remove_trigger_callback(stop_reference)
            if success and slot.check_pin(pin_type, trigger):
                # No homing callback or motor ownership crosses this yield.
                # A caller requests another pass only if it loses the edge.
                yield moved_total, pin_type
            if attempt + 1 < attempts:
                self.log_info(
                    f"slot[{slot_num}] buffer '{pin_type}' {destination} "
                    f"retry {attempt + 1}/{self.retry_times} "
                    "at calibration speed")
                self.pause(self.d_config.retry_period)
        raise DeliveryFailedError(
            f"slot[{slot_num}] could not confirm buffer position after "
            f"{attempts} local attempts ({moved_total:.2f} mm seeking); "
            f"last target='{pin_type}' {destination}, "
            f"current state={int(slot.check_pin(pin_type, True))}",
            slot)

    def fill_buffer(self, slot_num, distance=None):
        return next(self.buffer_references(slot_num, distance=distance))[0]

    def clear_buffer(self, slot_num, distance=None):
        references = self.buffer_references(slot_num, full=False,
                                            distance=distance)
        return next(references)[0]

    def measure_buffer(self, slot_num, distance, speed, accel):
        self.locate_buffer(
            slot_num, self.pin_type.outlet, True, distance, speed, accel)
        # Sum every accepted segment, including a retry. Reading only the
        # final manual_home loses earlier travel and underestimates capacity.
        return self.locate_buffer(
            slot_num, self.pin_type.buffer_runout, False,
            distance, speed, accel)

    # ---- Deliver commands ----
    # -- Single stepper manual move --
    def mms_drive_move(
        self, slot_num, distance, speed, accel, log=True, drip=False,
        check=None, stop_when=None
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        context = (self.mms_fil_detection.monitor(slot_num)
                   if distance > 0 else nullcontext())
        with context:
            self._check_task_cancelled()
            if drip:
                mms_drive.drip_move(
                    distance, speed, accel, slot_num=slot_num, check=check,
                    stop_when=stop_when)
            else:
                mms_drive.manual_move(distance, speed, accel, slot_num=slot_num)
            self._check_task_cancelled()
        if log:
            self.log_info_s(
                f"slot[{slot_num}] {mms_drive.get_mms_name()} "
                f"move {distance:.2f} mm"
            )

    def has_pending_print_tail_release(self):
        return self._print_tail_release is not None

    def print_tail_release_is_running(self):
        task = self.async_task_sp
        return (self.has_pending_print_tail_release()
                and task.completion is self._print_tail_release
                and task.is_running())

    def start_print_tail_release(self, slot_num):
        # Admission is serialized by G-code. The delivery task owns the
        # motors through switch verification, without taking the G-code lock.
        self.check_background_idle()
        print_start = print_stats_adapter.get_print_start_time()

        def expired():
            return (print_stats_adapter.get_print_start_time() != print_start
                    or print_stats_adapter.is_finished())

        def release():
            try:
                if not expired():
                    result = self.release_tail(slot_num, background=True)
                else:
                    result = False
            except DeliveryTerminateSignal:
                result = False
            except Exception as e:
                # A concurrent swap can start parking before the task exits.
                # Its hardware handoff must see this same result.
                return e
            self._print_tail_release = None
            return result

        task = self.async_task_sp
        if not task.setup(release, stop_when=expired):
            raise MMSException("could not schedule print tail release")
        completion = task.start()
        if not completion:
            raise MMSException("could not start print tail release")
        self._print_tail_release = completion
        return completion

    def cancel_print_tail_release(self):
        if self.print_tail_release_is_running():
            self.async_task_sp.stop()
        self.clear_finished_print_tail_release()

    def clear_finished_print_tail_release(self):
        completion = self._print_tail_release
        if completion is not None and completion.test():
            self._print_tail_release = None

    def wait_for_print_tail_release(self):
        completion = self._print_tail_release
        if completion is None:
            return
        result = completion.wait()
        if isinstance(result, Exception):
            # Cleanup in the same request must not repeat a failed stroke.
            # An explicit repair command can acknowledge it and try again.
            raise result
        if self._print_tail_release is completion:
            self._print_tail_release = None

    def release_tail(self, slot_num, *, background=False):
        """Return True once E can consume the tail with the selector free."""
        if not background:
            self.wait_for_print_tail_release()
        resume = self.mms.get_mms_resume()
        if resume.can_drive_filament(slot_num):
            return False
        slot = self.mms.get_mms_slot(slot_num)
        buffer = slot.get_mms_buffer()
        feed_slot = buffer.get_feed_slot()
        if feed_slot not in (None, slot_num):
            if slot.selector_is_triggered():
                raise DeliveryFailedError(
                    f"cannot release slot[{slot_num}]: "
                    f"buffer feeds slot[{feed_slot}]", slot)
            return True
        selector = slot.get_mms_selector()
        generation = resume.get_status()["generation"]

        def check_owner():
            self._check_task_cancelled()
            # An active print release keeps the old hardware until it
            # exits, including when Entry or T starts a new recovery request.
            if not background and resume.get_status()["generation"] != generation:
                raise DeliveryTerminateSignal()

        buffer.deactivate_monitor(release_tail=not background)
        check_owner()
        if resume.is_extruder_tail(slot_num):
            drive = slot.get_mms_drive()
            if (drive.is_running() and drive.move_type == MoveType.EXTRUDER_SYNC
                    and drive.get_focus_slot() == slot_num):
                if background:
                    raise DeliveryFailedError(
                        f"slot[{slot_num}] is explicitly synchronized; "
                        "run MMS_SLOT_EXTRUDER_UNSYNC before RESUME", slot)
                # Drain the explicit E sync, discarding its old feed lease.
                drive.unsync_to_extruder()
                check_owner()
        if (not slot.selector_is_triggered()
                and selector.get_focus_slot() != slot_num):
            return True
        if not self.wait_mms_selector_and_drive(slot_num):
            raise DeliveryFailedError(
                "cannot release tail: selector or drive is still running",
                slot)
        check_owner()
        # The grip can return while accepted E/drive steps are draining.
        if resume.can_drive_filament(slot_num):
            if feed_slot == slot_num:
                buffer.activate_monitor(slot_num, allow_unreferenced=True)
                buffer.check_feed(slot_num)
            return False
        if (slot.selector_is_triggered()
                or selector.get_focus_slot() == slot_num):
            if background:
                self.unselect(slot_nums=[slot_num], background=True)
            else:
                self.unselect(slot_nums=[slot_num])
                if resume.is_extruder_tail(slot_num):
                    # A release can end on an adjacent slot of this selector.
                    self._refresh_selectors(
                        (s for s in self.mms.get_mms_slots()
                         if s.get_mms_selector() is selector),
                        expect_released=True)
        return True

    def move_with_buffer(self, slot_num, distance, speed, check,
                         on_segment_accepted=None):
        """Execute normal E with one buffer owner, releasing a spent tail."""
        resume = self.mms.get_mms_resume()
        buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()

        def check_feed():
            check()
            buffer.check_feed(slot_num)

        check()
        remaining = distance
        while not self.release_tail(slot_num):
            check_feed()
            if not remaining:
                return
            remaining -= extruder_adapter.move_filament(
                remaining, speed, check=check_feed,
                on_segment_accepted=on_segment_accepted,
                stop_when=lambda: not resume.can_drive_filament(slot_num))
        check()
        if remaining:
            extruder_adapter.move_filament(
                remaining, speed, check=check,
                on_segment_accepted=on_segment_accepted)
        check_feed()

    def mms_drive_sprint(self, slot_num, distance):
        mms_drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        context = (self.mms_fil_detection.monitor(slot_num)
                   if distance > 0 else nullcontext())
        with context:
            self._check_task_cancelled()
            mms_drive.manual_move(
                distance,
                self.pd_config.sprint_speed,
                self.pd_config.sprint_accel,
                slot_num=slot_num,
            )
            self._check_task_cancelled()
        self.log_info_s(
            f"slot[{slot_num}] {mms_drive.get_mms_name()} "
            f"sprint {distance:.2f} mm"
        )

    def check_background_idle(self, *, allow_tail_release=False):
        ("Reject competing delivery tasks, "
         "including waits and stop unwinding.")
        if self._stopping:
            raise CommandError("MMS_STOP is still in progress; retry later")
        autoload = self._get_autoload()
        delivery_busy = self.async_task_sp.is_running() and not (
            allow_tail_release and self.print_tail_release_is_running())
        if delivery_busy or (autoload is not None and autoload.is_busy()):
            raise CommandError(
                "another background delivery is still running; retry later")

    def deliver_task(self, func, params=None, wait=False, manage_feeds=True,
                     stop_when=None):
        """One delivery owner; WAIT only controls whether its caller waits."""
        self.check_background_idle()

        def run_delivery(**kwargs):
            # Claim the whole feed path, not just an idle motor between bursts.
            self._check_task_cancelled()
            # Buffer maintenance owns its measured reference across handoff.
            context = (self.maintenance_feeds() if manage_feeds
                       else nullcontext())
            try:
                with context:
                    self._check_task_cancelled()
                    return func(**kwargs)
            except (MMSException, CommandError) as e:
                # The async owner reports only completion to WAIT callers.
                # Preserve the specific cause in the console for WAIT=0 too.
                self.log_error(str(e))
                return False

        try:
            if not self.async_task_sp.setup(run_delivery, params or {},
                                            stop_when=stop_when):
                raise CommandError("delivery task could not start")
            completion = self.async_task_sp.start()
            if not completion:
                raise CommandError("delivery task could not start")
        except Exception as e:
            raise CommandError(f"delivery task could not start: {e}") from e
        # Keep this completion local: a later task may replace the task field
        # before this waiter wakes. Cancellation returns -1, not success.
        if wait and completion.wait() is not True:
            raise CommandError(
                f"{func.__name__.upper()} failed or was cancelled; "
                "inspect the preceding slot/sensor error"
            )
        return completion

    @log_time_cost("log_info_s")
    def mms_load(self, slot_num):
        self.log_info_s(f"slot[{slot_num}] load begin")
        mms_slot = self.mms.get_mms_slot(slot_num)
        buffer = mms_slot.get_mms_buffer()
        try:
            mms_slot.check_load_allowed()
            # The caller has retired competing Gates. The downstream endpoint
            # may already be triggered when we take over a different strand.
            charge = self.mms.mms_charge
            if charge.get_charged_slot() not in (None, slot_num):
                charge.invalidate_loaded_slot()
            # The caller has already retired other paths under the same owner.
            if mms_slot.entry_is_set():
                self.load_to_entry(slot_num)
            else:
                self.load_to_outlet(slot_num)
            self.select_slot(slot_num)
            selector = mms_slot.get_mms_selector()
            downstream = (
                mms_slot.entry if mms_slot.entry_is_set() else mms_slot.outlet
            )
            if (
                not mms_slot.inlet.is_triggered()
                or not mms_slot.gate.is_triggered()
                or not downstream.is_triggered()
                or not mms_slot.selector_is_triggered()
                or selector.get_focus_slot() != slot_num
            ):
                raise DeliveryFailedError(
                    f"slot[{slot_num}] load path not confirmed; "
                    "check Inlet/Gate/"
                    f"{downstream.get_pin_type()} and selector",
                    mms_slot,
                )
            buffer.prepare_feed(slot_num, loading=True)
            if mms_slot.entry_is_set():
                mms_slot.entry.refresh_state()
                self._check_task_cancelled()
            if (mms_slot.entry_is_set()
                    and not mms_slot.entry.is_triggered()):
                raise DeliveryFailedError(
                    f"slot[{slot_num}] Entry released during buffer "
                    "preparation; retry LOAD", mms_slot)

        except DeliveryTerminateSignal:
            buffer.deactivate_monitor()
            self.log_info_s(f"slot[{slot_num}] load terminated")
            return False
        except Exception as e:
            buffer.deactivate_monitor()
            self.log_error(f"slot[{slot_num}] load error: {e}")
            return False
        self.log_info_s(f"slot[{slot_num}] load finish")
        return True

    @log_time_cost("log_info_s")
    def mms_unload(self, slot_num=None):
        msg_slot = slot_num if slot_num is not None else "*"
        self.log_info_s(f"slot[{msg_slot}] unload begin")
        try:
            if slot_num is not None:
                self.unload_to_gate(slot_num)
            else:
                self.unload_loading_slots()
        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{msg_slot}] unload terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{msg_slot}] unload error: {e}")
            return False
        self.log_info_s(f"slot[{msg_slot}] unload finish")
        return True

    @log_time_cost("log_info_s")
    def mms_pop(self, slot_num):
        self.log_info_s(f"slot[{slot_num}] pop begin")
        try:
            self.unload_to_inlet(slot_num)
        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{slot_num}] pop terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] pop error: {e}")
            return False
        self.log_info_s(f"slot[{slot_num}] pop finish")
        return True

    @log_time_cost("log_info_s")
    def mms_prepare(self, slot_num):
        self.log_info_s(f"slot[{slot_num}] prepare begin")
        try:
            # Skip wanted slot
            self.unload_loading_slots(skip_slot=slot_num)

            mms_slot = self.mms.get_mms_slot(slot_num)
            # Load wanted slot to gate triggered
            self.load_to_gate(slot_num)
            # Unload wanted slot to gate released
            self.unload_to_gate(slot_num)

        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{slot_num}] prepare terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] prepare error: {e}")
            return False
        self.log_info_s(f"slot[{slot_num}] prepare finish")
        return True

    @log_time_cost("log_info_s")
    def mms_move(self, slot_num, distance, speed=None, accel=None):
        if abs(distance) > self.pd_config.bowden_distance:
            self.log_warning(
                f"slot[{slot_num}] can not move {distance}mm, "
                "check config 'bowden_distance'")
            return False

        try:
            if distance:
                printer_adapter.get_mms_charge().invalidate_loaded_slot(
                    slot_num)
            if distance > 0:
                self.move_forward(slot_num, distance, speed, accel)
            else:
                self.move_backward(slot_num, distance, speed, accel)
        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{slot_num}] move terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] move error: {e}")
            return False
        return True

    @log_time_cost("log_info_s")
    def mms_drip_move(self, slot_num, distance, speed=None, accel=None):
        if abs(distance) > self.pd_config.bowden_distance:
            self.log_warning(
                f"slot[{slot_num}] can not drip move {distance}mm, "
                "check config 'bowden_distance'")
            return False

        try:
            if distance:
                printer_adapter.get_mms_charge().invalidate_loaded_slot(
                    slot_num)
            if distance > 0:
                self.drip_move_forward(slot_num, distance, speed, accel)
            else:
                self.drip_move_backward(slot_num, distance, speed, accel)
        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{slot_num}] drip move terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] drip move error: {e}")
            return False
        return True

    @log_time_cost("log_info_s")
    def mms_select(self, slot_num):
        try:
            self.select_slot(slot_num)
        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{slot_num}] select terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] select error: {e}")
            return False
        return True

    # @log_time_cost("log_info_s")
    def mms_unselect(self):
        try:
            self.unselect()
        except DeliveryTerminateSignal:
            self.log_info_s("slot[*] unselect terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[*] unselect error: {e}")
            return False
        return True

    def _can_walk(self):
        msg = "can not walk when printer is "
        conditions = [
            (self.mms.printer_is_shutdown, "shutdown"),
            (print_stats_adapter.is_printing, "printing"),
            (pause_resume_adapter.is_paused, "paused"),
            (self.mms.printer_is_resuming, "resuming"),

            (self.mms.mms_selector_is_running, "busy"),
            (self.mms.mms_drive_is_running, "busy"),
        ]
        for condition,state in conditions:
            if condition():
                self.log_warning(msg+state)
                return False

        return True

    def mms_bowden_calibration(self):
        if not self._can_walk():
            return False

        spd = self.d_config.walk_speed
        acc = self.d_config.walk_accel

        measured = False
        distance = self.pd_config.bowden_distance
        self.log_info("mms bowden calibration begin")
        for slot_num in self.mms.get_slot_nums():
            if not self._can_walk():
                return False
            slot = self.mms.get_mms_slot(slot_num)
            if not slot.is_ready():
                continue
            try:
                self.unload_loading_slots()
                self.unload_to_gate(slot_num)
                self.load_to_gate(slot_num)
                first = (self.pin_type.entry if slot.entry_is_set()
                         else self.pin_type.outlet)
                with slot.update_deliver_distance(replace=True):
                    forward = self._load_to_trigger(
                        slot_num, first, distance=distance,
                        speed=spd, accel=acc)
                    backward = self._unload_to_release(
                        slot_num, self.pin_type.gate, distance=distance,
                        speed=spd, accel=acc)
                    if not forward or not backward or not self._can_walk():
                        raise DeliveryFailedError(
                            f"slot[{slot_num}] calibration incomplete", slot)
                    self._safety_retract(slot_num)
                    end = slot.entry if slot.entry_is_set() else slot.outlet
                    slot.gate.refresh_state()
                    end.refresh_state()
                    self._check_task_cancelled()
                    for pin in (slot.gate, end):
                        if pin.is_triggered():
                            raise DeliveryFailedError(
                                f"slot[{slot_num}] calibration: "
                                f"{pin.get_pin_type()} still detects filament",
                                slot)
                    if not slot.meta.write_file():
                        raise DeliveryFailedError(
                            f"slot[{slot_num}] calibration save failed", slot)
                    measured = True

            except DeliveryTerminateSignal:
                self.log_info("mms bowden calibration terminated")
                return False
            except Exception as e:
                self.log_error(f"mms bowden calibration error: {e}")
                return False

        if not measured:
            self.log_warning("no slot available for bowden calibration")
            return False
        self.unselect()
        self.log_info("mms bowden calibration finish")
        self.mms.log_deliver_distance()
        return True

    def verify_pins(self, mms_slot, loaded):
        trigger = loaded
        slot_num = mms_slot.get_num()
        if not mms_slot.inlet.is_triggered():
            raise Exception(f"slot[{slot_num}] Inlet")
        if (mms_slot.gate.is_triggered() != trigger):
            raise Exception(f"slot[{slot_num}] Gate")
        if (mms_slot.buffer_runout.is_triggered() == trigger):
            raise Exception(f"slot[{slot_num}] Buffer_runout: PA4")
        if (mms_slot.outlet.is_triggered() != trigger):
            raise Exception(f"slot[{slot_num}] Outlet: PA5")
        if mms_slot.entry_is_set() \
            and (mms_slot.entry_is_triggered() != trigger):
            raise Exception(f"slot[{slot_num}] Entry")

    def verify_rfid(self, mms_slot):
        slot_rfid = mms_slot.slot_rfid
        if not slot_rfid.is_enabled():
            return

        tag_uid = slot_rfid.get_tag_uid()
        if not tag_uid:
            raise Exception(f"slot[{mms_slot.get_num()}] RFID")

    def _finish_slot_walk(self, check_pins=False):
        if not self._can_walk():
            return False
        try:
            self.unload_loading_slots()
            for n in self.mms.get_slot_nums():
                slot = self.mms.get_mms_slot(n)
                if check_pins:
                    self.verify_pins(slot, False)
                    continue
                end = slot.entry if slot.entry_is_set() else slot.outlet
                if slot.gate.is_triggered() or end.is_triggered():
                    raise DeliveryFailedError(
                        f"slot[{n}] diagnostic unload incomplete", slot)
            return self._can_walk()
        except DeliveryTerminateSignal:
            return False
        except Exception as e:
            self.log_error(f"diagnostic unload failed: {e}")
            return False

    def mms_slots_check(self, check_rfid=True):
        self.log_info("slots check begin")
        # Walk through all SLOTs and check every Pin
        for slot_num in self.mms.get_slot_nums():
            if not self._can_walk():
                return False

            mms_slot = self.mms.get_mms_slot(slot_num)
            rfid_enabled = mms_slot.slot_rfid.is_enabled()
            slot_check_rfid = check_rfid and rfid_enabled
            if slot_check_rfid:
                rfid_status = "CHECK"
            elif not rfid_enabled:
                rfid_status = "SKIP reason=disabled"
            else:
                rfid_status = "SKIP reason=round_limit"
            self.log_info(f"slot[{slot_num}] RFID={rfid_status}")
            tag_uid = None

            try:
                # Unload release all pins except Inlet
                self.unload_loading_slots()
                self.pause(1)
                if not self._can_walk():
                    return False
                self.log_info(
                    "unload: " + mms_slot.format_pins_status())
                # Verify
                self.verify_pins(mms_slot, False)

                if slot_check_rfid:
                    tag_uid = mms_slot.slot_rfid.detect_tag()
                if tag_uid:
                    # Tag UID is not None, retract nearby slot_pairs
                    slot_nums = self.mms.get_slot_nums()
                    i = slot_nums.index(slot_num)
                    offset = (i // 2) * 2
                    pairs = slot_nums[offset:offset+2]

                    # Retract until no tag can be detected
                    for i in range(self.retry_times):
                        for s_num in pairs:
                            if not self._can_walk():
                                return False
                            self.log_info(
                                f"slot[{s_num}] retract to free RFID tag")
                            self._safety_retract(s_num)
                            # Detect again
                            tag_uid = mms_slot.slot_rfid.detect_tag()
                            if not tag_uid:
                                break

                        if not tag_uid:
                            break

                if tag_uid:
                    self.log_warning(
                        f"slot[{slot_num}] has already detected "
                        f"RFID tag: {tag_uid}"
                    )

                # Load to check pins and RFID detection
                for i in range(self.retry_times):
                    if not self._can_walk():
                        return False
                    with (mms_slot.slot_rfid.detect_only()
                          if slot_check_rfid else nullcontext()):
                        # Trigger all Outlet and Entry
                        self.load_to_outlet(slot_num)
                        if mms_slot.entry_is_set() \
                            and not mms_slot.entry_is_triggered():
                            self.load_to_entry(slot_num)
                        self.log_info(
                            "load: " + mms_slot.format_pins_status())
                        # Verify
                        self.verify_pins(mms_slot, True)

                    if not slot_check_rfid:
                        break
                    tag_uid = mms_slot.slot_rfid.get_tag_uid()
                    if tag_uid:
                        # Tag has been detected, exit retry
                        break
                    self.log_info(
                        f"slot[{slot_num}] no RFID tag detected, "
                        f"retry {i+1}/{self.retry_times} ..."
                    )
                    # Context manager is exit, detect has been teardown
                    # Unload to Gate and retry
                    if not self._can_walk():
                        return False
                    self.unload_to_gate(slot_num)

                # Raise Exception if RFID is still detecting
                if slot_check_rfid:
                    self.verify_rfid(mms_slot)

            except DeliveryTerminateSignal:
                self.log_info("slots check terminated")
                return False
            except DeliveryReadyError:
                pass
            except Exception as e:
                msg = f"slots check error: {e}"
                self.log_error(msg)
                gcode_adapter.respond_error(msg)
                return False

        if not self._finish_slot_walk(check_pins=True):
            return False

        self.log_info("slots check finish")
        return True

    def mms_slots_check_loop(self):
        self.log_info("slots loop begin")
        total = self.pd_config.slots_loop_times
        limit = self.pd_config.slots_rfid_check_loops
        for i in range(total):
            check_rfid = limit == -1 or i < limit
            msg = f"############### check loop: {i+1}/{total} ###############"
            self.log_info(msg)
            success = self.mms_slots_check(check_rfid=check_rfid)
            if not success or not self._can_walk():
                return False
        self.log_info("slots check loop finish")
        self.log_info("#" * 60)
        return True


    def mms_slots_walk(self):
        self.log_info("slots walk begin")
        # Walk through all SLOTs
        for slot_num in self.mms.get_slot_nums():
            if not self._can_walk():
                return False

            try:
                self.unload_loading_slots(skip_slot=slot_num)
                self.pause(1)
                if not self._can_walk():
                    return False

                mms_slot = self.mms.get_mms_slot(slot_num)
                if mms_slot.entry_is_set():
                    self.load_to_entry(slot_num)
                else:
                    self.load_to_outlet(slot_num)

            except DeliveryTerminateSignal:
                self.log_info("slots walk terminated")
                return False
            except DeliveryReadyError:
                pass
            except Exception as e:
                self.log_error(f"slots walk error:{e}")
                return False

        if not self._finish_slot_walk():
            return False
        self.unselect()
        self.log_info("slots walk finish")
        return True

    def mms_slots_loop(self):
        self.log_info("slots loop begin")
        total = self.pd_config.slots_loop_times
        for i in range(total):
            msg = f"############### loop: {i+1}/{total} ###############"
            self.log_info(msg)
            success = self.mms_slots_walk()
            if not success or not self._can_walk():
                return False
        self.log_info("slots loop finish")
        return True


    @log_time_cost("log_info_s")
    def mms_stop(self, slot_num=None, wait=True):
        msg_slot = slot_num if slot_num is not None else "*"
        self.log_info_s(f"slot[{msg_slot}] stop begin")

        try:
            deadline = (self.reactor.monotonic()
                        + self.d_config.wait_mms_stepper_timeout)
            slots = ([self.mms.get_mms_slot(slot_num)]
                     if slot_num is not None else self.mms.get_mms_slots())
            buffers = set()
            steppers = {}
            for mms_slot in slots:
                slot_pin = mms_slot.get_waiting_pin()
                if slot_pin:
                    mms_slot.stop_homing(slot_pin)
                buffers.add(mms_slot.get_mms_buffer())
                for stepper in (mms_slot.get_mms_drive(),
                                mms_slot.get_mms_selector()):
                    steppers.setdefault(stepper, mms_slot.get_num())

            # Stop all new callbacks before yielding to in-flight work.
            for mms_buffer in buffers:
                mms_buffer.deactivate_monitor(wait=False)
            for stepper in steppers:
                stepper.terminate_moving()

            # A deadline callback must only request stop: joining here could
            # wait for the very operation whose reactor stack it interrupted.
            if not wait:
                return True

            # Slots can share a buffer, selector and drive. Join each owner
            # once, and retain a timeout even if it exits during a later wait.
            stopped = True
            for mms_buffer in buffers:
                stopped = (mms_buffer.deactivate_monitor(deadline=deadline)
                           and stopped)
            for stepper, owner_slot in steppers.items():
                if not stepper.is_running():
                    continue
                remaining = deadline - self.reactor.monotonic()
                if remaining <= 0.:
                    stopped = False
                else:
                    stopped = self._wait_mms_stepper(
                        owner_slot, stepper, timeout=remaining) and stopped
            stopped = stopped and not any(
                stepper.is_running() for stepper in steppers)
        except Exception as e:
            self.log_error(f"slot[{msg_slot}] stop error: {e}")
            return False

        if not stopped:
            self.log_warning(f"slot[{msg_slot}] stop still pending")
            return False
        self.log_info_s(f"slot[{msg_slot}] stop finish")
        return True

    # ---- GCode commands ----
    def _check_maintenance_source(self, gcmd):
        resume = self.mms.get_mms_resume()
        if (resume.get_exhausted_slot() is not None
                and not resume.accept_cleared_path()):
            raise gcmd.error(
                f"{gcmd.get_command()}: clear the exhausted path first, "
                "or use T to handle its remaining tail")

    def _check_load_target(self, gcmd, slot_num):
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")
        slot = self.mms.get_mms_slot(slot_num)
        try:
            slot.check_load_allowed()
        except MMSException as e:
            raise gcmd.error(str(e)) from e
        if not slot.is_ready():
            raise gcmd.error(
                f"{gcmd.get_command()}: slot[{slot_num}] Inlet is empty; "
                "load this slot before ejecting the current filament"
            )
        self._check_maintenance_source(gcmd)
        return slot

    def cmd_MMS_LOAD_SLOT(self, gcmd):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT_NUM", minval=0)
        slot = self._check_load_target(gcmd, slot_num)
        resume = self.mms.get_mms_resume()
        generation = resume.get_status()["generation"]

        def check_current():
            if (self.async_task_sp.current_is_cancelled()
                    or resume.get_status()["generation"] != generation):
                raise gcmd.error("MMS_LOAD_SLOT was cancelled")

        def mms_load_slot():
            # One owner spans eject preparation, unselection and new feeding.
            # Internal actions do not repeat public admission or start tasks.
            check_current()
            self._check_load_target(gcmd, slot_num)
            self.mms.mms_eject._manual_eject(gcmd, unselect=True)
            check_current()
            if not slot.is_ready():
                raise gcmd.error(
                    f"MMS_LOAD_SLOT blocked: slot[{slot_num}] "
                    "Inlet became empty "
                    "during ejection; refill it and retry"
                )
            # The motion scope belongs to the worker that performs the load.
            with toolhead_adapter.motion_scope(check=check_current):
                return self.mms_load(slot_num)

        self.deliver_task(mms_load_slot, wait=True)

    def cmd_MMS_LOAD(self, gcmd):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT", minval=0)
        slot = self._check_load_target(gcmd, slot_num)

        def load():
            eject = self.mms.mms_eject
            buffer = slot.get_mms_buffer()
            endpoint = None
            if (self.mms.mms_charge.get_charged_slot() in (None, slot_num)
                    and slot.selector_is_triggered()
                    and buffer.get_feed_slot() == slot_num):
                endpoint = slot.entry if slot.entry_is_set() else slot.outlet
                endpoint.refresh_state()
                self._check_task_cancelled()
            # Recheck after scheduling and any MCU query, before stopping feed.
            self._check_load_target(gcmd, slot_num)
            gates = eject.check_paths()
            if (endpoint is not None and endpoint.is_triggered()
                    and gates == [slot_num] and eject._get_progress() is None
                    and self.mms.mms_charge.get_charged_slot() in
                    (None, slot_num) and self.is_exclusively_selected(slot_num)
                    and buffer.get_feed_slot() == slot_num):
                buffer.check_feed(slot_num)
                return True
            with self.maintenance_feeds():
                self._check_task_cancelled()
                self._check_load_target(gcmd, slot_num)
                if not eject.prepare_charge(slot_num, gcmd=gcmd):
                    return False
                self._check_task_cancelled()
                return self.mms_load(slot_num)

        self.deliver_task(
            load, manage_feeds=False,
            wait=bool(gcmd.get_int("WAIT", default=0)))

    def cmd_MMS_UNLOAD(self, gcmd):
        self._withdraw_task(gcmd, pop=False)

    def cmd_MMS_POP(self, gcmd):
        self._withdraw_task(gcmd, pop=True)

    def _withdraw_task(self, gcmd, pop):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT", default=None, minval=0)
        if not self.mms.slot_is_available(slot_num, can_none=True):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")

        def withdraw():
            self._check_maintenance_source(gcmd)
            eject = self.mms.mms_eject
            slot = (self.mms.get_mms_slot(slot_num)
                    if slot_num is not None else None)
            needs_head = eject.withdraw_needs_eject(slot_num)
            if (not needs_head and slot is not None and slot.gate.is_released()
                    and (not pop or slot.inlet.is_released())):
                return True
            with self.maintenance_feeds(slot_num):
                self._check_task_cancelled()
                self._check_maintenance_source(gcmd)
                if eject.withdraw_needs_eject(slot_num):
                    eject._manual_eject(gcmd)
                    self._check_task_cancelled()
                    if not pop or (slot is not None and not slot.is_ready()):
                        # The withdrawal may already have cleared Inlet too.
                        return True
                if slot_num is not None:
                    targets = [slot_num]
                elif pop:
                    targets = [s.get_num() for s in self.mms.get_mms_slots()
                               if s.is_ready()]
                else:
                    targets = self.mms.get_loading_slots()
                action = self.mms_pop if pop else self.mms_unload
                for target in targets:
                    eject.release_other_selectors(target)
                    self._check_task_cancelled()
                    if not action(target):
                        raise gcmd.error(f"{gcmd.get_command()} failed")
            return True

        self.deliver_task(withdraw, manage_feeds=False,
                          wait=bool(gcmd.get_int("WAIT", default=0)))

    def cmd_MMS_PREPARE(self, gcmd):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT", minval=0)
        self._upstream_task(gcmd, self.mms_prepare, slot_num)

    def cmd_MMS_MOVE(self, gcmd):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")

        valid_distance = abs(self.pd_config.bowden_distance)
        valid_speed = abs(self.pd_config.speed_drive)
        valid_accel = abs(self.pd_config.accel_drive)

        distance = gcmd.get_float(
            "DISTANCE",
            default=0.0,
            minval=-valid_distance,
            maxval=valid_distance
        )
        speed = gcmd.get_float(
            "SPEED",
            default=valid_speed,
            above=0.0,
            maxval=valid_speed
        )
        accel = gcmd.get_float(
            "ACCEL",
            default=valid_accel,
            minval=0.0,
            maxval=valid_accel
        )

        self.deliver_task(
            self.mms_move, {"slot_num": slot_num, "distance": distance,
                            "speed": speed, "accel": accel},
            wait=bool(gcmd.get_int("WAIT", default=0)))

    def cmd_MMS_DRIP_MOVE(self, gcmd):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")

        valid_distance = abs(self.pd_config.bowden_distance)
        distance = gcmd.get_float(
            "DISTANCE",
            default=0.0, minval=-valid_distance, maxval=valid_distance
        )
        speed = gcmd.get_float("SPEED", default=None, above=0.0)
        accel = gcmd.get_float("ACCEL", default=None, minval=0.0)

        self.deliver_task(
            self.mms_drip_move, {"slot_num": slot_num, "distance": distance,
                            "speed": speed, "accel": accel},
            wait=bool(gcmd.get_int("WAIT", default=0)))

    def cmd_MMS_SELECT(self, gcmd):
        self.mms.check_maintenance(gcmd, selector_only=True)
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")

        wait = bool(gcmd.get_int("WAIT", default=0))
        slot = self.mms.get_mms_slot(slot_num)
        if (slot.selector_is_triggered()
                and slot.get_mms_selector().get_focus_slot() == slot_num):
            # No path change: retain the valid reserve and manual-E supply.
            try:
                self.select_slot(slot_num)
            except Exception as e:
                raise gcmd.error(str(e)) from e
            return
        self.deliver_task(
            self.mms_select, {"slot_num": slot_num}, wait=wait)

    def cmd_MMS_UNSELECT(self, gcmd):
        self.mms.check_maintenance(gcmd, selector_only=True)
        self.deliver_task(
            self._release_detected_selectors,
            wait=bool(gcmd.get_int("WAIT", default=0)))

    def _upstream_task(self, gcmd, action, slot_num=None):
        # Only the material found at entry to this task is external.
        # The walk/check itself may reach Entry and withdraw its own filament.
        def check():
            if slot_num is not None:
                self._check_load_target(gcmd, slot_num)
            else:
                self._check_maintenance_source(gcmd)
            try:
                needs_head = self.mms.mms_eject._eject_needs_toolhead()
            except MMSException as e:
                raise gcmd.error(str(e)) from e
            if needs_head:
                raise gcmd.error(
                    f"{gcmd.get_command()} needs a clear toolhead; "
                    "use MMS_EJECT before upstream maintenance")

        def run():
            check()
            with self.maintenance_feeds():
                self._check_task_cancelled()
                check()
                if slot_num is not None:
                    return action(slot_num=slot_num)
                return action()

        check()
        resume = self.mms.get_mms_resume()
        generation = resume.get_status()["generation"]

        def interrupted():
            return (pause_resume_adapter.is_paused()
                    or print_stats_adapter.is_printing()
                    or resume.get_status()["generation"] != generation)

        self.deliver_task(run, wait=bool(gcmd.get_int("WAIT", default=0)),
                          manage_feeds=False, stop_when=interrupted)

    def cmd_MMS_BOWDEN_CALIBRATION(self, gcmd=None):
        self.mms.check_maintenance(gcmd)
        self._upstream_task(gcmd, self.mms_bowden_calibration)

    def cmd_MMS_SLOTS_CHECK(self, gcmd=None):
        self.mms.check_maintenance(gcmd)

        self._upstream_task(gcmd, self.mms_slots_check)

    def cmd_MMS_SLOTS_CHECK_LOOP(self, gcmd=None):
        self.mms.check_maintenance(gcmd)

        self._upstream_task(gcmd, self.mms_slots_check_loop)

    def cmd_MMS_SLOTS_WALK(self, gcmd=None):
        self.mms.check_maintenance(gcmd)

        self._upstream_task(gcmd, self.mms_slots_walk)

    def cmd_MMS_SLOTS_LOOP(self, gcmd=None):
        self.mms.check_maintenance(gcmd)

        self._upstream_task(gcmd, self.mms_slots_loop)

    def stop_and_join(self, slot_num=None, deadline=None):
        """Cancel background delivery and join its actual movement owners."""
        autoload = self._get_autoload()
        if self._stopping:
            raise MMSException("MMS_STOP is still in progress; retry later")
        if slot_num is not None and (self.async_task_sp.is_running()
                or (autoload is not None and autoload.is_busy())):
            raise MMSException(
                "MMS_STOP SLOT cannot cancel a running background delivery; "
                "use MMS_STOP without SLOT to stop all background delivery")

        # Physical stop can yield after an old task exits. Keep both task
        # entry points closed until this complete stop/join sequence returns.
        self._stopping = True
        try:
            completions = []
            stop_deadline = (self.reactor.monotonic()
                             + self.d_config.wait_mms_stepper_timeout)
            deadline = (
                min(deadline, stop_deadline)
                if deadline is not None
                else stop_deadline
            )
            if slot_num is None:
                if self.async_task_sp.is_running():
                    completions.append(self.async_task_sp.completion)
                    self.async_task_sp.stop()
                if autoload is not None:
                    completion = autoload.request_stop()
                    if completion is not None:
                        completions.append(completion)

            stopped = self.mms_stop(slot_num)
            for completion in completions:
                completion.wait(deadline)
            # The old task may be done while execute() is still unwinding
            # its physical stop. Do not release admission during that tail.
            if slot_num is None and autoload is not None:
                while (
                    autoload.is_busy() and self.reactor.monotonic() < deadline
                ):
                    self.pause(min(self.d_config.wait_mms_stepper_interval,
                                   deadline - self.reactor.monotonic()))
            tasks_done = (all(completion.test() for completion in completions)
                          and (autoload is None or not autoload.is_busy()))
            if slot_num is None:
                motors_idle = (not self.mms.mms_drive_is_running()
                               and not self.mms.mms_selector_is_running())
            else:
                mms_slot = self.mms.get_mms_slot(slot_num)
                motors_idle = (not mms_slot.get_mms_drive().is_running()
                               and not mms_slot.get_mms_selector().is_running())
            if not stopped or not tasks_done or not motors_idle:
                raise MMSException(
                    "MMS_STOP did not finish; resolve the reported stop "
                    "condition, then retry MMS_STOP")
        finally:
            self._stopping = False

    def cmd_MMS_STOP(self, gcmd=None):
        self.mms.check_maintenance(gcmd, allow_running=True)
        slot_num = gcmd.get_int("SLOT", default=None, minval=0)
        if not self.mms.slot_is_available(slot_num, can_none=True):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")
        try:
            self.stop_and_join(slot_num)
        except MMSException as e:
            raise gcmd.error(str(e))

    def cmd_MMS_TEST_SELECTOR_MEASURE(self, gcmd):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")

        self.deliver_task(self._measure_selector, {"slot_num": slot_num},
                          wait=True)

    def _measure_selector(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_selector = mms_slot.get_mms_selector()
        result = self._selector_deliver_to(slot_num)
        if not mms_selector.move_is_completed(result):
            raise DeliveryFailedError(
                f"slot[{slot_num}] selector trigger not reached", mms_slot)

        pin_type = self.pin_type.selector
        wait = mms_slot.get_wait_func(pin_type)
        with wait():
            self._check_task_cancelled()
            result = mms_selector.manual_home(
                distance = 100,
                speed = 2,
                accel = 2,
                forward = True,
                trigger = False,
                endstop_pair_lst = mms_slot.format_endstop_pair(pin_type),
                check_endstop=lambda: self._check_home_endstop(
                    slot_num, pin_type, False),
            )
            self._check_task_cancelled()

        if not mms_selector.move_is_completed(result):
            raise DeliveryFailedError(
                f"slot[{slot_num}] selector release not reached", mms_slot)
        result = mms_selector.get_distance_moved()
        self.log_info(
            f"slot[{slot_num}] measured selector trigger "
            f"interval is {result:.2f} mm"
        )
        return True

    def _check_extruder_sync_source(self, slot_num, borrowed):
        slot = self.mms.get_mms_slot(slot_num)
        resume = self.mms.get_mms_resume()
        if (not self.is_exclusively_selected(slot_num)
                or not resume.can_drive_filament(slot_num)):
            raise MMSException(f"slot[{slot_num}] must be exclusively "
                               "selected and drivable for synchronized feed")
        charged = self.mms.mms_charge.get_charged_slot()
        if charged not in (None, slot_num):
            raise MMSException(f"slot[{charged}] owns the loaded path")
        loaded = borrowed or charged is not None
        for other in self.mms.get_mms_slots():
            if other is not slot and other.gate.is_triggered():
                raise MMSException("another slot occupies the Gate path")
            if other.entry_is_set():
                loaded = loaded or other.entry.is_triggered()
            elif other.outlet.is_triggered():
                if other.get_mms_buffer() is not slot.get_mms_buffer():
                    raise MMSException("another buffer's Outlet is triggered")
                loaded = True
        if loaded:
            # A released Outlet is normal during E consumption. With an
            # accepted or borrowed strand the Inlet/Gate path still matters.
            resume.check_loaded_source(slot_num)

    def cmd_MMS_SLOT_EXTRUDER_SYNC(self, gcmd):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")

        slot = self.mms.get_mms_slot(slot_num)
        buffer = slot.get_mms_buffer()
        resume = self.mms.get_mms_resume()

        def sync():
            self._check_extruder_sync_source(
                slot_num, buffer.get_feed_slot() is not None)
            toolhead_adapter.wait_moves()
            self._check_task_cancelled()
            source = buffer.get_feed_slot()
            if source not in (None, slot_num):
                raise gcmd.error(f"slot[{source}] owns the buffer")
            self._check_extruder_sync_source(slot_num, source is not None)
            generation = (resume.get_status()["generation"]
                          if source is not None else None)
            self.pause_feeds([slot_num])
            self._check_task_cancelled()
            self._check_extruder_sync_source(slot_num, source is not None)
            slot.get_mms_drive().sync_to_extruder(slot_num, generation)
            return True

        self.deliver_task(sync, wait=True, manage_feeds=False)

    def cmd_MMS_SLOT_EXTRUDER_UNSYNC(self, gcmd):
        self.mms.check_maintenance(gcmd, allow_running=True)
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")

        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        if (not mms_drive.is_running()
                or mms_drive.move_type != MoveType.EXTRUDER_SYNC):
            return
        self.check_background_idle()
        if mms_drive.get_focus_slot() not in (None, slot_num):
            raise gcmd.error("another slot owns the drive")
        generation = mms_drive.unsync_to_extruder()
        resume = self.mms.get_mms_resume()
        if (generation is None
                or generation != resume.get_status()["generation"]):
            return
        try:
            self._check_extruder_sync_source(slot_num, borrowed=True)
        except MMSException as e:
            self.log_info(f"slot[{slot_num}] unsynchronized; original feed "
                          f"was not restored: {e}")
            return
        buffer = mms_slot.get_mms_buffer()
        try:
            buffer.activate_monitor(slot_num, allow_unreferenced=True)
            buffer.check_feed(slot_num)
        except MMSException as e:
            raise gcmd.error(str(e)) from e

    def cmd_MMS_D_TEST(self, gcmd):
        return

    # For KlipperScreen
    def cmd_MMS_SELECT_U(self, gcmd):
        return self.cmd_MMS_SELECT(gcmd)

    def cmd_MMS_LOAD_U(self, gcmd):
        return self.cmd_MMS_LOAD(gcmd)

    def cmd_MMS_POP_U(self, gcmd):
        return self.cmd_MMS_POP(gcmd)

    def cmd_MMS_PREPARE_U(self, gcmd):
        return self.cmd_MMS_PREPARE(gcmd)


def load_config(config):
    return MMSDelivery(config)
