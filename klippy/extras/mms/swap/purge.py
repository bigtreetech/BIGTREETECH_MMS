# Support for MMS Purge
#
# Copyright (C) 2025-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, fields

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    printer_adapter,
    toolhead_adapter,
    virtual_sdcard_adapter,
)
from ..core.config import (
    OptionalField,
    OptionalPoint,
    PointType,
    PrinterConfig
)
from ..core.exceptions import MMSException, PurgeFailedError
from ..core.logger import log_time_cost


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
    purge_speed: float = 300.0

    # Runout E-travel limits (mm). Independent of Bowden length to allow slip.
    runout_max_distance: float = 5000.0
    # Distance-based fallback for machines without Entry.
    runout_distance: float = 2000.0
    # Optional mechanical cleaning before a fresh extrusion sequence.
    # No E moves; must finish at tray_point. Disabled by default.
    pre_extrude_gcode: OptionalField = ""
    # Optional mechanical cleaning/ejection after each runout segment.
    runout_chunk_gcode: OptionalField = ""

    # Ordinary pause/recovery (mm, mm/min). Zero distance disables the
    # respective extrusion; existing swap purges use their own parameters.
    pause_retract_distance: float = 0.0
    pause_retract_speed: float = 1800.0
    resume_purge_distance: float = 0.0
    # Extra filament after XY return on ordinary RESUME, before lowering Z.
    # Independent of retraction debt; uses repay_speed. Unit: mm.
    resume_extra_extrude_distance: float = 0.0

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
    # Speed when repaying purge retraction; defaults to retract_speed.
    # Unit: mm/min
    repay_speed: float = retract_speed

    # # Priming distance
    # # Unit: mm
    # nozzle_priming_dist: float = 20.0
    # # Priming speed
    # # Unit: mm/min
    # nozzle_priming_speed: float = 600.0

    # # Pressure pulse cleaning
    # # 0 = disable, 1 = enable
    # pulse_clean_enable: int = 0
    # # Unit: second
    # pulse_rest_time: float = 0.1
    # pulse_count: int = 4
    # # Unit: mm/min
    # pulse_speed: float = 1200
    # # Unit: mm
    # retract_dist: float = 10
    # # Unit: mm
    # extrude_dist: float = retract_dist * 0.5

    # Tray
    # Order to process axis from tray_point.
    # Options: 'X', 'Y', 'XY'.
    axis_first: str = "Y"
    # X/Y coordinates of the purge tray location
    tray_point: PointType = "(60.0, 100.0)"
    # X/Y coordinates for ejecting purged filament from tray
    eject_point: OptionalPoint = "(60.0, 100.0)"

    # Custom Macro
    custom_before: OptionalField = "MMS_PURGE_CUSTOM_BEFORE"
    custom_after: OptionalField = "MMS_PURGE_CUSTOM_AFTER"

    def get_type_handlers(self):
        handlers = super().get_type_handlers()
        def get_float(config, name):
            if name == "repay_speed":
                retract_speed = config.getfloat("retract_speed", above=0.)
                return config.getfloat(name, retract_speed, above=0.)
            if name in ("runout_max_distance", "runout_distance",
                        "pause_retract_speed"):
                return config.getfloat(
                    name, getattr(type(self), name), above=0.0
                )
            if name in ("pause_retract_distance", "resume_purge_distance",
                        "resume_extra_extrude_distance"):
                return config.getfloat(
                    name, getattr(type(self), name), minval=0.0
                )
            return config.getfloat(name)

        handlers[float] = get_float
        return handlers


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
        # Accepted E displacement still owed after the last purge, in mm.
        self._retraction_debt = 0.0

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
        self.mms_fil_detection = self.mms.get_mms_filament_detection()

    def _initialize_gcode(self):
        commands = [
            ("MMS_PURGE", self.cmd_MMS_PURGE),
            ("MMS_REPAY_RETRACT", self.cmd_MMS_REPAY_RETRACT),
            ("MMS_CLEAR_RETRACT", self.cmd_MMS_CLEAR_RETRACT),

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
            yield self.mms.get_mms_resume().get_status()["generation"]
        finally:
            self._is_running = False

    def get_purge_speed(self):
        return self.purge_speed

    def get_purge_distance(self):
        return self.orphan_filament_length * self.purge_modifier

    def get_status(self, eventtime=None):
        return {
            "tray_point_x": self.tray_point[0],
            "tray_point_y": self.tray_point[1],
            "retraction_debt": self._retraction_debt,
        }

    def clear_retract(self):
        """Abandon compensation after unloading, replacement or cancellation."""
        self._retraction_debt = 0.0

    def _check_generation(self, generation):
        if generation != self.mms.get_mms_resume().get_status()["generation"]:
            raise PurgeFailedError("MMS purge retraction was cancelled")

    def _set_retraction_debt(self, distance, generation):
        # G1 may yield internally. A cancelled call must not revive its debt.
        self._check_generation(generation)
        self._retraction_debt = distance

    def _retract(self, distance, generation, speed=None, slot_num=None,
                 work=None):
        self._check_generation(generation)
        if work is not None:
            distance = work.setdefault("retract_remaining", distance)
        if distance <= 0.:
            return

        def accepted(amount):
            self._set_retraction_debt(self._retraction_debt - amount,
                                      generation)
            if work is not None:
                work["retract_remaining"] = max(
                    0., work["retract_remaining"] + amount)

        options = dict(
            check=lambda: self._check_generation(generation),
            on_segment_accepted=accepted)
        speed = self.retract_speed if speed is None else speed
        if slot_num is None:
            extruder_adapter.move_filament(-distance, speed, **options)
        else:
            self.mms_delivery.move_with_buffer(
                slot_num, -distance, speed, **options)
        self._check_generation(generation)

    def _motion_slot(self):
        resume = self.mms.get_mms_resume()
        slot_num = resume.get_exhausted_slot()
        if slot_num is None or not resume.is_tail_available(slot_num):
            slot_num = self.mms.get_current_slot()
        if slot_num is None:
            raise PurgeFailedError("filament motion has no known source")
        return slot_num

    def repay_retract(self, free_tail=False, *, extra_distance=0.,
                      on_extra_remaining=None):
        distance = self._retraction_debt + extra_distance
        if distance <= 0.:
            return
        generation = self.mms.get_mms_resume().get_status()["generation"]
        remaining = distance

        def accepted(amount):
            nonlocal remaining
            self._set_retraction_debt(max(0., self._retraction_debt - amount),
                                      generation)
            if on_extra_remaining is not None:
                # The retract is repaid first. The extra occupies the last
                # part of this same E move, including on partial acceptance.
                remaining = max(0., remaining - amount)
                on_extra_remaining(min(extra_distance, remaining))

        options = dict(
            check=lambda: self._check_generation(generation),
            on_segment_accepted=accepted)
        if free_tail:
            extruder_adapter.move_filament(distance, self.repay_speed,
                                           **options)
        else:
            slot_num = self._motion_slot()
            self.mms.get_mms_slot(slot_num).get_mms_buffer().check_feed(
                slot_num)
            self._check_generation(generation)
            self.mms_delivery.move_with_buffer(
                slot_num, distance, self.repay_speed, **options)
        self._check_generation(generation)

    def retract_for_pause(self, work=None):
        generation = self.mms.get_mms_resume().get_status()["generation"]
        slot_num = self._motion_slot()
        slot = self.mms.get_mms_slot(slot_num)
        slot.get_mms_buffer().check_feed(slot_num)
        self._check_generation(generation)
        self._retract(self.pause_retract_distance, generation,
                      speed=self.pause_retract_speed, slot_num=slot_num,
                      work=work)

    def prepare_extrusion(self):
        """Remove hanging waste without changing the caller's E/debt state."""
        if not self.pre_extrude_gcode:
            return
        generation = self.mms.get_mms_resume().get_status()["generation"]
        toolhead_adapter.check_motion()
        gcode_adapter.run_command(self.pre_extrude_gcode, strict=True)
        self._check_generation(generation)
        toolhead_adapter.check_motion()

    def purge_for_resume(self, prepare=True, work=None):
        """Short ordinary/reload purge; the caller owns parking and checks."""
        if work is None:
            work = {}
        slot_num = self._motion_slot()
        generation = self.mms.get_mms_resume().get_status()["generation"]
        self._check_generation(generation)
        self.mms.get_mms_slot(slot_num).get_mms_buffer().prepare_feed(slot_num)
        self._check_generation(generation)
        with toolhead_adapter.temporary_fan_speed(0.):
            toolhead_adapter.check_motion()
            if work.get("phase", "extrude") == "extrude":
                if self.custom_before:
                    gcode_adapter.run_command(self.custom_before, strict=True)
                toolhead_adapter.check_motion()
                if prepare:
                    self.prepare_extrusion()
                self.repay_retract()
                self._extrude_remaining(
                    slot_num, self.resume_purge_distance,
                    self.purge_speed, generation, work)
                work["phase"] = "retract"
            if work["phase"] == "retract":
                self.retract_for_pause(work)
                work["phase"] = "after"
            if work["phase"] == "after":
                if self.custom_after:
                    gcode_adapter.run_command(self.custom_after, strict=True)
                work["phase"] = "done"
            toolhead_adapter.check_motion()

    def runout_chunk_end(self, retract):
        """Relieve pressure, then run the machine's segment cleaning action."""
        generation = self.mms.get_mms_resume().get_status()["generation"]
        toolhead_adapter.check_motion()
        if retract:
            # The feeder is released; use the configured E-only relief.
            self._retract(self.retraction_compensation - self._retraction_debt,
                          generation)
        if self.runout_chunk_gcode:
            gcode_adapter.run_command(self.runout_chunk_gcode, strict=True)
        else:
            self.tray_eject()
        self._check_generation(generation)
        toolhead_adapter.check_motion()

    # ---- Tray ----
    def move_to_tray(self):
        """Move toolhead to tray point with configured axis order."""
        axis_first = self.axis_first.strip().upper()
        tray_x, tray_y = self.tray_point[:2]
        if axis_first == "X":
            toolhead_adapter.move_x(tray_x, wait_toolhead=True)
            toolhead_adapter.move_y(tray_y, wait_toolhead=True)
        elif axis_first == "XY":
            toolhead_adapter.move_xy(tray_x, tray_y, wait_toolhead=True)
        else:
            # Default move Y-axis first
            toolhead_adapter.move_y(tray_y, wait_toolhead=True)
            toolhead_adapter.move_x(tray_x, wait_toolhead=True)

    def _can_tray_eject(self):
        return self.eject_point is not None

    def tray_eject(self):
        if not self._can_tray_eject():
            return
        # Toolhead move to eject point to eject the blob,
        # if eject point is set
        toolhead_adapter.move_xy(
            position_x = self.eject_point[0],
            position_y = self.eject_point[1],
            wait_toolhead = True
        )

    def _purge_check(self, slot_num, generation):
        self._check_generation(generation)
        self.mms_fil_detection.check(slot_num)
        toolhead_adapter.check_motion()

    def apply_retraction_compensation(self, slot_num, generation=None,
                                      distance=None, speed=None, work=None):
        """Extruder retract a little bit to decrease nozzle remain."""
        if generation is None:
            generation = self.mms.get_mms_resume().get_status()["generation"]
        mms_buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()
        mms_buffer.prepare_feed(slot_num)
        log_prefix = f"slot[{slot_num}] apply retraction compensation"
        if self.mms_fil_detection.is_activating():
            self.mms_fil_detection.check(slot_num)

        distance = min(
            abs(self.retraction_compensation if distance is None else distance),
            mms_buffer.get_spring_stroke()
        )
        self._retract(distance, generation, speed, slot_num=slot_num,
                      work=work)
        self.log_info_s(f"{log_prefix}, distance: {distance} mm")

    # ---- Cold pull ----
    # ---- Bussiness methods ----
    def _safety_checks(self, slot_num):
        if slot_num is None:
            raise PurgeFailedError("purge has no identified source slot")
        if self.is_running():
            raise PurgeFailedError("another MMS purge is running")
        if not toolhead_adapter.is_homed():
            raise PurgeFailedError("home XYZ before purging")
        if not extruder_adapter.is_hot_enough():
            raise PurgeFailedError("heat the nozzle before purging")

    def _exec_custom_macro(self, macro, position):
        if macro:
            self.log_info(
                f"MMS execute macro {position} PURGE:\n"
                f"{macro}"
            )
            gcode_adapter.run_command(macro, strict=True)

    def _extrude_remaining(self, slot_num, distance, speed, generation, work):
        distance = work.setdefault("purge_remaining", distance)
        if distance <= 0.:
            return

        def accepted(amount):
            if self._retraction_debt:
                self._set_retraction_debt(
                    max(0., self._retraction_debt - amount), generation)
            work["purge_remaining"] = max(0., work["purge_remaining"] - amount)

        self.mms_delivery.move_with_buffer(
            slot_num, distance, speed,
            check=lambda: self._purge_check(slot_num, generation),
            on_segment_accepted=accepted)

    def mms_purge(self, prepare=True, distance=None, speed=None,
                  retract=None, retract_speed=None, work=None):
        if work is None:
            work = {}
        if work.get("phase", "extrude") == "extrude":
            self._exec_custom_macro(self.custom_before, "before")
        resume = self.mms.get_mms_resume()
        slot_num = resume.get_exhausted_slot()
        if slot_num is None or not resume.is_tail_available(slot_num):
            slot_num = self.mms.get_current_slot()
        self._safety_checks(slot_num)

        log_prefix = f"slot[{slot_num}] mms purge"
        self.log_info_s(f"{log_prefix} begin")
        context = (self.mms_fil_detection.monitor(slot_num, configured=True)
                   if self.is_enabled() else nullcontext())
        with self._purge_is_running() as generation, context, \
                toolhead_adapter.temporary_fan_speed(0.):
            if work.get("phase", "extrude") == "extrude":
                if self.is_enabled():
                    self._purge_check(slot_num, generation)
                    self.move_to_tray()
                    self._purge_check(slot_num, generation)
                    buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()
                    buffer.prepare_feed(slot_num)
                    if prepare:
                        self.prepare_extrusion()
                        self._purge_check(slot_num, generation)
                    self._extrude_remaining(
                        slot_num, self.get_purge_distance()
                        if distance is None else distance,
                        self.get_purge_speed() if speed is None else speed,
                        generation, work)
                    self._purge_check(slot_num, generation)
                work["phase"] = "retract"
            if work["phase"] == "retract":
                self.apply_retraction_compensation(
                    slot_num, generation, retract, retract_speed, work)
                work["phase"] = "cool"
            if work["phase"] == "cool":
                with toolhead_adapter.fan_cooldown(
                        speed=self.fan_cooldown_speed,
                        wait=self.fan_cooldown_wait):
                    pass
                work["phase"] = "eject"
            if work["phase"] == "eject":
                if self.is_enabled():
                    self._purge_check(slot_num, generation)
                    self.tray_eject()
                work["phase"] = "after"

        if work["phase"] == "after":
            self._exec_custom_macro(self.custom_after, "after")
            work["phase"] = "done"
        gcode_adapter.respond_echo(f"{log_prefix} finish")
        self.log_info_s(f"{log_prefix} finish")
        return True

    # ---- GCode ----
    def cmd_MMS_REPAY_RETRACT(self, gcmd):
        self.mms.check_maintenance(gcmd)
        try:
            with toolhead_adapter.motion_scope():
                if self._retraction_debt > 0.:
                    slot_num = self._motion_slot()
                    resume = self.mms.get_mms_resume()
                    slot = resume.check_loaded_source(slot_num)
                    buffer = slot.get_mms_buffer()
                    buffer.prepare_feed(slot_num)
                    resume.check_loaded_source(slot_num)
                self.repay_retract()
        except MMSException as e:
            raise gcmd.error(str(e))

    def cmd_MMS_CLEAR_RETRACT(self, gcmd):
        self.mms.check_maintenance(gcmd)
        self.clear_retract()

    @log_time_cost("log_info_s")
    def cmd_MMS_PURGE(self, gcmd):
        # A file may purge at PRINT_START. Console maintenance still cannot
        # interrupt a print or a pending recovery. Purge uses normal feeding.
        from_sd = virtual_sdcard_adapter.is_cmd_from_sd()
        self.mms.check_maintenance(gcmd, allow_printing=from_sd)
        distance = gcmd.get_float("DISTANCE", None, minval=0.)
        speed = gcmd.get_float("SPEED", None, above=0.)
        retract = gcmd.get_float("RETRACT", None, minval=0.)
        retract_speed = gcmd.get_float("RETRACT_SPEED", None, above=0.)
        try:
            resume = self.mms.get_mms_resume()
            slot_num = resume.get_exhausted_slot()
            if slot_num is None or not resume.is_tail_available(slot_num):
                slot_num = self.mms.get_current_slot()
            slot = resume.check_loaded_source(slot_num)
            buffer = slot.get_mms_buffer()
            generation = resume.get_status()["generation"]
            with toolhead_adapter.motion_scope(z_raise=self.z_raise) as state:
                self.mms_purge(distance=distance, speed=speed,
                               retract=retract, retract_speed=retract_speed)
                buffer.check_feed(slot_num)
                toolhead_adapter.return_to_saved(state, clearance=self.z_raise)
                self._check_generation(generation)
                buffer.check_feed(slot_num)
        except Exception as e:
            raise gcmd.error(f"MMS_PURGE failed: {e}") from e


    @log_time_cost("log_info_s")
    def cmd_MMS_TRAY(self, gcmd):
        self.mms.check_maintenance(gcmd)
        with toolhead_adapter.motion_scope(z_raise=self.z_raise) as state:
            self.move_to_tray()
            toolhead_adapter.move_z(
                state["position"]["z"], wait_toolhead=True)

    def cmd_MMS_TRAY_EJECT(self, gcmd):
        self.mms.check_maintenance(gcmd)
        if not self._can_tray_eject():
            raise gcmd.error("MMS_TRAY_EJECT requires eject_point")
        with toolhead_adapter.motion_scope(z_raise=self.z_raise) as state:
            self.move_to_tray()
            self.tray_eject()
            toolhead_adapter.return_to_saved(state, clearance=self.z_raise)


def load_config(config):
    return MMSPurge(config)
