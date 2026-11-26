# Support for MMS Service
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass

import greenlet

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
        self.stop_when = None
        # Each accepted start owns a fresh completion until its callback exits.
        self.completion = None

        self.running = False
        self._cancel_requested = False
        self._execution_greenlet = None
        self.mms_logger = None

    def _initialize_loggers(self):
        if not self.mms_logger:
            self.mms_logger = printer_adapter.get_mms_logger()
            self.log_info = self.mms_logger.create_log_info(
                console_output=False)
            self.log_warning = self.mms_logger.create_log_warning()
            self.log_error = self.mms_logger.create_log_error()

    def setup(self, func, params=None, callback=None, stop_when=None):
        """
        Sets up the asynchronous task with the given function and parameters.
        Returns True if the setup was successful, False if a task is
        already running.
        """
        self._initialize_loggers()

        if self.running:
            self.log_warning(
                f"async task func:{self.func} exists and running, skip...")
            return False

        self.func = func
        self.params = params
        self.callback = callback
        self.stop_when = stop_when
        return True

    def _complete(self, completion, result):
        """
        Completes the asynchronous task by signaling the reactor and
        resetting the task state.
        """
        if completion is not self.completion or not self.running:
            return
        self.func = None
        self.params = None
        self.callback = None
        self.stop_when = None

        self.running = False
        self._cancel_requested = False
        self._execution_greenlet = None
        self.reactor.async_complete(completion, result)

    def _execute(self, eventtime, completion):
        """
        Executes the asynchronous task.
        eventtime (float): The time at which the event is executed.
        """
        # A delayed callback may belong to a previously completed start.
        if completion is not self.completion or not self.running:
            return
        if self._cancel_requested:
            self._complete(completion, -1)
            return -1

        func, params, callback = self.func, self.params, self.callback
        self._execution_greenlet = greenlet.getcurrent()
        result = None
        try:
            result = func(**params) if params is not None else func()
        except Exception as e:
            self.log_error(f"async task error:{e}")
            result = False

        # A running function cannot be forcibly unwound. Keep ownership until
        # it exits, and skip a callback that has not begun when stop is asked.
        if callback and not self._cancel_requested:
            try:
                callback(result)
            except Exception as e:
                self.log_error(f"async task callback error:{e}")
                result = False

        # Completion means the function ended, not that its result is true.
        if self._cancel_requested:
            result = -1
        self._complete(completion, result)
        return result

    def start(self):
        if not self.func:
            self.log_warning("async task func not exists, return")
            return False

        if self.running:
            self.log_warning("async task func is running, return")
            return False

        completion = self.reactor.completion()
        self.completion = completion
        self._cancel_requested = False
        self.running = True
        try:
            self.reactor.register_async_callback(
                lambda eventtime: self._execute(eventtime, completion))
        except Exception:
            self._complete(completion, False)
            raise
        return completion

    def stop(self):
        """Request cancellation; completion signals actual callback exit.

        The operation's owner must stop its physical actions cooperatively.
        Setup/start stay unavailable until the old execution has unwound.
        """
        if not self.running:
            return False

        self._cancel_requested = True
        return True

    def current_is_cancelled(self):
        # Other reactor callbacks can run while this task is waiting. Its
        # cancellation must not leak into those unrelated callers.
        return (self._execution_greenlet is greenlet.getcurrent()
                and (self._cancel_requested
                     or self.stop_when is not None and self.stop_when()))

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
        # An optional callback function to be called when task is timeout
        self.timeout_callback = None

        # The timer object registered with the reactor
        self.timer = None
        # A boolean indicating whether the task is currently running
        self.running = False
        # Stopping a timer does not unwind an entered callback. Keep its
        # ownership until it returns, including any reactor waits in it.
        self._execution_greenlet = None

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
        if not self.is_running():
            self.period = period

    def set_timeout(self, timeout):
        if not self.is_running():
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

        if self.func or self.timer or self._execution_greenlet is not None:
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
        if self.timer:
            self.reactor.unregister_timer(self.timer)
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
        Returns:
            float: The next wake time for the timer, or reactor.NEVER
            if the timer no longer exists.
        """
        if self.func is None or self.timer is None or not self.running:
            self.log_warning(f"periodic task func or timer not exists, exit")
            return self.reactor.NEVER

        self._execution_greenlet = greenlet.getcurrent()
        try:
            try:
                result = self.func(**self.params) \
                    if self.params is not None else self.func()
                if self.callback:
                    self.callback(result)
            except Exception as e:
                self.log_error(f"periodic task error:{e}, exit")
                self.stop()
                return self.reactor.NEVER

            if self.timer is None:
                return self.reactor.NEVER
            if self.timeout is not None and self.start_at is not None:
                if self.reactor.monotonic() - self.start_at > self.timeout:
                    self.log_info(f"periodic task execution timeout, exit")
                    if self.timeout_callback:
                        self.timeout_callback()
                    self.stop()
                    return self.reactor.NEVER
            return self.get_next_waketime()
        finally:
            self._execution_greenlet = None

    def start(self):
        if not self.func:
            self.log_warning("periodic task func not exists, return")
            return False
        if self.is_running():
            self.log_warning(f"periodic task is running, return")
            return False

        self.running = True
        self.start_at = self.reactor.monotonic()
        self.timer = self.reactor.register_timer(
            callback = self._execute,
            waketime = self.get_next_waketime()
        )
        return True

    def stop(self):
        if not self.running and self.func is None and self.timer is None:
            return False
        self._teardown()
        self.running = False
        self.start_at = None
        return True

    def is_running(self):
        return self.running or self._execution_greenlet is not None

    def wait_stopped(self, deadline):
        """Join an entered callback after stop(); never wait for ourselves."""
        if self._execution_greenlet is greenlet.getcurrent():
            return False
        while self._execution_greenlet is not None:
            now = self.reactor.monotonic()
            if now >= deadline:
                return False
            self.reactor.pause(min(deadline, now + 0.05))
        return not self.running
