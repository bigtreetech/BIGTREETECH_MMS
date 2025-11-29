import colorsys
import itertools
import logging
import math

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk


def extract_root_vars(css_content):
    """
    Extract CSS variable definitions from :root section
    Returns (variables dictionary, cleaned CSS content)
    """
    vars = {}
    root_start = css_content.find(":root {")
    if root_start == -1:
        return vars, css_content

    # Locate the end of :root block by counting braces
    brace_count = 1
    root_end = root_start + len(":root {")
    while root_end < len(css_content) and brace_count > 0:
        if css_content[root_end] == "{":
            brace_count += 1
        elif css_content[root_end] == "}":
            brace_count -= 1
        root_end += 1

    # Extract variable definitions
    root_block = css_content[root_start + len(":root {") : root_end - 1]
    for line in root_block.split(";"):
        line = line.strip()
        if line.startswith("--"):
            name, value = line.split(":", 1)
            vars[name.strip()] = value.strip()

    # Remove original :root block
    cleaned_css = css_content[:root_start] + css_content[root_end:]
    return vars, cleaned_css


def replace_var_usage(css_content, vars):
    """
    Replace all var(--variable) usages with actual values
    """
    result = []
    i = 0
    content_len = len(css_content)

    while i < content_len:
        # Find next 'var(' occurrence
        var_start = css_content.find("var(", i)
        if var_start == -1:
            result.append(css_content[i:])
            break

        result.append(css_content[i:var_start])

        # Parse variable content
        paren_depth = 1
        j = var_start + 4  # Skip "var("

        while j < content_len and paren_depth > 0:
            if css_content[j] == "(":
                paren_depth += 1
            elif css_content[j] == ")":
                paren_depth -= 1
            j += 1

        if paren_depth == 0:
            var_expr = css_content[var_start+4 : j-1].strip()
            var_name = var_expr.split(",", 1)[0].strip()
            replacement = vars.get(var_name, f"var({var_expr})")
            result.append(replacement)
            i = j
        else:
            result.append(css_content[var_start:])
            break

    return "".join(result)


def convert_css_to_gtk3(css_content):
    """Main conversion function"""
    vars, cleaned_css = extract_root_vars(css_content)
    css_data = replace_var_usage(cleaned_css, vars)
    # logging.info(f"{css_data}")
    return css_data


# ---- UI Utility Methods ----
def create_section_container(style_class):
    """Create a container section with uniform styling"""
    grid = Gtk.Grid(
        row_homogeneous=False,
        column_homogeneous=False,
        hexpand=True,
        vexpand=True
    )
    grid.get_style_context().add_class(style_class)
    return grid


def create_popup_window(title, content, style_class):
    """
    Create a consistent popup window with transparent overlay
    Args:
        title: Window title
        content: Main content widget
        style_class: CSS class for window styling
    """
    # Create overlay window (mask/backdrop)
    overlay = Gtk.Window(type=Gtk.WindowType.POPUP)
    overlay.set_decorated(False)
    overlay.set_app_paintable(True)
    overlay.set_default_size(
        overlay.get_screen().get_width(),
        overlay.get_screen().get_height()
    )
    overlay.move(0, 0)
    overlay.get_style_context().add_class("vvd-window-overlay")

    # Create main window -- the actual dialog window
    main_window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    main_window.set_title(title)
    main_window.set_position(Gtk.WindowPosition.CENTER)
    main_window.set_default_size(
        main_window.get_screen().get_width() / 1.5,
        main_window.get_screen().get_height() / 1.5
    )
    # Keep window decorations
    main_window.set_decorated(True)
    main_window.get_style_context().add_class(style_class)
    main_window.set_modal(True)
    # Set window hierarchy relationship
    main_window.set_transient_for(overlay)

    # Create close function
    def close_window():
        main_window.destroy()
        overlay.destroy()

    # Compose interface
    main_window.add(content)

    # Add click handler
    main_window.connect("button-press-event", lambda w, e: close_window())

    # Show windows
    # Show overlay first, then dialog
    # Ensure dialog is on top
    overlay.show_all()
    main_window.show_all()
    main_window.present()

    return close_window


def apply_button_css(button, style_class, custom_css=""):
    """
    Apply CSS styling to a button
    Args:
        button: Target button widget
        style_class: Base CSS class name
        custom_css: Custom CSS properties to add
    """
    context = button.get_style_context()
    context.add_class(style_class)

    # Add dynamic CSS if provided
    if custom_css:
        provider = Gtk.CssProvider()
        provider.load_from_data(f".{style_class} {{{custom_css}}}".encode())
        context.add_provider(
            provider, 
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


def get_screen_width(panel):
    # Get screen dimensions for proportional sizing
    # screen_width = Gdk.Screen.get_default().get_width()
    return panel._screen.get_screen().get_width()


# ---- Time calculate Methods ----
def convert_seconds_to_hms(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds_remaining = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds_remaining:02d}"


# ---- Color Methods ----
def hex_to_rgb(hex_color: str) -> tuple:
    """Convert a hex color string to an RGB tuple with values in the range [0, 1]"""
    hex_color = hex_color.lstrip('#')
    r, g, b = tuple(int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    return r, g, b


def rgb_to_hex(r: float, g: float, b: float) -> str:
    """Convert RGB values in the range [0, 1] to a hex color string"""
    return "#{:02X}{:02X}{:02X}".format(
        int(max(0, min(1, r)) * 255),
        int(max(0, min(1, g)) * 255),
        int(max(0, min(1, b)) * 255)
    )


def lighten_color(hex_color: str, amount: float = 0.3) -> str:
    """
    Lighten a hex color by increasing its lightness in the HLS color space.
    Parameters:
        hex_color (str): Original hex color (e.g., "#2CDA29")
        amount (float): Amount to increase lightness by (0 to 1)
    Returns:
        str: Lightened hex color
    """
    r, g, b = hex_to_rgb(hex_color)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    # Increase lightness but keep it within bounds
    l = min(1.0, l + amount)  
    r_new, g_new, b_new = colorsys.hls_to_rgb(h, l, s)
    return rgb_to_hex(r_new, g_new, b_new)


def generate_color_wave(base_color: str, steps: int = 30, amount: float = 0.3):
    """Generate border colors with sinusoidal lightness oscillation"""
    for i in itertools.cycle(range(steps)):
        # Range 0~1
        phase = (1 + math.sin(2 * math.pi * i / steps)) / 2
        yield lighten_color(base_color, phase * amount)


def calculate_contrast_color(bg_color: Gdk.RGBA):
    """
    https://www.w3.org/TR/WCAG20/#relativeluminancedef
    """
    r = bg_color.red
    g = bg_color.green
    b = bg_color.blue
    
    # sRGB
    def adjust_channel(c):
        if c <= 0.03928:
            return c / 12.92
        else:
            return math.pow((c + 0.055) / 1.055, 2.4)

    r = adjust_channel(r)
    g = adjust_channel(g)
    b = adjust_channel(b)
    # Range 0~1
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b

    # result = Gdk.RGBA()
    # result.alpha = 1.0
    # if luminance > 0.5:
    #     # Light background -> black result
    #     result.red = 0.0
    #     result.green = 0.0
    #     result.blue = 0.0
    # else:
    #     # Dark background -> white result
    #     result.red = 1.0
    #     result.green = 1.0
    #     result.blue = 1.0

    result = "#000000" if luminance > 0.5 else "#FFFFFF"
    return result

