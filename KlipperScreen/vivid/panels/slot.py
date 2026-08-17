import logging

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ks_includes.screen_panel import ScreenPanel

from vivid.controllers.mms import get_klippy_api, get_rest_client
from vivid.config.vivid_config import ColorConfig, VividConfig
from vivid.components.box import FixedSquareBox
from vivid.components.button import (
    VerticalImageButton as VerButton,
    HorizontalImageButton as HorButton
)
from vivid.components.image import VividImage as VImage
from vivid.components.label import VividLabel as VLabel
from vivid.components.utils import (
    apply_button_css,
    create_popup_window,
    create_section_container,
    get_screen_width,
)


class Panel(ScreenPanel):

    def __init__(self,
        screen,
        title=None,
        slot_num=0,
        color="#FFFFFF",
        material="ABS",
        parent_btn_hook=None,
        cfg_manager=None
    ):
        super().__init__(screen, title or _("ViViD SLOT"))
        self.slot_num = slot_num
        self.color = color
        self.material = material
        self.parent_btn_hook = parent_btn_hook
        self.cfg_manager = cfg_manager

        # Track widgets needing color updates
        self.color_dependent_widgets = []
        # Track material buttons for selection state
        self.material_buttons = {}
        # Currently selected material button
        self.selected_material_button = None

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
        bottom_area.attach(self.create_details_bar(), 0, 1, 1, 1)

        # Main grid layout (Main View)
        self.main_view = Gtk.Grid(
            row_homogeneous=False,
            column_homogeneous=False,
            hexpand=True,
            vexpand=True
        )
        self.main_view.attach(top_area, 0, 0, 1, 1)
        self.main_view.attach(bottom_area, 0, 1, 1, 1)
        
        # Initial view
        self.content.add(self.main_view)
        self.content.show_all()

    def _switch_view(self, new_view):
        # Remove current children from self.content
        for child in self.content.get_children():
            self.content.remove(child)
        # Add the new view
        self.content.add(new_view)
        self.content.show_all()

    # ---- Material Components ----
    def create_material_scroll(self):
        """Create scrollable material selection area"""
        material_scroll = Gtk.Grid(
            row_homogeneous=True,
            column_homogeneous=True,
            hexpand=False,
            vexpand=False
        )
        material_scroll.get_style_context().add_class("vvd-material-scroll")

        for i, material_obj in enumerate(VividConfig.MATERIALS):
            material = material_obj.name
            button = self.create_material_button(self.color, material)
            self.material_buttons[material] = button
            material_scroll.attach(button, 0, i, 1, 1)

            # Select the button if it matches the current material
            if material == self.material:
                self.select_material_button(button)

        self.color_dependent_widgets.append(material_scroll)
        return material_scroll

    def create_material_button(self, color, material):
        """Create a material selection button"""
        screen_width = get_screen_width(self)
        font_size = screen_width / 45

        btn = HorButton(label=VLabel(content=material, size=font_size))
        btn.set_halign(Gtk.Align.START)
        btn.connect(
            "clicked",
            lambda _, m=material: self.refresh_slot_material(m)
        )

        # Define pattern for refresh
        base_class = "vvd-material-scroll-btn"
        btn.refresh_pattern = f"""
            .{base_class} {{
                border-left-color: %s;
            }}
            .{base_class}:active {{
                background-color: %s;
            }}
        """

        # Apply dynamic CSS
        apply_button_css(btn, base_class, "")
        apply_button_css(btn, f"{base_class}:active", "")
        return btn

    def select_material_button(self, button):
        """Select a material button and deselect the previous one"""
        # Deselect previously selected button
        if self.selected_material_button:
            # Use transparent color to effectively remove the border
            self.apply_button_border(
                self.selected_material_button, 
                "transparent"
            )
            # Recover original_color to None to skip refresh
            self.selected_material_button.original_color = None

        # Update the selected button
        button.original_color = self.color
        self.selected_material_button = button
        # Apply selected style
        self.apply_button_border(button, self.color)

    def apply_button_border(self, button, color):
        """Apply the border style to a selected material button"""
        data = button.refresh_pattern % (color, color)
        add_widget_context(button, data)

    def refresh_slot_material(self, material):
        """Update the selected material and UI state"""
        # Update current material
        self.material = material

        # Update UI selection
        if material in self.material_buttons:
            self.select_material_button(self.material_buttons[material])

        # Notify parent if hook exists
        if self.parent_btn_hook:
            self.parent_btn_hook(self.slot_num, label=material)

        # Update config manager cache
        self.cfg_manager.update_slot_material(self.slot_num, material)

        # Sync to Klipper
        script = f"MMS_SLOT_MAP SLOT={self.slot_num} MATERIAL='{material}'"
        get_klippy_api(self._screen).gcode_script(script)

    # ---- Color Palette Components ----
    def create_color_palette(self):
        """Create a color selection grid"""
        palette = Gtk.Grid(
            row_homogeneous=True, 
            column_homogeneous=True, 
            hexpand=True,
            vexpand=True
        )
        palette.get_style_context().add_class("vvd-color-palette")

        # Two-row color arrangement
        palette_map = (
            ("red", "orange", "yellow", "lime", "green", "cyan"),
            ("blue", "purple", "pink", "white", "black", "gray")
        )
        for row, colors in enumerate(palette_map):
            for col, color_name in enumerate(colors):
                btn = self.create_color_button(color_name)
                palette.attach(btn, col, row, 1, 1)

        return palette

    def create_color_button(self, color_name):
        """Add individual color button to palette"""
        screen_width = get_screen_width(self)
        square_size = screen_width / 12
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

        # Update all UI components
        for widget in self.color_dependent_widgets:
            update_widget_color(widget, new_color)

        # Notify parent if hook exists
        if self.parent_btn_hook:
            self.parent_btn_hook(self.slot_num, color=new_color)

        # Update config manager cache
        self.cfg_manager.update_slot_color(self.slot_num, new_color)

        # Sync to Klipper
        color_hex = new_color[1:] if new_color.startswith("#") else new_color
        color_hex = color_hex.lower()
        script = f"MMS_SLOT_MAP SLOT={self.slot_num} COLOR='{color_hex}'"
        get_klippy_api(self._screen).gcode_script(script)

        # Update hardware LED color
        self.mms_update_slot_led(new_color)

    def mms_update_slot_led(self, color):
        """Update hardware LED color (strip '#' prefix)"""
        # self.color = "#FFFFFF" --> COLOR=FFFFFF
        color_hex = color[1:] if color.startswith("#") else color
        color_hex = color_hex.lower()
        script = f"MMS_LED_SET_COLOR SLOT={self.slot_num} COLOR={color_hex}"
        get_klippy_api(self._screen).gcode_script(script)

    # ---- SLOT Control Functions ----
    def create_slot_control(self, slot_num, color):
        # Create action buttons with consistent styling
        self.buttons = {
            "select": self.create_slot_action_button(
                "vivid_select", "Select", color, f"MMS_SELECT_U SLOT={slot_num}"
            ),
            "load": self.create_slot_action_button(
                "vivid_load", "Extruder", color, f"MMS_LOAD_U SLOT={slot_num}"
            ),
            "prepare": self.create_slot_action_button(
                "vivid_prepare", "Buffer", color, f"MMS_PREPARE_U SLOT={slot_num}"
            ),
            "pop": self.create_slot_action_button(
                "vivid_pop", "Inlet", color, f"MMS_POP_U SLOT={slot_num}"
            ),
            "pre_load": self.create_slot_action_button(
                "vivid_pre_load", "Pre-Load", color, f"MMS_PRE_LOAD SLOT={slot_num}"
            )
        }

        # Build button grid with proper alignment
        # Notice always be grid for refresh color
        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True)

        for i, button_name in enumerate(["select", "load", "prepare", "pop", "pre_load"]):
            # x=0, y=0, width=2, height=1
            grid.attach(self.buttons[button_name], i, 0, 1, 1)

        return grid

    def create_slot_action_button(self, icon, label, color, script):
        # Calculate dimensions based on screen size
        screen_width = get_screen_width(self)
        # width = height = screen_width / 15
        # font_size = screen_width / 50
        width = height = screen_width / 20
        font_size = screen_width / 60

        # Create button
        button = VerButton(
            image=VImage(file_name=f"{icon}.svg", width=width, height=height),
            label=VLabel(content=label, size=font_size, bold=True)
        )
        # Apply styling
        base_class = "vvd-slot-ctrl-btn"
        apply_button_css(button, base_class, f"border-bottom-color: {color};")
        apply_button_css(button, f"{base_class}:active," f"background-color: {color};")

        # Mark for refresh color
        button.original_color = color
        # button.refresh_pattern = f".{base_class} {{border-bottom-color: {new_color};}}"
        button.refresh_pattern = f"""
        .{base_class} {{
            border-bottom-color: %s;
        }}
        .{base_class}:active {{
            background-color: %s;
        }}
        """

        # Connect event handler
        button.connect("clicked", lambda w: self.mms_slot_action(script))

        return button

    def create_details_bar(self):
        bar = Gtk.Grid(row_homogeneous=True, column_homogeneous=True)
        bar.attach(self.create_details_button(), 0, 0, 1, 1)
        return bar

    def create_details_button(self):
        screen_width = get_screen_width(self)
        font_size = screen_width / 60
        button = HorButton(
            label=VLabel(content="Details", size=font_size, bold=True)
        )
        base_class = "vvd-slot-details-btn"
        apply_button_css(button, base_class, f"border-bottom-color: {self.color};")
        apply_button_css(
            button, f"{base_class}:active", f"background-color: {self.color};"
        )
        button.original_color = self.color
        button.refresh_pattern = f"""
        .{base_class} {{
            border-bottom-color: %s;
        }}
        .{base_class}:active {{
            background-color: %s;
        }}
        """
        self.color_dependent_widgets.append(button)
        button.connect("clicked", lambda w: self.show_details_window())
        return button

    def _format_gcode_str(self, value):
        escaped = value.replace('"', '\\"')
        return f"\"{escaped}\""

    def _parse_optional_float(self, value, field_name):
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            logging.error(f"Invalid {field_name} value: {value}")
            return "__invalid__"

    def _coalesce_detail_value(self, value, fallback):
        if value is None:
            return fallback
        if isinstance(value, str):
            value = value.strip()
            return value if value else fallback
        return str(value)

    def _get_lane_data(self):
        client = get_rest_client(self._screen)
        if not client:
            return None
        lane_result = client.send_request("server/database/item?namespace=lane_data")
        if not lane_result or not isinstance(lane_result, dict):
            return None
        lane_data = lane_result.get("value", lane_result)
        if not isinstance(lane_data, dict):
            return None
        slot_data = lane_data.get(f"lane{self.slot_num}")
        return slot_data if isinstance(slot_data, dict) else None

    def _get_mms_metadata(self):
        client = get_rest_client(self._screen)
        if not client:
            return None
        # Query Klipper directly for mms object status
        method = "printer/objects/query?mms"
        result = client.send_request(method)
        if result is False:
            return None
        
        mms_data = result.get("status", {}).get("mms", {})
        slots_data = mms_data.get("slots", {})
        slot_data = slots_data.get(str(self.slot_num))
        if not slot_data:
            return None
        
        # Extract filament info
        info = slot_data.get("filament_info", {})
        vendor = info.get("filament_manufacturer")
        name = info.get("filament_type_detailed") or info.get("color_name_a")
        nozzle_temp = info.get("nozzle_temp")
        bed_temp = info.get("bed_temperature")
        
        # Fallback to direct fields if info is sparse
        if not vendor:
            vendor = slot_data.get("filament_vendor")
        if not name:
            name = slot_data.get("filament_name")
        
        return {
            "vendor": vendor,
            "name": name,
            "nozzle_temp": nozzle_temp,
            "bed_temp": bed_temp
        }

    def show_details_window(self):
        vendor, name, nozzle_temp, bed_temp = self.cfg_manager.get_slot_details(
            self.slot_num
        )
        
        # Try metadata from Klipper first
        meta = self._get_mms_metadata()
        if meta:
            vendor = self._coalesce_detail_value(meta.get("vendor"), vendor)
            name = self._coalesce_detail_value(meta.get("name"), name)
            nozzle_temp = self._coalesce_detail_value(meta.get("nozzle_temp"), nozzle_temp)
            bed_temp = self._coalesce_detail_value(meta.get("bed_temp"), bed_temp)

        screen_width = get_screen_width(self)
        font_size = screen_width / 45

        # --- Details Overview View ---
        details_view = Gtk.Grid(hexpand=True, vexpand=True)
        details_view.get_style_context().add_class("vvd-details-view")

        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        content_box.get_style_context().add_class("vvd-modal-box")
        content_box.set_size_request(screen_width * 0.8, -1)
        
        grid = Gtk.Grid(row_spacing=15, column_spacing=20, margin=20)
        content_box.pack_start(grid, True, True, 20)

        def add_detail_row(row, label_text, value, field_key):
            lbl = VLabel(content=f"<b>{label_text}:</b>", size=font_size)
            val_lbl = VLabel(content=value if value else "-", size=font_size)
            val_lbl.set_hexpand(True)
            
            edit_btn = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            img_size = int(font_size * 1.2)
            edit_img = VImage("vivid_edit.svg", width=img_size, height=img_size)
            edit_btn.set_image(edit_img)
            
            grid.attach(lbl, 0, row, 1, 1)
            grid.attach(val_lbl, 1, row, 1, 1)
            grid.attach(edit_btn, 2, row, 1, 1)
            
            edit_btn.connect("clicked", lambda w: self.show_edit_view(
                label_text, value, field_key))

        add_detail_row(0, "Vendor", vendor, "vendor")
        add_detail_row(1, "Name", name, "name")
        add_detail_row(2, "Nozzle Temp", nozzle_temp, "nozzle_temp")
        add_detail_row(3, "Bed Temp", bed_temp, "bed_temp")

        # Bottom Actions
        action_bar = Gtk.Box(spacing=20, halign=Gtk.Align.CENTER, margin_bottom=15)
        back_btn = HorButton(label=VLabel(content="Back", size=font_size, bold=True))
        back_btn.connect("clicked", lambda w: self._switch_view(self.main_view))
        action_bar.pack_start(back_btn, False, False, 0)
        content_box.add(action_bar)

        details_view.attach(content_box, 0, 0, 1, 1)
        self._switch_view(details_view)

    def show_edit_view(self, label_text, current_value, field_key):
        screen_width = get_screen_width(self)
        font_size = screen_width / 40

        edit_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin=20)
        
        # Header with Title
        header = VLabel(content=f"Edit {label_text}", size=font_size, bold=True)
        edit_view.pack_start(header, False, False, 0)

        # Large Input Field at Top
        entry = Gtk.Entry()
        entry.set_text(str(current_value) if current_value is not None else "")
        entry.get_style_context().add_class("vvd-edit-entry")
        entry.set_hexpand(True)
        
        if "temp" in field_key.lower():
            entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        
        edit_view.pack_start(entry, False, False, 0)

        # Action Buttons
        btn_box = Gtk.Box(spacing=20, halign=Gtk.Align.CENTER)
        cancel_btn = HorButton(label=VLabel(content="Cancel", size=font_size*0.8))
        confirm_btn = HorButton(label=VLabel(content="Confirm", size=font_size*0.8))
        
        # Add borders/styling
        cancel_btn.get_style_context().add_class("vvd-slot-ctrl-btn")
        confirm_btn.get_style_context().add_class("vvd-slot-ctrl-btn")
        
        btn_box.pack_start(cancel_btn, False, False, 0)
        btn_box.pack_start(confirm_btn, False, False, 0)
        edit_view.pack_start(btn_box, False, False, 0)

        self._switch_view(edit_view)
        
        # Auto-focus and show keyboard
        from gi.repository import GLib
        GLib.timeout_add(100, lambda: (
            entry.grab_focus(),
            self._screen.show_keyboard(entry=entry, box=self.content),
            False
        ))

        def on_confirm():
            new_val = entry.get_text().strip()
            self._save_single_detail(field_key, new_val)
            self._screen.remove_keyboard(box=self.content)
            # Refresh details overview
            self.show_details_window()

        def on_cancel():
            self._screen.remove_keyboard(box=self.content)
            self.show_details_window()

        confirm_btn.connect("clicked", lambda w: on_confirm())
        cancel_btn.connect("clicked", lambda w: on_cancel())
        # Also handle Enter key
        entry.connect("activate", lambda w: on_confirm())

    def _save_single_detail(self, field_key, value):
        vendor, name, nozzle_temp, bed_temp = self.cfg_manager.get_slot_details(
            self.slot_num
        )
        
        if field_key == "vendor": vendor = value
        elif field_key == "name": name = value
        elif field_key == "nozzle_temp": nozzle_temp = value
        elif field_key == "bed_temp": bed_temp = value

        # Validate temps
        if "temp" in field_key:
            f_val = self._parse_optional_float(value, field_key)
            if f_val == "__invalid__":
                return
            value = "" if f_val is None else str(f_val)
            if field_key == "nozzle_temp": nozzle_temp = value
            else: bed_temp = value

        self.cfg_manager.update_slot_details(
            self.slot_num, vendor, name, nozzle_temp, bed_temp
        )

        empty_str = "\"\""
        script = (
            f"MMS_SLOT_MAP SLOT={self.slot_num}"
            f" VENDOR={self._format_gcode_str(vendor)}"
            f" NAME={self._format_gcode_str(name)}"
            f" NOZZLE_TEMP={nozzle_temp if nozzle_temp else empty_str}"
            f" BED_TEMP={bed_temp if bed_temp else empty_str}"
        )
        get_klippy_api(self._screen).gcode_script(script)

    def mms_slot_action(self, script):
        """Execute GCode command for slot action"""
        get_klippy_api(self._screen).gcode_script(script)

    # ---- Panel life ----
    def activate(self):
        # logging.info("==== ViViD slot panel activate! ====")
        return

    def deactivate(self):
        # logging.info("==== ViViD slot panel deactivate! ====")
        self._screen.remove_keyboard(box=self.content)
        # Save new config
        self.cfg_manager.manual_save()


def add_widget_context(widget, data):
    provider = Gtk.CssProvider()
    provider.load_from_data(data.encode())
    context = widget.get_style_context()
    context.add_provider(
        provider, 
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def update_widget_color(widget, color, attr_key="original_color"):
    """Recursively update widget color scheme"""
    if isinstance(widget, Gtk.Grid):
        for child in widget.get_children():
            update_widget_color(child, color)

    elif hasattr(widget, attr_key) and \
        getattr(widget, attr_key, None):
        # Apply new border color to slot control button
        data = widget.refresh_pattern % (color, color)
        add_widget_context(widget, data)
        setattr(widget, attr_key, color)
