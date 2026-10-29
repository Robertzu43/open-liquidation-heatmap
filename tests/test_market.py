"""Offline tests for live context, observed liquidations and the watchlist; no network is used."""
import time
from unittest import mock

import numpy as np
import pytest

from liqmap import data, market, service
from liqmap.data import DataError, RequestDeadlineExceeded, fetch_demo, range_spec, request_deadline
from liqmap.service import SourceUnavailable

NOW_MS = 1793289000000

# Shapes captured from the live OKX public API (2026-10-29).
OKX_FUNDING = {'code': '0', 'msg': '', 'data': [{
    'fundingRate': '0.0000979766647128', 'fundingTime': '1793289600000', 'nextFundingTime': '1793318400000',
    'prevFundingTime': '1793260800000', 'instId': 'BTC-USDT-SWAP', 'instType': 'SWAP', 'method': 'current_period',
    'nextFundingRate': '', 'settFundingRate': '0.0000338041564747', 'ts': '1793288954495'}]}
OKX_OI = {'code': '0', 'msg': '', 'data': [{
    'instId': 'BTC-USDT-SWAP', 'instType': 'SWAP', 'oi': '3177321.14', 'oiCcy': '31773.2114',
    'oiUsd': '2595531398.01802899', 'ts': '1793288987964'}]}
OKX_MARK = {'code': '0', 'msg': '', 'data': [{'instId': 'BTC-USDT-SWAP', 'instType': 'SWAP', 'markPx': '81629.8', 'ts': '1793288988319'}]}
OKX_INSTRUMENT = {'code': '0', 'msg': '', 'data': [{
    'instId': 'BTC-USDT-SWAP', 'instType': 'SWAP', 'ctVal': '0.01', 'ctValCcy': 'BTC', 'ctMult': '1',
    'ctType': 'linear', 'settleCcy': 'USDT', 'uly': 'BTC-USDT', 'state': 'live'}]}
OKX_UNKNOWN = {'code': '51001', 'data': [], 'msg': "Instrument ID, Instrument ID code, or Spread ID doesn't exist."}
# Documented Binance USDT-M and Bybit v5 shapes.
BINANCE_PREMIUM = {'symbol': 'BTCUSDT', 'markPrice': '81600.10000000', 'indexPrice': '81620.0', 'estimatedSettlePrice': '81610.0',
                   'lastFundingRate': '0.00010000', 'interestRate': '0.00010000', 'nextFundingTime': 1793289600000, 'time': 1793288990000}
BINANCE_OI = {'openInterest': '80000.500', 'symbol': 'BTCUSDT', 'time': 1793288980000}
BYBIT_TICKERS = {'retCode': 0, 'retMsg': 'OK', 'result': {'category': 'linear', 'list': [{
    'symbol': 'BTCUSDT', 'lastPrice': '81610.00', 'indexPrice': '81620.50', 'markPrice': '81611.20',
    'openInterest': '104000.246', 'openInterestValue': '8487620001.10', 'singleOpenInterest': '52000.123',
    'singleOpenInterestValue': '4243810000.55', 'fundingRate': '-0.00005',
    'nextFundingTime': '1793289600000', 'fundingIntervalHour': '8', 'fundingCap': '0.005'}]}, 'retExtInfo': {}, 'time': 1793288995000}


@pytest.fixture(autouse=True)
def clear_caches():
    def clear():
        service._cache.clear()
        market._context_cache.clear()
        market._liquidation_cache.clear()
        market._instruments.clear()
    clear()
    yield
    clear()


def router(routes):
    """A stand-in for data._get_json that answers by URL suffix and records each call."""
    calls = []

    def get_json(url, params=None, **kwargs):
        calls.append((url, dict(params or {}), kwargs))
        for suffix, response in routes.items():
            if url.endswith(suffix):
                result = response(params or {}) if callable(response) else response
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f'unexpected URL {url}')

    get_json.calls = calls
    return get_json


LIVE_ROUTES = {
    '/api/v5/public/funding-rate': (200, OKX_FUNDING),
    '/api/v5/public/open-interest': (200, OKX_OI),
    '/api/v5/public/mark-price': (200, OKX_MARK),
    '/fapi/v1/premiumIndex': (200, BINANCE_PREMIUM),
    '/fapi/v1/openInterest': (200, BINANCE_OI),
    '/fapi/v1/fundingInfo': (200, [{'symbol': 'ETHUSDT', 'fundingIntervalHours': 4}]),
    '/v5/market/tickers': (200, BYBIT_TICKERS),
}


