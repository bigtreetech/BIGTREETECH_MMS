# Support for MMS ADC
#
# Copyright (C) 2024-2025 Garvey Ding <garveyding@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import bisect
import math
import time

from collections import deque
from dataclasses import dataclass

from ..adapters import pins_adapter, printer_adapter


@dataclass(frozen=True)
class ADCConfig:
    """Configuration for ADC sampling and reporting"""
    # Time interval in seconds to report ADC readings
    report_time: float = 0.01
    # Time interval in seconds to sample ADC readings
    sample_time: float = 0.001
    # Number of samples to take for averaging
    sample_count: int = 6
    # No need to do debounce check for MMS pins now
    # debounce_time = 0.35

    # Length of ADC value history
    adc_window_size: int = 10
    # Scale adc_upper for adc_middle calculation
    adc_upper_scale: float = 1.1
    # ADC value realtime interval upper-lower = delta
    interval_delta_threshold: int = 200
    # ADC threshold init trigger judge
    # Available if adc_upper-adc_lower<interval_delta_threshold
    init_trigger_threshold: int = 150


@dataclass(frozen=True)
class DetectorConfig:
    trigger: int = 1
    release: int = 0

    # == Parameters for edge detection algorithm ==
    # Samples for trend averaging
    trend_window_size: int = 5

    # Sigma multiplier for rising edges
    rise_sensitivity: float = 0.8
    # Sigma multiplier for falling edges
    fall_sensitivity: float = 1.6

    # Threshold scaling for slow changes
    slow_scale: float = 1.3
    # Threshold scaling for fast changes
    fast_scale: float = 0.8
    # Minimum trend magnitude for detection
    min_trend: float = 50.0

    # Outlet params should be more sensitive
    outlet_slow_scale: float = 2.0
    outlet_fast_scale: float = 0.8
    outlet_min_trend: float = 5.0


