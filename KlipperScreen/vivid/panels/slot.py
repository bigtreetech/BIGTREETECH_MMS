import logging
from math import isfinite
from shlex import quote

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from ks_includes.screen_panel import ScreenPanel
from vivid.components.box import FixedSquareBox
from vivid.components.button import HorizontalImageButton as HorButton
from vivid.components.button import VerticalImageButton as VerButton
from vivid.components.image import VividImage as VImage
from vivid.components.label import VividLabel as VLabel
from vivid.components.utils import (
    apply_button_css,
    create_section_container,
    get_screen_width,
)
from vivid.config.manager import VividConfigManager
from vivid.config.vivid_config import ColorConfig, VividConfig
from vivid.controllers.mms import MMSController
from vivid.controllers.mms_slot import MMSSlotController


class Panel(ScreenPanel):

    def __init__(self,
        screen,
        title=None,
        slot_num=0,
        color="#FFFFFF",
        material="PLA"
    ):
        super().__init__(screen, title or _("ViViD SLOT"))
        self.slot_num = slot_num
        self.color = color
        self.material = material

        # Track widgets needing color updates
        self.color_dependent_widgets = []
        # Track material buttons for selection state
        self.material_buttons = {}
        # Currently selected material button
        self.selected_material_button = None
        self.config_manager = None
        self.details_view = None

        self.mms_controller = MMSController(self._screen)
        self.mms_slot_controller = MMSSlotController(self._screen)

        self.mms_controller.register_slot_color_callback(
            self._on_slot_color_changed)
        self.mms_controller.register_slot_material_callback(
            self._on_slot_material_changed)

        self.build_ui()

    def build_ui(self):
        """Build the main UI structure"""
        # Top area: Material scroll and Color palette
        top_area = create_section_container("vvd-slotpanel-area-top")
        top_area.attach(self.create_material_scroll(), 0, 0, 1, 1)
        top_area.attach(self.create_color_palette(), 1, 0, 1, 1)

        # Bottom area: SLOT control
        bottom_area = create_section_container("vvd-slotpanel-area-bottom")
        slot_control = self.create_slot_control(self.slot_num, self.color)
        self.color_dependent_widgets.append(slot_control)
        bottom_area.attach(slot_control, 0, 0, 1, 1)

        # Main grid layout
        main_grid = create_section_container("vvd-slotpanel-area")
        main_grid.attach(top_area, 0, 0, 1, 1)
        main_grid.attach(bottom_area, 0, 1, 1, 1)
        details = Gtk.Button(label=_("Details"))
        apply_button_css(details, "vvd-slot-ctrl-btn")
        details.connect("clicked", lambda _: self.show_details())
        main_grid.attach(details, 0, 2, 1, 1)
        self.main_grid = main_grid
        self.content.add(main_grid)

    def get_slot_details(self):
        if self.config_manager is None:
            self.config_manager = VividConfigManager(self._config.config_path)
        self.config_manager.manual_load()
        details = self.config_manager.get_slot_details(self.slot_num)
        slot = self.mms_slot_controller.get_status(self.slot_num)
        info = slot.get("filament_info") or {}
        reported = {
            "vendor": info.get("filament_manufacturer") or slot.get("filament_vendor"),
            "name": (
                info.get("filament_type_detailed")
                or info.get("color_name_a")
                or slot.get("filament_name")
            ),
            "nozzle_temp": info.get("nozzle_temp"),
            "bed_temp": info.get("bed_temperature"),
        }
        details.update({key: str(value) for key, value in reported.items() if value is not None})
        return details

    def show_details(self, details=None):
        if details is None:
            try:
                details = self.get_slot_details()
            except OSError:
                logging.exception("Could not read local ViViD slot details")
                self._screen.show_popup_message(_("Could not read slot details."))
                return
        grid = Gtk.Grid(row_spacing=8, column_spacing=16, margin=16, column_homogeneous=True)
        fields = (
            ("vendor", _("Vendor")), ("name", _("Name")),
            ("nozzle_temp", _("Nozzle Temp")), ("bed_temp", _("Bed Temp")),
        )
        for row, (field, label) in enumerate(fields):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            edit = Gtk.Button(label=details[field] or _("Edit"))
            edit.connect("clicked", self.edit_detail, field, label, details)
            grid.attach(edit, 1, row, 1, 1)
        back = Gtk.Button(label=_("Back"))
        back.connect("clicked", lambda _: self.close_details())
        grid.attach(back, 0, len(fields), 2, 1)
        self.set_details_view(grid)

    def set_details_view(self, view):
        if self.details_view is None:
            self.content.remove(self.main_grid)
        else:
            self.details_view.destroy()
        self.details_view = view
        self.content.add(view)
        view.show_all()

    def close_details(self):
        if self.details_view is not None:
            self._screen.remove_keyboard()
            self.details_view.destroy()
            self.details_view = None
            self.content.add(self.main_grid)
            self.main_grid.show_all()

    def edit_detail(self, widget, field, label, details):
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        view.pack_start(Gtk.Label(label=label), False, False, 0)
        entry = Gtk.Entry(text=details[field])
        if field in ("nozzle_temp", "bed_temp"):
            entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        entry.connect("button-press-event", self._screen.show_keyboard)
        entry.connect("touch-event", self._screen.show_keyboard)
        view.pack_start(entry, False, False, 0)
        buttons = Gtk.Box(spacing=12, homogeneous=True)
        save = Gtk.Button(label=_("Save"))
        cancel = Gtk.Button(label=_("Cancel"))
        buttons.add(save)
        buttons.add(cancel)
        view.pack_start(buttons, False, False, 0)

        def back(_widget):
            self._screen.remove_keyboard()
            self.show_details(details)

        def submit(_widget):
            if not save.get_sensitive():
                return
            value = entry.get_text().strip()
            if value and field in ("nozzle_temp", "bed_temp"):
                try:
                    temperature = float(value)
                    if not isfinite(temperature) or temperature < 0:
                        raise ValueError
                except ValueError:
                    self._screen.show_popup_message(_("Enter a valid temperature."))
                    return
                value = str(temperature)
            if ";" in value or (value and not value.isprintable()):
                self._screen.show_popup_message(
                    _("Enter a single line of text without semicolons.")
                )
                return
            save.set_sensitive(False)
            timeout_id = None

            def saved(response, *_args):
                nonlocal timeout_id
                if timeout_id is None:
                    return
                GLib.source_remove(timeout_id)
                timeout_id = None
                if "error" in response:
                    if self.details_view is view:
                        save.set_sensitive(True)
                        self._screen.show_popup_message(_("Could not save slot details."))
                    return
                try:
                    self.config_manager.save_slot_detail(self.slot_num, field, value)
                except OSError:
                    logging.exception("Could not save local ViViD slot details")
                details[field] = value
                if self.details_view is view:
                    back(save)

            script = f"MMS_SLOT_MAP SLOT={self.slot_num} {field.upper()}={quote(value)}"
            timeout_id = GLib.timeout_add_seconds(10, saved, {"error": True})
            if not self.mms_controller.send_script(script, saved):
                saved({"error": True})

        save.connect("clicked", submit)
        entry.connect("activate", submit)
        cancel.connect("clicked", back)
        self.set_details_view(view)
        entry.grab_focus()
        self._screen.show_keyboard(entry=entry)

    # ---- Material Components ----
    def create_material_scroll(self):
        """Create scrollable material selection area"""
        screen_width = get_screen_width(self)
        scroll_height = screen_width / 4.5
        slider_radius = screen_width / 256
        slider_width = screen_width / 256
        slider_height = screen_width / 11

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.get_style_context().add_class("vvd-material-scroll")
        scroll.set_size_request(-1, scroll_height)

        css_provider = Gtk.CssProvider()
        css = (
            f".vvd-material-scroll scrollbar slider {{"
            f"  background-color: #FFFFFF;"
            f"  border-radius: {slider_radius}px;"
            f"  min-width: {slider_width}px;"
            f"  min-height: {slider_height}px;"
            f"}}"
        )
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            scroll.get_screen(), css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        m_grid = create_section_container("vvd-material-scroll")
        for i, material_obj in enumerate(VividConfig.MATERIALS):
            material = material_obj.name
            dp_color = material_obj.display_color
            button = self.create_material_button(dp_color, material)
            self.material_buttons[material] = button
            m_grid.attach(button, 0, i, 1, 1)
            if material == self.material:
                self.select_material_button(button)

        scroll.add(m_grid)
        self.color_dependent_widgets.append(scroll)
        return scroll

    def create_material_button(self, color, material):
        """Create a material selection button"""
        screen_width = get_screen_width(self)
        font_size = screen_width / 60

        btn = HorButton(label=VLabel(content=material, size=font_size))
        btn.box.set_halign(Gtk.Align.START)
        btn.connect("clicked",
                     lambda _, m=material: self.refresh_slot_material(m))

        base_class = "vvd-material-scroll-btn"
        btn.selected_pattern = f".{base_class} {{ background-color: %s; }}"

        provider = Gtk.CssProvider()
        provider.load_from_data(
            f".{base_class} {{ border-left-color: {color}; }}".encode())
        ctx = btn.get_style_context()
        ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        ctx.add_class(base_class)
        return btn

    def select_material_button(self, button):
        """Select a material button and deselect the previous one"""
        if self.selected_material_button:
            self.apply_unselect(self.selected_material_button)
            self.selected_material_button.original_color = None

        button.original_color = self.color
        self.selected_material_button = button
        self.apply_select(button)

    def apply_unselect(self, widget):
        _apply_dynamic_css(widget, None, "#1A2E39", widget.selected_pattern)

    def apply_select(self, widget):
        _apply_dynamic_css(widget, None, "#142027", widget.selected_pattern)

    def refresh_slot_material(self, material):
        """Update the selected material and UI state"""
        # Update current material
        self.material = material

        # Update UI selection
        if material in self.material_buttons:
            self.select_material_button(self.material_buttons[material])

        # Notify MMS
        self.mms_controller.update_filament_material(
            self.slot_num, material)

    # ---- Color Palette Components ----
    def create_color_palette(self):
        """Create a color selection grid"""
        screen_width = get_screen_width(self)
        spacing_box_p = screen_width / 50
        spacing_box_line = screen_width / 70

        palette = create_section_container("vvd-color-palette")

        box_p = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True,
            spacing=spacing_box_p
        )

        # Two-row color arrangement
        palette_map = (
            ("red", "orange", "yellow", "lime", "green", "cyan"),
            ("blue", "purple", "pink", "white", "black", "gray")
        )
        for colors_tup in palette_map:
            box_line = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                halign=Gtk.Align.CENTER,
                valign=Gtk.Align.CENTER,
                hexpand=True,
                vexpand=True,
                spacing=spacing_box_line
            )
            for color_name in colors_tup:
                btn = self.create_color_button(color_name)
                box_line.add(btn)

            box_p.add(box_line)

        palette.set_halign(Gtk.Align.FILL)
        palette.set_valign(Gtk.Align.FILL)
        palette.attach(box_p, 0, 0, 1, 1)
        return palette

    def create_color_button(self, color_name):
        """Add individual color button to palette"""
        screen_width = get_screen_width(self)
        square_size = screen_width / 14
        box = FixedSquareBox(size=square_size)

        base_class = "vvd-color-btn"
        color_val = ColorConfig.get_color_hex(color_name)

        btn = Gtk.Button(
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=False, 
            vexpand=False
        )
        btn.add(box)
        btn.connect("clicked", lambda _, c=color_name: self.refresh_slot_color(c))
        apply_button_css(btn, base_class, f"background-color: {color_val};")

        return btn

    def refresh_slot_color(self, color_name):
        """Update slot color throughout UI and hardware"""
        new_color = ColorConfig.get_color_hex(color_name)
        self.color = new_color
        for widget in self.color_dependent_widgets:
            update_widget_color(widget, new_color)
        self.mms_controller.update_filament_color(self.slot_num, new_color)

    # ---- SLOT Control Functions ----
    def create_slot_control(self, slot_num, color):
        self.buttons = {
            "preload": self.create_slot_action_button(
                "vivid_preload", "Pre-Load", color,
                f"MMS_PRELOAD SLOT_NUM={slot_num}"),
            "eject": self.create_slot_action_button(
                "vivid_eject", "Eject", color, "MMS_EJECT_SLOT"),
            "select": self.create_slot_action_button(
                "vivid_select", "Select", color,
                f"MMS_SELECT_U SLOT={slot_num}"),
            "load": self.create_slot_action_button(
                "vivid_load", "Load", color,
                f"MMS_LOAD_SLOT SLOT_NUM={slot_num}"),
        }
        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True)
        for i, name in enumerate(["preload", "eject", "select", "load"]):
            grid.attach(self.buttons[name], i, 0, 1, 1)
        return grid

    def create_slot_action_button(self, icon, label, color, script):
        screen_width = get_screen_width(self)
        button = VerButton(
            image=VImage(file_name=f"{icon}.svg",
                         width=screen_width / 15, height=screen_width / 15),
            label=VLabel(content=label, size=screen_width / 65, bold=True))
        base_class = "vvd-slot-ctrl-btn"
        button.original_color = color
        button.refresh_pattern = (
            f".{base_class} {{ border-bottom-color: %s; }}"
            f".{base_class}:active {{ background-color: %s; }}")
        _apply_dynamic_css(button, base_class, color, button.refresh_pattern)
        button.connect("clicked", lambda w: self.mms_slot_action(script))
        return button

    def mms_slot_action(self, script):
        """Execute GCode command for slot action"""
        self.mms_controller.send_script(script)

    # ---- Panel life ----
    def activate(self):
        # Update filament color
        f_color = self.mms_slot_controller.get_filament_color(self.slot_num)
        if f_color != self.color:
            self.color = f_color or "#FFFFFF"
            for widget in self.color_dependent_widgets:
                update_widget_color(widget, self.color)

        # Update filament material
        f_material = self.mms_slot_controller.get_filament_material(self.slot_num)
        if f_material != self.material:
            self.material = f_material or "N/A"
            if f_material and f_material in self.material_buttons:
                self.select_material_button(self.material_buttons[f_material])
            elif self.selected_material_button:
                self.apply_unselect(self.selected_material_button)
                self.selected_material_button = None

    def deactivate(self):
        self.close_details()

    def process_update(self, action, data):
        if action == "notify_status_update":
            self.mms_controller.handle_notify_status_update(data)

    def _on_slot_color_changed(self, slot_num, color):
        if slot_num != self.slot_num:
            return
        new_color = color or "#FFFFFF"
        if new_color != self.color:
            self.color = new_color
            for widget in self.color_dependent_widgets:
                update_widget_color(widget, self.color)

    def _on_slot_material_changed(self, slot_num, material):
        if slot_num != self.slot_num:
            return
        new_material = material or "N/A"
        if new_material != self.material:
            self.material = new_material
            if material and material in self.material_buttons:
                self.select_material_button(self.material_buttons[material])
            elif self.selected_material_button:
                self.apply_unselect(self.selected_material_button)
                self.selected_material_button = None


def _apply_dynamic_css(widget, base_class, color, pattern):
    """Apply a pattern-based CSS snippet (with %s for color) to a widget."""
    count = pattern.count("%s")
    args = (color,) * count
    provider = Gtk.CssProvider()
    provider.load_from_data((pattern % args).encode())
    ctx = widget.get_style_context()
    ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    if base_class is not None:
        ctx.add_class(base_class)


def update_widget_color(widget, color, attr_key="original_color"):
    """Recursively apply new colour to widgets that have a refresh_pattern."""
    if isinstance(widget, Gtk.Grid):
        for child in widget.get_children():
            update_widget_color(child, color)
    elif hasattr(widget, attr_key) and getattr(widget, attr_key):
        _apply_dynamic_css(widget, None, color, widget.refresh_pattern)
        setattr(widget, attr_key, color)
