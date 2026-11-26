# Adapter of printer's pause_resume
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter
from .gcode import gcode_adapter


class PauseResumeAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "pause_resume"

    def _get_pause_resume(self):
        return self.safe_get(self._obj_name)

    def get_current_status(self):
        """
        {'is_paused': False}
        """
        return self._get_pause_resume().get_status(self.reactor.monotonic())

    def is_paused(self):
        return self.get_current_status().get("is_paused")

    def get_sd_file(self):
        sd = self._get_pause_resume().v_sd
        return sd.current_file if sd is not None else None

    def send_pause_command(self):
        native = self._get_pause_resume()
        sd_file = native.v_sd.current_file if native.is_sd_active() else None
        native.send_pause_command()
        return sd_file

    def owns_file_pause(self, sd_file):
        native = self._get_pause_resume()
        return (native.pause_command_sent and native.sd_paused
                and native.v_sd is not None and native.v_sd.must_pause_work
                and native.v_sd.current_file is sd_file)

    def pause(self):
        self._get_pause_resume().cmd_PAUSE(gcode_adapter.easy_gcmd("PAUSE"))

    def get_printer_object(self):
        return self._get_pause_resume()


# Global instance for singleton
pause_resume_adapter = PauseResumeAdapter()
