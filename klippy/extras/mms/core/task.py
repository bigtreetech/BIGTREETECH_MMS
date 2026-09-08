# Support for MMS Service
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import time
from dataclasses import dataclass

from ..adapters import (
    printer_adapter,
)


@dataclass(frozen=True)
class TaskConfig:
    default_period: float = 0.5


class AsyncTask:
    """
    A class to represent an asynchronous task.
    This class allows you to schedule a function to be executed asynchronously
    after a specified period of time.

    Usage:
        def foo(p1, p2):
            pass

        task = AsyncTask()
        try:
            is_ready = task.setup(foo, {"p1": 1, "p2": 2})
            if is_ready:
                task.start()
        except Exception as e:
            self.log_error(f"error:{e}")
    """
    def __init__(self):
        self.reactor = printer_adapter.get_reactor()

        # The function to be executed
        self.func = None
        # The parameters to be passed to the function
        self.params = None
        # An optional callback function to be called after the task is completed
        self.callback = None
        # An optional callback function to be called if the task times out
        self.timeout_callback = None

        # Completion object to signal the end of the task
        self.completion = None
        # A boolean indicating whether the task is currently running
        self.running = False

        self.mms_logger = None

    def _initialize_loggers(self):
        if not self.mms_logger:
            self.mms_logger = printer_adapter.get_mms_logger()
            self.log_info = self.mms_logger.create_log_info(
                console_output=False)
            self.log_warning = self.mms_logger.create_log_warning()
            self.log_error = self.mms_logger.create_log_error()

    def setup(self, func, params=None, callback=None, timeout_callback=None):
        """
        Setup the asynchronous task.
        Args:
            func (callable): The function to be executed.
            params (optional): Parameters to be passed to the function.
            callback (optional): A callback function to be executed after
                                 the task is completed.
        Returns:
            bool: True if the task was successfully setup
                  False if a task is already running.

        Notice:
            "func" should not be @ by a decorator, such as contextmanager,
            which may return a generator but not the wanner function.

            E.g.
                @contextmanager
                def foo():
                    yield

                schedule(func=foo)

                # self.func => <contextlib._GeneratorContextManager object>
        """
        self._initialize_loggers()

        if self.running:
            self.log_warning(
                f"async task func:{self.func} exists and running, skip...")
            return False

        self.func = func
        self.params = params
        self.callback = callback
        self.timeout_callback = timeout_callback
        return True

    def start(self):
        """
        Start the asynchronous task.
        """
        if not self.func:
            self.log_warning("async task func not exists, return")
            return False

        if self.running:
            self.log_warning("async task func is running, return")
            return False

        self.running = True
        self.reactor.register_async_callback(self._execute)
        return self.completion

    def stop(self):
        if not self.func:
            self.log_warning("async task func not exists, return")
            return False

        if not self.running:
            self.log_warning("async taskfunc is not running, return")
            return False

        self._complete(-1)
        self.running = False
        return True

    def is_running(self):
        return self.running

    def _execute(self, eventtime):
        """
        Executes the asynchronous task function.
        Args:
            eventtime (float): The current event time.
        """
        try:
            result = self.func(**self.params) \
                if self.params is not None \
                else self.func()

            if self.callback:
                self.callback(result)
        except Exception as e:
            self.log_error(f"async task '{self.func}' execute error: {e}")
            if self.timeout_callback:
                self.timeout_callback(e)
        finally:
            self._complete(0)
            self.running = False

    def _complete(self, result):
        if self.completion:
            if self.reactor:
                try:
                    self.reactor.async_complete(self.completion, result)
                except Exception:
                    pass
            self.completion = None