def test_okx_context_parses_live_shapes_with_upcoming_settlement_and_interval(monkeypatch):
    fake = router(LIVE_ROUTES)
    monkeypatch.setattr(data, '_get_json', fake)
    payload = market.context_payload('okx', 'btc')
    assert payload['schema_version'] == '1.0' and payload['synthetic'] is False
    assert (payload['coin'], payload['symbol'], payload['exchange'], payload['mode']) == ('BTC', 'BTCUSDT', 'okx', 'pair')
    [venue] = payload['venues']
    assert venue == {
        'exchange': 'okx', 'name': 'OKX', 'ok': True, 'funding_rate': pytest.approx(0.0000979766647128),
        'next_funding_time': 1793289600000, 'funding_interval_hours': 8,
        'open_interest_base': pytest.approx(31773.2114), 'open_interest_usd': pytest.approx(2595531398.018029),
        'mark_price': pytest.approx(81629.8), 'observed_at': 1793288954495, 'error': None,
    }
    assert payload['funding_rate_oi_weighted'] == pytest.approx(venue['funding_rate'])
    assert payload['open_interest_usd_total'] == pytest.approx(venue['open_interest_usd'])
    assert all(call[2] == {'timeout': (4, 8), 'retries': 1} for call in fake.calls)
    assert {call[1].get('instId') for call in fake.calls} == {'BTC-USDT-SWAP'}


def test_okx_context_without_mark_price_stays_observed(monkeypatch):
    routes = dict(LIVE_ROUTES)
    routes['/api/v5/public/mark-price'] = DataError('Could not reach www.okx.com after 1 tries.')
    monkeypatch.setattr(data, '_get_json', router(routes))
    venue = market.context_payload('okx', 'BTC')['venues'][0]
    assert venue['ok'] is True and venue['mark_price'] is None
    assert venue['open_interest_usd'] == pytest.approx(2595531398.018029)


def test_binance_context_values_base_open_interest_at_mark_and_reads_interval(monkeypatch):
    routes = dict(LIVE_ROUTES)
    routes['/fapi/v1/fundingInfo'] = (200, [{'symbol': 'BTCUSDT', 'fundingIntervalHours': 4}])
    monkeypatch.setattr(data, '_get_json', router(routes))
    venue = market.context_payload('binance', 'BTC')['venues'][0]
    assert venue['ok'] is True
    assert venue['funding_rate'] == pytest.approx(0.0001)
    assert venue['next_funding_time'] == 1793289600000
    assert venue['funding_interval_hours'] == 4
    assert venue['open_interest_base'] == pytest.approx(80000.5)
    assert venue['open_interest_usd'] == pytest.approx(80000.5 * 81600.1)
    assert venue['mark_price'] == pytest.approx(81600.1)
    assert venue['observed_at'] == 1793288980000


def test_binance_interval_defaults_to_eight_hours_only_when_funding_info_answered(monkeypatch):
    monkeypatch.setattr(data, '_get_json', router(LIVE_ROUTES))
    assert market.context_payload('binance', 'BTC')['venues'][0]['funding_interval_hours'] == 8
    market._context_cache.clear()
    routes = dict(LIVE_ROUTES)
    routes['/fapi/v1/fundingInfo'] = (451, {'code': 0, 'msg': 'Service unavailable from a restricted location'})
    monkeypatch.setattr(data, '_get_json', router(routes))
    assert market.context_payload('binance', 'BTC')['venues'][0]['funding_interval_hours'] is None


@pytest.mark.parametrize('status,body,message', [
    (451, {'code': 0, 'msg': 'Service unavailable from a restricted location'}, 'HTTP 451'),
    (400, {'code': -1121, 'msg': 'Invalid symbol.'}, 'Binance has no BTCUSDT perpetual'),
    (418, None, 'HTTP 418'),
])
def test_binance_failures_are_reported_and_never_replaced(monkeypatch, status, body, message):
    routes = dict(LIVE_ROUTES)
    routes['/fapi/v1/premiumIndex'] = (status, body)
    monkeypatch.setattr(data, '_get_json', router(routes))
    with pytest.raises(SourceUnavailable, match=message):
        market.context_payload('binance', 'BTC')


def test_bybit_context_parses_ticker_and_top_level_time(monkeypatch):
    monkeypatch.setattr(data, '_get_json', router(LIVE_ROUTES))
    venue = market.context_payload('bybit', 'BTC')['venues'][0]
    assert venue == {
        'exchange': 'bybit', 'name': 'Bybit', 'ok': True, 'funding_rate': pytest.approx(-0.00005),
        'next_funding_time': 1793289600000, 'funding_interval_hours': 8,
        'open_interest_base': pytest.approx(52000.123), 'open_interest_usd': pytest.approx(4243810000.55),
        'mark_price': pytest.approx(81611.2), 'observed_at': 1793288995000, 'error': None,
    }


