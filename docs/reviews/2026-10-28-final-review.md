# Final independent whole-branch review

**Final disposition:** both findings were corrected and passed the independent scoped re-review below. The 0.3.0 local research milestone has no remaining material finding from these reviews; external validation remains open.

Reviewed `42c35d7..42a0a24` on 2026-10-28 against the approved product-excellence design, release review, supplied diff, prior core/interface reviews, and actual source. No product files changed, commits created, or subagents used. Coordinator-only benchmark asset regeneration does not affect the findings.

**Initial readiness verdict: changes requested (before correction wave).** Two reproducible correctness defects remain, one affecting source chronology and one exported provenance. Fix and add focused regressions before calling this implementation candidate ready. No further confirmed blocker was found in the inspected causal engine, API/runtime, research, packaging or CI paths. The disclosed external evidence gates are appropriate; passing these fixes would not establish trading edge or provider equivalence.

## P1 — Long-range OKX candles are assigned an earlier UTC close, including an unclosed candle

**Locations:** `liqmap/data.py:60`, `743-751`, `818-826`; grid construction at `687-692` and folding at `409-438`.

`OKX_BAR` selects `12H` and `1D`, which are OKX's UTC+8 candle periods, while `_window` builds UTC-aligned 12-hour/daily intervals. `fold_candles` assigns the entire source OHLC to the grid interval containing its open timestamp. It does not preserve the actual source close. `fetch_okx` also ignores the returned `confirm` field. Consequently a daily candle opened at 16:00 UTC is labeled as the 00:00 UTC grid candle and its values are attributed to a close **16 hours before** its real close. The analogous 12-hour offset is four hours. This affects advertised 180d/1y/2y OKX views and their aggregate contribution.

At 2026-10-28 01:00 UTC, an unconfirmed daily candle opened on Oct 27 at 16:00 is accepted as observed data for a feature state supposedly closed on Oct 28 at 00:00. It actually closes on Oct 28 at 16:00. Historical columns also consume prices after their represented close. This invalidates post-close timestamps, sweep chronology and the claim that future input does not affect earlier state; it is not merely unknown archive publication time.

**Executed deterministic reproduction** (no exchange access):

```python
from unittest.mock import patch
from datetime import datetime, timezone
import numpy as np
from liqmap import data, service
base = int(datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp() * 1000)
grid = base + np.arange(3) * 86400000
candles = [[str(t + 16*3600000), '100', '101', '99', '100',
            '1', '1', '1000', '1' if i < 2 else '0']
           for i, t in enumerate(grid)]
oi = [[str(t), '100', str(10+i), str((10+i)*100)]
      for i, t in enumerate(grid)]
def pages(path, *args):
    if path.endswith('candles'): return candles
    if path.endswith('open-interest-history'): return oi
    return []
with patch.object(data, '_window', return_value=grid), \
     patch.object(data, '_okx_pages', side_effect=pages), \
     patch.object(data.time, 'time', return_value=(grid[-1]+90000000)/1000):
    loaded = data.fetch_okx('BTC', 'BTCUSDT', {
        'key': 'fixture', 'bar': '1d', 'bar_seconds': 86400, 'seconds': 259200})
    print(loaded.series.candle_observed.tolist())  # [True, True, True]
    print(service._quality(loaded)['status'])     # observed
    print(int(loaded.series.times[-1] + 86400000)) # Oct 28 00:00 UTC
    print(int(candles[-1][0]) + 86400000)          # Oct 28 16:00 UTC
```

**Expected correction:** request UTC-aligned candle periods (distinguish candle parameters from supported OI/taker period parameters), reject unconfirmed candles and ensure a source candle's actual duration/close fits its represented grid interval. Add 12H/daily adapter regressions exercising real timezone offsets and the current forming candle. Verify OI/taker alignment too; changing one shared period variable blindly could cause a different provider contract error.

## P2 — PNG export during loading labels old data with the newly selected scenario

**Locations:** `web/app.js:1118`, `1141`; pending-load behavior at `363-368`, `402-417`, `1386-1403`.

