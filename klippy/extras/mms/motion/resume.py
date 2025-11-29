# Support for MMS Resume
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager

from ..adapters import (
    gcode_adapter,
    pause_resume_adapter,
    printer_adapter,
    toolhead_adapter,
)


class MMSResume:
    def __init__(self):
        # Command in mainsail.cfg->[gcode_macro RESUME]
        # self._gcode_command = "RESUME"

        self._origin_resume = None
        # Optional?
        # Replace origin resume with mms_resume
        self._replace_resume()

        self._is_resuming = False
        self._mms_swap_resume_func = None
        self._mms_swap_resume_gcmd = None

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.print_observer = self.mms.get_print_observer()
        self.mms_pause = self.mms.get_mms_pause()

        self.mms_swap = printer_adapter.get_mms_swap()

    def _initialize_gcode(self):
        commands = [
            ("MMS_RESUME", self.cmd_MMS_RESUME),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info()
        self.log_warning = mms_logger.create_log_warning()
        self.log_error = mms_logger.create_log_error()

    # ---- Gcode control ----
    # def gcode_resume(self):
    #     gcode_adapter.run_command(self._gcode_command)
    #     self.log_info(
    #         f"mms_resume send gcode resume: {self._gcode_command}")

    # ---- Status ----
    def is_resuming(self):
        return self._is_resuming

    @contextmanager
    def _mms_resuming(self):
        self._is_resuming = True
        try:
            yield
        finally:
            self._is_resuming = False

    # ---- Print control ----
    def _replace_resume(self):
        pr = pause_resume_adapter.get_printer_object()
        self._origin_resume = pr.send_resume_command
        pr.send_resume_command = self.send_resume_command

    def send_resume_command(self):
        if self.is_resuming():
            return
        # Update paused state early
        self._turn_off_pause_flag()
        self.mms_resume()

    def _turn_on_pause_flag(self):
        pr = pause_resume_adapter.get_printer_object()
        pr.is_paused = True

    def _turn_off_pause_flag(self):
        pr = pause_resume_adapter.get_printer_object()
        pr.is_paused = False

    def set_mms_swap_resume(self, func, gcmd):
        self._mms_swap_resume_func = func
        self._mms_swap_resume_gcmd = gcmd
        self.log_info(
            f"'{gcmd.get_command().strip()}' is set as mms_swap resume command")

    def _resume_mms_swap(self):
        if not self._mms_swap_resume_func \
            or not self._mms_swap_resume_gcmd:
            self.log_warning(
                "no mms_swap resume is set, "
                "continue with origin resume command")
            return True
        # if not self.mms.printer_is_paused():
        #     self.log_warning("printer is not paused, return")
        #     return True

        cmd = self._mms_swap_resume_gcmd.get_command().strip()
        msg = f"mms_resume resume command '{cmd}'"
        self.log_info(f"{msg} begin")

        try:
            with self._mms_resuming():
                # Restore extruder target_temp
                toolhead_adapter.restore_target_temp()

                success = self._mms_swap_resume_func(
                    self._mms_swap_resume_gcmd)

                if success:
                    # Set to None if success
                    # If fail again, another pause may cover
                    self._mms_swap_resume_func = None
                    self._mms_swap_resume_gcmd = None
                    self.log_info(f"{msg} finish")
                    return True
                else:
                    self.log_warning(f"{msg} failed, origin resume skip...")
                    return False

        except Exception as e:
            self.log_error(f"{msg} error: {e}, origin resume skip...")
            return False

    def mms_resume(self):
        if not self._origin_resume:
            self.log_error("mms_resume error: origin resume command not exists")
            return False

        self.log_info("mms_resume begin")

        # If not pause by MMS, continue with origin resume
        if self.mms_pause.is_mms_paused():
            # Resume mms_swap
            success = self._resume_mms_swap()
            # Reset flag
            self.mms_pause.reset_mms_pause_flag()

            if not success:
                # Recover paused state
                self._turn_on_pause_flag()
                return False

        # Execute origin send_resume_command() of pause_resume
        self.log_info("mms_resume run origin resume command")
        self._origin_resume()

        self.log_info("mms_resume finish")
        return True

    def cmd_MMS_RESUME(self, gcmd):
        return self.mms_resume()
