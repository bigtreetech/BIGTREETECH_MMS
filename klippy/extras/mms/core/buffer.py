# Support for MMS Buffer
#
#   +----------------------------------------------+
# ==|= Gate =|                                  |  |
# ==|= Gate =|                                  |  |
#   |        += Runout =\/\/\/\/\/\/\/= Outlet =|==| Extruder
# ==|= Gate =|          |<= Spring =>|         /   | --> Extrude
# ==|= Gate =|      Relax <--    --> Compress /    | <-- Retract
#   +----------------------------------------+
#
# Copyright (C) 2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import math
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, fields

from ..adapters import (
    extruder_adapter,
    gcode_adapter,
    printer_adapter,
)
from ..core.exceptions import DeliveryTerminateSignal
from ..core.task import PeriodicTask


@dataclass(frozen=True)
class BufferConfig:
    """
    Spring of Buffer

    Max relaxation
        /\/\/\/\/\/\/\/\/\/\/
    Max compression
        ||||||
             |<-- Stroke -->|
      max_compression   min_compression
    """
    # Spring's stroke between max compression and max relaxation, in mm
    spring_stroke: float = 20.0
    # Diameter of filament loaded in buffer, in mm
    filament_diameter: float = 1.75

    # Target percentage of buffer, in % rate
    target_percentage: float = 50.0

    # Feed configs
    # Minimum feed volume, in mm^3
    min_deliver_volume: float = 2.0

    # Spring stroke measure config
    # Max distance of Spring stroke measure, in mm
    # max_measure_distance: float = 15
    # Distance of per step, in mm
    measure_step: float = 1
    measure_speed: float = 10.0
    measure_accel: float = 10.0

    # Period of Extruder monitor task, in second
    monitor_period: float = 0.2

    # Extruder extrude/retract distance limit
    e_distance_moved_min: float = -20
    e_distance_moved_max: float = 100

    # ---- Calculate ----
    # Cross section of filament, in mm^2
    filament_cross_section: float = math.pi * (
        (filament_diameter * 0.5) ** 2
    )
    # The total capacity of buffer
    buffer_capacity: float = filament_cross_section * spring_stroke
    buffer_empty: float = 0.0

    # Buffer volume configs
    max_volume: float = buffer_capacity
    min_volume: float = buffer_empty
    target_volume: float = (max_volume - min_volume) * target_percentage / 100

    # max_distance: float = spring_stroke * target_percentage / 100
    # min_distance: float = 2.0


