# Support for MMS Cut
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from contextlib import contextmanager
from dataclasses import dataclass, fields

from ..adapters import (
    buttons_adapter,
    gcode_adapter,
    printer_adapter,
    toolhead_adapter,
)
from ..core.config import OptionalField, PointType, PrinterConfig
from ..core.exceptions import MMSException


@dataclass(frozen=True)
class PrinterCutConfig(PrinterConfig):
    # Enable/disable the cutter module
    # 0 = disable, 1 = enable
    # When disabled, all cutter operations will be skipped
    enable: int = 1

    # Z-axis lift distance during cutting operations
    # Unit: mm
    z_raise: float = 1.0

    # Toolhead movement speed from cut_init to cut_final position
    # Unit: mm/min
    cut_speed: float = 2000.0

    # (position_x, position_y)
    # X/Y coordinates of the initial cutting point
    cutter_init_point: PointType = "(40.0, 50.0)"
    # X/Y coordinates of the final cutting point
    cutter_final_point: PointType = "(20.0, 50.0)"

    # Optional sensor pin, owned by this module. ADC options are read at setup.
    cutter_sensor_pin: OptionalField = ""
    # Maximum feedback wait after movement, not an unconditional dwell.
    cutter_sensor_timeout: float = 0.5

    def get_type_handlers(self):
        handlers = super().get_type_handlers()
        handlers[float] = lambda config, name: (
            config.getfloat(name, 0.5, above=0.)
            if name == "cutter_sensor_timeout" else config.getfloat(name))
        return handlers


