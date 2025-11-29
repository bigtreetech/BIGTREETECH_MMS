# Adapter of printer's GCode
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from gcode import GCodeCommand

from .base import BaseAdapter


class GCodeAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "gcode"

    def _get_gcode(self):
        return self.safe_get(self._obj_name)

    def register(self, command, handler):
        self._get_gcode().register_command(command, handler)

    def unregister(self, command):
        self._get_gcode().register_command(command, None)

    def register_mux(self, cmd, key, value, func):
        self._get_gcode().register_mux_command(
            cmd=cmd, key=key, value=value, func=func)

    def bulk_register(self, commands):
        for command, handler in commands:
            self.register(command, handler)

    def run_command(self, command):
        if command:
            self._get_gcode().run_script_from_command(command)

    # def run_script(self, script):
    #     if script:
    #         self._get_gcode().run_script(script)

    def easy_gcmd(self, params=None):
        params_dct = {} if params is None else params
        return GCodeCommand(
            gcode=self._get_gcode(), command="", commandline="",
            params=params_dct, need_ack=False
        )

    def console_print(self, msg, log=False):
        self._get_gcode().respond_info(msg, log)


# Global instance for singleton
gcode_adapter = GCodeAdapter()
