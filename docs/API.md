# HTTP API v1

Machine-readable contract: [OpenAPI 3.1](openapi.json).

Run `python -m liqmap.server` and use `http://127.0.0.1:8765`. No API key is needed. Public sources can be unavailable or delayed; demo is explicitly synthetic. HTTP responses use UTF-8 strict JSON (non-finite scalars are never emitted), `Cache-Control: no-store`, and optional gzip.

Application **0.4.0**, API schema **1.0**, model **2.0.0**.

## Routes and parameters

| Route | Result |
| --- | --- |
| `GET /api/v1/heatmap` | Chart arrays, display analysis, exact logical features, market context and metadata |
| `GET /api/v1/features` | Same metadata, market context, times and exact features, without chart arrays or display analysis |
| `GET /api/v1/context` | Live funding and open-interest snapshot per venue |
| `GET /api/v1/liquidations` | Observed recent OKX filled liquidation orders; exchange records, not model output |
| `GET /api/v1/watchlist` | Nearest modeled liquidation clusters for up to six symbols |
| `GET /api/v1/status` | Process configuration, available scenarios/ranges and capabilities |
| `GET /api/v1/levels` | Display-bin leverage breakdown at `t` and `price` |
| `GET /api/v1/replay` | Load a bounded Binance archive span for subsequent offline frames |
| `GET /healthz` | Process liveness; no exchange request |

Each `/api/v1/...` route has a legacy `/api/...` compatibility alias. Heatmap, features and levels use model 2.0.0. Heatmap and features include schema and model versions, but levels does not. A schema version describes the envelope; a model version describes numerical semantics. Record both from the corresponding heatmap or features response for research. Unknown routes return 404.

Heatmap, features and levels share these query parameters:

| Parameter | Default | Accepted values |
| --- | --- | --- |
| `exchange` | `okx` | `okx`, `binance`, `bybit`, `demo`, `aggregate` |
| `symbol` | `BTC` | 1–16 letter/digit base ticker, optionally followed by USDT |
| `range` | `24h` | `12h`, `24h`, `3d`, `7d`, `30d`, `90d`, `180d`, `1y`, `2y` |
| `model` | `1` | `1`, `2`, `3`; inspect scenario assumptions in status and response |
| `mode` | `pair` | `pair`, `symbol` (aggregate by base asset across OKX, Binance and Bybit) |
| `asof` | live | Unix **seconds**, or ISO time with explicit timezone |
| `bar` | range default | `5m`, `15m`, `1h`, `4h`, `12h`, `1d` |
| `replay` | `0` | `0` or `1`; `1` requires a previously loaded span and historical `asof` |
| `visible_bars` | all loaded bars | Integer 2–3000, no larger than available loaded history; show only this tail after modeling the full selected range |

`exchange=aggregate` and `mode=symbol` both build the all-venue view. `asof` and explicit `bar` are Binance pair-only; Bybit and the aggregate are live only. At most 3000 bars per view; the default bars vary by range. The default chart uses 720 price bins; decode using the returned `bins` value. Exact economic features are independent of chart resolution. A past request uses only fully closed bars at its cutoff. For compatibility, an `asof` at or after the current clock selects live data. Duplicate parameters, invalid selections and excessive query fields/length return 400. Levels additionally requires `t` (a bar-open Unix **millisecond** timestamp from `times`) and finite `price` in quote units. Replay takes `symbol`, `range`, `bar`, `from`, `to` (same timestamp input conventions as `asof`); `from` is required. Omitting `to` uses the current time. Alternatively, `span` (seconds, up to 730 days) replays the newest data Binance can serve, without `from` or `to`: it ends at the current bar when the live Binance API answers, otherwise at the public archive's newest bar, and `note` says so. The UI's Last 24h / 3 days / 7 days presets use `span`. Replay is limited to 730 days of playback, 12000 total span bars including the lookback window, and 1100 days of total source history including lookback. Large cold archive loads can exceed the 60-second cooperative request budget; choose a shorter interval if necessary. Load errors do not silently substitute demo data.

