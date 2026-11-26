# Support for MMS Moonraker adapter
#
# Copyright (C) 2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from datetime import datetime, timezone
from uuid import uuid4

from .printer import printer_adapter


class MMSMoonrakerAdapter:
    """Transfer filament metadata between SlotMeta and public lane_data."""

    def initialize(self):
        # klippy:connect belongs to a new Printer, including an in-process
        # RESTART. A Moonraker reconnection uses _handle_ready on this session.
        self.mms = printer_adapter.get_mms()
        self.reactor = printer_adapter.get_reactor()
        self.webhooks = printer_adapter.get_obj("webhooks")
        self._state = "offline"
        self._request_id = None
        self._write = None
        self._scheduled_publish = None
        # Local edits before the first successful pull, during a pull, or
        # while disconnected. Keep field patches so unrelated saved data can
        # still be restored. An explicit reset also erases unedited fields.
        self._pending = {}
        self.webhooks.register_endpoint(
            "mms/endpoint/moonraker_ready", self._handle_ready)
        self.webhooks.register_endpoint(
            "mms/endpoint/restore_lane_data", self._handle_restore)
        self.webhooks.register_endpoint(
            "mms/endpoint/lane_data_written", self._handle_written)

    def _call(self, method, **kwargs):
        try:
            self.webhooks.call_remote_method(method, **kwargs)
        except Exception as e:
            # The optional component or its socket may be unavailable. Never
            # block command processing or change the print state.
            self.mms.log_info_s(f"Moonraker {method} unavailable: {e}")
            return False
        return True

    def _handle_ready(self, request):
        request.send({"requested": self.pull(peer_ready=True)})

    def pull(self, peer_ready=False):
        known_peer = peer_ready or self._state != "offline"
        self._state = "restoring"
        # The result of this read determines the next permitted publication.
        # An already queued callback cannot retry a subsequently failed read.
        self._scheduled_publish = None
        self._requeue_write()
        self._request_id = uuid4().hex
        if not self._call("moonraker_pull_lane_data",
                          request_id=self._request_id,
                          slot_nums=self.mms.get_slot_nums()):
            self._request_id = None
            self._state = "retry" if known_peer else "offline"
            return False
        return True

    def changed(self, slot_num, patch, replace):
        old_patch, old_replace = self._pending.get(slot_num, ({}, False))
        self._pending[slot_num] = (
            dict(patch) if replace else {**old_patch, **patch},
            old_replace or replace)
        self._schedule_publish()

    def _schedule_publish(self):
        if (self._state in ("ready", "retry")
                and self._pending and self._write is None
                and self._scheduled_publish is None):
            self._scheduled_publish = ticket = object()
            self.reactor.register_callback(
                lambda _: self._publish_pending(ticket))

    def _requeue_write(self):
        if self._write is None:
            return
        _, earlier = self._write
        self._write = None
        for num, (patch, replace) in earlier.items():
            newer, newer_replace = self._pending.get(num, ({}, False))
            if not newer_replace:
                self._pending[num] = ({**patch, **newer}, replace)

    def _handle_restore(self, request):
        if request.get_str("request_id") != self._request_id:
            request.send({"accepted": False})
            return

        error = request.get_str("error", None)
        lane_data = {} if error else request.get_dict("lane_data")
        self._request_id = None
        if error:
            self._state = "retry"
            self.mms.log_info_s(f"Moonraker lane data restore failed: {error}")
            request.send({"accepted": True})
            return

        for slot in self.mms.get_mms_slots():
            slot_num = slot.get_num()
            row = lane_data.get(f"lane{slot_num}", {})
            if not isinstance(row, dict):
                self.mms.log_info_s(
                    f"slot[{slot_num}] lane data is not a record")
                continue
            slot.meta.restore_filament(row)
            if slot_num in self._pending:
                patch, replace = self._pending[slot_num]
                slot.meta.update_filament(patch, replace)

        self._state = "ready"
        self._schedule_publish()
        request.send({"accepted": True})

    def _publish_pending(self, ticket):
        if ticket is not self._scheduled_publish:
            return
        self._scheduled_publish = None
        if not self._pending or self._write is not None:
            return
        if self._state == "retry":
            # A new edit after a failed transfer gets one fresh snapshot.
            # Nothing schedules a retry merely because a transfer failed.
            self.pull()
            return
        if self._state != "ready":
            return

        scan_time = datetime.now(timezone.utc).isoformat()
        data = {}
        for num in self._pending:
            slot = self.mms.get_mms_slot(num)
            data[f"lane{num}"] = slot.meta.stored_lane(num, scan_time)

        write_id = uuid4().hex
        self._write = write_id, self._pending
        self._pending = {}
        if not self._call("moonraker_push_lane_data", lane_data=data,
                          write_id=write_id):
            self._state = "retry"
            self._requeue_write()

    def _handle_written(self, request):
        if (self._write is None
                or request.get_str("write_id") != self._write[0]):
            request.send({"accepted": False})
            return
        error = request.get_str("error", None)
        if error:
            self.mms.log_info_s(f"Moonraker lane data write failed: {error}")
            self._state = "retry"
            self._requeue_write()
        else:
            self._write = None
            self._schedule_publish()
        request.send({"accepted": True})


moonraker_adapter = MMSMoonrakerAdapter()
