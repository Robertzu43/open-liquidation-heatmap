# Open Liq 0.3.0 — release review

The approved in-place upgrade is implemented and independently reviewed as a local research release candidate. Application version: **0.3.0**; API schema: **1.0**; model: **2.0.0**. The baseline was commit `8668994` (192 passing tests, two skips). This review records engineering evidence separately from external model/strategy evidence.

## What changed

- Native OI, exact causal cohorts, correct close-entry ordering, independent venue inventories, invalid-source rejection, masks and conservative availability semantics.
- Exact features including signed nearest distances, imbalance, concentration, persistence and modeled depletion; independently selected visible tail over full loaded history.
- Versioned API and full-source semantic fingerprints, local snapshot recorder/export/comparison, chronological held-out diagnostics and explicit units.
- Bounded HTTP workers/computation/cache/replay work, cooperative deadlines, source freshness separate from process health, user-writable archive cache and bounded response/decompression sizes.
- Responsive chart-first interface, named scenarios, UTC/local display, keyboard/touch controls, shared settings, JSON/PNG export and race-safe replay.
- Original logo, installable package, API specification, reproducible benchmark, updated methodology/migration/operations guides and offline CI workflow.

## Verification record

Final integration verification after the scanner correction: **318 tests passed, two skipped** in 21.68 seconds; JavaScript syntax, compilation and diff whitespace checks passed. The initial whole-branch review and its focused correction review are recorded in the [review record](reviews/2026-10-28-final-review.md). The two skips are an opt-in live historical-network test and a redundant interface-gap sentinel. A skipped historical network test is not a successful archive backfill test.

- Installed-wheel smoke succeeded outside the checkout: console command, health, packaged HTML/JS/logo and synthetic API. A separate clean virtual environment also resolved the pinned dependencies from the package index and passed the installed command/assets/API check on Python 3.12.14; this does not establish every supported platform.
- Extended Chromium browser smoke succeeded at 360, 390, 768, 1280, 1440 and 1920px widths, plus a narrow zoom-equivalent layout. It checks keyboard/touch, exports, fullscreen, error recovery, shared Auto bars, replay cancellation/context and time-zoom limits.
- Final default phone chart top measured **283px**, versus approximately **960px** in the previous demo review. The chart is visible in the first 390x844 viewport; no horizontal overflow was observed.
- A live OKX BTC/24h smoke returned native base-OI accounting with 288/288 observed candle/OI/flow rows and a successful readiness response, in approximately 1.75 seconds. After the final chronology correction, separate three-bar 12-hour and daily adapter spot checks returned UTC-aligned opens and complete candle/OI/flow masks in approximately 0.793 and 0.602 seconds. These support endpoint compatibility, not an SLA or long-history completeness result.
- Benchmark chart and raw samples are in [assets/benchmark.json](assets/benchmark.json). Nine model/range cases each have seven cached-source repetitions and exclude exchange/HTTP latency. A separate fresh local server records a cold request, five warm repeats, an eight-client burst, status/error counts, transfer sizes and Linux process memory. Hardware, workload, predeclared local budgets and child cleanup are recorded. These are small synthetic workloads, not model-accuracy or maximum-load evidence.
- OpenAPI response schema was checked against an actual synthetic feature envelope. CI is configured in source; remote CI execution is a separate event.

## Recorded local workload

Final benchmark source revision: `96f28b7`; working tree clean when measured. This is demo BTC/12h/model 1 over loopback, with startup measured separately and no exchange access.

| Measurement | Recorded result |
|---|---:|
| Fresh-server first request | 99.4 ms |
| Five warm requests, median / maximum | 85.9 / 94.5 ms |
| Eight-client burst | 5 HTTP 200; 3 HTTP 429 `busy` |
| Observed server RSS / reported high-water mark | 50.9 / 53.0 MiB |
| Model/payload chart samples | 9 cases × 7 runs = 63 |

All predeclared small-workload checks passed: successful warmed requests below five seconds and observed server memory below 512 MiB. Burst admission depends on scheduling, so counts can vary between runs. One burst and four memory observations are not sustained-load or maximum-memory certification. The raw file contains hardware, samples, status-specific latency, byte counts and successful child-process cleanup.

## Independent review and fixes

A core reviewer checked causal accounting, data quality and research contracts. An entirely missing native OI source initially produced numeric zero; it now fails explicitly. Exact distance features and independent visible/history selection were added. Research comparison remains strict user-normalized agreement, without a native provider-equivalence claim.

