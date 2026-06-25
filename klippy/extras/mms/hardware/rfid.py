# Support for MMS RFID Reader: mfrc522
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field, fields

from ...bus import MCU_SPI_from_config

from .mfrc522 import (
    HashAssistant,
    MFRC522Handler,
    RFIDCache,
    RFIDModel
)
from ..adapters import gcode_adapter, printer_adapter
from ..core.task import PeriodicTask


@dataclass(frozen=True)
class RFIDEvent:
    """
    Event key string defined for RFID.
    """
    tag_detected: str = "rfid:tag:detected"
    tag_data: str = "rfid:tag:data"


@dataclass(frozen=True)
class RFIDConfig:
    # Must be first line, printer_config is the param of config object
    printer_config: object

    # Period of detect/read, in seconds
    period: float = 0.1
    # Timeout limit of detect/read, in seconds
    timeout: float = 60.0

    skip_configs = [
        "printer_config",
        "period",
        "timeout",
    ]
    # ==== configuration values in *.cfg, must set default  ====
    # Retreat distance after load to gate, in mm
    cs_pin: str = ""
    spi_bus: str = ""
    slots: str = ""
    rfid_data_file: str = ""

    def __post_init__(self):
        type_method_map = {
            str: "get",
            int: "getint",
            float: "getfloat",
            list: "getintlist",
        }

        for field_info in fields(self):
            field_name = field_info.name
            field_type = field_info.type

            if field_name in self.skip_configs:
                continue

            if field_name=="slots":
                self._parse_string_list(field_name="slots")
                continue

            # Default type is str
            get_method = type_method_map.get(field_type, "get")
            config_value = getattr(self.printer_config, get_method)(field_name)

            object.__setattr__(self, field_name, config_value)

    def _parse_string_list(self, field_name):
        val_str = self.printer_config.get(field_name) or ""
        lst = [int(val.strip()) for val in val_str.split(",") if val.isdigit()]
        return lst