def test_bybit_context_without_interval_field_reports_null_interval(monkeypatch):
    ticker = {**BYBIT_TICKERS['result']['list'][0]}
    ticker.pop('fundingIntervalHour')
    body = {**BYBIT_TICKERS, 'result': {'category': 'linear', 'list': [ticker]}}
    monkeypatch.setattr(data, '_get_json', router({'/v5/market/tickers': (200, body)}))
    venue = market.context_payload('bybit', 'BTC')['venues'][0]
    assert venue['ok'] is True and venue['funding_interval_hours'] is None
    payload = market.context_payload('bybit', 'BTC')
    assert payload['funding_rate_oi_weighted'] is None  # An unknown interval cannot be scaled.
    assert payload['open_interest_usd_total'] == pytest.approx(4243810000.55)


def test_aggregate_context_isolates_failed_venues(monkeypatch):
    routes = dict(LIVE_ROUTES)
    routes['/fapi/v1/premiumIndex'] = (451, {'code': 0, 'msg': 'restricted location'})
    routes['/v5/market/tickers'] = (403, None)
    monkeypatch.setattr(data, '_get_json', router(routes))
    payload = market.context_payload('aggregate', 'BTC')
    assert (payload['exchange'], payload['mode']) == ('aggregate', 'symbol')
    assert [venue['exchange'] for venue in payload['venues']] == ['okx', 'binance', 'bybit']
    okx, binance, bybit = payload['venues']
    assert okx['ok'] is True and okx['error'] is None
    assert binance['ok'] is False and 'HTTP 451' in binance['error'] and binance['funding_rate'] is None
    assert bybit['ok'] is False and 'blocks some regions' in bybit['error']
    assert payload['open_interest_usd_total'] == pytest.approx(okx['open_interest_usd'])
    assert payload['funding_rate_oi_weighted'] == pytest.approx(okx['funding_rate'])
    assert '1 of 3 venues' in payload['note'] and 'Binance, Bybit unavailable' in payload['note']


def test_symbol_mode_context_queries_every_venue_and_weights_by_open_interest(monkeypatch):
    monkeypatch.setattr(data, '_get_json', router(LIVE_ROUTES))
    payload = market.context_payload('okx', 'BTC', 'symbol')
    assert payload['exchange'] == 'aggregate'
    assert all(venue['ok'] for venue in payload['venues'])
    usd = [venue['open_interest_usd'] for venue in payload['venues']]
    rates = [venue['funding_rate'] for venue in payload['venues']]
    assert payload['open_interest_usd_total'] == pytest.approx(sum(usd))
    assert payload['funding_rate_oi_weighted'] == pytest.approx(np.dot(rates, usd) / sum(usd))


def test_weighted_funding_scales_each_rate_to_the_eight_hour_basis():
    rows = [market._venue_row('okx', ok=True, funding_rate=0.0001, funding_interval_hours=8, open_interest_usd=100.0),
            market._venue_row('bybit', ok=True, funding_rate=0.0001, funding_interval_hours=4, open_interest_usd=100.0),
            market._venue_row('binance', ok=False, error='down')]
    assert market._weighted_funding(rows) == pytest.approx(0.00015)


def test_every_venue_failing_is_source_unavailable_and_not_cached(monkeypatch):
    fake = router({'/api/v5/public/funding-rate': DataError('Could not reach www.okx.com after 1 tries.'),
                   '/fapi/v1/premiumIndex': (451, None), '/v5/market/tickers': (403, None)})
    monkeypatch.setattr(data, '_get_json', fake)
    with pytest.raises(SourceUnavailable, match='No live funding or open interest'):
        market.context_payload('aggregate', 'BTC')
    assert len(market._context_cache) == 0
    with pytest.raises(SourceUnavailable):
        market.context_payload('okx', 'NOPE')


def test_unknown_okx_instrument_is_named(monkeypatch):
    monkeypatch.setattr(data, '_get_json', router({'/api/v5/public/funding-rate': (200, OKX_UNKNOWN)}))
    with pytest.raises(SourceUnavailable, match='OKX has no NOPE-USDT-SWAP perpetual swap'):
        market.context_payload('okx', 'NOPE')


def test_unexpected_venue_exception_is_isolated(monkeypatch):
    monkeypatch.setattr(data, '_get_json', router(LIVE_ROUTES))
    monkeypatch.setitem(market._CONTEXT_READERS, 'bybit', mock.Mock(side_effect=KeyError('surprise')))
    payload = market.context_payload('aggregate', 'BTC')
    assert payload['venues'][2] == market._venue_row('bybit', ok=False, error='Bybit failed (KeyError).')
    assert payload['venues'][0]['ok'] is True


