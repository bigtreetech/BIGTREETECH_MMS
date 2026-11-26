# Support for MMS SLOT RFID
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
from contextlib import contextmanager

from ..adapters import printer_adapter


class SlotRFID:
    def __init__(self, mms_slot):
        # SLOT meta
        self.mms_slot = mms_slot
        self.slot_num = mms_slot.get_num()

        # Setup later
        self.name = None
        self.enable = None
        self.detect_duration = None
        self.read_duration = None
        self.mms_rfid = None

        # Status
        self._is_detecting = False
        self._is_reading = False
        self._read_start_timer = None
        self.detect_begin_at = None
        self.detect_end_at = None
        self.read_begin_at = None
        self.read_end_at = None

        # Tag data
        self._initialize_tag()

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_loggers()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    def _initialize_tag(self):
        self.tag_data = None
        self.tag_uid = None
        self.tag_color = None

    def setup(self, name, enable, detect_duration, read_duration):
        self.name = name
        self.enable = enable
        self.detect_duration = detect_duration
        self.read_duration = read_duration

        self.mms_rfid = printer_adapter.get_obj(name)
        self.mms_delivery = printer_adapter.get_mms_delivery()

    def get_status(self):
        return {
            "name": self.name,
            # "detecting": self._is_detecting,
            # "detect_duration": self.detect_duration,
            # "detect_begin_at": self.detect_begin_at,
            # "detect_end_at": self.detect_end_at,
            # "reading": self._is_reading,
            # "read_duration": self.read_duration,
            # "read_begin_at": self.read_begin_at,
            # "read_end_at": self.read_end_at,

            "tag": {
                "uid": self.tag_uid,
                "data": self.tag_data,
                "color": self.tag_color,
            }
        }

    def has_tag_read(self):
        return self.tag_color is not None

    def is_enabled(self):
        return self.enable

    # ---- Write ----
    def rfid_write(self):
        self.log_info(f"SLOT[{self.slot_num}] RFID write begin")
        success = self.mms_rfid.write()
        result = "success" if success else "failed"
        self.log_info(f"SLOT[{self.slot_num}] RFID write {result}")

    # ---- Detect ----
    def rfid_detect_begin(self):
        if self._is_detecting:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is already detecting"
            )
            return

        self.mms_rfid.set_duration(self.detect_duration)
        if not self.mms_rfid.detect_begin(callback=self._handle_detected):
            return False
        self._is_detecting = True
        self.detect_begin_at = printer_adapter.get_reactor().monotonic()
        self.log_info_s(
            f"slot[{self.slot_num}] RFID detect begin, "
            f"duration: {self.detect_duration}"
        )

    def rfid_detect_end(self):
        if not self._is_detecting:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is not detecting"
            )
            return

        self.mms_rfid.detect_end()
        self._is_detecting = False
        self.detect_end_at = printer_adapter.get_reactor().monotonic()
        self.log_info_s(f"slot[{self.slot_num}] RFID detect end")

    def _handle_detected(self, data):
        if data:
            self.rfid_detect_end()
            self.log_info(
                f"slot[{self.slot_num}] RFID detect data:\n"
                f"{data}"
            )

            self.tag_uid = data
            # Publish the handoff before stop() yields, so reset and flow
            # cleanup can revoke it while the interrupted motor exits.
            reactor = printer_adapter.get_reactor()
            timer = reactor.register_timer(self._start_detected_read)
            self._read_start_timer = timer
            success = self.mms_delivery.mms_stop(self.slot_num)
            if self._read_start_timer is timer:
                if success:
                    # Reuse the periodic task only after this callback exits.
                    reactor.update_timer(timer, reactor.NOW)
                else:
                    self._cancel_read_start()

        elif self._detect_is_timeout():
            self.rfid_detect_end()
            self.log_info_s(f"slot[{self.slot_num}] RFID detect timeout")

    def _start_detected_read(self, eventtime):
        self._cancel_read_start()
        try:
            self.rfid_read_begin()
        except Exception as e:
            self.log_error(f"slot[{self.slot_num}] RFID read start failed: {e}")
            self.rfid_read_end()
        return printer_adapter.get_reactor().NEVER

    def _cancel_read_start(self):
        if self._read_start_timer is not None:
            printer_adapter.get_reactor().unregister_timer(
                self._read_start_timer)
            self._read_start_timer = None

    def _detect_is_timeout(self):
        elapsed = (printer_adapter.get_reactor().monotonic()
                   - self.detect_begin_at)
        return elapsed > self.detect_duration

    def is_detecting(self):
        return self._is_detecting

    def detect_only_begin(self):
        if self._is_detecting:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is already only detecting")
            return False

        self.mms_rfid.set_duration(self.detect_duration)
        if not self.mms_rfid.detect_begin(callback=self._handle_detected_only):
            return False
        self._is_detecting = True
        self._initialize_tag()
        self.detect_begin_at = printer_adapter.get_reactor().monotonic()
        self.log_info_s(
            f"slot[{self.slot_num}] RFID only detect begin, "
            f"duration: {self.detect_duration}"
        )
        return True

    def detect_only_end(self):
        if not self._is_detecting:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is not only detecting")
            return False

        self.mms_rfid.detect_end()
        self._is_detecting = False
        self.detect_end_at = printer_adapter.get_reactor().monotonic()
        self.log_info_s(
            f"slot[{self.slot_num}] RFID only detect end")
        return True

    def _handle_detected_only(self, data):
        if data:
            self.rfid_detect_end()
            self.tag_uid = data
            self.log_info(
                f"slot[{self.slot_num}] RFID only detect data: {data}")

        elif self._detect_is_timeout():
            self.detect_only_end()
            self.log_info_s(
                f"slot[{self.slot_num}] RFID only detect timeout")

    def detect_tag(self):
        # Return tag UID or None
        if self.enable:
            return self.mms_rfid.rfid_manager.get_uid()
        return None

    def get_tag_uid(self):
        return self.tag_uid

    # ---- Read ----
    def rfid_read_begin(self):
        self._cancel_read_start()
        if self._is_reading:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is already reading"
            )
            return

        # Truncate existing RFID Tag data
        if self.has_tag_read():
            self._initialize_tag()

        self.mms_rfid.set_duration(self.read_duration)
        if not self.mms_rfid.read_begin(callback=self._handle_read):
            return False
        self._is_reading = True
        self.read_begin_at = printer_adapter.get_reactor().monotonic()
        self.log_info_s(
            f"slot[{self.slot_num}] RFID read begin, "
            f"duration: {self.read_duration}"
        )

        # Activate LED effect
        self.mms_slot.slot_led.activate_marquee()

    def rfid_read_end(self):
        self._cancel_read_start()
        if not self._is_reading:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is not reading"
            )
            return

        self.mms_rfid.read_end()
        self._is_reading = False
        self.read_end_at = printer_adapter.get_reactor().monotonic()
        self.log_info_s(f"slot[{self.slot_num}] RFID read end")

        # Deactivate LED effect
        self.mms_slot.slot_led.deactivate_marquee()

    def _handle_read(self, data):
        if data:
            self.rfid_read_end()
            self.log_info(
                f"slot[{self.slot_num}] RFID read data:\n"
                f"{data}"
            )

            try:
                self.tag_data = json.loads(data)
                self.tag_color = self.tag_data.get("color_code")
                self.mms_slot.slot_led.rfid_set_color(self.tag_color)
            except Exception as e:
                self.log_error(
                    f"slot[{self.slot_num}] RFID read tag data error: {e}")
            else:
                try:
                    self.mms_slot.apply_rfid_metadata(self.tag_data)
                except ValueError as e:
                    self.log_warning(
                        f"slot[{self.slot_num}] RFID filament metadata ignored: {e}")

        elif (printer_adapter.get_reactor().monotonic() - self.read_begin_at
              > self.read_duration):
            self.rfid_read_end()
            self.log_info(f"slot[{self.slot_num}] RFID read timeout")

    # ---- Flow ----
    def wait_read(self, should_stop):
        # RFID deliberately stops autoload to keep the tag at the reader.
        # Retain that task until reading ends; user cancellation still exits.
        reactor = printer_adapter.get_reactor()
        while self._read_start_timer is not None or self._is_reading:
            if should_stop():
                return
            if (self._is_reading
                    and reactor.monotonic() - self.read_begin_at
                    >= self.read_duration):
                return
            reactor.pause(reactor.monotonic() + 0.05)

    @contextmanager
    def execute(self):
        if self.enable:
            self.rfid_detect_begin()
        try:
            yield
        finally:
            if self.enable:
                if self._is_detecting:
                    self.rfid_detect_end()
                if self._is_reading or self._read_start_timer is not None:
                    self.rfid_read_end()

    @contextmanager
    def detect_only(self):
        if self.enable and not self.detect_only_begin():
            raise RuntimeError(f"slot[{self.slot_num}] RFID reader is busy")
        try:
            yield
        finally:
            if self.enable and self._is_detecting:
                self.detect_only_end()

    # ---- Truncate ----
    def rfid_truncate(self):
        self._initialize_tag()

    # ---- Reset ----
    def reset(self):
        if self._is_detecting:
            self.rfid_detect_end()
        if self._is_reading or self._read_start_timer is not None:
            self.rfid_read_end()

        if self.mms_rfid.is_detecting():
            self.mms_rfid.detect_end()
        if self.mms_rfid.is_reading():
            self.mms_rfid.read_end()

        try:
            self.mms_rfid.rfid_manager.handler.pcd_reset()
            self.log_info(f"slot[{self.slot_num}] RFID reset done")
        except Exception as e:
            self.log_error(f"slot[{self.slot_num}] RFID reset failed")

    # ---- Erase ----
