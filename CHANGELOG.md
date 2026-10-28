# Changelog

## 0.3.0 — 2026-10-28

### Changed
- Model 2.0.0 separates exact economic inventory from rendering and fixes close-entry/same-bar sweep chronology.
- Public-data adapters preserve native OI and source-quality semantics; venue inventories remain separate before aggregation.
- Historical replay, source availability and warm-up limitations are explicit rather than implied to be production backtest guarantees.
- Desktop/mobile controls prioritize the chart, with progressive research/display panels, timezone selection and keyboard controls.

### Added
- Versioned research API, metadata/fingerprints, exact features, readiness/health checks and bounded runtime work.
- Tamper-evident local snapshots, offline inspection/exports, normalized reference agreement and held-out diagnostic tools.
- Installable wheel and console commands, packaged static assets, offline CI and browser acceptance.
- Original Open Liq logo, reproducible benchmark and release evidence.

### Migration
Numerical estimates differ from 0.2.0 by design. Recalibrate and re-evaluate strategy thresholds; see [migration notes](docs/MIGRATION.md). No predictive-accuracy, uptime or CoinGlass-replacement guarantee accompanies this release.
