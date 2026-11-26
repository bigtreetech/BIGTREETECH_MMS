import time
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from ks_includes.screen_panel import ScreenPanel
from vivid.components.button import CircleButtonStaticSVG, CountdownButton, TempDisplayButton
from vivid.components.button import HorizontalImageButton as HorButton
from vivid.components.button import VerticalImageButton as VerButton
from vivid.components.image import VividImage as VImage
from vivid.components.label import VividLabel as VLabel
from vivid.components.utils import (
    apply_button_css,
    create_popup_window,
    create_section_container,
    get_screen_width,
)
from vivid.config.vivid_config import VividConfig
from vivid.controllers.mms import MMSController

# Sentinel to distinguish "argument not passed" from "explicit None/empty"
_SENTINEL = object()


@dataclass(frozen=True)
class NotifyActionType:
    proc_stat: str = "notify_proc_stat_update"
    status: str = "notify_status_update"
    gcode_res: str = "notify_gcode_response"


class Panel(ScreenPanel):
    """Main panel for ViViD interface with slot-based control"""

    def __init__(self, screen, title=None):
        """Initialize the ViViD panel"""
        super().__init__(screen, title or _("ViViD"))
        self.slot_buttons = {}

        self.temp_btn = None
        self.time_btn = None
        self.stop_heat_btn = None
        self.temp_str = "--"

        # {
        #     group_num : {
        #         "area_heat": ...,
        #         "temp_btn": ...,
        #         "time_btn": ...,
        #         "stop_heat_btn": ...,
        #     }
        # }
        self.heat_group = {}

        self.current_group = None
        self.area_groups = None
        self.scroll = None

        # SLOT buttons config
        self.column_one_row = 4
        self.heat_material_count = 4
        self.default_color = "#FFFFFF"
        self.default_material = "N/A"

        # Dryer
        self._dryer_status = None
        self._slot_layout = ()

        self.mms_selecting_slots = []
        self.notify_action = NotifyActionType()

        self.mms_controller = MMSController(self._screen)

        self.build_ui()

        self.mms_controller.register_slot_selected_callback(
            self.select_slot_button)
        self.mms_controller.register_slot_delivery_play_callback(
            self.play_slot_button)
        self.mms_controller.register_slot_delivery_pause_callback(
            self.pause_slot_button)
        self.mms_controller.register_heater_temp_callback(
            self.set_current_temp)
        self.mms_controller.register_slot_color_callback(
            self._on_slot_color_changed)
        self.mms_controller.register_slot_material_callback(
            self._on_slot_material_changed)

    def build_ui(self):
        """Build the main UI structure"""
        # Top area: Slot and delivery controls
        top_area = create_section_container("vvd-main-area-top")
        top_area.attach(self.create_slot_area(), 0, 0, 1, 1)

        # Bottom area: Heat+filament, and MMS controls
        heat_area = self.create_heat_area()
        mms_area = self.create_mms_area()
        self.mms_controls = (heat_area, mms_area)
        bottom_area = create_section_container("vvd-main-area-bottom")
        bottom_area.attach(heat_area, 0, 0, 1, 1)
        bottom_area.attach(mms_area, 1, 0, 1, 1)
        # bottom_area.attach_next_to(
        #     mms_area, heat_area,
        #     Gtk.PositionType.RIGHT, 1, 1
        # )

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

    # ---- Section Creation Methods ----
    def create_slot_area(self):
        """Create the slot selection area with colored buttons"""
        screen_width = get_screen_width(self)
        font_size = screen_width / 80
        scroll_height = screen_width / 4.3
        slider_radius = screen_width / 256
        slider_width = screen_width / 256
        slider_height = screen_width / 11
        label_margin_start = screen_width / 23
        label_margin_bottom = screen_width / 90

        self.area_groups = self.create_group_area()

        # Scroll
        # scroll = self._gtk.ScrolledWindow()
        scroll = Gtk.ScrolledWindow()
        # Horizontal auto, vertical disable
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Default weight: -1
        scroll.set_size_request(-1, scroll_height)
        scroll.get_style_context().add_class("vvd-slot-area-scroll")

        css_provider = Gtk.CssProvider()
        css = f"""
        .vvd-slot-area-scroll scrollbar slider {{
            background-color: #FFFFFF;
            border-radius: {slider_radius}px;
            min-width: {slider_width}px;
            min-height: {slider_height}px;
        }}
        """
        css_provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            scroll.get_screen(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        scroll.add(self.area_groups)
        self.scroll = scroll

        # Status
        self.status_label = VLabel(content="", size=font_size, bold=False)
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_margin_start(label_margin_start)
        self.status_label.set_margin_bottom(label_margin_bottom)

        # Combine area
        area = create_section_container("vvd-slot-area")
        area.attach(self.scroll, 0, 0, 1, 1)
        area.attach(self.status_label, 0, 1, 1, 1)
        return area

    def create_group_area(self, group_num=None):
        screen_width = get_screen_width(self)
        arrow_width = screen_width / 26
        center_width = screen_width / 12
        center_font_size = screen_width / 60
        btn_width = screen_width / 22

        has_extend = bool(self.get_mms_groups())

        area_groups = create_section_container("vvd-slot-area-groups")

        mms_slots = self.get_mms_slots()
        for slot_num, status_dct in mms_slots.items():
            # Skip not target slots
            # extend_num is None => Main group
            if status_dct.get("extend_num") != group_num:
                continue

            slot_num = int(slot_num)
            slot_button = self.create_slot_button(
                slot_num, status_dct.get("filament_color") or self.default_color,
                status_dct.get("filament_material") or self.default_material)
            # Position buttons in the grid
            # No need to sort slot_num, attach by calculation
            area_groups.attach(
                slot_button,
                slot_num % self.column_one_row + 1,
                # slot_num // self.column_one_row,
                0,
                1, 1
            )
            # self.mms_controller.update_slot_led(slot_num, color)

        # Extend control
        if has_extend:
            # Arrow Up
            btn_prev = VerButton(
                image=VImage(
                    file_name="vivid_arrow_up.svg",
                    width=arrow_width, height=arrow_width
                )
            )
            btn_prev.get_style_context().add_class("vvd-slot-extend-button")
            btn_prev.set_size_request(btn_width, -1)
            btn_prev.connect("clicked", self.show_slots_previous)

            # Center Logo
            if group_num is not None:
                extend_label = VLabel(
                    content=f"#{int(group_num)}",
                    size=center_font_size,
                    bold=True
                )
                extend_label.get_style_context().add_class("vvd-slot-extend-label")
                btn_main = VerButton(
                    image=VImage(
                        file_name="vivid_extend.svg",
                        width=center_width, height=center_width
                    ),
                    label=extend_label
                )
            else:
                btn_main = VerButton(
                    image=VImage(
                        file_name="vivid_extend.svg",
                        width=center_width, height=center_width
                    )
                )
            btn_main.get_style_context().add_class("vvd-slot-extend-button")
            btn_main.set_size_request(btn_width, -1)

            # Arrow Down
            btn_next = VerButton(
                image=VImage(
                    file_name="vivid_arrow_down.svg",
                    width=arrow_width, height=arrow_width
                )
            )
            btn_next.get_style_context().add_class("vvd-slot-extend-button")
            btn_next.set_size_request(btn_width, -1)
            btn_next.connect("clicked", self.show_slots_next)

            # Combine
            extend_ctrl = create_section_container()
            extend_ctrl.attach(btn_prev, 0, 0, 1, 1)
            extend_ctrl.attach(btn_main, 0, 1, 1, 1)
            extend_ctrl.attach(btn_next, 0, 2, 1, 1)
            area_groups.attach(extend_ctrl, self.column_one_row+1, 0, 1, 1)

        return area_groups

    def create_heat_area(self):
        """
        Create a heat control panel with
        temperature/time buttons and material selection
        """
        # Size calculation
        screen_width = get_screen_width(self)
        # Calculate proportional dimensions:
        width = height = screen_width / 35
        font_size_material = screen_width / 50
        font_size_btn = screen_width / 63

        width_btn = screen_width / 3.8
        height_btn = screen_width / 11
        spacing_btn = screen_width / 100

        width_m_btn = screen_width / 8

        width_stop_image = screen_width / 18
        width_stop_btn = screen_width / 9

        area = create_section_container("vvd-heat-area")
        area_f = create_section_container("vvd-heat-area-f")

        # Temperature display button
        # Shows current/target temperature
        self.temp_btn = TempDisplayButton(
            image=VImage(file_name="vivid_temp.svg", width=width, height=height),
            label=VLabel(content="Temp", size=font_size_btn),
            temp=VLabel(
                content=f"{self.get_current_temp()}°C",
                size=font_size_btn
            )
        )
        self.temp_btn.get_style_context().add_class("vvd-heat-btn-temp")
        self.temp_btn.set_size_request(width_btn, height_btn)
        self.temp_btn.update_spacing(spacing_btn)
        self.temp_btn.refresh_temp(get_temp_func=self.get_current_temp)

        # Time display button
        # Shows elapsed time
        self.time_btn = CountdownButton(
            image=VImage(file_name="vivid_time.svg", width=width, height=height),
            label=VLabel(content="Time", size=font_size_btn),
            countdown=VLabel(content="--: --: --", size=font_size_btn)
        )
        self.time_btn.get_style_context().add_class("vvd-heat-btn-time")
        self.time_btn.set_size_request(width_btn, height_btn)
        self.time_btn.update_spacing(spacing_btn)

        area_btn = create_section_container()
        area_btn.attach(self.temp_btn, 0, 0, 1, 1)   # Column 0, Row 0
        area_btn.attach(self.time_btn, 0, 1, 1, 1)   # Column 0, Row 1
        area_f.attach(area_btn, 0, 0, 1, 1)

        # Filament material buttons
        # Create material buttons with temperature-specific actions
        area_ma = create_section_container()
        for i, material in enumerate(VividConfig.MATERIALS):
            if i >= self.heat_material_count:
                break

            btn = VerButton(
                label=VLabel(
                    content=material.name,
                    size=font_size_material,
                    bold=True
                )
            )
            btn.get_style_context().add_class(material.css_class)
            apply_button_css(btn, material.css_class,
                f"border-bottom-color: {material.display_color};")
            btn.set_size_request(width_m_btn, -1)

            btn.connect(
                "clicked",
                lambda _, m=material: self.heat_with_temp(m)
            )

            # Position buttons in the grid:
            # - Columns 1 & 2 (since i%2: 0 => 1, 1 => 2)
            # - Rows 0 and 1 (i//2: 0-1 => row 0, 2-3 => row 1)
            area_ma.attach(btn, i % 2 + 1, i // 2, 1, 1)

        area_f.attach(area_ma, 1, 0, 1, 2)

        # Stop heating button
        self.stop_heat_btn = VerButton(
            image=VImage(
                file_name="vivid_stop_idle.svg",
                width=width_stop_image,
                height=width_stop_image
            ),
            label=VLabel(
                content="Stop",
                size=font_size_btn,
                bold=True
            )
        )
        self.stop_heat_btn.get_style_context().add_class("vvd-stop-heat-btn")
        self.stop_heat_btn.connect("clicked", self.mms_heater_stop)
        self.stop_heat_btn.set_size_request(width_stop_btn, -1)
        self.stop_heat_btn.update_spacing(spacing_btn)
        area_f.attach(self.stop_heat_btn, 2, 0, 1, 2)

        area.attach(area_f, 0, 0, 1, 1)
        return area

    def create_mms_area(self):
        """Create MMS control area"""
        # MMS button dimensions
        # width = height = self._gtk.img_scale * self.bts * 4.8
        screen_width = get_screen_width(self)
        width = height = screen_width / 18
        # width_btn = screen_width / 100
        # height_btn = screen_width / 50
        font_size_btn = screen_width / 60
        spacing_btn = screen_width / 100

        # Main MMS button
        mms_btn = VerButton(
            image=VImage(
                file_name="vivid_more.svg",
                width=width,
                height=height
            ),
            label=VLabel(
                content="More",
                size=font_size_btn,
                bold=True
            )
        )
        mms_btn.get_style_context().add_class("vvd-mms-main-btn")
        # mms_btn.set_size_request(width_btn, height_btn)
        mms_btn.connect("clicked", lambda w: self.show_mms_window())
        mms_btn.update_spacing(spacing_btn)

        area_f = create_section_container("vvd-mms-area-f")
        area_f.attach(mms_btn, 0, 0, 1, 1)

        area = create_section_container("vvd-mms-area")
        area.attach(area_f, 0, 0, 1, 1)

        return area

    # ---- Slot Functions ----
    def create_slot_button(self, slot_num, color, material):
        """Create a slot button with animation and labeling"""
        slot_button = self.slot_buttons.get(slot_num)
        need_image = color == self.default_color
        if slot_button:
            color = slot_button["color"]
            material = slot_button["material"]
            need_image = slot_button["need_image"]

        screen_width = get_screen_width(self)
        diameter = screen_width / 27
        border_width = screen_width / 120
        font_size_num = screen_width / 54
        font_size_material = screen_width / 65
        img_width = screen_width / 11

        # Create circle button
        btn_image = VImage(
            file_name="vivid_slot_mosaic.svg",
            width=img_width,
            height=img_width
        ) if need_image else None

        circle = CircleButtonStaticSVG(
            image=btn_image,
            diameter=diameter,
            border_width=border_width,
            border_color=color,
            label=VLabel(
                content=f"{slot_num}",
                size=font_size_num,
                bold=True
            )
        )

        # Create bottom label
        bottom_label = VLabel(
            content=material,
            size=font_size_material,
            bold=False
        )

        # Compose components
        button_wrapper = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            # spacing=10
        )
        # button_wrapper.pack_start(circle, True, False, 0)
        # button_wrapper.pack_start(bottom_label, True, False, 0)
        button_wrapper.add(circle)
        button_wrapper.add(bottom_label)

        # Final button container
        button = Gtk.Button(
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True
        )
        button.add(button_wrapper)
        # Apply dynamic CSS
        self.apply_slot_button_color(button, color)
        button.connect(
            "clicked",
            lambda _: self.open_slot_panel(slot_num)
        )

        # Format dct for update
        self.slot_buttons[slot_num] = {
            "button" : button,
            "circle" : circle,
            "bottom_label" : bottom_label,
            "color" : color,
            "material" : material,
            "need_image" : need_image
        }
        return button

    def apply_slot_button_color(self, button, color):
        """Apply dynamic border-bottom and :active colour to a slot button.

        Reuses a single CSS provider per button (stored as _slot_css_provider)
        to avoid provider accumulation on repeated colour changes.
        """
        css = (
            f".vvd-slot-button {{ border-bottom-color: {color}; }}"
            f".vvd-slot-button:active {{ background-color: {color}; }}"
        )
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        ctx = button.get_style_context()
        # Replace previous provider if one exists
        old = getattr(button, '_slot_css_provider', None)
        if old is not None:
            ctx.remove_provider(old)
        ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        ctx.add_class("vvd-slot-button")
        button._slot_css_provider = provider

    def open_slot_panel(self, slot_num):
        slot = self.slot_buttons[slot_num]
        self._screen.show_panel(
            "vivid/panels/slot",
            title = f"ViViD SLOT {slot_num}",
            # Use an unique_panel_name here,
            # panel_name is the cache key of KlipperScreen's show_panel()
            panel_name = f"vivid/slot/{slot_num}",
            slot_num = slot_num,
            color = slot["color"],
            material = slot["material"]
        )

    def _on_slot_color_changed(self, slot_num, color):
        """WebSocket-pushed filament colour update — refresh slot button in-place."""
        slot_button = self.slot_buttons.get(slot_num)
        if slot_button is None:
            return
        self.refresh_slot_button(slot_num, color=color)

    def _on_slot_material_changed(self, slot_num, material):
        """WebSocket-pushed filament material update — refresh label in-place."""
        slot_button = self.slot_buttons.get(slot_num)
        if slot_button is None:
            return
        self.refresh_slot_button(slot_num, label=material)

    def refresh_slot_button(self, slot_num, color=_SENTINEL, label=_SENTINEL):
        slot_button = self.slot_buttons[slot_num]

        if color is not _SENTINEL:
            actual_color = color or self.default_color
            circle = slot_button["circle"]
            circle.set_border_color(actual_color)
            self.apply_slot_button_color(slot_button["button"], actual_color)
            self.slot_buttons[slot_num]["color"] = actual_color
            # Only remove mosaic image when a real (non-default) colour is set
            if actual_color != self.default_color:
                circle.remove_mosaic_image()
                self.slot_buttons[slot_num]["need_image"] = False
            else:
                self.slot_buttons[slot_num]["need_image"] = True
                if not circle.has_mosaic_image():
                    # Image was destroyed by a previous colour — recreate
                    self._replace_slot_button_in_grid(slot_num)

        if label is not _SENTINEL:
            actual_label = label or self.default_material
            slot_button["bottom_label"].set_content(actual_label)
            self.slot_buttons[slot_num]["material"] = actual_label

    def _replace_slot_button_in_grid(self, slot_num):
        """Recreate a slot button in-place (with mosaic image) and replace in grid."""
        old_button = self.slot_buttons[slot_num]["button"]
        new_button = self.create_slot_button(
            slot_num,
            self.slot_buttons[slot_num]["color"],
            self.slot_buttons[slot_num]["material"]
        )
        if self.area_groups:
            col = slot_num % self.column_one_row + 1
            self.area_groups.remove(old_button)
            self.area_groups.attach(new_button, col, 0, 1, 1)
            new_button.show_all()

    def show_slots_previous(self, widget):
        if self.scroll and self.area_groups:
            mms_groups = self.get_mms_groups()
            group_num = prev_element(mms_groups, self.current_group)
            if group_num is self.current_group:
                return

            self.show_slot_group(group_num)

    def show_slots_next(self, widget):
        if self.scroll and self.area_groups:
            mms_groups = self.get_mms_groups()
            group_num = next_element(mms_groups, self.current_group)
            if group_num is self.current_group:
                return

            self.show_slot_group(group_num)

    def show_slot_group(self, group_num):
        container = self.area_groups.get_parent()
        self.area_groups.destroy()
        self.slot_buttons.clear()
        self.current_group = group_num
        self.area_groups = self.create_group_area(group_num)
        container.add(self.area_groups)
        self.area_groups.show_all()
        self._update_dryer_ui()

    def refresh_slot_layout(self):
        slots = self.get_mms_slots()
        layout = tuple(sorted((int(num), data.get("extend_num")) for num, data in slots.items()))
        if layout != self._slot_layout:
            self._slot_layout = layout
            group = self.current_group if self.current_group in self.get_mms_groups() else None
            self.show_slot_group(group)
        connection = self.mms_controller.connection
        if not connection.connected:
            message = _("Moonraker is disconnected.")
        elif connection.available is False:
            message = _("This printer does not have MMS configured.")
        elif "slots" not in self.mms_controller.get_status():
            message = _("Waiting for MMS status…")
        elif not slots:
            message = _("No MMS slots available.")
        else:
            message = ""
        for area in self.mms_controls:
            area.set_sensitive(not message)
        self.status_label.set_content(message)

    # ---- Heat Functions ----
    # def heat_with_temp(self, temperature, heat_duration, color):
    #     """
    #     Handle temperature setting button click
    #     temp_btn: The BaseImageButton that show the temperature
    #     time_btn: The BaseImageButton that show the remain time
    #     """
    #     # Send command to printer
    #     self.mms_controller.start_heating(self.current_group, temperature)
    #     # Update UI
    #     self.temp_btn.set_target(temperature, color)
    #     self.time_btn.start_countdown(heat_duration, teardown_func=self._stop_heating)
    #     if self.stop_heat_btn:
    #         self.stop_heat_btn.image.update_image_file("vivid_stop_busy.svg")

    def heat_with_temp(self, material):
        """
        Handle temperature setting button click
        temp_btn: The BaseImageButton that show the temperature
        time_btn: The BaseImageButton that show the remain time
        """
        # Send command to printer
        self.mms_controller.start_mms_dryer(material.name, self.current_group)

    def _update_dryer_ui(self):
        # Update dryer UI
        mms_dryer_status = self.mms_controller.get_mms_dryer_status(self.current_group)
        finish_at = mms_dryer_status.get("finish_at")
        target = mms_dryer_status.get("temperature")
        if not mms_dryer_status.get("is_heating") or finish_at is None or target is None:
            self._stop_heating()
            return

        if self._dryer_status == (finish_at, target):
            return
        remaining = finish_at - time.time()
        if remaining <= 0:
            self._stop_heating()
            return
        self._dryer_status = (finish_at, target)
        self.temp_btn.set_target(target)
        self.time_btn.start_countdown(
            int(remaining),
            teardown_func=self._stop_heating
        )
        if self.stop_heat_btn:
            self.stop_heat_btn.image.update_image_file("vivid_stop_busy.svg")

    def _stop_heating(self):
        if self._dryer_status is None:
            return
        self._dryer_status = None
        self.temp_btn.reset_target()
        self.time_btn.stop_countdown()
        if self.stop_heat_btn:
            self.stop_heat_btn.image.update_image_file("vivid_stop_idle.svg")

    def set_current_temp(self, temp):
        if temp is not None:
            # self.temp_str = f"{temp:.1f}"
            self.temp_str = f"{int(temp)}"

    def get_current_temp(self):
        return self.temp_str

    # ---- MMS Window Functions ----
    def show_mms_window(self):
        """Show MMS control window"""
        title = "MMS Control"

        # Create title label
        screen_width = get_screen_width(self)
        label_title = VLabel(content=title, size=screen_width/30, bold=True)

        # Create MMS action buttons
        buttons = [
            ("mms00", "vivid_mms00", "MMS00\nMMS_STATUS", self.mms_action_c, "MMS_STATUS"),
            ("mms0", "vivid_mms0", "MMS0\nMMS_SAMPLE", self.mms_action_c, "MMS_SAMPLE"),
            ("mms9", "vivid_walk", "MMS9\nMMS_SLOTS_WALK", self.mms_action, "MMS_SLOTS_WALK"),
            ("mms8", "vivid_loop", "MMS8\nMMS_SLOTS_LOOP", self.mms_action, "MMS_SLOTS_LOOP"),
            (
                "mms_unselect", "vivid_unselect", "MMS_UNSELECT",
                self.mms_unselect_action, "MMS_UNSELECT",
            ),
        ]

        # Create and store buttons
        all_buttons = {}
        for btn_id, icon, label, handler, param in buttons:
            all_buttons[btn_id] = self.create_mms_button(icon, label, handler, param)

        # Layout with centered title
        title_container = Gtk.Box(halign=Gtk.Align.CENTER, hexpand=True)
        title_container.add(label_title)

        # Create layout grid
        grid = Gtk.Grid(
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True,
            column_homogeneous=True,
        )
        grid.attach(title_container, 0, 0, 2, 1)
        # Position buttons
        grid.attach(all_buttons["mms00"], 0, 1, 1, 1)
        grid.attach(all_buttons["mms0"], 0, 2, 1, 1)
        grid.attach(all_buttons["mms9"], 1, 1, 1, 1)
        grid.attach(all_buttons["mms8"], 1, 2, 1, 1)
        grid.attach(all_buttons["mms_unselect"], 0, 3, 2, 1)

        # Create popup window
        self.close_window_func = create_popup_window(title, grid, "vvd-mms-window")

    def create_mms_button(self, icon_name, label_text, click_handler, handler_param=None):
        """
        Create a standardized MMS control button
        Args:
            icon_name: SVG file name without extension
            label_text: Button label text
            click_handler: Click event handler
            handler_param: Parameter to pass to handler (optional)
        Returns:
            Configured HorizontalButton instance
        """
        # Calculate dimensions
        screen_width = get_screen_width(self)
        width = height = screen_width / 20
        font_size = screen_width / 60

        # Create button
        button = HorButton(
            image=VImage(file_name=f"{icon_name}.svg", width=width, height=height),
            label=VLabel(content=label_text, size=font_size)
        )
        # Apply base styling
        button.set_halign(Gtk.Align.START)
        button.set_valign(Gtk.Align.START)
        button.get_style_context().add_class("vvd-mms-btn")

        # Connect event handler
        if handler_param is not None:
            button.connect("clicked", lambda w: click_handler(handler_param))
        else:
            button.connect("clicked", click_handler)

        return button

    def mms_action(self, script):
        self.mms_controller.send_script(script)

    def mms_action_c(self, script):
        self.mms_controller.send_script(script)
        self._screen.show_panel("console")
        if self.close_window_func:
            self.close_window_func()
            self.close_window_func = None

    def mms_unselect_action(self, script):
        self.mms_controller.send_script(script)
        # Defer clearing: wait for the WebSocket update to confirm
        # the selector has actually been released
        GLib.timeout_add_seconds(1, self._clear_slot_selection)

    def _clear_slot_selection(self):
        """Clear focus ring from all currently selected slot buttons."""
        for slot_num in list(self.mms_selecting_slots):
            btn = self.slot_buttons.get(slot_num)
            if btn:
                btn.get("circle").set_focusing(False)
        self.mms_selecting_slots.clear()
        return False  # single-shot timeout

    def mms_heater_stop(self, widget=None):
        self.mms_controller.stop_mms_dryer(self.current_group)

    # ---- Panel life ----
    def activate(self):
        self.refresh_slot_layout()
        selecting_slot_num = []

        mms_selectors = self.mms_controller.get_mms_selectors()
        for index,status_dct in mms_selectors.items():
            # Focusing slot is not None
            slot_num = status_dct.get("focus_slot")
            if slot_num is not None:
                selecting_slot_num.append(int(slot_num))

        mms_slots = self.get_mms_slots()
        for slot_num, status_dct in mms_slots.items():
            slot_num = int(slot_num)
            # Pin selector is triggered
            if status_dct.get("selector"):
                selecting_slot_num.append(slot_num)

            # Key 'slot_num' is string
            slot_button = self.slot_buttons.get(slot_num, None)
            if slot_button:
                # Update filament material
                f_material = status_dct.get("filament_material")
                material = slot_button["material"]

                # if f_material and f_material != material:
                if f_material != material:
                    slot_button["material"] = f_material
                    self.refresh_slot_button(slot_num, label=f_material)

                # Update filament color
                f_color = status_dct.get("filament_color")
                color = slot_button["color"]

                # if f_color and f_color != color:
                if f_color != color:
                    if f_color:
                        # Real filament color assigned — destroy mosaic image,
                        # show colored circle with CSS border
                        slot_button["color"] = f_color
                        slot_button["need_image"] = False
                        self.refresh_slot_button(slot_num, color=f_color)
                    else:
                        # Filament color cleared — refresh_slot_button
                        # handles CSS reset + mosaic restoration
                        self.refresh_slot_button(slot_num, color=None)

        # Play select animation
        for slot_num in set(selecting_slot_num):
            self.select_slot_button(slot_num)

        # Update dryer UI
        self._update_dryer_ui()

    def deactivate(self):
        return
        # for slot_num in self.slot_buttons:
        #     self.pause_slot_button(slot_num)

    def process_update(self, action, data):
        # logging.info(f"#### action:{action}, data:{data}")

        if self._screen.printer.state in ("error", "shutdown"):
            return

        if action == self.notify_action.status:
            if "mms" in data or not self._slot_layout:
                self.refresh_slot_layout()
                self._update_dryer_ui()
            self.mms_controller.handle_notify_status_update(data)

        # if action == self.notify_action.proc_stat:
        #     return
        # elif action == self.notify_action.status:
        #     self.mms_controller.handle_notify_status_update(data)
        # elif action == self.notify_action.gcode_res:
        #     # "data" is a string
        #     # begin with "// " -> "// slot[1] 'selector' is triggered"
        #     # log = data[3:]
        #     # self.parse_mms_log(log)
        #     return

        # if self._screen.printer and \
        #     self._screen.printer.state in ("printing", "paused"):
        #         mute_buttons()

    def select_slot_button(self, slot_num):
        # Init status, slot_num could be None
        if slot_num is None:
            return

        # Find the index&offset slots
        period_slots = find_period(slot_num)
        for s_slot in self.mms_selecting_slots:
            if s_slot in period_slots:
                # Disable focusing of same period slot
                btn = self.slot_buttons.get(s_slot, None)
                if btn:
                    btn.get("circle").set_focusing(False)
                # Also remove from list
                self.mms_selecting_slots.remove(s_slot)

        # Append to list
        self.mms_selecting_slots.append(slot_num)
        # Enable focusing
        btn = self.slot_buttons.get(slot_num, None)
        if btn:
            btn.get("circle").set_focusing(True)

    def play_slot_button(self, slot_num, reverse=False):
        return
        # btn = self.slot_buttons.get(slot_num, None)
        # if btn:
        #     btn.get("circle").start_animation(reverse)

    def pause_slot_button(self, slot_num):
        return
        # btn = self.slot_buttons.get(slot_num, None)
        # if btn:
        #     btn.get("circle").stop_animation()

    def kick_slot_button(self, slot_num):
        return
        # btn = self.slot_buttons.get(slot_num, None)
        # if btn:
        #     btn.get("circle").kick_animation()

    def get_mms_slots(self):
        return self.mms_controller.get_mms_slots()

    def get_mms_groups(self):
        return self.mms_controller.get_extend_nums()


# ==== Utils ====
def find_period(n):
    period = 4
    index = (n // period) * period
    return [index, index+1, index+2, index+3]


def prev_element(lst, i):
    if i is None:
        if lst:
            return lst[-1]
        return None
    try:
        idx = lst.index(i)
    except ValueError:
        return None
    if idx == 0:
        return None
    return lst[idx - 1]


def next_element(lst, i):
    if i is None:
        if lst:
            return lst[0]
        return None
    try:
        idx = lst.index(i)
    except ValueError:
        return None
    if idx == len(lst) - 1:
        return None
    return lst[idx + 1]
