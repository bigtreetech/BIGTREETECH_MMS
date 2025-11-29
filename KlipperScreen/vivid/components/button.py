import cairo
import logging
import math

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

from vivid.components.utils import (
    convert_seconds_to_hms,
    hex_to_rgb,
    rgb_to_hex,
    lighten_color,
    generate_color_wave,
)


class ImmutableImageButton(Gtk.Button):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._image_set = False

    def set_image(self, image):
        if not self._image_set:
            super().set_image(image)
            self._image_set = True
        else:
            # print("Warning: Image can only be set once!")
            return


class BaseImageButton(Gtk.Button):
    def __init__(self, 
            image: Gtk.Image = None,
            label: Gtk.Label = None,
            # Gtk.VBox or Gtk.HBox
            box_type: type = Gtk.VBox,
            spacing: int = 5,
            hexpand: bool = True,
            vexpand: bool = True,
            can_focus: bool = False,
            relief_style: Gtk.ReliefStyle = Gtk.ReliefStyle.NONE):

        super().__init__(
            hexpand=hexpand, 
            vexpand=vexpand, 
            can_focus=can_focus, 
            relief=relief_style
        )

        self.box = box_type(spacing=spacing)
        self.box.set_halign(Gtk.Align.CENTER)
        self.box.set_valign(Gtk.Align.CENTER)
        self.box.set_hexpand(True)
        self.box.set_vexpand(True)

        self._image = image
        self._label = label
        if image:
            self.box.pack_start(image, expand=False, fill=False, padding=0)
        if label:
            self.box.pack_start(label, expand=False, fill=False, padding=0)
        self.add(self.box)

    @property
    def image(self) -> Gtk.Image:
        return self._image

    @property
    def label(self) -> Gtk.Label:
        return self._label

    def update_label_text(self, new_text: str):
        """
        Update the text content of the button's label
        Args:
            new_text: New text content for the label
        """
        if self._label:
            self._label.set_text(new_text)

    def update_label_markup(self, markup: str):
        """
        Update the label with Pango markup
        Useful for adding formatting like bold, color, etc.
        Args:
            markup: Pango markup string
        button.update_label_markup(
            f'<span color="{label_color}">{text_here}</span>')
        """
        if self._label:
            self._label.set_markup(markup)


class VerticalImageButton(BaseImageButton):
    """
    +---------------------------+
    |        Gtk.Button         |
    |  +---------------------+  |
    |  |      Gtk.VBox       |  |
    |  |  +---------------+  |  |
    |  |  |     Image     |  |  |
    |  |  +---------------+  |  |
    |  |  +---------------+  |  |
    |  |  |     Label     |  |  |
    |  |  +---------------+  |  |
    |  +---------------------+  |
    +---------------------------+
    """
    def __init__(self, image: Gtk.Image = None, label: Gtk.Label = None, **kwargs):
        super().__init__(image=image, label=label, box_type=Gtk.VBox, **kwargs)


class HorizontalImageButton(BaseImageButton):
    """
    +---------------------------------------------+
    |                Gtk.Button                   |
    |  +---------------------------------------+  |
    |  |              Gtk.VBox                 |  |
    |  |  +---------------+ +---------------+  |  |
    |  |  |     Image     | |     Label     |  |  |
    |  |  +---------------+ +---------------+  |  |
    |  +---------------------------------------+  |
    +---------------------------------------------+
    """
    def __init__(self, image: Gtk.Image = None, label: Gtk.Label = None, **kwargs):
        super().__init__(image=image, label=label, box_type=Gtk.HBox, **kwargs)


# =====================================================
# Temp Display Button
# =====================================================
class TempDisplayButton(HorizontalImageButton):
    def __init__(self, image: Gtk.Image = None, label: Gtk.Label = None, **kwargs):
        super().__init__(image=image, label=label, **kwargs)

        self.tag_id = None
        self.get_temp_func = None
        self.target_temp = None
        self.display_color = "#FFFFFF"

        self.set_halign(Gtk.Align.START)

    def refresh_temp(self, get_temp_func):
        self.get_temp_func = get_temp_func
        # Start rotating every 1s
        self.tag_id = GLib.timeout_add_seconds(
            interval=1, function=self.update_temp)

    def update_temp(self):
        # self.update_label_markup(
        #     f'<span color="{self.display_color}">Temp {self.get_temp_func()}°C/{self.target_temp}°C</span>')

        text = f"Temp {self.get_temp_func()}°C"
        if self.target_temp:
            text += f"/{self.target_temp}°C"

        self.update_label_text(text)

        # Continue
        return True

    def set_target(self, target_temp, display_color="#FFFFFF"):
        self.target_temp = target_temp
        self.display_color = display_color

    def reset_target(self):
        self.target_temp = None


