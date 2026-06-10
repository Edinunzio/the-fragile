"""Postgres access for The Fragile.

Thin layer over psycopg 3. Shared by ingest.py (writes) and web/app.py (reads).

The one piece of real logic here is computing the stored 1h/3h pressure deltas at insert
time: for a new reading at time ``ts`` we look up the nearest earlier reading to ``ts - 1h``
and ``ts - 3h`` (within a tolerance window) and store the difference. Doing this on insert
keeps the dashboard and any future analysis free of window math at query time.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg

from reader import Reading

DEFAULT_SOURCE = "flipper_bme280"

# How far from the exact ts-1h / ts-3h target a prior reading may be and still count.
# At a 1-minute poll cadence this is generous; it mainly covers gaps/restarts.
_TOLERANCE = timedelta(minutes=5)


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Open a connection. Defaults to $DATABASE_URL.

    autocommit=True so each statement persists on its own. Without it psycopg3 opens an
    implicit transaction on the first read (the delta lookups), which would turn the
    insert's ``conn.transaction()`` into a never-committed savepoint and silently drop
    rows on disconnect. The insert is still wrapped in an explicit transaction for atomicity.
    """
    dsn = dsn or os.environ["DATABASE_URL"]
    return psycopg.connect(dsn, autocommit=True)


def _pressure_at(conn: psycopg.Connection, source: str, target: datetime) -> float | None:
    """Pressure of the reading closest to ``target`` within +/- tolerance, else None.

    Deltas are computed within a single source so we never compare a Flipper reading
    against a NOAA one.
    """
    row = conn.execute(
        """
        SELECT pressure_hpa
        FROM environmental_reading
        WHERE source = %s
          AND ts BETWEEN %s AND %s
        ORDER BY abs(extract(epoch FROM ts - %s))
        LIMIT 1
        """,
        (source, target - _TOLERANCE, target + _TOLERANCE, target),
    ).fetchone()
    return row[0] if row else None


def compute_deltas(
    conn: psycopg.Connection, source: str, ts: datetime, pressure_hpa: float
) -> tuple[float | None, float | None]:
    """Return (change_1h, change_3h): current pressure minus the earlier reading's."""
    p1 = _pressure_at(conn, source, ts - timedelta(hours=1))
    p3 = _pressure_at(conn, source, ts - timedelta(hours=3))
    change_1h = round(pressure_hpa - p1, 2) if p1 is not None else None
    change_3h = round(pressure_hpa - p3, 2) if p3 is not None else None
    return change_1h, change_3h


def insert_reading(
    conn: psycopg.Connection,
    reading: Reading,
    ts: datetime | None = None,
    source: str = DEFAULT_SOURCE,
) -> tuple[float | None, float | None]:
    """Insert one reading (computing + storing deltas). Idempotent on (source, ts).

    Returns the (change_1h, change_3h) so the caller can decide whether to alert.
    """
    ts = ts or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        raise ValueError("ts must be timezone-aware (UTC)")

    change_1h, change_3h = compute_deltas(conn, source, ts, reading.pressure_hpa)

    with conn.transaction():
        conn.execute(
            """
            INSERT INTO environmental_reading
                (ts, source, pressure_hpa, humidity_pct, temp_c,
                 pressure_change_1h, pressure_change_3h)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, ts) DO NOTHING
            """,
            (
                ts,
                source,
                reading.pressure_hpa,
                reading.humidity_pct,
                reading.temp_c,
                change_1h,
                change_3h,
            ),
        )
    return change_1h, change_3h
