"""Lookback windows. Unknown units are rejected."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

_PRESETS: dict[str, tuple[timedelta, str]] = {
    "12h": (timedelta(hours=12), "5m"),
    "24h": (timedelta(hours=24), "15m"),
    "3d": (timedelta(days=3), "15m"),
    "7d": (timedelta(days=7), "1h"),
    "30d": (timedelta(days=30), "4h"),
    "90d": (timedelta(days=90), "4h"),
}

SUPPORTED_INTERVALS = ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d")

_SECONDS = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
}


@dataclass(frozen=True)
class Window:
    name: str
    delta: timedelta
    interval: str


WINDOW_PRESETS: dict[str, Window] = {
    name: Window(name, delta, interval) for name, (delta, interval) in _PRESETS.items()
}


def interval_seconds(interval: str) -> int:
    try:
        return _SECONDS[interval]
    except KeyError as exc:
        raise ValueError(
            f"unsupported interval {interval!r}; choose from {', '.join(SUPPORTED_INTERVALS)}"
        ) from exc


def parse_window(text: str, interval: str | None = None) -> Window:
    """Parse ``12h``, ``24h``, ``3d``, ``7d``, ``30d``, ``90d``, or ``36h`` / ``2d``.

    Units other than hours and days raise ``ValueError``. There is no silent
    fallback to a 3-day window.
    """
    key = text.strip().lower()
    if interval is not None:
        interval_seconds(interval)
    if key in WINDOW_PRESETS:
        preset = WINDOW_PRESETS[key]
        return Window(preset.name, preset.delta, interval or preset.interval)
    if len(key) < 2 or key[-1] not in ("h", "d") or not key[:-1].isdigit():
        known = ", ".join(WINDOW_PRESETS)
        raise ValueError(
            f"unknown lookback {text!r}; use one of {known} or a duration like 36h or 2d. "
            "Weeks, months, and bare numbers are not accepted."
        )
    count = int(key[:-1])
    if count <= 0:
        raise ValueError("lookback must be positive")
    delta = timedelta(hours=count) if key[-1] == "h" else timedelta(days=count)
    return Window(key, delta, interval or "1h")
