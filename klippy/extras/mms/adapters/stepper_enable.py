# Adapter of printer's stepper_enable
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter


class StepperEnableAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "stepper_enable"

    def _get_stepper_enable(self):
        return self.safe_get(self._obj_name)

    def get_enable_tracking(self, stepper_name):
        return self._get_stepper_enable().lookup_enable(stepper_name)

    def is_motor_enabled(self, stepper_name):
        return self.get_enable_tracking(stepper_name).is_motor_enabled()

    def enable(self, stepper_name, print_time):
        if not self.is_motor_enabled(stepper_name):
            self.get_enable_tracking(stepper_name).motor_enable(print_time)
            return True
        return False

    def disable(self, stepper_name, print_time):
        if self.is_motor_enabled(stepper_name):
            self.get_enable_tracking(stepper_name).motor_disable(print_time)
            return True
        return False


# Global instance for singleton
stepper_enable_adapter = StepperEnableAdapter()
