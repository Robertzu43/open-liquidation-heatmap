"""Time-alignment helpers.

Adapted from the author's earlier research code and included here under this
repository's MIT license. Only the alignment helpers live here. Nothing in
this module detects or records a personal book.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def to_5m(frame: pd.DataFrame, time_col: str = "timestamp", how: str = "oi") -> pd.DataFrame:
    """Bucket rows onto a 5-minute grid.

    ``how="oi"`` keeps the last observation in each bucket (a stock).
    ``how="volume"`` sums numeric columns (a flow).
    """
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    stamps = pd.to_datetime(out[time_col], utc=True)
    out["_bucket"] = stamps.dt.floor("5min")
    numeric = [col for col in out.columns if col not in (time_col, "_bucket") and pd.api.types.is_numeric_dtype(out[col])]
    if how == "volume":
        grouped = out.groupby("_bucket", sort=True)[numeric].sum(min_count=1)
    elif how == "oi":
        grouped = out.groupby("_bucket", sort=True)[numeric].last()
    else:
        raise ValueError("how must be oi or volume")
    grouped = grouped.reset_index().rename(columns={"_bucket": time_col})
    return grouped


def asof_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    left_on: str,
    right_on: str,
    tolerance: pd.Timedelta,
    oi_col: str | None = "oi_contracts",
) -> pd.DataFrame:
    """Backward as-of join.

    A match older than ``tolerance`` is stale and its payload columns are
    cleared. A zero open-interest print is treated as missing so a single
    bad zero cannot close the whole book.
    """
    if left.empty:
        return left.copy()
    left_sorted = left.sort_values(left_on)
    payload = [col for col in right.columns if col != right_on]
    if right.empty:
        out = left_sorted.copy()
        for col in payload:
            out[col] = np.nan
        return out.reset_index(drop=True)
    right_sorted = right.sort_values(right_on)
    merged = pd.merge_asof(
        left_sorted,
        right_sorted,
        left_on=left_on,
        right_on=right_on,
        direction="backward",
        tolerance=tolerance,
    )
    if oi_col and oi_col in merged.columns:
        merged.loc[merged[oi_col] == 0, payload] = np.nan
    return merged.reset_index(drop=True)


def taker_share(
    buy: float | None = None,
    sell: float | None = None,
    ratio: float | None = None,
) -> float | None:
    """Long share of taker flow.

    ``ratio`` is taker-buy / taker-sell, the same shape as Binance
    ``sum_taker_long_short_vol_ratio``. Buy and sell volumes win when both
    are present.
    """
    if buy is not None and sell is not None and np.isfinite(buy) and np.isfinite(sell):
        total = float(buy) + float(sell)
        if total > 0:
            return float(buy) / total
    if ratio is not None and np.isfinite(ratio) and ratio >= 0:
        return float(ratio) / (1.0 + float(ratio))
    return None


def gap_guard(timestamps: pd.Series, max_gap: pd.Timedelta) -> np.ndarray:
    """True where this row may be differenced against the previous one.

    The first row is always False. A step larger than ``max_gap`` is False
    so a hole is not booked as a position change.
    """
    if timestamps.empty:
        return np.array([], dtype=bool)
    stamps = pd.to_datetime(timestamps, utc=True)
    delta = stamps.diff()
    allowed = delta.notna() & (delta <= max_gap) & (delta >= pd.Timedelta(0))
    return allowed.to_numpy()