```sh
curl 'http://127.0.0.1:8765/api/v1/features?exchange=demo&symbol=BTC&range=12h&model=1'
curl 'http://127.0.0.1:8765/api/v1/heatmap?exchange=okx&symbol=BTC&range=24h'
curl 'http://127.0.0.1:8765/api/v1/features?exchange=bybit&symbol=ETH&range=24h'
curl 'http://127.0.0.1:8765/api/v1/features?exchange=aggregate&symbol=BTC&mode=symbol&range=3d'
# Model all 24h, display only the final 36 bars:
curl 'http://127.0.0.1:8765/api/v1/heatmap?exchange=demo&range=24h&visible_bars=36'
curl 'http://127.0.0.1:8765/api/v1/features?exchange=binance&symbol=BTC&range=24h&bar=15m&asof=2021-05-19T23:59:59Z'
curl 'http://127.0.0.1:8765/api/v1/replay?symbol=BTC&range=24h&bar=15m&from=2021-05-18T00:00:00Z&to=2021-05-20T00:00:00Z'
# The newest 3 days Binance can serve from your network:
curl 'http://127.0.0.1:8765/api/v1/replay?symbol=BTC&range=24h&bar=15m&span=259200'
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

Both also carry the [`market_context`](#market-context) object.

Market identity remains at the root: `exchange`, `exchange_name`, `symbol` (e.g. `BTCUSDT`), `coin`, `mode`, `range`, `bar`, `bar_seconds`, `model`, `source`, `venues`, `note`, `warnings`, `stale`, `times`. The `model` object supplies scenario weights, decay, churn, rendering kernel and liquidation formulas. For the aggregate, `venues` lists the venues actually included.

`times` contains ascending bar-open Unix ms timestamps. `as_of` is the final source-series bar **open** in Unix ms; it is not receipt time. `asof` is the requested historical closed-bar cutoff in Unix ms, or null for live. `feature_as_of` identifies the final grid bar **close** after the state update. `generated_at` is server response-generation Unix ms; it proves neither source freshness nor historical public availability. `time_semantics.state` is `bar_close_post_update`.

`quality` contains:

- `status`: `synthetic`, `stale`, `degraded`, `observed`, or `unknown`. Observed means source data were loaded with provenance; it is not an exchange audit or quality guarantee.
- `synthetic`, `stale`, `degraded`: explicit booleans. Live sources, including Binance archive fallback, are stale when the latest bar with both observed price and open interest is older than twice the bar interval. Aggregate sources exclude stale venues, mark missing or incomplete venue coverage degraded, and refresh cached aggregates when a component ages out. The aggregate expects OKX, Binance and Bybit. Historical/as-of/replay views retain the loader assessment of their requested boundary. Root `stale` agrees with `quality.stale`.
- `source_as_of`: same source bar-open Unix ms as root `as_of`.
- `coverage`: `candle_observed`, `oi_observed`, `flow_observed`, each `{observed: integer, total: integer}`. Filled values do not increase observed counts. Bybit publishes no historical taker flow, so a Bybit view reports zero observed flow bars.
- `degraded`: true when candle/OI observation masks contain gaps; a degraded source is not ready. Missing flow is disclosed by its mask and model prior.
- `coverage_basis`: `source_masks` when loader provenance exists, otherwise `inferred_from_series`; inferred masks do not establish observation history.
- `historical_availability`: `known` only when every OI availability boundary is positive and finite, provenance explicitly says `availability="recorded_receipt"` or `"known_publication"`, and history is neither reconstructed archive nor synthetic; otherwise `unknown`. This does not certify availability of every other input. Current archive loaders cannot retrospectively prove receipt times.
- `provenance`: actual loader metadata, or an empty object; `warnings`: source warnings.

The fingerprint includes numeric data, native OI, observation/availability masks, market identity, bar interval, venue components, provenance, source and source timestamp. It excludes generated time, model choice, prose notes/warnings, transport `provenance.received_at_ms` and the age-dependent stale flag. Event/availability timestamps consumed by the model remain included. It identifies the input representation, not a provider revision guarantee or a snapshot file checksum. Canonical numeric arrays use explicit little-endian dtypes and normalized NaN bytes. A captured response is historical evidence of what this service returned at capture time; an archive reconstruction cannot establish what was publicly available at the historical event time.

The history anchor is the beginning of the loaded window; unknown earlier inventory is not estimated as known. Select `range` for model history and optionally `visible_bars` for a shorter displayed tail. This does not fetch extra history: all loaded bars seed state, then chart arrays/times and per-bar model coverage are trimmed. Final logical features, profiles and full-source fingerprint stay the same. `model_metadata.history_bars`, `visible_bars` and `warmup_anchor_ms` make this separation explicit; quality coverage still describes the full source history. Levels accepts only timestamps in the visible tail when this parameter is set. `model_metadata.insufficient_history` explicitly remains true. Venue membership and data gaps are disclosed by `venue_coverage` and `excluded_venue_observations`. Model 3's neutral fallback and Model 2's assumed turnover remain scenarios.

## Market context

Heatmap and features responses include a `market_context` object.

Every value describes the **displayed window**. With `visible_bars`, that is the displayed tail, not the full modeled history.

| Field | Type | Meaning |
| --- | --- | --- |
| `price_change_pct` | number or null | Percent change from the first displayed close to the last displayed close |
| `oi_usd` | number or null | Last observed open interest in USD. For the aggregate, the sum across the included venues |
| `oi_change_pct` | number or null | Percent change from the first observed to the last observed USD open interest in the window |

- Null means the value could not be computed from observed data, for example when the window has no observed open interest. Null is not zero.
- USD open interest is native quantity times price. It rises and falls with price even when no position opens or closes. Compare `oi_change_pct` with `price_change_pct` before reading it as new positions. The model itself uses native quantity changes.
- For the aggregate, `oi_usd` depends on which venues are included. A venue that drops out lowers the total although no position closed. Check `venues` and `quality`.
- These values summarize the same source series. They are not model inputs and do not change `features`.

```sh
curl -s 'http://127.0.0.1:8765/api/v1/features?exchange=demo&symbol=BTC&range=24h' \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["market_context"])'
```

## Exact features and units

All dollar amounts below are estimated **entry-notional USD**, using quote prices as an approximation. They are neither account-level measured positions nor predictions of future realized liquidations. They use exact modeled liquidation levels, so cluster locations and window sums can differ slightly from the smoothed display-bin analysis.

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

Do not parse the display-bin `analysis.summary` text; use `analysis` fields or `features`.

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

## Status and capabilities

`GET /api/v1/status` returns `ok`, `schema_version`, `version` (the application version, `0.4.0`), `default`, `exchanges`, `ranges`, `models`, `mmr` and `coins`. `exchanges` lists `okx`, `binance`, `bybit` and `demo`; the aggregate is a view over the live venues, not a separate entry.

It also returns `capabilities`:

```json
"capabilities": {"context_venues": ["okx", "binance", "bybit"], "observed_liquidations": ["okx"], "watchlist_max_symbols": 6}
```

| Field | Meaning |
| --- | --- |
| `context_venues` | Live venues `/api/v1/context` can query |
| `observed_liquidations` | Venues whose filled liquidation orders `/api/v1/liquidations` can return |
| `watchlist_max_symbols` | Most symbols one `/api/v1/watchlist` request accepts |

## Live-only routes

`/api/v1/context`, `/api/v1/liquidations` and `/api/v1/watchlist` describe the market now. Passing `asof`, `bar` or `replay=1` returns 400 `invalid_request`. Unknown parameters are ignored, as on the other routes. `exchange=demo` in pair mode never contacts an exchange. `mode=symbol` always means the live aggregate, also when `exchange=demo`, matching the heatmap.

## Funding and open interest: `/api/v1/context`

A live snapshot of funding and open interest for each venue. This is market context. The model does not use it.

| Parameter | Default | Accepted values |
| --- | --- | --- |
| `exchange` | `okx` | `okx`, `binance`, `bybit`, `demo`, `aggregate` |
| `symbol` | `BTC` | 1–16 letter/digit base ticker, optionally followed by USDT |
| `mode` | `pair` | `pair` (the selected venue) or `symbol` (OKX, Binance and Bybit for that base asset) |

`exchange=aggregate` or `mode=symbol` returns all three live venues. `exchange=demo` returns synthetic values with `synthetic: true` and never contacts an exchange. `range` and `model` are ignored; `asof`, `bar` and `replay=1` are rejected.

Response:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1.0` |
| `coin`, `symbol` | Base asset (e.g. `BTC`) and pair (e.g. `BTCUSDT`) |
| `exchange`, `mode` | The requested view |
| `generated_at` | Response generation time, Unix ms |
| `synthetic` | `true` for demo |
| `venues` | One object per venue, described below |
| `funding_rate_oi_weighted` | Each venue's rate is first scaled to `funding_basis_hours` (rate × 8 / interval), then weighted by its USD open interest. Venues without a known interval, rate or open interest are left out; null when none qualify |
| `funding_basis_hours` | `8`: the interval `funding_rate_oi_weighted` is expressed in |
| `open_interest_usd_total` | Sum of `open_interest_usd` over venues that reported it; null when none did |
| `note` | Plain-text explanation |

