"""Research fixtures are synthetic interface tests, never trading evidence."""
import copy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from liqmap import research


def envelope(as_of=1000):
    return {
        'schema_version': '1.0', 'model_version': '2.0.0',
        'data_fingerprint': 'sha256:' + 'a' * 64,
        'exchange': 'binance', 'symbol': 'BTCUSDT', 'as_of': as_of,
        'generated_at': as_of + 10, 'source': 'synthetic-interface-test',
        'quality': {'availability': 'unknown'}, 'units': {'imbalance': 'ratio'},
        'features': {'imbalance': 0.25, 'distance': None},
    }


def reference(**updates):
    result = {'reference_schema_version': '1.0', 'source': 'user-normalized-example',
              'exchange': 'binance', 'symbol': 'BTCUSDT', 'as_of': 1000,
              'units': {'imbalance': 'ratio'}, 'features': {'imbalance': 0.5}}
    result.update(updates)
    return result


def evaluation_rows():
    return [dict(sample_id=str(i), exchange='binance', symbol='BTCUSDT',
                 feature_time=i * 100, available_at=i * 100 + 1,
                 outcome_start=i * 100 + 2, outcome_end=i * 100 + 80,
                 features={'signal': i / 10}, baseline=i / 10 + 0.1,
                 outcome=i / 10 + 0.2) for i in range(4)]


def test_capture_is_detached_deterministic_and_verified(tmp_path):
    payload = envelope()
    record = research.capture(payload, received_at=2000)
    payload['features']['imbalance'] = 99
    assert record['envelope']['features']['imbalance'] == 0.25
    assert record == research.capture(envelope(), received_at=2000)
    path = research.save_capture(record, tmp_path)
    assert research.load_capture(path) == record
    assert research.inspect_capture(record)['data_time'] == 1000
    assert research.inspect_capture(record)['received_at'] == 2000
    assert research.inspect_capture(record)['availability'] == 'unknown'
    with pytest.raises(FileExistsError):
        research.save_capture(record, tmp_path)


@pytest.mark.parametrize('field,value', [('received_at', 2001), ('data_time', 999)])
def test_tampered_capture_metadata_rejected(tmp_path, field, value):
    record = research.capture(envelope(), received_at=2000)
    record[field] = value
    path = tmp_path / 'tampered.json'
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='integrity'):
        research.load_capture(path)


def test_tampered_payload_rejected():
    record = research.capture(envelope(), received_at=2000)
    record['envelope']['features']['imbalance'] = 0.75
    with pytest.raises(ValueError, match='integrity'):
        research.inspect_capture(record)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, '0.2', 10 ** 400])
def test_invalid_feature_values_rejected(bad):
    payload = envelope()
    payload['features']['imbalance'] = bad
    with pytest.raises(ValueError):
        research.capture(payload, received_at=2000)


def test_missing_features_is_not_zero():
    payload = envelope()
    del payload['features']
    with pytest.raises(ValueError, match='features'):
        research.capture(payload, received_at=2000)


def test_export_is_sorted_deterministic_and_retains_null():
    records = [research.capture(envelope(2000), received_at=3000),
               research.capture(envelope(), received_at=2000)]
    output = research.export_features(records)
    assert output == research.export_features(list(reversed(records)))
    rows = list(csv.DictReader(io.StringIO(output)))
    assert [row['as_of'] for row in rows] == ['1000', '1000', '2000', '2000']
    assert rows[0]['feature'] == 'distance'
    assert rows[0]['value'] == ''
    assert rows[0]['status'] == 'unavailable'
    with pytest.raises(ValueError, match='empty'):
        research.export_features([])


def test_reference_comparison_is_alignment_and_agreement_only():
    result = research.compare_reference(envelope(), reference())
    assert result['kind'] == 'normalized_reference_agreement'
    assert result['compared'] == 1
    assert result['mean_absolute_difference'] == 0.25
    assert result['features']['imbalance']['difference'] == -0.25
    assert result['claims_accuracy'] is False


@pytest.mark.parametrize('updates,reason', [
    ({'symbol': 'ETHUSDT'}, 'market'), ({'exchange': 'okx'}, 'market'),
    ({'as_of': 1001}, 'time'), ({'units': {'imbalance': 'USD'}}, 'unit'),
    ({'features': {}}, 'empty'), ({'features': {'unknown': 1}}, 'missing'),
    ({'features': {'imbalance': float('nan')}}, 'finite'),
])
def test_reference_mismatches_are_explicit(updates, reason):
    with pytest.raises(ValueError, match=reason):
        research.compare_reference(envelope(), reference(**updates))


