"""Property tests for liquidation prices.

Prices are checked on the documented formulas in ``oracle`` and on production
``liqmap.model.liq_price``.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from liqmap import model
from tests.validation import oracle

PriceFn = Callable[..., float]

# 0.4% stays under 1/125, so every leverage in 2x–125x is still openable.
_MMR = st.integers(min_value=0, max_value=40).map(lambda value: value / 10_000)
_ENTRY = st.integers(min_value=1, max_value=1_000_000).map(float)
_LEVERAGE = st.integers(min_value=2, max_value=125)


def _finite_pair(fn: PriceFn, entry: float, leverage: int, mmr: float, venue: str) -> None:
    for side in ("long", "short"):
        price = fn(entry, float(leverage), side, mmr, venue=venue)
        assert math.isfinite(price)
        if side == "long":
            assert price < entry
        else:
            assert price > entry


@pytest.mark.parametrize("venue", ["binance", "bybit", "okx"])
@given(entry=_ENTRY, leverage=_LEVERAGE, mmr=_MMR)
@example(entry=100, leverage=10, mmr=0.005)
def test_oracle_long_below_short_above_and_finite(
    venue: str, entry: float, leverage: int, mmr: float
) -> None:
    _finite_pair(oracle.liq_price, entry, leverage, mmr, venue)


@pytest.mark.parametrize("venue", ["binance", "bybit", "okx"])
@given(entry=_ENTRY, leverage=_LEVERAGE, mmr=_MMR)
@example(entry=100, leverage=10, mmr=0.005)
def test_model_long_below_short_above_and_finite(
    venue: str, entry: float, leverage: int, mmr: float
) -> None:
    _finite_pair(model.liq_price, entry, leverage, mmr, venue)


@pytest.mark.parametrize("venue", ["binance", "bybit", "okx"])
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
    near = model.liq_price(entry, float(low), side, mmr)
    far = model.liq_price(entry, float(high), side, mmr)
    assert math.isfinite(near) and math.isfinite(far)
    assert abs(near - entry) > abs(far - entry)


def test_oracle_rejects_nan_prices() -> None:
    """The spec raises ``ValueError`` (``inputs must be finite``)."""
    with pytest.raises(ValueError):
        oracle.liq_price(float("nan"), 10, "long", 0.005)
