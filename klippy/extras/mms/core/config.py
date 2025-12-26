# Support for MMS Configuration
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class PrinterConfig:
    # Must be first line, printer_config is the param of loading object
    printer_config: object
    skip_configs = ["printer_config",]

    def __post_init__(self):
        type_handlers = self.get_type_handlers()

        for field_info in fields(self):
            field_name = field_info.name
            field_type = field_info.type

            if self.should_skip(field_name):
                continue

            # Default type is "str"
            handler = type_handlers.get(field_type, type_handlers.get(str))
            if handler is None:
                value = self.printer_config.get(field_name)
            else:
                value = handler(self.printer_config, field_name)

            # Set value as self attribute
            object.__setattr__(self, field_name, value)

    def get_type_handlers(self):
        return {
            str: lambda config, name: config.get(name),
            int: lambda config, name: config.getint(name),
            float: lambda config, name: config.getfloat(name),
            list: lambda config, name: config.getintlist(name),
            # Optional
            OptionalField: lambda config, name: OptionalField.parse(
                config, name),
            # Custom type
            PointType: lambda config, name: PointType.parse(config.get(name)),
            PointsType: lambda config, name: PointsType.parse(config.get(name)),
            StringList: lambda config, name: StringList.parse(config.get(name)),
        }

    def should_skip(self, config_key):
        return config_key in self.skip_configs

    # def extend_skip_keys(self, skip_keys):
    #     self.skip_configs.extend(skip_keys)

    def gen_packaged_config(self):
        class PConfig:
            pass
        p_config = PConfig()
        for field in fields(self):
            key = field.name
            if not self.should_skip(key) \
                and not hasattr(p_config, key):
                val = getattr(self, key)
                setattr(p_config, key, val)
        return p_config


class OptionalField:
    @staticmethod
    def parse(config, name):
        """
        Always get()
        """
        return config.get(name, None)


class PointsType:
    @staticmethod
    def parse(points_string):
        """
        Example:
            "(90.0, 300.0), (60.0, 300.0)"
        """
        clean_string = points_string.replace(" ", "").strip("()")
        points = []

        if clean_string:
            for point_str in clean_string.split("),("):
                if not point_str:
                    continue

                coords = point_str.split(",")
                if len(coords) == 2:
                    try:
                        x = float(coords[0])
                        y = float(coords[1])
                        points.append((x,y))
                    except ValueError:
                        continue

        return points


class PointType:
    @staticmethod
    def parse(point_string):
        """
        Example:
            "(90.0, 300.0)"
        """
        p_lst = PointsType.parse(point_string)
        return p_lst[0] if p_lst else None


class StringList:
    @staticmethod
    def parse(val_string):
        """
        Example:
            "1,2,3,4"
        """
        val_string = val_string or ""
        lst = [val.strip() for val in val_string.split(",")]
        return lst
