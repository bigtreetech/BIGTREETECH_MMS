# Support for MMS SLOT RFID
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import time
import traceback
from contextlib import contextmanager

from ..adapters import printer_adapter
from ..hardware.openprinttag import OPTDecoder


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

    def is_reading(self):
        if self.mms_rfid:
            return self.mms_rfid.is_reading()
        return False

    # ---- Write ----
    def align_and_write(self, data):
        mms = printer_adapter.get_mms()
        # Safety checks
        if mms.printer_is_printing():
            self.log_error(f"slot[{self.slot_num}] printer is printing, write operation blocked")
            return False
            
        if self.mms_slot.gate.is_triggered():
            self.log_error(f"slot[{self.slot_num}] filament is loaded past gate, write operation blocked")
            return False
            
        if not self.mms_slot.inlet.is_triggered():
            self.log_error(f"slot[{self.slot_num}] slot is empty, write operation blocked")
            return False
            
        self.log_info(f"slot[{self.slot_num}] aligning RFID tag...")
        
        from .exceptions import DeliveryTerminateSignal
        
        self.tag_uid = None
        self.detect_only_begin()
        
        mms_delivery = printer_adapter.get_mms_delivery()
        try:
            # Safely move BACKWARD until the inlet releases or we move 800mm
            mms_delivery._unload_to_release(
                self.slot_num, 
                self.mms_slot.pin_type.inlet, 
                distance=800, 
                speed=40
            )
        except DeliveryTerminateSignal:
            pass # tag was found and movement stopped by _handle_detected_only
        except Exception as e:
            self.log_warning(f"slot[{self.slot_num}] align interrupted: {e}")
            
        self.detect_only_end()
        
        success = False
        if not self.tag_uid:
            self.log_warning(f"slot[{self.slot_num}] RFID tag not detected during alignment")
        else:
            self.log_info(f"slot[{self.slot_num}] RFID tag aligned, starting write...")
            # Write IMMEDIATELY while the tag is at the antenna
            success = self.rfid_write(data)

        # NOW restore filament to the standard "Preload" position
        try:
            self.log_info(f"slot[{self.slot_num}] alignment sequence finished, restoring filament...")
            mms_delivery.autoload_to_gate(self.slot_num)
        except Exception as e:
            self.log_warning(f"slot[{self.slot_num}] failed to restore filament position: {e}")
            
        return success

    def rfid_write(self, data):
        self.log_info(f"SLOT[{self.slot_num}] RFID write begin")
        success = self.mms_rfid.write_ntag(data)
        result = "success" if success else "failed"
        self.log_info(f"SLOT[{self.slot_num}] RFID write {result}")
        return success

    # ---- Detect ----
    def rfid_detect_begin(self):
        if self._is_detecting:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is already detecting"
            )
            return

        self.log_info(f"slot[{self.slot_num}] RFID automatic detection started")
        self._is_detecting = True
        self.detect_begin_at = time.time()
        self.mms_rfid.set_duration(self.detect_duration)
        self.mms_rfid.detect_begin(callback=self._handle_detected)
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
        self.detect_end_at = time.time()
        self.log_info_s(f"slot[{self.slot_num}] RFID detect end")

    def _handle_detected(self, data):
        if data:
            self.rfid_detect_end()
            # Format UID as Hex for better readability
            if isinstance(data, (list, bytes, bytearray)):
                uid_hex = " ".join([f"{b:02X}" for c in [data] for b in c])
            else:
                uid_hex = str(data)
                
            self.log_info(
                f"slot[{self.slot_num}] RFID detect data (UID): {uid_hex}"
            )

            self.tag_uid = data
            # Start read phase regardless
            self.rfid_read_begin()

            # Make stop robust: even if it fails, try to start reading
            try:
                self.mms_delivery.mms_stop(self.slot_num)
            except Exception:
                self.log_info_s(f"slot[{self.slot_num}] mms_stop error (ignored): {traceback.format_exc()}")

        elif self._detect_is_timeout():
            self.rfid_detect_end()
            self.log_info_s(f"slot[{self.slot_num}] RFID detect timeout")

    def _detect_is_timeout(self):
        return time.time()-self.detect_begin_at > self.detect_duration

    def is_detecting(self):
        return self._is_detecting

    def detect_only_begin(self):
        if self._is_detecting:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is already only detecting")
            return

        self._is_detecting = True
        self._initialize_tag()
        self.detect_begin_at = time.time()

        self.mms_rfid.set_duration(self.detect_duration)
        self.mms_rfid.detect_begin(callback=self._handle_detected_only)
        self.log_info_s(
            f"slot[{self.slot_num}] RFID only detect begin, "
            f"duration: {self.detect_duration}"
        )

    def detect_only_end(self):
        if not self._is_detecting:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is not only detecting")
            return False

        self.mms_rfid.detect_end()
        self._is_detecting = False
        self.detect_end_at = time.time()
        self.log_info_s(
            f"slot[{self.slot_num}] RFID only detect end")
        return True

    def _handle_detected_only(self, data):
        if data:
            self.detect_only_end()
            self.tag_uid = data
            self.log_info(
                f"slot[{self.slot_num}] RFID only detect data: {data}")
            
            try:
                mms_delivery = printer_adapter.get_mms_delivery()
                mms_delivery.mms_stop(self.slot_num)
            except Exception:
                pass

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
        if self._is_reading:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is already reading"
            )
            return

        # Truncate existing RFID Tag data
        if self.has_tag_read():
            self._initialize_tag()

        self._is_reading = True
        self.read_begin_at = time.time()
        self.mms_rfid.set_duration(self.read_duration)
        self.mms_rfid.read_begin(callback=self._handle_read)
        self.log_info_s(
            f"slot[{self.slot_num}] RFID read begin, "
            f"duration: {self.read_duration}"
        )

        # Activate LED effect
        self.mms_slot.slot_led.activate_marquee()

    def rfid_read_end(self):
        if not self._is_reading:
            self.log_warning(
                f"slot[{self.slot_num}] RFID is not reading"
            )
            return

        self.mms_rfid.read_end()
        self._is_reading = False
        self.read_end_at = time.time()
        self.log_info_s(f"slot[{self.slot_num}] RFID read end")

        # Deactivate LED effect
        self.mms_slot.slot_led.deactivate_marquee()

    def _handle_read(self, data):
        if data:
            self.rfid_read_end()
            try:
                tag_dict = json.loads(data)
                # 1. Standard BTT Tag (JSON contains filament details directly)
                if "filament_material_type" in tag_dict:
                    self.log_info(f"slot[{self.slot_num}] BTT tag detected")
                    self.tag_data = tag_dict
                    self.tag_color = self.tag_data.get("color_code")
                    self._apply_tag_data()
                
                # 2. Universal Read Fallback (contains raw ntag data)
                elif tag_dict.get("_type") == "ntag_raw":
                    raw_hex = tag_dict.get("data", "")
                    self.log_info(f"slot[{self.slot_num}] NTAG chip detected, trying OpenPrintTag decoder...")
                    raw_data = bytes.fromhex(raw_hex)
                    
                    decoder = OPTDecoder()
                    opt_data = decoder.decode(raw_data)
                    if opt_data:
                        self.log_info(f"slot[{self.slot_num}] OpenPrintTag decoded: {opt_data}")
                        mapped = decoder.map_to_mms(opt_data)
                        if mapped:
                            self.tag_data = mapped
                            self.tag_color = mapped.get("color_code")
                            self._apply_tag_data()
                    else:
                        self.log_info(f"slot[{self.slot_num}] Decoding Failed / Blank TAG Detected")
                
                else:
                    self.log_warning(f"slot[{self.slot_num}] Unknown tag data format: {data[:100]}...")

            except Exception as e:
                self.log_error(f"slot[{self.slot_num}] RFID processing error: {e}")

        elif time.time()-self.read_begin_at > self.read_duration:
            self.rfid_read_end()
            self.log_info(f"slot[{self.slot_num}] RFID read timeout")

    def _try_openprinttag_read(self):
        try:
            # OPT usually uses NTAG/Type 2 which doesn't need auth per sector
            raw_data = self.mms_rfid.rfid_manager.handler.read_ntag_loop()
            if not raw_data:
                self.log_error(f"slot[{self.slot_num}] Failed to read NTAG data")
                return

            decoder = OPTDecoder()
            opt_data = decoder.decode(raw_data)
            if opt_data:
                self.log_info(f"slot[{self.slot_num}] OpenPrintTag decoded: {opt_data}")
                mapped = decoder.map_to_mms(opt_data)
                if mapped:
                    self.tag_data = mapped
                    self.tag_color = mapped.get("color_code")
                    self._apply_tag_data()
            else:
                self.log_info(f"slot[{self.slot_num}] Decoding Failed / Blank TAG Detected")
        except Exception as e:
            self.log_error(f"slot[{self.slot_num}] OpenPrintTag read error: {e}")

    def _apply_tag_data(self):
        if not self.tag_data: return
        
        self.mms_slot.set_filament_color(self.tag_color)
        self.mms_slot.set_filament_material(
            self.tag_data.get("filament_material_type")
        )
        self.mms_slot.set_filament_info(self.tag_data)

        mms = printer_adapter.get_mms()
        if mms:
            mms.notify_lane_data_changed([self.slot_num])

        # Set LED color
        self.mms_slot.slot_led.rfid_set_color(self.tag_color)

    # ---- Flow ----
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

    @contextmanager
    def detect_only(self):
        if self.enable:
            self.detect_only_begin()
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
            if not self.detect_only_end():
                self.detect_end()
        if self._is_reading:
            self.read_end()

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
