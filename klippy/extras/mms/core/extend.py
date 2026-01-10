# Support for MMS Extend
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass, fields
from typing import List

from ..adapters import printer_adapter


@dataclass(frozen=True)
class ExtendConfig:
    # Must be first line, printer_config is the param of Config object
    printer_config: object
    # Skip configs use in __post_init__()
    skip_configs = [
        "printer_config",
    ]
    # ==== configuration values in *.cfg, must set default  ====
    slot: str = "4,5,6,7"

    selector_name: str = "selector"
    drive_name: str = "drive"

    outlet: str = "buffer:PA5"
    buffer_runout: str = "buffer:PA4"

    def __post_init__(self):
        type_method_map = {
            int: "getint",
            float: "getfloat",
            str: "get",
        }

        for field_info in fields(self):
            field_name = field_info.name
            field_type = field_info.type

            if field_name in self.skip_configs:
                continue

            if field_name in ("slot",):
                self._parse_list_field(field_name)
                continue

            # Default type is int
            get_method = type_method_map.get(field_type, "getint")
            config_value = getattr(self.printer_config, get_method)(field_name)
            object.__setattr__(self, field_name, config_value)

    def _parse_string_list(self, val_str):
        val_str = val_str or ""
        lst = [val.strip() for val in val_str.split(",")]
        return lst

    def _parse_list_field(self, field_name):
        val = self.printer_config.get(field_name)
        object.__setattr__(self, field_name, self._parse_string_list(val))


class MMSExtend:
    def __init__(self, config):
        self.name = config.get_name()
        self.num = int(self.name.split()[-1])
        self.mms_buffer = None

        self.extend_config = ExtendConfig(config)

        printer_adapter.register_mms_initialized(
            self._handle_mms_initialized)

    def _handle_mms_initialized(self, mms):
        # Extend self to MMS
        assert mms, "MMS not found"
        mms.extend(self)

    def get_num(self) -> int:
        return self.num

    def get_slot_nums(self) -> List[int]:
        return [int(slot_num) for slot_num in self.extend_config.slot]

    def get_outlet_pin(self) -> str:
        return self.extend_config.outlet

    def get_selector_name(self) -> str:
        return self.extend_config.selector_name

    def get_drive_name(self) -> str:
        return self.extend_config.drive_name

    def get_buffer_runout_pin(self) -> str:
        return self.extend_config.buffer_runout

    def get_mms_slots(self):
        mms_slots = [
            printer_adapter.get_mms_slot(slot_num)
            for slot_num in self.get_slot_nums()
        ]
        return mms_slots

    def set_mms_buffer(self, mms_buffer):
        self.mms_buffer = mms_buffer

    def get_mms_buffer(self):
        return self.mms_buffer

    # def has_slot(self, slot_num):
    #     return slot_num in self.get_slot_nums()


def load_config(config):
    return MMSExtend(config)
