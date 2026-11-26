# Adapter of printer's GCode
#
# Copyright (C) 2025-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from gcode import GCodeCommand

from .base import BaseAdapter
from .motion_queuing import motion_queuing_adapter
from .printer import printer_adapter


class GCodeAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "gcode"
        self._command_lst = []

    # -- Initialize --
    def register_self_command(self):
        self.register("MMS_MAN", self.cmd_MMS_MAN, "MMS commands manual")

    def _setup_logger(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)

    # -- GCode Control --
    def _get_gcode(self):
        return self.safe_get(self._obj_name)

    def register(self, command, handler, desc=None):
        self._get_gcode().register_command(command, handler, desc=desc)
        if command not in self._command_lst:
            self._command_lst.append(command)

    def unregister(self, command):
        handler = self._get_gcode().register_command(command, None)
        if command in self._command_lst:
            self._command_lst.remove(command)
        return handler

    def register_mux(self, cmd, key, value, func):
        self._get_gcode().register_mux_command(
            cmd=cmd, key=key, value=value, func=func)

    def bulk_register(self, commands):
        for command, handler, *rest in commands:
            desc = rest[0] if rest else None
            self.register(command, handler, desc)

    def run_command(self, command, strict=False):
        if command:
            try:
                motion_queuing_adapter.run_native_motion(
                    self._get_gcode().run_script_from_command, command)
            except Exception as e:
                if strict:
                    raise
                self.log_error(
                    f"gcode adapter run command '{command}' error: {e}")

    # def run_script(self, script):
    #     if script:
    #         self._get_gcode().run_script(script)

    def easy_gcmd(self, command=None, params=None):
        # params_dct = {} if params is None else params
        return GCodeCommand(
            gcode=self._get_gcode(),
            command=command or "",
            commandline="",
            params=params or {},
            need_ack=False
        )

    def console_print(self, msg, log=False):
        self._get_gcode().respond_info(msg, log)

    def _respond(self, res_type, msg):
        # Output to Console and
        # UI(like KlipperScreen) would pop up a dialog
        self.run_command(f"RESPOND TYPE={res_type} MSG='{msg}'")

    def respond_echo(self, msg):
        self._respond(res_type="echo", msg=msg)

    def respond_error(self, msg):
        self._respond(res_type="error", msg=msg)

    # -- GCode Command --
    def cmd_MMS_MAN(self, gcmd):
        # Sort commands
        self._command_lst.sort()

        # Get config patterns
        version = printer_adapter.get_mms().get_version()
        swap_cmd = printer_adapter.get_mms_swap().get_command_string()

        # Filter
        cmd_filtered = [
            cmd for cmd in self._command_lst
            if cmd.startswith(swap_cmd) \
                or (cmd.startswith("MMS_") and "TEST" not in cmd)
        ]

        # Format message
        # cmd_str = "\n".join(cmd_filtered)
        cmd_formatted = [f"- {cmd}" for cmd in cmd_filtered]
        cmd_str = "\n".join(cmd_formatted)
        msg = f"== MMS Native Commands (v{version}) ==\n{cmd_str}"

        # Log and print to console
        self.log_info(msg)


# Global instance for singleton
gcode_adapter = GCodeAdapter()
