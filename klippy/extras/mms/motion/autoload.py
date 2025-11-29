# Support for MMS Autoload
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import time
from contextlib import contextmanager
from dataclasses import dataclass

from ..adapters import (
    gcode_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.exceptions import (
    DeliveryTerminateSignal,
)
from ..core.task import AsyncTask


@dataclass(frozen=True)
class AutoloadConfig:
    show_debug_log: bool = True
    delay_seconds: float = 3
    distance_load: float = 1000
    execute_stop_delay: float = 0.3


class MMSAutoload:
    def __init__(self, config):
        self.reactor = printer_adapter.get_reactor()

        self.al_config = AutoloadConfig()

        # Status
        self._in_progress = False
        self._should_break = False
        self._klippy_ready_at = None
        self._klippy_ready_delay_done = False

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()
        self._klippy_ready_at = time.time()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()
        # MMS objects
        self.async_task = AsyncTask()

    def _initialize_gcode(self):
        commands = [
            ("MMS_SEMI_AUTOLOAD", self.cmd_MMS_SEMI_AUTOLOAD),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()

    # Progress control
    def pause(self, period_seconds):
        self.reactor.pause(self.reactor.monotonic() + period_seconds)

    def is_in_progress(self):
        return self._in_progress

    # ---- Autoload ----
    @contextmanager
    def _execution(self, slot_num):
        self._in_progress = True
        self._should_break = False
        self.log_info(f"slot[{slot_num}] autoload begin")
        try:
            yield
        finally:
            self._in_progress = False
            self.log_info(f"slot[{slot_num}] autoload end")

    def _delay_satisfied(self):
        # Delay serveral seconds to avoid accident trigger
        if self._klippy_ready_delay_done:
            return True

        if self._klippy_ready_at is None \
            or (time.time()
                - self._klippy_ready_at <= self.al_config.delay_seconds):
            return False

        self._klippy_ready_delay_done = True
        return True

    def _can_execute(self):
        if not self._delay_satisfied():
            return False

        if self._in_progress:
            check_lst = []
        else:
            check_lst = [
                (self.mms.mms_drive_is_running, "drive is running"),
                (self.mms.mms_selector_is_running, "selector is running"),
                (self.mms.printer_is_shutdown, "printer is prishutdownnting"),
                (self.mms.printer_is_printing, "printer is printing"),
                (self.mms.printer_is_paused, "printer is paused"),
                (self.mms.printer_is_resuming, "printer is resuming"),
                (toolhead_adapter.is_busy, "toolhead is busy")
            ]

        for condition,msg in check_lst:
            if condition():
                if self.al_config.show_debug_log:
                    self.log_info(f"autoload skip: {msg}")
                return False

        return True

    def _check_slot(self, mms_slot):
        # is_new_insert is True means slot is ready
        if mms_slot.is_new_insert():
            self.log_info(
                f"slot[{mms_slot.get_num()}] is new insert, "
                "ready for autoload"
            )
            return True
        return False

    def _fetch_slot(self, slot_num=None):
        """Find out mms_slot which is loaded to inlet but not gate"""
        if slot_num is None:
            for mms_slot in self.mms.get_slots():
                if self._check_slot(mms_slot):
                    return mms_slot

        mms_slot = self.mms.get_slot(slot_num)
        if self._check_slot(mms_slot):
            return mms_slot

        # Default return None
        self.log_warning(
            f"slot[{slot_num}] is not new insert, "
            "autoload skip..."
        )
        return None

    def execute(self, slot_num=None):
        if not self._can_execute():
            return

        mms_slot = self._fetch_slot(slot_num)
        if mms_slot is None:
            return

        func = self._run
        params = {"mms_slot":mms_slot}
        try:
            # Stop current autoload first
            if self._in_progress:
                self._stop_current()
                self.async_task.stop()
                self.pause(self.al_config.execute_stop_delay)

            is_ready = self.async_task.setup(func, params)
            if is_ready:
                self.async_task.start()

        except Exception as e:
            self.log_error(f"slot[{slot_num or '*'}] autoload error:{e}")

    def _stop_current(self):
        slot_num = self.mms.get_current_slot()
        if slot_num is None:
            return

        self._should_break = True
        self.mms_delivery.mms_stop(slot_num)
        self.log_info(f"slot[{slot_num}] autoload stop")

    def _run(self, mms_slot):
        slot_num = mms_slot.get_num()

        with self._execution(slot_num):
            self._unload_other_slots(slot_num)

            if self._should_break:
                return

            with mms_slot.slot_rfid.execute():
                if mms_slot.inlet.is_triggered():
                    self.mms_delivery.mms_prepare(slot_num)

    def _unload_other_slots(self, slot_num):
        try:
            self.mms_delivery.unload_loading_slots(skip_slot=slot_num)
        except Exception as e:
            self.log_error(
                f"slot[{slot_num}] autoload unload other slots error: {e}")

    # ---- Semi-Autoload ----
    def _can_semi_autoload(self):
        check_lst = [
            (self.mms.printer_is_shutdown, "printer is shutdown"),
            (self.mms.printer_is_printing, "printer is printing"),
            (self.mms.printer_is_resuming, "printer is resuming"),
            (self.mms.mms_drive_is_running, "drive is running"),
            (self.mms.mms_selector_is_running, "selector is running"),
        ]
        for condition,msg in check_lst:
            if condition():
                self.log_info(f"semi-autoload skip: {msg}")
                return False
        return True

    def mms_semi_autoload(self, slot_num):
        try:
            self.mms_delivery.select_slot(slot_num)
            self.mms_delivery.semi_load_to_gate(
                slot_num, distance=self.al_config.distance_load)
            self.mms_delivery.unload_to_gate(slot_num)
        except DeliveryTerminateSignal:
            self.log_info(f"slot[{slot_num}] semi-autoload terminated")
        except Exception as e:
            self.log_error(f"slot[{slot_num}] semi-autoload error: {e}")

    def cmd_MMS_SEMI_AUTOLOAD(self, gcmd):
        if not self._delay_satisfied():
            return

        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        if not self._can_semi_autoload():
            return

        self.mms_delivery.deliver_async_task(
            self.mms_semi_autoload, {"slot_num":slot_num})


def load_config(config):
    return MMSAutoload(config)
