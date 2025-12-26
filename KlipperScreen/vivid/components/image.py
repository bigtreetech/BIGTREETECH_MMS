import logging
import os
import pathlib

from dataclasses import dataclass

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk, Pango


@dataclass(frozen=True)
class ImageConfig:
    resource_folder: str = "resources"
    resource_path: str = os.path.join(
        pathlib.Path(__file__).parent.resolve().parent, 
        resource_folder)


class VividImage(Gtk.Image):
    def __init__(self, file_name: str, width: int, height: int):
        super().__init__()
        self.width = int(width)
        self.height = int(height)
        config = ImageConfig()
        
        # file_name should set with ext, like "*.svg"
        self.file_full_path = os.path.join(config.resource_path, file_name)

        if not os.path.exists(self.file_full_path):
            logging.error(f"Image not found: {self.file_full_path}")
            return

        pixbuf = self._create_pixbuf()
        if pixbuf:
            self.set_from_pixbuf(pixbuf)

    def _create_pixbuf(self):
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(
                self.file_full_path, self.width, self.height)
        except Exception as e:
            logging.error(f"Failed to load image: {self.file_full_path}")
            logging.error(str(e))
            return None

    def _create_pixbuf_free(self):
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                self.file_full_path, self.width, self.height, preserve_aspect_ratio=False)
        except Exception as e:
            logging.error(f"Failed to load image: {self.file_full_path}")
            logging.error(str(e))
            return None

    def update_image_file(self, new_file_name):
        config = ImageConfig()
        new_file_full_path = os.path.join(config.resource_path, new_file_name)

        if not os.path.exists(new_file_full_path):
            logging.error(f"Image not found: {new_file_full_path}")
            return False

        self.file_full_path = new_file_full_path
        return self._refresh_image()

    def update_image_size(self, new_width, new_height):
        self.width = int(new_width)
        self.height = int(new_height)
        return self._refresh_image()

    def _refresh_image(self):
        pixbuf = self._create_pixbuf()
        if not pixbuf:
            return False

        self.set_from_pixbuf(pixbuf)
        self.queue_draw()
        return True