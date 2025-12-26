# Support for MMS Pause
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from ..adapters import (
    gcode_adapter,
    # pause_resume_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter,
)


class MMSPause:
    def __init__(self):
        # Command in mainsail.cfg->[gcode_macro PAUSE]
        self._gcode_command = "PAUSE"

        self._is_mms_paused = False

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.print_observer = self.mms.get_print_observer()
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
        # self.log_error = mms_logger.create_log_error()

    # ---- Gcode control ----
    def gcode_pause(self):
        gcode_adapter.run_command(self._gcode_command)
        self.log_info(
            f"mms_pause send gcode pause: {self._gcode_command}"
        )

    # ---- Print control ----
    def is_mms_paused(self):
        return self._is_mms_paused

    def set_mms_paused(self):
        self._is_mms_paused = True

    def free_mms_paused(self):
        self._is_mms_paused = False

    def mms_pause(self):
        if print_stats_adapter.is_paused_or_finished() \
            and not self.mms_resume.is_resuming():
            self.log_info(
                "mms_pause skip, current print status: "
                f"{self.print_observer.get_status()}"
            )
            return False

        # if self.mms_resume.is_resuming():
        #     self.log_info("mms_pause skip, mms_resume is resuming")
        #     return False

        if self.is_mms_paused():
            return False

        self.log_info("mms_pause begin")
        # Mark is paused by MMS
        self.set_mms_paused()
        # Log status of MMS and Toolhead
        self.mms.log_status()
        toolhead_adapter.log_snapshot()
        # Save target temp of extruder for resume
        toolhead_adapter.save_target_temp()
        # Pause with gcode command
        self.gcode_pause()
        self.log_info("mms_pause finish")
        return True

    def cmd_MMS_PAUSE(self, gcmd):
        return self.mms_pause()