A separate interface reviewer reproduced four behavioral defects despite the original smoke passing: a late old-model replay response, a pending replay load surviving Live, shared Auto links inheriting saved custom bars, and a time-zoom minimum that was not applied. All four received deterministic regressions and fixes. Extended tests also cover rapid frame selection and late failed span responses.

Runtime review added bounded connection workers, cooperative source/cache waits, stricter live freshness, receipt-independent semantic fingerprints, user cache location and transfer/decompression caps.

The final whole-branch reviewer found two additional defects: UTC+8 OKX long candles entering earlier UTC bars, and pending selections changing exported PNG provenance before the displayed data changed. The independent scoped re-review accepted both fixes with no remaining material scoped concern. Both were corrected in one wave: candles/OI/taker now use supported UTC periods and closed-interval validation, and PNG/JSON exports identify the displayed payload. New browser cases hold model, market and replay responses and exercise failed selections. The benchmark now supplies the previously missing cold/warm/concurrent HTTP and observed-memory evidence with explicit small-workload limits.

An additional verification run in the original checkout exposed a test-scanner defect: its absolute-path exclusions skipped the entire nested worktree, hiding unapproved URLs in mocked fixtures. The scanner now filters paths relative to the repository, with a regression for ignored ancestors and nested directories. Fixtures use the existing allowed loopback host; the allowlist is unchanged. Independent review confirmed real source scanning and rejection of an injected unapproved host. Earlier worktree passes did not establish scanner coverage; the final result above includes the repaired checks. Production code and the recorded benchmark workload were unchanged by this test-only repair.

## Every category reassessed

Scores are subjective engineering/product judgments, not measurements of predictive accuracy. They remain below ten where evidence or capability is incomplete.

| Category | Before | Current assessment | Evidence / remaining gate |
|---|---:|---:|---|
| Overall product quality | 6 | 8 | Integrated research release candidate; prospective operations still untested |
| Relevance to algorithmic traders | 8 | 9 | Exact features, versions, captures, comparison and evaluation; needs strategy-user feedback |
| Beginner approachability | 5 | 7.5 | Named scenarios and plain estimate explanation; no representative-user comprehension study |
| Usability | 6 | 8 | Installable app, saved/shared views, export/recovery; no measured task-completion study |
| Desktop UI | 7.5 | 8.5 | Visually inspected responsive hierarchy and chart; broader human testing remains |
| UX / onboarding | 5.5 | 8 | Progressive controls, UTC/local, quality and model explanation; onboarding comprehension unmeasured |
| Mobile | 4 | 8 | First-screen chart, usable control sizing and emulated touch; physical devices and other engines untested |
| API / research utility | 7 | 8.5 | Versioned contracts, exact features, reproducibility and examples; only documented markets/providers |
| Model correctness / data integrity | 4 | 7.5 | Independent causal regressions and source handling; priors, mark-price approximation and unobserved inventory remain |
| Testing / validation | 6 | 8.5 | Regression, API, browser, package and resource gates; actual liquidation/prediction evidence absent |
| Documentation / transparency | 7 | 8.5 | Methodology matches code, contracts and limits; verify external docs as providers change |
| Production readiness | 4 | 7.5 | Bounded local runtime and operational guide; no sustained deployment, disk-retention automation or uptime evidence |
| Free/customizable value | 8 | 9 | Keyless public sources, MIT code, local recorder; operator maintenance costs persist |
| Paid CoinGlass API replacement | 3 | 5 | Useful alternative research input; no authorized matched dataset or strategy-equivalence validation |
| Overall software engineering | 7 | 8.5 | Causal state separation, explicit contracts, protected regression paths; long-term maintenance evidence absent |
| Demonstrated trading edge | Not established | Not established | Requires real held-out and prospective evidence after costs |

**Overall judgment: approximately 8/10; engineering 8.5/10.** Assigning ten to every row would conceal the missing evidence. Passing implementation tests cannot establish unknowable account positions or profitable strategy behavior.

## Remaining external gates

1. Representative users: novice interpretation and technical-trader task completion. Browser automation is not a user study.
2. Authorized provider reference exports, aligned instruments/times/features and frozen model parameters; agreement is separate from accuracy.
3. Actual exchange outcomes with feed completeness documented; held-out baselines and costs/slippage-aware strategy evaluation.
4. Prospective shadow recording in the intended deployment geography, with failed polls and gaps counted. Proposed evaluation includes at least 30 days and more than one historical volatility regime; time alone does not prove statistical sufficiency.
5. Physical-device and Safari/Firefox accessibility/interaction testing, and observed production maintenance/uptime.

No order execution or paid provider access was used. Historical archive availability remains unknown unless captured evidence supports it. These are release-evidence boundaries, not problems that can honestly be solved by adding a badge.
