# Ten out of Ten Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise every section of the independent review to its highest honest score, measured against the reviewer's own 10/10 criteria.

**Architecture:** Phase 1 is a set of small fixes on top of `feat/history-replay`, all checked by tests: model timing, safe defaults, docs, dead-code removal and display. Phases 2–4 are scoped work packages: mark price and CI, CoinGlass evidence, and accuracy against real liquidations. Each gets its own spec, review and plan when it starts.

**Tech Stack:** Python 3.14 standard library plus numpy and requests, vanilla JavaScript canvas, pytest and hypothesis, Playwright for headless checks.

**Approval boundary:**
- Execution model and effort: Claude Opus 5.5, high effort, the current session's setting.
- Delivery: local commits on `feat/quality`, built from `feat/history-replay`, which is open as PR #9.
- No push, PR or merge without the owner's go-ahead.
- Phase 1 starts only after the owner approves this plan.

---

## Where the scores come from

Review of 2026-10-27, run by a fresh agent on HEAD `b54beeb`. The 10/10 criteria below are that reviewer's.

| Section | Now | Ceiling | Why the ceiling | Reached by |
|---|---|---|---|---|
| 1 Usefulness | 6 | 9 | True per-account leverage is private, and Binance's liquidation feed is sampled at one event per symbol per second. | Phases 1, 2, 4 |
| 2 Usability | 7 | 10 | Not blocked. | Phases 1, 2 |
| 3 Reliability | 5 | 9 | Free endpoints can change, block regions (HTTP 451) or lag by 1–2 days without notice. | Phases 1, 2 |
| 4 Construction | 6 | 10 | Not blocked. | Phases 1, 2 |
| 5 Reverse-engineering | 4 | 8 | Screenshots show peak placement and relative intensity only. CoinGlass's inputs are unknown, tiers above 50x are close together in price, and their model can change. | Phase 3 |
| 6 Overall | 5.5 | about 8.5 | Held down by section 5. | All |

A 10 in every section is not reachable under the project's constraints: free data, self-hosted, and black-box observation of CoinGlass. The README states these ceilings (Task 4).

## Decisions for the owner

- **D1, one objective per model.** Proposal:
  - Models 1 and 2 become "CoinGlass-like". They are redesigned on CoinGlass's Liquidation Map tiers, 10/25/50/100x, and fitted to screenshots (Phase 3). That replaces today's 5/20/50/60 and 20/60. It is a redesign, not a refit.
  - Model 3 becomes "outcome-calibrated": fitted to real liquidations (Phase 4).
- **D2, capture tooling and disclosure.** The Phase 3 capture is automated: it screenshots the owner's logged-in CoinGlass pages. Either:
  - keep the tooling private in `~/liqmap-evidence/` and reword README:5 to say plainly that "screenshots of CoinGlass's rendered pages are captured automatically for calibration; no CoinGlass API, code or data is used or shipped"; or
  - commit the tooling with the same wording.

  Either way the README stops saying "does not scrape" without qualification.
- **D3, a host for the Phase 4 recorder.** Recording outcome data for 90 days needs an always-on machine: the owner's Mac, kept awake, or a small server.

## File map (Phase 1)

- `liqmap/model.py`: entry timing; remove venue formulas, `fade_swept` and `bin_intensity`, which the model never calls.
- `liqmap/data.py`: replay span cap; `finalize_series` takes `RawSeries`; times in UTC.
- `liqmap/service.py`: limit on cached spans; entry text; view parsed once.
- `liqmap/server.py`: bind `127.0.0.1` by default; `_parser()`.
- `heatmap.py`: drop re-exports of removed functions.
- `web/app.js`, `web/index.html`: time-zone selector and color-bar ticks.
- `README.md`, `docs/HOW_IT_WORKS.md`: entry, host, test command, ceilings, current status.
- Tests:
  - `tests/test_model.py`: entry timing, README table, JS tables;
  - `tests/test_history.py`: caps;
  - `tests/test_server.py`: new;
  - `tests/validation/`: slimmed to production paths;
  - `tools/ui_check.mjs`: new headless check.

---

## Phase 1: quick wins (sections 2, 3, 4)

Work on `feat/quality`. Run every command from the repo root with `.venv/bin/python`. Each task ends green and with one commit.

### Task 0: Commit the headless page check

Later tasks rerun this. Earlier copies lived in `/tmp` and are gone.

**Files:** Create `tools/ui_check.mjs`.

- [ ] **Step 1: Write the script.** It needs a server on `UI_BASE` (default `http://127.0.0.1:8791`). `PLAYWRIGHT_CORE` is the path to playwright-core's `index.mjs` (on this machine `/Users/robertozuniga/.npm/_npx/da7caeae9db9c589/node_modules/playwright-core/index.mjs`). `CHROME` is the Chrome executable.