class MMSCut:
    def __init__(self, config):
        p_cut_config = PrinterCutConfig(config)
        for field in fields(p_cut_config):
            key = field.name
            # Don't cover
            if not p_cut_config.should_skip(key) \
                and not hasattr(self, key):
                val = getattr(p_cut_config, key)
                setattr(self, key, val)

        # State tracking
        self._is_running = False
        self._return_pending = False
        self._sensor_state = "RELEASED"
        self.reactor = printer_adapter.get_reactor()
        self._initialize_sensor(config)

        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    # ---- Initial ----
    def _initialize_sensor(self, config):
        analog_range = config.getfloatlist(
            "cutter_sensor_analog_range", None, count=2)
        pullup = config.getfloat(
            "cutter_sensor_analog_pullup_resistor", 4700., above=0.)
        if analog_range is not None:
            if not self.cutter_sensor_pin:
                raise config.error(
                    "cutter_sensor_analog_range requires cutter_sensor_pin"
                )
            if not 0. <= analog_range[0] < analog_range[1]:
                raise config.error(
                    "cutter_sensor_analog_range requires 0 <= min < max"
                )
        if not self.cutter_sensor_pin:
            return
        buttons_adapter.register_sensor(
            config, self.cutter_sensor_pin, self._sensor_callback,
            analog_range, pullup)

    def _sensor_callback(self, eventtime, state):
        self._sensor_state = "PRESSED" if state else "RELEASED"

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()

    def _initialize_gcode(self):
        commands = [
            ("MMS_CUT", self.cmd_MMS_CUT),
            ("MMS_SIMPLE_CUT", self.cmd_MMS_SIMPLE_CUT),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)
        self.log_info_s = mms_logger.create_log_info(console_output=False)

    # ---- Status ----
    def is_enabled(self):
        return bool(self.enable)

    def is_running(self):
        return self._is_running

    @contextmanager
    def _cut_is_running(self):
        self._is_running = True
        try:
            yield
        finally:
            self._is_running = False

    def cutter_return_pending(self):
        return self._return_pending

    def get_status(self, eventtime=None):
        return {
            "cutter_final_point_x": self.cutter_final_point[0],
            "cutter_final_point_y": self.cutter_final_point[1],
            "cutter_sensor_state": (
                self._sensor_state if self.cutter_sensor_pin else None
            ),
        }

    # ---- Cut ----
    def _generation(self):
        return self.mms.get_mms_resume().get_status()["generation"]

    def _check_generation(self, generation):
        if (self.mms.printer_is_shutdown()
                or generation != self._generation()
                or self.mms_delivery.async_task_sp.current_is_cancelled()):
            raise MMSException(
                "cutter operation was cancelled or printer shut down"
            )

    def _wait_sensor(self, expected, generation):
        if not self.cutter_sensor_pin:
            return
        deadline = self.reactor.monotonic() + self.cutter_sensor_timeout
        while True:
            self._check_generation(generation)
            now = self.reactor.monotonic()
            state = self._sensor_state
            if state == expected:
                return
            if now >= deadline:
                instruction = (
                    "inspect the cutter stroke and sensor before retrying"
                    if expected == "PRESSED"
                    else "clear trapped filament and release the cutter "
                    "before retrying"
                )
                raise MMSException(
                    f"cutter sensor '{self.cutter_sensor_pin}' is {state}, "
                    f"expected {expected}; {instruction}")
            self.reactor.pause(min(deadline, now + 0.01))

    def check_released(self):
        # Called before cutting/loading and after complete ejection, never
        # immediately after cut_init when the blade may still hold filament.
        self._wait_sensor("RELEASED", self._generation())

    def _safety_checks(self):
        if self.is_running():
            self.log_warning("another cut is running, return")
            return False
        # Check toolhead
        if not toolhead_adapter.is_homed():
            self.log_warning("toolhead is not homed, return")
            return False

        return True

    def cut_init(self):
        # The caller checks ownership/cancellation. Native G-code keeps this
        # return stroke possible even when a filament fault blocks the swap.
        # The enclosing motion scope already normalized coordinates/speed.
        gcode_adapter.run_command(
            f"G1 X{self.cutter_init_point[0]} "
            f"Y{self.cutter_init_point[1]} "
            f"F{toolhead_adapter.get_move_speed()}\nM400", strict=True)

    def _return_from_cut(self):
        self.cut_init()
        # The return stroke has drained. Filament may still hold the blade
        # and is checked after withdrawal by the eject owner.
        self._return_pending = False

    def finish_pending_return(self):
        if not self._return_pending:
            return False
        if not self._safety_checks():
            raise MMSException(
                "Cannot return the cutter; check the mechanism and XYZ"
            )
        self._check_generation(self._generation())
        with self._cut_is_running(), toolhead_adapter.motion_scope():
            self._return_from_cut()
        return True

    def cut_final(self):
        # Toolhead move to the cut final X-axis and Y-axis of cutter
        toolhead_adapter.move_xy(
            position_x = self.cutter_final_point[0],
            position_y = self.cutter_final_point[1],
            speed = self.cut_speed,
            wait_toolhead = True
        )

    def mms_cut(self, on_pressed=None, on_returned=None):
        if not self._safety_checks():
            return False

        if not self.is_enabled():
            self.log_warning("slot[*] cut is disabled")
            return False

        if self._return_pending:
            self.log_warning("finish the previous cutter return first")
            return False

        self.log_info_s("slot[*] cut begin")
        with self._cut_is_running():
            try:
                # A failed monitor handoff must reject cutting as a normal
                # command error, and leave the cut operation retryable.
                for mms_buffer in self.mms.get_mms_buffers():
                    mms_buffer.deactivate_monitor()
                generation = self._generation()
                self._wait_sensor("RELEASED", generation)
                toolhead_adapter.check_motion()
                self.cut_init()
                self._check_generation(generation)
                pressed = False
                try:
                    printer_adapter.get_mms_charge().invalidate_loaded_slot()
                    self._return_pending = True
                    self.cut_final()
                    self._wait_sensor("PRESSED", generation)
                    pressed = True
                    if on_pressed is not None:
                        self._check_generation(generation)
                        on_pressed()
                finally:
                    # Leave the cutter along its configured return stroke,
                    # including missing feedback, before failure can park XY.
                    # Cancellation/shutdown must not start another move.
                    self._check_generation(generation)
                    if self._return_pending:
                        self._return_from_cut()
                    if pressed and on_returned is not None:
                        self._check_generation(generation)
                        on_returned()
                self._check_generation(generation)
                toolhead_adapter.check_motion()
            except Exception as e:
                self.log_error(f"slot[*] cut error: {e}")
                return False

        self.log_info_s("slot[*] cut finish")
        return True

    def _manual_cut(self, gcmd, simple=False):
        generation = self._generation()

        def check_current():
            self._check_generation(generation)

        check_current()
        if self.finish_pending_return():
            return True
        if not toolhead_adapter.is_homed():
            gcode_adapter.run_command('G28', strict=True)
        check_current()
        # Simple cut keeps the current Z and has no saved-position return.
        with toolhead_adapter.motion_scope(
            z_raise=0.0 if simple else self.z_raise, check=check_current
        ) as state:
            if not self.mms_cut():
                raise gcmd.error(f"{gcmd.get_command()} failed")
            if not simple:
                toolhead_adapter.return_to_saved(state, clearance=self.z_raise)
        return True

    def cmd_MMS_CUT(self, gcmd):
        self.mms.check_maintenance(gcmd)
        self.mms_delivery.deliver_task(
            self._manual_cut, {"gcmd": gcmd}, wait=True)

    def cmd_MMS_SIMPLE_CUT(self, gcmd):
        self.mms.check_maintenance(gcmd)
        self.mms_delivery.deliver_task(
            self._manual_cut, {"gcmd": gcmd, "simple": True}, wait=True)


def load_config(config):
    return MMSCut(config)
