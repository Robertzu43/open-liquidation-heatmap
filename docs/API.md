# HTTP API v1

Run `python -m liqmap.server` and use `http://127.0.0.1:8765`. No API key is needed. Public sources can be unavailable or delayed; demo is explicitly synthetic. HTTP responses use UTF-8 strict JSON (non-finite scalars are never emitted), `Cache-Control: no-store`, and optional gzip.

## Routes and parameters

| Route | Result |
| --- | --- |
| `GET /api/v1/heatmap` | Chart arrays, display analysis, exact logical features and metadata |
| `GET /api/v1/features` | Same metadata, market context, times and exact features, without chart arrays or display analysis |
| `GET /api/v1/status` | Process configuration, available scenarios/ranges and cached default-market readiness |
| `GET /api/v1/symbols` | Suggested symbols and supported exchanges |
| `GET /api/v1/levels` | Display-bin leverage breakdown at `t` and `price` |
| `GET /api/v1/replay` | Load a bounded Binance archive span for subsequent offline frames |
| `GET /healthz` | Process liveness; no exchange request |
| `GET /readyz` | Cached default live-market observation; no exchange request |

Each `/api/v1/...` route has a legacy `/api/...` compatibility alias. Existing heatmap fields remain. These aliases use the corrected model and include its version; numerical output changes from the previous release. A schema version describes the envelope; a model version describes numerical semantics. Pin both for research. Unknown routes return 404.

Heatmap, features and levels share these query parameters:

| Parameter | Default | Accepted values |
| --- | --- | --- |
| `exchange` | `okx` | `okx`, `binance`, `demo`, `aggregate` |
| `symbol` | `BTC` | 2–12 letter/digit base ticker, optionally followed by USDT |
| `range` | `24h` | `12h`, `24h`, `3d`, `7d`, `30d`, `90d`, `180d`, `1y`, `2y` |
| `model` | `1` | `1`, `2`, `3`; inspect scenario assumptions in status and response |
| `mode` | `pair` | `pair`, `symbol` (aggregate by base asset) |
| `asof` | live | Unix **seconds**, or ISO time with explicit timezone |
| `bar` | range default | `5m`, `15m`, `1h`, `4h`, `12h`, `1d` |
| `replay` | `0` | `0` or `1`; `1` requires a previously loaded span and historical `asof` |
| `visible_bars` | all loaded bars | Integer 2–3000, no larger than available loaded history; show only this tail after modeling the full selected range |

`asof` and explicit `bar` are Binance pair-only. At most 3000 bars per view; the default bars vary by range. A past request uses only fully closed bars at its cutoff. For compatibility, an `asof` at or after the current clock selects live data. Duplicate parameters, invalid selections and excessive query fields/length return 400. Levels additionally requires `t` (a bar-open Unix **millisecond** timestamp from `times`) and finite `price` in quote units. Replay takes `symbol`, `range`, `bar`, `from`, `to` (same timestamp input conventions as `asof`); `from` is required. Omitting `to` uses the current time. Replay is limited to 31 days and 12000 total span bars including the lookback window. Load errors do not silently substitute demo data.

```sh
curl 'http://127.0.0.1:8765/api/v1/features?exchange=demo&symbol=BTC&range=12h&model=1'
curl 'http://127.0.0.1:8765/api/v1/heatmap?exchange=okx&symbol=BTC&range=24h'
# Model all 24h, display only the final 36 bars:
curl 'http://127.0.0.1:8765/api/v1/heatmap?exchange=demo&range=24h&visible_bars=36'
curl 'http://127.0.0.1:8765/api/v1/features?exchange=binance&symbol=BTC&range=24h&bar=15m&asof=2021-05-19T23:59:59Z'
curl 'http://127.0.0.1:8765/api/v1/replay?symbol=BTC&range=24h&bar=15m&from=2021-05-18T00:00:00Z&to=2021-05-20T00:00:00Z'
# After loading that span, this frame never fetches from the network:
curl 'http://127.0.0.1:8765/api/v1/features?exchange=binance&symbol=BTC&range=24h&bar=15m&asof=2021-05-19T23:59:59Z&replay=1'
```

## Envelope and quality

Both heatmap and features include these required metadata fields:

| Field | Type / meaning |
| --- | --- |
| `schema_version` | String `1.0` |
| `model_version` | String `2.0.0`; changed native-OI, causal-state and post-close accounting |
| `data_fingerprint` | `sha256:<64 hex digits>` of canonical source-series values and provenance |
| `quality` | Structured source assessment described below |
| `assumptions` | Array of plain-text model assumptions |
| `time_semantics` | Object defining bar opens, post-close state and generation time |
| `units` | Object defining prices, estimated entry-notional exposure, percent windows, ratios, fractions and seconds |
| `features` | Exact last post-close logical-state feature object |
| `feature_as_of` | Unix ms close of the final grid bar represented by `features` |
| `model_metadata` | Parameter digest, units, warmup anchor, insufficient-history flag, per-bar venue coverage, excluded observations, OI accounting and flow-prior choices |

