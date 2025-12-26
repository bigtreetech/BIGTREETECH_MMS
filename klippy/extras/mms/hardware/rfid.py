# Support for MMS RFID Reader: mfrc522
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
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
        self.log_info = mms_logger.create_log_info(console_output=False)
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()

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
            self.log_info(f"hash_read: {hash_read}")

    def rfid_read(self):
        """
        hash_read
            hash data read from Tag
        hash_cached
            hash data read from Cached
        hash_calculate
            hash data calculate from full blocks data read from Tag
        """
        with self.use_antenna():
            uid = self.handler.prepare_loop()
            if not uid:
                self.log_info("No Tag, return")
                return

            uid_s = self.handler.format_block_data(uid)
            # self.log_info(f"Tag uid={uid_s}")

            # Read the Sector 15 to get hash data, prepare have done before
            sector_15_lst = self.handler.read_sector(uid=uid, sector_num=15)
            sector_15_lst.sort(key=lambda tup: tup[0])
            # Filter block 60 & block 61 data
            blocks_lst = list(filter(lambda tup: tup[0] in [60, 61],
                                     sector_15_lst))

            # Block data to string
            hash_read = self.hash_assistant.block_to_string(blocks_lst)
            self.log_info(f"hash_read: {hash_read}")

            # Validation schema
            if not hash_read:
                self.log_error(f"Hash block read error with UID: {uid_s}")
                return

            if not self.hash_assistant.is_valid_length(hash_read):
                self.log_error(f"The hash data has wrong length: {hash_read}")
                return

            if not self.hash_assistant.is_hexadecimal(hash_read):
                self.log_error(f"The hash data is not hex: {hash_read}")
                return

            if self.hash_assistant.has_high_zero_ratio(hash_read):
                self.log_error(f"The hash data has high zero ratio:"
                               f" {hash_read}")
                return

            # Get cached blocks data by uid string
            cache_key = self.cache.gen_key(uid_s)
            blocks_cached = self.cache.get(cache_key)
            # Reload flag init False
            need_reload = False

            if blocks_cached:
                self.log_info("cache load")

                # If cached blocks exists, find the cached block 60/61 first
                blocks_cached.sort(key=lambda tup: tup[0])
                blocks_hash = list(
                    filter(lambda tup: tup[0] in [60, 61], blocks_cached))

                hash_cached = self.hash_assistant.block_to_string(blocks_hash)
                self.log_info(f"hash_cached: {hash_cached}")

                if hash_read == hash_cached:
                    # Read and cached hash data are the same, return cached data
                    self.log_info("cached found and hash match, return"
                                  " blocks cached")
                    # for i,data in blocks_cached:
                    #     self.log_info(f"Block {i}: {data}")

                    cache_key = self.cache.gen_key(uid_s, prefix="rfid_dict")
                    rfid_model_json = self.cache.get(cache_key)
                    # self.log_info(rfid_model_json)

                    return rfid_model_json

                else:
                    # Read and cached hash data are different
                    # reload new block data
                    self.log_info("cache not the same, reload")
                    need_reload = True
            else:
                # No cached found, reload new block data
                self.log_info("init load...")
                need_reload = True

            if need_reload:
                # Reload begin, prepare before read
                uid_new = self.handler.prepare_loop()
                if not uid_new:
                    self.log_info("no Tag, reload failed, exit")
                    return

                uid_new_s = self.handler.format_block_data(uid_new)

                # If new UID is not the same UID of begin,
                # a new Tag collision problem may happen, exit
                if uid_new_s != uid_s:
                    self.log_info(f"UID begin: {uid_s}")
                    self.log_info(f"UID current: {uid_new_s}")
                    self.log_info(f"found different UID, reload failed, exit")
                    return

                # Read full blocks data
                blocks_read = self.handler.read_all_loop(uid)

                if blocks_read:
                    # Calculate and check hash_block is valid

                    # Get the data from block 0 to block 59
                    blocks_read.sort(key=lambda tup: tup[0])
                    data_string = (
                        self.hash_assistant.block_to_string(blocks_read[:60]))
                    hash_calculate = (
                        self.hash_assistant.hash_as_string(data_string))
                    self.log_info(f"hash_calculate: {hash_calculate}")

                    # Validation check
                    if hash_read != hash_calculate:
                        self.log_error("read hash block data not equal to"
                                       " calculated, exit")
                        return

                    # Cached the full blocks data
                    cache_key = self.cache.gen_key(uid_s)
                    self.cache.add(cache_key, blocks_read)
                    self.log_info(f"RFID data success cached with UID: {uid_s}")
                    # for i,data in blocks_read:
                    #     self.log_info(f"Block {i}: {data}")

                    blocks_dct = {
                        str(tup[0]):tup[1].replace(" ", "")
                        for tup in blocks_read
                    }
                    # self.log_info(f"blocks_dct: {blocks_dct}")

                    rfid_model = self.new_rfid_model()
                    rfid_model.from_blocks(blocks_dct)
                    rfid_model_json = rfid_model.to_json()

                    cache_key = self.cache.gen_key(uid_s, prefix="rfid_dict")
                    self.cache.add(cache_key, rfid_model_json)
                    # self.log_info(rfid_model_json)

                    return rfid_model_json

                else:
                    self.log_info("failed to Read all blocks data while"
                                  " reloading, exit")

            return

    def rfid_write_block(self, block_num, byte_array):
        with self.use_antenna():
            # Write single block
            uid = self.handler.prepare_loop()
            if not uid:
                return

            uid_s = self.handler.format_block_data(uid)
            self.log_info(f"Card UID: {uid_s}")

            # block_num = 16
            # byte_array = [0x00,] * 16
            self.handler.write_single_block(uid, block_num, byte_array)

            uid = self.handler.prepare_loop()
            if uid:
                blocks_read = self.handler.read_single_block(uid, block_num)
                if blocks_read:
                    self.log_info(f"Block {block_num}: {blocks_read}")

    def rfid_write_hash(self):
        with self.use_antenna():
            # Calculate hash block data and write into block 60/61
            uid = self.handler.prepare_loop()
            if not uid:
                return

            uid_s = self.handler.format_block_data(uid)
            self.log_info(f"Card UID: {uid_s}")

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
        self.is_detecting = False
        self.is_reading = False

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
        self.log_info = mms_logger.create_log_info(console_output=False)
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()

    def _initialize_gcode(self):
        gcode_adapter.register_mux(cmd = "MMS_RFID_DETECT",
            key = "NAME", value = self.name, func = self.cmd_RFID_detect)
        gcode_adapter.register_mux(cmd = "MMS_RFID_READ",
            key = "NAME", value = self.name, func = self.cmd_RFID_read)
        gcode_adapter.register_mux(cmd = "MMS_RFID_WRITE",
            key = "NAME", value = self.name, func = self.cmd_RFID_write)
        gcode_adapter.register_mux(cmd = "MMS_RFID_READ_TAGS",
            key = "NAME", value = self.name, func = self.cmd_RFID_read_tags)

    def _initialize_task(self):
        self.periodic_task = PeriodicTask()
        self.periodic_task.set_period(self.period)
        self.periodic_task.set_timeout(self.timeout)

    def _initialize_manager(self):
        self.rfid_manager = RFIDManager(self.spi)

    def write(self):
        # Most likely return "/home/.../printer_data/config/printer.cfg"
        cfg_path = printer_adapter.get_klippy_configfile()
        # base_dir should be "/home/.../printer_data/config/"
        base_dir = os.path.dirname(cfg_path)
        # filename should be "rfid_write.json"
        filename = os.path.basename(self.rfid_data_file)

        json_data = None
        for root, _, files in os.walk(base_dir):
            if filename in files:
                full_path = os.path.join(root, filename)
                if self.rfid_data_file in full_path:
                    self.log_info(f"write data from file:{full_path}")
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        json_data = json.loads(content)
                    except json.JSONDecodeError as e:
                        self.log_error(f"JSON decode error ({full_path}): {e}")
                    except Exception as e:
                        self.log_error(f"open file error {full_path}: {e}")

        # full_path = os.path.join(base_dir, self.rfid_data_file)
        # try:
        #     with open(full_path, 'r', encoding='utf-8') as f:
        #         content = f.read()
        #     json_data = json.loads(content)
        # except json.JSONDecodeError as e:
        #     self.log_error(f"JSON decode error ({full_path}): {e}")
        # except Exception as e:
        #     self.log_error(f"open file error {full_path}: {e}")

        if json_data:
            rfid_model = self.rfid_manager.new_rfid_model()
            rfid_model.from_dict(json_data)
            # Log data
            data_encode_json = rfid_model.to_json()
            self.log_info(f"data_encode_json: {data_encode_json}")

            # Write to tag
            prepared_blocks = rfid_model.prepare_blocks_writing()

            for block_num, byte_array in prepared_blocks.items():
                self.rfid_manager.rfid_write_block(block_num, byte_array)

            self.rfid_manager.rfid_write_hash()

    def detect_begin(self, callback):
        func = self.rfid_manager.get_uid

        try:
            is_ready = self.periodic_task.schedule(func=func, callback=callback)
            if is_ready:
                ret = self.periodic_task.start()
                if ret:
                    self.is_detecting = True
                    self.log_info(f"RFID[{self.name}] detect initiated"
                                  f" in the backend")
                else:
                    self.log_error(f"RFID[{self.name}] detect begin failed")
            else:
                self.log_warning(f"RFID[{self.name}] detect is already running")
        except Exception as e:
            self.log_error(f"RFID[{self.name}] detect_begin error:{e}")

    def detect_end(self):
        try:
            ret = self.periodic_task.stop()
            if ret:
                self.is_detecting = False
                self.log_info(f"RFID[{self.name}] detect terminated"
                              f" in the backend")
            else:
                self.log_warning(f"RFID[{self.name}] detect is not running")

            return ret
        except Exception as e:
            self.log_error(f"RFID[{self.name}] detect_end error:{e}")

    def read_begin(self, callback):
        func = self.rfid_manager.rfid_read

        try:
            is_ready = self.periodic_task.schedule(func=func, callback=callback)
            if is_ready:
                ret = self.periodic_task.start()
                if ret:
                    self.is_reading = True
                    self.log_info(f"RFID[{self.name}] read initiated"
                                  f" in the backend")
                else:
                    self.log_error(f"RFID[{self.name}] read begin failed")
            else:
                self.log_warning(f"RFID[{self.name}] read is already running")

        except Exception as e:
            self.log_error(f"RFID[{self.name}] read_begin error:{e}")

    def read_end(self):
        try:
            ret = self.periodic_task.stop()
            if ret:
                self.is_reading = False
                self.log_info(f"RFID[{self.name}] read terminated"
                              f" in the backend")
            else:
                self.log_warning(f"RFID[{self.name}] read is not running")

            return ret
        except Exception as e:
            self.log_error(f"RFID[{self.name}] read_end error:{e}")

    def _handle_detected(self, data):
        if data:
            if self.detect_end():
                uid = self.rfid_manager.to_string(block_data=data)
                self.log_info(f"Tag uid={uid}")

    def _handle_read(self, data):
        if data:
            if self.read_end():
                self.log_info(f"RFID[{self.name}] read data={data}")

    def get_tags_begin(self, callback):
        func = self.rfid_manager.get_tags

        try:
            is_ready = self.periodic_task.schedule(func=func, callback=callback)
            if is_ready:
                ret = self.periodic_task.start()
                if ret:
                    self.log_info(f"RFID[{self.name}] get tags"
                                  f" initiated in the backend")
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
                self.log_info(f"RFID[{self.name}] get tags"
                              f" terminated in the backend")
            else:
                self.log_warning(f"RFID[{self.name}] get tags is not running")
            return ret
        except Exception as e:
            self.log_error(f"RFID[{self.name}] get_tags_end error:{e}")

    # CMD func for G-Code
    def cmd_RFID_detect(self, gcmd):
        flag = gcmd.get_int("SWITCH", 0)
        if flag == 1:
            self.detect_begin(callback=self._handle_detected)
        else:
            self.detect_end()

    def cmd_RFID_read(self, gcmd):
        flag = gcmd.get_int("SWITCH", 0)
        if flag == 1:
            self.read_begin(callback=self._handle_read)
        else:
            self.read_end()

    def cmd_RFID_write(self, gcmd):
        self.log_info(f"RFID[{self.name}] write start")
        self.write()
        self.log_info(f"RFID[{self.name}] write finish")

    def cmd_RFID_read_tags(self, gcmd):
        self.rfid_manager.get_tags()


def load_config(config):
    return MMSRfid(config)
