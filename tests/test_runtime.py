"""Resource and metadata regressions using offline market data."""
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from liqmap import service
from liqmap.data import DataError, fetch_demo, range_spec


@pytest.fixture(autouse=True)
def clear_cache():
    service._cache.clear()
    service._spans.clear()
    yield
    service._cache.clear()
    service._spans.clear()


def test_data_fingerprint_stable_across_render_time_and_changes_with_input():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    with mock.patch.object(service, 'get_series', return_value=loaded):
        with mock.patch.object(service.time, 'time', return_value=1700000000):
            first = service.load_heatmap('demo', 'BTC', '12h', 1)
        with mock.patch.object(service.time, 'time', return_value=1700000100):
            second = service.load_heatmap('demo', 'BTC', '12h', 1)
        assert first['generated_at'] != second['generated_at']
        assert first['data_fingerprint'] == second['data_fingerprint']
        loaded.series.close[-1] *= 1.001
        changed = service.load_heatmap('demo', 'BTC', '12h', 1)
        assert changed['data_fingerprint'] != first['data_fingerprint']


def test_cache_and_lock_bookkeeping_are_bounded():
    with mock.patch.object(service, 'load_market', return_value=mock.sentinel.loaded):
        for idx in range(service.MAX_SERIES_CACHE + 12):
            service.get_series('demo', f'A{idx:03}', '12h')
    assert len(service._cache) <= service.MAX_SERIES_CACHE
    assert len(service._locks) <= service.MAX_SERIES_CACHE


def test_same_key_concurrent_calls_load_once():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    with mock.patch.object(service, 'load_market', return_value=loaded) as load:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: service.get_series('demo', 'BTC', '12h'), range(16)))
    assert all(value is loaded for value in results)
    assert load.call_count == 1


def test_replay_duration_is_rejected_before_network():
    with mock.patch.object(service, 'load_span') as load:
        with pytest.raises(DataError, match='730 days'):
            service.replay_payload('BTC', '12h', '5m', '1609459200', str(1609459200 + 731*86400))
    load.assert_not_called()


def test_span_cache_count_and_idle_lifetime_bounded():
    span = mock.Mock(first_end_open=1609458900, last_end_open=1609545300, frames=289, days=2, note='')
    with mock.patch.object(service, 'load_span', return_value=span):
        for idx in range(service.MAX_SPAN_CACHE + 2):
            service.replay_payload(f'A{idx:03}', '12h', '5m', '1609459200', '1609545600')
    assert len(service._spans) <= service.MAX_SPAN_CACHE
    for entry in service._spans.values():
        entry[0] = 0
    assert service._span_for('BTC', range_spec('12h'), 1609458900) is None
    assert not service._spans


def test_feature_metadata_uses_actual_masks_availability_and_closed_state():
    import numpy as np
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    loaded.series.oi_observed[3:8] = False
    loaded.series.oi_available_at = loaded.series.times.astype(float) + loaded.series.bar_seconds * 1000
    with mock.patch.object(service, 'get_series', return_value=loaded):
        payload = service.load_heatmap('demo', 'BTC', '12h', 1)
    coverage = payload['quality']['coverage']['oi_observed']
    assert coverage == {'observed': len(loaded.series.times) - 5, 'total': len(loaded.series.times)}
    assert payload['quality']['historical_availability'] == 'unknown'  # synthetic receipt times prove nothing
    assert payload['feature_as_of'] == int(loaded.series.times[-1]) + loaded.series.bar_seconds * 1000
    assert payload['model_metadata']['state_timestamp'] == 'bar_close_post_update'
    assert payload['model_metadata']['insufficient_history'] is True
    assert payload['features']['resting_long_usd'] >= 0
    assert payload['features']['persistence_usd_weighted_seconds'] >= 0
    loaded.source = 'fixture-exchange'
    loaded.series.exchange = 'okx'
    assert service._quality(loaded)['historical_availability'] == 'unknown'
    loaded.series.provenance.update(history_kind='captured', availability='recorded_receipt')
    assert service._quality(loaded)['historical_availability'] == 'known'
    loaded.series.provenance['history_kind'] = 'reconstructed_archive'
    assert service._quality(loaded)['historical_availability'] == 'unknown'
    loaded.series.provenance['history_kind'] = 'captured'
    loaded.series.oi_available_at[0] = np.nan
    assert service._quality(loaded)['historical_availability'] == 'unknown'


