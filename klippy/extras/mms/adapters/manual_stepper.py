# Adapter of printer's manual_stepper
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from .base import BaseAdapter


class ManualStepperAdapter(BaseAdapter):
    def __init__(self, stepper_name):
        super().__init__()
        self.stepper_name = stepper_name

    def get_manual_stepper(self):
        return self.safe_get(self.stepper_name)

    def set_home_accel(self, accel):
        self.get_manual_stepper().homing_accel = accel

    def reset_position(self):
        # Don't use the do_set_position()
        # "toolhead.flush_step_generation()" in do_set_position()
        # may block this function
        # self.get_manual_stepper().do_set_position(setpos=0.0)
        self.get_manual_stepper().commanded_pos = 0.0


class ManualStepperDispatch:
    def __init__(self):
        # key: stepper_name
        # val: ManualStepperAdapter()
        self.ms_adapter_dct = {}

    def get_adapter(self, stepper_name):
        if stepper_name in self.ms_adapter_dct:
            return self.ms_adapter_dct.get(stepper_name)

        manual_stepper_adapter = ManualStepperAdapter(stepper_name)
        self.ms_adapter_dct[stepper_name] = manual_stepper_adapter
        return manual_stepper_adapter


# Global instance for singleton
manual_stepper_dispatch = ManualStepperDispatch()
