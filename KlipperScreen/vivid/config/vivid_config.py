import logging
from dataclasses import dataclass
from random import choice


@dataclass
class ColorConfig:
    red: str = "#FF0000"
    orange: str = "#FF7A00"
    green: str = "#2CDA29"
    purple: str = "#AE46FF"
    yellow: str = "#EECB07"
    blue: str = "#1E88E5"
    cyan: str = "#00BCD4"
    teal: str = "#009688"
    pink: str = "#E91E63"
    indigo: str = "#3F51B5"
    lime: str = "#8BC34A"
    amber: str = "#FFC107"
    brown: str = "#795548"
    gray: str = "#607D8B"
    dark_orange: str ="#FF5722"
    dark_red: str = "#8C0000"
    dark_blue: str = "#0D47A1"
    dark_green: str = "#1B5E20"
    gold: str = "#FFD700"
    silver: str = "#C0C0C0"
    coral: str = "#FF6B6B"
    turquoise: str = "#40E0D0"
    violet: str = "#9C27B0"
    forest: str = "#228B22"
    crimson: str = "#DC143C"
    white: str = "#FFFFFF"
    black: str = "#000000"

    @classmethod
    def get_color_hex(cls, color_name):
        """Get color value by name, case-insensitive"""
        color_name = color_name.lower().replace(" ", "_")
        return getattr(cls, color_name, "#FFFFFF")

    @classmethod
    def all_colors(cls):
        """Get all available colors as a dict"""
        return {f: getattr(cls, f) for f in cls.__dataclass_fields__}

    @classmethod
    def get_rand_color(cls):
        return choice(list(cls.all_colors().values()))


@dataclass
class MaterialConfig:
    name: str
    # Example: #FFFFFF
    display_color: str
    # Example: 30.0°C
    temperature: float
    # In seconds
    heat_duration: int
    # Optional
    css_class: str = None


@dataclass
class SlotConfig:
    number: int
    display_color: str
    # Material name as string for simplicity
    material: str


class VividConfig:
    """Simple configuration container for ViViD"""

    # Predefined material configurations
    MATERIALS = [
        MaterialConfig(
            name="ABS",
            display_color=ColorConfig.green,
            temperature=55,
            heat_duration=14400, # 4 Hours
            css_class="vvd-filament-btn-abs"
        ),
        MaterialConfig(
            name="ASA",
            display_color=ColorConfig.orange,
            temperature=55,
            heat_duration=14400, # 4 Hours
            css_class="vvd-filament-btn-asa"
        ),
        MaterialConfig(
            name="PLA",
            display_color=ColorConfig.yellow,
            temperature=45,
            heat_duration=14400, # 4 Hours
            css_class="vvd-filament-btn-pla"
        ),
        MaterialConfig(
            name="PETG",
            display_color=ColorConfig.purple,
            temperature=50,
            heat_duration=14400, # 4 Hours
            css_class="vvd-filament-btn-petg"
        ),
    ]

    # Predefined slot configurations
    SLOTS = {
        0 : SlotConfig(number=0, display_color=ColorConfig.green, material="ABS"),
        1 : SlotConfig(number=1, display_color=ColorConfig.orange, material="ASA"),
        2 : SlotConfig(number=2, display_color=ColorConfig.yellow, material="PLA"),
        3 : SlotConfig(number=3, display_color=ColorConfig.purple, material="PETG")
    }

    @classmethod
    def get_material(cls, name):
        """Get material configuration by name"""
        for material in cls.MATERIALS:
            if material.name == name:
                return material
        raise ValueError(f"Material not found: {name}")

    @classmethod
    def get_slot_material_config(cls, slot_number):
        """Get material config for a specific slot"""
        slot = cls.get_slot(slot_number)
        return cls.get_material(slot.material)

    @classmethod
    def get_slot(cls, slot_number):
        """Get slot configuration by slot number"""
        # for slot in cls.SLOTS:
        for slot_num, slot in cls.SLOTS.items():
            if slot_num == slot_number:
                return slot
        raise ValueError(f"Slot number not found: {slot_number}")

    # @classmethod
    # def update_slots(cls, slot_pins_status):
    #     is_updated = False
    #     current_slots = cls.SLOTS.keys()

    #     for slot_num in slot_pins_status.keys():
    #         if slot_num not in current_slots:
    #             new_slot = SlotConfig(
    #                 number = slot_num, 
    #                 display_color = ColorConfig.get_rand_color(), 
    #                 material = choice(["ABS", "PLA", "ASA", "PETG"])
    #             )
    #             cls.SLOTS[slot_num] = new_slot
    #             is_updated = True

    #         logging.info(f"slot_num:{slot_num}, cls.SLOTS:{cls.SLOTS}")

    #     return is_updated