def test_context_deadline_reaches_venue_threads_and_propagates(monkeypatch):
    seen = []

    def reader(coin):
        seen.append(request_deadline.get())
        raise RequestDeadlineExceeded('budget exhausted')

    for name in data.AGGREGATE_VENUES:
        monkeypatch.setitem(market._CONTEXT_READERS, name, reader)
    token = request_deadline.set(time.monotonic() + 30)
    try:
        with pytest.raises(RequestDeadlineExceeded):
            market.context_payload('aggregate', 'BTC')
        expected = request_deadline.get()
    finally:
        request_deadline.reset(token)
    assert seen == [expected] * 3


def test_context_cache_lasts_thirty_seconds_and_is_bounded(monkeypatch):
    reader = mock.Mock(side_effect=lambda coin: market._venue_row('okx', ok=True, funding_rate=0.0001))
    monkeypatch.setitem(market._CONTEXT_READERS, 'okx', reader)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(market.time, 'time', lambda: clock[0])
    market.context_payload('okx', 'BTC')
    market.context_payload('okx', 'BTC')
    assert reader.call_count == 1
    clock[0] += market.CONTEXT_TTL_S
    market.context_payload('okx', 'BTC')
    assert reader.call_count == 2
    for idx in range(market.MAX_CONTEXT_CACHE + 8):
        market.context_payload('okx', f'C{idx:03}')
    assert len(market._context_cache) == market.MAX_CONTEXT_CACHE
    assert not any(key[0] == 'context' for key in service._locks)


def test_cached_context_rows_cannot_be_mutated_by_a_caller(monkeypatch):
    monkeypatch.setattr(data, '_get_json', router(LIVE_ROUTES))
    first = market.context_payload('okx', 'BTC')
    first['venues'][0]['funding_rate'] = 1.0
    assert market.context_payload('okx', 'BTC')['venues'][0]['funding_rate'] == pytest.approx(0.0000979766647128)


def test_demo_context_is_synthetic_deterministic_and_offline(monkeypatch):
    monkeypatch.setattr(data, '_get_json', mock.Mock(side_effect=AssertionError('demo must not contact an exchange')))
    first = market.context_payload('demo', 'ETH')
    second = market.context_payload('demo', 'ETH')
    assert first['synthetic'] is True and first['exchange'] == 'demo'
    [venue] = first['venues']
    assert venue['exchange'] == 'demo' and venue['ok'] is True and venue['funding_interval_hours'] == 8
    assert venue == second['venues'][0]
    assert venue['open_interest_usd'] == pytest.approx(venue['open_interest_base'] * venue['mark_price'])
    assert 'synthetic' in first['note'].lower()


@pytest.mark.parametrize('kwargs', [{'asof': '1609459200'}, {'bar': '5m'}, {'replay': True}])
def test_context_is_live_only(kwargs):
    with pytest.raises(DataError, match='live only'):
        market.context_payload('okx', 'BTC', **kwargs)


# Observed OKX liquidations.


def liq(ts, px='80000', sz='2', pos='long', side='sell'):
    return {'bkLoss': '0', 'bkPx': px, 'ccy': '', 'posSide': pos, 'side': side, 'sz': sz, 'time': int(ts), 'ts': str(ts)}


class FakeLiquidations:
    """OKX liquidation-orders pages: newest first; ``after`` returns strictly older orders."""

    def __init__(self, rows, fail_page=None):
        self.rows = sorted(rows, key=lambda row: -int(row['ts']))
        self.fail_page = fail_page
        self.params = []

    def __call__(self, params):
        self.params.append(dict(params))
        if self.fail_page is not None and len(self.params) == self.fail_page:
            return 200, {'code': '50011', 'msg': 'Too Many Requests', 'data': []}
        after = int(params['after']) if 'after' in params else None
        eligible = [row for row in self.rows if after is None or int(row['ts']) < after]
        chunk = eligible[:int(params['limit'])]
        return 200, {'code': '0', 'msg': '', 'data': [{'details': chunk, 'instFamily': 'BTC-USDT', 'instId': 'BTC-USDT-SWAP',
                                                        'instType': 'SWAP', 'totalLoss': '0', 'uly': 'BTC-USDT'}]}


def use_liquidations(monkeypatch, fake, instrument=(200, OKX_INSTRUMENT)):
    monkeypatch.setattr(market.time, 'time', lambda: NOW_MS / 1000)
    get_json = router({'/api/v5/public/instruments': instrument, '/api/v5/public/liquidation-orders': fake})
    monkeypatch.setattr(data, '_get_json', get_json)
    return get_json


@pytest.mark.parametrize('pos,side,expected', [
    ('long', 'sell', 'long'), ('short', 'buy', 'short'), ('net', 'sell', 'long'),
    ('net', 'buy', 'short'), ('', 'SELL', 'long'), (None, None, None), ('net', 'hold', None)])
