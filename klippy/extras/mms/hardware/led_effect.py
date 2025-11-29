# Support for MMS LED Effect
#
# +-----------------------------------------------+
# | Effect                                        |
# |   +---------+   +-----------+   +---------+   |
# |   | Marquee |   | Breathing |   | Rainbow |   |
# |   +---------+   +-----------+   +---------+   |
# +-----------------------------------------------+
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import math, time
from dataclasses import dataclass

from ..adapters import printer_adapter


@dataclass(frozen=True)
class MMSLedEffect:
    marquee: str = "marquee"
    breathing: str = "breathing"
    rainbow: str = "rainbow"
    blinking: str = "blinking"
    # wave: str = "wave"


@dataclass(frozen=True)
class MMSLedEffectConfig:
    # LED Effect configs
    # Marquee
    marquee_freq: float = 0.08 # second
    # [(1,1,1,1), (1,1,1,1), (1,1,1,1)]
    # -> [(1,1,1,1) * 0 * (1-marquee_step),
    #     (1,1,1,1) * 1 * (1-marquee_step),
    #     (1,1,1,1) * 2 * (1-marquee_step)]
    marquee_step: float = 0.15

    # Breathing
    breathing_freq: float = 0.05 # second
    # Bigger sin_freq faster rhythm
    breathing_sin_freq: float = 0.5
    breathing_max_brightness: float = 1.0
    breathing_min_brightness: float = 0.1

    # Rainbow
    # freq too small may cause neopixel update failed
    rainbow_freq: float = 0.05 # second
    rainbow_colors = [
        (238/255.0, 130/255.0, 238/255.0, 0.0),  # Purple
        (75/255.0, 0.0, 130/255.0, 0.0),     # Indigo
        (0.0, 0.0, 1.0, 0.0),                # Blue
        (0.0, 1.0, 0.0, 0.0),                # Green
        (1.0, 1.0, 0.0, 0.0),                # Yellow
        (1.0, 165/255.0, 0.0, 0.0),          # Orange
        (1.0, 0.0, 0.0, 0.0),                # Red
    ]
    rainbow_interpolate_count = 28

    # Blinking
    blink_freq: float = 1.0 # second

    # Wave
    # wave_freq: float = 0.1 # second


def interpolate_colors(original_colors, target_length):
    """
    Generate smooth color transitions between given colors using linear
    interpolation.
    Args:
        original_colors (list): List of RGBA tuples in 0.0-1.0 range
        target_length (int): Desired total number of output colors
    Returns:
        list: Interpolated color list with length == target_length
    """
    num_base_colors = len(original_colors)
    interpolated = []

    for i in range(target_length):
        # Calculate position in original color space
        pos = i * (num_base_colors - 1) / (target_length - 1)
        left_idx = int(pos)
        blend_weight = pos - left_idx  # 0.0-1.0 interpolation weight

        # Handle last color boundary condition
        if left_idx >= num_base_colors - 1:
            interpolated.append(original_colors[-1])
            continue

        # Get neighboring colors for interpolation
        color_start = original_colors[left_idx]
        color_end = original_colors[left_idx + 1]

        # Linear interpolation for each channel
        red = color_start[0] + blend_weight * (color_end[0] - color_start[0])
        green = color_start[1] + blend_weight * (color_end[1] - color_start[1])
        blue = color_start[2] + blend_weight * (color_end[2] - color_start[2])
        white = color_start[3] + blend_weight * (color_end[3] - color_start[3])

        # Round to 4 decimal places for cleaner output
        interpolated.append((
            round(red, 4),
            round(green, 4),
            round(blue, 4),
            round(white, 4)
        ))

    return interpolated


