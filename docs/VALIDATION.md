# Validation and reproducible research

The local toolkit validates capture integrity and research interfaces. It does **not** establish liquidation-position accuracy, a trading edge, uptime, or replacement readiness for CoinGlass. Real provider exports, exchange outcomes and strategy executions were unavailable during implementation. Synthetic examples are engineering fixtures, never empirical results.

## Three different evidence gates

| Gate | What it can establish | What is still required |
| --- | --- | --- |
| Engineering | Deterministic snapshots/exports; schema, numeric and integrity checks; strict market/time/unit alignment; chronological held-out split guards | Run the project's full tests and runtime/browser checks on the release revision; record failures and environment |
| Empirical | Incremental prediction or strategy usefulness on authorized real observations | Point-in-time features, explicit definitions, representative held-out regimes, baseline predictions, timestamped outcomes, costs and uncertainty |
| Operating | Coverage, missing captures, failure recovery and latency in the target environment | A sustained shadow run with declared markets, geography and thresholds; outage/gap accounting |

A proposed observation window is at least 30 consecutive days plus historical held-out periods covering multiple volatility regimes. This is a starting point for the user's acceptance criteria; duration alone does not establish statistical sufficiency. No order placement is included.

## Run the offline example

From the repository root, with its Python requirements installed:

```bash
python -m pytest tests/test_research.py -q
python examples/research.py --output /tmp/open-liq-research-example
python -m liqmap.research --help
```

The example writes a capture, `features.csv`, a normalized reference, and an evaluation input. Every input is labeled synthetic. Repeating it in the same directory refuses to overwrite the existing identical capture. Choose another output directory for another run. Without `--output`, files live only in a temporary directory.

## Capture, inspect and replay

Capture a v1 API response from a running local service, or from a JSON file:

```bash
python -m liqmap.research record --url 'http://127.0.0.1:8765/api/v1/features?exchange=demo&symbol=BTC&range=12h&model=1' --directory /tmp/open-liq-captures --count 10 --interval 5
python -m liqmap.research record --input /path/to/v1-response.json --directory /tmp/open-liq-file-captures
python -m liqmap.research inspect /tmp/open-liq-captures/*.json
python -m liqmap.research export /tmp/open-liq-captures/*.json > /tmp/features.csv
```

Use your server's actual port and supported query parameters; `/api/v1/heatmap` also works. The recorder reads one response at a time with explicit bounds: 1–1,000 captures per invocation, 16 MiB maximum input **and persisted capture**, 0.01–60 second interval, and timeout up to 60 seconds. Defaults are one capture, one second interval and ten second timeout. `--max-bytes` lowers the input bound. A network, schema, filesystem or size error stops the invocation with exit status 2; earlier captures remain. Failed polls do not generate fake successful observations. Record scheduler logs and expected poll times separately to count outages; a directory of successful captures alone cannot prove uptime. Disk use accumulates between invocations: use a retention policy outside this tool.

