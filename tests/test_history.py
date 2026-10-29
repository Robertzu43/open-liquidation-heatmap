"""Binance time travel: window placement, no lookahead, source choice, cache. No network."""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

import pytest

from liqmap import data, service
from liqmap.model import MODELS
from liqmap.data import (
    METRICS_START_S,
    DataError,
    history_end_open,
    load_market,
    parse_asof,
    range_spec,
    resolve_view,
)

T_2021 = 1_621_468_799  # 2021-05-19 23:59:59 UTC


def _klines(start_ms, end_ms, bar_ms):
    return [(t, 100.0, 101.0, 99.0, 100.0, 1.0) for t in range(start_ms, end_ms, bar_ms)]


def _metrics(start_ms, end_ms):
    # Open interest is the print's own timestamp, so a test can tell which print was kept.
    return [(t, float(t), 0.5, float(t) / 100.0) for t in range(start_ms, end_ms, 300_000)]


class WindowTests(unittest.TestCase):
    def test_parse_asof(self):
        self.assertIsNone(parse_asof(""))
        self.assertIsNone(parse_asof(None))
        self.assertEqual(parse_asof(str(T_2021)), T_2021)
        self.assertEqual(parse_asof("2021-05-19T23:59:59Z"), T_2021)
        self.assertEqual(parse_asof("2021-05-19T18:59:59-05:00"), T_2021)
        self.assertIsNone(parse_asof(str(int(time.time()) + 3600)))
        for bad in ("2021-05-19T23:59:59", "yesterday", "12:00"):
            with self.assertRaises(DataError):
                parse_asof(bad)

    def test_end_open_is_last_closed_bar(self):
        for bar_s in (300, 900, 3600, 14_400, 43_200, 86_400):
            end_open = history_end_open(T_2021, bar_s)
            self.assertEqual(end_open % bar_s, 0)
            self.assertLessEqual(end_open + bar_s, T_2021)
            self.assertGreater(end_open + 2 * bar_s, T_2021)
        # On an exact boundary the bar that just closed is the last one.
        self.assertEqual(history_end_open(1_621_468_800, 900), 1_621_468_800 - 900)

    def test_bar_override_and_limits(self):
        spec = range_spec("24h", "15m")
        self.assertEqual((spec["bar"], spec["bar_seconds"]), ("15m", 900))
        self.assertEqual(range_spec("24h")["bar"], "5m")
        self.assertEqual(range_spec("2y", "1d")["bar_seconds"], 86_400)
        for rng, bar in (("2y", "5m"), ("12h", "1d"), ("24h", "2m")):
            with self.assertRaises(DataError):
                range_spec(rng, bar)

    def test_history_is_binance_pair_only(self):
        for exchange, mode in (("okx", "pair"), ("demo", "pair"), ("binance", "symbol"), ("aggregate", "symbol")):
            with self.assertRaises(DataError):
                resolve_view(exchange, mode, "24h", str(T_2021), None)
            with self.assertRaises(DataError):
                resolve_view(exchange, mode, "24h", None, "15m")
        self.assertEqual(resolve_view("okx", "pair", "24h", "", "")[1], None)

    def test_before_metrics_start_is_refused(self):
        with self.assertRaisesRegex(DataError, "2020-09-01"):
            resolve_view("binance", "pair", "24h", str(METRICS_START_S - 3600), None)
        _spec, end_open = resolve_view("binance", "pair", "24h", str(METRICS_START_S + 86_400), None)
        self.assertIsNotNone(end_open)