class EffectMarquee:
    def __init__(self, led_manager):
        self.led_manager = led_manager
        self.reactor = printer_adapter.get_reactor()

        config = MMSLedEffectConfig()
        self.freq = config.marquee_freq
        self.step = config.marquee_step

        self.shift_count = 0
        self.timer = None

        # Default from left to right
        # Reverse from right to left
        self.reverse = False

    def _color_wheel(self, rgbw_tup_lst):
        lst = []
        n = len(rgbw_tup_lst)
        for index, rgbw_tup in enumerate(rgbw_tup_lst):
            # Adjust direction by self.reverse
            effective_index = index if not self.reverse else (n - 1 - index)
            multiplier = max(1 - effective_index * self.step, 0)
            # Calculate color value, make sure is not negative
            rgbw_lst = [
                max(round(color * multiplier, 2), 0)
                for color in rgbw_tup
            ]
            lst.append(tuple(rgbw_lst))

        # Shift logic, make sure not negative
        shift = max(self.shift_count - 1, 0)
        if self.reverse:
            # Move left "shift" times：move first "shift" elements to the end
            shifted_lst = lst[shift:] + lst[:shift]
        else:
            # Move right "shift" times：move last "shift" elements to the begin
            shifted_lst = (
                lst[-shift:] + lst[:-shift] if shift != 0 else lst.copy())

        return shifted_lst

    def run(self, eventtime):
        chips = self.led_manager.get_chips()
        color_data = self.led_manager.get_chip_color_snapshot()

        if color_data is None:
            self.led_manager.capture_chip_color()
            color_data = self.led_manager.get_chip_color_snapshot()

        chips_cnt = len(chips)
        color_data_wheel = self._color_wheel(color_data)

        chip_color = {chip:color_data_wheel[chip % chips_cnt] for chip in chips}
        self.led_manager.update_leds(chip_color)

        self.shift_count += 1
        if self.shift_count >= chips_cnt:
            self.shift_count = 0

        next_waketime = self.reactor.monotonic() + self.freq
        if self.timer:
            self.reactor.update_timer(self.timer, next_waketime)
            return next_waketime
        else:
            return self.reactor.NEVER

    def clean(self, recover=True):
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None

        if recover:
            self.led_manager.recover_chip_color()

        self.shift_count = 0

    def activate(self, reverse=False):
        if self.timer:
            self.clean(recover=False)
        self.reverse = reverse
        waketime = self.reactor.monotonic() + self.freq
        self.timer = self.reactor.register_timer(
            callback=self.run, waketime=waketime)

    def deactivate(self):
        self.clean()


class EffectBreathing:
    def __init__(self, led_manager):
        self.led_manager = led_manager
        self.reactor = printer_adapter.get_reactor()

        config = MMSLedEffectConfig()
        self.freq = config.breathing_freq
        self.sin_freq = config.breathing_sin_freq
        self.max_brightness = config.breathing_max_brightness
        self.min_brightness = config.breathing_min_brightness

        self.timer = None

    def calculate_brightness(self):
        current_time = time.time()
        # Use a sine wave function to generate the variation in breathing effect
        # The sine wave oscillates between 0 and 1
        brightness = (math.sin(math.pi * current_time * self.sin_freq) + 1) / 2
        # brightness = (
        #    self.min_brightness
        #    + (self.max_brightness - self.min_brightness) * brightness)
        brightness = max(brightness, self.min_brightness)
        brightness = min(brightness, self.max_brightness)
        return round(brightness, 2)

    def _color_wheel(self, rgbw_tup_lst):
        brightness = self.calculate_brightness()
        return [tuple([round(c * brightness, 2) for c in list(rgbw_tup)])
                for rgbw_tup in rgbw_tup_lst]

    def run(self, eventtime):
        chips = self.led_manager.get_chips()
        color_data = self.led_manager.get_chip_color_snapshot()

        if color_data is None:
            self.led_manager.capture_chip_color()
            color_data = self.led_manager.get_chip_color_snapshot()

        chips_cnt = len(chips)
        color_data_wheel = self._color_wheel(color_data)

        chip_color = {chip:color_data_wheel[chip % chips_cnt] for chip in chips}
        self.led_manager.update_leds(chip_color)

        next_waketime = self.reactor.monotonic() + self.freq
        if self.timer:
            self.reactor.update_timer(self.timer, next_waketime)
            return next_waketime
        else:
            return self.reactor.NEVER

    def clean(self, recover=True):
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None

        if recover:
            self.led_manager.recover_chip_color()

    def activate(self):
        if self.timer:
            self.clean(recover=False)
        waketime = self.reactor.monotonic() + self.freq
        self.timer = self.reactor.register_timer(
            callback=self.run, waketime=waketime)

    def deactivate(self):
        self.clean()


