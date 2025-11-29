# Support for MMS LED
#
# MMS_LED
# +-----------------------------------------------+
# | Neopixel_0                                    |
# |    +--------------+                           |
# |    | ChipModel_0  |                           |
# |    |   red        |                           |
# |    |   green      |                           |
# |    |   blue       |                           |
# |    |   white      |                           |
# |    |   brightness |                           |
# |    |              | <- LEDManager -> SLOT_0   |
# |    | ChipModel_1  |                           |
# |    | ChipModel_2  |                           |
# |    | ChipModel_3  |                           |
# |    +--------------+                           |
# |                                               |
# |    +--------------+                           |
# |    | ChipModel_4  |                           |
# |    | ...          | <- LEDManager -> SLOT_1   |
# |    | ChipModel_7  |                           |
# |    +--------------+                           |
# |===============================================|
# | Neopixel_1                                    |
# |    +--------------+                           |
# |    | ChipModel_0  |                           |
# |    | ...          | <- LEDManager -> SLOT_2   |
# |    | ChipModel_3  |                           |
# |    +--------------+                           |
# |                                               |
# |    +--------------+                           |
# |    | ChipModel_4  |                           |
# |    | ...          | <- LEDManager -> SLOT_3   |
# |    | ChipModel_7  |                           |
# |    +--------------+                           |
# +-----------------------------------------------+
#
# +-------------------+
# | LEDChip           |
# |   +-----------+   |
# |   | ChipModel |   |
# |   +-----------+   |
# +-------------------+
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import copy, json, re
from dataclasses import dataclass

from .led_effect import (
    MMSLedEffect,
    EffectMarquee,
    EffectBreathing,
    EffectRainbow,
    EffectBlinking,
    # EffectWave,
)
from ..adapters import (
    gcode_adapter,
    neopixel_dispatch,
    printer_adapter,
)


# Utils
def is_valid_color_code(color_code):
    """
    Check if the given color code is in the format "#RRGGBB".
    color_code (str): The color code to validate.
    """
    pattern = "^[0-9A-Fa-f]{6}$"
    # Match the color code against the regular expression
    return True if re.match(pattern, color_code) else False


def color_code_to_rgbw(color_code):
    """
    Convert a hexadecimal color code to an RGBW (Red, Green, Blue, White) tuple.

    Args:
        color_code (str): A string representing the hexadecimal color
        code (e.g., "6495ED").

    Returns:
        tuple: A tuple containing four float values representing the
               normalized RGBW values
               in the range of 0 to 1. If the white component is 1, the
               function returns (1, 1, 1, 1).

    Example:
        "color_code": "6495ED"
        => (100, 149, 237)
        => (0, 49, 137, 100)
        => (0, 0.19, 0.54, 0.39)
    """
    r, g, b = [int(color_code[i:i+2], 16) for i in range(0, 6, 2)]
    w = min(r, g, b)

    # Normalize RGB values to 0-1 range
    rgbw = (tuple(round((x - w) / 255, 2) for x in (r, g, b))
            + (round(w / 255, 2),))

    return (1, 1, 1, 1) if rgbw[3] == 1 else rgbw


def rgbw_to_color_code(rgbs):
    """
    Convert an RGBW color value to a hexadecimal color code.

    Args:
        rgbs (tuple): A tuple containing four float values (r, g, b, w),
                      where r, g, b are in the range 0 to 1, and w is in
                      the range 0 to 1.

    Returns:
        str: A string representing the hexadecimal color code.

    Notes:
        - If the white component (w) is equal to 1, the function returns
          "FFFFFF" indicating
          that all RGB values are at their maximum.
        - The function recovers the RGB values by reversing the white
          component and then
          converts them to a 16-bit color code.
    """
    # if rgbs[3] == 1:
    #     return "FFFFFF"

    # # Recover RGB values by reversing the white component
    # r = round((rgbs[0] * 255) + rgbs[3] * 255, 0)
    # g = round((rgbs[1] * 255) + rgbs[3] * 255, 0)
    # b = round((rgbs[2] * 255) + rgbs[3] * 255, 0)

    # # Convert to a 16-bit color code
    # color_code = "{:02X}{:02X}{:02X}".format(int(r), int(g), int(b))
    # return color_code

    r, g, b, w = rgbs
    if w == 1:
        return "FFFFFF"

    # Recover RGB values by reversing the white component
    r_prime = min(1.0, r + w * (1 - (1 - r)))
    g_prime = min(1.0, g + w * (1 - (1 - g)))
    b_prime = min(1.0, b + w * (1 - (1 - b)))

    r_int = int(round(r_prime * 255))
    g_int = int(round(g_prime * 255))
    b_int = int(round(b_prime * 255))

    # Convert to a 16-bit color code
    return f"{r_int:02X}{g_int:02X}{b_int:02X}"


