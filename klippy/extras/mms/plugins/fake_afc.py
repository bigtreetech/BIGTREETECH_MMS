# Support for temp fake AFC panels for MMS control
#
# Copyright (C) 2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# ln -sf \
#     ~/klipper/klippy/extras/mms/plugins/fake_afc.py \
#     ~/klipper/klippy/extras/AFC.py
#
# printer.cfg add:
# [AFC]

from .mms.adapters import gcode_adapter, printer_adapter


class AFCLane:
    def __init__(self, lane_num):
        self.lane_num = int(lane_num)
        self.slot_num = self.lane_num-1

        self.mms = printer_adapter.get_mms()
        self.mms_slot = self.mms.get_mms_slot(self.slot_num)

    # "AFC_lane lane1"
    def get_status(self, eventtime=None):
        return {
            # Show in "Infinite Spool" dialog"
            # "name": f"slot[{self.lane_num}]",
            "name": f"lane{self.lane_num}",

            "load": True,
            "prep": True,

            "tool_loaded": False,

            "material": self.mms_slot.meta.filament_material,
            "color": self.mms_slot.meta.filament_color,

            "weight": 1000,
            # "spool_id": int(self.lane_num),

            "map": int(self.lane_num),

            # "runout_lane": "NONE",
            "runout_lane": f"lane{self.mms_slot.get_endless_with_slot()+1}",

            "hub": "VVD buffer hub1",
            "buffer": "VVD buffer",

            "status": "Ready",

            # "td1_td": 17,
            # "td1_color": "FF0000"

            # "current_toolchange": None,
            # "current_toolchange": "lane2"

        }


class AFCBoxTurtle:
    # def __init__(self, config):
    def __init__(self):
        pass

    # "AFC_BoxTurtle Turtle_1"
    def get_status(self, eventtime=None):
        return {
            "lanes": [
                "lane1",
                "lane2",
                "lane3",
                "lane4"
            ],
            "hubs": []
        }


class AFC:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.objects["afc"] = self
        # self.printer.objects["AFC"] = self

        self.mms = printer_adapter.get_mms()

        self.box_name = "ViViD_1"

        box_turtle = AFCBoxTurtle()
        # self.printer.objects["AFC_BoxTurtle"] = box_turtle
        self.printer.objects[f"AFC_BoxTurtle {self.box_name}"] = box_turtle

        # self.printer.objects["AFC_lane lane1"] = AFCLaneRaw(1, "#3064B8")
        # self.printer.objects["AFC_lane lane2"] = AFCLane(2, "#E01DB7")
        # self.printer.objects["AFC_lane lane3"] = AFCLane(3, "#D9F2B6")
        # self.printer.objects["AFC_lane lane4"] = AFCLane(4, "#E37917")
        self.printer.objects["AFC_lane lane1"] = AFCLane(1)
        self.printer.objects["AFC_lane lane2"] = AFCLane(2)
        self.printer.objects["AFC_lane lane3"] = AFCLane(3)
        self.printer.objects["AFC_lane lane4"] = AFCLane(4)

        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)

    def _handle_klippy_connect(self):
        self._initialize_gcode()

    def _initialize_gcode(self):
        commands = [
            ("SET_COLOR", self.cmd_SET_COLOR),
            ("SET_MATERIAL", self.cmd_SET_MATERIAL),
        ]
        gcode_adapter.bulk_register(commands)

    def _get_selecting_slot(self):
        for mms_slot in self.mms.get_mms_slots():
            if mms_slot.selector.is_triggered():
                return mms_slot.get_num()
        return None

    def get_status(self, eventtime=None):
        # sel_slot_num = self._get_selecting_slot()
        # current_lane = f"lane{sel_slot_num+1}" \
        #     if sel_slot_num is not None else None

        return {
            "units": [
                # "Box_Turtle Turtle_1"
                f"Box_Turtle {self.box_name}"
            ],
            "lanes": [
                "lane1",
                "lane2",
                "lane3",
                "lane4"
            ],
            "extruders": [
                "extruder"
            ],
            # "current_lane": "lane4",
            # "current_lane": current_lane,
            "current_state": "Idle",
            "error_state": False
        }

    def cmd_SET_COLOR(self, gcmd):
        # SET_COLOR LANE=lane4 COLOR=31C32F
        lane_num = int(gcmd.get("LANE")[4:])
        color_code = gcmd.get("COLOR")

        mms_cmd = f"MMS_SLOT_COLOR SLOT={lane_num-1} CODE='#{color_code}'"
        gcode_adapter.run_command(mms_cmd)

    def cmd_SET_MATERIAL(self, gcmd):
        # SET_MATERIAL LANE=lane4 MATERIAL=PLA
        lane_num = int(gcmd.get("LANE")[4:])
        material = gcmd.get("MATERIAL")

        mms_cmd = f"MMS_SLOT_MATERIAL SLOT={lane_num-1} MATERIAL='{material}'"
        gcode_adapter.run_command(mms_cmd)


def load_config(config):
    return AFC(config)
