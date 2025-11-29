# Global printer instance cached
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.


class GlobalKlippy:
    # Printer() instance in klippy.py
    printer = None
    config = None

    @classmethod
    def set_printer(cls, printer):
        """Set the global printer instance of klippy"""
        if printer is None:
            raise RuntimeError("Printer instance cannot be None")
        cls.printer = printer

    @classmethod
    def set_config(cls, config):
        """Set the global config instance of klippy"""
        if config is None:
            raise RuntimeError("Config instance cannot be None")
        cls.config = config

    @classmethod
    def initialize(cls, config):
        if (cls.printer is None) or (cls.printer is not config.get_printer()):
            cls.set_printer(config.get_printer())
            cls.set_config(config)

    @classmethod
    def validate_printer(cls):
        if cls.printer is None:
            raise RuntimeError("Printer is not available")
        return cls.printer

    @classmethod
    def validate_config(cls):
        if cls.config is None:
            raise RuntimeError("Config is not available")
        return cls.config
