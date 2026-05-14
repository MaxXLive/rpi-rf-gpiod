"""Protocol definitions for 433 MHz RF communication.

Based on rc-switch / rpi-rf protocol specifications.
Each protocol defines timing ratios relative to a base pulse length.
"""

from collections import namedtuple

Protocol = namedtuple(
    "Protocol",
    [
        "pulselength",  # Base pulse length in microseconds
        "sync_high",  # Sync pulse HIGH duration (multiple of pulselength)
        "sync_low",  # Sync pulse LOW duration (multiple of pulselength)
        "zero_high",  # Data bit 0 HIGH duration (multiple of pulselength)
        "zero_low",  # Data bit 0 LOW duration (multiple of pulselength)
        "one_high",  # Data bit 1 HIGH duration (multiple of pulselength)
        "one_low",  # Data bit 1 LOW duration (multiple of pulselength)
    ],
)

PROTOCOLS = (
    None,  # index 0 unused — protocols are 1-indexed
    Protocol(350, 1, 31, 1, 3, 3, 1),   # Protocol 1 — PT2262 / most common
    Protocol(650, 1, 10, 1, 3, 3, 1),   # Protocol 2
    Protocol(100, 30, 71, 4, 11, 9, 6),  # Protocol 3
    Protocol(380, 1, 6, 1, 3, 3, 1),    # Protocol 4
    Protocol(500, 6, 14, 1, 2, 2, 1),   # Protocol 5
    Protocol(200, 1, 28, 1, 3, 3, 1),   # Protocol 6 — HT6P20B
)

MAX_CHANGES = 67
"""Maximum number of signal changes to record per packet.
24 bits × 2 changes per bit + sync + margin = 67."""

RECEIVE_TOLERANCE = 60
"""Timing tolerance in percent for RX protocol matching."""
