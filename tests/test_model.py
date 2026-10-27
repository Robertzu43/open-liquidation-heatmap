"""Model invariants and pure data helpers. No network."""

from __future__ import annotations

import base64
import unittest
from unittest import mock

import numpy as np

from liqmap import data
from liqmap.data import (
    DataError,
    combine_series,
    fold_candles,
    _parse_metrics_csv,
    fetch_okx,
    fold_oi,
    is_stale,
    make_grid,
    normalize_symbol,
    range_spec,
    ratio_to_share,
)
from liqmap.model import (
    MODELS,
    MarketSeries,
    bin_intensity,
    build_heatmap,
    fade_swept,
    liq_price,
    liq_price_long,
    liq_price_short,
)
from liqmap.service import load_heatmap


def _series(price, oi, share=0.5, high=None, low=None, coin="BTC"):
    price = np.asarray(price, dtype=np.float64)
    n = len(price)
    if high is None:
        high = price + 0.05
    if low is None:
        low = price - 0.05
    share_arr = np.full(n, share, dtype=np.float64) if np.isscalar(share) else np.asarray(share, dtype=np.float64)
    return MarketSeries(
        times=np.arange(n, dtype=np.int64) * 300_000,
        open=price,
        high=np.asarray(high, dtype=np.float64),
        low=np.asarray(low, dtype=np.float64),
        close=price,
        volume_usd=np.zeros(n),
        oi_usd=np.asarray(oi, dtype=np.float64),
        long_share=share_arr,
        exchange="test",
        symbol=f"{coin}USDT",
        coin=coin,
        bar_seconds=300,
    )