@dataclass(frozen=True)
class MMSLedEvent:
    """
    Event key string defined for MMS_LED.
    """
    slot_notify: str = "mms_led:slot:notify"
    slot_change_color: str = "mms_led:slot:change_color"
    slot_change_brightness: str = "mms_led:slot:change_brightness"

    slot_marquee_activate: str = "mms_led:slot:marquee_activate"
    slot_marquee_deactivate: str = "mms_led:slot:marquee_deactivate"

    slot_breathing_activate: str = "mms_led:slot:breathing_activate"
    slot_breathing_deactivate: str = "mms_led:slot:breathing_deactivate"

    slot_rainbow_activate: str = "mms_led:slot:rainbow_activate"
    slot_rainbow_deactivate: str = "mms_led:slot:rainbow_deactivate"

    slot_blinking_activate: str = "mms_led:slot:blinking_activate"
    slot_blinking_deactivate: str = "mms_led:slot:blinking_deactivate"

    # slot_wave_activate: str = "mms_led:slot:wave_activate"
    # slot_wave_deactivate: str = "mms_led:slot:wave_deactivate"

    def get_effect_event(self, effect_name, enable=True):
        effect = MMSLedEffect()

        if effect_name == effect.marquee:
            return self.slot_marquee_activate if enable \
                else self.slot_marquee_deactivate

        elif effect_name == effect.breathing:
            return self.slot_breathing_activate if enable \
                else self.slot_breathing_deactivate

        elif effect_name == effect.rainbow:
            return self.slot_rainbow_activate if enable \
                else self.slot_rainbow_deactivate

        elif effect_name == effect.blinking:
            return self.slot_blinking_activate if enable \
                else self.slot_blinking_deactivate

        # elif effect_name == effect.wave:
        #     return self.slot_wave_activate if enable \
        #         else self.slot_wave_deactivate


@dataclass(frozen=True)
class MMSLedConfig:
    """
    Default configs for MMSLed
    """
    # Percent, 0.2 => 20% birghtness
    default_brightness: float = 0.5

    # Define the mapping of colors to conditions based on endstop states
    """
    Color logic:
    - RED:     inlet is off
    - GREEN:   inlet is on, gate is off
    - BLUE:    inlet is on, gate is on, outlet is off
    - WHITE:   outlet is on
    - OFF:     No condition matches the above
    """
    color_mapping = [
        ("RED", lambda s: not s["inlet"]),
        ("GREEN", lambda s: s["inlet"] and not s["gate"]),
        ("BLUE", lambda s: s["inlet"] and s["gate"] and not s["outlet"]),
        # ("WHITE", lambda s: s["inlet"] and s["gate"] and s["outlet"])
        ("BLUE", lambda s: s["inlet"] and s["gate"] and s["outlet"])
    ]

    # Log sample related
    # Sample duration seconds = sample_count * sample_period
    sample_count: int = 100
    sample_period: float = 0.1 # second


@dataclass
class ChipModel:
    red: float = 0
    green: float = 0
    blue: float = 0
    white: float = 0
    brightness: float = MMSLedConfig().default_brightness

    def get_rgbw(self):
        return (self.red, self.green, self.blue, self.white)

    def set_rgbw(self, rgbw_tup):
        self.red, self.green, self.blue, self.white = rgbw_tup

    def get_brightness(self):
        return self.brightness

    def set_brightness(self, new_brightness):
        self.brightness = max(min(new_brightness, 1), 0)

    def get_display_color(self):
        # display_color => the rgbw tuple after brightness is set
        return tuple(round(max(0, min(c * self.brightness, 1)), 2)
                     for c in self.get_rgbw())

    def get_color_code(self):
        return rgbw_to_color_code(self.get_rgbw())