```js
// Headless check of the page: past view, replay, hover tiers, model switch, Live.
// UI_BASE=http://127.0.0.1:8791 PLAYWRIGHT_CORE=/path/index.mjs CHROME=/path/chrome node tools/ui_check.mjs
const { chromium } = await import(process.env.PLAYWRIGHT_CORE);
const base = process.env.UI_BASE || "http://127.0.0.1:8791";
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: 1500, height: 1150 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const ev = (fn) => page.evaluate(fn);
let failed = 0;
const check = (name, ok, detail = "") => {
  if (!ok) failed += 1;
  console.log(ok ? "PASS" : "FAIL", name, detail);
};
try {
  await page.goto(base + "/");
  await page.waitForTimeout(2500);
  await page.selectOption("#exchange", "binance");
  await page.waitForTimeout(2500);
  await page.selectOption("#bar", "15m");
  await page.fill("#asof-input", "2021-05-18T19:00");
  await page.dispatchEvent("#asof-input", "change");
  await page.waitForTimeout(2500);
  check("past view loads", await ev(() => Boolean(state.data && state.data.asof)));
  await page.fill("#to-input", "2021-05-20T19:00");
  await page.click("#rp-load");
  await page.waitForFunction(() => state.replay && state.data && state.data.replay, null, { timeout: 120000 });
  const r0 = await ev(() => ({ frames: state.replay.frames, asof: state.data.asof }));
  check("replay loads", r0.frames > 100, JSON.stringify(r0));
  const box = await page.locator("canvas").first().boundingBox();
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.wheel(0, -400);
  await page.waitForTimeout(300);
  const view = await ev(() => [state.view.lo, state.view.hi]);
  for (let i = 0; i < 3; i++) { await page.click("#rp-fwd"); await page.waitForTimeout(250); }
  const after = await ev(() => ({ idx: state.replay.idx, lo: state.view.lo, hi: state.view.hi }));
  check("steps keep zoom", after.idx === 3 && after.lo === view[0] && after.hi === view[1], JSON.stringify(after));
  await page.selectOption("#rp-speed", "10");
  await page.click("#rp-play");
  await page.waitForTimeout(2000);
  await page.click("#rp-play");
  await page.waitForTimeout(400);
  const played = await ev(() => ({ idx: state.replay.idx, shown: state.data.asof, want: state.replay.first + state.replay.idx * state.replay.step }));
  check("play never skips", played.idx > 8 && played.shown === played.want, JSON.stringify(played));
  const spot = await ev(() => {
    const d = state.data, L = state.layout, n = d.times.length;
    let best = -1, bi = 0, bj = 0;
    for (let b = 0; b < d.bins; b++) {
      if (d.centersA[b] < state.view.lo || d.centersA[b] > state.view.hi) continue; // stay inside the zoomed view
      for (let j = 0; j < n; j++) { const v = d.heatA[b * n + j]; if (v > best) { best = v; bi = b; bj = j; } }
    }
    return { x: xOf(bj, L), y: yOf(d.centersA[bi], L) };
  });
  const cbox = await page.locator("canvas").first().boundingBox();
  await page.mouse.move(cbox.x + spot.x, cbox.y + spot.y);
  await page.waitForTimeout(900);
  check("hover lists tiers", /\d+x (L|S) \$/.test(await page.textContent("#tip")));
  await page.click('.tab[data-model="2"]');
  await page.waitForTimeout(800);
  check("model switch keeps replay", await ev(() => Boolean(state.replay) && state.data.model.id === 2));
  await page.click("#live");
  await page.waitForTimeout(4000);
  check("Live leaves replay", await ev(() => !state.replay && state.data && !state.data.asof));
  check("no page errors", errors.length === 0, JSON.stringify(errors));
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
```

- [ ] **Step 2: Run it.**

```bash
.venv/bin/python -m liqmap.server --host 127.0.0.1 --port 8791 &
SERVER=$!
curl -sf --retry 30 --retry-connrefused --retry-delay 1 http://127.0.0.1:8791/ >/dev/null
UI_BASE=http://127.0.0.1:8791 \
  PLAYWRIGHT_CORE=/Users/robertozuniga/.npm/_npx/da7caeae9db9c589/node_modules/playwright-core/index.mjs \
  CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  node tools/ui_check.mjs
kill $SERVER
lsof -nP -iTCP:8791 -sTCP:LISTEN | wc -l   # 0
```

Expected: 8 PASS lines and exit code 0. The As-of inputs are COT until Task 6 changes them.

- [ ] **Step 3:** Mention it in README "Tests", then commit.

```bash
git add tools/ui_check.mjs README.md
git commit -m "Add a headless check of the page, replay and hover"
```

### Task 1: Entry at the price when open interest was read

