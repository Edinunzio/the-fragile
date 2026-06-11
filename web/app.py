"""The Fragile — FastAPI dashboard.

Serves a live pressure dashboard (current reading, 1h/3h deltas, time-series chart), a
browse-all table, and two write actions: pull recent NOAA data on demand (/api/sync) and
set the active location (/api/location). Everything reads/writes a single active location —
which NOAA station drives the chart and sync — persisted in the active_location table.

The shared db/reader/backfill_noaa modules are baked into the image so the sync and station
resolution reuse the same code as the CLI.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import backfill_noaa  # shared module, baked into the image
import db

BASE = Path(__file__).parent

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
# Target number of points for the chart (server downsamples to stay near this).
MAX_POINTS = 2000


def _conn() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


# --------------------------------------------------------------------------- #
# Active location (which NOAA station the chart + sync use)
# --------------------------------------------------------------------------- #
def _ensure_location() -> None:
    """Create the active_location table if missing and seed the default once."""
    with db.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_location (
                id boolean PRIMARY KEY DEFAULT true,
                station_id text NOT NULL, source text NOT NULL, name text NOT NULL,
                lat double precision NOT NULL, lon double precision NOT NULL,
                distance_km double precision, updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT single_row CHECK (id)
            )
            """
        )
        if not conn.execute("SELECT 1 FROM active_location LIMIT 1").fetchone():
            d = backfill_noaa.DEFAULT_LOCATION
            conn.execute(
                "INSERT INTO active_location (station_id, source, name, lat, lon, distance_km)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (d["station_id"], d["source"], d["name"], d["lat"], d["lon"], d["distance_km"]),
            )


def get_active() -> dict:
    with _conn() as conn:
        r = conn.execute(
            "SELECT station_id, source, name, lat, lon, distance_km FROM active_location LIMIT 1"
        ).fetchone()
    if not r:
        return dict(backfill_noaa.DEFAULT_LOCATION)
    return {
        "station_id": r[0], "source": r[1], "name": r[2],
        "lat": r[3], "lon": r[4], "distance_km": r[5],
    }


def set_active(loc: dict) -> None:
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO active_location (id, station_id, source, name, lat, lon, distance_km, updated_at)
            VALUES (true, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (id) DO UPDATE SET
                station_id = EXCLUDED.station_id, source = EXCLUDED.source, name = EXCLUDED.name,
                lat = EXCLUDED.lat, lon = EXCLUDED.lon, distance_km = EXCLUDED.distance_km,
                updated_at = now()
            """,
            (loc["station_id"], loc["source"], loc["name"], loc["lat"], loc["lon"], loc["distance_km"]),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_location()
    yield


app = FastAPI(title="The Fragile", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


# --------------------------------------------------------------------------- #
# Queries (all scoped to a source — the active location)
# --------------------------------------------------------------------------- #
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
    source: str,
    limit: int,
    offset: int = 0,
    newest_first: bool = False,
) -> list[dict]:
    clauses, params = ["source = %s"], [source]
    if start is not None:
        clauses.append("ts >= %s")
        params.append(start)
    if end is not None:
        clauses.append("ts <= %s")
        params.append(end)
    where = "WHERE " + " AND ".join(clauses)
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
    start: datetime | None, end: datetime | None, source: str
) -> tuple[datetime | None, datetime | None]:
    """Fill open-ended bounds from the source's actual min/max ts (for range=all)."""
    if start is not None and end is not None:
        return start, end
    with _conn() as conn:
        dmin, dmax = conn.execute(
            "SELECT min(ts), max(ts) FROM environmental_reading WHERE source = %s", (source,)
        ).fetchone()
    return (start or dmin), (end or dmax)


def _choose_bucket(start: datetime, end: datetime) -> str | None:
    """Finest date_trunc unit keeping the span under MAX_POINTS buckets (None = raw)."""
    span = (end - start).total_seconds()
    if span <= 2 * 86400:  # <= 2 days: show raw
        return None
    for unit, secs in (("hour", 3600), ("day", 86400), ("week", 604800)):
        if span / secs <= MAX_POINTS:
            return unit
    return "week"


def _query_series(
    start: datetime | None, end: datetime | None, source: str, max_points: int = MAX_POINTS
) -> list[dict]:
    """Chart series for a range + source, downsampled to ~max_points via aggregation."""
    start, end = _effective_bounds(start, end, source)
    if start is None:  # source has no data
        return []
    bucket = _choose_bucket(start, end)

    clauses, params = ["source = %s"], [source]
    if start is not None:
        clauses.append("ts >= %s")
        params.append(start)
    if end is not None:
        clauses.append("ts <= %s")
        params.append(end)
    where = "WHERE " + " AND ".join(clauses)

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


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/api/readings")
def api_readings(
    range: str | None = Query(default=None),
    frm: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    max_points: int = Query(default=MAX_POINTS, ge=10, le=MAX_ROWS),
):
    start, end = _resolve_range(range, frm, to)
    return JSONResponse(_query_series(start, end, get_active()["source"], max_points))


@app.get("/api/latest")
def api_latest():
    rows = _query_readings(None, None, get_active()["source"], limit=1, newest_first=True)
    return JSONResponse(rows[0] if rows else None)


@app.post("/api/sync")
def api_sync():
    """Gap-fill recent NOAA readings for the active location (the "Update" button)."""
    active = get_active()
    conn = db.connect()
    try:
        inserted = backfill_noaa.sync_recent(conn, station=active["station_id"], source=active["source"])
    finally:
        conn.close()
    latest = _query_readings(None, None, active["source"], limit=1, newest_first=True)
    return JSONResponse({"inserted": inserted, "latest": latest[0] if latest else None})


@app.get("/api/location")
def api_get_location():
    return JSONResponse(get_active())


class LocationIn(BaseModel):
    lat: float
    lon: float


@app.post("/api/location")
def api_set_location(loc: LocationIn):
    """Resolve lat/lon to the nearest NOAA pressure station, make it active, sync recent."""
    resolved = backfill_noaa.resolve_station(loc.lat, loc.lon)
    if not resolved:
        return JSONResponse(
            {"error": "No NOAA pressure station found near that location."}, status_code=404
        )
    set_active(resolved)
    conn = db.connect()
    try:
        inserted = backfill_noaa.sync_recent(
            conn, station=resolved["station_id"], source=resolved["source"]
        )
    finally:
        conn.close()
    return JSONResponse({"location": get_active(), "inserted": inserted})


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "threshold_1h": THRESHOLD_1H,
            "threshold_3h": THRESHOLD_3H,
            "presets": list(PRESETS.keys()),
            "location": get_active(),
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
        start, end, get_active()["source"], limit=per_page,
        offset=(page - 1) * per_page, newest_first=True,
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
