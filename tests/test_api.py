"""Offline HTTP contract tests; no exchange connections are used."""
import contextlib
import json
import threading
import urllib.error
import urllib.request
from unittest import mock

import pytest

from liqmap import market, server, service
from liqmap.data import DataError, fetch_demo, range_spec


@pytest.fixture
def http_api():
    app = server.BoundedThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    # A short poll keeps shutdown fast; each test starts its own server.
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    def get(path, headers=None):
        request = urllib.request.Request(f'http://127.0.0.1:{app.server_port}{path}', headers=headers or {})
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            return response.status, json.loads(raw, parse_constant=lambda value: pytest.fail(f'nonfinite JSON: {value}'))
    get.app = app
    yield get
    app.shutdown()
    app.server_close()
    thread.join()


@pytest.fixture(autouse=True)
def clear_cache():
    def clear():
        service._cache.clear()
        service._spans.clear()
        market._context_cache.clear()
        market._liquidation_cache.clear()
        market._instruments.clear()
    clear()
    yield
    clear()


def test_versioned_routes_preserve_heatmap_and_feature_contract(http_api):
    status, heat = http_api('/api/v1/heatmap?exchange=demo&range=12h')
    assert status == 200
    assert heat['schema_version'] == '1.0'
    assert heat['model_version']
    assert heat['data_fingerprint'].startswith('sha256:')
    assert heat['quality']['status'] == 'synthetic'
    assert heat['time_semantics']['state'] == 'bar_close_post_update'
    assert heat['assumptions']
    status, features = http_api('/api/v1/features?exchange=demo&range=12h')
    assert status == 200
    assert features['features'] == heat['features']
    assert features['data_fingerprint'] == heat['data_fingerprint']
    assert 'heat' not in features
    assert http_api('/api/v1/status')[1]['schema_version'] == '1.0'
    assert http_api('/api/heatmap?exchange=demo&range=12h')[0] == 200


@pytest.mark.parametrize('query', ['model=no', 'exchange=unknown', 'range=no', 'symbol=!', 'mode=no', 'replay=wat', 'model=1&model=2'])
def test_validation_has_stable_error_code(http_api, query):
    status, body = http_api('/api/v1/heatmap?' + query)
    assert status == 400
    assert body['code'] == 'invalid_request'
    assert isinstance(body['error'], str)


def test_health_reports_the_process_only(http_api):
    assert http_api('/healthz') == (200, {'ok': True, 'process': 'healthy'})


def test_stale_source_is_reported_and_source_failure_is_503(http_api):
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    loaded.source = 'fixture-exchange'
    loaded.series.exchange = 'okx'
    loaded.series.provenance['history_kind'] = 'exchange_history'
    loaded.stale = True
    with mock.patch.object(service, 'load_market', return_value=loaded):
        status, body = http_api('/api/v1/heatmap?range=12h')
    assert status == 200
    assert body['quality']['status'] == 'stale'
    service._cache.clear()
    with mock.patch.object(service, 'load_market', side_effect=DataError('fixture unavailable')):
        status, body = http_api('/api/v1/heatmap?range=12h')
    assert status == 503
    assert body['code'] == 'source_unavailable'


def test_json_serialization_rejects_nonfinite_without_partial_response(http_api):
    with mock.patch.object(server, 'status_payload', return_value={'bad': float('nan')}):
        status, body = http_api('/api/v1/status')
    assert status == 500
    assert body['code'] == 'internal_error'


def test_replay_expired_and_unknown_route_have_stable_codes(http_api):
    status, body = http_api('/api/v1/heatmap?exchange=binance&asof=1609459200&replay=1')
    assert (status, body['code']) == (409, 'replay_expired')
    for path in ('/api/v1/nope', '/readyz', '/api/v1/symbols', '/api/symbols'):
        assert http_api(path)[0] == 404, path


def test_overload_is_retryable_and_health_remains_available(http_api):
    with contextlib.ExitStack() as stack:
        for _ in range(server.MAX_EXPENSIVE_REQUESTS):
            assert server._expensive.acquire(blocking=False)
            stack.callback(server._expensive.release)
        status, body = http_api('/api/v1/heatmap?exchange=demo')
        assert (status, body['code']) == (429, 'busy')
        assert http_api('/healthz')[0] == 200


def test_failed_source_releases_expensive_slot(http_api):
    with mock.patch.object(service, 'load_market', side_effect=DataError('temporary outage')):
        for _ in range(server.MAX_EXPENSIVE_REQUESTS + 1):
            assert http_api('/api/v1/heatmap')[0] == 503
    assert http_api('/api/v1/heatmap?exchange=demo&range=12h')[0] == 200


