"""Join open interest onto the candle that contains it. No forward candle."""

from __future__ import annotations

import numpy as np
import pandas as pd

from olh.infra.events import gap_guard


def align_book(candles: pd.DataFrame, oi: pd.DataFrame, bar: pd.Timedelta) -> pd.DataFrame:
    """One row per candle.

    The open-interest print is the last one at or before the candle's end,
    and it must fall inside the candle. Entry fields (close, mark) come from
    that same candle. A zero print is dropped. The contract-count delta is
    cleared when the previous candle is missing or the step is too long.
    """
    if candles.empty:
        return candles.copy()
    frame = candles.sort_values("open_time").drop_duplicates("open_time").copy()
    frame["bar_end"] = frame["open_time"] + bar - pd.Timedelta(milliseconds=1)
    if oi.empty:
        frame["oi_contracts"] = np.nan
        frame["oi_usd"] = np.nan
    else:
        right = oi.sort_values("timestamp").drop_duplicates("timestamp")
        if "oi_contracts" in right.columns:
            right = right.loc[right["oi_contracts"].fillna(0) != 0]
        merged = pd.merge_asof(
            frame.sort_values("bar_end"),
            right,
            left_on="bar_end",
            right_on="timestamp",
            direction="backward",
            tolerance=bar + pd.Timedelta(seconds=1),
        )
        frame = merged.sort_values("open_time")
    if "oi_contracts" not in frame.columns:
        frame["oi_contracts"] = np.nan
    if "oi_usd" not in frame.columns:
        frame["oi_usd"] = np.nan
    if "mark" not in frame.columns:
        frame["mark"] = np.nan
    frame["oi_delta"] = frame["oi_contracts"].diff()
    allowed = gap_guard(frame["open_time"], bar * 1.5)
    frame.loc[~allowed, "oi_delta"] = np.nan
    # A contract delta also requires the previous contract print itself.
    previous = frame["oi_contracts"].shift(1)
    frame.loc[previous.isna() | frame["oi_contracts"].isna(), "oi_delta"] = np.nan
    return frame.reset_index(drop=True)
