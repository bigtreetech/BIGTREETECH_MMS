# Controller of MMS
from dataclasses import dataclass

from vivid.controllers.connection import get_mms_connection


@dataclass(frozen=True)
class ViViDKey:
    mms: str = "mms"
    mms_slots: str = "slots"
    mms_steppers: str = "steppers"
    mms_buffers: str = "buffers"

    heater: str = "heater_generic ViViD_Dryer"


class MMSController:
    def __init__(self, screen):
        self.connection = get_mms_connection(screen)
        self.vvd_key = ViViDKey()

        self._slot_selected_callback = None
        self._slot_delivery_play_callback = None
        self._slot_delivery_pause_callback = None
        self._heater_temp_callback = None
        self._slot_color_callback = None
        self._slot_material_callback = None

        self._slot_delivery_is_playing = False

        # Track last-known slot colour/material to deduplicate callbacks
        self._slot_colors: dict[int, str] = {}
        self._slot_materials: dict[int, str] = {}

    def send_script(self, script, callback=None):
        return self.connection.send("printer.gcode.script", {"script": script}, callback)

    def query_mms_status(self):
        """Query cmd_MMS_STATUS"""
        self.send_script("MMS_STATUS")

    def get_status(self):
        return self.connection.get_status()

    def get_mms_steppers(self):
        return self.get_status().get("steppers", {})

    def get_mms_selectors(self):
        return self.get_mms_steppers().get("selectors", {})

    def get_mms_slots(self):
        return self.get_status().get("slots", {})

    def get_extend_nums(self):
        extend_nums = [
            mms_slot.get("extend_num")
            for mms_slot in self.get_mms_slots().values()
            if mms_slot.get("extend_num") is not None
        ]
        return sorted(set(extend_nums)) if extend_nums else []

    def get_extend_slot_nums(self, extend_num):
        slot_nums = []

        for slot_num, mms_slot in self.get_mms_slots().items():
            ms_extend_num = mms_slot.get("extend_num")
            if ms_extend_num is None:
                continue

            ms_extend_num = int(ms_extend_num)
            if ms_extend_num == extend_num:
                slot_nums.append(int(slot_num))

        return sorted(slot_nums)

    def get_dryer_heater(self, extend_num=None):
        if extend_num is not None:
            return self.get_status().\
                get("extends", {}).\
                get(str(extend_num), {}).\
                get("dryer_heater", "")
        return self.get_status().get("dryer_heater", "")

    def handle_notify_status_update(self, data):
        # SubscrStatus Subscriptions arrive as a "notify_status_update" notification
        if self.vvd_key.mms in data:
            self._parse_mms_status(data)
        if self.vvd_key.heater in data:
            self._parse_heater_temp(data)

    def _parse_mms_status(self, data):
        if self.vvd_key.mms not in data:
            return None

        # logging.info(
        #     "\n"
        #     "###################\n"
        #     f"data: {data}\n"
        #     "###################"
        # )

        # "mms": {
        #     "slots": {...},
        #     "steppers": {...},
        #     "buffers": {...}
        # }
        mms_data = data.get(self.vvd_key.mms, {})

        mms_slots_data = mms_data.get(self.vvd_key.mms_slots, {})
        if mms_slots_data:
            self._parse_mms_slots(mms_slots_data)

        mms_steppers_data = mms_data.get(self.vvd_key.mms_steppers, {})
        if mms_steppers_data:
            self._parse_mms_steppers(mms_steppers_data)

    # ---- MMS SLOTs ----
    def _parse_mms_slots(self, data):
        """
        {
            "0": {
                "selector": 0,
                "inlet": 1, "gate": 0, "runout": 1, "outlet": 0,
                "entry": null, "buffer_index": 0,
                "selector_index": 0, "drive_index": 0,
                "is_extended": false, "extend_num": null,
                "filament_color": "#FF0000",
                "filament_material": "PLA"
            },
            ...
        }
        """
        for slot_num_str, slot_dct in data.items():
            slot_num = int(slot_num_str)
            if slot_dct.get("selector") and self._slot_selected_callback:
                self._slot_selected_callback(slot_num=slot_num)
            f_color = slot_dct.get("filament_color")
            if f_color != self._slot_colors.get(slot_num) and self._slot_color_callback:
                self._slot_colors[slot_num] = f_color
                self._slot_color_callback(slot_num=slot_num, color=f_color)
            f_material = slot_dct.get("filament_material")
            if f_material != self._slot_materials.get(slot_num) and self._slot_material_callback:
                self._slot_materials[slot_num] = f_material
                self._slot_material_callback(slot_num=slot_num, material=f_material)

    def register_slot_selected_callback(self, func):
        self._slot_selected_callback = func

    def register_slot_color_callback(self, func):
        self._slot_color_callback = func

    def register_slot_material_callback(self, func):
        self._slot_material_callback = func

    def update_filament_color(self, slot_num, color):
        script = f"MMS_SLOT_COLOR SLOT={slot_num} CODE='{color}'"
        self.send_script(script)

    def update_filament_material(self, slot_num, material):
        script = f"MMS_SLOT_MATERIAL SLOT={slot_num} MATERIAL='{material}'"
        self.send_script(script)

    def get_slots_filament_color(self):
        return [
            (int(slot_num), status_dct.get("filament_color") or "#FFFFFF")
            for slot_num,status_dct in self.get_mms_slots().items()
        ]

    # ---- MMS Steppers ----
    def _parse_mms_steppers(self, data):
        """
        {
            "selectors": {
                "0": {
                    "index": 0,
                    "name": "manual_stepper selector_stepper",
                    "mms_name": "Selector",
                    "focus_slot": null,
                    "is_running": false,
                    "forward": true,
                    "move_type": "manual_home",
                    "move_status": "expired",
                    "step_dist": 0.045,
                    "steps_moved": 22222,
                    "distance_moved": 999.99
                }
            },
            "drives": {
                "0": {
                    "index": 0,
                    "name": "manual_stepper drive_stepper",
                    "mms_name": "Drive",
                    "focus_slot": null,
                    "is_running": false,
                    "forward": true,
                    "move_type": "",
                    "move_status": "ready",
                    "step_dist": 0.0026,
                    "steps_moved": 0,
                    "distance_moved": 0
                }
            }
        }
        """
        mms_selectors = data.get("selectors", {})
        mms_drives = data.get("drives", {})

        for index,mms_selector_dct in mms_selectors.items():
            focus_slot_num = mms_selector_dct.get("focus_slot")
            if focus_slot_num is not None and self._slot_selected_callback:
                self._slot_selected_callback(slot_num=int(focus_slot_num))

        for index,mms_drive_dct in mms_drives.items():
            focus_slot_num = mms_drive_dct.get("focus_slot")
            # move_status = mms_drive_dct.get("move_type")
            is_running = mms_drive_dct.get("is_running")
            forward = mms_drive_dct.get("forward")

            # if move_status == "moving":
            if is_running and focus_slot_num is not None \
                    and self._slot_delivery_play_callback:
                self._slot_delivery_play_callback(
                    slot_num = int(focus_slot_num),
                    reverse = not forward
                )
                self._slot_delivery_is_playing = True

            elif self._slot_delivery_is_playing \
                    and focus_slot_num is not None \
                    and self._slot_delivery_pause_callback:
                self._slot_delivery_pause_callback(
                    slot_num = int(focus_slot_num)
                )
                self._slot_delivery_is_playing = False

    def register_slot_delivery_play_callback(self, func):
        self._slot_delivery_play_callback = func

    def register_slot_delivery_pause_callback(self, func):
        self._slot_delivery_pause_callback = func

    # ---- Heater ----
    def _parse_heater_temp(self, data):
        """
        Example data:
        {
            ...
            'extruder': {'temperature': -93.23},
            'heater_generic ViViD_Dryer': {'temperature': 25.96},
            'temperature_sensor vivid': {'temperature': 39.51},
            ...
        }
        """
        if self.vvd_key.heater not in data:
            return

        temp = data.get(self.vvd_key.heater).get("temperature", None)
        if temp is not None and self._heater_temp_callback:
            self._heater_temp_callback(temp=temp)

    def register_heater_temp_callback(self, func):
        self._heater_temp_callback = func

    # def start_heating(self, current_group, temperature):
    #     heater = self.get_dryer_heater(current_group)
    #     script = f"SET_HEATER_TEMPERATURE HEATER={heater} TARGET={temperature}"
    #     self.send_script(script)

    # def stop_heating(self, current_group):
    #     heater = self.get_dryer_heater(current_group)
    #     script = f"SET_HEATER_TEMPERATURE HEATER={heater} TARGET=0"
    #     self.send_script(script)

    def start_mms_dryer(self, material_name, current_group=None):
        # MMS_DRYER_START MATERIAL='PLA'
        # MMS_DRYER_START MATERIAL='PLA' GROUP=1
        script = f"MMS_DRYER_START MATERIAL='{material_name}'"
        if current_group is not None:
            script += f" GROUP={current_group}"
        self.send_script(script)

    def stop_mms_dryer(self, current_group=None):
        # MMS_DRYER_STOP
        # MMS_DRYER_STOP GROUP=1
        script = "MMS_DRYER_STOP"
        if current_group is not None:
            script += f" GROUP={current_group}"
        self.send_script(script)

    def get_mms_dryer_status(self, current_group=None):
        """
        "dryer": {
            "heater": "ViViD_Dryer",
            "is_heating": false,
            "start_at": null,
            "finish_at": null,
            "temperature": null,
            "material_name": null
        }
        """
        if current_group is None:
            return self.get_status().get("dryer", {})

        group_num_key = str(current_group)
        return self.get_status().\
            get("extends", {}).\
            get(group_num_key, {}).\
            get("dryer", {})

    # ---- LED ----
    def update_slot_led(self, slot_num, color):
        """Update hardware LED color (strip '#' prefix)"""
        # self.color = "#FFFFFF" --> COLOR=FFFFFF
        color_hex = color[1:] if color.startswith("#") else color
        script = f"MMS_LED_SET_COLOR SLOT={slot_num} COLOR={color_hex}"
        self.send_script(script)

    # ---- MMS Swap ----
    def update_swap_mapping(self, mapping_pairs, filename):
        for swap_num, slot_num in mapping_pairs:
            # Filename could have " ", so use \"\" to avoid error
            script = f"MMS_SWAP_MAPPING SWAP_NUM={swap_num} SLOT={slot_num}"
            self.send_script(script)