Each capture contains `capture_schema_version: "1.0"`, `received_at` (local collector Unix milliseconds), `data_time` (the response's source `as_of`), the complete `envelope`, and `sha256`. The hash covers **all fields except the hash itself**, using sorted compact UTF-8 JSON. Files use exclusive creation and a hash-based filename. Inspection and exports verify the hash before use. Python replay is simply:

```python
from liqmap.research import load_capture
record = load_capture('/path/to/capture.json')
response = record['envelope']  # exact offline replay, no network or recomputation
```

A hash detects accidental modification when compared to a trusted prior hash; anyone who can replace a file can also recompute its hash. This is not a digital signature, proof of the provider's truth or a claim that the filesystem is immutable. Preserve a separate trusted hash manifest if stronger provenance is required. API `data_fingerprint` identifies semantic input data; the capture hash additionally binds response metadata and receipt time, so repeated observations can have different capture hashes for identical source data.

`as_of` denotes the last source bar **open**, not the feature's first availability. Keep API `time_semantics`/bar interval and quality metadata when determining the actual post-close decision boundary. `received_at` is collection time, not exchange availability, and a local clock may be wrong. Capturing reconstructed historical data today does not establish historical availability. `historical_availability: unknown` stays unknown; the recorder does not replace it with observation time.

## Feature export and normalized reference agreement

Exports use deterministic market/time/receipt/hash and feature-name order. Nested numeric features become dot paths; lists use zero-based indices, for example `imbalance.0.short_to_long`. Preserve the corresponding `imbalance.0.window_pct` so a list index is never mistaken for a percentage window. Each CSV row carries source and receipt times, model version, data fingerprint, capture hash, feature, value, units and status. Null values remain blank with `status=unavailable`; missing units remain blank. Missing values never become a zero signal. Comparisons require units to be present and identical.

A **user-normalized reference format** is supported. Native CoinGlass API responses are not supported by a raw adapter; normalize an authorized export yourself and preserve its source timestamps/units. Declare exactly which reference definition corresponds to which model feature; similar names are insufficient. Agreement is a diagnostic between estimates, not correctness against truth.

```json
{
  "reference_schema_version": "1.0",
  "source": "user-authorized-normalized-export",
  "exchange": "binance",
  "symbol": "BTCUSDT",
  "as_of": 1793145600000,
  "feature_as_of": 1793145900000,
  "units": {"imbalance.0.short_to_long": "ratio"},
  "features": {"imbalance.0.short_to_long": 0.2}
}
```

```bash
python -m liqmap.research compare /path/to/capture.json /path/to/normalized-reference.json
```

The comparator requires **exact** exchange, symbol and source `as_of` matching, and matching `feature_as_of` whenever the API envelope declares it. It performs no nearest-time interpolation, symbol guessing, currency conversion or aggregation across venues. Align bar width, close/open timestamp convention, market type, horizon, model scenario and feature definition in preprocessing. Preserve original provider fields in your normalization audit trail. A missing feature, empty reference, non-finite value or missing/mismatched unit is an error; explicit nulls produce an unavailable comparison. Differences are `actual - reference`. A combined mean absolute difference is returned only when all comparable fields share one unit; per-feature differences remain available for mixed units. A matching unit does not prove identical economic meaning.

## Held-out prediction diagnostic

The runnable evaluator accepts **predeclared predictions**, outcome values and baseline predictions in the **same declared units**. It fits no model and chooses no strategy. An arbitrary raw exposure feature is not automatically a prediction of returns or event size: supply a documented transformation calibrated using training data only. Archive availability that is unknown cannot be asserted as known to bypass the timing guards.

Input JSON:

```json
{
  "train_end": 180,
  "test_start": 200,
  "feature": "declared_score",
  "units": "normalized-score",
  "rows": [
    {"sample_id": "train-1", "exchange": "binance", "symbol": "BTCUSDT", "feature_time": 100, "available_at": 101, "outcome_start": 102, "outcome_end": 180, "features": {"declared_score": 0.1}, "baseline": 0.2, "outcome": 0.3},
    {"sample_id": "test-1", "exchange": "binance", "symbol": "BTCUSDT", "feature_time": 200, "available_at": 201, "outcome_start": 202, "outcome_end": 280, "features": {"declared_score": 0.2}, "baseline": 0.3, "outcome": 0.4}
  ]
}
```

These tiny timestamps/values are synthetic interface examples. Real timestamps must be integer Unix milliseconds. `feature_time` means a declared causal decision time, using API time semantics, not blindly the bar-open `as_of`. `available_at` must cover availability of **every** feature/source used by the prediction and baseline, including fitted parameters. It must not be inferred from reconstructed archive bar labels. The caller must audit outcome-feed timestamps and completeness.

```bash
python -m liqmap.research evaluate /path/to/evaluation.json
```

Requirements and reported behavior:

- `train_end < test_start`; training outcomes end by `train_end`. Purge labels crossing this boundary. Rows strictly between the two feature-time boundaries are excluded and counted.
- `feature_time <= available_at <= outcome_start < outcome_end`; unknown availability, duplicate sample IDs, repeated market/timestamp observations, missing features, empty train/test sets, null predictions and non-finite numbers are rejected.
- Only held-out rows contribute to model/baseline MAE. Positive `incremental_mae_improvement` means lower model error on those supplied rows. It is not a significance test. The report returns sample counts and explicitly leaves `pnl=null` and `empirical_validation=false`.
- Predeclare features, transformations, horizons, splits and baseline before examining held-out results. Use a simple price/OI baseline plus the identical outcome target. Record the source revisions and feature construction in a separate manifest. Do not tune against this holdout; reserve a new untouched period after tuning.
- The scaffold verifies timing and arithmetic on supplied data; it cannot establish that timestamps or predictions were honestly constructed. Overlapping test horizons and serial correlation require a suitable block/cluster uncertainty analysis. The scaffold intentionally returns no unsupported confidence interval.

Treat event detection, cluster visitation, timing and strategy PnL as different targets. A touched predicted price does not establish accurate liquidation size or an executable profitable trade. Exchange liquidation event feeds can be incomplete or sampled; absence in an incomplete feed is not a verified negative label. Evaluate missingness and coverage before scoring. Open Liq's `/api/v1/liquidations` is such a feed: OKX only, at most the 1,000 most recent filled orders per request, priced at the bankruptcy price, with `coverage.truncated` set when older orders were not fetched. Keep its `coverage` object with any label derived from it, and do not score OKX events against an aggregate or another venue's estimate as if they covered that venue.