# =====================================================
# Time Countdown Button
# =====================================================
class CountdownButton(HorizontalImageButton):
    def __init__(self, image: Gtk.Image = None, label: Gtk.Label = None, **kwargs):
        super().__init__(image=image, label=label, **kwargs)

        self.tag_id = None
        self.remain_seconds = 0
        self.teardown_func = None

        self.set_halign(Gtk.Align.START)

    def start_countdown(self, remain_seconds, teardown_func):
        if self.tag_id:
            # Old countdown should stop before new one start
            self.stop_countdown()

        self.remain_seconds = remain_seconds
        self.teardown_func = teardown_func
        self.update_label_text(
            f"Time {convert_seconds_to_hms(self.remain_seconds)}")

        # Start rotating every 1000ms = 1s
        # self.tag_id = GLib.timeout_add(
        #     interval=1000, function=self.update_countdown)
        self.tag_id = GLib.timeout_add_seconds(
            interval=1, function=self.update_countdown)

    def update_countdown(self):
        self.remain_seconds = max(0, self.remain_seconds-1)
        self.update_label_text(
            f"Time {convert_seconds_to_hms(self.remain_seconds)}")

        if self.remain_seconds > 0:
            # Continue
            return True
        else:
            # Stop
            if self.teardown_func:
                self.teardown_func()
            self.stop_countdown()
            return False

    def stop_countdown(self):
        if self.tag_id:
            result = GLib.source_remove(self.tag_id)
            if result:
                logging.info(f"Countdown ID:{self.tag_id} remove success.")
                self.tag_id = None

                self.update_label_text("Time --:--:--")


