# Changelog

## 0.4.0 — 2026-10-29

Application 0.4.0, API schema 1.0, model 2.0.0. The model itself is unchanged. Binance Flow skew inputs changed (see Fixed), and a newest bar still waiting for its open interest is left out instead of counted as a gap.

### Added
- **Bybit** USDT perpetuals as a third live venue (`exchange=bybit`). Candles and native base-asset open interest come from the public v5 API, valued at bar close, closed bars only. Bybit's `openInterest` counts both sides of every position, so Open Liq reads its single-side figure to match OKX and Binance. Bybit has no 12-hour OI period, so 12-hour bars use the last 4-hour OI snapshot inside each closed bar. Bybit publishes no historical taker flow, so Flow skew uses its neutral fallback for Bybit; account long/short ratios are not substituted. There is no archive fallback: a geo-blocked Bybit API is reported unavailable.
- `market_context` in heatmap and features responses: price change, last observed USD open interest and its change over the displayed window. Additive; `schema_version` stays `1.0`.
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
- OKX keeps only about the last day of liquidation orders; ranges older than that report `coverage.truncated` and the chart marks where the dots begin.
- An invalid watchlist symbol fails as its own item; the other symbols still load.
- Replay spans stop at the newest bar Binance actually has, so custom spans past a lagging archive no longer offer frames that fail.
- Server warnings use UTC instead of a fixed UTC−5 offset. The startup line prints the host the server is bound to.
- Replay presets ask the server for the newest span Binance can serve (`/api/v1/replay?span=`). Where the live Binance API is blocked, they replay the archive's newest data and say so, instead of stale frames.
- The main map retries a busy server twice before showing an error, and side requests (context, liquidations, watchlist) are sequenced after the map so one browser stays within the 4 expensive-request slots.
- `ruff check` (pyflakes, syntax and bugbear rules) is part of local verification.
- The three new routes have `/api/` aliases, use the 4 expensive-request slots, the 60-second cooperative budget, 400 `invalid_request` validation and strict finite JSON.
- The README leads with how to read the chart; methodology and evidence stay in the linked docs.
- Tickers may be 1–16 letters or digits; OKX lists one-letter and 13-letter perpetuals. TON is no longer suggested because OKX lists no TON perpetual.
- Open-interest coverage warnings give exact bar counts ("covers N of M bars") instead of a rounded percentage.
- Watchlist market data loads in parallel and the models build one at a time; six demo symbols take 0.85 s instead of 3.1 s on the test machine.
- Cold Binance archive loads download 32 files at once over pooled connections (a cold 2-year load: 53 s → 30 s on the test machine).
- Bybit candles are one request; a window over 1,000 bars is refused instead of truncated. OKX uses `/market/candles` only, which serves the newest 1,440 bars; the longest built-in window needs 731.
- The hover tier breakdown is skipped on views over 1,000 bars, where each hover rebuilt the model.
- `scripts/benchmark.py` measures model build time and payload sizes only.

### Fixed
- Binance Flow skew uses each bar's whole taker buy share, the sum of taker buy quote volume over the sum of quote volume of its klines, for archive and live data alike. The archive used to read only the 5-minute row at the bar open (BTC 2025-09-10 09:00 1h: 0.29 instead of the hour's 0.49), and live data no longer needs the taker long/short ratio requests.
- OKX sometimes publishes the newest bar's open interest seconds after its candle. Up to two trailing bars still waiting for it are left out until it arrives instead of degrading quality and being treated as missing; interior gaps are still reported.
- All venues: each venue runs on a budget a little shorter than the request's, and a venue that runs out is left out and reported instead of failing the whole response.
- A truncated cached or downloaded archive zip is downloaded again instead of failing every view; partial files are named per process so servers sharing a cache never write the same one. Retries no longer pause after the final attempt.
- An installed copy serves its packaged web files. It used to serve any top-level `web` folder in site-packages instead.
- Web: dragging the replay slider or holding an arrow key fetches one frame after 150 ms instead of one per event (most got 429); an auto-refresh no longer overrides a coin switch in progress; a tap on a phone opens the tooltip; Space on a panel header opens the panel; prices under 0.01 keep four significant digits; after a failed load the heading and watchlist no longer show the previous market; screen readers no longer hear an unchanged alert banner or every frame of a playing replay.

### Security
- The local server refuses `/api/` requests that a browser marks `Sec-Fetch-Site: cross-site` (403 `forbidden`), so another website cannot make it download exchange data from your IP. On a loopback bind the `Host` header must be `127.0.0.1`, `localhost` or `[::1]`, which blocks DNS rebinding. Same-origin pages, links from other sites to the page, curl and `--host` binds keep working.

### Removed
- `/readyz`, the readiness block in `/api/v1/status` and `/api/v1/symbols`; no client used them. `/healthz` stays.
- `python -m liqmap`. Start the server with `liqmap` or `python -m liqmap.server`.
- The legacy `heatmap.py` PNG command and the matplotlib runtime dependency; the browser exports PNG. matplotlib is only in the `dev` extra now, for `scripts/benchmark.py`. Unused `liqmap.model` helpers went with it: `liq_price_long`, `liq_price_short`, `fade_swept`, `bin_intensity` and the `bybit_uta` venue.
- The GitHub Actions workflow. Verification runs locally with the commands in the README.
- `examples/research.py` no longer writes `strategy-input-contract.example.json`, which no tool read.
- `requirements-dev.txt`; install the `dev` extra instead. The 0.2 migration notes, the 0.3 release review and the 0.3.0 benchmark files.

### Notes
- Historical as-of views and replay remain Binance-only.
- Observed liquidations come from OKX only. Binance and Bybit publish liquidations only over websocket streams. Observed liquidations and funding are context; they are not model inputs.
- Not supported: Hyperliquid and other venues without public historical open interest (the model needs OI history); contracts other than USDT-margined linear perpetuals; order placement or account access.
- New outbound host: `api.bybit.com`. See [operations](docs/OPERATIONS.md).

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
Numerical estimates differ from 0.2.0 by design. Recalibrate and re-evaluate strategy thresholds. No predictive-accuracy, uptime or CoinGlass-replacement guarantee accompanies this release.
