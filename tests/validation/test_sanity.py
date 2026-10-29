"""Sanity properties that do not depend on a particular exchange worksheet.

Higher leverage liquidates closer to the entry. A long liquidates below the
entry and a short above it, while maintenance margin is inside the initial
margin.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

import pytest

from tests.validation import oracle
from tests.validation.test_liq_price import ENTRY, LEVERAGES, TIER1_MMR

PriceFn = Callable[..., float]


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("venue", ["binance", "bybit", "okx"])
def test_higher_leverage_liquidates_closer(price_of: PriceFn, side: str, venue: str) -> None:
    distances: list[float] = []
    for leverage in LEVERAGES:
        price = price_of(ENTRY, leverage, side, TIER1_MMR, venue=venue)
        distances.append(abs(price - ENTRY))
        if side == "long":
            assert price < ENTRY
        else:
            assert price > ENTRY
    assert all(left > right for left, right in itertools.pairwise(distances))


def test_higher_maintenance_rate_liquidates_closer(price_of: PriceFn) -> None:
    # 1/20 = 0.05, so every rate below is still an openable isolated position.
    rates = (0.0, 0.001, 0.0025, 0.004, 0.005, 0.01)
    for side in ("long", "short"):
        distances = [
            abs(price_of(ENTRY, 20, side, rate, venue="binance") - ENTRY)
            for rate in rates
        ]
        assert all(left > right for left, right in itertools.pairwise(distances))


def test_okx_tier_snapshot_higher_mmr_is_closer(price_of: PriceFn) -> None:
    """Public OKX BTC-USDT isolated tiers retrieved 2026-10-27.

    Tier 1 MMR 0.004 (max leverage 100), tier 2 MMR 0.005 (max 66.66),
    tier 3 MMR 0.0075 (max 50). 50x is inside all three max leverages.
    Source: GET /api/v5/public/position-tiers?instType=SWAP&tdMode=isolated&instFamily=BTC-USDT
    """
    mmrs = (0.004, 0.005, 0.0075)
    for side in ("long", "short"):
        distances = [
            abs(price_of(ENTRY, 50, side, mmr, venue="okx") - ENTRY) for mmr in mmrs
        ]
        assert all(left > right for left, right in itertools.pairwise(distances))


def test_fee_on_okx_moves_liquidation_closer() -> None:
    for side in ("long", "short"):
        plain = oracle.liq_price(ENTRY, 25, side, 0.004, venue="okx")
        with_fee = oracle.liq_price(ENTRY, 25, side, 0.004, fee=0.0005, venue="okx")
        assert abs(with_fee - ENTRY) < abs(plain - ENTRY)


def test_long_stays_below_entry_and_short_above_at_125x(price_of: PriceFn) -> None:
    long_price = price_of(ENTRY, 125, "long", TIER1_MMR, venue="binance")
    short_price = price_of(ENTRY, 125, "short", TIER1_MMR, venue="binance")
    assert long_price < ENTRY < short_price
    # 2x is much farther out than 125x on both sides.
    long_2 = price_of(ENTRY, 2, "long", TIER1_MMR, venue="binance")
    short_2 = price_of(ENTRY, 2, "short", TIER1_MMR, venue="binance")
    assert long_2 < long_price
    assert short_2 > short_price
