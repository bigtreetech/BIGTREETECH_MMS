# Support for MMS Brush
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager
from dataclasses import dataclass, fields

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.config import (
    OptionalField,
    PointType,
    PointsType,
    PrinterConfig
)


@dataclass(frozen=True)
class PrinterBrushConfig(PrinterConfig):
    # Enable/disable the brush module
    # 0 = disable, 1 = enable
    # When disabled, all brush operations will be skipped
    enable: int = 1

    # Z-axis lift distance during filament brushing operations
    # Unit: mm
    z_raise: float = 1.0

    # Cooling Fan Settings
    # Fan speed during cooldown phase
    # Value range: 0.0 to 1.0 (0% to 100%)
    fan_cooldown_speed: float = 1.0
    # Duration to wait while cooling with fan
    # Unit: seconds
    fan_cooldown_wait: float = 1.0

    # Brush Wiping Configuration
    # X/Y coordinates of the wipe points on the brush
    # Toolhead moves sequentially between these points to wipe the nozzle
    wipe_points: PointsType = "(90.0, 300.0), (60.0, 300.0)"
    # Wiping movement speed
    # Unit: mm/min
    wipe_speed: float = 10000.0
    # Number of wiping passes on the brush
    # Increasing this value helps remove more excess filament from nozzle tip
    # Adjust based on filament type and cleanliness requirements
    wipe_times: int = 5

    # Brush Pecking Configuration
    # X/Y coordinates of the peck point on the brush
    # Toolhead moves to this point for pecking (plunging) action
    peck_point: PointType = "(150.0, 300.0)"
    # Pecking movement speed
    # Unit: mm/min
    peck_speed: float = 10000.0
    # Depth of each peck into the brush
    # Unit: mm
    peck_depth: float = 2.0
    # Number of pecking cycles on the brush
    peck_times: int = 0

    # Custom Macro
    custom_before: OptionalField = "MMS_BRUSH_CUSTOM_BEFORE"
    custom_after: OptionalField = "MMS_BRUSH_CUSTOM_AFTER"


