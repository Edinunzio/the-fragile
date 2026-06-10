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

# Guardrail so "all"/"30d" at 1-minute cadence can't return an unbounded payload.
MAX_ROWS = 5000


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


@app.get("/api/readings")
def api_readings(
    range: str | None = Query(default=None),
    frm: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    limit: int = Query(default=MAX_ROWS, ge=1, le=MAX_ROWS),
    offset: int = Query(default=0, ge=0),
    newest_first: bool = Query(default=False),
):
    start, end = _resolve_range(range, frm, to)
    data = _query_readings(start, end, limit, offset, newest_first)
    return JSONResponse(data)


@app.get("/api/latest")
def api_latest():
    rows = _query_readings(None, None, limit=1, newest_first=True)
    return JSONResponse(rows[0] if rows else None)


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
