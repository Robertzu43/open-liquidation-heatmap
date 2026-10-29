"""Independent expected-value regressions for the post-close causal inventory."""
import numpy as np
import pytest

from liqmap.data import combine_series, finalize_series, fold_oi, _parse_metrics_csv
from liqmap.model import MarketSeries, build_heatmap


def series(oi, prices=None, *, base=None, seconds=300, high=None, low=None, venue='binance', **kwargs):
    n = len(oi)
    prices = np.full(n, 100.0) if prices is None else np.asarray(prices, dtype=float)
    result = MarketSeries(np.arange(n) * seconds * 1000, prices.copy(),
        prices + .01 if high is None else np.asarray(high),
        prices - .01 if low is None else np.asarray(low), prices.copy(),
        np.zeros(n), np.asarray(oi, dtype=float), np.full(n, .5), venue, 'BTCUSDT', 'BTC', seconds)
    if base is not None:
        result.oi_base = np.asarray(base, dtype=float)
    for name, value in kwargs.items():
        setattr(result, name, value)
    return result


def total(heat):
    return heat.long_heat.sum(axis=0) + heat.short_heat.sum(axis=0)


def test_native_contracts_constant_price_appreciation_creates_no_flow():
    heat = build_heatmap(series([1000, 1100, 1200], [100, 110, 120], base=[10, 10, 10]), 1)
    np.testing.assert_allclose(total(heat), 0)


def test_close_entry_survives_same_bar_prior_wick_and_next_trigger_sweeps():
    entry = build_heatmap(series([1000, 2000], base=[10,20], high=[100.01,140], low=[99.99,60]), 1)
    assert entry.profile_long.sum() + entry.profile_short.sum() == pytest.approx(1000)
    heat = build_heatmap(series([1000, 2000, 2000], base=[10, 20, 20],
        high=[100.01, 140, 140], low=[99.99, 60, 60]), 1)
    np.testing.assert_allclose(total(heat), [0, 1000, 0], atol=1e-9)
    assert heat.profile_long.sum() == 0


def test_prefix_and_resolution_do_not_change_logical_state():
    prefix = series([1000, 2000, 2000], base=[10, 20, 20], high=[100.01,100.01,101.3])
    future = series([1000,2000,2000,2000], [100,100,100,1000], base=[10,20,20,20], high=[100.01,100.01,101.3,1000.01])
    a = build_heatmap(prefix, 1, n_bins=80)
    b = build_heatmap(future, 1, n_bins=480)
    np.testing.assert_allclose(total(a), total(b)[:3], atol=1e-9)
    assert a.logical_features == b.logical_features[:3]


@pytest.mark.parametrize('invalid', [0, -1, np.nan, np.inf])
def test_invalid_oi_rebaselines_without_erasing_or_recreating(invalid):
    heat = build_heatmap(series([1000,2000,invalid,3000,3000], base=[10,20,invalid,30,30]), 1)
    np.testing.assert_allclose(total(heat), [0,1000,1000,1000,1000], atol=1e-9)


def test_missing_and_returning_venue_does_not_create_aggregate_flow():
    a = series([1000,2000,2000,2000,2000], base=[10,20,20,20,20])
    b = series([1000,1000,np.nan,1000,1000], base=[10,10,np.nan,10,10], venue='okx')
    combined = combine_series([a,b], a.times, 300, 'BTC')
    heat = build_heatmap(combined, 1)
    np.testing.assert_allclose(total(heat), [0,1000,1000,1000,1000], atol=1e-9)


def test_last_column_profile_and_features_are_same_post_update_state():
    heat = build_heatmap(series([1000,2000,2000], base=[10,20,20], high=[100.01,100.01,140]), 2)
    np.testing.assert_allclose(heat.long_heat[:,-1], heat.profile_long)
    np.testing.assert_allclose(heat.short_heat[:,-1], heat.profile_short)
    assert heat.features['resting_long_usd'] == pytest.approx(heat.profile_long.sum())
    assert heat.features['resting_short_usd'] == pytest.approx(heat.profile_short.sum())


def test_decay_depends_on_elapsed_time_not_bar_count():
    fast = build_heatmap(series([1000,2000,2000,2000,2000], base=[10,20,20,20,20], seconds=300), 2)
    slow = build_heatmap(series([1000,2000,2000], base=[10,20,20], seconds=900), 2)
    assert total(fast)[-1] == pytest.approx(total(slow)[-1])


def test_known_availability_after_bar_close_is_excluded():
    heat = build_heatmap(series([1000,2000,2000], base=[10,20,20],
        oi_available_at=np.array([0, 950000, 950000])), 1)
    np.testing.assert_allclose(total(heat), 0)


