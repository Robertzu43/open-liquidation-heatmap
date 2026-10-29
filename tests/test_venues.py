"""Offline Bybit adapter and three-venue aggregate tests; no exchange connections are used."""
from unittest import mock

import numpy as np
import pytest

from liqmap import data, service
from liqmap.data import DataError, fetch_demo, range_spec
from liqmap.model import build_heatmap

BASE = 1792886400000  # 2026-10-25 00:00:00 UTC
FIVE = 300_000


def ok(result):
    return {'retCode': 0, 'retMsg': 'OK', 'result': result, 'retExtInfo': {}, 'time': BASE}


class FakeBybit:
    """Bybit v5 kline and open-interest pages as the public API serves them: newest first."""

    def __init__(self, klines, oi, *, kline_page=1000, oi_page=200, extra_oi=()):
        self.klines = [tuple(row) for row in klines]
        self.oi = list(oi)
        self.kline_page = kline_page
        self.oi_page = oi_page
        self.extra_oi = list(extra_oi)
        self.calls = []

    def __call__(self, url, params=None, **kwargs):
        self.calls.append((url.rsplit('/', 1)[-1], dict(params or {})))
        if url.endswith('/v5/market/kline'):
            start, end = int(params['start']), int(params['end'])
            rows = sorted((row for row in self.klines if start <= row[0] <= end), reverse=True)
            rows = rows[:min(int(params['limit']), self.kline_page)]
            return 200, ok({'category': 'linear', 'symbol': params['symbol'], 'list': [[str(value) for value in row] for row in rows]})
        if url.endswith('/v5/market/open-interest'):
            start, end = int(params['startTime']), int(params['endTime'])
            rows = sorted((row for row in self.oi if start <= row[0] <= end), reverse=True)
            page = int(params.get('cursor') or 0)
            chunk = rows[page * self.oi_page:(page + 1) * self.oi_page]
            if page == 0:
                chunk = chunk + self.extra_oi  # Rows a lenient API might add outside the request.
            more = (page + 1) * self.oi_page < len(rows)
            return 200, ok({'category': 'linear', 'symbol': params['symbol'],
                            # Documented row: openInterest sums both sides, singleOpenInterest is one side.
                            'list': [{'openInterest': str(2 * base), 'singleOpenInterest': str(base), 'timestamp': str(ts)}
                                     for ts, base in chunk],
                            'nextPageCursor': str(page + 1) if more else ''})
        raise AssertionError(f'unexpected URL {url}')

    def named(self, name):
        return [params for endpoint, params in self.calls if endpoint == name]


def kline(ts, close, *, high=None, low=None, turnover=1000.0):
    return (int(ts), close, close + 1 if high is None else high, close - 1 if low is None else low, close, 10.0, turnover)


def fixture_spec(bar='5m', bars=6):
    seconds = data.BAR_SECONDS[bar]
    return {'key': 'fixture', 'bar': bar, 'bar_seconds': seconds, 'seconds': seconds * bars}


def use_grid(monkeypatch, grid, now_ms):
    monkeypatch.setattr(data, '_window', lambda spec, end_open_s=None: grid)
    monkeypatch.setattr(data.time, 'time', lambda: now_ms / 1000)


def test_bybit_parses_newest_first_rows_and_keeps_native_base_oi(monkeypatch):
    grid = BASE + np.arange(6) * FIVE
    use_grid(monkeypatch, grid, int(grid[-1]) + FIVE)
    fake = FakeBybit([kline(t, 100 + i, turnover=500 + i) for i, t in enumerate(grid)],
                     [(int(t), 10 + i) for i, t in enumerate(grid)])
    monkeypatch.setattr(data, '_get_json', fake)
    loaded = data.fetch_bybit('BTC', 'BTCUSDT', fixture_spec())
    series = loaded.series
    np.testing.assert_array_equal(series.times, grid)
    np.testing.assert_array_equal(series.close, 100 + np.arange(6))
    np.testing.assert_array_equal(series.volume_usd, 500 + np.arange(6))
    np.testing.assert_array_equal(series.oi_base, 10 + np.arange(6))
    np.testing.assert_allclose(series.oi_usd, (10 + np.arange(6)) * (100 + np.arange(6)))
    assert series.candle_observed.all() and series.oi_observed.all()
    assert not series.flow_observed.any()
    assert loaded.source == 'bybit' and series.exchange == 'bybit' and loaded.venues == ['bybit']
    assert series.provenance['flow_source'] == 'unavailable_neutral'
    assert series.provenance['native_oi_unit'] == 'base_asset'
    assert fake.named('kline')[0]['interval'] == '5' and fake.named('open-interest')[0]['intervalTime'] == '5min'


