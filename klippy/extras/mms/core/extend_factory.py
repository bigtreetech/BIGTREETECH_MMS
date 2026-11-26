# Support for MMS Extend Factory
#
# Copyright (C) 2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import configparser
import glob
import json
import os

from dataclasses import dataclass, field

from .config import PrinterConfig
from ..adapters import printer_adapter


@dataclass(frozen=True)
class ExtendMap:
    # ---- Extend id ----
    ext_id: str = "__EXT_ID__"

    # ---- SLOT ----
    slot_nums: str = "__SLOT_NUMS__"
    slot_a: str = "__SLOT_A__"
    slot_b: str = "__SLOT_B__"
    slot_c: str = "__SLOT_C__"
    slot_d: str = "__SLOT_D__"

    # ---- MCU ----
    mcu_buffer: str = "__MCU_BUFFER__"
    mcu_buffer_type: str = "__MCU_BUFFER_TYPE__"
    mcu_buffer_prop: str = "__MCU_BUFFER_PROP__"
    mcu_vivid: str = "__MCU_VIVID__"
    mcu_vivid_type: str = "__MCU_VIVID_TYPE__"
    mcu_vivid_prop: str = "__MCU_VIVID_PROP__"

    # ---- Stepper ----
    stepper_selector: str = "__STEPPER_SELECTOR__"
    stepper_drive: str = "__STEPPER_DRIVE__"

    # ---- LED ----
    led_vivid_a: str = "__RGB_VIVID_0__"
    led_vivid_b: str = "__RGB_VIVID_1__"

    # ---- RFID ----
    rfid_mfrc522_a: str = "__RFID_MFRC522_0__"
    rfid_mfrc522_b: str = "__RFID_MFRC522_1__"

    # ---- Heater ----
    controller_fan: str = "__CTRL_FAN__"
    ptc_temper: str = "__PTC_TEMPER__"
    dryer_vivid_l: str = "__DRYER_VIVID_L__"
    dryer_vivid_r: str = "__DRYER_VIVID_R__"
    dryer_vivid: str = "__DRYER_VIVID__"
    dryer_vivid_fan: str = "__DRYER_VIVID_FAN__"


@dataclass(frozen=True)
class ExtendFileName:
    template_file: str = "mms-extend.template"
    json_file: str = "mms-extend.json"
    output_file: str = "mms-extend.cfg"
    factory_file: str = "mms-extend-factory.cfg"


