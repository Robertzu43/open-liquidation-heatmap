"""Model oi_leverage_v1.

New positions come from the change in contract count, valued at the entry
candle's mark price when that print exists and otherwise at its close. The
USD open-interest series is not differenced. An increase is split across the
leverage prior and carried forward as a band. A decrease scales the open
book down by the same fraction. Optional age decay and a sweep fade can
shrink a band after it is born. The entry candle does not sweep a band that
was just opened on it.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from olh.core.text import DISCLAIMER, ESTIMATE_LABEL, MODEL_NAME, MODEL_VERSION, SCHEMA
from olh.features.binning import bin_centers, bin_index, make_price_grid
from olh.features.splits import long_fraction
from olh.models.brackets import SymbolBrackets, bracket_for_notional, mmr_for_leverage
from olh.models.leverage import build_leverage_weights, filter_weights, resolve_ladder
from olh.models.mathutil import liq_price_long, liq_price_short


class OiLeverageV1:
    name = MODEL_NAME
    version = MODEL_VERSION

    def build(self, books: list[tuple[str, pd.DataFrame, float]], config: dict) -> dict:
        schedule: SymbolBrackets = config["schedule"]
        profile = config.get("profile", "lognormal")
        ladder_name = "classic" if profile == "classic" else config.get("ladder", "full")
        cells: list[dict] = []
        warnings: list[str] = []
        placed = 0.0
        outside = 0.0
        exchanges = []
        for exchange, frame, max_leverage in books:
            cap = min(float(max_leverage), schedule.max_leverage)
            rungs = resolve_ladder(ladder_name, cap)
            weights = filter_weights(
                build_leverage_weights(
                    rungs,
                    profile,
                    alpha=float(config.get("alpha", 1.0)),
                    median=float(config.get("median", 15.0)),
                    sigma=float(config.get("sigma", 0.55)),
                ),
                config.get("leverages"),
            )
            if not weights:
                warnings.append(f"{exchange}: leverage filter removed every rung")
                continue
            exchanges.append(exchange)
            records, book_placed = _simulate(
                exchange,
                frame,
                weights,
                schedule,
                config,
            )
            placed += book_placed
            cells.extend(records)
        if not cells and not books:
            raise RuntimeError("no books")
        edges = _edges_from_cells(cells, books, config)
        binned, dropped = _bin_cells(cells, edges)
        outside += dropped
        times = _times(books)
        close, mark, high, low = _price_series(books, times)
        return {
            "schema": SCHEMA,
            "model": self.name,
            "model_version": self.version,
            "label": ESTIMATE_LABEL,
            "disclaimer": DISCLAIMER,
            "metadata": {
                "assumptions": _assumptions(config, schedule),
                "profile": profile,
                "ladder": ladder_name,
                "split": config.get("split", "even"),
                "fade": bool(config.get("fade", True)),
                "decay": float(config.get("decay", 0.0)),
                "age_half_life_bars": config.get("age_half_life_bars"),
                "mmr_mode": config.get("mmr_mode", "bracket"),
                "bracket_version": schedule.version,
                "notional_placed": placed,
                "notional_outside_grid": outside,
                "warnings": warnings,
            },
            "axes": {
                "time": [pd.Timestamp(ts).isoformat() for ts in times],
                "price_edges": [float(v) for v in edges],
                "price": [float(v) for v in bin_centers(edges)],
                "side": ["long", "short"],
                "leverage": sorted({float(cell["leverage"]) for cell in binned}),
                "exchange": exchanges,
            },
            "price_series": [
                {
                    "time": pd.Timestamp(ts).isoformat(),
                    "close": None if not np.isfinite(close[i]) else float(close[i]),
                    "mark": None if not np.isfinite(mark[i]) else float(mark[i]),
                    "high": None if not np.isfinite(high[i]) else float(high[i]),
                    "low": None if not np.isfinite(low[i]) else float(low[i]),
                }
                for i, ts in enumerate(times)
            ],
            "cells": binned,
        }


def _simulate(exchange, frame, weights, schedule, config) -> tuple[list[dict], float]:
    lots: list[dict] = []
    records: list[dict] = []
    placed = 0.0
    fade = bool(config.get("fade", True))
    decay = float(config.get("decay", 0.0))
    half_life = config.get("age_half_life_bars")
    split = config.get("split", "even")
    mmr_mode = config.get("mmr_mode", "bracket")
    constant = config.get("mmr")
    qty = config.get("assumed_qty")
    age_factor = 0.5 ** (1.0 / float(half_life)) if half_life else None
    for i, row in enumerate(frame.itertuples(index=False)):
        if i > 0 and age_factor is not None:
            for lot in lots:
                lot["notional"] *= age_factor
        delta = _finite(getattr(row, "oi_delta", np.nan))
        previous = _finite(getattr(row, "oi_contracts", np.nan))
        # previous print is oi - delta when both exist
        if delta is not None and delta < 0:
            before = previous - delta if previous is not None else None
            if before is not None and before > 0:
                scale = max(0.0, previous / before) if previous is not None else 0.0
                for lot in lots:
                    lot["notional"] *= scale
        shown: list[tuple[dict, float]] = []
        kept: list[dict] = []
        for lot in lots:
            display = lot["notional"]
            swept = _swept(lot, row)
            if fade and swept:
                display = lot["notional"]
                lot["notional"] *= decay
            if display > 0:
                shown.append((lot, display))
            if lot["notional"] > 1e-8:
                kept.append(lot)
        lots = kept
        if delta is not None and delta > 0:
            entry = _entry(row)
            if entry is not None:
                fresh_notional = delta * entry
                long_share = long_fraction(
                    split,
                    count_long_short_ratio=_opt(row, "count_long_short_ratio"),
                    taker_long_short_ratio=_opt(row, "sum_taker_long_short_vol_ratio"),
                )
                position_notional = None if qty is None else float(qty) * entry
                tier_cap = None
                if str(mmr_mode).lower() == "bracket":
                    tier_cap = bracket_for_notional(schedule, position_notional).max_leverage
                for lev, weight in weights.items():
                    applied = float(lev)
                    if tier_cap is not None and applied > tier_cap:
                        applied = float(tier_cap)
                    mmr, cum = mmr_for_leverage(applied, schedule, mmr_mode, constant, position_notional)
                    piece = fresh_notional * weight
                    for side, share in (("long", long_share), ("short", 1.0 - long_share)):
                        if share <= 0 or piece <= 0:
                            continue
                        price = (
                            liq_price_long(entry, applied, mmr, cum, qty)
                            if side == "long"
                            else liq_price_short(entry, applied, mmr, cum, qty)
                        )
                        if price is None:
                            continue
                        lot = {
                            "side": side,
                            "leverage": applied,
                            "price": float(price),
                            "notional": piece * share,
                            "exchange": exchange,
                        }
                        lots.append(lot)
                        shown.append((lot, lot["notional"]))
                        placed += lot["notional"]
        t_key = pd.Timestamp(row.open_time).isoformat()
        for lot, notional in shown:
            records.append(
                {
                    "time": t_key,
                    "price": lot["price"],
                    "side": lot["side"],
                    "leverage": lot["leverage"],
                    "exchange": exchange,
                    "notional": float(notional),
                }
            )
    return records, placed


def _swept(lot: dict, row) -> bool:
    if lot["side"] == "long":
        return float(row.low) <= lot["price"]
    return float(row.high) >= lot["price"]


def _entry(row) -> float | None:
    mark = _finite(getattr(row, "mark", np.nan))
    if mark is not None and mark > 0:
        return mark
    close = _finite(getattr(row, "close", np.nan))
    if close is not None and close > 0:
        return close
    return None


def _opt(row, name: str) -> float | None:
    if not hasattr(row, name):
        return None
    return _finite(getattr(row, name))


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _edges_from_cells(cells: list[dict], books, config) -> np.ndarray:
    prices = [cell["price"] for cell in cells]
    for _exchange, frame, _cap in books:
        if not frame.empty:
            prices.extend(float(v) for v in frame["low"].tolist() if np.isfinite(v))
            prices.extend(float(v) for v in frame["high"].tolist() if np.isfinite(v))
    prices = [price for price in prices if price > 0]
    if not prices:
        prices = [1.0, 1.01]
    lo, hi = min(prices), max(prices)
    lo *= 0.999
    hi *= 1.001
    n_time = max((len(frame) for _e, frame, _c in books), default=1)
    return make_price_grid(
        lo,
        hi,
        config.get("bin_mode", "percent"),
        float(config.get("bin_size", 0.25)),
        n_time=n_time,
        tick_size=config.get("tick_size"),
    )


def _bin_cells(cells: list[dict], edges: np.ndarray) -> tuple[list[dict], float]:
    grouped: dict[tuple, float] = {}
    outside = 0.0
    for cell in cells:
        slot = bin_index(edges, cell["price"])
        if slot is None:
            outside += cell["notional"]
            continue
        key = (cell["time"], slot, cell["side"], float(cell["leverage"]), cell["exchange"])
        grouped[key] = grouped.get(key, 0.0) + cell["notional"]
    binned = [
        {
            "t": key[0],
            "p": key[1],
            "side": key[2],
            "leverage": key[3],
            "exchange": key[4],
            "notional": value,
        }
        for key, value in grouped.items()
        if value > 0
    ]
    return binned, outside


def _times(books) -> list[pd.Timestamp]:
    stamps = set()
    for _exchange, frame, _cap in books:
        for ts in frame["open_time"]:
            stamps.add(pd.Timestamp(ts))
    return sorted(stamps)


def _price_series(books, times):
    close = np.full(len(times), np.nan)
    mark = np.full(len(times), np.nan)
    high = np.full(len(times), np.nan)
    low = np.full(len(times), np.nan)
    index = {pd.Timestamp(ts).isoformat(): i for i, ts in enumerate(times)}
    for _exchange, frame, _cap in books:
        for row in frame.itertuples(index=False):
            slot = index.get(pd.Timestamp(row.open_time).isoformat())
            if slot is None:
                continue
            if not np.isfinite(close[slot]):
                close[slot] = float(row.close)
                high[slot] = float(row.high)
                low[slot] = float(row.low)
                mark_value = getattr(row, "mark", np.nan)
                mark[slot] = float(mark_value) if np.isfinite(mark_value) else np.nan
            else:
                high[slot] = max(high[slot], float(row.high))
                low[slot] = min(low[slot], float(row.low))
    return close, mark, high, low


def _assumptions(config: dict, schedule: SymbolBrackets) -> list[str]:
    return [
        "Only the change in contract count opens or closes estimated positions. A move in the USD value of open interest does not.",
        "New contracts are valued at the mark price of the same candle when a mark print exists, otherwise at that candle's close. The next candle is not used.",
        "The default leverage prior is log-normal over 2x through the symbol maximum, centered near 15x, and it includes 25x.",
        f"Maintenance margin follows bracket version {schedule.version} ({config.get('mmr_mode', 'bracket')} mode). The default tier is the lowest notional band. An assumed quantity selects the band that contains quantity times entry, and leverage above that band's maximum is capped there. The maintenance amount is applied only when an assumed quantity is set.",
        "Open interest that falls scales every still-open level by the same fraction. Levels then stay on the map until price trades through them or age decay is enabled.",
        "The even long/short split is the default. The metrics split is optional and uses public account and taker ratios when those columns exist.",
        DISCLAIMER,
    ]
