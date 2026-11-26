# Support for MMS Eject
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, fields

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.config import OptionalField, PrinterConfig
from ..core.exceptions import EjectFailedError, MMSException
from ..core.task import AsyncTask
from ..core.logger import log_time_cost
from ..hardware.stepper import MoveType


@dataclass(frozen=True)
class PrinterEjectConfig(PrinterConfig):
    # Z-axis lift distance during eject operations
    # Unit: mm
    z_raise: float = 1.0

    # Fast retraction before eject
    # Unit: mm
    fast_retract_distance: float = 8.0
    # Unit: mm/min
    fast_retract_speed: float = 10000.0

    # Optional short pull while the toolhead still presses the cutter.
    cut_hold_retract_distance: float = 0.0  # mm
    cut_hold_retract_speed: float = 120.0   # mm/min

    # Extruder retraction distance
    # Unit: mm
    retract_distance: float = 120.0
    # Extruder retraction speed
    # Unit: mm/min
    retract_speed: float = 600.0

    # Execute eject after print finish (0=disable, 1=enable)
    print_finish_exec: int = 1

    # Custom Macro
    custom_before: OptionalField = "MMS_EJECT_CUSTOM_BEFORE"
    custom_after: OptionalField = "MMS_EJECT_CUSTOM_AFTER"

    def get_type_handlers(self):
        handlers = super().get_type_handlers()
        # Older configurations omit this switch; retain automatic end eject.
        handlers[int] = lambda config, name: (
            config.getint(name, 1, minval=0, maxval=1)
            if name == "print_finish_exec" else config.getint(name))

        def get_float(config, name):
            if name == "cut_hold_retract_distance":
                value = config.getfloat(name, self.cut_hold_retract_distance)
                if not 0. <= value < float("inf"):
                    raise config.error(f"{name} must be finite and at least 0")
                return value
            if name == "cut_hold_retract_speed":
                value = config.getfloat(name, self.cut_hold_retract_speed)
                if not 0. < value < float("inf"):
                    raise config.error(f"{name} must be finite and above 0")
                return value
            return config.getfloat(name)

        handlers[float] = get_float
        return handlers


