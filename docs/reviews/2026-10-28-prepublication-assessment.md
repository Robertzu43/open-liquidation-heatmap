# Pre-publication assessment — direct review

Reviewed private `main` at `de5f0a12cd45242b0caf9d966c258e1f9629c1a9` (application 0.3.1) on October 28, 2026. This is a **direct review by the implementation assistant**, not the requested fresh independent agent or the subsequent judge review. A separate Codex session was attempted, but cloud authentication returned HTTP 401. The repository remained private. The findings below are evidence for those reviews, not a replacement for them.

## Priority 1: live archive fallback and aggregation can misrepresent freshness

During a real public-data spot check of BTC/USDT 24h, Binance's live endpoint was unavailable and the API used `binance-vision`. The last 5-minute bar close was approximately **844.6 minutes old**, but `/api/v1/features?exchange=binance&symbol=BTC&range=24h&model=1` reported `quality.status=observed`, `quality.stale=false`, and no warnings. This was a live request, not an `asof` historical request.

At the same time the aggregate response listed both OKX and Binance, reported `quality.status=observed`, `stale=false`, and no warnings, even though its `model_metadata.venue_coverage` contained Binance in only **120 of 288** bars; the latest bar contained only OKX. `excluded_venue_observations` was 168. The aggregate reported modeled exposure above the OKX-only estimate while the Binance component lacked recent observations. These figures are one time-specific observation, not a general market measurement.

Code evidence:

- [`_quality`](../../liqmap/service.py#L290) applies the two-bar freshness check to live OKX, Binance-live, and aggregate, but explicitly excludes reconstructed archives. Its aggregate status derives from combined masks, which can remain complete when just one venue supplies each bar.
- [`fetch_binance_vision`](../../liqmap/data.py#L1207) and [`fetch_aggregate`](../../liqmap/data.py#L1452) rely on the older **18-hour** loader stale threshold when deciding whether the archive is acceptable for aggregation. An aggregate can therefore include a delayed venue while its latest bar is current thanks to another venue.

**Impact:** an automated consumer that treats `observed` and `stale=false` as a live signal can act on old Binance data or an incomplete aggregate without a prominent warning. Per-venue coverage exists in metadata, but it does not make the root quality state safe to interpret as complete and current.

**Proposed fix for the final iron-out:** apply the live freshness budget to archive fallback when no historical cutoff is requested; check each aggregate venue's latest observed bar separately; exclude stale components and report partial venue coverage as degraded with an explicit warning. Re-evaluate freshness on cached aggregate responses as time passes. Preserve the separate historical/as-of semantics, and add regressions covering fresh/stale boundaries, mixed-venue gaps, cache aging, and archive history.

## Priority 2: the agent prompt can reuse an older app

The previous README prompt said to reuse an existing Open Liq instance if port 8765 was occupied, without comparing versions. In this cloud workspace, `/api/v1/status` on that port reported **0.3.0**, while the reviewed checkout was **0.3.1**. An agent following the original instructions could therefore open an older application after installing the current checkout. This does not prove that the original prompt was actually run by an independent agent on a user's Mac.

The draft README prompt in this branch now tells agents to compare the running application's reported version with the checkout, use another local port if they differ, avoid stopping someone else's process, and report the checkout revision. A version match alone does not prove the running process uses the exact same commit; a fresh-agent acceptance test is still needed.

## Evidence boundaries and remaining work

1. **Trading accuracy and replacement readiness are unproven.** The engine estimates exposure from public OI, trade-price candles and assumed leverage; account entries, collateral, mark-price liquidation triggers, and pre-anchor inventory are unknown. Historical first-public availability is generally unknown. Run frozen, point-in-time held-out tests against a simple baseline and authorized references, including fees and slippage, before claiming strategy value or CoinGlass equivalence.
2. **Replay is useful but can revise history.** The UI explicitly says earlier modeled exposure can change as the rolling lookback moves ([implementation](../../web/app.js#L503)). For backtests or visual claims about what was knowable, preserve captured point-in-time data and distinguish it from reconstructed history.
3. **Health/readiness is not universal market readiness.** The readiness probe reports the cached default OKX/BTC/24h market and does not poll the exchange. Consumers need market-specific source-age, coverage, and missingness checks. Long cold history requests may hit the cooperative 60-second budget; disk retention remains operator-managed.
4. **Public-release usability needs human and cross-browser testing.** The desktop and mobile demo were visually inspected and automated in Chromium, including keyboard/touch, exports and replay. That is not a user study, physical-device test, or Safari/Firefox certification. The prior [release scorecard](../RELEASE_REVIEW.md) documents 0.3.0 and should not be presented as a fresh independent 0.3.1 assessment.
5. **Independent and judge reviews remain outstanding.** Supply a fresh reviewer the target commit and task without this assessment first; then have a separate judge verify both sets of findings and the fixes. Do not label this review independent.

## Checks performed

- `python -m pytest -q -ra`: **330 passed, 2 skipped**. Skips: opt-in historical network and redundant validation sentinel.
- `python scripts/check_install.py`: installed console command, packaged assets, health and demo API passed outside the checkout with inherited dependencies.
- `python tests/browser/smoke.py --url http://127.0.0.1:8790`: passed; Chromium desktop/mobile viewport and interaction checks. Synthetic demo/replay fixtures, not a cross-browser or user study.
- Local HTTP: health 200; readiness initially 503 (no cached default live market), then 200 after a successful default OKX load. Versioned status, symbols, features and heatmap responded. Demo was labeled synthetic. OKX live features returned 200 and `observed`; Binance fallback and aggregate exposed the freshness issue above.
- Repository visibility checked through GitHub: **private**. No visibility change, paid-provider access, trades, or public release was performed.

The above live spot checks used the cloud environment's geography and connectivity. They do not establish how all exchanges behave on the user's Mac or guarantee latency or availability.