Market context remains at the root: `exchange`, `exchange_name`, `symbol` (e.g. `BTCUSDT`), `coin`, `mode`, `range`, `bar`, `bar_seconds`, `model`, `source`, `venues`, `note`, `warnings`, `stale`, `times`. The `model` object supplies scenario weights, decay, churn, rendering kernel and liquidation formulas.

`times` contains ascending bar-open Unix ms timestamps. `as_of` is the final source-series bar **open** in Unix ms; it is not receipt time. `asof` is the requested historical closed-bar cutoff in Unix ms, or null for live. `feature_as_of` identifies the final grid bar **close** after the state update. `generated_at` is server response-generation Unix ms; it proves neither source freshness nor historical public availability. `time_semantics.state` is `bar_close_post_update`.

`quality` contains:

- `status`: `synthetic`, `stale`, `degraded`, `observed`, or `unknown`. Observed means source data were loaded with provenance; it is not an exchange audit or quality guarantee.
- `synthetic`, `stale`, `degraded`: explicit booleans; for live `okx`, `binance-live` and `aggregate` sources, stale includes a current source bar-close age check against twice the bar interval. Historical/as-of/replay and reconstructed archive views preserve the loader assessment of their requested historical boundary. Root `stale` agrees with `quality.stale`.
- `source_as_of`: same source bar-open Unix ms as root `as_of`.
- `coverage`: `candle_observed`, `oi_observed`, `flow_observed`, each `{observed: integer, total: integer}` or null if masks are unavailable. Filled values do not increase observed counts.
- `degraded`: true when candle/OI observation masks contain gaps; a degraded source is not ready. Missing flow is disclosed by its mask and model prior.
- `coverage_basis`: `source_masks` when loader provenance exists, otherwise `inferred_from_series`; inferred masks do not establish observation history.
- `historical_availability`: `known` only when every OI availability boundary is positive and finite, provenance explicitly says `availability="recorded_receipt"` or `"known_publication"`, and history is neither reconstructed archive nor synthetic; otherwise `unknown`. This does not certify availability of every other input. Current archive loaders cannot retrospectively prove receipt times.
- `provenance`: actual loader metadata, or an empty object; `warnings`: source warnings.

The fingerprint includes numeric data, native OI, observation/availability masks, market identity, bar interval, venue components, provenance, source and source timestamp. It excludes generated time, model choice, prose notes/warnings, transport `provenance.received_at_ms` and the age-dependent stale flag. Event/availability timestamps consumed by the model remain included. It identifies the input representation, not a provider revision guarantee or a snapshot file checksum. Canonical numeric arrays use explicit little-endian dtypes and normalized NaN bytes. A captured response is historical evidence of what this service returned at capture time; an archive reconstruction cannot establish what was publicly available at the historical event time.

The history anchor is the beginning of the loaded window; unknown earlier inventory is not estimated as known. Select `range` for model history and optionally `visible_bars` for a shorter displayed tail. This does not fetch extra history: all loaded bars seed state, then chart arrays/times and per-bar model coverage are trimmed. Final logical features, profiles and full-source fingerprint stay the same. `model_metadata.history_bars`, `visible_bars` and `warmup_anchor_ms` make this separation explicit; quality coverage still describes the full source history. Levels accepts only timestamps in the visible tail when this parameter is set. `model_metadata.insufficient_history` explicitly remains true. Venue membership and data gaps are disclosed by `venue_coverage` and `excluded_venue_observations`. Model 3's neutral fallback and Model 2's assumed turnover remain scenarios.

## Exact features and units

All dollar amounts below are estimated **entry-notional USD**, using quote prices as an approximation. They are neither account-level measured positions nor predictions of future realized liquidations. They use exact modeled liquidation levels, so shelf locations and window sums can differ slightly from the smoothed display-bin analysis.

