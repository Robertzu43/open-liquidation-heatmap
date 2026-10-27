# Ten out of Ten Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise every section of the independent review to its highest honest score, measured against the reviewer's own 10/10 criteria.

**Architecture:** Phase 1 is a set of small fixes on top of `feat/history-replay`, all checked by tests: model timing, safe server defaults, docs, dead-code removal and display. Phases 2–4 are scoped work packages: mark price and CI, CoinGlass evidence, and accuracy against real liquidations. Each gets its own spec and plan when it starts, because each is a separate subsystem with its own data.

**Tech Stack:** Python 3.14 standard library plus numpy and requests, vanilla JavaScript canvas, pytest and hypothesis, Playwright (headless checks only).

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

A 10 in every section is not reachable under the project's constraints: free data, self-hosted, and black-box observation of CoinGlass. The README will state these ceilings (Task 4).

## Decisions for the owner before Phases 3–4

- **D1, one objective per model.** Fixing the model's timing (Task 1) can move it away from CoinGlass if CoinGlass has the same flaw. Calibrating on real liquidations (Phase 4) can pull the weights away from CoinGlass's. Proposal:
  - Models 1 and 2 are "CoinGlass-like": fitted to screenshots (Phase 3).
  - Model 3 is "outcome-calibrated": fitted to real liquidations (Phase 4).
- **D2, where the capture tooling lives.** A CoinGlass screen-capture script in a public repo that says it "does not scrape CoinGlass" is a contradiction. Proposal: keep the capture and fit tooling and its evidence in a private folder, `~/liqmap-evidence/`, outside the repo. Commit only the method and the summary metrics.

## File map (Phase 1)

- `liqmap/model.py`: entry timing; remove venue formulas, `fade_swept` and `bin_intensity`, which the model never calls.
- `liqmap/data.py`: replay span cap; `finalize_series` takes `RawSeries`; times in UTC.
- `liqmap/service.py`: limit on cached spans; entry text; bar list in `/api/status`.
- `liqmap/server.py`: bind `127.0.0.1` by default; `_parser()`.
- `heatmap.py`: drop re-exports of removed functions.
- `web/app.js`, `web/index.html`: time-zone selector, color-bar ticks, range and bar tables from `/api/status`.
- `README.md`, `docs/HOW_IT_WORKS.md`: entry text, test command, honest ceilings, current status section.
- Tests:
  - `tests/test_model.py`: entry timing, README table;
  - `tests/test_history.py`: caps;
  - `tests/test_server.py`: new;
  - `tests/validation/`: slimmed to production paths.

---

## Phase 1: quick wins (sections 2, 3, 4)

Work on branch `feat/quality`. Run every command from the repo root with `.venv/bin/python`.

### Task 1: Entry at the price when open interest was read

The model reads open interest at the bar open, but enters new positions at that bar's close (`liqmap/model.py:524`). That bar's high and low then sweep them (`:565-568`), although part of that range traded before the close. The consistent fix is entry = open: the price at the moment the open-interest print was taken. The bar's whole range then trades after that entry, so sweeping with it is correct. The deposit-then-sweep order stays.

**Files:**
- Modify: `liqmap/model.py:524-526`
- Modify: `liqmap/service.py:169`, `README.md:62`
- Test: `tests/test_model.py`

- [ ] **Step 1: Write the failing test.** Add it to `ModelTests` in `tests/test_model.py`.

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
        # Shorts enter at 100. The rally to 110.5 sweeps every tier except 5x (119.4).
        five_x_share = dict(spec["weights"])[5]
        self.assertAlmostEqual(float(heat.short_heat[:, 2].sum()), 500_000.0 * five_x_share, delta=1.0)
        # The strongest long sits at the highest-leverage tier priced from the open, not the close.
        top_lev = max(lev for lev, _w in spec["weights"])
        peak = float(heat.centers[int(np.argmax(heat.long_heat[:, 1]))])
        step = float(heat.centers[1] - heat.centers[0])
        self.assertLess(abs(peak - liq_price_long(100.0, top_lev)), 2 * step)
