# Support for MMS Slot Meta
#
# Copyright (C) 2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .filament_metadata import SlotFilamentMeta
from .slot_pin import PinType


@dataclass
class SlotPinMeta:
    selector: Optional["SlotPinSelector"] = field(default=None)
    inlet: Optional["SlotPinInlet"] = field(default=None)
    gate: Optional["SlotPinGate"] = field(default=None)
    outlet: Optional["SlotPinOutlet"] = field(default=None)
    buffer_runout: Optional["SlotPinBufferRunout"] = field(default=None)
    entry: Optional["SlotPinEntry"] = field(default=None)


# class MaterialFactor(Enum):
#     ABS = 0.04
#     PLA = 0.03
#     TPU = 0.08


@dataclass
class DeliverDistance:
    # scale_factor: float = 1.0      # 1=100%
    # distance_window: Optional[List] = field(default_factory=list)
    deliver_distance: float = 0.0 # in mm


@dataclass
class SlotMeta(SlotPinMeta, SlotFilamentMeta):
    """
    Meta of MMS Slot
    """
    num: int = field(init=False)
    # name: str = field(init=False)

    cfg_path: str = field(default=None)
    full_path: str = field(default=None)
    _meta_file: str = "bigtreetech-mms/mms-slot-meta.json"

    mms_buffer_index: int = field(init=False)
    mms_selector_index: int = field(init=False)
    mms_drive_index: int = field(init=False)

    mms_buffer: Optional["MMSBuffer"] = field(default=None)
    mms_selector: Optional["MMSSelector"] = field(default=None)
    mms_drive: Optional["MMSDrive"] = field(default=None)

    is_extended: bool = False
    extend_num: Optional[int] = field(default=None)

    # key:(
    #     destination -> pin_type:str,
    #     direction -> forward:boolean
    # )
    deliver_vector: Dict[
        Tuple[PinType, bool], DeliverDistance
    ] = field(default_factory=dict)

    def report(self):
        try:
            return {
                "slot_num" : self.num,

                "selector": self.selector.get_state(),
                "inlet": self.inlet.get_state(),
                "gate": self.gate.get_state(),
                "runout": self.buffer_runout.get_state(),
                "outlet": self.outlet.get_state(),
                "entry": self.entry.get_state(),

                "buffer_index" : self.mms_buffer_index,
                "selector_index" : self.mms_selector_index,
                "drive_index" : self.mms_drive_index,

                "is_extended" : self.is_extended,
                "extend_num" : self.extend_num,

                "filament_color" : self.filament_color,
                "filament_material" : self.filament_material,
                "spool_id" : self.spool_id,
                "filament_info" : self.filament_info,

                "deliver_vector": {
                    f"{pin}_{'forward' if direction else 'backward'}" : {
                        # "scale_factor": dist.scale_factor,
                        "deliver_distance": round(dist.deliver_distance, 2)
                    }
                    for (pin,direction),dist in self.deliver_vector.items()
                }
            }
        except Exception as e:
            return {}

    def get_deliver_distance(self, pin_type, forward):
        key = (pin_type, forward)
        d_dist = self.deliver_vector.get(key)
        if d_dist and d_dist.deliver_distance:
            # Return cached data
            return d_dist.deliver_distance

        # New key, read from file
        full_path, data = self.read_file()
        if data:
            f_str = "forward" if forward else "backward"
            d_dist = data.\
                get(str(self.num), {}).\
                get("deliver_vector", {}).\
                get(f"{pin_type}_{f_str}")

            if d_dist:
                deliver_distance = d_dist.get("deliver_distance")
                if deliver_distance:
                    self.set_deliver_distance(
                        pin_type, forward, deliver_distance)
                    return deliver_distance

        return None

    def set_deliver_distance(self, pin_type, forward, distance):
        dist = abs(distance) if forward else distance
        key = (pin_type, forward)
        if self.deliver_vector.get(key):
            self.deliver_vector[key].deliver_distance = dist
        else:
            d_dist = DeliverDistance(deliver_distance=dist)
            self.deliver_vector[key] = d_dist

    def truncate_deliver_distance(self):
        self.deliver_vector = {}

    # ---- Config File ----
    def set_cfg_path(self, cfg_path):
        # printer_adapter.get_klippy_configfile()
        # Most likely "/home/.../printer_data/config/printer.cfg"
        self.cfg_path = cfg_path

    def _find_full_path(self):
        if not self.cfg_path or not self._meta_file:
            return None

        if self.full_path:
            return self.full_path

        # base_dir should be "/home/.../printer_data/config/"
        base_dir = os.path.dirname(self.cfg_path)
        # Deterministic path, avoids scanning the whole config directory
        # e.g. "<config_dir>/bigtreetech-mms/mms-slot-meta.json"
        full_path = os.path.join(base_dir, self._meta_file)

        if os.path.isfile(full_path):
            # Cache
            self.full_path = full_path
            return full_path

        return None

    def read_file(self):
        full_path = None
        data = {}

        full_path = self._find_full_path()
        if not full_path:
            return full_path, data

        try:
            if os.path.exists(full_path):
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    data = json.loads(content) if content else {}
            else:
                data = {}

        except json.JSONDecodeError as e:
            self.log_error(f"JSON decode error '{full_path}': {e}")
            return full_path, None
        except Exception as e:
            self.log_error(f"open file error '{full_path}': {e}")
            return full_path, None

        return full_path, data

    def _create_file(self):
        full_path = self._find_full_path()
        if not full_path:
            base_dir = os.path.dirname(self.cfg_path)
            full_path = os.path.join(base_dir, self._meta_file)

        try:
            # Create if not exists
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
        except Exception as e:
            self.log_error(f"create file error '{full_path}': {e}")
            return None

        return full_path

    def _atomic_write_file(self, full_path, data):
        if not full_path:
            return False

        # Atomic write
        tmp_path = full_path + ".tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            os.replace(tmp_path, full_path)
        except Exception as e:
            self.log_error(f"write file error '{full_path}': {e}")
            return False

        return True

    def write_file(self):
        # reactor.register_timer(
        #     self._delayed_write, reactor.monotonic() + 1.0)

        full_path = self._create_file()
        if not full_path:
            self.log_error(f"write file error '{full_path}'")
            return False

        # Read exists data
        _, data = self.read_file()

        slot_key = str(self.num)
        if not isinstance(data, dict):
            return False
        if slot_key not in data:
            data[slot_key] = {}

        dv = data[slot_key]["deliver_vector"] = {}

        for (pin_type, forward), obj in self.deliver_vector.items():
            f_str = "forward" if forward else "backward"
            key = f"{pin_type}_{f_str}"

            dv[key] = {
                "deliver_distance": obj.deliver_distance,
                # "scale_factor": getattr(obj, "scale_factor", 0.001)
            }

        return self._atomic_write_file(full_path, data)
