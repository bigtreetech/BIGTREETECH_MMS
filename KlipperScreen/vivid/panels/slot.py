import logging

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ks_includes.screen_panel import ScreenPanel

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

        # Main grid layout
        main_grid = Gtk.Grid(
            row_homogeneous=False,
            column_homogeneous=False,
            hexpand=True,
            vexpand=True
        )
        main_grid.attach(top_area, 0, 0, 1, 1)
        main_grid.attach(bottom_area, 0, 1, 1, 1)
        self.content.add(main_grid)

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

        # Update hardware LED color
        # self.mms_update_slot_led(new_color)

    def mms_update_slot_led(self, color):
        """Update hardware LED color (strip '#' prefix)"""
        # self.color = "#FFFFFF" --> COLOR=FFFFFF
        color_hex = color[1:] if color.startswith("#") else color
        script = f"MMS_LED_SET_COLOR SLOT={self.slot_num} COLOR={color_hex}"
        self._screen._ws.klippy.gcode_script(script)

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

    def mms_slot_action(self, script):
        """Execute GCode command for slot action"""
        self._screen._ws.klippy.gcode_script(script)

    # ---- Panel life ----
    def activate(self):
        # logging.info("==== ViViD slot panel activate! ====")
        return

    def deactivate(self):
        # logging.info("==== ViViD slot panel deactivate! ====")
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