```

- [ ] **Step 2: Run it and confirm it fails.**

Run: `.venv/bin/python -m pytest -q tests/test_model.py -k entry_is_the_open`
Expected: FAIL. The short heat at column 2 is about 500,000, because shorts priced from the close (110) sit above 110.5.

- [ ] **Step 3: Implement.** In `liqmap/model.py`, replace the entry lines.

```python
        # Open interest is read at the bar open, so new positions enter at that price.
        entry = float(series.open[i])
        if not np.isfinite(entry) or entry <= 0:
            entry = float(series.close[i])
```

In `liqmap/service.py:169` set `"entry": "open of the candle at which the open-interest print was taken"`.

In `README.md:62` replace "The entry is that bar's close." with "The entry is that bar's open, the price when the open interest was read. The bar's own range can then sweep it."

- [ ] **Step 4: Run the suite.**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass. If an existing test assumed close entries, fix its expected values (not the model), and say so in the commit.

- [ ] **Step 5: Commit.**

```bash
git add liqmap/model.py liqmap/service.py README.md tests/test_model.py
git commit -m "Enter new positions at the open, when open interest was read"
```

Note: the Model 1 and 2 weights were fitted with the old timing. They are refitted in Phase 3. Until then they are not re-tuned.

### Task 2: Safe server defaults and replay caps

**Files:**
- Modify: `liqmap/server.py:150-153` (`main`)
- Modify: `liqmap/data.py` (`load_span`), `liqmap/service.py` (`replay_payload`)
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

Add to `ReplayServiceTests` in `tests/test_history.py`:

```python
    def test_span_length_is_capped(self):
        with self.assertRaisesRegex(DataError, "400 days"):
            service.replay_payload("BTC", "24h", "1h", str(T_2021 - 500 * 86_400), str(T_2021))

    def test_only_three_spans_are_kept(self):
        for days in (2, 3, 4, 5):
            service.replay_payload("BTC", "24h", "15m", str(T_2021 - days * 86_400), str(T_2021 - (days - 1) * 86_400))
        self.assertEqual(len(service._spans), 1)  # one key: coin, bar and range repeat
        for rng in ("12h", "24h", "3d", "7d"):
            service.replay_payload("BTC", rng, "1h", str(T_2021 - 8 * 86_400), str(T_2021))
        self.assertLessEqual(len(service._spans), 3)
```

- [ ] **Step 2: Run and confirm they fail.**

Run: `.venv/bin/python -m pytest -q tests/test_server.py tests/test_history.py -k "localhost or capped or three_spans"`
Expected: FAIL. `_parser` does not exist yet, and nothing caps a span.

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

In `liqmap/data.py`, next to `MAX_BARS`:

```python
# Longest replay span: one archive file per day is downloaded on first load.
MAX_SPAN_DAYS = 400
```

At the top of `load_span`, after `last` is computed:

```python
    if last - first > MAX_SPAN_DAYS * 86400:
        raise DataError(f"A replay can cover at most {MAX_SPAN_DAYS} days. Pick a later start or an earlier end.")
```

In `liqmap/service.py`, next to `SPAN_TTL`, add `MAX_SPANS = 3`. In `replay_payload` after the insert, under `_guard`:

```python
        while len(_spans) > MAX_SPANS:
            oldest = min(_spans, key=lambda key: _spans[key][0])
            _spans.pop(oldest)
