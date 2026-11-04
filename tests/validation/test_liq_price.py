"""The model's liquidation price against the exchange-documented oracle."""

from __future__ import annotations

import pytest

from liqmap.model import MMR, liq_price_long, liq_price_short
from tests.validation import oracle

ENTRIES = (0.0123, 1.0, 99.5, 100.0, 3_210.0, 82_000.0, 1_000_000.0)
LEVERAGES = range(2, 126)


def test_model_matches_binance_zero_tier_oracle() -> None:
    for entry in ENTRIES:
        for leverage in LEVERAGES:
            assert liq_price_long(entry, leverage) == pytest.approx(oracle.liq_price(entry, leverage, "long", MMR), rel=1e-12)
            assert liq_price_short(entry, leverage) == pytest.approx(oracle.liq_price(entry, leverage, "short", MMR), rel=1e-12)


def test_golden_entry_100_at_10x() -> None:
    assert liq_price_long(100.0, 10) == pytest.approx(90.4523, abs=1e-4)
    assert liq_price_short(100.0, 10) == pytest.approx(109.4527, abs=1e-4)


def test_higher_leverage_liquidates_closer() -> None:
    for entry in ENTRIES:
        for low, high in zip(LEVERAGES, LEVERAGES[1:]):
            assert entry - liq_price_long(entry, high) < entry - liq_price_long(entry, low)
            assert liq_price_short(entry, high) - entry < liq_price_short(entry, low) - entry