class RFIDManager:
    def __init__(self, spi):
        self.handler = MFRC522Handler(spi)
        self.hash_assistant = HashAssistant()

        self._initialize_loggers()

        # Default max_size=16
        cache_max_size = 32
        self.cache = RFIDCache(max_size=cache_max_size)
        # self.retry_times = 10

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    def new_rfid_model(self):
        return RFIDModel()

    def to_string(self, block_data):
        return self.handler.format_block_data(block_data)

    @contextmanager
    def use_antenna(self):
        with self.handler.antenna_manager():
            yield

    def get_version(self):
        with self.use_antenna():
            return hex(self.handler.get_version()).upper().zfill(2)
        # self.log_info(f"Firmware Version: {version}")

    def get_uid(self):
        with self.use_antenna():
            return self.handler.read_uid()

    def read_with_uid(self, uid):
        with self.use_antenna():
            self.handler.picc_select(uid)

            # Read the Sector 15 to get hash data, prepare have done before
            sector_15_lst = self.handler.read_sector(uid=uid, sector_num=15)
            sector_15_lst.sort(key=lambda tup: tup[0])
            # Filter block 60 & block 61 data
            blocks_lst = list(filter(lambda tup: tup[0] in [60, 61],
                                     sector_15_lst))

            # Block data to string
            hash_read = self.hash_assistant.block_to_string(blocks_lst)
            self.log_info_s(f"hash_read: {hash_read}")

    def rfid_read(self):
        """
        Modified rfid_read to be more flexible.
        Returns BTT JSON if valid, otherwise returns a dictionary with raw data for fallback.
        """
        with self.use_antenna():
            uid = self.handler.prepare_loop()
            if not uid:
                return None

            uid_s = self.handler.format_block_data(uid)

            # Try to read Sector 15 for BTT Hash
            sector_15_lst = self.handler.read_sector(uid=uid, sector_num=15)
            if sector_15_lst:
                sector_15_lst.sort(key=lambda tup: tup[0])
                blocks_lst = list(filter(lambda tup: tup[0] in [60, 61],
                                         sector_15_lst))
                hash_read = self.hash_assistant.block_to_string(blocks_lst)
                
                if hash_read and self.hash_assistant.is_valid_length(hash_read):
                    # It looks like a BTT tag, try standard BTT read logic
                    cache_key = self.cache.gen_key(uid_s)
                    blocks_cached = self.cache.get(cache_key)
                    if blocks_cached:
                        blocks_hash = list(filter(lambda tup: tup[0] in [60, 61], blocks_cached))
                        hash_cached = self.hash_assistant.block_to_string(blocks_hash)
                        if hash_read == hash_cached:
                            cache_key = self.cache.gen_key(uid_s, prefix="rfid_dict")
                            return self.cache.get(cache_key)

                    # Reload/Read full blocks for BTT
                    blocks_read = self.handler.read_all_loop(uid)
                    if blocks_read:
                        blocks_read.sort(key=lambda tup: tup[0])
                        data_string = self.hash_assistant.block_to_string(blocks_read[:60])
                        hash_calculate = self.hash_assistant.hash_as_string(data_string)
                        if hash_read == hash_calculate:
                            blocks_dct = {str(tup[0]):tup[1].replace(" ", "") for tup in blocks_read}
                            rfid_model = self.new_rfid_model()
                            rfid_model.from_blocks(blocks_dct)
                            rfid_model_json = rfid_model.to_json()
                            self.cache.add(self.cache.gen_key(uid_s), blocks_read)
                            self.cache.add(self.cache.gen_key(uid_s, prefix="rfid_dict"), rfid_model_json)
                            return rfid_model_json

            # If BTT check failed or wasn't a MIFARE tag, try NTAG (OpenPrintTag)
            ntag_data = self.handler.read_ntag_loop()
            if ntag_data:
                return json.dumps({"_type": "ntag_raw", "data": ntag_data.hex()})

            return None

    def rfid_write_block(self, block_num, byte_array):
        with self.use_antenna():
            # Write single block
            uid = self.handler.prepare_loop()
            if not uid:
                return False

            uid_s = self.handler.format_block_data(uid)
            self.log_info_s(f"Card UID: {uid_s}")

            # block_num = 16
            # byte_array = [0x00,] * 16
            self.handler.write_single_block(uid, block_num, byte_array)

            uid = self.handler.prepare_loop()
            if uid:
                blocks_read = self.handler.read_single_block(uid, block_num)
                if blocks_read:
                    self.log_info_s(f"Block {block_num}: {blocks_read}")
                    return True

            return False

    def rfid_write_hash(self):
        with self.use_antenna():
            # Calculate hash block data and write into block 60/61
            uid = self.handler.prepare_loop()
            if not uid:
                return

            uid_s = self.handler.format_block_data(uid)
            self.log_info_s(f"Card UID: {uid_s}")

            sha256_data_lst = self.handler.cal_blocks_sha256(uid)

            block_num = 60
            data = sha256_data_lst[:16]
            self.handler.prepare_loop()
            self.handler.write_single_block(uid, block_num, data)

            block_num = 61
            data = sha256_data_lst[16:]
            self.handler.prepare_loop()
            self.handler.write_single_block(uid, block_num, data)

    def get_tags(self):
        with self.use_antenna():
            return self.handler.read_tags()