| Feature | Definition |
| --- | --- |
| `resting_long_usd`, `resting_short_usd` | Remaining modeled inventory after the last closed-bar update |
| `opened_usd` | Last-bar additions after prior-price sweeps, OI reduction and elapsed-time decay; includes explicit turnover in Model 2 |
| `swept_long_usd`, `swept_short_usd` | Prior modeled inventory removed by the last bar's observed price trigger; trade-price OHLC approximates mark-price triggers |
| `imbalance` | Array of four objects with `window_pct` 1, 2, 5, 10, long exposure below price, short exposure above price and `short_to_long` ratio |
| `nearest_long_price`, `nearest_short_price` | Nearest exact modeled level strictly below / above final close; null means no qualifying modeled level |
| `nearest_long_distance_pct`, `nearest_short_distance_pct` | Signed distance from final close to those exact levels, in percent; null when the corresponding level is absent |
| `concentration_top_level_share` | Largest equal-price inventory sum divided by all remaining inventory; null means no modeled inventory |
| `persistence_usd_weighted_seconds` | Entry-notional-weighted age of remaining cohorts; null means no modeled inventory |

Each imbalance row is `{window_pct, long_usd, short_usd, short_to_long}`. The ratio is null when the long denominator is zero, including an empty window. Window sizes and legacy `analysis.distance_pct` are **percent**, ratios can exceed one, concentration is a fraction, persistence is seconds, and price is USDT per base asset. Zero modeled inventory means no retained cohorts under this history and scenario; it does not establish zero actual market risk. Missing source information is disclosed in quality and model metadata, rather than upgraded to verified observations.

## Binary arrays

Heatmap chart fields are base64-encoded contiguous **little-endian IEEE-754 float32** arrays:

| Fields | Shape |
| --- | --- |
| `open`, `high`, `low`, `close`, `volume` | `(len(times),)`; volume is quote USD approximation |
| `centers`, `profile_long`, `profile_short` | `(bins,)` |
| `heat` | `(bins, len(times))`, row-major; long + short estimated entry notional |
| `profile_long_tiers`, `profile_short_tiers` | `(len(tiers), bins)`, row-major |

Profiles describe the same final post-update state. Kernels redistribute exposure for display while preserving mass. `analysis` is display-bin analysis; `features` is the research contract. `tiers` gives integer leverage identifiers. Example decoding:

```python
import base64
import json
import urllib.request
import numpy as np

url = 'http://127.0.0.1:8765/api/v1/heatmap?exchange=demo&range=12h'
with urllib.request.urlopen(url, timeout=30) as response:
    payload = json.load(response)
assert payload['schema_version'] == '1.0'
grid = np.frombuffer(base64.b64decode(payload['heat']), dtype='<f4')
grid = grid.reshape(payload['bins'], len(payload['times']))
print(payload['quality']['status'], payload['features'])
```

## Errors and readiness

Error responses preserve `error` as a string and add a stable `code`:

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `invalid_request` | Validation or bounds failure; fix the query |
| 404 | `not_found` | No such route / asset |
| 409 | `replay_expired` | No loaded span covers the requested replay frame; also `replay_expired: true` |
| 429 | `busy` | Expensive request slots are occupied; retry with backoff |
| 503 | `server_busy` | All 16 connection workers are occupied; retry with backoff |
| 503 | `deadline_exceeded` | Cooperative 60-second request budget exhausted |
| 503 | `source_unavailable` | Validated query could not load its public source |
| 500 | `internal_error` | Unexpected build/serialization failure; detail is logged server-side |

For example, an unavailable source returns `{"error":"Could not reach public source...","code":"source_unavailable"}` with HTTP 503; an expired frame returns `{"error":"This replay is no longer loaded. Load it again.","code":"replay_expired","replay_expired":true}` with HTTP 409. A delayed source can still return HTTP 200 with `quality.status="stale"`; inspect quality before use. The server never silently labels synthetic values live.

`/healthz` returns `{"ok":true,"process":"healthy"}`. `/readyz` returns `{ok, process, data}` with HTTP 200 only if the default **OKX / BTC / 24h / pair / live** cache contains a nonsynthetic, nonstale observation with provenance and complete candle/OI observation masks, within its 45-second series TTL, whose bar close is no older than twice its 5-minute bar interval (**600 seconds / 10 minutes**). Its `data` contains `status`, `market`, `freshness_budget_seconds: 600`, and source/timestamp when available. All other states return 503: `unknown`, `expired`, `stale`, `synthetic`, `degraded`. Another market, demo or historical response cannot satisfy the probe. The probe performs no network request and starts unknown until a client requests the default live view. This explicit bar-based budget is a source-availability signal, not a latency SLA; consumers needing tighter freshness must check event timestamps themselves. HTTP 429/503 responses include `Retry-After: 5`; apply bounded exponential backoff and jitter. The transport admits 16 connection workers with a 10-second socket idle timeout; when all workers are occupied, even probes can receive 503 `server_busy`. A cooperative 60-second handler budget propagates through source HTTP calls, retries and archive work, and rejects late success before response headers; it does not forcibly preempt CPU work or every in-flight socket operation. See [operations](OPERATIONS.md).
