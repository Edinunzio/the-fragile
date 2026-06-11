"""The Fragile — read-only FastAPI dashboard.

Serves a live pressure dashboard (current reading, 1h/3h deltas, time-series chart) plus a
browse-all table. Strictly read-only: there are no write routes, so it can never disturb the
data the host-side ingest loop is recording.

Self-contained (its own SELECTs) so the container doesn't depend on the host modules.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import backfill_noaa  # shared module, baked into the image
import db

BASE = Path(__file__).parent
app = FastAPI(title="The Fragile")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

DATABASE_URL = os.environ["DATABASE_URL"]
THRESHOLD_1H = float(os.environ.get("PRESSURE_DROP_1H", "0.5"))
THRESHOLD_3H = float(os.environ.get("PRESSURE_DROP_3H", "1.5"))

# Named range presets -> lookback window. None means "all of it".
PRESETS: dict[str, timedelta | None] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}

# Guardrail so a raw query can't return an unbounded payload.
MAX_ROWS = 5000

# Target number of points for the chart. The server downsamples (hourly/daily/weekly
# buckets) so any range — including 5+ years — stays around this many points.
MAX_POINTS = 2000


def _conn() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


def _resolve_range(
    range_: str | None, frm: str | None, to: str | None
) -> tuple[datetime | None, datetime | None]:
    """Resolve an explicit from/to (ISO) or a named preset into (start, end) UTC bounds."""
    if frm or to:
        start = datetime.fromisoformat(frm) if frm else None
        end = datetime.fromisoformat(to) if to else None
        return start, end
    window = PRESETS.get(range_ or "24h", PRESETS["24h"])
    if window is None:
        return None, None
    return datetime.now(timezone.utc) - window, None


def _query_readings(
    start: datetime | None,
    end: datetime | None,
    limit: int,
    offset: int = 0,
    newest_first: bool = False,
) -> list[dict]:
    clauses, params = [], []
    if start is not None:
        clauses.append("ts >= %s")
        params.append(start)
    if end is not None:
        clauses.append("ts <= %s")
        params.append(end)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = "DESC" if newest_first else "ASC"
    params.extend([limit, offset])

    sql = f"""
        SELECT ts, pressure_hpa, humidity_pct, temp_c,
               pressure_change_1h, pressure_change_3h
        FROM environmental_reading
        {where}
        ORDER BY ts {order}
        LIMIT %s OFFSET %s
    """
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "ts": r[0].isoformat(),
            "pressure_hpa": r[1],
            "humidity_pct": r[2],
            "temp_c": r[3],
            "pressure_change_1h": r[4],
            "pressure_change_3h": r[5],
        }
        for r in rows
    ]


def _effective_bounds(
    start: datetime | None, end: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Fill open-ended bounds from the table's actual min/max ts (for range=all)."""
    if start is not None and end is not None:
        return start, end
    with _conn() as conn:
        dmin, dmax = conn.execute(
            "SELECT min(ts), max(ts) FROM environmental_reading"
        ).fetchone()
    return (start or dmin), (end or dmax)


def _choose_bucket(start: datetime, end: datetime) -> str | None:
    """Pick the finest date_trunc unit that keeps the span under MAX_POINTS buckets.

    Returns None for short ranges, meaning 'no aggregation' — preserves full (per-minute
    Flipper) resolution. Otherwise 'hour' / 'day' / 'week'.
    """
    span = (end - start).total_seconds()
    if span <= 2 * 86400:  # <= 2 days: show raw
        return None
    for unit, secs in (("hour", 3600), ("day", 86400), ("week", 604800)):
        if span / secs <= MAX_POINTS:
            return unit
    return "week"


def _query_series(
    start: datetime | None, end: datetime | None, max_points: int = MAX_POINTS
) -> list[dict]:
    """Chart series for a range, downsampled to ~max_points via Postgres aggregation.

    Aggregated points carry avg pressure plus the min/max for that bucket (so the chart
    can show an intraday range band). Raw points set min=max=pressure.
    """
    start, end = _effective_bounds(start, end)
    if start is None:  # empty table
        return []
    bucket = _choose_bucket(start, end)

    clauses, params = [], []
    if start is not None:
        clauses.append("ts >= %s")
        params.append(start)
    if end is not None:
        clauses.append("ts <= %s")
        params.append(end)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    if bucket is None:
        sql = f"""
            SELECT ts, pressure_hpa, pressure_hpa, pressure_hpa, humidity_pct, temp_c,
                   pressure_change_1h, pressure_change_3h
            FROM environmental_reading
            {where}
            ORDER BY ts ASC
            LIMIT %s
        """
        params.append(max_points * 5)
    else:
        # For downsampled buckets the deltas are averaged — a reasonable "typical rate of
        # change during this bucket" so the hover readout stays populated.
        sql = f"""
            SELECT date_trunc(%s, ts) AS bucket,
                   round(avg(pressure_hpa)::numeric, 2),
                   round(min(pressure_hpa)::numeric, 2),
                   round(max(pressure_hpa)::numeric, 2),
                   round(avg(humidity_pct)::numeric, 1),
                   round(avg(temp_c)::numeric, 2),
                   round(avg(pressure_change_1h)::numeric, 2),
                   round(avg(pressure_change_3h)::numeric, 2)
            FROM environmental_reading
            {where}
            GROUP BY bucket
            ORDER BY bucket ASC
        """
        params = [bucket] + params

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    def f(v):
        return float(v) if v is not None else None

    return [
        {
            "ts": r[0].isoformat(),
            "pressure_hpa": f(r[1]),
            "pressure_min": f(r[2]),
            "pressure_max": f(r[3]),
            "humidity_pct": f(r[4]),
            "temp_c": f(r[5]),
            "pressure_change_1h": f(r[6]),
            "pressure_change_3h": f(r[7]),
        }
        for r in rows
    ]


@app.get("/api/readings")
def api_readings(
    range: str | None = Query(default=None),
    frm: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    max_points: int = Query(default=MAX_POINTS, ge=10, le=MAX_ROWS),
):
    start, end = _resolve_range(range, frm, to)
    return JSONResponse(_query_series(start, end, max_points))


@app.get("/api/latest")
def api_latest():
    rows = _query_readings(None, None, limit=1, newest_first=True)
    return JSONResponse(rows[0] if rows else None)


@app.post("/api/sync")
def api_sync():
    """Gap-fill recent NOAA readings on demand (the dashboard's "Update" button).

    The only write route in the app. Uses an autocommit connection (db.connect) so the
    inserted rows persist correctly.
    """
    conn = db.connect()
    try:
        inserted = backfill_noaa.sync_recent(conn)
    finally:
        conn.close()
    latest = _query_readings(None, None, limit=1, newest_first=True)
    return JSONResponse({"inserted": inserted, "latest": latest[0] if latest else None})


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "threshold_1h": THRESHOLD_1H,
            "threshold_3h": THRESHOLD_3H,
            "presets": list(PRESETS.keys()),
        },
    )


@app.get("/data")
def data(
    request: Request,
    range: str | None = Query(default=None),
    frm: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=1, le=1000),
):
    start, end = _resolve_range(range, frm, to)
    rows = _query_readings(
        start, end, limit=per_page, offset=(page - 1) * per_page, newest_first=True
    )
    return templates.TemplateResponse(
        request,
        "data.html",
        {
            "rows": rows,
            "page": page,
            "per_page": per_page,
            "has_next": len(rows) == per_page,
            "range": range or "",
            "frm": frm or "",
            "to": to or "",
        },
    )
