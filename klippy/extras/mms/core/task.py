# Support for MMS Service
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import time
from dataclasses import dataclass

from ..adapters import printer_adapter


@dataclass(frozen=True)
class TaskConfig:
    # The default interval for rescheduling tasks, in seconds
    default_period: float = 0.25


class AsyncTask:
    """
    A class to run asynchronous functions in a reactor.

    Example:
        func = self.log_pin_adc
        params = {"target_pin":"buffer:PA2"}

        task = AsyncTask()
        try:
            is_ready = task.setup(func, params)
            if is_ready:
                task.start()
        except Exception as e:
            self.log_error(f"error:{e}")
    """
    def __init__(self):
        self.reactor = printer_adapter.get_reactor()

        # The function to be executed asynchronously
        self.func = None
        # The parameters to be passed to the function
        self.params = None
        # An optional callback function to be called with the result of func
        self.callback = None
        # The completion event for the async task
        self.completion = self.reactor.completion()

        self.running = False
        self.mms_logger = None

    def _initialize_loggers(self):
        if not self.mms_logger:
            self.mms_logger = printer_adapter.get_mms_logger()
            self.log_info = self.mms_logger.create_log_info(
                console_output=False)
            self.log_warning = self.mms_logger.create_log_warning()
            self.log_error = self.mms_logger.create_log_error()

    def setup(self, func, params=None, callback=None):
        """
        Sets up the asynchronous task with the given function and parameters.
        Returns True if the setup was successful, False if a task is
        already running.
        """
        self._initialize_loggers()

        if self.func and self.running:
            self.log_warning(
                f"async task func:{self.func} exists and running, skip...")
            return False

        self.func = func
        self.params = params
        self.callback = callback
        return True

    def _complete(self, result):
        """
        Completes the asynchronous task by signaling the reactor and
        resetting the task state.
        """
        self.reactor.async_complete(self.completion, result)
        self.func = None
        self.params = None
        self.callback = None

        # Update running to stop the task
        self.running = False
        # self.log_info(
        #     f"async task complete func:{self.func}"
        #     f" at {self.reactor.monotonic()}")

    def _execute(self, eventtime):
        """
        Executes the asynchronous task.
        eventtime (float): The time at which the event is executed.
        """
        # self.log_info(f"async task executed func:{self.func} at {eventtime}")
        result = None
        try:
            result = self.func(**self.params) \
                if self.params is not None \
                else self.func()
        except Exception as e:
            self.log_error(f"async task error:{e}")

        # No matter func task is success or not, call callback
        if self.callback:
            try:
                self.callback(result)
            except Exception as e:
                self.log_error(f"async task callback error:{e}")

        self._complete(result or 1)
        return result

    def start(self):
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


class PeriodicTask:
    """
    Timer manager class for MMS.
    Schedule loop in reactor.

    In the PeriodicTask:

    - Non-blocking but non-precise:
        Task execution is non-blocking to the reactor, but won't strictly
        adhere to the set period when overrun occurs.

    - Execution mechanism:
        If a task exceeds its period (e.g. runs 20ms for 10ms period),
        the next execution waits until current completion.
        This is enforced by reactor's timer system - it processes next
        event only after current callback finishes.

    - Timer logic:
        Next waketimeis calculated based on task completion
        time (now + period), not fixed intervals.
        Visible in _execute() where update_timer() is called post-execution

    - vs Threads:
        Threaded solutions maintain period precision (parallel execution).
        Reactor's single-threaded event loop must complete tasks sequentially.

    Key implication:
        This design prevents task pileup but trades off timing precision,
        which is characteristic of event-loop architectures.

    Usage:
        Start a PeriodicTask
            task.schedule() -> task.start()
        Stop a PeriodicTask
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

    def schedule(self, func, params=None, callback=None):
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
        return True

    def _teardown(self):
        """
        Clean up the MMS service by unregistering the timer and clearing
        the function references.
        """
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None

        if self.func:
            self.func = None
            self.params = None
            self.callback = None

    def get_next_waketime(self):
        return self.reactor.monotonic() + self.period

    def _execute(self, eventtime):
        """
        Executes the periodic task function and handles the timer.
        Args:
            eventtime (float): The current event time.
        Returns:
            float: The next wake time for the timer, or reactor.NEVER
            if the timer no longer exists.
        """
        if self.func is None or self.timer is None:
            self.log_warning(f"periodic task func or timer not exists, exit")
            return self.reactor.NEVER

        try:
            result = self.func(**self.params) \
                if self.params is not None \
                else self.func()
            # self.log_info(
            #     f"periodic task executed func:{self.func} at {eventtime}")

            if self.callback:
                self.callback(result)

        except Exception as e:
            self.log_error(f"periodic task error:{e}, exit")
            self.stop()
            return self.reactor.NEVER

        # Check timer again after func is executed
        if self.timer is None:
            self.log_info(f"periodic task timer not exists, exit")
            return self.reactor.NEVER

        if self.timeout is not None and self.start_at is not None:
            if time.time() - self.start_at > self.timeout:
                self.log_info(f"periodic task execution timeout, exit")
                self.stop()
                return self.reactor.NEVER

        # Re-register the timer for the next execution
        waketime = self.get_next_waketime()
        # self.log_info(f"periodic task next waketime: {waketime}")
        # self.reactor.update_timer(self.timer, waketime)
        return waketime

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
