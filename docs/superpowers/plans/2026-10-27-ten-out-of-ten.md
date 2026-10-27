# Ten Out of Ten Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the inference engine, harden and test the server, redesign the page around one toolbar, and rewrite the README so a trader sees why a free, open heatmap beats a paid, closed one.

**Architecture:** Model fixes stay inside `build_heatmap` and the three OI parsers. The server gains tests and a safer default, not new endpoints. The UI keeps plain HTML/CSS/JS and moves rarely used controls into one drawer.

**Tech Stack:** Python 3.11+, numpy, requests, matplotlib, stdlib `http.server`; vanilla JS canvas; pytest + hypothesis; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-27-ten-out-of-ten-design.md`

**Lean rule (ponytail, full):** take the smallest change that fully works. No new runtime dependency, no framework, no option nobody asked for. A deliberate limit gets a `shortcut: <limit>, <when to upgrade>` comment.

## Global Constraints

- No new runtime dependencies in `requirements.txt`.
- No vendor URL, import or key anywhere. The vendor may be named in README and `docs/` text only. `tests/validation/test_supply_chain.py` must stay green.
- Python host literals stay within `tests/validation/net_allowlist.py`.
- Zero-tier golden case unchanged: entry 100, 10x, MMR 0.005 gives long 90.4523, short 109.4527.
- Times shown to users stay America/Bogota, labeled COT.
- Replay frames must equal direct views (`tests/test_history.py::ReplaySpanTests::test_frames_equal_direct_views`).
- Every control added to the page removes or hides one.

## Review Focus

1. A zero or missing OI print mid-window must be treated as missing, not as everyone closing. Test in Task 2.
2. A venue that stamps OI at the period end instead of the start would shift entries by one bar. Task 2 pins each parser's timestamp with a fixture.
3. Symbol mode sums coin-unit OI across venues. Mixing a USD fallback with coin units would be wrong. Test in Task 2.
4. Old cached `.cache/` archive zips must still parse after the column change. Test in Task 2 uses the real archive header.
5. At 390px width the drawer opens, the chart stays usable, and the page never scrolls sideways. Check in Task 6.

---

### Task 1: Entries priced at the bar they happened in, and levels survive that bar

**Files:**
- Modify: `liqmap/model.py:503-575` (`build_heatmap` loop)
- Test: `tests/test_model.py`

**Interfaces:**
- Produces: `build_heatmap(series, model_id, n_bins=360, tier_column=None) -> Heatmap`, signature unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_levels_survive_the_bar_that_opened_them(self):
    # OI rises between bar 0 and bar 1 prints, so it opened during bar 0.
    # Bar 0 wicks 2% down; bar 1 and 2 are flat.
    price = np.full(3, 100.0)
    low = np.array([98.0, 99.95, 99.95])
    high = np.full(3, 100.05)
    oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0])
    heat = build_heatmap(_series(price, oi, high=high, low=low), 1)
    self.assertAlmostEqual(float(heat.long_heat[:, 2].sum()), 500_000.0, delta=1.0)
    self.assertGreater(float(heat.profile_long_tiers[3].sum()), 0.0)  # 60x tier kept

def test_entry_is_previous_bar_typical_price(self):
    price = np.array([100.0, 110.0, 110.0])
    high = np.array([102.0, 110.05, 110.05])
    low = np.array([97.0, 109.95, 109.95])
    oi = np.array([1_000_000.0, 2_000_000.0, 2_000_000.0])
    heat = build_heatmap(_series(price, oi, high=high, low=low), 1)
    typical = (102.0 + 97.0 + 100.0) / 3
    expected = liq_price_long(typical, 5)
    peak = heat.centers[np.argmax(heat.profile_long_tiers[0])]
    self.assertLess(abs(peak - expected) / expected, 0.01)
```

- [ ] **Step 2: Run and confirm both fail**

Run: `python -m pytest tests/test_model.py -k "survive or typical" -v`
Expected: 2 failed (column 2 keeps about 110,000; peak sits near the 110 close).

- [ ] **Step 3: Implement**

In the loop, price the delta at step `i` with `(high[i-1] + low[i-1] + close[i-1]) / 3`, falling back to `close[i-1]` then `open[i]` when not finite. Leave the order deposit, snapshot, sweep. Rule 2 of the spec then holds without moving the sweep.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -q`
Expected: all pass. If `test_sweep_removes_shorts_after_price_trades_through` or a history test shifts by one column, update its column index and say why in the commit message.

- [ ] **Step 5: Commit** — `fix(model): price new OI at the bar it happened in`

---

### Task 2: Difference contracts, not dollars

**Files:**
- Modify: `liqmap/model.py` (`MarketSeries`: add `oi_base: np.ndarray | None = None` as the last field; `build_heatmap` delta)
- Modify: `liqmap/data.py` — OKX parser `:703` reads `row[2]` (`oiCcy`); Binance live `:1003` reads `sumOpenInterest`; archive `_parse_metrics_csv` `:788` reads `sum_open_interest`; `fold_oi`, `finalize_series`, `combine_series`, `_records_from_series`, `RawSeries`, `fetch_demo` carry the extra column.
- Test: `tests/test_model.py`, `tests/test_history.py`

**Interfaces:**
- Produces: `fold_oi(records, grid_ms, bar_ms) -> tuple[oi_usd, share, oi_base]`. Records become `(ts, oi_usd, long_share, oi_base)`.
- Produces: `MarketSeries.oi_base` (coin units, NaN when missing).

- [ ] **Step 1: Write the failing tests**

```python
def test_price_move_with_flat_contracts_adds_nothing(self):
    price = np.array([100.0, 105.0, 105.0])
    base = np.full(3, 10_000.0)
    s = _series(price, base * price)  # USD OI rises 5% with flat contracts
    s.oi_base = base
    heat = build_heatmap(s, 1)
    self.assertAlmostEqual(float(heat.profile_long.sum() + heat.profile_short.sum()), 0.0, delta=1.0)