def test_installed_copy_ignores_another_packages_web_folder(tmp_path, monkeypatch):
    site = tmp_path.resolve()
    monkeypatch.setattr(server, '__file__', str(site / 'liqmap' / 'server.py'))
    (site / 'web').mkdir()  # another distribution's top-level package beside liqmap in site-packages
    with mock.patch.object(server.resources, 'files', return_value=site / 'liqmap_web') as files:
        assert server.web_root() == site / 'liqmap_web'
    files.assert_called_once_with('liqmap_web')
    (site / 'web' / 'index.html').write_text('<html>')  # a source checkout
    assert server.web_root() == site / 'web'


def test_server_default_bind_and_version_match_installed_release():
    from liqmap import __version__
    app = mock.Mock()
    app.serve_forever.side_effect = KeyboardInterrupt
    with mock.patch.object(server, 'BoundedThreadingHTTPServer', return_value=app) as create:
        server.main([])
    assert create.call_args.args[0] == ('127.0.0.1', 8765)
    assert server.Handler.server_version == f'open-liqmap/{__version__}'


def test_request_deadline_is_set_reset_and_late_success_is_503(http_api):
    from liqmap import data
    import time
    def late_map(*_args, **_kwargs):
        deadline = data.request_deadline.get()
        assert deadline is not None
        assert 0 < deadline - time.monotonic() <= server.REQUEST_DEADLINE_S
        data.request_deadline.set(time.monotonic() - 1)
        return {'ok': True}
    with mock.patch.object(server, 'load_heatmap', side_effect=late_map):
        status, body = http_api('/api/v1/heatmap?exchange=demo')
    assert (status, body['code']) == (503, 'deadline_exceeded')
    assert data.request_deadline.get() is None
    assert http_api('/healthz')[0] == 200


def test_upstream_deadline_is_not_reclassified_as_bad_query(http_api):
    from liqmap import data
    with mock.patch.object(service, 'load_market', side_effect=data.RequestDeadlineExceeded('Request budget exhausted.')):
        status, body = http_api('/api/v1/heatmap')
    assert (status, body['code']) == (503, 'deadline_exceeded')


@pytest.mark.parametrize('value', ['0', '1', '-1', '2.5', 'no', '3001'])
def test_visible_bars_invalid_value_rejected_before_loading(http_api, value):
    with mock.patch.object(service, 'load_market', side_effect=AssertionError('validation must precede source loading')):
        status, body = http_api('/api/v1/heatmap?exchange=demo&visible_bars=' + value)
    assert (status, body['code']) == (400, 'invalid_request')


def test_visible_tail_preserves_history_fingerprint_features_and_level_mapping(http_api):
    import base64
    import numpy as np
    status, full = http_api('/api/v1/heatmap?exchange=demo&range=12h')
    assert status == 200
    status, tail = http_api('/api/v1/heatmap?exchange=demo&range=12h&visible_bars=10')
    assert status == 200
    assert tail['times'] == full['times'][-10:]
    assert tail['data_fingerprint'] == full['data_fingerprint']
    assert tail['features'] == full['features']
    assert tail['model_metadata']['history_bars'] == len(full['times'])
    assert tail['model_metadata']['visible_bars'] == 10
    assert tail['model_metadata']['warmup_anchor_ms'] == full['times'][0]
    full_grid = np.frombuffer(base64.b64decode(full['heat']), dtype='<f4').reshape(full['bins'], -1)
    tail_grid = np.frombuffer(base64.b64decode(tail['heat']), dtype='<f4').reshape(tail['bins'], -1)
    np.testing.assert_array_equal(tail_grid, full_grid[:, -10:])
    t = tail['times'][-4]
    price = full['analysis']['price'] * .99
    query = f'?exchange=demo&range=12h&t={t}&price={price}'
    full_levels = http_api('/api/v1/levels' + query)[1]
    status, tail_levels = http_api('/api/v1/levels' + query + '&visible_bars=10')
    assert status == 200
    assert tail_levels == full_levels
    status, feature_tail = http_api('/api/v1/features?exchange=demo&range=12h&visible_bars=10')
    assert status == 200
    assert feature_tail['times'] == tail['times']
    assert feature_tail['features'] == full['features']
    assert http_api(f'/api/v1/levels?exchange=demo&range=12h&visible_bars=10&t={full["times"][0]}&price={price}')[0] == 400
    assert http_api('/api/v1/heatmap?exchange=demo&range=12h&visible_bars=145')[0] == 400


def test_recently_fetched_live_source_thirty_minutes_old_is_stale(http_api):
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    loaded.source = 'okx'
    loaded.series.exchange = 'okx'
    loaded.series.provenance['history_kind'] = 'exchange_history'
    loaded.series.times -= 30 * 60 * 1000
    loaded.as_of = int(loaded.series.times[-1])
    loaded.stale = False  # Original loader threshold would accept this age.
    with mock.patch.object(service, 'load_market', return_value=loaded):
        status, body = http_api('/api/v1/heatmap')
    assert status == 200
    assert body['quality']['status'] == 'stale'
    assert body['quality']['stale'] is True
    assert body['stale'] is True


