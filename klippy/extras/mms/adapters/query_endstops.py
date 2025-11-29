# Adapter of printer's query_endstops
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter


class QueryEndstopsAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "query_endstops"

    def _get_query_endstops(self):
        return self.safe_get(self._obj_name)

    def register_endstop(self, mcu_endstop, mcu_pin):
        self._get_query_endstops().register_endstop(mcu_endstop, mcu_pin)


# Global instance for singleton
query_endstops_adapter = QueryEndstopsAdapter()
