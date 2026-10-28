# Product Excellence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver an inspectable, causal liquidation research application with reliable contracts, accessible interfaces and honest release evidence.
**Architecture:** Preserve Python data/model/service boundaries and the Canvas UI. Economic inventory is independent of display bins; per-venue provenance reaches the API and research tools.
**Tech Stack:** Python 3.11+, NumPy, requests, matplotlib, standard-library HTTP; HTML/CSS/JS; pytest and Playwright for verification.
**Spec:** `docs/superpowers/specs/2026-10-28-product-excellence-design.md` (approved by user).

## Global Constraints
- Keep keyless public-data and offline demo paths. No order placement, paid services or invented external evidence.
- Proposed release 0.3.0, schema version 1, changed model version explicitly identified.
- Preserve current output fields for UI compatibility where possible; missing data is not zero exposure.
- No framework rewrite. Share one worktree with exclusive file ownership per implementer; coordinator commits completed tasks.
- Reference results and trading advantage remain unverified without authorized external data; final scorecard must say so.

## Review Focus
- Missing, zero or returning venue OI must not create positions (Task 1).
- Future extrema and same-bar wicks must not change past logical inventory (Task 1).
- Overload/expired caches and malformed API requests need bounded, recoverable behavior (Task 2).
- Phone chart, keyboard access and timezone formatting must remain usable across modes (Task 3).
- Historical comparisons must reject mismatched sources/times and never imply alpha from synthetic results (Task 4).

### Task 1: Causal data and model
**Files:** `liqmap/data.py`, `liqmap/model.py`, `tests/test_model.py`, `tests/test_history.py`, new `tests/test_causality.py`.
**Interfaces:** Keep `build_heatmap(series, model_id, tier_column=None)` and existing Heatmap fields. Extend `MarketSeries` with optional native OI, masks/provenance and per-venue components using defaults; expose deterministic model metadata and logical feature totals. Preserve public loader signatures. Coordinate exact added names with Task 2.
- [x] Write failing tests: fixed base OI + price appreciation -> zero new exposure; close entry not swept by prior wick; append extreme future bar -> unchanged earlier state; missing/reappearing venue -> no fake flow; invalid OI rebaselines; final heat/profile totals reconcile; elapsed-time decay is bar-invariant.
- [x] Run `python -m pytest tests/test_causality.py -q` and record expected failures.
- [x] Preserve native Binance/OKX OI and observation masks, remove account-ratio-as-taker fallback, label trade-price approximation; make OI alignment causal.
- [x] Use exact-level cohort state with pre-existing sweeps before close deposits; render afterward, keep venue state separate, document warm-up anchor and observation gaps.
- [x] Run causality plus model/history suites. Update obsolete expectations only for explicitly changed semantics, not to hide bugs.
- [x] Provide changed interfaces and evidence to coordinator for independent review and commit.

### Task 2: Versioned API and bounded runtime
**Files:** `liqmap/service.py`, `liqmap/server.py`, new `liqmap/api.py` if needed, `tests/test_api.py`, `tests/test_runtime.py`, `docs/API.md`, `docs/OPERATIONS.md`.
**Interfaces:** Existing `load_heatmap` fields remain. Add `schema_version`, `model_version`, `data_fingerprint`, `quality`, `assumptions`, `time_semantics` and exact features where Task 1 supports them. Expose `/api/v1/heatmap`, `/api/v1/features`, `/api/v1/status`, `/healthz`, `/readyz`, plus legacy routes. Research recorder consumes v1 heatmap envelopes.
- [x] Add failing HTTP tests for versioned routes, validation errors, source vs process readiness, strict finite JSON, stale quality and stable fingerprints excluding generated_at.
- [x] Implement versioned metadata and features, schema/units documentation and examples; do not invent verified freshness/quality when upstream metadata is absent.
- [x] Bound active expensive requests, series/span cache count and lifetime, replay duration; return explicit 400/409/429/503-style errors as appropriate. Bind loopback by default and document exposed hosting requirements.
- [x] Run `python -m pytest tests/test_api.py tests/test_runtime.py -q`; preserve existing history/replay tests.
- [x] Provide exact contracts and independent-review evidence, then coordinator commits.