@pytest.fixture
def offline_exchanges():
    from liqmap import data
    with mock.patch.object(data, '_get_json', side_effect=AssertionError('API tests must not contact an exchange')) as guard:
        yield guard


@pytest.mark.parametrize('prefix', ['/api/v1', '/api'])
def test_demo_context_route_is_synthetic_and_offline(http_api, offline_exchanges, prefix):
    status, body = http_api(prefix + '/context?exchange=demo&symbol=ETH')
    assert status == 200
    assert body['schema_version'] == '1.0'
    assert (body['coin'], body['symbol'], body['exchange'], body['mode'], body['synthetic']) == ('ETH', 'ETHUSDT', 'demo', 'pair', True)
    [venue] = body['venues']
    assert set(venue) == {'exchange', 'name', 'ok', 'funding_rate', 'next_funding_time', 'funding_interval_hours',
                          'open_interest_base', 'open_interest_usd', 'mark_price', 'observed_at', 'error'}
    assert venue['ok'] is True and venue['exchange'] == 'demo'
    assert body['funding_rate_oi_weighted'] == pytest.approx(venue['funding_rate'])
    assert body['open_interest_usd_total'] == pytest.approx(venue['open_interest_usd'])
    assert isinstance(body['generated_at'], int) and isinstance(body['note'], str)


@pytest.mark.parametrize('prefix', ['/api/v1', '/api'])
def test_demo_liquidations_route_is_synthetic_and_ordered(http_api, offline_exchanges, prefix):
    status, body = http_api(prefix + '/liquidations?exchange=demo&symbol=BTC&range=12h')
    assert status == 200
    assert (body['schema_version'], body['coin'], body['source'], body['available'], body['synthetic']) == ('1.0', 'BTC', 'demo', True, True)
    events = body['events']
    assert events and [event['ts'] for event in events] == sorted(event['ts'] for event in events)
    assert all(set(event) == {'ts', 'price', 'side', 'size_base', 'usd'} and event['side'] in {'long', 'short'} for event in events)
    assert body['coverage'] == {'from_ms': events[0]['ts'], 'to_ms': events[-1]['ts'], 'count': len(events), 'truncated': False}
    status, heat = http_api('/api/v1/heatmap?exchange=demo&symbol=BTC&range=12h')
    assert heat['times'][0] < events[0]['ts'] and events[-1]['ts'] < heat['feature_as_of']


def test_binance_pair_liquidations_are_explicitly_unavailable(http_api, offline_exchanges):
    status, body = http_api('/api/v1/liquidations?exchange=binance&symbol=BTC')
    assert status == 200
    assert body['available'] is False and body['events'] == [] and body['synthetic'] is False
    assert 'OKX only' in body['note']


@pytest.mark.parametrize('prefix', ['/api/v1', '/api'])
def test_demo_watchlist_route_keeps_request_order(http_api, offline_exchanges, prefix):
    status, body = http_api(prefix + '/watchlist?exchange=demo&range=12h&model=1&symbols=SOL,btc,ETH')
    assert status == 200
    assert {key: body[key] for key in ('schema_version', 'exchange', 'mode', 'range', 'model')} == {
        'schema_version': '1.0', 'exchange': 'demo', 'mode': 'pair', 'range': '12h', 'model': 1}
    assert [item['coin'] for item in body['items']] == ['SOL', 'BTC', 'ETH']
    for item in body['items']:
        assert set(item) == {'coin', 'ok', 'price', 'price_change_pct', 'quality', 'stale', 'above', 'below',
                             'short_to_long_5pct', 'as_of', 'error'}
        assert item['ok'] is True and item['quality'] == 'synthetic' and item['stale'] is False
        assert set(item['above']) == {'price', 'distance_pct', 'usd'} and set(item['below']) == {'price', 'distance_pct', 'usd'}


@pytest.mark.parametrize('path', [
    '/api/v1/watchlist?exchange=demo&symbols=A1,B2,C3,D4,E5,F6,G7',
    '/api/v1/watchlist?exchange=demo&symbols=',
    '/api/v1/watchlist?exchange=demo',
    '/api/v1/watchlist?exchange=kraken&symbols=BTC',
    '/api/v1/watchlist?exchange=demo&symbols=BTC&asof=1609459200',
    '/api/v1/watchlist?exchange=demo&symbols=BTC&bar=5m',
    '/api/v1/watchlist?exchange=demo&symbols=BTC&model=9',
    '/api/v1/watchlist?exchange=demo&symbols=BTC&symbols=ETH',
    '/api/v1/context?exchange=kraken',
    '/api/v1/context?exchange=demo&mode=wat',
    '/api/v1/context?exchange=demo&symbol=!',
    '/api/v1/context?exchange=demo&asof=1609459200',
    '/api/v1/liquidations?exchange=kraken',
    '/api/v1/liquidations?exchange=demo&range=5h',
    '/api/v1/liquidations?exchange=demo&replay=1',
    '/api/v1/liquidations?exchange=demo&replay=wat',
])
def test_market_routes_reject_bad_parameters(http_api, offline_exchanges, path):
    status, body = http_api(path)
    assert (status, body['code']) == (400, 'invalid_request')
    assert isinstance(body['error'], str)


