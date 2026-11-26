# Support for MMS Charge
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
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
from ..core.config import OptionalField, PrinterConfig
from ..core.exceptions import (
    ChargeFailedError, ChargeFeedRetrySignal, DeliveryFailedError,
)
from ..core.logger import log_time_cost


@dataclass(frozen=True)
class PrinterChargeConfig(PrinterConfig):
    # Z-axis lift distance during filament charging operations
    # Unit: mm
    z_raise: float = 1.0

    # Sprint distance before charge
    sprint_distance: float = 100.0 # mm

    # Extrude distance to fill space
    # from entry/outlet triggered to nozzle.
    fill_extrude_distance: float = 75.0  # mm
    fill_extrude_speed: float = 600.0    # mm/min

    # Custom Macro
    custom_before: OptionalField = "MMS_CHARGE_CUSTOM_BEFORE"
    custom_after: OptionalField = "MMS_CHARGE_CUSTOM_AFTER"

    def get_type_handlers(self):
        handlers = super().get_type_handlers()

        def get_float(config, name):
            value = config.getfloat(name)
            if (name in ("sprint_distance", "fill_extrude_distance")
                    and not 0. <= value < float("inf")):
                raise config.error(
                    f"{name} must be finite and greater than or equal to 0")
            if name == "fill_extrude_speed" and not 0. < value < float("inf"):
                raise config.error(f"{name} must be finite and greater than 0")
            return value

        handlers[float] = get_float
        return handlers


