# Mapping of adapters import
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .buttons import buttons_adapter
from .extruder import extruder_adapter
from .fan import fan_adapter
from .force_move import force_move_adapter
from .gcode_move import gcode_move_adapter
from .gcode import gcode_adapter
from .heaters import heaters_adapter
from .idle_timeout import idle_timeout_adapter
from .manual_stepper import manual_stepper_dispatch
from .motion_queuing import motion_queuing_adapter
from .motion_report import motion_report_adapter
from .neopixel import neopixel_dispatch
from .pause_resume import pause_resume_adapter
from .pins import pins_adapter
from .print_stats import print_stats_adapter
from .printer import printer_adapter
from .query_endstops import query_endstops_adapter
from .stepper_enable import stepper_enable_adapter
from .toolhead import toolhead_adapter
from .virtual_sdcard import virtual_sdcard_adapter

__all__ = [
    'buttons_adapter',
    'extruder_adapter',
    'fan_adapter',
    'force_move_adapter',
    'gcode_move_adapter',
    'gcode_adapter',
    'heaters_adapter',
    'idle_timeout_adapter',
    'manual_stepper_dispatch',
    'motion_queuing_adapter',
    'motion_report_adapter',
    'neopixel_dispatch',
    'pause_resume_adapter',
    'pins_adapter',
    'print_stats_adapter',
    'printer_adapter',
    'query_endstops_adapter',
    'stepper_enable_adapter',
    'toolhead_adapter',
    'virtual_sdcard_adapter',
]