class MMSRfid:
    """
    Printer class that controls RFID sensor
    """
    def __init__(self, config):
        self.spi = MCU_SPI_from_config(
            config=config,
            mode=0,
            pin_option="cs_pin",
            default_speed=5000000,
            share_type=None,
            cs_active_high=False)

        self.name = config.get_name().split()[-1]
        self._is_detecting = False
        self._is_reading = False

        self.rfid_config = RFIDConfig(config)
        # Parse params
        self._parse_config()

        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)

    def _parse_config(self):
        """Parse common configuration"""
        vars_list = [
            # "slots",
            "rfid_data_file",
            "period",
            "timeout",
        ]
        for var in vars_list:
            setattr(self, var, getattr(self.rfid_config, var))

    def _handle_klippy_connect(self):
        self._initialize_loggers()
        self._initialize_gcode()
        self._initialize_task()
        self._initialize_manager()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    def _initialize_gcode(self):
        gcode_adapter.register_mux(
            cmd = "MMS_RFID_DETECT_DEV",
            key = "NAME", value = self.name,
            func = self.cmd_MMS_RFID_DETECT
        )
        gcode_adapter.register_mux(
            cmd = "MMS_RFID_READ_DEV",
            key = "NAME", value = self.name,
            func = self.cmd_MMS_RFID_READ
        )
        # gcode_adapter.register_mux(
        #     cmd = "MMS_RFID_READ_TAGS",
        #     key = "NAME", value = self.name,
        #     func = self.cmd_MMS_RFID_READ_TAGS
        # )

    def _initialize_task(self):
        self.periodic_task = PeriodicTask()
        self.periodic_task.set_period(self.period)
        self.periodic_task.set_timeout(self.timeout)

    def _initialize_manager(self):
        self.rfid_manager = RFIDManager(self.spi)

    def set_duration(self, duration):
        self.periodic_task.set_timeout(duration)

    # ---- Tag write ----
    def write_ntag(self, data_json):
        try:
            data = json.loads(data_json)
        except Exception as e:
            self.log_error(f"JSON decode error: {e}")
            return False

        from .openprinttag import OPTEncoder
        encoder = OPTEncoder()
        opt_data = encoder.map_from_mms(data)
        ndef_payload = encoder.encode(opt_data)

        self.log_info(f"RFID[{self.name}] writing NTAG with payload length: {len(ndef_payload)}")

        with self.rfid_manager.use_antenna():
            success = self.rfid_manager.handler.write_ntag_loop(ndef_payload, start_page=4)

        if success:
            self.log_info(f"RFID[{self.name}] NTAG write successful")
            return True
        else:
            self.log_error(f"RFID[{self.name}] NTAG write failed")
            return False

    # ---- Tag detect ----
    def detect_begin(self, callback):
        if self._is_detecting:
            self.log_warning(
                f"RFID[{self.name}] detect is already running")
            return False

        try:
            is_ready = self.periodic_task.schedule(
                func=self.rfid_manager.get_uid,
                callback=callback,
                timeout_callback=self._handle_detect_timeout
            )
            if not is_ready:
                self.log_warning(
                    f"RFID[{self.name}] detect schedule failed")
                return False

            ret = self.periodic_task.start()
            if not ret:
                self.log_error(
                    f"RFID[{self.name}] detect begin failed")
                return False

            self._is_detecting = True
            self.log_info_s(
                f"RFID[{self.name}] detect initiated in the backend")
            return True

        except Exception as e:
            self.log_error(f"RFID[{self.name}] detect_begin error:{e}")

    def detect_end(self):
        if not self._is_detecting:
            self.log_warning(
                f"RFID[{self.name}] detect is not running")
            return False

        try:
            ret = self.periodic_task.stop()
            if not ret:
                self.log_warning(
                    f"RFID[{self.name}] detect stop failed")
                return False

            self._is_detecting = False
            self.log_info_s(
                f"RFID[{self.name}] detect terminated in the backend")
            return True

        except Exception as e:
            self.log_error(f"RFID[{self.name}] detect_end error:{e}")

    def handle_detected(self, data):
        if data and self.detect_end():
            uid = self.rfid_manager.to_string(block_data=data)
            self.log_info(
                f"RFID[{self.name}] detect Tag uid:\n"
                f"{uid}"
            )

    def _handle_detect_timeout(self):
        self.log_info(f"RFID[{self.name}] detect timeout")
        self._is_detecting = False

    def is_detecting(self):
        return self._is_detecting

    # ---- Tag read ----
    def read_begin(self, callback):
        if self._is_reading:
            self.log_warning(
                f"RFID[{self.name}] read is already running")
            return False

        try:
            is_ready = self.periodic_task.schedule(
                func=self.rfid_manager.rfid_read,
                callback=callback,
                timeout_callback=self._handle_read_timeout
            )
            if not is_ready:
                self.log_warning(
                    f"RFID[{self.name}] read schedule failed")
                return False

            ret = self.periodic_task.start()
            if not ret:
                self.log_error(
                    f"RFID[{self.name}] read begin failed")
                return False

            self._is_reading = True
            self.log_info(
                f"RFID[{self.name}] read initiated in the backend")

        except Exception as e:
            self.log_error(f"RFID[{self.name}] read_begin error:{e}")

    def read_end(self):
        if not self._is_reading:
            self.log_warning(
                f"RFID[{self.name}] read is not running")
            return False

        try:
            ret = self.periodic_task.stop()
            if not ret:
                self.log_warning(
                    f"RFID[{self.name}] read stop failed")
                return False

            self._is_reading = False
            self.log_info(
                f"RFID[{self.name}] read terminated in the backend")
            return True

        except Exception as e:
            self.log_error(f"RFID[{self.name}] read_end error:{e}")

    def _handle_read(self, data):
        if data and self.read_end():
            self.log_info(
                f"RFID[{self.name}] read data:\n"
                f"{data}"
            )

    def _handle_read_timeout(self):
        self.log_info(f"RFID[{self.name}] read timeout")
        self._is_reading = False

    def is_reading(self):
        return self._is_reading

    # ---- Dev ----
    def get_tags_begin(self, callback):
        func = self.rfid_manager.get_tags

        try:
            is_ready = self.periodic_task.schedule(
                func=func, callback=callback)

            if is_ready:
                ret = self.periodic_task.start()
                if ret:
                    self.log_info(
                        f"RFID[{self.name}] get tags initiated in the backend"
                    )
                else:
                    self.log_error(f"RFID[{self.name}] get tags begin failed")
            else:
                self.log_warning(f"RFID[{self.name}] get tags"
                                 f" is already running")
        except Exception as e:
            self.log_error(f"RFID[{self.name}] get_tags_begin error:{e}")

    def get_tags_end(self):
        try:
            ret = self.periodic_task.stop()
            if ret:
                self.log_info(
                    f"RFID[{self.name}] get tags terminated in the backend"
                )
            else:
                self.log_warning(f"RFID[{self.name}] get tags is not running")
            return ret
        except Exception as e:
            self.log_error(f"RFID[{self.name}] get_tags_end error:{e}")

    # def reset(self):
    #     if self._is_detecting:
    #         self.detect_end()
    #     if self._is_reading:
    #         self.read_end()

    # ---- GCode commands ----
    def cmd_MMS_RFID_DETECT(self, gcmd):
        """
        Usage:
            MMS_RFID_DETECT_DEV NAME=mfrc522_0 SWITCH=0/1
        """
        switch = gcmd.get_int("SWITCH", 0)
        if switch == 1:
            self.detect_begin(callback=self.handle_detected)
        else:
            self.detect_end()

    def cmd_MMS_RFID_READ(self, gcmd):
        """
        Usage:
            MMS_RFID_READ_DEV NAME=mfrc522_0 SWITCH=0/1
        """
        switch = gcmd.get_int("SWITCH", 0)
        if switch == 1:
            self.read_begin(callback=self._handle_read)
        else:
            self.read_end()

    # def cmd_MMS_RFID_READ_TAGS(self, gcmd):
    #     self.rfid_manager.get_tags()


def load_config(config):
    return MMSRfid(config)
