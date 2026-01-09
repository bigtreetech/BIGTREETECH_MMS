# Support for MMS Purge
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager
from dataclasses import dataclass, fields

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.config import (
    OptionalField,
    PointType,
    PrinterConfig
)
from ..core.exceptions import PurgeFailedError
from ..core.logger import log_time_cost
from ..core.task import AsyncTask


@dataclass(frozen=True)
class PrinterPurgeConfig(PrinterConfig):
    # Enable/disable the purge module
    # 0 = disable, 1 = enable
    # When disabled, all purge operations will be skipped
    enable: int = 1

    # Z-axis lift distance during cutting operations
    # Unit: mm
    z_raise: float = 1.0

    # Fan speed during cooldown phase
    # Value range: 0.0 to 1.0 (0% to 100%)
    fan_cooldown_speed: float = 1.0
    # Duration to wait while cooling with fan
    # Unit: seconds
    fan_cooldown_wait: float = 2.0

    # Extruder speed during filament purging
    # Unit: mm/min
    purge_speed: float = 600.0

    # Length of orphan filament to be purged
    #     | | Extruder | |
    #     | | Stepper  | |
    #     | +----------+ |
    #  ======> Cutter    | ---
    #     |              |  |
    #      \            /   | orphan filament
    #       \  Nozzle  /    | length
    #        \        /     |
    #         \______/     ---
    #
    # Unit: mm
    orphan_filament_length: float = 60
    # Multiplier for purge volume calculation
    purge_modifier: float = 2.5

    # Should be less than or equal to buffer spring stroke length
    # Unit: mm
    retraction_compensation: float = 3.0
    # Retraction speed
    # Unit: mm/min
    retract_speed: float = 10000.0

    # Priming distance
    # Unit: mm
    nozzle_priming_dist: float = 20.0
    # Priming speed
    # Unit: mm/min
    nozzle_priming_speed: float = 600.0

    # Pressure pulse cleaning
    # 0 = disable, 1 = enable
    pulse_clean_enable: int = 0
    # Unit: second
    pulse_rest_time: float = 0.1
    pulse_count: int = 4
    # Unit: mm/min
    pulse_speed: float = 1200
    # Unit: mm
    retract_dist: float = 10
    # Unit: mm
    extrude_dist: float = retract_dist * 0.5

    # Tray
    # X/Y coordinates of the purge tray location
    tray_point: PointType = "(60.0, 100.0)"
    # X/Y coordinates for ejecting purged filament from tray
    eject_point: PointType = "(60.0, 100.0)"

    # Custom Macro
    custom_before: OptionalField = "MMS_PURGE_CUSTOM_BEFORE"
    custom_after: OptionalField = "MMS_PURGE_CUSTOM_AFTER"