The model enters new positions at the bar's close (`liqmap/model.py:524`). That bar's high and low then sweep them (`:565-568`), although part of that range traded before the close.

On Binance, open interest is the print at the bar open (`data.py:1017` keeps only prints exactly at an open). The consistent entry is the open: the bar's whole range then trades after it, so sweeping with it is correct, and the deposit-then-sweep order stays.

**Files:**
- Modify: `liqmap/model.py:524-526`, `liqmap/service.py:169`
- Modify: `README.md:62` (entry) and `README.md:72` ("a later candle")
- Test: `tests/test_model.py`

- [ ] **Step 1: Check what OKX's timestamp means.**
  - Fetch OKX's v5 docs for `GET /api/v5/rubik/stat/contracts/open-interest-history`, the endpoint `fetch_okx` uses (find its exact path in `liqmap/data.py`), and read what `ts` means.
  - Then, with the network allowed, compare one OKX row's `ts` and open interest with Binance's open interest at the same bar open for BTC.
  - If OKX's `ts` marks the start of the period (open interest at the open), go on as written.
  - If it marks the end, add a `MarketSeries` field `oi_at` (`"open"` or `"close"`, default `"open"`). Set it to `"close"` in `fetch_okx`, and enter at the close when `oi_at == "close"`. Add a second test for that case.
  - Record the evidence (doc quote and the two numbers) in the commit message.
- [ ] **Step 2: Write the failing test.** Add it to `ModelTests` in `tests/test_model.py`.

```python
    def test_entry_is_the_open_when_open_interest_was_read(self):
        # Open interest doubles between bar 0 and bar 1. Bar 1 opens at 100, rallies to 110.5, closes at 110.
        n = 3
        series = MarketSeries(
            times=np.arange(n, dtype=np.int64) * 86_400_000,
            open=np.array([100.0, 100.0, 110.0]),
            high=np.array([100.5, 110.5, 110.5]),
            low=np.array([99.5, 99.9, 109.5]),
            close=np.array([100.0, 110.0, 110.0]),
            volume_usd=np.zeros(n),
            oi_usd=np.array([1_000_000.0, 2_000_000.0, 2_000_000.0]),
            long_share=np.full(n, 0.5),
            exchange="test",
            symbol="BTCUSDT",
            coin="BTC",
            bar_seconds=86_400,
        )
        heat = build_heatmap(series, 1)
        spec = MODELS[1]
        # Shorts enter at 100. The rally to 110.5 sweeps every tier except the far 5x (119.4).
        five_x_share = dict(spec["weights"])[5]
        self.assertAlmostEqual(float(heat.short_heat[:, 2].sum()), 500_000.0 * five_x_share, delta=1.0)
        # The strongest long sits at the highest tier priced from the open, not the close.
        top_lev = max(lev for lev, _w in spec["weights"])
        peak = float(heat.centers[int(np.argmax(heat.long_heat[:, 1]))])
        step = float(heat.centers[1] - heat.centers[0])
        self.assertLess(abs(peak - liq_price_long(100.0, top_lev)), 2 * step)
```

- [ ] **Step 3: Run it and confirm it fails.**

Run: `.venv/bin/python -m pytest -q tests/test_model.py -k entry_is_the_open`
Expected: FAIL. Short heat at column 2 is about 500,000, and the long peak is about 108.67.

- [ ] **Step 4: Implement.** In `liqmap/model.py`, replace the entry lines.

```python
        # Open interest is read at the bar open, so new positions enter at that price.
        entry = float(series.open[i])
        if not np.isfinite(entry) or entry <= 0:
            entry = float(series.close[i])
```

Then update the text:
- `liqmap/service.py:169`: set `"entry": "open of the candle at which the open-interest print was taken"`.
- `README.md:62`: replace "The entry is that bar's close." with "The entry is that bar's open, the price when the open interest was read."
- `README.md:72`: say that the bar's own range, which trades after that open, and every later candle can sweep it.

- [ ] **Step 5: Run.**

Run: `.venv/bin/python -m pytest -q`
Expected: 193 passed, 2 skipped. The reviewer ran this on a scratch copy. If Step 1 added the OKX `oi_at` test, it is 194, and Task 2's count becomes 197.

- [ ] **Step 6: Commit.**

```bash
git add liqmap/model.py liqmap/service.py README.md tests/test_model.py
git commit -m "Enter new positions at the open, when open interest was read"
```

The Model 1 and 2 weights were fitted under the old timing. They are redesigned in Phase 3, not re-tuned now.

### Task 2: Safe server defaults and replay caps

**Files:**
- Modify: `liqmap/server.py:150-153`, `liqmap/data.py` (`load_span`), `liqmap/service.py` (`replay_payload`)
- Modify: `README.md` (run section and `README.md:121`)
- Test: create `tests/test_server.py`; modify `tests/test_history.py`