The UI retains the prior dataset/canvas while a new model or market loads, which is reasonable. However, `provenanceLines()` takes its scenario name from mutable `state.model`, while the image and data fingerprint still belong to `state.data`. `snapshot()` also names the file from mutable selected exchange/coin/range/model. Export remains available while loading. The resulting durable PNG can therefore assert the wrong model or market even though the eventual response race is fixed.

**Executed Chromium reproduction:** load `/?exchange=demo&model=1&range=24h&mode=pair`, wait for `state.data.model.id === 1`, hold subsequent `/api/heatmap` requests with a Playwright route, then click `.tab[data-model="2"]`. Evaluate:

```javascript
({selected: state.model, displayed: state.data.model.id,
  loading: !document.getElementById('loading').hidden,
  exported: provenanceLines()[1]})
```

Observed:

```json
{"selected":2,"displayed":1,"loading":true,"exported":"Open Liq · Tight magnets scenario · estimated USD exposure, not exchange position records"}
```

Clicking PNG in this state renders those same provenance lines over the Model 1 chart. The same issue applies while a replay model frame is pending.

**Expected correction:** derive exported identity/scenario/filenames from the actual displayed payload, or prevent export until the selected request is represented. Test export while an adversarially delayed model/market response is pending, including replay. JSON contains the correct payload today, though its settings URL and filename can describe the pending selection; keep that distinction explicit.

## Verification and coverage

- Independently ran `python -m pytest tests/test_causality.py tests/test_api.py tests/test_research.py tests/test_runtime.py -q`: **110 passed in 17.48s**. This does not cover the two adversarial cases above.
- Independently ran both reproductions and `git diff --check 42c35d7..42a0a24` successfully. The browser probe used a temporary local server and deterministic demo; it required no live market access.
- Inspected native parsers, independent venue inventories, exact-level update order/features, visible-history trimming, fingerprints, availability/quality semantics, replay source selection, bounded HTTP/cache/archive work, research capture/export/comparison/evaluation, UI cancellation/export, package assets/entry points and CI configuration. Prior missing-native-OI, distance/history separation and replay race fixes are present.
- Full-suite, extended responsive/browser, clean-install and single live OKX smoke results are coordinator evidence, not independently repeated here. The short-range live smoke does not test the long-range boundary defect.

## Declined judgments and remaining evidence boundaries

- No judgment of position-size truth, predictive performance, CoinGlass equivalence or net strategy profitability: authorized matched observations, a frozen strategy and real held-out outcomes are absent. The normalized agreement adapter and MAE diagnostic are correctly described as limited scaffolds; they do not imply a native provider importer or costs-aware simulator.
- No sustained uptime, geographic coverage, archive completeness, disk-retention effectiveness or 30-day shadow-run claim: these require observed operation. The fail-fast recorder explicitly delegates expected-poll/outage logging and scheduling to the operator.
- No physical-device, Safari/Firefox or formal screen-reader/contrast certification: this pass used Chromium plus source inspection and relied on prior responsive captures.
- No general performance or maximum-load memory verdict: the shipped benchmark measures warmed synthetic model/payload work and size. The approved broader cold/warm/concurrency/RSS benchmark remains incomplete engineering evidence, distinct from an empirical trading gate. Runtime resource bounds/tests provide useful protection but do not constitute that workload study.
- No remote-CI success claim: the workflow exists, but this review did not execute hosted CI or every supported Python environment. These limitations support the release's restrained score, not automatic rejection of the local research milestone after the concrete defects are fixed.

---

# Scoped correction re-review

Reviewed the uncommitted correction diff against `42a0a24` on 2026-10-28: `liqmap/data.py`, `tests/test_causality.py`, `web/app.js`, `tests/browser/smoke.py`, the implementer's `final-fix-report.md`, and the expanded benchmark/operations documentation. This is limited to the two original findings and benchmark evidence; it does not reopen the whole-branch review. No product files edited, commits created, or subagents used.

