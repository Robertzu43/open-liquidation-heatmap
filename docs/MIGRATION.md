# Migration notes

Application, API schema and model have separate version numbers. They describe different contracts.

## 0.3 to 0.4

Application **0.4.0**, API schema **1.0**, model **2.0.0**. Nothing was removed. Existing routes, fields and parameters keep their meaning.

### Model numbers

Model 2.0.0 is unchanged. For the same OKX, Binance or demo input, heatmap and features return the same numbers as 0.3.1. Nothing in 0.4.0 requires recalibrating thresholds set on 0.3.x single-venue views.

### All venues now includes Bybit

`exchange=aggregate` and `mode=symbol` now combine OKX, Binance **and Bybit**.

- Aggregate totals change: resting exposure, features and `market_context.oi_usd` now include Bybit's inventory. Recalibrate thresholds set on 0.3 aggregates.
- The aggregate now expects three venues. When Bybit is blocked or stale, it is left out, a warning names it, and `quality` reports degraded coverage. An aggregate that was `observed` in 0.3 can be `degraded` in 0.4 from a network where Bybit is blocked. Check `venues` and `quality.degraded` rather than assuming three venues.
- Model 3 in the aggregate: Bybit's component uses the neutral allocation, because Bybit has no historical taker flow. OKX and Binance components keep their observed flow.
- Historical as-of views and replay remain Binance-only.

### Additive API fields and routes

- `exchange=bybit` is accepted wherever `exchange` is. `/api/v1/status` lists `bybit` and adds a `capabilities` object. Detect 0.4 features from `capabilities`.
- Heatmap and features add an optional `market_context` object. `schema_version` stays `1.0`. Treat the field as optional, because a 0.3 server does not send it.
- New routes: `/api/v1/context` (funding and open interest per venue), `/api/v1/liquidations` (observed OKX liquidation orders) and `/api/v1/watchlist` (nearest clusters for up to six symbols), each with a `/api/` alias. They share the 4 expensive-request slots with the existing routes, so a client that adds them can see more 429 `busy` responses.
- Observed liquidations are exchange records, not model output, and not model inputs. Keep them separate from model features in research data.

See [API.md](API.md) for fields, errors and caching.

### Summary wording

The display-bin `analysis.summary` text uses "short liquidation cluster" and "long liquidation cluster" where 0.3 said "shelf". The numbers did not change. If you parse that text, switch to the `analysis` fields or to exact `features`.

### Operations

Allow outbound HTTPS to `api.bybit.com`. Context and liquidations have 30-second caches, and the browser refreshes live views every 60 seconds by default. See [OPERATIONS.md](OPERATIONS.md).

## 0.2 to 0.3

Application version is **0.3.0**, API schema is **1.0**, and the corrected model is **2.0.0**.

### Model outputs change

Native contract-quantity changes replace price-sensitive dollar-OI changes on supported adapter paths. Close-created cohorts cannot be swept by earlier wicks. Exact cohorts drive liquidation decisions independently of viewport bins, with consistent post-update profiles. Venue gaps and invalid OI restart baselines without artificial recovery flow. Time decay is elapsed-time based.

Do not reuse thresholds calibrated on 0.2.0—or CoinGlass—without re-running out-of-sample evaluation. Similar pictures do not imply interchangeable datasets.

### API clients

Existing `/api/heatmap`, `/api/levels`, `/api/status`, `/api/symbols` and replay routes remain available. New clients should use the documented `/api/v1/` routes and inspect `schema_version`, `model_version`, `quality`, `data_fingerprint`, `time_semantics` and `feature_as_of`.

`times` and `as_of` identify source bar opens. Economic features refer to closed-bar state. A new `generated_at` timestamp does not make a stale source fresh. Unknown/insufficient fields are not trading zeros. Use exact `features` rather than treating binned analysis shelves as resolution-independent quantities.

See [API.md](API.md) for actual fields, limits, routes and error semantics. The `liqmap.research` tools consume v1 envelopes; normalized reference JSON is not a raw CoinGlass API response.

### Python callers

`MarketSeries` now accepts native OI, observed masks and provenance. The old quote-only constructor remains a labeled legacy path; it is not equivalent to native accounting. `build_heatmap` retains existing chart fields and adds metadata and exact economic features. OI records align to their containing bar rather than to the nearest open.

### Installation and hosting

Use `python -m pip install '.[dev]'` from a clone, or install a built wheel. `liqmap` serves the app, and `liqmap-research --help` lists research commands. The server defaults to loopback. Deliberately exposed deployments need the controls in [OPERATIONS.md](OPERATIONS.md).

Existing localStorage preferences may be restored, while URL settings make a shared view explicit. UTC is the default display timezone; local time can be selected.

### Archive cache location

Archives now use `LIQMAP_CACHE_DIR` when set, otherwise `$XDG_CACHE_HOME/open-liq` or `~/.cache/open-liq`. Existing repository `.cache` files are not automatically moved. To reuse them, point `LIQMAP_CACHE_DIR` at the old cache directory or copy its contents into the new directory. Apply an operator retention policy; per-file size bounds are not a total disk quota.
