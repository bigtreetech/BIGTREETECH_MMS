# Support for MMS
#
# Copyright (C) 2024-2026 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

"""Product configuration integration \
(all hooks are optional, synchronous G-code).

Recovery order:
    native check/park/temperature -> remaining swap OR reload/ordinary purge
    -> brush -> return XY -> repay retract -> lower Z -> native RESUME once.
MMS owns all retraction debt. Ordinary pause/recovery distances and speeds are
configured in [mms purge]; [mms brush] owns cooling and machine cleaning paths.
PAUSE RESTORE=0 disables automatic idle-timeout reheating for that pause.
Product PAUSE wrappers must forward RESTORE to the renamed native command.
They also own machine parking, including repeated PAUSE after failed RESUME.
Keep the original snapshot/retract on repeat, but repark if no longer at the
safe waiting position. Filament faults must not suppress that XY parking.
Machine checks may use purge.custom_before and brush.custom_clean/custom_after.
Hooks must propagate errors and must not recursively call T/RESUME.

MMS_LOAD_SLOT SLOT_NUM=n is native. Replace the old multi-action macro with
its single-command wrapper in mms-macros.cfg (rename_existing: _MMS_LOAD_SLOT)
to retain the frontend button, or remove the macro and call native directly.
Loading and validation belong only to the native command.

PRINT_START must call MMS_CHECK_START before heating, motion or resetting
product context. This rejects pending recovery, finish and background feed.
MMS_PURGE accepts optional DISTANCE/SPEED/RETRACT/RETRACT_SPEED overrides
(mm and mm/min); omitted values retain [mms purge] settings. Virtual-SD files
may use it for startup cleaning. Console calls cannot interrupt a print or
recovery. Successful purge restores active buffer monitoring before returning.
Blocking cleaning hooks must call MMS_CHECK_RUNOUT after M400/G4/etc., before
submitting further motion, so cancellation or a latched runout aborts the tail.
CLEAR_PAUSE invalidates MMS recovery without resuming; product wrappers should
also clear their own pause fields. CANCEL_PRINT likewise invalidates recovery.
A product wrapper must delegate to the renamed native command exactly once.
"""

import json
from dataclasses import dataclass

from .adapters import (
    buttons_adapter,
    gcode_adapter,
    heaters_adapter,
    moonraker_adapter,
    pause_resume_adapter,
    print_stats_adapter,
    printer_adapter,
    toolhead_adapter
)

from .core.buffer import Buffer, BufferCommand
from .core.config import (
    get_buffer_config_float,
    OptionalField,
    PrinterConfig,
    StringList
)
from .core.dryer import MMSDryer
from .core.filament_metadata import normalize_filament
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
    version: str = "0.1.0496"
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
    # Shared reserve target for the slots connected to this buffer.
    buffer_target_percentage: float = 50.0
    buffer_max_velocity: float = 80.0
    buffer_max_accel: float = 120.0
    # Dryer heater
    dryer_heater: str = "ViViD_Dryer"

    # Optional filament switches before and after the extruder drive.
    entry_sensor: OptionalField = ""
    exit_sensor: OptionalField = None

    filament_detection_enable: int = 1
    endless_spool_enable: int = 1

    def get_type_handlers(self):
        handlers = super().get_type_handlers()
        def get_int(config, name):
            if name == "retry_times":
                return config.getint(name, minval=2)
            return config.getint(name)

        handlers[int] = get_int
        handlers[float] = get_buffer_config_float
        return handlers


