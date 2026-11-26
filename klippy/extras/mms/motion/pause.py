# Support for MMS Pause
#
# Copyright (C) 2025-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager

from ..adapters import (
    gcode_adapter,
    pause_resume_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter,
    virtual_sdcard_adapter,
)


class MMSPause:
    def __init__(self):
        # Command in mainsail.cfg->[gcode_macro PAUSE]
        self._gcode_command = "PAUSE"

        self._replace_pause()

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_resume = self.mms.get_mms_resume()

    def _initialize_gcode(self):
        commands = [
            ("MMS_PAUSE", self.cmd_MMS_PAUSE),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=False)
        # self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()

    # ---- Gcode control ----
    def gcode_pause(self):
        gcode_adapter.run_command(self._gcode_command, strict=True)
        self.log_info(
            f"mms_pause send gcode pause: {self._gcode_command}"
        )

    # ---- Print control ----
    @contextmanager
    def background_pause(self, current):
        # An executing SD macro may still complete a T before releasing the
        # lock. Klipper can flag its input immediately without yielding here.
        from_sd = virtual_sdcard_adapter.is_cmd_from_sd()
        original_file = pause_resume_adapter.get_sd_file()
        sd_file = None
        if from_sd and not pause_resume_adapter.is_paused():
            sd_file = pause_resume_adapter.send_pause_command()
        gcode = printer_adapter.get_printer().lookup_object("gcode")
        with gcode.get_mutex():
            # A console macro can reset or replace the file while we wait.
            if original_file is not pause_resume_adapter.get_sd_file():
                yield False
                return
            # Between SD commands do_pause() can yield to the file worker.
            # Holding the mutex serializes this stop with CLEAR/CANCEL/RESUME.
            if (not from_sd and current()
                    and not pause_resume_adapter.is_paused()):
                sd_file = pause_resume_adapter.send_pause_command()
            try:
                same_input = (sd_file is None
                              or pause_resume_adapter.owns_file_pause(sd_file))
                yield same_input and current()
            finally:
                if (sd_file is not None
                        and not pause_resume_adapter.is_paused()
                        and pause_resume_adapter.owns_file_pause(sd_file)
                        and not self.mms.printer_is_shutdown()
                        and not print_stats_adapter.is_finished()):
                    self.log_info("File input stopped during a print command; "
                                  "parking the current tool for RESUME")
                    self.mms_resume.capture_return_state()
                    generation = self.mms_resume.get_status()["generation"]
                    self.mms_pause(force=True, generation=generation)

    def _replace_pause(self):
        # Capture the physical source before the user's parking/maintenance
        # actions. gcode_macro later renames this wrapper to PAUSE_BASE.
        pause_resume_adapter.get_printer_object()
        self._origin_pause = gcode_adapter.unregister("PAUSE")
        gcode_adapter.register("PAUSE", self.cmd_PAUSE)

    def cmd_PAUSE(self, gcmd):
        if pause_resume_adapter.is_paused():
            return self._origin_pause(gcmd)
        restore = gcmd.get_int("RESTORE", 1, minval=0, maxval=1)
        self.mms_resume.capture_return_state()
        self.mms_resume.capture_pause_source(restore_temperature=bool(restore))
        self.mms_resume.check_runout()
        result = self._origin_pause(gcmd)
        # A failed cutter return leaves the toolhead against the knife.
        # The matching RESUME retracts the head before normal parking.
        if printer_adapter.get_mms_eject().cutter_return_pending():
            return result
        try:
            self.mms_resume.retract_for_pause()
        except Exception as e:
            # Extrusion failure must not abort the product's PAUSE parking.
            # The retract owner retains only displacement already accepted.
            self.mms_resume.note_failure(f"MMS pause retract failed: {e}")
            self.log_error(f"MMS pause retract failed: {e}; continue parking")
        try:
            self.mms_resume.lift_for_pause()
        except Exception as e:
            self.log_error(f"MMS pause lift failed: {e}; continue parking")
        try:
            toolhead_adapter.wait_moves(check=False)
            recovery = self.mms_resume.get_status()
            delivery = printer_adapter.get_mms_delivery()
            if ((recovery["kind"] is not None or recovery["reason"])
                    and not delivery.has_pending_print_tail_release()):
                delivery.pause_feeds(self.mms.get_slot_nums())
        except Exception as e:
            self.mms_resume.note_failure(f"MMS pause feed handoff failed: {e}")
            self.log_error(f"MMS pause feed handoff failed: {e}; "
                           "continue parking")
        # Healthy supply stays with Buffer through retraction and manual E.
        return result

    def mms_pause(self, force=False, swap_gcmd=None, generation=None):
        def is_current():
            return ((swap_gcmd is None
                     or self.mms_resume.is_current_swap(swap_gcmd))
                    and (generation is None
                         or self.mms_resume.get_status()["generation"]
                         == generation))

        if not is_current():
            return False
        # Automatic sensor callbacks can outlive the observer's printing
        # state. Explicit command/swap recovery may still pause an idle
        # printer whose last file is complete or cancelled.
        if not force and (print_stats_adapter.is_finished()
                          or pause_resume_adapter.is_paused()):
            return False
        self.log_info("mms_pause begin")
        # print_stats may already say paused after input was stopped, while
        # PAUSE_STATE has not been captured. Only native is_paused proves it.
        was_paused = pause_resume_adapter.is_paused()

        # Log status of MMS and Toolhead
        self.mms.log_status()
        toolhead_adapter.log_snapshot()

        # The immutable native return state owns the original temperature.
        if not was_paused:
            self.mms_resume.capture_return_state()
            self.mms_resume.capture_pause_source()
        # Pause with gcode command
        try:
            self.gcode_pause()
        finally:
            # A failed user parking macro must still leave a real native
            # PAUSE_STATE, not just stopped file input or a hand-set flag.
            # A cancelled swap must not recreate that pause after a wait.
            if is_current() and not pause_resume_adapter.is_paused():
                pause_resume_adapter.pause()

        if not is_current():
            return False
        self.log_info("mms_pause finish")
        return True

    def cmd_MMS_PAUSE(self, gcmd):
        return self.mms_pause(force=True)