def test_held_out_evaluation_reports_baseline_without_claiming_edge():
    report = research.evaluate(evaluation_rows(), train_end=180, test_start=200,
                               feature='signal', units='normalized-score')
    assert report['train_samples'] == 2
    assert report['test_samples'] == 2
    assert report['model_mae'] == pytest.approx(0.2)
    assert report['baseline_mae'] == pytest.approx(0.1)
    assert report['incremental_mae_improvement'] == pytest.approx(-0.1)
    assert report['pnl'] is None
    assert report['empirical_validation'] is False


@pytest.mark.parametrize('mutation,reason', [
    ('overlap', 'overlap'), ('availability', 'availability'),
    ('missing', 'feature'), ('nan', 'finite'), ('duplicate', 'duplicate'),
])
def test_evaluation_rejects_leakage_and_invalid_inputs(mutation, reason):
    rows = evaluation_rows()
    if mutation == 'overlap':
        rows[1]['outcome_end'] = 250
    elif mutation == 'availability':
        rows[2]['available_at'] = rows[2]['outcome_start'] + 1
    elif mutation == 'missing':
        rows[2]['features'] = {}
    elif mutation == 'nan':
        rows[2]['outcome'] = float('inf')
    else:
        rows[2]['sample_id'] = rows[0]['sample_id']
    with pytest.raises(ValueError, match=reason):
        research.evaluate(rows, train_end=180, test_start=200,
                          feature='signal', units='normalized-score')


def test_evaluation_rejects_empty_or_missing_partitions():
    for rows in ([], evaluation_rows()[:2]):
        with pytest.raises(ValueError, match='empty'):
            research.evaluate(rows, train_end=180, test_start=200,
                              feature='signal', units='normalized-score')


def test_cli_capture_inspect_export_and_errors(tmp_path):
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(envelope()))
    result = subprocess.run([sys.executable, '-m', 'liqmap.research', 'record',
                             '--input', str(source), '--directory', str(tmp_path / 'captures')],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    capture_path = json.loads(result.stdout)['paths'][0]
    inspected = subprocess.run([sys.executable, '-m', 'liqmap.research', 'inspect', capture_path],
                               capture_output=True, text=True)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout)['integrity'] == 'verified'
    invalid = subprocess.run([sys.executable, '-m', 'liqmap.research', 'record',
                              '--input', str(source), '--count', '1001',
                              '--directory', str(tmp_path / 'captures')], capture_output=True, text=True)
    assert invalid.returncode != 0


def test_nested_api_features_and_mixed_units_do_not_get_combined_mae():
    env = envelope()
    env['features'] = {'imbalance': {'1': 0.25}, 'resting_long_usd': 100.0,
                       'nearest_short_price': None}
    env['units'] = {'imbalance': 'fraction', 'exposure': 'USD entry notional estimate',
                    'price': 'USDT per base asset'}
    normalized = reference(features={'imbalance.1': 0.5, 'resting_long_usd': 110},
                           units={'imbalance.1': 'fraction',
                                  'resting_long_usd': 'USD entry notional estimate'})
    result = research.compare_reference(env, normalized)
    assert result['compared'] == 2
    assert result['mean_absolute_difference'] is None
    assert result['features']['resting_long_usd']['difference'] == -10
    exported = list(csv.DictReader(io.StringIO(research.export_features([
        research.capture(env, received_at=2000)]))))
    assert exported[0]['feature'] == 'imbalance.1'
    assert exported[0]['unit'] == 'fraction'


def test_empty_features_rejected():
    env = envelope()
    env['features'] = {}
    with pytest.raises(ValueError, match='empty'):
        research.capture(env, received_at=2000)


def test_duplicate_market_observation_rejected_even_with_different_id():
    rows = evaluation_rows()
    repeated = copy.deepcopy(rows[2])
    repeated['sample_id'] = 'different-id-same-observation'
    rows.append(repeated)
    with pytest.raises(ValueError, match='duplicate'):
        research.evaluate(rows, train_end=180, test_start=200,
                          feature='signal', units='normalized-score')