- [ ] **Step 1: Write the failing tests.**

`tests/test_server.py`:

```python
"""Server defaults. No network."""

import unittest

from liqmap.server import _parser


class ServerDefaultsTests(unittest.TestCase):
    def test_binds_localhost_by_default(self):
        args = _parser().parse_args([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)


if __name__ == "__main__":
    unittest.main()
```

Add to `ReplayServiceTests` in `tests/test_history.py`. Its `setUp` already stubs the archive, and these dates stay after 2020-09-01.

```python
    def test_span_length_is_capped(self):
        # 850 days of frames plus a 7-day window is over the cap.
        with self.assertRaisesRegex(DataError, "800 days"):
            service.replay_payload("BTC", "7d", "1d", str(T_2021 - 250 * 86_400), str(T_2021 + 600 * 86_400))
        # The window counts: 200 days of frames plus a 2-year window is also over.
        with self.assertRaisesRegex(DataError, "800 days"):
            service.replay_payload("BTC", "2y", "1d", str(T_2021 - 200 * 86_400), str(T_2021))

    def test_only_three_spans_are_kept(self):
        for rng in ("12h", "24h", "3d", "7d"):
            service.replay_payload("BTC", rng, "1h", str(T_2021 - 8 * 86_400), str(T_2021))
        self.assertEqual(len(service._spans), 3)
```

- [ ] **Step 2: Run and confirm they fail.**

Run: `.venv/bin/python -m pytest -q tests/test_server.py tests/test_history.py -k "localhost or capped or three_spans"`
Expected:
- `test_server.py` fails at import: there is no `_parser` yet.
- `test_span_length_is_capped` fails: no error mentions "800 days".
- `test_only_three_spans_are_kept` fails: there are 4 spans.

- [ ] **Step 3: Implement.**

In `liqmap/server.py`:

```python
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the open liquidation heatmap")
    # Localhost only by default. Pass --host 0.0.0.0 to share on a trusted network.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
```

Keep the rest of `main`, and print `http://{args.host}:{args.port}`.

In `liqmap/data.py` next to `MAX_BARS`:

```python
# Longest replay span, counting the first frame's window: one archive file per day is downloaded.
# 800 days keeps a 2y range replayable for about 70 days.
MAX_SPAN_DAYS = 800
```

In `load_span`, right after `last` is computed:

```python
    if (last - first) + int(spec["seconds"]) > MAX_SPAN_DAYS * 86400:
        raise DataError(f"A replay can cover at most {MAX_SPAN_DAYS} days including its window. Pick a later start or an earlier end.")
```

In `liqmap/service.py` next to `SPAN_TTL`, add `MAX_SPANS = 3`. In `replay_payload`, inside the `with _guard:` block after the insert:

```python
        while len(_spans) > MAX_SPANS:
            oldest = min(_spans, key=lambda key: _spans[key][0])
            _spans.pop(oldest)
```

- [ ] **Step 4:** Run `.venv/bin/python -m pytest -q`. Expected: 196 passed, 2 skipped (193 plus 3 new).
- [ ] **Step 5: Update the README, then commit.**
  - Fix `README.md:121` to say "on `127.0.0.1:8765` by default; `--host 0.0.0.0` shares it on your network".
  - In `README.md:53` (Replay row), add: "A replay downloads at most 800 days, counting the range's window, so a 2-year range can replay about 70 days."
  - Add the same sentence after the run command.

```bash
git add liqmap/server.py liqmap/data.py liqmap/service.py tests/test_server.py tests/test_history.py README.md
git commit -m "Bind localhost by default and cap replay spans"
```

### Task 3: Docs that match the code

**Files:**
- Modify: `README.md` (Tests section and Limitations), `docs/HOW_IT_WORKS.md:3`, `:131`, `:169`, `:349`, `:407-421`
- Test: `tests/test_model.py`

- [ ] **Step 1: Add the drift test** to `ParserTests`.

```python
    def test_readme_model_table_matches_models(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parents[1] / "README.md").read_text()
        for model_id, spec in MODELS.items():
            row = next(line for line in text.splitlines() if line.startswith(f"| {model_id} {spec['name']} |"))
            listed = row.rstrip().rstrip("|").rsplit("|", 1)[-1].strip()
            expected = ", ".join(f"{lev}× {round(weight * 100)}%" for lev, weight in spec["weights"])
            self.assertEqual(listed, expected, f"README row for model {model_id}")
```

- [ ] **Step 2: Prove the test bites.** Run `.venv/bin/python -m pytest -q tests/test_model.py -k readme_model_table`: it should PASS. Change one weight in the README, rerun to see FAIL, then revert.
- [ ] **Step 3: Fix the README.**
  - In "Tests", use `python -m pytest -q`, plus `RUN_NETWORK=1` for the network test, plus `tools/ui_check.mjs` from Task 0.
  - In "Limitations", add the ceilings from the score table: true leverage is private; screenshot matching tops out around 8/10.