**Verdict: both correctness findings resolved; no remaining material concern in this scoped review.** The corrected local research milestone is ready subject to the coordinator's final artifact/documentation refresh and reported package verification. External evidence gates remain unchanged.

## Fix rulings

- **P1 OKX UTC/confirmation chronology — resolved.** Candle and statistics period mappings explicitly use `12Hutc`/`1Dutc`. Source rows must match a represented UTC grid open and close by the captured request cutoff; candles additionally require `confirm == "1"`. Misaligned/unconfirmed candles cannot contribute a future wick. Statistics use the same intervals, and absent taker intervals are no longer silently borrowed. The five new parameterized regressions cover 12-hour/daily offsets, confirmation, closed cutoff and statistics matching. Independently rerun: **5 passed, 31 deselected in 0.48s**. The implementer's provider-documentation check and coordinator's small live UTC samples support endpoint acceptance; this review did not repeat network calls or infer long-history completeness.
- **P2 pending-selection export identity — resolved.** PNG title/scenario and filenames now derive from the displayed payload. JSON also uses that identity and explicitly separates the displayed `settings_url` from the current `selected_settings_url`. A failed request leaves no dataset to export. Independently executed `export_identity_regressions` in its declared 1440x1000 Chromium context: pending **model, market and replay** cases passed, including actual PNG/JSON downloads, drawn labels, filenames and failure cleanup. Artifacts: `/tmp/final-scoped-review/pending-{model,market,replay}.{png,json}`. An initial standalone run at Playwright's default 1280x720 timed out because the intentionally open settings panel overlaid the PNG button; rerunning with the helper's declared test context passed. This was not evidence of incorrect exported identity and does not expand this scoped ruling into responsive certification.

## Benchmark ruling

The extension credibly fills the named measurement dimensions for its explicitly small, synthetic workload: a fresh production HTTP server subprocess; separate startup and first-request latency; five warm repeats; an eight-client synchronized burst; per-status latency/error/payload samples; OS-reported hardware; process RSS/Linux high-water observations; and declared workload budgets. Cleanup reaps the subprocess. Unsupported memory readings remain null with reasons. The documentation correctly avoids claiming an SLA, total-system memory bound, maximum-load certification or empirical trading value.

Independent `measure_http()` run observed:

| Measurement | Result |
| --- | --- |
| First request | HTTP 200, 181.1 ms |
| Warm repeats | 5/5 HTTP 200; maximum 180.9 ms |
| Eight-client burst | 4 HTTP 200 and 4 HTTP 429 `busy` |
| Observed maximum server RSS | 53,563,392 bytes |
| Reported server high-water mark | 55,443,456 bytes |
| Declared local checks | All three true |
| Subprocess cleanup | Reaped |

These are review-run observations, not replacement values for the final published artifact. Regenerate the report from the final reviewed state and retain the dirty/revision metadata honestly. Cold here means a fresh synthetic-service cache, not cold exchange/archive retrieval. One burst and four memory observations do not establish sustained stability or a universal peak, as the script already states.

## Verification and factual restraint

`node --check web/app.js` and the scoped `git diff --check` passed. Coordinator reports the broader corrected suite as **317 passed, 2 skipped**, extended browser acceptance successful, and small live 12-hour/daily UTC checks successful; those results were not redundantly rerun here.

The scorecard retains **8/10 overall, 8.5/10 engineering, 5/10 paid-provider replacement**, labels scores as judgments, and leaves demonstrated trading edge unestablished. No empirical 10/10 or provider-equivalence assertion was found. At inspection, `docs/RELEASE_REVIEW.md` still described the correction/benchmark wave as pending; replace that transitional text and refresh benchmark descriptions/counts before final handoff. This is a documentation completion step, not an unresolved code finding.

Declined judgments remain real strategy accuracy/profitability, paid-provider equivalence, sustained operations, full historical archive coverage, cross-browser/physical-device accessibility, remote CI success and generalized load performance: the necessary independent observations are outside this correction review.
