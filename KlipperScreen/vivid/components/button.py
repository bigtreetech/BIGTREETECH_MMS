import math

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from vivid.components.utils import (
    convert_seconds_to_hms,
    hex_to_rgb,
    lighten_color,
)


class ImmutableImageButton(Gtk.Button):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._image_set = False

    def set_image(self, image):
        if not self._image_set:
            super().set_image(image)
            self._image_set = True


class BaseImageButton(Gtk.Button):
    def __init__(self,
                 image: Gtk.Image = None,
                 label: Gtk.Label = None,
                 box_type: type = Gtk.VBox,
                 hexpand: bool = True,
                 vexpand: bool = True,
                 can_focus: bool = False,
                 relief_style: Gtk.ReliefStyle = Gtk.ReliefStyle.NONE):
        super().__init__(hexpand=hexpand, vexpand=vexpand,
                         can_focus=can_focus, relief=relief_style)
        self.box = box_type()
        self.box.set_halign(Gtk.Align.CENTER)
        self.box.set_valign(Gtk.Align.CENTER)
        self.box.set_hexpand(True)
        self.box.set_vexpand(True)

        self._image = image
        self._label = label
        if image:
            if box_type == Gtk.Overlay:
                self.box.add_overlay(image)
            else:
                self.box.pack_start(image, expand=False, fill=False, padding=0)
        if label:
            if box_type == Gtk.Overlay:
                self.box.add_overlay(label)
            else:
                self.box.pack_start(label, expand=False, fill=False, padding=0)

        self.add(self.box)

    @property
    def image(self) -> Gtk.Image:
        return self._image

    @property
    def label(self) -> Gtk.Label:
        return self._label

    def update_label_text(self, new_text: str):
        if self._label:
            self._label.set_text(new_text)

    def update_label_markup(self, markup: str):
        if self._label:
            self._label.set_markup(markup)

    def update_spacing(self, spacing):
        if self.box:
            self.box.set_spacing(spacing)


class VerticalImageButton(BaseImageButton):
    """Image on top, label below (Gtk.VBox layout)."""
    def __init__(self, image: Gtk.Image = None, label: Gtk.Label = None, **kwargs):
        super().__init__(image=image, label=label, box_type=Gtk.VBox, **kwargs)


class HorizontalImageButton(BaseImageButton):
    """Image left, label right (Gtk.HBox layout)."""
    def __init__(self, image: Gtk.Image = None, label: Gtk.Label = None, **kwargs):
        super().__init__(image=image, label=label, box_type=Gtk.HBox, **kwargs)


# =====================================================
# Temp Display Button
# =====================================================
class TempDisplayButton(HorizontalImageButton):
    def __init__(self, image=None, label=None, temp=None, **kwargs):
        super().__init__(image=image, label=label, **kwargs)
        self._temp = temp
        self.tag_id = None
        self.get_temp_func = None
        self.target_temp = None
        self.interval = 1

        spacer = Gtk.Box()
        self.box.pack_start(spacer, expand=True, fill=True, padding=0)
        self.box.pack_start(self._temp, expand=False, fill=False, padding=0)
        self.box.set_halign(Gtk.Align.START)

    def refresh_temp(self, get_temp_func):
        self.get_temp_func = get_temp_func
        self.tag_id = GLib.timeout_add_seconds(interval=self.interval,
                                               function=self._update_temp)

    def _update_temp(self):
        new_temp = f"{self.get_temp_func()}°C"
        if self.target_temp:
            new_temp += f"/{self.target_temp}°C"
        if self._temp:
            self._temp.set_text(new_temp)
        return True  # continue timer

    def set_target(self, target_temp):
        self.target_temp = target_temp

    def reset_target(self):
        self.target_temp = None


# =====================================================
# Time Countdown Button
# =====================================================
class CountdownButton(HorizontalImageButton):
    def __init__(self, image=None, label=None, countdown=None, **kwargs):
        super().__init__(image=image, label=label, **kwargs)
        self._countdown = countdown
        self.tag_id = None
        self.remain_seconds = 0
        self.teardown_func = None
        self.interval = 1

        spacer = Gtk.Box()
        self.box.pack_start(spacer, expand=True, fill=True, padding=0)
        self.box.pack_start(self._countdown, expand=False, fill=False, padding=0)
        self.box.set_halign(Gtk.Align.START)

    def _update_countdown_text(self, text):
        if self._countdown:
            self._countdown.set_text(text)

    def start_countdown(self, remain_seconds, teardown_func):
        if self.tag_id:
            self.stop_countdown()
        self.remain_seconds = remain_seconds
        self.teardown_func = teardown_func
        self._update_countdown_text(convert_seconds_to_hms(self.remain_seconds))
        self.tag_id = GLib.timeout_add_seconds(interval=self.interval,
                                               function=self._update_countdown)

    def _update_countdown(self):
        self.remain_seconds = max(0, self.remain_seconds - 1)
        self._update_countdown_text(convert_seconds_to_hms(self.remain_seconds))
        if self.remain_seconds > 0:
            return True  # continue
        if self.teardown_func:
            self.teardown_func()
        self.stop_countdown()
        return False  # stop

    def stop_countdown(self):
        if self.tag_id:
            GLib.source_remove(self.tag_id)
            self.tag_id = None
            self._update_countdown_text("--: --: --")


