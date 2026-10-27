"""View helpers over a heatmap.v1 document. Filters do not refetch."""

from __future__ import annotations

import numpy as np
import pandas as pd


def aggregate(
    contract: dict,
    *,
    sides: list[str] | None = None,
    leverages: list[float] | None = None,
    exchanges: list[str] | None = None,
    smooth_sigma: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sum the stored breakdown onto a price by time matrix."""
    from olh.features.binning import smooth_price_axis

    times = contract["axes"]["time"]
    prices = np.asarray(contract["axes"]["price"], dtype=float)
    heat = np.zeros((len(prices), len(times)), dtype=float)
    breakdown = [["" for _ in times] for _ in prices]
    buckets: dict[tuple[int, int], list[str]] = {}
    time_index = {ts: i for i, ts in enumerate(times)}
    side_set = set(sides) if sides else None
    lev_set = {float(lev) for lev in leverages} if leverages else None
    ex_set = set(exchanges) if exchanges else None
    for cell in contract["cells"]:
        if side_set is not None and cell["side"] not in side_set:
            continue
        if lev_set is not None and float(cell["leverage"]) not in lev_set:
            continue
        if ex_set is not None and cell["exchange"] not in ex_set:
            continue
        col = time_index.get(cell["t"])
        row = int(cell["p"])
        if col is None or row < 0 or row >= heat.shape[0]:
            continue
        heat[row, col] += float(cell["notional"])
        buckets.setdefault((row, col), []).append(
            f"{cell['side']} {cell['exchange']} {cell['leverage']:g}x {cell['notional']:.0f}"
        )
    for (row, col), parts in buckets.items():
        breakdown[row][col] = "; ".join(parts[:8])
    heat = smooth_price_axis(heat, smooth_sigma)
    return heat, prices, np.asarray(breakdown, dtype=object)


def reference_close(contract: dict) -> np.ndarray:
    return np.array(
        [np.nan if row["close"] is None else float(row["close"]) for row in contract["price_series"]],
        dtype=float,
    )


def reference_mark(contract: dict) -> np.ndarray:
    return np.array(
        [np.nan if row["mark"] is None else float(row["mark"]) for row in contract["price_series"]],
        dtype=float,
    )


def time_axis(contract: dict) -> list[pd.Timestamp]:
    return [pd.Timestamp(ts) for ts in contract["axes"]["time"]]
