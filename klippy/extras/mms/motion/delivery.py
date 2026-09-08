# Support for MMS Delivery
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import time
import traceback
from contextlib import nullcontext
from dataclasses import dataclass, field, fields

import mcu

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    printer_adapter,
    toolhead_adapter
)
from ..core.config import PrinterConfig
from ..core.exceptions import (
    DeliveryFailedError,
    DeliveryPreconditionError,
    DeliveryReadyError,
    DeliveryTerminateSignal,
)
from ..core.logger import log_time_cost
from ..core.slot_pin import PinType
from ..core.task import AsyncTask


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

    # MMS_SLOTS_LOOP times
    slots_loop_times: int = 200

    # The expire timeout for endstop homing
    # Which overwrite global variable "TRSYNC_TIMEOUT" in mcu.py
    custom_trsync_timeout: float = 0.05


class MMSDelivery:
    def __init__(self, config):
        self.reactor = printer_adapter.get_reactor()

        # Delivery config
        pd_config = PrinterDeliveryConfig(config)
        self.pd_config = pd_config.gen_packaged_config()
        self.d_config = DeliveryConfig()

        # Pins
        self.pin_type = PinType()

        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)

    # ---- Initialization ----
    def _handle_klippy_connect(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

        # Overwrite global variable in mcu.py
        try:
            mcu.TRSYNC_TIMEOUT = self.pd_config.custom_trsync_timeout
        except Exception as e:
            self.log_error(f"MCU 'TRSYNC_TIMEOUT' overwrite failed: {e}")

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
            ("MMS_TEST_SELECTOR", self.cmd_MMS_TEST_SELECTOR),
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
    def pause(self, period_seconds):
        self.reactor.pause(self.reactor.monotonic() + period_seconds)

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

        begin_at = time.time()
        # Wait until timeout or idle
        while mms_stepper.is_running():
            # First wait
            self.pause(interval)

            # Calculate elapsed time
            elapsed_time = time.time() - begin_at
            if elapsed_time > timeout:
                # Timeout, return
                self.log_warning(
                    f"{log_prefix} timeout after {elapsed_time:.2f}s")
                return False

        # Idle
        elapsed_time = time.time() - begin_at
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
        self.wait_mms_selector(slot_num, interval, timeout)
        self.wait_mms_drive(slot_num, interval, timeout)
        mms_selector = self.mms.get_mms_slot(slot_num).get_mms_selector()
        mms_drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        return not (mms_selector.is_running() or mms_drive.is_running())

    def wait_toolhead(self, interval=None, timeout=None):
        interval = interval or self.d_config.wait_toolhead_interval
        timeout = timeout or self.d_config.wait_toolhead_timeout

        # Block waiting for toolhead to complete pause movement operations
        begin_at = time.time()
        while toolhead_adapter.is_busy():
            self.pause(interval)
            # Handle timeout scenario if toolhead
            # doesn't complete within allocated time
            if time.time() - begin_at > timeout:
                return False
        return True

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
            mms_selector.manual_move(
                distance = dist,
                speed = self.pd_config.speed_selector,
                accel = self.pd_config.accel_selector,
            )

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
            return mms_selector.manual_home(
                distance = self.pd_config.bowden_distance,
                speed = self.pd_config.speed_selector,
                accel = self.pd_config.accel_selector,
                forward = not reverse,
                trigger = True,
                endstop_pair_lst = mms_slot.format_endstop_pair(pin_type),
            )

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
                if self._wait_for_rfid_reading(slot_num):
                    continue

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

            # Retry
            self.pause(self.d_config.retry_period)
            self.log_info(f"{msg} retry {i+1}/{self.retry_times} ...")

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

    def _wait_for_rfid_reading(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if mms_slot.slot_rfid.is_reading():
            self.log_info(f"slot[{slot_num}] paused for RFID reading")
            begin_at = time.time()
            # Wait one spool rotation tops (approx 8 seconds)
            timeout = 8.0
            while mms_slot.slot_rfid.is_reading():
                self.pause(0.1)
                if time.time() - begin_at > timeout:
                    self.log_warning(f"slot[{slot_num}] RFID wait timeout after {timeout}s")
                    mms_slot.slot_rfid.read_end()
                    break
            return True
        return False

    # -- Deliver --
    def _deliver_distance(self, slot_num, distance, speed=None, accel=None):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if not self._can_deliver():
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        msg = f"slot[{slot_num}] deliver {distance:.2f} mm"

        # Wait until mms_selector/mms_drive idle
        is_idle = self.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            self.log_warning(
                f"{msg} wait selector or drive stepper idle timeout")

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
        mms_drive.update_focus_slot(slot_num)
        
        remaining_distance = distance
        while abs(remaining_distance) > 0.001:
            context = (
                self.mms_fil_detection.monitor(slot_num)
                if remaining_distance>0 else nullcontext()
            )
            with context:
                mms_drive.manual_move(remaining_distance, speed, accel)
            
            moved = mms_drive.get_distance_moved()
            remaining_distance -= moved
            
            if abs(remaining_distance) > 0.001:
                if not self._wait_for_rfid_reading(slot_num):
                    # Terminated by other reason
                    break
                # RFID read finished, loop will retry remaining distance

        self.log_info_s(f"{msg} finish")

    def _drip_deliver_distance(
        self, slot_num, distance,
        speed=None, accel=None
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if not self._can_deliver():
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        msg = f"slot[{slot_num}] drip deliver {distance:.2f} mm"

        # Wait until mms_selector/mms_drive idle
        is_idle = self.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            self.log_warning(
                f"{msg} wait selector or drive stepper idle timeout")

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
        mms_drive.update_focus_slot(slot_num)
        
        remaining_distance = distance
        while abs(remaining_distance) > 0.001:
            # If deliver forward, enable monitoring
            # Else disable with Null context manager
            context = (
                self.mms_fil_detection.monitor(slot_num)
                if remaining_distance>0 else nullcontext()
            )
            with context:
                mms_drive.drip_move(remaining_distance, speed, accel)
            
            moved = mms_drive.get_distance_moved()
            remaining_distance -= moved

            if abs(remaining_distance) > 0.001:
                if not self._wait_for_rfid_reading(slot_num):
                    # Terminated by other reason
                    break
                # RFID read finished, loop will retry remaining distance

        self.log_info_s(f"{msg} finish")

    # -- Deliver to --
    def _drive_deliver_to(
        self, slot_num, pin_type, forward, trigger, distance, speed, accel
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if mms_slot.slot_rfid.is_reading():
            self.log_info(f"slot[{slot_num}] waiting for RFID reading before move")
            while mms_slot.slot_rfid.is_reading():
                self.pause(0.1)

        if not self._can_deliver():
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        mms_drive = mms_slot.get_mms_drive()
        mms_drive.update_focus_slot(slot_num)
        wait = mms_slot.get_wait_func(pin_type)

        with wait():
            # If deliver forward, enable monitoring
            # Else disable with Null context manager
            context = (
                self.mms_fil_detection.monitor(slot_num)
                if forward else nullcontext()
            )
            with context:
                return mms_drive.manual_home(
                    distance=distance, speed=speed, accel=accel,
                    forward=forward, trigger=trigger,
                    endstop_pair_lst=mms_slot.format_endstop_pair(pin_type),
                )

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
        if mms_slot.check_pin(pin_type, trigger):
            self.log_info_s(f"{msg} is already done, skip...")
            return True

        # Wait until mms_selector/mms_drive idle
        is_idle = self.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            self.log_warning(
                f"{msg} wait selector or drive stepper idle timeout")

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
        
        while True:
            self._drive_deliver_to(
                slot_num, pin_type, forward, trigger,
                distance, spd, acc
            )
            distance_moved = mms_drive.get_distance_moved()
            
            if mms_drive.move_is_terminated():
                if self._wait_for_rfid_reading(slot_num):
                    continue
                raise DeliveryTerminateSignal()
            break

        # Check destination pin state
        if mms_slot.check_pin(pin_type, trigger):
            self.log_info_s(
                f"{msg} complete, moved: {distance_moved:.2f} mm")
            return True

        # Gentle homing
        self.log_info_s(f"{msg} gentle homing")
        for i in range(self.retry_times):
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

            # Retry
            self.pause(self.d_config.retry_period)
            self.log_info(f"{msg} gentle retry {i+1}/{self.retry_times} ...")

        # Finally not return, raise exception
        raise DeliveryFailedError(
            f"{msg} gentle failed after full movement", mms_slot)

    def deliver_confidently_org(
        self, slot_num, pin_type, forward, trigger,
        distance, speed, accel
    ):
        success, distance_moved = self.deliver_once(
            slot_num, pin_type, forward, trigger, distance, speed, accel)
        if success:
            return True

        # Homing is not terminated or completed
        gentle_success = self._gentle_homing(
            slot_num, pin_type, forward, trigger, distance_moved)
        if gentle_success:
            return True

        # Finally not return
        msg = self._format_deliver_msg(slot_num, pin_type, forward, trigger)
        mms_slot = self.mms.get_mms_slot(slot_num)
        # Reset distance_moved to 0
        # is_set = mms_slot.set_deliver_distance(pin_type, forward, distance=0)
        # if is_set:
        #     self.log_info_s(f"{msg} reset deliver distance to 0 mm")
        # Raise error
        raise DeliveryFailedError(
            f"{msg} failed after full movement", mms_slot)

    def _gentle_homing(
        self, slot_num, pin_type, forward, trigger, distance_moved
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()

        result = self._drive_deliver_to(
            slot_num, pin_type, forward, trigger,
            self.d_config.gentle_homing_distance,
            self.pd_config.sprint_speed,
            self.pd_config.sprint_accel
        )

        new_dist = mms_drive.get_distance_moved()
        if mms_drive.move_is_completed(result):
            self.log_info_s(
                f"slot[{slot_num}] gentle homing complete, "
                f"gentle moved: {new_dist:.2f} mm"
            )
            # Save distance
            distance_moved += new_dist
            # is_set = mms_slot.set_deliver_distance(
            #     pin_type, forward, distance_moved)
            return True

        # Not completed
        self.log_info_s(
            f"slot[{slot_num}] gentle homing failed after "
            f"{new_dist:.2f} mm movement"
        )
        return False

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
                return True

            # Retry
            self.pause(self.d_config.retry_period)
            self.log_info(f"{msg} retry {i+1}/{self.retry_times} ...")

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
        if mms_slot.check_pin(pin_type, trigger):
            self.log_info_s(f"{msg} is already done, skip...")
            self.mms.log_status()
            return True, 0

        # Wait until mms_selector/mms_drive idle
        if not self.wait_mms_selector_and_drive(slot_num):
            self.log_warning(
                f"{msg} wait selector/drive stepper idle timeout")
            # return False, 0

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
            # If it was terminated by RFID detection, don't raise Exception
            # Instead, return False to allow deliver_with_retry to retry
            if self._wait_for_rfid_reading(slot_num):
                return False, distance_moved

            self.log_info_s(
                f"{msg} is terminated, moved: {distance_moved:.2f} mm")
            raise DeliveryTerminateSignal()

        # Check complated
        if mms_drive.move_is_completed(result):
            self.log_info_s(
                f"{msg} is completed, moved: {distance_moved:.2f} mm")
            # Save distance
            # is_set = mms_slot.set_deliver_distance(
            #     pin_type, forward, distance_moved)
            # if is_set:
            #     self.log_info_s(
            #         f"{msg} distance saved: {distance_moved:.2f} mm")
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

    def unload_to_gate(self, slot_num):
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

    def unload_to_release_gate(self, slot_num, need_check=True):
        if need_check:
            self._check_slot_is_ready(slot_num)
        self._unload_to_release(slot_num, self.pin_type.gate)

    def pop_slot(self, slot_num):
        self._check_slot_is_ready(slot_num)
        self.unload_to_inlet(slot_num)

    def pop_all_slots(self):
        # Pop all slots if not target one
        for slot_num in self.mms.get_slot_nums():
            if self.mms.get_mms_slot(slot_num).is_ready():
                self.pop_slot(slot_num)

    def select_another_slot(self, slot_num):
        for new_slot_num in self.mms.get_slot_nums():
            if new_slot_num != slot_num:
                self.log_info_s(
                    f"slot[{slot_num}] select another slot[{new_slot_num}]")
                self.select_slot(new_slot_num)
                return

    def autoload_to_gate(self, slot_num):
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
        self.select_slot(slot_num, reverse=True)

        distance = mms_slot.get_deliver_distance(p_type, True) \
            or self.pd_config.bowden_distance
        speed = self.pd_config.sprint_speed
        accel = self.pd_config.sprint_accel

        distance_moved_sum = 0
        success = False
        # Force autoload with retry
        for i in range(self.retry_times):
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
            distance_moved_sum += mms_drive.get_distance_moved()

            # Check terminated
            if mms_drive.move_is_terminated():
                if self._wait_for_rfid_reading(slot_num):
                    continue # Retry this autoload step

                raise DeliveryTerminateSignal()
            # Check complated
            if mms_drive.move_is_completed(result):
                success = True
                break

            # Retry
            self.pause(self.d_config.retry_period)
            self.log_info_s(
                f"slot[{slot_num}] autoload failed, "
                f"retry {i+1}/{self.retry_times} ..."
            )

        # Failed after all retry
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
        self._safety_retract(slot_num)
        self.log_info_s(
            f"slot[{slot_num}] deliver safety retract finish")

        # Re-calibrate backward with twice factor
        self._selector_refine_calibration(slot_num, factor=-2)
        if mms_slot.selector.is_triggered():
            mms_selector = mms_slot.get_mms_selector()
            mms_selector.update_focus_slot(slot_num)

    def preload_to_gate(self, slot_num):
        # Pre-load don't need to check Inlet
        mms_slot = self.mms.get_mms_slot(slot_num)
        p_type = self.pin_type.gate

        # Reverse select
        self.select_slot(slot_num, reverse=True)

        # Use default distance to prevent using slot meta
        self._load_to_trigger(
            slot_num, p_type,
            # distance=mms_slot.get_deliver_distance(p_type, True),
            speed=self.pd_config.sprint_speed,
            accel=self.pd_config.sprint_accel
        )
        # Deliver backward without re-select
        self._safety_retract(slot_num)
        self.log_info_s(
            f"slot[{slot_num}] deliver safety retract finish")

        # Re-calibrate backward with twice factor
        self._selector_refine_calibration(slot_num, factor=-2)
        if mms_slot.selector.is_triggered():
            mms_selector = mms_slot.get_mms_selector()
            mms_selector.update_focus_slot(slot_num)

    def unselect(self):
        for slot_num in self.mms.get_selecting_slots():
            self.log_info_s(f"slot[{slot_num}] unselect begin")
            mms_slot = self.mms.get_mms_slot(slot_num)
            mms_selector = mms_slot.get_mms_selector()

            mms_selector.manual_move(
                distance=mms_selector.get_unselect_distance(),
                speed=self.pd_config.sprint_speed,
                accel=self.pd_config.sprint_accel
            )
            if mms_slot.selector.is_released():
                mms_selector.update_focus_slot(None)
            self.log_info_s(f"slot[{slot_num}] unselect finished")

    # ---- MMS Buffer support ----
    def fill_buffer(self, slot_num, distance=None):
        self._check_slot_is_ready(slot_num)

        mms_slot = self.mms.get_mms_slot(slot_num)
        distance = distance or self.pd_config.bowden_distance

        self._load_to_trigger(
            slot_num, self.pin_type.outlet,
            distance = distance,
            speed = self.pd_config.sprint_speed,
            accel = self.pd_config.sprint_accel
        )

    def clear_buffer(self, slot_num, distance=None):
        self._check_slot_is_ready(slot_num)

        mms_slot = self.mms.get_mms_slot(slot_num)
        distance = distance or self.pd_config.bowden_distance

        self._unload_to_trigger(
            slot_num, self.pin_type.buffer_runout,
            distance = distance,
            speed = self.pd_config.sprint_speed,
            accel = self.pd_config.sprint_accel
        )

    def halfway_buffer(self, slot_num, spring_stroke):
        self._check_slot_is_ready(slot_num)

        p_type = self.pin_type.buffer_runout
        distance = self.pd_config.bowden_distance
        spd = self.pd_config.sprint_speed
        acc = self.pd_config.sprint_accel

        mms_slot = self.mms.get_mms_slot(slot_num)
        # First let buffer_runout trigger
        self._unload_to_trigger(slot_num, p_type, distance, spd, acc)
        # Secondary let buffer_runout release
        self._load_to_release(slot_num, p_type, distance, spd, acc )

        # Move forward half of spring stroke
        self.move_forward(slot_num, abs(spring_stroke)*0.5, spd, acc)
        return True

    def measure_buffer(self, slot_num, distance, speed, accel):
        self._check_slot_is_ready(slot_num)

        mms_slot = self.mms.get_mms_slot(slot_num)
        self.load_to_outlet(slot_num)
        self._unload_to_trigger(
            slot_num, self.pin_type.buffer_runout,
            distance, speed, accel
        )

        mms_drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        distance_moved = round(abs(mms_drive.get_distance_moved()), 4)
        return distance_moved

    # ---- Deliver commands ----
    # -- Single stepper manual move --
    def mms_selector_move(self, slot_num, distance, speed, accel):
        mms_selector = self.mms.get_mms_slot(slot_num).get_mms_selector()
        mms_selector.update_focus_slot(slot_num)
        mms_selector.manual_move(distance, speed, accel)
        self.log_info_s(
            f"slot[{slot_num}] {mms_selector.get_mms_name()} "
            f"move {distance:.2f} mm"
        )

    def mms_drive_move(self, slot_num, distance, speed, accel, log=True):
        mms_drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        mms_drive.update_focus_slot(slot_num)
        mms_drive.manual_move(distance, speed, accel)
        if log:
            self.log_info_s(
                f"slot[{slot_num}] {mms_drive.get_mms_name()} "
                f"move {distance:.2f} mm"
            )

    def mms_drive_sprint(self, slot_num, distance):
        mms_drive = self.mms.get_mms_slot(slot_num).get_mms_drive()
        mms_drive.update_focus_slot(slot_num)
        mms_drive.manual_move(
            distance,
            self.pd_config.sprint_speed,
            self.pd_config.sprint_accel
        )
        self.log_info_s(
            f"slot[{slot_num}] {mms_drive.get_mms_name()} "
            f"sprint {distance:.2f} mm"
        )

    def deliver_async_task(self, func, params=None):
        if self.async_task_sp.is_running():
            self.log_warning(
                "another deliver async_task is running, return...")
            return

        try:
            if self.async_task_sp.setup(func, params or {}):
                self.async_task_sp.start()
        except Exception as e:
            self.log_error(f"deliver async task error: {e}")

    @log_time_cost("log_info_s")
    def mms_load(self, slot_num):
        self.log_info_s(f"slot[{slot_num}] load begin")
        try:
            # Skip wanted slot
            self.unload_loading_slots(skip_slot=slot_num)

            # Load wanted slot
            mms_slot = self.mms.get_mms_slot(slot_num)
            if mms_slot.entry_is_set():
                self.load_to_entry(slot_num)
            else:
                self.load_to_outlet(slot_num)

        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{slot_num}] load terminated")
            return False
        except Exception as e:
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
    def mms_pop(self, slot_num=None):
        msg_slot = slot_num if slot_num is not None else "*"
        self.log_info_s(f"slot[{msg_slot}] pop begin")
        try:
            if slot_num is not None:
                self.pop_slot(slot_num)
            else:
                self.pop_all_slots()
        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{slot_num}] pop terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{msg_slot}] pop error: {e}")
            return False
        self.log_info_s(f"slot[{msg_slot}] pop finish")
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

    @log_time_cost("log_info_s")
    def mms_select_others(self, slot_num):
        try:
            self.select_another_slot(slot_num)
        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{slot_num}] unselect terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] unselect error: {e}")
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
            (self.mms.printer_is_printing, "printing"),
            (self.mms.printer_is_paused, "paused"),
            (self.mms.printer_is_resuming, "resuming"),
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

        file_need_truncated = True

        self.log_info("mms bowden calibration begin")
        for slot_num in self.mms.get_slot_nums():
            try:
                self._check_slot_is_ready(slot_num)

                mms_slot = self.mms.get_mms_slot(slot_num)
                mms_drive = mms_slot.get_mms_drive()

                # Truncate file at lease once
                if file_need_truncated:
                    mms_slot.meta.truncate_file()
                    file_need_truncated = False

                # Prepare before
                self.unload_loading_slots()
                self.unload_to_gate(slot_num)
                self.load_to_gate(slot_num)

                # Start
                if mms_slot.entry_is_set():
                    first_p = self.pin_type.entry
                else:
                    first_p = self.pin_type.outlet

                with mms_slot.update_deliver_distance():
                    mms_slot.truncate_deliver_distance()

                    # Pin: Entry/Outlet Forward
                    # Not necessary to add safety_retract_distance,
                    # cause unload_to_gate() in prepare phase
                    # has actually played
                    self._load_to_trigger(
                        slot_num, first_p,
                        speed=spd, accel=acc
                    )

                    # Pin: Gate Backward
                    self._unload_to_release(
                        slot_num, self.pin_type.gate,
                        speed=spd, accel=acc
                    )

                    self._safety_retract(slot_num)

                    # # Pin: Inlet Backward
                    # self._unload_to_release(
                    #     slot_num, self.pin_type.inlet,
                    #     speed=spd, accel=acc
                    # )
                    # # Pin: Inlet Forward
                    # mms_slot.set_deliver_distance(
                    #     self.pin_type.gate,
                    #     forward=True,
                    #     distance=abs(mms_drive.get_distance_moved())
                    # )

                    # Finally write to file
                    mms_slot.meta.write_file()

            except DeliveryReadyError:
                # Keep on next SLOT
                self.log_info(
                    f"slot[{slot_num}] is not ready, calibration skip...")
            except DeliveryTerminateSignal:
                self.log_info("mms bowden calibration terminated")
                return False
            except Exception as e:
                self.log_error(f"mms bowden calibration error: {e}")
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

    def mms_slots_check(self):
        self.log_info("slots check begin")
        # Walk through all SLOTs and check every Pin
        for slot_num in self.mms.get_slot_nums():
            if not self._can_walk():
                return False

            mms_slot = self.mms.get_mms_slot(slot_num)
            tag_uid = None

            try:
                # Unload release all pins except Inlet
                self.unload_loading_slots()
                self.pause(1)
                self.log_info(
                    "unload: " + mms_slot.format_pins_status())
                # Verify
                self.verify_pins(mms_slot, False)

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
                    with mms_slot.slot_rfid.detect_only():
                        # Trigger all Outlet and Entry
                        self.load_to_outlet(slot_num)
                        if mms_slot.entry_is_set() \
                            and not mms_slot.entry_is_triggered():
                            self.load_to_entry(slot_num)
                        self.log_info(
                            "load: " + mms_slot.format_pins_status())
                        # Verify
                        self.verify_pins(mms_slot, True)

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
                    self.unload_to_gate(slot_num)

                # Raise Exception if RFID is still detecting
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

        # Finally unload
        if self._can_walk():
            try:
                self.unload_loading_slots()
                self.log_info(
                    "Finally unload: " + mms_slot.format_pins_status())
                self.verify_pins(mms_slot, False)
            except DeliveryTerminateSignal:
                self.log_info("slots check terminated")
                return False
            except DeliveryReadyError:
                pass
            except Exception as e:
                self.log_error(f"slots check error:{e}")
                return False

        self.log_info("slots check finish")
        return True

    def mms_slots_check_loop(self):
        self.log_info("slots loop begin")
        total = self.pd_config.slots_loop_times
        for i in range(total):
            msg = f"############### check loop: {i+1}/{total} ###############"
            self.log_info(msg)
            success = self.mms_slots_check()
            if not success or not self._can_walk():
                break
        self.log_info("slots check loop finish")
        self.log_info("#" * 60)

    def mms_slots_walk(self):
        self.log_info("slots walk begin")
        # Walk through all SLOTs
        for slot_num in self.mms.get_slot_nums():
            if not self._can_walk():
                return False

            try:
                self.unload_loading_slots(skip_slot=slot_num)
                self.pause(1)

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

        # Finally unload
        if self._can_walk():
            try:
                self.unload_loading_slots()
            except DeliveryTerminateSignal:
                self.log_info("slots walk terminated")
                return False
            except DeliveryReadyError:
                pass
            except Exception as e:
                self.log_error(f"slots walk error:{e}")
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
                break
        self.log_info("slots loop finish")

    @log_time_cost("log_info_s")
    def mms_stop(self, slot_num=None):

        def _stop(mms_slot):
            # Terminate ManualHome
            slot_pin = mms_slot.get_waiting_pin()
            if slot_pin:
                mms_slot.stop_homing(slot_pin)

            # Attempt to deactivate mms_buffer
            mms_buffer = mms_slot.get_mms_buffer()
            if mms_buffer.is_activating():
                mms_buffer.deactivate_monitor()

            # Terminate and wait
            slot_num = mms_slot.get_num()
            mms_drive = mms_slot.get_mms_drive()
            if mms_drive.is_running():
                mms_drive.terminate_moving()
                self.wait_mms_drive(slot_num)

            mms_selector = mms_slot.get_mms_selector()
            if mms_selector.is_running():
                mms_selector.terminate_moving()
                self.wait_mms_selector(slot_num)

        msg_slot = slot_num if slot_num is not None else "*"
        self.log_info_s(f"slot[{msg_slot}] stop begin")

        try:
            if slot_num is not None:
                _stop(self.mms.get_mms_slot(slot_num))
            else:
                for mms_slot in self.mms.get_mms_slots():
                    _stop(mms_slot)

            # if self.async_task_sp.is_running():
            #     self.async_task_sp.stop()
        except Exception as e:
            self.log_error(f"slot[{msg_slot}] stop error: {e}")
            self.log_error(traceback.format_exc())
            return False

        self.log_info_s(f"slot[{msg_slot}] stop finish")
        return True

    # ---- GCode commands ----
    def cmd_MMS_LOAD(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_load(slot_num)
        else:
            self.deliver_async_task(
                self.mms_load,
                {"slot_num":slot_num}
            )

    def cmd_MMS_UNLOAD(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=None, minval=0)
        if not self.mms.slot_is_available(slot_num, can_none=True):
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_unload(slot_num)
        else:
            self.deliver_async_task(
                self.mms_unload,
                {"slot_num":slot_num}
            )

    def cmd_MMS_POP(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=None, minval=0)
        if not self.mms.slot_is_available(slot_num, can_none=True):
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_pop(slot_num)
        else:
            self.deliver_async_task(
                self.mms_pop,
                {"slot_num":slot_num}
            )

    def cmd_MMS_PREPARE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_prepare(slot_num)
        else:
            self.deliver_async_task(
                self.mms_prepare, {"slot_num":slot_num})

    def cmd_MMS_MOVE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

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
            minval=0.0,
            maxval=valid_speed
        )
        accel = gcmd.get_float(
            "ACCEL",
            default=valid_accel,
            minval=0.0,
            maxval=valid_accel
        )

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_move(slot_num, distance, speed, accel)
        else:
            self.deliver_async_task(
                self.mms_move,
                {
                    "slot_num" : slot_num,
                    "distance" : distance,
                    "speed" : speed,
                    "accel" : accel,
                }
            )

    def cmd_MMS_DRIP_MOVE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        valid_distance = abs(self.pd_config.bowden_distance)
        distance = gcmd.get_float(
            "DISTANCE",
            default=0.0, minval=-valid_distance, maxval=valid_distance
        )
        speed = gcmd.get_float("SPEED", default=None, minval=0.0)
        accel = gcmd.get_float("ACCEL", default=None, minval=0.0)

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_drip_move(slot_num, distance, speed, accel)
        else:
            self.deliver_async_task(
                self.mms_drip_move,
                {
                    "slot_num" : slot_num,
                    "distance" : distance,
                    "speed" : speed,
                    "accel" : accel,
                }
            )

    def cmd_MMS_SELECT(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_select(slot_num)
        else:
            self.deliver_async_task(
                self.mms_select,
                {"slot_num":slot_num}
            )

    def cmd_MMS_UNSELECT(self, gcmd):
        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_unselect()
        else:
            self.deliver_async_task(self.mms_unselect)

    def cmd_MMS_BOWDEN_CALIBRATION(self, gcmd=None):
        if not self.mms.cmd_can_exec():
            self.log_warning("MMS_BOWDEN_CALIBRATION can not execute now")
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_bowden_calibration()
        else:
            self.deliver_async_task(self.mms_bowden_calibration)

    def cmd_MMS_SLOTS_CHECK(self, gcmd=None):
        if not self.mms.cmd_can_exec():
            self.log_warning("MMS_SLOTS_CHECK can not execute now")
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_slots_check()
        else:
            self.deliver_async_task(self.mms_slots_check)

    def cmd_MMS_SLOTS_CHECK_LOOP(self, gcmd=None):
        if not self.mms.cmd_can_exec():
            self.log_warning("cmd_MMS_SLOTS_CHECK_LOOP can not execute now")
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_slots_check_loop()
        else:
            self.deliver_async_task(self.mms_slots_check_loop)

    def cmd_MMS_SLOTS_WALK(self, gcmd=None):
        if not self.mms.cmd_can_exec():
            self.log_warning("MMS_SLOTS_WALK can not execute now")
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_slots_walk()
        else:
            self.deliver_async_task(self.mms_slots_walk)

    def cmd_MMS_SLOTS_LOOP(self, gcmd=None):
        if not self.mms.cmd_can_exec():
            self.log_warning("MMS_SLOTS_LOOP can not execute now")
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_slots_loop()
        else:
            self.deliver_async_task(self.mms_slots_loop)

    def cmd_MMS_STOP(self, gcmd=None):
        if not self.mms.cmd_can_exec():
            self.log_warning("MMS_STOP can not execute now")
            return
        if printer_adapter.get_mms_swap().is_running():
            self.log_warning("MMS_STOP can not execute while swapping")
            return

        slot_num = gcmd.get_int("SLOT", default=None, minval=0)
        if not self.mms.slot_is_available(slot_num, can_none=True):
            return
        self.mms_stop(slot_num)

    def cmd_MMS_TEST_SELECTOR(self, gcmd):
        slot_num = 0
        dist = 1
        speed = accel = 10
        times = 400
        mms_selector = self.mms.get_mms_slot(slot_num).get_mms_selector()

        for i in range(times):
            mms_selector.manual_move(dist, speed, accel)
            mms_selector.log_status()

    def cmd_MMS_TEST_SELECTOR_MEASURE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        self._selector_deliver_to(slot_num)

        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_selector = mms_slot.get_mms_selector()

        pin_type = self.pin_type.selector
        wait = mms_slot.get_wait_func(pin_type)
        with wait():
            mms_selector.manual_home(
                distance = 100,
                speed = 2,
                accel = 2,
                forward = True,
                trigger = False,
                endstop_pair_lst = mms_slot.format_endstop_pair(pin_type),
            )

        result = mms_selector.get_distance_moved()
        self.log_info(
            f"slot[{slot_num}] measured selector trigger "
            f"interval is {result:.2f} mm"
        )

    def cmd_MMS_SLOT_EXTRUDER_SYNC(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        mms_drive.sync_to_extruder()

    def cmd_MMS_SLOT_EXTRUDER_UNSYNC(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        mms_drive.unsync_to_extruder()

    def cmd_MMS_D_TEST(self, gcmd):
        return

    # For KlipperScreen
    def cmd_MMS_SELECT_U(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return
        if not self.mms.cmd_can_exec():
            self.log_warning(
                f"slot[{slot_num}] MMS_SELECT_U can not execute now")
            return
        self.deliver_async_task(self.mms_select, {"slot_num":slot_num})

    def cmd_MMS_LOAD_U(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return
        if not self.mms.cmd_can_exec():
            self.log_warning(
                f"slot[{slot_num}] MMS_LOAD_U can not execute now")
            return
        self.deliver_async_task(self.mms_load, {"slot_num":slot_num})

    def cmd_MMS_POP_U(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=None, minval=0)
        if not self.mms.slot_is_available(slot_num, can_none=True):
            return
        if not self.mms.cmd_can_exec():
            self.log_warning(
                f"slot[{slot_num}] MMS_POP_U can not execute now")
            return
        self.deliver_async_task(self.mms_pop, {"slot_num":slot_num})

    def cmd_MMS_PREPARE_U(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return
        if not self.mms.cmd_can_exec():
            self.log_warning(
                f"slot[{slot_num}] MMS_PREPARE_U can not execute now")
            return
        self.deliver_async_task(self.mms_prepare, {"slot_num":slot_num})


def load_config(config):
    return MMSDelivery(config)
