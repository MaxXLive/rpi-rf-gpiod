"""CLI tools for sending and receiving 433 MHz RF codes."""

import argparse
import logging
import signal
import sys
import time

from .tx import RFTransmitter
from .rx import RFReceiver


def send_main() -> None:
    """CLI entry point: send an RF code."""
    parser = argparse.ArgumentParser(description="Send 433 MHz RF code")
    parser.add_argument("code", type=int, help="RF code to send")
    parser.add_argument("-g", "--gpio", type=int, default=17, help="GPIO pin (default: 17)")
    parser.add_argument("-p", "--protocol", type=int, default=1, help="Protocol (default: 1)")
    parser.add_argument("-l", "--pulselength", type=int, default=None, help="Pulse length in µs")
    parser.add_argument("-r", "--repeat", type=int, default=10, help="Repeat count (default: 10)")
    parser.add_argument("-b", "--bits", type=int, default=24, help="Code length in bits (default: 24)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    with RFTransmitter(gpio=args.gpio) as tx:
        tx.send(
            code=args.code,
            protocol=args.protocol,
            pulselength=args.pulselength,
            repeat=args.repeat,
            length=args.bits,
        )
        print(f"Sent code {args.code} (proto={args.protocol}, pl={args.pulselength or 'auto'})")


def receive_main() -> None:
    """CLI entry point: receive and display RF codes."""
    parser = argparse.ArgumentParser(description="Receive 433 MHz RF codes")
    parser.add_argument("-g", "--gpio", type=int, default=16, help="GPIO pin (default: 16)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    stop_event = [False]

    def on_signal(sig, frame):
        stop_event[0] = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    def on_code(code: int, protocol: int, pulselength: int) -> None:
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] Code: {code}  Protocol: {protocol}  Pulselength: {pulselength}µs")

    print(f"Listening on GPIO {args.gpio}... (Ctrl+C to stop)")

    with RFReceiver(gpio=args.gpio, callback=on_code) as rx:
        while not stop_event[0]:
            time.sleep(0.5)

    print("\nStopped.")
