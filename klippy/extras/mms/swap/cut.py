# Support for MMS Cut
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager
from dataclasses import dataclass, fields

from ..adapters import (
    gcode_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.config import PointType, PrinterConfig


@dataclass(frozen=True)
class PrinterCutConfig(PrinterConfig):
    # Enable/disable the cutter module
    # 0 = disable, 1 = enable
    # When disabled, all cutter operations will be skipped
    enable: int = 1

    # Z-axis lift distance during cutting operations
    # Unit: mm
    z_raise: float = 1.0

    # Toolhead movement speed from cut_init to cut_final position
    # Unit: mm/min
    cut_speed: float = 2000.0

    # (position_x, position_y)
    # X/Y coordinates of the initial cutting point
    cutter_init_point: PointType = "(40.0, 50.0)"
    # X/Y coordinates of the final cutting point
    cutter_final_point: PointType = "(20.0, 50.0)"

    # def get_type_handlers(self):
    #     handlers = super().get_type_handlers()
    #     handlers[PointType] =
    #         lambda config, name: PointType.parse_point(config.get(name))
    #     return handlers


class MMSCut:
    def __init__(self, config):
        p_cut_config = PrinterCutConfig(config)
        for field in fields(p_cut_config):
            key = field.name
            # Don't cover
            if not p_cut_config.should_skip(key) \
                and not hasattr(self, key):
                val = getattr(p_cut_config, key)
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
            ("MMS_CUT", self.cmd_MMS_CUT),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)

    # ---- Status ----
    def is_enable(self):
        return bool(self.enable)

    def is_running(self):
        return self._is_running

    @contextmanager
    def _cut_is_running(self):
        self._is_running = True
        try:
            yield
        finally:
            self._is_running = False

    # ---- Cut ----
    def _safety_checks(self, slot_num):
        if slot_num is None:
            self.log_warning("current slot is None, return")
            return False

        if self.is_running():
            self.log_warning("another cut is running, return")
            return False

        # Check toolhead
        if not toolhead_adapter.is_homed():
            self.log_warning("toolhead is not homed, return")
            return False

        return True

    def cut_init(self):
        # Toolhead move to the cut init X-axis and Y-axis of cutter
        toolhead_adapter.move_xy(
            position_x = self.cutter_init_point[0],
            position_y = self.cutter_init_point[1],
            wait_toolhead = True
        )

    def cut_final(self):
        # Toolhead move to the cut final X-axis and Y-axis of cutter
        toolhead_adapter.move_xy(
            position_x = self.cutter_final_point[0],
            position_y = self.cutter_final_point[1],
            speed = self.cut_speed,
            wait_toolhead = True
        )

    def mms_cut(self):
        slot_num = self.mms.get_current_slot()
        if not self._safety_checks(slot_num):
            return False

        if not self.is_enable():
            self.log_warning(f"slot[{slot_num}] cut is disabled")
            return False

        # Deactivate before Extruder unload
        mms_buffer = self.mms.get_slot(slot_num).get_mms_buffer()
        mms_buffer.deactivate_monitor()

        self.log_info(f"slot[{slot_num}] cut begin")

        with self._cut_is_running():
            try:
                self.cut_init()
                self.cut_final()
                self.cut_init()
            except Exception as e:
                self.log_error(f"slot[{slot_num}] cut error: {e}")
                return False

        self.log_info(f"slot[{slot_num}] cut finish")
        return True

    def cmd_MMS_CUT(self, gcmd):
        if not self.mms.cmd_can_exec():
            self.log_warning("MMS_CUT can not execute now")
            return False

        with toolhead_adapter.snapshot():
            with toolhead_adapter.safe_z_raise(self.z_raise):
                self.mms_cut()


def load_config(config):
    return MMSCut(config)