def test_bybit_excludes_unclosed_candle_and_snapshots_outside_closed_bars(monkeypatch):
    grid = BASE + np.arange(6) * FIVE
    use_grid(monkeypatch, grid, int(grid[-1]) + FIVE // 3)  # The last grid bar is still open.
    candles = [kline(t, 100) for t in grid]
    candles[-1] = kline(grid[-1], 100, high=5000)
    fake = FakeBybit(candles, [(int(t), 10) for t in grid],
                     extra_oi=[(int(grid[0]) - FIVE, 999), (int(grid[-1]) + FIVE, 999)])
    monkeypatch.setattr(data, '_get_json', fake)
    series = data.fetch_bybit('BTC', 'BTCUSDT', fixture_spec()).series
    assert series.times.tolist() == grid[:-1].tolist()
    assert series.high.max() < 5000
    assert 999 not in series.oi_base


def test_bybit_twelve_hour_bars_use_last_four_hour_snapshot_inside_each_bar(monkeypatch):
    bar_ms = 12 * 3600 * 1000
    grid = BASE + np.arange(3) * bar_ms
    use_grid(monkeypatch, grid, int(grid[-1]) + bar_ms)
    snapshots = [(int(grid[0]) + k * 4 * 3600 * 1000, 10 + k) for k in range(10)]
    fake = FakeBybit([kline(t, 100) for t in grid], snapshots)
    monkeypatch.setattr(data, '_get_json', fake)
    series = data.fetch_bybit('BTC', 'BTCUSDT', fixture_spec('12h', 3)).series
    # Snapshots at +0h, +4h and +8h fall in each bar; +8h is the last one, never the close print.
    np.testing.assert_array_equal(series.oi_base, [12, 15, 18])
    assert fake.named('kline')[0]['interval'] == '720'
    assert fake.named('open-interest')[0]['intervalTime'] == '4h'


def test_bybit_klines_take_one_page_and_oi_follows_page_cursor(monkeypatch):
    grid = BASE + np.arange(730) * FIVE  # The longest built-in view: 2y of daily bars.
    use_grid(monkeypatch, grid, int(grid[-1]) + FIVE)
    fake = FakeBybit([kline(t, 100 + (i % 7)) for i, t in enumerate(grid)], [(int(t), 10) for t in grid])
    monkeypatch.setattr(data, '_get_json', fake)
    series = data.fetch_bybit('BTC', 'BTCUSDT', fixture_spec(bars=730)).series
    assert len(fake.named('kline')) == 1
    assert len(series.times) == 730 and series.candle_observed.all() and series.oi_observed.all()
    pages = fake.named('open-interest')
    assert [params.get('cursor') for params in pages] == [None, '1', '2', '3']


def test_bybit_window_beyond_one_kline_page_is_refused_not_truncated(monkeypatch):
    monkeypatch.setattr(data, '_get_json', lambda *args, **kwargs: pytest.fail('no request for an oversized window'))
    with pytest.raises(data.DataError, match='1000 bars'):
        data.fetch_bybit('BTC', 'BTCUSDT', fixture_spec(bars=1001))


def test_bybit_oi_cursor_pagination_stops_on_repeated_cursor(monkeypatch):
    calls = []

    def get_json(url, params=None, **kwargs):
        calls.append(dict(params))
        return 200, ok({'list': [{'openInterest': '5', 'timestamp': str(BASE)}], 'nextPageCursor': 'same'})

    monkeypatch.setattr(data, '_get_json', get_json)
    rows = data._bybit_open_interest('BTCUSDT', '5min', BASE, BASE + FIVE)
    assert len(calls) == 2
    assert calls[1]['cursor'] == 'same'
    assert [int(row['timestamp']) for row in rows] == [BASE]


def test_bybit_region_block_without_json_body_is_explained(monkeypatch):
    class Blocked:
        status_code = 403
        headers = {'content-type': 'text/html'}

        def iter_content(self, chunk_size):
            yield b'{\n error:The Amazon CloudFront distribution is configured to block access from your country\n}'

        def close(self):
            pass

    monkeypatch.setattr(data._SESSION, 'get', lambda *args, **kwargs: Blocked())
    with pytest.raises(DataError, match='blocks some regions') as caught:
        data.fetch_bybit('BTC', 'BTCUSDT', range_spec('24h'))
    assert 'HTTP 403' in str(caught.value)


def test_bybit_unknown_symbol_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(data, '_get_json', lambda *args, **kwargs: (200, {'retCode': 10001, 'retMsg': 'params error: symbol invalid', 'result': {}}))
    with pytest.raises(DataError, match='Bybit has no NOPEUSDT perpetual'):
        data.fetch_bybit('NOPE', 'NOPEUSDT', range_spec('24h'))


def test_bybit_other_rejection_and_malformed_result_are_errors(monkeypatch):
    monkeypatch.setattr(data, '_get_json', lambda *args, **kwargs: (200, {'retCode': 10006, 'retMsg': 'Too many visits!'}))
    with pytest.raises(DataError, match='rejected the request'):
        data._bybit_result('/v5/market/kline', {'symbol': 'BTCUSDT'})
    monkeypatch.setattr(data, '_get_json', lambda *args, **kwargs: (200, {'retCode': 0, 'result': []}))
    with pytest.raises(DataError, match='unexpected response'):
        data._bybit_result('/v5/market/kline', {'symbol': 'BTCUSDT'})


def test_bybit_without_candles_or_open_interest_fails_honestly(monkeypatch):
    grid = BASE + np.arange(6) * FIVE
    use_grid(monkeypatch, grid, int(grid[-1]) + FIVE)
    monkeypatch.setattr(data, '_get_json', FakeBybit([kline(t, 100) for t in grid], []))
    with pytest.raises(DataError, match='no BTCUSDT open interest'):
        data.fetch_bybit('BTC', 'BTCUSDT', fixture_spec())
    monkeypatch.setattr(data, '_get_json', FakeBybit([], [(int(t), 10) for t in grid]))
    with pytest.raises(DataError, match='no BTCUSDT candles'):
        data.fetch_bybit('BTC', 'BTCUSDT', fixture_spec())


@pytest.fixture
def clean_cache():
    service._cache.clear()
    yield
    service._cache.clear()


def test_bybit_heatmap_payload_builds_with_venue_formula_and_provenance(monkeypatch, clean_cache):
    grid = BASE + np.arange(12) * FIVE
    use_grid(monkeypatch, grid, int(grid[-1]) + FIVE)
    oi = [(int(t), 10 + i * (1 if i % 3 else -0.5)) for i, t in enumerate(grid)]
    monkeypatch.setattr(data, '_get_json', FakeBybit([kline(t, 100 + i) for i, t in enumerate(grid)], oi))
    payload = service.load_heatmap('bybit', 'BTC', '24h', 1)
    assert payload['exchange'] == 'bybit' and payload['exchange_name'] == 'Bybit'
    assert payload['source'] == 'bybit' and payload['venues'] == ['bybit']
    assert payload['quality']['status'] == 'observed'
    assert payload['quality']['provenance']['flow_source'] == 'unavailable_neutral'
    assert payload['model_metadata']['oi_accounting'] == {'bybit': 'native_base'}
    assert payload['features']['resting_long_usd'] > 0
    loaded = service.get_series('bybit', 'BTC', '24h')
    for model_id in (1, 2, 3):
        heat = build_heatmap(loaded.series, model_id)
        assert heat.profile_long.sum() + heat.profile_short.sum() > 0


def venue_series(name, source):
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    loaded.series.exchange, loaded.source = name, source
    loaded.series.provenance['history_kind'] = 'exchange_history'
    return loaded


def test_aggregate_includes_bybit_and_is_observed_when_all_venues_complete(monkeypatch):
    grid = BASE + np.arange(12) * FIVE
    use_grid(monkeypatch, grid, int(grid[-1]) + FIVE)
    monkeypatch.setattr(data, '_get_json', FakeBybit([kline(t, 82000 + i) for i, t in enumerate(grid)], [(int(t), 50 + i) for i, t in enumerate(grid)]))
    with mock.patch.object(data, 'fetch_okx', side_effect=lambda *a, **k: venue_series('okx', 'okx')), \
         mock.patch.object(data, 'fetch_binance', side_effect=lambda *a, **k: venue_series('binance', 'binance-live')):
        loaded = data.fetch_aggregate('BTC', 'BTCUSDT', range_spec('24h'))
    assert loaded.venues == ['okx', 'binance', 'bybit']
    assert [part.exchange for part in loaded.series.components] == ['okx', 'binance', 'bybit']
    quality = service._quality(loaded)
    assert quality['status'] == 'observed' and quality['degraded'] is False
    heat = build_heatmap(loaded.series, 1)
    assert heat.metadata['oi_accounting']['bybit'] == 'native_base'
    assert all(set(venues) == {'okx', 'binance', 'bybit'} for venues in heat.metadata['venue_coverage'][1:])


def test_aggregate_is_degraded_with_bybit_warning_when_bybit_fails(monkeypatch):
    grid = BASE + np.arange(12) * FIVE
    use_grid(monkeypatch, grid, int(grid[-1]) + FIVE)
    monkeypatch.setattr(data, '_get_json', lambda *args, **kwargs: (403, None))
    with mock.patch.object(data, 'fetch_okx', side_effect=lambda *a, **k: venue_series('okx', 'okx')), \
         mock.patch.object(data, 'fetch_binance', side_effect=lambda *a, **k: venue_series('binance', 'binance-live')):
        loaded = data.fetch_aggregate('BTC', 'BTCUSDT', range_spec('24h'))
    assert loaded.venues == ['okx', 'binance']
    quality = service._quality(loaded)
    assert quality['status'] == 'degraded' and quality['degraded'] is True
    assert any(warning.startswith('Bybit') and 'blocks some regions' in warning for warning in quality['warnings'])


def test_load_market_routes_bybit_pair_to_the_bybit_adapter():
    sentinel = mock.sentinel.loaded
    with mock.patch.object(data, 'fetch_bybit', return_value=sentinel) as fetch:
        assert data.load_market('bybit', 'ETH', '24h') is sentinel
    assert fetch.call_args.args[:2] == ('ETH', 'ETHUSDT')
