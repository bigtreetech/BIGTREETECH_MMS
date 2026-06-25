# Support for MMS
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .adapters import (
    gcode_adapter,
    printer_adapter,
    toolhead_adapter
)

from .core.buffer import Buffer, BufferCommand
from .core.config import (
    OptionalField,
    PrinterConfig,
    StringList
)
from .core.dryer import MMSDryer
from .core.observer import PrintObserver
from .core.slot_pin import PinType, PinState
from .core.task import PeriodicTask

from .hardware.button import (
    MMSButtonBufferRunout,
    MMSButtonEntry,
    MMSButtonOutlet,
)
from .hardware.stepper import MMSSelector, MMSDrive

from .motion.endless_spool import MMSEndlessSpool
from .motion.filament_detection import MMSFilamentDetection
from .motion.pause import MMSPause
from .motion.resume import MMSResume


@dataclass(frozen=True)
class MMSConfig:
    # Current version
    version: str = "0.1.0453"
    # Welcome for MMS initail
    welcome: str = "*"*10 + f" MMS Ver {version} Ready for Action! " + "*"*10

    # Log sample related
    # Sample duration seconds = sample_count * sample_period
    sample_count: int = 120
    sample_period: float = 0.5 # second


@dataclass(frozen=True)
class PrinterMMSConfig(PrinterConfig):
    """ Configuration values in mms.cfg """
    retry_times: int = 3

    slot: StringList = "0,1,2,3"
    selector_name: str = "selector"
    drive_name: str = "drive"

    # Buffer Outlet Pin
    # Also the Buffer Full Pin
    outlet: str = "buffer:PA5"
    # Buffer Runout Pin
    buffer_runout: str = "buffer:PA4"
    # Dryer heater
    dryer_heater: str = "ViViD_Dryer"

    # The optional Pin configured for entry_sensor
    entry_sensor: OptionalField = ""

    filament_detection_enable: int = 1
    endless_spool_enable: int = 1


