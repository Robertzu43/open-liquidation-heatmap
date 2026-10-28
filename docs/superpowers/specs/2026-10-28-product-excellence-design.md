# Open Liquidation Heatmap: product excellence design

Status: PROPOSED — awaiting user choice/approval; no product implementation has started.

Baseline: commit `8668994`, version `0.2.0`, 192 tests passed / 2 skipped in the prior review. Audience: an algorithmic trader currently paying for the CoinGlass API, plus desktop and mobile users interpreting liquidation scenarios. Authoring date: 2026-10-28.

## Intent and honest definition of excellence

Improve every previously scored category, without declaring estimated liquidation positions to be measured exchange positions. A 10/10 is an aspiration, not an automatic release label. Engineering acceptance can be demonstrated in this workspace; operational reliability, strategy value, and substitution for a paid provider need external data and observation. Missing evidence remains missing in the final reassessment. No fabricated accuracy, return, uptime, coverage or benchmark claims.

The user requested: install/use obra Superpowers and Ponytail; cover every review category; plan and implement the improvements; independently reassess; create a logo; finalize README with version, badges, buttons and graphs. This document makes that scope reviewable before product implementation.

## Approaches

1. **Recommended: improve the existing Python + HTTP API + Canvas app in place.** Keep public loaders and existing functionality where sound; replace faulty accounting and state handling; add API contracts, validation and a simpler UI. Lowest migration cost and preserves local-first value.
2. **Framework rewrite.** Could help future team conventions but introduces migration and deployment work without solving the estimation defects. Not justified by current code size.
3. **Validation-only patch release.** Corrects critical model defects quickly but does not satisfy the requested UI, mobile, onboarding, branding and delivery scope.

The recommended option is a builder proposal, not yet a user-approved decision. Preserve the current stack unless the user chooses otherwise.

## Every score: what would justify excellence?

All scores below are the previous subjective review, not measured model performance. Overall/product scores summarize these gates and are not extra features.

| Category | Baseline | Excellence requirements | Required evidence |
|---|---:|---|---|
| Overall product quality | 6 | Reliable, coherent end-to-end workflows; no unresolved critical correctness defects; explicit limitations | Release checklist linking all gates; independent final review |
| Relevance / relatability for algorithmic traders | 8 | Clear workflows from dataset to features to evaluation; direct control of assumptions; precise units | Runnable feature and strategy-evaluation examples; reviewed field definitions |
| Beginner approachability (earlier review) | 5 | Explain above/below bands, estimates, model choices and freshness without requiring README study | First-use walkthrough and task-based review; actual user testing distinguished from agent review |
| Usability | 6 | Simple install; useful default; recoverable failures; saved and shareable settings | Fresh environment install; live/demo/error/recovery scenarios |
| Desktop UI | 7.5 | Chart dominates; deliberate hierarchy; consistent typography and palette; legible axes and focus states | Screenshots and browser checks at 1280/1440/1920 widths, zoom and fullscreen |
| UX / onboarding | 5.5 | Plain model names; basic vs advanced controls; unavailable features explained only in relevant context; UTC/local timezone choice | Keyboard-only happy path; novice interpretation checklist; no misleading dollar precision |
| Mobile UX | 4 | Chart visible in first viewport; controls collapsed; usable touch pan/zoom; readable summaries | 390x844 and 360x800 browser checks, tablet check, no horizontal overflow; real-device caveat documented |
| API and research utility | 7 | Versioned documented schemas, deterministic features, explicit units/status, simple Python client/examples, export | Contract tests; HTTP integration tests; executable examples; migration guide |
| Model correctness / data integrity | 4 | Native OI unit accounting, causal ordering, stable state, correct gap treatment, no fake venue flows, honest assumptions | Independent expected-value regressions, conservation and prefix-causality properties, parser fixtures |
| Testing / validation | 6 | Critical production paths exercised, not merely math helpers; offline reproducibility; explicit external validation | CI commands, coverage of named risks, browser tests, walk-forward validation artifacts |
| Documentation / transparency | 7 | README, methodology, API and actual implementation agree; reproducible results and prominent limitations | Link checks, fresh-clone instructions, field/model contract review |
| Production readiness | 4 | Bounded resource use, clear readiness/health, durable data provenance, deployment instructions, failure observability | Fault injection, concurrent request tests, resource/load benchmark; real uptime remains unproven until observed |
| Free/customizable value | 8 | No API key needed for public-data path; offline demo; inspectable model; reproducible self-hosting with operating-cost explanation | Clean install and offline run; license/dependency review; documented maintenance requirements |
| Paid CoinGlass API replacement | 3 | Explicit scope and schema mapping, comparable coverage/freshness, viable shadow run, successful strategy-specific out-of-sample evaluation | User-authorized CoinGlass export/API data and baseline strategy results; no full replacement claim without evidence |
| Overall software engineering | 7 | Minimal cohesive modules, clear contracts, regression protection, maintainable deployment and versioned behavior | Independent code review, passing gates, documented design decisions and known limitations |
| Demonstrated trading edge (previously unscored) | Not established | Robust incremental usefulness over price/OI baselines after costs; no guarantee of profitability | Held-out event/strategy evaluation, predeclared metrics, uncertainty and sample limitations |