def test_liquidated_position_side(pos, side, expected):
    assert market.liquidated_side(pos, side) == expected


def test_okx_liquidations_value_contracts_in_base_and_sort_ascending(monkeypatch):
    rows = [liq(NOW_MS - 1000, px='80000', sz='2', pos='long', side='sell'),
            liq(NOW_MS - 3000, px='81000', sz='5', pos='net', side='buy'),
            liq(NOW_MS - 2000, px='80500', sz='1', pos='net', side='sell'),
            liq(NOW_MS - 4000, px='bad', sz='1')]
    fake = use_liquidations(monkeypatch, FakeLiquidations(rows))
    payload = market.liquidations_payload('okx', 'BTC', 'pair', '12h')
    assert (payload['source'], payload['available'], payload['synthetic']) == ('okx', True, False)
    assert payload['events'] == [
        {'ts': NOW_MS - 3000, 'price': 81000.0, 'side': 'short', 'size_base': pytest.approx(0.05), 'usd': pytest.approx(0.05 * 81000)},
        {'ts': NOW_MS - 2000, 'price': 80500.0, 'side': 'long', 'size_base': pytest.approx(0.01), 'usd': pytest.approx(0.01 * 80500)},
        {'ts': NOW_MS - 1000, 'price': 80000.0, 'side': 'long', 'size_base': pytest.approx(0.02), 'usd': pytest.approx(1600)},
    ]
    assert payload['coverage'] == {'from_ms': NOW_MS - 3000, 'to_ms': NOW_MS - 1000, 'count': 3, 'truncated': False}
    assert payload['note'] == market.OKX_LIQUIDATION_NOTE
    first = [params for url, params, _kw in fake.calls if url.endswith('liquidation-orders')][0]
    assert first == {'instType': 'SWAP', 'uly': 'BTC-USDT', 'state': 'filled', 'limit': '100'}


def test_okx_liquidation_pages_use_after_cursor_and_keep_shared_millisecond_orders(monkeypatch):
    rows = [liq(NOW_MS - 1000 - i * 1000, sz=str(1 + i)) for i in range(250)]
    boundary = NOW_MS - 1000 - 98 * 1000
    for i in (98, 99, 100):
        rows[i] = liq(boundary, sz='7')  # Identical orders at one millisecond straddle the page edge.
    rows[101] = liq(boundary, sz='9')
    pages = FakeLiquidations(rows)
    use_liquidations(monkeypatch, pages)
    payload = market.liquidations_payload('okx', 'BTC', 'pair', '12h')
    assert payload['coverage']['count'] == 250
    assert sum(event['ts'] == boundary for event in payload['events']) == 4
    assert [params.get('after') for params in pages.params] == [None, str(boundary + 1), str(NOW_MS - 1000 - 197 * 1000 + 1)]
    assert payload['coverage']['truncated'] is False


def test_okx_liquidations_stop_at_window_and_filter_older_orders(monkeypatch):
    rows = [liq(NOW_MS - (i + 1) * 600_000) for i in range(150)]  # One every 10 minutes for 25 hours.
    pages = FakeLiquidations(rows)
    use_liquidations(monkeypatch, pages)
    payload = market.liquidations_payload('okx', 'BTC', 'pair', '12h')
    assert len(pages.params) == 1
    assert payload['coverage']['count'] == 72
    assert payload['events'][0]['ts'] >= NOW_MS - 12 * 3600 * 1000
    assert payload['coverage']['truncated'] is False


def test_okx_liquidation_page_cap_marks_coverage_truncated(monkeypatch):
    rows = [liq(NOW_MS - 1000 - i * 10) for i in range(1200)]
    pages = FakeLiquidations(rows)
    use_liquidations(monkeypatch, pages)
    payload = market.liquidations_payload('okx', 'BTC', 'pair', '24h')
    assert len(pages.params) == market.MAX_LIQUIDATION_PAGES
    # Each later page re-reads the order at its boundary millisecond, which is skipped once.
    assert payload['coverage']['count'] == 100 + 9 * 99
    assert len({event['ts'] for event in payload['events']}) == payload['coverage']['count']
    assert payload['coverage']['truncated'] is True


def test_okx_later_page_failure_keeps_observed_pages_and_marks_truncated(monkeypatch):
    rows = [liq(NOW_MS - 1000 - i * 10) for i in range(300)]
    use_liquidations(monkeypatch, FakeLiquidations(rows, fail_page=2))
    payload = market.liquidations_payload('okx', 'BTC', 'pair', '24h')
    assert payload['coverage']['count'] == 100
    assert payload['coverage']['truncated'] is True