- [ ] **Step 4: Fix `docs/HOW_IT_WORKS.md`.**
  - Replace the opening paragraph (line 3) and "Implementation status" (lines 407–421) with a short, current description of `liqmap/model.py`:
    - one stock book per leverage tier;
    - entry at the bar open;
    - deposit, then sweep by the bar's range;
    - proportional scaling when open interest drops;
    - model 2 decay and churn;
    - last-price candles, not mark price;
    - one fixed 0.5% MMR, no brackets.
  - Edit the sentences at lines 131, 169 and 349 that say "`heatmap.py` does not …" to name `liqmap/model.py` and state what it does now.
- [ ] **Step 5: Commit.** Run `.venv/bin/python -m pytest -q`; it passes, including `test_supply_chain.py`, which scans the docs.

```bash
git add README.md docs/HOW_IT_WORKS.md tests/test_model.py
git commit -m "Make the docs describe the current model and state the score ceilings"
```

### Task 4: Remove code the model never calls

**Files:**
- Modify: `liqmap/model.py:17-276`, `heatmap.py:23-32`, `README.md:135` (it cites `heatmap.liq_price`)
- Modify: `tests/test_model.py` (imports, and the `liq_price` and NaN assertions at lines 68–72), `tests/validation/__init__.py:4` (mentions `adapter`)
- Modify: `tests/validation/oracle.py` (keep only `liq_price` with the Binance branch and its validation)
- Delete: `tests/validation/adapter.py`, `tests/validation/test_sanity.py`, `tests/validation/test_hypothesis.py`
- Replace: `tests/validation/test_liq_price.py`, `tests/validation/conftest.py`
- Keep: `test_supply_chain.py`, `net_allowlist.py`

- [ ] **Step 1: Record the size before.**

Run: `git ls-files '*.py' | xargs wc -l | tail -1`

- [ ] **Step 2: Replace `tests/validation/test_liq_price.py`.**

```python
"""The model's liquidation price against the exchange-documented oracle."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from liqmap.model import MMR, liq_price_long, liq_price_short
from tests.validation import oracle


@given(
    entry=st.integers(min_value=1, max_value=1_000_000).map(float),
    leverage=st.integers(min_value=2, max_value=125),
)
def test_model_matches_binance_zero_tier_oracle(entry: float, leverage: int) -> None:
    assert liq_price_long(entry, leverage) == pytest.approx(oracle.liq_price(entry, leverage, "long", MMR), rel=1e-12)
    assert liq_price_short(entry, leverage) == pytest.approx(oracle.liq_price(entry, leverage, "short", MMR), rel=1e-12)


def test_golden_entry_100_at_10x() -> None:
    assert liq_price_long(100.0, 10) == pytest.approx(90.4523, abs=1e-4)
    assert liq_price_short(100.0, 10) == pytest.approx(109.4527, abs=1e-4)


@given(entry=st.integers(min_value=1, max_value=1_000_000).map(float), low=st.integers(2, 124), high=st.integers(3, 125))
def test_higher_leverage_liquidates_closer(entry: float, low: int, high: int) -> None:
    if high <= low:
        return
    assert entry - liq_price_long(entry, high) < entry - liq_price_long(entry, low)
    assert liq_price_short(entry, high) - entry < liq_price_short(entry, low) - entry
```

- [ ] **Step 3: Slim the test support files.**
  - `tests/validation/conftest.py`: keep the hypothesis profile, the `network` marker hook and `sys.path`; remove `price_of`, `fade_of`, `bin_of` and the CI sentence.
  - `tests/validation/oracle.py`: keep `liq_price` with only the Binance branch, and its input checks; delete `fade_swept`, `bin_intensity` and the Bybit and OKX branches.
  - Delete the three files listed above, and fix `tests/validation/__init__.py:4`.
- [ ] **Step 4: Shrink `liqmap/model.py`.**
  - Delete `VENUES`, `liq_price`, `_validate_liq`, `_binance_liq`, `_okx_liq`, `_bybit_classic`, `_bybit_uta`, `fade_swept`, `bin_intensity` and the now-unused imports.
  - Then:

```python
def liq_price_long(entry: float, leverage: float, mmr: float = MMR) -> float:
    """Isolated long liquidation price at zero fee, zero extra margin, zero maintenance amount."""
    return entry * (1.0 - 1.0 / leverage) / (1.0 - mmr)


def liq_price_short(entry: float, leverage: float, mmr: float = MMR) -> float:
    """Isolated short liquidation price at zero fee, zero extra margin, zero maintenance amount."""
    return entry * (1.0 + 1.0 / leverage) / (1.0 + mmr)
```

