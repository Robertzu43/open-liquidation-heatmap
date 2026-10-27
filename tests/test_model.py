"""Model invariants and pure data helpers. No network."""

from __future__ import annotations

import base64
import unittest

import numpy as np

from liqmap.data import (
    DataError,
    combine_series,
    fold_candles,
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
        early, _share = fold_oi([(600_000, 100.0, 0.5)], grid, 3_600_000)
        self.assertEqual(early[0], 100.0)
        self.assertTrue(np.isnan(early[1]))
        later, _share = fold_oi([(3_000_000, 600.0, 0.5)], grid, 3_600_000)
        self.assertTrue(np.isnan(later[0]))
        self.assertEqual(later[1], 600.0)

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
