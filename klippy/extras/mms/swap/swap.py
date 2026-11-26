# Support for MMS Swap
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, fields

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    pause_resume_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter,
    virtual_sdcard_adapter,
)
from ..core.config import OptionalField, PrinterConfig
from ..core.exceptions import (
    EjectFailedError,
    SwapFailedSignal,
    SwapRunoutSignal
)
from ..core.logger import log_time_cost


@dataclass(frozen=True)
class PrinterSwapConfig(PrinterConfig):
    # Enable/disable the swap module
    # 0 = disable, 1 = enable
    # When disabled, all filament mms swap operations will be skipped
    # If "custom_macro" is set, that macro will be executed
    # instead of automatic swapping
    enable: int = 1

    # Custom macro to execute when enable=0
    # This setting has no effect when enable=1
    # custom_macro: str = "SWAP_CUSTOM_MACRO"

    # Z-axis lift distance during filament swap operations
    # Unit: mm
    z_raise: float = 1.0

    # Filament Swap Command Configuration
    # Command string used for filament slot selection
    # For example, with a 4-slot system:
    # "T" ==> "T0" to "T3" where "T0" means "swap current filament to slot 0"
    command_string: str = "T"

    # Safe Mode Settings
    # Enable/disable safe mode for toolhead movements
    # 0 = disabled, 1 = enabled
    # When enabled, inserts M400 (wait for move completion)
    # after each G1 movement
    # This slows down operations but increases safety
    # by ensuring moves complete
    safe_mode: int = 0

    # Toolhead Movement Speed
    # Speed for toolhead during all MMS Swap operations
    # (brush, charge, cut, eject, purge, swap)
    # Unit: mm/min
    toolhead_move_speed: float = 24000.0
    # Maintenance acceleration (mm/s^2); unset uses the printer's limit.
    toolhead_move_accel: float = None

    # Custom Macro
    custom_before: OptionalField = "MMS_SWAP_CUSTOM_BEFORE"
    custom_after: OptionalField = "MMS_SWAP_CUSTOM_AFTER"

    def get_type_handlers(self):
        handlers = super().get_type_handlers()
        handlers[float] = lambda config, name: (
            config.getfloat(
                name,
                config.getsection("printer").getfloat("max_accel"),
                above=0.0,
            )
            if name == "toolhead_move_accel"
            else config.getfloat(name)
        )
        return handlers


