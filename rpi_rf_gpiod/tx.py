"""433 MHz RF Transmitter using gpiod.

Sends RF codes via a connected transmitter module.
Compatible with PT2262-based devices and rc-switch protocols.
"""

import time
import logging

import gpiod
from gpiod.line import Direction, Value

from .protocols import PROTOCOLS

_LOGGER = logging.getLogger(__name__)


def _detect_chip() -> str:
    """Auto-detect the GPIO chip path.

    Pi 5 uses /dev/gpiochip4 (RP1), all others use /dev/gpiochip0.
    """
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read()
        if "Pi 5" in model:
            return "/dev/gpiochip4"
    except OSError:
        pass
    return "/dev/gpiochip0"


class RFTransmitter:
    """433 MHz RF Transmitter.

    Args:
        gpio: BCM GPIO pin number connected to the transmitter data pin.
        chip: GPIO chip path (e.g. "/dev/gpiochip0"). None for auto-detection.
    """

    def __init__(self, gpio: int = 17, chip: str | None = None):
        self.gpio = gpio
        self.chip = chip if chip is not None else _detect_chip()
        self._request = None

    def enable(self) -> None:
        """Open GPIO and claim the TX pin as output."""
        if self._request is not None:
            return
        self._request = gpiod.request_lines(
            self.chip,
            consumer="rpi-rf-tx",
            config={
                self.gpio: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=Value.INACTIVE,
                ),
            },
        )
        _LOGGER.debug("TX enabled on GPIO %d (%s)", self.gpio, self.chip)

    def disable(self) -> None:
        """Release GPIO resources."""
        if self._request is not None:
            self._request.release()
            self._request = None
            _LOGGER.debug("TX disabled")

    def send(
        self,
        code: int,
        protocol: int = 1,
        pulselength: int | None = None,
        repeat: int = 10,
        length: int = 24,
    ) -> bool:
        """Send an RF code.

        Args:
            code: The RF code to transmit (integer).
            protocol: Protocol number (1-6).
            pulselength: Override protocol's default pulse length (µs).
            repeat: Number of times to repeat the transmission.
            length: Code length in bits (default 24).

        Returns:
            True if transmission succeeded.
        """
        if self._request is None:
            self.enable()

        proto = PROTOCOLS[protocol]
        pl = pulselength if pulselength is not None else proto.pulselength

        waveform = self._make_waveform(code, proto, pl, length)

        _LOGGER.debug(
            "TX code=%d proto=%d pl=%dµs repeat=%d len=%d",
            code, protocol, pl, repeat, length,
        )

        req = self._request
        gpio = self.gpio
        for _ in range(repeat):
            for value, duration_us in waveform:
                req.set_value(gpio, value)
                _sleep(duration_us / 1_000_000)
            req.set_value(gpio, Value.INACTIVE)

        return True

    @staticmethod
    def _make_waveform(
        code: int,
        proto,
        pulselength: int,
        length: int,
    ) -> list[tuple[Value, int]]:
        """Build the (Value, duration_µs) sequence for a code."""
        waveform = []

        for i in range(length - 1, -1, -1):
            if code & (1 << i):
                waveform.append((Value.ACTIVE, pulselength * proto.one_high))
                waveform.append((Value.INACTIVE, pulselength * proto.one_low))
            else:
                waveform.append((Value.ACTIVE, pulselength * proto.zero_high))
                waveform.append((Value.INACTIVE, pulselength * proto.zero_low))

        # Sync pulse (after data, per rc-switch convention)
        waveform.append((Value.ACTIVE, pulselength * proto.sync_high))
        waveform.append((Value.INACTIVE, pulselength * proto.sync_low))

        return waveform

    def __enter__(self):
        self.enable()
        return self

    def __exit__(self, *args):
        self.disable()


def _sleep(seconds: float) -> None:
    """High-resolution busy-wait sleep.

    Uses time.perf_counter() for microsecond-accurate timing.
    Standard time.sleep() on Linux has ~1ms minimum granularity,
    which is too imprecise for 350µs RF pulses.
    """
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        pass
