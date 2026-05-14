# rpi-rf-gpiod

Modern 433 MHz RF library for Raspberry Pi using **gpiod** — works on **Pi 3B, 4, and 5**.

Drop-in replacement for [rpi-rf](https://github.com/milaq/rpi-rf) with:
- ✅ **Pi 5 support** (RP1 chip, `/dev/gpiochip4`)
- ✅ **Kernel 6.12+ compatible** (gpiod edge detection works where lgpio/RPi.GPIO don't)
- ✅ **Interrupt-based RX** (kernel edge events with nanosecond timestamps, ~0% CPU idle)
- ✅ **No GIL contention** between TX and RX
- ✅ **No daemon required** (unlike pigpio)

## Installation

```bash
pip install rpi-rf-gpiod
```

**Prerequisites**: `gpiod` Python bindings (usually pre-installed on Raspberry Pi OS Bookworm):

```bash
sudo apt install python3-libgpiod
```

## Quick Start

### Send a code

```python
from rpi_rf_gpiod import RFTransmitter

with RFTransmitter(gpio=17) as tx:
    tx.send(code=4539729, protocol=1, repeat=10)
```

### Receive codes

```python
from rpi_rf_gpiod import RFReceiver

def on_code(code, protocol, pulselength):
    print(f"Received: {code} (proto={protocol}, pl={pulselength}µs)")

with RFReceiver(gpio=16, callback=on_code) as rx:
    import time
    while True:
        time.sleep(1)
```

## CLI Tools

```bash
# Send
rf_send 4539729 --gpio 17 --protocol 1 --repeat 10

# Receive
rf_receive --gpio 16
```

## API Reference

### `RFTransmitter(gpio=17, chip=None)`

| Method | Description |
|--------|-------------|
| `enable()` | Open GPIO and claim pin as output |
| `disable()` | Release GPIO resources |
| `send(code, protocol=1, pulselength=None, repeat=10, length=24)` | Transmit an RF code |

### `RFReceiver(gpio=16, chip=None, callback=None)`

| Method | Description |
|--------|-------------|
| `enable()` | Start edge detection |
| `disable()` | Stop and release GPIO |
| `register_callback(cb)` | Add a `(code, proto, pl) -> None` callback |
| `unregister_callback(cb)` | Remove a callback |
| `set_tx_guard(duration=1.0)` | Ignore codes for N seconds (anti self-reception) |
| `clear_tx_guard(cooldown=0.5)` | Resume receiving after TX |
| `start_capture()` | Begin buffering codes (for learn mode) |
| `stop_capture()` | Stop and return buffered `[(code, proto, pl), ...]` |
| `get_capture_snapshot()` | Get buffer copy without stopping |

### Supported Protocols

| # | Pulse (µs) | Typical Use |
|---|-----------|-------------|
| 1 | 350 | PT2262, most common outlets |
| 2 | 650 | — |
| 3 | 100 | — |
| 4 | 380 | — |
| 5 | 500 | — |
| 6 | 200 | HT6P20B |

## Why not rpi-rf?

| Feature | rpi-rf | rpi-rf-gpiod |
|---------|--------|-------------|
| GPIO backend | RPi.GPIO (unmaintained) | gpiod (active, kernel-supported) |
| Pi 5 | ❌ | ✅ |
| RX method | GPIO.add_event_detect (broken 6.12+) | gpiod edge events (works on 6.12+) |
| CPU usage (RX idle) | ~15% (polling workaround) | ~0% |
| GIL contention | Yes (TX vs RX fight for GIL) | No (edge events are lightweight) |

## Why gpiod over lgpio?

Both lgpio and RPi.GPIO edge detection are **broken on kernel 6.12+**.
We tested all available GPIO libraries on a Pi 1B+ with kernel 6.12.47:

| Library | Edge Detection |
|---------|---------------|
| lgpio 0.2.2 | ❌ Broken |
| RPi.GPIO | ❌ Broken |
| **gpiod 2.2** | **✅ Works** |
| gpiozero 2.0 | ✅ Works (uses gpiod internally) |
| pigpio | ✅ Works (requires daemon) |

## Hardware

Connect a 433 MHz transmitter/receiver module:

```
TX Module:          RX Module:
  VCC → 5V            VCC → 5V
  GND → GND           GND → GND
  DATA → GPIO 17      DATA → GPIO 16
  (+ antenna)         (+ antenna)
```

## License

BSD 3-Clause. Based on protocol definitions from [rc-switch](https://github.com/sui77/rc-switch) and [rpi-rf](https://github.com/milaq/rpi-rf).
