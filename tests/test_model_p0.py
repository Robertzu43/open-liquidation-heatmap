"""Behaviors the original single-file heatmap got wrong.

The original builder differenced USD open interest, painted a level on the
entry candle only, and dropped anything outside 0.95*low .. 1.05*high. A
calm 10x long (about 90.45 when entry is 100) disappeared. These tests
build the heatmap.v1 cube directly and would have failed on that builder.
"""

import numpy as np
import pandas as pd
import pytest

from olh.features.align import align_book
from olh.models import get_model
from olh.models.brackets import brackets_for, mmr_for_leverage
from olh.models.leverage import build_leverage_weights, resolve_ladder
from olh.models.mathutil import liq_price_long


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    return frame


def _build(frame: pd.DataFrame, **overrides) -> dict:
    config = {
        "schedule": brackets_for("BTCUSDT"),
        "profile": "lognormal",
        "ladder": "full",
        "leverages": [10],
        "mmr": 0.005,
        "mmr_mode": "constant",
        "fade": True,
        "decay": 0.0,
        "age_half_life_bars": None,
        "bin_mode": "percent",
        "bin_size": 0.1,
        "split": "even",
    }
    config.update(overrides)
    return get_model().build([("okx", frame, 125.0)], config)


def _at(contract: dict, when: str) -> list[dict]:
    return [cell for cell in contract["cells"] if cell["t"] == when]


def test_calm_10x_level_is_kept_and_notional_is_conserved():
    # low/high sit inside a 1% band. 0.95*99.5 = 94.525, so 90.45 was deleted.
    t0 = "2026-10-25T00:00:00+00:00"
    t1 = "2026-10-25T00:15:00+00:00"
    frame = _frame(
        [
            dict(open_time=t0, open=100, high=100.2, low=99.5, close=100, mark=100, oi_contracts=100, oi_usd=10000, oi_delta=np.nan),
            dict(open_time=t1, open=100, high=100.2, low=99.5, close=100, mark=100, oi_contracts=110, oi_usd=11000, oi_delta=10),
        ]
    )
    contract = _build(frame)
    placed = contract["metadata"]["notional_placed"]
    birth = _at(contract, t1)
    assert birth
    assert sum(cell["notional"] for cell in birth) == pytest.approx(placed)
    assert contract["metadata"]["notional_outside_grid"] == pytest.approx(0, abs=1e-6)
    weight = build_leverage_weights(resolve_ladder("full", 125), "lognormal")[10]
    assert placed == pytest.approx(10 * 100 * weight)
    assert placed < 10 * 100 * 0.5
    centers = contract["axes"]["price"]
    long_center = centers[next(cell["p"] for cell in birth if cell["side"] == "long")]
    assert long_center == pytest.approx(liq_price_long(100, 10, 0.005), abs=0.15)
    assert long_center < 94.525
    assert contract["axes"]["price_edges"][0] < 91
    n_price = len(contract["axes"]["price"])
    n_time = len(contract["axes"]["time"])
    assert n_price * n_time <= 150_000


def test_usd_open_interest_does_not_open_a_position_when_contracts_are_flat():
    t0 = "2026-10-25T00:00:00+00:00"
    t1 = "2026-10-25T00:15:00+00:00"
    frame = _frame(
        [
            dict(open_time=t0, open=100, high=100, low=100, close=100, mark=100, oi_contracts=50, oi_usd=5000, oi_delta=np.nan),
            dict(open_time=t1, open=110, high=112, low=109, close=111, mark=111, oi_contracts=50, oi_usd=9000, oi_delta=0),
        ]
    )
    contract = _build(frame)
    assert contract["metadata"]["notional_placed"] == 0
    assert contract["cells"] == []


