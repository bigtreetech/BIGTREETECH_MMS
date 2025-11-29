# Adapter of printer's virtual_sdcard
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter


class VirtualSDCardAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "virtual_sdcard"

    def _get_virtual_sdcard(self):
        return self.safe_get(self._obj_name)

    def get_current_status(self):
        """
        {
            'file_path': '/home/biqu/printer_data/gcodes/xxx.gcode',
            'progress': 0.0012693468630994573,
            'is_active': True,
            'file_position': 9541,
            'file_size': 7516464
        }
        """
        return self._get_virtual_sdcard().get_status(self.reactor.monotonic())

    def is_active(self):
        return self.get_current_status().get("is_active")

    ###########################
    # --
    # Klipper Pause Flow
    # --
    # PauseResume.send_pause_command()
    # -> VirtualSD.do_pause() set VirtualSD.must_pause_work to True
    # -> VirtualSD.work_handler() loop break
    # -> | PrintStats.note_pause() update PrintStats.state
    #    | PauseResume.send_pause_command() set
    #      PauseResume.pause_command_sent to True
    ###########################
    def has_pause_flag(self):
        return self.is_active() \
            and self._get_virtual_sdcard().must_pause_work

    def has_resume_or_shutdown_flag(self):
        return self.is_active() \
            and not self._get_virtual_sdcard().must_pause_work


# Global instance for singleton
virtual_sdcard_adapter = VirtualSDCardAdapter()
