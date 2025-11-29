from dataclasses import dataclass

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, Pango

from vivid.components.utils import (
    calculate_contrast_color,
)


@dataclass(frozen=True)
class LabelConfig:
    # font: str = "Mono"
    # font: str = "Free Mono"
    font: str = "B612 Mono"
    # default_font_size: int = 12
    default_color: str = "#FFFFFF"


"""
Notice:
override_font + Pango.FontDescription
is abandoned in GTK 4.0
"""
class VividLabel(Gtk.Label):
    """Enhanced label widget with dynamic content and style updates"""

    def __init__(self, 
        content: str,
        size=None,
        bold=False,
        color=None,
        halign=Gtk.Align.CENTER,
        valign=Gtk.Align.CENTER
    ):
        super().__init__(label=content)
        self._content = content
        self._size = size
        self._bold = bold

        self.config = LabelConfig()
        self._color = color or self.config.default_color
        self._apply_color()

        # Create initial font description
        self._font_desc = self._create_font_description()
        self.override_font(self._font_desc)

        # Alignment setup
        self.set_halign(halign)
        self.set_valign(valign)

        # Line wrap
        # self.set_line_wrap(True)
        # self.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)

    def _create_font_description(self):
        """Generate current font description based on size and bold settings"""
        base_font = self.config.font
        font_string = f"{base_font} {self._size}" if self._size else base_font

        desc = Pango.FontDescription(font_string)
        if self._bold:
            desc.set_weight(Pango.Weight.BOLD)

        return desc

    # Base style
    def set_content(self, new_content):
        """Update label text content"""
        if self._content == new_content:
            return
        self._content = new_content
        self.set_text(new_content)
        # Queue UI refresh to ensure proper rendering
        self.queue_draw()

    def set_size(self, new_size):
        """Update font size"""
        if self._size == new_size:
            return
        self._size = new_size
        self._refresh_style()

    def set_bold(self, bold_state):
        """Toggle bold styling"""
        if self._bold == bold_state:
            return
        self._bold = bold_state
        self._refresh_style()

    def _refresh_style(self):
        """Apply updated font settings"""
        self._font_desc = self._create_font_description()
        self.override_font(self._font_desc)
        # Ensure the change renders immediately
        self.queue_draw()
        self.queue_resize()

    # Color adjust
    def get_color(self):
        return self._color

    def set_color(self, new_color):
        if self._color == new_color:
            return
        self._color = new_color
        self._apply_color()
        self.queue_draw()

    def _apply_color(self):
        color_parsed = self._parse_color(self._color)
        if color_parsed:
            # Gtk 3.0 can use override_color
            # But abandoned in Gtk 4.0
            self.override_color(Gtk.StateFlags.NORMAL, color_parsed)

    def _parse_color(self, color):
        if not color:
            return None

        rgba = Gdk.RGBA()

        if isinstance(color, str):
            if color.startswith("#"):
                rgba.parse(color)
            else:
                try:
                    rgba.parse(color)
                except ValueError:
                    rgba.parse(self.config.default_color)
        elif isinstance(color, tuple) and len(color) == 3:
            rgba.red = color[0] / 255.0
            rgba.green = color[1] / 255.0
            rgba.blue = color[2] / 255.0
            rgba.alpha = 1.0
        else:
            return None

        return rgba

    def adjust_color(self, background_color):
        bg_color_parsed = self._parse_color(background_color)
        if bg_color_parsed:
            contrast_color = calculate_contrast_color(bg_color_parsed)
            self.set_color(contrast_color)

    # CSS style modify
    def add_style_class(self, class_name):
        context = self.get_style_context()
        context.add_class(class_name)

    def remove_style_class(self, class_name):
        context = self.get_style_context()
        context.remove_class(class_name)

    def set_margin(self, margin: int):
        self.set_margin_start(margin)
        self.set_margin_end(margin)
        self.set_margin_top(margin)
        self.set_margin_bottom(margin)

    def set_padding(self, padding: int):
        css = f"padding: {padding}px;"
        self.set_style(css)

    def set_style(self, css: str):
        provider = Gtk.CssProvider()
        provider.load_from_data(f"* {{{css}}}".encode())
        context = self.get_style_context()
        context.add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # def highlight(self, enable=True):
    #     """
    #     .highlighted-label {
    #         background-color: #FFF9C4 !important;
    #         box-shadow: 0 0 5px rgba(255, 204, 0, 0.8);
    #     }
    #     """
    #     if enable:
    #         self.add_style_class("highlighted-label")
    #     else:
    #         self.remove_style_class("highlighted-label")

    # def pulse_effect(self, duration=1000):
    #     """
    #     .pulse-effect {
    #         animation: pulse 1.5s infinite;
    #     }

    #     @keyframes pulse {
    #         0% { box-shadow: 0 0 0 0 rgba(255, 204, 0, 0.7); }
    #         70% { box-shadow: 0 0 0 10px rgba(255, 204, 0, 0); }
    #         100% { box-shadow: 0 0 0 0 rgba(255, 204, 0, 0); }
    #     }
    #     """
    #     self.add_style_class("pulse-effect")
    #     def remove_effect(widget):
    #         self.remove_style_class("pulse-effect")
    #         return False
    #     GLib.timeout_add(duration, remove_effect, self)