def test_bands_persist_until_a_sweep_and_the_birth_wick_does_not_clear_them():
    t0 = "2026-10-25T00:00:00+00:00"
    t1 = "2026-10-25T00:15:00+00:00"
    t2 = "2026-10-25T00:30:00+00:00"
    t3 = "2026-10-25T00:45:00+00:00"
    # The birth candle trades down through 90. The level is still opened,
    # because a new band is not swept by the candle that created it.
    frame = _frame(
        [
            dict(open_time=t0, open=100, high=101, low=99, close=100, mark=100, oi_contracts=10, oi_usd=1000, oi_delta=np.nan),
            dict(open_time=t1, open=100, high=101, low=80, close=100, mark=100, oi_contracts=20, oi_usd=2000, oi_delta=10),
            dict(open_time=t2, open=100, high=101, low=99, close=100, mark=100, oi_contracts=20, oi_usd=2000, oi_delta=0),
            dict(open_time=t3, open=100, high=101, low=80, close=90, mark=90, oi_contracts=20, oi_usd=1800, oi_delta=0),
        ]
    )
    contract = _build(frame, leverages=[10], ladder="standard")
    placed = contract["metadata"]["notional_placed"]
    assert sum(cell["notional"] for cell in _at(contract, t1)) == pytest.approx(placed)
    assert sum(cell["notional"] for cell in _at(contract, t2)) == pytest.approx(placed)
    # Sweep candle keeps full intensity, then decay 0 removes the band.
    assert sum(cell["notional"] for cell in _at(contract, t3)) == pytest.approx(placed)
    assert contract["axes"]["time"][-1] == t3


def test_sweep_removes_the_band_on_the_following_candle():
    t0 = "2026-10-25T00:00:00+00:00"
    t1 = "2026-10-25T00:15:00+00:00"
    t2 = "2026-10-25T00:30:00+00:00"
    t3 = "2026-10-25T00:45:00+00:00"
    frame = _frame(
        [
            dict(open_time=t0, open=100, high=100, low=99, close=100, mark=100, oi_contracts=10, oi_usd=1, oi_delta=np.nan),
            dict(open_time=t1, open=100, high=100, low=99, close=100, mark=100, oi_contracts=12, oi_usd=1, oi_delta=2),
            dict(open_time=t2, open=95, high=120, low=80, close=95, mark=95, oi_contracts=12, oi_usd=1, oi_delta=0),
            dict(open_time=t3, open=95, high=96, low=94, close=95, mark=95, oi_contracts=12, oi_usd=1, oi_delta=0),
        ]
    )
    contract = _build(frame, leverages=[10], ladder="standard")
    assert _at(contract, t2)
    assert _at(contract, t3) == []


def test_open_interest_decrease_scales_the_tracked_book():
    t0 = "2026-10-25T00:00:00+00:00"
    t1 = "2026-10-25T00:15:00+00:00"
    t2 = "2026-10-25T00:30:00+00:00"
    frame = _frame(
        [
            dict(open_time=t0, open=100, high=100, low=99, close=100, mark=100, oi_contracts=100, oi_usd=1, oi_delta=np.nan),
            dict(open_time=t1, open=100, high=100, low=99, close=100, mark=100, oi_contracts=160, oi_usd=1, oi_delta=60),
            dict(open_time=t2, open=100, high=100, low=99, close=100, mark=100, oi_contracts=100, oi_usd=1, oi_delta=-60),
        ]
    )
    contract = _build(frame, leverages=[10], ladder="standard")
    placed = contract["metadata"]["notional_placed"]
    assert sum(cell["notional"] for cell in _at(contract, t2)) == pytest.approx(placed * 100 / 160)


def test_age_decay_is_off_by_default_and_halves_when_requested():
    t0 = "2026-10-25T00:00:00+00:00"
    t1 = "2026-10-25T00:15:00+00:00"
    t2 = "2026-10-25T00:30:00+00:00"
    rows = [
        dict(open_time=t0, open=100, high=100, low=99, close=100, mark=100, oi_contracts=10, oi_usd=1, oi_delta=np.nan),
        dict(open_time=t1, open=100, high=100, low=99, close=100, mark=100, oi_contracts=20, oi_usd=1, oi_delta=10),
        dict(open_time=t2, open=100, high=100, low=99, close=100, mark=100, oi_contracts=20, oi_usd=1, oi_delta=0),
    ]
    held = _build(_frame(rows), leverages=[10], ladder="standard")
    placed = held["metadata"]["notional_placed"]
    assert sum(cell["notional"] for cell in _at(held, t2)) == pytest.approx(placed)
    aged = _build(_frame(rows), leverages=[10], ladder="standard", age_half_life_bars=1)
    assert sum(cell["notional"] for cell in _at(aged, t2)) == pytest.approx(placed * 0.5)


