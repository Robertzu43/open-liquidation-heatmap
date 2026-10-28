# Changelog

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
