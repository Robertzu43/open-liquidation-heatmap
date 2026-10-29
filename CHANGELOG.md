# Changelog

## 0.4.0 — 2026-10-29

Application 0.4.0, API schema 1.0, model 2.0.0. The model's numbers are unchanged for OKX and Binance.

### Added
- **Bybit** USDT perpetuals as a third live venue (`exchange=bybit`). Candles and native base-asset open interest come from the public v5 API, valued at bar close, closed bars only. Bybit has no 12-hour OI period, so 12-hour bars use the last 4-hour OI snapshot inside each closed bar. Bybit publishes no historical taker flow, so Flow skew uses its neutral fallback for Bybit; account long/short ratios are not substituted. There is no archive fallback: a geo-blocked Bybit API is reported unavailable.
- `market_context` in heatmap and features responses: price change, last observed USD open interest and its change over the displayed window, and the window bounds. Additive; `schema_version` stays `1.0`.
- `GET /api/v1/context`: live funding rate, next funding time, funding interval, open interest and mark price per venue, with an OI-weighted funding rate and total USD open interest. Per-venue errors are isolated. 30-second cache.
- `GET /api/v1/liquidations`: observed recent OKX filled liquidation orders, at OKX's bankruptcy price, with an explicit coverage window and a `truncated` flag. These are exchange records, not model output. Binance and Bybit pair views return `available: false`. 30-second cache.
- `GET /api/v1/watchlist`: price, window price change, quality, nearest short and long liquidation clusters and the ±5% short-to-long ratio for up to six symbols, with per-symbol error isolation.
- `capabilities` in `/api/v1/status`, and `bybit` in its exchange list.
- UI: venue picker with OKX, Binance, Bybit and All venues; a reading line above the chart with the nearest short and long liquidation clusters, distance, size and ±5% ratio; a market strip with open interest, OI change, price change, funding and funding countdown; a watchlist of up to six coins; observed OKX liquidations drawn as dots (red = longs liquidated, green = shorts liquidated); a Replay panel with Last 24h / 3 days / 7 days presets from the Binance archive; an optional price-proximity alert (in-page message and browser notification).

### Changed
- All venues (`exchange=aggregate` or `mode=symbol`) now combines OKX, Binance and Bybit. Aggregate totals change, and a missing or excluded Bybit marks coverage degraded.
- The model summary says "short/long liquidation cluster" instead of "shelf". The numbers are the same.
- Live views refresh every 60 seconds by default.
- Disclaimers are one footer line plus collapsible Source & quality details.
- Side colors are consistent everywhere: short liquidation clusters and liquidated shorts are green, long clusters and liquidated longs are red. The 0.3 sidebar cards and level tags used the opposite colors.
- All venues moved from the Research panel into the venue picker. As-of views and replay moved from the Research panel into the new Replay panel.
- Replay keeps candles in view: each frame can widen the price axis, and it never narrows during playback.
- The main map retries a busy server twice before showing an error, and side requests (context, liquidations, watchlist) are sequenced after the map so one browser stays within the 4 expensive-request slots.
- The CI Python job runs `ruff check` (pyflakes, syntax and bugbear rules) before the tests.
- The three new routes have `/api/` aliases, use the 4 expensive-request slots, the 60-second cooperative budget, 400 `invalid_request` validation and strict finite JSON.
- The README leads with how to read the chart; methodology and evidence stay in the linked docs.

### Notes
- Historical as-of views and replay remain Binance-only.
- Observed liquidations come from OKX only. Binance and Bybit publish liquidations only over websocket streams. Observed liquidations and funding are context; they are not model inputs.
- Not supported: Hyperliquid and other venues without public historical open interest (the model needs OI history); contracts other than USDT-margined linear perpetuals; order placement or account access.
- New outbound host: `api.bybit.com`. See [migration notes](docs/MIGRATION.md) and [operations](docs/OPERATIONS.md).

## 0.3.1 — 2026-10-28

- Replay starts with price on the left and reveals historical price and modeled exposure together as each bar arrives. Future time is blank; earlier modeled exposure can change as the lookback window moves.
- The default chart grid now has 720 price bins instead of 360, and zoomed heatmaps preserve bin boundaries. Exact economic features remain independent of chart resolution; binned shelves and payload size change. README screenshots show observed OKX market prices.
- Replay accepts longer bounded windows, including the 58-day interval reported by a user; large cold downloads can still exceed the request budget.
- Normal client disconnects no longer produce server error tracebacks; idle connection timeouts are logged at debug level.

## 0.3.0 — 2026-10-28

### Changed
- Model 2.0.0 separates exact economic inventory from rendering and fixes close-entry/same-bar sweep chronology.
- Public-data adapters preserve native OI and source-quality semantics; venue inventories remain separate before aggregation. OKX long periods use UTC intervals, with confirmed candles and exact closed-interval alignment.
- Historical replay, source availability and warm-up limitations are explicit rather than implied to be production backtest guarantees.
- Desktop/mobile controls prioritize the chart, with progressive research/display panels, timezone selection and keyboard controls. Replay races are guarded, and exports retain the displayed dataset’s identity during pending selections.

### Added
- Versioned research API, metadata/fingerprints, exact features, readiness/health checks and bounded runtime work.
- Tamper-evident local snapshots, offline inspection/exports, normalized reference agreement and held-out diagnostic tools.
- Installable wheel and console commands, packaged static assets, offline CI and browser acceptance.
- Original Open Liq logo, reproducible benchmark and release evidence.

### Migration
Numerical estimates differ from 0.2.0 by design. Recalibrate and re-evaluate strategy thresholds; see [migration notes](docs/MIGRATION.md). No predictive-accuracy, uptime or CoinGlass-replacement guarantee accompanies this release.