Equal-weight cosmetic improvements must not outweigh financial-data errors in the final score. Report every category again, each with evidence and remaining gaps.

## Data and model design

### Preserve native units and provenance

- Keep native base-asset OI from Binance metrics/live fields and OKX data, with venue-specific unit conversions documented and fixture-tested. Do not infer native OI merely by dividing a dollar value by an unrelated candle close.
- Track event timestamp, bar interval/end, observation/receipt timestamp when collected, source, symbol, native unit, valuation price/source, freshness, fill status and coverage. Archives cannot retroactively prove when a value was first publicly available: identify them as reconstructed history.
- Positive flow is change in native OI valued at an explicit entry/valuation price; price changes alone do not create contracts. Define whether displayed values represent entry notional or current marked exposure and use that definition everywhere.
- Zero, negative, non-finite, missing and stale OI are explicit states. Do not reset a live book or create recovery cohorts merely because an invalid observation is followed by a valid one. Exclude or rebaseline unobserved intervals and report the excluded information.
- Preserve observed-vs-filled candle/OI masks; gap filling must not silently manufacture observations.
- Do not present long/short account ratios as taker-flow measurements. Model 3 must record the actual available prior, or explicitly use a neutral fallback. Matched counterparties and assumed vulnerable-side allocation must be explained.

### Causal model state

- Keep state in exact liquidation levels/cohorts or another sparse causal representation independent of the chart's final-window price bins. The builder may choose the simplest bounded representation that meets prefix invariance and performance gates.
- At each closed bar: apply valid price-trigger sweeps to already-existing cohorts, handle validated OI reductions and elapsed-time decay, then create new cohorts at the specified close entry. Use one documented post-update state for heat columns, profiles, features and exports.
- Future bars must not alter past logical inventory or features. Rendering can rebin an unchanged inventory for display; display resolution cannot change liquidation decisions.
- Use mark-price bars for sweep triggers where supported and sourced. If only trade-price OHLC is available, flag that approximation rather than claiming exchange-engine parity.
- Define decay by elapsed time, not an unexplained constant per bar. Publish model version, assumptions and parameter digest.
- Keep fixed leverage priors as transparent scenarios. Validate and label isolated margin/MMR assumptions; advanced margin calculations do not imply account-level position knowledge.
- Track venue-local model state and quality before aggregation. Venue membership changes cannot be treated as position changes. Report contributing venues and coverage changes per time step.
- Separate visible chart range from model warm-up/history anchor. Publish anchor and insufficient-history flags. A changed range may change the information available; disclose this rather than pretending inventory is known before observation begins.

### Minimum regression cases

1. Constant native contracts + changing price generates no new contracts.
2. New positions opened at candle close survive that candle's earlier wick.
3. A later valid trigger removes the correct existing side and amount.
4. Missing/returning venue does not create aggregate opening/closing flow.
5. Zero/NaN/missing OI does not silently erase and recreate inventory.
6. Appending an extreme future candle leaves prior logical state/features unchanged.
7. Heat/profile/feature totals agree under a single documented timestamp convention.
8. Different bar durations have equivalent time-decay behavior under controlled fixtures.
9. Replay frame and independent as-of query agree for the same versioned data snapshot.
10. Model calculations never include data whose availability boundary is after the requested evaluation point when that boundary is known.

## API and algorithmic workflows