# =====================================================
# Circle Button
# =====================================================
class CircleButton(BaseImageButton):
    def __init__(self, 
        image: Gtk.Image = None, 
        label: Gtk.Label = None,
        diameter: int = 60,
        border_width: int = 20,
        border_color: str = "#000000",
        **kwargs
    ):
        super().__init__(image=image, label=label, box_type=Gtk.VBox, **kwargs)
        self.set_size_request(diameter, diameter)

        # Slightly lighter for active
        active_color = lighten_color(border_color, 0.2)

        self.css = f"""
        .circle-button-s {{
            border-radius: 50%;
            min-width: {diameter}px;
            min-height: {diameter}px;
            border: {border_width}px solid {border_color};
            background-color: transparent;
            background-image: none;
        }}
        .circle-button-s:active {{
            background-color: {active_color};
        }}
        """
        self.apply_css()        
        self.connect("style-updated", self.on_style_updated)

    def apply_css(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(self.css.encode())
        context = self.get_style_context()
        context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        context.add_class("circle-button-s")

    def on_style_updated(self, widget):
        self.queue_draw()


class CircleButtonAnime(BaseImageButton):
    def __init__(self,
        image: Gtk.Image = None, 
        label: Gtk.Label = None,
        diameter: int = 60, 
        border_width: int = 20, 
        border_color: str = "#000000", 
        **kwargs
    ):
        super().__init__(image=image, label=label, box_type=Gtk.VBox, **kwargs)

        # Initial configuration
        self.diameter = diameter
        self.border_width = border_width
        self.border_color = border_color
        # Angle in degrees
        self._glow_angle = 0
        # self._glow_angle = 30

        # Initialize focus state to False
        self.is_focusing = False
        # Rotation anime direction
        self.reverse = False

        # Derived colors
        self._update_derived_colors()

        # Set size
        self.set_size_request(diameter, diameter)

        # CSS providers
        self._static_provider = None
        self._dynamic_provider = None
        # Setup CSS styling
        self._setup_css_style()

        # Connect animation + drawing
        self.connect("draw", self.on_draw)
        self.connect("style-updated", lambda _: self.queue_draw())

        # Start rotating animation
        self.tag_id = None
        # self.tag_id = GLib.timeout_add(50, self.update_glow)

    def _update_derived_colors(self):
        """Update colors derived from the main border color"""
        self.base_color = self.border_color
        self.hover_color = lighten_color(self.border_color, 0.3)
        self.active_color = lighten_color(self.border_color, 0.2)
        # self._glow_color = lighten_color(border_color, 0.4)
        self._glow_color = self.border_color

    def _setup_css_style(self):
        """Setup CSS styles with separate providers for static and dynamic properties"""
        context = self.get_style_context()

        # Remove existing providers to prevent duplication
        if self._static_provider:
            context.remove_provider(self._static_provider)
        if self._dynamic_provider:
            context.remove_provider(self._dynamic_provider)

        # Create static CSS properties (unchanging)
        self._static_provider = Gtk.CssProvider()
        static_css = f"""
        .circle-button {{
            border-radius: 50%;
            min-width: {self.diameter}px;
            min-height: {self.diameter}px;
            border: {self.border_width}px solid;
            background-color: transparent;
            background-image: none;
            padding: 0.5em;
            margin: 0.5em;
        }}
        .circle-button > box {{
            border-radius: 50%;
            background-color: transparent;
        }}
        """
        self._static_provider.load_from_data(static_css.encode())

        # Create dynamic CSS properties (color-related)
        self._dynamic_provider = Gtk.CssProvider()
        self._update_dynamic_css()

        # Add to style context
        context.add_provider(self._static_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        context.add_provider(self._dynamic_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        context.add_class("circle-button")

    def _update_dynamic_css(self):
        """Update dynamic CSS properties (colors)"""
        if not self._dynamic_provider:
            return

        dynamic_css = f"""
        .circle-button {{
            border-color: {self.base_color};
        }}
        .circle-button:hover {{
            background-color: {self.hover_color};
        }}
        .circle-button:active {{
            background-color: {self.active_color};
        }}
        """
        self._dynamic_provider.load_from_data(dynamic_css.encode())

    def set_border_color(self, new_color):
        """Update the border color and refresh UI"""
        if self.border_color == new_color:
            return

        self.border_color = new_color
        self._update_derived_colors()
        self._update_dynamic_css()
        # Update visible state immediately
        self.queue_draw()  

    def update_glow(self):
        """Update the arc's angle and trigger redraw."""
        step = -5 if self.reverse else 5
        self._glow_angle = (self._glow_angle + step) % 360 
        self.queue_draw()
        # Continue animation
        return True

    def start_animation(self, reverse=False):
        if self.tag_id is None:
            self.reverse = reverse
            # First param is in ms
            self.tag_id = GLib.timeout_add(50, self.update_glow)

    def stop_animation(self):
        if self.tag_id is not None:
            GLib.source_remove(self.tag_id)
            self.tag_id = None

    def kick_animation(self):
        for _ in range(5):
            self.update_glow()

    def set_focusing(self, focusing):
        """Set the focus state and trigger redraw"""
        if self.is_focusing != focusing:
            self.is_focusing = focusing
            self.queue_draw()

    def on_draw(self, widget, cr):
        """
        Draw a translucent rotating arc over the button border, 
        and a focus ring if needed.
        """
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        cx, cy = width / 2, height / 2
        radius = min(cx, cy) / 2

        # Draw rotating arc
        # One round 360 degrees
        arc_length = 120  # degrees
        arc_width = radius / 5
        start_angle = math.radians(self._glow_angle)
        end_angle = start_angle + math.radians(arc_length)

        r, g, b = hex_to_rgb(self._glow_color)
        # Semi-transparent arc (r, g, b, 0.3)
        cr.set_source_rgba(r, g, b, 0.7)
        cr.set_line_width(arc_width)
        cr.arc(cx, cy, radius, start_angle, end_angle)
        cr.stroke()

        # Draw focus ring if in focus state
        if self.is_focusing:
            # Calculate position for the focus ring - just outside the main border
            # Using min(cx, cy) gets the maximum radius that fits the widget
            focus_radius = min(cx, cy) - 1
            # Add focus ring properties
            cr.set_source_rgb(1, 1, 1)  # White color (RGB values all 1)
            cr.set_line_width(focus_radius / 15)  # border width
            # Draw a full circle for the focus ring
            cr.arc(cx, cy, focus_radius, 0, 2 * math.pi)
            cr.stroke()


class CircleButtonAnimeFull(BaseImageButton):
    def __init__(self,
                 image: Gtk.Image = None,
                 label: Gtk.Label = None,
                 diameter: int = 60,
                 border_width: int = 20,
                 border_color: str = "#000000",
                 **kwargs):
        super().__init__(image=image, label=label, box_type=Gtk.VBox, **kwargs)

        # Button configuration
        self.diameter = diameter
        self.border_width = border_width
        self.base_color = border_color
        self.hover_color = lighten_color(self.base_color, 0.3)
        self.active_color = lighten_color(self.base_color, 0.2)

        # Internal state
        # Rotation angle for the glow ring
        self._glow_angle = 0
        # Color used in glow arc
        self._glow_color = lighten_color(border_color, 0.4)

        # Timer ID for border color animation
        self.animation_id = None
        # Breathing effect generator
        self._border_color_gen = generate_color_wave(
            border_color, steps=50, amount=0.25)

        # Set button size
        self.set_size_request(diameter, diameter)

        self.connect("realize", self.start_animation)
        self.connect("destroy", self.stop_animation)
        self.connect("style-updated", lambda _: self.queue_draw())

        # Initial CSS injection
        self.update_css(self.base_color)

        # Connect draw and animation lifecycle signals
        self.connect("draw", self.on_draw)
        # Start glow ring updater, 50ms
        GLib.timeout_add(50, self.update_glow)

    def start_animation(self, widget):
        """Start the border color breathing animation"""
        if not self.animation_id:
            # First param is in ms
            self.animation_id = GLib.timeout_add(
                50, self.update_border_color)

    def stop_animation(self, widget):
        """Stop the breathing animation if running"""
        if self.animation_id:
            GLib.source_remove(self.animation_id)
            self.animation_id = None

    def update_border_color(self):
        """Advance border color using the color wave generator and update CSS"""
        next_color = next(self._border_color_gen)
        self.update_css(next_color)
        # Continue timer
        return True

    def update_css(self, color):
        """Apply dynamic CSS styling with updated border and background hover colors"""
        css = f"""
        .circle-button {{
            border-radius: 50%;
            min-width: {self.diameter}px;
            min-height: {self.diameter}px;
            border: {self.border_width}px solid {color};
            background-color: transparent;
            background-image: none;
            padding: 0.5em;
            margin: 0.5em;
        }}
        .circle-button:active {{
            background-color: {self.active_color};
        }}
        .circle-button > box {{
            border-radius: 50%;
            background-color: transparent;
        }}
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        context = self.get_style_context()
        context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        context.add_class("circle-button")

    def update_glow(self):
        """Update glow arc angle to rotate over time"""
        self._glow_angle = (self._glow_angle + 5) % 360
        self.queue_draw()
        # Continue timer
        return True  

    def on_draw(self, widget, cr):
        """Draw the animated glowing arc around the button using Cairo"""
        line_width = 10
        line_length = 60

        width = self.get_allocated_width()
        height = self.get_allocated_height()
        cx, cy = width / 2, height / 2
        radius = min(cx, cy)

        # Calculate arc position
        start_angle = math.radians(self._glow_angle)
        # Glow arc length in radians
        arc_len = math.radians(line_length)  

        # Create radial gradient for glow effect
        r, g, b = hex_to_rgb(self._glow_color)
        gradient = cairo.RadialGradient(cx, cy, radius - 5, cx, cy, radius + 3)
        gradient.add_color_stop_rgba(0.0, r, g, b, 0.8)
        gradient.add_color_stop_rgba(1.0, r, g, b, 0.0)

        # Draw arc
        cr.set_source(gradient)
        cr.set_line_width(line_width)
        cr.arc(cx, cy, radius, start_angle, start_angle + arc_len)
        cr.stroke()
