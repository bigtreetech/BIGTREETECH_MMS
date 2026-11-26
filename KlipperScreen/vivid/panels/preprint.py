import logging
import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ks_includes.screen_panel import ScreenPanel
from vivid.components.box import CircleBox, FixedRectangleBox, FixedSquareBox
from vivid.components.button import CircleButton
from vivid.components.label import VividLabel as VLabel
from vivid.components.utils import apply_button_css, create_section_container, get_screen_width
from vivid.controllers.mms import MMSController
from vivid.controllers.mms_slot import MMSSlotController


class Panel(ScreenPanel):

    def __init__(self, screen, title=None, gcode_file=None, parent_hook=None):
        super().__init__(screen, title or _("ViViD PrePrint"))
        self.gcode_file = gcode_file
        gcode_dir = self._screen.files.gcodes_path if gcode_file else None
        self.gcode_fullpath = (
            os.path.join(gcode_dir, gcode_file) if gcode_dir and gcode_file else None
        )
        self.parent_hook = parent_hook

        self.mms_controller = MMSController(self._screen)
        self.mms_slot_controller = MMSSlotController(self._screen)

        self.mms_controller.register_slot_color_callback(
            self._on_slot_color_changed)
        self.mms_controller.register_slot_material_callback(
            self._on_slot_material_changed)

        self.mapping_btn_lst = []
        self.main_grid = None
        self.slot_ids = set()
        # [(0,0), ... (swap_num,slot_num)]
        self.mapping_pairs = []

        # self.gf_uuid = None
        self.gf_filename = None
        self.gf_meta = []
        if self.gcode_file is not None:
            self.parse_gcodefile_meta()

    def parse_gcodefile_meta(self):
        fileinfo = self._screen.files.get_file_info(self.gcode_file) or {}

        # self.gf_uuid = fileinfo.get("uuid")
        self.gf_filename = fileinfo.get("filename")

        filament_type_str = fileinfo.get("filament_type") or ""
        filament_types = [f.strip() for f in filament_type_str.split(';') if f and f.strip()]

        filament_colors = fileinfo.get("filament_colors", None)
        if not filament_colors and self.gcode_fullpath:
            filament_colors = parse_filament_colors(self.gcode_fullpath) or []

        filament_colors = filament_colors or []
        max_items = min(len(filament_types), len(filament_colors))

        if max_items > 0:
            self.gf_meta = [
                (filament_types[i],
                filament_colors[i] if i < len(filament_colors) else "")
                for i in range(max_items)
            ]

        logging.info(
            # f"fileinfo: {fileinfo};"
            # f"parsed gcode file UUID: {self.gf_uuid};"
            f"parsed gcode file name: {self.gf_filename};"
            f"metadata: {self.gf_meta}"
        )

    # ---- Panel life ----
    def activate(self):
        # Rebuild UI
        self.build_ui()

    def deactivate(self):
        # Release old button mapping
        for btn in self.mapping_btn_lst:
            btn.destroy()
        self.mapping_btn_lst = []
        self.mapping_pairs = []

        # Remove UI
        self.remove_ui()

    def process_update(self, action, data):
        if action == "notify_status_update":
            if "mms" in data and self.main_grid is not None:
                slot_ids = {num for num, _ in self.mms_controller.get_slots_filament_color()}
                if slot_ids != self.slot_ids:
                    self.remove_ui()
                    self.build_ui()
                    self.main_grid.show_all()
            self.mms_controller.handle_notify_status_update(data)

    def _on_slot_color_changed(self, slot_num, color):
        """WebSocket push: update mapping button colour for the affected slot."""
        if not self.mapping_btn_lst:
            return  # UI not built yet
        for i, (swap_num, s_num) in enumerate(self.mapping_pairs):
            if s_num == slot_num and i < len(self.mapping_btn_lst):
                new_color = color or "#FFFFFF"
                new_btn = self.create_mapping_button(swap_num, slot_num, new_color)
                self.replace_slot_button(swap_num, new_btn)
                self.mapping_pairs[i] = (swap_num, slot_num)

    def _on_slot_material_changed(self, slot_num, material):
        """WebSocket push: preprint panel does not display material per slot."""
        pass

    def build_ui(self):
        """Build the main UI structure"""
        # Top area: SLOT Color
        mapping_scroll = self.create_filament_slot_mapping()
        top_area = create_section_container("vvd-preprint-area-top")
        top_area.attach(mapping_scroll, 0, 0, 1, 1)

        # Bottom area: Print control
        self.bottom_area = self.create_bottom_area()
        self.bottom_area.attach(self.create_print_control(), 0, 0, 1, 1)

        # Main grid layout
        self.main_grid = Gtk.Grid(
            row_homogeneous=False,
            column_homogeneous=False,
            hexpand=True,
            vexpand=True
        )
        self.main_grid.attach(top_area, 0, 0, 1, 1)
        self.main_grid.attach(self.bottom_area, 0, 1, 1, 1)
        self.content.add(self.main_grid)

    def remove_ui(self):
        if self.main_grid is not None:
            self.main_grid.destroy()
            self.main_grid = None

    def create_bottom_area(self):
        bottom_area = create_section_container("vvd-preprint-area-bottom")
        bottom_area.set_hexpand(False)
        bottom_area.set_vexpand(False)
        return bottom_area

    # ---- SLOT Reflection ----
    def create_filament_slot_mapping(self):
        area = create_section_container("vvd-preprint-area-slot")

        colors = dict(self.mms_controller.get_slots_filament_color())
        previous = dict(self.mapping_pairs)
        self.slot_ids = set(colors)
        self.mapping_btn_lst.clear()
        self.mapping_pairs.clear()
        for swap_num in colors:
            slot_num = previous.get(swap_num, swap_num)
            if slot_num not in colors:
                slot_num = swap_num

            button = self.create_mapping_button(swap_num, slot_num, colors[slot_num])
            # Init mapping
            self.mapping_btn_lst.append(button)
            self.mapping_pairs.append((swap_num, slot_num))

            area.attach(button, swap_num % 2 + 1, swap_num // 2, 1, 1)

        self.mapping_area = area

        # scroll = self._gtk.ScrolledWindow()
        scroll = Gtk.ScrolledWindow()
        # Horizontal disable, vertical auto
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(area)
        scroll.get_style_context().add_class("vvd-preprint-area-slot")
        return scroll

    def create_mapping_button(self, swap_num, slot_num, color):
        """Create a slot button with animation and labeling"""
        screen_width = get_screen_width(self)
        width = screen_width / 2.7
        height = screen_width / 8.5

        # Create filament metadata box
        box_f = self.pack_box_filament(swap_num)
        box_circle = self.pack_circle_slot(slot_num, color)

        # Compose components
        box_compact = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.FILL,
            hexpand=True,
            vexpand=True
        )
        box_compact.add(box_f)
        box_compact.add(box_circle)

        box_fixed = FixedRectangleBox(width, height)
        box_fixed.set_content(box_compact)

        # Final button container
        button = Gtk.Button()
        button.add(box_fixed)
        self.apply_slot_button_color(button, color)
        button.connect("clicked", lambda _, sw=swap_num: self.on_mapping_clicked(sw))
        return button

    def pack_box_filament(self, swap_num):
        screen_width = get_screen_width(self)
        font_size = screen_width / 45
        square_size = screen_width / 10

        if swap_num < len(self.gf_meta):
            f_type, f_color = self.gf_meta[swap_num]
            label_content = f"T{swap_num}"
            bg_color = f_color
            base_class = "vvd-preprint-filament-box"
            custom_css = f"background-color: {f_color};"
        else:
            label_content = "N/A"
            bg_color = "#FFFFFF"
            base_class = "vvd-preprint-filament-box-na"
            custom_css = ""

        box_l = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                        hexpand=True, vexpand=True)
        lable_num = VLabel(content=label_content, size=font_size, bold=True)
        lable_num.adjust_color(background_color=bg_color)
        box_l.add(lable_num)

        box_f = FixedSquareBox(size=square_size)
        box_f.set_content(box_l)
        apply_button_css(box_f, base_class, custom_css)
        return box_f

    def pack_circle_slot(self, slot_num, color, clicked_callback=None):
        screen_width = get_screen_width(self)
        diameter = screen_width / 20
        border_width = screen_width / 55
        font_size = screen_width / 45
        square_size = screen_width / 10

        label = VLabel(content=f"{slot_num}", size=font_size, bold=True)

        if clicked_callback:
            circle = CircleButton(
                diameter=diameter,
                border_width=border_width,
                # border_color=color or "#FFFFFF",
                border_color=color,
                label=label
            )
            circle.connect("clicked", clicked_callback)
        else:
            circle = CircleBox(diameter, border_width, color)
            circle.add_content(label)

        box_circle = FixedSquareBox(size=square_size)
        box_circle.set_content(circle)
        return box_circle

    def apply_slot_button_color(self, button, color):
        base_class = "vvd-preprint-btn-slot"
        apply_button_css(button, base_class)

    def on_mapping_clicked(self, swap_num):
        self.main_grid.remove(self.bottom_area)

        box_f = self.pack_box_filament(swap_num)

        box_compact = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True
        )
        box_compact.add(box_f)

        for slot_num, color in self.mms_controller.get_slots_filament_color():
            button = self.pack_circle_slot(
                slot_num, color, lambda _, sl=slot_num: self.mapping_update(swap_num, sl)
            )
            box_compact.add(button)

        area = create_section_container("vvd-preprint-area-ctrl")
        area.attach(box_compact, 0, 0, 1, 1)

        # scroll = self._gtk.ScrolledWindow()
        scroll = Gtk.ScrolledWindow()
        # Horizontal auto, vertical disable
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.add(area)
        scroll.get_style_context().add_class("vvd-preprint-area-ctrl")

        self.bottom_area = self.create_bottom_area()
        self.bottom_area.attach(scroll, 0, 0, 1, 1)
        self.main_grid.attach(self.bottom_area, 0, 1, 1, 1)
        self.main_grid.show_all()

    def mapping_update(self, swap_num, slot_num):
        logging.info(f"mapping click swap_num:{swap_num}, slot_num:{slot_num}")
        index = next(i for i, pair in enumerate(self.mapping_pairs) if pair[0] == swap_num)
        self.mapping_pairs[index] = (swap_num, slot_num)

        color = self.mms_slot_controller.get_filament_color(slot_num)
        new_btn = self.create_mapping_button(swap_num, slot_num, color)
        self.replace_slot_button(swap_num, new_btn)

        self.main_grid.remove(self.bottom_area)
        self.bottom_area = self.create_bottom_area()
        self.bottom_area.attach(self.create_print_control(), 0, 0, 1, 1)
        self.main_grid.attach(self.bottom_area, 0, 1, 1, 1)
        self.main_grid.show_all()

    def replace_slot_button(self, swap_num, new_button):
        index = next((i for i, pair in enumerate(self.mapping_pairs) if pair[0] == swap_num), None)
        if index is not None:
            current_button = self.mapping_btn_lst[index]

            self.mapping_area.remove(current_button)
            self.mapping_area.attach(
                new_button, swap_num % 2 + 1, swap_num // 2, 1, 1)
            new_button.show()
            self.mapping_area.show_all()

            self.mapping_btn_lst[index] = new_button
            if current_button:
                current_button.destroy()

    # ---- Print Control ----
    def create_fixed_button(self, label, width, height, font_size, base_class):
        box_fixed = FixedRectangleBox(width, height)
        box_fixed.set_content(VLabel(content=label, size=font_size))
        button = Gtk.Button()
        button.add(box_fixed)
        apply_button_css(button, base_class)
        return button

    def create_print_control(self):
        screen_width = get_screen_width(self)
        font_size = screen_width / 40
        width = screen_width / 2.7
        height = screen_width / 14

        base_class = "vvd-preprint-btn-ctrl"
        button_cancel = self.create_fixed_button(
            "Cancel", width, height, font_size, base_class)
        button_confirm = self.create_fixed_button(
            "Confirm", width, height, font_size, base_class)

        button_cancel.connect("clicked", self.on_cancel_clicked)
        button_confirm.connect("clicked", self.on_confirm_clicked)

        box_compact = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.FILL,
        )
        box_compact.add(button_cancel)
        box_compact.add(button_confirm)

        area = create_section_container("vvd-preprint-area-ctrl")
        area.attach(box_compact, 0, 0, 1, 1)
        return area

    def _back_to_parent(self):
        self.mapping_pairs = []
        self._screen._menu_go_back()
        self.parent_hook(widget=None, filename=self.gcode_file)

    def on_cancel_clicked(self, widget):
        self._back_to_parent()

    def on_confirm_clicked(self, widget):
        if self.mapping_pairs:
            logging.info(f"current mapping:{self.mapping_pairs}")
            self.mms_controller.update_swap_mapping(
                self.mapping_pairs, self.gf_filename)
        self._back_to_parent()


