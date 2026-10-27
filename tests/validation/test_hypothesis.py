"""Property tests for the liquidation contract.

Prices are checked on the documented formulas and, where the legacy binding
covers them, on production code. Fade and bin properties run on the spec in
``oracle``. The same tests are collected against production and skipped until
``fade_swept`` and ``bin_intensity`` are exported.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from tests.validation import adapter, oracle

Level = tuple[float, float, str]
Bar = tuple[float, float]
PriceFn = Callable[..., float]

# 0.4% stays under 1/125, so every leverage in 2x–125x is still openable.
_MMR = st.integers(min_value=0, max_value=40).map(lambda value: value / 10_000)
_ENTRY = st.integers(min_value=1, max_value=1_000_000).map(float)
_LEVERAGE = st.integers(min_value=2, max_value=125)


def _fade_bound() -> bool:
    return not adapter.binding_status()["fade_swept"].startswith("unbound:")


def _bin_bound() -> bool:
    return not adapter.binding_status()["bin_intensity"].startswith("unbound:")


def _finite_pair(fn: PriceFn, entry: float, leverage: int, mmr: float, venue: str) -> None:
    for side in ("long", "short"):
        price = fn(entry, float(leverage), side, mmr, venue=venue)
        assert math.isfinite(price)
        if side == "long":
            assert price < entry
        else:
            assert price > entry


@pytest.mark.parametrize("venue", ["binance", "bybit", "bybit_uta", "okx"])
@given(entry=_ENTRY, leverage=_LEVERAGE, mmr=_MMR)
@example(entry=100, leverage=10, mmr=0.005)
def test_oracle_long_below_short_above_and_finite(
    venue: str, entry: float, leverage: int, mmr: float
) -> None:
    _finite_pair(oracle.liq_price, entry, leverage, mmr, venue)


@pytest.mark.parametrize("venue", ["binance", "bybit_uta", "okx"])
@given(entry=_ENTRY, leverage=_LEVERAGE, mmr=_MMR)
@example(entry=100, leverage=10, mmr=0.005)
def test_model_long_below_short_above_and_finite(
    venue: str, entry: float, leverage: int, mmr: float
) -> None:
    _finite_pair(adapter.liq_price, entry, leverage, mmr, venue)


@pytest.mark.parametrize("venue", ["binance", "bybit", "bybit_uta", "okx"])
@given(
    entry=_ENTRY,
    low=_LEVERAGE,
    high=_LEVERAGE,
    mmr=_MMR,
    side=st.sampled_from(["long", "short"]),
)
def test_oracle_higher_leverage_is_closer(
    venue: str, entry: float, low: int, high: int, mmr: float, side: str
) -> None:
    assume(high > low)
    near = oracle.liq_price(entry, float(low), side, mmr, venue=venue)
    far = oracle.liq_price(entry, float(high), side, mmr, venue=venue)
    assert math.isfinite(near) and math.isfinite(far)
    assert abs(near - entry) > abs(far - entry)


@given(
    entry=_ENTRY,
    low=_LEVERAGE,
    high=_LEVERAGE,
    mmr=_MMR,
    side=st.sampled_from(["long", "short"]),
)
def test_model_higher_leverage_is_closer(
    entry: float, low: int, high: int, mmr: float, side: str
) -> None:
    assume(high > low)
    near = adapter.liq_price(entry, float(low), side, mmr)
    far = adapter.liq_price(entry, float(high), side, mmr)
    assert math.isfinite(near) and math.isfinite(far)
    assert abs(near - entry) > abs(far - entry)


@st.composite
def _books(
    draw: st.DrawFn,
) -> tuple[list[Level], list[Bar]]:
    n_levels = draw(st.integers(min_value=0, max_value=8))
    levels: list[Level] = []
    for _ in range(n_levels):
        price = float(draw(st.integers(min_value=1, max_value=200)))
        intensity = float(draw(st.integers(min_value=0, max_value=40)))
        side = draw(st.sampled_from(["long", "short"]))
        levels.append((price, intensity, side))
    n_bars = draw(st.integers(min_value=0, max_value=5))
    bars: list[Bar] = []
    for _ in range(n_bars):
        low = draw(st.integers(min_value=1, max_value=200))
        high = draw(st.integers(min_value=low, max_value=220))
        bars.append((float(high), float(low)))
    return levels, bars


def _swept(level: Level, bars: Sequence[Bar]) -> bool:
    price, _intensity, side = level
    if side == "long":
        return any(low <= price for _high, low in bars)
    return any(high >= price for high, _low in bars)


def _assert_fade(fade: Callable[..., list[Level]], levels: list[Level], bars: list[Bar]) -> None:
    result = fade(levels, bars)
    expected = [level for level in levels if not _swept(level, bars)]
    assert result == expected
    assert sum(level[1] for level in result) == pytest.approx(
        sum(level[1] for level in expected)
    )
    assert all(math.isfinite(price) and math.isfinite(intensity) for price, intensity, _ in result)


@given(_books())
def test_oracle_fade_removes_swept_levels(book: tuple[list[Level], list[Bar]]) -> None:
    levels, bars = book
    _assert_fade(oracle.fade_swept, levels, bars)


@pytest.mark.skipif(
    not _fade_bound(),
    reason=(
        "fade_swept is not exported yet. Repoint FADE_CANDIDATES in "
        "tests/validation/adapter.py."
    ),
)
@given(_books())
def test_model_fade_removes_swept_levels(book: tuple[list[Level], list[Bar]]) -> None:
    levels, bars = book
    _assert_fade(adapter.fade_swept, levels, bars)


@st.composite
def _histograms(
    draw: st.DrawFn,
) -> tuple[list[float], list[float], list[float]]:
    width = draw(st.integers(min_value=2, max_value=6))
    raw = draw(
        st.lists(
            st.integers(min_value=0, max_value=80),
            min_size=width,
            max_size=width,
            unique=True,
        )
    )
    edges = [float(value) for value in sorted(raw)]
    n_points = draw(st.integers(min_value=0, max_value=10))
    prices: list[float] = []
    weights: list[float] = []
    for _ in range(n_points):
        prices.append(float(draw(st.integers(min_value=-5, max_value=90))))
        weights.append(float(draw(st.integers(min_value=0, max_value=30))))
    return prices, weights, edges


def _assert_bins(
    binner: Callable[..., list[float]],
    prices: Sequence[float],
    weights: Sequence[float],
    edges: Sequence[float],
) -> None:
    got = binner(prices, weights, edges)
    assert len(got) == len(edges) - 1
    assert all(math.isfinite(value) and value >= 0 for value in got)
    landed = sum(
        weight
        for price, weight in zip(prices, weights, strict=True)
        if edges[0] <= price <= edges[-1]
    )
    assert sum(got) == pytest.approx(landed)


@given(_histograms())
def test_oracle_bin_totals_conserve_intensity(
    hist: tuple[list[float], list[float], list[float]],
) -> None:
    prices, weights, edges = hist
    _assert_bins(oracle.bin_intensity, prices, weights, edges)


@pytest.mark.skipif(
    not _bin_bound(),
    reason=(
        "bin_intensity is not exported yet. Repoint BIN_CANDIDATES in "
        "tests/validation/adapter.py."
    ),
)
@given(_histograms())
def test_model_bin_totals_conserve_intensity(
    hist: tuple[list[float], list[float], list[float]],
) -> None:
    prices, weights, edges = hist
    _assert_bins(adapter.bin_intensity, prices, weights, edges)


def test_oracle_rejects_nan_prices() -> None:
    with pytest.raises(ValueError):
        oracle.liq_price(float("nan"), 10, "long", 0.005)
    with pytest.raises(ValueError):
        oracle.fade_swept([(float("nan"), 1.0, "long")], [])
    with pytest.raises(ValueError):
        oracle.bin_intensity([float("nan")], [1.0], [0.0, 1.0])
