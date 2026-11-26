# Support for MMS Endless Spool
# Well... I prefer to call it "Limit Spool"
#
# Copyright (C) 2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from dataclasses import dataclass

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    pause_resume_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter
)
from ..core.exceptions import EndlessSpoolFailedError


@dataclass(frozen=True)
class MMSEndlessSpoolConfig:
    log_flag: str = "==O=="
    hint_prompt: str = (
        "The filament in slot[{slot_num}] has runout. "
        "Please clear any remaining filament. "
        "After inserting a new filament, "
        "click RESUME to resume printing."
    )

    def get_hint(self, slot_num):
        return self.hint_prompt.format(slot_num=slot_num)


class MMSEndlessSpool:
    def __init__(self):
        self.es_config = MMSEndlessSpoolConfig()
        self.log_flag = self.es_config.log_flag

        # Status
        self._enable = False
        self._activating = False

        self._runout_event = None

        # Klippy event handler
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_loggers()
        self._register()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_charge = printer_adapter.get_mms_charge()
        self.mms_delivery = printer_adapter.get_mms_delivery()
        self.mms_purge = printer_adapter.get_mms_purge()
        self.mms_swap = printer_adapter.get_mms_swap()

        self.mms_pause = self.mms.get_mms_pause()
        self.mms_resume = self.mms.get_mms_resume()
        self.mms_fil_detection = self.mms.get_mms_filament_detection()

        self._enable = self.mms.filament_detection_is_enabled() \
            and self.mms.endless_spool_is_enabled() \
            and self.mms_purge.is_enabled() \
            and self.mms_swap.is_enabled()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)

        self.log_info_s = mms_logger.create_log_info(console_output=False)
        self.log_warning_s = mms_logger.create_log_warning(
            console_output=False)
        self.log_error_s = mms_logger.create_log_error(console_output=False)

    # ---- Register ----
    def _register(self):
        for mms_slot in self.mms.get_mms_slots():
            slot_num = mms_slot.get_num()

            if self.mms.get_entry():
                # Entry is set
                mms_slot.entry.add_release_callback(
                    callback=self._handle_entry_is_released,
                    params={"slot_num":slot_num}
                )
            else:
                mms_slot.gate.add_release_callback(
                    callback=self._handle_gate_is_released,
                    params={"slot_num":slot_num}
                )

        # Register callbacks
        print_observer = self.mms.get_print_observer()
        print_observer.register_pause_callback(self.deactivate)
        print_observer.register_finish_callback(self.deactivate)
        # Start/resume activation shares MMS._start_print_monitors admission.

    # ---- Status ----
    def is_enabled(self):
        return self._enable

    def is_activating(self):
        return self._activating

    # ---- Handlers ----
    def _can_handle(self, slot_num):
        # Detection is the master switch. A running T owns its sensor edges;
        # only ordinary printing may create a new downstream runout request.
        if not self.mms.filament_detection_is_enabled() \
                or self.mms_resume.get_status()["kind"] is not None:
            return False
        slot_num_c = self.mms_charge.get_charged_slot()

        if self._activating \
            and self.mms.printer_is_printing() \
            and slot_num_c is not None \
            and slot_num == slot_num_c:
            return True

        # Not activating/not printing/self is not charged slot
        return False

    def _pause(self, slot_num, manual=False):
        # Called with the G-code mutex after the event ownership was checked.
        # Runout reports a physical slot, not a logical T mapping.
        tool, root = self.mms_swap.get_runout_request(slot_num)
        gcmd = gcode_adapter.easy_gcmd(
            command=self.mms_swap.format_command(tool))
        if not self.mms_resume.set_mms_swap_resume(
                gcmd=gcmd, physical_slot=slot_num,
                initial_slot=root):
            return None

        mms_slot = self.mms.get_mms_slot(slot_num)
        if manual:
            self.mms_resume.require_filament_clearance(
                slot_num, "downstream filament released; clear residual "
                "filament and refill the original Inlet before RESUME")
        # Pause print
        if not self.mms_pause.mms_pause(swap_gcmd=gcmd):
            # A stale sensor callback did not establish this pause. Do not
            # leave its newly registered task blocking a later tool change.
            self.mms_resume.clear_mms_swap_resume(gcmd)
            raise EndlessSpoolFailedError(
                f"slot[{slot_num}] endless spool pause failed",
                mms_slot
            )

        return gcmd if self.mms_resume.is_current_swap(gcmd) else None

    def _pause_and_hint(self, slot_num):
        if self._pause(slot_num, manual=True) is None:
            return
        self.mms_resume.release_for_manual()
        # No exception raise, activate led effect
        self.mms.get_mms_slot(slot_num).slot_led.activate_blinking()
        self.log_info(self.es_config.get_hint(slot_num))

    def _pause_and_wait(self, slot_num):
        gcmd = self._pause(slot_num)
        if gcmd is None:
            return None

        # Wait Toolhead idle
        is_idle = self.mms_delivery.wait_toolhead()
        if not self.mms_resume.is_current_swap(gcmd):
            return None
        if not is_idle:
            raise EndlessSpoolFailedError(
                f"slot[{slot_num}] endless spool wait toolhead idle timeout",
                self.mms.get_mms_slot(slot_num)
            )
        try:
            self.mms_delivery.wait_for_print_tail_release()
        except Exception as e:
            if self.mms_resume.is_current_swap(gcmd):
                self.mms_resume.note_failure(e)
                self.log_warning(f"slot[{slot_num}] tail release failed: {e}; "
                                 "check the selector, then RESUME")
            return None
        return gcmd if self.mms_resume.is_current_swap(gcmd) else None

    class TailPurgeProgress:
        """Distances accepted by E before and since the last segment clean."""
        def __init__(self):
            self.cleaned = 0.
            self.accepted = 0.
            self.entry_released = False

        def finished(self, slot, purge):
            if not slot.entry_is_set():
                return self.accepted >= purge.runout_distance - self.cleaned
            if slot.entry.is_released():
                self.entry_released = True
            return self.entry_released

        def account(self, amount):
            self.accepted += amount

    def _purge_tail(self, slot, progress):
        purge = self.mms_purge
        entry = slot.entry if slot.entry_is_set() else None
        limit = (purge.runout_max_distance if entry is not None
                 else purge.runout_distance)
        batch = purge.get_purge_distance()
        if batch <= 0.:
            raise EndlessSpoolFailedError(
                "runout purge distance must be positive", slot)
        def finished():
            return progress.finished(slot, purge)

        prepare = True
        with toolhead_adapter.temporary_fan_speed(0.):
            while progress.cleaned < limit:
                toolhead_adapter.check_motion()
                amount = min(batch, limit - progress.cleaned)
                pending = max(0., amount - progress.accepted)
                if pending and not finished():
                    if prepare:
                        purge.prepare_extrusion()
                        prepare = False
                    purge.move_to_tray()
                    if not finished():
                        purge.repay_retract(free_tail=True)
                        extruder_adapter.extrude(
                            pending, purge.get_purge_speed(),
                            stop_when=finished if entry is not None else None,
                            on_segment_accepted=progress.account)
                done = finished()
                if progress.accepted:
                    purge.runout_chunk_end(retract=not done)
                    # A failed cleaning hook leaves the accepted E in this
                    # segment until a retry completes its mechanical cleanup.
                    progress.cleaned += progress.accepted
                    progress.accepted = 0.
                if done:
                    return
        if entry is not None and not finished():
            raise EndlessSpoolFailedError(
                f"slot[{slot.get_num()}] Entry still has filament after "
                f"{limit} mm; check for a jam before RESUME", slot)

    # ---- Handlers ----
    def _source_path_present(self, slot):
        downstream = slot.entry if slot.entry.is_set() else slot.gate
        return downstream.is_triggered() and (
            slot.gate.is_triggered() or slot.inlet.is_released()
            or self.mms_resume.is_tail_available(slot.get_num()))

    def check_starting_source(self):
        source = self.mms_charge.get_charged_slot()
        if source is None or not self.mms.filament_detection_is_enabled():
            return False
        if self._source_path_present(self.mms.get_mms_slot(source)):
            return False
        self._handle_downstream_runout(source)
        return True

    def _handle_gate_is_released(self, slot_num):
        self._handle_downstream_runout(slot_num)

    def _handle_entry_is_released(self, slot_num):
        self._handle_downstream_runout(slot_num)

    def _handle_downstream_runout(self, slot_num):
        # A shared Entry dispatches to every slot. Claim the edge without
        # yielding, so later callbacks cannot apply it to a newly loaded slot.
        if self._runout_event is not None or not self._can_handle(slot_num):
            return
        slot = self.mms.get_mms_slot(slot_num)
        if not self.mms_swap.is_enabled():
            if not self._source_path_present(slot):
                self.mms_resume.request_reload(
                    slot_num, "downstream path lost; clear residual filament "
                    "and check the original Inlet before RESUME")
            return
        if slot.inlet.is_released():
            # Monitoring may start after the Inlet edge. The accepted source
            # and its current empty Inlet identify the same exhausted tail.
            self.mms_resume.handle_inlet_runout(slot_num)
            if not self._can_handle(slot_num):
                return
        self.log_info(
            f"slot[{slot_num}] downstream filament released {self.log_flag}")
        event = (self.mms_resume.get_status()["generation"], slot_num)
        self._runout_event = event
        printer_adapter.get_reactor().register_callback(
            lambda eventtime: self._process_runout(event))

    def _process_runout(self, event):
        generation, slot_num = event

        def current():
            return (self._runout_event is event
                    and self.mms_resume.get_status()["generation"] == generation
                    and self.mms_charge.get_charged_slot() == slot_num
                    and not self.mms.printer_is_shutdown()
                    and not print_stats_adapter.is_finished()
                    and not pause_resume_adapter.is_paused())

        try:
            if not current() or not self._can_handle(slot_num):
                return
            try:
                slot = self.mms.get_mms_slot(slot_num)
                slot.get_mms_buffer().deactivate_monitor(wait=False)
                slot.get_mms_drive().terminate_moving()
            except Exception as e:
                # Still stop file input if stopping the feeder fails.
                self.log_error_s(
                    f"slot[{slot_num}] runout feed stop failed: {e}"
                )
            if not current():
                return
            with self.mms_pause.background_pause(current) as owned:
                if not owned:
                    return
                if not self._enable \
                        or self.mms_resume.get_exhausted_slot() != slot_num:
                    self._pause_and_hint(slot_num)
                    return
                gcmd = self._pause_and_wait(slot_num)
                if gcmd is not None:
                    self.mms_resume.gcode_resume()
        except Exception as e:
            self.log_error_s(f"slot[{slot_num}] endless spool failed: {e}")
        finally:
            if self._runout_event is event:
                self._runout_event = None

    # ---- Control ----
    def activate(self):
        self._activating = True
        self.log_info_s("MMS endless spool is activated")
        if self._enable:
            self.mms_fil_detection.disable()

    def deactivate(self):
        self._activating = False
        self.log_info_s("MMS endless spool is deactivated")
        self.mms_fil_detection.recover()

    def can_clear_runout(self, slot_num):
        if self._enable:
            return True
        slot = self.mms.get_mms_slot(slot_num)
        return (self.mms_resume.get_exhausted_slot() == slot_num
                and self.mms_charge.get_charged_slot() != slot_num
                and slot.entry_is_set() and slot.entry.is_released())

    def purge_truncate(self, slot_num, loaded=None, progress=None):
        """Clear one known exhausted path; mapping belongs to the swap owner."""
        slot = self.mms.get_mms_slot(slot_num)
        # Known upstream tails can be withdrawn regardless of replacement
        # policy. Long downstream purging still requires automatic refill.
        if not self.can_clear_runout(slot_num):
            raise EndlessSpoolFailedError(
                "automatic refill disabled; clear downstream residual "
                "filament and refill the target Inlet before RESUME", slot)
        if loaded is None:
            loaded = self.mms_charge.get_charged_slot() == slot_num
        if slot.entry_is_set():
            pending_clean = progress is not None and progress.accepted > 0.
            if slot.entry.is_released() and not pending_clean:
                if loaded or slot.gate.is_released():
                    return
                # Withdraw what occupies Gate. Only a tail pulled back
                # across an empty Inlet needs the local remnant check.
                inlet_was_empty = slot.inlet.is_released()
                self.mms_delivery.unload_to_gate(slot_num, need_check=False)
                if inlet_was_empty:
                    slot.mark_tail_withdrawn()
                return
        elif not loaded:
            raise EndlessSpoolFailedError(
                "runout during charge without Entry; clear residual manually",
                slot)

        if not self.mms_delivery.mms_unselect():
            raise EndlessSpoolFailedError(
                "runout selector release failed", slot)
        if progress is None:
            progress = self.TailPurgeProgress()
        self._purge_tail(slot, progress)
