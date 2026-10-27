"""Build one heatmap.v1 document from public adapters.

A venue that raises is recorded and skipped. The document is only
rejected when every requested venue failed.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from olh.core.errors import ExchangeError
from olh.core.symbols import parse_symbol
from olh.core.windows import interval_seconds, parse_window
from olh.exchanges import build_adapters
from olh.features.align import align_book
from olh.infra.http import HttpClient
from olh.models import get_model
from olh.models.brackets import brackets_for


def run_heatmap(
    symbol: str = "BTCUSDT",
    window: str = "24h",
    interval: str | None = None,
    exchanges: list[str] | None = None,
    profile: str = "lognormal",
    alpha: float = 1.0,
    median: float = 15.0,
    sigma: float = 0.55,
    ladder: str = "full",
    leverages: list[float] | None = None,
    mmr: float | None = None,
    mmr_mode: str = "bracket",
    fade: bool = True,
    decay: float = 0.0,
    age_half_life_bars: float | None = None,
    bin_mode: str = "percent",
    bin_size: float = 0.25,
    split: str = "even",
    assumed_qty: float | None = None,
    adapters: list | None = None,
    http: HttpClient | None = None,
    now: Any = None,
    bracket_table: dict | None = None,
) -> dict:
    """Return a heatmap.v1 document for ``symbol`` over ``window``."""
    spec = parse_window(window, interval)
    parsed = parse_symbol(symbol)
    end = _as_utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    start = end - spec.delta
    names = list(exchanges) if exchanges else ["binance", "bybit", "okx"]
    if adapters is None:
        adapters = build_adapters(names, http=http or HttpClient())
    bar = pd.Timedelta(seconds=interval_seconds(spec.interval))
    books = []
    warnings: list[str] = []
    failures: list[str] = []
    tick = None
    for adapter in adapters:
        try:
            series = adapter.fetch(parsed, start, end, spec.interval)
        except ExchangeError as exc:
            message = f"{adapter.name}: {exc}"
            failures.append(message)
            warnings.append(message)
            continue
        for item in series.warnings:
            text = str(item)
            warnings.append(text if text.startswith(adapter.name) else f"{adapter.name}: {text}")
        frame = align_book(series.candles, series.oi, bar)
        if not series.trust_oi_deltas:
            frame = frame.copy()
            frame["oi_delta"] = np.nan
            warnings.append(
                f"{adapter.name}: open-interest timestamps were not trusted, so contract deltas were dropped."
            )
        if tick is None and series.tick_size:
            tick = series.tick_size
        books.append((adapter.name, frame, float(series.max_leverage)))
    if not books:
        detail = "; ".join(failures) or "no exchange returned data"
        raise RuntimeError(f"no exchange returned data for {parsed}. {detail}")
    schedule = brackets_for(parsed.binance, bracket_table)
    contract = get_model().build(
        books,
        {
            "schedule": schedule,
            "profile": profile,
            "alpha": alpha,
            "median": median,
            "sigma": sigma,
            "ladder": ladder,
            "leverages": leverages,
            "mmr": mmr,
            "mmr_mode": mmr_mode,
            "fade": fade,
            "decay": decay,
            "age_half_life_bars": age_half_life_bars,
            "bin_mode": bin_mode,
            "bin_size": bin_size,
            "split": split,
            "assumed_qty": assumed_qty,
            "tick_size": tick,
        },
    )
    meta = contract["metadata"]
    meta["symbol"] = str(parsed)
    meta["window"] = spec.name
    meta["interval"] = spec.interval
    meta["start"] = start.isoformat()
    meta["end"] = end.isoformat()
    meta["exchanges"] = [name for name, _frame, _cap in books]
    meta["warnings"] = list(meta.get("warnings") or []) + warnings
    return contract


def _as_utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")
