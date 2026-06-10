# Flipper firmware — unitemp fork with serial readout

> **Status: not yet implemented.** Hardware (Flipper Zero + BME280) is not purchased yet.
> This directory documents the plan; the fork sources land here once the device arrives.

## Goal
Take [unitemp](https://github.com/quen0n/unitemp-flipperzero) (GPL-3.0) — which reads a
BME280 and shows pressure/humidity/temp on the Flipper screen — and add one thing: a CLI
command `bme280read` that prints the latest reading as a JSON line, per
[`CLI_PROTOCOL.md`](./CLI_PROTOCOL.md). That lets the Mac pull readings over USB serial.

## Hardware wiring (BME280 → Flipper GPIO, I²C)
| BME280 | Flipper pin |
|--------|-------------|
| VIN    | 3V3 (pin 9) |
| GND    | GND (pin 8/11/18) |
| SCL    | C0 (pin 16) |
| SDA    | C1 (pin 15) |

(Confirm against the unitemp README for your exact module before powering on.)

## Build & flash (with hardware)
Uses [`ufbt`](https://github.com/flipperdevices/flipperzero-ufbt), the micro Flipper Build Tool.

```sh
pip install ufbt
ufbt update                 # fetch the SDK for your firmware channel
# (place the unitemp fork sources in this directory)
ufbt                        # build the .fap
ufbt launch                 # build, upload, and start the app on a connected Flipper
```

## Implementation notes for the fork
- Preserve GPL-3.0 license headers and upstream attribution.
- Reuse unitemp's existing BME280 I²C driver — do not rewrite it.
- Keep the GUI loop; cache the most recent reading in a struct the CLI callback can read.
- Register the CLI command with the firmware CLI subsystem
  (see https://docs.flipper.net/zero/development/cli); have the callback emit the JSON line.

## Verify the contract (with hardware)
With the app running and the Flipper connected:

```sh
# Talk to the CLI directly:
screen /dev/cu.usbmodemflip_* 230400
# type: bme280read   -> expect one JSON line

# Or via the project reader:
READER=flipper FLIPPER_SERIAL_PORT=/dev/cu.usbmodemflip_Xxxx1 python ../ingest.py
```