class ModelTests(unittest.TestCase):
    def test_weight_sums(self):
        for spec in MODELS.values():
            self.assertAlmostEqual(sum(weight for _lev, weight in spec["weights"]), 1.0)
            self.assertAlmostEqual(sum(spec["kernel"]), 1.0)
            self.assertGreater(len(spec["weights"]), 0)

    def test_zero_tier_golden_and_non_finite(self):
        self.assertAlmostEqual(liq_price_long(100, 10, 0.005), 90.4522613065, places=4)
        self.assertAlmostEqual(liq_price_short(100, 10, 0.005), 109.4527363184, places=4)
        self.assertAlmostEqual(liq_price(100, 10, "long", 0.005), 90.4522613065, places=4)
        with self.assertRaisesRegex(ValueError, "inputs must be finite"):
            liq_price(float("nan"), 10, "long", 0.005)
        with self.assertRaisesRegex(ValueError, "inputs must be finite"):
            liq_price(100, float("inf"), "short", 0.005)

    def test_fade_and_bins(self):
        kept = fade_swept([(90.0, 5.0, "long"), (110.0, 4.0, "short")], [(100.0, 95.0)])
        self.assertEqual(kept, [(90.0, 5.0, "long"), (110.0, 4.0, "short")])
        swept = fade_swept([(90.0, 5.0, "long"), (110.0, 4.0, "short")], [(112.0, 89.0)])
        self.assertEqual(swept, [])
        self.assertEqual(bin_intensity([1.0, 2.0, 3.0], [10.0, 1.0, 4.0], [0.0, 2.0, 3.0]), [10.0, 5.0])

    def test_open_interest_joins_the_nearest_candle_open(self):
        grid = np.array([0, 3_600_000, 7_200_000], dtype=np.int64)
        early, _share, _base = fold_oi([(600_000, 100.0, 0.5, 2.0)], grid, 3_600_000)
        self.assertEqual(early[0], 100.0)
        self.assertTrue(np.isnan(early[1]))
        later, _share, later_base = fold_oi([(3_000_000, 600.0, 0.5, 12.0)], grid, 3_600_000)
        self.assertTrue(np.isnan(later[0]))
        self.assertEqual(later[1], 600.0)
        self.assertEqual(later_base[1], 12.0)

    def test_price_move_with_flat_contracts_adds_nothing(self):
        price = np.array([100.0, 105.0, 105.0])
        base = np.full(3, 10_000.0)
        s = _series(price, base * price)  # USD OI rises 5% with flat contracts
        s.oi_base = base
        heat = build_heatmap(s, 1)
        self.assertAlmostEqual(float(heat.profile_long.sum() + heat.profile_short.sum()), 0.0, delta=1.0)

    def test_zero_oi_print_is_missing_not_a_close(self):
        price = np.full(4, 100.0)
        base = np.array([100.0, 200.0, 0.0, 200.0])

        def total(n):
            s = _series(price[:n], base[:n] * price[:n])
            s.oi_base = base[:n]
            heat = build_heatmap(s, 1)
            return float(heat.long_heat[:, n - 1].sum() + heat.short_heat[:, n - 1].sum())

        self.assertGreater(total(2), 0.0)
        self.assertAlmostEqual(total(4), total(2), delta=total(2) * 0.01)

    def test_missing_coin_column_falls_back_to_usd_and_warns(self):
        price = np.array([100.0, 100.0, 100.0])
        s = _series(price, np.array([1e6, 2e6, 3e6]))
        self.assertIsNone(s.oi_base)
        heat = build_heatmap(s, 1)
        total = float(heat.profile_long.sum() + heat.profile_short.sum())
        self.assertAlmostEqual(total, 2e6, delta=2e6 * 0.01)
        self.assertEqual(s.warnings.count("Open interest in coin units was unavailable; using USD value."), 1)
        build_heatmap(s, 1)
        self.assertEqual(len(s.warnings), 1)

    def test_metrics_csv_reads_coin_open_interest(self):
        text = (
            "create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
            "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
            "2021-05-19 00:00:00,BTCUSDT,1000,40000000,1.5,1.2,1.0,1.0\n"
        )
        rows = _parse_metrics_csv(text)
        self.assertEqual(len(rows), 1)
        ts, usd, share, coins = rows[0]
        self.assertEqual((ts, usd, coins), (1621382400000, 40000000.0, 1000.0))
        self.assertAlmostEqual(share, 0.5)

    def test_okx_open_interest_row_uses_coin_column(self):
        grid = data._window(range_spec("12h"))
        start = int(grid[0])
        step = 300_000
        candles = [[str(start + i * step), "100", "101", "99", "100", "1", "1", "1", "1"] for i in range(len(grid))]
        row = [str(start + 5 * step), "123456", "1234.5", "45000000"]

        def pages(path, params, *args):
            if "open-interest-history" in path:
                return [row, [str(start + 6 * step), "123457", "1235.5", "45100000"]]
            if "candles" in path:
                return candles
            return []

        with mock.patch.object(data, "_okx_pages", side_effect=pages):
            loaded = fetch_okx("BTC", "BTCUSDT", range_spec("12h"))
        series = loaded.series
        i = int(np.flatnonzero(series.times == start + 5 * step)[0])
        self.assertEqual(float(series.oi_base[i]), 1234.5)
        self.assertEqual(float(series.oi_usd[i]), 45000000.0)

    def test_aggregate_sums_coin_units(self):
        grid = np.array([0, 3_600_000, 7_200_000], dtype=np.int64)

        def part(base, exchange):
            close = np.full(3, 100.0)
            base = None if base is None else np.full(3, base, dtype=np.float64)
            return MarketSeries(
                times=grid.copy(), open=close, high=close + 1, low=close - 1, close=close,
                volume_usd=np.ones(3), oi_usd=np.full(3, 1e4 if base is None else base[0] * 100.0),
                long_share=np.full(3, 0.5), exchange=exchange, symbol="BTCUSDT", coin="BTC",
                bar_seconds=3600, oi_base=base,
            )

        combined = combine_series([part(100.0, "okx"), part(50.0, "binance")], grid, 3600, "BTC")
        self.assertEqual(list(combined.oi_base), [150.0, 150.0, 150.0])
        # A venue without coin units must not be mixed with the others' coins.
        mixed = combine_series([part(100.0, "okx"), part(None, "binance")], grid, 3600, "BTC")
        self.assertTrue(np.isnan(mixed.oi_base).all())

    def test_liq_price_direction(self):
        entry = 100.0
        self.assertLess(liq_price_long(entry, 10), entry)
        self.assertGreater(liq_price_short(entry, 10), entry)
        # Higher leverage sits closer to the entry.
        self.assertGreater(liq_price_long(entry, 50), liq_price_long(entry, 10))
        self.assertLess(liq_price_short(entry, 50), liq_price_short(entry, 10))
        self.assertLess(liq_price_long(entry, 100), entry)
        self.assertGreater(liq_price_short(entry, 5), liq_price_short(entry, 100))

    def test_usd_conservation_when_price_cannot_sweep(self):
        oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0, 2_000_000.0])
        heat = build_heatmap(_series(np.full(4, 100.0), oi), 1)
        self.assertAlmostEqual(float(heat.long_heat[:, 1].sum() + heat.short_heat[:, 1].sum()), 1_000_000.0, delta=1.0)
        self.assertAlmostEqual(float(heat.profile_long.sum() + heat.profile_short.sum()), 1_000_000.0, delta=1.0)
        self.assertAlmostEqual(float(heat.profile_long.sum()), 500_000.0, delta=1.0)
        self.assertAlmostEqual(float(heat.profile_short.sum()), 500_000.0, delta=1.0)

    def test_sweep_removes_shorts_after_price_trades_through(self):
        price = np.array([100.0, 100.0, 100.0, 120.0, 120.0])
        high = np.array([100.05, 100.05, 100.05, 140.0, 130.0])
        low = np.array([99.95, 99.95, 99.95, 110.0, 110.0])
        oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0, 2_000_000.0, 2_000_000.0])
        heat = build_heatmap(_series(price, oi, high=high, low=low), 1)
        self.assertGreater(float(heat.short_heat[:, 1].sum()), 100_000.0)
        self.assertAlmostEqual(float(heat.short_heat[:, 4].sum()), 0.0, delta=1.0)
        self.assertAlmostEqual(float(heat.profile_short.sum()), 0.0, delta=1.0)
        self.assertGreater(float(heat.profile_long.sum()), 100_000.0)

    def test_model3_flow_skew(self):
        oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0])
        price = np.full(3, 100.0)
        bull = build_heatmap(_series(price, oi, share=0.9), 3)
        bear = build_heatmap(_series(price, oi, share=0.1), 3)
        self.assertGreater(float(bull.profile_long.sum()), float(bull.profile_short.sum()) * 2)
        self.assertGreater(float(bear.profile_short.sum()), float(bear.profile_long.sum()) * 2)
        self.assertGreater(float(bull.profile_long.sum()), float(bear.profile_long.sum()))

    def test_analyze_finds_shelves(self):
        oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0, 2_000_000.0])
        heat = build_heatmap(_series(np.full(4, 100.0), oi, coin="BTC"), 1)
        analysis = heat.analysis
        self.assertIsNotNone(analysis["above"])
        self.assertIsNotNone(analysis["below"])
        self.assertEqual(analysis["above"]["side"], "short")
        self.assertEqual(analysis["below"]["side"], "long")
        self.assertGreater(analysis["above"]["price"], analysis["price"])
        self.assertLess(analysis["below"]["price"], analysis["price"])
        self.assertGreater(len(analysis["levels"]), 0)
        self.assertEqual([row["window_pct"] for row in analysis["imbalance"]], [1, 2, 5, 10])
        self.assertIn("BTC", analysis["summary"])

    def test_levels_survive_the_bar_that_opened_them(self):
        # OI rises print0->print1, so positions opened during flat bar 0 (about 100).
        # Bar 1 rallies; its low of 103.5 must not sweep 60x longs opened at 100.
        price = np.array([100.0, 105.0, 105.0])
        high = np.array([100.05, 105.05, 105.05])
        low = np.array([99.95, 103.5, 104.95])
        base = np.array([10_000.0, 20_000.0, 20_000.0])  # contracts, so the rally adds nothing
        s = _series(price, base * price, high=high, low=low)
        s.oi_base = base
        heat = build_heatmap(s, 1)
        self.assertGreater(float(heat.profile_long_tiers[3].sum()), 0.0)  # 60x tier kept
        self.assertAlmostEqual(float(heat.long_heat[:, 2].sum()), 500_000.0, delta=5_000)

    def test_entry_falls_back_to_previous_close_when_range_missing(self):
        price = np.array([100.0, 110.0, 110.0])
        high = np.array([np.nan, 110.05, 110.05])
        low = np.array([97.0, 109.95, 109.95])
        oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0])
        heat = build_heatmap(_series(price, oi, high=high, low=low), 1)
        expected = liq_price_long(100.0, 5)
        peak = heat.centers[np.argmax(heat.profile_long_tiers[0])]
        self.assertLess(abs(peak - expected) / expected, 0.01)

    def test_entry_is_previous_bar_typical_price(self):
        price = np.array([100.0, 110.0, 110.0])
        high = np.array([102.0, 110.05, 110.05])
        low = np.array([97.0, 109.95, 109.95])
        oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0])
        heat = build_heatmap(_series(price, oi, high=high, low=low), 1)
        typical = (102.0 + 97.0 + 100.0) / 3
        expected = liq_price_long(typical, 5)
        peak = heat.centers[np.argmax(heat.profile_long_tiers[0])]
        self.assertLess(abs(peak - expected) / expected, 0.01)

    def test_fade_depends_on_time_not_bar_count(self):
        # One deposit at loop step 1, then the fade runs at the end of steps 1..n-1.
        # 288 steps of 5m and 24 steps of 1h are both exactly 24 hours.
        def remaining(n, bar_seconds):
            oi = np.full(n, 2_000_000.0)
            oi[0] = 1_000_000.0
            s = _series(np.full(n, 100.0), oi)
            s.bar_seconds = bar_seconds
            heat = build_heatmap(s, 2)
            return float(heat.profile_long.sum() + heat.profile_short.sum())

        five_minute = remaining(289, 300)
        hourly = remaining(25, 3600)
        expected = 1_000_000.0 * 0.5 ** (24 / 72)
        self.assertAlmostEqual(five_minute / hourly, 1.0, delta=0.01)
        self.assertAlmostEqual(five_minute / expected, 1.0, delta=0.01)
        self.assertAlmostEqual(hourly / expected, 1.0, delta=0.01)

    def test_wick_into_a_bin_removes_only_the_crossed_part(self):
        price = np.full(3, 100.0)
        oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0])
        # Bar 0 pins the grid (its low and high set the padded range) in both runs.
        anchor_low = np.array([95.0, 99.95, 99.95])
        base = build_heatmap(_series(price, oi, low=anchor_low), 1)
        tier = 3
        k = int(np.argmax(base.profile_long_tiers[tier]))
        width = float(base.centers[1] - base.centers[0])
        lo_edge = float(base.centers[k]) - width / 2
        low = np.array([95.0, 99.95, lo_edge + 0.25 * width])
        cut = build_heatmap(_series(price, oi, low=low), 1)
        before = base.profile_long_tiers[tier]
        after = cut.profile_long_tiers[tier]
        self.assertAlmostEqual(float(after[k]), 0.25 * float(before[k]), delta=1e-6 * float(before[k]))
        self.assertTrue(np.all(after[k + 1 :] == 0.0))
        np.testing.assert_allclose(after[:k], before[:k])

    def test_wick_into_a_bin_removes_only_the_crossed_part_for_shorts(self):
        price = np.full(3, 100.0)
        oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0])
        anchor_high = np.array([105.0, 100.05, 100.05])
        base = build_heatmap(_series(price, oi, high=anchor_high), 1)
        tier = 3
        k = int(np.argmax(base.profile_short_tiers[tier]))
        width = float(base.centers[1] - base.centers[0])
        hi_edge = float(base.centers[k]) + width / 2
        high = np.array([105.0, 100.05, hi_edge - 0.25 * width])
        cut = build_heatmap(_series(price, oi, high=high), 1)
        before = base.profile_short_tiers[tier]
        after = cut.profile_short_tiers[tier]
        self.assertAlmostEqual(float(after[k]), 0.25 * float(before[k]), delta=1e-6 * float(before[k]))
        self.assertTrue(np.all(after[:k] == 0.0))
        np.testing.assert_allclose(after[k + 1 :], before[k + 1 :])


