"""Offline HTTP contract tests; no exchange connections are used."""
import contextlib
import json
import threading
import urllib.error
import urllib.request
from unittest import mock

import pytest

from liqmap import server, service
from liqmap.data import DataError, fetch_demo, range_spec


@pytest.fixture
def http_api():
    app = server.BoundedThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    def get(path):
        try:
            response = urllib.request.urlopen(f'http://127.0.0.1:{app.server_port}{path}', timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            return response.status, json.loads(raw, parse_constant=lambda value: pytest.fail(f'nonfinite JSON: {value}'))
    yield get
    app.shutdown()
    app.server_close()
    thread.join()


@pytest.fixture(autouse=True)
def clear_cache():
    service._cache.clear()
    service._spans.clear()
    yield
    service._cache.clear()
    service._spans.clear()


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


def test_health_is_distinct_from_source_readiness(http_api):
    assert http_api('/healthz') == (200, {'ok': True, 'process': 'healthy'})
    status, ready = http_api('/readyz')
    assert status == 503
    assert ready['process'] == 'healthy'
    assert ready['data']['status'] == 'unknown'
    http_api('/api/v1/heatmap?exchange=demo&range=12h')
    assert http_api('/readyz')[0] == 503


def test_stale_source_never_claims_readiness_and_source_failure_is_503(http_api):
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    loaded.source = 'fixture-exchange'
    loaded.series.exchange = 'okx'
    loaded.series.provenance['history_kind'] = 'exchange_history'
    loaded.stale = True
    with mock.patch.object(service, 'load_market', return_value=loaded):
        status, body = http_api('/api/v1/heatmap?range=12h')
    assert status == 200
    assert body['quality']['status'] == 'stale'
    assert http_api('/readyz')[0] == 503
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
    assert http_api('/api/v1/nope')[0] == 404


def test_overload_is_retryable_and_health_remains_available(http_api):
    with contextlib.ExitStack() as stack:
        for _ in range(server.MAX_EXPENSIVE_REQUESTS):
            assert server._expensive.acquire(blocking=False)
            stack.callback(server._expensive.release)
        status, body = http_api('/api/v1/heatmap?exchange=demo')
        assert (status, body['code']) == (429, 'busy')
        assert http_api('/healthz')[0] == 200


def test_ready_requires_current_default_market_observation(http_api):
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    loaded.source = 'fixture-exchange'
    loaded.series.exchange = 'okx'
    loaded.series.provenance['history_kind'] = 'exchange_history'
    with mock.patch.object(service, 'load_market', return_value=loaded):
        assert http_api('/api/v1/heatmap?symbol=ETH')[0] == 200
        assert http_api('/readyz')[0] == 503
        assert http_api('/api/v1/heatmap')[0] == 200
        status, body = http_api('/readyz')
        assert status == 200
        assert body['data']['status'] == 'observed'
        with mock.patch.object(service.time, 'time', return_value=service.time.time() + service.SERIES_TTL['24h'] + 1):
            assert http_api('/readyz')[0] == 503
    loaded.as_of = 1609459200000
    assert http_api('/readyz')[0] == 503


def test_failed_source_releases_expensive_slot(http_api):
    with mock.patch.object(service, 'load_market', side_effect=DataError('temporary outage')):
        for _ in range(server.MAX_EXPENSIVE_REQUESTS + 1):
            assert http_api('/api/v1/heatmap')[0] == 503
    assert http_api('/api/v1/heatmap?exchange=demo&range=12h')[0] == 200


def test_installed_assets_fallback_is_resolvable(tmp_path):
    with mock.patch.object(server.Path, 'is_dir', return_value=False), mock.patch.object(server.resources, 'files', return_value=tmp_path) as files:
        assert server.web_root() == tmp_path
    files.assert_called_once_with('liqmap_web')


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


def test_recently_fetched_live_source_thirty_minutes_old_is_stale_not_ready(http_api):
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
    status, ready = http_api('/readyz')
    assert status == 503
    assert ready['data']['status'] == 'stale'
    assert ready['data']['freshness_budget_seconds'] == 600
