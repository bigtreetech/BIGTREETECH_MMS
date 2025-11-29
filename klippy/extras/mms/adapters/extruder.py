# Adapter of printer's Extruder
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager

from .base import BaseAdapter
from .gcode_move import gcode_move_adapter as gm_adp
from .motion_report import motion_report_adapter as mr_adp
from .printer import printer_adapter
from .toolhead import toolhead_adapter


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
        self._log_error = mms_logger.create_log_error(console_output=True)

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
        if self.get_current_temp() < self.get_min_temp():
            self.set_temperature(self.get_min_temp())

    def is_hot_enough(self):
        if not self.get_extruder_status().get("can_extrude"):
            self.log_error(
                f"extruder[{self.get_extruder_name()}] is not hot enough")
            return False
        return True

    # ---- Extrude/Retract ----
    @contextmanager
    def _apply_disable_absolute_extrude(self):
        gm_adp.save_absolute_extrude()
        gm_adp.disable_absolute_extrude()
        try:
            yield
        finally:
            gm_adp.restore_absolute_extrude()

    def _move(self, distance, speed, wait=True):
        with self._apply_disable_absolute_extrude():
            toolhead_adapter.safe_move(
                {"E":distance, "F":speed}, wait)

    def extrude(self, distance, speed, wait=True):
        self.log_info(
            "\n"
            f"mms extruder extrude:\n"
            f"{distance} mm\n"
            f"{speed} mm/min\n"
            f"wait: {wait}"
        )
        self._move(abs(distance), speed, wait)

    def retract(self, distance, speed, wait=True):
        self.log_info(
            "\n"
            f"mms extruder retract:\n"
            f"{distance} mm\n"
            f"{speed} mm/min\n"
            f"wait: {wait}"
        )
        self._move(-abs(distance), speed, wait)

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
        return gm_speed / 60.

    def get_position_speed(self):
        position = gm_adp.get_toolhead_position().get("e")
        speed = gm_adp.get_current_status().get("speed")
        return position, speed / 60.

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


# Global instance for singleton
extruder_adapter = ExtruderAdapter()
