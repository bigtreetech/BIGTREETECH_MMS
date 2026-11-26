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

    def query_state(self, endstop, print_time):
        # The public query drops the receive timestamp. Keep MCU internals
        # isolated here; these fields match both supported core versions.
        mcu = endstop.get_mcu()
        if mcu.is_fileoutput():
            return self.reactor.monotonic(), 0
        params = endstop._query_cmd.send(
            [endstop._oid], minclock=mcu.print_time_to_clock(print_time))
        return params["#receive_time"], params["pin_value"] ^ endstop._invert

    def set_homing_timeout(self, endstop, timeout):
        # Both supported cores choose the timeout in TriggerDispatch.start.
        # Each MMS endstop owns its trsync instances, including on shared MCUs.
        trsyncs = endstop._dispatch._trsyncs
        if len(trsyncs) == 1:
            return
        for trsync in trsyncs:
            def start(print_time, offset, completion, expire_timeout,
                      native_start=trsync.start):
                return native_start(print_time, offset, completion, timeout)
            trsync.start = start

    def register_endstop(self, mcu_endstop, mcu_pin):
        self._get_query_endstops().register_endstop(mcu_endstop, mcu_pin)


# Global instance for singleton
query_endstops_adapter = QueryEndstopsAdapter()
