from gi.repository import Gtk, Gdk


class FixedSizeBox(Gtk.Box):
    """
    Base class for fixed-size containers with centered content.
    Provides common functionality for fixed-size boxes.
    """
    def __init__(self, content=None, *args, **kwargs):
        """
        Initialize the fixed-size box.
        Args:
            content: Widget to place inside the box (default: empty label)
            *args: Additional positional arguments for Gtk.Box
            **kwargs: Additional keyword arguments for Gtk.Box
        """
        super().__init__(*args, **kwargs)

        # Create centered container for content
        self.center_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True
        )

        # Add content or default label
        self.inner_content = content or Gtk.Label(label="")
        self.center_box.add(self.inner_content)
        self.add(self.center_box)

        # Center the entire box in its parent
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        # Prevent expansion in parent container
        self.set_hexpand(False)
        self.set_vexpand(False)

    def set_content(self, widget):
        """
        Replace the inner content of the box.
        Args:
            widget: New widget to place in the center
        """
        # Remove existing children
        for child in self.center_box.get_children():
            self.center_box.remove(child)

        # Add new content
        self.inner_content = widget
        self.center_box.add(widget)
        widget.show()

    def add_style_class(self, style_class):
        """
        Add a CSS style class to the box.
        Args:
            style_class: Name of the CSS class to add
        """
        self.get_style_context().add_class(style_class)

    def remove_style_class(self, style_class):
        """
        Remove a CSS style class from the box.
        Args:
            style_class: Name of the CSS class to remove
        """
        self.get_style_context().remove_class(style_class)


class FixedSquareBox(FixedSizeBox):
    """
    A square container that maintains a fixed size and centers its content.
    """
    def __init__(self, size=100, content=None, *args, **kwargs):
        """
        Initialize a fixed-size square box.
        Args:
            size: Width and height of the square (default: 100)
            content: Widget to place inside the square (default: empty label)
            *args: Additional positional arguments for FixedSizeBox
            **kwargs: Additional keyword arguments for FixedSizeBox
        """
        # Set orientation to vertical by default
        kwargs.setdefault("orientation", Gtk.Orientation.VERTICAL)
        super().__init__(content, *args, **kwargs)

        self.fixed_size = size
        self.set_size_request(size, size)

    def do_size_allocate(self, allocation):
        """
        Allocate size for the widget and its children.
        Args:
            allocation: Gdk.Rectangle defining available space
        """
        # Calculate square size (min of available space and fixed size)
        size = min(allocation.width, allocation.height, self.fixed_size)

        # Create centered square allocation
        square_allocation = Gdk.Rectangle()
        square_allocation.width = size
        square_allocation.height = size
        square_allocation.x = allocation.x + (allocation.width - size) // 2
        square_allocation.y = allocation.y + (allocation.height - size) // 2

        # Allocate space for the box itself
        # Gtk.Widget.do_size_allocate(self, square_allocation)
        # Gtk.Box.do_size_allocate(self, square_allocation)
        FixedSizeBox.do_size_allocate(self, square_allocation)

        # center_x = (size - square_allocation.width) // 2
        # center_y = (size - square_allocation.height) // 2

        # center_allocation = Gdk.Rectangle()
        # center_allocation.width = square_allocation.width
        # center_allocation.height = square_allocation.height
        # center_allocation.x = center_x
        # center_allocation.y = center_y

        # self.center_box.size_allocate(center_allocation)

        # self._allocate_child(self.inner_content, center_allocation, center_x, center_y)

    # def _allocate_child(self, widget, parent_allocation, offset_x=0, offset_y=0):
    #     min_width, nat_width = widget.get_preferred_width()
    #     min_height, nat_height = widget.get_preferred_height()

    #     child_x = offset_x + (parent_allocation.width - min_width) // 2
    #     child_y = offset_y + (parent_allocation.height - min_height) // 2

    #     child_allocation = Gdk.Rectangle()
    #     child_allocation.x = child_x
    #     child_allocation.y = child_y
    #     child_allocation.width = min_width
    #     child_allocation.height = min_height

    #     widget.size_allocate(child_allocation)

    #     if isinstance(widget, Gtk.Container):
    #         for child in widget.get_children():
    #             self._allocate_child(child, child_allocation, child_x, child_y)

    # Size request methods to enforce fixed size
    def do_get_preferred_width(self):
        return (self.fixed_size, self.fixed_size)

    def do_get_preferred_height(self):
        return (self.fixed_size, self.fixed_size)

    def do_get_preferred_width_for_height(self, height):
        return (self.fixed_size, self.fixed_size)

    def do_get_preferred_height_for_width(self, width):
        return (self.fixed_size, self.fixed_size)