class MMSBrush:
    def __init__(self, config):
        p_brush_config = PrinterBrushConfig(config)
        for field in fields(p_brush_config):
            key = field.name
            # Don't cover
            if not p_brush_config.should_skip(key) \
                and not hasattr(self, key):
                val = getattr(p_brush_config, key)
                setattr(self, key, val)

        # State tracking
        self._is_running = False

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    # ---- Initial ----
    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()

    def _initialize_gcode(self):
        commands = [
            ("MMS_BRUSH", self.cmd_MMS_BRUSH),
            ("MMS_BRUSH_WIPE", self.cmd_MMS_BRUSH_WIPE),
            ("MMS_BRUSH_PECK", self.cmd_MMS_BRUSH_PECK),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    # ---- Status ----
    def is_enabled(self):
        return bool(self.enable)

    def is_running(self):
        return self._is_running

    @contextmanager
    def _brush_is_running(self):
        self._is_running = True
        try:
            yield
        finally:
            self._is_running = False

    # ---- Brush ----
    def _safety_checks(self):
        if self.is_running():
            self.log_warning("another brush is running, return")
            return False

        # Check toolhead
        if not toolhead_adapter.is_homed():
            self.log_warning("toolhead is not homed, return")
            return False

        return True

    def wipe(self):
        if not self._safety_checks():
            return False

        if not self.wipe_points:
            self.log_warning("wipe_points is not available, return")
            return False

        with self._brush_is_running():
            try:
                for i in range(self.wipe_times):
                    for point in self.wipe_points:
                        toolhead_adapter.move_xy(
                            position_x = point[0],
                            position_y = point[1],
                            speed = self.wipe_speed,
                            wait_toolhead = False
                        )

                toolhead_adapter.wait_moves()
                return True
            except Exception as e:
                self.log_error(f"brush wipe error: {e}")
                return False

    def wipe_cold(self):
        if not self._safety_checks():
            return False

        if not self.wipe_points:
            self.log_warning("wipe_points is not available, return")
            return

        # wipe_cold_temp = get_filament_cold_temp()
        wipe_cold_temp = 180.0
        wipe_cold_wait = 3.0

        with self._brush_is_running():
            current_temp = extruder_adapter.get_current_temp()
            if current_temp > wipe_cold_temp:
                extruder_adapter.set_temperature(wipe_cold_temp)

            # Wipe only once
            for point in self.wipe_points:
                toolhead_adapter.move_xy(
                    position_x = point[0],
                    position_y = point[1],
                    speed = self.wipe_speed,
                    wait_toolhead = False
                )

            extruder_adapter.set_temperature(current_temp)
            toolhead_adapter.dwell(delay=wipe_cold_wait)

    def peck(self):
        if not self._safety_checks():
            return False

        if not self.peck_point:
            self.log_warning("peck_point is not available, return")
            return False

        with self._brush_is_running():
            try:
                toolhead_adapter.move_xy(
                    position_x = self.peck_point[0],
                    position_y = self.peck_point[1],
                    wait_toolhead = True
                )

                current_z = toolhead_adapter.get_position().get("z")

                for i in range(self.peck_times):
                    toolhead_adapter.move_z(
                        position = current_z - self.peck_depth,
                        speed = self.peck_speed,
                        wait_toolhead = False
                    )
                    toolhead_adapter.move_z(
                        position = current_z,
                        speed = self.peck_speed,
                        wait_toolhead = False
                    )

                toolhead_adapter.wait_moves()
                return True
            except Exception as e:
                self.log_error(f"brush peck error: {e}")
                return False

    def mms_brush(self):
        if self.custom_before:
            self.log_info(
                f"MMS execute macro before BRUSH: {self.custom_before}")
            gcode_adapter.run_command(self.custom_before)

        if not self.is_enabled():
            self.log_info_s("MMS BRUSH is disabled, skip...")
            return True

        if not self._safety_checks():
            return False

        log_prefix = f"slot[*] brush"
        self.log_info_s(f"{log_prefix} begin")

        with toolhead_adapter.fan_cooldown(
                speed = self.fan_cooldown_speed,
                wait = self.fan_cooldown_wait
            ):
            if not self.wipe():
                self.log_warning(f"{log_prefix} failed")
                return False

            if not self.peck():
                self.log_warning(f"{log_prefix} failed")
                return False

        self.log_info_s(f"{log_prefix} finish")

        if self.custom_before:
            self.log_info(
                f"MMS execute macro before BRUSH: {self.custom_before}")
            gcode_adapter.run_command(self.custom_before)

        return True

    # ---- GCode ----
    def cmd_MMS_BRUSH(self, gcmd=None):
        with toolhead_adapter.snapshot():
            with toolhead_adapter.safe_z_raise(self.z_raise):
                self.mms_brush()

    def cmd_MMS_BRUSH_WIPE(self, gcmd=None):
        if self.is_running():
            self.log_warning("another brush is running, return")
            return False

        with toolhead_adapter.snapshot():
            with toolhead_adapter.safe_z_raise(self.z_raise):
                with toolhead_adapter.fan_cooldown(
                        speed = self.fan_cooldown_speed,
                        wait = self.fan_cooldown_wait
                    ):
                    self.wipe()

    def cmd_MMS_BRUSH_PECK(self, gcmd=None):
        if self.is_running():
            self.log_warning("another brush is running, return")
            return False

        with toolhead_adapter.snapshot():
            with toolhead_adapter.safe_z_raise(self.z_raise):
                with toolhead_adapter.fan_cooldown(
                        speed = self.fan_cooldown_speed,
                        wait = self.fan_cooldown_wait
                    ):
                    self.peck()


def load_config(config):
    return MMSBrush(config)