def test_zero_oi_print_is_missing_not_a_close(self): ...
    # base [100, 200, 0, 200]: total resting after bar 3 equals total after bar 1.

def test_metrics_csv_reads_coin_open_interest(self): ...
    # Header: create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,
    #         sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio
    # Row "2021-05-19 00:00:00,BTCUSDT,1000,40000000,..." -> (1621382400000, 40000000.0, share, 1000.0)

def test_okx_open_interest_row_uses_coin_column(self): ...
    # Row ["1700000000000", "123456", "1234.5", "45000000"] -> oi_base 1234.5, oi_usd 45000000.0

def test_aggregate_sums_coin_units(self): ...
    # Two parts with oi_base 100 and 50 on the same grid -> combined oi_base 150.
```

- [ ] **Step 2: Run and confirm they fail**

Run: `python -m pytest tests -k "flat_contracts or zero_oi or coin" -v`

- [ ] **Step 3: Implement**

In `build_heatmap`, take `base = oi_base` when finite, else `oi_usd / close` for that bar; treat `base <= 0` as missing; delta in coins times the Task 1 entry price is the USD deposit. The OI-drop shrink uses the coin fraction. Add the warning "Open interest in coin units was unavailable; using USD value." once per series when the fallback is used.

Before changing parsers, confirm each venue's column with one real sample (cached archive zip, or a saved OKX response) and paste the header into the test fixture.

- [ ] **Step 4: Run the whole suite** — `python -m pytest -q`, all pass, including replay equality.

- [ ] **Step 5: Commit** — `fix(model): count new positions in contracts, not dollars`

---

### Task 3: Fade by time, and split the swept bin

**Files:**
- Modify: `liqmap/model.py` (`MODELS[2]`: replace `"decay": 0.9992` with `"half_life_hours": 72.0`; loop at `:567-572`)
- Modify: `liqmap/service.py` `public_model` (expose `half_life_hours`), `web/app.js` `renderHow` (show it)
- Test: `tests/test_model.py`

- [ ] **Step 1: Failing tests**

```python
def test_fade_depends_on_time_not_bar_count(self):
    # Same 24h of flat price after one deposit, as 288 x 5m bars and as 24 x 1h bars.
    # Remaining USD agrees within 1%, and equals 0.5 ** (24 / 72) of the deposit within 1%.

def test_wick_into_a_bin_removes_only_the_crossed_part(self):
    # One long level in a bin [a, b). A later low at a + 0.25 * (b - a) keeps 25% of it.