def test_okx_liquidation_failures_are_source_unavailable(monkeypatch):
    use_liquidations(monkeypatch, FakeLiquidations([liq(NOW_MS - 1000)], fail_page=1))
    with pytest.raises(SourceUnavailable, match='OKX rejected'):
        market.liquidations_payload('okx', 'BTC', 'pair', '24h')
    use_liquidations(monkeypatch, FakeLiquidations([]), instrument=(200, OKX_UNKNOWN))
    with pytest.raises(SourceUnavailable, match='OKX has no NOPE-USDT-SWAP'):
        market.liquidations_payload('okx', 'NOPE', 'pair', '24h')
    bad = {'code': '0', 'data': [{**OKX_INSTRUMENT['data'][0], 'ctValCcy': 'USDT'}]}
    market._instruments.clear()
    use_liquidations(monkeypatch, FakeLiquidations([]), instrument=(200, bad))
    with pytest.raises(SourceUnavailable, match='contract value'):
        market.liquidations_payload('okx', 'BTC', 'pair', '24h')


def test_empty_okx_answer_is_an_observed_empty_window(monkeypatch):
    use_liquidations(monkeypatch, lambda params: (200, {'code': '0', 'msg': '', 'data': []}))
    payload = market.liquidations_payload('okx', 'BTC', 'pair', '12h')
    assert payload['events'] == []
    assert payload['coverage'] == {'from_ms': None, 'to_ms': None, 'count': 0, 'truncated': False}


def test_aggregate_and_symbol_mode_liquidations_use_okx_and_say_so(monkeypatch):
    use_liquidations(monkeypatch, FakeLiquidations([liq(NOW_MS - 1000)]))
    for args in (('aggregate', 'BTC', 'pair'), ('bybit', 'BTC', 'symbol')):
        payload = market.liquidations_payload(*args, '24h')
        assert payload['source'] == 'okx' and payload['exchange'] == 'aggregate' and payload['available'] is True
        assert 'Binance and Bybit liquidations are not included' in payload['note']


@pytest.mark.parametrize('exchange', ['binance', 'bybit'])
def test_binance_and_bybit_pair_liquidations_are_unavailable_without_network(monkeypatch, exchange):
    monkeypatch.setattr(data, '_get_json', mock.Mock(side_effect=AssertionError('no request expected')))
    payload = market.liquidations_payload(exchange, 'BTC', 'pair', '24h')
    assert payload['available'] is False and payload['events'] == [] and payload['source'] is None
    assert payload['coverage'] == {'from_ms': None, 'to_ms': None, 'count': 0, 'truncated': False}
    assert 'OKX only' in payload['note']


def test_liquidation_and_instrument_caches_are_bounded(monkeypatch):
    fake = use_liquidations(monkeypatch, FakeLiquidations([liq(NOW_MS - 1000)]))
    market.liquidations_payload('okx', 'BTC', 'pair', '24h')
    market.liquidations_payload('okx', 'BTC', 'pair', '24h')
    assert sum(url.endswith('liquidation-orders') for url, _params, _kw in fake.calls) == 1
    instrument = lambda params: (200, {'code': '0', 'data': [{**OKX_INSTRUMENT['data'][0], 'ctValCcy': params['instId'].split('-')[0]}]})
    use_liquidations(monkeypatch, FakeLiquidations([liq(NOW_MS - 1000)]), instrument=instrument)
    monkeypatch.setattr(market._instruments, 'max_entries', 5)
    for idx in range(market.MAX_LIQUIDATION_CACHE + 8):
        market.liquidations_payload('okx', f'C{idx:03}', 'pair', '24h')
    assert len(market._liquidation_cache) == market.MAX_LIQUIDATION_CACHE
    assert len(market._instruments) == 5


def test_demo_liquidations_sit_inside_bars_near_the_matching_extreme(monkeypatch):
    monkeypatch.setattr(data, '_get_json', mock.Mock(side_effect=AssertionError('demo must not contact an exchange')))
    monkeypatch.setattr(data.time, 'time', lambda: NOW_MS / 1000)
    payload = market.liquidations_payload('demo', 'SOL', 'pair', '24h')
    assert (payload['source'], payload['available'], payload['synthetic']) == ('demo', True, True)
    series = fetch_demo('SOL', 'SOLUSDT', range_spec('24h')).series
    bar_ms = series.bar_seconds * 1000
    events = payload['events']
    assert len(events) >= 4 and [event['ts'] for event in events] == sorted(event['ts'] for event in events)
    assert {event['side'] for event in events} == {'long', 'short'}
    for event in events:
        i = int(np.searchsorted(series.times, event['ts'], side='right') - 1)
        assert series.times[i] < event['ts'] < series.times[i] + bar_ms
        span = series.high[i] - series.low[i]
        if event['side'] == 'long':
            assert series.low[i] <= event['price'] <= series.low[i] + 0.15 * span + 1e-9
        else:
            assert series.high[i] - 0.15 * span - 1e-9 <= event['price'] <= series.high[i]
        assert event['usd'] == pytest.approx(event['size_base'] * event['price'])
    assert payload['coverage']['count'] == len(events)
    assert market.liquidations_payload('demo', 'SOL', 'pair', '24h')['events'] == events


