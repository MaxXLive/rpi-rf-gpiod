"""433 MHz RF Transmitter using lgpio.

Sends RF codes via a connected transmitter module.
Compatible with PT2262-based devices and rc-switch protocols.
"""

import time
import logging
import lgpio

from .protocols import PROTOCOLS

_LOGGER = logging.getLogger(__name__)


def _detect_chip() -> int:
    """Auto-detect the GPIO chip number.

    Pi 5 uses /dev/gpiochip4 (RP1), all others use /dev/gpiochip0.
    """
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read()
        if "Pi 5" in model:
            return 4
    except OSError:
        pass
    return 0


class RFTransmitter:
    """433 MHz RF Transmitter.

    Args:
        gpio: BCM GPIO pin number connected to the transmitter data pin.
        chip: GPIO chip number. None for auto-detection.
    """

    def __init__(self, gpio: int = 17, chip: int | None = None):
        self.gpio = gpio
        self.chip = chip if chip is not None else _detect_chip()
        self._handle = None

    def enable(self) -> None:
        """Open GPIO handle and claim the TX pin as output."""
        if self._handle is not None:
            return
        self._handle = lgpio.gpiochip_open(self.chip)
        lgpio.gpio_claim_output(self._handle, self.gpio, 0)
        _LOGGER.debug("TX enabled on GPIO %d (chip %d)", self.gpio, self.chip)

    def disable(self) -> None:
        """Release GPIO resources."""
        if self._handle is not None:
            try:
                lgpio.gpio_free(self._handle, self.gpio)
                lgpio.gpiochip_close(self._handle)
            except lgpio.error:
                pass
            self._handle = None
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
        if self._handle is None:
            self.enable()

        proto = PROTOCOLS[protocol]
        if pulselength is not None:
            pl = pulselength
        else:
            pl = proto.pulselength

        waveform = self._make_waveform(code, proto, pl, length)

        _LOGGER.debug(
            "TX code=%d proto=%d pl=%dµs repeat=%d len=%d",
            code, protocol, pl, repeat, length,
        )

        for _ in range(repeat):
            for level, duration_us in waveform:
                lgpio.gpio_write(self._handle, self.gpio, level)
                _sleep(duration_us / 1_000_000)
            # End LOW
            lgpio.gpio_write(self._handle, self.gpio, 0)

        return True

    @staticmethod
    def _make_waveform(
        code: int,
        proto,
        pulselength: int,
        length: int,
    ) -> list[tuple[int, int]]:
        """Build the (level, duration_µs) sequence for a code.

        Returns a list of (0/1, microseconds) tuples representing
        the complete waveform for one transmission (sync + data bits).
        """
        waveform = []

        # Data bits (MSB first)
        for i in range(length - 1, -1, -1):
            if code & (1 << i):
                waveform.append((1, pulselength * proto.one_high))
                waveform.append((0, pulselength * proto.one_low))
            else:
                waveform.append((1, pulselength * proto.zero_high))
                waveform.append((0, pulselength * proto.zero_low))

        # Sync pulse (after data, per rc-switch convention)
        waveform.append((1, pulselength * proto.sync_high))
        waveform.append((0, pulselength * proto.sync_low))

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
