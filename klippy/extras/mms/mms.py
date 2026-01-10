# Support for MMS
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
from dataclasses import dataclass, field

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
from .core.observer import PrintObserver
from .core.slot_pin import PinType, PinState
from .core.task import PeriodicTask
from .hardware.button import (
    MMSButtonBufferRunout,
    MMSButtonEntry,
    MMSButtonOutlet,
)
from .hardware.stepper import MMSSelector, MMSDrive
from .motion.fracture import MMSFilamentFracture
from .motion.pause import MMSPause
from .motion.resume import MMSResume


@dataclass(frozen=True)
class MMSConfig:
    # Current version
    version: str = "0.1.0380"
    # Welcome for MMS initail
    welcome: str = "*"*10 + f" MMS Ver {version} Ready for Action! " + "*"*10

    # MMS Extend module prefix in Config section
    # mms_extend_prefix = "mms extend"

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
    # The optional Pin configured for entry_sensor
    entry_sensor: OptionalField = ""

    fracture_detection_enable: int = 1

    slot_substitute_enable: int = 1


@dataclass(frozen=True)
class SlotMetaKey:
    # outlet: str = "outlet"
    # buffer_runout: str = "buffer_runout"
    mms_buffer: str = "mms_buffer"
    mms_selector: str = "mms_selector"
    mms_drive: str = "mms_drive"

    is_extended: str = "is_extended"
    extend_num: str = "extend_num"


