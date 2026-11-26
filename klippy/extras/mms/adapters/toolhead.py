# Adapter of printer's toolhead
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager

import greenlet
from gcode import CommandError

from .base import BaseAdapter
from .fan import fan_adapter
from .gcode import gcode_adapter
from .gcode_move import gcode_move_adapter
from .heaters import heaters_adapter
from .motion_queuing import motion_queuing_adapter
from .print_stats import print_stats_adapter
from .printer import printer_adapter


class ToolheadAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._obj_name = "toolhead"

        self.pressure_release_time = 0.5

        self._safe_mode = False
        self._move_speed = 0.0

        self._motion_scope = None
        self._fan_scope_speed = None

    def _setup_logger(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=False)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    def _get_toolhead(self):
        return self.safe_get(self._obj_name)

    def dwell(self, delay):
        self.check_motion()
        self._get_toolhead().dwell(delay)
        self.check_motion()

    def is_busy(self):
        print_time, est_print_time, lookahead_empty = \
            self._get_toolhead().check_busy(self.reactor.monotonic())
        idle_time = est_print_time - print_time
        return not lookahead_empty or idle_time < 1.

    def get_print_time(self):
        return self._get_toolhead().get_last_move_time()

    def wait_moves(self, check=True):
        # Cleanup may drain already accepted moves after cancellation. It does
        # not submit new motion; core also stops waiting on printer shutdown.
        if check:
            self.check_motion()
        self._get_toolhead().wait_moves()
        if check:
            self.check_motion()

    def get_status(self):
        return self._get_toolhead().get_status(self.reactor.monotonic())

    def get_homed_axes(self):
        return self.get_status().get("homed_axes")

    def is_homed(self):
        return "xyz" in self.get_homed_axes()

    # ---- Extruder ----
    def has_extruder(self):
        extruder_name = self.get_status().get("extruder")
        return extruder_name and extruder_name != ""

    def get_extruder(self):
        return self._get_toolhead().get_extruder()

    def get_extruder_heater(self):
        return self.get_extruder().get_heater()

    def get_extruder_heater_status(self):
        return self.get_extruder_heater().get_status(self.reactor.monotonic())

    def get_extruder_heater_temp(self):
        return self.get_extruder_heater_status().get("temperature")

    def get_extruder_heater_target_temp(self):
        return self.get_extruder_heater_status().get("target")

    def set_extruder_temperature(self, temp, wait=True):
        e_name = self.get_extruder().get_name()
        heater = self.get_extruder_heater()

        # wait timeout?
        if wait:
            self.log_info(
                f"extruder[{e_name}] waiting heat to temp:{temp}..."
            )
        else:
            self.log_info_s(
                f"extruder[{e_name}] heat to temp:{temp} without waiting"
            )

        self.check_motion()
        heaters_adapter.set_temperature(
            heater, temp, wait, check=self.check_motion
        )
        if wait:
            self.log_info(
                f"extruder[{e_name}] finish heat to temp:{temp}"
            )

    def flush_step_generation(self):
        return motion_queuing_adapter.run_native_motion(
            self._get_toolhead().flush_step_generation)

    # ---- Fan ----
    @contextmanager
    def temporary_fan_speed(self, speed):
        # Fan status may lag queued M106 commands. Nested scopes restore the
        # parent's requested speed, not the last applied fan status.
        parent_speed = self._fan_scope_speed
        saved = (fan_adapter.get_speed() if parent_speed is None
                 else parent_speed)
        if saved is None:
            yield
            return
        resume = printer_adapter.get_mms().get_mms_resume()
        generation = resume.get_status()["generation"]
        was_finished = print_stats_adapter.is_finished()
        try:
            self.check_motion()
            self._fan_scope_speed = speed
            fan_adapter.set_speed(speed)
            self.check_motion()
            yield
        finally:
            try:
                if not self.printer.is_shutdown():
                    current = generation == resume.get_status()["generation"]
                    ended = (not was_finished
                             and print_stats_adapter.is_finished())
                    restore = saved if current and not ended else 0.
                    # Always queue the restore: even equal reported speed
                    # may still have an earlier pending command to override.
                    fan_adapter.set_speed(restore)
            finally:
                self._fan_scope_speed = parent_speed

    @contextmanager
    def fan_cooldown(self, speed, wait):
        with self.temporary_fan_speed(speed):
            self.dwell(delay=wait)
            yield

    # ---- Move ----
    # At the beginning, Move methods are coded in gcode_move_adapter.
    # However after developed, toolhead move xyz methods are coded here

    def get_position(self):
        return gcode_move_adapter.get_toolhead_position()

    def set_move_speed(self, move_speed):
        self._move_speed = move_speed

    def get_move_speed(self):
        return self._move_speed

    def enable_safe_mode(self):
        self._safe_mode = True

    def disable_safe_mode(self):
        self._safe_mode = False

    def safe_move(self, params, wait_toolhead=False, on_accepted=None):
        self.check_motion()
        gcode_move_adapter.g1(params)
        if on_accepted is not None:
            on_accepted()
        self.check_motion()
        if wait_toolhead or self._safe_mode:
            self.wait_moves()

    def move(self, params_dct, speed=None, wait_toolhead=False):
        params = dict(params_dct)
        params["F"] = self._move_speed if speed is None else speed
        self.safe_move(params, wait_toolhead)

    def move_x(self, position, speed=None, wait_toolhead=False):
        self.move({"X":position}, speed, wait_toolhead)

    def move_y(self, position, speed=None, wait_toolhead=False):
        self.move({"Y":position}, speed, wait_toolhead)

    def move_z(self, position, speed=None, wait_toolhead=False):
        self.move({"Z":position}, speed, wait_toolhead)

    def move_xy(self, position_x, position_y, speed=None, wait_toolhead=False):
        self.move({"X":position_x, "Y":position_y}, speed, wait_toolhead)

    # ---- Native operation state ----
    def check_motion(self, require_owner=True):
        if self.printer.is_shutdown():
            raise CommandError("MMS operation stopped: printer is shut down")
        scope = self._motion_scope
        if scope is not None:
            if scope["owner"] is not greenlet.getcurrent():
                if require_owner:
                    raise CommandError(
                        "Another MMS toolhead operation is still running"
                    )
                # Independent drive tasks have their own stepper ownership.
                return
            if scope.get("error") is not None:
                raise CommandError(scope["error"])
            if scope["check"] is not None:
                scope["check"]()

    def fail_motion(self, reason):
        """Deliver a background feed failure to the operation using it."""
        scope = self._motion_scope
        if scope is None:
            return False
        if scope.get("error") is None:
            scope["error"] = str(reason)
        return True

    def _motion_limits(self):
        status = self.get_status()
        limits = {
            "VELOCITY": status["max_velocity"],
            "ACCEL": status["max_accel"],
            "SQUARE_CORNER_VELOCITY": status["square_corner_velocity"],
        }
        if "minimum_cruise_ratio" in status:
            limits["MINIMUM_CRUISE_RATIO"] = status["minimum_cruise_ratio"]
        elif "max_accel_to_decel" in status:
            limits["ACCEL_TO_DECEL"] = status["max_accel_to_decel"]
        return limits

    def _set_motion_limits(self, limits):
        gcode_adapter.run_command("SET_VELOCITY_LIMIT " + " ".join(
            f"{key}={value}" for key, value in limits.items()), strict=True)

    def _save_motion_state(self, name):
        gcode_move_adapter.save_state(name)
        return {
            "name": name,
            "position": self.get_position(),
            "limits": self._motion_limits(),
            "fan_speed": fan_adapter.get_speed(),
        }

    def capture_return_state(self):
        # This fixed name belongs to MMSResume until commit/cancellation.
        return self._save_motion_state("__MMS_RETURN_STATE")

    def restore_return_modes(self, state):
        # MOVE=0 preserves the current physical position and E displacement.
        try:
            gcode_move_adapter.restore_state(state["name"])
        finally:
            self._set_motion_limits(state["limits"])

    @contextmanager
    def motion_scope(self, return_state=None, z_raise=0., check=None):
        scope = self._motion_scope
        if scope is not None:
            if scope["owner"] is not greenlet.getcurrent():
                raise CommandError(
                    "Another MMS toolhead operation is still running"
                )
            previous_check = scope["check"]
            if check is not None and check is not previous_check:
                def nested_check():
                    if previous_check is not None:
                        previous_check()
                    check()
                scope["check"] = nested_check
            try:
                self.check_motion()
                yield scope["state"]
                self.check_motion()
            finally:
                scope["check"] = previous_check
            return

        if check is None:
            resume = printer_adapter.get_mms().get_mms_resume()
            generation = resume.get_status()["generation"]
            def check():
                if resume.get_status()["generation"] != generation:
                    raise CommandError("MMS operation was cancelled")
        state = return_state or self._save_motion_state("__MMS_MOTION_SCOPE")
        scope = {"state": state, "owner": greenlet.getcurrent(), "check": check}
        self._motion_scope = scope
        try:
            self.check_motion()
            gcode_move_adapter.normalize_motion()
            config = self.config.getsection("printer")
            accel = printer_adapter.get_mms_swap().toolhead_move_accel
            profile = {
                "VELOCITY": config.getfloat("max_velocity"),
                "ACCEL": min(accel, config.getfloat("max_accel")),
                "SQUARE_CORNER_VELOCITY": config.getfloat(
                    "square_corner_velocity", 5.),
            }
            if "MINIMUM_CRUISE_RATIO" in state["limits"]:
                profile["MINIMUM_CRUISE_RATIO"] = config.getfloat(
                    "minimum_cruise_ratio", .5)
            elif "ACCEL_TO_DECEL" in state["limits"]:
                profile["ACCEL_TO_DECEL"] = min(
                    profile["ACCEL"], config.getfloat(
                        "max_accel_to_decel", profile["ACCEL"] * .5))
            self._set_motion_limits(profile)
            if z_raise > 0.:
                position = self.get_position()
                self._check_return_position(position, position["z"] + z_raise)
                self.move_z(position["z"] + z_raise, wait_toolhead=True)
            yield state
            self.check_motion()
        finally:
            try:
                self.restore_return_modes(state)
            finally:
                self._motion_scope = None

    def _check_return_position(self, position, safe_z):
        status = self.get_status()
        current = self.get_position()
        minimum, maximum = status["axis_minimum"], status["axis_maximum"]
        physical_z = status["position"].z + safe_z - current["z"]
        if (not self.is_homed()
                or not minimum.x <= position["x"] <= maximum.x
                or not minimum.y <= position["y"] <= maximum.y
                or position["z"] < minimum.z or physical_z > maximum.z):
            raise CommandError(
                "MMS return blocked: home XYZ and leave sufficient Z clearance")

    def return_to_saved(self, state, position=None, before_lower=None,
                        clearance=1., speed=None):
        target = dict(state["position"])
        if position is not None:
            target.update(position)
        self.check_motion()
        current = self.get_position()
        safe_z = max(current["z"], target["z"] + clearance)
        self._check_return_position(target, safe_z)
        self.move_z(safe_z, wait_toolhead=True)
        self.move_xy(target["x"], target["y"], speed=speed, wait_toolhead=True)
        if before_lower is not None:
            before_lower()
        self.check_motion()
        self.move_z(target["z"], wait_toolhead=True)

    # ---- Snapshot ----
    def _format_snapshot(self):
        return {
            "position" : self.get_position(),
            "extruder_current_temp" : self.get_extruder_heater_temp(),
            "extruder_target_temp" : self.get_extruder_heater_target_temp(),
            "fan_speed" : fan_adapter.get_speed(),
        }

    def log_snapshot(self, snapshot=None):
        snapshot = snapshot or self._format_snapshot()
        self.log_info_s(
            "\n"
            "current toolhead snapshot:\n"
            "toolhead position - "
            f"x: {snapshot['position']['x']:.2f} "
            f"y: {snapshot['position']['y']:.2f} "
            f"z: {snapshot['position']['z']:.2f}\n"
            f"extruder current temp: {snapshot['extruder_current_temp']:.2f}\n"
            f"extruder target temp: {snapshot['extruder_target_temp']:.2f}\n"
            f"fan speed: {snapshot['fan_speed']:.2f}"
        )

    # ---- Pressure ----
    def release_pressure(self, pressure_release_time=None):
        delay = pressure_release_time or self.pressure_release_time
        self.dwell(delay)


# Global instance for singleton
toolhead_adapter = ToolheadAdapter()