class MMS:
    def __init__(self, config):
        pm_config = PrinterMMSConfig(config)
        self.p_mms_config = pm_config.gen_packaged_config()

        self.mms_config = MMSConfig()
        self.pin_type = PinType()
        self.pin_state = PinState()

        self.entry = None
        self.exit_sensor_state = None
        if pm_config.exit_sensor:
            self.exit_sensor_state = "RELEASED"
            buttons_adapter.register_sensor(
                config, pm_config.exit_sensor,
                self._handle_extruder_exit)
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
        self._parse_mms_buffer(
            self.slot_num_lst, self.p_mms_config)
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

    def _parse_mms_buffer(self, slot_num_lst, config):
        mms_buffer = Buffer(
            target_percentage=config.buffer_target_percentage,
            max_velocity=config.buffer_max_velocity,
            max_accel=config.buffer_max_accel)
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
        self._validate_backup_slots()
        self._initialize_gcode()
        self._initialize_loggers()
        self._initialize_swap()
        self._initialize_observer()
        self.welcome()
        self._is_connected = True
        # Setup adapters
        printer_adapter.set_is_connected()
        moonraker_adapter.initialize()

    def _validate_backup_slots(self):
        # Extensions are merged before connect. Reject broken references as
        # configuration errors, before any T command can traverse the chain.
        for slot in self.get_mms_slots():
            backup = slot.get_endless_with_slot()
            if backup is not None and not self.slot_is_available(backup):
                raise printer_adapter.get_printer().config_error(
                    f"[mms slot {slot.get_num()}] endless_with_slot: "
                    f"slot[{backup}] is not available")

    def _handle_klippy_ready(self):
        printer_adapter.set_is_ready()
        self.reactor = printer_adapter.get_reactor()

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
        mms_buffer = self._parse_mms_buffer(
            extend_slot_num_lst, mms_extend.ex_config)
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
            ("MMS_CHECK_START", self.cmd_MMS_CHECK_START),

            # RFID Support
            ("MMS_RFID_READ", self.cmd_MMS_RFID_READ),
            ("MMS_RFID_WRITE", self.cmd_MMS_RFID_WRITE),
            ("MMS_RFID_TRUNCATE", self.cmd_MMS_RFID_TRUNCATE),
            ("MMS_RFID_RESET", self.cmd_MMS_RFID_RESET),

            # SLOT Meta and published Moonraker commands
            ("MMS_LOG", self.cmd_MMS_LOG),
            ("MMS_LANE_DATA_PULL", self.cmd_MMS_LANE_DATA_PULL),
            ("MMS_SLOT_MAP", self.cmd_MMS_SLOT_MAP),
            ("MMS_SLOT_SPOOL", self.cmd_MMS_SLOT_SPOOL),
            ("MMS_SLOT_COLOR", self.cmd_MMS_SLOT_COLOR),
            ("MMS_SLOT_MATERIAL", self.cmd_MMS_SLOT_MATERIAL),
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

            (
                "MMS_TEST",
                self.cmd_MMS_TEST,
                "Development only."
            ),
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

        # Already-loaded prints need the same monitors as resumed prints;
        # neither entry may take feeding away from an active MMS operation.
        self.print_observer.register_start_callback(self._start_print_monitors)
        self.print_observer.register_resume_callback(self._start_print_monitors)
        # PAUSE drains queued E before joining Buffer; the observer must not
        # stop supply while that final print movement is still executing.

        # One owner drains old work, unloads and closes heaters for every
        # terminal print state. The observer only schedules that owner.
        self.print_observer.register_finish_callback(
            self.mms_eject.request_finish)

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

    def _start_print_monitors(self):
        if not print_stats_adapter.is_printing() \
                or self.printer_is_shutdown() \
                or pause_resume_adapter.is_paused() \
                or self.mms_resume.is_resuming() \
                or self.mms_resume.get_status()["kind"] is not None \
                or self.mms_eject.finish_is_active() \
                or self._busy_reason(allow_running=True):
            return
        # Detection only arms callbacks. A resumed buffer may already be
        # feeding when this delayed event arrives; it must not suppress runout.
        self.mms_endless_spool.activate()
        if (self._busy_reason()
                or self.mms_endless_spool.check_starting_source()):
            return
        # Explicit swap completion starts its buffer after acceptance. These
        # callbacks must not restart feeding over an active background owner.
        source = self.mms_resume.identify_source_slot()
        if source is None:
            return
        slot = self.get_mms_slot(source)
        buffer = slot.get_mms_buffer()
        generation = self.mms_resume.get_status()["generation"]
        try:
            accepted = self.mms_charge.get_charged_slot() == source
            if accepted and self.filament_detection_is_enabled():
                if slot.inlet.is_released():
                    self.mms_resume.handle_inlet_runout(source)
                    if self.mms_resume.get_status()["pending"]:
                        return
                self.mms_resume.check_loaded_source(source)
            # A reselected strand can follow E until the next physical buffer
            # reference. Never seek a reference from the print observer.
            buffer.activate_monitor(source, allow_unreferenced=accepted)
            if accepted:
                buffer.check_feed(source)
        except Exception as e:
            buffer.pause_after_failure(generation, str(e))

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
        if self._is_connected:
            slot_num = self.mms_charge.get_charged_slot()
            if slot_num is not None:
                self.get_mms_slot(slot_num).update_filament_presence()

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
        - The accepted charged slot takes priority.
        - Otherwise prefer an actively selected slot that is also loading.
        - Then prefer the lowest active selection, followed by any selection.
        - If no slot is selected, use the lowest loading slot.

        -- Selection/loading fallback when no charged slot is known
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

    def _busy_reason(self, allow_running=False):
        if any(op.is_running() for op in (self.mms_swap, self.mms_charge,
                self.mms_eject, self.mms_swap.mms_cut, self.mms_swap.mms_purge,
                self.mms_swap.mms_brush)):
            return "another MMS operation is running"
        if not allow_running:
            delivery = printer_adapter.get_mms_delivery()
            autoload = delivery._get_autoload()
            if (
                delivery.is_stopping()
                or delivery.async_task_sp.is_running()
                or autoload is not None
                and autoload.is_busy()
                or self.mms_selector_is_running()
                or self.mms_drive_is_running(include_buffer=False)
            ):
                return "MMS motion is still running; wait or use MMS_STOP"
        return None

    def _pending_recovery_action(self, recovery):
        if (recovery.get("return_position") is not None
                and not recovery.get("return_valid")):
            return recovery["reason"]
        if recovery.get("print_job"):
            return "use RESUME after repair or CANCEL_PRINT to end the print"
        return ("use RESUME after repair or CLEAR_PAUSE "
                "to discard the manual request")

    def cmd_MMS_CHECK_START(self, gcmd):
        # PRINT_START normally runs after virtual-SD has marked the new file
        # printing. Reject previous work, not that expected current state.
        recovery = self.mms_resume.get_status()
        if self.printer_is_shutdown():
            reason = "printer is shutdown"
        elif self.mms_eject.finish_is_active():
            reason = "print finish is still running"
        elif (
            pause_resume_adapter.is_paused()
            or recovery.get("kind") is not None
            or self.mms_resume.is_resuming()
        ):
            reason = self._pending_recovery_action(recovery)
        elif self.mms_eject.cutter_return_pending():
            reason = "cutter return pending; check the cutter before starting"
        else:
            reason = self._busy_reason()
        if reason:
            raise gcmd.error(f"MMS_CHECK_START blocked: {reason}")

    def check_maintenance(self, gcmd, selector_only=False, allow_running=False,
                          allow_printing=False):
        ("Public-command admission; internal operation calls keep their "
         "owner.")
        command = gcmd.get_command().strip()
        recovery = self.mms_resume.get_status()
        paused = pause_resume_adapter.is_paused()
        # Prepared loading is not a completed recovery: later steps can fail.
        # The existing busy check still protects selector motion ownership.
        repairing = (
            selector_only
            and paused
            and not self.mms_resume.is_resuming()
        )
        reason = None
        if self.printer_is_shutdown():
            reason = "printer is shutdown"
        elif self.mms_eject.finish_is_active():
            reason = "print finish is still running"
        elif print_stats_adapter.is_printing() and not allow_printing:
            reason = "a print is running"
        elif not (repairing or allow_running) and (
            paused
            or recovery.get("kind") is not None
            or self.mms_resume.is_resuming()
        ):
            reason = self._pending_recovery_action(recovery)
        elif (not (selector_only or allow_running or command in (
                "MMS_CUT", "MMS_SIMPLE_CUT", "MMS_EJECT", "MMS_EJECT_UNSELECT"))
                and self.mms_eject.cutter_return_pending()):
            reason = "cutter return pending; check the cutter before moving"
        else:
            if repairing:
                delivery = printer_adapter.get_mms_delivery()
                delivery.clear_finished_print_tail_release()
                delivery.wait_for_print_tail_release()
            reason = self._busy_reason(
                allow_running=allow_running or allow_printing)
            if reason is None and allow_printing:
                printer_adapter.get_mms_delivery().check_background_idle(
                    allow_tail_release=True)
        if reason:
            raise gcmd.error(f"{command} blocked: {reason}")

    def mms_selector_is_running(self):
        for mms_slot in self.get_mms_slots():
            if mms_slot.get_mms_selector().is_running():
                return True
        return False

    def mms_drive_is_running(self, include_buffer=True):
        for mms_slot in self.get_mms_slots():
            drive = mms_slot.get_mms_drive()
            if not drive.is_running():
                continue
            if (not include_buffer and drive.move_type == "buffer_feed"
                    and mms_slot.get_mms_buffer().is_activating()):
                # Public operations take over this cooperative owner through
                # pause_feeds; ordinary replenishment is not a competing task.
                continue
            return True
        return False

    # -- Config enable --
    def filament_detection_is_enabled(self):
        return bool(self.p_mms_config.filament_detection_enable)

    def endless_spool_is_enabled(self):
        return bool(self.p_mms_config.endless_spool_enable)

    # -- MMS Status --
    def _handle_extruder_exit(self, eventtime, state):
        self.exit_sensor_state = "PRESSED" if state else "RELEASED"

    def get_status(self, eventtime=None):
        return {
            "exit_sensor_state": self.exit_sensor_state,
            "recovery": self.mms_resume.get_status(),
            "finish": self.mms_eject.get_finish_status(),
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
        exit_state = self.exit_sensor_state
        info += f"Extruder exit sensor: {exit_state or 'not configured'}\n"
        cutter_state = self.mms_swap.mms_cut.get_status()["cutter_sensor_state"]
        info += f"""Cutter sensor: {
            cutter_state if cutter_state is not None else 'not configured'
        }\n"""
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

    def turn_off_all_heaters(self):
        heaters_adapter.turn_off_all_heaters()
        self.mms_dryer.clear_countdown()
        for extend in self.mms_extends:
            extend.get_mms_dryer().clear_countdown()

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

        dryer = self.mms_dryer
        if extend_num is not None:
            extend = self.get_mms_extend(extend_num)
            if not extend:
                return
            dryer = extend.get_mms_dryer()
        try:
            dryer.stop_heating()
        except printer_adapter.get_printer().config_error as e:
            raise gcmd.error(str(e)) from e

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

    def cmd_MMS_RFID_RESET(self, gcmd):
        """
        Usage:
            MMS_RFID_RESET
        """
        for mms_slot in self.mms_slots:
            mms_slot.slot_rfid.reset()
        self.log_info("MMS RFID reset end")

    def cmd_MMS_LOG(self, gcmd):
        log = self.log_error_s if gcmd.get_int("ERROR", 0) else self.log_info_s
        log(gcmd.get("MSG"))

    def cmd_MMS_LANE_DATA_PULL(self, gcmd):
        if not moonraker_adapter.pull():
            gcmd.respond_info("Moonraker lane data is unavailable")

    def _edit_one_slot(self, gcmd, patch):
        slot_num = gcmd.get_int("SLOT", minval=0)
        if not self.slot_is_available(slot_num):
            raise gcmd.error(f"slot[{slot_num}] is not configured")
        try:
            patch = normalize_filament(patch)
        except ValueError as e:
            raise gcmd.error(str(e)) from None
        self.get_mms_slot(slot_num).update_filament_metadata(patch)

    def cmd_MMS_SLOT_COLOR(self, gcmd):
        self._edit_one_slot(gcmd, {"color": gcmd.get("CODE")})

    def cmd_MMS_SLOT_MATERIAL(self, gcmd):
        self._edit_one_slot(gcmd, {"material": gcmd.get("MATERIAL")})

    def cmd_MMS_SLOT_SPOOL(self, gcmd):
        self._edit_one_slot(gcmd, {"spool_id": gcmd.get_int("SPOOL_ID", -1)})

    def _slot_map_targets(self, gcmd):
        slot = gcmd.get_int("SLOT", None, minval=0)
        gate = gcmd.get_int("GATE", None, minval=0)
        if slot is not None and gate is not None and slot != gate:
            raise gcmd.error("GATE and SLOT must match")
        targets = [slot if slot is not None else gate]
        for arg in ("SLOTS", "GATES"):
            value = gcmd.get(arg, None)
            if value is not None:
                try:
                    targets.extend(int(num) for num in value.split(","))
                except ValueError:
                    raise gcmd.error(f"{arg} must contain slot numbers") from None
        targets = list(dict.fromkeys(num for num in targets if num is not None))
        for num in targets:
            if not self.slot_is_available(num):
                raise gcmd.error(f"slot[{num}] is not configured")
        return targets

    def cmd_MMS_SLOT_MAP(self, gcmd):
        fields = (
            ("VENDOR", "vendor_name"), ("NAME", "name"), ("COLOR", "color"),
            ("MATERIAL", "material"), ("BED_TEMP", "bed_temp"),
            ("NOZZLE_TEMP", "nozzle_temp"), ("SPOOL_ID", "spool_id"),
            ("FILAMENT_ID", "filament_id"),
        )
        params = gcmd.get_command_parameters()
        patch = {key: params[arg] for arg, key in fields if arg in params}
        try:
            patch = normalize_filament(patch)
        except ValueError as e:
            raise gcmd.error(str(e)) from None
        reset = bool(gcmd.get_int("RESET", 0, minval=0, maxval=1))
        quiet = gcmd.get_int("QUIET", 0)
        detail = gcmd.get_int("DETAIL", 0)
        targets = self._slot_map_targets(gcmd)
        if not targets:
            if patch and not reset:
                raise gcmd.error("Specify SLOT or SLOTS to edit filament data")
            targets = self.get_slot_nums()

        if patch or reset:
            for num in targets:
                self.get_mms_slot(num).update_filament_metadata(patch, reset)

        if not quiet:
            report = {}
            for num in targets:
                row = self.get_mms_slot(num).meta.lane_data()
                data = {key: row[key] for key in ("material", "color", "name")}
                data["vendor"] = row["vendor_name"]
                if detail:
                    for key in ("spool_id", "filament_id", "bed_temp",
                                "nozzle_temp"):
                        data[key] = row[key]
                report[str(num)] = data
            gcmd.respond_info(json.dumps(report))

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
        mms_slot.truncate_meta()

    def cmd_MMS_TEST(self, gcmd):
        return


def load_config(config):
    mms = MMS(config)
    printer_adapter.notify_mms_extend(mms)
    return mms