def parse_filament_colors(
        gcode_file_path,
        max_lines=500,
        target_prefix="; filament_colour ="
    ):
    """
    Extract filament colors from G-code file by searching backward from the end.
    Args:
        gcode_file_path: Full path to the G-code file
        max_lines: Maximum lines to search backward (default: 500)
        target_prefix: Target comment prefix to identify color line (default: "; filament_colour =")
    Returns:
        List of color values (hex codes) if found, else None
    """
    # Validate file existence
    if not os.path.exists(gcode_file_path):
        logging.error(f"File not found: {gcode_file_path}")
        return None

    try:
        # Open file in binary mode for efficient backward reading
        with open(gcode_file_path, 'rb') as file:
            # Read the last chunk of the file (considering max_lines)
            file.seek(0, os.SEEK_END)
            file_size = file.tell()

            # Calculate approximate bytes to read (80 chars per line)
            chunk_size = min(file_size, max_lines * 80)
            file.seek(max(0, file_size - chunk_size))

            # Read and decode only the tail content
            tail_content = file.read().decode('utf-8', errors='ignore')

            # Process lines in reverse order
            lines = tail_content.strip().splitlines()
            for line in reversed(lines):
                if len(line) > 0:  # Skip empty lines
                    # Check for target prefix
                    if line.startswith(target_prefix):
                        # Extract and split color values
                        colors_str = line[len(target_prefix):].strip()
                        return [c.strip() for c in colors_str.split(';') if c.strip()]

                # Optimize: stop when max_lines is reached
                if (max_lines := max_lines - 1) <= 0:
                    break

        # Return None if color string not found
        return None

    except Exception as e:
        logging.error(f"Error processing file {gcode_file_path}: {str(e)}")
        return None