- [ ] **Step 5: Clean up callers.**
  - `heatmap.py`: drop the imports of removed names and the `__all__` block.
  - `tests/test_model.py`: drop those imports, the `liq_price` and NaN-error assertions at lines 68–72, and `test_fade_and_bins` at lines 74–79. Keep the `liq_price_long` and `liq_price_short` checks.
  - `README.md:135`: remove the `heatmap.liq_price` sentence.
- [ ] **Step 6: Run.**
  - `.venv/bin/python -m pytest -q` passes.
  - `.venv/bin/python -c "import heatmap"` works.
  - `grep -rn "fade_swept\|bin_intensity\|_bybit\|_okx_liq\|heatmap.liq_price\|adapter" liqmap tests heatmap.py README.md` prints nothing.
  - Rerun the line count from Step 1. Expected: at least 15% fewer lines.
- [ ] **Step 7: Commit.**

```bash
git add -A liqmap/model.py heatmap.py README.md tests
git commit -m "Delete liquidation formulas and helpers the model never calls"
```

### Task 5: `finalize_series` takes a `RawSeries`, the view is parsed once, and the JS tables are checked

**Files:**
- Modify: `liqmap/data.py`. Move `RawSeries` above `finalize_series`, and change `finalize_series`, its 6 call sites, `combine_series` and `_records_from_series`.
- Modify: `liqmap/service.py` (`get_series`, `load_heatmap`, `levels_payload`)
- Test: `tests/test_model.py`

- [ ] **Step 1:** Run `.venv/bin/python -m pytest -q | tail -1` and record the counts. These tests guard the refactor.
- [ ] **Step 2: Change the signature** to `finalize_series(raw: RawSeries, *, exchange, symbol, coin, bar_seconds, source, note, warnings, venues=None, now_s=None)`.
  - Its body reads `raw.grid`, `raw.open` and the rest; the `source` argument wins over `raw.source`.
  - Each call site builds one `RawSeries(source, grid, open_, high, low, close, volume, oi, share)` and passes it.
- [ ] **Step 3: Rewrite `combine_series(parts, grid_ms, bar_seconds, coin)`** on arrays, keeping today's four rules exactly:
  1. **Placement.** For each part, map its `times` onto `grid_ms` with `idx = np.searchsorted(grid_ms, times)`. Keep only `ok = (idx < len(grid_ms)) & (grid_ms[np.minimum(idx, len(grid_ms) - 1)] == times)`. A shorter part, or one on another grid, never misfolds.
  2. **Open interest.** Per bar, sum the finite OI of the parts that have a value. A bar with no finite OI stays NaN (track `oi_seen`), not 0.
  3. **Share and volume.** Share is weighted by each part's OI. Volume is the sum across parts. The test expects `volume_usd[-1] == 6`.
  4. **Price.** Candles come from the donor ranked as today (`data.py:500`): `times[-1]` first, then `nansum(volume)`.

  Also keep today's skip of parts with no open interest and no candles, and the "no overlapping bars" `DataError` (`data.py:516`, `525-526`).

  Delete `_records_from_series`. `test_combine_sums_oi_and_keeps_busiest_price` must pass unchanged.
- [ ] **Step 4: Parse the view once.** `get_series` returns `(loaded, spec, end_open)`. `load_heatmap` and `levels_payload` unpack it, and the second `resolve_view` call at `service.py:219-220` goes away.
- [ ] **Step 5: Check the JS tables.** Keep the literals in `web/app.js` (`BAR_S` and `RANGE_S`), because `restore()` needs them before `/api/status` answers. Add this to `ParserTests`:

```python
    def test_page_tables_match_the_server(self):
        import json
        import re
        from pathlib import Path

        from liqmap.data import BAR_SECONDS, RANGES

        js = (Path(__file__).resolve().parents[1] / "web" / "app.js").read_text()

        def table(name):
            body = re.search(rf"const {name} = (\{{[^}}]*\}});", js).group(1)
            return json.loads(re.sub(r'(\w+|"[^"]+")\s*:', lambda m: f'"{m.group(1).strip(chr(34))}":', body))

        self.assertEqual(table("BAR_S"), BAR_SECONDS)
        self.assertEqual(table("RANGE_S"), {key: spec["seconds"] for key, spec in RANGES.items()})
```

- [ ] **Step 6: Run and commit.** Run `.venv/bin/python -m pytest -q`, `node --check web/app.js`, and the Task 0 check with a server on 8791, stopped afterwards. All must pass.

```bash
git add liqmap/data.py liqmap/service.py tests/test_model.py
git commit -m "Pass folded bars to finalize_series, parse the view once, and check the page tables"
```

### Task 6: Time-zone selector and readable color scale