class TierTests(unittest.TestCase):
    def test_tier_books_sum_to_totals(self):
        rng = np.random.default_rng(7)
        price = 100 + np.cumsum(rng.normal(0, 0.4, 200))
        oi = 1_000_000 + np.cumsum(rng.normal(0, 20_000, 200))
        share = np.clip(0.5 + rng.normal(0, 0.1, 200), 0.05, 0.95)
        series = _series(price, oi, share=share, high=price + 0.6, low=price - 0.6)
        series.volume_usd = np.abs(rng.normal(0, 50_000, 200))
        for model_id, spec in MODELS.items():
            heat = build_heatmap(series, model_id)
            self.assertEqual(heat.tiers, tuple(lev for lev, _w in spec["weights"]))
            self.assertIsNone(heat.long_tier_column)
            np.testing.assert_allclose(heat.profile_long_tiers.sum(axis=0), heat.profile_long, rtol=1e-9, atol=1e-6)
            np.testing.assert_allclose(heat.profile_short_tiers.sum(axis=0), heat.profile_short, rtol=1e-9, atol=1e-6)
            for column in (0, 57, 199):
                one = build_heatmap(series, model_id, tier_column=column)
                self.assertEqual(one.long_tier_column.shape, (len(one.tiers), len(one.centers)))
                np.testing.assert_allclose(one.long_tier_column.sum(axis=0), heat.long_heat[:, column], rtol=1e-9, atol=1e-6)
                np.testing.assert_allclose(one.short_tier_column.sum(axis=0), heat.short_heat[:, column], rtol=1e-9, atol=1e-6)
            self.assertGreater(float(heat.long_heat.sum()), 0)


