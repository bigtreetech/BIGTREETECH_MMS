# Package definition for the extras/mms directory
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging

from .core import (
    extend,
    logger,
    slot,
)
from .hardware import (
    led,
    rfid
)
from .motion import (
    autoload,
    delivery
)
from .swap import (
    brush,
    charge,
    cut,
    eject,
    purge,
    swap
)
from .klippy import GlobalKlippy
from . import mms


def load_config(config):
    GlobalKlippy.initialize(config)
    logging.info(
        f"MMS module 'mms' load from config section [{config.get_name()}]")
    return mms.load_config(config)


def load_config_prefix(config):
    load_map = {
        "extend": extend.load_config,
        "logger": logger.load_config,
        "slot": slot.load_config,

        "led": led.load_config,
        "rfid": rfid.load_config,

        "autoload": autoload.load_config,
        "delivery": delivery.load_config,

        "brush": brush.load_config,
        "charge": charge.load_config,
        "cut": cut.load_config,
        "eject": eject.load_config,
        "purge": purge.load_config,
        "swap": swap.load_config,
    }

    GlobalKlippy.initialize(config)

    section_name = config.get_name()
    module_lst = section_name.split()

    for name,load_func in load_map.items():
        if name in module_lst:
            logging.info(
                f"MMS module '{name}' load from config"
                f" section [{section_name}]")
            return load_func(config)

    raise config.error(
        f"Section name [{section_name}] is not valid."
        "Please check it out.")
