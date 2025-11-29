# Support for MMS Logger
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field, fields
from datetime import datetime
from functools import wraps

from ..adapters import gcode_adapter, printer_adapter


@dataclass(frozen=True)
class LoggerConfig:
    # Must be first line, printer_config is the param of Config object
    printer_config: object

    # Skip configs use in __post_init__()
    skip_configs = ["printer_config", ]
    # ==== configuration values in *.cfg, must set default  ====
    # Log file name
    filename: str = "mms.log"
    # Log rotation timing (see TimedRotatingFileHandler)
    rotate_when: str = "midnight"
    # Maximum number of backup logs to keep
    backup_count: int = 5

    def __post_init__(self):
        type_method_map = {
            int: "getint",
            str: "get",
        }

        for field_info in fields(self):
            field_name = field_info.name
            field_type = field_info.type

            if field_name in self.skip_configs:
                continue

            # Default type is int
            get_method = type_method_map.get(field_type, "getint")
            config_value = getattr(self.printer_config, get_method)(field_name)
            object.__setattr__(self, field_name, config_value)


class MMSLogHandler(logging.handlers.TimedRotatingFileHandler):
    """
    Custom log handler with background processing and formatted messages.

    Features:
    - Timed log rotation
    - Background thread for non-blocking I/O
    - Structured message formatting
    """
    def __init__(self, filename, rotate_when, backup_count):
        super().__init__(
            filename=filename,
            when=rotate_when,
            backupCount=backup_count)

        # Datetime format for log entries
        self.date_format = "%Y-%m-%d %H:%M:%S.%f"

        self._bg_queue = queue.Queue()
        self._bg_thread = None
        self._start_background_thread()

    def _start_background_thread(self):
        """Initialize and start background processing thread."""
        self._bg_thread = threading.Thread(target=self._process_queue)
        self._bg_thread.start()

    def _process_queue(self):
        """Background thread processing log records from queue."""
        while True:
            record = self._bg_queue.get(block=True)
            if record is None:
                # Termination sentinel
                break
            self.handle(record)

    def format_message(self, level, func_name, message):
        """
        Create standardized log message format.

        Args:
            level: Logging level constant (e.g. logging.INFO)
            func_name: Name of calling function
            message: Actual log content

        Returns:
            Formatted log string
        """
        timestamp = datetime.now().strftime(self.date_format)[:-3]
        level_name = logging.getLevelName(level)
        return f"{timestamp} {level_name} {func_name} : {message}"

    def enqueue_record(self, level, func_name, message):
        """
        Add log record to processing queue.

        Args:
            level: Logging level constant
            func_name: Calling function name
            message: Log message content
        """
        try:
            formatted_msg = self.format_message(level, func_name, message)
            record = logging.makeLogRecord({
                "levelno": level,
                "levelname": logging.getLevelName(level),
                "msg": formatted_msg,
                "funcName": func_name
            })
            self._bg_queue.put_nowait(record)
            # record = logging.makeLogRecord(
            #     {"level":level, "msg":formatted_msg})
            # self.emit(record)
        except Exception as e:
            self.handleError(e)

    def close(self):
        """Gracefully shutdown handler and background thread."""
        # Send termination signal
        self._bg_queue.put_nowait(None)
        if self._bg_thread:
            # self._bg_thread.join(timeout=5)
            self._bg_thread.join()
        super().close()


class MMSLogger:
    """
    Main logger interface for application integration.

    Provides:
    - Multiple log levels (INFO, WARNING, ERROR)
    - Console output integration
    - Dynamic logger creation
    """
    def __init__(self, config):
        self.logger_config = LoggerConfig(config)
        self.level_map = {
            logging.INFO: self.log_info,
            logging.WARNING: self.log_warning,
            logging.ERROR: self.log_error
        }
        self._handler = None
        self._initialize_handler()

    def _initialize_handler(self):
        """Set up log file handler with proper paths."""
        log_dir = os.path.dirname(printer_adapter.get_klippy_logfile())
        full_path = os.path.join(log_dir, self.logger_config.filename)
        self._handler = MMSLogHandler(
            filename = full_path,
            rotate_when = self.logger_config.rotate_when,
            backup_count = self.logger_config.backup_count
        )

    def teardown(self):
        """Clean up logging resources."""
        if self._handler:
            self._handler.close()
            self._handler = None

    # def get_func_name(self):
    #     return sys._getframe().f_code.co_name

    def log(self, level, func_name, message):
        """
        Unified logging method for all levels.

        Args:
            level: Logging level constant
            func_name: Calling function name
            message: Log message content
        """
        if self._handler:
            self._handler.enqueue_record(level, func_name, message)

    # Syntactic sugar methods
    def log_info(self, func_name, message):
        self.log(logging.INFO, func_name, message)

    def log_warning(self, func_name, message):
        self.log(logging.WARNING, func_name, message)

    def log_error(self, func_name, message):
        self.log(logging.ERROR, func_name, message)

    # Factory Loggers
    def create_logger(self, level=logging.INFO, console_output=False):
        """
        Factory method for creating leveled loggers.

        Args:
            level: Desired logging level
            console_output: Mirror logs to console

        Returns:
            Configured logging function
        """
        log_method = self.level_map.get(level, self.log_info)

        def logger(message):
            """Generated logging function with caller context."""
            # Get father caller func name with _getframe(1)
            caller = sys._getframe(1).f_code.co_name
            log_method(caller, message)
            if console_output:
                gcode_adapter.console_print(str(message), log=False)

        return logger

    def create_log_info(self, console_output=False):
        return self.create_logger(logging.INFO, console_output)

    def create_log_warning(self, console_output=False):
        return self.create_logger(logging.WARNING, console_output)

    def create_log_error(self, console_output=False):
        return self.create_logger(logging.ERROR, console_output)


def load_config(config):
    return MMSLogger(config)


def log_time_cost(log_method=None):
    """
    Factory decorator with configurable logging method.

    Usage:
        @log_time_cost()                 # Auto-detect log_info
        @log_time_cost("log_debug")      # Specify method name
        @log_time_cost(log_method=fun)   # Direct pass logger
    """
    def decorator(func):
        @wraps(func)
        def time_cost(self, *args, **kwargs):
            start = time.perf_counter()
            result = func(self, *args, **kwargs)
            elapsed = (time.perf_counter() - start) # seconds
            log_msg = f"{func.__name__} executed in {elapsed:.2f}s"

            logger = None
            if isinstance(log_method, str):
                logger = getattr(self, log_method, None)
            elif callable(log_method):
                logger = log_method
            else:
                # Default log_method is "log_info"
                logger = getattr(self, "log_info", None)

            if callable(logger):
                logger(log_msg)
            else:
                logging.info(log_msg)

            return result
        return time_cost

    if callable(log_method):
        return decorator(log_method)
    return decorator
