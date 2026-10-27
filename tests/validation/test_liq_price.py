"""The model's liquidation price against the exchange-documented oracle."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from liqmap.model import MMR, liq_price_long, liq_price_short
from tests.validation import oracle


@given(
    entry=st.integers(min_value=1, max_value=1_000_000).map(float),
    leverage=st.integers(min_value=2, max_value=125),
)
def test_model_matches_binance_zero_tier_oracle(entry: float, leverage: int) -> None:
    assert liq_price_long(entry, leverage) == pytest.approx(oracle.liq_price(entry, leverage, "long", MMR), rel=1e-12)
    assert liq_price_short(entry, leverage) == pytest.approx(oracle.liq_price(entry, leverage, "short", MMR), rel=1e-12)


def test_golden_entry_100_at_10x() -> None:
    assert liq_price_long(100.0, 10) == pytest.approx(90.4523, abs=1e-4)
    assert liq_price_short(100.0, 10) == pytest.approx(109.4527, abs=1e-4)


@given(entry=st.integers(min_value=1, max_value=1_000_000).map(float), low=st.integers(2, 124), high=st.integers(3, 125))
def test_higher_leverage_liquidates_closer(entry: float, low: int, high: int) -> None:
    if high <= low:
        return
    assert entry - liq_price_long(entry, high) < entry - liq_price_long(entry, low)
    assert liq_price_short(entry, high) - entry < liq_price_short(entry, low) - entry