```

- [ ] **Step 4: Run.**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5:** Add "Pass `--host 0.0.0.0` to open it on your network." after the run command in `README.md`. Then commit.

```bash
git add liqmap/server.py liqmap/data.py liqmap/service.py tests/test_server.py tests/test_history.py README.md
git commit -m "Bind localhost by default and cap replay spans"
```

### Task 3: Persistent evidence store (private, outside the repo; D2)

This is local tooling, not repository code. It is needed before any refit.

- [ ] **Step 1: Create the store.** Run `mkdir -p ~/liqmap-evidence/{shots,series,tools}`. Write `~/liqmap-evidence/README.md`:
  - what each folder holds;
  - that `manifest.jsonl` has one line per screenshot: `utc`, `venue_pair`, `range`, `model`, `file`, `sha256`, `our_series`.
- [ ] **Step 2: Capture script.** Write `~/liqmap-evidence/tools/capture.mjs` from the session's earlier `capture.mjs`.
  - It connects to a logged-in Chrome on `127.0.0.1:9222` and sets the viewport to 1600×1700.
  - Per coin (BTC, ETH, SOL), per model page, per range (24 hour, 1 week): it picks the pair by searching "OKX" and choosing the "OKX <COIN>/USDT Perpetual" option, then takes a screenshot.
  - After each screenshot it appends a manifest line with its `sha256`.
  - It does nothing else: no scripts read, no network capture.
- [ ] **Step 3: Series snapshot.** Write `~/liqmap-evidence/tools/snap.py`. It imports `liqmap.data.load_market` from the repo and pickles OKX series for the same coins and ranges into `series/`, named with the capture UTC time. The capture script calls it after each coin.
- [ ] **Step 4: Dry run.** Use one coin, one model and one range. Check that `manifest.jsonl` gained one line, and that `shasum -a 256` matches it.

No commit: this lives outside the repo.

### Task 4: Docs that match the code

**Files:**
- Modify: `README.md` (Tests section, Limitations), `docs/HOW_IT_WORKS.md:3`, `:131`, `:169`, `:349`, `:407-421`
- Test: `tests/test_model.py`

- [ ] **Step 1: Write the failing test.** Add it to `ParserTests`.

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

- [ ] **Step 2:** Run `.venv/bin/python -m pytest -q tests/test_model.py -k readme_model_table`. Expected: PASS today. It guards future drift, so to prove it works, edit one README weight, watch it fail, then revert.
- [ ] **Step 3: Fix the README.**
  - In "Tests", replace the `unittest` command with `python -m pytest -q`. Say that the network test runs with `RUN_NETWORK=1`.
  - In "Limitations", add the ceilings from the score table: true leverage is private; screenshot matching tops out around 8/10.
- [ ] **Step 4: Fix `docs/HOW_IT_WORKS.md`.**
  - Replace the opening "Where this repository is" paragraph (line 3) and the "Implementation status" section (lines 407–421) with a short, current description of `liqmap/model.py`:
    - stock book per tier;
    - entry at the open;
    - deposit, then sweep by the bar's range;
    - proportional scaling on open-interest drops;
    - model 2 decay and churn;
    - last-price candles, not mark price;
    - one fixed 0.5% MMR, no brackets.
  - In the sentences at lines 131, 169 and 349 that say "`heatmap.py` does not …", replace `heatmap.py` with `liqmap/model.py` and correct what it now does.
- [ ] **Step 5:** Run `.venv/bin/python -m pytest -q`. Expected: all pass, including `test_supply_chain.py`, which scans the docs. Then commit.

```bash
git add README.md docs/HOW_IT_WORKS.md tests/test_model.py
git commit -m "Make the docs describe the current model and state the score ceilings"
```

### Task 5: Remove code the model never calls

**Files:**
- Modify: `liqmap/model.py:17-276`, `heatmap.py:23-32`, `tests/test_model.py` (imports and venue tests)
- Delete: `tests/validation/adapter.py`, `tests/validation/test_sanity.py`, `tests/validation/test_hypothesis.py`
- Replace: `tests/validation/test_liq_price.py`, `tests/validation/conftest.py`
- Keep: `tests/validation/oracle.py` (the independent formula from exchange docs), `test_supply_chain.py`, `net_allowlist.py`

- [ ] **Step 1: Replace `tests/validation/test_liq_price.py`** with tests of the production formula only.

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

- [ ] **Step 2: Slim the conftest.** In `tests/validation/conftest.py`, keep only the hypothesis profile, the `network` marker hook and `sys.path`. Remove the `price_of`, `fade_of` and `bin_of` fixtures and the CI sentence. Delete `adapter.py`, `test_sanity.py` and `test_hypothesis.py`.
- [ ] **Step 3: Shrink `liqmap/model.py`.**
  - Delete `VENUES`, `liq_price`, `_validate_liq`, `_binance_liq`, `_okx_liq`, `_bybit_classic`, `_bybit_uta`, `fade_swept`, `bin_intensity` and the now-unused imports (`bisect`, `itertools`, `math` if unused).
  - Make the two helpers the formula itself:

```python
def liq_price_long(entry: float, leverage: float, mmr: float = MMR) -> float:
    """Isolated long liquidation price at zero fee, zero extra margin, zero maintenance amount."""
    return entry * (1.0 - 1.0 / leverage) / (1.0 - mmr)


