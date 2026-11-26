# Support for MMS Resume
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    pause_resume_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter,
    virtual_sdcard_adapter,
)
from ..core.exceptions import MMSException, SwapRunoutSignal


class MMSResume:
    def __init__(self):
        # Command in mainsail.cfg->[gcode_macro RESUME]
        self._gcode_command = "RESUME"

        # Physical runout survives cancellation of a command or pause.
        self._exhausted_slot = None
        self._tail_release_epoch = None
        self._epoch = 0
        self._attempt_epoch = None
        self._is_resuming = False
        self._initialize_status(invalidate=False)
        self._replace_resume()

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)
        printer_adapter.register_event(
            "homing:home_rails_begin", self._invalidate_swap_return)
        printer_adapter.register_event(
            "stepper_enable:motor_off", self._invalidate_swap_return)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()
        self.mms_charge = printer_adapter.get_mms_charge()
        self.mms_swap = printer_adapter.get_mms_swap()
        self.mms_pause = self.mms.get_mms_pause()

        self.print_observer = self.mms.get_print_observer()
        self.print_observer.register_start_callback(
            self._handle_print_is_started)
        self.print_observer.register_finish_callback(
            self._handle_print_is_finished)

    def _initialize_gcode(self):
        commands = [
            ("MMS_RESUME", self.cmd_MMS_RESUME),
            ("MMS_PAUSE_COOL", self.cmd_MMS_PAUSE_COOL),
            ("MMS_CHECK_RUNOUT", self.cmd_MMS_CHECK_RUNOUT),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=False)
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()

    def _initialize_status(self, invalidate=True):
        # Invalidate work suspended in a selector/heater wait. Its finally
        # block owns _attempt_epoch until that stack has actually unwound.
        if invalidate:
            self._epoch += 1
        self._prepared = False
        self._recovery_kind = None
        self._mms_swap_resume_gcmd = None
        self._mms_swap_physical_slot = None
        self._mms_swap_initial_slot = None
        self._swap_stage = None
        # Accepted motion belongs to this request and this loading stage.
        self.stage_work = {}
        self._swap_return_valid = False
        self._clear_required = False
        self._clear_reason = None
        self._runout_slot = None
        self._pause_source_slot = None
        self._paused_before_feed = False
        self._return_state = None
        self._print_job = False
        self._target_temperature = 0.0
        self._restore_temperature = True
        self._resume_idle = False
        self._resume_extra_remaining = None
        self._failure_reason = None

    # ---- Handlers ----
    def _invalidate_swap_return(self, *args):
        # Rehoming cannot restore the saved print/model relationship. Manual
        # MMS selector homing uses a different event and does not invalidate it.
        if self._return_state is not None:
            self._swap_return_valid = False

    def _handle_print_is_started(self):
        self._reset_idle_status()
        # Also cover a source already empty when file input starts; no new
        # Inlet edge is guaranteed in that case.
        self.check_runout()

    def _handle_print_is_finished(self):
        # The finish runner owns cancellation. A late observer callback must
        # not change its captured generation or clear a newer manual request.
        self._reset_idle_status()

    def _reset_idle_status(self):
        # Observer events are delayed. A new T may already own a request by
        # the time the previous finish/current start is observed. Only the
        # command's success or explicit CANCEL_PRINT may clear that request.
        if (
            self._recovery_kind is None
            and self._return_state is None
            and self._failure_reason is None
            and not self.is_resuming()
            and not pause_resume_adapter.is_paused()
        ):
            # A delayed observer notification is cleanup, not a new cancel
            # request for an independent maintenance operation in progress.
            self._initialize_status(invalidate=False)

    # ---- Gcode control ----
    def gcode_resume(self):
        gcode_adapter.run_command(self._gcode_command, strict=True)
        self.log_info(
            f"mms_resume send gcode resume: {self._gcode_command}"
        )

    # ---- Status ----
    def is_resuming(self):
        return self._is_resuming

    def get_status(self):
        # The task lives here; macros only inspect it. Slot is the original
        # logical T number, before the swap's per-file mapping is applied.
        gcmd = self._mms_swap_resume_gcmd
        reason = self._clear_reason or self._failure_reason
        if self._return_state is not None and not self._swap_return_valid:
            reason = self._untrusted_return_message()
        return {
            "resuming": self._is_resuming,
            "pending": (self._recovery_kind in ("swap", "reload")
                        and not self._prepared),
            "kind": self._recovery_kind,
            "pause_slot": self._pause_source_slot,
            "exhausted_slot": self._exhausted_slot,
            "generation": self._epoch,
            "slot": self.mms_swap._parse_slot(gcmd.get_command().strip())
                    if gcmd is not None else None,
            "physical_slot": self._mms_swap_physical_slot,
            "initial_slot": self._mms_swap_initial_slot,
            "stage": self._swap_stage,
            "return_valid": self._swap_return_valid,
            "clear_required": self._clear_required,
            "reason": reason,
            "prepared": self._prepared,
            "return_position": ([self._return_state["position"][axis]
                                 for axis in ("x", "y", "z")]
                                if self._return_state is not None else None),
            "print_job": self._print_job,
            "target_temperature": self._target_temperature,
            "resume_idle": self._resume_idle,
        }

    def get_exhausted_slot(self):
        return self._exhausted_slot

    def is_tail_available(self, slot_num):
        if slot_num is None or self._exhausted_slot != slot_num:
            return False
        slot = self.mms.get_mms_slot(slot_num)
        return slot.entry.is_set() and slot.entry.is_triggered()

    def can_drive_filament(self, slot_num, forward=True,
                           require_selection=True):
        if self._exhausted_slot != slot_num:
            return True
        return (self._tail_release_epoch is None and
                self.has_tail_grip(slot_num, forward, require_selection))

    def is_extruder_tail(self, slot_num):
        return (self._exhausted_slot == slot_num
                and self._tail_release_epoch is not None)

    def has_tail_grip(self, slot_num, forward=True, require_selection=True):
        slot = self.mms.get_mms_slot(slot_num)
        # Forward feeding must not push a new segment into the old tail.
        # A controlled withdrawal can return the same tail across Inlet.
        if (not slot.gate.is_triggered()
                or forward and not slot.inlet.is_released()):
            return False
        return (not require_selection or
                (slot.selector.is_triggered()
                 and slot.get_mms_selector().get_focus_slot() == slot_num))

    def accept_cleared_path(self):
        """Accept physical clearance without reviving a cancelled request."""
        for slot in self.mms.get_mms_slots():
            downstream = slot.entry if slot.entry.is_set() else slot.outlet
            if slot.gate.is_triggered() or downstream.is_triggered():
                return False
        self._exhausted_slot = None
        self._tail_release_epoch = None
        self.mms_swap.clear_loaded_tool()
        return True

    def request_reload(self, slot_num, reason):
        if self._recovery_kind is not None:
            return
        if not pause_resume_adapter.is_paused():
            self._epoch += 1
        self._recovery_kind = "reload"
        self._mms_swap_physical_slot = slot_num
        self._pause_source_slot = slot_num
        self._request_reload_clearance(slot_num, reason)

    def _set_tail_recovery(self, slot_num):
        if not self.mms_swap.is_enabled():
            # Detection remains useful when T commands are disabled. Never
            # bypass their admission through the internal swap executor.
            self.request_reload(
                slot_num, "tail reached Entry; clear residual filament "
                "and refill the original Inlet before RESUME")
            return
        # A normal pause may consume its final tail while cleaning. Adopt the
        # same snapshot and generation, then use the ordinary swap executor.
        if self._recovery_kind is None:
            tool, root = self.mms_swap.get_runout_request(slot_num)
            if not self.set_mms_swap_resume(
                    gcode_adapter.easy_gcmd(self.mms_swap.format_command(tool)),
                    slot_num,
                    initial_slot=root):
                return
        if self._recovery_kind == "swap":
            self.require_filament_clearance(
                self._mms_swap_physical_slot,
                f"slot[{slot_num}] tail reached Entry; clear residual "
                "filament and refill before RESUME", runout=True)

    def is_current_attempt(self):
        return self._attempt_epoch is None or (
            self._attempt_epoch == self._epoch
            and pause_resume_adapter.is_paused())

    @contextmanager
    def _mms_resuming(self):
        self._is_resuming = True
        self._attempt_epoch = self._epoch
        try:
            yield
        finally:
            self._is_resuming = False
            self._attempt_epoch = None

    def capture_return_state(self):
        ("Capture once before product hooks; "
         "retries never overwrite the origin.")
        if self._return_state is not None:
            return
        if self._recovery_kind is None:
            self._epoch += 1
        self._return_state = toolhead_adapter.capture_return_state()
        self._swap_return_valid = toolhead_adapter.is_homed()
        self._print_job = (virtual_sdcard_adapter.is_cmd_from_sd()
                           or print_stats_adapter.is_printing()
                           or print_stats_adapter.is_paused())
        self._target_temperature = (
            toolhead_adapter.get_extruder_heater_target_temp()
        )
        if self._recovery_kind == "swap" and not self._print_job:
            # Manual changes always finish over the configured waste tray.
            tray = printer_adapter.get_mms_purge().tray_point
            self._return_state["position"].update(x=tray[0], y=tray[1])

    def note_failure(self, reason):
        # Cleanup owns when its automatic attempt is consumed. Preparation
        # errors retain that eligibility for the next explicit RESUME.
        self._failure_reason = str(reason)

    def capture_pause_source(self, restore_temperature=True):
        self._restore_temperature = restore_temperature
        if pause_resume_adapter.is_paused() or self._recovery_kind == "reload":
            return
        slot = self.identify_source_slot()
        self._pause_source_slot = slot
        self._paused_before_feed = (
            slot is None and self._print_job and self._recovery_kind is None
            and self._failure_reason is None and self._empty_feed_path()
            and self.mms_swap.is_before_first_print_tool()
            and print_stats_adapter.get_current_status()["filament_used"] == 0.
        )
        self._prepared = False

    def _empty_feed_path(self):
        if (self.mms_charge.get_charged_slot() is not None
                or self._exhausted_slot is not None):
            return False
        for slot in self.mms.get_mms_slots():
            end = slot.entry if slot.entry.is_set() else slot.outlet
            if not slot.gate.is_released() or not end.is_released():
                return False
        return True

    def identify_source_slot(self):
        # Never use is_loading(): it drops the source as soon as Inlet empties.
        charged = self.mms_charge.get_charged_slot()
        if charged is not None:
            # Retain the accepted identity even if its path needs repair.
            return charged
        slots = self.mms.get_mms_slots()
        selected = [s.get_num() for s in slots
                    if s.selector.is_triggered()
                    and s.get_mms_selector().get_focus_slot() == s.get_num()]
        if len(selected) == 1:
            slot = self.mms.get_mms_slot(selected[0])
            if (slot.gate.is_triggered()
                    and (not slot.entry.is_set() or slot.entry.is_triggered())
                    and all(s is slot or s.gate.is_released() for s in slots)):
                return selected[0]
            return None
        if selected or any(not s.selector.is_released() for s in slots):
            return None
        loaded = [s for s in slots if s.gate.is_triggered()]
        if len(loaded) != 1 or self.mms.mms_selector_is_running() \
                or self.mms.mms_drive_is_running():
            return None
        source = loaded[0]
        if any(s.get_mms_selector().get_focus_slot()
               not in (None, source.get_num()) for s in slots):
            return None
        if source.entry.is_set() and not source.entry.is_triggered():
            return None
        return source.get_num()

    def check_runout(self):
        if not self.mms.filament_detection_is_enabled():
            return
        if (self._recovery_kind == "swap" and self._swap_stage == "charge"
                and not self.mms_charge.is_running()):
            slot_num = self.mms_charge.get_charged_slot()
        elif self._recovery_kind in ("reload", "swap"):
            slot_num = self._mms_swap_physical_slot
        elif pause_resume_adapter.is_paused():
            slot_num = self._pause_source_slot
        else:
            slot_num = self.mms_charge.get_charged_slot()
            if slot_num is None:
                slot_num = self.identify_source_slot()
        if slot_num is None:
            return
        slot = self.mms.get_mms_slot(slot_num)
        if slot.inlet.is_set() and slot.inlet.is_released():
            self.handle_inlet_runout(slot_num)
        elif (self._exhausted_slot == slot_num
              and not printer_adapter.get_mms_eject().is_running()):
            self.handoff_print_tail(slot_num)
        if (self._exhausted_slot == slot_num and slot.entry.is_set()
                and slot.entry.is_released()
                and pause_resume_adapter.is_paused()
                and self._recovery_kind is None
                and self.mms_swap.is_enabled()):
            self._set_tail_recovery(slot_num)

    def handle_inlet_runout(self, slot_num, during_delivery=False, *,
                            inlet_edge=False):
        # Record the physical break before yielding. Refilling Inlet cannot
        # reconnect that tail. Only accepted path clearance clears this fact.
        if not self.mms.filament_detection_is_enabled() \
                or self.mms.printer_is_shutdown() \
                or self._swap_stage == "runout":
            return
        eject = printer_adapter.get_mms_eject()
        if eject.is_running():
            # The same withdrawal can carry the tail back across Inlet.
            # Let Eject finish and verify this strand with its current grip.
            if (inlet_edge and self._exhausted_slot is None
                    and eject.is_withdrawing_source(slot_num)):
                self._exhausted_slot = slot_num
            return
        target = (self._recovery_kind in ("swap", "reload")
                  and self._mms_swap_physical_slot == slot_num
                  and (self._swap_stage != "charge" or during_delivery
                       or self.mms_charge.is_running()))
        source = (self._pause_source_slot if pause_resume_adapter.is_paused()
                  else self.mms_charge.get_charged_slot())
        if source is None and print_stats_adapter.is_printing():
            source = self.identify_source_slot()
        downstream = False
        if during_delivery and not (target or source == slot_num):
            slot = self.mms.get_mms_slot(slot_num)
            waiting = slot.get_waiting_pin()
            downstream = (self.mms_charge.is_running()
                          or slot.get_mms_buffer().get_feed_slot() == slot_num
                          or waiting is not None and waiting in
                          (slot.entry, slot.outlet))
        if not (target or source == slot_num or downstream):
            return
        if self._exhausted_slot != slot_num:
            self._exhausted_slot = slot_num
            self._tail_release_epoch = None
        if self.is_tail_available(slot_num):
            self.handoff_print_tail(slot_num)
            if target and self._clear_required:
                self.require_filament_clearance(
                    slot_num, "Inlet emptied during filament repair",
                    runout=True)
            return
        if target and self._recovery_kind == "swap":
            self.require_filament_clearance(
                slot_num, "Inlet emptied before filament reached Entry",
                runout=True)
            return
        slot = self.mms.get_mms_slot(slot_num)
        if slot.entry.is_set() and self.mms_swap.is_enabled():
            # The downstream callback owns normal-print pause; an active
            # delivery owns its exception. Neither moves in this callback.
            return
        if self.mms.get_mms_endless_spool().is_enabled():
            return
        if self.mms_swap.is_running() or self.mms_charge.is_running() \
                or printer_adapter.get_mms_purge().is_running():
            return
        if not (print_stats_adapter.is_printing()
                or pause_resume_adapter.is_paused()):
            return
        if self._recovery_kind == "reload":
            self._request_reload_clearance(
                slot_num, "Inlet emptied during print recovery")
            return
        if source == slot_num:
            self.request_reload(
                slot_num, "Inlet emptied during printing or pause")

    def handoff_print_tail(self, slot_num):
        # Charge exposes the target to hooks before acceptance. Only a
        # completed load may leave the extruder in sole control of the tail.
        if (self._exhausted_slot != slot_num
                or self._tail_release_epoch == self._epoch
                or self.mms_charge.is_running()
                or self.mms_charge.get_charged_slot() != slot_num
                or not self.is_tail_available(slot_num)
                or not (self._print_job or print_stats_adapter.is_printing()
                        or print_stats_adapter.is_paused())):
            return
        if self._tail_release_epoch is None:
            self.log_info(f"slot[{slot_num}] Inlet empty with filament at "
                          "Entry; handing the remaining tail to the extruder")
        # Foreground motion checks may read the empty Inlet while the
        # selector is moving or after a failed release. Queue once per owner.
        self._tail_release_epoch = self._epoch
        # A fault already being parked owns its manual-repair release.
        if self._failure_reason is None:
            buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()
            buffer.request_tail_release(slot_num)

    def _request_reload_clearance(self, slot_num, reason):
        was_clear_required = self._clear_required
        if (
            self.require_filament_clearance(slot_num, reason)
            and not was_clear_required
        ):
            epoch = self._epoch
            printer_adapter.get_reactor().register_callback(
                lambda eventtime: self._pause_for_runout(epoch, slot_num))

    def _pause_for_runout(self, epoch, slot_num):
        if epoch == self._epoch and self._recovery_kind == "reload" \
                and print_stats_adapter.is_finished() \
                and not pause_resume_adapter.is_paused():
            return
        def current():
            return (
                epoch == self._epoch
                and self._recovery_kind == "reload"
                and self._mms_swap_physical_slot == slot_num
                and self._clear_required
                and not self.mms.printer_is_shutdown()
                and (
                    pause_resume_adapter.is_paused()
                    or not print_stats_adapter.is_finished()
                )
            )
        if not current():
            return
        try:
            slot = self.mms.get_mms_slot(slot_num)
            slot.get_mms_buffer().deactivate_monitor(wait=False)
            slot.get_mms_drive().terminate_moving()
        except Exception as e:
            # Failure to stop the feeder must not skip stopping file input.
            self.log_error(f"slot[{slot_num}] runout feed stop failed: {e}")
        try:
            if not current():
                return
            with self.mms_pause.background_pause(current) as owned:
                if not owned:
                    return
                if not pause_resume_adapter.is_paused():
                    self.mms_pause.mms_pause(force=True, generation=epoch)
                if current():
                    self.release_for_manual()
        except Exception as e:
            self.log_error(f"slot[{slot_num}] runout pause failed: {e}")

    # ---- Print control ----
    def _replace_resume(self):
        # Load the native command before gcode_macro handles rename_existing
        # at connect. The macro will rename this wrapper to RESUME_BASE.
        pause_resume_adapter.get_printer_object()
        self._origin_resume = gcode_adapter.unregister("RESUME")
        gcode_adapter.register("RESUME", self.cmd_RESUME)
        self._origin_clear_pause = gcode_adapter.unregister("CLEAR_PAUSE")
        gcode_adapter.register("CLEAR_PAUSE", self.cmd_CLEAR_PAUSE)
        self._origin_cancel = gcode_adapter.unregister("CANCEL_PRINT")
        gcode_adapter.register("CANCEL_PRINT", self.cmd_CANCEL_PRINT)

    def set_mms_swap_resume(self, gcmd, physical_slot, replace_runout=False,
                            initial_slot=None, already_loaded=False):
        replace_runout = (replace_runout and self._recovery_kind == "reload"
                          and self._clear_required and not self._prepared
                          and not pause_resume_adapter.is_paused()
                          and not self.is_resuming())
        if not self.is_current_attempt() \
            or self._recovery_kind is not None and not replace_runout:
            return False
        if replace_runout:
            self._clear_reason = (
                f"source slot[{self._mms_swap_physical_slot}] "
                f"ran out before {gcmd.get_command().strip()} started"
            )
        if not self.is_resuming():
            self._epoch += 1
        self._prepared = False
        self._recovery_kind = "swap"
        self._mms_swap_resume_gcmd = gcmd
        self._mms_swap_physical_slot = physical_slot
        self._mms_swap_initial_slot = (physical_slot if initial_slot is None
                                       else initial_slot)
        # Restoring selection does not invalidate accepted filament or load it.
        self._set_stage("select" if already_loaded else "charge")
        self.capture_return_state()
        if not replace_runout:
            self._clear_required = False
            self._clear_reason = None
        self.log_info(
            f"'{gcmd.get_command().strip()}' is set as mms_swap resume command")
        return True

    def is_current_swap(self, gcmd):
        return (gcmd is not None and gcmd is self._mms_swap_resume_gcmd
                and self.is_current_attempt())

    def check_swap_request(self, gcmd, allow_clearance=False):
        if not self.is_current_swap(gcmd):
            raise MMSException("swap request was cancelled")
        if allow_clearance:
            self._check_attempt_allow_clearance()
        else:
            self.check_swap_path()
            self._check_attempt()

    @contextmanager
    def swap_motion(self, gcmd, allow_clearance=False, z_raise=0.):
        with toolhead_adapter.motion_scope(
                return_state=self._return_state, z_raise=z_raise,
                check=lambda: self.check_swap_request(gcmd, allow_clearance)):
            yield

    def _set_stage(self, stage):
        if self._swap_stage != stage:
            self.stage_work = {}
            self._swap_stage = stage

    def advance_stage(self, stage):
        # A step may yield to cancellation or an Inlet fault. Commit only to
        # its still-current request; a clearance request always wins.
        self._check_attempt()
        if self._recovery_kind not in ("swap", "reload"):
            raise MMSException("MMS loading request is no longer current")
        self._set_stage(stage)
        self.check_swap_path()
        self._check_attempt()

    def check_loading_request(self):
        """Validate the request after a charge action may have yielded."""
        self._check_attempt()

    def handle_swap_path_release(self, slot_num, pin_type):
        pin = pin_type.lower()
        if pin == "inlet":
            self.handle_inlet_runout(slot_num)
            return self._exhausted_slot == slot_num
        if pin == "gate" and self.is_tail_available(slot_num):
            return True
        if self._swap_stage == "runout" \
                or printer_adapter.get_mms_eject().is_running():
            return False
        if (pin == "entry" and self._exhausted_slot == slot_num
                and (self.is_resuming() or (
                    not self.mms_swap.is_enabled()
                    and (print_stats_adapter.is_printing()
                         or pause_resume_adapter.is_paused())))
                and self._recovery_kind is None):
            self._set_tail_recovery(slot_num)
            return True
        if self._recovery_kind in ("swap", "reload") and self._swap_stage in (
            "select", "charge", "feed", "fill", "charge_check",
            "purge", "brush",
        ):
            if pin == "entry" and self._exhausted_slot == slot_num:
                return self.require_filament_clearance(
                    slot_num, "exhausted filament reached Entry", runout=True)
            # Charge owns legitimate Entry/Gate movement before acceptance.
            if self._swap_stage == "charge":
                return False
            if self._swap_stage == "feed" and (
                pin == "entry" or self.mms_charge.is_running()
            ):
                return False
            # Synchronous fill checkpoints distinguish an unfinished feed
            # from a broken upstream path using the same live sensor facts.
            if self._swap_stage == "fill" and pin == "entry":
                return False
            return self.require_filament_clearance(
                slot_num, f"{pin_type} released while recovering loaded "
                "filament; inspect the path before RESUME", loading_path=True)
        return False

    def check_swap_path(self):
        """Invalidate completed loading/purge when the target path is lost."""
        self._check_swap_return()
        if (self._clear_required
                or self._recovery_kind not in ("swap", "reload")
                or printer_adapter.get_mms_eject().is_running()):
            return
        if self._swap_stage not in (
            "select",
            "feed",
            "fill",
            "charge_check",
            "purge",
            "brush",
        ):
            return
        slot_num = self._mms_swap_physical_slot
        slot = self.mms.get_mms_slot(slot_num)
        if slot.inlet.is_released():
            self.handle_inlet_runout(slot_num)
        # Outlet also indicates a full buffer and can legitimately release
        # during relaxation/measurement. Only optional Entry proves the
        # downstream path remains loaded after charge.
        entry_missing = slot.entry.is_set() and not slot.entry.is_triggered()
        gates = [s.get_num() for s in self.mms.get_mms_slots()
                 if s.gate.is_triggered()]
        tail = self.is_tail_available(slot_num)
        intact = slot.is_ready() and gates == [slot_num]
        if any(n != slot_num for n in gates):
            try:
                if self._swap_stage == "feed":
                    printer_adapter.get_mms_eject().check_paths()
                    return
                if self._swap_stage in ("select", "purge", "brush"):
                    self.check_loaded_source(slot_num, prepare=True)
                    return
            except MMSException as e:
                self.require_filament_clearance(slot_num, str(e))
                return
        if self._swap_stage == "feed":
            # While actively feeding, detection owns legitimate drive
            # retractions. Admission and completion check the settled path.
            if self.mms_charge.is_running():
                return
            intact = slot.is_ready() and (
                gates == [slot_num] or not gates and entry_missing
                or not gates and not slot.entry.is_set()
            )
            entry_missing = False
        elif self._swap_stage == "fill" \
                and entry_missing and intact \
                and self._exhausted_slot is None:
            # Active fill owns Entry and its accepted E distance. A later
            # manual repair that leaves only Gate occupied starts at feed.
            if not self.mms_charge.is_running():
                self._set_stage("feed")
            return
        if not (intact or tail and gates in ([], [slot_num])) or entry_missing:
            self.require_filament_clearance(
                slot_num,
                f"slot[{slot_num}] loaded swap path changed: "
                f"Inlet={int(slot.inlet.is_triggered())}, Gates={gates}, "
                f"""Entry={
                    int(slot.entry.is_triggered())
                    if slot.entry.is_set() else 'unset'
                }; """
                "inspect the original feed path before RESUME",
                runout=(self._exhausted_slot == slot_num),
                loading_path=True,
            )

    def clear_mms_swap_resume(self, gcmd):
        if not self.is_current_swap(gcmd):
            return False
        self._initialize_status(invalidate=False)
        return True

    def require_filament_clearance(self, physical_slot, reason, runout=False,
                                   loading_path=False):
        if not (self._recovery_kind == "reload" and self.is_current_attempt()
                or self.is_current_swap(self._mms_swap_resume_gcmd)) \
            or physical_slot != self._mms_swap_physical_slot:
            return False
        if (not self._clear_required
                or self._swap_stage in ("feed", "fill")
                or self._runout_slot is not None and not runout):
            self._clear_reason = reason
            # A later empty Inlet cannot reclassify a cutter/path failure.
            if runout and self._recovery_kind == "swap" \
                    and self.mms.get_mms_endless_spool().can_clear_runout(
                        physical_slot):
                self._runout_slot = physical_slot
        if not runout:
            self._runout_slot = None
        self._clear_required = True
        self._prepared = False
        # Stop the active attempt even if the pin returns before it wakes.
        # Keep completed loading work available for an explicit repair retry.
        if not (loading_path and not runout and self._exhausted_slot is None
                and self._swap_stage in ("feed", "fill", "charge_check")):
            self._set_stage("charge")
        return True

    def _check_recovery_path(self, gcmd, require_ready=True):
        if not (self._recovery_kind == "reload" and self.is_current_attempt()
                or self.is_current_swap(gcmd)):
            raise MMSException("MMS clearance request was cancelled")
        slot_num = self._mms_swap_physical_slot
        slot = self.mms.get_mms_slot(slot_num)
        paths = self.mms.get_mms_slots()
        repair = self._swap_stage in ("feed", "fill", "charge_check")
        retry_stage = None
        if (repair and slot.entry.is_set() and slot.is_ready()
                and [p.get_num() for p in paths if p.gate.is_triggered()]
                == [slot_num]):
            retry_stage = (self._swap_stage if slot.entry.is_triggered()
                           else "feed")
        if require_ready or retry_stage is not None:
            slot.check_load_allowed()
        if require_ready and not slot.is_ready():
            raise MMSException(f"slot[{slot_num}] Inlet is empty; refill the "
                               "original target and retry RESUME")
        if retry_stage is None:
            for path in paths:
                end = path.entry if path.entry.is_set() else path.outlet
                if not (path.gate.is_triggered() or end.is_triggered()):
                    continue
                if repair:
                    raise MMSException(
                        f"slot[{slot_num}] cannot take over the repaired path; "
                        "restore its Inlet and Gate as the only feed source "
                        "or clear the path, then retry RESUME")
                raise MMSException(
                    f"slot[{slot_num}] recovery requires manual residual "
                    f"removal: slot[{path.get_num()}] "
                    f"Gate={int(path.gate.is_triggered())}, "
                    f"{end.get_pin_type()}={int(end.is_triggered())}; "
                    "clear the path, then retry RESUME")
        if (
            self.mms.mms_selector_is_running()
            or self.mms.mms_drive_is_running()
            or any(b.is_activating() for b in self.mms.get_mms_buffers())
        ):
            raise MMSException("MMS recovery requires idle feed/selector and "
                             "stopped buffer monitors; wait before RESUME")
        return retry_stage

    def _verify_filament_clearance(self, require_ready=True):
        self.check_swap_path()
        if not self._clear_required or self._runout_slot is not None:
            # A known Inlet runout is cleared and verified by the swap loop.
            # Failed automatic cleanup drops this eligibility and stays manual.
            return
        gcmd = self._mms_swap_resume_gcmd
        epoch = self._epoch
        self._check_recovery_path(gcmd, require_ready)
        printer_adapter.get_mms_cut().check_released()
        if epoch != self._epoch:
            raise MMSException("MMS clearance request was cancelled")
        # Cutter feedback can wait. Recheck before consuming clearance or
        # submitting any loading movement.
        stage = self._check_recovery_path(gcmd, require_ready)
        if stage is not None:
            self._set_stage(stage)
            self._clear_required = False
            self._clear_reason = None

    def update_mms_swap_target(self, gcmd, physical_slot):
        # Only an explicit endless-spool transition may retarget its own
        # pending request. Editing the tool mapping never changes a retry.
        if not self.is_current_swap(gcmd) or self._clear_required:
            return False
        self._mms_swap_physical_slot = physical_slot
        self._set_stage("charge")
        self._prepared = False
        return True

    @contextmanager
    def clearing_runout(self, gcmd, source):
        if not self.is_current_swap(gcmd):
            raise MMSException("MMS runout cleanup cancelled")
        if self._clear_required and self._runout_slot is None:
            raise MMSException(self._clear_reason)
        self.require_filament_clearance(
            self._mms_swap_physical_slot,
            f"slot[{source}] exhausted; clear residual before RESUME")
        # One automatic attempt. Failure leaves manual clearance pending.
        self._runout_slot = None
        self._set_stage("runout")
        try:
            yield
            if not self.is_current_swap(gcmd):
                raise MMSException("MMS runout cleanup was cancelled")
            self._verify_filament_clearance(require_ready=False)
            self._commit_filament_clearance()
        finally:
            if self.is_current_swap(gcmd) and self._swap_stage == "runout":
                self._set_stage("charge")

    def _commit_filament_clearance(self):
        """Discard old filament state only after clearance acceptance."""
        printer_adapter.get_mms_purge().clear_retract()
        self.mms_charge.teardown()
        self._exhausted_slot = None
        self._tail_release_epoch = None
        self._pause_source_slot = None
        self._clear_required = False
        self._clear_reason = None
        self._runout_slot = None
        self._set_stage("charge")

    def _resume_mms_swap(self):
        self._verify_filament_clearance(
            require_ready=not self.mms.get_mms_endless_spool().is_enabled())
        if self._clear_required and self._runout_slot is None:
            self._commit_filament_clearance()
        request = self._mms_swap_resume_gcmd
        if not self.mms_swap.execute_swap(request) \
                or not self.is_current_swap(request):
            raise MMSException("MMS swap request is no longer executable")

    def _resume_reload(self):
        self._verify_filament_clearance()
        if self._clear_required:
            self._commit_filament_clearance()
        # An extension may have been selected during manual repair.
        self.mms_delivery.pause_feeds(self.mms.get_slot_nums())
        self._check_attempt()
        printer_adapter.get_mms_eject().release_other_selectors(
            self._mms_swap_physical_slot)
        self._check_attempt()
        precleaned = self.mms_charge.load_filament(
            self._mms_swap_physical_slot, progress=self)
        self._prepared = True
        self.log_info(
            f"slot[{self._mms_swap_physical_slot}] reloaded; "
            "print remains paused"
        )
        return precleaned

    def lift_for_pause(self):
        if not self._swap_return_valid or not toolhead_adapter.is_homed():
            return
        current = toolhead_adapter.get_position()
        target = self._return_state["position"]["z"] + self.mms_swap.z_raise
        lift = max(0., target - current["z"])
        if lift > 0.:
            # Queue clearance directly behind accepted print moves, before
            # waiting for feeder handoff or pause retraction.
            with toolhead_adapter.motion_scope(z_raise=lift):
                pass

    def retract_for_pause(self):
        if (self._failure_reason or self._recovery_kind in ("swap", "reload")
                or self.mms_delivery.has_pending_print_tail_release()):
            return
        slot_num = self._pause_source_slot
        if slot_num is None or not extruder_adapter.get_extruder_status().get(
            "can_extrude"
        ):
            return
        slot = self.mms.get_mms_slot(slot_num)
        if (not slot.entry.is_set() or not slot.entry.is_triggered()
                or self.is_extruder_tail(slot_num)
                and slot.selector_is_triggered()):
            # Let the nozzle lift before waiting for a clamped print tail.
            return
        # E-only motion is valid even if XYZ cannot be parked. Native PAUSE
        # has already saved PAUSE_STATE and stopped file input.
        with toolhead_adapter.motion_scope():
            printer_adapter.get_mms_purge().retract_for_pause()

    def cmd_MMS_PAUSE_COOL(self, gcmd):
        if not pause_resume_adapter.is_paused():
            raise gcmd.error("MMS_PAUSE_COOL requires a paused printer")
        target = toolhead_adapter.get_extruder_heater_target_temp()
        if target > 0.:
            toolhead_adapter.set_extruder_temperature(0., wait=False)
            self._target_temperature = target
            self._resume_idle = self._restore_temperature

    def _prepare_temperature(self):
        if self._resume_idle:
            if toolhead_adapter.get_extruder_heater_target_temp() == 0.:
                toolhead_adapter.set_extruder_temperature(
                    self._target_temperature, wait=True
                )
            self._resume_idle = False
        self._check_attempt_allow_clearance()
        heater = toolhead_adapter.get_extruder_heater()
        if not extruder_adapter.is_hot_enough() or (
            toolhead_adapter.get_extruder_heater_target_temp()
            < heater.min_extrude_temp
        ):
            raise MMSException(
                "Heat the nozzle to the material temperature before RESUME"
            )
        self._check_attempt_allow_clearance()

    def _clean_for_resume(self, prepare=True):
        # Reload clearance has been committed. New runout now aborts every
        # extrusion segment and cooling wait, before further machine actions.
        with toolhead_adapter.motion_scope(check=self._check_attempt), \
                toolhead_adapter.temporary_fan_speed(0.):
            printer_adapter.get_mms_purge().purge_for_resume(
                prepare=prepare, work=self.stage_work)
            self._check_resume_ready()
            brush = printer_adapter.get_mms_brush()
            if not brush.mms_brush(
                cooldown_wait=brush.resume_fan_cooldown_wait
            ):
                raise MMSException("MMS resume brushing failed")
            self._check_resume_ready()
            slot_num = self._ordinary_source()
            buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()
            self.mms_delivery.release_tail(slot_num)
            self._check_resume_ready()
            buffer.check_feed(slot_num)

    def _check_attempt(self):
        if not self.is_current_attempt() or self.mms.printer_is_shutdown():
            raise MMSException("MMS recovery was cancelled")
        self._check_swap_return()
        slot_num = self._ordinary_source()
        if (self.is_resuming() and self._recovery_kind is None
                and slot_num == self._exhausted_slot
                and slot_num is not None
                and self.mms.get_mms_slot(slot_num).entry.is_released()):
            self._set_tail_recovery(slot_num)
        if self._runout_slot is not None:
            raise SwapRunoutSignal(self._runout_slot)
        if self._clear_required and self._swap_stage != "runout":
            raise MMSException(self._clear_reason)

    def _untrusted_return_message(self):
        action = (
            "use CANCEL_PRINT to end the print" if self._print_job else
            "use CLEAR_PAUSE to discard the manual request, then G28"
        )
        return f"MMS saved return position is no longer trusted; {action}"

    def _check_swap_return(self):
        if self._print_job and print_stats_adapter.is_finished() \
                and not pause_resume_adapter.is_paused():
            raise MMSException("MMS print operation ended before completion")
        if self._return_state is None or not toolhead_adapter.is_homed():
            self._swap_return_valid = False
        if not self._swap_return_valid:
            raise MMSException(self._untrusted_return_message())

    def check_loaded_source(self, slot_num, prepare=False):
        if slot_num is None:
            raise MMSException("MMS has no identified source slot; "
                               "check the filament path before continuing")
        slot = self.mms.get_mms_slot(slot_num)
        gates = [s.get_num() for s in self.mms.get_mms_slots()
                 if s.gate.is_triggered()]
        tail = self.is_tail_available(slot_num)
        if prepare and any(n != slot_num for n in gates) and (
                self.mms_charge.get_charged_slot() == slot_num or tail):
            printer_adapter.get_mms_eject().check_paths(slot_num if tail
                                                      else None)
            gates = [n for n in gates if n == slot_num]
        intact = slot.inlet.is_triggered() and gates == [slot_num]
        if not (intact or tail and gates in ([], [slot_num])) \
                or slot.entry.is_set() and not slot.entry.is_triggered():
            raise MMSException(
                f"slot[{slot_num}] original filament path is incomplete: "
                f"Inlet={int(slot.inlet.is_triggered())}, Gates={gates}, "
                f"""Entry={
                    int(slot.entry.is_triggered())
                    if slot.entry.is_set() else 'unset'
                }; """
                "restore the original path, or clear residual filament when "
                "runout recovery is requested"
            )
        return slot

    def _ordinary_source(self):
        return (self._mms_swap_physical_slot if self._recovery_kind == "reload"
                else self._pause_source_slot)

    def _prepare_ordinary_source(self):
        slot_num = self._ordinary_source()
        self.check_loaded_source(slot_num, prepare=True)
        self.mms_delivery.pause_feeds(self.mms.get_slot_nums())
        self._check_attempt()
        printer_adapter.get_mms_eject().select_loaded_slot(slot_num)
        self._check_attempt()
        self.check_loaded_source(slot_num)

    def park_for_recovery(self):
        printer_adapter.get_mms_eject().finish_pending_cutter_return()
        position = self._return_state["position"]
        current = toolhead_adapter.get_position()
        safe_z = max(position["z"] + self.mms_swap.z_raise, current["z"])
        toolhead_adapter.move_z(safe_z, wait_toolhead=True)
        self._check_attempt_allow_clearance()
        printer_adapter.get_mms_purge().move_to_tray()
        self._check_attempt_allow_clearance()

    def _check_attempt_allow_clearance(self):
        # A validated clearance request is consumed only by its loading stage.
        if not self.is_current_attempt() or self.mms.printer_is_shutdown():
            raise MMSException("MMS recovery was cancelled")
        self._check_swap_return()

    def _check_resume_ready(self, require_feed=False):
        self._check_attempt()
        if self._recovery_kind == "swap":
            self.check_swap_complete(self._mms_swap_resume_gcmd, self._epoch)
        else:
            printer_adapter.get_mms_cut().check_released()
            self._check_attempt()
            if self._paused_before_feed:
                if not self._empty_feed_path():
                    raise MMSException("filament path changed since pause")
                return
            self.check_loaded_source(self._ordinary_source())
            if require_feed:
                self._check_feed_ready(self._ordinary_source())

    def _repay_on_return(self, purge):
        ordinary_resume = self.is_resuming() and self._recovery_kind != "swap"
        if ordinary_resume and self._resume_extra_remaining is None:
            self._resume_extra_remaining = purge.resume_extra_extrude_distance
        if not ordinary_resume or not self._resume_extra_remaining:
            purge.repay_retract()
            return
        generation = self._epoch

        def store_remaining(remaining):
            if generation != self._epoch:
                raise MMSException("MMS resume priming was cancelled")
            self._resume_extra_remaining = remaining

        purge.repay_retract(extra_distance=self._resume_extra_remaining,
                            on_extra_remaining=store_remaining)

    def return_to_origin(self, speed=None):
        def check_return():
            if self.is_resuming():
                self._check_resume_ready(require_feed=True)
            else:
                self.check_swap_complete(
                    self._mms_swap_resume_gcmd, self._epoch)

        check_return()
        purge = printer_adapter.get_mms_purge()
        keep_debt = self._swap_stage == "select" and not self.is_resuming()

        def before_lower():
            check_return()
            if not keep_debt and not self._paused_before_feed and (
                    self._recovery_kind != "swap" or self._print_job):
                self._repay_on_return(purge)
            # Repayment and XY return can yield to a feed fault. Never lower
            # onto the model after the monitor stopped, even at the same epoch.
            check_return()
        toolhead_adapter.return_to_saved(
            self._return_state, before_lower=before_lower,
            clearance=self.mms_swap.z_raise,
            speed=self.mms_swap.toolhead_move_speed if speed is None else speed)
        check_return()
        if (not keep_debt and self._recovery_kind == "swap"
                and not self._print_job):
            purge.clear_retract()

    def restore_return_fan(self):
        # The motion scope has already restored the original modes/limits.
        self._check_attempt()
        fan_speed = self._return_state["fan_speed"]
        if fan_speed is not None:
            gcode_adapter.run_command(f"M106 S{fan_speed * 255.0}", strict=True)
        self._check_attempt()

    def check_swap_complete(self, request, generation):
        if generation != self._epoch or not self.is_current_swap(request) \
                or self._swap_stage not in ("select", "brush"):
            raise MMSException(
                "MMS swap completion belongs to an expired request"
            )
        self.check_swap_path()
        if self._clear_required:
            raise MMSException(self._clear_reason)
        printer_adapter.get_mms_cut().check_released()
        self._check_attempt()
        slot_num = self._mms_swap_physical_slot
        if (self._swap_stage == "select"
                and self.mms_charge.get_charged_slot() != slot_num):
            raise MMSException("previous filament acceptance was lost")
        self.check_loaded_source(slot_num)
        self._check_feed_ready(slot_num)

    def _check_feed_ready(self, slot_num):
        slot = self.mms.get_mms_slot(slot_num)
        if self.can_drive_filament(slot_num):
            selector_ready = self.mms_delivery.is_exclusively_selected(slot_num)
            feed_ready = slot.get_mms_buffer().get_feed_slot() == slot_num
        else:
            # The downstream detector watches a free tail. It has no upstream
            # motor to replenish, so a Buffer owner must not be required.
            selected = self.mms.get_selecting_slots()
            if self.is_extruder_tail(slot_num) and selected == [slot_num]:
                # This T/RESUME still owns the G-code lock. Finish the
                # pending handoff here before lowering onto the model.
                self.mms_delivery.release_tail(slot_num)
                self._check_attempt()
                self.check_loaded_source(slot_num)
                selected = self.mms.get_selecting_slots()
            selector_ready = not selected
            feed_ready = True
        if not selector_ready or not feed_ready:
            raise MMSException(f"slot[{slot_num}] completed selector or buffer "
                               "changed; retry RESUME")

    def _stop_recovery_feed(self):
        slot_num = (self._mms_swap_physical_slot if self._recovery_kind
                    else self._ordinary_source())
        if slot_num is None:
            return
        slot = self.mms.get_mms_slot(slot_num)
        slot.get_mms_buffer().deactivate_monitor(wait=False)
        slot.get_mms_drive().terminate_moving()

    def release_for_manual(self):
        target = self._mms_swap_physical_slot
        waiting_for_filament = (
            self._swap_stage == "charge" and target is not None
            and (self.mms.get_mms_slot(target).cleanup_required
                 or not self.mms.get_mms_slot(target).is_ready()))
        if not (self._clear_required or waiting_for_filament
                or self._failure_reason):
            return
        if not pause_resume_adapter.is_paused():
            return
        generation = self._epoch
        self.mms_delivery.pause_feeds(self.mms.get_slot_nums())
        if generation != self._epoch or not self.is_current_attempt():
            raise MMSException("manual release was cancelled")
        source = self._exhausted_slot
        if source is None:
            source = self.mms_charge.get_charged_slot()
        self.log_warning(
            "MMS manual intervention before selector release: "
            f"source_slot={source}, target_slot={target}, "
            f"stage={self._swap_stage}, "
            f"reason={self._clear_reason or self._failure_reason}")
        self.mms.log_status(silent=False)
        if not self.mms_delivery.mms_unselect():
            raise MMSException("selector release failed; check the mechanism "
                               "and retry MMS_UNSELECT before manual removal")
        for buffer in self.mms.get_mms_buffers():
            # Released filament may now be moved by hand. A later LOAD or
            # RESUME must re-establish reserve before automatic feeding.
            buffer.invalidate_reference()
        if self._return_state is not None and not self._swap_return_valid:
            action = self._untrusted_return_message()
        else:
            action = ("Repair the reported feed fault, then RESUME "
                      "to reselect and prepare the original task")
        self.log_warning(
            "MMS feed stopped; selector released without additional "
            f"filament withdrawal. {action}")

    def cmd_MMS_CHECK_RUNOUT(self, gcmd):
        # Cancellation clears recovery kind, but the suspended motion scope
        # still owns its generation. Check it before reading new sensor state.
        try:
            toolhead_adapter.check_motion()
            self.check_runout()
            toolhead_adapter.check_motion()
            # A pending swap does not own homing or maintenance. Active
            # swap scopes and RESUME still propagate runout and clearance.
            if self.is_resuming() or self.mms_swap.is_running():
                self._check_attempt()
        except Exception as e:
            raise gcmd.error(str(e))

    def cmd_RESUME(self, gcmd):
        if not pause_resume_adapter.is_paused():
            return self._origin_resume(gcmd)
        if self.is_resuming() or self.mms_swap.is_running() \
                or printer_adapter.get_mms_eject().finish_is_active():
            raise gcmd.error(
                "MMS operation is still running; retry RESUME later"
            )
        self.mms_delivery.check_background_idle(allow_tail_release=True)
        self.mms_delivery.clear_finished_print_tail_release()
        velocity = gcmd.get_float(
            "VELOCITY", self.mms_swap.toolhead_move_speed / 60., above=0.)
        self.check_runout()
        generation = self._epoch
        try:
            with self._mms_resuming():
                self._check_attempt_allow_clearance()
                self._verify_filament_clearance(
                    require_ready=not (
                        self._recovery_kind == "swap"
                        and self.mms.get_mms_endless_spool().is_enabled()
                    )
                )
                if (
                    self._recovery_kind == "swap"
                    and self._swap_stage in ("charge", "feed")
                    and not self._clear_required
                    and self._exhausted_slot is None
                    and not self.mms.get_mms_endless_spool().is_enabled()
                ):
                    # Known runout belongs to the swap executor. Ordinary
                    # empty targets still fail before temperature preparation.
                    self.mms_swap.check_source_path(
                        self._mms_swap_resume_gcmd,
                        self._mms_swap_physical_slot,
                    )
                if (self._recovery_kind not in ("swap", "reload")
                        and not self._paused_before_feed):
                    self.check_loaded_source(self._ordinary_source(),
                                             prepare=True)
                # The only recovery execution scope: all product hooks are
                # synchronous, strict and inside this exception boundary.
                with toolhead_adapter.motion_scope(
                        return_state=self._return_state,
                        check=self._check_attempt_allow_clearance):
                    self.park_for_recovery()
                    if not self._paused_before_feed:
                        self._prepare_temperature()
                    while True:
                        executing_swap = self._recovery_kind == "swap"
                        try:
                            if executing_swap:
                                self._resume_mms_swap()
                                executing_swap = False
                            elif self._paused_before_feed:
                                self._check_resume_ready()
                                purge = printer_adapter.get_mms_purge()
                                purge.prepare_extrusion()
                            else:
                                precleaned = False
                                if (
                                    self._recovery_kind == "reload"
                                    and not self._prepared
                                ):
                                    precleaned = self._resume_reload()
                                self._prepare_ordinary_source()
                                self._check_resume_ready()
                                self._clean_for_resume(prepare=not precleaned)
                            self._check_resume_ready(require_feed=True)
                            self.return_to_origin(speed=velocity * 60.)
                            break
                        except Exception:
                            # A G-code hook may wrap SwapRunoutSignal. The
                            # owner, not the exception type, owns the handoff.
                            # The swap executor owns its handoffs. A failed
                            # attempt waits for the user's next RESUME.
                            if (executing_swap or self._recovery_kind != "swap"
                                    or self._runout_slot is None
                                    or not self.is_current_attempt()):
                                raise
                            # A handoff cannot repair lost homing or a stale
                            # return snapshot. Fail here rather than retrying
                            # the same admission error indefinitely.
                            self._check_attempt_allow_clearance()
                self.restore_return_fan()
                self._check_resume_ready(require_feed=True)
                # Native RESUME now sees a state at the already-returned point.
                # The immutable MMS state remains available if this fails.
                gcode_adapter.run_command(
                    "SAVE_GCODE_STATE NAME=PAUSE_STATE", strict=True
                )
                self._check_resume_ready(require_feed=True)
                self._origin_resume(gcmd)
                if generation != self._epoch:
                    raise MMSException(
                        "MMS resume was cancelled during final commit"
                    )
                if pause_resume_adapter.is_paused():
                    raise MMSException(
                        "MMS final resume did not release the pause"
                    )
            self._initialize_status(invalidate=False)
        except Exception as e:
            if generation == self._epoch:
                self.note_failure(e)
                try:
                    self._stop_recovery_feed()
                except Exception as stop_error:
                    self.log_error(
                        f"MMS recovery feed stop failed: {stop_error}"
                    )
                if (generation == self._epoch
                        and pause_resume_adapter.is_paused()
                        and self._swap_return_valid
                        and not self.mms.printer_is_shutdown()):
                    try:
                        # Failure may follow XY return toward the model.
                        # Re-run product parking without replacing snapshots.
                        self.mms_pause.mms_pause(
                            force=True, generation=generation)
                    except Exception as park_error:
                        self.log_error(
                            f"MMS recovery parking failed: {park_error}")
                if generation == self._epoch:
                    try:
                        self.release_for_manual()
                    except Exception as release_error:
                        self.log_error(
                            f"MMS selector release failed: {release_error}")
                # An unexpected loss of the pause is evidence of another
                # state change, not permission to fabricate a fresh PAUSE_STATE.
                if not pause_resume_adapter.is_paused():
                    self._swap_return_valid = False
                    pause_resume_adapter.send_pause_command()
                if (generation == self._epoch
                        and not self.mms.printer_is_shutdown()
                        and self._return_state is not None):
                    fan_speed = self._return_state["fan_speed"]
                    if fan_speed is not None:
                        try:
                            gcode_adapter.run_command(
                                f"M106 S{fan_speed * 255.0}", strict=True)
                        except Exception as fan_error:
                            self.log_error(
                                f"MMS fan restore failed: {fan_error}"
                            )
            if generation != self._epoch:
                raise gcmd.error(f"MMS RESUME cancelled or replaced: {e}")
            if not pause_resume_adapter.is_paused():
                raise gcmd.error(
                    f"MMS RESUME failed: {e}; pause state changed, "
                    "cancel and check the printer"
                )
            paused_task = "print" if self._print_job else "manual operation"
            raise gcmd.error(
                f"MMS RESUME failed: {e}; {paused_task} remains paused")

    def cmd_MMS_RESUME(self, gcmd):
        # Follow the public product entry; it delegates once to this owner.
        velocity = gcmd.get_float("VELOCITY", None, above=0.)
        command = self._gcode_command
        if velocity is not None:
            command += f" VELOCITY={velocity}"
        gcode_adapter.run_command(command, strict=True)

    def cancel_recovery(self):
        self.mms_delivery.cancel_print_tail_release()
        self._initialize_status()
        printer_adapter.get_mms_purge().clear_retract()

    def cmd_CLEAR_PAUSE(self, gcmd):
        self.cancel_recovery()
        return self._origin_clear_pause(gcmd)

    def cmd_CANCEL_PRINT(self, gcmd):
        self.cancel_recovery()
        return self._origin_cancel(gcmd)