- Introduce a versioned v1 research contract alongside the existing UI routes, or version existing envelopes explicitly. Keep legacy routes as documented compatibility wrappers where feasible; do not silently change the numerical meaning under an unchanged model version.
- Return schema version, model version, data revision/fingerprint, timestamps, units, quality status, assumptions, venue coverage and warnings with each result.
- Provide structured feature output (price distance, concentration, imbalance, persistence/depletion under defined semantics), rather than forcing every client to decode canvas arrays. Missing/insufficient information is null with a reason, never a zero opportunity.
- Document binary grid layout, endianness, dimensions and decoding; offer plain JSON/tabular export for small features and examples using standard Python tooling.
- Publish API schema/spec, stable error codes and examples for demo, live, as-of, replay and stale/unavailable data. Validate the documentation with contract tests.
- Keep historical research claims distinct: reconstructed archive replay versus captured point-in-time snapshots. Add a local snapshot recorder with content hashes/metadata and a replay interface that consumes recorded fixtures without network calls.
- Add a user-supplied CoinGlass comparison importer/adapter; preserve source timestamps and units. Do not scrape, assume credentials, or promise byte-for-byte numerical equivalence.

## UX, accessibility and branding

Keep the familiar dark financial-chart aesthetic with accessible contrast, but prioritize the chart and data quality.

- Header: Open Liq brand, asset/venue, current data status. Primary controls: asset, venue/aggregate, range and refresh. Everything else moves into clearly labeled Model, Display and Research panels.
- Model options expose their scenario names and compact differences, rather than only Model 1/2/3.
- Historical/replay controls appear in their supported context, with a concise capability explanation.
- Replace ambiguous liquidity/order-book wording with estimated liquidation exposure. Tooltips explain how the estimate is made and how it should not be interpreted. Display precision matches model uncertainty.
- UTC default plus selectable local timezone; clarify event time versus generated time. Never claim freshness merely because the response was generated now.
- Mobile: chart begins within the first 844px viewport at 390px width (target canvas top <= 300px in default compact layout); meaningful plot area, native page scroll, explicit touch gesture affordances and zoom/reset buttons. Collapse secondary tables and advanced controls.
- Keyboard-accessible controls, visible focus, appropriate labels/status announcements, equivalent tabular data, and chart interactions that are not pointer-only. Check WCAG AA text contrast where applicable and at 200% zoom.
- New original Open Liq logo: abstract layered liquidation bands with a price trace, legible at favicon and README sizes; no CoinGlass imitation. Deliver transparent PNG and practical small-size variants; use SVG only if faithfully authored/verified as appropriate. Generate with the available image tool, inspect the result, then integrate.
- Retain PNG export, include model/data timestamps and estimation provenance in exported images.

## Runtime and engineering scope

- Preserve Python and lightweight frontend unless measurement shows a concrete reason to change. No database, queue, frontend framework or plugin architecture merely to chase a score.
- Add installable package metadata and command entry point, documented supported Python versions, development dependencies and a clean reproducible install path.
- Local loopback binding by default. Document reverse-proxy/TLS/auth requirements for exposed deployments. Do not deploy publicly or provision paid services as part of local verification.
- Bound request ranges, archive work, concurrent expensive computation, cache/span memory and lock lifetime. Define retry/backoff and usable overload/error behavior. Never silently return synthetic data as live.
- Health/readiness describes service and data states separately; logs include actionable failure context without secrets. A healthy process is not proof of fresh markets.
- Add CI for tests, static checks appropriate to the existing language, packaging/install smoke tests, and deterministic browser smoke tests against demo fixtures. Pin or constrain dependencies deliberately.
- Benchmark declared hardware/workload, warmed versus cold requests, concurrency, payload size and memory. Establish budgets before final assessment from representative measured baseline; do not claim general latency from a single local request.
- No changes to live trading accounts, order execution, paid subscriptions or external credentials. No auto-publication of release tags or deployment.

## Validation and release evidence

### Engineering gate (implementable here)

Named regressions, prefix invariance, adapter fixtures, HTTP contracts, offline replay tests, error/recovery and responsive keyboard/touch browser checks, clean-install smoke test, bounded-work benchmark and independent code review. Fix failures and rerun only affected/broader necessary checks. Evidence records commands, commit, data source and limitations.

### Empirical gate (requires real evidence)

Provide a runnable evaluation harness with train/calibration periods separate from held-out periods; compare against simple price/OI baselines; distinguish event detection, shelf visitation, timing, and PnL. Touching a predicted level alone does not establish accurate liquidation size or profitable alpha. Report exchange event-feed incompleteness, sample sizes, fees, slippage, turnover, drawdown and uncertainty. CoinGlass agreement is a separate diagnostic, not truth.

If authorized data are absent, ship clearly labeled synthetic tests/example fixtures and an input contract. Mark empirical conclusions and provider replacement score as unverified. Do not call a synthetic benchmark evidence of trading value.

