# The Fragile

Local barometric-pressure logger for migraine prodrome tracking. A BME280 wired to a
**Flipper Zero** is read over USB serial, logged to Postgres, and shown on a small FastAPI
dashboard. A falling barometer triggers a macOS desktop alert.

> Reduced scope: this is the pressure-logging core. Biometric correlation (Fitbit, Notion
> episode log, activation-state classifier) was deliberately deferred — the schema's `source`
> column and stored deltas leave room to add it without a migration.

## Architecture

```
 Flipper Zero (BME280)            Mac Mini host                    Docker
 ┌────────────────────┐  USB   ┌───────────────┐  :5432   ┌──────────────────┐
 │ unitemp fork:      │ serial │ ingest.py     │ ───────► │ db  (Postgres)   │
 │  bme280read CLI    │ ─────► │ poll → insert │          │  environmental_  │
 │  (JSON line)       │        │ → drop alert  │          │  reading         │
 └────────────────────┘        └───────────────┘          └────────┬─────────┘
                                                                    │ reads
                                                          ┌─────────▼─────────┐
                                                          │ web (FastAPI)     │
                                                          │  :8000 dashboard  │
                                                          └───────────────────┘
```

`ingest.py` runs on the **host** (it needs USB); Postgres and the dashboard run in Docker.

## Quick start (no hardware — mock sensor)

```sh
cp .env.example .env

# 1. Infra: Postgres (schema auto-applied) + dashboard
docker compose up -d --build

# 2. Host deps + ingestion loop (writes synthetic readings every 60s)
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python ingest.py          # READER=mock by default

# 3. Open the dashboard
open http://localhost:8000
```

Browse raw rows at <http://localhost:8000/data>; pick presets (24h/7d/30d/all) or a custom
range on the dashboard.

## Historical backfill (NOAA)

Backfill barometric pressure from **Robbins Reef (NOAA CO-OPS 8530973)** — a met station in
the Narrows beside the Verrazzano-Narrows Bridge (40.6584, -74.0647), with pressure +
temperature back to ~2012. Rows land in the same table under `source="noaa_robbins_reef"`,
joinable on `ts` with the Flipper data.

```sh
# Hourly (default) — recommended; tens of thousands of rows for the full history.
./.venv/bin/python backfill_noaa.py --begin 2012-01 --end 2026-06

# All 6-minute samples instead of hourly:
./.venv/bin/python backfill_noaa.py --begin 2024-01 --end 2024-03 --full
```

Idempotent on `(source, ts)` — safe to re-run, fills gaps, never duplicates. Chunks requests
monthly (the CO-OPS API caps air_pressure at 31 days/request). The bridge's own sensor
(8517986) is air-gap only and has no barometer, so Robbins Reef is the nearest pressure source.

## Tests

```sh
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q
```

## Configuration (`.env`)
| var | meaning |
|-----|---------|
| `DATABASE_URL` | Postgres DSN (host reaches Docker on `localhost:5432`) |
| `READER` | `mock` (default) or `flipper` |
| `FLIPPER_SERIAL_PORT` | serial device when `READER=flipper` |
| `POLL_INTERVAL` | seconds between readings (default 60) |
| `PRESSURE_DROP_1H` / `_3H` | drop magnitude (hPa) that fires an alert |
| `INGEST_LOG` | log file path |

## Run on boot
See [`deploy/com.thefragile.ingest.plist`](deploy/com.thefragile.ingest.plist) — a launchd
LaunchAgent that runs `ingest.py` at login and restarts it if it exits.

## Hardware / firmware
See [`firmware/README.md`](firmware/README.md) and
[`firmware/CLI_PROTOCOL.md`](firmware/CLI_PROTOCOL.md). The Flipper runs a unitemp fork that
adds a `bme280read` CLI command; switch `READER=flipper` once it's flashed.
```

## Layout
| path | role |
|------|------|
| `reader.py` | `MockReader`, `FlipperSerialReader`, JSON parser |
| `db.py` | connect, idempotent insert, 1h/3h delta computation |
| `notify.py` | macOS pressure-drop notification |
| `ingest.py` | the poll loop (host, launchd) |
| `backfill_noaa.py` | historical pressure backfill from NOAA Robbins Reef |
| `web/` | FastAPI read-only dashboard |
| `schema.sql` | single-table schema |
| `firmware/` | Flipper unitemp-fork docs + serial protocol |
