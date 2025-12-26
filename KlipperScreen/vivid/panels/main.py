# import logging
from dataclasses import dataclass

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ks_includes.screen_panel import ScreenPanel

from vivid.config.vivid_config import VividConfig
from vivid.config.manager import VividConfigManager
from vivid.components.button import (
    VerticalImageButton as VerButton,
    HorizontalImageButton as HorButton,
    CountdownButton, 
    TempDisplayButton,
    CircleButtonAnime
)
from vivid.components.image import VividImage as VImage
from vivid.components.label import VividLabel as VLabel
from vivid.components.utils import (
    create_popup_window,
    create_section_container,
    apply_button_css,
    get_screen_width
)
from vivid.controllers.mms import MMSController


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

        self.mms_selecting_slots = []

        self.cfg_manager = VividConfigManager(
            self._screen._config.config_path)

        self.build_ui()

        self.notify_action = NotifyActionType()

        self.mms_controller = MMSController(self._screen)
        # self.mms_controller.subscribe()
        self.mms_controller.register_slot_selected_callback(
            self.select_slot_button)
        self.mms_controller.register_slot_delivery_play_callback(
            self.play_slot_button)
        self.mms_controller.register_slot_delivery_pause_callback(
            self.pause_slot_button)
        self.mms_controller.register_heater_temp_callback(
            self.set_current_temp)

    def build_ui(self):
        """Build the main UI structure"""
        # Top area: Slot and delivery controls
        top_area = create_section_container("vvd-main-area-top")
        top_area.attach(self.create_slot_area(), 0, 0, 1, 1)

        # Bottom area: Heat+filament, and MMS controls
        bottom_area = create_section_container("vvd-main-area-bottom")
        bottom_area.attach(self.create_heat_area(), 0, 0, 1, 1)
        bottom_area.attach(self.create_mms_area(), 1, 0, 1, 1)

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

    def send_klippy_script(self, script):
        self._screen._ws.klippy.gcode_script(script)

    # ---- Section Creation Methods ----
    def create_slot_area(self):
        """Create the slot selection area with colored buttons"""
        area = Gtk.Grid(
            row_homogeneous=True,
            column_homogeneous=True,
            hexpand=True,
            vexpand=True
        )
        area.get_style_context().add_class("vvd-slot-area")

        for slot_num, color, material in self.cfg_manager.get_slots_configuration():
            slot_button = self.create_slot_button(slot_num, color, material)
            area.attach(slot_button, slot_num, 0, 1, 1)
            # self.mms_update_slot_led(slot_num, color)

        # scroll = self._gtk.ScrolledWindow()
        scroll = Gtk.ScrolledWindow()
        # Horizontal auto, vertical disable
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.get_style_context().add_class("vvd-slot-area")
        scroll.add(area)
        return scroll

    def create_heat_area(self):
        """
        Create a heat control panel with 
        temperature/time buttons and material selection
        """
        # Create main grid container
        area = Gtk.Grid(
            row_homogeneous=False,      # Rows don't need equal height
            column_homogeneous=False,   # Columns don't need equal width
            hexpand=True,               # Expand horizontally to fill available space
            vexpand=True                # Expand vertically to fill available space
        )
        area.get_style_context().add_class("vvd-heat-area")

        # Button image size calculation
        screen_width = get_screen_width(self)
        # Calculate proportional dimensions:
        width = height = screen_width / 35
        font_size = screen_width / 50

        # Temperature display button (shows current/target temperature)
        self.temp_btn = TempDisplayButton(
            image=VImage(file_name="vivid_temp.svg", width=width, height=height),
            label=VLabel(content=f"Temp {self.get_current_temp()}°C", size=font_size)
        )
        self.temp_btn.get_style_context().add_class("vvd-heat-btn-temp")
        self.temp_btn.refresh_temp(get_temp_func=self.get_current_temp)

        # Time display button (shows elapsed time)
        self.time_btn = CountdownButton(
            image=VImage(file_name="vivid_time.svg", width=width, height=height),
            label=VLabel(content="Time --:--:--", size=font_size)
        )
        self.time_btn.get_style_context().add_class("vvd-heat-btn-time")

        # Position status buttons in the left column
        area.attach(self.temp_btn, 0, 0, 1, 1)   # Column 0, Row 0
        area.attach(self.time_btn, 0, 1, 1, 1)   # Column 0, Row 1

        # Filament material buttons
        # Create material buttons with temperature-specific actions
        for i, material in enumerate(VividConfig.MATERIALS):
            btn = VerButton(
                label=VLabel(content=material.name, size=font_size, bold=True))
            btn.get_style_context().add_class(material.css_class)
            apply_button_css(btn, material.css_class, 
                f"border-bottom-color: {material.display_color};")

            # Corrected event binding: capture current temp in closure
            # The first '_' is btn itself
            btn.connect(
                "clicked", 
                lambda _, t=material.temperature, d=material.heat_duration, c=material.display_color: self.heat_with_temp(t, d, c)
            )

            # Position buttons in the grid:
            # - Columns 1 & 2 (since i%2: 0 => 1, 1 => 2)
            # - Rows 0 and 1 (i//2: 0-1 => row 0, 2-3 => row 1)
            area.attach(btn, i % 2 + 1, i // 2, 1, 1)

        # Stop heating button
        self.stop_heat_btn = VerButton(
            image=VImage(
                file_name="vivid_stop_idle.svg",
                width=width * 2,
                height=height * 2
            ),
            label=VLabel(content="Stop", size=font_size)
        )
        self.stop_heat_btn.get_style_context().add_class("vvd-stop-heat-btn")
        self.stop_heat_btn.connect("clicked", self.mms_heater_stop)
        area.attach(self.stop_heat_btn, 3, 0, 1, 2)

        return area

    def create_mms_area(self):
        """Create MMS control area"""
        area = Gtk.Grid(
            row_homogeneous=True,
            column_homogeneous=True,
            hexpand=False,
            vexpand=False
        )
        area.get_style_context().add_class("vvd-mms-area")

        # MMS button dimensions
        # width = height = self._gtk.img_scale * self.bts * 4.8
        screen_width = get_screen_width(self)
        width = height = screen_width / 8
        font_size = screen_width / 40

        # Main MMS button
        mms_btn = VerButton(
            image=VImage(file_name="vivid_mms.svg", width=width, height=height),
            label=VLabel(content="MMS", size=font_size, bold=True)
        )
        mms_btn.get_style_context().add_class("vvd-mms-main-btn")
        mms_btn.connect("clicked", lambda w: self.show_mms_window())

        area.attach(mms_btn, 0, 0, 1, 1)
        return area

    # ---- Slot Functions ----
    def create_slot_button(self, slot_num, color, material):
        """Create a slot button with animation and labeling"""
        screen_width = get_screen_width(self)
        diameter = screen_width / 12
        border_width = diameter / 3.5
        font_size_num = diameter / 2
        font_size_material = border_width

        # Create animated circle button
        circle = CircleButtonAnime(
            diameter=diameter,
            border_width=border_width,
            border_color=color,
            label=VLabel(content=f"{slot_num}", size=font_size_num, bold=True)
        )

        # Create bottom label
        bottom_label = VLabel(content=material, size=font_size_material, bold=True)

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
            lambda p: self.show_slot_panel(slot_num, color, material)
        )

        # Format dct for update
        self.slot_buttons[slot_num] = {
            "button" : button,
            "circle" : circle,
            "bottom_label" : bottom_label,
        }
        return button

    def apply_slot_button_color(self, button, color):
        base_class = "vvd-slot-button"
        # Apply dynamic CSS
        apply_button_css(
            button, base_class, f"border-bottom-color: {color};")
        apply_button_css(
            button, f"{base_class}:active", f"background-color: {color};")

    def show_slot_panel(self, slot_num, color, material):
        self._screen.show_panel(
            "vivid/panels/slot",
            title = f"ViViD SLOT {slot_num}",
            # Use an unique_panel_name here,
            # panel_name is the cache key of KlipperScreen's show_panel()
            panel_name = f"vivid/slot/{slot_num}",
            slot_num = slot_num,
            color = color,
            material = material,
            parent_btn_hook = self.refresh_slot_button,
            cfg_manager = self.cfg_manager,
        )

    def refresh_slot_button(self, slot_num, color=None, label=None):
        slot_button = self.slot_buttons[slot_num]

        if color:
            slot_button["circle"].set_border_color(color)
            self.apply_slot_button_color(slot_button["button"], color)

        if label:
            slot_button["bottom_label"].set_content(label)

    # ---- Heat Functions ----
    def heat_with_temp(self, temperature, heat_duration, color):
        """
        Handle temperature setting button click
        temp_btn: The BaseImageButton that show the temperature
        time_btn: The BaseImageButton that show the remain time
        Args:
            temperature: Target temperature for the heater
        """
        # Send command to printer
        self.start_heating(temperature)
        # Update UI
        self.temp_btn.set_target(temperature, color)
        self.time_btn.start_countdown(heat_duration, teardown_func=self.stop_heating)
        if self.stop_heat_btn:
            self.stop_heat_btn.image.update_image_file("vivid_stop_busy.svg")

    def start_heating(self, temperature):
        heater = "vivid_heater"
        script = f"SET_HEATER_TEMPERATURE HEATER={heater} TARGET={temperature}"
        self.send_klippy_script(script)

    def stop_heating(self):
        heater = "vivid_heater"
        script = f"SET_HEATER_TEMPERATURE HEATER={heater} TARGET=0"
        self.send_klippy_script(script)

    def set_current_temp(self, temp):
        if temp is not None:
            self.temp_str = f"{temp:.1f}"

    def get_current_temp(self):
        # heater_device = "heater_generic vivid_heater"
        # # May return empty dict {}
        # temp = self._printer.get_stat(heater_device, "temperature")
        # return f"{temp:.1f}" if temp else "--"
        return self.temp_str

    # ---- Delivery Functions ----
    # def show_delivery_window(self, forward=True):
    #     """Show filament delivery destination window"""
    #     action = "Extrude" if forward else "Retract"
    #     title = f"{action} To"

    #     # Create title label
    #     screen_width = get_screen_width(self)
    #     label_title = VLabel(content=title, size=screen_width/30, bold=True)

    #     # Create destination buttons
    #     button_extruder = HorButton(
    #         label=VLabel(content="Extruder", size=screen_width/40)
    #     )
    #     button_extruder.get_style_context().add_class("vvd-delivery-btn")
    #     button_extruder.connect("clicked", 
    #         lambda w: self.delivery_action(forward, "EXTRUDER")
    #     )

    #     button_buffer = HorButton(
    #         label=VLabel(content="Buffer", size=screen_width/40)
    #     )
    #     button_buffer.get_style_context().add_class("vvd-delivery-btn")
    #     button_buffer.connect("clicked", 
    #         lambda w: self.delivery_action(forward, "BUFFER")
    #     )

    #     button_outside = HorButton(
    #         label=VLabel(content="Outside", size=screen_width/40)
    #     )
    #     button_outside.get_style_context().add_class("vvd-delivery-btn")
    #     button_outside.connect("clicked", 
    #         lambda w: self.delivery_action(forward, "OUTSIDE")
    #     )

    #     # Create grid layout
    #     grid = Gtk.Grid(
    #         row_spacing=10,
    #         halign=Gtk.Align.CENTER
    #     )

    #     # Layout with centered title
    #     title_container = Gtk.Box(halign=Gtk.Align.CENTER, hexpand=True)
    #     title_container.add(label_title)

    #     grid.attach(title_container, 0, 0, 1, 1)
    #     grid.attach(button_extruder, 0, 1, 1, 1)
    #     grid.attach(button_buffer, 0, 2, 1, 1)
    #     grid.attach(button_outside, 0, 3, 1, 1)

    #     # Create popup window
    #     create_popup_window(title, grid, "vvd-delivery-window")

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
            # ("dripload", "vivid_switch", "DRIPLOAD  \nDisable", self.mms_dripload_clicked, None),
            ("mms9", "vivid_walk", "MMS9\nMMS_SLOTS_WALK", self.mms_action, "MMS_SLOTS_WALK"),
            ("mms8", "vivid_loop", "MMS8\nMMS_SLOTS_LOOP", self.mms_action, "MMS_SLOTS_LOOP"),
            # ("mms999", "vivid_stop", "MMS999\nMMS_STOP      ", self.mms_action, "MMS_STOP"),
            # ("heater", "vivid_stop", "Heater    \nStop", self.mms_heater_stop, None),
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
        )
        grid.attach(title_container, 0, 0, 2, 1)
        # Position buttons
        grid.attach(all_buttons["mms00"], 0, 1, 1, 1)
        grid.attach(all_buttons["mms0"], 0, 2, 1, 1)
        # grid.attach(all_buttons["dripload"], 0, 3, 1, 1)
        # grid.attach(all_buttons["heater"], 0, 4, 1, 1)
        grid.attach(all_buttons["mms9"], 1, 1, 1, 1)
        grid.attach(all_buttons["mms8"], 1, 2, 1, 1)
        # grid.attach(all_buttons["mms999"], 1, 3, 1, 1)

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

        # Create button
        button = HorButton(
            image=VImage(file_name=f"{icon_name}.svg", width=width, height=height),
            label=VLabel(content=label_text, size=width/3)
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
        self.send_klippy_script(script)

    def mms_action_c(self, script):
        self.send_klippy_script(script)
        self._screen.show_panel("console")
        if self.close_window_func:
            self.close_window_func()
            self.close_window_func = None

    def mms_dripload_clicked(self, widget):
        return
        # self.send_klippy_script("MMS_DRIPLOAD SWITCH=0")

    def mms_heater_stop(self, widget):
        self.stop_heating()
        self.temp_btn.reset_target()
        self.time_btn.stop_countdown()
        if self.stop_heat_btn:
            self.stop_heat_btn.image.update_image_file("vivid_stop_idle.svg")

    # ---- MMS LED ----
    def mms_update_slot_led(self, slot_num, color):
        """Update hardware LED color (strip '#' prefix)"""
        # self.color = "#FFFFFF" --> COLOR=FFFFFF
        color_hex = color[1:] if color.startswith("#") else color
        script = f"MMS_LED_SET_COLOR SLOT={slot_num} COLOR={color_hex}"
        self.send_klippy_script(script)

    # ---- Panel life ----
    def activate(self):
        selecting_slot_num = []

        mms_selectors = self.mms_controller.get_mms_selectors()
        for index,status_dct in mms_selectors.items():
            # Focusing slot is not None
            slot_num = status_dct.get("focus_slot")
            if slot_num:
                selecting_slot_num.append(int(slot_num))

        mms_slots = self.mms_controller.get_mms_slots()
        for slot_num,status_dct in mms_slots.items():
            # Pin selector is triggered
            if status_dct.get("selector"):
                selecting_slot_num.append(int(slot_num))

        # Play select animation
        for slot_num in set(selecting_slot_num):
            self.select_slot_button(slot_num)

    def deactivate(self):
        for slot_num in self.slot_buttons:
            self.pause_slot_button(slot_num)

    def process_update(self, action, data):
        # logging.info(f"#### action:{action}, data:{data}")

        if self._screen.printer.state in ("error", "shutdown"):
            return

        if action == self.notify_action.status:
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
        btn = self.slot_buttons.get(slot_num, None)
        if btn:
            btn.get("circle").start_animation(reverse)
        # self.slot_buttons[slot_num]["circle"].start_animation(reverse)

    def pause_slot_button(self, slot_num):
        btn = self.slot_buttons.get(slot_num, None)
        if btn:
            btn.get("circle").stop_animation()
        # self.slot_buttons[slot_num]["circle"].stop_animation()

    def kick_slot_button(self, slot_num):
        btn = self.slot_buttons.get(slot_num, None)
        if btn:
            btn.get("circle").kick_animation()
        # self.slot_buttons[slot_num]["circle"].kick_animation()


# ==== Utils ====
def find_period(n):
    period = 4
    index = (n // period) * period
    return [index, index+1, index+2, index+3]
