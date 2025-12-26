# Support for MMS Dripload
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import time

from collections import deque
from dataclasses import dataclass
from statistics import median

from ..adapters import gcode_adapter, printer_adapter
from ..core.task import PeriodicTask


@dataclass(frozen=True)
class DriploadConfig:
    # Must be first line, printer_config is the param of DriploadConfig object
    printer_config: object
    show_debug_log: bool = False

    monitor_period: float = 0.1

    filament_diameter: float = 1.75 # diameter of filament, in mm

    # ==== configuration values in *.cfg, must set default  ====
    speed: float = 60
    accel: float = 60
    # Long distance may cause "Timer too close" error
    drip_distance: float = 10

    def __post_init__(self):
        # Use a dictionary to store config keys and attributes
        config_map = {
            "speed": "speed",
            "accel": "accel",
            "drip_distance": "drip_distance"
        }

        # Loop through the config map to assign values
        for config_key, field_name in config_map.items():
            if isinstance(self.__getattribute__(config_key), float):
                value = self.printer_config.getfloat(config_key)
            else:
                value = self.printer_config.getint(config_key)
            # Apply max for drip_distance to ensure it is at least 1
            if field_name == "drip_distance":
                value = max(abs(value), 1)
            object.__setattr__(self, field_name, value)


class MMSDripload:
    def __init__(self, config):
        self.dl_config = DriploadConfig(config)

        self._drip_slot_num = None
        self._in_progress = False

        self.call_count = 0
        self.break_count = 0
        self.break_accumulate = 0

        self.dist_calibrator = DistanceCalibrator()
        self.dist_calibrator.add_measurement(
            self.dl_config.drip_distance)

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()

        self.periodic_task = PeriodicTask()
        self.periodic_task.set_period(period=self.dl_config.monitor_period)

        print_observer = self.mms.get_print_observer()
        print_observer.register_pause_callback(self._print_paused)
        print_observer.register_resume_callback(self._print_resumed)

    def _initialize_gcode(self):
        commands = [
            ("MMS_DRIPLOAD", self.cmd_MMS_DRIPLOAD),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error()
        self.log_info_silent = mms_logger.create_log_info(console_output=False)

    # -- Print observer callbacks --
    def _print_paused(self):
        return
        self.log_info("dripload recv print is paused, deactivate")
        self.deactivate()

    def _print_resumed(self):
        return
        self.log_info("dripload recv print is resumed, activate")
        self.activate()

    def _print_stopped(self):
        return
        self.log_info("dripload recv print is stopped, deactivate")
        self.deactivate()

    # -- Status --
    def is_in_progress(self):
        return self._in_progress

    def mms_autoload_in_progress(self):
        return printer_adapter.get_mms_autoload().is_in_progress()

    # -- Execution --
    def _drive_drip(self, slot_num, distance):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        mms_drive.update_focus_slot(slot_num)

        with mms_slot.inlet.monitor_release(
                condition = self.mms.fracture_detection_is_enabled,
                callback = self.mms_delivery.handle_filament_fracture,
                params = {"slot_num":slot_num}
            ):
            mms_drive.manual_move(
                distance = distance,
                speed = self.dl_config.speed,
                accel = self.dl_config.accel
            )

            if mms_drive.move_is_terminated():
                self.break_accumulate += 1
                if self.break_accumulate >= 2:
                    self.dist_calibrator.scale_down_next()
            elif mms_drive.move_is_completed():
                self.break_accumulate = 0

            return abs(mms_drive.get_distance_moved())

    def _can_dripload(self):
        check_lst = [
            (self.mms.printer_is_shutdown, "printer is shutdown"),
            # (self.mms.printer_is_paused, "printer is paused"),
            (self.mms.mms_selector_is_running, "selector is running"),
            (self.mms.mms_drive_is_running, "drive is running"),
            (self.mms_autoload_in_progress, "autoload is in progress"),
        ]
        for condition,msg in check_lst:
            if condition():
                if self.dl_config.show_debug_log:
                    self.log_info_silent(f"dripload skip: {msg}")
                return False

        if not self._in_progress:
            return False

        return True

    def _dripload(self, slot_num):
        if not self._can_dripload():
            return

        try:
            if self.mms.get_mms_slot(slot_num).drip.is_triggered():
                dist_cfg = self.dl_config.drip_distance
                dist_est = self.dist_calibrator.get_estimate()
                dist_want = min(dist_est, dist_cfg) if dist_est else dist_cfg

                dist_dripped = self._drive_drip(slot_num, dist_want)
                self.call_count += 1
                self.log_info(
                    f"slot[{slot_num}] dripload count: {self.call_count}, "
                    f"distance: {dist_dripped:.2f}")

                success = self.dist_calibrator.add_measurement(dist_dripped)
                self.log_info_silent(
                    "dripload status:\n"
                    f"dist_cfg: {dist_cfg:.2f}\n"
                    f"dist_est: {dist_est:.2f}\n"
                    f"dist_want: {dist_want:.2f}\n"
                    f"dist_dripped: {dist_dripped:.2f}\n"
                    f"added: {success}"
                )

        except Exception as e:
            self.log_error(f"slot[{slot_num}] dripload error: {e}")

    # -- Origin Ver --
    def org_activate(self):
        if self.periodic_task.is_running() or self._in_progress:
            self.log_warning("another dripload task is activating")
            return

        slot_num = self.mms.get_current_slot()
        if slot_num is None:
            self.log_warning("current slot is None, dripload activate failed")
            return

        func = self._dripload
        params = {"slot_num":slot_num}
        try:
            is_ready = self.periodic_task.schedule(func, params)
            if is_ready:
                self.periodic_task.start()
        except Exception as e:
            self.log_error(f"slot[{slot_num}] dripload activate error: {e}")
            return

        self._in_progress = True
        self._drip_slot_num = slot_num
        self.log_info(f"slot[{slot_num}] dripload activate")

    def org_deactivate(self):
        if not self._in_progress:
            # self.log_warning("no dripload task is activating")
            return

        self._in_progress = False
        self.periodic_task.stop()
        self.log_info(f"slot[{self._drip_slot_num}] dripload deactivate")
        self._drip_slot_num = None

    # -- Advanced Ver --
    def _break_dripload(self, mcu_pin=None):
        if self._drip_slot_num is None:
            self.log_info_silent("slot unknown, break dripload failed")
            return

        mms_drive = self.mms.get_mms_slot(self._drip_slot_num).get_mms_drive()
        if mms_drive.is_running():
            mms_drive.terminate_manual_move()

            self.break_count += 1
            self.log_info_silent(
                f"slot[{self._drip_slot_num}]"
                f" break count: {self.break_count}")

    def activate(self):
        if self.periodic_task.is_running() or self._in_progress:
            self.log_warning("another dripload task is activating")
            return

        slot_num = self.mms.get_current_slot()
        if slot_num is None:
            self.log_warning("current slot is None, dripload activate failed")
            return

        func = self._dripload
        params = {"slot_num":slot_num}
        try:
            is_ready = self.periodic_task.schedule(func, params)
            if is_ready:
                self.periodic_task.start()

            mms_slot = self.mms.get_mms_slot(slot_num)
            mms_slot.outlet.add_trigger_callback(self._break_dripload)
        except Exception as e:
            self.log_error(f"slot[{slot_num}] dripload activate error: {e}")
            return

        self._in_progress = True
        self._drip_slot_num = slot_num
        self.log_info(f"slot[{slot_num}] dripload activate")

    def deactivate(self):
        if not self._in_progress:
            # self.log_warning("no dripload task is activating")
            return
        if self._drip_slot_num is None:
            self.log_warning("slot unknown, dripload deactivate failed")
            return

        try:
            mms_slot = self.mms.get_mms_slot(self._drip_slot_num)
            mms_slot.outlet.remove_trigger_callback(self._break_dripload)

            self.periodic_task.stop()
            self._break_dripload()
        except Exception as e:
            self.log_error(
                f"slot[{self._drip_slot_num}] dripload deactivate"
                f" error: {e}")
            return

        self.log_info(f"slot[{self._drip_slot_num}] dripload deactivate")
        self._drip_slot_num = None
        self._in_progress = False

    # -- GCode command --
    def cmd_MMS_DRIPLOAD(self, gcmd):
        switch = gcmd.get_int("SWITCH", 0)
        if switch:
            self.activate()
        else:
            self.deactivate()


def load_config(config):
    return MMSDripload(config)


#######################################
#  Distance Calibrator for Dripload
#######################################
class DistanceCalibrator:
    """
    Distance calibrator with noise rejection

    Theoretically, calculating some functions with Numpy
    would be more efficient and convenient,
    but since Klipper’s default requirements do not include Numpy,
    the non-Numpy approach is used by default for computation.
    """
    def __init__(self):
        self.maxlen = 5
        # Lower Sensitivity
        # =>Stricter outlier detection
        # =>Fewer measurements accepted
        # Higher Sensitivity
        # =>Looser outlier detection
        # =>More measurements accepted
        self.sensitivity = 3.0
        # 0.5 mm
        self.std_deviation_limit = 0.5

        # Last return time
        self._return_time = 0
        # 1.0 second
        self._return_delta = 1.0

        self._factor_up = 1.1
        self._factor_down = 0.9
        self._scale_down_next = False

        self.dist_deque = deque([], maxlen=self.maxlen)
        self.estimate = 0

    def add_measurement(self, dist):
        """Process new measurement with outlier rejection"""
        if len(self.dist_deque) > 2 and self._is_outlier(dist):
            # Skip outliers
            return False

        self.dist_deque.append(dist)
        # Center estimate
        self.estimate = median(self.dist_deque)
        return True

    def _is_outlier(self, dist):
        """Check if value is an outlier using IQR method"""
        sorted_data = sorted(self.dist_deque)
        n = len(sorted_data)
        q1 = sorted_data[n//4]
        q3 = sorted_data[n*3//4]
        # Interquartile range
        iqr = q3 - q1
        return (dist < (q1-self.sensitivity*iqr)
                or dist > (q3+self.sensitivity*iqr))

    def is_converged(self):
        """Check if estimates have stabilized"""
        l = len(self.dist_deque)
        if l < self.dist_deque.maxlen:
            return False

        mean = sum(self.dist_deque) / l
        variance = sum((x-mean) ** 2 for x in self.dist_deque) / l
        std_deviation = variance ** 0.5
        # Standard deviation < limit mm
        return std_deviation < self.std_deviation_limit

    def scale_down_next(self):
        self._scale_down_next = True

    def _need_scale_up(self):
        cur_time = time.time()
        delta = cur_time - self._return_time
        self._return_time = cur_time
        # Apply scale up if called too frequently
        return delta < self._return_delta

    def _need_scale_down(self):
        return self._scale_down_next

    def get_estimate(self):
        """Get calibrated estimate with business logic applied"""
        if not self.is_converged():
            return 0

        # Scale calibration value
        if self._need_scale_down():
            self._scale_down_next = False
            return self.estimate * self._factor_down
        elif self._need_scale_up():
            return self.estimate * self._factor_up

        # Normal case: return calibrated estimate
        return self.estimate
