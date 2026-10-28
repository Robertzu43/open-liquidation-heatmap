"""Run offline interface tests: python examples/research.py [--output DIRECTORY].

All values below are synthetic. These outputs do not measure trading usefulness.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

# Supports running directly from an uninstalled source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liqmap.research import (capture, canonical_json, compare_reference, evaluate,
                             export_features, inspect_capture, load_capture, save_capture)


def run(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = {
        'schema_version': '1.0', 'model_version': '2.0.0',
        'data_fingerprint': 'sha256:' + '0' * 64,
        'exchange': 'binance', 'symbol': 'BTCUSDT', 'as_of': 1000, 'feature_as_of': 61000,
        'generated_at': 1010, 'source': 'synthetic-interface-test',
        'quality': {'status': 'synthetic', 'historical_availability': 'unknown'},
        'units': {'imbalance': 'ratio', 'window_pct': 'percent', 'exposure': 'USD entry notional estimate',
                  'price': 'USDT per base asset'},
        'features': {'resting_long_usd': 100.0, 'resting_short_usd': 75.0,
                     'nearest_long_price': None,
                     'imbalance': [{'window_pct': 1, 'long_usd': 100,
                                    'short_usd': 75, 'short_to_long': 0.75}]},
    }
    capture_path = save_capture(capture(snapshot, received_at=62000), directory / 'captures')
    replay = load_capture(capture_path)
    (directory / 'features.csv').write_text(export_features([replay]), encoding='utf-8')
    reference = {
        'reference_schema_version': '1.0', 'source': 'synthetic-normalized-reference',
        'exchange': 'binance', 'symbol': 'BTCUSDT', 'as_of': 1000, 'feature_as_of': 61000,
        'features': {'imbalance.0.short_to_long': 0.5},
        'units': {'imbalance.0.short_to_long': 'ratio'},
    }
    (directory / 'normalized-reference.json').write_text(canonical_json(reference) + '\n', encoding='utf-8')
    agreement = compare_reference(replay['envelope'], reference)
    # These are predeclared comparable scores, not a fitted model or trade returns.
    rows = [dict(sample_id=f'synthetic-{i}', exchange='binance', symbol='BTCUSDT',
                 feature_time=i * 100, available_at=i * 100 + 1,
                 outcome_start=i * 100 + 2, outcome_end=i * 100 + 80,
                 features={'declared_score': i / 10}, baseline=i / 10 + 0.1,
                 outcome=i / 10 + 0.2) for i in range(4)]
    config = dict(rows=rows, train_end=180, test_start=200,
                  feature='declared_score', units='normalized-score')
    (directory / 'evaluation.json').write_text(canonical_json(config) + '\n', encoding='utf-8')
    held_out = evaluate(**config)
    # A contract example for a future/user-owned strategy simulator. The diagnostic
    # above deliberately does not consume this object or produce strategy PnL.
    strategy_contract = {
        'purpose': 'synthetic-interface-contract-only; not a runnable strategy backtest',
        'strategy_id': 'user-must-declare-before-held-out-run',
        'market': {'exchange': 'binance', 'symbol': 'BTCUSDT'},
        'fees_bps_per_fill': 5.0, 'slippage_bps_per_fill': 3.0,
        'execution_delay_ms': 1000, 'funding_policy': 'supply timestamped funding charges',
        'fill_policy': 'declare executable prices, partial fills and rejected orders',
        'position_sizing': 'supply predeclared sizing and leverage limits',
        'input_fields_required': ['signal_available_at', 'decision_at', 'fill_at',
                                  'quantity', 'executable_price', 'funding_charge'],
        'output_metrics_required': ['net_pnl', 'fees', 'slippage', 'funding',
                                    'turnover', 'max_drawdown', 'trade_count',
                                    'uncertainty_method', 'missing_data_coverage'],
    }
    (directory / 'strategy-input-contract.example.json').write_text(
        canonical_json(strategy_contract) + '\n', encoding='utf-8')
    print(json.dumps({'evidence': 'synthetic interface test only', 'capture': str(capture_path),
                      'inspection': inspect_capture(replay), 'agreement': agreement,
                      'held_out_diagnostic': held_out}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='retain example inputs and outputs in this directory')
    args = parser.parse_args()
    if args.output:
        run(args.output)
    else:
        with tempfile.TemporaryDirectory(prefix='open-liq-research-') as directory:
            run(directory)