class EffectRainbow:
    def __init__(self, led_manager):
        self.led_manager = led_manager
        self.reactor = printer_adapter.get_reactor()

        config = MMSLedEffectConfig()
        self.freq = config.rainbow_freq
        # self.rainbow_colors = config.rainbow_colors
        self.rainbow_colors = interpolate_colors(
            config.rainbow_colors,
            config.rainbow_interpolate_count)

        self.shift_count = 0
        self.timer = None

        # Default from left to right
        # Reverse from right to left
        self.reverse = False

    def _shift_colors(self, shift):
        shift = shift % len(self.rainbow_colors)
        if self.reverse:
            return self.rainbow_colors[shift:] + self.rainbow_colors[:shift]
        else:
            return self.rainbow_colors[-shift:] + self.rainbow_colors[:-shift] \
            if shift !=0 else self.rainbow_colors.copy()

    def run(self, eventtime):
        chips = self.led_manager.get_chips()

        color_data = self.led_manager.get_chip_color_snapshot()
        if color_data is None:
            self.led_manager.capture_chip_color()

        shifted_colors = self._shift_colors(self.shift_count)

        # Activate color to chips
        chip_color = {}
        for idx, chip in enumerate(chips):
            color_idx = idx % len(shifted_colors)
            chip_color[chip] = shifted_colors[color_idx]
        self.led_manager.update_leds(chip_color)

        self.shift_count = (self.shift_count + 1) % len(self.rainbow_colors)

        next_waketime = self.reactor.monotonic() + self.freq
        if self.timer:
            self.reactor.update_timer(self.timer, next_waketime)
            return next_waketime
        else:
            return self.reactor.NEVER

    def clean(self, recover=True):
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
        if recover:
            self.led_manager.recover_chip_color()
        self.shift_count = 0

    def activate(self, reverse=False):
        if self.timer:
            self.clean(recover=False)
        self.reverse = reverse
        waketime = self.reactor.monotonic() + self.freq
        self.timer = self.reactor.register_timer(self.run, waketime)

    def deactivate(self):
        self.clean()