class PeriodicTask:
    """
    Timer manager class for MMS.
    Schedule loop in reactor.

    Usage:
        def foo(p1, p2):
            pass

        task = PeriodicTask()
        task.set_period(period=0.1)
        try:
            is_ready = task.schedule(foo, {"p1": 1, "p2": 2})
            if is_ready:
                task.start()

        except Exception as e:
            self.log_error(f"error:{e}")

        # In another process, stop the task
        task.stop()

    Example:
        func = self.log_pin_adc
        params = {"target_pin":"buffer:PA2"}

        task = PeriodicTask()
        task.set_period(period=0.1)
        try:
            is_ready = task.schedule(func, params)
            if is_ready:
                task.start()
        except Exception as e:
            self.log_error(f"error:{e}")
    """
    def __init__(self):
        self.reactor = printer_adapter.get_reactor()

        # The function to be executed periodically
        self.func = None
        # The parameters to be passed to the function
        self.params = None
        # An optional callback function to be called with the result of func
        self.callback = None
        # An optional callback function to be called when task is timeout
        self.timeout_callback = None

        # The timer object registered with the reactor
        self.timer = None
        # A boolean indicating whether the task is currently running
        self.running = False

        task_config = TaskConfig()
        # The interval in seconds between executions of the function
        self.period = task_config.default_period

        self.start_at = None
        # Task timeout limit, in seconds
        self.timeout = None

        self.mms_logger = None

    def _initialize_loggers(self):
        if not self.mms_logger:
            self.mms_logger = printer_adapter.get_mms_logger()
            self.log_info = self.mms_logger.create_log_info(
                console_output=False)
            self.log_warning = self.mms_logger.create_log_warning()
            self.log_error = self.mms_logger.create_log_error()

    def set_period(self, period):
        if not self.running:
            self.period = period

    def set_timeout(self, timeout):
        if not self.running:
            self.timeout = timeout

    def schedule(
        self, func,
        params=None, callback=None, timeout_callback=None
    ):
        """
        Schedule a periodic task.
        Args:
            func (callable): The function to be scheduled.
            params (optional): Parameters to be passed to the function.
            callback (optional): A callback function to be executed after
                                 the scheduled function.
        Returns:
            bool: True if the task was successfully scheduled
                  False if a task is already running.

        Notice:
            "func" should not be @ by a decorator, such as contextmanager,
            which may return a generator but not the wanner function.

            E.g.
                @contextmanager
                def foo():
                    yield

                schedule(func=foo)

                # self.func => <contextlib._GeneratorContextManager object>
        """
        self._initialize_loggers()

        if self.func or self.timer:
            self.log_warning(
                f"periodic task func:{self.func} exists and running, skip...")
            return False

        self.func = func
        self.params = params
        self.callback = callback
        self.timeout_callback = timeout_callback
        return True

    def _teardown(self):
        """
        Clean up the MMS service by unregistering the timer and clearing
        the function references.
        """
        if self.timer and self.reactor:
            try:
                self.reactor.unregister_timer(self.timer)
            except Exception:
                pass
            self.timer = None

        if self.func:
            self.func = None
            self.params = None
            self.callback = None
            self.timeout_callback = None

    def get_next_waketime(self):
        return self.reactor.monotonic() + self.period

    def _execute(self, eventtime):
        """
        Executes the periodic task function and handles the timer.
        Args:
            eventtime (float): The current event time.
        """
        if self.func is None:
            self.log_warning(f"periodic task func not exists, return")
            return self.reactor.NEVER

        if self.timer is None:
            self.log_warning(f"periodic task timer not exists, return")
            return self.reactor.NEVER

        try:
            # Check for timeout
            if self.timeout is not None:
                if (time.time() - self.start_at) > self.timeout:
                    self.log_warning(
                        f"periodic task '{self.func}' timeout, return")
                    if self.timeout_callback:
                        self.timeout_callback()
                    self._teardown()
                    self.running = False
                    return self.reactor.NEVER

            # Execute the function
            result = self.func(**self.params) \
                if self.params is not None \
                else self.func()

            if self.callback:
                self.callback(result)

            # Schedule the next execution
            return self.get_next_waketime()

        except Exception as e:
            self.log_error(f"periodic task '{self.func}' execute error: {e}")
            self._teardown()
            self.running = False
            return self.reactor.NEVER

    def start(self):
        if not self.func:
            self.log_warning("periodic task func not exists, return")
            return False
        if self.running:
            self.log_warning(f"periodic task is running, return")
            return False

        self.running = True
        self.start_at = time.time()
        self.timer = self.reactor.register_timer(
            callback = self._execute,
            waketime = self.get_next_waketime()
        )
        return True

    def stop(self):
        if not self.running:
            return False
        self.running = False

        self._teardown()
        self.start_at = None
        return True

    def is_running(self):
        return self.running