def test_missing_source_provenance_never_claims_verified_quality():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    loaded.source = 'fixture-exchange'
    loaded.series.exchange = 'okx'
    loaded.series.provenance = {}
    assert service._quality(loaded)['status'] == 'unknown'
    assert service._quality(loaded)['coverage_basis'] == 'inferred_from_series'


def test_fingerprint_tracks_masks_components_and_provenance_but_not_presentation():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    first = service.data_fingerprint(loaded)
    loaded.series.note = 'New explanatory text'
    loaded.series.warnings.append('New explanatory warning')
    loaded.stale = not loaded.stale
    assert service.data_fingerprint(loaded) == first
    loaded.series.oi_observed[0] = not loaded.series.oi_observed[0]
    masks = service.data_fingerprint(loaded)
    assert masks != first
    loaded.series.provenance['revision'] = 'source-correction'
    provenance = service.data_fingerprint(loaded)
    assert provenance != masks
    part = fetch_demo('ETH', 'ETHUSDT', range_spec('12h')).series
    loaded.series.components = (part,)
    components = service.data_fingerprint(loaded)
    part.oi_usd[-1] *= 1.01
    assert service.data_fingerprint(loaded) != components


def test_source_observation_gaps_are_degraded_and_synthetic_provenance_is_respected():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    loaded.source = 'fixture-exchange'
    loaded.series.exchange = 'okx'
    loaded.series.provenance['history_kind'] = 'exchange_history'
    assert service._quality(loaded)['status'] == 'observed'
    loaded.series.oi_observed[0] = False
    assert service._quality(loaded)['status'] == 'degraded'
    assert service._quality(loaded)['degraded'] is True
    loaded.series.provenance['history_kind'] = 'synthetic'
    assert service._quality(loaded)['status'] == 'synthetic'


def test_fingerprint_ignores_transport_receipt_but_keeps_event_availability():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    loaded.series.provenance['received_at_ms'] = 1700000000000
    first = service.data_fingerprint(loaded)
    loaded.series.provenance['received_at_ms'] += 60000
    assert service.data_fingerprint(loaded) == first
    loaded.series.oi_available_at = loaded.series.times.astype(float) + 300000
    assert service.data_fingerprint(loaded) != first


def test_http_transport_caps_workers_and_returns_explicit_503(monkeypatch):
    import socket
    import threading
    from liqmap import server
    monkeypatch.setattr(server, 'MAX_HTTP_WORKERS', 2)
    started = threading.Semaphore(0)
    class TrackedHandler(server.Handler):
        def setup(self):
            super().setup()
            started.release()
    app = server.BoundedThreadingHTTPServer(('127.0.0.1', 0), TrackedHandler)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    sockets = []
    try:
        for _ in range(2):
            client = socket.create_connection(app.server_address, timeout=2)
            client.sendall(b'GET /healthz HTTP/1.1\r\n')
            sockets.append(client)
            assert started.acquire(timeout=2)
        with socket.create_connection(app.server_address, timeout=2) as extra:
            reply = extra.recv(4096)
            assert b'503 Service Unavailable' in reply
            assert b'"code":"server_busy"' in reply
        assert not started.acquire(timeout=0.05), 'an overloaded connection must not start another worker'
    finally:
        for client in sockets:
            client.close()
        app.shutdown()
        app.server_close()
        thread.join()