class LEDChip:
    def __init__(self, chip_index):
        self.chip_index = chip_index
        self.chip_model = ChipModel()
        self.snapshot = None

    def get_rgbw(self):
        return self.chip_model.get_rgbw()

    def set_rgbw(self, rgbw_tup):
        self.chip_model.set_rgbw(rgbw_tup)

    def get_brightness(self):
        return self.chip_model.get_brightness()

    def set_brightness(self, new_brightness):
        self.chip_model.set_brightness(new_brightness)

    def get_display_color(self):
        return self.chip_model.get_display_color()

    def get_color_code(self):
        return self.chip_model.get_color_code()

    def capture(self):
        # Use shallow copy if no deep reference issues exist
        self.snapshot = copy.copy(self.chip_model)

    def recover(self):
        if self.snapshot:
            self.chip_model = copy.copy(self.snapshot)
        self.snapshot = None

    def get_snapshot(self):
        return self.snapshot.get_display_color() if self.snapshot else None


class LEDManager:
    def __init__(self, led_name, chips):
        """
        Initializes the LEDManager.

        Parameters:
        - led_name: LED name set in config. Example:[neopixel vivid_rgb_1]
        - chips: List of chips, index like [4,5,6,7]

        Sets up a dictionary mapping color names to their corresponding
        RGBW values.
        """
        self.led_name = led_name
        self.neopixel_adapter = neopixel_dispatch.get_adapter(led_name)
        # A list of chips index
        self.chips = chips

        # key:chip, val:LEDChip()
        self.led_chips = {chip:LEDChip(chip) for chip in self.chips}

        # Default log
        mms_logger = printer_adapter.get_mms_logger()
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)

    def get_chips(self):
        return self.chips

    # def get_chips_color(self):
    #     return [self.led_chips[chip].get_rgbw() for chip in self.chips]

    def update_chip_color(self, chip, rgbw_tup):
        if chip not in self.chips:
            self.log_warning(f"chip[{chip}] update color failed,"
                             f" chip is not available")
            return False

        if any(c < 0 or c > 1 for c in rgbw_tup):
            self.log_warning(f"chip[{chip}] update color failed with"
                             f" rgbw:{rgbw_tup}")
            return False

        self.led_chips[chip].set_rgbw(rgbw_tup)
        return True

    def refresh_leds(self):
        color_data = self.neopixel_adapter.get_color_data()

        try:
            for chip,led_chip in self.led_chips.items():
                color_data[chip] = led_chip.get_display_color()
        except IndexError as e:
            self.log_error(
                f"mms_led error: chain_count:{chip} does not match "
                "the length of slot led index, "
                "please check 'chip_index' in mms-slot.cfg "
                "and 'chain_count' in mms-led.cfg"
            )
            return
        except Exception as e:
            self.log_error(f"mms_led refresh error: {e}")
            return

        self.neopixel_adapter.update_leds(color_data)

    def update_leds(self, chip_color):
        # chip_color-key:chip, val:rgbw_tup
        # {0:(0,1,0,0), 1:(1,1,1,1)}
        for chip,rgbw_tup in chip_color.items():
            self.update_chip_color(chip, rgbw_tup)
        self.refresh_leds()

    # Brightness related
    def get_brightness(self, chip):
        if chip not in self.chips:
            self.log_warning(f"chip[{chip}] get brightness failed,"
                             f" chip is not available")
            return None

        return self.led_chips[chip].get_brightness()

    def adjust_brightness(self, new_brightness, chips=None):
        new_brightness = max(min(new_brightness, 1), 0)
        chips = chips or self.chips
        need_refresh = False

        for chip in chips:
            if chip not in self.chips:
                self.log_warning(f"chip[{chip}] update brightness failed,"
                                 f" chip is not available")
                continue

            brightness = self.led_chips[chip].get_brightness()
            if brightness != new_brightness:
                self.led_chips[chip].set_brightness(new_brightness)
                need_refresh = True

        if need_refresh:
            self.refresh_leds()

    # Snapshot of color related
    def capture_chip_color(self):
        for led_chip in self.led_chips.values():
            led_chip.capture()

    def recover_chip_color(self):
        for led_chip in self.led_chips.values():
            led_chip.recover()
        self.refresh_leds()

    def get_chip_color_snapshot(self):
        lst = []
        for chip in self.chips:
            snapshot = self.led_chips[chip].get_snapshot()
            if snapshot is None:
                return None
            lst.append(snapshot)

        return lst

    # Status
    def get_status(self):
        return {
            f"chip[{chip}]" : {
                "rgbw" : ", ".join(map(str, led_chip.get_rgbw())),
                "brightness" : led_chip.get_brightness(),
                "color_code" : led_chip.get_color_code(),
                "display_color" : ", ".join(
                    map(str, led_chip.get_display_color())),
            } for chip,led_chip in self.led_chips.items()
        }


