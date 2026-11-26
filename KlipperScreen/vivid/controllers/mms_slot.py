from vivid.controllers.connection import get_mms_connection


class MMSSlotController:
    def __init__(self, screen):
        self.connection = get_mms_connection(screen)

    def get_status(self, slot_num):
        slots = self.connection.get_status().get("slots", {})
        return slots.get(str(slot_num), slots.get(slot_num, {}))

    def get_filament_color(self, slot_num):
        return self.get_status(slot_num).get("filament_color") or "#FFFFFF"

    def get_filament_material(self, slot_num):
        return self.get_status(slot_num).get("filament_material")