class EffectBlinking:
    def __init__(self, led_manager):
        self.led_manager = led_manager
        self.reactor = printer_adapter.get_reactor()

        config = MMSLedEffectConfig()
        # Interval time for each blink (seconds)
        self.blink_freq = config.blink_freq

        # Fade duration as a fraction of blink frequency
        self.fade_duration = self.blink_freq * 0.3  # 30% of blink time for fade

        # Default color
        # yellow -> Red=1,Green=1,Blue=0,White=0
        self.color = (1.0, 1.0, 0.0, 0.0)
        self.off_color = (0.0, 0.0, 0.0, 0.0)

        # State tracking
        self.is_on = False
        self.current_color = self.off_color
        self.target_color = self.off_color
        self.transition_start = 0.0
        self.timer = None
        self.step_timer = None

    def set_frequency(self, frequency):
        """Dynamically set the blinking frequency"""
        self.blink_freq = frequency
        self.fade_duration = self.blink_freq * 0.3
        # If currently running, reactivate to apply new frequency
        if self.timer:
            self.activate()

    def set_color(self, color):
        self.color = color
        # If currently fading, update target color
        if self.is_on:
            self.target_color = color
        else:
            self.target_color = self.off_color

    def _interpolate_color(self, start, end, progress):
        """Interpolate between two colors based on progress (0.0 to 1.0)"""
        return tuple(
            start[i] + (end[i] - start[i]) * progress
            for i in range(4)
        )

    def _update_color(self, eventtime):
        """Update color based on current transition progress"""
        elapsed = eventtime - self.transition_start
        progress = min(1.0, max(0.0, elapsed / self.fade_duration))

        self.current_color = self._interpolate_color(
            self.current_color,
            self.target_color,
            progress
        )

        # Set the same color for all LED chips
        chips = self.led_manager.get_chips()
        chip_color = {chip: self.current_color for chip in chips}
        self.led_manager.update_leds(chip_color)

        # Schedule next update if transition isn't complete
        if progress < 1.0:
            next_step = self.reactor.monotonic() + 0.02  # 50 FPS
            if self.step_timer:
                self.reactor.update_timer(self.step_timer, next_step)
                return next_step
            else:
                return self.reactor.NEVER

        # Transition complete, clean up step timer
        self.reactor.unregister_timer(self.step_timer)
        self.step_timer = None
        return self.reactor.NEVER

    def _start_transition(self, target_color):
        """Start a color transition to the target color"""
        self.target_color = target_color
        self.transition_start = self.reactor.monotonic()

        # Create or update step timer for smooth transitions
        if not self.step_timer:
            next_step = self.reactor.monotonic() + 0.02
            self.step_timer = self.reactor.register_timer(
                callback=self._update_color,
                waketime=next_step
            )
        else:
            next_step = self.reactor.monotonic() + 0.02
            self.reactor.update_timer(self.step_timer, next_step)

    def run(self, eventtime):
        # Save current color
        color_data = self.led_manager.get_chip_color_snapshot()
        if color_data is None:
            self.led_manager.capture_chip_color()
            color_data = self.led_manager.get_chip_color_snapshot()
        # Set last chip color as current collor
        self.set_color(color_data[-1])

        # Toggle on/off state
        self.is_on = not self.is_on
        # Start transition to new color
        target = self.color if self.is_on else self.off_color
        self._start_transition(target)

        # Calculate next blink time
        next_waketime = self.reactor.monotonic() + self.blink_freq
        if self.timer:
            self.reactor.update_timer(self.timer, next_waketime)
            return next_waketime
        else:
            return self.reactor.NEVER

    def clean(self, recover=True):
        """Clean up timers and recover LED state"""
        if self.timer:
            self.reactor.unregister_timer(self.timer)
            self.timer = None

        if self.step_timer:
            self.reactor.unregister_timer(self.step_timer)
            self.step_timer = None

        if recover:
            self.led_manager.recover_chip_color()

    def activate(self):
        """Activate the blinking effect"""
        if self.timer:
            self.clean(recover=False)

        # Reset state and start immediately
        self.is_on = False
        self.current_color = self.off_color
        self.target_color = self.off_color

        # Start blink timer
        waketime = self.reactor.monotonic() + self.blink_freq
        self.timer = self.reactor.register_timer(
            callback=self.run,
            waketime=waketime
        )

    def deactivate(self):
        """Deactivate the blinking effect"""
        self.clean()


# class EffectWave:
#     def __init__(self, led_manager):
#         self.led_manager = led_manager
#         self.reactor = printer_adapter.get_reactor()

#         config = MMSLedEffectConfig()
#         # Animation step interval in seconds
#         self.wave_freq = config.wave_freq
#         self.brightness_min = 0.0    # Minimum brightness (fully dark)
#         self.brightness_max = 1.0    # Maximum brightness (original color)

#         # State tracking
#         self.current_step = 0
#         self.chips = self.led_manager.get_chips()
#         self.num_chips = len(self.chips)
#         self.timer = None
#         self.original_colors = {}    # Stores original LED colors

#     def set_frequency(self, frequency):
#         """Update animation frequency and restart if active"""
#         self.wave_freq = frequency
#         if self.timer:
#             self.activate()

#     def run(self, eventtime):
#         """Main animation handler called by reactor timer"""
#         # Capture original colors on first run
#         self.original_colors = self.led_manager.get_chip_color_snapshot()
#         if self.original_colors is None:
#             self.led_manager.capture_chip_color()
#             self.original_colors = self.led_manager.get_chip_color_snapshot()

#         # Calculate brightness for each chip based on current step
#         brightness = self._calculate_brightness()

#         # Apply brightness to original colors
#         chip_colors = {
#             chip: tuple(
#                 c * brightness[chip]
#                 for c in self.original_colors[chip]
#             ) for chip in self.chips
#         }