# =====================================================
# Circle Button — CSS-only (used in preprint panel)
# =====================================================
class CircleButton(BaseImageButton):
    def __init__(self, image=None, label=None,
                 diameter=60, border_width=20, border_color="#000000",
                 **kwargs):
        super().__init__(image=image, label=label, box_type=Gtk.VBox, **kwargs)
        self.set_size_request(diameter, diameter)
        active_color = lighten_color(border_color, 0.2)
        css = (
            f".circle-button-s {{"
            f"  border-radius: 50%;"
            f"  min-width: {diameter}px;"
            f"  min-height: {diameter}px;"
            f"  border: {border_width}px solid {border_color};"
            f"  background-color: transparent;"
            f"  background-image: none;"
            f"}}"
            f".circle-button-s:active {{"
            f"  background-color: {active_color};"
            f"}}"
        )
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        ctx = self.get_style_context()
        ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        ctx.add_class("circle-button-s")
        self.connect("style-updated", lambda w: w.queue_draw())


# =====================================================
# _CircleBase — shared Cairo-drawn circle logic
# =====================================================
class _CircleBase(BaseImageButton):
    """Base for circular buttons with Cairo-drawn borders and CSS styling.

    Subclasses only need to override _do_draw() to customise the arc
    style.  Colour updates, CSS, focus ring, and mosaic image toggle
    are handled here.
    """

    def __init__(self, diameter, border_width, border_color,
                 image=None, label=None, **kwargs):
        super().__init__(image=None, label=None, box_type=Gtk.VBox, **kwargs)
        if hasattr(self, 'box') and self.box:
            self.remove(self.box)

        self.diameter = diameter
        self.border_width = border_width
        self.border_color = border_color
        self._image = image
        self._label = label
        self._glow_angle = 0
        self.is_focusing = False
        self._static_provider = None
        self._dynamic_provider = None

        self._update_derived_colors()
        self.set_size_request(diameter, diameter)
        self._make_transparent(self)

        if self._image:
            self._setup_image_layout()
        else:
            self._setup_no_image_layout()

        self.connect("draw", self._on_draw_dispatch)
        self.connect("style-updated", lambda _: self.queue_draw())

    # ---- layout ----

    @staticmethod
    def _make_transparent(widget):
        widget.override_background_color(Gtk.StateFlags.NORMAL,
                                         Gdk.RGBA(0, 0, 0, 0))

    def _setup_image_layout(self):
        """Overlay: image fills the circle, label floats on top."""
        self.overlay = Gtk.Overlay()
        self.overlay.set_halign(Gtk.Align.CENTER)
        self.overlay.set_valign(Gtk.Align.CENTER)
        self.overlay.set_hexpand(True)
        self.overlay.set_vexpand(True)
        self._make_transparent(self.overlay)
        if self._image:
            self._make_transparent(self._image)
            self.overlay.add(self._image)
        if self._label:
            self._make_transparent(self._label)
            self.overlay.add_overlay(self._label)
            self._label.set_halign(Gtk.Align.CENTER)
            self._label.set_valign(Gtk.Align.CENTER)
        self.add(self.overlay)
        self._css_static(_with_border=False)
        self._css_dynamic()

    def _setup_no_image_layout(self):
        """Plain VBox with optional label."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_hexpand(True)
        box.set_vexpand(True)
        if self._label:
            if self._label.get_parent():
                self._label.get_parent().remove(self._label)
            box.pack_start(self._label, expand=False, fill=False, padding=0)
        self.add(box)
        box.show_all()
        self._css_static(_with_border=True)
        self._css_dynamic()

    # ---- CSS ----

    def _css_static(self, _with_border):
        if self._static_provider:
            self.get_style_context().remove_provider(self._static_provider)
        if _with_border:
            css = (
                f".circle-button {{"
                f"  border-radius: 50%;"
                f"  min-width: {self.diameter}px;"
                f"  min-height: {self.diameter}px;"
                f"  border: {self.border_width}px solid;"
                f"  background-color: transparent;"
                f"  background-image: none;"
                f"  padding: 1.0em;"
                f"  margin: 0.5em 1.0em 0.5em 1.0em;"
                f"}}"
                f".circle-button > box,"
                f".circle-button > overlay {{"
                f"  border-radius: 50%;"
                f"  background-color: transparent;"
                f"}}"
            )
        else:
            css = (
                ".circle-button {"
                "  border-radius: 50%;"
                "  margin: 0.4em 0.9em 0.4em 0.9em;"
                "}"
                ".circle-button > box,"
                ".circle-button > overlay {"
                "  border-radius: 50%;"
                "  background-color: transparent;"
                "}"
            )
        self._static_provider = Gtk.CssProvider()
        self._static_provider.load_from_data(css.encode())
        self.get_style_context().add_provider(
            self._static_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.get_style_context().add_class("circle-button")

    def _css_dynamic(self):
        if self._dynamic_provider:
            self.get_style_context().remove_provider(self._dynamic_provider)
        css = (
            f".circle-button {{ border-color: #FFFFFF; }}"
            f".circle-button:hover {{ background-color: {self.hover_color}; }}"
            f".circle-button:active {{ background-color: {self.active_color}; }}"
        )
        self._dynamic_provider = Gtk.CssProvider()
        self._dynamic_provider.load_from_data(css.encode())
        self.get_style_context().add_provider(
            self._dynamic_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # ---- colour ----

    def _update_derived_colors(self):
        self.base_color = self.border_color
        self.hover_color = lighten_color(self.border_color, 0.3)
        self.active_color = lighten_color(self.border_color, 0.2)
        self._glow_color = self.border_color

    def set_border_color(self, new_color):
        self.border_color = new_color
        self._update_derived_colors()
        self._css_dynamic()
        self.queue_draw()

    def set_focusing(self, focusing):
        self.is_focusing = focusing
        self.queue_draw()
        # Force immediate repaint on transparent-background widgets
        # where Cairo drawings can otherwise persist across frames
        parent = self.get_parent()
        if parent:
            parent.queue_draw()
        window = self.get_window()
        if window:
            window.invalidate_rect(None, True)
            window.process_updates(True)

    # ---- mosaic image toggle ----

    def remove_mosaic_image(self):
        if self._image is not None:
            self._image = None
            child = self.get_child()
            if child:
                self.remove(child)
            self._setup_no_image_layout()
            self.queue_draw()

    def has_mosaic_image(self):
        return self._image is not None

    # ---- Cairo draw ----

    def _on_draw_dispatch(self, widget, cr):
        if self._image:
            if self.is_focusing:
                self._draw_focus_ring(cr)
            return
        self._do_draw(widget, cr)

    def _draw_focus_ring(self, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        fr = min(w, h) / 2 - 1
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(fr / 15)
        cr.arc(w / 2, h / 2, fr, 0, 2 * math.pi)
        cr.stroke()

    def _do_draw(self, widget, cr):
        """Full opaque circle + optional focus ring.  Override for variants."""
        w, h = self.get_allocated_width(), self.get_allocated_height()
        cx, cy = w / 2, h / 2
        radius = min(cx, cy) / 2

        r, g, b = hex_to_rgb(self._glow_color)
        cr.set_source_rgba(r, g, b, 1.0)
        cr.set_line_width(radius / 3)
        cr.arc(cx, cy, radius,
               math.radians(self._glow_angle),
               math.radians(self._glow_angle + 360))
        cr.stroke()

        if self.is_focusing:
            self._draw_focus_ring(cr)


# =====================================================
# CircleButtonStaticSVG — static circle + mosaic image
# =====================================================
class CircleButtonStaticSVG(_CircleBase):
    """Slot button: shows mosaic SVG until a filament colour is assigned."""

    def __init__(self, image=None, label=None,
                 diameter=60, border_width=20, border_color="#000000",
                 **kwargs):
        super().__init__(diameter=diameter, border_width=border_width,
                         border_color=border_color,
                         image=image, label=label, **kwargs)


# =====================================================
# CircleButtonAnime — rotating glow arc
# =====================================================
class CircleButtonAnime(_CircleBase):
    """Circle with a 120° semi-transparent rotating glow arc.

    Call start_animation() / stop_animation() to control.
    """

    def __init__(self, image=None, label=None,
                 diameter=60, border_width=20, border_color="#000000",
                 **kwargs):
        super().__init__(diameter=diameter, border_width=border_width,
                         border_color=border_color,
                         image=image, label=label, **kwargs)
        self.reverse = False
        self._anim_tag = None

    # ---- animation ----

    def start_animation(self, reverse=False):
        if self._anim_tag is None:
            self.reverse = reverse
            self._anim_tag = GLib.timeout_add(50, self._tick_glow)

    def stop_animation(self):
        if self._anim_tag is not None:
            GLib.source_remove(self._anim_tag)
            self._anim_tag = None

    def _tick_glow(self):
        step = -5 if self.reverse else 5
        self._glow_angle = (self._glow_angle + step) % 360
        self.queue_draw()
        return True  # keep timer

    # ---- draw override ----

    def _do_draw(self, widget, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        cx, cy = w / 2, h / 2
        radius = min(cx, cy) / 2

        r, g, b = hex_to_rgb(self._glow_color)
        cr.set_source_rgba(r, g, b, 0.7)
        cr.set_line_width(radius / 5)
        cr.arc(cx, cy, radius,
               math.radians(self._glow_angle),
               math.radians(self._glow_angle + 120))
        cr.stroke()

        if self.is_focusing:
            self._draw_focus_ring(cr)
