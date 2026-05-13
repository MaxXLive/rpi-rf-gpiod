"""rpi-rf-lgpio — Modern 433 MHz RF library for Raspberry Pi.

Drop-in replacement for rpi-rf using lgpio instead of RPi.GPIO.
Supports Pi 3B, 4, and 5. Interrupt-based RX, no polling.
"""

__version__ = "1.0.0"

from .tx import RFTransmitter
from .rx import RFReceiver
from .protocols import PROTOCOLS, Protocol

__all__ = [
    "RFTransmitter",
    "RFReceiver",
    "PROTOCOLS",
    "Protocol",
    "__version__",
]