def test_oi_alignment_never_uses_print_after_bar_close():
    grid = np.array([0, 3600000, 7200000])
    oi, _ = fold_oi([(4200000, 100, .5), (3000000, 90, .5)], grid, 3600000)
    assert oi[0] == 90
    assert oi[1] == 100


def test_metrics_preserves_native_units_and_does_not_substitute_account_ratio():
    rows = _parse_metrics_csv('create_time,sum_open_interest_value,sum_open_interest,count_long_short_ratio\n2024-01-01 00:00:00,500,5,3\n')
    assert np.isnan(rows[0][2])
    assert rows[0][3] == 5


def test_filled_prices_and_oi_retain_observation_masks():
    loaded = finalize_series(times=np.arange(3)*300000, open_=np.array([100,np.nan,100]),
        high=np.array([101,np.nan,101]), low=np.array([99,np.nan,99]),
        close=np.array([100,np.nan,100]), volume=np.zeros(3), oi=np.array([1000,np.nan,2000]),
        share=np.array([.5,np.nan,.5]), exchange='binance', symbol='BTCUSDT', coin='BTC',
        bar_seconds=300, source='fixture', note='', warnings=[])
    np.testing.assert_array_equal(loaded.series.candle_observed, [True,False,True])
    np.testing.assert_array_equal(loaded.series.oi_observed, [True,False,True])
    assert total(build_heatmap(loaded.series, 1))[-1] == 0


def test_finalize_does_not_mutate_raw_gaps_for_later_replays():
    close = np.array([100., np.nan, 100.])
    original = close.copy()
    args = dict(times=np.arange(3)*300000, open_=close.copy(), high=close+1,
        low=close-1, close=close, volume=np.zeros(3), oi=np.array([1000,np.nan,2000]),
        share=np.full(3,.5), exchange='binance', symbol='BTCUSDT', coin='BTC',
        bar_seconds=300, source='fixture', note='', warnings=[])
    first = finalize_series(**args)
    np.testing.assert_array_equal(close, original)
    second = finalize_series(**args)
    np.testing.assert_array_equal(first.series.candle_observed, second.series.candle_observed)


def test_aggregate_price_choice_cannot_depend_on_future_volume():
    a = series([1000,2000], [100,100], venue='binance')
    b = series([1000,1000], [101,101], venue='okx')
    a.volume_usd[:] = 10
    b.volume_usd[:] = 1
    prefix = combine_series([a,b], a.times, 300, 'BTC')
    aa = series([1000,2000,2000], [100,100,100], venue='binance')
    bb = series([1000,1000,1000], [101,101,101], venue='okx')
    aa.volume_usd[:] = [10,10,10]
    bb.volume_usd[:] = [1,1,100000]
    future = combine_series([aa,bb], aa.times, 300, 'BTC')
    np.testing.assert_array_equal(prefix.close, future.close[:2])
    assert build_heatmap(prefix,1).logical_features == build_heatmap(future,1).logical_features[:2]


def test_default_live_grid_excludes_unclosed_candle(monkeypatch):
    from liqmap import data
    monkeypatch.setattr(data.time, 'time', lambda: 901)
    assert data._window({'seconds':600,'bar_seconds':300}).tolist() == [300000,600000]


def test_native_binance_live_and_archive_parser_paths(monkeypatch):
    from liqmap import data
    grid = np.array([300000,600000,900000])
    def get_json(url, params, **kwargs):
        if url.endswith('/klines'):
            return 200, [[t, 100,101,99,100,0,t+299999,10] for t in grid]
        if url.endswith('/openInterestHist'):
            return 200, [{'timestamp':int(t),'sumOpenInterest':'5','sumOpenInterestValue':str(500+i*50)}
                         for i,t in enumerate(grid) if t >= params['startTime']]
        return 200, []
    monkeypatch.setattr(data, '_get_json', get_json)
    raw = data._fold_binance_live('BTCUSDT','5m',grid)
    np.testing.assert_array_equal(raw.oi_base, [5,5,5])
    # Test the complete archive finalization independently of the wall-clock grid.
    monkeypatch.setattr(data, '_load_vision_klines', lambda *args: [(int(t),100,101,99,100,10,5) for t in grid])
    monkeypatch.setattr(data, '_load_vision_metrics', lambda *args: [(int(t),500+i*50,.5,5) for i,t in enumerate(grid)])
    archive = data._fold_binance_archive('BTCUSDT','5m',grid)
    finished = data.finish_history(archive,900,{'bar_seconds':300,'seconds':900},'BTC','BTCUSDT')
    np.testing.assert_array_equal(finished.series.oi_base,[5,5,5])
    np.testing.assert_array_equal(finished.series.long_share,[.5,.5,.5])  # Taker buy 5 of quote volume 10.
    assert finished.series.provenance['history_kind'] == 'reconstructed_archive'
    np.testing.assert_allclose(total(build_heatmap(finished.series,1)),0)


