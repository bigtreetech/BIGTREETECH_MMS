# Support for MMS Delivery
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import time
from contextlib import nullcontext
from dataclasses import dataclass, field, fields

from ..adapters import (
    gcode_adapter,
    printer_adapter,
    toolhead_adapter
)
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
    # Must be first line,
    # printer_config is the param of DeliveryConfig object
    printer_config: object

    # Retry period of delivery, in seconds
    retry_period: float = 0.5
    # Selector refine calibration
    refine_calibration_distance: float = 3.7 # mm

    wait_toolhead_interval: float = 0.5 # seconds
    wait_toolhead_timeout: float = 60 # seconds

    wait_mms_stepper_interval: float = 0.2 # seconds
    wait_mms_stepper_timeout: float = 5 # seconds

    # Skip configs use in __post_init__()
    skip_configs = [
        "printer_config",

        "retry_period",
        "refine_calibration_distance",

        "wait_toolhead_interval",
        "wait_toolhead_timeout",

        "wait_mms_stepper_interval",
        "wait_mms_stepper_timeout",
    ]
    # ==== configuration values in *.cfg, must set default  ====
    speed_selector: float = 100
    accel_selector: float = 100
    speed_drive: float = 60
    accel_drive: float = 10
    # The distance stepper move before endstop is triggered, in mm
    stepper_move_distance: float = 1000

    # The distance stepper retrace after unload to gate, in mm
    safety_retract_distance: float = 50

    # MMS_SLOTS_LOOP times
    slots_loop_times: int = 200

    def __post_init__(self):
        type_method_map = {
            int: "getint",
            float: "getfloat",
            str: "get",
            list: "getintlist"
        }

        for field_info in fields(self):
            field_name = field_info.name
            field_type = field_info.type

            if field_name in self.skip_configs:
                continue

            # Default type is int
            get_method = type_method_map.get(field_type, "getint")
            config_value = getattr(self.printer_config, get_method)(field_name)
            object.__setattr__(self, field_name, config_value)