class MMSCharge:
    def __init__(self, config):
        p_charge_config = PrinterChargeConfig(config)
        for field in fields(p_charge_config):
            key = field.name
            # Don't cover
            if not p_charge_config.should_skip(key) \
                and not hasattr(self, key):
                val = getattr(p_charge_config, key)
                setattr(self, key, val)

        self.reactor = printer_adapter.get_reactor()

        # State tracking
        self._is_running = False
        self._charged_slot_num = None

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
        self.mms_purge = printer_adapter.get_mms_purge()
        self.mms_cut = printer_adapter.get_mms_cut()
        self.mms_resume = self.mms.get_mms_resume()
        self.mms_fil_detection = self.mms.get_mms_filament_detection()

    def _initialize_gcode(self):
        commands = [
            ("MMS_CHARGE", self.cmd_MMS_CHARGE),
            # ("MMS_CAREFUL_CHARGE", self.cmd_MMS_CAREFUL_CHARGE),
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
    def is_running(self):
        return self._is_running

    @contextmanager
    def _charge_is_running(self):
        self._is_running = True
        try:
            yield
        finally:
            self._is_running = False

    def get_charged_slot(self):
        return self._charged_slot_num

    def invalidate_loaded_slot(self, slot_num=None):
        """Discard acceptance before cutting or moving an upstream path."""
        if slot_num is None or self._charged_slot_num == slot_num:
            self._charged_slot_num = None
        eject = printer_adapter.get_mms_eject()
        if not eject.is_running():
            eject.reset_progress()

    def teardown(self):
        self._is_running = False
        self._charged_slot_num = None

    # ---- Control ----
    def _exec_custom_macro(self, macro, position):
        if macro:
            self.log_info(
                f"MMS execute macro {position} CHARGE:\n"
                f"{macro}"
            )
            gcode_adapter.run_command(macro, strict=True)

    def _pause_mms_buffer(self, slot_num):
        self.mms_delivery.pause_feeds([slot_num])

    def _safety_checks(self, slot_num):
        if slot_num is None:
            self.log_warning("current slot is None, return")
            return False

        if self.is_running():
            msg = "another mms charge is running, skip"
            gcode_adapter.respond_error(msg)
            self.log_warning(msg)
            return False

        # Check extruder
        if not extruder_adapter.is_hot_enough():
            return False

        return True

    @contextmanager
    def charge_scope(self, slot_num, feeding=True):
        """Keep feeding and acceptance under one monitored operation."""
        self.mms.get_mms_slot(slot_num).check_load_allowed()
        # Preserve the pre-feed hook before temperature validation. An
        # acceptance retry must not repeat that hook's preparation actions.
        if feeding:
            self._exec_custom_macro(self.custom_before, "before")
        if not self._safety_checks(slot_num):
            raise ChargeFailedError(f"slot[{slot_num}] charge not ready")
        try:
            with self._charge_is_running(), \
                    toolhead_adapter.temporary_fan_speed(0.), \
                    self.mms_fil_detection.monitor(slot_num, configured=True):
                yield
        except Exception:
            self._charged_slot_num = None
            try:
                self._pause_mms_buffer(slot_num)
            except Exception as error:
                self.log_error(f"charge feed cleanup failed: {error}")
            raise

    def load_filament(self, slot_num, progress=None):
        """Load and accept filament, returning whether this call pre-cleaned."""
        precleaned = False
        stage = (progress.get_status()["stage"]
                 if progress is not None else "feed")
        feeding = stage in ("charge", "feed")
        if feeding and progress is not None:
            progress.advance_stage("feed")
        try:
            guard = progress.check_swap_path if progress is not None else None
            with self.charge_scope(slot_num, feeding=feeding), \
                    toolhead_adapter.motion_scope(check=guard):
                if feeding:
                    if self.mms_purge.is_enabled():
                        self.mms_purge.move_to_tray()
                else:
                    self.mms_delivery.select_slot(slot_num)
                    if progress is not None:
                        progress.check_swap_path()
                        progress.check_loading_request()
                if ((feeding or stage == "fill") and self.fill_extrude_distance
                        and self.mms_purge.is_enabled()):
                    self.mms_purge.prepare_extrusion()
                    precleaned = True
                if feeding:
                    self.feed_filament(slot_num)
                    stage = "fill"
                    if progress is not None:
                        progress.advance_stage(stage)
                if stage == "fill":
                    work = progress.stage_work if progress else None
                    self.fill_filament(slot_num, work)
                    if progress is not None:
                        progress.advance_stage("charge_check")
                work = progress.stage_work if progress is not None else None
                if (stage == "charge_check" and work is not None
                        and work.pop("extruder_exit_empty", False)
                        and progress.is_resuming()
                        and not self._exit_triggered()):
                    precleaned = self._feed_to_extruder_exit(slot_num, work)
                self.verify_charge(slot_num, work)
                if progress is not None:
                    progress.check_loading_request()
            # The Inlet may have emptied during fill or the acceptance hook.
            # Commit this handoff only after every charge check has succeeded.
            if self.mms_resume.get_exhausted_slot() == slot_num:
                self.mms_resume.handoff_print_tail(slot_num)
        except ChargeFeedRetrySignal as e:
            if progress is None:
                raise
            progress.advance_stage("feed")
            raise ChargeFeedRetrySignal(
                f"slot[{slot_num}] feed incomplete: {e}; "
                "fix the feed path, then RESUME to retry this slot") from e
        except Exception:
            # A hook may wrap runout; the request owner retains the cause.
            if progress is not None:
                progress.check_loading_request()
            raise
        return precleaned

    def feed_filament(self, slot_num):
        """Prepare upstream feed before the extruder takes over."""
        self.invalidate_loaded_slot()
        self.log_info_s(f"slot[{slot_num}] mms charge begin")
        mms_slot = self.mms.get_mms_slot(slot_num)
        # Make sure mms_buffer is idle
        self._pause_mms_buffer(slot_num)

        # Skip wanted slot
        self.mms_delivery.unload_loading_slots(
            skip_slot=slot_num)

        # A cutter may remain held until post-cut retraction. Require
        # release only now, immediately before feeding new filament.
        self.mms_cut.check_released()
        # Starting a new loading/fill sequence replaces the old
        # filament's purge compensation, including swap retries.
        self.mms_purge.clear_retract()
        # Load wanted slot
        # Entry already occupied (including manual repair) may mean that
        # the extruder has grip. Never repeat an upstream-only sprint then.
        if mms_slot.entry_is_set():
            mms_slot.entry.refresh_state()
            toolhead_adapter.check_motion()
        entry_loaded = (mms_slot.entry_is_set()
                        and mms_slot.entry.is_triggered())
        if not entry_loaded:
            mms_slot.get_mms_buffer().invalidate_reference()
        if mms_slot.entry_is_set():
            try:
                self.mms_delivery.load_to_entry(slot_num)
            except DeliveryFailedError as e:
                # A latched runout/fracture takes precedence over
                # a mechanical feed failure, even after refilling.
                self.mms_fil_detection.check(slot_num)
                if not mms_slot.entry.is_triggered():
                    raise ChargeFeedRetrySignal(str(e)) from e
                raise
        else:
            self.mms_delivery.load_to_outlet(slot_num)
        # Loading skips its selector move when the destination
        # is already triggered (for example after manual repair).
        self.mms_delivery.select_slot(slot_num)
        self.mms_fil_detection.check(slot_num)

        # A known tail still needs extruder takeover and acceptance, but
        # cannot perform the normal upstream buffer preparation.
        if (self.sprint_distance > 0. and not entry_loaded
                and not self.mms_resume.is_tail_available(slot_num)):
            self.mms_delivery.mms_drive_sprint(
                slot_num, -self.sprint_distance)
            self.mms_fil_detection.check(slot_num)
            if (mms_slot.buffer_runout.is_set()
                    and mms_slot.outlet.is_triggered()
                    and not self.mms_resume.is_tail_available(slot_num)):
                # Leave headroom before the final approach to the gears.
                self.mms_delivery.clear_buffer(slot_num)
                self.mms_fil_detection.check(slot_num)
            if mms_slot.entry_is_set():
                # Equal motor travel need not restore the filament position.
                # Reacquire Entry using bounded delivery and its retries.
                config = self.mms_delivery.pd_config
                self.mms_delivery.load_to_entry(
                    slot_num, distance=self.sprint_distance,
                    speed=config.sprint_speed, accel=config.sprint_accel)
            else:
                self.mms_delivery.mms_drive_sprint(
                    slot_num, self.sprint_distance)
            self.mms_fil_detection.check(slot_num)
        if mms_slot.entry_is_set():
            # Sprint may retract Entry. Query after motion, not a delayed edge.
            mms_slot.entry.refresh_state()
            toolhead_adapter.check_motion()

    def fill_filament(self, slot_num, work=None):
        """Keep the accepted E distance across local Entry recoveries."""
        slot = self.mms.get_mms_slot(slot_num)
        if work is None:
            work = {}
        remaining = work.setdefault("remaining", self.fill_extrude_distance)
        if remaining > 0.:
            self._pause_mms_buffer(slot_num)
        attempts = self.mms.get_retry_times()
        for attempt in range(attempts):
            if attempt:
                remaining = work["remaining"]
                if not self._recover_fill_entry(
                        slot, remaining, attempt, attempts - 1):
                    if attempt + 1 < attempts:
                        continue
                    raise ChargeFeedRetrySignal(
                        f"Entry still released on final fill retry {attempt}, "
                        f"{remaining:.2f} mm of E remaining")
                if not remaining:
                    return True
            try:
                self._fill_remaining(slot, work)
                return True
            except ChargeFeedRetrySignal:
                if attempt + 1 == attempts:
                    raise

    def _recover_fill_entry(self, slot, remaining, retry, limit):
        slot_num = slot.get_num()

        # loading_feed and the interrupted E move have both drained. Check
        # ownership across every wait before allowing an upstream-only move.
        with toolhead_adapter.motion_scope(
                check=lambda: self._check_retry_source(slot)):
            slot.entry.refresh_state()
            toolhead_adapter.check_motion()
            if slot.entry.is_triggered():
                self.log_info(f"slot[{slot_num}] Entry restored; "
                              f"continuing fill with {remaining:.2f} mm of E")
                return True
            if not remaining:
                raise ChargeFeedRetrySignal("Entry still released after fill")
            if not self.sprint_distance:
                raise ChargeFeedRetrySignal(
                    "Entry still released and sprint_distance is zero")
            self.log_info(f"slot[{slot_num}] seeking Entry, retry "
                          f"{retry}/{limit}, {remaining:.2f} mm of E left")
            delivery = self.mms_delivery
            config = delivery.pd_config
            reached, moved = delivery.deliver_once(
                slot_num, delivery.pin_type.entry, True, True,
                self.sprint_distance, config.sprint_speed, config.sprint_accel)
            self.log_info_s(f"slot[{slot_num}] fill Entry seek moved "
                            f"{moved:.2f} mm, reached={reached}")
            return reached

    def _fill_remaining(self, mms_slot, work):
        slot_num = mms_slot.get_num()
        distance = work["remaining"]
        if distance > 0.:
            self._check_fill_source(slot_num)
            if not self.mms_delivery.is_exclusively_selected(slot_num):
                raise ChargeFailedError(
                    f"slot[{slot_num}] is not selected for fill",
                    mms_slot)

            def accepted(amount):
                work["remaining"] = max(0., work["remaining"] - amount)

            def check_fill():
                self._check_fill_source(slot_num)
                # Once all E is accepted, confirm Entry live after drain.
                if (work["remaining"] and mms_slot.entry_is_set()
                        and mms_slot.entry.is_released()):
                    raise ChargeFeedRetrySignal("Entry released during fill")

            with mms_slot.get_mms_buffer().loading_feed(slot_num):
                if mms_slot.entry_is_set():
                    mms_slot.entry.refresh_state()
                    toolhead_adapter.check_motion()
                self.log_info_s(
                    f"slot[{slot_num}] fill {distance} mm "
                    f"at {self.fill_extrude_speed} mm/min with buffer feedback")
                self.mms_delivery.move_with_buffer(
                    slot_num, distance, self.fill_extrude_speed,
                    check=check_fill, on_segment_accepted=accepted)
        if mms_slot.entry_is_set():
            mms_slot.entry.refresh_state()
            toolhead_adapter.check_motion()
        if mms_slot.entry.is_set() and mms_slot.entry.is_released():
            raise ChargeFeedRetrySignal(
                "Entry is not triggered after feed preparation and fill"
            )

    def _check_fill_source(self, slot_num):
        self.mms_fil_detection.check(slot_num)
        if self.mms_resume.can_drive_filament(slot_num):
            return
        reason = f"slot[{slot_num}] exhausted during fill"
        if self.mms_resume.require_filament_clearance(
                slot_num, reason, runout=True):
            self.mms_resume.check_loading_request()
        raise ChargeFailedError(reason, self.mms.get_mms_slot(slot_num))

    def _check_retry_source(self, slot):
        slot_num = slot.get_num()
        self._check_fill_source(slot_num)
        gates = [s.get_num() for s in self.mms.get_mms_slots()
                 if s.gate.is_triggered()]
        if (not slot.is_ready() or gates != [slot_num]
                or self.mms_resume.get_exhausted_slot() is not None
                or not self.mms_delivery.is_exclusively_selected(slot_num)):
            raise ChargeFailedError(
                f"slot[{slot_num}] fill retry lost its feed path", slot)

    def _exit_triggered(self):
        return self.mms.exit_sensor_state == "PRESSED"

    def _check_extruder_exit(self, slot_num, work):
        if self.mms.exit_sensor_state == "RELEASED":
            if work is not None:
                work["extruder_exit_empty"] = True
            raise ChargeFailedError(
                f"slot[{slot_num}] extruder exit sensor RELEASED after feed; "
                "check the feed path, then retry",
                self.mms.get_mms_slot(slot_num))

    def _feed_to_extruder_exit(self, slot_num, work):
        slot = self.mms.get_mms_slot(slot_num)
        if (not self.fill_extrude_distance or not slot.entry_is_set()
                or not self.mms_purge.is_enabled()):
            return False
        buffer = slot.get_mms_buffer()

        def check_source():
            self._check_retry_source(slot)
            if slot.entry.is_released():
                raise ChargeFailedError(
                    f"slot[{slot_num}] Entry released while feeding to the "
                    "extruder exit sensor", slot)

        self._pause_mms_buffer(slot_num)
        slot.entry.refresh_state()
        with toolhead_adapter.motion_scope(check=check_source):
            self.mms_cut.check_released()
            self.mms_purge.move_to_tray()
            self.mms_purge.prepare_extrusion()
            if not self._exit_triggered():
                with buffer.loading_feed(slot_num):
                    self.log_info(f"slot[{slot_num}] RESUME feeding to the "
                                  "extruder exit sensor, limit "
                                  f"{self.fill_extrude_distance:.2f} mm")
                    moved = extruder_adapter.move_filament(
                        self.fill_extrude_distance, self.fill_extrude_speed,
                        check=lambda: buffer.check_feed(slot_num),
                        stop_when=self._exit_triggered)
                self.log_info(f"slot[{slot_num}] exit sensor feed stopped "
                              f"after {moved:.2f} mm of E")
            self._check_extruder_exit(slot_num, work)
        return True

    def verify_charge(self, slot_num, work=None):
        # Record a missing exit signal only after the other checks succeed.
        buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()
        buffer.prepare_for_print(slot_num)
        buffer.activate_monitor(slot_num)
        buffer.check_feed(slot_num)
        # The hook sees the target provisionally. charge_scope clears it if
        # acceptance fails; both callers keep the whole check in that scope.
        self._charged_slot_num = slot_num
        toolhead_adapter.check_motion()
        self.mms_cut.check_released()
        toolhead_adapter.check_motion()
        self._exec_custom_macro(self.custom_after, "after")
        toolhead_adapter.check_motion()
        self.mms_fil_detection.check(slot_num)
        slot = self.mms.get_mms_slot(slot_num)
        gates = [path.get_num() for path in self.mms.get_mms_slots()
                 if path.gate.is_triggered()]
        # Outlet is also the full-buffer switch and releases in the working
        # range. Only optional Entry proves that
        # the toolhead path remains loaded after the final extrusion.
        tail = self.mms_resume.is_tail_available(slot_num)
        if ((not tail and (not slot.inlet.is_triggered()
                           or gates != [slot_num]))
                or any(n != slot_num for n in gates)
                or slot.entry_is_set() and not slot.entry.is_triggered()):
            raise ChargeFailedError(
                f"slot[{slot_num}] charge incomplete: "
                f"Inlet={int(slot.inlet.is_triggered())}, Gates={gates}, "
                f"""Entry={
                    int(slot.entry.is_triggered())
                    if slot.entry_is_set() else 'not configured'
                }; """
                "check the target filament path before retrying",
                slot,
            )

        self._check_extruder_exit(slot_num, work)
        msg = f"slot[{slot_num}] mms charge finish"
        gcode_adapter.respond_echo(msg)
        self.log_info_s(msg)

    # ---- GCode ----
    @log_time_cost("log_info_s")
    def cmd_MMS_CHARGE(self, gcmd):
        self.mms.check_maintenance(gcmd)
        slot_num = gcmd.get_int("SLOT", minval=0)
        z_raise = gcmd.get_float("Z_RAISE", self.z_raise, minval=0.)
        if not self.mms.slot_is_available(slot_num):
            raise gcmd.error("MMS_CHARGE target slot is unavailable")

        if not self.mms_resume.accept_cleared_path():
            if self.mms_resume.get_exhausted_slot() is not None:
                raise gcmd.error("MMS_CHARGE blocked: clear the exhausted path "
                                 "first, or use T to handle its remaining tail")
            raise gcmd.error("MMS_CHARGE requires empty Gates and an empty "
                             "toolhead path; eject or clear existing filament")

        try:
            eject = printer_adapter.get_mms_eject()
            # A pending product check can yield before the first motion.
            with self._charge_is_running():
                eject.verify_pending_eject()
            slot = self.mms.get_mms_slot(slot_num)
            slot.check_load_allowed()
            if not slot.is_ready():
                raise ChargeFailedError(
                    f"slot[{slot_num}] Inlet is empty; insert filament", slot)
            with toolhead_adapter.motion_scope(z_raise=z_raise) as state:
                eject.release_other_selectors(slot_num)
                self.load_filament(slot_num)
                slot.get_mms_buffer().prepare_feed(slot_num)
                toolhead_adapter.return_to_saved(state, clearance=z_raise)
        except ChargeFeedRetrySignal as e:
            raise gcmd.error(f"MMS_CHARGE failed before Entry: {e}") from e
        except Exception as e:
            raise gcmd.error(f"MMS_CHARGE failed: {e}") from e



def load_config(config):
    return MMSCharge(config)