def test_market_route_source_failures_are_503(http_api):
    from liqmap import data
    with mock.patch.object(data, '_get_json', return_value=(403, None)):
        status, body = http_api('/api/v1/context?exchange=aggregate&symbol=BTC')
        assert (status, body['code']) == (503, 'source_unavailable')
        status, body = http_api('/api/v1/liquidations?exchange=okx&symbol=BTC')
        assert (status, body['code']) == (503, 'source_unavailable')


@pytest.mark.parametrize('path', ['/api/v1/context?exchange=demo', '/api/v1/liquidations?exchange=demo',
                                  '/api/v1/watchlist?exchange=demo&symbols=BTC', '/api/context?exchange=demo'])
def test_market_routes_share_the_expensive_gate(http_api, offline_exchanges, path):
    with contextlib.ExitStack() as stack:
        for _ in range(server.MAX_EXPENSIVE_REQUESTS):
            assert server._expensive.acquire(blocking=False)
            stack.callback(server._expensive.release)
        status, body = http_api(path)
        assert (status, body['code']) == (429, 'busy')
        assert http_api('/healthz')[0] == 200
    assert http_api(path)[0] == 200


def test_heatmap_and_features_carry_displayed_market_context(http_api):
    status, heat = http_api('/api/v1/heatmap?exchange=demo&range=12h&visible_bars=24')
    assert status == 200
    context = heat['market_context']
    assert set(context) == {'price_change_pct', 'oi_usd', 'oi_change_pct'}
    assert isinstance(context['price_change_pct'], float) and context['oi_usd'] > 0
    status, features = http_api('/api/v1/features?exchange=demo&range=12h&visible_bars=24')
    assert features['market_context'] == context
    assert heat['schema_version'] == features['schema_version'] == '1.0'


def test_status_advertises_market_capabilities(http_api):
    status, body = http_api('/api/v1/status')
    assert status == 200
    assert body['capabilities']['observed_liquidations'] == ['okx']
    assert body['capabilities']['watchlist_max_symbols'] == 6
    assert 'readiness' not in body


@pytest.mark.parametrize('path', ['/%2e%2e/pyproject.toml', '/..%2fpyproject.toml', '/../pyproject.toml',
                                  '/%2e%2e%2fliqmap%2fserver.py', '//etc/passwd', '/assets/../../README.md'])
def test_static_files_never_leave_the_web_folder(http_api, path):
    status, body = http_api(path)
    assert (status, body['code']) == (404, 'not_found')


@pytest.mark.parametrize('headers,expected', [
    ({'Sec-Fetch-Site': 'cross-site'}, 403),
    ({'Host': 'attacker.example'}, 403),  # DNS rebinding: a hostile name resolving to 127.0.0.1
    ({'Host': '127.0.0.1.attacker.example:80'}, 403),
    ({'Host': 'localhost:8765'}, 200),
    ({'Host': '127.0.0.1:8765'}, 200),
    ({'Host': '[::1]:8765'}, 200),
    ({'Sec-Fetch-Site': 'same-origin'}, 200),
    ({'Sec-Fetch-Site': 'none'}, 200),  # typed into the address bar
    ({}, 200),  # curl and scripts send no Sec-Fetch headers
])
def test_loopback_server_refuses_other_websites(http_api, headers, expected):
    with mock.patch.object(service, 'load_market', side_effect=AssertionError('a refused request must not load data')):
        status, body = http_api('/api/v1/status', headers)
        assert status == expected
        if expected == 403:
            assert body['code'] == 'forbidden' and isinstance(body['error'], str)
            assert http_api('/api/v1/heatmap?range=12h', headers)[0] == 403


def test_non_loopback_bind_accepts_any_host_but_not_cross_site(http_api):
    http_api.app.server_address = ('192.0.2.10', http_api.app.server_port)  # as if started with --host 192.0.2.10
    assert http_api('/healthz', {'Host': 'liq.lan:8765'})[0] == 200
    status, body = http_api('/healthz', {'Host': 'liq.lan:8765', 'Sec-Fetch-Site': 'cross-site'})
    assert (status, body['code']) == (403, 'forbidden')
