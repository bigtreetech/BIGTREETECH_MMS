# Adapter of printer's print_stats
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass

from .base import BaseAdapter


# Enum state from klippy/extras/print_stats.py
@dataclass(frozen=True)
class PrintState:
    standby: str = "standby"
    printing: str = "printing"
    paused: str = "paused"
    complete: str = "complete"
    error: str = "error"
    cancelled: str = "cancelled"

    def get_finish_states(self):
        return (self.complete, self.error, self.cancelled)


class PrintStatsAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "print_stats"
        self.print_state = PrintState()

    def _get_print_stats(self):
        return self.safe_get(self._obj_name)

    def get_print_start_time(self):
        return self._get_print_stats().print_start_time

    def get_last_pause_time(self):
        return self._get_print_stats().last_pause_time

    def get_current_status(self):
        """
        {
            'filename': 'xxx.gcode',
            'total_duration': 0.019674915994983166,
            'print_duration': 0.0,
            'filament_used': 0.0,
            'state': 'printing',
            'message': '',
            'info': {
                'total_layer': None,
                'current_layer': None
            }
        }
        """
        # The better solution is receive a event from print_stats
        # However print_stats does not send events while note_xxx is called
        return self._get_print_stats().get_status(self.reactor.monotonic())

    def get_state(self):
        return self.get_current_status().get("state")

    def get_filename(self):
        return self.get_current_status().get("filename")

    def is_printing(self, state=None):
        state = state or self.get_state()
        return state == self.print_state.printing

    def is_paused(self, state=None):
        state = state or self.get_state()
        return state == self.print_state.paused

    def is_finished(self, state=None):
        state = state or self.get_state()
        return state in self.print_state.get_finish_states()

    def is_paused_or_finished(self):
        state = self.get_state()
        return self.is_paused(state) or self.is_finished(state)


# Global instance for singleton
print_stats_adapter = PrintStatsAdapter()
