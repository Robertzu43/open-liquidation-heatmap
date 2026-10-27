# Replay and leverage breakdown (Binance)

Approved 2026-10-27. Builds on `2026-10-27-binance-time-travel-design.md`.

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

- A replay starts no earlier than the coin's first archive data. The loader trims leading bars that have no data and reports the real first frame. Nothing is hardcoded per coin.
- The finest bar is 5m.
- A replay loads its whole span once. Frames make no exchange calls.
- The live API fills only the gap after the archive's last bar, which is always inside its one-month window.
- There is no span-length cap. A whole-span series is about 13 MB for two years of 5m bars.
- One cap is ours: 3,000 bars per frame on screen. It keeps a frame's payload small. It is unchanged from time travel.

## Behavior

1. **Per-tier books.**
   - `build_heatmap` keeps one long book and one short book per leverage tier of the model.
   - Deposits, sweeps, decay, OI scaling and churn act per tier exactly as they act on the total today.
   - `long_heat` and `short_heat` are the sums over tiers, unchanged.
   - `Heatmap` gains:
     - `tiers`: the leverages;
     - `long_tiers` and `short_tiers`: float32 arrays of shape tiers × bins × bars;
     - `profile_long_tiers` and `profile_short_tiers`: the latest column per tier.
2. **Span loading.** `GET /api/replay?symbol&range&bar&model&from&to` is Binance pair only.
   - `from` and `to` take the same formats as `asof`. An empty `to` means now.
   - The server loads one series covering `[from − range, to]` at the bar.
   - The archive supplies everything up to its last bar. If the span ends later, the live API supplies the bars after that. Klines and OI are paged.
   - Both sources give open interest at the bar open, so they can be joined on one grid: no overlap, no gap.
   - It returns:
     - `first_asof` and `last_asof`, in ms: the close of the first and last frame;
     - `bar_seconds`;
     - `frames`;
     - a note when the coin's data starts after `from`.
   - The loaded span is kept in memory for 30 minutes, keyed by coin and bar. Older spans are dropped on insert.
3. **Frames.**
   - A frame is `GET /api/heatmap` with `asof`, exactly as in time travel.
   - When a cached span covers the frame's window, the service slices the window out of the span instead of loading.
   - Source `replay`; stale only if the span ends before the frame.
   - Otherwise it uses the normal time-travel path.
4. **Leverage breakdown.**
   - `GET /api/levels` takes the map query plus `t` (bar open, ms) and `price`.
   - It returns, at the nearest bin:
     - the bin's center price;
     - the tiers, as `{leverage, long_usd, short_usd}`;
     - the long and short totals.
   - It reuses the cached series and rebuilds the map. No map cache.
5. **Payload.**
   - `/api/heatmap` adds `tiers`, plus `profile_long_tiers` and `profile_short_tiers`: latest-column tiers × bins float32, base64.
   - The `heat` payload is unchanged.
6. **Page: liquidation map panel.**
   - The existing optional side bars stack by tier with one color per tier, and show a legend.
   - A cumulative long line runs from price downward and a cumulative short line from price upward.
   - It updates with every frame.
7. **Page: hover.** After about 120 ms on a cell, the crosshair tooltip fetches `/api/levels` and lists the tiers with USD, long and short. The last result is cached per cell.
8. **Page: player.** A row under the toolbar, enabled for Binance pair:
   - Start from the As-of field, then Load.
   - Step back, Play/Pause, step forward.
   - Speed: 1, 2, 5 or 10 bars per second.
   - A scrubber over the frames, and the current frame's time.
   - Space toggles play. The arrow keys step, when focus is not in an input.

   During replay:
   - The zoom and pan stay when the frame advances.
   - The page fetches up to two frames ahead.
   - Auto-refresh is paused.

   Playback stops on the last frame. Live leaves replay.

## Out of scope

OKX and multi-exchange replay, per-exchange charts, video or data export, and a model refit to 10x/25x/50x/100x (a follow-up).

## Checks

- **Offline tests:**
  - per-tier books sum to the totals at every column, for all three models;
  - `/api/levels` numbers equal the tier arrays at that cell;
  - span joining, with stubs:
    - the archive ends early and the live tail is joined with no duplicate or missing bar;
    - leading empty bars are trimmed;
  - a frame sliced from a span equals the direct time-travel series for the same `asof`, when OI is complete;
  - frames make no loader calls while the span is cached;
  - `/api/replay` refuses non-Binance and bars over the per-frame cap;
  - the payload carries the tier profiles.
- **Headless browser:** load a 2021 replay, play, pause, step, scrub, hover a cell and see the tier tooltip, Live. No page errors.
- **Full suite passes,** including `test_supply_chain.py`.

## Delivery

Branch `feat/replay` from `feat/time-travel`. Local feature commits. No push, no PR.

## Steps

1. Model: per-tier books and tests.
2. Data and service: span loader, slicing, `/api/replay`, `/api/levels`, payload tiers, tests.
3. Page: tier-stacked map panel, hover breakdown, player.
4. Full suite, headless browser check, commit.