def liq_price_short(entry: float, leverage: float, mmr: float = MMR) -> float:
    """Isolated short liquidation price at zero fee, zero extra margin, zero maintenance amount."""
    return entry * (1.0 + 1.0 / leverage) / (1.0 + mmr)
```

- [ ] **Step 4: Clean up callers.**
  - `heatmap.py`: drop the `bin_intensity`, `fade_swept` and `liq_price` imports and the `__all__` block.
  - `tests/test_model.py`: drop those imports and any tests of them. Keep the tests of `liq_price_long` and `liq_price_short`.
- [ ] **Step 5: Run.**
  - `.venv/bin/python -m pytest -q` passes.
  - `.venv/bin/python -c "import heatmap"` works.
  - `grep -rn "fade_swept\|bin_intensity\|_bybit\|_okx_liq" liqmap tests heatmap.py` prints nothing.
  - Record the line counts before and after: `wc -l liqmap/*.py tests/**/*.py`. The target is at least 15% fewer lines.
- [ ] **Step 6: Commit.**

```bash
git add -A liqmap/model.py heatmap.py tests
git commit -m "Delete liquidation formulas and helpers the model never calls"
```

### Task 6: `finalize_series` takes a `RawSeries`, and the range and bar tables have one source

**Files:**
- Modify: `liqmap/data.py`. Move `RawSeries` above `finalize_series`, and change `finalize_series`, its 6 call sites and `combine_series`/`_records_from_series`.
- Modify: `liqmap/service.py` (`status_payload` gains `"bars": BAR_SECONDS`), `web/app.js` (`BAR_S`, `RANGE_S`, `bootStatus`).

- [ ] **Step 1:** Run the current suite and save the counts: `.venv/bin/python -m pytest -q | tail -1`. These tests guard the refactor; they must stay green throughout.
- [ ] **Step 2: Change the signature** to `finalize_series(raw: RawSeries, *, exchange, symbol, coin, bar_seconds, source, note, warnings, venues=None, now_s=None)`.
  - Its body reads `raw.grid`, `raw.open` and the rest. `raw.source` is ignored, and the `source` argument wins.
  - Each call site builds `RawSeries(source, grid, open_, high, low, close, volume, oi, share)` once and passes it.
- [ ] **Step 3: Rewrite `combine_series`** to work on arrays.
  - For each part, map its `times` onto `grid_ms` with `np.searchsorted`.
  - Sum OI with `np.nansum` across the parts that have a value.
  - Weight the share by OI.
  - Take price from the part with the most volume, as today.
  - Delete `_records_from_series`.
  - The existing test `test_combine_sums_oi_and_keeps_busiest_price` must pass unchanged.
- [ ] **Step 4: One source for the tables.**
  - `status_payload` adds `"bars": BAR_SECONDS`.
  - In `web/app.js`, change `BAR_S` and `RANGE_S` from literals to `let BAR_S = {}; let RANGE_S = {};`, and fill them in `bootStatus` from `body.bars` and `body.ranges`.
  - `barFits` returns `true` while they are empty.
  - Remove `service.py:219-220`'s second `resolve_view` by having `get_series` return `(loaded, spec, end_open)`. `load_heatmap` and `levels_payload` unpack it.
- [ ] **Step 5:** Run `.venv/bin/python -m pytest -q` and `node --check web/app.js`, and rerun the headless replay check (Task 8). All must pass. Then commit.

```bash
git add liqmap/data.py liqmap/service.py web/app.js
git commit -m "Pass folded bars to finalize_series and serve the range and bar tables once"
```

### Task 7: Time-zone selector and readable color scale

**Files:**
- Modify: `web/app.js:4,68,80-88,146-170` (time zone), `drawScale` (`:712+`)
- Modify: `web/index.html` (a select in `.sub`), `liqmap/data.py:468-470` (`_fmt_cot`)
- Modify: `README.md:47,52`

- [ ] **Step 1: Server messages in UTC.** Rename `_fmt_cot` to `_fmt_utc`, returning `stamp.strftime("%d %b %Y, %H:%M UTC")` from a UTC datetime, and update its callers. Run `.venv/bin/python -m pytest -q`; it passes.
- [ ] **Step 2: Time-zone state.**
  - In `web/app.js`, replace `const TZ` with `state.tz`, default `Intl.DateTimeFormat().resolvedOptions().timeZone`.
  - Persist it in `localStorage` (inside the existing `try`), and use `state.tz` in `fmtStamp` and `fmtAxis`.
  - The label suffix comes from `new Intl.DateTimeFormat("en-US", { timeZone: state.tz, timeZoneName: "short" }).formatToParts(date)`.
  - Add `<select id="tz">` with the browser zone, `UTC`, `America/New_York`, `America/Bogota`, `Europe/London` and `Asia/Singapore`.
  - Rename the field labels "As of (COT)" and "To (COT)" to "As of" and "To".
- [ ] **Step 3: Convert any zone, including daylight-saving time.**

```js
function zoneOffsetMs(utcMs, tz) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(new Date(utcMs)).map((p) => [p.type, p.value]),
  );
  return Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second) - utcMs;
}