def test_liquidations_validate_parameters():
    for args in (('kraken', 'BTC', 'pair', '24h'), ('okx', '!', 'pair', '24h'), ('okx', 'BTC', 'x', '24h'), ('okx', 'BTC', 'pair', '5h')):
        with pytest.raises(DataError):
            market.liquidations_payload(*args)
    with pytest.raises(DataError, match='live only'):
        market.liquidations_payload('okx', 'BTC', 'pair', '24h', asof='1609459200')


# Watchlist.


def test_watchlist_symbols_are_normalized_unique_and_bounded():
    assert market.watchlist_symbols(' btc, ETHUSDT ,BTC,sol,') == ['BTC', 'ETH', 'SOL']
    assert market.watchlist_symbols('BTC,!') == ['BTC', '!']  # The invalid one fails as its own item.
    for raw in ('', ' , ', 'A1,B2,C3,D4,E5,F6,G7'):
        with pytest.raises(DataError):
            market.watchlist_symbols(raw)


def test_watchlist_matches_main_chart_analysis_for_each_symbol():
    payload = market.watchlist_payload('demo', '12h', '2', 'pair', 'SOL,BTC')
    assert {key: payload[key] for key in ('schema_version', 'exchange', 'mode', 'range', 'model')} == {
        'schema_version': '1.0', 'exchange': 'demo', 'mode': 'pair', 'range': '12h', 'model': 2}
    assert [item['coin'] for item in payload['items']] == ['SOL', 'BTC']
    for item in payload['items']:
        chart = service.load_heatmap('demo', item['coin'], '12h', 2)
        analysis = chart['analysis']
        assert item['ok'] is True and item['error'] is None
        assert item['price'] == analysis['price']
        assert item['price_change_pct'] == pytest.approx(chart['market_context']['price_change_pct'])
        assert (item['quality'], item['stale']) == ('synthetic', False)
        assert item['above'] == {key: analysis['above'][key] for key in ('price', 'distance_pct', 'usd')}
        assert item['below'] == {key: analysis['below'][key] for key in ('price', 'distance_pct', 'usd')}
        assert item['above']['price'] > item['price'] > item['below']['price']
        assert item['short_to_long_5pct'] == analysis['imbalance'][2]['short_to_long']
        assert item['as_of'] == chart['as_of']


def test_one_watchlist_failure_does_not_fail_the_others():
    real = service.get_series

    def flaky(exchange, coin, *args):
        if coin == 'ETH':
            raise SourceUnavailable('OKX has no ETH-USDT-SWAP perpetual swap.')
        if coin == 'XRP':
            raise RuntimeError('unexpected')
        return real('demo', coin, *args)

    with mock.patch.object(market, 'get_series', side_effect=flaky):
        payload = market.watchlist_payload('okx', '12h', '1', 'pair', 'BTC,ETH,XRP,SOL')
    assert [item['coin'] for item in payload['items']] == ['BTC', 'ETH', 'XRP', 'SOL']
    btc, eth, xrp, sol = payload['items']
    assert btc['ok'] is True and sol['ok'] is True
    assert eth == market._failed_item('ETH', 'OKX has no ETH-USDT-SWAP perpetual swap.')
    assert xrp['ok'] is False and xrp['error'] == 'The server failed while building this symbol.'
    assert all(xrp[key] is None for key in xrp if key not in {'coin', 'ok', 'error'})


def test_watchlist_runs_with_the_request_deadline_and_propagates_it():
    seen = []

    def expired(*args):
        seen.append(request_deadline.get())
        raise RequestDeadlineExceeded('budget exhausted')

    token = request_deadline.set(time.monotonic() + 30)
    try:
        with mock.patch.object(market, 'get_series', side_effect=expired):
            with pytest.raises(RequestDeadlineExceeded):
                market.watchlist_payload('demo', '12h', '1', 'pair', 'BTC,ETH,SOL')
        expected = request_deadline.get()
    finally:
        request_deadline.reset(token)
    assert seen == [expected] * 3


@pytest.mark.parametrize('kwargs', [
    {'symbols': 'A1,B2,C3,D4,E5,F6,G7'}, {'symbols': ''}, {'symbols': 'BTC', 'exchange': 'kraken'},
    {'symbols': 'BTC', 'model': '4'}, {'symbols': 'BTC', 'model': 'x'}, {'symbols': 'BTC', 'range_key': '5h'},
    {'symbols': 'BTC', 'asof': '1609459200'}, {'symbols': 'BTC', 'bar': '5m'}, {'symbols': 'BTC', 'mode': 'x'}])