class MMSLed:
    def __init__(self, config):
        self.reactor = printer_adapter.get_reactor()
        self.mms_led_cfg = MMSLedConfig()

        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_connect(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _handle_klippy_ready(self):
        self._initialize_slot_led()
        self._initialize_led_effects()
        self._setup_led_manager()
        self._register_event_handlers()

    # ---- Init setup ----
    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()

    def _initialize_gcode(self):
        commands = [
            ("MMS_LED_STATUS", self.cmd_STATUS),
            ("MMS_LED_SAMPLE", self.cmd_SAMPLE),

            ("MMS_LED_SET_BRIGHTNESS", self.cmd_SET_BRIGHTNESS),
            ("MMS_LED_SET_COLOR", self.cmd_SET_COLOR),
            ("MMS_LED_SET_CHIP_COLOR", self.cmd_SET_CHIP_COLOR),

            ("MMS_LED_MARQUEE", self.cmd_MARQUEE),
            ("MMS_LED_BREATHING", self.cmd_BREATHING),
            ("MMS_LED_RAINBOW", self.cmd_RAINBOW),
            ("MMS_LED_BLINKING", self.cmd_BLINKING),
            # ("MMS_LED_WAVE", self.cmd_WAVE),

            ("MMS_LED_EFFECT_TRUNCATE", self.cmd_EFFECT_TRUNCATE),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        # MMS loggers
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)

    def _initialize_slot_led(self):
        """
        Parses the MMS LED configuration and returns a dictionary for
        each slot's configuration.
        The returned dictionary has the following structure:
        {
            0: {
                "led_name": <name of the LED>,
                "chip_index": <list of LED Chip indices>,
                "effect": [],
                "led_manager": LEDManager(),  # Will be initialized later
            },
            1: { ... },
            ...
        }
        Key is '*' number of SLOT_*
        """
        mms_slot_led = {}
        # Loop through all slots and extract LED configurations
        for slot_num in self.mms.get_slot_nums():
            mms_slot = self.mms.get_slot(slot_num)
            # Populate the dictionary for the current slot with LED name
            # and chip index
            mms_slot_led[slot_num] = {
                "led_name": mms_slot.get_led_name(),
                "chip_index": mms_slot.get_chip_index(),
                "effect": [],
            }

        self.mms_slot_led = mms_slot_led

    def _initialize_led_effects(self):
        # Mapping of color names to RGBW values
        self.rgbw_dct = {
            "RED": (1, 0, 0, 0),
            "GREEN": (0, 1, 0, 0),
            "BLUE": (0, 0, 1, 0),
            "WHITE": (1, 1, 1, 1),
            "OFF": (0, 0, 0, 0),
            "YELLOW": (1, 1, 0, 0),
        }

        # LED Effects slot mapping
        # key:slot, val:EffectMarquee()
        self.slot_marquee = {}
        # key:slot, val:EffectBreathing()
        self.slot_breathing = {}
        # key:slot, val:EffectRainbow()
        self.slot_rainbow = {}
        # key:slot, val:EffectBlinking()
        self.slot_blinking = {}
        # # key:slot, val:EffectWave()
        # self.slot_wave = {}

    def _setup_led_manager(self):
        """
        Initializes the LEDManager for each slot based on the LED configuration.
        This method assigns a new LEDManager instance to each slot.
        """
        # Iterate over each slot and its configuration
        for slot,led_dct in self.mms_slot_led.items():
            led_manager = LEDManager(
                led_dct.get("led_name"), led_dct.get("chip_index"))
            # Set up an LEDManager for the slot, if it doesn't already exist
            self.mms_slot_led[slot].setdefault("led_manager", led_manager)

    def _register_event_handlers(self):
        ev = MMSLedEvent()
        # Register connect handler to printer
        events = [
            (ev.slot_notify, self.handle_slot_notify),
            # Change color
            (ev.slot_change_color, self.handle_slot_change_color),
            # Change brightness
            (ev.slot_change_brightness, self.handle_slot_change_brightness),
            # Marquee events
            (ev.slot_marquee_activate, self.handle_slot_marquee_activate),
            (ev.slot_marquee_deactivate, self.handle_slot_marquee_deactivate),
            # Breathing events
            (ev.slot_breathing_activate, self.handle_slot_breathing_activate),
            (ev.slot_breathing_deactivate,
             self.handle_slot_breathing_deactivate),
            # Rainbow events
            (ev.slot_rainbow_activate, self.handle_slot_rainbow_activate),
            (ev.slot_rainbow_deactivate, self.handle_slot_rainbow_deactivate),
            # Blinking events
            (ev.slot_blinking_activate, self.handle_slot_blinking_activate),
            (ev.slot_blinking_deactivate, self.handle_slot_blinking_deactivate),
            # Wave events
            # (ev.slot_wave_activate, self.handle_slot_wave_activate),
            # (ev.slot_wave_deactivate, self.handle_slot_wave_deactivate),
        ]
        printer_adapter.bulk_register_event(events)

    # ---- Notification handles ----
    def handle_slot_notify(self, slot):
        """
        Handles notifications and sets up the LED managers when the system
        is initialized.
        After initialization, it determines and updates the LED colors based
        on pins state.
        """
        color = self.determine_led_color(
            self.mms.get_slot(slot).get_pins_state())
        led_manager = self.get_led_manager(slot)

        # Create the color mapping for each chip index
        led_manager.update_leds(
            {chip:self.rgbw_dct.get(color, self.rgbw_dct["OFF"]) \
            for chip in led_manager.get_chips()})

    def determine_led_color(self, pins_state):
        """
        Determines the LED color for each slot based on endstop status.
        """
        # Find the first matching color based on the condition
        # Default to "OFF" if none match
        color = next(
            (color \
            for color,condition in self.mms_led_cfg.color_mapping \
            if condition(pins_state)),
            "OFF"
        )
        return color

    def get_led_manager(self, slot):
        return self.mms_slot_led.get(slot).get("led_manager")

    def handle_slot_change_color(self, slot, color_code):
        if not is_valid_color_code(color_code):
            self.log_warning(f"invalid color_code:{color_code}, update failed")
            return

        rgbw = color_code_to_rgbw(color_code)
        # Retrieve slot configuration and LED manager
        led_manager = self.get_led_manager(slot)
        # Create the color mapping for each chip index
        led_manager.update_leds({chip:rgbw for chip in led_manager.get_chips()})

    def handle_slot_change_brightness(self, slot, brightness):
        self.set_slot_brightness(slot, brightness)

    def change_slot_chip_color(self, slot, index_lst, color_code):
        if not is_valid_color_code(color_code):
            self.log_warning(f"invalid color_code:{color_code}, update failed")
            return

        rgbw = color_code_to_rgbw(color_code)
        # Retrieve slot configuration and LED manager
        led_manager = self.get_led_manager(slot)

        # index_lst:[1,3]
        # chips: [4,5,6,7]
        # target_chips: [5,7]
        chips = led_manager.get_chips()
        target_chips = [chips[i] for i in index_lst if i < len(chips)]

        for chip in target_chips:
            led_manager.update_chip_color(chip, rgbw)
        led_manager.refresh_leds()

    def get_led_status(self, slot=None):
        slot_lst = [slot] if slot is not None else self.mms.get_slot_nums()

        return {
            f"SLOT[{slot}]":{
                "led_name" : led_dct["led_name"],
                "chip_index" : ",".join(map(str, led_dct["chip_index"])),
                "effect" : led_dct["effect"],
                "led_detail" : led_dct["led_manager"].get_status(),
            } for slot,led_dct in self.mms_slot_led.items() if slot in slot_lst
        }

    def log_status(self, slot=None):
        info = "MMS LED status:\n"
        info += json.dumps(self.get_led_status(slot), indent=4) + "\n"
        self.log_info(info)

    # ---- LED control ----
    def set_slot_brightness(self, slot, brightness):
        led_manager = self.get_led_manager(slot)
        led_manager.adjust_brightness(brightness)
        # self.log_info(f"slot[{slot}] update brightness to"
        #               f" {brightness * 100}%")

    # ---- LED Effects ----
    def mark_effect_activated(self, slot, effect_name):
        if effect_name not in self.mms_slot_led[slot]["effect"]:
            self.mms_slot_led[slot]["effect"].append(effect_name)

    def mark_effect_deactivated(self, slot, effect_name):
        if effect_name in self.mms_slot_led[slot]["effect"]:
            self.mms_slot_led[slot]["effect"].remove(effect_name)

    # Effect: Marquee
    def handle_slot_marquee_activate(self, slot, reverse=False):
        self.log_info(f"slot[{slot}] LED marquee effect activate")

        effect = self.slot_marquee.get(slot)
        if not effect:
            effect = EffectMarquee(led_manager=self.get_led_manager(slot))
            self.slot_marquee[slot] = effect

        effect.activate(reverse)
        self.mark_effect_activated(slot, "marquee")

    def handle_slot_marquee_deactivate(self, slot):
        self.log_info(f"slot[{slot}] LED marquee effect deactivate")

        effect = self.slot_marquee.get(slot)
        if not effect:
            self.log_warning(f"slot[{slot}] has no marquee effect, return")
            return

        effect.deactivate()
        self.mark_effect_deactivated(slot, "marquee")

    # Effect: Breathing
    def handle_slot_breathing_activate(self, slot, *args):
        self.log_info(f"slot[{slot}] LED breathing effect activate")

        effect = self.slot_breathing.get(slot)
        if not effect:
            effect = EffectBreathing(led_manager=self.get_led_manager(slot))
            self.slot_breathing[slot] = effect

        effect.activate()
        self.mark_effect_activated(slot, "breathing")

    def handle_slot_breathing_deactivate(self, slot):
        self.log_info(f"slot[{slot}] LED breathing effect deactivate")

        effect = self.slot_breathing.get(slot)
        if not effect:
            self.log_warning(f"slot[{slot}] has no breathing effect, return")
            return

        effect.deactivate()
        self.mark_effect_deactivated(slot, "breathing")

    # Effect: Rainbow
    def handle_slot_rainbow_activate(self, slot, reverse=False):
        self.log_info(f"slot[{slot}] LED rainbow effect activate")

        effect = self.slot_rainbow.get(slot)
        if not effect:
            effect = EffectRainbow(led_manager=self.get_led_manager(slot))
            self.slot_rainbow[slot] = effect

        effect.activate(reverse)
        self.mark_effect_activated(slot, "rainbow")

    def handle_slot_rainbow_deactivate(self, slot):
        self.log_info(f"slot[{slot}] LED rainbow effect deactivate")

        effect = self.slot_rainbow.get(slot)
        if not effect:
            self.log_warning(f"slot[{slot}] has no rainbow effect, return")
            return

        effect.deactivate()
        self.mark_effect_deactivated(slot, "rainbow")

    # Effect: Blinking
    def handle_slot_blinking_activate(self, slot, *args):
        self.log_info(f"slot[{slot}] LED blinking effect activate")

        effect = self.slot_blinking.get(slot)
        if not effect:
            effect = EffectBlinking(led_manager=self.get_led_manager(slot))
            self.slot_blinking[slot] = effect

        effect.activate()
        self.mark_effect_activated(slot, "blinking")

    def handle_slot_blinking_deactivate(self, slot):
        self.log_info(f"slot[{slot}] LED blinking effect deactivate")

        effect = self.slot_blinking.get(slot)
        if not effect:
            self.log_warning(f"slot[{slot}] has no blinking effect, return")
            return

        effect.deactivate()
        self.mark_effect_deactivated(slot, "blinking")

    # Effect: Wave
    # def handle_slot_wave_activate(self, slot, *args):
    #     self.log_info(f"slot[{slot}] LED wave effect activate")

    #     effect = self.slot_wave.get(slot)
    #     if not effect:
    #         effect = EffectWave(led_manager=self.get_led_manager(slot))
    #         self.slot_wave[slot] = effect

    #     effect.activate()
    #     self.mark_effect_activated(slot, "wave")

    # def handle_slot_wave_deactivate(self, slot):
    #     self.log_info(f"slot[{slot}] LED wave effect deactivate")

    #     effect = self.slot_wave.get(slot)
    #     if not effect:
    #         self.log_warning(f"slot[{slot}] has no wave effect, return")
    #         return

    #     effect.deactivate()
    #     self.mark_effect_deactivated(slot, "wave")

    # ---- GCode commands ----
    def cmd_STATUS(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=None, minval=0)
        if not self.mms.slot_is_available(slot_num):
            return
        self.log_status(slot_num)

    def cmd_SAMPLE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=None, minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        self.log_info("sample begin")
        for i in range(self.mms_led_cfg.sample_count):
            self.log_status(slot_num)
            self.reactor.pause(self.reactor.monotonic()
                               + self.mms_led_cfg.sample_period)
        self.log_info("sample end")

    def cmd_SET_BRIGHTNESS(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=None, minval=0)
        if not self.mms.slot_is_available(slot_num):
            return
        brightness = gcmd.get_float("BRIGHTNESS", default=1.0,
                                    minval=0.0, maxval=1.0)

        slots_to_update = (
            [slot_num] if slot_num is not None else self.mms.get_slot_nums())
        for slot_num in slots_to_update:
            self.set_slot_brightness(slot_num, brightness)
            self.log_info(f"slot[{slot_num}] update brightness to"
                          f" {brightness * 100}%")

    def cmd_SET_COLOR(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=0, minval=0)
        if not self.mms.slot_is_available(slot_num):
            return
        color_code = gcmd.get("COLOR")

        if not is_valid_color_code(color_code):
            self.log_warning(f"invalid color_code:{color_code}")
            return

        self.handle_slot_change_color(slot_num, color_code)
        self.log_info(f"slot[{slot_num}] set color:{color_code}")

    def cmd_SET_CHIP_COLOR(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=0, minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        color_code = gcmd.get("COLOR")
        if not is_valid_color_code(color_code):
            self.log_warning(f"invalid color_code:{color_code}")
            return

        chips = [c.strip() for c in gcmd.get("CHIP").split(",")]
        index_lst = [int(s) for s in chips if s.isdigit()]
        self.change_slot_chip_color(slot_num, index_lst, color_code)
        self.log_info(f"slot[{slot_num}] chips:{index_lst}"
                      f" set color:{color_code}")

    def cmd_MARQUEE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=0, minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        switch = gcmd.get_int("SWITCH", 0)
        reverse = gcmd.get_int("REVERSE", 0)
        reverse = True if reverse==1 else False

        if switch:
            self.handle_slot_marquee_activate(slot_num, reverse=reverse)
        else:
            self.handle_slot_marquee_deactivate(slot_num)

    def cmd_BREATHING(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=0, minval=0)
        if not self.mms.slot_is_available(slot_num):
            return
        switch = gcmd.get_int("SWITCH", 0)

        if switch:
            self.handle_slot_breathing_activate(slot_num)
        else:
            self.handle_slot_breathing_deactivate(slot_num)

    def cmd_RAINBOW(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=0, minval=0)
        if not self.mms.slot_is_available(slot_num):
            return
        switch = gcmd.get_int("SWITCH", 0)

        if switch:
            self.handle_slot_rainbow_activate(slot_num)
        else:
            self.handle_slot_rainbow_deactivate(slot_num)

    def cmd_BLINKING(self, gcmd):
        slot_num = gcmd.get_int("SLOT", default=0, minval=0)
        if not self.mms.slot_is_available(slot_num):
            return
        switch = gcmd.get_int("SWITCH", 0)

        if switch:
            self.handle_slot_blinking_activate(slot_num)
        else:
            self.handle_slot_blinking_deactivate(slot_num)

    # def cmd_WAVE(self, gcmd):
    #     slot_num = gcmd.get_int("SLOT", default=0, minval=0)
    #     if not self.mms.slot_is_available(slot_num):
    #         return
    #     switch = gcmd.get_int("SWITCH", 0)

    #     if switch:
    #         self.handle_slot_wave_activate(slot_num)
    #     else:
    #         self.handle_slot_wave_deactivate(slot_num)

    def cmd_EFFECT_TRUNCATE(self, gcmd):
        for mms_slot in self.mms.get_slots():
            mms_slot.slot_led.deactivate_led_effect()


def load_config(config):
    return MMSLed(config)