function parseZonedInput(text) {
  // datetime-local text read on the chosen clock → unix seconds.
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

Replace `cotInputValue` and `parseCotInput` and their callers, and delete `COT_OFFSET_S`.

- [ ] **Step 4: Readable color bar.**
  - In `drawScale`, place ticks at round USD fractions of `state.vmax` (0, 25%, 50%, 75%, 100%), each at the bar height of its color: `intensity = Math.pow(usd / state.vmax, gamma)`.
  - Label each with `fmtUsd(usd)`.
  - Add "nonlinear: threshold <value>" under the bar when there is room.
- [ ] **Step 5: Headless check.** It covers:
  - switching the zone moves the axis labels;
  - a set As-of survives a zone switch: the same instant is shown on the new clock;
  - with zone "UTC", As-of `2021-05-19T23:59` gives `asof=1621468740`;
  - the color-bar tick at 50% sits where `shade` gives 0.5^gamma.

  Update `README.md:47,52`.
- [ ] **Step 6: Commit.**

```bash
git add web/app.js web/index.html liqmap/data.py README.md
git commit -m "Let the viewer pick the time zone and label the color scale by USD"
```

### Task 8: Phase 1 verification

- [ ] Run `.venv/bin/python -m pytest -q`. Expected: all pass, and the counts are recorded.
- [ ] Run `RUN_NETWORK=1 .venv/bin/python -m pytest -q tests/test_history.py -k network`. Expected: pass.
- [ ] Start the server on port 8791 and rerun the headless replay script, updated for the new labels. Expected: all checks pass and there are no page errors. Then stop the server and Chrome, and confirm with `lsof -nP -iTCP:8791 -sTCP:LISTEN | wc -l` that the port is free (0).
- [ ] Ask a fresh agent to rescore sections 2–4 against the criteria in this plan. Record the scores in the PR.

---

## Phase 2: mark price, CI, export, Bybit (sections 1–4)

Each item gets its own spec, review and plan before code.

1. **Mark-price sweeps** (Reliability).
   - Fold Binance mark-price klines (`data.binance.vision .../markPriceKlines/`, from 2019-12-23; live `/fapi/v1/markPriceKlines`) next to the last-price candles.
   - The model sweeps on the mark high and low when present.
   - Accept when a test shows a last-price wick that the mark never reached does not sweep a level, and live, past and replay views all use the mark.
2. **CI and checks** (Reliability, Construction). A GitHub Actions workflow, free for public repos, runs:
   - `pytest`;
   - `ruff check --select C901 --max-complexity 12`;
   - `vulture liqmap --min-confidence 80`, reporting nothing;
   - `coverage run -m pytest` with `liqmap/` at 90% or more.

   A scheduled daily job runs `RUN_NETWORK=1` smoke tests against OKX live, Binance live, the archive, and a simulated 451 that falls back to the archive. Development dependencies are pinned in `requirements-dev.txt`.
3. **Hardening** (Reliability).
   - Honor `Retry-After` on 429.
   - A hypothesis property test: for random `asof`, range and bar over stubbed loaders, no frame contains a time at or after `asof`.
   - Error messages name their fix.
4. **Export** (Usefulness). `GET /api/heatmap?...&format=csv` and a page button export the latest column and the levels table. A replay can also export every frame's top shelves as CSV.
5. **Accessibility** (Usability). A keyboard and screen-reader alternative to the canvas: the levels table, plus an ARIA live summary of the frame. Accept at a Lighthouse accessibility score of 90 or more.
6. **Bybit as a third venue** (Usefulness, Construction).
   - Public open-interest history back to listing, `/v5/market/open-interest`, with one-side versus two-side handled per `HOW_IT_WORKS.md`.
   - Use one shared paging helper for OKX, Binance and Bybit.
   - Add `api.bybit.com` to `net_allowlist.py`.

## Phase 3: CoinGlass evidence and refit (section 5, ceiling 8). Calendar: at least 4 weeks

Needs decisions D1 and D2, and the owner's logged-in capture browser for each session.

1. **Captures.** At least 20 sessions over at least 4 weeks, including at least 3 days with moves of 5% or more. Each session covers BTC, ETH and SOL × 24h and 1 week × 3 models, using Task 3 tooling with the manifest and matched series.
2. **Extractor check.** Render our own map in CoinGlass's colors, run the extractor, and recover the peaks within 1 px. Read the price axis from the crosshair tooltip rather than by hand, so no OCR dependency is needed.
3. **Fit.** Fixed tiers 10/25/50/100, so there are at most 4 free weights and at least 10 targets per weight. Split by time: fit on the first 60% of sessions and test on the last 40%. SOL is never used in fitting. Model 3 is fitted only if D1 says so.
4. **Pass bar for the held-out sessions:**
   - median peak-distance error of at most 0.15 points within 3% of price, and at most 0.30 beyond;
   - peak recall of at least 80% within 0.25 points;
   - Spearman correlation of the latest-column profile of at least 0.8;
   - correlation of the 2D map, resampled to their grid, of at least 0.7, with band lifetimes off by no more than 10% of the window.

   The new weights must beat both the old weights and a uniform prior, with bootstrap 95% confidence intervals. Commit only the method and the metric table, not the screenshots.
5. **Drift.** Recapture monthly. If the held-out metrics drop below the bar, refit.

## Phase 4: accuracy against real liquidations (section 1, ceiling 9)

1. **Outcome data, collected from now on.**
   - A small recorder for Binance `@forceOrder`: sampled at one event per symbol per second, so treat it as a lower bound.
   - Hyperliquid public positions and liquidation prices, recorded to `~/liqmap-evidence/outcomes/`.
2. **Report.** After 90 or more days that the model was not tuned on:
   - precision and recall of the top-5 shelves within ±0.3% over the next 24h;
   - both compared with a uniform-leverage baseline, using bootstrap 95% confidence intervals that exclude zero;
   - published in the README with the date range.
3. **Hyperliquid layer.** Optionally show real Hyperliquid liquidation prices as an overlay, and use them to calibrate Model 3 (D1).

## Self-review

- Every reviewer criterion is mapped to a task or phase.
- Sections 2 and 4: Tasks 4–7 plus Phase 2 items 2, 3 and 5.
- Section 3: Tasks 1–2 plus Phase 2 items 1–3.
- Section 1: Phase 2 items 4 and 6, plus Phase 4.
- Section 5: Task 3 plus Phase 3.
- The ceilings are stated, not promised away.
- Phase 1 tasks are independent except Task 6, which edits the same file after Task 5. Run them in order.
