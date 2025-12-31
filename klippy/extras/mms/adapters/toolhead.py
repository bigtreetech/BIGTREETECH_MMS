# Adapter of printer's toolhead
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager

from .base import BaseAdapter
from .fan import fan_adapter
from .gcode_move import gcode_move_adapter
from .heaters import heaters_adapter
from .print_stats import print_stats_adapter
from .printer import printer_adapter


class ToolheadAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "toolhead"

        self.pressure_release_time = 0.5

        self._safe_mode = False
        self._move_speed = 0.0

        self._snapshot = None
        self._resume_target_temp = None

    def _setup_logger(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=False)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    def _get_toolhead(self):
        return self.safe_get(self._obj_name)

    def dwell(self, delay):
        self._get_toolhead().dwell(delay)

    def is_busy(self):
        print_time, est_print_time, lookahead_empty = \
            self._get_toolhead().check_busy(self.reactor.monotonic())
        idle_time = est_print_time - print_time
        return not lookahead_empty or idle_time < 1.

    def get_print_time(self):
        return self._get_toolhead().get_last_move_time()

    def wait_moves(self):
        # M400
        self._get_toolhead().wait_moves()

    def get_status(self):
        return self._get_toolhead().get_status(self.reactor.monotonic())

    def get_homed_axes(self):
        return self.get_status().get("homed_axes")

    def is_homed(self):
        return "xyz" in self.get_homed_axes()

    # ---- Extruder ----
    def has_extruder(self):
        extruder_name = self.get_status().get("extruder")
        return extruder_name and extruder_name != ""

    def get_extruder(self):
        return self._get_toolhead().get_extruder()

    def get_extruder_heater(self):
        return self.get_extruder().get_heater()

    def get_extruder_heater_status(self):
        return self.get_extruder_heater().get_status(self.reactor.monotonic())

    def get_extruder_heater_temp(self):
        return self.get_extruder_heater_status().get("temperature")

    def get_extruder_heater_target_temp(self):
        return self.get_extruder_heater_status().get("target")

    def set_extruder_temperature(self, temp, wait=True):
        e_name = self.get_extruder().get_name()
        heater = self.get_extruder_heater()

        # wait timeout?
        if wait:
            self.log_info(
                f"extruder[{e_name}] waiting heat to temp:{temp}..."
            )
        else:
            self.log_info_s(
                f"extruder[{e_name}] heat to temp:{temp} without waiting"
            )

        heaters_adapter.set_temperature(heater, temp, wait)
        if wait:
            self.log_info(
                f"extruder[{e_name}] finish heat to temp:{temp}"
            )

    # ---- Fan ----
    @contextmanager
    def fan_cooldown(self, speed, wait):
        # Set fan cooldown speed and wait a while
        is_paused = fan_adapter.pause()
        fan_adapter.set_speed(speed)
        self.dwell(delay=wait)
        try:
            yield
        finally:
            if is_paused:
                fan_adapter.resume()

    # ---- Move ----
    # At the beginning, Move methods are coded in gcode_move_adapter.
    # However after developed, toolhead move xyz methods are coded here

    def get_position(self):
        return gcode_move_adapter.get_toolhead_position()

    def set_move_speed(self, move_speed):
        self._move_speed = move_speed

    def get_move_speed(self):
        return self._move_speed

    def enable_safe_mode(self):
        self._safe_mode = True

    def disable_safe_mode(self):
        self._safe_mode = False

    def safe_move(self, params, wait_toolhead=False):
        gcode_move_adapter.g1(params)
        if wait_toolhead or self._safe_mode:
            self.wait_moves()

    def move(self, params_dct, speed=None, wait_toolhead=False):
        speed = speed or self._move_speed
        if speed is not None:
            params_dct["F"] = speed
        self.safe_move(params_dct, wait_toolhead)

    def move_x(self, position, speed=None, wait_toolhead=False):
        self.move({"X":position}, speed, wait_toolhead)

    def move_y(self, position, speed=None, wait_toolhead=False):
        self.move({"Y":position}, speed, wait_toolhead)

    def move_z(self, position, speed=None, wait_toolhead=False):
        self.move({"Z":position}, speed, wait_toolhead)

    def move_xy(self, position_x, position_y, speed=None, wait_toolhead=False):
        self.move({"X":position_x, "Y":position_y}, speed, wait_toolhead)

    def raise_z(self, distance):
        gcode_move_adapter.disable_absolute_coordinates()
        toolhead_adapter.move_z(abs(distance))
        gcode_move_adapter.enable_absolute_coordinates()

    def lower_z(self, distance):
        gcode_move_adapter.disable_absolute_coordinates()
        toolhead_adapter.move_z(-abs(distance))
        gcode_move_adapter.enable_absolute_coordinates()

    @contextmanager
    def safe_z_raise(self, raise_distance):
        # Always raise a little on Z-axis to avoid touching the model
        self.raise_z(raise_distance)
        try:
            yield
        finally:
            # Recover Z-axis
            self.lower_z(raise_distance)

    # ---- Snapshot ----
    def _format_snapshot(self):
        return {
            "position" : self.get_position(),
            "extruder_current_temp" : self.get_extruder_heater_temp(),
            "extruder_target_temp" : self.get_extruder_heater_target_temp(),
            "fan_speed" : fan_adapter.get_speed(),
        }

    def log_snapshot(self, snapshot=None):
        snapshot = snapshot or self._format_snapshot()
        self.log_info_s(
            "\n"
            "current toolhead snapshot:\n"
            "toolhead position - "
            f"x: {snapshot['position']['x']:.2f} "
            f"y: {snapshot['position']['y']:.2f} "
            f"z: {snapshot['position']['z']:.2f}\n"
            f"extruder current temp: {snapshot['extruder_current_temp']:.2f}\n"
            f"extruder target temp: {snapshot['extruder_target_temp']:.2f}\n"
            f"fan speed: {snapshot['fan_speed']:.2f}"
        )

    def save_snapshot(self):
        if self._snapshot:
            self.log_warning(
                "another toolhead snapshot exists, save failed")
            return False

        self._snapshot = self._format_snapshot()
        self.log_info_s("new toolhead snapshot saved:")
        self.log_snapshot(self._snapshot)
        return True

    def restore_snapshot(self, ignore_cool_down=True):
        if not self._snapshot:
            self.log_warning(
                "no toolhead snapshot found, restore failed")
            return False

        if not self.is_homed():
            self.log_warning("toolhead not homed")
            self.truncate_snapshot()
            return False

        if print_stats_adapter.is_paused_or_finished():
            self.log_info_s("print is paused or finished")
            self.truncate_snapshot()
            return True

        # XYZ
        position = self._snapshot.get("position")
        self.move_xy(
            position_x = position["x"],
            position_y = position["y"],
            wait_toolhead = True
        )
        self.move_z(
            position = position["z"],
            wait_toolhead = True
        )

        # Extruder heater
        target_temp = self._snapshot.get("extruder_target_temp")
        current_temp = self.get_extruder_heater_temp()
        if target_temp > current_temp:
            self.log_info_s(
                f"current temp: {current_temp:.2f}, "
                f"resume extruder saved target_temp: {target_temp:.2f}")
            # Heat to snapshot temp
            self.set_extruder_temperature(target_temp, wait=False)
        elif target_temp < current_temp and not ignore_cool_down:
            self.log_info_s(
                f"current temp: {current_temp:.2f}, "
                f"cooldown extruder to saved target_temp: {target_temp:.2f}")
            # Cooldown to snapshot temp
            self.set_extruder_temperature(target_temp, wait=True)

        # Fan
        fan_speed = self._snapshot.get("fan_speed")
        fan_adapter.set_speed(fan_speed)

        self.log_info_s("saved toolhead snapshot restore:")
        self.log_snapshot(self._snapshot)

        # Finally truncate saved state
        self.truncate_snapshot()
        return True

    def truncate_snapshot(self):
        if not self._snapshot:
            self.log_warning(
                "no toolhead snapshot found, truncate failed")
            return False
        self._snapshot = None
        self.log_info_s("saved toolhead snapshot is truncated")
        return True

    @contextmanager
    def snapshot(self):
        self.save_snapshot()
        try:
            yield
        finally:
            self.restore_snapshot()

    def save_target_temp(self):
        target_temp = self.get_extruder_heater_target_temp()

        if self._resume_target_temp:
            self.log_warning(
                "resume target_temp is already exists: "
                f"{self._resume_target_temp:.2f}, "
                f"cover with: {target_temp:.2f}"
            )

        self._resume_target_temp = target_temp
        self.log_info_s(f"new resume target_temp saved: {target_temp:.2f}")

    def restore_target_temp(self):
        if not self._resume_target_temp:
            self.log_warning("no target_temp found, restore failed")
            return False

        # Extruder heater
        target_temp = self._resume_target_temp
        current_temp = self.get_extruder_heater_temp()
        if target_temp > current_temp:
            self.log_info_s(
                f"current temp:{current_temp:.2f}, "
                f"restore saved target_temp :{target_temp:.2f}")
            # Heat to snapshot temp
            self.set_extruder_temperature(target_temp, wait=True)
        elif target_temp < current_temp:
            self.log_warning(
                f"saved target_temp:{target_temp:.2f} < "
                f"current_temp:{current_temp:.2f}, restore skip")
            # Cooldown to snapshot temp
            # self.set_extruder_temperature(target_temp, wait=True)

        # Finally truncate
        self._resume_target_temp = None
        return True

    # ---- Pressure ----
    def release_pressure(self, pressure_release_time=None):
        delay = pressure_release_time or self.pressure_release_time
        self.dwell(delay)


# Global instance for singleton
toolhead_adapter = ToolheadAdapter()