class ArchiveHistoryTests(unittest.TestCase):
    def _load(self, asof, bar="15m", kline_end_shift_ms=86_400_000, live=None):
        """Load a past view with stubbed archive files; ``live`` is what the live fold returns."""
        def klines(symbol, interval, start_ms, end_ms):
            bar_ms = data.BAR_SECONDS[interval] * 1000
            # Hand back rows well past T: the loader must drop them.
            return _klines(start_ms - bar_ms, end_ms + kline_end_shift_ms, bar_ms)

        def metrics(symbol, start_ms, end_ms):
            return _metrics(start_ms - 300_000, end_ms + kline_end_shift_ms)

        with mock.patch.object(data, "_load_vision_klines", side_effect=klines), \
             mock.patch.object(data, "_load_vision_metrics", side_effect=metrics), \
             mock.patch.object(data, "_latest_archive_day", side_effect=AssertionError("must not probe today")), \
             mock.patch.object(data, "_fold_binance_live", side_effect=live) as live_call:
            loaded = load_market("binance", "BTC", "24h", "pair", asof=asof, bar=bar)
        return loaded, live_call

    def test_no_row_after_asof_reaches_the_series(self):
        loaded, live_call = self._load(str(T_2021))
        end_open_ms = history_end_open(T_2021, 900) * 1000
        series = loaded.series
        self.assertEqual(int(series.times[-1]), end_open_ms)
        self.assertEqual(len(series.times), 96)
        self.assertTrue((series.times + 900_000 <= T_2021 * 1000).all())
        # Open-only filter: each bar keeps the print stamped at its own open.
        self.assertEqual(float(series.oi_usd[-1]), float(end_open_ms))
        self.assertEqual(loaded.source, "binance-vision")
        self.assertFalse(loaded.stale)
        self.assertIn("Historical view", series.note)
        self.assertFalse(any("unavailable" in line for line in series.warnings))
        live_call.assert_not_called()

    def test_short_archive_recent_window_uses_live(self):
        asof = int(time.time()) - 86_400

        def live(pair, bar, grid):
            return data.RawSeries("binance-live", grid, *data.fold_candles(_klines(int(grid[0]), int(grid[-1]) + 900_000, 900_000), grid, 900_000),
                                  *data.fold_oi(_metrics(int(grid[0]), int(grid[-1]) + 900_000), grid, 900_000))

        # The archive stops three hours short of the window end.
        loaded, live_call = self._load(str(asof), kline_end_shift_ms=-3 * 3_600_000, live=live)
        live_call.assert_called_once()
        self.assertEqual(loaded.source, "binance-live")
        self.assertFalse(loaded.stale)
        self.assertEqual(int(loaded.series.times[-1]), history_end_open(asof, 900) * 1000)

    def test_short_archive_old_window_is_stale_not_live(self):
        loaded, live_call = self._load(str(T_2021), kline_end_shift_ms=-3 * 3_600_000)
        live_call.assert_not_called()
        self.assertTrue(loaded.stale)
        self.assertTrue(any("ends" in line for line in loaded.series.warnings))