class Buffer:
    def __init__(self):
        buffer_config = BufferConfig()
        for field in fields(buffer_config):
            key = field.name
            # Don't cover
            if not hasattr(self, key):
                val = getattr(buffer_config, key)
                setattr(self, key, val)

        self._index = None
        # Sensor setting
        self._sensor_full = None
        self._sensor_runout = None

        # Current volume
        self._volume = 0
        # Last extruder position
        self._last_e_position = 0
        # Monitor periodic task
        self._p_task = None

        # Flags
        # Spring stroke measure flag
        self._stroke_is_measured = False
        # Activate flag
        self._is_activating = False
        # Volume freeze flag
        self._is_freezing = False
        # Inlet triggered flag
        self._inlet_triggered_before = False

        # Printer status
        self._is_ready = False
        printer_adapter.register_klippy_ready(
            self._handle_klippy_ready)

    def _handle_klippy_ready(self):
        self._initialize_mms()
        self._initialize_gcode()
        self._initialize_loggers()
        self._initialize_task()
        self._is_ready = True

    def _initialize_mms(self):
        self.mms = printer_adapter.get_mms()
        self.mms_delivery = printer_adapter.get_mms_delivery()
        self.mms_filament_fracture = self.mms.get_mms_filament_fracture()

    def _initialize_gcode(self):
        commands = [
            ("MMS_BUFFER_ACTIVATE", self.cmd_MMS_BUFFER_ACTIVATE),
            ("MMS_BUFFER_DEACTIVATE", self.cmd_MMS_BUFFER_DEACTIVATE),
            ("MMS_BUFFER_MEASURE", self.cmd_MMS_BUFFER_MEASURE),

            ("MMS_BUFFER_FILL", self.cmd_MMS_BUFFER_FILL),
            ("MMS_BUFFER_CLEAR", self.cmd_MMS_BUFFER_CLEAR),
            ("MMS_BUFFER_HALFWAY", self.cmd_MMS_BUFFER_HALFWAY),
        ]
        gcode_adapter.bulk_register(commands)

    def _initialize_loggers(self):
        mms_logger = printer_adapter.get_mms_logger()
        self.log_info = mms_logger.create_log_info(console_output=True)
        self.log_warning = mms_logger.create_log_warning(console_output=True)
        self.log_error = mms_logger.create_log_error(console_output=True)

        self.log_info_s = mms_logger.create_log_info(console_output=False)
        self.log_warning_s = mms_logger.create_log_warning(console_output=False)

    def _initialize_task(self):
        self._p_task = PeriodicTask()
        self._p_task.set_period(self.monitor_period)

    # ---- Monitor ----
    def _monitor(self):
        self._check_sensors()

        # e_position = extruder_adapter.get_position()
        e_position, e_speed = extruder_adapter.get_position_speed()
        e_distance_moved = e_position - self._last_e_position
        if not e_distance_moved:
            # No extrude/retract
            return
        elif e_distance_moved <= self.e_distance_moved_min \
            or abs(e_distance_moved) >= self.e_distance_moved_max:
            self.log_warning_s(
                "\n"
                "########################################\n"
                f"extruder moved distance {e_distance_moved:.2f}mm"
                f" overlimit, skip...\n"
                f"e_distance_moved: {e_distance_moved:.2f}\n"
                f"e_position: {e_position:.2f}\n"
                f"last_e_position: {self._last_e_position:.2f}\n"
                f"e_distance_moved_min: {self.e_distance_moved_min:.2f}\n"
                f"e_distance_moved_max: {self.e_distance_moved_max:.2f}\n"
                # f"extruder speed: {e_speed}\n"
                "########################################"
            )
            # Reset
            self._last_e_position = e_position
            return

        # Extrude: e_distance_moved > 0
        # Retract: e_distance_moved < 0
        self.log_info_s(f"extruder moved distance: {e_distance_moved:.2f} mm")

        # Update last extruder position
        self._last_e_position = e_position

        # Update volume
        e_volume = (e_distance_moved
                    * extruder_adapter.get_extruder_filament_area())
        new_volume = self._volume - e_volume
        self._set_volume(new_volume)

        if self._volume < self.target_volume:
            # Feed
            feed_volume = self.target_volume - self._volume
            if feed_volume < self.min_deliver_volume:
                return

            with self._freeze_volume():
                delivered_vol = self._feed(feed_volume, e_speed)
                if delivered_vol:
                    new_volume = self._volume + delivered_vol
                    self._set_volume(new_volume)

        elif self._volume > self.max_volume:
            # Retract or Release or Negative Select
            retract_volume = self._volume - self.max_volume
            if retract_volume < self.min_deliver_volume:
                return

            with self._freeze_volume():
                delivered_vol = self._retract(retract_volume)
                if delivered_vol:
                    new_volume = self._volume - delivered_vol
                    self._set_volume(new_volume)

        elif self._volume >= self.target_volume \
            and self._volume <= self.max_volume:
            # Hold, do nothing
            return

    def _set_volume(self, new_volume):
        old_volume = self._volume
        # self._volume = max(
        #     self.min_volume,
        #     min(new_volume, self.max_volume)
        # )
        self._volume = new_volume

        if not self._is_ready \
            or not self._is_activating \
            or old_volume == new_volume:
            return

        self.log_info_s(
            "buffer volume update\n"
            f"old: {old_volume:.2f}\n"
            f"new: {new_volume:.2f}\n"
            # f"set: {self._volume:.2f}\n"
            f"pct: {self.get_volume_percentage():.2f}%"
        )

        if new_volume == self.max_volume:
            self.log_info_s(
                f"buffer volume is full: {self.max_volume:.2f} mm^3")
        elif new_volume == self.min_volume:
            self.log_info_s(
                f"buffer volume is empty: {self.min_volume:.2f} mm^3")

    @contextmanager
    def _freeze_volume(self):
        self._is_freezing = True
        try:
            yield
        finally:
            self._is_freezing = False
            # self._check_sensors()

    def activate_monitor(self):
        if not self._p_task \
            or self._p_task.is_running() \
            or self._is_activating:
            self.log_warning_s("another buffer monitor task is activating")
            return

        # Reset last extruder position
        self._last_e_position = extruder_adapter.get_position()

        try:
            if self._p_task.schedule(self._monitor):
                self._p_task.start()
        except Exception as e:
            self.log_error(f"buffer monitor activate error: {e}")
            return

        self.log_info_s("buffer monitor activated")
        self._is_activating = True
        self._inlet_triggered_before = False

    def deactivate_monitor(self):
        if not self._p_task \
            or not self._p_task.is_running() \
            or not self._is_activating:
            return

        try:
            self._p_task.stop()
        except Exception as e:
            self.log_error(f"buffer monitor deactivate error: {e}")
            return

        self.log_info_s("buffer monitor deactivated")
        self._is_activating = False
        self._inlet_triggered_before = False

    # ---- Feed & Retract ----
    # def _simple_move_old(self, slot_num, distance, speed, accel):
    #     mms_slot = self.mms.get_mms_slot(slot_num)
    #     mms_drive = mms_slot.get_mms_drive()
    #     mms_drive.update_focus_slot(slot_num)
    #     # No select method
    #     # if not mms_slot.selector_is_triggered():
    #     #     self.mms_delivery.select_slot(slot_num)
    #     # Manual Move
    #     mms_drive.manual_move(distance, speed, accel)

    def _simple_move(self, slot_num, distance, speed, accel):
        mms_slot = self.mms.get_mms_slot(slot_num)
        mms_drive = mms_slot.get_mms_drive()
        mms_drive.update_focus_slot(slot_num)
        # No select method
        # if not mms_slot.selector_is_triggered():
        #     self.mms_delivery.select_slot(slot_num)

        # Inlet is triggered last move and now is released
        if self._inlet_triggered_before \
            and mms_slot.inlet.is_released():
            self.mms_filament_fracture.handle_while_feeding(slot_num)
            return

        # Record Inlet is triggered
        self._inlet_triggered_before = mms_slot.inlet.is_triggered()

        # Move
        context = (
            self.mms_filament_fracture.monitor_while_feeding(slot_num)
            if distance>0 else nullcontext()
        )
        with context:
            # Drip Move
            # mms_drive.drip_move(distance, speed, accel)
            # Manual Move
            mms_drive.manual_move(distance, speed, accel)

        # Inlet is triggered before manual_move and now is released
        if self._inlet_triggered_before \
            and mms_slot.inlet.is_released():
            self.mms_filament_fracture.handle_while_feeding(slot_num)

    def _feed(self, volume, extrude_speed):
        if not volume or volume < 0:
            self.log_warning(
                f"buffer feed failed: unavailable volume: {volume}")
            return None

        slot_num = self.mms.get_current_slot()
        if slot_num is None:
            self.log_warning("buffer feed failed: no active slot")
            return None

        distance = volume / self.filament_cross_section

        # extruder velocity could be 0
        # speed = extruder_adapter.get_velocity() or distance
        # 'distance' => 1s done
        # 'distance' * 2 => 0.5s done
        speed = (distance*2 if not extrude_speed
                 else min(distance*2, extrude_speed))
        accel = speed

        self.log_info_s(
            "\n"
            f"slot[{slot_num}] buffer feed:\n"
            f"volume: {volume:.2f} mm^3\n"
            f"distance: {distance:.2f} mm\n"
            f"speed: {speed:.2f} mm/s\n"
            f"accel: {accel:.2f} mm/s^2"
        )
        # Simple log for console
        # self.log_info(
        #     f"slot[{slot_num}] buffer feed distance: {distance:.2f} mm")

        try:
            self._simple_move(slot_num, abs(distance), speed, accel)
            return volume
        except Exception as e:
            self.log_error(f"buffer feed failed: {e}")

        return None

    def _retract(self, volume):
        if not volume or volume < 0:
            self.log_warning(
                f"buffer feed failed: unavailable volume: {volume}")
            return None

        slot_num = self.mms.get_current_slot()
        if slot_num is None:
            self.log_warning("buffer feed failed: no active slot")
            return None

        distance = volume / self.filament_cross_section
        # 'distance' => 1s done
        # 'distance' * 2 => 0.5s done
        speed = distance * 2
        accel = speed

        self.log_info_s(
            "\n"
            f"slot[{slot_num}] buffer retract:\n"
            f"volume: {volume:.2f} mm^3\n"
            f"distance: {distance:.2f} mm\n"
            f"speed: {speed:.2f} mm/s\n"
            f"accel: {accel:.2f} mm/s^2"
        )
        # Simple log for console
        # self.log_info(
        #     f"slot[{slot_num}] buffer retract distance: {distance:.2f} mm")

        try:
            self._simple_move(slot_num, -abs(distance), speed, accel)
            return volume
        except Exception as e:
            self.log_error(f"buffer retract failed: {e}")

        return None

    # ---- Control ----
    def fill(self, slot_num, speed=None, accel=None):
        if not self._stroke_is_measured:
            self.measure_stroke(slot_num)

        if self.is_full():
            return True

        try:
            self.mms_delivery.load_to_outlet(
                slot_num, speed=speed, accel=accel
            )
            self.log_info_s(f"slot[{slot_num}] fill mms_buffer success")
            return True
        except DeliveryTerminateSignal:
            self.log_error(f"slot[{slot_num}] fill mms_buffer is terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] fill mms_buffer error: {e}")
            return False

    def clear(self, slot_num, speed=None, accel=None):
        if not self._stroke_is_measured:
            self.measure_stroke(slot_num)

        if self.is_empty():
            return True

        try:
            self.mms_delivery.unload_until_buffer_runout_trigger(
                slot_num, speed=speed, accel=accel
            )
            self.log_info_s(f"slot[{slot_num}] clear mms_buffer success")
            return True
        except DeliveryTerminateSignal:
            self.log_error(f"slot[{slot_num}] clear mms_buffer is terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] clear mms_buffer error: {e}")
            return False

    def halfway(self, slot_num, speed=None, accel=None):
        if not self._stroke_is_measured:
            self.measure_stroke(slot_num)

        try:
            # First let buffer_runout trigger
            self.mms_delivery.unload_until_buffer_runout_trigger(
                slot_num, speed=speed, accel=accel
            )
            # Secondary let buffer_runout release
            self.mms_delivery.load_until_buffer_runout_release(
                slot_num, speed=speed, accel=accel
            )

            # Than move forward half of spring stroke
            distance = abs(self.spring_stroke * 0.5)
            speed = speed or distance * 2
            accel = accel or distance * 2
            # success = self.mms_delivery.mms_drip_move(
            #             slot_num, distance, speed, accel)
            success = self.mms_delivery.mms_move(
                        slot_num, distance, speed, accel)
            if not success:
                return False

            # Finally set volume
            self._handle_half()
            self.log_info_s(f"slot[{slot_num}] halfway mms_buffer success")
            return True

        except DeliveryTerminateSignal:
            self.log_error(f"slot[{slot_num}] halfway mms_buffer is terminated")
            return False
        except Exception as e:
            self.log_error(f"slot[{slot_num}] halfway mms_buffer error: {e}")
            return False

    def measure_stroke(self, slot_num, force=False):
        if self._stroke_is_measured and not force:
            return

        try:
            mms_drive = self.mms.get_mms_slot(slot_num).get_mms_drive()

            self.log_info_s(f"slot[{slot_num}] measure buffer stroke begin")

            self.mms_delivery.load_to_outlet(slot_num)
            self.mms_delivery.unload_until_buffer_runout_trigger(
                slot_num = slot_num,
                speed = self.measure_speed,
                accel = self.measure_accel
            )

            distance_moved = round(abs(mms_drive.get_distance_moved()), 4)
            old_stroke = self.spring_stroke
            self.spring_stroke = min(distance_moved, old_stroke)
            self._stroke_is_measured = True
            self.log_info_s(
                "buffer spring stroke is measured, "
                f"update from {old_stroke} mm to {self.spring_stroke} mm")

        except DeliveryTerminateSignal:
            self.log_error(
                f"slot[{slot_num}] measure mms_buffer is terminated")
        except Exception as e:
            self.log_error(
                f"slot[{slot_num}] measure mms_buffer stroke error: {e}")

    # ---- Pins trigger/release handlers and check ----
    def _handle_full(self, mcu_pin):
        if self._is_freezing:
            return
        self._set_volume(self.max_volume)

    def _handle_runout(self, mcu_pin):
        if self._is_freezing:
            return
        self._set_volume(self.min_volume)

        # if extruder_adapter.is_extruding():
        #     self.log_warning("buffer volume is minimum but still"
        #                      " extruding")
        #     # Force feed
        # elif extruder_adapter.is_retracting():
        #     self.log_warning("buffer volume is minimum but still"
        #                      " retracting")
        # else:
        #     self.log_warning("buffer volume is minimum but not"
        #                      " extruding/retracting")
        #     # Force feed

    def _handle_half(self):
        if self._is_freezing:
            return
        self._set_volume(
            (self.max_volume - self.min_volume) / 2)

    def _check_sensors(self):
        if self._is_freezing:
            return
        # If both _sensor_full and _sensor_runout are triggered?
        if self.is_full():
            self._set_volume(self.max_volume)
        elif self.is_empty():
            self._set_volume(self.min_volume)

    def set_sensor_full(self, mms_button):
        if self._sensor_full is mms_button:
            # Already set
            return
        self._sensor_full = mms_button
        self._sensor_full.register_trigger_callback(self._handle_full)

    def set_sensor_runout(self, mms_button):
        if self._sensor_runout is mms_button:
            # Already set
            return
        self._sensor_runout = mms_button
        self._sensor_runout.register_trigger_callback(self._handle_runout)

    def set_index(self, index):
        self._index = index

    # ---- Status ----
    def is_activating(self):
        return self._is_activating

    def is_full(self):
        # if self._is_freezing \
        #     and self._sensor_full \
        #     and self._sensor_full.is_triggered():
        #         return True
        # self._check_sensors()
        # return self._volume == self.max_volume
        full = self._sensor_full and self._sensor_full.is_triggered()
        return True if full else False

    def is_empty(self):
        # if self._is_freezing \
        #     and self._sensor_runout \
        #     and self._sensor_runout.is_triggered():
        #         return True
        # self._check_sensors()
        # return self._volume == self.min_volume
        runout = self._sensor_runout and self._sensor_runout.is_triggered()
        return True if runout else False

    def get_spring_stroke(self):
        return self.spring_stroke

    def get_volume(self):
        return self._volume

    def get_volume_percentage(self):
        return round(
            self._volume/(self.max_volume-self.min_volume), 4
        ) * 100

    def get_index(self):
        return self._index

    def get_status(self):
        return {
            "index" : self._index,

            "volume" : self._volume,
            "pct" : self.get_volume_percentage(),

            "is_activating" : self._is_activating,
            "is_freezing" : self._is_freezing,
            "stroke_is_measured" : self._stroke_is_measured,

            # Static config
            "spring_stroke" : self.spring_stroke,
            "filament_diameter" : self.filament_diameter,
            "target_percentage" : self.target_percentage,

            "filament_cross_section" : self.filament_cross_section,
            "max_volume" : self.max_volume,
            "min_volume" : self.min_volume,
            "target_volume" : self.target_volume,
            "min_deliver_volume" : self.min_deliver_volume,

            "measure_step" : self.measure_step,
            "measure_speed" : self.measure_speed,
            "measure_accel" : self.measure_accel,

            "monitor_period" : self.monitor_period,
        }

    # ---- GCode command ----
    def cmd_MMS_BUFFER_ACTIVATE(self, gcmd):
        self.activate_monitor()

    def cmd_MMS_BUFFER_DEACTIVATE(self, gcmd):
        self.deactivate_monitor()

    def cmd_MMS_BUFFER_MEASURE(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        force = gcmd.get_int("FORCE", 0)
        force = True if force==1 else False
        if not self.mms.slot_is_available(slot_num):
            return

        if self._stroke_is_measured:
            self.log_info(
                f"slot[{slot_num}] buffer spring stroke: "
                f"{self.spring_stroke} mm"
            )
            if not force:
                return

        self.measure_stroke(slot_num, force=True)

    def cmd_MMS_BUFFER_FILL(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        speed = gcmd.get_float("SPEED", default=None, minval=0.)
        accel = gcmd.get_float("ACCEL", default=None, minval=0.)
        if not self.mms.slot_is_available(slot_num):
            return
        self.fill(slot_num, speed, accel)

    def cmd_MMS_BUFFER_CLEAR(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        speed = gcmd.get_float("SPEED", default=None, minval=0.)
        accel = gcmd.get_float("ACCEL", default=None, minval=0.)
        if not self.mms.slot_is_available(slot_num):
            return
        self.clear(slot_num, speed, accel)

    def cmd_MMS_BUFFER_HALFWAY(self, gcmd):
        slot_num = gcmd.get_int("SLOT", minval=0)
        speed = gcmd.get_float("SPEED", default=None, minval=0.)
        accel = gcmd.get_float("ACCEL", default=None, minval=0.)
        if not self.mms.slot_is_available(slot_num):
            return
        self.halfway(slot_num, speed, accel)