def test_entry_is_the_same_candle_and_mark_wins_over_close():
    start = pd.Timestamp("2026-10-25T00:00:00Z")
    bar = pd.Timedelta(minutes=15)
    candles = pd.DataFrame(
        {
            "open_time": [start, start + bar, start + 2 * bar],
            "open": [100, 110, 999],
            "high": [101, 111, 1000],
            "low": [99, 109, 998],
            "close": [100, 110, 999],
            "mark": [np.nan, 80.0, 999.0],
        }
    )
    oi = pd.DataFrame(
        {
            "timestamp": [start, start + bar],
            "oi_contracts": [10.0, 12.0],
            "oi_usd": [1000.0, 5000.0],
        }
    )
    aligned = align_book(candles, oi, bar)
    opened = aligned.loc[aligned["open_time"] == start + bar].iloc[0]
    assert opened["oi_delta"] == pytest.approx(2)
    assert opened["close"] == 110
    contract = _build(aligned, leverages=[10], ladder="standard", mmr_mode="constant", mmr=0.005)
    expected = liq_price_long(80, 10, 0.005)
    not_close = liq_price_long(110, 10, 0.005)
    not_future = liq_price_long(999, 10, 0.005)
    centers = contract["axes"]["price"]
    long_centers = [
        centers[cell["p"]]
        for cell in contract["cells"]
        if cell["side"] == "long" and cell["leverage"] == 10
    ]
    assert long_centers
    assert min(abs(center - expected) for center in long_centers) < 0.15
    assert min(abs(center - not_close) for center in long_centers) > 1
    assert min(abs(center - not_future) for center in long_centers) > 1


def test_default_btc_10x_uses_the_lowest_notional_maintenance_tier():
    """Default bracket mode prices a typical BTC 10x position on the first tier.

    The bundled schedule's first band is 0.40% maintenance, not the 5% band
    that only applies once notional is in the millions.
    """
    schedule = brackets_for("BTCUSDT")
    mmr, cum = mmr_for_leverage(10, schedule, "bracket", None)
    assert mmr == schedule.brackets[0].mmr == 0.004
    assert cum == 0
    expected = liq_price_long(100, 10, mmr)
    assert expected == pytest.approx(90.361445783, rel=1e-9)
    t0 = "2026-10-25T00:00:00+00:00"
    t1 = "2026-10-25T00:15:00+00:00"
    frame = _frame(
        [
            dict(open_time=t0, open=100, high=100.2, low=99.5, close=100, mark=100, oi_contracts=100, oi_usd=10000, oi_delta=np.nan),
            dict(open_time=t1, open=100, high=100.2, low=99.5, close=100, mark=100, oi_contracts=110, oi_usd=11000, oi_delta=10),
        ]
    )
    contract = _build(
        frame,
        mmr_mode="bracket",
        mmr=None,
        assumed_qty=None,
        leverages=[10],
        ladder="standard",
        bin_mode="absolute",
        bin_size=0.02,
    )
    birth = [cell for cell in _at(contract, t1) if cell["side"] == "long" and cell["leverage"] == 10]
    assert birth
    center = contract["axes"]["price"][birth[0]["p"]]
    assert center == pytest.approx(expected, abs=0.02)


def test_assumed_quantity_moves_the_tier_and_caps_leverage_at_that_maximum():
    # entry 100 and qty 60_000 is 6_000_000 notional: BTC max leverage 10, mmr 5%, cum 141300.
    schedule = brackets_for("BTCUSDT")
    mmr, cum = mmr_for_leverage(25, schedule, "bracket", None, notional=6_000_000)
    assert mmr == 0.05
    assert cum == 141300
    t0 = "2026-10-25T00:00:00+00:00"
    t1 = "2026-10-25T00:15:00+00:00"
    frame = _frame(
        [
            dict(open_time=t0, open=100, high=101, low=99, close=100, mark=100, oi_contracts=10, oi_usd=1000, oi_delta=np.nan),
            dict(open_time=t1, open=100, high=101, low=99, close=100, mark=100, oi_contracts=20, oi_usd=2000, oi_delta=10),
        ]
    )
    contract = _build(
        frame,
        mmr_mode="bracket",
        mmr=None,
        assumed_qty=60_000,
        leverages=[10, 25],
        ladder="standard",
        bin_mode="absolute",
        bin_size=0.02,
    )
    assert contract["cells"]
    assert max(cell["leverage"] for cell in contract["cells"]) <= 10
    expected = liq_price_long(100, 10, mmr, cum, 60_000)
    center = contract["axes"]["price"][next(cell["p"] for cell in _at(contract, t1) if cell["side"] == "long")]
    assert center == pytest.approx(expected, abs=0.02)
    weights = build_leverage_weights(resolve_ladder("standard", 125), "lognormal")
    kept = {lev: weight for lev, weight in weights.items() if lev in (10.0, 25.0)}
    assert contract["metadata"]["notional_placed"] == pytest.approx(10 * 100 * sum(kept.values()))
