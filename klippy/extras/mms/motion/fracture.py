# Support for MMS Filament Fracture
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager
from dataclasses import dataclass

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    printer_adapter
)


@dataclass(frozen=True)
class MMSFilamentFractureConfig:
    unload_distance: float = 100 # mm
    extrude_distance_max: float = 3000 # mm
    log_flag: str = "==X=="


class MMSFilamentFracture:
    def __init__(self):
        self.reactor = printer_adapter.get_reactor()

        ff_config = MMSFilamentFractureConfig()
        self.unload_distance = ff_config.unload_distance
        self.extrude_distance_max = ff_config.extrude_distance_max
        self.log_flag = ff_config.log_flag

        self._enable = True

        # Klippy event handler
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_brush = printer_adapter.get_mms_brush()
        self.mms_delivery = printer_adapter.get_mms_delivery()
        self.mms_eject = printer_adapter.get_mms_eject()
        self.mms_purge = printer_adapter.get_mms_purge()
        self.mms_swap = printer_adapter.get_mms_swap()

        self.mms_pause = self.mms.get_mms_pause()
        self.mms_resume = self.mms.get_mms_resume()
        self._enable = self.mms.fracture_detection_is_enabled()

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    # ---- Status ----
    def is_enabled(self):
        return self._enable

    # ---- Control ----
    def activate(self):
        self._enable = True
        self.log_info_s("Filament Fracture Detection is enabled.")

    def deactivate(self):
        self._enable = False
        self.log_info_s("Filament Fracture Detection is disabled.")

    @contextmanager
    def pause_monitoring(self):
        self.deactivate()
        try:
            yield
        finally:
            self.activate()

    @contextmanager
    def monitor_while_homing(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        with mms_slot.inlet.monitor_release(
                condition=self.is_enabled,
                callback=self._handle_while_homing,
                params={"slot_num":slot_num}
            ):
            yield

    @contextmanager
    def monitor_while_feeding(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        with mms_slot.inlet.monitor_release(
                condition=self.is_enabled,
                callback=self._handle_while_feeding,
                params={"slot_num":slot_num}
            ):
            yield

    def force_handle_while_feeding(self, slot_num):
        if self.is_enabled():
            self._handle_while_feeding(slot_num)

    # ---- Handlers ----
    def _handle_while_homing(self, slot_num):
        log_prefix = f"slot[{slot_num}] filament fracture while homing"
        self.log_warning(f"{log_prefix} {self.log_flag}")

        # Immediately halt MMS operations
        # Note:
        # While mms_stop terminates stepper movement,
        # termination signals may be generated during mms_swap operations,
        # which would normally trigger pause command
        # for automatic pausing
        self.mms_delivery.mms_stop(slot_num)

        # Special case handling:
        # If detection occurs during mms_buffer feed/retract activation,
        # the system may fail to automatically call pause after mms_stop
        # Therefore, manually initiate print pause in this scenario
        # Verify printer is still in printing state before pausing
        if self.mms.printer_is_printing():
            self.mms_pause.mms_pause()

        # Wait for toolhead to complete pause movement operations
        if not self.mms_delivery.wait_toolhead():
            self.log_error(f"slot[{slot_num}] wait toolhead idle timeout")
            self.log_error(f"{log_prefix} failed")
            return

        # Check if entry or gate sensors are triggered in the specified slot
        mms_slot = self.mms.get_mms_slot(slot_num)
        entry_tri = mms_slot.entry_is_triggered()
        gate_tri = mms_slot.gate.is_triggered()

        can_play_led_effect = True
        can_resume = True

        # Execute filament retraction with fracture detection
        # temporarily disabled
        with self.pause_monitoring():
            if entry_tri or gate_tri:
                # Initiate emergency mms_eject
                self.mms_eject.mms_eject(check_entry=False)

            try:
                self.mms_delivery.move_backward(slot_num, self.unload_distance)
                self.mms_delivery.unload_to_release_gate(slot_num)
            except Exception as e:
                self.log_error(f"{log_prefix} error: {e}")
                can_resume = False

        if can_resume and self._resume_slot_substitute(slot_num):
            can_play_led_effect = False
        if can_play_led_effect:
            mms_slot.slot_led.activate_blinking()
        self.log_info_s(f"{log_prefix} done")

    def _handle_while_feeding(self, slot_num):
        log_prefix = f"slot[{slot_num}] filament fracture while feeding"
        self.log_warning(f"{log_prefix} {self.log_flag}")

        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_buffer = mms_slot.get_mms_buffer()
        # Deactivate buffer monitor
        mms_buffer.deactivate_monitor()
        # # Make sure all mms steppers are idle
        # # Timeout with default config in mms_delivery
        # is_idle = self.mms_delivery.wait_mms_selector_and_drive(slot_num)

        # Special case handling:
        # If detection occurs during mms_buffer feed/retract activation,
        # the system may fail to automatically call pause after mms_stop
        # Therefore, manually initiate print pause in this scenario
        # Verify printer is still in printing state before pausing
        if self.mms.printer_is_printing():
            if self.mms_pause.mms_pause():
                self.mms_resume.set_mms_swap_resume(
                    func = self.mms_swap.cmd_SWAP,
                    gcmd = gcode_adapter.easy_gcmd(
                        command = self.mms_swap.format_command(slot_num)
                    )
                )

        # Wait for toolhead to complete pause movement operations
        if not self.mms_delivery.wait_toolhead():
            self.log_error(f"slot[{slot_num}] wait toolhead idle timeout")
            self.log_error(f"{log_prefix} failed")
            return

        # Skip If mms_purge is disabled
        if not self.mms_purge.is_enabled():
            mms_slot.slot_led.activate_blinking()
            self.log_info_s(f"{log_prefix} done")
            return

        try:
            purge_success = self._purge_until_entry_release(slot_num)
            if purge_success:
                # Try to resume after purge success
                resume_success = self._resume_slot_substitute(slot_num)
                if resume_success:
                    self.log_info_s(f"{log_prefix} done")
                    return 
        except Exception as e:
            self.log_error(f"{log_prefix} error: {e}")

        # Finally
        mms_slot.slot_led.activate_blinking()
        self.log_info_s(f"{log_prefix} done")

    def _purge_until_entry_release(self, slot_num):
        mms_slot = self.mms.get_mms_slot(slot_num)
        success = True

        # Check if entry sensor is set and triggered
        if mms_slot.entry_is_triggered():
            speed = self.mms_purge.get_purge_speed()
            distance = self.mms_purge.get_purge_distance()
            distance_extruded = 0

            # Make sure is not selecting
            self.mms_delivery.select_another_slot(slot_num)
            # Extrude until entry is released
            while mms_slot.entry_is_triggered():
                # Move to tray
                self.mms_purge.move_to_tray()
                # Extrude
                extruder_adapter.extrude(distance, speed)
                # Brush to clean nozzle
                if self.mms_brush.is_enabled():
                    self.mms_brush.mms_brush()

                # Distance check
                distance_extruded += distance
                if distance_extruded >= self.extrude_distance_max:
                    self.log_warning(
                        f"{log_prefix} warning: total extrude distance "
                        f"reach limit {self.extrude_distance_max}mm, "
                        "break"
                    )
                    success = False
                    break

        return success

    def _resume_slot_substitute(self, slot_num):
        slot_num_sub = self.mms.find_available_substitute_slot(slot_num)
        if slot_num_sub is not None:
            self.mms_swap.update_mapping_slot_num(slot_num, slot_num_sub)
            self.mms_resume.gcode_resume()
            return True
        return False
