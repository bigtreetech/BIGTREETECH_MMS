# Support for MMS Slot Dryer
#
# Copyright (C) 2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import time

from dataclasses import dataclass
from typing import Final, Optional

from ..adapters import printer_adapter, heaters_adapter


@dataclass(frozen=True)
class DryerConfig:
    name: str
    temperature: float
    heat_duration: int = 14400  # seconds, 4 Hours


# Materials Setup
ABS: Final = DryerConfig(name="ABS", temperature=55)
ASA: Final = DryerConfig(name="ASA", temperature=55)
PETG: Final = DryerConfig(name="PETG", temperature=50)
PLA: Final = DryerConfig(name="PLA", temperature=45)
# PLA_LONG: Final = DryerConfig("PLA", 45, 18000)
PLA_CF: Final = DryerConfig(name="PLA-CF", temperature=45)
TEST_FILAMENT: Final = DryerConfig(
    name="TEST", temperature=45, heat_duration=30)


@dataclass
class Dryer:
    # group_num: int
    heater: str
    is_heating: bool = False
    start_at: Optional[int] = None
    finish_at: Optional[int] = None
    temperature: Optional[int] = None
    material_name: Optional[int] = None


class MMSDryer:
    def __init__(self, heater):
        self.reactor = printer_adapter.get_reactor()
        self._timer = self.reactor.register_timer(self.teardown)

        self._dryer = Dryer(heater = heater)
        self.materials = (PLA, ABS, PETG, ASA, PLA_CF, TEST_FILAMENT)

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_loggers()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info_s = mms_logger.create_log_info(console_output=False)
        self.log_warning_s = mms_logger.create_log_warning(
            console_output=False)

    def get_filament_material(self, material_name):
        material_name = material_name.upper()
        for ma in self.materials:
            if ma.name == material_name:
                return ma
        return None

    def start_heating(self, material_name):
        f_material = self.get_filament_material(material_name)
        if not f_material:
            return False

        pheater = heaters_adapter.find_heater(self._dryer.heater)
        if not pheater:
            return False

        heaters_adapter.set_temperature(
            heater=pheater, temp=f_material.temperature, wait=False)
        now = time.time()
        self._dryer.start_at = now
        self._dryer.finish_at = now + f_material.heat_duration
        self._dryer.temperature = f_material.temperature
        self._dryer.material_name = f_material.name
        self._dryer.is_heating = True

        self.log_info_s(
            f"mms_dryer[{self._dryer.heater}] begin, "
            f"{self._dryer.material_name} - "
            f"{self._dryer.temperature}C - "
            f"{f_material.heat_duration}s"
        )
        self.countdown(f_material.heat_duration)

        return True

    def stop_heating(self):
        pheater = heaters_adapter.find_heater(self._dryer.heater)
        if not pheater:
            return False

        if (not self._dryer.is_heating
                and not pheater.get_temp(self.reactor.monotonic())[1]):
            return False
        heaters_adapter.set_temperature(heater=pheater, temp=0)
        self.clear_countdown()
        return True

    def clear_countdown(self):
        self.reactor.update_timer(self._timer, self.reactor.NEVER)
        if self._dryer.is_heating:
            self.log_info_s(
                f"mms_dryer[{self._dryer.heater}] end, "
                f"{self._dryer.material_name} - {self._dryer.temperature}C")
        self._dryer.is_heating = False

    def countdown(self, duration):
        self.reactor.update_timer(
            self._timer, self.reactor.monotonic() + duration)

    def teardown(self, eventtime):
        self.stop_heating()
        return self.reactor.NEVER

    def report(self):
        return {
            "heater" : self._dryer.heater,
            "is_heating" : self._dryer.is_heating,
            "start_at" : self._dryer.start_at,
            "finish_at" : self._dryer.finish_at,
            "temperature" : self._dryer.temperature,
            "material_name" : self._dryer.material_name,
        }
