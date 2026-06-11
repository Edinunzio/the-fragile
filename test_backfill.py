"""Tests for the NOAA backfill date arithmetic (the fiddly month-boundary logic)."""

from datetime import datetime, timezone

from backfill_noaa import (
    _haversine_km,
    _month_starts,
    _next_month,
    _parse_month,
    source_for,
)


def _utc(y, m, d=1):
    return datetime(y, m, d, tzinfo=timezone.utc)


def test_next_month_within_year():
    assert _next_month(_utc(2024, 1)) == _utc(2024, 2)


def test_next_month_crosses_year():
    assert _next_month(_utc(2024, 12)) == _utc(2025, 1)


def test_next_month_ignores_day_of_month():
    # Robust regardless of which day we hand it.
    assert _next_month(_utc(2024, 1, 31)) == _utc(2024, 2)


def test_month_starts_inclusive_range():
    months = list(_month_starts(_utc(2024, 11), _utc(2025, 1)))
    assert months == [_utc(2024, 11), _utc(2024, 12), _utc(2025, 1)]


def test_month_starts_single_month():
    assert list(_month_starts(_utc(2024, 6), _utc(2024, 6))) == [_utc(2024, 6)]


def test_month_starts_normalizes_begin_day():
    # A mid-month begin still starts at the first of that month.
    months = list(_month_starts(_utc(2024, 11, 17), _utc(2024, 12, 2)))
    assert months == [_utc(2024, 11), _utc(2024, 12)]


def test_parse_month():
    assert _parse_month("2012-03") == _utc(2012, 3)


# --- station resolver helpers ---------------------------------------------- #
def test_source_for_slugifies_name():
    assert source_for("Robbins Reef") == "noaa_robbins_reef"
    assert source_for("San Francisco Pier 1") == "noaa_san_francisco_pier_1"
    assert source_for("The Battery, NY") == "noaa_the_battery_ny"


def test_haversine_zero_distance():
    assert _haversine_km(40.65, -74.06, 40.65, -74.06) == 0.0


def test_haversine_known_distance():
    # NY harbor to Seattle is ~3870 km; allow a generous tolerance.
    d = _haversine_km(40.66, -74.06, 47.60, -122.33)
    assert 3700 < d < 4000