**Files:**
- Modify: `web/app.js:4,68,80-88,146-170` and `drawScale` (`:712+`)
- Modify: `web/index.html` (a select in `.sub`; As-of and To labels)
- Modify: `liqmap/data.py:468-470` (`_fmt_cot`)
- Modify: `README.md:47,52,92`, `tools/ui_check.mjs`

- [ ] **Step 1: Server messages in UTC.** Rename `_fmt_cot` to `_fmt_utc`, which returns `datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d %b %Y, %H:%M UTC")`. Update its callers. Run `.venv/bin/python -m pytest -q`; it passes.
- [ ] **Step 2: Time-zone state.**
  - Replace `const TZ` with `state.tz`, defaulting to `Intl.DateTimeFormat().resolvedOptions().timeZone`.
  - Persist and restore it in the existing `localStorage` `try` blocks, and use `state.tz` in `fmtStamp` and `fmtAxis`.
  - The suffix comes from `formatToParts` with `timeZoneName: "short"`.
  - Add `<select id="tz">` with the browser zone, `UTC`, `America/New_York`, `America/Bogota`, `Europe/London` and `Asia/Singapore`.
  - The labels become "As of" and "To".
- [ ] **Step 3: Convert across daylight-saving time.** Replace `cotInputValue` and `parseCotInput` and delete `COT_OFFSET_S`.

```js
function zoneOffsetMs(utcMs, tz) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(new Date(utcMs)).map((p) => [p.type, p.value]),
  );
  return Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second) - utcMs;
}

// datetime-local text read on the chosen clock → unix seconds.
// A repeated fall-back hour resolves to its first (daylight) instant.
// A skipped spring-forward time maps one hour earlier (New York 02:30 on 2026-03-08 → 01:30).
function parseZonedInput(text) {
  const wall = Date.parse(text + ":00Z");
  if (!Number.isFinite(wall)) return null;
  let utc = wall - zoneOffsetMs(wall, state.tz);
  utc = wall - zoneOffsetMs(utc, state.tz);
  return Math.floor(utc / 1000);
}

function zonedInputValue(seconds) {
  const ms = seconds * 1000;
  return new Date(ms + zoneOffsetMs(ms, state.tz)).toISOString().slice(0, 16);
}
```

- [ ] **Step 4: Color bar in round USD.**
  - In `drawScale`, place ticks at 0, 25, 50, 75 and 100% of `state.vmax`, each at the height of its color: `intensity = Math.pow(usd / state.vmax, gamma)`.
  - Label each with `fmtUsd(usd)`.
  - Add "nonlinear · threshold <t>" under the bar when there is room.
- [ ] **Step 5: Extend `tools/ui_check.mjs`.**
  - Set zone `UTC` before the As-of inputs, and change the inputs to UTC: 2021-05-19T00:00 and 2021-05-21T00:00.
  - Add these checks:
    - with zone `UTC`, As-of `2021-05-19T23:59` sends `asof=1621468740`;
    - New York `2026-11-01T01:30` gives `1793511000`, the first (daylight) instant;
    - New York `2026-03-08T02:30` gives `1772951400`, the same as 01:30;
    - switching the zone keeps the As-of instant and moves the axis labels;
    - the color-bar tick at 50% sits at 0.5^gamma.
  - Update `README.md:47,52,92`.
- [ ] **Step 6: Run and commit.** Run `.venv/bin/python -m pytest -q` and the Task 0 check with a server on 8791, stopped afterwards; both pass.

```bash
git add web/app.js web/index.html liqmap/data.py README.md tools/ui_check.mjs
git commit -m "Let the viewer pick the time zone and label the color scale by USD"
```

### Task 7: Phase 1 verification and rescore

- [ ] Run `.venv/bin/python -m pytest -q`; all pass. Run `RUN_NETWORK=1 .venv/bin/python -m pytest -q tests/test_history.py -k network`; it passes. Run the Task 0 check; it passes. Confirm ports 8791 and 8765 are free afterwards.
- [ ] Ask a fresh agent to rescore sections 2–4 against this plan's criteria. Report the scores to the owner. Push or open a PR only on the owner's go-ahead.

---

## Phase 2: mark price, CI, hardening (sections 2–4; 1 optional)

Each item gets its own spec, review and plan before code. Units are defined here.

1. **Mark-price sweeps** (Reliability).
   - Binance: archive `markPriceKlines` (from 2019-12-23) and live `/fapi/v1/markPriceKlines`.
   - OKX: `/api/v5/market/history-mark-price-candles`.
   - The model sweeps on the mark high and low when present, and on the last price otherwise, with a banner saying so.
   - Accept when a test shows that a last-price wick the mark never reached does not sweep a level, and live, past and replay views use the mark on both venues.
