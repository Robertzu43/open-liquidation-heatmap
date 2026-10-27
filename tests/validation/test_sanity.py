"""Sanity properties that do not depend on a particular exchange worksheet.

Higher leverage liquidates closer to the entry. A long liquidates below the
entry and a short above it, while maintenance margin is inside the initial
margin. Fade deletes levels price has traded through and leaves the rest
alone. Bin totals of in-range prices add back to the intensity that was placed.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

import pytest

from tests.validation import oracle
from tests.validation.test_liq_price import ENTRY, LEVERAGES, TIER1_MMR

PriceFn = Callable[..., float]
FadeFn = Callable[..., list[tuple[float, float, str]]]
BinFn = Callable[..., list[float]]


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


def test_fade_removes_a_long_level_a_wick_touches(fade_of: FadeFn) -> None:
    levels = [(90.0, 5.0, "long"), (110.0, 3.0, "short"), (80.0, 1.5, "long")]
    bars = [(100.0, 90.0)]
    assert fade_of(levels, bars) == [(110.0, 3.0, "short"), (80.0, 1.5, "long")]


def test_fade_removes_a_short_level_a_high_touches(fade_of: FadeFn) -> None:
    levels = [(90.0, 5.0, "long"), (110.0, 3.0, "short")]
    bars = [(110.0, 95.0)]
    assert fade_of(levels, bars) == [(90.0, 5.0, "long")]


def test_fade_keeps_levels_price_has_not_traded_through(fade_of: FadeFn) -> None:
    levels = [(90.0, 5.0, "long"), (110.0, 4.0, "short")]
    bars = [(109.0, 91.0), (108.0, 95.0)]
    assert fade_of(levels, bars) == [(90.0, 5.0, "long"), (110.0, 4.0, "short")]


def test_fade_does_not_let_the_other_side_sweep_a_level(fade_of: FadeFn) -> None:
    levels = [(90.0, 2.0, "long"), (110.0, 2.0, "short")]
    # High reaches the short, but the long is still below every low.
    # A deep low would be required to sweep the long; this low does not.
    swept_short = fade_of(levels, [(110.0, 95.0)])
    assert (90.0, 2.0, "long") in swept_short
    assert (110.0, 2.0, "short") not in swept_short
    swept_long = fade_of(levels, [(100.0, 90.0)])
    assert swept_long == [(110.0, 2.0, "short")]


def test_fade_conserves_unswept_intensity_and_order(fade_of: FadeFn) -> None:
    levels = [
        (100.0, 1.0, "short"),
        (70.0, 4.0, "long"),
        (130.0, 2.5, "short"),
        (69.0, 0.5, "long"),
    ]
    bars = [(120.0, 70.0)]
    got = fade_of(levels, bars)
    assert got == [(130.0, 2.5, "short"), (69.0, 0.5, "long")]
    assert sum(level[1] for level in got) == pytest.approx(3.0)


def test_fade_is_idempotent_and_later_bars_still_sweep(fade_of: FadeFn) -> None:
    levels = [(90.0, 1.0, "long"), (120.0, 1.0, "short")]
    first_bars = [(100.0, 95.0)]
    once = fade_of(levels, first_bars)
    assert fade_of(once, first_bars) == once
    assert once == [(90.0, 1.0, "long"), (120.0, 1.0, "short")]
    after = fade_of(levels, [(100.0, 95.0), (120.0, 94.0)])
    assert after == [(90.0, 1.0, "long")]


def test_fade_of_empty_inputs(fade_of: FadeFn) -> None:
    assert fade_of([], [(100.0, 90.0)]) == []
    levels = [(90.0, 1.0, "long")]
    assert fade_of(levels, []) == [(90.0, 1.0, "long")]


def test_fade_rejects_a_bar_whose_high_is_below_its_low(fade_of: FadeFn) -> None:
    with pytest.raises(ValueError):
        fade_of([(90.0, 1.0, "long")], [(90.0, 100.0)])


def test_bin_totals_conserve_in_range_intensity(bin_of: BinFn) -> None:
    prices = [1.0, 2.5, 2.5, 4.9]
    weights = [1.0, 2.0, 3.0, 4.0]
    edges = [0.0, 2.0, 3.0, 5.0]
    got = bin_of(prices, weights, edges)
    assert got == pytest.approx([1.0, 5.0, 4.0])
    assert sum(got) == pytest.approx(sum(weights))


def test_bin_puts_an_interior_edge_in_the_upper_bin(bin_of: BinFn) -> None:
    got = bin_of([2.0], [7.0], [0.0, 2.0, 4.0])
    assert got == pytest.approx([0.0, 7.0])


def test_bin_includes_the_outer_right_edge_only(bin_of: BinFn) -> None:
    edges = [0.0, 2.0, 4.0]
    assert bin_of([0.0, 4.0], [3.0, 5.0], edges) == pytest.approx([3.0, 5.0])
    # Just outside the right edge is excluded, not stuffed into the last bin.
    assert bin_of([4.0000001], [9.0], edges) == pytest.approx([0.0, 0.0])


def test_bin_excludes_outsiders_so_totals_match_what_landed(bin_of: BinFn) -> None:
    got = bin_of([-1.0, 1.0, 10.0], [5.0, 3.0, 8.0], [0.0, 2.0])
    assert got == pytest.approx([3.0])
    assert sum(got) == pytest.approx(3.0)


def test_bin_empty_and_single_bin(bin_of: BinFn) -> None:
    assert bin_of([], [], [0.0, 1.0, 2.0]) == pytest.approx([0.0, 0.0])
    assert bin_of([10.0, 10.5], [1.5, 2.5], [10.0, 11.0]) == pytest.approx([4.0])


def test_bin_rejects_uneven_inputs_and_unsorted_edges(bin_of: BinFn) -> None:
    with pytest.raises(ValueError):
        bin_of([1.0, 2.0], [1.0], [0.0, 3.0])
    with pytest.raises(ValueError):
        bin_of([1.0], [1.0], [3.0, 0.0, 4.0])