class MMSExtendFile:
    def __init__(self):
        # Setup file path and name
        ext_fname = ExtendFileName()
        self.template_file = ext_fname.template_file
        self.json_file = ext_fname.json_file
        self.output_file = ext_fname.output_file
        self.factory_file = ext_fname.factory_file

        # File full path cache
        self._full_path = {
            self.template_file: None,
            self.json_file: None,
            self.factory_file: None,
        }

        # Get Klippy config path from printer
        cfg_path = printer_adapter.get_klippy_configfile()
        # base_dir should be "/home/.../printer_data/config/"
        self.base_dir = os.path.dirname(cfg_path) if cfg_path else None

        self.emap = ExtendMap()

    # ---- Format ----
    def format_replace_map(self, ext_id, slot_nums, buffer, vivid):
        return {
            self.emap.ext_id: str(ext_id),

            self.emap.slot_nums: ','.join(slot_nums),
            self.emap.slot_a: slot_nums[0],
            self.emap.slot_b: slot_nums[1],
            self.emap.slot_c: slot_nums[2],
            self.emap.slot_d: slot_nums[3],

            self.emap.mcu_buffer: f"buffer_{ext_id}",
            self.emap.mcu_buffer_type: buffer.get('type', 'serial'),
            self.emap.mcu_buffer_prop: buffer.get('property', ''),
            self.emap.mcu_vivid: f"vivid_{ext_id}",
            self.emap.mcu_vivid_type: vivid.get('type', 'serial'),
            self.emap.mcu_vivid_prop: vivid.get('property', ''),

            self.emap.stepper_selector: f"selector_stepper_{ext_id}",
            self.emap.stepper_drive: f"drive_stepper_{ext_id}",

            self.emap.led_vivid_a: f"vivid_rgb_{ext_id}_0",
            self.emap.led_vivid_b: f"vivid_rgb_{ext_id}_1",

            self.emap.rfid_mfrc522_a: f"mfrc522_{ext_id}_0",
            self.emap.rfid_mfrc522_b: f"mfrc522_{ext_id}_1",

            self.emap.controller_fan: f"my_controller_fan_{ext_id}",
            self.emap.ptc_temper: f"_PTC_Temper_{ext_id}",
            self.emap.dryer_vivid_l: f"ViViD_Dryer_L_{ext_id}",
            self.emap.dryer_vivid_r: f"ViViD_Dryer_R_{ext_id}",
            self.emap.dryer_vivid: f"ViViD_Dryer_{ext_id}",
            self.emap.dryer_vivid_fan: f"ViViD_Dryer_Fan_{ext_id}",
        }

    # ---- Path ----
    def _find_full_path(self, filename):
        if not self.base_dir or not filename:
            return None

        # Full path is cached, just return
        f_full_path = self._full_path.get(filename, None)
        if f_full_path:
            return f_full_path

        # Check and cache
        fname = os.path.basename(filename)
        for root, _, files in os.walk(self.base_dir):
            if fname in files:
                full_path = os.path.join(root, fname)
                if fname in full_path:
                    # Cache
                    self._full_path[fname] = full_path
                    return full_path

        # Found nothing
        return None

    # ---- Read ----
    def _read_file(self, filename):
        full_path = self._find_full_path(filename)
        if not full_path:
            return None

        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()

    def load_json(self):
        content = self._read_file(self.json_file)

        return json.loads(content) if content else {}

    def load_template(self):
        return self._read_file(self.template_file)

    # ---- Write ----
    def _atomic_write_file(self, full_path, content):
        tmp_path = full_path + ".tmp"
        tmp = open(tmp_path, 'w', encoding='utf-8')
        try:
            with tmp as f:
                f.write(content)
            os.replace(tmp_path, full_path)
        except OSError:
            os.unlink(tmp_path)
            raise

    def install_output(self, filename, render):
        factory = self._find_full_path(self.factory_file)
        if not factory:
            raise ValueError(f"Missing {self.factory_file}")
        with open(factory, encoding="utf-8") as f:
            config = f.read()
        # Klipper resolves this include relative to its containing file.
        output = os.path.join(os.path.dirname(factory), filename)
        try:
            generate = os.path.getsize(output) == 0
        except FileNotFoundError:
            generate = True
        if generate:
            self._atomic_write_file(output, render())

        # Match Klipper's include syntax and Linux path/glob case rules.
        for line in config.splitlines():
            match = configparser.RawConfigParser.SECTCRE.match(
                line.partition("#")[0])
            if match and match.group("header").startswith("include "):
                spec = match.group("header")[8:].strip()
                pattern = os.path.join(os.path.dirname(factory), spec)
                if any(os.path.samefile(path, output)
                       for path in glob.iglob(pattern)):
                    return generate
        self._atomic_write_file(factory, f"{config}\n[include {filename}]\n")
        return True


