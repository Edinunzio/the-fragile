"""Unit tests for the sensor reader layer.

These pin down the Flipper serial protocol parser (the only real logic we can verify
without hardware) plus the MockReader and reader factory.
"""

import math

import pytest

from reader import (
    FlipperSerialReader,
    MockReader,
    Reading,
    SensorError,
    get_reader,
    parse_reading_line,
)


# --- parse_reading_line: happy path --------------------------------------- #
def test_parses_full_reading():
    line = '{"ok": true, "p_hpa": 1013.25, "rh": 41.2, "t_c": 23.41}'
    r = parse_reading_line(line)
    assert r == Reading(pressure_hpa=1013.25, humidity_pct=41.2, temp_c=23.41)


def test_parses_pressure_only():
    r = parse_reading_line('{"ok": true, "p_hpa": 1000.0}')
    assert r.pressure_hpa == 1000.0
    assert r.humidity_pct is None
    assert r.temp_c is None


def test_tolerates_surrounding_whitespace_and_newline():
    r = parse_reading_line('  {"ok": true, "p_hpa": 1011.1}\r\n')
    assert r.pressure_hpa == 1011.1


def test_integer_pressure_coerced_to_float():
    r = parse_reading_line('{"ok": true, "p_hpa": 1013}')
    assert r.pressure_hpa == 1013.0
    assert isinstance(r.pressure_hpa, float)


def test_null_optional_fields_become_none():
    r = parse_reading_line('{"ok": true, "p_hpa": 1009.0, "rh": null, "t_c": null}')
    assert r.humidity_pct is None
    assert r.temp_c is None


# --- parse_reading_line: failure modes ------------------------------------ #
def test_ok_false_raises():
    with pytest.raises(SensorError, match="no_sensor"):
        parse_reading_line('{"ok": false, "err": "no_sensor"}')


def test_missing_ok_treated_as_failure():
    with pytest.raises(SensorError):
        parse_reading_line('{"p_hpa": 1013.0}')


def test_malformed_json_raises():
    with pytest.raises(SensorError, match="malformed JSON"):
        parse_reading_line('{"ok": true, "p_hpa": 1013.0')  # missing closing brace


def test_empty_line_raises():
    with pytest.raises(SensorError, match="empty"):
        parse_reading_line("   ")


def test_missing_pressure_raises():
    with pytest.raises(SensorError, match="missing pressure"):
        parse_reading_line('{"ok": true, "rh": 40.0}')


def test_non_numeric_pressure_raises():
    with pytest.raises(SensorError, match="non-numeric"):
        parse_reading_line('{"ok": true, "p_hpa": "high"}')


def test_non_finite_pressure_raises():
    with pytest.raises(SensorError, match="non-finite"):
        parse_reading_line('{"ok": true, "p_hpa": NaN}')  # json allows bare NaN


def test_non_object_json_raises():
    with pytest.raises(SensorError, match="unexpected JSON shape"):
        parse_reading_line("[1, 2, 3]")


def test_garbage_optional_field_ignored_not_fatal():
    # A bad rh shouldn't sink an otherwise-valid pressure reading.
    r = parse_reading_line('{"ok": true, "p_hpa": 1013.0, "rh": "wet"}')
    assert r.pressure_hpa == 1013.0
    assert r.humidity_pct is None


# --- MockReader ----------------------------------------------------------- #
def test_mock_reader_is_deterministic_with_seed():
    a = [MockReader(seed=42).read() for _ in range(3)]
    b = [MockReader(seed=42).read() for _ in range(3)]
    assert a == b


def test_mock_reader_produces_plausible_values():
    r = MockReader(seed=1).read()
    assert 900 < r.pressure_hpa < 1100
    assert 0 <= r.humidity_pct <= 100
    assert math.isfinite(r.temp_c)


# --- get_reader factory --------------------------------------------------- #
def test_get_reader_defaults_to_mock():
    assert isinstance(get_reader({}), MockReader)


def test_get_reader_flipper_requires_port():
    with pytest.raises(SensorError, match="FLIPPER_SERIAL_PORT"):
        get_reader({"READER": "flipper"})


def test_get_reader_flipper_builds_serial_reader():
    reader = get_reader({"READER": "flipper", "FLIPPER_SERIAL_PORT": "/dev/cu.test"})
    assert isinstance(reader, FlipperSerialReader)
    assert reader.port == "/dev/cu.test"


def test_get_reader_rejects_unknown():
    with pytest.raises(SensorError, match="unknown READER"):
        get_reader({"READER": "banana"})
