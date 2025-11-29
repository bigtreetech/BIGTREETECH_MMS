# Support for MMS Observer
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass

from .task import PeriodicTask
from ..adapters import (
    idle_timeout_adapter,
    print_stats_adapter,
    printer_adapter,
    virtual_sdcard_adapter,
)


@dataclass(frozen=True)
class PrintObserverConfig:
    # Period (in seconds) for monitoring print status
    task_period: float = 0.2


@dataclass(frozen=True)
class PrintProgress:
    idle: str = "idle"
    started: str = "started"

    pausing: str = "pausing"
    paused: str = "paused"

    resuming: str = "resuming"
    resumed: str = "resumed"

    finished: str = "finished"


class PrintObserver:
    """Monitors print state changes
    and handle event corresponding callbacks"""

    def __init__(self):
        # Current state
        self.state = None
        # Previous state
        self.state_prev = None

        self.p_progress = PrintProgress()
        # Default progress
        self.progress = self.p_progress.idle

        self._initialize_loggers()
        self._initialize_callback_managers()

        self.task = self._run_task()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info()
        self.log_error = mms_logger.create_log_error()

    def _initialize_callback_managers(self):
        self.cb_manager = CallbackManager()
        self.dcb_manager = DisposableCallbackManager()
        # idle_timeout_adapter.setup_callback_manager()

    def _run_task(self):
        task = PeriodicTask()
        task.set_period(PrintObserverConfig.task_period)
        try:
            is_ready = task.schedule(self._observe)
            if is_ready:
                task.start()
        except Exception as e:
            self.log_error(f"print observer task error: {e}")
        return task

    def stop(self):
        self.task.stop()

    # ---- Observe ----
    def _observe(self):
        """Periodic task monitoring print state changes"""
        self.state_prev = self.state
        self.state = print_stats_adapter.get_state()
        # Skip processing if state hasn't changed
        if self.state_prev == self.state:
            return

        if self.is_printing():
            if self._prev_paused():
                self.progress = self.p_progress.resumed
            else:
                self.progress = self.p_progress.started
        elif self.is_pausing():
            self.progress = self.p_progress.pausing
        elif self.is_paused():
            self.progress = self.p_progress.paused
        elif self.is_finished():
            self.progress = self.p_progress.finished
        else:
            # No judgement, direct return
            return

        self.log_info(f"print new progress: '{self.progress}'")
        # Trigger callbacks with new progress
        self.cb_manager.handle_event(self.progress)
        self.dcb_manager.handle_event(self.progress)

    def _prev_paused(self):
        return self.progress == self.p_progress.paused

    def is_printing(self):
        return print_stats_adapter.is_printing(self.state)

    def is_pausing(self):
        # self.is_printing() and?
        return virtual_sdcard_adapter.has_pause_flag()

    def is_paused(self):
        return print_stats_adapter.is_paused(self.state)

    def is_finished(self):
        return print_stats_adapter.is_finished(self.state)

    def get_status(self):
        return {
            "progress": self.progress,
            "print_stats": print_stats_adapter.get_current_status(),
            "idle_timeout": idle_timeout_adapter.get_current_status(),
            "vr_sdcard": virtual_sdcard_adapter.get_current_status(),
        }

    # ---- Registers ----
    def register_start_callback(self, callback, params=None):
        self.cb_manager.register_start_callback(callback, params)

    def register_finish_callback(self, callback, params=None):
        self.cb_manager.register_finish_callback(callback, params)

    def register_resume_callback(self, callback, params=None):
        self.cb_manager.register_resume_callback(callback, params)

    def register_pause_callback(self, callback, params=None):
        self.cb_manager.register_pause_callback(callback, params)

    def register_resume_callback_disposable(self, callback, params=None):
        self.dcb_manager.register_resume_callback(callback, params)


class CallbackManager:
    def __init__(self):
        self.p_progress = PrintProgress()

        """
        Callback func in lists
        {
            progress: [
                (callback_func, params),
                ...
            ],
            ...
        }
        """
        self.callbacks = {
            self.p_progress.started: [],
            self.p_progress.finished: [],
            self.p_progress.resumed: [],
            self.p_progress.paused: [],
            # self.p_progress.pausing: [],
        }

        self._initialize_loggers()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info()
        self.log_error = mms_logger.create_log_error()

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

    def register_start_callback(self, callback, params=None):
        self._register_callback(self.p_progress.started, callback, params)

    def register_finish_callback(self, callback, params=None):
        self._register_callback(self.p_progress.finished, callback, params)

    def register_resume_callback(self, callback, params=None):
        self._register_callback(self.p_progress.resumed, callback, params)

    def register_pause_callback(self, callback, params=None):
        self._register_callback(self.p_progress.paused, callback, params)

    def _unregister_callback(self, event_type, callback=None):
        if event_type not in self.callbacks:
            return False

        if callback:
            original_count = len(self.callbacks[event_type])
            self.callbacks[event_type] = [
                (cb, param) for cb, param in self.callbacks[event_type]
                if cb != callback
            ]
            # Return removed result
            return len(self.callbacks[event_type]) < original_count
        else:
            # Remove all event_type callback
            self.callbacks[event_type]= []
            return True

    def unregister_resume_callback(self, callback):
        self._unregister_callback(self.p_progress.resumed, callback)

    def handle_event(self, event_type):
        # Callbacks
        callbacks = self.callbacks.get(event_type, [])
        if not callbacks:
            return

        for callback, params in callbacks:
            try:
                callback(**params)
                self.log_info(f"'{event_type}' callback executed: {callback}")
            except Exception as e:
                self.log_error(f"'{event_type}' callback error: {e}")
                continue


class DisposableCallbackManager(CallbackManager):
    def __init__(self):
        super().__init__()

    def handle_event(self, event_type):
        callbacks = self.callbacks.get(event_type, [])
        if not callbacks:
            return

        for callback, params in callbacks:
            try:
                callback(**params)
                self.log_info(
                    f"'{event_type}' disposable callback executed: {callback}")
            except Exception as e:
                self.log_error(
                    f"'{event_type}' disposable callback error: {e}")
                continue

        # Finally truncate disposable callbacks
        try:
            self.callbacks[event_type]= []
        except Exception as e:
            self.log_error(
                f"'{event_type}' disposable callback truncate error: {e}")
