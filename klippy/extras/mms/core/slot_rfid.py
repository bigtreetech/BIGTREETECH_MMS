# Support for MMS SLOT RFID
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import time
from contextlib import contextmanager

from ..adapters import printer_adapter


class SlotRFID:
    def __init__(self, mms_slot):
        self.mms_slot = mms_slot
        # SLOT meta
        self.slot_num = mms_slot.get_num()
        # Logger
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=False)
        self.log_error = mms_logger.create_log_error(console_output=True)

        # Setup later
        self.name = None
        self.enable = None
        self.detect_duration = None
        self.read_duration = None

        self.mms_rfid = None

        # Status
        self._is_detecting = False
        self._is_reading = False

        self.detect_begin_at = None
        self.detect_end_at = None
        self.read_begin_at = None
        self.read_end_at = None

        # Tag data
        self._init_tag()

    def _init_tag(self):
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

            "tag_uid": self.tag_uid,
            "tag_color": self.tag_color,
        }

    def has_tag_read(self):
        return self.tag_color is not None

    # ---- Detect ----
    def rfid_detect_begin(self):
        if self._is_detecting:
            self.log_info(f"slot[{self.slot_num}] RFID is detecting")
            return

        self._is_detecting = True
        self.detect_begin_at = time.time()
        self.mms_rfid.detect_begin(callback=self.handle_detected)
        self.log_info(f"slot[{self.slot_num}] RFID detect begin")

    def rfid_detect_end(self):
        if not self._is_detecting:
            self.log_info(f"slot[{self.slot_num}] RFID is not detecting")
            return

        self.mms_rfid.detect_end()
        self._is_detecting = False
        self.detect_end_at = time.time()
        self.log_info(f"slot[{self.slot_num}] RFID detect end")

    def handle_detected(self, data):
        if data:
            self.rfid_detect_end()
            self.log_info(f"slot[{self.slot_num}] RFID detect: {data}")

            self.tag_uid = data
            success = self.mms_delivery.mms_stop(self.slot_num)
            if success:
                self.rfid_read_begin()

        elif time.time()-self.detect_begin_at > self.detect_duration:
            self.rfid_detect_end()
            self.log_info(f"slot[{self.slot_num}] RFID detect timeout")

    # ---- Read ----
    def rfid_read_begin(self):
        if self._is_reading:
            self.log_info(f"slot[{self.slot_num}] RFID is reading")
            return

        # Truncate existing RFID Tag data
        if self.has_tag_read():
            self._init_tag()

        self._is_reading = True
        self.read_begin_at = time.time()
        self.mms_rfid.read_begin(callback=self.handle_read)
        self.log_info(f"slot[{self.slot_num}] RFID read begin")

        # Activate LED effect
        self.mms_slot.slot_led.activate_marquee()

    def rfid_read_end(self):
        if not self._is_reading:
            self.log_info(f"slot[{self.slot_num}] RFID is not reading")
            return

        self.mms_rfid.read_end()
        self._is_reading = False
        self.read_end_at = time.time()
        self.log_info(f"slot[{self.slot_num}] RFID read end")

        # Deactivate LED effect
        self.mms_slot.slot_led.deactivate_marquee()

    def handle_read(self, data):
        if data:
            self.rfid_read_end()
            self.log_info(f"slot[{self.slot_num}] RFID read: {data}")

            try:
                self.tag_data = json.loads(data)
                self.tag_color = self.tag_data.get("color_code")
                # Alter LED color
                self.mms_slot.slot_led.change_color(self.tag_color)
            except Exception as e:
                self.log_error(
                    f"slot[{self.slot_num}] RFID read tag data error: {e}")

            # Continue delivery
            self.mms_delivery.mms_prepare(self.slot_num)

        elif time.time()-self.read_begin_at > self.read_duration:
            self.rfid_read_end()
            self.log_info(f"slot[{self.slot_num}] RFID read timeout")

            # Continue delivery
            self.mms_delivery.mms_prepare(self.slot_num)

    # ---- Utils ----
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
                if self._is_reading:
                    self.rfid_read_end()
