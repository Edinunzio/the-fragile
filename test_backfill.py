"""Tests for the NOAA backfill date arithmetic (the fiddly month-boundary logic)."""

from datetime import datetime, timezone

from backfill_noaa import _month_starts, _next_month, _parse_month


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