class MMS:
    def __init__(self, config):
        pm_config = PrinterMMSConfig(config)
        self.p_mms_config = pm_config.gen_packaged_config()

        self.mms_config = MMSConfig()
        self.pin_type = PinType()
        self.pin_state = PinState()

        self.mms_logger = None
        self.mms_swap = None
        self.print_observer = None

        self._is_connected = False

        self.slot_num_lst = [int(num) for num in self.p_mms_config.slot]
        # Slot object list
        self.mms_slots = []
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
        # slot_meta -> {
        #     slot_num : {
        #         "outlet": ...,
        #         "buffer_runout": ...,
        #         "mms_buffer": ...,
        #         "mms_selector": ...,
        #         "mms_drive": ...,
        #         "is_extended": ...,
        #         "extend_num": ...,
        #     }, ...
        # }
        self.slot_meta = {
            slot_num : {
                SlotMetaKey.is_extended: False,
                SlotMetaKey.extend_num: None
            }
            for slot_num in self.slot_num_lst
        }

        # Setup MMS Buffer
        # Notice mms_buffer should always setup
        # before outlet and buffer_runout
        self._parse_mms_buffer(
            self.slot_num_lst
        )
        # Common pin outlet for slots
        self._parse_outlet(
            self.p_mms_config.outlet,
            self.slot_num_lst
        )
        # Common pin buffer_runout for slots
        self._parse_buffer_runout(
            self.p_mms_config.buffer_runout,
            self.slot_num_lst
        )
        # Common pin entry for slots
        self._parse_entry(
            self.p_mms_config.entry_sensor
        )
        # Setup MMS Steppers
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
        self.mms_filament_fracture = MMSFilamentFracture()

        # Init periodic service for MMS
        self.periodic_task_sp = PeriodicTask()

        # MMS Buffer command manager
        self.buffer_command = BufferCommand()

    def _parse_mms_buffer(self, slot_num_lst):
        mms_buffer = Buffer()
        for slot_num in slot_num_lst:
            self.slot_meta[slot_num][SlotMetaKey.mms_buffer] = mms_buffer

        self.mms_buffers.append(mms_buffer)
        mms_buffer.set_index(len(self.mms_buffers)-1)
        return mms_buffer

    def _parse_outlet(self, mcu_pin, slot_num_lst):
        outlet = MMSButtonOutlet(mcu_pin)
        outlet.register_trigger_callback(
            self.handle_outlet_is_triggered)
        outlet.register_release_callback(
            self.handle_outlet_is_released)

        for slot_num in slot_num_lst:
            self.slot_meta[slot_num][self.pin_type.outlet] = outlet

        mms_buffer = self.slot_meta[slot_num_lst[-1]][SlotMetaKey.mms_buffer]
        mms_buffer.set_sensor_full(outlet)

    def _parse_buffer_runout(self, mcu_pin, slot_num_lst):
        buffer_runout = MMSButtonBufferRunout(mcu_pin)
        buffer_runout.register_trigger_callback(
            self.handle_buffer_runout_is_triggered)
        buffer_runout.register_release_callback(
            self.handle_buffer_runout_is_released)

        for slot_num in slot_num_lst:
            self.slot_meta[slot_num][self.pin_type.buffer_runout] = (
                buffer_runout)

        mms_buffer = self.slot_meta[slot_num_lst[-1]][SlotMetaKey.mms_buffer]
        mms_buffer.set_sensor_runout(buffer_runout)

    def _parse_entry(self, mcu_pin):
        if not mcu_pin:
            self.entry = None
            return

        self.entry = MMSButtonEntry(mcu_pin)
        self.entry.register_trigger_callback(
            self.handle_entry_is_triggered)
        self.entry.register_release_callback(
            self.handle_entry_is_released)

    def _parse_mms_selector(self, selector_name, slot_num_lst):
        mms_selector = MMSSelector(selector_name)
        for slot_num in slot_num_lst:
            self.slot_meta[slot_num][SlotMetaKey.mms_selector] = mms_selector

        self.mms_selectors.append(mms_selector)
        mms_selector.set_index(len(self.mms_selectors)-1)

    def _parse_mms_drive(self, drive_name, slot_num_lst):
        mms_drive = MMSDrive(drive_name)
        for slot_num in slot_num_lst:
            self.slot_meta[slot_num][SlotMetaKey.mms_drive] = mms_drive

        self.mms_drives.append(mms_drive)
        mms_drive.set_index(len(self.mms_drives)-1)

    # -- Register handlers --
    def _register_event(self):
        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)
        # printer_adapter.register_klippy_ready(
        #     self._handle_klippy_ready)
        printer_adapter.register_klippy_shutdown(
            self._handle_klippy_shutdown)
        printer_adapter.register_klippy_disconnect(
            self._handle_klippy_disconnect)
        printer_adapter.register_klippy_firmware_restart(
            self._handle_klippy_firmware_restart)

    def _handle_klippy_connect(self):
        self._initialize_slots()
        self._initialize_gcode()
        self._initialize_loggers()
        self._initialize_observer()
        self.welcome()
        self._is_connected = True

    def _handle_klippy_ready(self):
        return

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

        # Extend slot_num list
        extend_slot_num_lst = mms_extend.get_slot_nums()
        self.slot_num_lst.extend(extend_slot_num_lst)
        self.slot_meta.update({
            slot_num:{
                SlotMetaKey.is_extended:True,
                SlotMetaKey.extend_num:mms_extend.get_num()
            } for slot_num in extend_slot_num_lst
        })

        # Extend mms_slot object list
        extend_mms_slots = mms_extend.get_mms_slots()
        for mms_slot in extend_mms_slots:
            if mms_slot not in self.mms_slots:
                self.mms_slots.append(mms_slot)

        # Extend MMS Buffer
        mms_buffer = self._parse_mms_buffer(
            extend_slot_num_lst
        )
        mms_extend.set_mms_buffer(mms_buffer)
        # Extend SLOT Outlet
        self._parse_outlet(
            mms_extend.get_outlet_pin(),
            extend_slot_num_lst
        )
        # Extend SLOT Buffer Runout button
        self._parse_buffer_runout(
            mms_extend.get_buffer_runout_pin(),
            extend_slot_num_lst
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

    # -- Initializers --
    def _initialize_slots(self):
        for slot_num in self.slot_num_lst:
            self.mms_slots.append(printer_adapter.get_mms_slot(slot_num))

    def _initialize_gcode(self):
        commands = [
            ("MMS", self.cmd_MMS),
            ("MMS_STATUS", self.cmd_MMS_STATUS),
            ("MMS_SAMPLE", self.cmd_MMS_SAMPLE),
            ("MMS_STATUS_STEPPER", self.cmd_MMS_STATUS_STEPPER),
            ("MMS_SAMPLE_STEPPER", self.cmd_MMS_SAMPLE_STEPPER),

            # RFID Support
            ("MMS_RFID_READ", self.cmd_MMS_RFID_READ),
            ("MMS_RFID_WRITE", self.cmd_MMS_RFID_WRITE),
            ("MMS_RFID_TRUNCATE", self.cmd_MMS_RFID_TRUNCATE),

            # Alias
            ("MMS00", self.cmd_MMS_STATUS),
            ("MMS0", self.cmd_MMS_SAMPLE),
            ("MMS009", self.cmd_MMS_STATUS_STEPPER),
            ("MMS09", self.cmd_MMS_SAMPLE_STEPPER),

            ("MMS_TEST", self.cmd_MMS_TEST),
        ]
        gcode_adapter.bulk_register(commands)

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

    def _initialize_observer(self):
        self.print_observer = PrintObserver()

        # Buffer monitor
        for mms_buffer in self.mms_buffers:
            # self.print_observer.register_start_callback(
            #     mms_buffer.activate_monitor)
            self.print_observer.register_resume_callback(
                mms_buffer.activate_monitor)
            self.print_observer.register_pause_callback(
                mms_buffer.deactivate_monitor)
            self.print_observer.register_finish_callback(
                mms_buffer.deactivate_monitor)

        # Register Eject for Print finish
        self.mms_eject = printer_adapter.get_mms_eject()
        self.print_observer.register_finish_callback(
            self.mms_eject.mms_eject)

        # Register Charge teardown for Print finish
        self.mms_charge = printer_adapter.get_mms_charge()
        self.print_observer.register_finish_callback(
            self.mms_charge.teardown)

        # Init mms_swap
        self.mms_swap = printer_adapter.get_mms_swap()

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

    def get_mms_filament_fracture(self):
        return self.mms_filament_fracture

    # -- Get slot meta data --
    def get_meta(self, slot_num):
        return self.slot_meta.get(slot_num, {})

    def get_mms_buffer(self, slot_num):
        return self.get_meta(slot_num).get(SlotMetaKey.mms_buffer, None)

    def get_mms_buffers(self):
        return self.mms_buffers

    def get_selector(self, slot_num):
        return self.get_meta(slot_num).get(SlotMetaKey.mms_selector, None)

    def get_drive(self, slot_num):
        return self.get_meta(slot_num).get(SlotMetaKey.mms_drive, None)

    def get_outlet(self, slot_num):
        return self.get_meta(slot_num).get(self.pin_type.outlet, None)

    def get_buffer_runout(self, slot_num):
        return self.get_meta(slot_num).get(self.pin_type.buffer_runout, None)

    def get_mms_extend(self, extend_num):
        for mms_extend in self.mms_extends:
            if mms_extend.get_num() == extend_num:
                return mms_extend
        return None

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

    def get_current_slot(self):
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
            self.log_info_s(msg)
            # return selecting if selecting is not None and loading else None
            return selecting, is_active, loading

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

        if not (0 <= slot_num < len(self.mms_slots)):
            raise IndexError(error_msg)

        mms_slot = self.mms_slots[slot_num]
        if mms_slot is None:
            raise IndexError(error_msg)

        return mms_slot

    def get_main_mms_slots(self):
        slot_filter = lambda meta: not meta.get(SlotMetaKey.is_extended)
        return [
            self.get_mms_slot(slot_num)
            for slot_num,meta in self.slot_meta.items()
            if slot_filter(meta)
        ]

    def get_extend_mms_slots(self, extend_num=None):
        # return the target extend one
        slot_filter = lambda meta: (
            meta.get(SlotMetaKey.extend_num) == extend_num
        )
        if extend_num is not None:
            return [
                self.get_mms_slot(slot_num)
                for slot_num,meta in self.slot_meta.items()
                if slot_filter(meta)
            ]

        # Default return all extend sets
        slot_filter = lambda meta: not meta.get(SlotMetaKey.is_extended)
        lst = [
            self.get_mms_slot(slot_num)
            for slot_num,meta in self.slot_meta.items()
            if slot_filter(meta)
        ]
        lst.sort(key=lambda s: s.get_num())
        return lst

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
        # Swap is resuming, keep pausing
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
    def fracture_detection_is_enabled(self):
        return bool(self.p_mms_config.fracture_detection_enable)

    def slot_substitute_is_enabled(self):
        return bool(self.p_mms_config.slot_substitute_enable)

    def find_available_substitute_slot(self, slot_num):
        if not self.slot_substitute_is_enabled():
            return None

        slot_num_a = None
        slot_num_org = slot_num
        slot_num_checked = [slot_num]

        while not slot_num_a:
            slot_num_sub = self.get_mms_slot(slot_num).get_substitute_with()

            if slot_num_sub is None \
                or slot_num_sub in slot_num_checked:
                break
            slot_num_checked.append(slot_num_sub)

            mms_slot_sub = self.get_mms_slot(slot_num_sub)
            if mms_slot_sub.inlet.is_triggered():
                slot_num_a = slot_num_sub
            else:
                slot_num = slot_num_sub

        return slot_num_a

    # -- MMS Status --
    def _format_slot_status(self, slot_num):
        slot = self.get_mms_slot(slot_num)
        meta = self.slot_meta.get(slot_num)

        return {
            # Pins state
            "selector": slot.selector.get_state(),
            "inlet": slot.inlet.get_state(),
            "gate": slot.gate.get_state(),
            "runout": slot.buffer_runout.get_state(),
            "outlet": slot.outlet.get_state(),
            "entry": slot.entry.get_state(),

            # mms_buffer
            "buffer_index" : meta.get(SlotMetaKey.mms_buffer).get_index(),
            # mms_selector
            "selector_index" : meta.get(SlotMetaKey.mms_selector).get_index(),
            # mms_drive
            "drive_index" : meta.get(SlotMetaKey.mms_drive).get_index(),

            # Extend
            SlotMetaKey.is_extended : meta.get(SlotMetaKey.is_extended),
            SlotMetaKey.extend_num : meta.get(SlotMetaKey.extend_num),
        }

    def get_status(self, eventtime=None):
        if not self._is_connected:
            return {}

        return {
            "slots" : {
                slot.get_num() : self._format_slot_status(slot.get_num())
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
            "loading_slots" : self.get_loading_slots()
        }

    def log_status(self, silent=True):
        log_func = self.log_info_s if silent else self.log_info
        log_func(f"MMS Version: {self.mms_config.version}")

        self.log_status_stepper(silent=True)

        info = ""
        info += "Slot pins status:\n"
        for slot in self.get_mms_slots():
            info += slot.format_pins_status()

        # if show_rfid:
        #     info += "\n"
        #     info += "RFID Tag Data:\n"
        #     for slot in self.mms_slots:
        #         info += f"slot[{slot.get_num()}] RFID data:\n"
        #         info += json.dumps(
        #             slot.get_rfid_status(), indent=4) + "\n"
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
        if not self.slot_is_available(slot_num):
            return
        mms_slot = self.get_mms_slot(slot_num)
        mms_slot.slot_rfid.rfid_write()

    def cmd_MMS_RFID_TRUNCATE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.slot_is_available(slot_num):
            return
        mms_slot = self.get_mms_slot(slot_num)
        mms_slot.slot_rfid.rfid_truncate()

    def cmd_MMS_TEST(self, gcmd):
        mcu = self.mms_selectors[0].get_mcu()
        mcu_name = mcu._name
        mcu_rm = mcu._conn_helper._restart_helper._restart_method
        self.log_info("\n"
           "mcu name:\n"
           f"{mcu_name}\n"
           "mcu restart_method:\n"
           f"{mcu_rm}\n"
        )
        return


def load_config(config):
    # return MMS(config)
    mms = MMS(config)
    printer_adapter.notify_mms_initialized(mms)
    return mms
