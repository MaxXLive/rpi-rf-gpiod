"""433 MHz RF Receiver using lgpio edge-detection callbacks.

Decodes RF codes from a connected receiver module using hardware interrupts.
No polling, no busy-wait — ~0% CPU usage when idle.
Compatible with PT2262-based devices and rc-switch protocols.
"""

import time
import logging
import threading
from typing import Callable

import lgpio

from .protocols import PROTOCOLS, MAX_CHANGES, RECEIVE_TOLERANCE

_LOGGER = logging.getLogger(__name__)

CodeCallback = Callable[[int, int, int], None]
"""Callback signature: (code, protocol, pulselength) -> None"""


def _detect_chip() -> int:
    """Auto-detect the GPIO chip number."""
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read()
        if "Pi 5" in model:
            return 4
    except OSError:
        pass
    return 0


class RFReceiver:
    """433 MHz RF Receiver with interrupt-based decoding.

    Uses lgpio.callback() for edge detection — fires on every signal
    transition with nanosecond timestamps from the kernel. Decoded
    codes are delivered to registered callbacks.

    Args:
        gpio: BCM GPIO pin number connected to the receiver data pin.
        chip: GPIO chip number. None for auto-detection.
        callback: Optional callback function, called for each decoded code.
    """

    def __init__(
        self,
        gpio: int = 16,
        chip: int | None = None,
        callback: CodeCallback | None = None,
    ):
        self.gpio = gpio
        self.chip = chip if chip is not None else _detect_chip()

        self._handle = None
        self._cb = None
        self._running = False

        # Callback registry
        self._callbacks: list[CodeCallback] = []
        if callback:
            self._callbacks.append(callback)

        # Edge timing state
        self._timings = [0] * MAX_CHANGES
        self._change_count = 0
        self._last_timestamp_ns: int = 0

        # Duplicate filter
        self._last_code: int = 0
        self._last_code_time: float = 0.0

        # TX guard — ignore self-received codes after transmitting
        self._tx_guard_until: float = 0.0

        # Capture mode for learn
        self._capture_active = False
        self._capture_buffer: list[tuple[int, int, int]] = []
        self._capture_lock = threading.Lock()

    def enable(self) -> None:
        """Open GPIO handle, configure input pin, and start edge detection."""
        if self._running:
            return

        self._handle = lgpio.gpiochip_open(self.chip)
        lgpio.gpio_claim_input(self._handle, self.gpio)

        # Register edge callback — fires on BOTH rising and falling edges
        self._cb = lgpio.callback(
            self._handle,
            self.gpio,
            lgpio.BOTH_EDGES,
            self._edge_callback,
        )
        self._running = True
        _LOGGER.debug("RX enabled on GPIO %d (chip %d)", self.gpio, self.chip)

    def disable(self) -> None:
        """Stop edge detection and release GPIO resources."""
        self._running = False
        if self._cb is not None:
            self._cb.cancel()
            self._cb = None
        if self._handle is not None:
            try:
                lgpio.gpio_free(self._handle, self.gpio)
                lgpio.gpiochip_close(self._handle)
            except lgpio.error:
                pass
            self._handle = None
        _LOGGER.debug("RX disabled")

    def register_callback(self, callback: CodeCallback) -> None:
        """Register an additional callback for decoded codes."""
        self._callbacks.append(callback)

    def unregister_callback(self, callback: CodeCallback) -> None:
        """Remove a previously registered callback."""
        try:
            self._callbacks.remove(callback)
        except ValueError:
            pass

    def set_tx_guard(self, duration: float = 1.0) -> None:
        """Ignore received codes for `duration` seconds.

        Call this before transmitting to prevent self-reception
        from being treated as incoming codes.
        """
        self._tx_guard_until = time.monotonic() + duration

    # --- Capture mode for learn flow ---

    def start_capture(self) -> None:
        """Begin capturing decoded codes into an internal buffer."""
        with self._capture_lock:
            self._capture_buffer.clear()
            self._capture_active = True

    def stop_capture(self) -> list[tuple[int, int, int]]:
        """Stop capturing and return all captured (code, protocol, pulselength)."""
        with self._capture_lock:
            self._capture_active = False
            result = list(self._capture_buffer)
            self._capture_buffer.clear()
        return result

    def get_capture_snapshot(self) -> list[tuple[int, int, int]]:
        """Return a copy of the capture buffer without stopping capture."""
        with self._capture_lock:
            return list(self._capture_buffer)

    # --- Internal edge callback ---

    def _edge_callback(self, chip: int, gpio: int, level: int, timestamp_ns: int) -> None:
        """Called by lgpio on every signal edge (rising or falling).

        Records pulse durations and triggers decode on sync gap.
        Runs in lgpio's C callback thread — keep it fast.
        """
        if self._last_timestamp_ns == 0:
            self._last_timestamp_ns = timestamp_ns
            return

        duration_us = (timestamp_ns - self._last_timestamp_ns) // 1000
        self._last_timestamp_ns = timestamp_ns

        # Ignore impossibly short glitches
        if duration_us < 50:
            return

        # Sync gap detection — long LOW pulse signals end of packet
        if duration_us > 4300:
            # The sync LOW duration IS part of the timing data.
            # Store it, then try to decode.
            if self._change_count > 0:
                self._timings[self._change_count] = duration_us
                self._try_decode()
            self._change_count = 0
            return

        # Record pulse duration
        if self._change_count < MAX_CHANGES:
            self._timings[self._change_count] = duration_us
            self._change_count += 1

    def _try_decode(self) -> None:
        """Try to decode a complete packet from recorded timings."""
        change_count = self._change_count

        for proto_num in range(1, len(PROTOCOLS)):
            proto = PROTOCOLS[proto_num]
            if proto is None:
                continue

            code = self._decode_protocol(proto, proto_num, change_count)
            if code is not None:
                self._on_code_decoded(code, proto_num, proto.pulselength)
                return

    def _decode_protocol(self, proto, proto_num: int, change_count: int) -> int | None:
        """Try to decode timings against a specific protocol.

        Returns the decoded code or None if timing doesn't match.
        """
        # Need sync pulse at end + pairs of data pulses
        # The sync LOW is at timings[change_count] (stored in _edge_callback)
        sync_duration = self._timings[change_count]

        # Calculate expected sync LOW duration as multiple of pulselength
        # Derive pulselength from the sync duration
        if proto.sync_low == 0:
            return None
        pulse_from_sync = sync_duration / proto.sync_low
        pulselength = int(pulse_from_sync)

        if pulselength == 0:
            return None

        # Need at least 24 bits = 48 changes for data
        if change_count < 6:
            return None

        code = 0
        delay = pulselength
        delay_tolerance = delay * RECEIVE_TOLERANCE / 100

        # Decode pairs: (HIGH_dur, LOW_dur)
        # Timings are: [HIGH_0, LOW_0, HIGH_1, LOW_1, ..., sync_LOW]
        for i in range(0, change_count - 1, 2):
            high_dur = self._timings[i]
            low_dur = self._timings[i + 1]

            expected_one_high = delay * proto.one_high
            expected_one_low = delay * proto.one_low
            expected_zero_high = delay * proto.zero_high
            expected_zero_low = delay * proto.zero_low

            if (
                abs(high_dur - expected_one_high) < delay_tolerance
                and abs(low_dur - expected_one_low) < delay_tolerance
            ):
                code = (code << 1) | 1
            elif (
                abs(high_dur - expected_zero_high) < delay_tolerance
                and abs(low_dur - expected_zero_low) < delay_tolerance
            ):
                code = code << 1
            else:
                return None

        # Sanity check: we should have decoded a reasonable number of bits
        bit_count = change_count // 2
        if bit_count < 8:
            return None

        _LOGGER.debug(
            "Decoded: code=%d proto=%d pl=%dµs bits=%d",
            code, proto_num, pulselength, bit_count,
        )
        return code

    def _on_code_decoded(self, code: int, protocol: int, pulselength: int) -> None:
        """Handle a successfully decoded code."""
        now = time.monotonic()

        # TX guard — ignore self-reception
        if now < self._tx_guard_until:
            _LOGGER.debug("Ignoring code %d (TX guard active)", code)
            return

        # Duplicate filter — same code within 500ms
        if code == self._last_code and (now - self._last_code_time) < 0.5:
            return

        self._last_code = code
        self._last_code_time = now

        # Capture mode
        if self._capture_active:
            with self._capture_lock:
                if self._capture_active:
                    self._capture_buffer.append((code, protocol, pulselength))

        # Deliver to callbacks
        for cb in self._callbacks:
            try:
                cb(code, protocol, pulselength)
            except Exception:
                _LOGGER.exception("Error in RX callback")

    def __enter__(self):
        self.enable()
        return self

    def __exit__(self, *args):
        self.disable()