#         # Update LEDs and schedule next step
#         self.led_manager.update_leds(chip_colors)
#         self.current_step = ((self.current_step + 1)
#                              % (self.num_chips // 2 + 3))

#         if self.timer:
#             next_time = eventtime + self.wave_freq
#             self.reactor.update_timer(self.timer, next_time)
#             return next_time
#         return self.reactor.NEVER

#     # def _calculate_brightness(self):
#     #     """Calculate brightness values for each chip based on current
#     #        animation step"""
#     #     brightness = {chip: self.brightness_min for chip in self.chips}

#     #     middle = self.num_chips // 2
#     #     is_odd = self.num_chips % 2 == 1

#     #     if self.current_step == 0:
#     #         # Step 1: All dark
#     #         pass
#     #     elif self.current_step == 1:
#     #         # Step 2: Light middle chip(s)
#     #         if is_odd:
#     #             brightness[self.chips[middle]] = self.brightness_max
#     #         else:
#     #             brightness[self.chips[middle-1]] = self.brightness_max
#     #             brightness[self.chips[middle]] = self.brightness_max
#     #     elif self.current_step < (middle + 2):
#     #         # Step 3: Expand outward
#     #         pairs_to_light = self.current_step - 1
#     #         for i in range(pairs_to_light):
#     #             left = middle - 1 - i
#     #             right = middle + i + (0 if is_odd else 1)
#     #             if left >= 0:
#     #                 brightness[self.chips[left]] = self.brightness_max
#     #             if right < self.num_chips:
#     #                 brightness[self.chips[right]] = self.brightness_max
#     #     else:
#     #         # Step 4: All bright
#     #         brightness = {chip: self.brightness_max for chip in self.chips}

#     #     return brightness

#     def _calculate_brightness(self):
#         """Calculate brightness values for each chip based on current
#            animation step"""
#         brightness = {chip: self.brightness_min for chip in self.chips}

#         middle = self.num_chips // 2
#         is_odd = self.num_chips % 2 == 1

#         if self.current_step == 0:
#             # Step 1: All dark
#             pass
#         elif self.current_step == 1:
#             # Step 2: Light middle chip(s)
#             if is_odd:
#                 brightness[self.chips[middle]] = self.brightness_max
#             else:
#                 brightness[self.chips[middle-1]] = self.brightness_max
#                 brightness[self.chips[middle]] = self.brightness_max
#         elif self.current_step < (middle + 2):
#             # Step 3: Expand outward while keeping previous LEDs lit
#             wave_front = self.current_step - 1  # How far we've expanded

#             # For odd counts: include all LEDs from center to wave_front
#             if is_odd:
#                 for i in range(wave_front + 1):
#                     left = middle - i
#                     right = middle + i
#                     if left >= 0:
#                         brightness[self.chips[left]] = self.brightness_max
#                     if right < self.num_chips:
#                         brightness[self.chips[right]] = self.brightness_max
#             else:
#                 # For even counts: include all LEDs from center pair
#                 # to wave_front
#                 for i in range(wave_front + 1):
#                     left = middle - 1 - i
#                     right = middle + i
#                     if left >= 0:
#                         brightness[self.chips[left]] = self.brightness_max
#                     if right < self.num_chips:
#                         brightness[self.chips[right]] = self.brightness_max
#         else:
#             # Step 4: All bright
#             brightness = {chip: self.brightness_max for chip in self.chips}

#         return brightness

#     def clean(self, recover=True):
#         """Clean up resources and optionally restore original colors"""
#         if self.timer:
#             self.reactor.unregister_timer(self.timer)
#             self.timer = None
#         if recover:
#             self.led_manager.recover_chip_color()

#     def activate(self):
#         """Start the wave animation effect"""
#         if self.timer:
#             self.clean(recover=False)

#         # Reset state
#         self.current_step = 0
#         self.chips = self.led_manager.get_chips()
#         self.num_chips = len(self.chips)
#         self.original_colors = {}

#         # Start animation
#         waketime = self.reactor.monotonic() + self.wave_freq
#         self.timer = self.reactor.register_timer(self.run, waketime)

#     def deactivate(self):
#         """Stop the wave animation effect and restore colors"""
#         self.clean()