```

- [ ] **Step 2: Confirm they fail.**
- [ ] **Step 3: Implement.** Per-bar factor `0.5 ** (series.bar_seconds / (half_life_hours * 3600))`; models without `half_life_hours` do not fade. Boundary bin keeps `(low - edge_lo) / bin_width` for longs, `(edge_hi - high) / bin_width` for shorts.
- [ ] **Step 4: Full suite passes.**
- [ ] **Step 5: Commit** — `fix(model): fade by half-life and split the swept bin`

---

### Task 4: Re-check the band placement

**Files:** `liqmap/model.py` `MODELS[*]["weights"]` only if the check fails; `README.md` model table.

- [ ] **Step 1:** Repeat Method steps 1–3 of `docs/superpowers/specs/2026-10-27-band-placement-fit-design.md` on BTC and ETH, 24h and 7d. Blocked on open decision 2 of the spec.
- [ ] **Step 2:** If the pass rule still holds, change nothing and record the errors in the spec's Results. If not, re-tune weights only, then record.
- [ ] **Step 3: Commit** — `chore(model): re-check band placement after the mechanics fix`

---

### Task 5: Server default, browser flag, handler tests, CI

**Files:**
- Modify: `liqmap/server.py:150-166` (default host `127.0.0.1`; `--open` uses `webbrowser.open`)
- Create: `tests/test_server.py`
- Create: `.github/workflows/ci.yml`
- Modify: `README.md` test command

**Interfaces:**
- Produces: `build_parser() -> argparse.ArgumentParser` with `--host`, `--port`, `--open`; `main(argv)` uses it.

- [ ] **Step 1: Failing tests** — start `ThreadingHTTPServer(("127.0.0.1", 0), Handler)` in a thread; assert:
  - `/api/status` 200 with `ok: true`
  - `/api/heatmap?exchange=demo&symbol=BTC&range=24h&model=1` 200, `bins == 360`
  - `/api/heatmap?exchange=demo&symbol=BTC&range=bad` 400 with an `error`
  - `/api/heatmap?exchange=okx&symbol=BTC&range=24h&asof=1700000000` 400 "Binance-only"
  - `/api/heatmap?exchange=binance&symbol=BTC&range=24h&asof=1700000000&replay=1` 409 with `replay_expired: true`
  - `/../liqmap/model.py` and `/%2e%2e/README.md` 404
  - `Accept-Encoding: gzip` on the demo heatmap returns `Content-Encoding: gzip`
  - `build_parser().parse_args([]).host == "127.0.0.1"`
- [ ] **Step 2: Confirm the host test fails.**
- [ ] **Step 3: Implement.** CI: checkout, setup-python matrix 3.11/3.12/3.13, `pip install -r requirements-dev.txt`, `python -m pytest -q`.
- [ ] **Step 4:** `python -m pytest -q` passes locally.
- [ ] **Step 5: Commit** — `test(server): cover the HTTP API and run CI`

---

### Task 6: One toolbar, one drawer, a page that explains itself

**Files:**
- Modify: `web/index.html`, `web/styles.css`, `web/app.js` (`syncControls` `:249`, `applyPayload` `:386`, `renderAnalysis` `:441`, `draw` `:646`, `drawScale` `:712`, `drawLiqBars` `:781`, `wire` `:1260`)
- Create: `tests/ui/smoke.mjs` (Playwright, run by hand and in CI if a browser is present)

**Interfaces:**
- Consumes: `/api/heatmap` fields unchanged plus `model.half_life_hours`.
- Produces: element ids `toolbar`, `more`, `drawer`, `intro`; existing ids keep their meaning so `localStorage` settings still restore.

- [ ] **Step 1: Write the smoke script** against the demo exchange at 1440×900 and 390×844. Assert:
  - no console errors;
  - `#drawer` hidden until `#more` is clicked;
  - replay controls hidden for OKX, visible for Binance pair;
  - `#intro` shows on first load, stays hidden after dismiss and reload;
  - `document.documentElement.scrollWidth <= innerWidth` at 390px;
  - after a bad coin, `#title` still names the last good map.
  Save screenshots to `docs/img/` (`hero.png`, `phone.png`).
- [ ] **Step 2: Run it and confirm it fails** — `node tests/ui/smoke.mjs`.
- [ ] **Step 3: Implement the spec's UI section.** Toolbar: coin, exchange (OKX, Binance, All venues, Demo), range, model segmented, More. Drawer: everything else. Imbalance column becomes "Short x% / Long y%"; ratios above 10 read "> 10×". Color bar ticks at the USD values the threshold curve maps to 0, 0.5 and 1 of the bar height. `ctx.imageSmoothingEnabled = false` at `:671`. Leverage labels at `:851` move left of the bars. `$("title")` updates only in the success path.
- [ ] **Step 4: Smoke script passes**; look at both screenshots.
- [ ] **Step 5: Commit** — `feat(ui): one toolbar, a settings drawer and a first-visit guide`

---

### Task 7: README that sells and teaches

**Files:**
- Modify: `README.md`, `.gitignore` (add `!docs/img/*.png`)
- Use: `docs/img/hero.png`, `docs/img/phone.png` from Task 6; add `docs/img/annotated.png` with three callouts (shelf above, shelf below, current price)

- [ ] **Step 1:** Write the README in the spec's order. Quick start is exactly:

```bash
pip install -r requirements.txt
python -m liqmap.server --open
```

- [ ] **Step 2:** The comparison table uses only the spec's dated facts, names the vendor in text, and has no vendor link. Each "this project" cell points at the file that proves it.
- [ ] **Step 3: Verify** — `python -m pytest tests/validation/test_supply_chain.py -q` passes; every README command run once from a clean venv works.
- [ ] **Step 4: Commit** — `docs: rewrite the README around the free, open heatmap`

---

### Task 8: Make the method doc match the code

**Files:** `docs/HOW_IT_WORKS.md` (lines 3, 131-133, 349, 407-421)

- [ ] **Step 1:** Replace the stale "heatmap.py does not…" passages and the Implementation status list with what `liqmap/model.py` now does, and list the remaining gaps: last price not mark price, one MMR, no brackets.
- [ ] **Step 2: Verify** — every number in Implementation status matches `MODELS` and `build_heatmap` (grep each one).
- [ ] **Step 3: Commit** — `docs: describe the current model in HOW_IT_WORKS`

---

### Task 9: Re-score

- [ ] **Step 1:** Dispatch a fresh reviewer with the same six-aspect rubric and no hint of the expected answer.
- [ ] **Step 2:** Any aspect under 9, or mechanics under 8: turn each cited finding into a task here and repeat Task 9.
