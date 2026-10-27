# Replay and leverage breakdown (Binance)

Approved 2026-10-27. Revised the same day after an independent review. Builds on `2026-10-27-binance-time-travel-design.md`.

## Goal

Watch liquidation clusters form, grow and get swept as price develops. Play the Binance heatmap forward bar by bar from any past moment. At any time and price, show which leverage tiers make up the estimate. Past and present behave the same.

## Limits come from Binance

Verified 2026-10-27.

- **Archive open interest:** 5-minute rows in daily files. BTCUSDT starts 2020-09-01; other coins start later.
- **New archive files:** a daily file appears the next day.
- **Live `openInterestHist`:**
  - covers the latest month only;
  - returns at most 500 rows per call;
  - is capped at 1,000 requests per 5 minutes per IP;
  - IP bans run from 2 minutes to 3 days.

Consequences:

- A replay starts no earlier than the coin's first archive data. Nothing is hardcoded per coin.
- The finest bar is 5m.
- A replay loads its data once. Frames never call an exchange.
- There is no span-length cap. Five years of 5m bars is about 35 MB in memory.
- The first load of a long span downloads one metrics file per day. Later loads read `.cache/`.
- One cap is ours: 3,000 bars per frame on screen. A full 3,000-bar frame is about 2.5 MB gzipped.

## Behavior

1. **Per-tier books.**
   - `build_heatmap` keeps one long book and one short book per tier, shape tiers × bins.
   - Deposits, sweeps, decay, OI scaling and churn act per tier. Totals are the sums.
   - `Heatmap` gains:
     - `tiers`;
     - `profile_long_tiers` and `profile_short_tiers`: the latest column per tier;
     - `long_tier_column` and `short_tier_column`: tiers × bins at one bar, recorded only when `build_heatmap(..., tier_column=j)` asks for it.
   - No full tiers × bins × bars arrays.
2. **Span loading.** `GET /api/replay?symbol&range&bar&model&from&to` is Binance pair only.
   - `from` and `to` take the same formats as `asof`. An empty `to` means now.
   - The server folds raw arrays on one grid covering `[from − range, to]`, before `finalize_series`. They are candles, OI and share from two sources, kept apart:
     - **archive:** the whole span;
     - **live:** `[max(from − range, now − 29d), to]` when that window is not empty and the live API answers. Klines, OI and taker are paged.

     Live page caps are raised to cover 29 days at 5m.
   - **Archive metrics:** only the print at each bar open, as in time travel.
   - It returns:
     - `first_asof` and `last_asof`, in ms: the close of the first and last frame. The first frame is the first one whose direct time-travel view has at least 2 candles and some OI.
     - `bar_seconds`;
     - `frames`;
     - `days`: the number of archive days loaded;
     - a note when data starts after `from`.
   - The span stays in memory for 30 minutes from its last use, keyed by coin, bar and range. Expired spans are dropped on insert.
3. **Frames.** `GET /api/heatmap?...&asof=T&replay=1`.
   - The service slices `[start, end_open]` from the span's raw arrays.
   - It picks the source with the time-travel rule:
     1. the archive, if its last finite candle is at or after `end_open`;
     2. else live, if the window starts after now − 29 days and live covers `end_open`;
     3. else the stale archive.
   - It then calls `finalize_series(now_s=end_open + bar)` with the same note and stale rule as the direct path. The direct time-travel path uses the same slice-and-finalize function, so the two are identical by construction.
   - Slices are not stored in the series cache.
   - Without a covering span, the request returns 409 "replay expired". It never falls back to the network.
   - The payload keeps the real `source` and adds `"replay": true`.
4. **Leverage breakdown.**
   - `GET /api/levels` takes the map query, `t` (a bar open in `times`, else 400) and `price`.
   - It uses the same series lookup as `/api/heatmap`, including `replay=1`, and builds with `tier_column`.
   - It returns, at the nearest bin:
     - the bin's center;
     - the tiers, as `{leverage, long_usd, short_usd}`;
     - the totals.
   - The totals equal `heat` at that cell.
5. **Payload.** `/api/heatmap` adds `tiers`, plus `profile_long_tiers` and `profile_short_tiers`: latest-column tiers × bins float32, base64. `heat` is unchanged.
6. **Page: liquidation map panel.**
   - The optional side bars stack by tier.
   - Each leverage has one fixed color across models, for 5x, 10x, 20x, 25x, 50x, 60x, 75x and 100x. Unknown tiers get gray.
   - There is a legend.
   - A cumulative long line runs from price downward and a cumulative short line from price upward. The owner approved these.
   - It updates every frame.
7. **Page: hover.**
   - While not playing, after about 120 ms on a cell, the tooltip fetches `/api/levels` and lists the tiers with long and short USD.
   - Its cache key includes the cell, the model and the frame `asof`.
8. **Page: player.** A row under the toolbar, enabled for Binance pair:
   - Start uses the As-of field. There is an optional To field; empty means now. Load shows "Loading N days…" and the first-load warning.
   - Step back, Play/Pause, step forward.
   - Speed is a maximum: 1, 2, 5 or 10 bars per second. Play waits for each frame and never skips.
   - A scrubber over the frames, and the current frame's time.
   - Keys:
     - Space toggles play and calls preventDefault.
     - The arrow keys step.
     - Keys are ignored while focus is in an input, select, textarea, button or contenteditable element.

   During replay:
   - Frames skip the `asof` part of `identityChanged`, so zoom and pan stay after the first frame.
   - Frame fetches use their own AbortController and a replay generation token, not `state.seq`. At most 2 frames are prefetched.
   - Changing the model keeps replay and refetches the current frame.
   - Changing coin, range, bar, exchange, mode or As-of, or pressing Live, leaves replay.
   - A 409 reloads the span once, then shows an error.
   - Auto-refresh is paused.

## Out of scope

OKX and multi-exchange replay, per-exchange charts, video or data export, and a model refit to 10x/25x/50x/100x (a follow-up).

## Checks

- **Offline tests:**
  - per-tier books sum to the totals for all three models, at several requested columns and in the profiles;
  - `/api/levels` totals equal `heat` at that cell; a missing `t` returns 400;
  - frame equality: for many `asof`, a frame from a stubbed span equals the direct time-travel series, with OI and candle gaps at both window edges;
  - frame source rule: archive, live tail, and the stale archive;
  - no loader calls while the span is cached; 409 after it expires;
  - `/api/replay` refuses non-Binance and bars over the per-frame cap;
  - the payload carries the tier profiles and `replay`.
- **Headless browser:**
  - load a 2021 replay;
  - play, pause, step and scrub;
  - zoom stays across frames;
  - hover shows the tier tooltip;
  - changing the model keeps replay;
  - Live leaves replay;
  - no page errors.
- **Full suite passes,** including `test_supply_chain.py`.

## Delivery

Branch `feat/replay` from `feat/time-travel`. Local feature commits. No push, no PR.

## Steps

1. Model: per-tier books, plus `tier_column` and tests. Replaces commit 8ffbb2c's full arrays.
2. Data:
   - refactor time travel into fold (raw arrays) and finish (slice, pick source, finalize);
   - the span loader with live paging;
   - tests.
3. Service and server: span cache, `replay=1` frames and 409, `/api/replay`, `/api/levels`, payload tiers, tests.
4. Page: tier-stacked panel with cumulative lines, hover breakdown, player.
5. Full suite, headless browser check, commit.
