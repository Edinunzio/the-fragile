#!/usr/bin/env python3
"""The Fragile — ingestion loop.

Runs on the host (it needs USB access to the Flipper, which Docker-on-Mac can't pass
through). Every POLL_INTERVAL seconds: read the sensor, insert the reading (which computes
and stores the 1h/3h pressure deltas), and fire a macOS notification on a pressure drop.

Reader is chosen by the READER env var (mock | flipper). Designed to be supervised by
launchd: it logs to stdout + INGEST_LOG and shuts down cleanly on SIGTERM.

    python ingest.py
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone

from dotenv import load_dotenv

import db
import notify
from reader import SensorError, get_reader

log = logging.getLogger("the-fragile.ingest")

# Set by the signal handler; the loop checks it to exit between polls.
_stop = threading.Event()


def _handle_signal(signum, _frame):
    log.info("received signal %s, shutting down", signum)
    _stop.set()


def _setup_logging(logfile: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if logfile:
        handlers.append(logging.FileHandler(logfile))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> int:
    load_dotenv()
    _setup_logging(os.environ.get("INGEST_LOG"))

    interval = float(os.environ.get("POLL_INTERVAL", "60"))
    threshold_1h = float(os.environ.get("PRESSURE_DROP_1H", "0.5"))
    threshold_3h = float(os.environ.get("PRESSURE_DROP_3H", "1.5"))

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    reader = get_reader(os.environ)
    log.info("starting ingest: reader=%s interval=%ss", type(reader).__name__, interval)

    conn = db.connect()
    try:
        while not _stop.is_set():
            ts = datetime.now(timezone.utc)
            try:
                reading = reader.read()
            except SensorError as exc:
                log.warning("sensor read failed, skipping: %s", exc)
                _stop.wait(interval)
                continue

            try:
                change_1h, change_3h = db.insert_reading(conn, reading, ts)
            except Exception:  # keep the loop alive across transient DB errors
                log.exception("insert failed, will retry next cycle")
                conn.rollback()
                _stop.wait(interval)
                continue

            log.info(
                "p=%.2f hPa rh=%s t=%s d1h=%s d3h=%s",
                reading.pressure_hpa,
                reading.humidity_pct,
                reading.temp_c,
                change_1h,
                change_3h,
            )

            message = notify.check_pressure_drop(
                change_1h, change_3h, threshold_1h, threshold_3h
            )
            if message:
                log.info("ALERT: %s", message)
                notify.notify("The Fragile", message)

            _stop.wait(interval)
    finally:
        conn.close()
        reader.close()
        log.info("ingest stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