Each entry in `venues`:

| Field | Meaning |
| --- | --- |
| `exchange`, `name` | Venue id and display name |
| `ok` | `false` when this venue failed. Then `error` says why; do not read its other fields as zero |
| `funding_rate` | Current funding rate as a **fraction per funding interval**: `0.0001` = 0.01% per interval. Positive means longs pay shorts |
| `next_funding_time` | Next funding settlement, Unix ms |
| `funding_interval_hours` | Hours between funding settlements for this contract |
| `open_interest_base` | Open interest in the base asset |
| `open_interest_usd` | Open interest in USD |
| `mark_price` | The venue's mark price |
| `observed_at` | Oldest timestamp among this venue's snapshot responses, Unix ms |
| `error` | Null, or the reason this venue failed |

A numeric field can be null when the venue did not report it. Venues and contracts can settle funding on different intervals. Check `funding_interval_hours` before comparing per-venue rates. When some venues fail, `note` says how many venues the totals cover.

Sources: OKX public funding-rate and open-interest endpoints; Binance `premiumIndex`, `openInterest` and `fundingInfo`; Bybit linear `tickers`.

Behavior:

- Venue errors are isolated. A blocked or failing venue appears with `ok: false`; the others are still returned.
- When every live venue in the request fails, the route returns 503 `source_unavailable`. In pair mode that means the one requested venue failed.
- Responses are cached for **30 seconds**, at most **32 entries**. Two requests within 30 seconds can return the same snapshot.
- The route counts against the expensive-request slots and the 60-second cooperative budget (see [errors](#errors-and-health)).

```sh
curl 'http://127.0.0.1:8765/api/v1/context?exchange=okx&symbol=BTC'
curl 'http://127.0.0.1:8765/api/v1/context?exchange=bybit&symbol=ETH'
curl 'http://127.0.0.1:8765/api/v1/context?exchange=aggregate&symbol=BTC&mode=symbol'
curl 'http://127.0.0.1:8765/api/v1/context?exchange=demo&symbol=BTC'
```

## Observed liquidations: `/api/v1/liquidations`

Recent **filled liquidation orders reported by OKX**. These are exchange records, not model output. The model does not use them.

| Parameter | Default | Accepted values |
| --- | --- | --- |
| `exchange` | `okx` | `okx`, `binance`, `bybit`, `demo`, `aggregate` |
| `symbol` | `BTC` | 1–16 letter/digit base ticker, optionally followed by USDT |
| `mode` | `pair` | `pair`, `symbol` |
| `range` | `24h` | Same values as heatmap; the time window of interest |

| Request | Result |
| --- | --- |
| `exchange=okx` | OKX events for the pair |
| `exchange=aggregate` or `mode=symbol` | OKX events. OKX is the only observed source, so these are **not** all-venue liquidations |
| `exchange=binance` or `exchange=bybit`, pair mode | `available: false` with a `note` and no events. Binance and Bybit publish liquidations only over websocket streams |
| `exchange=demo` | Synthetic events with `synthetic: true` |

Response:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1.0` |
| `coin`, `symbol` | Base asset and pair |
| `exchange`, `mode`, `range` | The requested view |
| `source` | `okx`, `demo`, or null when no observed source was read |
| `available` | `false` when the requested venue has no observed-liquidation source |
| `synthetic` | `true` for demo |
| `events` | Liquidation events, described below |
| `coverage` | `{from_ms, to_ms, count, truncated}`, described below |
| `generated_at` | Response generation time, Unix ms |
| `note` | Plain-text explanation |

Each event:

| Field | Meaning |
| --- | --- |
| `ts` | Event time, Unix ms |
| `price` | OKX **bankruptcy price** of the liquidated position, USDT |
| `side` | Side of the **liquidated position**: a long or a short |
| `size_base` | Size in the base asset: contracts × contract value |
| `usd` | contracts × contract value × `price` |

Coverage:

- The server reads up to **10 pages of the 100 most recent** filled orders, so at most 1,000 orders per request.
- Events are limited to the requested `range`, newest last. `coverage.from_ms` and `coverage.to_ms` are the oldest and newest returned events. `count` is the number of events.
- `coverage.truncated` is `true` when the range is not fully covered: the range starts more than 22 hours ago (OKX publishes only about the last 22 to 24 hours of liquidation orders), the 10-page cap was reached inside the range, or a later page failed after earlier pages succeeded. If the first page fails, the route returns 503 `source_unavailable`. On a busy market or a long `range`, the start of the window may then have no data. No dots in an uncovered period does not mean no liquidations happened.

Reading the price: an exchange triggers liquidation from the mark price and maintenance margin, before the bankruptcy price is reached. The bankruptcy price therefore lies beyond the trigger: below it for a long, above it for a short. The modeled bands use a maintenance-margin trigger, so dots and bands are not expected to line up exactly.

Behavior: responses are cached for **30 seconds**. The route counts against the expensive-request slots and the 60-second cooperative budget.

```sh
curl 'http://127.0.0.1:8765/api/v1/liquidations?exchange=okx&symbol=BTC&range=24h'
curl 'http://127.0.0.1:8765/api/v1/liquidations?exchange=aggregate&symbol=ETH&mode=symbol&range=12h'
curl 'http://127.0.0.1:8765/api/v1/liquidations?exchange=bybit&symbol=BTC'   # available: false
curl 'http://127.0.0.1:8765/api/v1/liquidations?exchange=demo&symbol=BTC'    # synthetic
```

## Watchlist: `/api/v1/watchlist`

The nearest modeled liquidation clusters for several coins in one request. Live only.

| Parameter | Default | Accepted values |
| --- | --- | --- |
| `exchange` | `okx` | `okx`, `binance`, `bybit`, `demo`, `aggregate` |
| `range` | `24h` | Same values as heatmap |
| `model` | `1` | `1`, `2`, `3` |
| `mode` | `pair` | `pair`, `symbol` |
| `symbols` | — | Required. Comma-separated base tickers, 1–6. Duplicates are dropped. Example: `BTC,ETH,SOL` |

`asof`, `bar` and `replay=1` are rejected. Use `/api/v1/features` for a historical or custom-bar view of one symbol. Missing, empty or more than six symbols return 400. An invalid symbol in the list is returned as its own item with `ok: false`.

Response:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1.0` |
| `exchange`, `mode`, `range`, `model` | The canonical view: `mode=symbol` reports `aggregate` / `symbol` |
| `generated_at` | Response generation time, Unix ms |
| `items` | One item per requested symbol, in request order |

Each item:

| Field | Meaning |
| --- | --- |
| `coin` | Base asset |
| `ok` | `false` when this symbol failed. Then `error` says why |
| `price` | Last close |
| `price_change_pct` | Percent change over the window, as in `market_context` |
| `quality` | Source quality for this symbol (see `quality.status` above) |
| `stale` | Same meaning as the heatmap's root `stale` |
| `above` | Nearest **short** liquidation cluster above price, `{price, distance_pct, usd}`, or null |
| `below` | Nearest **long** liquidation cluster below price, `{price, distance_pct, usd}`, or null |
| `short_to_long_5pct` | Short exposure above price divided by long exposure below price, within 5% of price; null when it cannot be computed |
| `as_of` | Final source bar open, Unix ms |
| `error` | Null, or the reason this symbol failed |

`above` and `below` are **display-bin clusters**: the same numbers as the chart sidebar and the reading line, not the exact `features`. `distance_pct` is signed percent from price. `usd` is estimated entry-notional USD. For research that must not depend on chart resolution, request `/api/v1/features` for each symbol.

Behavior:

- Each symbol is built independently. A failing symbol returns `ok: false` and does not fail the others.
- The whole request holds **one** expensive-request slot. It loads symbols' market data with up to **3 workers**, then builds the models one at a time, because the model is CPU-bound.
- Symbols use the same series cache as heatmap requests. A coin you already viewed within its cache lifetime is not fetched again.
- The 60-second cooperative budget covers the whole request. If it runs out, ask for fewer symbols or a shorter range.

```sh
curl 'http://127.0.0.1:8765/api/v1/watchlist?exchange=okx&range=24h&symbols=BTC,ETH,SOL'
curl 'http://127.0.0.1:8765/api/v1/watchlist?exchange=aggregate&mode=symbol&range=3d&model=2&symbols=BTC,ETH,SOL,XRP,DOGE,BNB'
curl 'http://127.0.0.1:8765/api/v1/watchlist?exchange=demo&symbols=BTC,ETH'
```

## Errors and health

Error responses preserve `error` as a string and add a stable `code`:

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `invalid_request` | Validation or bounds failure; fix the query |
| 403 | `forbidden` | An `/api/` request sent by another website (`Sec-Fetch-Site: cross-site`), or, on a loopback bind, addressed to a `Host` other than `127.0.0.1`, `localhost` or `[::1]` |
| 404 | `not_found` | No such route / asset |
| 409 | `replay_expired` | No loaded span covers the requested replay frame; also `replay_expired: true` |
| 429 | `busy` | Expensive request slots are occupied; retry with backoff |
| 503 | `server_busy` | All 16 connection workers are occupied; retry with backoff |
| 503 | `deadline_exceeded` | Cooperative 60-second request budget exhausted |
| 503 | `source_unavailable` | Validated query could not load its public source |
| 500 | `internal_error` | Unexpected build/serialization failure; detail is logged server-side |

The same codes apply to the context, liquidations and watchlist routes. Heatmap, features, levels, replay loading, context, liquidations and watchlist share the **4 expensive-request slots**; a fifth concurrent request receives 429 `busy`.

For example, an unavailable source returns `{"error":"Could not reach public source...","code":"source_unavailable"}` with HTTP 503; an expired frame returns `{"error":"This replay is no longer loaded. Load it again.","code":"replay_expired","replay_expired":true}` with HTTP 409. A delayed source can still return HTTP 200 with `quality.status="stale"`; inspect quality before use. The server never silently labels synthetic values live.

`/healthz` returns `{"ok":true,"process":"healthy"}` and makes no exchange request; it says nothing about data freshness, which each response reports in `quality`. HTTP 429/503 responses include `Retry-After: 5`; apply bounded exponential backoff and jitter. The transport admits 16 connection workers with a 10-second socket idle timeout; when all workers are occupied, even `/healthz` can receive 503 `server_busy`. A cooperative 60-second handler budget propagates through source HTTP calls, retries and archive work, and rejects late success before response headers; it does not forcibly preempt CPU work or every in-flight socket operation. See [operations](OPERATIONS.md).