def test_unknown_availability_and_unavailable_prediction_are_rejected():
    for field in ('available_at', 'features'):
        rows = evaluation_rows()
        rows[2][field] = None if field == 'available_at' else {'signal': None}
        with pytest.raises(ValueError, match='availability|feature'):
            research.evaluate(rows, train_end=180, test_start=200,
                              feature='signal', units='normalized-score')


def test_http_recorder_and_size_boundary(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    body = json.dumps(envelope()).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/api/v1/features'
    try:
        paths = research.record_snapshots(directory=tmp_path, url=url, count=2, interval=0.01)
        assert len(paths) == 2
        records = [research.load_capture(path) for path in paths]
        assert all(record['envelope']['features']['imbalance'] == 0.25 for record in records)
        assert records[1]['received_at'] >= records[0]['received_at']
        with pytest.raises(ValueError, match='byte limit'):
            research.record_snapshots(directory=tmp_path, url=url, max_bytes=10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_capture_file_size_limit_does_not_create_unreadable_capture(tmp_path):
    record = research.capture(envelope(), received_at=2000)
    record['envelope']['annotation'] = 'x' * research.MAX_BYTES
    # Re-capture to keep a valid hash: size enforcement is distinct from integrity.
    oversized = research.capture(record['envelope'], received_at=2000)
    with pytest.raises(ValueError, match='byte limit'):
        research.save_capture(oversized, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_cli_compare_and_evaluate(tmp_path):
    path = research.save_capture(research.capture(envelope(), received_at=2000), tmp_path)
    refpath = tmp_path / 'reference.json'
    refpath.write_text(json.dumps(reference()))
    result = subprocess.run([sys.executable, '-m', 'liqmap.research', 'compare',
                             str(path), str(refpath)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['mean_absolute_difference'] == 0.25
    config = tmp_path / 'evaluation.json'
    config.write_text(json.dumps(dict(rows=evaluation_rows(), train_end=180, test_start=200,
                                     feature='signal', units='normalized-score')))
    result = subprocess.run([sys.executable, '-m', 'liqmap.research', 'evaluate', str(config)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['test_samples'] == 2


def test_actual_v1_imbalance_list_and_feature_close_alignment():
    env = envelope()
    env['feature_as_of'] = 61000
    env['units'] = {'imbalance': 'ratio', 'window_pct': 'percent',
                    'exposure': 'USD entry notional estimate'}
    env['features'] = {'imbalance': [dict(window_pct=1, long_usd=10,
                                        short_usd=20, short_to_long=2),
                                   dict(window_pct=2, long_usd=0,
                                        short_usd=20, short_to_long=None)]}
    record = research.capture(env, received_at=62000)
    rows = list(csv.DictReader(io.StringIO(research.export_features([record]))))
    by_name = {row['feature']: row for row in rows}
    assert by_name['imbalance.0.window_pct']['unit'] == 'percent'
    assert by_name['imbalance.0.long_usd']['unit'] == 'USD entry notional estimate'
    assert by_name['imbalance.0.short_to_long']['unit'] == 'ratio'
    assert by_name['imbalance.1.short_to_long']['status'] == 'unavailable'
    normalized = reference(feature_as_of=61000,
                           features={'imbalance.0.short_to_long': 2},
                           units={'imbalance.0.short_to_long': 'ratio'})
    assert research.compare_reference(env, normalized)['mean_absolute_difference'] == 0
    normalized['feature_as_of'] = 121000
    with pytest.raises(ValueError, match='time'):
        research.compare_reference(env, normalized)


def test_finite_inputs_cannot_produce_nonfinite_agreement_or_evaluation():
    env = envelope()
    env['features']['imbalance'] = 1e308
    ref = reference(features={'imbalance': -1e308})
    with pytest.raises(ValueError, match='finite'):
        research.compare_reference(env, ref)
    rows = evaluation_rows()
    rows[2]['features']['signal'] = 1e308
    rows[2]['outcome'] = -1e308
    with pytest.raises(ValueError, match='finite'):
        research.evaluate(rows, train_end=180, test_start=200,
                          feature='signal', units='normalized-score')


def test_research_example_writes_only_inputs_the_toolkit_reads(tmp_path):
    example = Path(__file__).resolve().parents[1] / 'examples' / 'research.py'
    result = subprocess.run([sys.executable, str(example), '--output', str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['evidence'] == 'synthetic interface test only'
    assert sorted(path.name for path in tmp_path.iterdir()) == ['captures', 'evaluation.json', 'features.csv', 'normalized-reference.json']
