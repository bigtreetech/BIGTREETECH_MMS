# Support for MMS Swap
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager
from dataclasses import dataclass, fields

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    idle_timeout_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.config import OptionalField, PrinterConfig
from ..core.exceptions import SwapFailedSignal
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

    # Custom Macro
    custom_before: OptionalField = "MMS_SWAP_CUSTOM_BEFORE"
    custom_after: OptionalField = "MMS_SWAP_CUSTOM_AFTER"


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

        self._is_resuming = False
        self._resume_gcmd = None

        # Slots mapping
        self.mapping = None

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
        self.print_observer = self.mms.get_print_observer()
        self.print_observer.register_start_callback(self._init_mapping_filename)
        self.print_observer.register_finish_callback(self._reset_mapping)

        self.mms_pause = self.mms.get_mms_pause()
        self.mms_resume = self.mms.get_mms_resume()

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

    def _initialize_mapping(self):
        # Initial mapping for swap_num and slot_num
        # {"filename":"xxx.gcode", swap_num : slot_num, ...}
        self.mapping = {n:n for n in self.mms.get_slot_nums()}
        self.mapping["filename"] = None

    def _reset_mapping(self):
        self.log_info_s(
            f"reset current mapping:{self.mapping} to default")
        self._initialize_mapping()

    def _init_mapping_filename(self):
        if self.mapping["filename"] is None:
            filename = print_stats_adapter.get_filename()
            self.log_info_s(
                f"initialize current mapping:{self.mapping} "
                f"filename to '{filename}'"
            )
            self.mapping["filename"] = filename

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
    def _pause_mms_buffer(self, slot_num):
        mms_buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()
        # Deactivate buffer monitor before
        mms_buffer.deactivate_monitor()

        # Make sure all mms steppers are idle
        # Timeout with default config in mms_delivery
        is_idle = self.mms_delivery.wait_mms_selector_and_drive(slot_num)
        if not is_idle:
            raise SwapFailedSignal(
                f"slot[{slot_num}] selector or drive "
                "is still running after wait timeout"
            )
        return mms_buffer

    # ---- Swap ----
    def _safety_checks(self, slot_num_from, slot_num_to):
        # Notice: slot_num_from could be None
        # Think about both Pin:selector
        # and Stepper_Selector:focus_slot are None
        # if slot_num_from is None:
        #     self.log_warning("current slot is None, return")
        #     return False

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
        if not self.mms.get_mms_slot(slot_num_to).is_ready():
            self.log_warning(
                f"slot[{slot_num_to}] inlet is not triggered, swap failed")
            return False

        return True

    def _standard_swap(self, slot_num_from, slot_num_to):
        log_prefix = f"slot[{slot_num_from}] to slot[{slot_num_to}]" \
                     f" standard swap"
        self.log_info_s(f"{log_prefix} begin")

        if self.mms_purge.is_enabled():
            # Park to tray point
            self.mms_purge.move_to_tray()
        else:
            # Park to cutter init point
            self.mms_cut.cut_init()

        # Phase I: Eject
        if not self.mms_eject.mms_eject():
            raise SwapFailedSignal(f"slot[{slot_num_from}] eject failed")

        # Phase II: Charge
        if not self.mms_charge.mms_charge(slot_num_to):
            raise SwapFailedSignal(f"slot[{slot_num_to}] charge failed")

        # Phase III: Purge
        if not self.mms_purge.mms_purge():
            raise SwapFailedSignal(f"slot[{slot_num_to}] purge failed")

        # Phase IV: Halfway buffer for volume initilized
        mms_buffer = self.mms.get_mms_slot(slot_num_to).get_mms_buffer()
        if not mms_buffer.halfway(slot_num_to):
            raise SwapFailedSignal(
                f"slot[{slot_num_to}] halfway mms_buffer failed")

        # Phase V: Brush
        if not self.mms_brush.mms_brush():
            raise SwapFailedSignal(f"slot[{slot_num_to}] brush failed")

        self.log_info_s(f"{log_prefix} finish")

    def _shortcut_swap(self, slot_num):
        log_prefix = f"slot[{slot_num}] shortcut swap"
        self.log_info_s(f"{log_prefix} begin")

        if self.mms_purge.is_enabled():
            # Park to tray point
            self.mms_purge.move_to_tray()
        else:
            # Park to cutter init point
            self.mms_cut.cut_init()

        # Phase I: Charge
        if not self.mms_charge.mms_charge(slot_num):
            raise SwapFailedSignal(f"{log_prefix} charge failed")

        # Phase II: Purge
        if not self.mms_purge.mms_purge():
            raise SwapFailedSignal(f"{log_prefix} purge failed")

        # Phase III: Halfway buffer for volume initilized
        mms_buffer = self.mms.get_mms_slot(slot_num).get_mms_buffer()
        if not mms_buffer.halfway(slot_num):
            raise SwapFailedSignal(
                f"slot[{slot_num}] halfway mms_buffer failed")

        # Phase IV: Brush
        if not self.mms_brush.mms_brush():
            raise SwapFailedSignal(f"slot[{slot_num}] brush failed")

        self.log_info_s(f"{log_prefix} finish")

    def mms_swap(self, slot_num, gcmd):
        # Exec before mms_swap
        if self.custom_before:
            self.log_info(f"MMS execute macro before SWAP:"
                          f" {self.custom_before}")
            gcode_adapter.run_command(self.custom_before)

        if not self.is_enabled():
            self.log_info_s("MMS SWAP is disabled, skip...")
            return True

        slot_num_from = self.mms.get_current_slot()
        slot_num_to = self.get_mapping_slot_num(slot_num)
        loading_slots = self.mms.get_loading_slots()
        log_prefix = f"slot[{slot_num_from}] to slot[{slot_num_to}] swap"

        if not self._safety_checks(slot_num_from, slot_num_to):
            self._handle_swap_failure(gcmd,
                f"{log_prefix} safety checks failed")
            return False

        # Even is same slot, always do swap
        self.log_info_s(f"{log_prefix} begin")
        self.log_info_s(
            f"{log_prefix} determine loading slots: {loading_slots}"
        )
        with self._swap_is_running():
            try:
                # Deactivate both mms_buffers in the beginning
                if slot_num_from:
                    self._pause_mms_buffer(slot_num_from)
                mms_buffer_to = self._pause_mms_buffer(slot_num_to)

                # Execute swap method based on current loading state
                if not loading_slots:
                    # No filament loaded
                    self._shortcut_swap(slot_num_to)
                elif len(loading_slots)==1 and slot_num_to in loading_slots:
                    # Target slot already loaded
                    self._shortcut_swap(slot_num_to)
                else:
                    self._standard_swap(slot_num_from, slot_num_to)

                # Activate target slot's mms_buffer in the end
                mms_buffer_to.activate_monitor()

            except SwapFailedSignal as e:
                self.log_warning(f"{log_prefix} failed: {e}")
                self._handle_swap_failure(gcmd, e)
                return False
            except Exception as e:
                self.log_error(f"{log_prefix} error: {e}")
                self._handle_swap_failure(gcmd, e)
                return False
            self.log_info_s(f"{log_prefix} finish")

        # Exec mms_swap after
        if self.custom_after:
            self.log_info(
                f"MMS execute macro after SWAP: {self.custom_after}")
            gcode_adapter.run_command(self.custom_after)

        return True

    def _handle_swap_failure(self, gcmd, msg):
        toolhead_adapter.lower_z(self.z_raise)
        toolhead_adapter.truncate_snapshot()

        cmd = gcmd.get_command().strip()
        self.log_warning(f"'{cmd}' failed: {msg}, pause print...")
        self.mms_resume.set_mms_swap_resume(
            func=self.cmd_SWAP, gcmd=gcmd
        )

        if self.mms.printer_is_printing() \
            or idle_timeout_adapter.is_printing():
            self.mms_pause.mms_pause()

    # ---- T* ----
    def _parse_slot(self, command):
        """Parse slot number from command."""
        slot_str = command.removeprefix(self.command_string)
        return int(slot_str) if slot_str.isdigit() else None

    def format_command(self, slot_num):
        """Format command with slot number."""
        return f"{self.command_string}{slot_num}"

    @log_time_cost("log_info_s")
    def cmd_SWAP(self, gcmd):
        """The GCode command combine from slicer software."""
        cmd = gcmd.get_command().strip()

        if self.mms.printer_is_shutdown():
            self.log_warning(f"'{cmd}' can not execute now")
            return False

        cmd_slot_num = self._parse_slot(cmd)
        if not self.mms.slot_is_available(cmd_slot_num):
            self.log_error(f"invalid command: {cmd}")
            return False

        with toolhead_adapter.snapshot():
            # with toolhead_adapter.safe_z_raise(self.z_raise):
            toolhead_adapter.raise_z(self.z_raise)

            self._slot_num_to = cmd_slot_num
            self.log_info(f"'{cmd}' begin")

            success = self.mms_swap(cmd_slot_num, gcmd)

            self._slot_num_to = None
            if success:
                toolhead_adapter.lower_z(self.z_raise)
                self.log_info(f"'{cmd}' finish")
            else:
                self.log_info(f"'{cmd}' failed")
            return success

    # ---- Mapping ----
    def get_mapping_slot_num(self, slot_num):
        """Determine actual slot from mapping."""
        # Get current printing filename
        filename = print_stats_adapter.get_filename()

        if self.mapping.get("filename") == filename:
            target_slot_num = self.mapping.get(slot_num)

            self.log_info_s(
                "\n"
                f"command slot[{slot_num}]\n"
                f"target slot[{target_slot_num}]\n"
                f"current swap mapping: {self.mapping}"
            )
            return target_slot_num

        return slot_num

    def update_mapping_slot_num(self, slot_num, slot_num_new):
        if slot_num in self.mapping:
            self.mapping[slot_num] = slot_num_new

            for k,v in self.mapping.items():
                if v == slot_num:
                    self.mapping[k] = slot_num_new

            self.log_info_s(
                f"slot[{slot_num}] update with slot[{slot_num_new}]"
                f" in swap mapping\n"
                f"current swap mapping: {self.mapping}"
            )

    def cmd_MMS_SWAP_MAPPING(self, gcmd):
        swap_num = gcmd.get_int("SWAP_NUM", minval=0)
        if not self.mms.slot_is_available(swap_num):
            return

        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.mms.slot_is_available(slot_num):
            return

        filename = gcmd.get("FILENAME", default=None)

        if swap_num is not None and slot_num is not None:
            self.log_info(f"origin swap mapping: {self.mapping}")
            self.mapping[swap_num] = slot_num
            self.mapping["filename"] = filename

        self.log_info(f"current swap mapping: {self.mapping}")


def load_config(config):
    return MMSSwap(config)