class MMS:
    def __init__(self, config):
        pm_config = PrinterMMSConfig(config)
        self.p_mms_config = pm_config.gen_packaged_config()

        self.mms_config = MMSConfig()
        self.pin_type = PinType()
        self.pin_state = PinState()

        self.entry = None
        self.mms_logger = None
        self.mms_swap = None
        self.print_observer = None

        self._is_connected = False

        self.slot_num_lst = [int(num) for num in self.p_mms_config.slot]
        # MMS Slots
        self.mms_slots = []
        self.mms_slot_dct = {}
        # List to store mms_extend
        self.mms_extends = []
        # List to store mms_buffer
        self.mms_buffers = []
        # List to store mms_steppers
        self.mms_selectors = []
        self.mms_drives = []

        # Init components
        self._initialize()
        # Register event handler to printer
        self._register_event()

    # -- Initialize --
    def _initialize(self):
        # Always follow the parsing sequence
        self._parse_mms_slots(self.slot_num_lst)
        self._parse_mms_buffer(self.slot_num_lst)
        # Pins for MMS Slots
        self._parse_buffer_runout(
            self.p_mms_config.buffer_runout,
            self.slot_num_lst
        )
        self._parse_outlet(
            self.p_mms_config.outlet,
            self.slot_num_lst
        )
        self._parse_entry(
            self.p_mms_config.entry_sensor,
            self.slot_num_lst
        )
        # MMS Steppers
        self._parse_mms_selector(
            self.p_mms_config.selector_name,
            self.slot_num_lst
        )
        self._parse_mms_drive(
            self.p_mms_config.drive_name,
            self.slot_num_lst
        )

        self.mms_pause = MMSPause()
        self.mms_resume = MMSResume()
        self.mms_endless_spool = MMSEndlessSpool()
        self.mms_fil_detection = MMSFilamentDetection()

        # Init periodic service for MMS
        self.periodic_task_sp = PeriodicTask()

        # MMS Buffer command manager
        self.buffer_command = BufferCommand()

        self.mms_dryer = MMSDryer(heater=self.p_mms_config.dryer_heater)

    def _parse_mms_slots(self, slot_num_lst):
        for slot_num in slot_num_lst:
            mms_slot = printer_adapter.get_mms_slot(slot_num)
            if mms_slot not in self.mms_slots:
                self.mms_slots.append(mms_slot)
                self.mms_slot_dct[slot_num] = mms_slot

        self.mms_slots.sort(key=lambda mms_slot: mms_slot.get_num())

    def _parse_mms_buffer(self, slot_num_lst):
        mms_buffer = Buffer()
        mms_buffer.set_index(len(self.mms_buffers))
        self.mms_buffers.append(mms_buffer)

        for slot_num in slot_num_lst:
            mms_slot = self.get_mms_slot(slot_num)
            mms_slot.set_mms_buffer(mms_buffer)

        return mms_buffer

    def _parse_buffer_runout(self, mcu_pin, slot_num_lst):
        buffer_runout = MMSButtonBufferRunout(mcu_pin)
        buffer_runout.register_trigger_callback(
            self.handle_buffer_runout_is_triggered)
        buffer_runout.register_release_callback(
            self.handle_buffer_runout_is_released)

        is_init = True
        for slot_num in slot_num_lst:
            mms_slot = self.get_mms_slot(slot_num)
            mms_slot.set_buffer_runout(buffer_runout)
            if is_init:
                mms_slot.get_mms_buffer().set_sensor_runout(buffer_runout)
                is_init = False

    def _parse_outlet(self, mcu_pin, slot_num_lst):
        outlet = MMSButtonOutlet(mcu_pin)
        outlet.register_trigger_callback(
            self.handle_outlet_is_triggered)
        outlet.register_release_callback(
            self.handle_outlet_is_released)

        is_init = True
        for slot_num in slot_num_lst:
            mms_slot = self.get_mms_slot(slot_num)
            mms_slot.set_outlet(outlet)
            if is_init:
                mms_slot.get_mms_buffer().set_sensor_full(outlet)
                is_init = False

    def _parse_entry(self, mcu_pin, slot_num_lst):
        if not mcu_pin:
            return

        if not self.entry:
            self.entry = MMSButtonEntry(mcu_pin)
            self.entry.register_trigger_callback(
                self.handle_entry_is_triggered)
            self.entry.register_release_callback(
                self.handle_entry_is_released)

        for slot_num in slot_num_lst:
            mms_slot = self.get_mms_slot(slot_num)
            mms_slot.set_entry(self.entry)

    def _parse_mms_selector(self, selector_name, slot_num_lst):
        mms_selector = MMSSelector(selector_name)
        mms_selector.set_index(len(self.mms_selectors))
        self.mms_selectors.append(mms_selector)

        for slot_num in slot_num_lst:
            mms_slot = self.get_mms_slot(slot_num)
            mms_slot.set_mms_selector(mms_selector)

    def _parse_mms_drive(self, drive_name, slot_num_lst):
        mms_drive = MMSDrive(drive_name)
        mms_drive.set_index(len(self.mms_drives))
        self.mms_drives.append(mms_drive)

        for slot_num in slot_num_lst:
            mms_slot = self.get_mms_slot(slot_num)
            mms_slot.set_mms_drive(mms_drive)

    # -- Register handlers --
    def _register_event(self):
        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)
        printer_adapter.register_klippy_shutdown(
            self._handle_klippy_shutdown)
        printer_adapter.register_klippy_disconnect(
            self._handle_klippy_disconnect)
        printer_adapter.register_klippy_firmware_restart(
            self._handle_klippy_firmware_restart)

    def _handle_klippy_connect(self):
        self._initialize_gcode()
        self._initialize_loggers()
        self._initialize_swap()
        self._initialize_observer()
        self.welcome()
        self._is_connected = True

    def _handle_klippy_ready(self):
        self._sync_retry_count = 0
        reactor = printer_adapter.get_reactor()
        reactor.register_timer(
            callback=self._persistent_moonraker_sync,
            waketime=reactor.monotonic() + 1.0
        )

    def _persistent_moonraker_sync(self, eventtime):
        self._sync_retry_count += 1
        
        # Try to pull data
        success = False
        try:
            if self._is_connected:
                webhooks = printer_adapter.get_obj("webhooks")
                webhooks.call_remote_method("moonraker_pull_lane_data")
                success = True
        except Exception:
            success = False

        if success:
            self.log_info(f"Moonraker lane_data sync successful on attempt {self._sync_retry_count}")
            self._moonraker_sync_lane_data()
            return printer_adapter.get_reactor().NEVER
        
        if self._sync_retry_count < 5:
            return eventtime + 2.0
        
        self.log_warning("Moonraker lane_data sync failed after 5 attempts")
        return printer_adapter.get_reactor().NEVER

    def _handle_klippy_shutdown(self):
        if self.mms_logger:
            self._last_breath()
            self.log_info_s("!!! Klippy Shutdown !!!")
            self.mms_logger.teardown()

    def _handle_klippy_disconnect(self):
        if self.mms_logger:
            self._last_breath()
            self.log_info_s("!!! Klippy Disconnect !!!")
            self.mms_logger.teardown()

    def _handle_klippy_firmware_restart(self):
        if self.mms_logger:
            self._last_breath()
            self.log_info_s("!!! Klippy Firmware Restart !!!")
            self.mms_logger.teardown()

    # -- Extend module init --
    def extend(self, mms_extend):
        self.mms_extends.append(mms_extend)
        extend_num = mms_extend.get_num()

        # Extend slot_num list
        extend_slot_num_lst = mms_extend.get_slot_nums()
        self.slot_num_lst.extend(extend_slot_num_lst)
        self.slot_num_lst.sort()

        # Extend mms_slot object list
        self._parse_mms_slots(extend_slot_num_lst)
        self.mms_slots.sort(key=lambda mms_slot: mms_slot.get_num())
        for slot_num in extend_slot_num_lst:
            self.get_mms_slot(slot_num).mark_is_extended(extend_num)

        # Extend MMS Buffer
        mms_buffer = self._parse_mms_buffer(extend_slot_num_lst)
        mms_extend.set_mms_buffer(mms_buffer)

        # Extend SLOT Buffer Runout button
        self._parse_buffer_runout(
            mms_extend.get_buffer_runout_pin(),
            extend_slot_num_lst
        )
        # Extend SLOT Outlet
        self._parse_outlet(
            mms_extend.get_outlet_pin(),
            extend_slot_num_lst
        )
        self._parse_entry(
            self.p_mms_config.entry_sensor,
            self.slot_num_lst
        )

        # Extend Stepper Selector/Drive
        self._parse_mms_selector(
            mms_extend.get_selector_name(),
            extend_slot_num_lst
        )
        self._parse_mms_drive(
            mms_extend.get_drive_name(),
            extend_slot_num_lst
        )

        mms_extend.set_mms_dryer(
            MMSDryer(heater=mms_extend.get_dryer_heater())
        )

    # -- Initializers --
    def _initialize_gcode(self):
        commands = [
            ("MMS", self.cmd_MMS, "Print version of MMS."),
            (
                "MMS_STATUS",
                self.cmd_MMS_STATUS,
                "Print status of MMS."
            ),
            (
                "MMS_SAMPLE",
                self.cmd_MMS_SAMPLE,
                "Sample status of MMS in 60 seconds."
            ),
            ("MMS_STATUS_STEPPER", self.cmd_MMS_STATUS_STEPPER),
            ("MMS_SAMPLE_STEPPER", self.cmd_MMS_SAMPLE_STEPPER),

            # RFID Support
            ("MMS_RFID_READ", self.cmd_MMS_RFID_READ),
            ("MMS_RFID_WRITE", self.cmd_MMS_RFID_WRITE),
            ("MMS_RFID_TRUNCATE", self.cmd_MMS_RFID_TRUNCATE),
            ("MMS_RFID_RESET", self.cmd_MMS_RFID_RESET),
            ("MMS_LOG", self.cmd_MMS_LOG),

            # SLOT Meta
            ("MMS_SLOT_COLOR", self.cmd_MMS_SLOT_COLOR),
            ("MMS_SLOT_MATERIAL", self.cmd_MMS_SLOT_MATERIAL),
            ("MMS_SLOT_SPOOL", self.cmd_MMS_SLOT_SPOOL),
            ("MMS_SLOT_MAP", self.cmd_MMS_SLOT_MAP),
            ("MMS_LANE_DATA_PULL", self.cmd_MMS_LANE_DATA_PULL),
            ("MMS_SLOT_META", self.cmd_MMS_SLOT_META),
            ("MMS_SLOT_META_TRUNCATE", self.cmd_MMS_SLOT_META_TRUNCATE),

            # MMS Dryer
            ("MMS_DRYER_START", self.cmd_MMS_DRYER_START),
            ("MMS_DRYER_STOP", self.cmd_MMS_DRYER_STOP),

            # Alias
            (
                "MMS00",
                self.cmd_MMS_STATUS,
                "Print status of MMS."
            ),
            (
                "MMS0",
                self.cmd_MMS_SAMPLE,
                "Sample status of MMS in 60 seconds."
            ),
            ("MMS07", self.cmd_MMS_STATUS_STEPPER),
            ("MMS007", self.cmd_MMS_SAMPLE_STEPPER),

            ("MMS_TEST", self.cmd_MMS_TEST),
        ]
        gcode_adapter.bulk_register(commands)
        gcode_adapter.register_self_command()

    def _initialize_loggers(self):
        self.mms_logger = printer_adapter.get_mms_logger()
        self.log_info = self.mms_logger.create_log_info(
            console_output=True)
        self.log_warning = self.mms_logger.create_log_warning(
            console_output=True)
        self.log_error = self.mms_logger.create_log_error(
            console_output=True)
        # Silent
        self.log_info_s = self.mms_logger.create_log_info(
            console_output=False)
        self.log_error_s = self.mms_logger.create_log_error(
            console_output=False)

    def _initialize_swap(self):
        self.mms_charge = printer_adapter.get_mms_charge()
        self.mms_eject = printer_adapter.get_mms_eject()
        self.mms_swap = printer_adapter.get_mms_swap()

    def _initialize_observer(self):
        # Notice the sequence of callbacks registration
        self.print_observer = PrintObserver()

        # Buffer monitor
        for mms_buffer in self.mms_buffers:
            self.print_observer.register_resume_callback(
                mms_buffer.activate_monitor)
            self.print_observer.register_pause_callback(
                mms_buffer.deactivate_monitor)
            self.print_observer.register_finish_callback(
                mms_buffer.deactivate_monitor)

        # Register Eject for Print finish
        self.print_observer.register_finish_callback(
            self.mms_eject.mms_eject_unselect)
        # Register Charge teardown for Print finish
        self.print_observer.register_finish_callback(
            self.mms_charge.teardown)

        for mms_drive in self.mms_drives:
            self.print_observer.register_mms_stepper(
                mms_drive,
                mms_drive.handle_is_not_running
            )
        for mms_selector in self.mms_selectors:
            self.print_observer.register_mms_stepper(
                mms_selector,
                mms_selector.handle_is_not_running
            )

    def welcome(self):
        self.log_info(self.mms_config.welcome)

    def _last_breath(self):
        # self.log_info_s(f"MMS Version: {self.mms_config.version}")

        def _format(data):
            return json.dumps(data, indent=4)

        # Log pins and steppers
        if self.mms_selectors and self.mms_drives:
            self.log_status(silent=True)

        if self.mms_buffers:
            buffers_status = {
                b.get_index():b.get_status() for b in self.mms_buffers
            }
            self.log_info_s("MMS Buffers:\n" + _format(buffers_status))

        if self.mms_selectors:
            msg = ""
            for s in self.mms_selectors:
                msg += _format(s.get_mcu_stepper_status())
                msg += "\n"
            self.log_info_s("MMS Selector MCU_Stepper:\n" + msg)
            # self.log_info_s(
            #     "MMS Selector MCU_Stepper:\n"
            #     f"{[s.get_mcu_stepper_status() for s in self.mms_selectors]}"
            # )

        if self.mms_drives:
            msg = ""
            for s in self.mms_drives:
                msg += _format(s.get_mcu_stepper_status())
                msg += "\n"
            self.log_info_s("MMS Drive MCU_Stepper:\n" + msg)
            #     f"{[s.get_mcu_stepper_status() for s in self.mms_drives]}"
            # )

        if self.mms_swap:
            self.log_info_s(
                "MMS Swap:\n" + _format(self.mms_swap.get_status())
            )

        if self.print_observer:
            self.log_info_s(
                "MMS Print Observer:\n" + \
                _format(self.print_observer.get_status())
            )
            # Stop observer
            self.print_observer.stop()

        toolhead_adapter.log_snapshot()

        # Terminate running tasks
        self.periodic_task_sp.stop()

    # -- MMS SLOT Pin updated --
    def _handle_state(self, mcu_pin, pin_type, pin_state):
        if not self._is_connected:
            return

        for mms_slot in self.mms_slots:
            if mms_slot.find_waiting(mcu_pin, pin_type, pin_state):
                return

        # Find failed
        self.log_info_s(f"slot[*] '{pin_type}' is {pin_state}")

    # Outlet handlers
    def handle_outlet_is_triggered(self, mcu_pin):
        self._handle_state(
            mcu_pin, self.pin_type.outlet, self.pin_state.triggered)

    def handle_outlet_is_released(self, mcu_pin):
        self._handle_state(
            mcu_pin, self.pin_type.outlet, self.pin_state.released)

    # Buffer Runout handlers
    def handle_buffer_runout_is_triggered(self, mcu_pin):
        self._handle_state(
            mcu_pin, self.pin_type.buffer_runout, self.pin_state.triggered)

    def handle_buffer_runout_is_released(self, mcu_pin):
        self._handle_state(
            mcu_pin, self.pin_type.buffer_runout, self.pin_state.released)

    # Entry handlers
    def handle_entry_is_triggered(self, mcu_pin):
        self._handle_state(
            mcu_pin, self.pin_type.entry, self.pin_state.triggered)

    def handle_entry_is_released(self, mcu_pin):
        self._handle_state(
            mcu_pin, self.pin_type.entry, self.pin_state.released)

    # -- Get config or componet --
    def get_version(self):
        return self.mms_config.version

    def get_retry_times(self):
        return self.p_mms_config.retry_times

    def get_entry(self):
        return self.entry

    def get_print_observer(self):
        return self.print_observer

    def get_mms_pause(self):
        return self.mms_pause

    def get_mms_resume(self):
        return self.mms_resume

    def get_mms_filament_detection(self):
        return self.mms_fil_detection

    def get_mms_endless_spool(self):
        return self.mms_endless_spool

    def get_mms_selectors(self):
        return self.mms_selectors

    def get_mms_drives(self):
        return self.mms_drives

    def get_mms_buffers(self):
        return self.mms_buffers

    def get_mms_buffer(self, slot_num):
        return self.get_mms_slot(slot_num).get_mms_buffer()

    def get_mms_extend(self, extend_num):
        for mms_extend in self.mms_extends:
            if mms_extend.get_num() == extend_num:
                return mms_extend
        return None

    def get_min_slot_nums(self):
        slot_nums = [min(self.slot_num_lst)]
        for mms_extend in self.mms_extends:
            slot_num_min = min(mms_extend.get_slot_nums())
            if slot_num_min not in slot_nums:
                slot_nums.append(slot_num_min)
        return slot_nums

    # -- Get slot_num --
    def get_slot_nums(self):
        return self.slot_num_lst

    def get_loading_slots(self):
        """Return slots list which are loading to buffer."""
        return [
            slot.get_num()
            for slot in self.mms_slots
            if slot.is_loading()
        ]

    def get_current_slot(self, no_log=False):
        """
        Current slot is determined by the following logic:
        - If selector has a focused slot (selected_slot), it takes priority
        - If no focused slot, use the first loading slot in the buffer

        -- Multi-Extend consider
        +------+---------------------+---------------------+---+---------+
        |      |   Main SLOT 0~3     |   Extend SLOT 4~7   |...|         |
        | Case |---------------------+---------------------|...| Return  |
        |      | selecting | loading | selecting | loading |...|         |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | 2         | [2]     | 4         | []      |...|    2    |
        | I    |---------------------------------------------------------|
        |      |  <- short, first s in l                                 |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | 2         | []      | 4         | [4]     |...|    4    |
        | II   |---------------------------------------------------------|
        |      |  <- short, first s in l                                 |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | 2         | []      | 4         | []      |...|    2    |
        | III  |---------------------------------------------------------|
        |      |  <- compare, return first not None s                    |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | 2         | [2]     | 4         | [4]     |...|    2    |
        | IV   |---------------------------------------------------------|
        |      |  <- short, first s in l                                 |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | 2         | [3]     | 4         | [7]     |...|    2    |
        | V    |---------------------------------------------------------|
        |      |  <- compare, return first not None s                    |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | 2         | [3]     | 4         | [4]     |...|    4    |
        | VI   |---------------------------------------------------------|
        |      |  <- short, first s in l                                 |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | 2         | [2, 3]  | 4         | [4, 7]  |...|    2    |
        | VII  |---------------------------------------------------------|
        |      |  <- short, first s in l                                 |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | None      | [2]     | 4         | [7]     |...|    4    |
        | VIII |---------------------------------------------------------|
        |      |  <- compare, return first not None s                    |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | None      | [2]     | None      | [7]     |...|    2    |
        | IX   |---------------------------------------------------------|
        |      |  <- compare, all s are None, return min not None l      |
        |------+-----------+---------+-----------+---------+---+---------|
        |      | None      | []      | None      | []      |...|   None  |
        | X    |---------------------------------------------------------|
        |      |  <- compare, all None return None                       |
        +----------------------------------------------------------------+
        """
        def find_slot_sl(extend_num=None):
            if extend_num is not None:
                # Find from extend
                selecting, is_active = self.get_selecting_slot(extend_num)
                loading = [
                    mms_slot.get_num()
                    for mms_slot in self.get_extend_mms_slots(extend_num)
                    if mms_slot.is_loading()
                ]
                prefix = f"extend'{extend_num}' "
            else:
                # Find from main
                selecting, is_active = self.get_selecting_slot()
                loading = [
                    mms_slot.get_num()
                    for mms_slot in self.get_main_mms_slots()
                    if mms_slot.is_loading()
                ]
                prefix = ""

            msg = (prefix + (f"selecting:{selecting}/is_active:{is_active},"
                             f" loading:{loading}"))
            if not no_log:
                self.log_info_s(msg)
            # return selecting if selecting is not None and loading else None
            return selecting, is_active, loading

        # Directly return the charged slot
        slot_num_charged = self.mms_charge.get_charged_slot()
        if slot_num_charged is not None:
            return slot_num_charged

        # Check the main
        m_selecting, is_active, m_loading = find_slot_sl()
        if m_selecting is not None \
            and is_active \
            and m_selecting in m_loading:
            return m_selecting

        selecting_lst = [(m_selecting, is_active),]
        loading_lst = m_loading

        # Check the extend
        for mms_extend in self.mms_extends:
            extend_num = mms_extend.get_num()
            e_selecting, is_active, e_loading = find_slot_sl(extend_num)

            if e_selecting is not None \
                and is_active \
                and e_selecting in e_loading:
                return e_selecting

            selecting_lst.append((e_selecting, is_active))
            loading_lst.extend(e_loading)

        # Return first active and not None selecting
        active_selecting_lst = [
            s for s,a in selecting_lst
            if a and s is not None
        ]
        if active_selecting_lst:
            return min(active_selecting_lst)

        # Return first not None selecting
        exist_selecting_lst = [
            s for s,a in selecting_lst
            if s is not None
        ]
        if exist_selecting_lst:
            return min(exist_selecting_lst)

        # All selecting are None, return min not None loading
        exist_loading_lst = [
            s for s in loading_lst
            if s is not None
        ]
        if exist_loading_lst:
            return min(exist_loading_lst)

        # All None return None
        return None

    def get_selecting_slot(self, extend_num=None):
        """
        Return selecting slot which is selecting by stepper
        or selector pin is triggered
        """
        def find_selecting_one(mms_slots):
            selector = mms_slots[0].get_mms_selector()
            selecting_num = selector.get_focus_slot()
            is_active = True

            # Selector Stepper is not focusing
            # Find the min slot which selector pin is triggered
            if selecting_num is None:
                selecting_lst = [
                    s.get_num()
                    for s in mms_slots
                    if s.selector_is_triggered()
                ]
                if selecting_lst:
                    selecting_num = min(selecting_lst)
                    is_active = False

            return selecting_num, is_active

        mms_slots = self.get_extend_mms_slots(extend_num) \
            if extend_num is not None \
            else self.get_main_mms_slots()
        return find_selecting_one(mms_slots)

    def get_selecting_slots(self):
        """
        Return all selecting slots which is selecting by stepper
        or selector pin is triggered, main and all extend
        """
        def find_selecting_slots(mms_slots):
            slot_num_lst = [
                s.get_num()
                for s in mms_slots
                if s.selector_is_triggered()
            ]

            focus_slot = mms_slots[0].get_mms_selector().get_focus_slot()
            if focus_slot not in slot_num_lst:
                slot_num_lst.append(focus_slot)

            return [s for s in slot_num_lst if s is not None]

        # Main selecting slot_nums
        selecting_slots = find_selecting_slots(
            self.get_main_mms_slots()
        )
        # Extend selecting slot_nums
        for mms_extend in self.mms_extends:
            extend_ss = find_selecting_slots(
                mms_extend.get_mms_slots()
            )
            selecting_slots.extend(extend_ss)

        return selecting_slots

    # -- Get mms_slot --
    def get_mms_slots(self):
        # Return all mms_slot objects,
        # include main and extends
        return self.mms_slots

    def get_mms_slot(self, slot_num):
        error_msg = f"slot[{slot_num}] is not available"

        if slot_num is None:
            raise IndexError(error_msg)

        if type(slot_num) is str:
            if not slot_num.isdigit():
                raise IndexError(error_msg)
            slot_num = int(slot_num)

        mms_slot = self.mms_slot_dct.get(slot_num)
        if mms_slot is None:
            raise IndexError(error_msg)

        return mms_slot

    def get_main_mms_slots(self):
        return [
            mms_slot
            for mms_slot in self.mms_slots
            if not mms_slot.is_extended()
        ]

    def get_extend_mms_slots(self, extend_num=None):
        if extend_num is not None:
            return [
                mms_slot
                for mms_slot in self.mms_slots
                if mms_slot.get_extend_num() == extend_num
            ]

        # Default return all extend mms_slots
        # lst.sort(key=lambda s: s.get_num())
        return [
            mms_slot
            for mms_slot in self.mms_slots
            if mms_slot.is_extended()
        ]

    # -- Check Related --
    def slot_is_available(self, slot_num, can_none=False):
        if can_none and slot_num is None:
            return True

        if slot_num not in self.slot_num_lst:
            self.log_error(
                f"slot '{slot_num}' is not available, "
                f"choices are: {self.slot_num_lst}"
            )
            return False
        return True

    def printer_is_shutdown(self):
        return printer_adapter.is_shutdown()

    def printer_is_printing(self):
        return self.print_observer.is_printing()

    def printer_is_paused(self):
        return self.print_observer.is_paused()

    def printer_is_resuming(self):
        return self.mms_resume.is_resuming()

    def cmd_can_exec(self):
        return not self.printer_is_printing() \
            and not self.printer_is_shutdown()

    def mms_selector_is_running(self):
        for mms_slot in self.get_mms_slots():
            if mms_slot.get_mms_selector().is_running():
                return True
        return False

    def mms_drive_is_running(self):
        for mms_slot in self.get_mms_slots():
            if mms_slot.get_mms_drive().is_running():
                return True
        return False

    def buffer_is_cleared(self, slot_num):
        mms_buffer = self.get_mms_buffer(slot_num)
        return

    # -- Config enable --
    def filament_detection_is_enabled(self):
        return bool(self.p_mms_config.filament_detection_enable)

    def endless_spool_is_enabled(self):
        return bool(self.p_mms_config.endless_spool_enable)

    # -- MMS Status --
    def get_status(self, eventtime=None):
        return {
            "slots" : {
                slot.get_num() : slot.get_status(eventtime)
                for slot in self.mms_slots
            },
            "steppers" : {
                "selectors" : {
                    s.get_index() : s.get_status()
                    for s in self.mms_selectors
                },
                "drives": {
                    d.get_index() : d.get_status()
                    for d in self.mms_drives
                },
            },
            "buffers" : {
                b.get_index() : b.get_status()
                for b in self.mms_buffers
            },
            "extends" : {
                extend.get_num() : extend.report()
                for extend in self.mms_extends
            },
            "loading_slots" : self.get_loading_slots(),
            "dryer": self.mms_dryer.report(),
        } if self._is_connected else {}

    def _find_slot_for_moonraker(self, slot_num):
        mms_slot = self.get_mms_slot(slot_num)
        return mms_slot if mms_slot and hasattr(mms_slot, "meta") else None

    def _normalize_lane_data_value(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value if value else None
        return value

    def _normalize_lane_data_number(self, value, field_name):
        if value is None:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        value = str(value).strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            self.log_error(f"lane_data invalid {field_name}: '{value}'")
            return None

    def _normalize_lane_data_int(self, value, field_name):
        if value is None:
            return None
        if isinstance(value, int) and not isinstance(value, bool):
            return value if value > 0 else None
        value = str(value).strip()
        if not value:
            return None
        try:
            num = int(value)
        except ValueError:
            self.log_error(f"lane_data invalid {field_name}: '{value}'")
            return None
        return num if num > 0 else None

    def _parse_lane_data_slot_num(self, lane_key, lane_value):
        lane_str = None
        if isinstance(lane_value, dict):
            lane_str = lane_value.get("lane")
        if not lane_str and isinstance(lane_key, str) and lane_key.startswith("lane"):
            lane_str = lane_key[4:]
        if lane_str is None:
            return None
        try:
            return int(lane_str)
        except (ValueError, TypeError):
            return None

    def _apply_lane_data_to_slot(self, mms_slot, lane_value):
        filament_info = dict(mms_slot.meta.filament_info or {})
        updated = False

        def update_filament_info(key, value):
            nonlocal updated
            if value is None:
                if key in filament_info:
                    filament_info.pop(key, None)
                    updated = True
            else:
                if filament_info.get(key) != value:
                    filament_info[key] = value
                    updated = True

        vendor = self._normalize_lane_data_value(lane_value.get("vendor_name"))
        name = self._normalize_lane_data_value(lane_value.get("name"))
        material = self._normalize_lane_data_value(lane_value.get("material"))
        color = self._normalize_lane_data_value(lane_value.get("color"))
        if isinstance(color, str):
            color = color.lstrip("#") or None
        bed_temp = self._normalize_lane_data_number(
            lane_value.get("bed_temp"), "BED_TEMP")
        nozzle_temp = self._normalize_lane_data_number(
            lane_value.get("nozzle_temp"), "NOZZLE_TEMP")
        filament_id = self._normalize_lane_data_value(
            lane_value.get("filament_id"))
        spool_id = self._normalize_lane_data_int(
            lane_value.get("spool_id"), "SPOOL_ID")

        update_filament_info("filament_manufacturer", vendor)
        if name is None:
            update_filament_info("filament_type_detailed", None)
            update_filament_info("color_name_a", None)
        else:
            update_filament_info("filament_type_detailed", name)
        update_filament_info("filament_material_type", material)
        update_filament_info("color_code", color)
        update_filament_info("bed_temperature", bed_temp)
        update_filament_info("nozzle_temp", nozzle_temp)
        update_filament_info("filament_id", filament_id)

        if mms_slot.meta.filament_color != color:
            mms_slot.set_filament_color(color)
            updated = True
        if mms_slot.meta.filament_material != material:
            mms_slot.set_filament_material(material)
            updated = True
        if mms_slot.meta.spool_id != spool_id:
            mms_slot.set_spool_id(spool_id)
            updated = True

        if updated:
            mms_slot.set_filament_info(filament_info)

        return updated

    def _build_lane_data(self, mms_slot, scan_time):
        slot_meta = mms_slot.meta
        filament_info = slot_meta.filament_info or {}

        nozzle_temp = (
            filament_info.get("printing_temperature_min")
            or filament_info.get("printing_temperature_max")
            or filament_info.get("nozzle_temp")
            or None
        )
        bed_temp = (
            filament_info.get("bed_temperature")
            or filament_info.get("bed_temerature_max")
            or filament_info.get("bed_temerature_min")
            or None
        )
        color = slot_meta.filament_color or filament_info.get("color_code") or None
        if isinstance(color, str):
            color = color.lstrip("#")
        material = (
            slot_meta.filament_material
            or filament_info.get("filament_material_type")
            or None
        )
        if hasattr(material, "value"):
            material = material.value
        spool_id = slot_meta.spool_id if slot_meta.spool_id and slot_meta.spool_id > 0 else None

        is_empty = mms_slot.is_empty()
        if is_empty:
            return {
                "vendor_name": None,
                "name": None,
                "color": None,
                "material": None,
                "bed_temp": None,
                "nozzle_temp": None,
                "scan_time": None,
                "td": None,
                "lane": str(mms_slot.get_num()),
                "spool_id": None,
                "filament_id": None,
            }

        return {
            "vendor_name": filament_info.get("filament_manufacturer") or None,
            "name": (
                filament_info.get("filament_type_detailed")
                or filament_info.get("color_name_a")
                or None
            ),
            "color": color,
            "material": material,
            "bed_temp": bed_temp,
            "nozzle_temp": nozzle_temp,
            "scan_time": scan_time,
            "td": 4.0,
            "lane": str(mms_slot.get_num()),
            "spool_id": spool_id,
            "filament_id": filament_info.get("filament_id") or None,
        }

    def _moonraker_pull_lane_data(self):
        if not self._is_connected:
            return

        try:
            webhooks = printer_adapter.get_obj("webhooks")
            webhooks.call_remote_method("moonraker_pull_lane_data")
        except Exception as e:
            self.log_info_s(f"failed to pull lane data from Moonraker: {e}")

    def _moonraker_push_lane_data(self, slot_nums=None):
        if not self._is_connected:
            return
        slot_nums = [s.get_num() for s in self.mms_slots] if slot_nums is None else slot_nums
        if not slot_nums:
            return

        batch_data = {}
        scan_time = datetime.now(timezone.utc).isoformat()
        for slot_num in slot_nums:
            mms_slot = self._find_slot_for_moonraker(slot_num)
            if not mms_slot:
                continue
            batch_data[f"lane{slot_num}"] = self._build_lane_data(mms_slot, scan_time)

        if not batch_data:
            return

        try:
            webhooks = printer_adapter.get_obj("webhooks")
            webhooks.call_remote_method("moonraker_push_lane_data", lane_data=batch_data)
        except Exception as e:
            self.log_info_s(f"failed to push lane data to Moonraker: {e}")

    def _moonraker_sync_lane_data(self):
        try:
            webhooks = printer_adapter.get_obj("webhooks")
            webhooks.call_remote_method(
                "moonraker_cleanup_lane_data",
                num_gates=len(self.mms_slots),
            )
        except Exception as e:
            self.log_info_s(f"failed to cleanup lane data in Moonraker: {e}")

    def notify_lane_data_changed(self, slot_nums=None):
        self._moonraker_push_lane_data(slot_nums=slot_nums)

    def log_status(self, silent=True):
        # Log stepper status if needed
        self.log_status_stepper(silent=True)

        # Version
        info = f"MMS Version: {self.mms_config.version}\n"
        # Pins
        info += "Slot pins status:\n"
        for mms_slot in self.get_mms_slots():
            info += mms_slot.format_pins_status()
        # Charged
        info += f"Charged SLOT: {self.mms_charge.get_charged_slot()}\n"
        # Deliver distance
        for mms_slot in self.get_mms_slots():
            info += mms_slot.format_deliver_distance()

        log_func = self.log_info_s if silent else self.log_info
        log_func(info)

    def log_status_stepper(self, silent=False):
        info = "Stepper status:\n"
        for s in self.mms_selectors:
            info += json.dumps(s.get_status(), indent=4) + "\n"
        for s in self.mms_drives:
            info += json.dumps(s.get_status(), indent=4) + "\n"

        if silent:
            self.log_info_s(info)
        else:
            self.log_info(info)

    def log_observer(self):
        if self.print_observer:
            self.log_info_s(
                "MMS Print Observer:\n"
                f"{self.print_observer.get_status()}"
            )

    def log_deliver_distance(self):
        info = ""
        for mms_slot in self.get_mms_slots():
            info += mms_slot.format_deliver_distance()
        self.log_info(info)

    # -- GCode commands --
    def cmd_MMS(self, gcmd):
        self.log_info(f"MMS Version:{self.mms_config.version}")

    def cmd_MMS_STATUS(self, gcmd):
        self.log_status(silent=False)

    def cmd_MMS_SAMPLE(self, gcmd):
        duration = gcmd.get_int("DURATION", default=0, minval=0)

        if self.periodic_task_sp.is_running():
            self.log_warning("MMS_SAMPLE is running, return...")
            return

        config_timeout = (
            self.mms_config.sample_count * self.mms_config.sample_period)
        timeout = duration or config_timeout
        self.periodic_task_sp.set_period(self.mms_config.sample_period)
        self.periodic_task_sp.set_timeout(timeout)

        func = self.log_status
        params = {"silent": False}
        try:
            if self.periodic_task_sp.schedule(func, params):
                self.periodic_task_sp.start()
        except Exception as e:
            self.log_error_s(f"MMS_SAMPLE error:{e}")
        self.log_info("MMS sample begin")

    def cmd_MMS_STATUS_STEPPER(self, gcmd):
        self.log_status_stepper()

    def cmd_MMS_SAMPLE_STEPPER(self, gcmd):
        duration = gcmd.get_int("DURATION", default=0, minval=0)

        if self.periodic_task_sp.is_running():
            self.log_warning("SAMPLE task is running, return...")
            return

        func = self.log_status_stepper
        self.periodic_task_sp.set_period(self.mms_config.sample_period)
        self.periodic_task_sp.set_timeout(
            duration or self.mms_config.sample_count
            * self.mms_config.sample_period)
        try:
            is_ready = self.periodic_task_sp.schedule(func)
            if is_ready:
                self.periodic_task_sp.start()
        except Exception as e:
            self.log_error_s(f"MMS_SAMPLE_STEPPER error:{e}")
        self.log_info("MMS sample stepper begin")

    def cmd_MMS_RFID_READ(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.slot_is_available(slot_num):
            return
        switch = gcmd.get_int("SWITCH", 0)

        mms_slot = self.get_mms_slot(slot_num)
        if switch == 1:
            mms_slot.slot_rfid.rfid_read_begin()
        else:
            mms_slot.slot_rfid.rfid_read_end()

    def cmd_MMS_RFID_WRITE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        data = gcmd.get("DATA", default="{}")
        align = gcmd.get_int("ALIGN", default=1)
        if not self.slot_is_available(slot_num):
            return
        mms_slot = self.get_mms_slot(slot_num)
        
        if align:
            mms_delivery = printer_adapter.get_mms_delivery()
            mms_delivery.deliver_async_task(
                mms_slot.slot_rfid.align_and_write,
                {"data": data}
            )
        else:
            mms_slot.slot_rfid.rfid_write(data)

    def cmd_MMS_RFID_TRUNCATE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.slot_is_available(slot_num):
            return
        mms_slot = self.get_mms_slot(slot_num)
        mms_slot.slot_rfid.rfid_truncate()

    def cmd_MMS_RFID_RESET(self, gcmd):
        """
        Usage:
            MMS_RFID_RESET
        """
        for mms_slot in self.mms_slots:
            mms_slot.slot_rfid.reset()
        self.log_info("MMS RFID reset end")

    def cmd_MMS_LOG(self, gcmd):
        """
        Usage:
            MMS_LOG MSG='<msg>' ERROR=<0|1>
        """
        msg = gcmd.get("MSG")
        is_error = gcmd.get_int("ERROR", 0)

        if is_error:
            self.log_error_s(msg)
        else:
            self.log_info_s(msg)

    def cmd_MMS_SLOT_COLOR(self, gcmd):
        """
        Usage:
            MMS_SLOT_COLOR SLOT=0 CODE='#FF00FF'
        """
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.slot_is_available(slot_num):
            return

        color_code = gcmd.get("CODE")
        mms_slot = self.get_mms_slot(slot_num)
        mms_slot.set_filament_color(color_code)
        self.notify_lane_data_changed([slot_num])

    def cmd_MMS_SLOT_MATERIAL(self, gcmd):
        """
        Usage:
            MMS_SLOT_MATERIAL SLOT=0 MATERIAL='PLA-CF'
        """
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.slot_is_available(slot_num):
            return

        material = gcmd.get("MATERIAL")
        mms_slot = self.get_mms_slot(slot_num)
        mms_slot.set_filament_material(material)
        self.notify_lane_data_changed([slot_num])

    def cmd_MMS_SLOT_SPOOL(self, gcmd):
        """
        Usage:
            MMS_SLOT_SPOOL SLOT=0 SPOOL_ID=123
            MMS_SLOT_SPOOL SLOT=0 SPOOL_ID=-1
        """
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.slot_is_available(slot_num):
            return

        spool_id = gcmd.get_int("SPOOL_ID", default=-1)
        mms_slot = self.get_mms_slot(slot_num)
        mms_slot.set_spool_id(spool_id)
        self.notify_lane_data_changed([slot_num])

    def _normalize_slot_map_value(self, value):
        if value is None:
            return None
        value = str(value).strip()
        return value if value else None

    def _normalize_slot_map_number(self, value, field_name):
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            self.log_error(f"MMS_SLOT_MAP invalid {field_name}: '{value}'")
            return "__invalid__"

    def _parse_slot_map_targets(self, gate, gates_param):
        gates = []
        if gate is not None:
            gates.append(gate)
        if gates_param:
            for item in gates_param.split(","):
                item = item.strip()
                if not item:
                    continue
                try:
                    gates.append(int(item))
                except ValueError:
                    self.log_error(
                        f"MMS_SLOT_MAP invalid GATES entry: '{item}'")
                    return None

        if not gates:
            return []

        seen = set()
        uniq = []
        for gate_num in gates:
            if gate_num not in seen:
                seen.add(gate_num)
                uniq.append(gate_num)
        return uniq

    def _get_slot_map_fields(self, mms_slot):
        slot_meta = mms_slot.meta
        filament_info = slot_meta.filament_info or {}
        material = (
            slot_meta.filament_material
            or filament_info.get("filament_material_type")
            or None
        )
        if hasattr(material, "value"):
            material = material.value
        color = slot_meta.filament_color or filament_info.get("color_code") or None
        vendor = filament_info.get("filament_manufacturer") or None
        name = (
            filament_info.get("filament_type_detailed")
            or filament_info.get("color_name_a")
            or None
        )
        return vendor, name, material, color

    def _format_slot_map(self, detail=False, as_json=False):
        def fmt(value):
            return value if value not in (None, "") else "-"

        if as_json:
            data = {}
            for mms_slot in sorted(self.mms_slots, key=lambda s: s.get_num()):
                vendor, name, material, color = self._get_slot_map_fields(mms_slot)
                filament_info = mms_slot.meta.filament_info or {}
                slot_data = {
                    "material": material,
                    "color": color,
                    "name": name,
                    "vendor": vendor,
                }
                if detail:
                    slot_data.update({
                        "spool_id": mms_slot.meta.spool_id,
                        "filament_id": filament_info.get("filament_id"),
                        "bed_temp": filament_info.get("bed_temperature"),
                        "nozzle_temp": filament_info.get("nozzle_temp"),
                    })
                data[str(mms_slot.get_num())] = slot_data
            return json.dumps(data)

        lines = ["MMS Slot Map:"]
        for mms_slot in sorted(self.mms_slots, key=lambda s: s.get_num()):
            vendor, name, material, color = self._get_slot_map_fields(mms_slot)
            filament_info = mms_slot.meta.filament_info or {}
            line = (
                f"Slot {mms_slot.get_num()}: "
                f"material={fmt(material)} "
                f"color={fmt(color)} "
                f"name={fmt(name)} "
                f"vendor={fmt(vendor)}"
            )
            if detail:
                bed_temp = filament_info.get("bed_temperature")
                nozzle_temp = filament_info.get("nozzle_temp")
                filament_id = (
                    filament_info.get("filament_id") if filament_info else None
                )
                line += (
                    f" spool_id={fmt(mms_slot.meta.spool_id)}"
                    f" filament_id={fmt(filament_id)}"
                    f" bed_temp={fmt(bed_temp)}"
                    f" nozzle_temp={fmt(nozzle_temp)}"
                )
            lines.append(line)
        return "\n".join(lines)

    def _apply_slot_map_updates(
        self,
        mms_slot,
        vendor,
        vendor_set,
        name,
        name_set,
        material,
        material_set,
        color,
        color_set,
        bed_temp,
        bed_temp_set,
        nozzle_temp,
        nozzle_temp_set,
        reset,
    ):
        filament_info = dict(mms_slot.meta.filament_info or {})
        updated = False

        if reset:
            for key in (
                "filament_manufacturer",
                "filament_type_detailed",
                "color_name_a",
                "color_code",
                "filament_material_type",
                "bed_temperature",
                "nozzle_temp",
            ):
                if key in filament_info:
                    filament_info.pop(key, None)
                    updated = True
            if mms_slot.meta.filament_color is not None:
                mms_slot.set_filament_color(None)
                updated = True
            if mms_slot.meta.filament_material is not None:
                mms_slot.set_filament_material(None)
                updated = True

        if vendor_set:
            if vendor is None:
                if "filament_manufacturer" in filament_info:
                    filament_info.pop("filament_manufacturer", None)
                    updated = True
            else:
                filament_info["filament_manufacturer"] = vendor
                updated = True

        if name_set:
            if name is None:
                if "filament_type_detailed" in filament_info:
                    filament_info.pop("filament_type_detailed", None)
                    updated = True
                if "color_name_a" in filament_info:
                    filament_info.pop("color_name_a", None)
                    updated = True
            else:
                filament_info["filament_type_detailed"] = name
                updated = True

        if material_set:
            if material is None:
                if mms_slot.meta.filament_material is not None:
                    mms_slot.set_filament_material(None)
                    updated = True
                if "filament_material_type" in filament_info:
                    filament_info.pop("filament_material_type", None)
                    updated = True
            else:
                mms_slot.set_filament_material(material)
                filament_info["filament_material_type"] = material
                updated = True

        if color_set:
            if color is None:
                if mms_slot.meta.filament_color is not None:
                    mms_slot.set_filament_color(None)
                    updated = True
                if "color_code" in filament_info:
                    filament_info.pop("color_code", None)
                    updated = True
            else:
                mms_slot.set_filament_color(color)
                filament_info["color_code"] = color
                updated = True

        if bed_temp_set:
            if bed_temp is None:
                if "bed_temperature" in filament_info:
                    filament_info.pop("bed_temperature", None)
                    updated = True
            else:
                filament_info["bed_temperature"] = bed_temp
                updated = True

        if nozzle_temp_set:
            if nozzle_temp is None:
                if "nozzle_temp" in filament_info:
                    filament_info.pop("nozzle_temp", None)
                    updated = True
            else:
                filament_info["nozzle_temp"] = nozzle_temp
                updated = True

        if updated:
            mms_slot.set_filament_info(filament_info)

        return updated

    def cmd_MMS_LANE_DATA_PULL(self, gcmd):
        """
        Usage:
            MMS_LANE_DATA_PULL
        """
        self.log_info("manual Moonraker lane_data pull triggered")
        self._moonraker_pull_lane_data()
        self.log_info(self._format_slot_map(detail=True))

    def cmd_MMS_SLOT_MAP(self, gcmd):
        """
        Usage:
            MMS_SLOT_MAP
            MMS_SLOT_MAP GATE=0 MATERIAL='PETG' COLOR='FF0000' NAME='PETG HF Black Red' VENDOR='Bambu'
            MMS_SLOT_MAP GATES=0,1,2,3 MATERIAL='PETG'
            MMS_SLOT_MAP RESET=1
        """
        gate = gcmd.get_int("GATE", default=None)
        slot = gcmd.get_int("SLOT", default=None)
        gates_param = gcmd.get("GATES", default=None)
        slots_param = gcmd.get("SLOTS", default=None)
        quiet = gcmd.get_int("QUIET", default=0)
        detail = gcmd.get_int("DETAIL", default=0)
        reset = gcmd.get_int("RESET", default=0)
        sync = gcmd.get_int("SYNC", default=0)

        vendor_raw = gcmd.get("VENDOR", default=None)
        name_raw = gcmd.get("NAME", default=None)
        material_raw = gcmd.get("MATERIAL", default=None)
        color_raw = gcmd.get("COLOR", default=None)
        bed_temp_raw = gcmd.get("BED_TEMP", default=None)
        nozzle_temp_raw = gcmd.get("NOZZLE_TEMP", default=None)

        vendor_set = vendor_raw is not None
        name_set = name_raw is not None
        material_set = material_raw is not None
        color_set = color_raw is not None
        bed_temp_set = bed_temp_raw is not None
        nozzle_temp_set = nozzle_temp_raw is not None

        vendor = self._normalize_slot_map_value(vendor_raw)
        name = self._normalize_slot_map_value(name_raw)
        material = self._normalize_slot_map_value(material_raw)
        color = self._normalize_slot_map_value(color_raw)
        if color and color.startswith("#"):
            color = color.lstrip("#")
        bed_temp = self._normalize_slot_map_number(bed_temp_raw, "BED_TEMP")
        nozzle_temp = self._normalize_slot_map_number(
            nozzle_temp_raw, "NOZZLE_TEMP")

        if bed_temp == "__invalid__" or nozzle_temp == "__invalid__":
            return

        if slot is not None:
            if gate is not None and gate != slot:
                self.log_error("MMS_SLOT_MAP GATE and SLOT must match")
                return
            gate = slot

        if slots_param:
            if gates_param:
                gates_param = f"{gates_param},{slots_param}"
            else:
                gates_param = slots_param

        has_updates = any(
            [
                vendor_set,
                name_set,
                material_set,
                color_set,
                bed_temp_set,
                nozzle_temp_set,
                reset,
            ]
        )
        has_gate = gate is not None or gates_param is not None

        if not has_updates and not has_gate:
            if not quiet:
                # Return JSON response for KlipperScreen if no args provided
                response = self._format_slot_map(detail=detail, as_json=True)
                gcmd.respond_info(response)
            return

        if not has_gate:
            self.log_error("MMS_SLOT_MAP requires GATE/GATES or SLOT/SLOTS to update")
            return

        gates = self._parse_slot_map_targets(gate, gates_param)
        if gates is None:
            return

        updated_slots = []
        for gate_num in gates:
            if not self.slot_is_available(gate_num):
                continue
            mms_slot = self.get_mms_slot(gate_num)
            updated = self._apply_slot_map_updates(
                mms_slot,
                vendor,
                vendor_set,
                name,
                name_set,
                material,
                material_set,
                color,
                color_set,
                bed_temp,
                bed_temp_set,
                nozzle_temp,
                nozzle_temp_set,
                reset,
            )
            if updated:
                updated_slots.append(gate_num)

        if updated_slots and not sync:
            self.notify_lane_data_changed(updated_slots)

        if not quiet:
            self.log_info(self._format_slot_map(detail=detail))

    def cmd_MMS_DRYER_START(self, gcmd):
        """
        Usage:
            MMS_DRYER_START MATERIAL='PLA'
            MMS_DRYER_START MATERIAL='PLA' GROUP=1
        """
        material = gcmd.get("MATERIAL")
        extend_num = gcmd.get_int("GROUP", default=None)

        if extend_num is None:
            self.mms_dryer.start_heating(material_name=material)
            return

        mms_extend = self.get_mms_extend(extend_num)
        if not mms_extend:
            return
        mms_dryer = mms_extend.get_mms_dryer()
        if not mms_dryer:
            return

        mms_dryer.start_heating(material_name=material)

    def cmd_MMS_DRYER_STOP(self, gcmd):
        """
        Usage:
            MMS_DRYER_STOP
            MMS_DRYER_STOP GROUP=1
        """
        extend_num = gcmd.get_int("GROUP", default=None)

        if extend_num is None:
            self.mms_dryer.stop_heating()
            return

        mms_extend = self.get_mms_extend(extend_num)
        if not mms_extend:
            return
        mms_dryer = mms_extend.get_mms_dryer()
        if not mms_dryer:
            return

        mms_dryer.stop_heating()

    def cmd_MMS_SLOT_META(self, gcmd):
        """
        Usage:
            MMS_SLOT_META SLOT=0
        """
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.slot_is_available(slot_num):
            return

        mms_slot = self.get_mms_slot(slot_num)
        self.log_info(json.dumps(mms_slot.meta.report(), indent=4))

    def cmd_MMS_SLOT_META_TRUNCATE(self, gcmd):
        """
        Usage:
            MMS_SLOT_META_TRUNCATE SLOT=0
        """
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.slot_is_available(slot_num):
            return

        mms_slot = self.get_mms_slot(slot_num)
        with mms_slot.update_deliver_distance():
            mms_slot.truncate_deliver_distance()
            self.log_info(
                f"slot[{slot_num}] deliver distance in meta is truncated")

    def cmd_MMS_TEST(self, gcmd):
        return

        stepper_name = "manual_stepper drive_stepper"
        tmc_obj_name = f"tmc2209 {stepper_name}"

        printer = printer_adapter.get_printer()
        tmc = printer.lookup_object(tmc_obj_name)

        # sg_result = tmc.mcu_tmc.get_register("SG_RESULT")
        # self.log_info(f"!!!!! sg_result:{sg_result}")

        def get_sg_result():
            sg_result = tmc.mcu_tmc.get_register("SG_RESULT")
            self.log_info(f"!!!!! sg_result:{sg_result}")

        sample_period = 0.5
        sample_timeout = 60.0

        p_task = PeriodicTask()
        p_task.set_period(sample_period)
        p_task.set_timeout(sample_timeout)

        func = get_sg_result
        try:
            if p_task.schedule(func):
                p_task.start()
        except Exception as e:
            self.log_error(f"p_task error:{e}")


def load_config(config):
    mms = MMS(config)
    printer_adapter.notify_mms_extend(mms)
    return mms
