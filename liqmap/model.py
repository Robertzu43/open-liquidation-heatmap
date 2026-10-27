"""Estimate liquidation shelves from open interest and assumed leverage.

The map is a model. Exchanges do not publish each account's entry or leverage.
A bright cell means "if new open interest were distributed across these
leverage tiers, this much notional would be forced out at this price."
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

MMR = 0.004

# Relative weight of each leverage tier. Weights in a model sum to 1.
MODELS: dict[int, dict] = {
    1: {
        "id": 1,
        "name": "Wide clusters",
        "blurb": "More weight on 5x–25x, so shelves sit further from price and stay until price trades through them.",
        "weights": ((5, 0.12), (10, 0.28), (25, 0.30), (50, 0.20), (100, 0.10)),
        "decay": 1.0,
        "churn": 0.0,
        "kernel": (0.15, 0.70, 0.15),
        "flow": False,
    },
    2: {
        "id": 2,
        "name": "Tight magnets",
        "blurb": "More weight on 25x–100x, a little turnover from volume, and a slow fade. Shelves hug price.",
        "weights": ((10, 0.08), (25, 0.22), (50, 0.35), (75, 0.20), (100, 0.15)),
        "decay": 0.9992,
        "churn": 0.015,
        "kernel": (0.06, 0.24, 0.40, 0.24, 0.06),
        "flow": False,
    },
    3: {
        "id": 3,
        "name": "Flow skew",
        "blurb": "Same family of tiers, but new positions follow taker buy/sell when that data exists.",
        "weights": ((10, 0.15), (20, 0.20), (25, 0.20), (50, 0.25), (100, 0.20)),
        "decay": 1.0,
        "churn": 0.0,
        "kernel": (0.10, 0.25, 0.30, 0.25, 0.10),
        "flow": True,
    },
}


def liq_price_long(entry: float, leverage: float, mmr: float = MMR) -> float:
    """Isolated long liquidation price, maintenance margin included."""
    return entry * (1.0 - 1.0 / leverage) / (1.0 - mmr)


def liq_price_short(entry: float, leverage: float, mmr: float = MMR) -> float:
    """Isolated short liquidation price, maintenance margin included."""
    return entry * (1.0 + 1.0 / leverage) / (1.0 + mmr)


@dataclass
class MarketSeries:
    times: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume_usd: np.ndarray
    oi_usd: np.ndarray
    long_share: np.ndarray
    exchange: str
    symbol: str
    coin: str
    bar_seconds: int
    note: str = ""
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.times = np.asarray(self.times, dtype=np.int64)
        self.open = np.asarray(self.open, dtype=np.float64)
        self.high = np.asarray(self.high, dtype=np.float64)
        self.low = np.asarray(self.low, dtype=np.float64)
        self.close = np.asarray(self.close, dtype=np.float64)
        self.volume_usd = np.asarray(self.volume_usd, dtype=np.float64)
        self.oi_usd = np.asarray(self.oi_usd, dtype=np.float64)
        self.long_share = np.asarray(self.long_share, dtype=np.float64)
        if not (
            len(self.times)
            == len(self.open)
            == len(self.high)
            == len(self.low)
            == len(self.close)
            == len(self.volume_usd)
            == len(self.oi_usd)
            == len(self.long_share)
        ):
            raise ValueError("market series columns must share one length")


@dataclass
class Heatmap:
    series: MarketSeries
    model_id: int
    centers: np.ndarray
    long_heat: np.ndarray
    short_heat: np.ndarray
    profile_long: np.ndarray
    profile_short: np.ndarray
    analysis: dict


def _nearest_bin(centers: np.ndarray, price: float) -> int:
    i = int(np.searchsorted(centers, price))
    if i <= 0:
        return 0
    if i >= len(centers):
        return len(centers) - 1
    if abs(centers[i - 1] - price) <= abs(centers[i] - price):
        return i - 1
    return i


def _deposit(book: np.ndarray, index: int, amount: float, kernel: tuple[float, ...]) -> None:
    if amount <= 0:
        return
    half = len(kernel) // 2
    weights = []
    indexes = []
    for offset, weight in enumerate(kernel):
        j = index + offset - half
        if 0 <= j < len(book) and weight > 0:
            indexes.append(j)
            weights.append(weight)
    if not indexes:
        return
    scale = float(sum(weights))
    for j, weight in zip(indexes, weights):
        book[j] += amount * (weight / scale)


def _clusters(centers: np.ndarray, values: np.ndarray, side: str, spot: float) -> list[dict]:
    if len(values) == 0 or float(values.max()) <= 0:
        return []
    floor = float(values.max()) * 0.18
    groups: list[dict] = []
    i = 0
    n = len(values)
    while i < n:
        if values[i] < floor:
            i += 1
            continue
        j = i + 1
        while j < n and values[j] >= floor:
            j += 1
        seg = values[i:j]
        usd = float(seg.sum())
        price = float(np.average(centers[i:j], weights=seg))
        groups.append(
            {
                "side": side,
                "price": price,
                "low": float(centers[i]),
                "high": float(centers[j - 1]),
                "usd": usd,
                "distance_pct": (price - spot) / spot * 100.0 if spot else 0.0,
            }
        )
        i = j
    groups.sort(key=lambda g: g["usd"], reverse=True)
    return groups[:8]


def analyze(centers: np.ndarray, profile_long: np.ndarray, profile_short: np.ndarray, spot: float, coin: str) -> dict:
    long_levels = _clusters(centers, profile_long, "long", spot)
    short_levels = _clusters(centers, profile_short, "short", spot)
    above = [g for g in short_levels if g["price"] > spot]
    below = [g for g in long_levels if g["price"] < spot]
    nearest_above = min(above, key=lambda g: g["price"]) if above else None
    nearest_below = max(below, key=lambda g: g["price"]) if below else None
    strongest = max(long_levels + short_levels, key=lambda g: g["usd"], default=None)

    windows = []
    for pct in (1, 2, 5, 10):
        band = spot * pct / 100.0
        long_usd = float(profile_long[(centers < spot) & (centers >= spot - band)].sum()) if spot else 0.0
        short_usd = float(profile_short[(centers > spot) & (centers <= spot + band)].sum()) if spot else 0.0
        windows.append(
            {
                "window_pct": pct,
                "long_usd": long_usd,
                "short_usd": short_usd,
                "short_to_long": (short_usd / long_usd) if long_usd > 0 else None,
            }
        )

    def fmt_usd(value: float) -> str:
        if value >= 1e9:
            return f"${value / 1e9:.2f}B"
        if value >= 1e6:
            return f"${value / 1e6:.2f}M"
        if value >= 1e3:
            return f"${value / 1e3:.1f}K"
        return f"${value:.0f}"

    def fmt_px(value: float) -> str:
        if value >= 1000:
            return f"{value:,.0f}"
        if value >= 1:
            return f"{value:,.2f}"
        return f"{value:.6f}"

    w5 = windows[2]
    if nearest_above and nearest_below and w5["long_usd"] > 0:
        ratio = w5["short_usd"] / w5["long_usd"]
        lean = (
            f"Within 5% of price, estimated short liquidity is {ratio:.2f}× long liquidity."
        )
    elif w5["short_usd"] > 0 and w5["long_usd"] == 0:
        lean = "Within 5% of price, the resting estimate is entirely short liquidity."
    elif w5["long_usd"] > 0 and w5["short_usd"] == 0:
        lean = "Within 5% of price, the resting estimate is entirely long liquidity."
    else:
        lean = "Within 5% of price, this window has little resting liquidity in the model."

    above_txt = (
        f"The nearest short shelf is {fmt_px(nearest_above['price'])} ({nearest_above['distance_pct']:+.2f}%), about {fmt_usd(nearest_above['usd'])}."
        if nearest_above
        else "No short shelf is resting above price in this window."
    )
    below_txt = (
        f"The nearest long shelf is {fmt_px(nearest_below['price'])} ({nearest_below['distance_pct']:+.2f}%), about {fmt_usd(nearest_below['usd'])}."
        if nearest_below
        else "No long shelf is resting below price in this window."
    )
    summary = (
        f"{coin} is trading at {fmt_px(spot)}. {above_txt} {below_txt} {lean} "
        "These shelves are an estimate from open-interest changes and a published leverage mix."
    )
    levels = sorted(long_levels + short_levels, key=lambda g: g["usd"], reverse=True)
    return {
        "price": spot,
        "above": nearest_above,
        "below": nearest_below,
        "strongest": strongest,
        "imbalance": windows,
        "levels": levels,
        "summary": summary,
        "resting_long_usd": float(profile_long.sum()),
        "resting_short_usd": float(profile_short.sum()),
    }


def build_heatmap(series: MarketSeries, model_id: int, n_bins: int = 360) -> Heatmap:
    spec = MODELS.get(int(model_id))
    if spec is None:
        raise ValueError(f"unknown model {model_id}")
    n = len(series.times)
    if n < 2:
        raise ValueError("need at least two candles")

    low = float(np.nanmin(series.low))
    high = float(np.nanmax(series.high))
    if not np.isfinite(low) or not np.isfinite(high) or low <= 0 or high <= low:
        raise ValueError("price series is empty")
    pad_lo = low * 0.78
    pad_hi = high * 1.22
    edges = np.linspace(pad_lo, pad_hi, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0

    long_book = np.zeros(n_bins, dtype=np.float64)
    short_book = np.zeros(n_bins, dtype=np.float64)
    long_heat = np.zeros((n_bins, n), dtype=np.float64)
    short_heat = np.zeros((n_bins, n), dtype=np.float64)
    kernel = tuple(spec["kernel"])
    weights = spec["weights"]
    use_flow = bool(spec["flow"])

    oi = np.where(np.isfinite(series.oi_usd), series.oi_usd, np.nan)
    prev = np.nan
    for i in range(n):
        share = series.long_share[i]
        if not np.isfinite(share):
            share = 0.5
        share = float(np.clip(share, 0.05, 0.95))
        if not use_flow:
            share = 0.5

        cur = oi[i]
        delta = 0.0
        if np.isfinite(cur) and np.isfinite(prev):
            delta = float(cur - prev)
            if prev > 0:
                delta = float(np.clip(delta, -prev, prev))
        if np.isfinite(cur):
            prev = float(cur)

        entry = float((series.high[i] + series.low[i] + series.close[i]) / 3.0)
        if not np.isfinite(entry) or entry <= 0:
            entry = float(series.close[i])

        add_long = 0.0
        add_short = 0.0
        if delta > 0:
            add_long += delta * share
            add_short += delta * (1.0 - share)
        elif delta < 0 and (long_book.sum() + short_book.sum()) > 0:
            prev_oi = float(oi[i] - delta) if np.isfinite(oi[i]) else 0.0
            frac = min(1.0, abs(delta) / prev_oi) if prev_oi > 0 else 1.0
            if use_flow:
                long_book *= 1.0 - frac * (1.0 - share)
                short_book *= 1.0 - frac * share
            else:
                long_book *= 1.0 - frac
                short_book *= 1.0 - frac

        churn = float(spec["churn"])
        vol = float(series.volume_usd[i]) if np.isfinite(series.volume_usd[i]) else 0.0
        if churn > 0 and vol > 0:
            seasoning = vol * churn
            if np.isfinite(cur) and cur > 0:
                seasoning = min(seasoning, cur * 0.02)
            add_long += seasoning * share
            add_short += seasoning * (1.0 - share)

        if add_long > 0 or add_short > 0:
            for lev, weight in weights:
                if add_long > 0:
                    _deposit(long_book, _nearest_bin(centers, liq_price_long(entry, lev)), add_long * weight, kernel)
                if add_short > 0:
                    _deposit(short_book, _nearest_bin(centers, liq_price_short(entry, lev)), add_short * weight, kernel)

        long_heat[:, i] = long_book
        short_heat[:, i] = short_book

        low_i = float(series.low[i])
        high_i = float(series.high[i])
        long_book[int(np.searchsorted(centers, low_i, side="left")) :] = 0.0
        short_book[: int(np.searchsorted(centers, high_i, side="right"))] = 0.0

        decay = float(spec["decay"])
        if decay != 1.0:
            long_book *= decay
            short_book *= decay

    spot = float(series.close[-1])
    profile_long = long_book.copy()
    profile_short = short_book.copy()
    return Heatmap(
        series=series,
        model_id=int(model_id),
        centers=centers,
        long_heat=long_heat,
        short_heat=short_heat,
        profile_long=profile_long,
        profile_short=profile_short,
        analysis=analyze(centers, profile_long, profile_short, spot, series.coin),
    )


def suggest_view(series: MarketSeries) -> tuple[float, float]:
    low = float(np.nanmin(series.low))
    high = float(np.nanmax(series.high))
    span = max(high - low, high * 0.01)
    return low - span * 0.35, high + span * 0.35