class LivePagingTests(unittest.TestCase):
    def test_klines_page_past_1500_rows(self):
        self.assertEqual(range_spec("7d", "5m")["seconds"] // 300, 2016)
        end_open = 1_800_000_000 // 300 * 300 - 300
        bar_ms = 300_000

        def fake(url, params=None, timeout=None, retries=None):
            start, end = int(params["startTime"]), int(params["endTime"])
            limit = int(params["limit"])
            stamps = list(range(start - start % bar_ms, end, bar_ms))[:limit]
            if url.endswith("/fapi/v1/klines"):
                return 200, [[t, "100", "101", "99", "100", "1", t + bar_ms - 1, "1000"] for t in stamps]
            if url.endswith("/openInterestHist"):
                return 200, [{"timestamp": t, "sumOpenInterestValue": "1000000"} for t in stamps]
            return 200, []

        grid = data.make_grid(end_open, 2016, 300)
        with mock.patch.object(data, "_get_json", side_effect=fake):
            raw = data._fold_binance_live("BTCUSDT", "5m", grid)
        self.assertIsNotNone(raw)
        self.assertEqual(int(__import__("numpy").isfinite(raw.close).sum()), 2016)
        self.assertEqual(raw.last_candle_ms, end_open * 1000)


class ReplaySpanTests(unittest.TestCase):
    """A frame cut from a span must equal the direct past view at the same moment."""

    def _stubs(self, gaps=()):
        def klines(symbol, interval, start_ms, end_ms):
            bar_ms = data.BAR_SECONDS[interval] * 1000
            rows = _klines(start_ms, end_ms, bar_ms)
            return [(t, o + (t // bar_ms) % 7, h + (t // bar_ms) % 7, lo, c + (t // bar_ms) % 5, v) for t, o, h, lo, c, v in rows if t not in gaps]

        def metrics(symbol, start_ms, end_ms):
            return [row for row in _metrics(start_ms, end_ms) if row[0] not in gaps]

        return klines, metrics

    def test_frames_equal_direct_views(self):
        end = history_end_open(T_2021, 900)
        # Gaps right at several window edges, so trimming and the 3-bar fill are exercised.
        gaps = {(end - k * 900) * 1000 for k in (0, 1, 95, 96, 97, 140, 141, 142, 143)}
        klines, metrics = self._stubs(gaps)
        spec = range_spec("24h", "15m")
        with mock.patch.object(data, "_load_vision_klines", side_effect=klines), \
             mock.patch.object(data, "_load_vision_metrics", side_effect=metrics), \
             mock.patch.object(data, "_fold_binance_live", return_value=None):
            span = data.load_span("BTC", "BTCUSDT", spec, T_2021 - 3 * 86_400, T_2021)
            for back in (0, 1, 2, 5, 40, 95, 96, 97, 143, 200):
                end_open = end - back * 900
                if not span.covers(end_open):
                    continue
                framed = span.frame(end_open).series
                direct = data.fetch_binance_history("BTC", "BTCUSDT", spec, end_open).series
                for name in ("times", "open", "high", "low", "close", "oi_usd", "long_share", "volume_usd"):
                    a, b = getattr(framed, name), getattr(direct, name)
                    self.assertTrue(((a == b) | (a != a) & (b != b)).all(), f"{name} differs at -{back} bars")
                self.assertEqual(framed.note, direct.note)
        self.assertEqual(span.last_end_open, end - 2 * 900)  # The two newest bars have no candles, so frames stop before them.
        self.assertEqual(span.frames, (span.last_end_open - span.first_end_open) // 900 + 1)

    def test_span_stops_at_the_last_bar_with_data(self):
        klines, metrics = self._stubs()
        cutoff = (T_2021 - 86_400) * 1000  # The archive ends a day before the requested end.

        def early_klines(symbol, interval, start_ms, end_ms):
            return [row for row in klines(symbol, interval, start_ms, end_ms) if row[0] < cutoff]

        def early_metrics(symbol, start_ms, end_ms):
            return [row for row in metrics(symbol, start_ms, end_ms) if row[0] < cutoff]

        spec = range_spec("24h", "15m")
        with mock.patch.object(data, "_load_vision_klines", side_effect=early_klines), \
             mock.patch.object(data, "_load_vision_metrics", side_effect=early_metrics), \
             mock.patch.object(data, "_fold_binance_live", return_value=None):
            span = data.load_span("BTC", "BTCUSDT", spec, T_2021 - 3 * 86_400, T_2021)
            self.assertEqual(span.last_end_open * 1000, (cutoff - 1) // 900_000 * 900_000)  # Newest bar opening before the cutoff.
            self.assertIn("ends at", span.note)
            last = span.frame(span.last_end_open)
        self.assertFalse(last.stale)

    def test_span_trims_to_first_data(self):
        klines, metrics = self._stubs()
        late = (T_2021 - 86_400) * 1000

        def late_klines(symbol, interval, start_ms, end_ms):
            return [row for row in klines(symbol, interval, start_ms, end_ms) if row[0] >= late]

        spec = range_spec("24h", "15m")
        with mock.patch.object(data, "_load_vision_klines", side_effect=late_klines), \
             mock.patch.object(data, "_load_vision_metrics", side_effect=metrics), \
             mock.patch.object(data, "_fold_binance_live", return_value=None):
            span = data.load_span("BTC", "BTCUSDT", spec, T_2021 - 5 * 86_400, T_2021)
        self.assertGreaterEqual(span.first_end_open * 1000, late)
        self.assertIn("starts later", span.note)


class CacheTests(unittest.TestCase):
    def setUp(self):
        service._cache.clear()
        service._locks.clear()

    def test_key_includes_bar_and_asof_and_evicts(self):
        calls = []

        def fake_load(exchange, coin, range_name, mode, asof=None, bar=None):
            calls.append((asof, bar))
            return mock.sentinel.loaded

        with mock.patch.object(service, "load_market", side_effect=fake_load):
            service.get_series("binance", "BTC", "24h", "pair", asof=str(T_2021), bar="15m")
            service.get_series("binance", "BTC", "24h", "pair", asof=str(T_2021), bar="15m")
            service.get_series("binance", "BTC", "24h", "pair", asof=str(T_2021 - 86_400), bar="15m")
            service.get_series("binance", "BTC", "24h", "pair", asof=str(T_2021), bar="1h")
        self.assertEqual(len(calls), 3)
        keys = list(service._cache)
        self.assertIn(("binance", "BTC", "24h", "pair", "15m", history_end_open(T_2021, 900)), keys)

        # Age every entry past its TTL; the next insert drops them.
        for key, (_stamp, value) in list(service._cache.items()):
            service._cache[key] = (0.0, value)
        with mock.patch.object(service, "load_market", side_effect=fake_load):
            service.get_series("binance", "BTC", "12h", "pair")
        self.assertEqual(len(service._cache), 1)

    def test_payload_reports_bar_used_and_asof(self):
        payload = service.load_heatmap("demo", "BTC", "24h", 1, "pair")
        self.assertEqual(payload["bar"], "5m")
        self.assertIsNone(payload["asof"])


class ReplayServiceTests(unittest.TestCase):
    def setUp(self):
        service._cache.clear()
        service._spans.clear()
        stubs = ReplaySpanTests()._stubs()
        self.patches = [
            mock.patch.object(data, "_load_vision_klines", side_effect=stubs[0]),
            mock.patch.object(data, "_load_vision_metrics", side_effect=stubs[1]),
            mock.patch.object(data, "_fold_binance_live", return_value=None),
        ]
        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        service._spans.clear()

    def test_frames_come_from_the_span_and_expire_to_409(self):
        meta = service.replay_payload("BTC", "24h", "15m", str(T_2021 - 2 * 86_400), str(T_2021))
        self.assertEqual(meta["last_asof"], (history_end_open(T_2021, 900) + 900) * 1000)
        self.assertGreater(meta["frames"], 1)
        calls = sum(m.call_count for m in self.mocks)
        frame_asof = str(meta["last_asof"] // 1000)
        payload = service.load_heatmap("binance", "BTC", "24h", 1, "pair", frame_asof, "15m", True)
        self.assertTrue(payload["replay"])
        self.assertEqual(payload["source"], "binance-vision")
        self.assertEqual(len(payload["tiers"]), len(MODELS[1]["weights"]))
        self.assertEqual(sum(m.call_count for m in self.mocks), calls, "a frame must not load anything")
        self.assertEqual(len(service._cache), 0, "frames are not cached one by one")

        # Levels: tier sums equal the map at that cell.
        t = payload["times"][-3]
        levels = service.levels_payload("binance", "BTC", "24h", 1, "pair", frame_asof, "15m", True, str(t), str(payload["analysis"]["price"] * 0.99))
        self.assertAlmostEqual(sum(row["long_usd"] for row in levels["tiers"]), levels["long_usd"], delta=1e-6 * max(1.0, levels["long_usd"]))
        self.assertAlmostEqual(sum(row["short_usd"] for row in levels["tiers"]), levels["short_usd"], delta=1e-6 * max(1.0, levels["short_usd"]))
        with self.assertRaises(DataError):
            service.levels_payload("binance", "BTC", "24h", 1, "pair", frame_asof, "15m", True, str(t + 1), "100")

        for key in service._spans:
            service._spans[key][0] = 0.0
        with self.assertRaises(service.ReplayExpired):
            service.load_heatmap("binance", "BTC", "24h", 1, "pair", frame_asof, "15m", True)

    def test_replay_refuses_bars_over_the_frame_cap(self):
        with self.assertRaises(DataError):
            service.replay_payload("BTC", "2y", "5m", str(T_2021), "")


@pytest.mark.network
@unittest.skipUnless(os.environ.get("RUN_NETWORK") == "1", "network test; set RUN_NETWORK=1")
class NetworkHistoryTests(unittest.TestCase):
    def test_btc_2021_05_19_from_archive(self):
        loaded = load_market("binance", "BTC", "24h", "pair", asof="2021-05-19T23:59:59Z", bar="15m")
        self.assertEqual(loaded.source, "binance-vision")
        self.assertGreaterEqual(len(loaded.series.times), 90)
        self.assertLessEqual(int(loaded.series.times[-1]) + 900_000, T_2021 * 1000 + 1000)


if __name__ == "__main__":
    unittest.main()


def test_expired_request_budget_prevents_network(monkeypatch):
    import pytest
    monkeypatch.setattr(data.time, 'monotonic', lambda: 100.)
    token = data.request_deadline.set(99.)
    try:
        monkeypatch.setattr(data._SESSION, 'get', lambda *args, **kwargs: pytest.fail('expired request made network call'))
        with pytest.raises(data.RequestDeadlineExceeded):
            data._get_json('http://127.0.0.1')
    finally:
        data.request_deadline.reset(token)


def test_streamed_response_stops_when_request_budget_expires(monkeypatch):
    import pytest
    clock = [100.]
    monkeypatch.setattr(data.time, 'monotonic', lambda: clock[0])
    class Response:
        status_code = 200
        closed = False
        def iter_content(self, chunk_size):
            yield b'{"ok":'
            clock[0] = 102.
            yield b'true}'
        def close(self):
            self.closed = True
    response = Response()
    observed = {}
    def get(*args, **kwargs):
        observed.update(kwargs)
        return response
    monkeypatch.setattr(data._SESSION, 'get', get)
    token = data.request_deadline.set(101.)
    try:
        with pytest.raises(data.RequestDeadlineExceeded):
            data._get_json('http://127.0.0.1')
        assert observed['stream'] is True
        assert observed['timeout'].total == 1.
        assert response.closed
    finally:
        data.request_deadline.reset(token)


def test_archive_workers_inherit_request_budget(monkeypatch):
    import pytest
    clock = [100.]
    monkeypatch.setattr(data.time, 'monotonic', lambda: clock[0])
    def cached(*args):
        assert data.request_deadline.get() == 101.
        clock[0] = 102.
        data.check_deadline()
    monkeypatch.setattr(data, '_cached_zip', cached)
    token = data.request_deadline.set(101.)
    try:
        with pytest.raises(data.RequestDeadlineExceeded):
            data._load_vision_metrics('BTCUSDT', 0, 4*86400*1000)
    finally:
        data.request_deadline.reset(token)


def test_source_fallback_does_not_swallow_request_deadline(monkeypatch):
    import numpy as np
    import pytest
    def expired(*args, **kwargs):
        raise data.RequestDeadlineExceeded('expired fixture budget')
    monkeypatch.setattr(data, '_get_json', expired)
    with pytest.raises(data.RequestDeadlineExceeded):
        data._fold_binance_live('BTCUSDT','5m',np.array([300000,600000]))


def test_archive_cache_uses_user_writable_location_and_explicit_override(monkeypatch, tmp_path):
    monkeypatch.delenv('LIQMAP_CACHE_DIR', raising=False)
    monkeypatch.delenv('XDG_CACHE_HOME', raising=False)
    monkeypatch.setattr(data.Path, 'home', lambda: tmp_path)
    assert data._cache_root() == tmp_path / '.cache' / 'open-liq'
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg'))
    assert data._cache_root() == tmp_path / 'xdg' / 'open-liq'
    monkeypatch.setenv('LIQMAP_CACHE_DIR', str(tmp_path / 'explicit'))
    assert data._cache_root() == tmp_path / 'explicit'
    assert data._cache_root().is_dir()


def test_archive_file_lock_cardinality_is_bounded():
    locks = [data._file_lock(f'fixture/{i}.zip') for i in range(2048)]
    assert len({id(lock) for lock in locks}) <= 64
    assert data._file_lock('same.zip') is data._file_lock('same.zip')


def test_remote_metadata_caches_evict_old_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(data, 'MAX_METADATA_CACHE_ENTRIES', 4, raising=False)
    monkeypatch.setattr(data, '_HEAD_CACHE', {})
    monkeypatch.setattr(data, '_NEG_ZIP', {})
    monkeypatch.setattr(data, '_cache_root', lambda: tmp_path)
    class Response:
        headers = {'content-length': '100'}
        def __init__(self,status):
            self.status_code = status
        def close(self):
            pass
        def iter_content(self,chunk_size):
            yield b'missing'
    monkeypatch.setattr(data._SESSION, 'head', lambda *args, **kwargs: Response(200))
    monkeypatch.setattr(data._SESSION, 'get', lambda *args, **kwargs: Response(404))
    for i in range(6):
        url = f'http://127.0.0.1/{i}.zip'
        assert data._head_ok(url)
        assert data._cached_zip(url,f'{i}.zip') is None
    assert len(data._HEAD_CACHE) == len(data._NEG_ZIP) == 4
    assert 'http://127.0.0.1/0.zip' not in data._HEAD_CACHE
    assert 'http://127.0.0.1/0.zip' not in data._NEG_ZIP


def test_response_body_limit_stops_stream_and_closes_response(monkeypatch):
    import pytest
    monkeypatch.setattr(data,'MAX_RESPONSE_BYTES',8,raising=False)
    class Response:
        closed = False
        def iter_content(self,chunk_size):
            yield b'12345'
            yield b'67890'
            pytest.fail('read continued after response limit')
        def close(self):
            self.closed = True
    response = Response()
    with pytest.raises(data.DataError,match='size limit'):
        data._response_bytes(response)
    assert response.closed


def test_zip_member_limit_prevents_inflating_oversized_archive(monkeypatch):
    import io
    import zipfile
    import pytest
    monkeypatch.setattr(data,'MAX_ZIP_MEMBER_BYTES',8,raising=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('fixture.csv','x'*1000)
    with pytest.raises(data.DataError,match='size limit'):
        data._zip_text(buf.getvalue())
    monkeypatch.setattr(data,'MAX_ZIP_MEMBER_BYTES',1000)
    assert data._zip_text(buf.getvalue()) == 'x'*1000


def test_cached_archive_size_limit_applies_before_reading(monkeypatch,tmp_path):
    import pytest
    monkeypatch.setattr(data,'MAX_RESPONSE_BYTES',40,raising=False)
    monkeypatch.setattr(data,'_cache_root',lambda:tmp_path)
    (tmp_path/'fixture.zip').write_bytes(b'PK'+b'x'*100)
    with pytest.raises(data.DataError,match='size limit'):
        data._cached_zip('http://127.0.0.1/fixture.zip','fixture.zip')
    allowed = b'PK'+b'x'*38
    (tmp_path/'fixture.zip').write_bytes(allowed)
    assert data._cached_zip('http://127.0.0.1/fixture.zip','fixture.zip') == allowed