### Task 3: Usable responsive research interface
**Files:** `web/index.html`, `web/styles.css`, `web/app.js`; new `tests/browser/smoke.py`.
**Interfaces:** Consume existing response fields and optional v1 quality metadata defensively. Keep element IDs where practical. Logo eventually at `/assets/logo.png`; package integration owned by coordinator.
- [x] Build a repeatable Playwright smoke check: demo load, all models, toggles, error/recovery, keyboard, snapshot, URL settings, desktop and 390x844/360x800 screenshots. Default mobile canvas top <=300px, no page overflow; 44px primary targets.
- [x] Keep asset, venue, range and refresh primary; move model/display/research controls into progressive disclosure; hide unavailable replay with explanation.
- [x] Add useful scenario labels, estimates explanation, explicit source/freshness status, UTC/local choice, shareable settings, touch/keyboard zoom and reset. Keep page scroll possible on touch.
- [x] Use semantic accessible HTML and visible focus; preserve tables as non-canvas equivalent. Preserve snapshot provenance and existing replay behavior.
- [x] Run browser checks with deterministic demo and report visual captures. Coordinator reviews screenshots and integrates logo/version.

### Task 4: Reproducible research, recorder and comparison
**Files:** new `liqmap/research.py`, `tests/test_research.py`, `examples/research.py`, `docs/VALIDATION.md`; scripts under `scripts/` only as needed.
**Interfaces:** CLI `python -m liqmap.research` supports record, inspect and compare/evaluate using versioned API JSON files; no network credentials or trades. Record JSON envelope plus received timestamp and SHA-256 with integrity verification. Inputs from a user-supplied normalized reference format are clearly distinguished from native CoinGlass API payloads.
- [x] Add failing tests for immutable capture hashes, tampering, missing features, time/market mismatch, empty data and invalid numeric values.
- [x] Implement bounded snapshot recording, deterministic replay/inspection, feature exports, and explicit normalized reference comparison (agreement, not correctness). Record both data and observation time; archive unknown availability remains unknown.
- [x] Add runnable held-out evaluation scaffold comparing declared features with baseline/outcome inputs; prevent train/test overlap and explain event-feed/strategy limitations. Never manufacture PnL/accuracy.
- [x] Provide offline synthetic fixtures/examples explicitly labeled as interface tests, and an example strategy-evaluation input contract with costs/slippage requirements.
- [x] Run `python -m pytest tests/test_research.py -q` and offline examples; write clear docs separating engineering tests, empirical validation, and operating evidence.

### Task 5: Packaging, brand and release evidence
**Files:** `pyproject.toml`, `liqmap/__init__.py`, packaging/static assets as needed, `.github/workflows/ci.yml`, `README.md`, `docs/HOW_IT_WORKS.md`, `CHANGELOG.md`, `docs/MIGRATION.md`, `docs/RELEASE_REVIEW.md`, `docs/assets/*`, performance script/tests as needed.
**Interfaces:** Package `open-liquidation-heatmap` version 0.3.0, entry point `liqmap = liqmap.server:main`; include web assets in wheel and verify outside source tree. Logo original, image-generation tool, visually inspected.
- [x] Add packaging/install smoke check and version consistency; implement package metadata without adding a runtime framework.
- [x] Generate logo, inspect and integrate it; create meaningful README links/badges that correspond to actual version/license/workflows.
- [x] Produce reproducible performance measurements and graphs with source JSON; no fictional uptime or predictive charts. Capture final desktop/mobile screenshots.
- [x] Rewrite stale methodology/README after implementation stabilizes; document model migration and unsupported assumptions.
- [x] Add CI for offline tests, wheel install, JS syntax and browser smoke; network tests remain separately labeled.
- [x] Independently review full diff and every score; fix critical findings; run final tests, install and UI checks; preserve remaining empirical gates.
- [x] Commit reviewable product and report actual results without claiming 10/10 absent evidence.

## Execution rulings
- User approved design and explicitly said proceed with implementation; no second plan approval request.
- Parallel tasks have exclusive files. Task 1/2 interface updates are coordinated in messages; root integration resolves shared metadata.
- Complete engineering changes now. External empirical/uptime gates cannot be manufactured in a coding session and remain explicitly unverified.

## Completion evidence

Implemented locally as application 0.3.0, API 1.0, model 2.0.0. Final corrected Python suite:318 passed,2 skipped; extended Chromium acceptance and installed-wheel check passed. Independent whole-branch review and its correction wave/scoped re-review are complete. A later original-checkout run revealed a vacuous worktree scanner; an independently reviewed test-only addendum restored actual scanning without expanding the allowlist. The [release review](../../RELEASE_REVIEW.md) records every category and open external evidence gate; the [review record](../../reviews/2026-10-28-final-review.md) preserves concrete findings and their resolution. Hosted CI, real strategy evidence and sustained operations are not marked complete.
