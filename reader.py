"""Sensor readers for The Fragile.

Two implementations behind one tiny interface so the ingest loop never changes:

    MockReader          — synthetic drifting values, used until the hardware arrives.
    FlipperSerialReader — reads a BME280 off a Flipper Zero over USB serial.

The Flipper firmware (a unitemp fork) registers a CLI command ``bme280read`` that prints
one JSON line per the contract in firmware/CLI_PROTOCOL.md:

    {"ok": true, "p_hpa": 1013.25, "rh": 41.2, "t_c": 23.41}
    {"ok": false, "err": "no_sensor"}

The JSON-parsing logic is split into the pure function ``parse_reading_line`` so it can be
unit-tested without a serial device (see test_reader.py).
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass


@dataclass
class Reading:
    """One environmental sample. Humidity/temp are optional; pressure is required."""

    pressure_hpa: float
    humidity_pct: float | None = None
    temp_c: float | None = None


class SensorError(RuntimeError):
    """Raised when a reader cannot produce a valid reading."""


# --------------------------------------------------------------------------- #
# Pure parser — the unit-tested core of the Flipper reader.
# --------------------------------------------------------------------------- #
def parse_reading_line(line: str) -> Reading:
    """Parse one JSON line from the Flipper into a Reading.

    Raises SensorError on anything we can't turn into a usable reading: malformed JSON,
    an explicit ``"ok": false`` payload, or a missing/invalid pressure value.
    """
    line = line.strip()
    if not line:
        raise SensorError("empty line from sensor")

    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise SensorError(f"malformed JSON from sensor: {line!r}") from exc

    if not isinstance(payload, dict):
        raise SensorError(f"unexpected JSON shape from sensor: {line!r}")

    if not payload.get("ok", False):
        raise SensorError(f"sensor reported failure: {payload.get('err', 'unknown')}")

    if "p_hpa" not in payload:
        raise SensorError(f"reading missing pressure: {line!r}")

    try:
        pressure = float(payload["p_hpa"])
    except (TypeError, ValueError) as exc:
        raise SensorError(f"non-numeric pressure: {payload['p_hpa']!r}") from exc

    if not math.isfinite(pressure):
        raise SensorError(f"non-finite pressure: {pressure!r}")

    def _opt(key: str) -> float | None:
        if payload.get(key) is None:
            return None
        try:
            val = float(payload[key])
        except (TypeError, ValueError):
            return None
        return val if math.isfinite(val) else None

    return Reading(pressure_hpa=pressure, humidity_pct=_opt("rh"), temp_c=_opt("t_c"))


# --------------------------------------------------------------------------- #
# Readers
# --------------------------------------------------------------------------- #
class MockReader:
    """Synthetic readings that drift like real weather, for use without hardware.

    Pressure does a slow random walk around sea level so the dashboard and the 1h/3h
    deltas have something believable to chart. Pass ``seed`` for reproducible runs.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._pressure = 1013.25  # standard sea-level pressure, hPa
        self._humidity = 45.0
        self._temp = 22.0

    def read(self) -> Reading:
        # Small bounded random walk on each channel.
        self._pressure += self._rng.uniform(-0.3, 0.3)
        self._humidity = min(100.0, max(0.0, self._humidity + self._rng.uniform(-1.0, 1.0)))
        self._temp += self._rng.uniform(-0.2, 0.2)
        return Reading(
            pressure_hpa=round(self._pressure, 2),
            humidity_pct=round(self._humidity, 1),
            temp_c=round(self._temp, 2),
        )

    def close(self) -> None:  # symmetry with FlipperSerialReader
        pass


class FlipperSerialReader:
    """Reads a BME280 off a Flipper Zero running the unitemp fork, over USB serial.

    The Flipper CLI command ``bme280read`` returns one JSON line (see CLI_PROTOCOL.md).
    pyserial is imported lazily so the rest of the system (and the test suite) doesn't
    need the dependency installed until you actually wire up the hardware.
    """

    def __init__(self, port: str, baudrate: int = 230400, timeout: float = 3.0) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None  # opened lazily on first read()

    def _ensure_open(self):
        if self._serial is not None:
            return self._serial
        try:
            import serial  # pyserial
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise SensorError("pyserial not installed; `pip install pyserial`") from exc

        self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        # The Flipper CLI greets with a banner and a ">: " prompt. Clear whatever is
        # buffered so our first command response isn't preceded by greeting text.
        self._serial.reset_input_buffer()
        return self._serial

    def read(self) -> Reading:
        ser = self._ensure_open()
        # Flush any pending prompt, issue the command, then read until we get a JSON line.
        ser.reset_input_buffer()
        ser.write(b"bme280read\r\n")
        ser.flush()

        # The Flipper echoes the command and reprints the prompt around the JSON payload,
        # so scan a few lines for the first one that parses as a reading.
        last_error: SensorError | None = None
        for _ in range(8):
            raw = ser.readline()
            if not raw:
                break  # timeout
            text = raw.decode("utf-8", errors="replace").strip()
            if not text or "{" not in text:
                continue
            # Trim any echoed prompt prefix like ">: " before the JSON object.
            text = text[text.index("{"):]
            try:
                return parse_reading_line(text)
            except SensorError as exc:
                last_error = exc
                continue

        raise last_error or SensorError("no reading received from Flipper before timeout")

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None


def get_reader(config: dict[str, str]):
    """Construct the reader named by config['READER'] ('mock' or 'flipper')."""
    name = (config.get("READER") or "mock").strip().lower()
    if name == "mock":
        return MockReader()
    if name == "flipper":
        port = config.get("FLIPPER_SERIAL_PORT")
        if not port:
            raise SensorError("READER=flipper requires FLIPPER_SERIAL_PORT")
        return FlipperSerialReader(port)
    raise SensorError(f"unknown READER {name!r} (expected 'mock' or 'flipper')")