def test_idle_partial_http_request_times_out_and_releases_worker(monkeypatch):
    import socket
    import threading
    import urllib.request
    from liqmap import server
    monkeypatch.setattr(server, 'MAX_HTTP_WORKERS', 1)
    monkeypatch.setattr(server, 'SOCKET_IDLE_TIMEOUT_S', 0.05)
    app = server.BoundedThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(app.server_address, timeout=2) as idle:
            idle.sendall(b'GET /healthz HTTP/1.1\r\n')
            assert idle.recv(4096) == b''
        assert app._worker_slots.acquire(timeout=2), 'the idle worker must release its slot'
        app._worker_slots.release()
        with urllib.request.urlopen(f'http://127.0.0.1:{app.server_port}/healthz', timeout=2) as response:
            assert response.status == 200
    finally:
        app.shutdown()
        app.server_close()
        thread.join()


def test_series_lock_wait_respects_request_budget():
    import threading
    import time
    from liqmap.data import RequestDeadlineExceeded, request_deadline
    from liqmap import service
    key = ('deadline-lock',)
    ready = threading.Event()
    release = threading.Event()

    def holder():
        with service._key_lock(key):
            ready.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert ready.wait(1)
    token = request_deadline.set(time.monotonic() + .02)
    try:
        with pytest.raises(RequestDeadlineExceeded):
            with service._key_lock(key):
                pass
    finally:
        request_deadline.reset(token)
        release.set()
        thread.join(3)
    assert key not in service._locks


