"""Liquidation identity, leverage prior, and maintenance brackets."""

import pytest

from olh.models.brackets import brackets_for, mmr_for_leverage
from olh.models.leverage import (
    CLASSIC_WEIGHTS,
    STANDARD_LADDER,
    build_leverage_weights,
    filter_weights,
    resolve_ladder,
)
from olh.models.mathutil import liq_price_long, liq_price_short


def test_golden_10x_at_half_percent_maintenance():
    # entry 100, 10x, mmr 0.005, cum 0. Documented isolated identity.
    assert round(liq_price_long(100, 10, 0.005), 4) == 90.4523
    assert round(liq_price_short(100, 10, 0.005), 4) == 109.4527


def test_higher_leverage_sits_closer_to_entry():
    nearer = liq_price_long(100, 25, 0.005)
    farther = liq_price_long(100, 10, 0.005)
    assert farther < nearer < 100
    assert liq_price_short(100, 25, 0.005) < liq_price_short(100, 10, 0.005)


def test_higher_mmr_liquidates_sooner_and_cum_moves_the_documented_way():
    base_long = liq_price_long(100, 10, 0.005)
    base_short = liq_price_short(100, 10, 0.005)
    assert liq_price_long(100, 10, 0.05) > base_long
    assert liq_price_short(100, 10, 0.05) < base_short
    # Positive maintenance amount gives a long more room and a short more room.
    assert liq_price_long(100, 10, 0.005, maintenance_amount=50, qty=1) < base_long
    assert liq_price_short(100, 10, 0.005, maintenance_amount=50, qty=1) > base_short


def test_invalid_inputs_and_long_at_or_above_entry():
    with pytest.raises(ValueError):
        liq_price_long(100, 1, 0.005)
    with pytest.raises(ValueError):
        liq_price_short(100, 10, 1)
    # mmr 0.9 at 2x pushes the long formula through the entry. That is not a level.
    assert liq_price_long(100, 2, 0.9) is None


def test_ladder_includes_25_and_respects_the_symbol_cap():
    assert 25 in STANDARD_LADDER
    assert 25 in resolve_ladder("full", 125)
    assert 25 in resolve_ladder("standard", 100)
    assert 125 not in resolve_ladder("full", 100)
    assert CLASSIC_WEIGHTS[25] > 0


def test_lognormal_peaks_near_15_and_a_filter_does_not_renormalize():
    weights = build_leverage_weights(resolve_ladder("standard", 125), "lognormal")
    assert weights[15] > weights[100]
    assert weights[25] > 0
    assert sum(weights.values()) == pytest.approx(1)
    kept = filter_weights(weights, [10])
    assert kept[10] == pytest.approx(weights[10])
    assert sum(kept.values()) < 0.5


def test_bracket_mode_selects_the_tightest_tier_that_allows_the_leverage():
    schedule = brackets_for("BTCUSDT")
    mmr, cum = mmr_for_leverage(100, schedule, "bracket", None)
    assert mmr == 0.005
    assert cum == 50
    ten, _ = mmr_for_leverage(10, schedule, "bracket", None)
    assert ten == 0.05
    constant, amount = mmr_for_leverage(10, schedule, "constant", 0.005)
    assert constant == 0.005
    assert amount == 0
