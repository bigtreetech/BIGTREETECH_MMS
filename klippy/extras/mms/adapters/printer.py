# Adapter of the printer
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass

from .base import BaseAdapter


@dataclass(frozen=True)
class MMSModule:
    mms: str = "mms"
    mms_logger: str = "mms logger"
    mms_delivery: str = "mms delivery"
    mms_autoload: str = "mms autoload"

    mms_brush: str = "mms brush"
    mms_charge: str = "mms charge"
    mms_cut: str = "mms cut"
    mms_eject: str = "mms eject"
    mms_purge: str = "mms purge"
    mms_swap: str = "mms swap"

    # Notice the blank space
    mms_slot_prefix: str = "mms slot "


@dataclass(frozen=True)
class KlippyEvent:
    connect: str = "klippy:connect"
    ready: str = "klippy:ready"
    shutdown: str = "klippy:shutdown"
    disconnect: str = "klippy:disconnect"
    firmware_restart: str = "klippy:firmware_restart"

    mms_initialized: str = "mms:initialized"
    mms_stepper_running: str = "mms:stepper:running"
    mms_stepper_idle: str = "mms:stepper:idle"


class PrinterAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self.klippy_event = KlippyEvent()
        self.mms_module = MMSModule()

    # ---- Register event handler to printer ----
    def register_event(self, event, handler):
        self.printer.register_event_handler(event, handler)

    def bulk_register_event(self, events):
        for event, handler in events:
            self.register_event(event, handler)

    def register_klippy_connect(self, handler):
        self.register_event(self.klippy_event.connect, handler)

    def register_klippy_ready(self, handler):
        self.register_event(self.klippy_event.ready, handler)

    def register_klippy_shutdown(self, handler):
        self.register_event(self.klippy_event.shutdown, handler)

    def register_klippy_disconnect(self, handler):
        self.register_event(self.klippy_event.disconnect, handler)

    def register_klippy_firmware_restart(self, handler):
        self.register_event(self.klippy_event.firmware_restart, handler)

    # ---- Printer methods ----
    def get_printer(self):
        return self.printer

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return self.printer.get_start_args()

    def get_klippy_logfile(self):
        return self.get_start_args().get("log_file")

    def get_klippy_configfile(self):
        # Most likely return "/home/.../printer_data/config/printer.cfg"
        return self.get_start_args().get("config_file")

    def is_shutdown(self):
        # category:["ready", "startup", "shutdown", "error"]
        # state_msg, category = self.printer.get_state_message()
        # return True if category == "ready" else False
        return self.printer.is_shutdown()

    def emergency_stop(self, reason):
        self.printer.invoke_shutdown(f"Shutdown by MMS: {reason}")

    def send_event(self, event, *params):
        return self.printer.send_event(event, *params)

    # ---- Quick methods for MMS ----
    def register_mms_initialized(self, handler):
        self.register_event(self.klippy_event.mms_initialized, handler)

    def notify_mms_initialized(self, mms):
        self.send_event(self.klippy_event.mms_initialized, mms)

    def register_mms_stepper_running(self, handler):
        self.register_event(self.klippy_event.mms_stepper_running, handler)

    def notify_mms_stepper_running(self):
        self.send_event(self.klippy_event.mms_stepper_running)

    def register_mms_stepper_idle(self, handler):
        self.register_event(self.klippy_event.mms_stepper_idle, handler)

    def notify_mms_stepper_idle(self):
        self.send_event(self.klippy_event.mms_stepper_idle)

    def get_mms(self):
        mms = self.get_obj(self.mms_module.mms)
        assert mms, "MMS not found"
        return mms

    def get_mms_logger(self):
        return self.safe_get(self.mms_module.mms_logger)

    def get_mms_delivery(self):
        return self.safe_get(self.mms_module.mms_delivery)

    def get_mms_autoload(self):
        return self.safe_get(self.mms_module.mms_autoload)

    def get_mms_brush(self):
        return self.safe_get(self.mms_module.mms_brush)

    def get_mms_charge(self):
        return self.safe_get(self.mms_module.mms_charge)

    def get_mms_cut(self):
        return self.safe_get(self.mms_module.mms_cut)

    def get_mms_eject(self):
        return self.safe_get(self.mms_module.mms_eject)

    def get_mms_purge(self):
        return self.safe_get(self.mms_module.mms_purge)

    def get_mms_swap(self):
        return self.safe_get(self.mms_module.mms_swap)

    def get_mms_slot(self, slot_num):
        return self.safe_get(self.mms_module.mms_slot_prefix+str(slot_num))


# Global instance for singleton
printer_adapter = PrinterAdapter()
