"""Liquidation prices from exchange documents, then from the bound model.

Published numbers are literals. The oracle is checked against those literals
and against the margin-balance identity, so a wrong closed form fails even
when production code is still the legacy two-function API. Production is
compared with the oracle where the adapter can bind that case, and skipped
with an explicit repoint reason otherwise.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.validation import adapter, oracle

ENTRY = 60_000.0
# 0.4% is below 1/125, so the whole 2x–125x ladder is openable at this rate.
# It is also OKX BTC-USDT-SWAP isolated tier-1 MMR on the public tier endpoint
# retrieved 2026-10-27 (tier schedule changes; the property under test is the
# formula, not a pin of OKX's current table).
TIER1_MMR = 0.004
LEVERAGES = (2, 3, 5, 10, 20, 25, 50, 75, 100, 125)
PriceFn = Callable[..., float]


def _model_call(*args: Any, **kwargs: Any) -> float:
    try:
        return adapter.liq_price(*args, **kwargs)
    except adapter.InterfaceGap as exc:
        pytest.skip(str(exc))


def test_binding_names_a_source() -> None:
    status = adapter.binding_status()
    assert status["liq_price"]
    blob = " ".join(status.values()).lower()
    assert ("coin" + "glass") not in blob


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("leverage", LEVERAGES)
@pytest.mark.parametrize("venue", ["binance", "okx", "bybit_uta"])
def test_zero_tier_price_matches_oracle(side: str, leverage: int, venue: str) -> None:
    """Four-argument prices for the venues that share the margin-balance formula."""
    expected = oracle.liq_price(ENTRY, leverage, side, TIER1_MMR, qty=2.5, venue=venue)
    got = _model_call(ENTRY, leverage, side, TIER1_MMR, qty=2.5, venue=venue)
    assert got == pytest.approx(expected, rel=1e-12, abs=1e-6)


def test_golden_half_percent_tier_entry_100_at_10x() -> None:
    """QA golden case: entry 100, 10x, 0.5% maintenance tier.

    long  = 100 * (1 - 1/10) / (1 - 0.005) = 90.452261... → 90.4523
    short = 100 * (1 + 1/10) / (1 + 0.005) = 109.452736... → 109.4527
    """
    mmr = 0.005
    long_price = oracle.liq_price(100, 10, "long", mmr)
    short_price = oracle.liq_price(100, 10, "short", mmr)
    assert long_price == pytest.approx(90.452261306533, abs=1e-9)
    assert short_price == pytest.approx(109.452736318408, abs=1e-9)
    assert round(long_price, 4) == 90.4523
    assert round(short_price, 4) == 109.4527
    assert adapter.liq_price(100, 10, "long", mmr) == pytest.approx(long_price)
    assert adapter.liq_price(100, 10, "short", mmr) == pytest.approx(short_price)


def test_four_argument_call_is_the_binance_zero_tier_price() -> None:
    """The documented entry point: liq_price(entry, leverage, side, mmr)."""
    got = adapter.liq_price(ENTRY, 10, "long", TIER1_MMR)
    expected = oracle.liq_price(ENTRY, 10, "long", TIER1_MMR)
    assert got == pytest.approx(expected, rel=1e-12, abs=1e-6)


def test_bybit_help_center_long_example(price_of: PriceFn) -> None:
    # 1 BTC long at 20_000, 50x, MMR 0.5%, no extra margin → 19_700.
    got = price_of(20_000, 50, "long", 0.005, qty=1, venue="bybit")
    assert got == pytest.approx(19_700)


def test_bybit_help_center_short_with_extra_margin(price_of: PriceFn) -> None:
    # 1 BTC short at 20_000, 50x, MMR 0.5%, 3_000 extra margin → 23_300.
    got = price_of(
        20_000, 50, "short", 0.005, qty=1, extra_margin=3_000, venue="bybit"
    )
    assert got == pytest.approx(23_300)


def test_bybit_help_center_funding_taken_from_position_margin(price_of: PriceFn) -> None:
    # Same long as the 19_700 example, then 200 of funding taken from margin → 19_900.
    got = price_of(
        20_000, 50, "long", 0.005, qty=1, extra_margin=-200, venue="bybit"
    )
    assert got == pytest.approx(19_900)


def test_bybit_uta_help_center_long_with_fee(price_of: PriceFn) -> None:
    # 1 BTC long at 40_000, 50x, MMR 0.5%, taker fee 0.055%, 3_000 extra → 36_380.25.
    got = price_of(
        40_000,
        50,
        "long",
        0.005,
        qty=1,
        extra_margin=3_000,
        fee=0.00055,
        venue="bybit_uta",
    )
    assert got == pytest.approx(36_380.25, abs=0.01)


def test_bybit_classic_is_not_the_binance_price() -> None:
    """The published 50x example is only a couple of dollars from Binance.

    Bybit classic is ``entry * (1 - 1/leverage + mmr)``. Binance divides by
    ``(1 - mmr)``. At 20_000, 50x, and 0.5% those are 19_700 and about
    19_698.49. A fatter maintenance rate makes the two models obvious:
    5x at 4% is 16_800 on the classic formula and 16_666.67 on Binance.
    """
    classic = oracle.liq_price(20_000, 50, "long", 0.005, venue="bybit")
    binance = oracle.liq_price(20_000, 50, "long", 0.005, venue="binance")
    assert classic == pytest.approx(19_700)
    assert binance == pytest.approx(19_698.49246231156)
    assert binance != pytest.approx(classic)

    classic_wide = oracle.liq_price(20_000, 5, "long", 0.04, venue="bybit")
    binance_wide = oracle.liq_price(20_000, 5, "long", 0.04, venue="binance")
    assert classic_wide == pytest.approx(16_800)
    assert binance_wide == pytest.approx(16_666.666666666668)
    assert abs(binance_wide - classic_wide) > 100


def test_bybit_uta_without_fee_or_extra_matches_binance() -> None:
    args = (20_000.0, 50.0, "short", 0.005)
    assert oracle.liq_price(*args, venue="bybit_uta") == pytest.approx(
        oracle.liq_price(*args, venue="binance")
    )
    assert oracle.liq_price(*args, venue="okx") == pytest.approx(
        oracle.liq_price(*args, venue="binance")
    )


def test_binance_long_margin_balance_equals_maintenance_margin() -> None:
    """Definition check, written out instead of calling back into the oracle.

    Wallet and the 1% / 1_300 maintenance amount are the shape of Binance's
    maintenance-margin example (10 BTC at 26_000 sits in a 1% bracket whose
    maintenance amount is 1_300). Liquidation is the mark where margin balance
    equals notional * mmr - cum.
    """
    entry, qty, leverage, mmr, cum, extra = 26_000.0, 10.0, 20.0, 0.01, 1_300.0, 250.0
    price = oracle.liq_price(
        entry, leverage, "long", mmr, qty=qty, cum=cum, extra_margin=extra
    )
    wallet = entry * qty / leverage + extra
    margin_balance = wallet + (price - entry) * qty
    maintenance = price * qty * mmr - cum
    assert price < entry
    assert margin_balance == pytest.approx(maintenance)


def test_binance_short_margin_balance_equals_maintenance_margin() -> None:
    entry, qty, leverage, mmr, cum = 26_000.0, 10.0, 20.0, 0.01, 1_300.0
    price = oracle.liq_price(entry, leverage, "short", mmr, qty=qty, cum=cum)
    wallet = entry * qty / leverage
    margin_balance = wallet + (entry - price) * qty
    maintenance = price * qty * mmr - cum
    assert price > entry
    assert margin_balance == pytest.approx(maintenance)


def test_okx_long_ratio_hits_one_at_the_estimated_price() -> None:
    """OKX liquidation is where (margin + PnL) / (maint + fee) = 1."""
    entry, qty, leverage, mmr, fee = 100.0, 1.0, 10.0, 0.005, 0.0005
    price = oracle.liq_price(entry, leverage, "long", mmr, qty=qty, fee=fee, venue="okx")
    margin = entry * qty / leverage
    equity = margin + (price - entry) * qty
    requirement = price * qty * (mmr + fee)
    assert price == pytest.approx(90.4977375566)
    assert equity == pytest.approx(requirement)


def test_okx_short_documented_algebra() -> None:
    entry, qty, leverage, mmr, fee = 100.0, 1.0, 10.0, 0.005, 0.0005
    price = oracle.liq_price(
        entry, leverage, "short", mmr, qty=qty, fee=fee, venue="okx"
    )
    margin = entry * qty / leverage
    # (margin + qty * entry) / (qty * (mmr + fee + 1))
    expected = (margin + qty * entry) / (qty * (mmr + fee + 1.0))
    assert price == pytest.approx(expected)
    assert price == pytest.approx(109.3983092989)


@pytest.mark.parametrize("side", ["long", "short"])
def test_bracket_boundary_is_continuous(side: str, price_of: PriceFn) -> None:
    """Adjacent brackets that share a maintenance-margin value share a liq price.

    cum_2 = cum_1 + floor * (mmr_2 - mmr_1) is the deduction that makes
    ``notional * mmr - cum`` continuous at the tier floor. The position is
    sized so the liquidation notional is that floor.
    """
    floor = 50_000.0
    mmr_1, cum_1 = 0.004, 0.0
    mmr_2 = 0.005
    cum_2 = cum_1 + floor * (mmr_2 - mmr_1)
    qty = 2.0
    leverage = 10.0
    price_at_floor = floor / qty
    if side == "long":
        entry = (price_at_floor * qty * (1.0 - mmr_1) + cum_1) / (
            qty * (1.0 - 1.0 / leverage)
        )
    else:
        entry = (price_at_floor * qty * (1.0 + mmr_1) - cum_1) / (
            qty * (1.0 + 1.0 / leverage)
        )
    first = price_of(entry, leverage, side, mmr_1, qty=qty, cum=cum_1, venue="binance")
    assert first == pytest.approx(price_at_floor, rel=1e-9, abs=1e-6)
    second = price_of(entry, leverage, side, mmr_2, qty=qty, cum=cum_2, venue="binance")
    assert second == pytest.approx(first, rel=1e-9, abs=1e-6)


def test_positive_cum_moves_long_farther_down_and_short_farther_up(
    price_of: PriceFn,
) -> None:
    common = {"qty": 1.5, "venue": "binance"}
    long_flat = price_of(ENTRY, 20, "long", 0.005, cum=0, **common)
    short_flat = price_of(ENTRY, 20, "short", 0.005, cum=0, **common)
    assert long_flat < ENTRY < short_flat
    long_tier = price_of(ENTRY, 20, "long", 0.005, cum=80, **common)
    short_tier = price_of(ENTRY, 20, "short", 0.005, cum=80, **common)
    assert long_tier < long_flat
    assert short_tier > short_flat


@pytest.mark.parametrize(
    ("leverage", "side", "mmr", "message"),
    [
        (1, "long", 0.004, "leverage"),
        (10, "both", 0.004, "side"),
        (10, "long", 1.0, "mmr"),
        (10, "long", -0.01, "mmr"),
    ],
)
def test_oracle_rejects_bad_inputs(
    leverage: float, side: str, mmr: float, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        oracle.liq_price(100.0, leverage, side, mmr)


def test_oracle_rejects_nonpositive_qty_and_unknown_venue() -> None:
    with pytest.raises(ValueError, match="qty"):
        oracle.liq_price(100, 10, "long", 0.004, qty=0)
    with pytest.raises(ValueError, match="venue"):
        oracle.liq_price(100, 10, "long", 0.004, venue="coin" + "glass")


def test_legacy_helpers_return_non_finite_values() -> None:
    """Prove the current helpers do not raise on non-finite input."""
    source = adapter.binding_status()["liq_price"]
    if not source.startswith("legacy:"):
        pytest.skip("liq_price is bound; non-finite input must raise ValueError")
    import importlib
    import math
    from typing import Any, cast

    heatmap = cast(Any, importlib.import_module("heatmap"))
    assert math.isnan(heatmap.liq_price_long(float("nan"), 10, 0.005))
    assert math.isinf(heatmap.liq_price_long(float("inf"), 10, 0.005))
    assert math.isnan(heatmap.liq_price_short(float("nan"), 10, 0.005))
    assert math.isinf(heatmap.liq_price_short(float("inf"), 10, 0.005))


def test_model_rejects_bad_inputs_once_full_contract_is_bound() -> None:
    """``liq_price`` must raise once it is exported.

    The legacy helpers do not. ``liq_price_long(nan, ...)`` returns ``nan``
    and ``liq_price_long(inf, ...)`` returns ``inf``. They do not raise.
    """
    source = adapter.binding_status()["liq_price"]
    if source.startswith("legacy:"):
        pytest.skip(
            "legacy heatmap.liq_price_long/liq_price_short return nan or inf "
            "for non-finite input and do not raise ValueError; "
            f"current binding is {source}. Rejection starts once "
            "LIQ_PRICE_CANDIDATES resolves to liq_price."
        )
    with pytest.raises(ValueError):
        adapter.liq_price(100, 0, "long", 0.004)
    with pytest.raises(ValueError):
        adapter.liq_price(float("nan"), 10, "long", 0.004)
    with pytest.raises(ValueError):
        adapter.liq_price(100, 10, "long", 0.004, venue="coin" + "glass")
