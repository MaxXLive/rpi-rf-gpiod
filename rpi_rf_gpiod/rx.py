"""433 MHz RF Receiver using gpiod edge detection.

Decodes RF codes from a connected receiver module using kernel-level
edge detection with nanosecond timestamps. Works on kernel 6.12+ where
lgpio/RPi.GPIO edge detection is broken.

Compatible with PT2262-based devices and rc-switch protocols.
"""

import time
import logging
import threading
from typing import Callable

import gpiod
from gpiod.line import Direction, Edge

from .protocols import PROTOCOLS, MAX_CHANGES, RECEIVE_TOLERANCE

_LOGGER = logging.getLogger(__name__)

CodeCallback = Callable[[int, int, int], None]
"""Callback signature: (code, protocol, pulselength) -> None"""


def _detect_chip() -> str:
    """Auto-detect the GPIO chip path."""
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read()
        if "Pi 5" in model:
            return "/dev/gpiochip4"
    except OSError:
        pass
    return "/dev/gpiochip0"


class RFReceiver:
    """433 MHz RF Receiver with gpiod edge-detection.

    Uses gpiod's kernel-level edge detection which provides nanosecond
    timestamps for each signal transition. A background thread waits
    for edge events and decodes RF packets.

    Args:
        gpio: BCM GPIO pin number connected to the receiver data pin.
        chip: GPIO chip path. None for auto-detection.
        callback: Optional callback function, called for each decoded code.
    """

    def __init__(
        self,
        gpio: int = 16,
        chip: str | None = None,
        callback: CodeCallback | None = None,
    ):
        self.gpio = gpio
        self.chip = chip if chip is not None else _detect_chip()

        self._request = None
        self._running = False
        self._thread: threading.Thread | None = None

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
        self._tx_active = False
        self._tx_guard_until: float = 0.0

        # Capture mode for learn
        self._capture_active = False
        self._capture_buffer: list[tuple[int, int, int]] = []
        self._capture_lock = threading.Lock()

    def enable(self) -> None:
        """Open GPIO, configure input pin, and start edge detection thread."""
        if self._running:
            return

        self._request = gpiod.request_lines(
            self.chip,
            consumer="rpi-rf-rx",
            config={
                self.gpio: gpiod.LineSettings(
                    direction=Direction.INPUT,
                    edge_detection=Edge.BOTH,
                ),
            },
        )
        self._running = True
        self._thread = threading.Thread(
            target=self._edge_loop, daemon=True, name="rf-rx-edge",
        )
        self._thread.start()
        _LOGGER.info("RX enabled on GPIO %d (%s)", self.gpio, self.chip)

    def disable(self) -> None:
        """Stop edge detection and release GPIO resources."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._request is not None:
            self._request.release()
            self._request = None
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

        Call before transmitting to prevent self-reception.
        Also pauses edge processing to avoid GIL contention with TX.
        """
        self._tx_active = True
        self._tx_guard_until = time.monotonic() + duration

    def clear_tx_guard(self, cooldown: float = 0.5) -> None:
        """Resume receiving after transmit, with cooldown.

        Args:
            cooldown: Seconds to keep ignoring codes after TX ends.
        """
        self._tx_guard_until = time.monotonic() + cooldown
        self._tx_active = False

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

    # --- Edge detection thread ---

    def _edge_loop(self) -> None:
        """Wait for edge events from gpiod and process them."""
        req = self._request

        while self._running:
            # TX guard — pause processing during transmission
            if self._tx_active:
                time.sleep(0.05)
                self._change_count = 0
                self._last_timestamp_ns = 0
                continue

            # Wait for edge events (100ms timeout to check _running flag)
            if not req.wait_edge_events(timeout=0.1):
                continue

            events = req.read_edge_events()
            for event in events:
                if self._tx_active:
                    break
                self._process_edge_event(event.timestamp_ns)

    def _process_edge_event(self, timestamp_ns: int) -> None:
        """Process a single edge event with its kernel timestamp."""
        if self._last_timestamp_ns == 0:
            self._last_timestamp_ns = timestamp_ns
            return

        duration_us = (timestamp_ns - self._last_timestamp_ns) // 1000
        self._last_timestamp_ns = timestamp_ns

        # Ignore impossibly short glitches
        if duration_us < 50:
            return

        # Sync gap detection — long pulse signals end of packet.
        # Must be below the shortest sync_low across all protocols
        # (Protocol 4: 380µs × 6 = 2280µs) but above the longest
        # data pulse (Protocol 2: 650µs × 3 = 1950µs).
        if duration_us > 2000:
            if 0 < self._change_count < MAX_CHANGES:
                self._timings[self._change_count] = duration_us
                self._try_decode()
            self._change_count = 0
            return

        # Record pulse duration
        if self._change_count < MAX_CHANGES:
            self._timings[self._change_count] = duration_us
            self._change_count += 1

    # --- Decode logic ---

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
        """Try to decode timings against a specific protocol."""
        sync_duration = self._timings[change_count]

        if proto.sync_low == 0:
            return None
        pulselength = int(sync_duration / proto.sync_low)

        if pulselength == 0:
            return None
        if change_count < 6:
            return None

        code = 0
        delay = pulselength
        delay_tolerance = delay * RECEIVE_TOLERANCE / 100

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
