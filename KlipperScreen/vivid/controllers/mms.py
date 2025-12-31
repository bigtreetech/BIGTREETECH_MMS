# Controller of MMS
import logging
from dataclasses import dataclass


@dataclass(frozen=True)
class ViViDKey:
    mms: str = "mms"
    mms_slots: str = "slots"
    mms_steppers: str = "steppers"
    mms_buffers: str = "buffers"

    heater: str = "heater_generic ViViD_Dryer"


class MMSController:
    def __init__(self, screen):
        self._screen = screen
        self._klippy = self._screen._ws.klippy
        # "client" should be KlippyRest() in ks_includes/KlippyRest.py
        # Or get it with ScreenPanel._screen.apiclient
        self._client = self._screen.apiclient

        self.method = "printer/objects/query?mms"

        self.vvd_key = ViViDKey()

        self._slot_selected_callback = None
        self._slot_delivery_play_callback = None
        self._slot_delivery_pause_callback = None
        self._heater_temp_callback = None

        self._slot_delivery_is_playing = False

    # Gcode script
    def send_script(self, script):
        self._klippy.gcode_script(script)

    def query_mms_status(self):
        """Query cmd_MMS_STATUS"""
        self.send_script("MMS_STATUS")

    # HTTP request
    def get_status(self):
        """
        Request function MMS.get_status() in mms.py
        {
            "result": {
                "eventtime": 60720.391551963,
                "status": {
                    "mms": {
                        "slots": {...}
                    }
                }
            }
        }
        """
        result = self._client.send_request(self.method)
        return {} if result is False \
            else result.get("status", {}).get("mms", {})

    def get_mms_steppers(self):
        return self.get_status().get("steppers", {})

    def get_mms_selectors(self):
        return self.get_mms_steppers().get("selectors", {})

    def get_mms_slots(self):
        return self.get_status().get("slots", {})

    # Subscribe
    def subscribe(self):
        updates = {
            "objects": {
                "mms": [
                    self.vvd_key.mms_slots,
                    self.vvd_key.mms_steppers,
                    self.vvd_key.mms_buffers,
                ],
            }
        }
        # self._klippy.object_subscription(updates)

    def handle_notify_status_update(self, data):
        # SubscrStatus Subscriptions arrive as a "notify_status_update" notification
        if self.vvd_key.mms in data:
            self._parse_mms_status(data)
        if self.vvd_key.heater in data:
            self._parse_heater_temp(data)

    def _parse_mms_status(self, data):
        if not self.vvd_key.mms in data:
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
                "inlet": 1,
                "gate": 0,
                "runout": 1,
                "outlet": 0,
                "entry": null,
                "buffer_index": 0,
                "selector_index": 0,
                "drive_index": 0,
                "is_extended": false,
                "extend_num": null
            },
            "1": {...},
            "2": {...},
            "3": {...},
            ...
        }
        """
        for slot_num_str,slot_dct in data.items():
            if slot_dct.get("selector"):
                self._slot_selected_callback(slot_num=int(slot_num_str))

    def register_slot_selected_callback(self, func):
        self._slot_selected_callback = func

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
            if focus_slot_num is not None:
                self._slot_selected_callback(slot_num=int(focus_slot_num))

        for index,mms_drive_dct in mms_drives.items():
            focus_slot_num = mms_drive_dct.get("focus_slot")
            # move_status = mms_drive_dct.get("move_type")
            is_running = mms_drive_dct.get("is_running")
            forward = mms_drive_dct.get("forward")

            # if move_status == "moving":
            if is_running and focus_slot_num is not None:
                self._slot_delivery_play_callback(
                    slot_num = int(focus_slot_num),
                    reverse = not forward
                )
                self._slot_delivery_is_playing = True

            elif self._slot_delivery_is_playing \
                and focus_slot_num is not None:
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
        if not self.vvd_key.heater in data:
            return

        temp = data.get(self.vvd_key.heater).get("temperature", None)
        if temp:
            self._heater_temp_callback(temp=temp)

    def register_heater_temp_callback(self, func):
        self._heater_temp_callback = func