### Operating gate (requires time and environment)

Define a configurable shadow-run recorder/evaluator with no order placement. A meaningful acceptance run needs the user's market universe, strategy thresholds, deployment geography and fee/slippage assumptions. Proposed default evaluation is at least 30 consecutive days plus historical held-out periods spanning more than one volatility regime; duration alone is not proof of statistical sufficiency. Gaps and outages must be counted. These choices need user confirmation before claiming replacement readiness, but do not block building the recorder and offline evaluation tools.

## Proposed work sequence and dependencies

1. Verify/install skills and record versions; select approach and approve design.
2. Create isolated development branch/worktree, preserve baseline and current test evidence.
3. Write failing regressions for model/data defects; implement unit/provenance-preserving loaders and causal model state. Review this before UI polish.
4. Add versioned API/feature contracts, quality metadata, snapshot/replay parity and migration examples on the corrected engine.
5. Improve bounded runtime behavior, packaging, CI and deployment documentation. Parallelize independent runtime and UI work after contracts settle.
6. Redesign desktop/mobile workflows and accessibility; verify live/demo/failed-source states against the same API.
7. Build reproducible empirical/comparison and performance harnesses; publish actual measured engineering results and clearly separate missing external validation.
8. Create/inspect logo, integrate brand assets, capture final screenshots, then rewrite README and methodology against the finished behavior.
9. Independent code, usability and evidence review; fix material findings; run release gates; reassess every score without mandatory rounding to ten.
10. Deliver reviewable code/commits and final evidence. Remote PR/publication only within confirmed repository permissions and user-authorized scope; do not claim a release tag exists before it does.

## Final README and release package

Proposed engineering milestone: `0.3.0` (existing version is `0.2.0`), not a premature 1.0 production claim. Numeric model changes get a separate model version and migration note. Final version may be adjusted based on actual compatibility changes.

README includes: original logo, concise audience/value statement, verified version and license badges, CI badge only for a workflow that exists, linked Quickstart/API/Methodology/Validation/Examples buttons, actual desktop/mobile screenshots, compact architecture diagram, measured performance/validation graphs with source commands, feature/coverage table, data limitations, upgrade instructions and self-hosting costs. Never add a 10/10, accuracy or uptime badge without an evidentiary basis. `.gitignore` currently excludes all PNGs; use narrow asset exceptions when final assets are added.

Supporting deliverables: API reference/schema, methodology matching code, version/changelog, migration guide, Python examples, validation scripts/fixtures/reports, operational guide, model/data-quality definitions, final scorecard and outstanding empirical gates.

## Decisions delegated to the builder

Choose simple internal representation, sensible module boundaries, accessible spacing/type scale, fixture organization and narrowly necessary dependencies. Preserve existing behavior when compatible with corrected semantics. No user-specific trading strategy or risk tolerance is invented.

## Decisions/evidence still needed

- Approval of this in-place design (or choice of rewrite).
- Actual CoinGlass dataset/API access and strategy evaluation inputs, only if the user wants empirically demonstrated replacement readiness. Do not ask for secrets in chat; use configured credentials or supplied files through supported mechanisms.
- A real operating window is required for uptime/shadow-run evidence. A coding session cannot stand in for it.

Completion status at design stage: none of the product acceptance gates is claimed satisfied by this document. Logo, implementation and final README remain pending.

## Preparation evidence

- Official `obra/superpowers` v7.0.1, source commit `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b`: installed and enabled in native Codex as `superpowers@superpowers-marketplace`; existing cloud skill also invoked. Fifteen installed skill bodies and Codex manifest match source. Codex manifest has no hooks.
- Official `DietrichGebert/ponytail` v5.3.2, source commit `09c434ab73ca43761c5d8b0c5d341926c2fba710`: installed and enabled as `ponytail@ponytail`. Six installed skills and eight hook files match inspected source; seven focused upstream checks passed. Full-mode skill read and explicitly applied in this conversation. Automatic hooks in a future native Codex thread require the upstream trust/startup workflow and have not been claimed active here.
- Both installations preserve pre-existing Codex configuration. They are installed in this cloud environment, not on the user's personal computer. Plugin listing is installation evidence, not evidence of automatic hook execution.
- Independent builder check of this draft returned: “nothing important left.” This is a design review, not a product acceptance result.
- Design approval requested; implementation, logo generation and finalized README remain pending. The current product score has not changed.