############################
# JSON File parse style
############################
class MMSExtendFactoryJSON:
    def __init__(self, config):
        self.ext_file = MMSExtendFile()
        # Check slots count
        self.slots_count = 4

    def _parse_extend(self, extend_dct):
        return (
            extend_dct.get("id"),
            [str(v) for v in extend_dct.get("slot_nums", [])],
            extend_dct.get("mcu_buffer", {}),
            extend_dct.get("mcu_vivid", {})
        )

    def _render(self):
        """
        JSON File Example:
        {
            "mms_extend": [
                {
                    "extend": {
                        "id": 1,
                        "slot_nums": [4,5,6,7],
                        "mcu_buffer": {
                            "type": "serial",
                            "property": "/dev/serial/by-id/usb-..."
                        },
                        "mcu_vivid": {
                            "type": "serial",
                            "property": "/dev/serial/by-id/usb-..."
                        }
                    }
                }
            ]
        }
        """
        template = self.ext_file.load_template()
        json_dct = self.ext_file.load_json()

        # Schema
        if not template or not json_dct:
            raise ValueError("Missing extension template or JSON data")

        mms_extend_lst = json_dct.get("mms_extend", [])
        if not mms_extend_lst:
            raise ValueError("No 'mms_extend' in JSON")

        # Render multiple extends
        render_content = ""

        for extend in mms_extend_lst:
            extend_list = extend['extend']
            ext_id, slot_nums, buffer, vivid = \
                self._parse_extend(extend_list)

            if ext_id is None:
                raise ValueError(f"Missing extend id: {extend}")

            if len(slot_nums) != self.slots_count:
                raise ValueError(
                    f"length of slot_nums should be {self.slots_count}, "
                    f"but have {len(slot_nums)}"
                )

            replace_map = self.ext_file.format_replace_map(
                ext_id, slot_nums, buffer, vivid)

            content = template
            for placeholder, value in replace_map.items():
                content = content.replace(placeholder, value)

            render_content += content

        return render_content

    def output(self):
        if self.ext_file.install_output(self.ext_file.output_file,
                                        self._render):
            printer_adapter.get_printer().request_exit("restart")


############################
# CFG File configured style
############################
@dataclass(frozen=True)
class PrinterExtFConfig(PrinterConfig):
    """ Configuration values in mms-extend-factory.cfg """
    slot_nums: list = field(default_factory=list)
    # "serial" for USB, "canbus_uuid" for CAN
    mcu_buffer_type: str = "serial"
    mcu_buffer_id: str = ""
    # "serial" for USB, "canbus_uuid" for CAN
    mcu_vivid_type: str = "serial"
    mcu_vivid_id: str = ""


class MMSExtendFactory:
    def __init__(self, config):
        name = config.get_name()
        self.ext_id = int(name.split()[-1])

        self.ext_file = MMSExtendFile()

        extf_config = PrinterExtFConfig(config).gen_packaged_config()
        # Function config.getintlist() return a tuple, trans to list
        self.slot_nums = [str(i) for i in extf_config.slot_nums]
        self.mcu_buffer_type = extf_config.mcu_buffer_type
        self.mcu_buffer_id = extf_config.mcu_buffer_id
        self.mcu_vivid_type = extf_config.mcu_vivid_type
        self.mcu_vivid_id = extf_config.mcu_vivid_id

        if len(self.slot_nums) != 4:
            raise ValueError("slot_nums must contain four slot numbers")

    def _render(self):
        template = self.ext_file.load_template()
        if not template:
            raise ValueError("Missing extension template")

        replace_map = self.ext_file.format_replace_map(
            ext_id = self.ext_id,
            slot_nums = self.slot_nums,
            buffer = {
                "type" : self.mcu_buffer_type,
                "property" : self.mcu_buffer_id
            },
            vivid = {
                "type" : self.mcu_vivid_type,
                "property" : self.mcu_vivid_id
            }
        )

        content = template
        for placeholder, value in replace_map.items():
            content = content.replace(placeholder, value)

        return content

    def output(self, config):
        if config.has_section(f"mms extend {self.ext_id}"):
            return False
        name, suffix = os.path.splitext(self.ext_file.output_file)
        return self.ext_file.install_output(f"{name}-{self.ext_id}{suffix}",
                                            self._render)


def load_config(config):
    section = config
    try:
        current = MMSExtendFactory(config)
        sections = config.get_prefix_sections("mms extend_factory ")
        if config.get_name() == sections[0].get_name():
            # A failed section must not restart an incompletely prepared set.
            changed = current.output(config)
            for section in sections[1:]:
                changed = MMSExtendFactory(section).output(section) or changed
            if changed:
                printer_adapter.get_printer().request_exit("restart")
    except (OSError, ValueError) as e:
        raise config.error(f"Unable to load [{section.get_name()}]: {e}") from e
    return current
