"""Estimate liquidation shelves from open interest and assumed leverage.

The map is a model. Exchanges do not publish each account's entry or leverage.
A bright cell means "if new open interest were distributed across these
leverage tiers, this much notional would be forced out at this price."
"""

from __future__ import annotations

import bisect
import itertools
import hashlib
import json
import math
from dataclasses import dataclass, field, replace

import numpy as np

MODEL_VERSION = "2.0.0"
DECAY_REFERENCE_SECONDS = 300
MMR = 0.005
VENUES = ("binance", "bybit", "bybit_uta", "okx")

# Relative weight of each leverage tier. Weights in a model sum to 1.
MODELS: dict[int, dict] = {
    1: {
        "id": 1,
        "name": "Wide clusters",
        "blurb": "Most weight on 50x–60x, with a far 5x tier. Shelves sit about 1–2% from price and stay until price trades through them.",
        "weights": ((5, 0.12), (20, 0.10), (50, 0.25), (60, 0.53)),
        "decay": 1.0,
        "churn": 0.0,
        "kernel": (0.15, 0.70, 0.15),
        "flow": False,
    },
    2: {
        "id": 2,
        "name": "Tight magnets",
        "blurb": "Two tiers, 20x and 60x, a little turnover from volume, and a slow fade. Shelves hug price.",
        "weights": ((20, 0.30), (60, 0.70)),
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
    """Isolated long liquidation price at zero fee, zero extra margin, zero cum."""
    return liq_price(entry, leverage, "long", mmr)


def liq_price_short(entry: float, leverage: float, mmr: float = MMR) -> float:
    """Isolated short liquidation price at zero fee, zero extra margin, zero cum."""
    return liq_price(entry, leverage, "short", mmr)


def liq_price(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    *,
    qty: float = 1.0,
    cum: float = 0.0,
    fee: float = 0.0,
    extra_margin: float = 0.0,
    venue: str = "binance",
) -> float:
    """Isolated liquidation price for one position.

    ``side`` is ``"long"`` or ``"short"``. ``mmr`` and ``fee`` are decimals.
    ``cum`` is the maintenance amount. ``qty`` is base size. ``extra_margin``
    is quote added to the isolated wallet.
    """
    _validate_liq(entry, leverage, side, mmr, qty, cum, fee, extra_margin, venue)
    if venue == "bybit":
        return _bybit_classic(entry, leverage, side, mmr, qty, cum, extra_margin)
    if venue == "bybit_uta":
        return _bybit_uta(entry, leverage, side, mmr, qty, cum, fee, extra_margin)
    if venue == "okx":
        return _okx_liq(entry, leverage, side, mmr, qty, fee, extra_margin)
    return _binance_liq(entry, leverage, side, mmr, qty, cum, extra_margin)


def _validate_liq(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    cum: float,
    fee: float,
    extra_margin: float,
    venue: str,
) -> None:
    if venue not in VENUES:
        raise ValueError(f"venue must be one of {VENUES}, got {venue!r}")
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    if not all(math.isfinite(value) for value in (entry, leverage, mmr, qty, cum, fee, extra_margin)):
        raise ValueError("inputs must be finite")
    if entry <= 0:
        raise ValueError("entry must be positive")
    if leverage <= 1:
        raise ValueError("leverage must be greater than 1")
    if mmr < 0 or mmr >= 1:
        raise ValueError("mmr must be in [0, 1)")
    if qty <= 0:
        raise ValueError("qty must be positive")
    if fee < 0 or fee >= 1:
        raise ValueError("fee must be in [0, 1)")
    if cum < 0:
        raise ValueError("cum must be >= 0")
    if venue == "bybit" and fee != 0:
        raise ValueError("the Bybit classic isolated formula has no close-fee term; use venue='bybit_uta'")
    if venue == "okx" and cum != 0:
        raise ValueError("OKX isolated tiers adjust mmr; they do not take cum")


def _binance_liq(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    cum: float,
    extra_margin: float,
) -> float:
    wallet = entry * qty / leverage + extra_margin
    side_sign = 1.0 if side == "long" else -1.0
    numerator = wallet + cum - side_sign * qty * entry
    denominator = qty * mmr - side_sign * qty
    if denominator == 0:
        raise ValueError("Binance denominator is zero")
    return numerator / denominator


def _okx_liq(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    fee: float,
    extra_margin: float,
) -> float:
    margin = entry * qty / leverage + extra_margin
    if side == "long":
        denominator = qty * (mmr + fee - 1.0)
        if denominator == 0:
            raise ValueError("OKX long denominator is zero")
        return (margin - qty * entry) / denominator
    denominator = qty * (mmr + fee + 1.0)
    return (margin + qty * entry) / denominator


def _bybit_classic(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    deduction: float,
    extra_margin: float,
) -> float:
    position_value = qty * entry
    initial = position_value / leverage
    maintenance = position_value * mmr - deduction
    cushion = (initial - maintenance) / qty
    extra = extra_margin / qty
    if side == "long":
        return entry - cushion - extra
    return entry + cushion + extra


def _bybit_uta(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    deduction: float,
    fee: float,
    extra_margin: float,
) -> float:
    if side == "long":
        numerator = (
            entry * qty
            - entry * qty / leverage
            - extra_margin / (1.0 - fee)
            - deduction
        )
        denominator = qty * (1.0 - mmr)
        if denominator <= 0:
            raise ValueError("UTA long denominator must be positive")
        return numerator / denominator
    numerator = (
        entry * qty
        + entry * qty / leverage
        + extra_margin / (1.0 + fee)
        + deduction
    )
    return numerator / (qty * (1.0 + mmr))


def fade_swept(
    levels: list[tuple[float, float, str]] | tuple[tuple[float, float, str], ...],
    bars: list[tuple[float, float]] | tuple[tuple[float, float], ...],
) -> list[tuple[float, float, str]]:
    """Drop levels whose price was traded in ``bars``.

    ``levels`` are ``(price, intensity, side)``. ``bars`` are later
    ``(high, low)`` prints, oldest first. A long is swept when any low is at
    or below its price. A short is swept when any high is at or above its price.
    """
    parsed_bars: list[tuple[float, float]] = []
    for high, low in bars:
        if not math.isfinite(high) or not math.isfinite(low):
            raise ValueError("bars must be finite")
        if high < low:
            raise ValueError("bar high must be >= low")
        parsed_bars.append((float(high), float(low)))
    kept: list[tuple[float, float, str]] = []
    for price, intensity, side in levels:
        if side not in ("long", "short"):
            raise ValueError(f"side must be 'long' or 'short', got {side!r}")
        if not math.isfinite(price) or not math.isfinite(intensity):
            raise ValueError("levels must be finite")
        if intensity < 0:
            raise ValueError("intensity must be >= 0")
        if side == "long":
            swept = any(low <= price for _high, low in parsed_bars)
        else:
            swept = any(high >= price for high, _low in parsed_bars)
        if not swept:
            kept.append((float(price), float(intensity), side))
    return kept


def bin_intensity(
    prices: list[float] | tuple[float, ...],
    intensities: list[float] | tuple[float, ...],
    edges: list[float] | tuple[float, ...],
) -> list[float]:
    """Sum intensity into ``[edges[i], edges[i + 1])``. The last bin is closed."""
    if len(prices) != len(intensities):
        raise ValueError("prices and intensities must have the same length")
    if len(edges) < 2:
        raise ValueError("edges need at least two boundaries")
    bounds = [float(edge) for edge in edges]
    if any(not math.isfinite(edge) for edge in bounds):
        raise ValueError("edges must be finite")
    if any(right <= left for left, right in itertools.pairwise(bounds)):
        raise ValueError("edges must be strictly increasing")
    totals = [0.0] * (len(bounds) - 1)
    for price, intensity in zip(prices, intensities, strict=True):
        if not math.isfinite(price) or not math.isfinite(intensity):
            raise ValueError("prices and intensities must be finite")
        if intensity < 0:
            raise ValueError("intensity must be >= 0")
        if price < bounds[0] or price > bounds[-1]:
            continue
        if price == bounds[-1]:
            totals[-1] += float(intensity)
            continue
        index = bisect.bisect_right(bounds, price) - 1
        totals[index] += float(intensity)
    return totals


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

    # Native base-asset OI is not reconstructed from quote OI and candle prices.
    oi_base: np.ndarray | None = None
    candle_observed: np.ndarray | None = None
    oi_observed: np.ndarray | None = None
    flow_observed: np.ndarray | None = None
    oi_available_at: np.ndarray | None = None
    provenance: dict = field(default_factory=dict)
    components: tuple[MarketSeries, ...] = ()

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

        for name in ("oi_base", "oi_available_at"):
            values = getattr(self, name)
            if values is not None:
                values = np.asarray(values, dtype=np.float64)
                if values.shape != self.times.shape:
                    raise ValueError(f"{name} must share the market series length")
                setattr(self, name, values)
        defaults = {
            "candle_observed": np.isfinite(self.close) & (self.close > 0) & np.isfinite(self.high) & np.isfinite(self.low),
            "oi_observed": np.isfinite(self.oi_usd) & (self.oi_usd > 0),
            "flow_observed": np.isfinite(self.long_share),
        }
        for name, default in defaults.items():
            values = getattr(self, name)
            values = default if values is None else np.asarray(values, dtype=bool)
            if values.shape != self.times.shape:
                raise ValueError(f"{name} must share the market series length")
            setattr(self, name, values)
        if len(self.times) > 1 and np.any(np.diff(self.times) <= 0):
            raise ValueError("market timestamps must be strictly increasing")
        if self.bar_seconds <= 0:
            raise ValueError("bar_seconds must be positive")


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
    # Per leverage tier, shape (tiers, bins): the latest column, and one requested column.
    tiers: tuple[int, ...] = ()
    profile_long_tiers: np.ndarray | None = None
    profile_short_tiers: np.ndarray | None = None
    long_tier_column: np.ndarray | None = None
    short_tier_column: np.ndarray | None = None
    metadata: dict = field(default_factory=dict)
    logical_totals: dict[str, np.ndarray] = field(default_factory=dict)
    logical_features: list[dict] = field(default_factory=list)

    @property
    def features(self) -> dict:
        return self.logical_features[-1] if self.logical_features else {}


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
    for j, weight in zip(indexes, weights, strict=True):
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
            return f"${value / 1e9:.1f}B"
        if value >= 1e6:
            return f"${value / 1e6:.0f}M"
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
            f"Within 5% of price, estimated short exposure is {ratio:.2f}× long exposure."
        )
    elif w5["short_usd"] > 0 and w5["long_usd"] == 0:
        lean = "Within 5% of price, the estimated exposure is entirely short exposure."
    elif w5["long_usd"] > 0 and w5["short_usd"] == 0:
        lean = "Within 5% of price, the estimated exposure is entirely long exposure."
    else:
        lean = "Within 5% of price, this window has little estimated exposure in the model."

    above_txt = (
        f"The nearest short liquidation cluster is {fmt_px(nearest_above['price'])} ({nearest_above['distance_pct']:+.2f}%), about {fmt_usd(nearest_above['usd'])}."
        if nearest_above
        else "No short liquidation cluster is resting above price in this window."
    )
    below_txt = (
        f"The nearest long liquidation cluster is {fmt_px(nearest_below['price'])} ({nearest_below['distance_pct']:+.2f}%), about {fmt_usd(nearest_below['usd'])}."
        if nearest_below
        else "No long liquidation cluster is resting below price in this window."
    )
    summary = f"{coin} is trading at {fmt_px(spot)}. {above_txt} {below_txt} {lean}"
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


def _render_cohorts(levels: np.ndarray, amounts: np.ndarray, tiers: np.ndarray,
                    centers: np.ndarray, n_tiers: int, kernel: tuple) -> np.ndarray:
    """Rendering is a mass-conserving projection, never the sweep state."""
    bins = len(centers)
    book = np.zeros((n_tiers, bins))
    if not len(levels):
        return book
    right = np.clip(np.searchsorted(centers, levels), 0, bins - 1)
    left = np.maximum(right - 1, 0)
    nearest = np.where(abs(centers[left] - levels) <= abs(centers[right] - levels), left, right)
    offsets = np.arange(len(kernel)) - len(kernel) // 2
    indexes = nearest[:, None] + offsets
    valid = (indexes >= 0) & (indexes < bins)
    weights = valid * np.asarray(kernel)
    weights /= weights.sum(axis=1)[:, None]
    for j in range(len(kernel)):
        keep = valid[:, j]
        book += np.bincount(tiers[keep] * bins + indexes[keep, j],
                            weights=amounts[keep] * weights[keep, j],
                            minlength=n_tiers * bins).reshape(n_tiers, bins)
    return book


def _logical_features(levels, amounts, sides, births, stamp, spot, opened, swept):
    long = sides == 0
    short = ~long
    total = float(amounts.sum())
    windows = []
    for pct in (1, 2, 5, 10):
        band = spot * pct / 100
        below = long & (levels < spot) & (levels >= spot - band)
        above = short & (levels > spot) & (levels <= spot + band)
        lv, sv = float(amounts[below].sum()), float(amounts[above].sum())
        windows.append({"window_pct": pct, "long_usd": lv, "short_usd": sv,
                        "short_to_long": sv / lv if lv > 0 else None})
    below = levels[long & (levels < spot)]
    above = levels[short & (levels > spot)]
    concentration = None
    if total > 0:
        _, inverse = np.unique(levels, return_inverse=True)
        concentration = float(np.bincount(inverse, weights=amounts).max() / total)
    return {"resting_long_usd": float(amounts[long].sum()),
            "resting_short_usd": float(amounts[short].sum()), "opened_usd": opened,
            "swept_long_usd": float(swept[0]), "swept_short_usd": float(swept[1]),
            "imbalance": windows,
            "nearest_long_price": float(below.max()) if len(below) else None,
            "nearest_short_price": float(above.min()) if len(above) else None,
            "nearest_long_distance_pct": float((below.max() / spot - 1) * 100) if len(below) and spot > 0 else None,
            "nearest_short_distance_pct": float((above.min() / spot - 1) * 100) if len(above) and spot > 0 else None,
            "concentration_top_level_share": concentration,
            "persistence_usd_weighted_seconds": float(np.dot(amounts, (stamp - births) / 1000) / total) if total > 0 else None}


def _visible_series(series: MarketSeries, start_ms: int) -> MarketSeries:
    keep = series.times >= start_ms
    columns = ("times", "open", "high", "low", "close", "volume_usd", "oi_usd", "long_share",
               "oi_base", "candle_observed", "oi_observed", "flow_observed", "oi_available_at")
    values = {name: None if getattr(series, name) is None else getattr(series, name)[keep].copy()
              for name in columns}
    return replace(series, **values,
                   components=tuple(_visible_series(part, start_ms) for part in series.components),
                   provenance=dict(series.provenance), warnings=list(series.warnings))


def build_heatmap(series: MarketSeries, model_id: int, n_bins: int = 720, tier_column: int | None = None,
                  visible_bars: int | None = None) -> Heatmap:
    """Causal post-close inventory. Native OI changes are valued at entry close.

    Each venue owns its baseline and exact-level cohorts. Missing observations
    break the OI delta chain; recovery establishes a baseline without inventing
    exposure. Price triggers use only observed bars and precede close deposits.
    ``visible_bars`` trims only the returned view; history state and render bins
    use the full input. ``tier_column`` is relative to that visible view.
    """
    spec = MODELS.get(int(model_id))
    if spec is None:
        raise ValueError(f"unknown model {model_id}")
    n = len(series.times)
    if n < 2:
        raise ValueError("need at least two candles")
    if visible_bars is not None and (type(visible_bars) is not int or not 2 <= visible_bars <= n):
        raise ValueError("visible_bars must be an integer from 2 to the loaded history length")
    view_start = n - visible_bars if visible_bars is not None else 0
    requested_column = None if tier_column is None else tier_column + view_start
    if not isinstance(n_bins, int) or n_bins < 2 or n_bins > 4096:
        raise ValueError("n_bins must be between 2 and 4096")
    low, high = float(np.nanmin(series.low)), float(np.nanmax(series.high))
    if not np.isfinite(low) or not np.isfinite(high) or low <= 0 or high <= low:
        raise ValueError("price series is empty")
    edges = np.linspace(low * .78, high * 1.22, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    tiers = tuple(int(lev) for lev, _ in spec["weights"])
    weights = np.asarray([w for _, w in spec["weights"]])
    parts = series.components or (series,)
    states = []
    for part in parts:
        states.append({"part": part, "at": {int(t): j for j, t in enumerate(part.times)},
                       "prev": math.nan, "prev_time": None,
                       "levels": np.empty(0), "amounts": np.empty(0),
                       "sides": np.empty(0, dtype=int), "tiers": np.empty(0, dtype=int),
                       "births": np.empty(0)})
    long_heat, short_heat = np.zeros((n_bins, n)), np.zeros((n_bins, n))
    features, coverage = [], []
    long_column = short_column = None
    excluded = 0
    for i, open_ms in enumerate(series.times):
        stamp = int(open_ms) + series.bar_seconds * 1000
        elapsed = (int(open_ms) - int(series.times[i - 1])) / 1000 if i else 0
        decay = float(spec["decay"]) ** (elapsed / DECAY_REFERENCE_SECONDS)
        opened, swept = 0.0, np.zeros(2)
        observed_venues = []
        for state in states:
            part = state["part"]
            j = state["at"].get(int(open_ms))
            amounts, levels, sides = state["amounts"], state["levels"], state["sides"]
            price_valid = j is not None and bool(part.candle_observed[j])
            if price_valid:
                price_valid = bool(np.isfinite(part.low[j]) and np.isfinite(part.high[j])
                                   and 0 < part.low[j] <= part.high[j]
                                   and np.isfinite(part.close[j]) and part.close[j] > 0)
            if price_valid:
                hit = ((sides == 0) & (levels >= part.low[j])) | ((sides == 1) & (levels <= part.high[j]))
                for side in (0, 1):
                    swept[side] += amounts[hit & (sides == side)].sum()
                amounts[hit] = 0
            # Decay is elapsed time even when a venue has an observation gap.
            amounts *= decay
            cur = math.nan
            if j is not None:
                native = part.oi_base
                cur = float(native[j] if native is not None else part.oi_usd[j])
            available = j is not None and (part.oi_available_at is None or
                (np.isfinite(part.oi_available_at[j]) and part.oi_available_at[j] <= stamp))
            valid = price_valid and available and bool(part.oi_observed[j]) and np.isfinite(cur) and cur > 0
            if valid:
                observed_venues.append(part.exchange)
                entry = float(part.close[j])
                prev = state["prev"]
                consecutive = state["prev_time"] is not None and int(open_ms) - state["prev_time"] == part.bar_seconds * 1000
                delta = cur - prev if np.isfinite(prev) and consecutive else 0.0
                share = float(part.long_share[j]) if spec["flow"] and part.flow_observed[j] else .5
                share = float(np.clip(share, .05, .95)) if np.isfinite(share) else .5
                if delta < 0:
                    # OI reductions are matched contracts; both scenario sides shrink equally.
                    amounts *= max(0.0, cur / prev)
                add = max(delta, 0.0) * (entry if part.oi_base is not None else 1.0)
                volume = float(part.volume_usd[j])
                if np.isfinite(volume) and volume > 0 and spec["churn"] > 0:
                    # Explicit turnover scenario, independent from native OI openings.
                    add += min(volume * spec["churn"], cur * (entry if part.oi_base is not None else 1.0) * .02)
                if add > 0:
                    opened += add
                    new_levels = [liq_price(entry, lev, side, MMR, venue=part.exchange if part.exchange in VENUES else "binance")
                                  for side in ("long", "short") for lev in tiers]
                    state["levels"] = np.concatenate((levels, new_levels))
                    state["amounts"] = np.concatenate((amounts, add * np.concatenate((weights * share, weights * (1-share)))))
                    state["sides"] = np.concatenate((sides, np.repeat([0, 1], len(tiers))))
                    state["tiers"] = np.concatenate((state["tiers"], np.tile(np.arange(len(tiers)), 2)))
                    state["births"] = np.concatenate((state["births"], np.full(2 * len(tiers), stamp)))
                state["prev"], state["prev_time"] = cur, int(open_ms)
            else:
                state["prev"], state["prev_time"] = math.nan, None
                excluded += 1
            keep = state["amounts"] > 0
            for key in ("levels", "amounts", "sides", "tiers", "births"):
                state[key] = state[key][keep]
        joined = {key: np.concatenate([state[key] for state in states])
                  for key in ("levels", "amounts", "sides", "tiers", "births")}
        long = joined["sides"] == 0
        books = []
        for side in (long, ~long):
            books.append(_render_cohorts(joined["levels"][side], joined["amounts"][side],
                         joined["tiers"][side], centers, len(tiers), spec["kernel"]))
        long_book, short_book = books
        long_heat[:, i], short_heat[:, i] = long_book.sum(axis=0), short_book.sum(axis=0)
        if i == requested_column:
            long_column, short_column = long_book.copy(), short_book.copy()
        features.append(_logical_features(joined["levels"], joined["amounts"], joined["sides"],
                        joined["births"], stamp, float(series.close[i]), opened, swept))
        coverage.append(observed_venues)
    profile_long, profile_short = long_heat[:, -1].copy(), short_heat[:, -1].copy()
    assumptions = ["Isolated margin scenario; fixed leverage weights and 0.5% maintenance margin; no account-level entries known.",
                   "Amounts are estimated entry-notional USD, not current marked exposure or predicted liquidations.",
                   "Observed trade-price OHLC approximates mark-price sweep triggers.",
                   "Net OI changes represent matched contracts; long/short shares allocate assumed vulnerable exposure.",
                   "No inventory is known before the history anchor; gaps rebaseline OI without recovery flow.",
                   "Model 2 volume turnover is an explicit scenario, not observed position openings."]
    metadata = {"model_version": MODEL_VERSION,
                "parameter_digest": hashlib.sha256(json.dumps({"model": spec, "mmr": MMR, "decay_reference_seconds": DECAY_REFERENCE_SECONDS}, sort_keys=True).encode()).hexdigest(),
                "assumptions": assumptions, "units": "entry_notional_usd",
                "time_semantics": "bar_close_post_update", "state_timestamp": "bar_close_post_update",
                "warmup_anchor_ms": int(series.times[0]), "insufficient_history": True,
                "excluded_venue_observations": excluded, "venue_coverage": coverage[view_start:],
                "history_bars": n, "visible_bars": n - view_start,
                "oi_accounting": {p.exchange: "native_base" if p.oi_base is not None else "legacy_quote_delta" for p in parts},
                "flow_prior": {p.exchange: "taker_when_observed_else_neutral" if spec["flow"] else "neutral" for p in parts},
                "decay_reference_seconds": DECAY_REFERENCE_SECONDS}
    result = Heatmap(series, int(model_id), centers, long_heat, short_heat, profile_long, profile_short,
                   analyze(centers, profile_long, profile_short, float(series.close[-1]), series.coin),
                   tiers, long_book.copy(), short_book.copy(), long_column, short_column,
                   metadata, {key: np.asarray([row[key] for row in features]) for key in
                              ("resting_long_usd", "resting_short_usd", "opened_usd", "swept_long_usd", "swept_short_usd")}, features)
    if view_start:
        result.series = _visible_series(series, int(series.times[view_start]))
        result.long_heat = result.long_heat[:, view_start:].copy()
        result.short_heat = result.short_heat[:, view_start:].copy()
        result.logical_features = result.logical_features[view_start:]
        result.logical_totals = {key: values[view_start:].copy() for key, values in result.logical_totals.items()}
    return result


def suggest_view(series: MarketSeries) -> tuple[float, float]:
    low = float(np.nanmin(series.low))
    high = float(np.nanmax(series.high))
    span = max(high - low, high * 0.01)
    return low - span * 0.35, high + span * 0.35