def test_watchlist_rejects_invalid_requests_before_loading(kwargs):
    with mock.patch.object(market, 'get_series', side_effect=AssertionError('validation must precede loading')):
        with pytest.raises(DataError):
            market.watchlist_payload(**{'exchange': 'demo', 'range_key': '12h', 'model': '1', 'mode': 'pair', **kwargs})


# Market context in heatmap and features payloads.


def test_market_context_uses_displayed_window_and_observed_open_interest():
    loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
    series = loaded.series
    series.oi_observed[-1] = False
    with mock.patch.object(service, 'get_series', return_value=loaded):
        full = service.load_heatmap('demo', 'BTC', '12h', 1)
        tail = service.load_heatmap('demo', 'BTC', '12h', 1, visible_bars=20)
        features = service.features_payload('demo', 'BTC', '12h', 1, visible_bars=20)
    context = tail['market_context']
    assert features['market_context'] == context
    assert tail['times'][0] == int(series.times[-20])
    assert context['price_change_pct'] == pytest.approx((series.close[-1] / series.close[-20] - 1) * 100)
    assert context['oi_usd'] == pytest.approx(series.oi_usd[-2])
    assert context['oi_change_pct'] == pytest.approx((series.oi_usd[-2] / series.oi_usd[-20] - 1) * 100)
    assert full['market_context']['price_change_pct'] == pytest.approx((series.close[-1] / series.close[0] - 1) * 100)


def test_market_context_is_null_without_observed_open_interest():
    series = fetch_demo('BTC', 'BTCUSDT', range_spec('12h')).series
    series.oi_observed[:] = False
    context = service.market_context(series)
    assert context['oi_usd'] is None and context['oi_change_pct'] is None
    series.oi_observed[5] = True
    context = service.market_context(series)
    assert context['oi_usd'] == pytest.approx(series.oi_usd[5]) and context['oi_change_pct'] is None
    series.close[0] = np.nan
    assert service.market_context(series)['price_change_pct'] is None


def test_aggregate_market_context_counts_only_bars_every_venue_observed():
    parts = []
    for name in ('okx', 'binance'):
        loaded = fetch_demo('BTC', 'BTCUSDT', range_spec('12h'))
        loaded.series.exchange, loaded.source = name, name
        parts.append(loaded)
    # A gap at either end must not read as an open-interest change.
    parts[1].series.oi_observed[[0, -1]] = False
    with mock.patch.object(data, 'fetch_okx', return_value=parts[0]), mock.patch.object(data, 'fetch_binance', return_value=parts[1]), \
         mock.patch.object(data, 'fetch_bybit', side_effect=DataError('Bybit is not reachable from this network (HTTP 403).')):
        loaded = data.fetch_aggregate('BTC', 'BTCUSDT', range_spec('12h'))
    context = service.market_context(loaded.series)
    total = parts[0].series.oi_usd + parts[1].series.oi_usd
    assert loaded.venues == ['okx', 'binance']
    assert context['oi_usd'] == pytest.approx(total[-2])
    assert context['oi_change_pct'] == pytest.approx((total[-2] / total[1] - 1) * 100)


def test_status_and_symbols_describe_capabilities():
    status = service.status_payload()
    assert status['capabilities'] == {'context_venues': ['okx', 'binance', 'bybit'],
                                      'observed_liquidations': ['okx'], 'watchlist_max_symbols': 6}
    assert any(row['id'] == 'bybit' and row['live'] for row in status['exchanges'])


@pytest.mark.parametrize('range_key,truncated', [('12h', False), ('24h', True), ('7d', True)])
def test_ranges_older_than_okx_liquidation_retention_are_marked_incomplete(monkeypatch, range_key, truncated):
    rows = [liq(NOW_MS - (i + 1) * 600_000) for i in range(60)]  # The feed runs out after 10 hours.
    use_liquidations(monkeypatch, FakeLiquidations(rows))
    payload = market.liquidations_payload('okx', 'BTC', 'pair', range_key)
    assert payload['coverage']['truncated'] is truncated


def test_watchlist_reports_an_invalid_symbol_as_its_own_failed_item():
    payload = market.watchlist_payload('demo', '12h', '1', 'pair', 'BTC,ABCDEFGHIJKLMNOPQ,eth,btcusdt')
    assert [(item['coin'], item['ok']) for item in payload['items']] == [('BTC', True), ('ABCDEFGHIJKLMNOPQ', False), ('ETH', True)]
    assert 'Symbol must be' in payload['items'][1]['error']
