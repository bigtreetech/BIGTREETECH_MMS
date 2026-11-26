import configparser
import logging
import os
from tempfile import NamedTemporaryFile

from vivid.config.vivid_config import VividConfig


class VividConfigManager:
    """Centralized manager for ViViD configuration with internal state management"""

    def __init__(self, ks_config_path):
        """
        Generate ViViD config file path
        Example:
            ks_config_path: "~/printer_data/config/KlipperScreen.conf"
            _config_path -> "~/printer_data/config/KlipperScreen_vivid.conf"
        """
        self._config_path = self._generate_config_path(ks_config_path)

        self.section_prefix = "slot_"
        self.option_color = "display_color"
        self.option_material = "material"

        # Tracks unsaved changes
        self._dirty = False

        # In-memory configuration cache
        # _config_cache : {
        #     section : {
        #         option : value,
        #         option : value,
        #     }
        # }
        self._config_cache = self._initalize_config_cache()

        # Internal state for panel-specific data
        # key:slot_num, value:color/material
        self._slot_colors = {}
        self._slot_materials = {}

        # Ensure config file exists and load initial state
        self._ensure_config_exists()
        self._load_configuration()

    def _generate_config_path(self, ks_config_path):
        """Generate ViViD config file path based on main config path"""
        config_dir = os.path.dirname(ks_config_path)
        config_name = os.path.basename(ks_config_path)
        name, ext = os.path.splitext(config_name)
        return os.path.join(config_dir, f"{name}_vivid{ext}")

    def _format_section(self, slot_num):
        return self.section_prefix + str(slot_num)

    def _split_section(self, section):
        return int(section.split(self.section_prefix)[1])

    def _initalize_config_cache(self):
        _config_cache = {}

        for slot_num,slot in VividConfig.SLOTS.items():
            section = self._format_section(slot_num)
            _config_cache[section] = {
                self.option_color : slot.display_color,
                self.option_material : slot.material,
            }

        return _config_cache

    def _ensure_config_exists(self):
        """Create config file if it doesn't exist"""
        if not os.path.exists(self._config_path):
            os.makedirs(os.path.dirname(self._config_path) or ".", exist_ok=True)
            logging.info(f"Created new ViViD config: {self._config_path}")
            # Force save
            self._dirty = True
            self._save_configuration()

            return False
        return True

    def _load_configuration(self):
        """Load entire configuration into memory"""
        self._config_cache = {}

        if not os.path.exists(self._config_path):
            return

        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self._config_path, encoding="utf-8")

        for section in parser.sections():
            self._config_cache[section] = dict(parser.items(section))
            if (
                section.startswith(self.section_prefix)
                and section[len(self.section_prefix):].isdigit()
            ):
                slot_num = self._split_section(section)

                for option, value in self._config_cache[section].items():
                    # Pre-cache slot-specific values for quick access
                    if option == self.option_color:
                        self._slot_colors[slot_num] = value
                    elif option == self.option_material:
                        self._slot_materials[slot_num] = value

    def _save_configuration(self):
        """Write configuration to disk if changes exist"""
        if not self._dirty:
            return

        parser = configparser.ConfigParser(interpolation=None)

        # Reconstruct sections from cache
        for section, options in self._config_cache.items():
            parser.add_section(section)
            for option, value in options.items():
                parser.set(section, option, value)

        destination = os.path.dirname(self._config_path) or "."
        temp_path = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination, delete=False
            ) as config:
                temp_path = config.name
                parser.write(config)
            os.replace(temp_path, self._config_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

        self._dirty = False
        logging.info(f"Saved ViViD configuration to {self._config_path}")

    def get_config_path(self):
        """Get path to ViViD config file"""
        return self._config_path

    def manual_save(self):
        # Force save
        self._save_configuration()

    def manual_load(self):
        # Force reload
        self._load_configuration()

    # ---- Quick methods for Panels ----
    def get_slot_configuration(self, slot_num):
        """Load configuration for a specific slot from cache"""
        color = self._slot_colors.get(slot_num, "#FFFFFF")
        material = self._slot_materials.get(slot_num, "ABS")
        return color, material

    def get_slot_details(self, slot_num):
        options = self._config_cache.get(self._format_section(slot_num), {})
        fields = ("vendor", "name", "nozzle_temp", "bed_temp")
        return {field: options.get(field, "") for field in fields}

    def save_slot_detail(self, slot_num, field, value):
        self._load_configuration()
        self._config_cache.setdefault(self._format_section(slot_num), {})[field] = value
        self._dirty = True
        self._save_configuration()

    # def get_slots_configuration(self, offset=None, size=None):
    #     """Load configuration for all slots from cache"""
    #     if offset is None and size is None:
    #         return [
    #             (slot_num, 
    #             self._slot_colors.get(slot_num, "#FFFFFF"),
    #             self._slot_materials.get(slot_num, "ABS"))
    #             for slot_num in sorted(self._slot_colors.keys())
    #         ]
        
    #     r_min = offset*size
    #     r_max = (offset+1)*size

    #     return [
    #         (slot_num, 
    #         self._slot_colors.get(slot_num, "#FFFFFF"),
    #         self._slot_materials.get(slot_num, "ABS"))
    #         for slot_num in sorted(self._slot_colors.keys())
    #         if slot_num in range(r_min,r_max)
    #     ]
    def get_slots_configuration(self):
        """Load configuration for all slots from cache"""
        return [
            (slot_num, 
            self._slot_colors.get(slot_num, "#FFFFFF"),
            self._slot_materials.get(slot_num, "ABS"))
            for slot_num in sorted(self._slot_colors.keys())
        ]

    def get_slots_count(self):
        return len(self._slot_colors.keys())

    def update_slot_color(self, slot_num, color):
        """Update color for a slot in internal state"""
        self._slot_colors[slot_num] = color

        section = self._format_section(slot_num)
        # Update full configuration cache
        if section not in self._config_cache:
            self._config_cache[section] = {}

        self._config_cache[section][self.option_color] = color
        self._dirty = True

        # Auto-save if needed
        # self._save_configuration()

    def update_slot_material(self, slot_num, material):
        """Update material for a slot in internal state"""
        self._slot_materials[slot_num] = material

        section = self._format_section(slot_num)
        # Update full configuration cache
        if section not in self._config_cache:
            self._config_cache[section] = {}

        self._config_cache[section][self.option_material] = material
        self._dirty = True

        # Auto-save if needed
        # self._save_configuration()