class ParserTests(unittest.TestCase):
    def test_normalize_symbol(self):
        self.assertEqual(normalize_symbol("btc"), ("BTC", "BTCUSDT"))
        self.assertEqual(normalize_symbol("ETHUSDT"), ("ETH", "ETHUSDT"))
        self.assertEqual(normalize_symbol("sol-usdt"), ("SOL", "SOLUSDT"))
        self.assertEqual(normalize_symbol("XRP/USDT"), ("XRP", "XRPUSDT"))
        for bad in ("", "A", "../etc", "BTC USDT", "http://evil", "USDT", "javascript:alert"):
            with self.assertRaises(DataError):
                normalize_symbol(bad)

    def test_range_spec_and_grid(self):
        spec = range_spec("24H")
        self.assertEqual(spec["bar"], "5m")
        self.assertEqual(spec["bar_seconds"], 300)
        self.assertEqual(range_spec("180d")["bar"], "12h")
        self.assertEqual(range_spec("1y")["bar"], "1d")
        self.assertEqual(range_spec("2y")["bar"], "1d")
        with self.assertRaises(DataError):
            range_spec("2h")
        grid = make_grid(1_000_000, 4, 300)
        self.assertEqual(len(grid), 4)
        self.assertEqual(int(grid[-1]), 1_000_000 * 1000)
        self.assertEqual(int(grid[1] - grid[0]), 300_000)

    def test_stale_and_ratio(self):
        now = 1_800_000_000
        self.assertFalse(is_stale(int((now - 60) * 1000), 300, now_s=now))
        self.assertTrue(is_stale(int((now - 2 * 86400) * 1000), 300, now_s=now))
        # A daily bar that opened 20h ago still covers the present.
        self.assertFalse(is_stale(int((now - 20 * 3600) * 1000), 86400, now_s=now))
        self.assertAlmostEqual(ratio_to_share(1), 0.5)
        self.assertAlmostEqual(ratio_to_share(3), 0.75)
        self.assertTrue(np.isnan(ratio_to_share(0)))
        self.assertTrue(np.isnan(ratio_to_share("nope")))

    def test_fold_candles(self):
        grid = np.array([0, 3_600_000], dtype=np.int64)
        records = [
            (0, 10, 12, 9, 11, 5),
            (1_000_000, 11, 14, 8, 13, 7),
            (3_600_000, 13, 15, 12, 14, 3),
            (9_000_000, 1, 1, 1, 1, 9),
        ]
        open_, high, low, close, volume = fold_candles(records, grid, 3_600_000)
        self.assertEqual(open_[0], 10)
        self.assertEqual(high[0], 14)
        self.assertEqual(low[0], 8)
        self.assertEqual(close[0], 13)
        self.assertEqual(volume[0], 12)
        self.assertEqual(close[1], 14)
        self.assertEqual(volume[1], 3)

    def test_combine_sums_oi_and_keeps_busiest_price(self):
        grid = np.array([0, 3_600_000, 7_200_000], dtype=np.int64)

        def part(close, oi, vol, share, exchange):
            close = np.asarray(close, dtype=np.float64)
            return MarketSeries(
                times=grid.copy(),
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume_usd=np.asarray(vol, dtype=np.float64),
                oi_usd=np.asarray(oi, dtype=np.float64),
                long_share=np.asarray(share, dtype=np.float64),
                exchange=exchange,
                symbol="BTCUSDT",
                coin="BTC",
                bar_seconds=3600,
            )

        quiet = part([10, 11, 12], [100, 110, 120], [1, 1, 1], [0.2, 0.2, 0.2], "okx")
        busy = part([20, 21, 22], [300, 330, 360], [5, 5, 5], [0.8, 0.8, 0.8], "binance")
        combined = combine_series([quiet, busy], grid, 3600, "BTC")
        self.assertEqual(combined.exchange, "aggregate")
        self.assertAlmostEqual(float(combined.close[-1]), 22.0)
        self.assertAlmostEqual(float(combined.oi_usd[-1]), 480.0)
        # (120 * 0.2 + 360 * 0.8) / 480 = 0.65
        self.assertAlmostEqual(float(combined.long_share[-1]), 0.65)
        self.assertAlmostEqual(float(combined.volume_usd[-1]), 6.0)

    def test_demo_payload_is_little_endian_float32(self):
        payload = load_heatmap("demo", "btc", "12h", 1, "pair")
        raw = base64.b64decode(payload["heat"])
        heat = np.frombuffer(raw, dtype="<f4")
        self.assertEqual(heat.size, payload["bins"] * len(payload["times"]))
        self.assertGreater(payload["analysis"]["price"], 0)
        self.assertEqual(payload["model"]["id"], 1)
        self.assertEqual(payload["range"], "12h")
        self.assertTrue(payload["warnings"])
        with self.assertRaises(DataError):
            load_heatmap("okx", "../btc", "24h", 1)
        with self.assertRaises(DataError):
            load_heatmap("demo", "BTC", "24h", 9)


if __name__ == "__main__":
    unittest.main()
