# Support for MMS Autoload
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import time
from contextlib import contextmanager
from dataclasses import dataclass

from ..adapters import (
    gcode_adapter,
    pause_resume_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.config import PrinterConfig
from ..core.exceptions import (
    DeliveryFailedError, DeliveryTerminateSignal, MMSException,
)
from ..core.task import AsyncTask


@dataclass(frozen=True)
class PrinterAutoloadConfig(PrinterConfig):
    # Enable/disable the autoload module
    # Default is disable
    # 0 = disable, 1 = enable
    enable: int = 0


@dataclass(frozen=True)
class AutoloadConfig:
    show_debug_log: bool = True
    delay_seconds: float = 3


class MMSAutoload:
    def __init__(self, config):
        self.reactor = printer_adapter.get_reactor()

        p_al_config = PrinterAutoloadConfig(config)
        self._enable = bool(p_al_config.enable)

        self.al_config = AutoloadConfig()

        # Status
        self._in_progress = False
        self._switching = False
        self._cancel_switch = False
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
            # ("MMS_SEMI_AUTOLOAD", self.cmd_MMS_PRE_LOAD),
            ("MMS_PRE_LOAD", self.cmd_MMS_PRE_LOAD),

            ("MMS_AUTOLOAD_ENABLE", self.cmd_MMS_AUTOLOAD_ENABLE),
            ("MMS_AUTOLOAD_DISABLE", self.cmd_MMS_AUTOLOAD_DISABLE),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=False)
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()

    # Progress control
    def is_in_progress(self):
        return self._in_progress

    def is_busy(self):
        return self._switching or self.async_task.is_running()

    def current_is_cancelled(self):
        return self.async_task.current_is_cancelled()

    def request_stop(self):
        # Also cancel an execute() that has joined its old task but is still
        # waiting to start the replacement.
        self._cancel_switch = True
        self._should_break = True
        if self.async_task.is_running():
            completion = self.async_task.completion
            self.async_task.stop()
            return completion
        return None

    # ---- Autoload ----
    def is_enabled(self):
        return self._enable

    def is_disabled(self):
        return not self._enable

    def enable(self):
        self._enable = True

    def disable(self):
        self._enable = False

    @contextmanager
    def _execution(self, slot_num):
        self._in_progress = True
        self.log_info(f"slot[{slot_num}] autoload begin")
        try:
            with self.mms_delivery.maintenance_feeds(slot_num):
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

    def _can_execute(self, allow_current=False):
        if not self._delay_satisfied():
            return False

        check_lst = [
            (self.mms_delivery.is_stopping, "MMS_STOP is in progress"),
            (self.mms_delivery.async_task_sp.is_running,
             "background delivery is running"),
            (self.is_disabled, "mms_autoload is disabled"),
            (self.mms.printer_is_shutdown, "printer is shutdown"),
            (print_stats_adapter.is_printing, "printer is printing"),
            (print_stats_adapter.is_paused, "printer is paused"),
            (pause_resume_adapter.is_paused, "printer is paused"),
            (self.mms.printer_is_resuming, "printer is resuming"),
            (lambda: self.mms.get_mms_resume().get_exhausted_slot()
             is not None, "known exhausted filament requires clearance"),
            (self.mms.mms_eject.finish_is_active, "print finish is running"),
            (lambda: any(operation.is_running() for operation in (
                self.mms.mms_swap, self.mms.mms_charge, self.mms.mms_eject,
                self.mms.mms_swap.mms_cut, self.mms.mms_swap.mms_purge,
                self.mms.mms_swap.mms_brush)), "MMS operation is running"),
        ]
        # An existing autoload may own the motors and toolhead timing while
        # being stopped. Printer and enable checks still apply during that wait.
        if not allow_current:
            check_lst += [
                (lambda: self.mms.mms_drive_is_running(include_buffer=False),
                 "drive is running"),
                (self.mms.mms_selector_is_running, "selector is running"),
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
        if (not mms_slot.cleanup_required and mms_slot.is_new_insert()
                and not mms_slot.gate.is_triggered()):
            self.log_info(
                f"slot[{mms_slot.get_num()}] is new insert, "
                "ready for autoload"
            )
            return True
        return False

    def _fetch_slot(self, slot_num=None):
        """Find out mms_slot which is loaded to inlet but not gate"""
        if slot_num is None:
            for mms_slot in self.mms.get_mms_slots():
                if self._check_slot(mms_slot):
                    return mms_slot
            return None

        mms_slot = self.mms.get_mms_slot(slot_num)
        if self._check_slot(mms_slot):
            return mms_slot

        # Default return None
        self.log_warning(
            f"slot[{slot_num}] autoload skipped; "
            "check Inlet and cleanup_required in MMS_STATUS"
        )
        return None

    def execute(self, slot_num=None):
        if self._switching:
            self.log_warning("autoload replacement is in progress; retry later")
            return

        # Own the complete stop/join/start sequence. A physical stop may still
        # be unwinding after the old task completes; no new task can start then.
        self._switching = True
        self._cancel_switch = False
        try:
            if not self._can_execute(
                allow_current=self.async_task.is_running()
            ):
                return
            mms_slot = self._fetch_slot(slot_num)
            if mms_slot is None:
                return

            if self.async_task.is_running():
                completion = self.async_task.completion
                # Capture ownership before stop or any operation that yields.
                active_slot = (self.async_task.params['mms_slot'].get_num()
                               if self._in_progress else None)
                deadline = (
                    self.reactor.monotonic()
                    + self.mms_delivery.d_config.wait_mms_stepper_timeout
                )
                self.async_task.stop()
                self._stop_current(active_slot)
                completion.wait(deadline)
                if not completion.test():
                    self.log_warning(
                        "autoload stop is still pending; retry after it exits")
                    return
                if self._cancel_switch or not self._can_execute(
                    allow_current=True
                ):
                    return
                # Homing updates toolhead print time; allow its existing idle
                # window to settle before deciding if a replacement can start.
                if not self.mms_delivery.wait_toolhead(
                        should_stop=lambda: self._cancel_switch):
                    self.log_warning(
                        "autoload toolhead is still busy; "
                        "retry after it is idle"
                    )
                    return
            if self._cancel_switch or not self._can_execute():
                return
            mms_slot = self._fetch_slot(slot_num)
            if mms_slot is None:
                return
            is_ready = self.async_task.setup(self._run, {"mms_slot": mms_slot})
            if is_ready:
                self._should_break = False
                self.async_task.start()

        except Exception as e:
            self.log_error(f"slot[{slot_num or '*'}] autoload error:{e}")
        finally:
            self._switching = False

    def _stop_current(self, slot_num):
        self._should_break = True
        if slot_num is None:
            return
        if self.mms_delivery.mms_stop(slot_num):
            self.log_info(f"slot[{slot_num}] autoload stop")

    def _run(self, mms_slot):
        # A scheduled callback can start after printing or sensor state has
        # changed, so the earlier execute() checks are not sufficient.
        if self._should_break or not self._can_execute() \
                or not self._check_slot(mms_slot):
            return
        slot_num = mms_slot.get_num()
        with self._execution(slot_num):
            self._mms_autoload(slot_num)

    def _mms_autoload(self, slot_num):
        if self._should_break:
            return

        mms_slot = self.mms.get_mms_slot(slot_num)
        with mms_slot.slot_rfid.execute():
            if (not self._should_break and not mms_slot.cleanup_required
                    and mms_slot.inlet.is_triggered()
                    and not mms_slot.gate.is_triggered()):
                eject = self.mms.mms_eject
                if (eject._get_progress() is not None
                        or self.mms.get_mms_resume().get_exhausted_slot()
                        is not None):
                    return
                eject.check_paths()
                eject.release_other_selectors(slot_num)
                try:
                    self.mms_delivery.autoload_to_gate(
                        slot_num, should_stop=lambda: self._should_break)
                except DeliveryTerminateSignal:
                    self.log_info(f"slot[{slot_num}] autoload terminated")
                except DeliveryFailedError:
                    return
                except Exception as e:
                    self.log_error(f"slot[{slot_num}] autoload error: {e}")
                mms_slot.slot_rfid.wait_read(lambda: self._should_break)

    # ---- Pre-load ----
    def _can_pre_load(self):
        check_lst = [
            (self.mms.printer_is_shutdown, "printer is shutdown"),
            (self.mms.printer_is_printing, "printer is printing"),
            (self.mms.printer_is_resuming, "printer is resuming"),
            (lambda: self.mms.get_mms_resume().get_exhausted_slot()
             is not None, "known exhausted filament requires clearance"),
            (lambda: self.mms.mms_drive_is_running(include_buffer=False),
                 "drive is running"),
            (self.mms.mms_selector_is_running, "selector is running"),
        ]
        for condition,msg in check_lst:
            if condition():
                self.log_info(f"pre_load skip: {msg}")
                return False
        return True

    def mms_pre_load(self, slot_num):
        def gate_ready():
            slot = self.mms.get_mms_slot(slot_num)
            slot.check_load_allowed()
            if self.mms.get_mms_resume().get_exhausted_slot() is not None:
                raise MMSException("clear the known exhausted filament first")
            eject = self.mms.mms_eject
            if eject._get_progress() is not None:
                raise MMSException("finish the pending MMS_EJECT first")
            return slot_num in eject.check_paths()

        try:
            if gate_ready():
                self.log_info(f"slot[{slot_num}] already at Gate")
                return True
            with self.mms_delivery.maintenance_feeds(slot_num):
                self.mms_delivery._check_task_cancelled()
                if not gate_ready():
                    self.mms.mms_eject.release_other_selectors(slot_num)
                    self.mms_delivery.preload_to_gate(slot_num)
            return True
        except DeliveryTerminateSignal:
            self.log_info(f"slot[{slot_num}] pre_load terminated")
        except Exception as e:
            self.log_error(f"slot[{slot_num}] pre_load error: {e}")
        return False

    def cmd_MMS_PRE_LOAD(self, gcmd):
        self.mms.check_maintenance(gcmd)
        if not self._delay_satisfied():
            raise gcmd.error(
                "MMS_PRE_LOAD is not ready; retry after initialization"
            )
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error(f"Invalid MMS slot: {slot_num}")
        if not self._can_pre_load():
            raise gcmd.error("MMS_PRE_LOAD blocked: check the reported state")
        self.mms_delivery.deliver_task(
            self.mms_pre_load, {"slot_num": slot_num}, manage_feeds=False,
            wait=bool(gcmd.get_int("WAIT", default=0)))

    # ---- GCode enable/disable ----
    def cmd_MMS_AUTOLOAD_ENABLE(self, gcmd):
        self.enable()

    def cmd_MMS_AUTOLOAD_DISABLE(self, gcmd):
        self.disable()


def load_config(config):
    return MMSAutoload(config)
