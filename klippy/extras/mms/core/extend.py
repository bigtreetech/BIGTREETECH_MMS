# Support for MMS Extend
#
# Copyright (C) 2025-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass

from .config import (
    get_buffer_config_float,
    PrinterConfig,
    StringList,
)
from ..adapters import printer_adapter


@dataclass(frozen=True)
class PrinterMMSExtendConfig(PrinterConfig):
    """ Configuration values in mms-extend.cfg """
    slot: StringList = "4,5,6,7"

    selector_name: str = "manual_stepper selector_stepper_2"
    drive_name: str = "manual_stepper drive_stepper_2"

    outlet: str = "buffer_2:PA5"
    buffer_runout: str = "buffer_2:PA4"
    buffer_target_percentage: float = 50.0
    buffer_max_velocity: float = 80.0
    buffer_max_accel: float = 120.0

    dryer_heater: str = "ViViD_Dryer_2"

    def get_type_handlers(self):
        handlers = super().get_type_handlers()
        handlers[float] = get_buffer_config_float
        return handlers


class MMSExtend:
    def __init__(self, config):
        self.name = config.get_name()
        self.num = int(self.name.split()[-1])
        self.mms_buffer = None

        ex_config = PrinterMMSExtendConfig(config)
        self.ex_config = ex_config.gen_packaged_config()

        printer_adapter.register_mms_extend(
            self._handle_mms_extend)

    def _handle_mms_extend(self, mms):
        # Extend self to MMS
        assert mms, "MMS not found"
        mms.extend(self)

    def get_num(self):
        return self.num

    def get_slot_nums(self):
        return [int(slot_num) for slot_num in self.ex_config.slot]

    def get_selector_name(self):
        return self.ex_config.selector_name

    def get_drive_name(self):
        return self.ex_config.drive_name

    def get_outlet_pin(self):
        return self.ex_config.outlet

    def get_buffer_runout_pin(self):
        return self.ex_config.buffer_runout

    def get_dryer_heater(self):
        return self.ex_config.dryer_heater

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

    def set_mms_dryer(self, mms_dryer):
        self.mms_dryer = mms_dryer

    def get_mms_dryer(self):
        return self.mms_dryer

    def report(self):
        return {
            "slots" : self.get_slot_nums(),
            "selector_name" : self.get_selector_name(),
            "drive_name" : self.get_drive_name(),
            "outlet" : self.get_outlet_pin(),
            "buffer_runout" : self.get_buffer_runout_pin(),
            "dryer": self.mms_dryer.report(),
        }

    # def has_slot(self, slot_num):
    #     return slot_num in self.get_slot_nums()


def load_config(config):
    return MMSExtend(config)