class FixedRectangleBox(FixedSizeBox):
    """
    A rectangular container that maintains a fixed aspect ratio and centers its content.
    """
    def __init__(self, width=150, height=100, content=None, *args, **kwargs):
        """
        Initialize a fixed-size rectangular box.
        Args:
            width: Width of the rectangle (default: 150)
            height: Height of the rectangle (default: 100)
            content: Widget to place inside the rectangle (default: empty label)
            *args: Additional positional arguments for FixedSizeBox
            **kwargs: Additional keyword arguments for FixedSizeBox
        """
        # Set orientation to vertical by default
        kwargs.setdefault("orientation", Gtk.Orientation.VERTICAL)
        super().__init__(content, *args, **kwargs)

        self.fixed_width = width
        self.fixed_height = height
        self.set_size_request(width, height)

    def do_size_allocate(self, allocation):
        """
        Allocate size for the widget and its children.
        Args:
            allocation: Gdk.Rectangle defining available space
        """
        if self.fixed_height == 0 or self.fixed_width == 0:
            return

        # Calculate available aspect ratio
        available_ratio = allocation.width / allocation.height
        target_ratio = self.fixed_width / self.fixed_height

        # Calculate size while maintaining aspect ratio
        if available_ratio > target_ratio:
            # Container is wider than target aspect ratio
            height = min(allocation.height, self.fixed_height)
            width = height * target_ratio
        else:
            # Container is taller than target aspect ratio
            width = min(allocation.width, self.fixed_width)
            height = width / target_ratio

        # Create centered rectangle allocation
        rect_allocation = Gdk.Rectangle()
        rect_allocation.width = width
        rect_allocation.height = height
        rect_allocation.x = allocation.x + (allocation.width - width) // 2
        rect_allocation.y = allocation.y + (allocation.height - height) // 2

        # Allocate space for the box itself
        FixedSizeBox.do_size_allocate(self, rect_allocation)

    # Size request methods to enforce fixed size
    def do_get_preferred_width(self):
        return (self.fixed_width, self.fixed_width)

    def do_get_preferred_height(self):
        return (self.fixed_height, self.fixed_height)

    def do_get_preferred_width_for_height(self, height):
        # Avoid division by zero
        if self.fixed_height == 0:
            return (0, 0)
        # Maintain aspect ratio when height is constrained
        width = height * (self.fixed_width / self.fixed_height)
        return (width, width)

    def do_get_preferred_height_for_width(self, width):
        # Avoid division by zero
        if self.fixed_width == 0:
            return (0, 0)

        # Maintain aspect ratio when width is constrained
        height = width / (self.fixed_width / self.fixed_height)
        return (height, height)


class CircleBox(Gtk.Box):
    """
    A circular container widget with centered content.
    Attributes:
        diameter (int): Diameter of the circular container
        border_width (int): Border thickness in pixels
        border_color (str): Hex color code for the border
        background_color (str): Hex color code for the background
        **kwargs: Additional arguments for Gtk.Box
    """
    def __init__(self, 
        diameter: int = 60,
        border_width: int = 4,
        border_color: str = "#000000",
        background_color: str = "transparent",
        **kwargs
    ):
        # Set default orientation to vertical
        kwargs.setdefault("orientation", Gtk.Orientation.VERTICAL)
        super().__init__(**kwargs)

        self.diameter = diameter
        self.border_width = border_width
        self.border_color = border_color
        self.background_color = background_color

        # Set fixed size
        self.set_size_request(diameter, diameter)

        # Create content container
        self.content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True
        )
        self.add(self.content_box)

        # Center the entire box in its parent
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        # Setup CSS styling
        self._setup_css()

    def _setup_css(self):
        """Configure CSS styling for the circular container."""
        # Create CSS provider
        self.css_provider = Gtk.CssProvider()
        # Generate CSS
        self._update_css()
        # Apply CSS
        context = self.get_style_context()
        context.add_provider(
            self.css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        context.add_class("circle-container")

    def _update_css(self):
        """Update CSS based on current state."""
        css = f"""
        .circle-container {{
            border-radius: 50%;
            min-width: {self.diameter}px;
            min-height: {self.diameter}px;
            border: {self.border_width}px solid {self.border_color};
            background-color: {self.background_color};
            background-image: none;
        }}
        """
        self.css_provider.load_from_data(css.encode())

    def add_content(self, widget):
        """
        Add a widget to the center of the circular container.
        
        Args:
            widget: Any Gtk.Widget to add to the container
        """
        self.content_box.add(widget)
        widget.show()

    def remove_content(self):
        """Remove all content widgets."""
        for child in self.content_box.get_children():
            self.content_box.remove(child)

    def set_border(self, width=None, color=None):
        """
        Update border properties.
        Args:
            width: New border width in pixels (or None to keep current)
            color: New border color in hex format (or None to keep current)
        """
        if width is not None:
            self.border_width = width

        if color is not None:
            self.border_color = color

        self._update_css()

    def set_background(self, color):
        """
        Update background color.
        Args:
            color: New background color in hex format
        """
        self.background_color = color
        self._update_css()

    def set_diameter(self, diameter):
        """
        Update container diameter.
        Args:
            diameter: New diameter in pixels
        """
        self.diameter = diameter
        self.set_size_request(diameter, diameter)
        self._update_css()