class MMSSwap:
    def __init__(self, config):
        p_swap_config = PrinterSwapConfig(config)
        for field in fields(p_swap_config):
            key = field.name
            # Don't cover
            if not p_swap_config.should_skip(key) \
                and not hasattr(self, key):
                val = getattr(p_swap_config, key)
                setattr(self, key, val)

        # State tracking
        self._is_running = False
        self._slot_num_to = None
        self._last_tool_print_start = None

        # Active mapping and one explicit preset for another file.
        self.mapping = None
        self._mapping_preset = None

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    # ---- Initial ----
    def _handle_klippy_ready(self):
        # Notice: the sequence of functions
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()
        self._initialize_adapters()
        self._initialize_mapping()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()
        self.mms_brush = printer_adapter.get_mms_brush()
        self.mms_cut = printer_adapter.get_mms_cut()
        self.mms_charge = printer_adapter.get_mms_charge()
        self.mms_eject = printer_adapter.get_mms_eject()
        self.mms_purge = printer_adapter.get_mms_purge()

        # Get objects from MMS
        self.mms_pause = self.mms.get_mms_pause()
        self.mms_resume = self.mms.get_mms_resume()
        self.mms_endless_spool = self.mms.get_mms_endless_spool()

        print_observer = self.mms.get_print_observer()
        print_observer.register_start_callback(
            self._init_mapping_filename
        )
        print_observer.register_finish_callback(
            self._reset_mapping
        )

    def _initialize_gcode(self):
        # Dynamic register swap action command
        # For example, "T*"
        for slot_num in self.mms.get_slot_nums():
            gcode_adapter.register(
                command = f"{self.command_string}{slot_num}",
                handler = self.cmd_SWAP
            )

        # GCode mapping and register
        commands = [
            ("MMS_SWAP_MAPPING", self.cmd_MMS_SWAP_MAPPING),
            ("MMS_SWAP_MAPPING_RESET", self.cmd_MMS_SWAP_MAPPING_RESET),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        # Log would not output to console
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    def _initialize_adapters(self):
        toolhead_adapter.set_move_speed(
            self.toolhead_move_speed)
        if self.safe_mode:
            toolhead_adapter.enable_safe_mode()

    def _initialize_mapping(self, keep_active=False):
        # Initial mapping for swap_num and slot_num
        # {"filename":"xxx.gcode", swap_num : slot_num, ...}
        self.mapping = {n:n for n in self.mms.get_slot_nums()}
        self.mapping["filename"] = None
        self._mapping_roots = {n:n for n in self.mms.get_slot_nums()}
        # Last accepted logical tool and its physical strand.
        if not keep_active:
            self._active_tool = None

    def _reset_mapping(self):
        # A preset for the next file is not owned by the finishing job.
        if self.mapping.get("filename") != print_stats_adapter.get_filename():
            return
        self.log_info_s(
            f"reset current swap mapping:{self.mapping} to default")
        self._initialize_mapping()

    def _select_mapping_file(self):
        """Select this file's table; pending presets contain only overrides."""
        filename = print_stats_adapter.get_filename()
        preset = self._mapping_preset
        if preset is not None and preset["filename"] == filename:
            self._initialize_mapping()
            self.mapping["filename"] = filename
            for tool, target in preset.items():
                if tool != "filename":
                    self.update_mapping_slot_num(tool, target)
            self._mapping_preset = None
        elif self.mapping.get("filename") not in (None, filename):
            self._initialize_mapping()
        return filename

    def _init_mapping_filename(self):
        filename = self._select_mapping_file()
        # Idle swaps are presets for the next print. print_stats may still
        # report the previous file, which must not own these substitutions.
        if self.mapping.get("filename") is None and (
            print_stats_adapter.is_printing() or print_stats_adapter.is_paused()
        ):
            self.log_info_s(
                f"initialize current swap mapping:{self.mapping} "
                f"filename to '{filename}'"
            )
            self.mapping["filename"] = filename

    def get_command_string(self):
        return self.command_string

    # ---- Status ----
    def is_enabled(self):
        return bool(self.enable)

    def is_running(self):
        return self._is_running

    @contextmanager
    def _swap_is_running(self):
        self._is_running = True
        try:
            yield
        finally:
            self._is_running = False

    def get_status(self, eventtime=None):
        return {
            "slot_num_to": self._slot_num_to,
            "is_running": self._is_running,
            "mapping": self.mapping
        }

    # ---- MMS Buffer control ----
    # ---- Swap ----
    def _safety_checks(self, slot_num_from, slot_num_to):
        # Notice: slot_num_from could be None
        # Think about both Pin:selector
        # and Stepper_Selector:focus_slot are None

        if slot_num_to is None:
            self.log_warning("target slot is None, return")
            return False

        if self.is_running():
            self.log_warning("another swap is running, return")
            return False

        # Check toolhead
        if not toolhead_adapter.is_homed():
            self.log_warning("toolhead is not homed, return")
            return False

        # Check extruder
        if not extruder_adapter.is_hot_enough():
            return False

        # Check slot inlet
        if (not self.mms.get_mms_slot(slot_num_to).is_ready()
                and not self.mms_resume.is_tail_available(slot_num_to)):
            self.log_warning(
                f"slot[{slot_num_to}] inlet is not triggered, swap failed")
            return False

        return True

    def _swap_no_blocking(self, skip_slot_num=None):
        # Check Buffers are no blocking after before charge
        # Filament Fracture may cause skip in eject
        for mms_slot in self.mms.get_mms_slots():
            if mms_slot.gate.is_triggered():
                slot_num = mms_slot.get_num()

                if skip_slot_num is not None \
                    and slot_num == skip_slot_num:
                    continue

                msg = f"slot[{slot_num}] gate is triggering before charge"
                raise EjectFailedError(msg, mms_slot)

    def _exec_custom_macro(self, macro, position):
        if macro:
            self.log_info(
                f"MMS execute macro {position} SWAP:\n"
                f"{macro}"
            )
            gcode_adapter.run_command(macro, strict=True)

    def check_source_path(self, gcmd, target, runout_source=None):
        """Validate after request capture, so any repair retains this T."""
        # Refilling Inlet cannot reconnect a known old tail. A real swap
        # handles that source before deciding whether its target needs fallback.
        charged = self.mms_charge.get_charged_slot()
        if (charged is not None
                and self.mms.get_mms_slot(charged).inlet.is_released()):
            self.mms_resume.handle_inlet_runout(charged)
        # An accepted destination needs no source retirement. This also holds
        # when a same-slot monitor restart or selector repair needs the owner.
        if self._has_loaded_path(target, self.mms_resume.get_status()):
            return
        source = self.mms_resume.get_exhausted_slot()
        if source is not None:
            if self.mms_endless_spool.can_clear_runout(source):
                try:
                    self.mms_eject.check_paths(exhausted_source=source)
                except EjectFailedError as e:
                    self.mms_resume.require_filament_clearance(target, str(e))
                    raise SwapFailedSignal(str(e)) from e
                if source == runout_source:
                    return
                raise SwapRunoutSignal(source)
            reason = (
                f"slot[{source}] exhausted; clear residual filament, "
                f"refill target slot[{target}] Inlet, then RESUME"
            )
            self.mms_resume.require_filament_clearance(target, reason)
            raise SwapFailedSignal(reason)
        try:
            self.mms_eject.check_paths()
        except EjectFailedError as e:
            self.mms_resume.require_filament_clearance(target, str(e))
            raise SwapFailedSignal(str(e)) from e
        if not self.mms_endless_spool.is_enabled():
            slot = self.mms.get_mms_slot(target)
            slot.check_load_allowed()
            if not slot.is_ready():
                raise SwapFailedSignal(f"target slot[{target}] Inlet is empty; "
                                       "refill this Inlet and retry RESUME")

    def _load_swap_filament(self, slot_num_from, slot_num_to, gcmd):
        # The pending request owns these milestones. Retrying a
        # failed brush must not refill or purge already-clean filament.
        fresh_charge = self.mms_resume.get_status()["stage"] in (
            "charge", "feed")
        if fresh_charge:
            self.check_source_path(gcmd, slot_num_to)
            self.mms_resume.check_swap_request(gcmd)
        precleaned = False
        if fresh_charge or self.mms_resume.get_status()["stage"] in (
                "fill", "charge_check"):
            if fresh_charge:
                if not self.mms_eject.prepare_charge(slot_num_to):
                    raise SwapFailedSignal(
                        f"slot[{slot_num_from}] eject failed")
                self.mms_resume.check_swap_request(gcmd)
                self._swap_no_blocking(skip_slot_num=slot_num_to)
            else:
                self.mms_eject.release_other_selectors(slot_num_to)
            precleaned = self.mms_charge.load_filament(
                slot_num_to, progress=self.mms_resume)
            self.mms_resume.advance_stage("purge")
        elif self.mms_resume.get_status()["stage"] in ("purge", "brush"):
            # Repair releases the selector without discarding accepted load
            # progress. Reclaim this same source before any later-stage E.
            self.mms_resume.check_loaded_source(slot_num_to, prepare=True)
            self.mms_eject.select_loaded_slot(slot_num_to)
            self.mms_resume.check_swap_request(gcmd)
        if self.mms_resume.get_status()["stage"] == "purge":
            # Reuse cleaning done by this load attempt. Resumed purge or
            # acceptance-only recovery must clean before their first E.
            self.mms_purge.mms_purge(
                prepare=not precleaned, work=self.mms_resume.stage_work)
            self.mms_resume.advance_stage("brush")

    def mms_swap(self, slot_num_to, gcmd):
        select_only = self.mms_resume.get_status()["stage"] == "select"
        if self.mms_resume.get_status()["stage"] in (
            "select", "charge", "feed"
        ):
            self.check_source_path(gcmd, slot_num_to)
        # Eject owns whether this is a fresh cut or an unfinished withdrawal.
        # A completed cut is checked for release after full ejection.
        self.mms_eject.check_cutter_ready()
        self.mms_resume.check_swap_request(gcmd)
        if not select_only:
            self._exec_custom_macro(self.custom_before, "before")
            self.mms_resume.check_swap_request(gcmd)

        if self.mms_resume.get_status()["stage"] in ("charge", "feed"):
            self.check_source_path(gcmd, slot_num_to)
            slot_num_to = self._resolve_target(gcmd, slot_num_to)
        slot_num_from = self.mms.get_current_slot()
        loading_slots = self.mms.get_loading_slots()
        log_prefix = f"slot[{slot_num_from}] to slot[{slot_num_to}] swap"

        if select_only:
            self.check_source_path(gcmd, slot_num_to)
            if not self._has_loaded_path(
                    slot_num_to, self.mms_resume.get_status()):
                raise SwapFailedSignal("accepted filament path changed")
        elif not self._safety_checks(slot_num_from, slot_num_to):
            raise SwapFailedSignal("safety checks failed")

        self.log_info_s(f"{log_prefix} begin")
        self.log_info_s(
            f"{log_prefix} determine loading slots: {loading_slots}"
        )

        fan_scope = (nullcontext() if select_only
                     else toolhead_adapter.temporary_fan_speed(0.))
        with self._swap_is_running(), fan_scope:
            if self.mms_delivery.has_pending_print_tail_release():
                self.mms_resume.park_for_recovery()
            # Stop each feed owner once, including an unchanged source.
            self.mms_delivery.pause_feeds(self.mms.get_slot_nums())
            mms_buffer_to = self.mms.get_mms_slot(slot_num_to).get_mms_buffer()
            self.mms_resume.check_swap_request(gcmd)

            self.mms_endless_spool.deactivate()
            if select_only:
                self.mms_eject.select_loaded_slot(slot_num_to)
                self.mms_resume.check_swap_request(gcmd)
                self.check_source_path(gcmd, slot_num_to)
                if not self._has_loaded_path(
                        slot_num_to, self.mms_resume.get_status()):
                    raise SwapFailedSignal("accepted filament path changed")
            else:
                self._load_swap_filament(
                    slot_num_from, slot_num_to, gcmd)
            # Establish working reserve before final cleaning. A completed
            # purge already owns the same controller; retries restart it here.
            mms_buffer_to.prepare_feed(slot_num_to)
            self.mms_resume.check_swap_request(gcmd)
            # Every real RESUME cleans before returning to the print.
            # A successful selector-only T does not move XYZ or extrude.
            if not select_only or self.mms_resume.is_resuming():
                if not self.mms_brush.mms_brush():
                    raise SwapFailedSignal(f"slot[{slot_num_to}] brush failed")

        self.mms_resume.check_swap_request(gcmd)
        # Actual swaps retain strict product acceptance after cleaning.
        if not select_only:
            self._exec_custom_macro(self.custom_after, "after")
            self.mms_resume.check_swap_request(gcmd)
        self.mms_delivery.release_tail(slot_num_to)
        self.mms_resume.check_swap_request(gcmd)
        mms_buffer_to.check_feed(slot_num_to)
        self.mms_endless_spool.activate()
        self.mms_resume.check_swap_request(gcmd)
        self.log_info_s(f"{log_prefix} finish")
        return True

    def _handle_swap_failure(self, gcmd, msg):
        cmd = gcmd.get_command().strip()
        if not self.mms_resume.is_current_swap(gcmd):
            self.log_info_s(f"'{cmd}' failure belongs to an expired request")
            return

        self.mms_resume.note_failure(msg)
        # The request was registered before any movement. A failure keeps it
        # for RESUME, but cannot recreate a request invalidated by cancellation.
        log_msg = f"'{cmd}' failed: {msg},"

        self.log_warning(f"{log_msg} pause...")
        # Manual swaps need the same pause context and retry path as file swaps.
        pause_resume_adapter.send_pause_command()
        # Late completion can fail after monitoring has already started. Stop
        # feeding before the user clears residuals; either stop may fail, but
        # neither may prevent PAUSE or resurrect a cancelled request.
        if not self.mms_resume.is_current_swap(gcmd):
            return
        stopped = set()
        for slot in self.mms.get_mms_slots():
            # Source validation can fail before its buffer was handed off.
            # Stop every distinct feed owner, not just the requested target.
            buffer = slot.get_mms_buffer()
            drive = slot.get_mms_drive()
            for name, owner, stop in (
                (
                    "buffer",
                    buffer,
                    lambda b=buffer: b.deactivate_monitor(wait=False),
                ),
                ("drive", drive, drive.terminate_moving),
            ):
                if id(owner) in stopped:
                    continue
                stopped.add(id(owner))
                if not self.mms_resume.is_current_swap(gcmd):
                    return
                try:
                    stop()
                except Exception as e:
                    self.log_error(f"'{cmd}' {name} stop failed: {e}")
        if not self.mms_resume.is_current_swap(gcmd):
            return
        try:
            self.mms_pause.mms_pause(force=True, swap_gcmd=gcmd)
        except Exception as e:
            self.log_error(f"'{cmd}' pause macro failed: {e}")
        if self.mms_resume.is_current_swap(gcmd):
            try:
                self.mms_resume.release_for_manual()
            except Exception as e:
                self.log_error(f"'{cmd}' selector release failed: {e}")

    # ---- T* ----
    def _parse_slot(self, command):
        """Parse slot number from command."""
        slot_str = command.removeprefix(self.command_string)
        return int(slot_str) if slot_str.isdigit() else None

    def format_command(self, slot_num):
        """Format command with slot number."""
        return f"{self.command_string}{slot_num}"

    def _has_loaded_path(self, slot_num, recovery):
        """Accepted filament is independent of selector and XYZ/E readiness."""
        if (self.mms_charge.get_charged_slot() != slot_num
                or recovery["clear_required"]):
            return False
        slot = self.mms.get_mms_slot(slot_num)
        if any(s.gate.is_triggered() and not s.inlet.is_triggered()
               for s in self.mms.get_mms_slots() if s.get_num() != slot_num):
            return False
        if recovery["exhausted_slot"] is not None:
            # Other intact upstream strands can retire independently.
            return self.mms_resume.is_tail_available(slot_num)
        return (slot.inlet.is_triggered() and slot.gate.is_triggered()
                and (not slot.entry.is_set() or slot.entry.is_triggered()))

    def _can_keep_loaded_slot(self, slot_num, recovery):
        """No XYZ/E motion needs no homing or extrusion temperature check."""
        if recovery["kind"] is not None \
                or not self._has_loaded_path(slot_num, recovery):
            return False
        slot = self.mms.get_mms_slot(slot_num)
        if any(s.get_num() != slot_num and s.gate.is_triggered()
               for s in self.mms.get_mms_slots()):
            return False
        if self.mms_cut.get_status()["cutter_sensor_state"] == "PRESSED":
            return False
        if (self.mms_resume.is_tail_available(slot_num)
                and not self.mms_resume.can_drive_filament(slot_num)):
            selected = self.mms.get_selecting_slots()
            return not selected or (
                selected == [slot_num]
                and slot.get_mms_buffer().tail_release_pending(slot_num))
        buffer = slot.get_mms_buffer()
        volume = buffer.get_volume()
        return (self.mms_delivery.is_exclusively_selected(slot_num)
                and volume is not None
                and (volume > 0. or buffer.is_activating()))

    @log_time_cost("log_info_s")
    def cmd_SWAP(self, gcmd):
        """Accept a new tool request; retries use the frozen physical target."""
        # Disabled tool commands are no-ops. Decide before capturing a
        # snapshot or registering recovery; no stage is completed by skipping.
        if not self.is_enabled():
            self.log_info_s("MMS SWAP is disabled, skip...")
            return True
        cmd = gcmd.get_command().strip()
        from_sd = virtual_sdcard_adapter.is_cmd_from_sd()

        if self.mms_eject.finish_is_active():
            raise gcmd.error(
                "MMS print finish is active; wait before changing tools"
            )
        if self.mms.printer_is_shutdown():
            raise gcmd.error(
                f"'{cmd}' cannot execute while printer is shutdown"
            )

        # Reconcile only an accepted source before choosing a target. A sensor
        # callback may not have recorded its tail yet; selector position alone
        # cannot establish that an unknown strand was loaded successfully.
        charged = self.mms_charge.get_charged_slot()
        if (charged is not None
                and self.mms.get_mms_slot(charged).inlet.is_released()):
            self.mms_resume.handle_inlet_runout(charged)
        recovery = self.mms_resume.get_status()
        print_job = (from_sd or recovery["print_job"]
                     or print_stats_adapter.is_printing()
                     or print_stats_adapter.is_paused())
        # The file may already be executing a T wrapper when Inlet empties
        # during its lift/M400. That consumed T must become the retry target,
        # rather than being skipped or raising into virtual_sd on_error.
        replace_runout = (
            from_sd
            and recovery.get("kind") == "reload"
            and recovery["clear_required"]
            and not recovery["prepared"]
            and not pause_resume_adapter.is_paused()
            and not self.mms_resume.is_resuming()
            and self._slot_num_to is None
        )
        if self._slot_num_to is not None \
            or recovery["pending"] and not replace_runout \
            or self.mms_resume.is_resuming() \
            or pause_resume_adapter.is_paused():
            if (recovery["return_position"] is not None
                    and not recovery["return_valid"]):
                msg = f"'{cmd}' blocked: {recovery['reason']}"
            elif print_job:
                msg = (
                    f"'{cmd}' blocked: the print is paused "
                    "or a tool change is pending. "
                    "Use RESUME to continue, "
                    "or CANCEL_PRINT to abandon the print."
                )
            else:
                msg = (
                    f"'{cmd}' blocked: a manual tool change "
                    "or pause is pending. "
                    "Use RESUME to continue, or CLEAR_PAUSE to discard it "
                    "before selecting another tool. "
                    "CLEAR_PAUSE does not unload filament."
                )
            # A second T in the same SD macro must not turn a recoverable
            # pause into on_error_gcode (commonly CANCEL_PRINT).
            if from_sd and pause_resume_adapter.is_paused():
                self.log_warning(msg)
                return False
            raise gcmd.error(msg)

        if self.mms_eject.cutter_return_pending():
            raise gcmd.error(f"'{cmd}' blocked: check the pending cutter return")

        cmd_slot_num = self._parse_slot(cmd)
        if not self.mms.slot_is_available(cmd_slot_num):
            raise gcmd.error(f"invalid command: {cmd}")

        physical_slot = self.get_mapping_slot_num(cmd_slot_num)
        if not self.mms.slot_is_available(physical_slot):
            raise gcmd.error(
                f"'{cmd}' has an invalid mapped slot: {physical_slot}"
            )
        # A background diagnostic may be waiting with idle motors. Reject it
        # before saving a return state; normal Buffer feed is stopped by swap.
        self.mms_delivery.check_background_idle(allow_tail_release=True)
        initial_slot = self.get_mapping_root(cmd_slot_num)
        # Repeating the active logical tool keeps its loaded substitute.
        # A newly selected tool prefers its refilled root (including an
        # explicit manual mapping). RESUME never re-enters this selection.
        active_tool = self.get_runout_tool(
            self.mms_charge.get_charged_slot())
        if (cmd_slot_num != active_tool
                and self.mms_endless_spool.is_enabled()
                and not self.mms.get_mms_slot(initial_slot).cleanup_required
                and self.mms.get_mms_slot(initial_slot).is_ready()):
            physical_slot = initial_slot
        target = self._find_target(physical_slot, initial_slot)
        restart_error = None
        if target is not None and self._can_keep_loaded_slot(target, recovery):
            buffer = self.mms.get_mms_slot(target).get_mms_buffer()
            free_tail = (self.mms_resume.is_tail_available(target)
                         and not self.mms_resume.can_drive_filament(target))
            try:
                if not free_tail and not buffer.is_activating():
                    buffer.activate_monitor(target)
                if buffer.is_activating():
                    buffer.check_feed(target)
            except Exception as e:
                # Starting a feeder changes neither XYZ nor logical E. Capture
                # this same T below and let the normal failure owner park it.
                restart_error = e
            if restart_error is None and (free_tail or buffer.is_activating()):
                # A known, accepted path needs neither XYZ/E movement nor a
                # new recovery request. Re-arm its existing reserve if needed.
                self._record_tool_target(cmd_slot_num, target)
                self._accept_tool(cmd_slot_num, target)
                self.log_info(
                    f"'{cmd}' slot[{target}] already loaded; skip swap")
                return True
        if not print_job and not toolhead_adapter.is_homed():
            raise gcmd.error(f"'{cmd}' requires XYZ homing; run G28 first")

        already_loaded = (target is not None
                          and self._has_loaded_path(target, recovery))
        if already_loaded:
            physical_slot = target
        if not self.mms_resume.set_mms_swap_resume(
                gcmd, physical_slot, replace_runout=replace_runout,
                initial_slot=initial_slot, already_loaded=already_loaded):
            raise gcmd.error(
                f"'{cmd}' blocked: another recovery request exists"
            )

        self._record_tool_target(cmd_slot_num, physical_slot)
        if replace_runout:
            # The old queued runout worker no longer owns this task. Its feed
            # stop obligation moves here, but a stop error must still pause.
            try:
                source = self.mms.get_mms_slot(recovery["physical_slot"])
                source.get_mms_buffer().deactivate_monitor(wait=False)
                source.get_mms_drive().terminate_moving()
            except Exception as e:
                self.log_error(f"'{cmd}' source feed stop failed: {e}")
            self._handle_swap_failure(
                gcmd, self.mms_resume.get_status()["reason"]
            )
            return False

        if restart_error is not None:
            self._handle_swap_failure(gcmd, restart_error)
            success = False
        else:
            success = self.execute_swap(gcmd)
        if success:
            return self.mms_resume.clear_mms_swap_resume(gcmd)
        if not from_sd:
            # GCodeDispatch ignores a handler's False return. Raising stops
            # the remaining manual macro/batch after failure; file input has
            # already been paused and must keep its retryable file context.
            recovery = self.mms_resume.get_status()
            if (recovery["return_position"] is not None
                    and not recovery["return_valid"]):
                action = recovery["reason"]
            elif recovery["kind"] != "swap":
                action = "check the fault above"
            elif print_job:
                action = "use RESUME after repair, or CANCEL_PRINT"
            else:
                action = ("use RESUME after repair, or CLEAR_PAUSE "
                          "to discard the manual request")
            raise gcmd.error(f"'{cmd}' failed or was cancelled; {action}")
        return False

    def _find_target(self, physical_slot, initial, exhausted=()):
        """Resolve one destination using the same rule throughout its chain."""
        if not self.mms_endless_spool.is_enabled():
            return physical_slot
        recovery = self.mms_resume.get_status()

        def usable(number):
            slot = self.mms.get_mms_slot(number)
            return (number not in exhausted and not slot.cleanup_required
                    and (slot.is_ready()
                         or self._has_loaded_path(number, recovery)))

        if usable(physical_slot):
            return physical_slot
        checked = set()
        candidate = initial
        while candidate is not None and candidate not in checked:
            checked.add(candidate)
            if usable(candidate):
                return candidate
            candidate = self.mms.get_mms_slot(candidate).get_endless_with_slot()
        return None

    def _resolve_target(self, gcmd, physical_slot, exhausted=()):
        target = self._find_target(
            physical_slot, self.mms_resume.get_status()["initial_slot"],
            exhausted)
        if target == physical_slot:
            return target
        if target is None:
            raise SwapFailedSignal(
                f"{gcmd.get_command().strip()} backup chain exhausted from "
                f"slot[{physical_slot}]; refill a slot in this chain and RESUME"
            )
        if not self.mms_resume.update_mms_swap_target(gcmd, target):
            raise SwapFailedSignal("MMS fallback request changed")
        self._record_tool_target(
            self._parse_slot(gcmd.get_command().strip()), target)
        return target

    def _clear_runout(self, gcmd, source, exhausted):
        ("Physical cleanup stays in this request; "
         "failures require manual repair.")
        if not self.mms_resume.is_current_swap(gcmd):
            raise SwapFailedSignal("MMS runout cleanup cancelled")
        target = self.mms_resume.get_status()["physical_slot"]
        with self._swap_is_running(), \
                self.mms_resume.swap_motion(gcmd, allow_clearance=True):
            self.mms_resume.park_for_recovery()
            self.mms_delivery.pause_feeds(self.mms.get_slot_nums())
            self.mms_endless_spool.deactivate()
            self.mms_cut.check_released()
            slot = self.mms.get_mms_slot(source)
            if ((not slot.entry.is_set() or slot.entry.is_triggered())
                    and not extruder_adapter.is_hot_enough()):
                raise SwapFailedSignal("runout purge requires a hot extruder")
            # Preparation can wait for repair without moving filament. Only
            # consume the automatic attempt when cleanup actually starts.
            self.check_source_path(gcmd, target, runout_source=source)
            with self.mms_resume.clearing_runout(gcmd, source):
                self.mms_eject.unload_others(source)
                self.mms_endless_spool.purge_truncate(source)
                self.mms_resume.check_swap_request(gcmd, allow_clearance=True)
        if not self.mms_endless_spool.is_enabled():
            slot.check_load_allowed()
            if (self.mms_resume.is_resuming()
                    and self.mms.get_mms_slot(target).is_ready()):
                return
            raise SwapFailedSignal(
                f"slot[{source}] residual path cleared; ensure target "
                f"slot[{target}] Inlet has filament and RESUME; "
                "automatic refill disabled")
        replacement = self._find_target(source, source, exhausted)
        if replacement is not None:
            self.update_mapping_slot_num_iter(source, replacement)
        # A new T keeps its own target; only an exhausted requested target
        # falls back. Lack of an old source's backup cannot block a healthy T.
        self._resolve_target(gcmd, target, exhausted)

    def execute_swap(self, gcmd):
        """One iterative owner for initial T, automatic fallback and RESUME."""
        if self._slot_num_to is not None \
                or not self.mms_resume.is_current_swap(gcmd):
            return False
        cmd = gcmd.get_command().strip()
        self._slot_num_to = self._parse_slot(cmd)
        exhausted = set()
        try:
            state = self.mms_resume.get_status()
            select_only = state["stage"] == "select"
            return_needed = (
                not select_only or self.mms_delivery.has_pending_print_tail_release()
                or any(s.get_num() != state["physical_slot"]
                       and s.gate.is_triggered()
                       for s in self.mms.get_mms_slots()))
            with self.mms_resume.swap_motion(
                    gcmd, allow_clearance=True,
                    z_raise=self.z_raise if return_needed else 0.):
                while True:
                    try:
                        with self.mms_resume.swap_motion(gcmd):
                            self.mms_resume.check_swap_request(gcmd)
                            self.log_info(f"'{cmd}' begin")
                            target = self.mms_resume.get_status()[
                                "physical_slot"
                            ]
                            if not self.mms_swap(target, gcmd):
                                raise SwapFailedSignal(
                                    "MMS swap did not complete"
                                )
                            self.mms_resume.check_swap_request(gcmd)
                            self.mms_resume.check_swap_complete(
                                gcmd,
                                self.mms_resume.get_status()["generation"],
                            )
                            if (not self.mms_resume.is_resuming()
                                    and (return_needed
                                         or self.mms_resume.get_status()[
                                             "stage"] != "select")):
                                self.mms_resume.return_to_origin()
                        break
                    except SwapRunoutSignal as runout:
                        if runout.slot_num in exhausted:
                            raise SwapFailedSignal(
                                "repeated runout; repair filament and RESUME"
                            )
                        exhausted.add(runout.slot_num)
                        self._clear_runout(gcmd, runout.slot_num, exhausted)
            if (not self.mms_resume.is_resuming()
                    and self.mms_resume.get_status()["stage"] != "select"):
                self.mms_resume.restore_return_fan()
            self.mms_resume.check_swap_complete(
                gcmd, self.mms_resume.get_status()["generation"])
            self._accept_tool(
                self._parse_slot(cmd),
                self.mms_resume.get_status()["physical_slot"],
            )
            self.log_info(f"'{cmd}' finish")
            return True
        except Exception as e:
            if self.mms_resume.is_current_swap(gcmd):
                try:
                    self.mms_eject.check_paths(
                        exhausted_source=self.mms_resume.get_exhausted_slot())
                except EjectFailedError as path_error:
                    self.mms_resume.require_filament_clearance(
                        self.mms_resume.get_status()["physical_slot"],
                        str(path_error))
            if self.mms_resume.is_resuming():
                # RESUME is the request entry and owns its one failure boundary.
                raise
            self.log_error(f"'{cmd}' error: {e}")
            self._handle_swap_failure(gcmd, e)
            return False
        finally:
            self._slot_num_to = None

    # ---- Mapping ----
    def _format_swap_mapping_info(self, mapping=None):
        if mapping is None:
            mapping = self.mapping
        lst = list(mapping.keys())
        if "filename" in lst:
            lst.remove("filename")
        lst.sort()

        info = ""
        for k in lst:
            info += f"slot[{k}] -> slot[{mapping[k]}]\n"
        info += f"filename: {mapping.get('filename')}"

        return info

    def get_mapping_slot_num(self, slot_num):
        """Determine actual slot from the current file's mapping."""
        self._select_mapping_file()
        target_slot_num = self.mapping[slot_num]
        log_func = self.log_info_s \
            if slot_num == target_slot_num \
            else self.log_info
        log_func(
            f"slot[{slot_num}] swap mapping to slot[{target_slot_num}],\n"
            f"current swap mapping:\n"
            f"{self._format_swap_mapping_info()}"
        )
        return target_slot_num

    def get_mapping_root(self, slot_num):
        self._select_mapping_file()
        return self._mapping_roots[slot_num]

    def _accept_tool(self, tool, physical_slot):
        self._active_tool = (tool, physical_slot)
        self._last_tool_print_start = print_stats_adapter.get_print_start_time()

    def is_before_first_print_tool(self):
        start = print_stats_adapter.get_print_start_time()
        return start is not None and start != self._last_tool_print_start

    def clear_loaded_tool(self):
        self._active_tool = None

    def get_runout_request(self, physical_slot):
        tool = self.get_runout_tool(physical_slot)
        if tool is None:
            return physical_slot, physical_slot
        return tool, self.get_mapping_root(tool)

    def get_runout_tool(self, physical_slot):
        self._select_mapping_file()
        active = self._active_tool
        if (active is not None and active[1] == physical_slot
                and self.mms_charge.get_charged_slot() == physical_slot):
            return active[0]
        return None

    def update_mapping_slot_num(self, slot_num, slot_num_new):
        if slot_num in self.mapping:
            self.mapping[slot_num] = slot_num_new
            self._mapping_roots[slot_num] = slot_num_new
            self.log_info_s(
                f"slot[{slot_num}] update with slot[{slot_num_new}]"
                f" in swap mapping\n"
                f"current swap mapping: {self.mapping}"
            )

    def _record_tool_target(self, tool, target):
        """Record this request's destination without changing other aliases."""
        if self.mapping[tool] != target:
            self._init_mapping_filename()
            self.mapping[tool] = target
            self.log_info(
                f"'{self.format_command(tool)}' maps to slot[{target}]"
            )

    def update_mapping_slot_num_iter(self, slot_num, slot_num_new):
        # A cleared exhausted source changes every alias using that source.
        # Ordinary tool selection only records its own destination above.
        if slot_num in self.mapping:
            self._init_mapping_filename()
            for k,v in self.mapping.items():
                if v == slot_num:
                    self.mapping[k] = slot_num_new

            self.log_info_s(
                f"slot[{slot_num}] update with slot[{slot_num_new}]"
                f" in swap mapping\n"
                f"current swap mapping: {self.mapping}"
            )

    def cmd_MMS_SWAP_MAPPING(self, gcmd):
        """
        Update SLOTs mapping.
        Change slot[swap_num] to slot[slot_num]
        """
        swap_num = gcmd.get_int("SWAP_NUM", minval=0, default=None)
        slot_num = gcmd.get_int("SLOT", minval=0, default=None)
        filename = gcmd.get("FILENAME", default=None)

        if swap_num is None or slot_num is None:
            self._select_mapping_file()
            info = f"current swap mapping:\n{self._format_swap_mapping_info()}"
            if self._mapping_preset is not None:
                info += ("\npreset swap mapping:\n"
                         + self._format_swap_mapping_info(self._mapping_preset))
            self.log_info(info)
            return

        if not self.mms.slot_is_available(swap_num) \
            or not self.mms.slot_is_available(slot_num):
            return

        if filename and filename != print_stats_adapter.get_filename():
            if (self._mapping_preset is None
                    or self._mapping_preset["filename"] != filename):
                self._mapping_preset = {"filename": filename}
            self._mapping_preset[swap_num] = slot_num
            self.log_info(
                "preset swap mapping:\n"
                + self._format_swap_mapping_info(self._mapping_preset))
            return
        self._select_mapping_file()
        # An idle manual preset is for the next print (and manual T commands).
        # Automatic substitutions bind separately to their current print.
        self.mapping["filename"] = filename or (
            print_stats_adapter.get_filename()
            if print_stats_adapter.is_printing()
            or print_stats_adapter.is_paused()
            else None
        )

        org = self.mapping[swap_num]
        self.update_mapping_slot_num(swap_num, slot_num)
        if org != slot_num:
            self.log_info(
                f"slot[{swap_num}] swap mapping update "
                f"from '{org}' to '{slot_num}',\n"
                f"current swap mapping:\n"
                f"{self._format_swap_mapping_info()}"
            )

    def cmd_MMS_SWAP_MAPPING_RESET(self, gcmd):
        self.log_info(
            f"current swap mapping:\n"
            f"{self._format_swap_mapping_info()}"
            "\n"
            "Reset to default"
        )
        # Reset destinations and roots together, preserving the file binding.
        filename = self.mapping.get("filename")
        self._initialize_mapping(keep_active=True)
        self._mapping_preset = None
        self.mapping["filename"] = filename
        self.log_info(
            f"default swap mapping:\n"
            f"{self._format_swap_mapping_info()}"
        )


def load_config(config):
    return MMSSwap(config)
