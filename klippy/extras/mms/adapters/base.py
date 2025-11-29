# Base adapter for adapters
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging

from ..klippy import GlobalKlippy


class BaseAdapter:
    def __init__(self):
        self._printer = None
        self._reactor = None
        self._config = None

    @property
    def printer(self):
        """Get validated printer instance"""
        self._check_printer()
        return self._printer

    @property
    def reactor(self):
        """Get validated reactor instance"""
        self._check_printer()
        return self._reactor

    @property
    def config(self):
        """Get validated config instance"""
        self._check_printer()
        return self._config

    def _check_printer(self):
        printer = GlobalKlippy.validate_printer()
        if self._printer is printer:
            return False

        self._printer = printer
        self._reactor = printer.get_reactor()
        self._config = GlobalKlippy.validate_config()
        self._setup_logger()
        return True

    def _setup_logger(self):
        return

    # -- Get printer objects --
    def get_obj(self, obj_name):
        try:
            return self.printer.lookup_object(obj_name)
        except Exception as e:
            logging.error(f"Object '{obj_name}' lookup failed: {e}")
        return None

    def create_obj(self, obj_name, config=None):
        logging.warning(f"Object '{obj_name}' is creating from config...")
        try:
            return self.printer.load_object(config or self.config, obj_name)
        except Exception as e:
            raise RuntimeError(f"Object '{obj_name}' not exists in printer!!!")

    def safe_get(self, obj_name):
        # Check if dealing with a new printer
        # is_new = self._check_printer()
        # Existing printer
        # # Check if attribute already exists
        # if not is_new and hasattr(self, obj_name):
        #     # Try to retrieve it
        #     obj = getattr(self, obj_name, None)
        #     if obj is not None:
        #         # Success
        #         return obj

        # New printer or retrieval failed, create new object
        obj = self.get_obj(obj_name) or self.create_obj(obj_name)
        # Set the object as an attribute and validate availability
        assert obj, f"Object '{obj_name}' is unavailable in printer"

        # setattr(self, obj_name, obj)

        return obj
