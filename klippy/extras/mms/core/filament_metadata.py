# Editable and published MMS filament metadata
#
# Copyright (C) 2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict


# Public Moonraker lane_data schema. These are also the only editable
# fields, regardless of whether the update comes from G-code, RFID or DB.
FILAMENT_FIELDS = ("vendor_name", "name", "color", "material", "bed_temp",
                   "nozzle_temp", "spool_id", "filament_id")


def _normalize_field(key, value):
    if isinstance(value, str):
        value = value.strip() or None
    if value is None:
        return None
    try:
        if key == "spool_id":
            if type(value) not in (int, str):
                raise ValueError()
            number = int(value)
            return number if number > 0 else None
        if key in ("bed_temp", "nozzle_temp"):
            if type(value) not in (int, float, str):
                raise ValueError()
            value = float(value)
            if not math.isfinite(value) or value < 0.:
                raise ValueError()
            return value
        if key == "filament_id" and type(value) is int:
            return str(value)
        if not isinstance(value, str):
            raise ValueError()
        if key == "color":
            if not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
                raise ValueError()
            return "#" + value.lstrip("#").upper()
        return value
    except (ValueError, OverflowError):
        raise ValueError(f"Invalid filament {key}") from None


def normalize_filament(data):
    """Validate an edit atomically. An explicit None clears a field."""
    return {key: _normalize_field(key, data[key])
            for key in FILAMENT_FIELDS if key in data}


@dataclass
class SlotFilamentMeta:
    # The only runtime source for editable filament fields. Public object
    # status and Moonraker records are views with different names/formats.
    _filament: Dict[str, Any] = field(default_factory=dict)
    # Keep only original values the runtime cannot represent losslessly.
    # Writes replace a whole lane, including fields the user did not edit.
    _legacy_fields: Dict[str, Any] = field(default_factory=dict)

    def restore_filament(self, row):
        restored = {}
        for key in FILAMENT_FIELDS:
            if key not in row:
                continue
            value = row[key]
            if (key == "color" and isinstance(value, str)
                    and re.fullmatch(r"#?[0-9a-fA-F]{8}", value.strip())):
                # Old lane records may contain RRGGBBAA.
                value = value.strip()[:-2]
            try:
                restored[key] = _normalize_field(key, value)
            except ValueError:
                continue
        self.update_filament(restored, replace=True)
        canonical = self.lane_data()
        self._legacy_fields = {
            key: value for key, value in row.items()
            if key not in ("lane", "scan_time", "td")
            and (key not in canonical or canonical[key] != value)
        }

    def update_filament(self, patch, replace=False):
        data = {} if replace else dict(self._filament)
        if replace:
            self._legacy_fields.clear()
        for key, value in patch.items():
            self._legacy_fields.pop(key, None)
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        self._filament = data

    @property
    def filament_color(self):
        return self._filament.get("color")

    @property
    def filament_material(self):
        return self._filament.get("material")

    @property
    def spool_id(self):
        return self._filament.get("spool_id")

    @property
    def filament_info(self):
        info = dict(self._filament)
        info.update({alias: info[field] for alias, field in (
            ("filament_manufacturer", "vendor_name"),
            ("filament_type_detailed", "name"),
            ("filament_name", "name"),
            ("filament_material_type", "material"),
            ("color_code", "color"),
            ("bed_temperature", "bed_temp"),
        ) if field in info})
        return info

    def lane_data(self):
        data = {key: self._filament.get(key) for key in FILAMENT_FIELDS}
        if data["color"] is not None:
            data["color"] = data["color"][1:]
        return data

    def stored_lane(self, slot_num, scan_time):
        data = self.lane_data()
        data.update(self._legacy_fields)
        data.update(lane=str(slot_num),
                    scan_time=scan_time if self._filament else None,
                    td=4.0 if self._filament else None)
        return data