class MMSEject:
    def __init__(self, config):
        p_eject_config = PrinterEjectConfig(config)
        for field in fields(p_eject_config):
            key = field.name
            # Don't cover
            if not p_eject_config.should_skip(key) \
                and not hasattr(self, key):
                val = getattr(p_eject_config, key)
                setattr(self, key, val)

        self.reactor = printer_adapter.get_reactor()

        # State tracking
        self._is_running = False
        self.mms_cut = None
        self.reset_progress()
        self._finish_task = AsyncTask()
        self._finish_running = False
        self._finish_error = None
        self._finished_job = None
        self._finish_deadline = None
        self._finish_epoch = None

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
        self.mms_charge = printer_adapter.get_mms_charge()
        self.mms_cut = printer_adapter.get_mms_cut()
        self.mms_purge = printer_adapter.get_mms_purge()

    def _initialize_gcode(self):
        commands = [
            ("MMS_EJECT", self.cmd_MMS_EJECT),
            ("MMS_EJECT_UNSELECT", self.cmd_MMS_EJECT_UNSELECT),
            ("MMS_FINISH", self.cmd_MMS_FINISH),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        # Log would not output to console
        self.log_info_s = mms_logger.create_log_info(console_output=False)
        self.log_warning_s = mms_logger.create_log_warning(console_output=False)

    # ---- Status ----
    def is_running(self):
        return self._is_running

    def is_withdrawing_source(self, slot_num):
        progress = self._get_progress()
        if (not self.is_running() or progress is None
                or progress[2] != slot_num
                or progress[1] not in ("fast", "cut_return", "head")):
            return False
        slot = self.mms.get_mms_slot(slot_num)
        drive = slot.get_mms_drive()
        return (slot.gate.is_triggered() and drive.is_running()
                and drive.move_type == MoveType.EXTRUDER_MOVE
                and self.mms_delivery.is_exclusively_selected(slot_num))

    @contextmanager
    def _eject_is_running(self):
        self._is_running = True
        try:
            yield
        finally:
            self._is_running = False

    def finish_is_active(self):
        return self._finish_task.is_running()

    def get_finish_status(self):
        return {"active": self.finish_is_active(),
                "running": self._finish_running, "error": self._finish_error}

    def _finished_job_key(self):
        status = print_stats_adapter.get_current_status()
        if not print_stats_adapter.is_finished(status["state"]):
            return None
        # total_duration is frozen when Klipper records a terminal state.
        return (status["filename"], status["total_duration"])

    def request_finish(self, retry=False):
        """The observer schedules exactly one end operation for this job."""
        if self.finish_is_active():
            return True
        job = self._finished_job_key()
        if job is None or not retry and job == self._finished_job:
            return False
        generation = self.mms.get_mms_resume().get_status()["generation"]
        self._finish_error = None
        self._finish_deadline = self.reactor.monotonic() + 300.
        if not self._finish_task.setup(self._finish_print, {
                "job": job, "generation": generation, "retry": retry}):
            return False
        if job != self._finished_job:
            self._finish_epoch = None
        self._finished_job = job
        return bool(self._finish_task.start())

    def _check_finish_current(self):
        if self.reactor.monotonic() >= self._finish_deadline:
            raise MMSException("MMS end unload exceeded 300 seconds")
        if (
            self._finish_task.current_is_cancelled()
            or self._finished_job_key() != self._finished_job
            or self.mms.get_mms_resume().get_status()["generation"]
            != self._finish_epoch
        ):
            raise MMSException(
                "MMS end unload was cancelled or its print changed"
            )

    def _expire_finish(self, eventtime, job, generation, completion):
        # The callback may outlive its timer cancellation. Never stop a newer
        # finish attempt, print or manual request that now owns these devices.
        if (
            not self._finish_running
            or not self.finish_is_active()
            or self._finish_task.completion is not completion
            or self._finished_job_key() != job
            or self.mms.get_mms_resume().get_status()["generation"]
            != generation
        ):
            return self.reactor.NEVER
        if eventtime < self._finish_deadline:
            return self._finish_deadline
        self._finish_error = "MMS end unload exceeded 300 seconds"
        self.log_error(self._finish_error)
        # No G-code mutex or join here: a manual_home/drip/heat wait may own
        # the suspended finish stack. The owner joins and cleans up on exit.
        for label, request in (
            ("heater shutdown", self.mms.turn_off_all_heaters),
            (
                "feed stop request",
                lambda: self.mms_delivery.mms_stop(wait=False),
            ),
        ):
            try:
                if request() is False:
                    raise MMSException(f"{label} did not finish")
            except Exception as e:
                self.log_error(f"MMS end timeout {label}: {e}")
        return self.reactor.NEVER

    def _finish_print(self, job, generation, retry=False):
        gcode = printer_adapter.get_printer().lookup_object("gcode")
        with gcode.get_mutex():
            resume = self.mms.get_mms_resume()
            # A terminal notification may wait behind a new print or a manual
            # operation. It must not unload or turn off that newer job's heat.
            if (self._finished_job_key() != job
                    or resume.get_status()["generation"] != generation):
                return False
            self._finish_running = True
            success = False
            timeout_timer = None
            try:
                # Retrying this end task keeps its accepted eject milestones.
                # A new job or intervening request must cancel the old owner.
                if generation != self._finish_epoch:
                    resume.cancel_recovery()
                self._finish_epoch = resume.get_status()["generation"]
                epoch = self._finish_epoch
                tail = resume.get_exhausted_slot()
                if (self.print_finish_exec and tail is not None
                        and self._get_progress() is None
                        and self.mms_charge.get_charged_slot() == tail):
                    # Capture only the known source. The previous motion
                    # owner can still be draining until the join below.
                    self._eject_progress = (epoch, None, tail)
                    self._tail_retry_switches = None
                completion = self._finish_task.completion
                timeout_timer = self.reactor.register_timer(
                    lambda eventtime: self._expire_finish(
                        eventtime, job, epoch, completion
                    ),
                    self._finish_deadline,
                )
                self._check_finish_current()
                # Let the cancelled operation drain accepted movement and
                # release its own motor ownership before end unloading.
                stop_deadline = min(
                    self._finish_deadline,
                    self.reactor.monotonic()
                    + self.mms_delivery.d_config.wait_mms_stepper_timeout,
                )
                operations = (self.mms.mms_swap, self.mms_charge,
                              self, self.mms_purge, self.mms_cut)
                while any(operation.is_running() for operation in operations):
                    self._check_finish_current()
                    if self.reactor.monotonic() >= stop_deadline:
                        raise MMSException(
                            "previous MMS operation is still exiting; "
                            "retry MMS_FINISH after it stops"
                        )
                    self.reactor.pause(self.reactor.monotonic() + 0.1)
                self.mms_delivery.stop_and_join(deadline=self._finish_deadline)
                self._check_finish_current()
                if self.print_finish_exec:
                    if not retry and self.cutter_return_pending():
                        raise MMSException(
                            "Cutter return pending; check the mechanism "
                            "before retrying MMS_FINISH"
                        )
                    self.finish_pending_cutter_return()
                    if retry:
                        self._confirm_manual_tail()
                    lift = self.z_raise if self._eject_needs_toolhead() else 0.
                    with toolhead_adapter.motion_scope(
                            z_raise=lift, check=self._check_finish_current):
                        if not self.mms_eject_unselect():
                            raise MMSException(
                                "end unload failed; inspect the preceding "
                                "slot/sensor error"
                            )
                success = True
            except Exception as e:
                if self._finish_error is None:
                    self._finish_error = str(e)
            finally:
                if timeout_timer is not None:
                    self.reactor.unregister_timer(timeout_timer)
                # Close heat before a physical stop may wait. Each cleanup is
                # attempted even if a preceding one fails; active lasts until
                # every cleanup stack exits, without a persistent macro lock.
                for label, cleanup in (
                    ("heater shutdown", self.mms.turn_off_all_heaters),
                    ("feed stop", self.mms_delivery.mms_stop),
                    ("charge cleanup", self.mms_charge.teardown),
                ):
                    try:
                        if cleanup() is False:
                            raise MMSException(f"{label} did not finish")
                    except Exception as e:
                        success = False
                        self._finish_error = f"{label}: {e}"
                        self.log_error(self._finish_error)
                self._finish_running = False
            if success:
                self.log_info("MMS print finish complete - heaters off")
            else:
                self.log_error(
                    f"MMS print finish failed: {self._finish_error}; "
                    "repair and retry MMS_FINISH"
                )
            return success

    def cmd_MMS_FINISH(self, gcmd):
        if self.finish_is_active():
            gcmd.respond_info("MMS print finish is already running")
            return
        if self._finished_job_key() is None:
            raise gcmd.error(
                "MMS_FINISH requires a completed, cancelled or failed print"
            )
        if not self.request_finish(retry=True):
            raise gcmd.error("MMS_FINISH could not start")

    def _eject_needs_toolhead(self):
        progress = self._get_progress()
        if (progress is not None and progress[1] == "tail"
                and self._tail_purge is not None and self._tail_purge.accepted):
            return True
        return any(
            not self._entry_is_released(slot)
            for slot in self.find_eject_slots()
        )

    def _confirm_manual_tail(self):
        progress = self._get_progress()
        if progress is None or progress[1] != "manual_tail":
            return
        source = progress[2]
        if self._filament_still_in_toolhead(source):
            raise EjectFailedError(f"slot[{source}] Outlet is occupied; "
                                   "clear the old tail and retry ejection")
        self._set_progress(progress[0], "gates", None)

    def _entry_is_released(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        return mms_slot.entry_is_set() and mms_slot.entry.is_released()

    def _filament_still_in_toolhead(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        # With Entry configured, Outlet is only the buffer-full signal.
        downstream = mms_slot.entry if mms_slot.entry_is_set() \
            else mms_slot.outlet
        return downstream.is_triggered()

    # ---- Control ----
    def pause(self, period_seconds):
        toolhead_adapter.check_motion()
        self.reactor.pause(self.reactor.monotonic() + period_seconds)
        toolhead_adapter.check_motion()

    # ---- Eject ----
    def reset_progress(self):
        self._eject_progress = None
        self._tail_purge = None
        self._tail_retry_switches = None
        self._fast_retract_remaining = 0.

    def _get_progress(self):
        progress = self._eject_progress
        epoch = self.mms.get_mms_resume().get_status()["generation"]
        if progress is not None and progress[0] != epoch:
            self.reset_progress()
            return None
        return progress

    def _tail_switches(self, source):
        slot = self.mms.get_mms_slot(source)
        return tuple(pin.get_change_count()
                     for pin in (slot.gate, slot.selector, slot.inlet))

    def _set_progress(self, epoch, stage, source):
        toolhead_adapter.check_motion()
        self._record_progress(epoch, stage, source)

    def _record_progress(self, epoch, stage, source):
        if self.mms.get_mms_resume().get_status()["generation"] != epoch:
            raise EjectFailedError("eject request was cancelled")
        progress = (epoch, stage, source)
        if self._eject_progress is None or self._eject_progress[2] != source:
            self._tail_retry_switches = None
        if stage != "tail":
            self._tail_purge = None
        if progress != self._eject_progress:
            self.log_info_s(f"MMS eject stage={stage}, old_source={source}")
        self._eject_progress = progress

    def cutter_return_pending(self):
        return self.mms_cut is not None and self.mms_cut.cutter_return_pending()

    def get_status(self, eventtime=None):
        return {"cutter_return_pending": self.cutter_return_pending()}

    def finish_pending_cutter_return(self):
        returned = self.mms_cut.finish_pending_return()
        progress = self._get_progress()
        if progress is not None and progress[1] == "cut_return":
            epoch, _, source = progress
            self._record_progress(epoch, "head", source)
        return returned

    def check_cutter_ready(self):
        progress = self._get_progress()
        if progress is not None:
            stage = progress[1]
            if stage in ("cut_return", "head"):
                return
            if stage in ("gates", "verify") and not any(
                    self._filament_still_in_toolhead(s.get_num())
                    for s in self.mms.get_mms_slots()):
                return
        # A completed cut may hold the sensor until ejection finishes. New
        # head material after a gates/verify milestone needs a fresh cut.
        self.mms_cut.check_released()

    def check_paths(self, exhausted_source=None):
        """Validate physical candidates without treating ambiguity as damage."""
        slots = self.mms.get_mms_slots()
        gates = [s.get_num() for s in slots if s.gate.is_triggered()]
        broken = [n for n in gates if n != exhausted_source
                  and self.mms.get_mms_slot(n).inlet.is_released()]
        if broken:
            raise EjectFailedError(
                f"Gate occupied but Inlet empty in slots {broken}; "
                "clear the disconnected residual filament")
        downstream = [s.get_num() for s in slots
                      if self._filament_still_in_toolhead(s.get_num())]
        if downstream and not gates and exhausted_source is None:
            raise EjectFailedError(
                f"downstream filament at slots {downstream} has no Gate "
                "source; clear the untracked residual filament")
        progress = self._get_progress()
        source = (progress[2] if progress is not None
                  else self.mms_charge.get_charged_slot())
        if exhausted_source is not None:
            source = exhausted_source
        if (len(gates) > 1 and source not in gates
                and any(not self.mms.get_mms_slot(n).entry_is_set()
                        for n in gates)):
            raise EjectFailedError(
                "multiple Gate paths need Entry feedback or a known source; "
                "inspect the paths before ejecting")
        return gates

    def withdraw_needs_eject(self, slot_num):
        gates = self.check_paths()
        progress = self._get_progress()
        if progress is not None:
            if slot_num is None or progress[2] == slot_num:
                return True
            raise EjectFailedError("finish the pending MMS_EJECT first")
        if all(self._entry_is_released(n) for n in gates):
            return False
        if slot_num is None:
            return True
        if slot_num not in gates:
            return False
        source = self.mms_charge.get_charged_slot()
        if len(gates) == 1:
            return True
        if source in gates and not self._entry_is_released(source):
            return source == slot_num
        raise EjectFailedError("head source is unknown; use MMS_EJECT first")

    def find_eject_slots(self):
        progress = self._get_progress()
        source = (progress[2] if progress is not None
                  else self.mms_charge.get_charged_slot())
        exhausted = self.mms.get_mms_resume().get_exhausted_slot()
        blind_tail = (exhausted is not None and not
                      self.mms.get_mms_slot(exhausted).entry_is_set())
        if (source is None and exhausted is not None
                and (self._filament_still_in_toolhead(exhausted)
                     or blind_tail and progress is None)):
            raise EjectFailedError("exhausted toolhead source is unconfirmed; "
                                   "clear the residual filament")
        tail = source if source == exhausted else None
        slots = sorted(self.check_paths(tail))
        # A known free tail can still occupy Entry after leaving every Gate.
        if (tail is not None and tail not in slots
                and (blind_tail or self._filament_still_in_toolhead(tail))):
            slots.insert(0, tail)
        elif source in slots:
            slots.remove(source)
            slots.insert(0, source)
        return slots

    def release_other_selectors(self, slot_num):
        # Slots on one selector release each other mechanically.
        selector = self.mms.get_mms_slot(slot_num).get_mms_selector()
        if any(self.mms.get_mms_slot(n).get_mms_selector() is not selector
               for n in self.mms.get_selecting_slots()):
            self.mms_delivery.unselect(keep_selector=selector)
            toolhead_adapter.check_motion()

    def _select_exclusive(self, slot_num):
        self.release_other_selectors(slot_num)
        self.mms_delivery.select_slot(slot_num)
        toolhead_adapter.check_motion()

    def _unload_only(self, slot_num):
        toolhead_adapter.check_motion()
        self._select_exclusive(slot_num)
        self.mms_charge.invalidate_loaded_slot(slot_num)
        if not self.mms_delivery.mms_unload(slot_num):
            raise EjectFailedError(
                f"slot[{slot_num}] failed to exit Gate; repair and retry",
                self.mms.get_mms_slot(slot_num))

    def unload_others(self, keep_slot, park=True):
        """Retire unrelated upstream paths, preserving the requested strand."""
        exhausted = self.mms.get_mms_resume().get_exhausted_slot()
        exempt = keep_slot if exhausted == keep_slot else None
        slots = self.check_paths(exempt)
        others = [n for n in slots if n != keep_slot]
        if not others:
            return
        self.mms_delivery.pause_feeds(self.mms.get_slot_nums())
        if park and self.mms_purge.is_enabled():
            self.mms_purge.move_to_tray()
        toolhead_adapter.check_motion()
        with self._eject_is_running():
            # A known exhausted head can remain while independent strands
            # retreat. Otherwise this shortcut requires an empty Entry.
            known_head = self.mms_charge.get_charged_slot() == keep_slot
            if (exempt is None and not known_head
                    and any(not self._entry_is_released(n) for n in slots)):
                raise EjectFailedError("head must exit before upstream unload")
            for n in self.check_paths(exempt):
                if n != keep_slot:
                    self._unload_only(n)
                    toolhead_adapter.check_motion()

    def select_loaded_slot(self, slot_num):
        """Retire other Gates and restore the accepted strand's grip."""
        resume = self.mms.get_mms_resume()
        slot = self.mms.get_mms_slot(slot_num)
        released_tail = (resume.is_tail_available(slot_num)
                         and slot.selector.is_released()
                         and slot.get_mms_selector().get_focus_slot()
                         != slot_num)
        self.unload_others(slot_num)
        if not released_tail and resume.can_drive_filament(
                slot_num, require_selection=False):
            self._select_exclusive(slot_num)
        else:
            # The extruder alone owns this tail; release the last side slot.
            self.mms_delivery.unselect()

    def prepare_charge(self, target, gcmd=None):
        """One exit boundary for fresh loads and unfinished-feed retries."""
        slots = self.check_paths()
        progress = self._get_progress()
        if progress is not None or any(n != target for n in slots):
            known_head = (target in slots
                          and self.mms_charge.get_charged_slot() == target)
            if progress is None and (
                    known_head or all(self._entry_is_released(n)
                                      for n in slots)):
                self.unload_others(target, park=gcmd is None)
            elif gcmd is not None:
                self._manual_eject(gcmd)
            elif not self.mms_eject():
                return False
        self.release_other_selectors(target)
        return True

    def _exec_custom_macro(self, macro, position):
        if macro:
            self.log_info(
                f"MMS execute macro {position} EJECT:\n"
                f"{macro}"
            )
            gcode_adapter.run_command(macro, strict=True)

    def verify_pending_eject(self):
        progress = self._get_progress()
        if progress is None:
            return
        if progress[1] != "verify":
            raise EjectFailedError("eject motion is unfinished; retry ejection")
        self._exec_custom_macro(self.custom_after, "after")
        self.mms_cut.check_released()
        slots = self.mms.get_mms_slots()
        for slot in slots:
            downstream = slot.entry if slot.entry_is_set() else slot.outlet
            for pin in (slot.gate, downstream):
                if pin.is_triggered():
                    raise EjectFailedError(
                        f"slot[{slot.get_num()}] eject incomplete: "
                        f"{pin.get_pin_type()} still detects filament; retry",
                        slot)
        # A detached tail can finish after the eject stage gave up its
        # source. Refresh occupancy only once every path passed clearance.
        for slot in slots:
            slot.update_filament_presence()
        self._set_progress(progress[0], "verify", progress[2])
        self.reset_progress()
        self.mms.get_mms_resume().accept_cleared_path()

    def _exit_head(self, slots, source):
        head = next((n for n in slots if not self._entry_is_released(n)), None)
        if head is None:
            return
        for n in slots:
            slot = self.mms.get_mms_slot(n)
            if not slot.gate.is_triggered():
                continue
            self._select_exclusive(n)
            known = source == n
            if known:
                self.mms_delivery.clear_buffer(n)
                # Relaxation can already have released Entry.
                if self._entry_is_released(head):
                    return
            entry = self.mms.get_mms_slot(head).entry
            stop = (lambda: entry.is_released()) if entry.is_set() else None
            if not known:
                stop = lambda: entry.is_released() or slot.gate.is_released()
            with slot.get_mms_drive().sync_with_extruder():
                extruder_adapter.retract(
                    self.retract_distance, self.retract_speed, stop_when=stop)
            toolhead_adapter.check_motion()
            head_slot = self.mms.get_mms_slot(head)
            downstream = entry if entry.is_set() else head_slot.outlet
            for pin in (downstream, slot.gate):
                pin.refresh_state()
                toolhead_adapter.check_motion()
            if slot.gate.is_released():
                self.mms_delivery._safety_retract(n)
            if not self._filament_still_in_toolhead(head):
                return
            if known and self.mms.get_mms_resume().get_exhausted_slot() == n:
                break
        raise EjectFailedError(
            f"Entry did not release after slots {slots}; repair and retry")

    def _cut_for_eject(self, epoch, source):
        def pull_while_pressed():
            self._record_progress(epoch, "cut_return", source)
            if not self.cut_hold_retract_distance or source is None:
                return
            self._pull_against_cutter(source)

        return self.mms_cut.mms_cut(
            on_pressed=pull_while_pressed,
            on_returned=lambda: self._record_progress(epoch, "head", source))

    def _pull_against_cutter(self, source):
        slot = self.mms.get_mms_slot(source)
        resume = self.mms.get_mms_resume()

        def grip_lost():
            return (slot.gate.is_released() or self._entry_is_released(source)
                    or (slot.inlet.is_released()
                        and resume.get_exhausted_slot() != source)
                    or not self.mms_delivery.is_exclusively_selected(source)
                    or not resume.can_drive_filament(source, forward=False))

        toolhead_adapter.check_motion()
        if grip_lost():
            return
        with slot.get_mms_drive().sync_with_extruder():
            extruder_adapter.retract(self.cut_hold_retract_distance,
                                     self.cut_hold_retract_speed,
                                     stop_when=grip_lost)

    def _standard_eject(self):
        self.finish_pending_cutter_return()
        progress = self._get_progress()
        resume = self.mms.get_mms_resume()
        epoch = resume.get_status()["generation"]
        source = progress[2] if progress is not None else None
        stage = progress[1] if progress is not None else None
        slots = self.find_eject_slots()
        occupied = any(self._filament_still_in_toolhead(s.get_num())
                       for s in self.mms.get_mms_slots())
        # New head material after an empty-head milestone needs a fresh cut.
        if stage in ("gates", "verify") and occupied:
            self.reset_progress()
            progress = None
            stage, source = None, None
        if stage is None:
            self._exec_custom_macro(self.custom_before, "before")
            slots = self.find_eject_slots()
            if progress is None:
                source = self.mms_charge.get_charged_slot()
            if source not in slots:
                source = slots[0] if len(slots) == 1 else None
        self.mms_delivery.pause_feeds(self.mms.get_slot_nums())
        toolhead_adapter.check_motion()
        # Manual repair can finish withdrawal while a cut/head retry waits.
        if stage == "manual_tail":
            raise EjectFailedError(
                "clear the toolhead tail, then confirm removal by retrying "
                "MMS_FINISH or MMS_EJECT")
        tail_cleanup = (stage == "tail" and self._tail_purge is not None
                        and self._tail_purge.accepted)
        if (stage == "verify" and slots and not occupied
                or not tail_cleanup
                and stage in (None, "fast", "tail", "cut", "head")
                and all(self._entry_is_released(n) for n in slots)):
            if stage == "tail":
                # Only the detached piece occupied the head. Upstream Gates
                # are separate strands, including a refilled original slot.
                source = None
            self._set_progress(epoch, "gates", source)
            stage = "gates"
        if stage in (None, "fast", "tail", "cut", "head"):
            if not toolhead_adapter.is_homed():
                raise EjectFailedError("home XYZ before ejecting from head")
            only_clean = (stage == "tail" and self._tail_purge is not None
                          and self._tail_purge.finished(
                              self.mms.get_mms_slot(source), self.mms_purge))
            if not only_clean:
                extruder_adapter.heat_to_min_temp()
                if (stage in (None, "fast", "tail")
                        and self.mms_cut.is_enabled()):
                    self.mms_cut.check_released()
            toolhead_adapter.check_motion()
        tail = source if source == resume.get_exhausted_slot() else None
        withdraw_tail = tail is not None and resume.can_drive_filament(tail)
        if (tail is not None and progress is not None
                and self._tail_retry_switches is not None):
            # The same tail may have travelled back across Inlet before the
            # fault. A release/reselection or refill during repair revokes
            # that proof even if the final switch levels happen to match.
            slot = self.mms.get_mms_slot(tail)
            before_pull = (stage is None or stage == "fast" and
                           self._fast_retract_remaining
                           == abs(self.fast_retract_distance))
            gripped = resume.can_drive_filament(tail, forward=before_pull)
            exited_gate = stage == "gates" and slot.gate.is_released()
            unchanged = self._tail_retry_switches == self._tail_switches(tail)
            withdraw_tail = unchanged and (gripped or exited_gate)
            if withdraw_tail:
                self._tail_retry_switches = None
        if stage in (None, "fast", "tail", "cut", "head"):
            if tail is not None and not withdraw_tail:
                if stage in ("cut", "head"):
                    raise EjectFailedError(
                        f"slot[{tail}] lost its upstream grip during "
                        "withdrawal; clear the remaining toolhead filament")
                self._set_progress(epoch, "tail", source)
                stage = "tail"
            if stage == "tail":
                # Separate pieces cannot be withdrawn together. Use the
                # existing E-only tail policy before touching upstream Gates.
                spool = self.mms.get_mms_endless_spool()
                if (not self.mms.get_mms_slot(source).entry_is_set()
                        and not spool.is_enabled()):
                    self._set_progress(epoch, "manual_tail", source)
                    raise EjectFailedError(
                        f"slot[{source}] has no Entry feedback; clear its "
                        "toolhead tail, then retry ejection to confirm")
                if not only_clean and not self.mms_cut.is_enabled():
                    # E pushes forward even when cutting strokes are disabled.
                    self.mms_cut.check_released()
                    toolhead_adapter.check_motion()
                if self._tail_purge is None:
                    self._tail_purge = spool.TailPurgeProgress()
                spool.purge_truncate(
                    source, loaded=True, progress=self._tail_purge)
                if self._filament_still_in_toolhead(source):
                    raise EjectFailedError("exhausted toolhead tail remains")
                source = None
                withdraw_tail = False
                self._set_progress(epoch, "gates", source)
                stage = "gates"
            if stage is None:
                self._fast_retract_remaining = abs(self.fast_retract_distance)
                self._set_progress(epoch, "fast", source)
                stage = "fast"
            if stage == "fast":
                if source in slots:
                    self._select_exclusive(source)
                    self.mms_charge.invalidate_loaded_slot(source)
                    drive = self.mms.get_mms_slot(source).get_mms_drive()
                    sync = drive.sync_with_extruder()
                else:
                    self.mms_delivery.unselect()
                    self.mms_charge.invalidate_loaded_slot()
                    sync = nullcontext()
                owner = self._eject_progress

                def account(moved):
                    if self._eject_progress is owner:
                        self._fast_retract_remaining = max(
                            0., self._fast_retract_remaining - abs(moved))

                if self._fast_retract_remaining:
                    with sync:
                        extruder_adapter.retract(
                            self._fast_retract_remaining,
                            self.fast_retract_speed,
                            on_segment_accepted=account)
                self._set_progress(epoch, "cut", source)
                stage = "cut"
            if stage == "cut":
                if self.mms_cut.is_enabled() and not self._cut_for_eject(
                        epoch, source):
                    raise EjectFailedError("cut failed; see the cutter error")
                self._set_progress(epoch, "head", source)
                stage = "head"
            if stage == "head":
                if self.mms_purge.is_enabled():
                    self.mms_purge.move_to_tray()
                if source is not None and resume.get_exhausted_slot() == source:
                    if not resume.can_drive_filament(source, forward=False):
                        raise EjectFailedError(
                            f"slot[{source}] lost its grip during withdrawal")
                    candidates = [source]
                else:
                    candidates = self.find_eject_slots()
                self._exit_head(candidates, source)
                self._set_progress(epoch, "gates", source)
                stage = "gates"
        if stage == "gates":
            # Inlet can empty during either synchronized pull. Its accepted
            # owner finishes the old strand even if it recrosses Inlet.
            if (tail is None and source is not None
                    and resume.get_exhausted_slot() == source):
                tail = source
                withdraw_tail = True
            if withdraw_tail:
                slot = self.mms.get_mms_slot(tail)
                if self._filament_still_in_toolhead(tail):
                    raise EjectFailedError("head path changed during unload")
                if slot.gate.is_triggered():
                    self.mms_delivery.unload_to_gate(tail, need_check=False)
                slot.mark_tail_withdrawn()
                if slot.gate.is_triggered():
                    raise EjectFailedError(f"slot[{tail}] did not exit Gate")
            if tail is not None:
                # The head is empty. A later side-slot retry must not claim
                # a replacement strand as the withdrawn remnant.
                source = None
                self._set_progress(epoch, "gates", source)
            for n in self.find_eject_slots():
                if self._filament_still_in_toolhead(n):
                    raise EjectFailedError("head path changed during unload")
                self._unload_only(n)
                toolhead_adapter.check_motion()
            self._set_progress(epoch, "verify", source)
            self.mms_charge.teardown()
            self.mms_purge.clear_retract()
        return True

    def mms_eject(self):
        if self.is_running():
            self.log_warning("another eject is running")
            return False
        epoch = self.mms.get_mms_resume().get_status()["generation"]
        try:
            with self._eject_is_running():
                self._standard_eject()
                self.verify_pending_eject()
            msg = "mms eject finish"
            gcode_adapter.respond_echo(msg)
            self.log_info_s(msg)
            return True
        except Exception as e:
            progress = self._get_progress()
            if (progress is not None and progress[0] == epoch
                    and progress[1] in (None, "fast", "cut", "cut_return",
                                        "head", "gates")
                    and progress[2] is not None and progress[2]
                    == self.mms.get_mms_resume().get_exhausted_slot()
                    and self._tail_retry_switches is None):
                self._tail_retry_switches = self._tail_switches(progress[2])
            self.log_error(f"mms eject error: {e}")
            gcode_adapter.respond_error("mms eject failed")
            return False

    def mms_eject_unselect(self):
        if not self.mms_eject():
            return False
        return self.mms_delivery.mms_unselect()

    # ---- GCode ----
    def _manual_eject(self, gcmd, unselect=False):
        """Run inside the caller's maintenance task, including homing/return."""
        resume = self.mms.get_mms_resume()
        generation = resume.get_status()["generation"]

        def check_current():
            if (self.mms_delivery.async_task_sp.current_is_cancelled()
                    or resume.get_status()["generation"] != generation):
                raise gcmd.error(f"{gcmd.get_command().strip()} was cancelled")

        check_current()
        self.finish_pending_cutter_return()
        if gcmd.get_command() in ("MMS_EJECT", "MMS_EJECT_UNSELECT"):
            self._confirm_manual_tail()
        needs_head = self._eject_needs_toolhead()
        if needs_head and not toolhead_adapter.is_homed():
            # The printer's G28 override owns TAP cleaning and safe homing.
            gcode_adapter.run_command("G28", strict=True)
        check_current()
        with toolhead_adapter.motion_scope(
            z_raise=self.z_raise if needs_head else 0.0, check=check_current
        ) as state:
            action = self.mms_eject_unselect if unselect else self.mms_eject
            if not action():
                raise gcmd.error(f"{gcmd.get_command().strip()} failed")
            if needs_head:
                toolhead_adapter.return_to_saved(state, clearance=self.z_raise)
        return True

    @log_time_cost("log_info_s")
    def cmd_MMS_EJECT(self, gcmd):
        self.mms.check_maintenance(gcmd)
        self.mms_delivery.deliver_task(
            self._manual_eject, {"gcmd": gcmd}, wait=True)

    @log_time_cost("log_info_s")
    def cmd_MMS_EJECT_UNSELECT(self, gcmd):
        self.mms.check_maintenance(gcmd)
        self.mms_delivery.deliver_task(
            self._manual_eject, {"gcmd": gcmd, "unselect": True}, wait=True)


def load_config(config):
    return MMSEject(config)