class MMSPurge:
    def __init__(self, config):
        p_purge_config = PrinterPurgeConfig(config)
        for field in fields(p_purge_config):
            key = field.name
            # Don't cover
            if not p_purge_config.should_skip(key) \
                and not hasattr(self, key):
                val = getattr(p_purge_config, key)
                setattr(self, key, val)

        # State tracking
        self._is_running = False

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    # ---- Initial ----
    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()

    def _initialize_gcode(self):
        commands = [
            ("MMS_PURGE", self.cmd_MMS_PURGE),
            ("MMS_TRAY", self.cmd_MMS_TRAY),
            ("MMS_TRAY_EJECT", self.cmd_MMS_TRAY_EJECT),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        # Log would not output to console
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    # ---- Status ----
    def is_enabled(self):
        return bool(self.enable)

    def is_running(self):
        return self._is_running

    @contextmanager
    def _purge_is_running(self):
        self._is_running = True
        try:
            yield
        finally:
            self._is_running = False

    def get_purge_speed(self):
        return self.purge_speed

    def get_purge_distance(self):
        return self.orphan_filament_length * self.purge_modifier

    # ---- MMS Buffer control ----
    def _pause_mms_buffer(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_buffer = mms_slot.get_mms_buffer()
        # Deactivate buffer monitor before
        mms_buffer.deactivate_monitor()

        # Make sure all mms steppers are idle
        # Timeout with default config in mms_delivery
        is_idle = self.mms_delivery.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            raise PurgeFailedError(
                f"slot[{slot_num}] wait selector or drive stepper idle timeout",
                mms_slot
            )
        return mms_buffer

    def _prepare_mms_buffer(self, slot_num):
        mms_buffer = self._pause_mms_buffer(slot_num)
        # Setup volume of buffer
        if not mms_buffer.halfway(slot_num):
            raise PurgeFailedError(
                f"slot[{slot_num}] halfway buffer failed during purge",
                self.mms.get_mms_slot(slot_num)
            )

    # ---- Tray ----
    def move_to_tray(self):
        # Toolhead move to tray point
        # Always move Y-axis first to avoid accident
        toolhead_adapter.move_y(
            position = self.tray_point[1],
            wait_toolhead = True
        )
        toolhead_adapter.move_x(
            position = self.tray_point[0],
            wait_toolhead = True
        )

    def tray_eject(self):
        # Toolhead move to eject point to eject the blob
        toolhead_adapter.move_xy(
            position_x = self.eject_point[0],
            position_y = self.eject_point[1],
            wait_toolhead = True
        )

    # ---- Task ----
    def _async_purge_feed(self, slot_num, distance):
        # Setup and start task in background
        func = self._purge_feed_task
        params = {"slot_num" : slot_num, "distance" : distance}
        async_task = AsyncTask()
        try:
            if async_task.setup(func, params):
                async_task.start()
        except Exception as e:
            self.log_error(f"slot[{slot_num}] async purge_feed error: {e}")
            return False
        return True

    def _purge_feed_task(self, slot_num, distance):
        distance = abs(distance)
        spd = self.purge_speed / 60
        # self.mms_delivery.mms_drip_move(slot_num, abs(distance), spd, spd)
        # self.mms_delivery.mms_move(slot_num, abs(distance), spd, spd)
        # self.mms_delivery.move_forward(slot_num, abs(distance), spd, spd)
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        mms_drive.update_focus_slot(slot_num)
        # No select method
        mms_drive.manual_move(distance, spd, spd)
        self.log_info_s(f"slot[{slot_num}] deliver distance={distance:.2f} mm")

    def _async_cold_pull(self, slot_num, distance, speed):
        # Setup and start task in background
        func = self._cold_pull_task
        params = {
            "slot_num" : slot_num,
            "distance" : distance,
            "speed" : speed,
        }
        async_task = AsyncTask()
        try:
            if async_task.setup(func, params):
                async_task.start()
        except Exception as e:
            self.log_error(f"slot[{slot_num}] async cold_pull error: {e}")
            return False
        return True

    def _cold_pull_task(self, slot_num, distance, speed):
        self.mms_delivery.mms_move(
            slot_num, -abs(distance), speed, speed)

    # def _pre_cut_nozzle_cleaning(self, slot_num):
    #     # degrade_temp = self._get_material_degrade_temp(slot_num) + 20
    #     degrade_temp = 220
    #     extruder_adapter.set_temperature(degrade_temp, wait=True)

    #     retract_speed = 25
    #     extruder_adapter.retract(
    #         distance=8,
    #         speed=retract_speed * 1.2
    #     )

    #     toolhead_adapter.dwell(0.5)

    #     # Cooldown
    #     solidify_temp = 170
    #     extruder_adapter.set_temperature(solidify_temp, wait=True)

    #     purge_speed = 25
    #     extruder_adapter.extrude(
    #         distance=8,
    #         speed=purge_speed * 0.8
    #     )
    #     extruder_adapter.retract(
    #         distance=10,
    #         speed=retract_speed * 0.8
    #     )

    def _async_move_forward(self, slot_num, distance, speed):

        def _task():
            self.mms_delivery.mms_move(
                slot_num, abs(distance), speed, speed)

        # Setup and start task in background
        try:
            async_task = AsyncTask()
            if async_task.setup(_task):
                async_task.start()
        except Exception as e:
            self.log_error(f"slot[{slot_num}] async move_forward error: {e}")
            return False
        return True

    def _async_move_backward(self, slot_num, distance, speed):

        def _task():
            self.mms_delivery.mms_move(
                slot_num, -abs(distance), speed, speed)

        # Setup and start task in background
        try:
            async_task = AsyncTask()
            if async_task.setup(_task):
                async_task.start()
        except Exception as e:
            self.log_error(f"slot[{slot_num}] async move_backward error: {e}")
            return False
        return True

    # ---- Toolhead Control ----
    def pressure_pulse_cleaning(self, slot_num):
        if self.pulse_clean_enable == 0:
            return

        log_prefix = f"slot[{slot_num}] pressure pulse cleaning"

        # Clear buffer volume
        mms_buffer = self._pause_mms_buffer(slot_num)
        if not mms_buffer.clear(slot_num):
            raise PurgeFailedError(
                f"{log_prefix} failed, mms_buffer is not clear",
                self.mms.get_mms_slot(slot_num)
            )

        # Calculate
        # Extruder params
        # If retracted_dist <= 0 ?
        retracted_dist = self.retract_dist - self.extrude_dist
        total_retracted_dist = retracted_dist * self.pulse_count
        # Drive stepper params
        unload_dist = total_retracted_dist - mms_buffer.get_spring_stroke()
        # Unit: pulse_speed::mm/min -> unload_speed::mm/s
        unload_speed = self.pulse_speed / 60 * 0.5

        self.log_info_s(f"{log_prefix} begin")

        # Startup async unload first
        self._async_move_backward(slot_num, unload_dist, unload_speed)
        for i in range(self.pulse_count):
            # Retract
            extruder_adapter.retract(self.retract_dist, self.pulse_speed)
            toolhead_adapter.dwell(self.pulse_rest_time)
            # Extrude
            extruder_adapter.extrude(self.extrude_dist, self.pulse_speed)
            toolhead_adapter.dwell(self.pulse_rest_time)

        # Finally wait idle
        self.mms_delivery.wait_mms_selector_and_drive(slot_num)
        self.log_info_s(
            f"{log_prefix} finish"
            f", total retracted: {total_retracted_dist:.2f} mm"
        )

    def _apply_nozzle_priming(self, slot_num):
        """Prime nozzle after filament change."""
        mms_buffer = self._pause_mms_buffer(slot_num)

        log_prefix = f"slot[{slot_num}] purge with nozzle priming only"
        self.log_info_s(f"{log_prefix} begin")

        # Make sure buffer is halfway
        if not mms_buffer.halfway(slot_num):
            raise PurgeFailedError(
                f"{log_prefix} failed, mms_buffer is not halfway",
                self.mms.get_mms_slot(slot_num)
            )

        # distance = min(
        #     abs(self.nozzle_priming_dist),
        #     mms_buffer.get_spring_stroke()
        # )
        distance = abs(self.nozzle_priming_dist)
        move_speed = self.nozzle_priming_speed / 60
        move_time = distance / move_speed

        self._async_move_forward(slot_num, distance, move_speed)
        extruder_adapter.extrude(distance, self.nozzle_priming_speed)
        self.log_info_s(f"{log_prefix}, distance: {distance} mm")

        # Wait async task finish
        self.mms_delivery.wait_mms_selector_and_drive(
            slot_num=slot_num, timeout=move_time+5)

        # Reduces underextrusion after retraction
        # toolhead_adapter.release_pressure()

        self.log_info_s(f"{log_prefix} finish")

    def apply_retraction_compensation(self, slot_num):
        """Extruder retract a little bit to decrease nozzle remain."""
        mms_buffer = self._pause_mms_buffer(slot_num)
        log_prefix = f"slot[{slot_num}] apply retraction compensation"

        # Make sure buffer is clear
        if not mms_buffer.clear(slot_num):
            raise PurgeFailedError(
                f"{log_prefix} failed, mms_buffer is not clear",
                self.mms.get_mms_slot(slot_num)
            )

        distance = min(
            abs(self.retraction_compensation),
            mms_buffer.get_spring_stroke()
        )
        extruder_adapter.retract(distance, self.retract_speed)
        self.log_info_s(f"{log_prefix}, distance: {distance} mm")

    # ---- Cold pull ----
    def cold_pull(self, slot_num):
        COLD_PULL_TEMP_MAP = {
            "PLA": {
                "temp" : 70,
                "length" : 15,
                "speed" : 300,
                "wait_second" : 5.0,
            },
            "ABS": {
                "temp" : 100,
                "length" : 20,
                "speed" : 200,
                "wait_second" : 5.0,
            },
            "PETG": {
                "temp" : 80,
                "length" : 18,
                "speed" : 250,
                "wait_second" : 5.0,
            },
            "TPU": {
                "temp" : 50,
                "length" : 15,
                "speed" : 300,
                "wait_second" : 5.0,
            },
        }

        self._prepare_mms_buffer(slot_num)
        mms_slot = self.mms.get_mms_slot(slot_num)
        # material_type = mms_slot.get_material_type()
        # solidify_temp = COLD_PULL_TEMP_MAP.get(material_type, 70)
        solidify_temp = 170
        solidify_wait = 3.0
        pull_length = 150
        pull_speed = 1200
        unload_speed = 20 # 1200/60

        self.log_info_s(f"slot[{slot_num}] cold pull begin")

        target_temp = extruder_adapter.get_target_temp()

        # if solidify_temp > target_temp?
        with toolhead_adapter.fan_cooldown(
                speed = self.fan_cooldown_speed,
                wait = self.fan_cooldown_wait
            ):
            extruder_adapter.set_temperature(solidify_temp, wait=True)

            self.log_info_s(
                f"slot[{slot_num}] solidify wait: {solidify_wait}s...")
            toolhead_adapter.dwell(delay=solidify_wait)

            # Begin async feed
            success = self._async_cold_pull(slot_num,
                                            pull_length, unload_speed)
            if not success:
                return False
            self.log_info_s(f"slot[{slot_num}] async move backward begin")

            # Begin extruder retract
            extruder_adapter.retract(pull_length, pull_speed)

        self.log_info_s(f"slot[{slot_num}] recover temp")
        # Recover original target temp
        extruder_adapter.set_temperature(target_temp, wait=False)

        # Make sure drive stepper is idle
        self.mms_delivery.wait_mms_drive(slot_num)
        self.log_info_s(f"slot[{slot_num}] cold pull finish")

        return True

    # ---- Bussiness methods ----
    def _safety_checks(self, slot_num):
        if slot_num is None:
            self.log_warning("current slot is None, return")
            return False

        if self.is_running():
            self.log_warning("another purge is running, return")
            return False

        # Check toolhead
        if not toolhead_adapter.is_homed():
            self.log_warning("toolhead is not homed, return")
            return False

        # Check extruder
        if not extruder_adapter.is_hot_enough():
            return False

        return True

    def _standard_purge(self, slot_num):
        log_prefix = f"slot[{slot_num}] standard purge"
        self.log_info_s(f"{log_prefix} begin")

        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_buffer = mms_slot.get_mms_buffer()

        # Calculation
        purge_distance = self.get_purge_distance()
        purge_volume = (
            purge_distance * extruder_adapter.get_extruder_filament_area())

        spring_stroke = mms_buffer.get_spring_stroke()
        filament_cross_section = (
            mms_buffer.get_status().get("filament_cross_section"))
        deliver_distance = (purge_volume / filament_cross_section
                            - spring_stroke * 0.5)

        # Prepare
        self._prepare_mms_buffer(slot_num)
        self.move_to_tray()
        self.mms_delivery.mms_select(slot_num)

        # Begin async feed
        if not self._async_purge_feed(slot_num, deliver_distance):
            raise PurgeFailedError(
                f"{log_prefix} async feed failed", mms_slot
            )

        # Begin extrude
        extruder_adapter.extrude(purge_distance, self.purge_speed)
        # Release pressure after extrude
        # toolhead_adapter.release_pressure()
        # Make sure drive stepper is idle
        self.mms_delivery.wait_mms_drive(slot_num)

        # Wait a while to solidify filament
        with toolhead_adapter.fan_cooldown(
                speed = self.fan_cooldown_speed,
                wait = self.fan_cooldown_wait
            ):
            self.apply_retraction_compensation(slot_num)

        self.log_info_s(f"{log_prefix} finish")

    def _exec_custom_macro(self, macro, position):
        if macro:
            self.log_info(
                f"MMS execute macro {position} PURGE:\n"
                f"{macro}"
            )
            gcode_adapter.run_command(macro)

    def mms_purge(self):
        self._exec_custom_macro(self.custom_before, "before")

        slot_num = self.mms.get_current_slot()
        if not self._safety_checks(slot_num):
            return False

        log_prefix = f"slot[{slot_num}] purge"
        self.log_info_s(f"{log_prefix} begin")

        with self._purge_is_running():
            try:
                if self.is_enabled():
                    self._standard_purge(slot_num)
                else:
                    self._apply_nozzle_priming(slot_num)

            except PurgeFailedError as e:
                self.log_warning(f"{log_prefix} failed: {e}")
                return False
            except Exception as e:
                self.log_error(f"{log_prefix} error: {e}")
                return False

        self.log_info_s(f"{log_prefix} finish")
        self._exec_custom_macro(self.custom_after, "after")
        return True

    # ---- GCode ----
    @log_time_cost("log_info_s")
    def cmd_MMS_PURGE(self, gcmd):
        with toolhead_adapter.snapshot():
            with toolhead_adapter.safe_z_raise(self.z_raise):
                self.mms_purge()

    def cmd_MMS_TRAY(self, gcmd=None):
        with toolhead_adapter.safe_z_raise(self.z_raise):
            self.move_to_tray()

    def cmd_MMS_TRAY_EJECT(self, gcmd=None):
        with toolhead_adapter.snapshot():
            with toolhead_adapter.safe_z_raise(self.z_raise):
                self.move_to_tray()
                self.tray_eject()


def load_config(config):
    return MMSPurge(config)