class MMSDelivery:
    def __init__(self, config):
        self.reactor = printer_adapter.get_reactor()

        # Delivery config
        self.d_config = DeliveryConfig(config)
        self.pin_type = PinType()

        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)

    # ---- Initialization ----
    def _handle_klippy_connect(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_pause = self.mms.get_mms_pause()
        self.mms_filament_fracture = self.mms.get_mms_filament_fracture()
        # Configuration parameters
        self.retry_times = self.mms.get_retry_times()
        # Singleton async task
        self.async_task_sp = AsyncTask()

    def _initialize_gcode(self):
        commands = [
            # Core operations
            ("MMS_LOAD", self.cmd_MMS_LOAD),
            ("MMS_UNLOAD", self.cmd_MMS_UNLOAD),
            ("MMS_POP", self.cmd_MMS_POP),
            ("MMS_PREPARE", self.cmd_MMS_PREPARE),
            ("MMS_MOVE", self.cmd_MMS_MOVE),
            ("MMS_DRIP_MOVE", self.cmd_MMS_DRIP_MOVE),
            # Selection controls
            ("MMS_SELECT", self.cmd_MMS_SELECT),
            ("MMS_UNSELECT", self.cmd_MMS_UNSELECT),

            # Stop commands
            ("MMS_STOP", self.cmd_MMS_STOP),
            # Diagnostic commands
            ("MMS_SLOTS_CHECK", self.cmd_MMS_SLOTS_CHECK),
            ("MMS_SLOTS_LOOP", self.cmd_MMS_SLOTS_LOOP),
            # Command aliases
            ("MMS999", self.cmd_MMS_STOP),
            ("MMS9", self.cmd_MMS_SLOTS_CHECK),
            ("MMS8", self.cmd_MMS_SLOTS_LOOP),

            # For KlipperScreen
            ("MMS_SELECT_U", self.cmd_MMS_SELECT_U),
            ("MMS_LOAD_U", self.cmd_MMS_LOAD_U),
            ("MMS_POP_U", self.cmd_MMS_POP_U),
            ("MMS_PREPARE_U", self.cmd_MMS_PREPARE_U),
            # Test
            ("MMS_D_TEST", self.cmd_MMS_D_TEST),
            ("MMS996", self.cmd_MMS_D_TEST),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        # All loggers in MMS Delivery will print to console
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_s = mms_logger.create_log_info(console_output=False)

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
        interval = interval or self.d_config.wait_mms_stepper_interval
        timeout = timeout or self.d_config.wait_mms_stepper_timeout

        stepper_name = mms_stepper.get_name()
        log_desc = f"slot[{slot_num}] waiting for {stepper_name} idle"

        begin_at = time.time()
        has_logged = False

        while mms_stepper.is_running():
            if not has_logged:
                self.log_info_s(f"{log_desc}...")
                has_logged = True

            self.pause(interval)

            elapsed_time = time.time()-begin_at
            if elapsed_time > timeout:
                # Timeout
                self.log_warning(
                    f"{log_desc} timed out after {elapsed_time:.2f} seconds")
                return False

        if has_logged:
            total_time = time.time()-begin_at
            self.log_info_s(
                f"{log_desc} completed in {total_time:.2f} seconds")

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

    def _selector_refine_calibration(self, mms_selector):
        if mms_selector.can_calibrate():
            dist = self.d_config.refine_calibration_distance
            self.log_info_s(f"selector refine calibration: {dist}")
            mms_selector.manual_move(
                distance = dist,
                speed = self.d_config.speed_selector,
                accel = self.d_config.accel_selector
            )

    def _selector_deliver_to(self, slot_num):
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
                distance = self.d_config.stepper_move_distance,
                speed = self.d_config.speed_selector,
                accel = self.d_config.accel_selector,
                forward = True,
                trigger = True,
                endstop_pair_lst = mms_slot.format_endstop_pair(pin_type),
            )

    def select_slot(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_selector = mms_slot.get_mms_selector()

        # Already selecting
        if mms_slot.selector_is_triggered():
            mms_selector.enable()
            mms_selector.update_focus_slot(slot_num)
            self.log_info_s(f"slot[{slot_num}] is already selected, skip...")
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
            result = self._selector_deliver_to(slot_num)

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

            # Retry
            self.pause(self.d_config.retry_period)
            self.log_info(f"{msg} failed, retry {i+1}/{self.retry_times} ...")

        # Try overtravel detect and recover after selector is triggered
        if is_completed:
            self._selector_refine_calibration(mms_selector)
        # Deactivate LED effect before exception raise
        self._led_effect_deactivate(slot_num_lst)

        if not is_completed:
            raise DeliveryFailedError(
                f"{msg} failed after full movement", mms_slot)

    # -- Deliver --
    def _deliver_distance(self, slot_num, distance, speed=None, accel=None):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if not self._can_deliver():
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        msg = f"slot[{slot_num}] deliver distance={distance:.2f} mm"

        # Wait until mms_selector/mms_drive idle
        is_idle = self.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            self.log_warning(
                f"{msg} wait selector or drive stepper idle timeout")

        speed = speed if speed is not None else self.d_config.speed_drive
        accel = accel if accel is not None else self.d_config.accel_drive

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
        mms_drive.manual_move(distance, speed, accel)

        self.log_info_s(f"{msg} finish")

    def _drip_deliver_distance(
        self, slot_num, distance,
        speed=None, accel=None
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if not self._can_deliver():
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        msg = f"slot[{slot_num}] drip deliver distance={distance:.2f} mm"

        # Wait until mms_selector/mms_drive idle
        is_idle = self.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            self.log_warning(
                f"{msg} wait selector or drive stepper idle timeout")

        speed = speed if speed is not None else self.d_config.speed_drive
        accel = accel if accel is not None else self.d_config.accel_drive

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
        # If deliver forward, enable filament fracture monitoring
        # Else disable with Null context manager
        context = (
            self.mms_filament_fracture.monitor_while_homing(slot_num)
            if distance>0 else nullcontext()
        )
        with context:
            mms_drive.drip_move(distance, speed, accel)

        self.log_info_s(f"{msg} finish")

    # -- Deliver to --
    def _drive_deliver_to(
        self, slot_num, pin_type, forward, trigger,
        distance=None, speed=None, accel=None
    ):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if not self._can_deliver():
            raise DeliveryPreconditionError(
                f"slot[{slot_num}] can not deliver", mms_slot)

        # Take care of 0
        dist = self.d_config.stepper_move_distance \
            if distance is None else distance
        spd = min(max(speed, 0), self.d_config.speed_drive) \
            if speed is not None else self.d_config.speed_drive
        acc = min(max(accel, 0), self.d_config.accel_drive) \
            if accel is not None else self.d_config.accel_drive

        mms_drive = mms_slot.get_mms_drive()
        mms_drive.update_focus_slot(slot_num)
        wait = mms_slot.get_wait_func(pin_type)
        endstop_pair = mms_slot.format_endstop_pair(pin_type)

        with wait():
            # If deliver forward, enable filament fracture monitoring
            # Else disable with Null context manager
            context = (
                self.mms_filament_fracture.monitor_while_homing(slot_num)
                if forward else nullcontext()
            )
            with context:
                return mms_drive.manual_home(
                    distance=dist, speed=spd, accel=acc,
                    forward=forward, trigger=trigger,
                    endstop_pair_lst=endstop_pair,
                )

    def _deliver_to(
        self, slot_num, pin_type, forward, trigger,
        distance=None, speed=None, accel=None
    ):
        direction = "forward" if forward else "backward"
        action = "trigger" if trigger else "release"
        msg = (f"slot[{slot_num}] deliver {direction}"
               f" until '{pin_type}' {action}")

        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        distance_moved = 0

        for i in range(self.retry_times):
            # Wait until mms_selector/mms_drive idle
            is_idle = self.wait_mms_selector_and_drive(slot_num)
            if not is_idle:
                self.log_warning(
                    f"{msg} wait selector or drive stepper idle timeout")
            # Apply select
            self.select_slot(slot_num)

            # Check destination
            if mms_slot.check_pin(pin_type, trigger):
                self.log_info_s(
                    f"{msg} is already done, skip..."
                    f"total moved: {distance_moved:.2f} mm"
                )
                self.mms.log_status()
                return False

            self.log_info_s(msg)
            result = self._drive_deliver_to(
                slot_num, pin_type, forward, trigger, distance, speed, accel)
            distance_moved += mms_drive.get_distance_moved()

            if mms_drive.move_is_terminated():
                self.log_info_s(
                    f"{msg} is terminated, "
                    f"total moved: {distance_moved:.2f} mm"
                )
                # Exit retry loop
                raise DeliveryTerminateSignal()

            if mms_drive.move_is_completed(result):
                self.log_info_s(
                    f"{msg} is completed, "
                    f"total moved: {distance_moved:.2f} mm"
                )
                # Exit retry loop
                return True

            # Retry
            self.pause(self.d_config.retry_period)
            self.log_info(f"{msg} failed, retry {i+1}/{self.retry_times} ...")

        # Finally not return, raise exception
        raise DeliveryFailedError(
            f"{msg} failed after full movement", mms_slot)

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
            slot_num, pin_type, forward=True, trigger=False,
            distance=distance, speed=speed, accel=accel
        )

    def _load_to_trigger(
        self, slot_num, pin_type,
        distance=None, speed=None, accel=None
    ):
        self.log_info_s(f"slot[{slot_num}] load to trigger: '{pin_type}'")
        return self._deliver_to(
            slot_num, pin_type, forward=True, trigger=True,
            distance=distance, speed=speed, accel=accel
        )

    def _unload_to_release(
        self, slot_num, pin_type,
        distance=None, speed=None, accel=None
    ):
        self.log_info_s(f"slot[{slot_num}] unload to release: '{pin_type}'")
        return self._deliver_to(
            slot_num, pin_type, forward=False, trigger=False,
            distance=distance, speed=speed, accel=accel
        )

    def _unload_to_trigger(
        self, slot_num, pin_type,
        distance=None, speed=None, accel=None
    ):
        self.log_info_s(f"slot[{slot_num}] unload to trigger: '{pin_type}'")
        return self._deliver_to(
            slot_num, pin_type, forward=False, trigger=True,
            distance=distance, speed=speed, accel=accel
        )

    def _check_slot_is_ready(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        if mms_slot.is_ready():
            return
        msg = f"slot[{slot_num}] is not ready, please check Inlet"
        self.log_warning(msg)
        raise DeliveryReadyError(msg, mms_slot)

    def load_to_gate(self, slot_num):
        self._check_slot_is_ready(slot_num)
        self._load_to_trigger(slot_num, self.pin_type.gate)

    def load_to_outlet(self, slot_num, distance=None, speed=None, accel=None):
        self._check_slot_is_ready(slot_num)
        self._load_to_trigger(
            slot_num, self.pin_type.outlet, distance, speed, accel
        )

    def load_to_entry(self, slot_num):
        self._check_slot_is_ready(slot_num)
        self._load_to_trigger(slot_num, self.pin_type.entry)

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

    def unload_until_buffer_runout_trigger(
        self, slot_num, distance=None, speed=None, accel=None
    ):
        self._check_slot_is_ready(slot_num)
        self._unload_to_trigger(
            slot_num, self.pin_type.buffer_runout, distance, speed, accel
        )

    def unload_to_gate(self, slot_num):
        self._check_slot_is_ready(slot_num)
        res = self._unload_to_release(slot_num, self.pin_type.gate)

        # Only unload safety distance
        # after unload homing move is not skipped
        if res:
            self.move_backward(
                slot_num, self.d_config.safety_retract_distance)

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

    def pre_load_to_gate(self, slot_num):
        # Pre-load don't need to check Inlet
        self._load_to_trigger(slot_num, self.pin_type.gate)

    def unload_to_release_gate(self, slot_num, need_check=True):
        if need_check:
            self._check_slot_is_ready(slot_num)
        self._unload_to_release(slot_num, self.pin_type.gate)

    # ---- Deliver commands ----
    def deliver_async_task(self, func, params=None):
        if self.async_task_sp.is_running():
            self.log_warning(
                "Another deliver async_task is running, return...")
            return

        func = func
        params = params or {}
        try:
            is_ready = self.async_task_sp.setup(func, params)
            if is_ready:
                self.async_task_sp.start()
        except Exception as e:
            self.log_error(f"deliver async task error:{e}")

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
        if abs(distance) > self.d_config.stepper_move_distance:
            self.log_warning(
                f"slot[{slot_num}] can not move {distance}mm, "
                "check config'stepper_move_distance'")
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
        if abs(distance) > self.d_config.stepper_move_distance:
            self.log_warning(
                f"slot[{slot_num}] can not drip move {distance}mm, "
                "check config'stepper_move_distance'")
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
    def mms_unselect(self, slot_num):
        try:
            self.select_another_slot(slot_num)
        except DeliveryTerminateSignal:
            self.log_info_s(f"slot[{slot_num}] unselect terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] unselect error: {e}")
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

    def verify_pins(self, mms_slot, loaded):
        trigger = loaded
        if not mms_slot.inlet.is_triggered():
            raise Exception("Intlet")
        if (mms_slot.gate.is_triggered() != trigger):
            raise Exception("Gate")
        if (mms_slot.buffer_runout.is_triggered() == trigger):
            raise Exception("Buffer_runout: PA4")
        if (mms_slot.outlet.is_triggered() != trigger):
            raise Exception("Outlet: PA5")
        if mms_slot.entry_is_set() \
            and (mms_slot.entry_is_triggered() != trigger):
            raise Exception("Entry")

    def mms_slots_check(self):
        self.log_info("slots check begin")
        # Walk through all SLOTs and check every Pin
        for slot_num in self.mms.get_slot_nums():
            if not self._can_walk():
                return False

            try:
                mms_slot = self.mms.get_mms_slot(slot_num)

                self.unload_loading_slots()
                self.pause(1)
                self.log_info("unload: " + mms_slot.format_pins_status())
                self.verify_pins(mms_slot, False)

                self.load_to_outlet(slot_num)
                if mms_slot.entry_is_set() \
                    and not mms_slot.entry_is_triggered():
                    self.load_to_entry(slot_num)
                self.log_info("load: " + mms_slot.format_pins_status())
                self.verify_pins(mms_slot, True)

            except DeliveryTerminateSignal:
                self.log_info("slots check terminated")
                return False
            except DeliveryReadyError:
                pass
            except Exception as e:
                self.log_error(f"slots check error:{e}")
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

    def mms_slots_loop(self):
        self.log_info("slots loop begin")
        total = self.d_config.slots_loop_times
        for i in range(total):
            msg = f"############### loop: {i+1}/{total} ###############"
            self.log_info(msg)
            success = self.mms_slots_check()
            if not success or not self._can_walk():
                break
        self.log_info("slots loop finish")
        self.log_info("#" * 60)

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

            # Terminate DripMove
            mms_drive = mms_slot.get_mms_drive()
            if mms_drive.is_running():
                mms_drive.terminate_drip_move()
            mms_selector = mms_slot.get_mms_selector()
            if mms_selector.is_running():
                mms_selector.terminate_drip_move()

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
                self.mms_prepare,
                {"slot_num":slot_num}
            )

    def cmd_MMS_MOVE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        valid_distance = abs(self.d_config.stepper_move_distance)
        distance = gcmd.get_float(
            "DISTANCE",
            default=0.0, minval=-valid_distance, maxval=valid_distance
        )
        speed = gcmd.get_float("SPEED", default=None, minval=0.0)
        accel = gcmd.get_float("ACCEL", default=None, minval=0.0)

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

        valid_distance = abs(self.d_config.stepper_move_distance)
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
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_unselect(slot_num)
        else:
            self.deliver_async_task(
                self.mms_unselect,
                {"slot_num":slot_num}
            )

    def cmd_MMS_SLOTS_CHECK(self, gcmd=None):
        if not self.mms.cmd_can_exec():
            self.log_warning("MMS_SLOTS_CHECK can not execute now")
            return

        should_wait = gcmd.get_int("WAIT", default=0)
        if bool(should_wait):
            self.mms_slots_check()
        else:
            self.deliver_async_task(self.mms_slots_check)

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

    def cmd_MMS_D_TEST(self, gcmd):
        loop_times = 200

        fracture_enabled = self.mms_filament_fracture.is_enabled()
        self.mms_filament_fracture.activate()

        for i in range(loop_times):
            for mms_slot in self.mms.get_mms_slots():
                slot_num = mms_slot.get_num()

                if mms_slot.inlet.is_released():
                    continue

                try:
                    self.load_to_outlet(slot_num)
                except:
                    pass
                self.pause(3)
                self.wait_mms_selector_and_drive(slot_num, timeout=60)
                mms_slot.slot_led.deactivate_blinking()
                if mms_slot.outlet.is_triggered():
                    self.mms_prepare(slot_num)

        if not fracture_enabled:
            self.mms_filament_fracture.deactivate()
        
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
