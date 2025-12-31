
import logging
import os
import pathlib
# import traceback

from dataclasses import dataclass
from importlib import import_module

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

# from ks_includes.KlippyGtk import find_widget
from vivid.components.button import ImmutableImageButton
from vivid.components.image import VividImage as VImage
from vivid.components.utils import convert_css_to_gtk3


@dataclass(frozen=True)
class VividPanelConfig:
    version: str = "0.2.0010"
    welcome: str = "*"*10 + f" KlipperScreen for VVD/MMS Ver {version} Start up! " + "*"*10
    panel_prefix: str = "vivid/panels/"


def _advanced_load_panels(base_panel):
    """
    Advanced _load_panels()

    KlipperScreen/ks_includes/screen_panel.py 
    :: base_panel.menu_item_clicked()
    |-> KlipperScreen/ks_includes/screen_panel.py 
        :: base_panel.screen.show_panel(item['panel'], **panel_args)
            |-> KlipperScreen/screen.py 
                :: show_panel()
                :: _load_panel()

    Vivid panels connect should format like :
        "vivid/panels/main"
        vivid_button.connect(
            "clicked", 
            base_panel.menu_item_clicked, 
            {"panel": "vivid/panels/main",}
        )

    or just:
        self._screen.show_panel("vivid/panels/main")
    """
    org_load_panel = base_panel._screen._load_panel

    def _load_panel(panel):
        if panel.startswith(VividPanelConfig.panel_prefix):
            logging.debug(f"Loading panel: {panel}")

            panel_filename = panel.split(os.path.sep)[-1]
            vivid_panel_path = os.path.join(os.path.dirname(__file__), "panels", f"{panel_filename}.py")

            if not os.path.exists(vivid_panel_path):
                logging.error(f"Panel {panel} does not exist")
                raise FileNotFoundError(os.strerror(2), "\n" + vivid_panel_path)
            return import_module(f"vivid.panels.{panel_filename}")

        return org_load_panel(panel)
    
    # Replace origin load_panel as the vivid version
    base_panel._screen._load_panel = _load_panel
    return base_panel


def _add_vivid_style(base_panel):
    """
    Add vivid styles.css to base_css
    Base css will not overwrite by change theme
    """
    # css_data = ""
    # style_css_path = os.path.join(os.path.dirname(__file__), "styles", "style.css")
    # if os.path.exists(style_css_path):
    #     css_data = pathlib.Path(style_css_path).read_text()
    # # Add Vivid style css data to base css
    # base_panel._screen.base_css += css_data

    styles_dir = pathlib.Path(__file__).parent / "styles"

    try:
        for css_file in styles_dir.glob("*.css"):
            try:
                if css_file.is_file():
                    css_data = css_file.read_text(encoding="utf-8")
                    # base_panel._screen.base_css += css_data

                    css_converted = convert_css_to_gtk3(css_data)
                    base_panel._screen.base_css += css_converted

            except Exception as e:
                print(f"Error reading {css_file}: {str(e)}")
                # traceback.print_exc()
    except PermissionError:
        print(f"Permission denied accessing directory: {styles_dir}")

    # change_theme() with no params should like refreshing the base_panel
    base_panel._screen.change_theme()
    return base_panel


def _setup_action_bar(base_panel):
    """
    Setup Vivid main button in Action bar
    """
    vivid_button = ImmutableImageButton(
        hexpand=True, 
        vexpand=True, 
        can_focus=False, 
        image_position=Gtk.PositionType.TOP, 
        always_show_image=True)

    scale = base_panel._gtk.img_scale * base_panel.abscale * 2.0
    width = height = scale
    # width = height = 60
    vimage = VImage(file_name="vivid_logo.svg", width=width, height=height)

    vivid_button.set_image(vimage)
    vivid_button.set_name("vivid_main")

    # spinner = Gtk.Spinner(width_request=width, height_request=height, no_show_all=True)
    # spinner.hide()
    # box = find_widget(vivid_button, Gtk.Box)
    # if box:
    #     box.add(spinner)

    vivid_button.connect("clicked", base_panel._gtk.screen.screensaver.reset_timeout)
    vivid_button.connect("clicked", _click_vivid, base_panel)
    # Any action bar button should close the keyboard
    vivid_button.connect("clicked", base_panel._screen.remove_keyboard)

    base_panel.action_bar.add(vivid_button)
    base_panel.action_bar.reorder_child(vivid_button, 2)


def _click_vivid(widget, base_panel):
    # "panel": "vivid/panels/main"
    main_panel = VividPanelConfig.panel_prefix + "main"
    base_panel._screen.show_panel(main_panel)


"""
The base_panel is the "homepage" of KlipperScreen
Call this function in screen.py(which have the main() of KlipperScreen)
"""
def install_vivid(base_panel):
    """
    Install Vivid to main screen
    """
    base_panel = _advanced_load_panels(base_panel)
    base_panel = _add_vivid_style(base_panel)
    _setup_action_bar(base_panel)
    logging.info(VividPanelConfig.welcome)
