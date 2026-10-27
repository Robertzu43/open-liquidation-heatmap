"""Long/short split. Even is the default. Metrics are optional."""

from __future__ import annotations

import numpy as np

from olh.infra.events import taker_share


def long_fraction(
    mode: str = "even",
    *,
    count_long_short_ratio: float | None = None,
    taker_long_short_ratio: float | None = None,
) -> float:
    """Share of a new open-interest increase treated as longs.

    ``even`` is one half. ``metrics`` blends Binance
    ``count_long_short_ratio`` (long accounts / short accounts) with
    ``sum_taker_long_short_vol_ratio`` (taker buy / taker sell). A missing
    print falls back to one half.
    """
    key = mode.strip().lower()
    if key == "even":
        return 0.5
    if key != "metrics":
        raise ValueError("split must be even or metrics")
    parts = []
    if count_long_short_ratio is not None and np.isfinite(count_long_short_ratio) and count_long_short_ratio >= 0:
        parts.append(float(count_long_short_ratio) / (1.0 + float(count_long_short_ratio)))
    share = taker_share(ratio=taker_long_short_ratio)
    if share is not None:
        parts.append(share)
    if not parts:
        return 0.5
    return float(sum(parts) / len(parts))
