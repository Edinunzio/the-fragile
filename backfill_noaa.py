#!/usr/bin/env python3
"""Backfill historical barometric pressure from a NOAA CO-OPS station.

Default station is Robbins Reef (8530973), in the Narrows beside the Verrazzano-Narrows
Bridge — the closest NOAA met station to Bay Ridge (40.6584, -74.0647) that reports air
pressure. (The bridge's own sensor, 8517986, is air-gap only — no barometer.) Data goes
back to ~2012 at 6-minute resolution.

Rows land in the same environmental_reading table under source="noaa_robbins_reef", so they
join the Flipper data on ts and feed the same dashboard. Idempotent on (source, ts): safe to
re-run, and re-running fills any gaps without duplicating.

Notes:
  * The CO-OPS API caps a single air_pressure request at 31 days, so we fetch month by month.
  * By default we downsample to one reading per hour (plenty for pressure trends, and keeps
    ~14 years to tens of thousands of rows instead of ~1M). Use --full for all 6-minute data.
  * No new dependency: uses urllib from the stdlib.

Examples:
    python backfill_noaa.py --begin 2012-01 --end 2026-06
    python backfill_noaa.py --begin 2024-01 --end 2024-03 --full
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import db
from reader import Reading

API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
DEFAULT_STATION = "8530973"
DEFAULT_SOURCE = "noaa_robbins_reef"


def _fetch(station: str, product: str, begin: datetime, end: datetime) -> dict:
    """Fetch one product for a <=31-day window. Returns {iso_ts: value}."""
    params = urllib.parse.urlencode(
        {
            "product": product,
            "station": station,
            "begin_date": begin.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "units": "metric",
            "time_zone": "gmt",
            "format": "json",
            "application": "the-fragile",
        }
    )
    url = f"{API}?{params}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        payload = json.load(resp)

    if "error" in payload:
        # "No data" for a month is normal (gaps / before station install) — treat as empty.
        return {}
    out: dict[str, float] = {}
    for row in payload.get("data", []):
        try:
            out[row["t"]] = float(row["v"])
        except (KeyError, ValueError):
            continue  # NOAA uses "" for missing samples
    return out


def _month_starts(begin: datetime, end: datetime):
    """Yield first-of-month datetimes from begin's month through end's month, inclusive."""
    cur = begin.replace(day=1)
    while cur <= end:
        yield cur
        cur = (cur.replace(day=28) + timedelta(days=7)).replace(day=1)


def _next_month(d: datetime) -> datetime:
    return (d.replace(day=28) + timedelta(days=7)).replace(day=1)


def backfill(station: str, source: str, begin: datetime, end: datetime, hourly: bool) -> None:
    conn = db.connect()
    total = 0
    try:
        for month in _month_starts(begin, end):
            nxt = _next_month(month)
            # CO-OPS end_date is inclusive; ask for one day before the next month starts.
            win_end = nxt - timedelta(days=1)
            pressures = _fetch(station, "air_pressure", month, win_end)
            if not pressures:
                print(f"{month:%Y-%m}: no data")
                continue
            temps = _fetch(station, "air_temperature", month, win_end)

            seen_hours: set[str] = set()
            month_count = 0
            for ts_str in sorted(pressures):
                # NOAA timestamps are naive UTC (we asked time_zone=gmt).
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                if hourly:
                    hour_key = ts_str[:13]  # "YYYY-MM-DD HH"
                    if hour_key in seen_hours:
                        continue
                    seen_hours.add(hour_key)
                reading = Reading(
                    pressure_hpa=pressures[ts_str],
                    humidity_pct=None,
                    temp_c=temps.get(ts_str),
                )
                db.insert_reading(conn, reading, ts, source=source)
                month_count += 1
            total += month_count
            print(f"{month:%Y-%m}: {month_count} readings")
    finally:
        conn.close()
    print(f"done: {total} readings inserted/updated under source={source!r}")


def _parse_month(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m").replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--station", default=DEFAULT_STATION, help="NOAA CO-OPS station id")
    ap.add_argument("--source", default=DEFAULT_SOURCE, help="source label stored in the DB")
    ap.add_argument("--begin", type=_parse_month, required=True, help="start month, YYYY-MM")
    ap.add_argument("--end", type=_parse_month, required=True, help="end month, YYYY-MM (inclusive)")
    ap.add_argument("--full", action="store_true", help="store all 6-min data (default: hourly)")
    args = ap.parse_args(argv)

    if args.begin > args.end:
        ap.error("--begin must be on or before --end")

    backfill(args.station, args.source, args.begin, args.end, hourly=not args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