2. **CI** (Reliability, Construction). A GitHub Actions workflow on push runs:
   - `pytest`;
   - `vulture liqmap --min-confidence 80`, reporting nothing;
   - `ruff check --select C901`, with the threshold set to today's maximum so it cannot grow. New or changed functions must stay at 12 or below.
   - Coverage of at least 90% for `model.py` and `service.py`, and at least 70% for `data.py`, which is mostly network code.

   A daily job runs the `RUN_NETWORK=1` smoke tests: OKX live, Binance live, the archive, and a simulated 451 that falls back to the archive. Pin the development dependencies.
3. **Hardening** (Reliability).
   - Honor `Retry-After` on 429.
   - Hypothesis property: for random `asof`, range and bar over stubbed loaders, no frame time is at or after `asof`.
   - Every `DataError` message ends with an action. A test collects all `raise DataError(...)` literals and requires one of: Pick, Use, Try, Check, Load, Set, Wait.
4. **Export** (Usefulness). A "Download CSV" for the current frame: time, price and long/short USD per tier.
5. **Accessibility** (Usability). A keyboard and screen-reader alternative to the canvas: the levels table, plus an ARIA live summary of the frame. Accept at a Lighthouse accessibility score of 90 or more.
6. *Optional:* **Bybit** as a third venue (Usefulness).
   - Uses `/v5/market/open-interest` with one-side versus two-side handled.
   - Adds `api.bybit.com` to `net_allowlist.py`, and updates `test_binance_archive_is_allowed_and_a_fourth_venue_is_not` on purpose.
   - Do it only if the accuracy work in Phase 4 shows that more venues change the shelves.

## Phase 3: CoinGlass evidence and model redesign (section 5, ceiling 8). Needs D1 and D2, and at least 4 weeks

1. **Evidence store and capture** (after D2).
   - Create `~/liqmap-evidence/{shots,series,tools}` and `manifest.jsonl`, one line per screenshot: `utc`, `pair`, `range`, `model`, `file`, `sha256`, `our_series`.
   - `capture.mjs` drives the owner's logged-in Chrome on `127.0.0.1:9222` at a 1600×1700 viewport. Per coin (BTC, ETH, SOL), model page and range (24 hour, 1 week), it picks "OKX <COIN>/USDT Perpetual" by searching "OKX", and screenshots.
   - `snap.py` pickles our matching OKX series.
   - Reword README:5 per D2.
2. **Sessions.** At least 20 over at least 4 weeks, including at least 3 days with a move of 5% or more.
3. **Extractor check.** Render our own map in CoinGlass's colors, run the extractor, and recover the peaks within 1 px. Read the price axis from the crosshair tooltip, so there is no OCR dependency.
4. **Redesign and fit** (D1). Models 1 and 2 use tiers 10/25/50/100 with at most 4 free weights. Split by time: fit on the first 60% of sessions, test on the last 40%. SOL is never used in fitting.
5. **Pass bar on the held-out sessions.** Distances are in percentage points of price.
   - median peak-distance error of at most 0.15 points within 3% of price, and at most 0.30 beyond;
   - peak recall of at least 80% within 0.25 points;
   - Spearman correlation of the latest-column profile of at least 0.8;
   - correlation of the 2D map, resampled to their grid, of at least 0.7;
   - median band lifetime within 10% of the window.

   The model must beat both the old weights and a uniform prior, with bootstrap 95% confidence intervals. Commit the method and the metric table only.
6. **Drift.** Recapture monthly; refit if the held-out metrics drop below the bar.

## Phase 4: accuracy against real liquidations (section 1, ceiling 9). Needs D3

1. **Recorder,** on D3's host, writing to `~/liqmap-evidence/outcomes/`. It records Binance `@forceOrder`, which is sampled and therefore a lower bound, and Hyperliquid liquidations from its public trades and liquidation stream. No bulk positions endpoint exists, so it does not snapshot per-account positions.
2. **Hit definition.** A predicted shelf (top 5 per side at time t) is hit when at least $250k notional (BTC; scaled by open interest for other coins) is liquidated inside its ±0.3% band within 24h of t.
3. **Report.** Over 90 or more days the model was not tuned on: precision and recall of hits, against a uniform-leverage baseline, with bootstrap 95% confidence intervals that exclude zero. Published in the README with the date range.
4. **Model 3** is calibrated on these outcomes (D1).

## Self-review

- Every reviewer criterion maps to a task or phase.
- Sections 2 and 4: Tasks 0 and 3–6, plus Phase 2 items 2, 3 and 5.
- Section 3: Tasks 1–2, plus Phase 2 items 1–3.
- Section 1: Phase 2 item 4, plus Phase 4.
- Section 5: Phase 3.
- The ceilings are stated, not promised away.
- Run Phase 1 tasks in order: 0, 1, 2, 3, 4, 5, 6, 7. Tasks 4 and 5 touch the same test files.