def test_okx_uses_base_oi_column_not_contract_count_or_usd(monkeypatch):
    from liqmap import data
    grid = np.array([300000,600000,900000])
    monkeypatch.setattr(data, '_window', lambda spec: grid)
    def pages(path, *args):
        if path.endswith('open-interest-history'):
            return [[str(t),'9999','5',str(500+i*50)] for i,t in enumerate(grid)]
        if path.endswith('taker-volume-contract'):
            return []
        return [[str(t),'100','101','99','100','0','0','10','1'] for t in grid]
    monkeypatch.setattr(data, '_okx_pages', pages)
    loaded = data.fetch_okx('BTC','BTCUSDT',{'key':'fixture','bar':'5m','bar_seconds':300,'seconds':900})
    np.testing.assert_array_equal(loaded.series.oi_base,[5,5,5])
    np.testing.assert_allclose(total(build_heatmap(loaded.series,1)),0)


def test_okx_candles_come_from_market_candles_alone(monkeypatch):
    from liqmap import data
    # /market/candles serves the latest 1,440 bars; every built-in window plus its lead bar fits.
    assert max(spec['seconds'] // spec['bar_seconds'] + 1 for spec in data.RANGES.values()) <= 1440
    grid = np.array([300000,600000,900000])
    monkeypatch.setattr(data, '_window', lambda spec: grid)
    paths = []
    def pages(path, *args):
        paths.append(path)
        if path.endswith('open-interest-history'):
            return [[str(t),'9999','5','500'] for t in grid]
        if path.endswith('taker-volume-contract'):
            return []
        return [[str(t),'100','101','99','100','0','0','10','1'] for t in grid]
    monkeypatch.setattr(data, '_okx_pages', pages)
    data.fetch_okx('BTC','BTCUSDT',{'key':'fixture','bar':'5m','bar_seconds':300,'seconds':900})
    assert paths == ['/api/v5/market/candles', '/api/v5/rubik/stat/contracts/open-interest-history',
                     '/api/v5/rubik/stat/taker-volume-contract']


def test_replay_bound_checked_before_grid_allocation(monkeypatch):
    from liqmap import data
    def forbidden(*args):
        pytest.fail('oversized replay allocated a grid')
    monkeypatch.setattr(data, 'make_grid', forbidden)
    with pytest.raises(data.DataError,match='730 days'):
        data.load_span('BTC','BTCUSDT',{'bar_seconds':300,'seconds':3600},0,731*86400)


def test_invalid_native_okx_observation_is_missing_not_an_exception(monkeypatch):
    from liqmap import data
    grid = np.array([300000,600000,900000])
    monkeypatch.setattr(data, '_window', lambda spec: grid)
    def pages(path, *args):
        if path.endswith('open-interest-history'):
            return [[str(t),'9999',base,'500'] for t,base in zip(grid,['5','bad','6'],strict=True)]
        if path.endswith('taker-volume-contract'):
            return []
        return [[str(t),'100','101','99','100','0','0','10','1'] for t in grid]
    monkeypatch.setattr(data, '_okx_pages', pages)
    loaded = data.fetch_okx('BTC','BTCUSDT',{'key':'fixture','bar':'5m','bar_seconds':300,'seconds':900})
    assert np.isnan(loaded.series.oi_base[1])
    assert not loaded.series.oi_observed[1]
    np.testing.assert_allclose(total(build_heatmap(loaded.series,1)),0)


@pytest.mark.parametrize('native', [[np.nan,np.nan,np.nan], [0,-1,np.inf]])
def test_quote_oi_cannot_make_entirely_unusable_native_source_valid(native):
    from liqmap.data import DataError
    args = dict(times=np.arange(3)*300000, open_=np.full(3,100.),
        high=np.full(3,101.), low=np.full(3,99.), close=np.full(3,100.),
        volume=np.zeros(3), oi=np.array([1000.,2000.,3000.]), share=np.full(3,.5),
        exchange='binance', symbol='BTCUSDT', coin='BTC', bar_seconds=300,
        source='binance-live', note='', warnings=[])
    with pytest.raises(DataError, match='usable native open interest'):
        finalize_series(**args, oi_base=np.array(native))
    # The existing explicitly absent-native compatibility path is distinct.
    legacy = finalize_series(**args, oi_base=None)
    assert build_heatmap(legacy.series,1).metadata['oi_accounting']['binance'] == 'legacy_quote_delta'


def test_exact_nearest_distances_are_signed_and_missing_values_null():
    heat = build_heatmap(series([1000,2000], base=[10,20]),1)
    empty = heat.logical_features[0]
    assert empty['nearest_long_distance_pct'] is None
    assert empty['nearest_short_distance_pct'] is None
    last = heat.features
    assert last['nearest_long_distance_pct'] == pytest.approx((last['nearest_long_price']/100-1)*100)
    assert last['nearest_short_distance_pct'] == pytest.approx((last['nearest_short_price']/100-1)*100)
    assert last['nearest_long_distance_pct'] < 0 < last['nearest_short_distance_pct']


@pytest.mark.parametrize('model_id',[1,2,3])
def test_visible_tail_keeps_full_history_state_and_tier_snapshot(model_id):
    original = series([1000,2000,2200,2300,2400], base=[10,20,22,23,24])
    full = build_heatmap(original,model_id,tier_column=3)
    view = build_heatmap(original,model_id,tier_column=0,visible_bars=2)
    assert len(original.times) == 5
    assert len(view.series.times) == 2
    np.testing.assert_array_equal(view.series.times,original.times[-2:])
    np.testing.assert_array_equal(view.series.oi_base,original.oi_base[-2:])
    np.testing.assert_allclose(view.long_heat,full.long_heat[:,-2:])
    np.testing.assert_allclose(view.short_heat,full.short_heat[:,-2:])
    np.testing.assert_allclose(view.long_tier_column,full.long_tier_column)
    np.testing.assert_allclose(view.profile_long,full.profile_long)
    assert view.features == full.features
    assert view.logical_features == full.logical_features[-2:]
    assert view.metadata['warmup_anchor_ms'] == int(original.times[0])
    assert view.metadata['history_bars'] == 5
    assert view.metadata['visible_bars'] == 2
    assert view.metadata['venue_coverage'] == full.metadata['venue_coverage'][-2:]
    assert len(view.logical_totals['opened_usd']) == 2


@pytest.mark.parametrize('visible_bars',[1,4,2.5,True])
def test_visible_bars_must_fit_loaded_history(visible_bars):
    with pytest.raises(ValueError,match='visible_bars'):
        build_heatmap(series([1000,2000,3000],base=[10,20,30]),1,visible_bars=visible_bars)


@pytest.mark.parametrize('bar,seconds,candle_period,offset_hours',[
    ('12h',43200,'12Hutc',4),('1d',86400,'1Dutc',16)])
def test_okx_long_bars_request_utc_and_reject_offset_or_unconfirmed_candles(monkeypatch,bar,seconds,candle_period,offset_hours):
    from liqmap import data
    base = 1792886400000  # 2026-10-25 00:00:00 UTC
    grid = base + np.arange(5)*seconds*1000
    monkeypatch.setattr(data,'_window',lambda spec:grid)
    monkeypatch.setattr(data.time,'time',lambda:(int(grid[-1])+seconds*1000)/1000)
    calls = []
    candles = [[str(t),'100','101','99','100','1','1','10','1'] for t in grid]
    candles[2][0] = str(int(grid[2])+offset_hours*3600000)
    candles[2][2] = '1000'  # Misaligned candle must not sweep earlier exact state.
    candles[3][8] = '0'
    def pages(path,params,*args):
        calls.append((path,params))
        if path.endswith('open-interest-history'):
            return [[str(t),'1',str(10+i),str((10+i)*100)] for i,t in enumerate(grid)]
        if path.endswith('taker-volume-contract'):
            return [[str(t),'1','3'] for t in grid]
        return candles
    monkeypatch.setattr(data,'_okx_pages',pages)
    loaded = data.fetch_okx('BTC','BTCUSDT',{'key':'fixture','bar':bar,'bar_seconds':seconds,'seconds':seconds*5})
    np.testing.assert_array_equal(loaded.series.candle_observed,[True,True,False,False,True])
    assert loaded.series.high[2] != 1000
    assert loaded.series.long_share[0] == .75
    for path,params in calls:
        if path.endswith('candles'):
            assert params['bar'] == candle_period
        else:
            assert params['period'] == candle_period


@pytest.mark.parametrize('bar,seconds',[('12h',43200),('1d',86400)])
def test_okx_even_confirmed_candle_cannot_close_after_request_cutoff(monkeypatch,bar,seconds):
    from liqmap import data
    grid = 1792886400000 + np.arange(3)*seconds*1000
    cutoff_ms = int(grid[-1]) + seconds*1000 - 1
    monkeypatch.setattr(data,'_window',lambda spec:grid)
    monkeypatch.setattr(data.time,'time',lambda:cutoff_ms/1000)
    def pages(path,*args):
        if path.endswith('open-interest-history'):
            return [[str(t),'1','10','1000'] for t in grid]
        if path.endswith('taker-volume-contract'):
            return []
        return [[str(t),'100','101','99','100','1','1','10','1'] for t in grid]
    monkeypatch.setattr(data,'_okx_pages',pages)
    loaded = data.fetch_okx('BTC','BTCUSDT',{'key':'fixture','bar':bar,'bar_seconds':seconds,'seconds':seconds*3})
    assert loaded.series.times.tolist() == grid[:2].tolist()
    assert int(loaded.series.times[-1])+seconds*1000 <= cutoff_ms


def test_okx_statistics_use_matching_utc_intervals_without_borrowing_flow(monkeypatch):
    from liqmap import data
    seconds = 86400
    grid = 1792886400000 + np.arange(4)*seconds*1000
    monkeypatch.setattr(data,'_window',lambda spec:grid)
    monkeypatch.setattr(data.time,'time',lambda:(int(grid[-1])+seconds*1000)/1000)
    def pages(path,params,*args):
        if path.endswith('open-interest-history'):
            return [[str(t+(1 if i==2 else 0)),'1',str(10+i),str((10+i)*100)] for i,t in enumerate(grid)]
        if path.endswith('taker-volume-contract'):
            return [[str(t+(1 if i==1 else 0)),'1','3'] for i,t in enumerate(grid)]
        return [[str(t),'100','101','99','100','1','1','10','1'] for t in grid]
    monkeypatch.setattr(data,'_okx_pages',pages)
    loaded = data.fetch_okx('BTC','BTCUSDT',{'key':'fixture','bar':'1d','bar_seconds':seconds,'seconds':seconds*4})
    np.testing.assert_array_equal(loaded.series.oi_observed,[True,True,False,True])
    np.testing.assert_array_equal(loaded.series.flow_observed,[True,False,False,True])


def test_replay_source_window_limit_precedes_grid_allocation(monkeypatch):
    from liqmap import data
    monkeypatch.setattr(data, 'make_grid', lambda *args: pytest.fail('oversized source window allocated a grid'))
    with pytest.raises(data.DataError, match='1100 days'):
        data.load_span('BTC', 'BTCUSDT', {'bar_seconds': 86400, 'seconds': 730*86400}, 0, 400*86400)


@pytest.mark.parametrize('model_id', [1, 2, 3])
def test_default_chart_resolution_preserves_exact_economic_features(model_id):
    data = series([1000, 2000, 3000, 4000], base=[10, 12, 13, 15])
    old = build_heatmap(data, model_id, n_bins=360)
    sharp = build_heatmap(data, model_id)
    assert len(sharp.centers) == 720
    assert sharp.features == old.features
    np.testing.assert_allclose(sharp.logical_totals['resting_long_usd'], old.logical_totals['resting_long_usd'])
    np.testing.assert_allclose(sharp.logical_totals['resting_short_usd'], old.logical_totals['resting_short_usd'])


def _finalize(oi):
    n = len(oi)
    price = np.full(n, 100.0)
    return finalize_series(times=np.arange(n) * 300000, open_=price, high=price + 1, low=price - 1, close=price,
                           volume=np.ones(n), oi=np.asarray(oi, dtype=float) * 100, share=np.full(n, .5),
                           oi_base=np.asarray(oi, dtype=float), exchange='okx', symbol='BTCUSDT', coin='BTC',
                           bar_seconds=300, source='okx', note='', warnings=[], now_s=n * 300)


def test_newest_bar_waiting_for_its_open_interest_is_left_out_not_a_gap():
    loaded = _finalize([10, 11, 12, 13, np.nan])
    assert len(loaded.series.times) == 4 and loaded.series.oi_observed.all()
    assert not any('covers' in warning for warning in loaded.series.warnings)


def test_only_two_trailing_bars_wait_and_interior_gaps_stay_reported():
    trailing = _finalize([10, 11, 12, 13, np.nan, np.nan, np.nan])
    assert len(trailing.series.times) == 5 and not trailing.series.oi_observed[-1]
    interior = _finalize([10, np.nan, 12, 13, 14])
    assert len(interior.series.times) == 5 and not interior.series.oi_observed[1]
    assert any('covers 4 of 5 bars' in warning for warning in interior.series.warnings)