@pytest.mark.parametrize('source', ['okx', 'binance-live', 'aggregate'])
def test_live_quality_uses_two_bar_age_budget_without_relabeling_history(source):
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    loaded.source = source
    loaded.series.exchange = 'binance'
    loaded.series.provenance['history_kind'] = 'exchange_history'
    loaded.series.times -= 30 * 60 * 1000
    loaded.as_of = int(loaded.series.times[-1])
    loaded.stale = False
    assert service._quality(loaded)['status'] == 'stale'
    if source == 'binance-live':
        with mock.patch.object(service, 'get_series', return_value=loaded):
            cutoff = str(loaded.as_of // 1000 + loaded.series.bar_seconds)
            historical = service.load_heatmap('binance', 'BTC', '24h', 1, asof=cutoff)
        assert historical['quality']['status'] == 'observed'
        assert historical['stale'] is False
    loaded.source = 'binance-vision'
    loaded.series.provenance['history_kind'] = 'reconstructed_archive'
    assert service._quality(loaded, historical=True)['stale'] is False


def test_client_disconnect_while_sending_response_does_not_report_internal_error(caplog):
    from liqmap import server
    handler = server.Handler.__new__(server.Handler)
    handler.path = '/api/heatmap'
    with mock.patch.object(handler, '_heatmap', side_effect=server.ClientDisconnected()), \
         mock.patch.object(handler, '_send_json') as send_error:
        handler._get()
    send_error.assert_not_called()
    assert 'request failed' not in caplog.text


def test_reset_in_response_write_is_classified_as_client_disconnect():
    from liqmap import server
    handler = server.Handler.__new__(server.Handler)
    handler.wfile = mock.Mock()
    handler.wfile.write.side_effect = ConnectionResetError('client closed')
    with mock.patch.object(handler, 'send_response'), mock.patch.object(handler, 'send_header'), \
         mock.patch.object(handler, 'end_headers'), pytest.raises(server.ClientDisconnected):
        handler._send_bytes(b'data', status=400, content_type='text/plain', cache='no-store')


def test_unrelated_connection_reset_still_reports_internal_error(caplog):
    from liqmap import server
    handler = server.Handler.__new__(server.Handler)
    handler.path = '/api/heatmap'
    with mock.patch.object(handler, '_heatmap', side_effect=ConnectionResetError('upstream')), \
         mock.patch.object(handler, '_send_json') as send_error:
        handler._get()
    assert send_error.call_args.kwargs['status'] == 500
    assert 'request failed' in caplog.text


def test_client_reset_while_waiting_for_request_does_not_escape_handler():
    from http.server import BaseHTTPRequestHandler
    from liqmap import server
    handler = server.Handler.__new__(server.Handler)
    with mock.patch.object(BaseHTTPRequestHandler, 'handle', side_effect=ConnectionResetError('client closed')):
        handler.handle()


def test_replay_accepts_the_58_day_daily_window_from_session_log():
    span = mock.Mock(first_end_open=1759276800, last_end_open=1764288000, frames=59, days=239, note='')
    with mock.patch.object(service, 'load_span', return_value=span) as load:
        result = service.replay_payload('BTC', '180d', '1d', '1759303560', '1764314940')
    assert result['frames'] == 59
    load.assert_called_once()


def test_replay_source_window_limit_precedes_network():
    with mock.patch.object(service, 'load_span') as load:
        with pytest.raises(DataError, match='1100 days'):
            service.replay_payload('BTC', '2y', '1d', '1609459200', str(1609459200 + 400*86400))
    load.assert_not_called()


def test_disconnected_browser_does_not_break_server(caplog):
    import logging
    import socket
    import struct
    import threading
    from liqmap import server

    started = threading.Event()
    resume = threading.Event()
    finished = threading.Event()
    class WaitingHandler(server.Handler):
        def _heatmap(self, query):
            started.set()
            assert resume.wait(3)
            self._send_bytes(b'x' * 65536, status=200, content_type='application/octet-stream', cache='no-store')
        def handle(self):
            try:
                super().handle()
            finally:
                finished.set()

    app = server.BoundedThreadingHTTPServer(('127.0.0.1', 0), WaitingHandler)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    caplog.set_level(logging.ERROR, logger='liqmap.server')
    client = None
    try:
        client = socket.create_connection(app.server_address, timeout=3)
        client.sendall(b'GET /api/heatmap HTTP/1.1\r\nHost: localhost\r\n\r\n')
        assert started.wait(3)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        client.close()
        client = None
        resume.set()
        assert finished.wait(3)
        assert 'request failed' not in caplog.text
        import urllib.request
        with urllib.request.urlopen(f'http://127.0.0.1:{app.server_port}/healthz', timeout=3) as response:
            assert response.status == 200
    finally:
        if client is not None:
            client.close()
        resume.set()
        app.shutdown()
        app.server_close()
        thread.join(timeout=3)


def test_idle_keepalive_timeout_is_not_logged_as_a_request_error(monkeypatch, caplog):
    import logging
    import socket
    import threading
    from liqmap import server

    monkeypatch.setattr(server, 'SOCKET_IDLE_TIMEOUT_S', 0.1)
    finished = threading.Event()
    class IdleHandler(server.Handler):
        def handle(self):
            try:
                super().handle()
            finally:
                finished.set()
    app = server.BoundedThreadingHTTPServer(('127.0.0.1', 0), IdleHandler)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    caplog.set_level(logging.INFO, logger='liqmap.server')
    try:
        with socket.create_connection(app.server_address, timeout=3):
            assert finished.wait(3)
        assert 'Request timed out' not in caplog.text
    finally:
        app.shutdown()
        app.server_close()
        thread.join(timeout=3)


def test_live_archive_fallback_is_stale_but_historical_view_is_not():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    loaded.source = 'binance-vision'
    loaded.series.exchange = 'binance'
    loaded.series.provenance['history_kind'] = 'reconstructed_archive'
    loaded.as_of -= 30 * 60 * 1000
    loaded.stale = False
    assert service._quality(loaded)['stale'] is True
    assert service._quality(loaded, historical=True)['stale'] is False


def test_aggregate_excludes_old_venue_and_reports_degraded_quality():
    from liqmap import data
    fresh = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    fresh.series.exchange, fresh.source = 'okx', 'okx'
    old = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    old.series.exchange, old.source = 'binance', 'binance-vision'
    old.series.times -= 30 * 60 * 1000
    old.as_of = int(old.series.times[-1])
    old.stale = False
    with mock.patch.object(data, 'fetch_okx', return_value=fresh), mock.patch.object(data, 'fetch_binance', return_value=old):
        loaded = data.fetch_aggregate('BTC', 'BTCUSDT', range_spec('24h'))
    assert loaded.venues == ['okx']
    quality = service._quality(loaded)
    assert quality['status'] == 'degraded'
    assert any('Binance' in warning for warning in quality['warnings'])


def test_aggregate_partial_component_coverage_is_degraded():
    from liqmap import data
    first = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    second = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    first.series.exchange, first.source = 'okx', 'okx'
    second.series.exchange, second.source = 'binance', 'binance-live'
    second.series.oi_observed[5] = False
    with mock.patch.object(data, 'fetch_okx', return_value=first), mock.patch.object(data, 'fetch_binance', return_value=second):
        loaded = data.fetch_aggregate('BTC', 'BTCUSDT', range_spec('24h'))
    quality = service._quality(loaded)
    assert quality['status'] == 'degraded'
    assert any('coverage' in warning.lower() for warning in quality['warnings'])


def test_cached_aggregate_is_reloaded_when_one_component_ages_out():
    from liqmap import data
    first = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    second = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    first.series.exchange, first.source = 'okx', 'okx'
    second.series.exchange, second.source = 'binance', 'binance-live'
    second.series.times -= (9 * 60 + 45) * 1000
    second.as_of = int(second.series.times[-1])
    now = int(first.series.times[-1] / 1000) + first.series.bar_seconds
    with mock.patch.object(data.time, 'time', return_value=now), mock.patch.object(data, 'fetch_okx', return_value=first), mock.patch.object(data, 'fetch_binance', return_value=second):
        aggregate = data.fetch_aggregate('BTC', 'BTCUSDT', range_spec('24h'))
    with mock.patch.object(service.time, 'time', return_value=now) as clock, mock.patch.object(service, 'load_market', return_value=aggregate) as load:
        service.get_series('aggregate', 'BTC', '24h', 'symbol')
        clock.return_value = now + 30
        service.get_series('aggregate', 'BTC', '24h', 'symbol')
    assert load.call_count == 2


def test_live_quality_detects_old_open_interest_even_with_new_candles():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    loaded.source = 'okx'
    loaded.series.exchange = 'okx'
    loaded.series.provenance['history_kind'] = 'exchange_history'
    loaded.series.oi_observed[-5:] = False
    assert service._quality(loaded)['stale'] is True


def test_live_stale_quality_explains_freshness_warning():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('24h'))
    loaded.source = 'binance-vision'
    loaded.series.exchange = 'binance'
    loaded.series.provenance['history_kind'] = 'reconstructed_archive'
    loaded.as_of -= 30 * 60 * 1000
    loaded.series.warnings.clear()
    assert any('stale' in warning.lower() for warning in service._quality(loaded)['warnings'])


def test_daily_live_aggregate_does_not_expire_before_two_daily_bars():
    from liqmap import data
    now = 20000 * 86400 + 20 * 3600
    with mock.patch.object(data.time, 'time', return_value=now):
        first = data.fetch_demo('BTC', 'BTCUSDT', range_spec('1y'))
        second = data.fetch_demo('BTC', 'BTCUSDT', range_spec('1y'))
        first.series.exchange, first.source = 'okx', 'okx'
        second.series.exchange, second.source = 'binance', 'binance-live'
        first.stale = data.is_stale(first.as_of, 86400)
        second.stale = data.is_stale(second.as_of, 86400)
        with mock.patch.object(data, 'fetch_okx', return_value=first), mock.patch.object(data, 'fetch_binance', return_value=second):
            loaded = data.fetch_aggregate('BTC', 'BTCUSDT', range_spec('1y'))
        assert loaded.venues == ['okx', 'binance']
        assert service._quality(first)['stale'] is False
