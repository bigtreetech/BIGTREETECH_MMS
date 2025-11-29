# Adapter of printer's pause_resume
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter


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
    # def has_pause_command_sent(self):
    #     return self._get_pause_resume().pause_command_sent is True

    # def has_resume_or_clear_command_sent(self):
    #     return self._get_pause_resume().pause_command_sent is False

    def get_printer_object(self):
        return self._get_pause_resume()


# Global instance for singleton
pause_resume_adapter = PauseResumeAdapter()
