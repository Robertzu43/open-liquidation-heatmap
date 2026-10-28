"""Offline capture, normalized agreement checks and held-out evaluation tools.

Hashes detect accidental changes; they are not signatures or proof of source truth.
This module never places orders or claims empirical validation from supplied rows.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import time
from urllib.request import urlopen

MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 1000


def canonical_json(value) -> str:
    """Stable UTF-8 JSON representation; non-finite numbers are never JSON data."""
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('input must contain JSON values with finite numbers') from exc


def _timestamp(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{name} must be nonnegative integer Unix milliseconds')
    return value


def _number(value, name):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{name} must be a finite numeric value')
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    return value


def _features(values, prefix=''):
    """Flatten nested numeric features to dot paths, preserving unavailable nulls."""
    if isinstance(values, list) and prefix:
        values = {str(i): value for i, value in enumerate(values)}
    if not isinstance(values, dict):
        raise ValueError('features must be an object')
    result = {}
    for key, value in sorted(values.items()):
        _text(key, 'feature name')
        # Dots are allowed for already-normalized flat reference feature names.
        name = f'{prefix}.{key}' if prefix else key
        if isinstance(value, (dict, list)):
            for path, item in _features(value, name).items():
                if path in result:
                    raise ValueError('ambiguous feature path')
                result[path] = item
        else:
            if name in result:
                raise ValueError('ambiguous feature path')
            result[name] = None if value is None else _number(value, name)
    return result


def _validate_envelope(envelope):
    if not isinstance(envelope, dict) or envelope.get('schema_version') != '1.0':
        raise ValueError('expected v1 API envelope with schema_version 1.0')
    for name in ('exchange', 'symbol', 'model_version', 'data_fingerprint'):
        _text(envelope.get(name), name)
    _timestamp(envelope.get('as_of'), 'as_of')
    if 'feature_as_of' in envelope:
        _timestamp(envelope['feature_as_of'], 'feature_as_of')
    values = _features(envelope.get('features'))
    if not values:
        raise ValueError('empty features in API envelope')
    canonical_json(envelope)
    return values


def _digest(value):
    return 'sha256:' + hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def capture(envelope: dict, *, received_at: int | None = None) -> dict:
    """Detach a v1 response and hash all capture fields, including observation time."""
    _validate_envelope(envelope)
    received_at = time.time_ns() // 1_000_000 if received_at is None else received_at
    _timestamp(received_at, 'received_at')
    record = {'capture_schema_version': '1.0', 'received_at': received_at,
              'data_time': envelope['as_of'],
              'envelope': json.loads(canonical_json(envelope))}
    record['sha256'] = _digest(record)
    return record


def _verify(record):
    if not isinstance(record, dict):
        raise ValueError('capture integrity: expected an object')
    content = {key: value for key, value in record.items() if key != 'sha256'}
    if record.get('sha256') != _digest(content):
        raise ValueError('capture integrity verification failed')
    if record.get('capture_schema_version') != '1.0':
        raise ValueError('unsupported capture schema version')
    _validate_envelope(record.get('envelope'))
    _timestamp(record.get('received_at'), 'received_at')
    if record.get('data_time') != record['envelope']['as_of']:
        raise ValueError('capture integrity: data_time differs from envelope')
    return record


def save_capture(record: dict, directory) -> Path:
    """Write once using exclusive creation; existing captures cannot be overwritten."""
    _verify(record)
    serialized = canonical_json(record) + '\n'
    if len(serialized.encode('utf-8')) > MAX_BYTES:
        raise ValueError('capture exceeds byte limit')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (record['sha256'].split(':', 1)[1] + '.json')
    with path.open('x', encoding='utf-8') as handle:
        handle.write(serialized)
    return path


def read_json(path, *, max_bytes=MAX_BYTES):
    """Read bounded local JSON input."""
    if not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError(f'max_bytes must be between 1 and {MAX_BYTES}')
    with Path(path).open('rb') as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('input exceeds byte limit')
    return json.loads(raw)


def load_capture(path) -> dict:
    """Offline replay: returns the exact recorded envelope after integrity checks."""
    return _verify(read_json(path))


def inspect_capture(record: dict) -> dict:
    _verify(record)
    envelope = record['envelope']
    quality = envelope.get('quality') or {}
    return {'integrity': 'verified', 'sha256': record['sha256'],
            'data_time': record['data_time'], 'feature_as_of': envelope.get('feature_as_of'),
            'received_at': record['received_at'],
            'exchange': envelope['exchange'], 'symbol': envelope['symbol'],
            'model_version': envelope['model_version'],
            'data_fingerprint': envelope['data_fingerprint'],
            'availability': quality.get('historical_availability', quality.get('availability', 'unknown')),
            'source': envelope.get('source'), 'quality': quality,
            'features': _features(envelope['features'])}


def _unit(envelope, feature):
    units = envelope.get('units') or {}
    if not isinstance(units, dict):
        raise ValueError('units must be an object')
    if feature in units:
        return units[feature]
    root = feature.split('.')[0]
    leaf = feature.split('.')[-1]
    if leaf in units:
        return units[leaf]
    if leaf.endswith('_usd'):
        return units.get('exposure')
    if leaf == 'short_to_long':
        return units.get('imbalance')
    if root in units:
        return units[root]
    if root.endswith('_usd'):
        return units.get('exposure')
    if root.endswith('_price'):
        return units.get('price')
    if 'distance' in root:
        return units.get('distance')
    if 'concentration' in root:
        return units.get('concentration')
    if 'persistence' in root:
        return units.get('persistence')
    return None


def export_features(records) -> str:
    """Deterministic long-form CSV; null feature values remain blank + unavailable."""
    records = list(records)
    if not records:
        raise ValueError('empty capture input')
    for record in records:
        _verify(record)
    records.sort(key=lambda r: (r['envelope']['exchange'], r['envelope']['symbol'],
                               r['data_time'], r['received_at'], r['sha256']))
    output = io.StringIO(newline='')
    fields = ['exchange', 'symbol', 'as_of', 'feature_as_of', 'received_at', 'model_version',
              'data_fingerprint', 'capture_sha256', 'feature', 'value', 'unit', 'status']
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    for record in records:
        env = record['envelope']
        for name, value in sorted(_features(env['features']).items()):
            writer.writerow({'exchange': env['exchange'], 'symbol': env['symbol'],
                             'as_of': env['as_of'], 'feature_as_of': env.get('feature_as_of'),
                             'received_at': record['received_at'],
                             'model_version': env['model_version'], 'data_fingerprint': env['data_fingerprint'],
                             'capture_sha256': record['sha256'], 'feature': name,
                             'value': value, 'unit': _unit(env, name),
                             'status': 'unavailable' if value is None else 'available'})
    return output.getvalue()


def compare_reference(envelope: dict, reference: dict) -> dict:
    """Compare a user-normalized reference, requiring exact market, time and units.

    This is NOT an importer for any provider's native API response. The caller is
    responsible for authorized normalization and comparable feature definitions.
    """
    actual = _validate_envelope(envelope)
    if not isinstance(reference, dict) or reference.get('reference_schema_version') != '1.0':
        raise ValueError('expected normalized reference_schema_version 1.0; native provider payloads unsupported')
    _text(reference.get('source'), 'reference source')
    for name in ('exchange', 'symbol'):
        if reference.get(name) != envelope[name]:
            raise ValueError(f'market mismatch: {name}')
    if _timestamp(reference.get('as_of'), 'reference as_of') != envelope['as_of']:
        raise ValueError('time mismatch: exact source as_of timestamps required')
    if 'feature_as_of' in envelope and reference.get('feature_as_of') != envelope['feature_as_of']:
        raise ValueError('time mismatch: exact feature_as_of close required')
    wanted = _features(reference.get('features'))
    if not wanted:
        raise ValueError('empty reference features')
    results = {}
    differences = []
    for name, expected in sorted(wanted.items()):
        if name not in actual:
            raise ValueError(f'missing feature: {name}')
        left_unit, right_unit = _unit(envelope, name), _unit(reference, name)
        if not isinstance(left_unit, str) or not left_unit or left_unit != right_unit:
            raise ValueError(f'unit mismatch or missing unit: {name}')
        observed = actual[name]
        difference = (None if observed is None or expected is None
                      else _number(observed - expected, 'feature difference'))
        results[name] = {'actual': observed, 'reference': expected, 'unit': left_unit,
                         'difference': difference,
                         'status': 'unavailable' if difference is None else 'compared'}
        if difference is not None:
            differences.append(abs(difference))
    # Mixing units in one MAE would create a meaningless statistic.
    distinct_units = {row['unit'] for row in results.values() if row['difference'] is not None}
    return {'kind': 'normalized_reference_agreement', 'claims_accuracy': False,
            'reference_source': reference['source'], 'as_of': envelope['as_of'],
            'exchange': envelope['exchange'], 'symbol': envelope['symbol'],
            'compared': len(differences), 'unavailable': len(results) - len(differences),
            'mean_absolute_difference': (sum(value / len(differences) for value in differences)
                                         if differences and len(distinct_units) == 1 else None),
            'features': results}


def evaluate(rows: list, *, train_end: int, test_start: int, feature: str, units: str) -> dict:
    """Score predeclared numeric predictions against outcomes and a baseline.

    No model is fitted here. Feature, baseline and outcome must use the same
    declared units. Calibration/training rows are counted but never scored as
    held-out evidence. Unknown point-in-time availability is rejected.
    """
    _timestamp(train_end, 'train_end')
    _timestamp(test_start, 'test_start')
    _text(feature, 'feature')
    _text(units, 'units')
    if train_end >= test_start:
        raise ValueError('train/test overlap: train_end must precede test_start')
    if not isinstance(rows, list) or not rows:
        raise ValueError('empty evaluation input')
    seen, observations, training, testing, excluded = set(), set(), [], [], 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('evaluation rows must be objects')
        sample_id = _text(row.get('sample_id'), 'sample_id')
        if sample_id in seen:
            raise ValueError(f'duplicate sample_id: {sample_id}')
        seen.add(sample_id)
        for key in ('exchange', 'symbol'):
            _text(row.get(key), key)
        feature_time = _timestamp(row.get('feature_time'), 'feature_time')
        observation = (row['exchange'], row['symbol'], feature_time)
        if observation in observations:
            raise ValueError('duplicate market observation')
        observations.add(observation)
        available = row.get('available_at')
        if available is None:
            raise ValueError('unknown feature availability cannot establish held-out timing')
        _timestamp(available, 'availability')
        start = _timestamp(row.get('outcome_start'), 'outcome_start')
        end = _timestamp(row.get('outcome_end'), 'outcome_end')
        if not feature_time <= available <= start < end:
            raise ValueError('availability/outcome ordering would leak future data')
        features = _features(row.get('features'))
        if feature not in features or features[feature] is None:
            raise ValueError(f'missing feature: {feature}')
        _number(row.get('baseline'), 'baseline')
        _number(row.get('outcome'), 'outcome')
        if feature_time <= train_end:
            if end > train_end:
                raise ValueError('train outcome overlap with held-out boundary; purge crossing labels')
            training.append(row)
        elif feature_time >= test_start:
            testing.append(row)
        else:
            excluded += 1
    if not training or not testing:
        raise ValueError('empty train or held-out partition')
    model_errors = [_number(abs(_features(row['features'])[feature] - row['outcome']),
                            'model error') for row in testing]
    baseline_errors = [_number(abs(row['baseline'] - row['outcome']), 'baseline error')
                       for row in testing]
    model_mae = sum(value / len(testing) for value in model_errors)
    baseline_mae = sum(value / len(testing) for value in baseline_errors)
    return {'kind': 'held_out_prediction_diagnostic', 'feature': feature, 'units': units,
            'train_end': train_end, 'test_start': test_start,
            'train_samples': len(training), 'test_samples': len(testing),
            'excluded_gap_samples': excluded, 'model_mae': model_mae,
            'baseline_mae': baseline_mae, 'incremental_mae_improvement': baseline_mae - model_mae,
            'pnl': None, 'empirical_validation': False,
            'limitations': ['User-supplied predictions and timestamps are not independently verified.',
                            'MAE is not liquidation-size accuracy, timing alpha or strategy PnL.',
                            'No uncertainty estimate: serial dependence and sample sufficiency require further analysis.',
                            'No trading simulation: fees, slippage, turnover and drawdown are not evaluated.']}


def record_snapshots(*, directory, input_path=None, url=None, count=1, interval=1.0,
                     timeout=10.0, max_bytes=MAX_BYTES):
    """Bounded synchronous recorder. Failure stops the run; prior files are retained."""
    if (input_path is None) == (url is None):
        raise ValueError('supply exactly one of input_path or url')
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_RECORDS:
        raise ValueError(f'count must be between 1 and {MAX_RECORDS}')
    if not 0.01 <= _number(interval, 'interval') <= 60:
        raise ValueError('interval must be between 0.01 and 60 seconds')
    if not 0 < _number(timeout, 'timeout') <= 60:
        raise ValueError('timeout must be between 0 and 60 seconds')
    if not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_BYTES:
        raise ValueError(f'max_bytes must be between 1 and {MAX_BYTES}')
    if url is not None and not url.startswith(('http://', 'https://')):
        raise ValueError('url must be http or https')
    paths = []
    for index in range(count):
        if url is None:
            env = read_json(input_path, max_bytes=max_bytes)
        else:
            with urlopen(url, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError('response exceeds byte limit')
            env = json.loads(raw)
        paths.append(str(save_capture(capture(env), directory)))
        if index + 1 < count:
            time.sleep(interval)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    record = commands.add_parser('record', help='capture bounded v1 JSON files or HTTP responses')
    source = record.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', dest='input_path')
    source.add_argument('--url')
    record.add_argument('--directory', required=True)
    record.add_argument('--count', type=int, default=1)
    record.add_argument('--interval', type=float, default=1.0)
    record.add_argument('--timeout', type=float, default=10.0)
    record.add_argument('--max-bytes', type=int, default=MAX_BYTES)
    inspect = commands.add_parser('inspect', help='verify captures and replay metadata/features offline')
    inspect.add_argument('captures', nargs='+')
    export = commands.add_parser('export', help='export captures as deterministic CSV on stdout')
    export.add_argument('captures', nargs='+')
    compare = commands.add_parser('compare', help='agreement with an explicitly normalized reference')
    compare.add_argument('capture')
    compare.add_argument('reference')
    evaluation = commands.add_parser('evaluate', help='evaluate predeclared held-out predictions; no trading')
    evaluation.add_argument('input', help='JSON object with rows/train_end/test_start/feature/units')
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        if command == 'record':
            result = {'paths': record_snapshots(**args)}
        elif command == 'inspect':
            summaries = [inspect_capture(load_capture(path)) for path in args['captures']]
            result = summaries[0] if len(summaries) == 1 else summaries
        elif command == 'export':
            sys.stdout.write(export_features(load_capture(path) for path in args['captures']))
            return 0
        elif command == 'compare':
            result = compare_reference(load_capture(args['capture'])['envelope'], read_json(args['reference']))
        else:
            config = read_json(args['input'])
            if not isinstance(config, dict):
                raise ValueError('evaluation config must be an object')
            result = evaluate(**config)
        print(canonical_json(result))
        return 0
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print(f'research: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
