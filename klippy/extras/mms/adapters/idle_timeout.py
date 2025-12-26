# Adapter of printer's idle_timeout
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass

from .base import BaseAdapter
from .printer import printer_adapter


@dataclass(frozen=True)
class IdleTimeoutEvent:
    printing: str = "idle_timeout:printing"
    ready: str = "idle_timeout:ready"
    idle: str = "idle_timeout:idle"


# Enum state from klippy/extras/idle_timeout.py
@dataclass(frozen=True)
class IdleTimeoutState:
    idle: str = "Idle"
    printing: str = "Printing"
    ready: str = "Ready"


class IdleTimeoutCallbackManager:
    def __init__(self):
        self._initialize_loggers()

        self.it_state = IdleTimeoutState()
        """
        Callback func in lists
        {
            key: [
                (callback_func, params),
                ...
            ],
            ...
        }
        """
        self.callbacks = {
            self.it_state.idle: [],
            self.it_state.printing: [],
            self.it_state.ready: [],
        }

        it_event = IdleTimeoutEvent()
        # Register Klipper event handlers
        events = [
            (it_event.printing, self._handle_busy),
            (it_event.ready, self._handle_ready),
            (it_event.idle, self._handle_idle),
        ]
        printer_adapter.bulk_register_event(events)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=False)
        self.log_error = mms_logger.create_log_error()

    def _handle_busy(self, print_time):
        # Would be called while any stepper begin to move
        self.log_info("klipper state transitioned to busy")
        self.trigger(self.it_state.printing)

    def _handle_ready(self, print_time):
        # Would be called if is not printing, any stepper finishing move
        self.log_info("klipper state transitioned to ready")
        self.trigger(self.it_state.ready)

    def _handle_idle(self, print_time):
        # Would be called if is printing, idle delay is timeout
        self.log_info("klipper state transitioned to idle")
        self.trigger(self.it_state.idle)

    def trigger(self, event_type):
        # Callbacks
        callbacks = self.callbacks.get(event_type, [])
        if not callbacks:
            return

        for callback, params in callbacks:
            try:
                callback(**params)
                self.log_info(
                    f"idle_timeout '{event_type}'"
                    f" callback executed: {callback}")
            except Exception as e:
                self.log_error(
                    f"idle_timeout '{event_type}' callback error: {e}")
                continue

        # Finally truncate callbacks
        try:
            self.callbacks[event_type]= []
        except Exception as e:
            self.log_error(
                f"idle_timeout '{event_type}' callback truncate error: {e}")

    def _inspect_params(self, params):
        if params is None:
            params = {}
        elif not isinstance(params, dict):
            raise TypeError(
                "callback params must be a dictionary")
        return params

    def _register_callback(self, event_type, callback, params=None):
        params = self._inspect_params(params)

        if event_type not in self.callbacks:
            self.callbacks[event_type] = []
        self.callbacks[event_type].append((callback, params))

    def register_idle_callback(self, callback, params=None):
        self._register_callback(self.it_state.idle, callback, params)

    def register_busy_callback(self, callback, params=None):
        self._register_callback(self.it_state.printing, callback, params)


class IdleTimeoutAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "idle_timeout"
        self.it_state = IdleTimeoutState()
        # self.it_cb_manager = None

    def _get_idle_timeout(self):
        return self.safe_get(self._obj_name)

    def setup_callback_manager(self):
        return
        # self.it_cb_manager = IdleTimeoutCallbackManager()

    def get_current_status(self):
        """
        {'state': 'Idle', 'printing_time': 0.0}
        """
        return self._get_idle_timeout().get_status(self.reactor.monotonic())

    def get_state(self):
        return self.get_current_status().get("state")

    def is_printing(self):
        return self.get_state() == self.it_state.printing

    # def register_busy_callback(self, callback, params=None):
    #     self.it_cb_manager.register_busy_callback(callback, params)


# Global instance for singleton
idle_timeout_adapter = IdleTimeoutAdapter()