class MMSAdc:
    """
    ADC signal monitoring and state detection class for handling ADC
    signal changes on MCU pins.
    Supports trigger/release event detection and provides monitoring
    capabilities.
    """
    def __init__(self, config, mcu_pin):
        self._init_dependencies(config)
        self._parse_mcu_pin(mcu_pin)
        self._setup_adc_config()
        self._init_state_variables()
        self._setup_adc_hardware()

        printer_adapter.register_klippy_connect(
            self._handle_klippy_connect)

    # ==== Initialization ====
    def _init_dependencies(self, config):
        """Initialize required printer dependencies"""
        self.mms_name = None

    def _parse_mcu_pin(self, mcu_pin):
        """Parse and validate MCU pin configuration"""
        # Process pin format (e.g., "buffer:PA4")
        # mcu_pin -> "!buffer:PA4"
        self.mcu_pin = mcu_pin
        self.invert = mcu_pin.startswith("!")
        # chip_pin -> "buffer:PA4"
        chip_pin = mcu_pin.lstrip("!")
        # chip_name -> "buffer"
        self.chip_name = chip_pin.split(':')[0]
        # pin -> "PA4"
        self.pin = chip_pin.split(':')[-1]

    def _setup_adc_config(self):
        """Load ADC configuration parameters"""
        self.adc_config = ADCConfig()
        self.interval_delta_threshold = self.adc_config.interval_delta_threshold
        self.init_trigger_threshold = self.adc_config.init_trigger_threshold
        self.adc_upper_scale = self.adc_config.adc_upper_scale

    def _init_state_variables(self):
        """Initialize state tracking variables"""
        # ADC value window for trend analysis
        self.adc_window = deque(maxlen=self.adc_config.adc_window_size)
        # Lazy initialization from MCU
        self.adc_max = None

        # Dynamic range tracking
        # Current window maximum
        self.adc_upper = 0
        # Current window minimum
        self.adc_lower = 9999
        # Dynamic calculated midpoint
        self.adc_middle = 5000

        # State variables
        self.state_trigger = 1
        self.state_release = 0
        # Current state
        self.state = None
        # Previous state
        self.state_prev = None
        # self.last_trigger_at = 0
        # self.last_release_at = 0

        # Edge detection processor
        self.detector = EdgeDetector()

        # Callback handlers
        self.trigger_callback = None
        self.release_callback = None

        # Monitoring control
        self.monitor_active = False
        self.monitor_callback = None

    def _setup_adc_hardware(self):
        """Configure MCU ADC hardware interface"""
        pins_adapter.allow_multi_use_pin(self.mcu_pin)
        # Configure ADC sampling parameters
        self.mcu_adc = pins_adapter.setup_adc(self.mcu_pin)
        self.mcu_adc.setup_adc_sample(
            self.adc_config.sample_time,
            self.adc_config.sample_count)
        self.mcu_adc.setup_adc_callback(
            self.adc_config.report_time,
            self.adc_callback)

        # Store MCU reference
        self.mcu = self.mcu_adc.get_mcu()
        self.mcu_name = self.mcu.get_name()

    def _handle_klippy_connect(self):
        return

    # ==== Configuration ====
    def set_max_adc(self):
        """Lazy initialize ADC maximum value from MCU"""
        self.adc_max = self.mcu.get_constant_float("ADC_MAX")

    def set_trigger_callback(self, callback):
        """Register trigger callback handler"""
        self.trigger_callback = callback

    def set_release_callback(self, callback):
        """Register release callback handler"""
        self.release_callback = callback

    # ==== Core ADC Processing ====
    def adc_callback(self, read_time, read_value):
        """
        Main ADC data callback handler
        Func register and callback by class MCU_adc
        """
        if self.adc_max is None:
            self.set_max_adc()
        # Convert normalized value to actual ADC reading
        adc_value = int(read_value * self.adc_max)
        self.adc_window.append(adc_value)

        # ADC interval upper/lower tracking, update dynamic range
        self._update_adc_interval(adc_value)
        # ADC trigger/release detect
        self.detect()
        # ADC Monitor
        self._handle_monitoring(adc_value)

    def _update_adc_interval(self, adc_value):
        """Update dynamic range values and recalculate midpoint"""
        self.adc_upper = max(self.adc_upper, adc_value)
        self.adc_lower = min(self.adc_lower, adc_value)
        # Calculate weighted midpoint
        self.adc_middle = int(
            (self.adc_upper * self.adc_upper_scale
             + self.adc_lower) // 2)

    # ==== State Detection Logic ====
    def detect(self):
        """Main state detection handler with priority logic"""
        if not self._interval_delta_available():
            # Fallback to initial threshold detection
            if self._check_init_trigger():
                self.trigger()
            return

        # Primary detection using midpoint threshold
        if self._check_mid_trigger():
            self.trigger()
            return
        elif self._check_mid_release():
            self.release()
            return

        # Edge detect fallback
        rising,falling = self.detector.detect(self.adc_window)
        if not any((rising, falling)):
            return

        # Map edges based on inversion setting
        # Default falling is trigger and rising is release
        trigger_edge = rising if self.invert else falling
        release_edge = falling if self.invert else rising

        if trigger_edge:
            self.trigger()
        elif release_edge:
            self.release()

    def _interval_delta_available(self):
        """Check if dynamic range meets minimum threshold"""
        diff = self.adc_upper - self.adc_lower
        return diff >= self.interval_delta_threshold

    def _check_init_trigger(self):
        """Initial phase forced trigger check"""
        return self.adc_window[-1] < self.init_trigger_threshold

    def _check_mid_trigger(self):
        """Midpoint trigger condition checker"""
        # adc_middle is scaled, so if adc_middle bigger than adc_upper
        # which means motion is not start
        if self.adc_middle >= self.adc_upper:
            return False

        current_value = self.adc_window[-1]
        if self.invert:
            # Invert rising trigger
            return (current_value >= self.adc_middle)
        else:
            # Default falling trigger
            return (current_value <= self.adc_middle)

    def _check_mid_release(self):
        """Midpoint release condition checker"""
        # adc_middle is scaled, so if adc_middle bigger than adc_upper
        # which means motion is not start
        if self.adc_middle >= self.adc_upper:
            return False

        current_value = self.adc_window[-1]
        if self.invert:
            # Invert falling release
            return (current_value <= self.adc_middle)
        else:
            # Default rising release
            return (current_value >= self.adc_middle)

    # ==== State Transition Methods ====
    def _update_state(self, new_state):
        """Update state with history tracking"""
        self.state_prev = self.state
        self.state = new_state

    def trigger(self):
        """Handle trigger state transition"""
        self._update_state(self.state_trigger)
        # self.last_trigger_at = time.time()
        if self.is_new_triggered() and self.trigger_callback:
            self.trigger_callback(self.mcu_pin)

    def release(self):
        """Handle release state transition"""
        self._update_state(self.state_release)
        # self.last_release_at = time.time()
        if self.is_new_release() and self.release_callback:
            self.release_callback(self.mcu_pin)

    # ==== State Query Methods ====
    def is_triggered(self):
        """Check if in triggered state"""
        return self.state == self.state_trigger

    def is_released(self):
        """Check if in released state"""
        return self.state == self.state_release

    def has_changed(self):
        """Detect state change from previous value"""
        return self.state_prev != self.state

    def is_new_triggered(self):
        """Check for new trigger event"""
        return self.is_triggered() and self.has_changed()

    def is_new_release(self):
        """Check for new release event"""
        return self.is_released() and self.has_changed()

    # ==== Monitoring Control ====
    def activate_monitor(self, callback):
        """Enable monitoring functionality"""
        self.monitor_active = True
        self.monitor_callback = callback

    def deactivate_monitor(self):
        """Disable monitoring functionality"""
        self.monitor_active = False
        self.monitor_callback = None

    def _monitor_rising(self, adc_value):
        return adc_value < self.adc_middle

    def _monitor_falling(self, adc_value):
        return adc_value > self.adc_middle

    def _handle_monitoring(self, adc_value):
        """
        Process monitoring threshold checks.
        Triggers a callback if necessary.
        """
        if not self.monitor_active or not self.monitor_callback:
            return
        # Determine threshold crossing based on polarity
        threshold_met = (
            self._monitor_rising(adc_value) if self.invert
            else self._monitor_falling(adc_value))
        if threshold_met and self._interval_delta_available():
            self.monitor_callback()

    # ==== Status Reporting ====
    def get_mcu_pin(self):
        return self.mcu_pin

    def get_state(self):
        return self.state

    # def format_status(self):
    #     """Generate formatted status report"""
    #     adc = self.adc_window[-1] if self.adc_window else None
    #     rise_thresh, fall_thresh = self.detector.get_edge_threshold()
    #     return (
    #         f"{self.mms_name} pin={self.mcu_pin} "
    #         f"v={adc}/({self.adc_lower},{self.adc_middle},{self.adc_upper}) "
    #         f"↑{rise_thresh}/↓{fall_thresh}"
    #     )


class MMSAdcGate(MMSAdc):
    def __init__(self, config, mcu_pin):
        super().__init__(config, mcu_pin)
        self.mms_name = "gate"


class MMSAdcOutlet(MMSAdc):
    def __init__(self, config, mcu_pin):
        super().__init__(config, mcu_pin)
        self.mms_name = "outlet"

        de_config = DetectorConfig()
        self.detector.set_min_trend(de_config.outlet_min_trend)
        self.detector.set_threshold_scale(
            de_config.outlet_slow_scale,
            de_config.outlet_fast_scale)

        # self.analyzer = MotionAnalyzer()

    # def adc_callback(self, read_time, read_value):
    #     # self.analyzer.update_sample(time.time(), adc_value)
    #     # mid_adc_value = self.analyzer.get_midpoint_adc()


class MMSAdcOutletCompact:
    def __init__(self, config, mcu_pins):
        self.mms_name = "outlet_compact"

        # Key:mcu_pin, Value:MMSAdcOutlet object
        self.outlets = {}
        for mcu_pin in mcu_pins:
            self.outlets[mcu_pin] = MMSAdcOutlet(config, mcu_pin)
            self.outlets[mcu_pin].set_trigger_callback(
                self.handle_outlet_is_triggered)
            self.outlets[mcu_pin].set_release_callback(
                self.handle_outlet_is_released)

        # State variables
        self.state_trigger = 1
        self.state_release = 0
        # Callback hooks
        self.trigger_callback = None
        self.release_callback = None

    # ==== Setup functions ====
    def set_trigger_callback(self, callback):
        self.trigger_callback = callback

    def set_release_callback(self, callback):
        self.release_callback = callback

    # ==== Callback from MMSAdc ====
    def handle_outlet_is_triggered(self, mcu_pin):
        if mcu_pin in self.outlets and self.trigger_callback:
            self.trigger_callback(mcu_pin)

    def handle_outlet_is_released(self, mcu_pin):
        if mcu_pin in self.outlets and self.release_callback:
            self.release_callback(mcu_pin)

    # ==== Status methods ====
    def is_triggered(self):
        return True if any(
            [outlet.is_triggered() for outlet in self.outlets.values()]
        ) else False

    def is_released(self):
        return True if any(
            [outlet.is_released() for outlet in self.outlets.values()]
        ) else False

    def get_mcu_pin(self):
        return ",".join(self.outlets.keys())

    def get_state(self):
        return self.state_trigger if self.is_triggered() else self.state_release

    # def format_status(self):
    #     info = ""
    #     for mcu_pin in sorted(self.outlets.keys()):
    #         info += f"slot[*] {self.outlets.get(mcu_pin).format_status()}\n"
    #     return info

    # def get_monitor_obj(self, mcu_pin):
    #     return self.outlets.get(mcu_pin)


class MMSAdcOutletThreshold(MMSAdc):
    def __init__(self, config, mcu_pin):
        super().__init__(config, mcu_pin)
        self.mms_name = "outlet"

        self.de_config = DetectorConfig()
        self.adc_window = deque(maxlen=3)

        self.detector = ThresholdDetector()
        self.detector.set_invert(self.invert)
        self.detector.set_adc_threshold(2000)

    # ==== Core ADC Processing ====
    def adc_callback(self, read_time, read_value):
        """
        Main ADC data callback handler
        Func register and callback by class MCU_adc
        """
        if self.adc_max is None:
            self.set_max_adc()
        # Convert normalized value to actual ADC reading
        adc_value = int(read_value * self.adc_max)
        self.adc_window.append(adc_value)

        # ADC interval upper/lower tracking, update dynamic range
        self._update_adc_interval(adc_value)
        # ADC trigger/release detect
        self.detect()
        # ADC Monitor
        self._handle_monitoring(adc_value)

    def _update_adc_interval(self, adc_value):
        """Update dynamic range values and recalculate midpoint"""
        self.adc_upper = max(self.adc_upper, adc_value)
        self.adc_lower = min(self.adc_lower, adc_value)
        # Calculate weighted midpoint
        self.adc_middle = self.detector.get_adc_threshold()

    def detect(self):
        """Main state detection handler with priority logic"""
        state = self.detector.detect(self.adc_window)

        if state == self.de_config.trigger:
            self.trigger()
        elif state == self.de_config.release:
            self.release()

    def _handle_monitoring(self, adc_value):
        """
        Process monitoring trigger state checks.
        Triggers a callback if necessary.
        """
        if not self.monitor_active or not self.monitor_callback:
            return
        if self.is_released():
            self.monitor_callback()


# =================================================
# Detectors
# =================================================
class EdgeDetector:
    """
    Dynamic edge detection using statistical analysis of ADC trends
    """
    def __init__(self):
        self.config = DetectorConfig()

        self.trend_window = deque(maxlen=self.config.trend_window_size)

        # Detector configs
        self.rise_sensitivity = self.config.rise_sensitivity
        self.fall_sensitivity = self.config.fall_sensitivity
        self.slow_scale = self.config.slow_scale
        self.fast_scale = self.config.fast_scale
        self.min_trend = self.config.min_trend

        # Tracking
        self.last_rise_at = 0
        self.last_fall_at = 0
        self.last_rise_thresh = 0
        self.last_fall_thresh = 0

    # -- Set params methods --
    def set_min_trend(self, min_trend):
        self.min_trend = min_trend

    def set_threshold_scale(self, slow_scale, fast_scale):
        self.slow_scale = slow_scale
        self.fast_scale = fast_scale

    # -- Get status methods --
    def get_edge_threshold(self):
        return self.last_rise_thresh, self.last_fall_thresh

    # -- Main detection interface --
    def _calculate_trend(self, adc_window):
        """Calculate weighted moving trend using recent samples"""
        recent_values = list(adc_window)[len(adc_window)//2:]
        if len(recent_values) < 2:
            return 0

        # Linear weighting
        weights = [i+1 for i in range(len(recent_values))]
        # Skip first element for deltas
        total_weight = sum(weights[1:])
        weighted_deltas = sum(
            (recent_values[i] - recent_values[i-1]) * weights[i]
            for i in range(1, len(recent_values))
        )
        return weighted_deltas/total_weight

    def detect(self, adc_window):
        if len(adc_window) < adc_window.maxlen//2:
            return False,False

        # Calculate current trend characteristics
        current_trend = self._calculate_trend(adc_window)
        self.trend_window.append(current_trend)
        avg_trend = (
            sum(self.trend_window) / len(self.trend_window)
            if self.trend_window else 0)

        # Determine adaptive scaling factor
        scale = self.slow_scale if abs(avg_trend) < 2 else self.fast_scale

        # Calculate dynamic thresholds
        n = len(adc_window)
        mean = sum(adc_window) / n
        variance = sum((v - mean)**2 for v in adc_window) / n
        std = math.sqrt(variance) if n > 1 else 0
        current_value = adc_window[-1]

        # Rising edge detection
        rise_thresh = int(mean + std * self.rise_sensitivity * scale)
        rising = current_value > rise_thresh and avg_trend > abs(self.min_trend)

        # Falling edge detection
        fall_thresh = int(mean - std * self.fall_sensitivity * scale)
        falling = (
            current_value < fall_thresh
            and avg_trend < -abs(self.min_trend))

        # Dual threshold tracking
        if rising:
            self.last_rise_at = time.time()
            self.last_rise_thresh = rise_thresh
        elif falling:
            self.last_fall_at = time.time()
            self.last_fall_thresh = fall_thresh

        return rising,falling


class ThresholdDetector:
    def __init__(self):
        self.config = DetectorConfig()

        # Lazy setup params
        self.adc_threshold = None
        self.adc_min_offset = None
        self.adc_limit = None

        self.state_default = self.config.release
        self.state_trigger = self.config.trigger
        self.state_release = self.config.release

        self.invert = False

        # Sample:
        # self.detector = ThresholdDetector()
        # self.detector.set_invert(self.invert)

    def set_invert(self, invert):
        self.invert = invert

    def set_adc_threshold(self, adc_threshold):
        self.adc_threshold = adc_threshold

    # def set_adc_min_offset(self, adc_min_offset):
    #     self.adc_min_offset = adc_min_offset

    # def cal_adc_limit(self):
    #     if self.adc_threshold is not None and self.adc_min_offset is not None:
    #         if self.invert:
    #             self.adc_limit = self.adc_threshold - self.adc_min_offset
    #         else:
    #             self.adc_limit = self.adc_threshold + self.adc_min_offset

    def get_edge_threshold(self):
        return 0,0

    def get_adc_threshold(self):
        return self.adc_threshold

    def detect(self, adc_window):
        """
        Check if the pin is triggered based on ADC values and threshold.

        This method evaluates whether the pin is considered triggered by
        comparing the ADC values in the queue against a predefined threshold.

        If the pin's ADC value is above/below the threshold for all entries
        in the queue,the pin is considered triggered.

        Additionally, if the pin is configured to be inverted,
        the result is negated.

        Returns:
            bool: True if the pin is triggered, False otherwise.
        """
        # Check adc_threshold first
        if self.adc_threshold is None:
            return self.state_default

        if self.invert:
            is_triggered = all(
                adc_value > self.adc_threshold for adc_value in adc_window)
        else:
            is_triggered = all(
                adc_value < self.adc_threshold for adc_value in adc_window)

        return self.state_trigger if is_triggered else self.state_release

    # def monitor(self):
    #     """
    #     Monitors the ADC values and triggers a callback if necessary.
    #     """
    #     if self.adc_limit is None:
    #         self.cal_adc_limit()
    #     # Compare the last value of queue with limit
    #     if self.invert:
    #         condition_is_met = self.adc_window[-1] <= self.adc_limit
    #     else:
    #         condition_is_met = self.adc_window[-1] >= self.adc_limit
    #     if self.monitor_callback and condition_is_met:
    #         self.monitor_callback()


class MotionAnalyzer:
    def __init__(self, max_samples=1000):
        self.time_buffer = deque(maxlen=max_samples)
        self.adc_buffer = deque(maxlen=max_samples)

        self.midpoint_adc = None

        self.motion_start = 0
        self.motion_end = 0
        self.in_motion = False

        self.recent_length = 30

        # ADC value delta threshold
        self.motion_threshold = 50

    def notice_motion_start(self):
        self.motion_start = self.time_buffer[-1]
        self.in_motion = True

    def notice_motion_end(self):
        self.motion_end = self.time_buffer[-1]
        self.in_motion = False

    def _detect_motion(self):
        if len(self.adc_buffer) < self.recent_length:
            return

        recent = list(self.adc_buffer)[-self.recent_length:]
        delta = max(recent) - min(recent)

        if abs(delta) > self.motion_threshold:
            if self.in_motion:
                self.notice_motion_end()
            else:
                self.notice_motion_start()

    def update_sample(self, timestamp, adc_value):
        self.time_buffer.append(timestamp)
        self.adc_buffer.append(adc_value)
        self._detect_motion()
        self.cal_midpoint_adc()

    def cal_midpoint_adc(self):
        if self.in_motion:
            return

        mid_time = (self.motion_start + self.motion_end) / 2
        times = list(self.time_buffer)

        index = bisect.bisect_left(times, mid_time)

        if index == 0:
            self.midpoint_adc = self.adc_buffer[0]
        elif index >= len(times):
            self.midpoint_adc = self.adc_buffer[-1]
        else:
            t0, t1 = times[index-1], times[index]
            v0, v1 = self.adc_buffer[index-1], self.adc_buffer[index]

            ratio = (mid_time - t0) / (t1 - t0)
            interpolated = v0 + ratio * (v1 - v0)
            self.midpoint_adc = int(interpolated)

    def get_midpoint_adc(self):
        return self.midpoint_adc
