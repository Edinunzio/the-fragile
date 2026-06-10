-- The Fragile — barometric pressure logger
-- Single-table schema. Apply once:  psql "$DATABASE_URL" -f schema.sql
--
-- Design notes:
--   * ts is stored normalized to UTC (TIMESTAMPTZ). Postgres stores TIMESTAMPTZ as UTC
--     internally regardless of the session zone, so this is correct as long as we hand it
--     timezone-aware datetimes (we do — see db.py).
--   * source distinguishes data origins so later correlation work can mix in other sources
--     (e.g. open_meteo) without a schema change.
--   * pressure_change_1h / _3h are computed and STORED on insert (not at query time) so the
--     dashboard and any future analysis read them directly.
--   * UNIQUE(source, ts) makes ingestion idempotent — re-running never duplicates a row.

CREATE TABLE IF NOT EXISTS environmental_reading (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ      NOT NULL,                      -- reading time, UTC
    source              TEXT             NOT NULL DEFAULT 'flipper_bme280',
    pressure_hpa        DOUBLE PRECISION NOT NULL,
    humidity_pct        DOUBLE PRECISION,
    temp_c              DOUBLE PRECISION,
    pressure_change_1h  DOUBLE PRECISION,                              -- hPa vs ~1h earlier
    pressure_change_3h  DOUBLE PRECISION,                              -- hPa vs ~3h earlier
    created_at          TIMESTAMPTZ      NOT NULL DEFAULT now(),       -- row insert time
    UNIQUE (source, ts)
);

-- Time-series access pattern: range scans and "latest N" both order by ts.
CREATE INDEX IF NOT EXISTS environmental_reading_ts_idx
    ON environmental_reading (ts DESC);
