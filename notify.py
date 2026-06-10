"""macOS desktop notifications for pressure-drop alerts.

Uses the built-in `osascript` so there's no third-party dependency. A falling barometer is
the migraine-relevant signal, so we alert when a delta drops by at least the configured
magnitude. Thresholds are stored as positive numbers; a drop is a negative delta, so the
test is ``delta <= -threshold``.
"""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("the-fragile.notify")


def notify(title: str, message: str) -> None:
    """Show a macOS notification. Never raises — a failed alert must not kill the loop."""
    script = f'display notification {_q(message)} with title {_q(title)}'
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # FileNotFoundError => not on macOS (e.g. running in CI); just log it.
        log.warning("desktop notification failed: %s", exc)


def _q(text: str) -> str:
    """Quote a string for AppleScript (double-quoted, backslash-escaped)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def check_pressure_drop(
    change_1h: float | None,
    change_3h: float | None,
    threshold_1h: float,
    threshold_3h: float,
) -> str | None:
    """Return an alert message if either window breached its drop threshold, else None."""
    breaches = []
    if change_1h is not None and change_1h <= -threshold_1h:
        breaches.append(f"{change_1h:+.2f} hPa/1h")
    if change_3h is not None and change_3h <= -threshold_3h:
        breaches.append(f"{change_3h:+.2f} hPa/3h")
    if not breaches:
        return None
    return "Pressure dropping: " + ", ".join(breaches)
