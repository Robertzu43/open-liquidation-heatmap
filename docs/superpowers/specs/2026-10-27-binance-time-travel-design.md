# Binance time travel

Approved 2026-10-27. Revised the same day after an independent review.

## Goal

Show the Binance heatmap for any USDT-M perpetual as it stood at any past moment, with range and bar size chosen independently. Free public data only. No account and no API key. This is the base for a later backtesting spec.

## Data

Verified on 2026-10-27 by listing the archive bucket:

- **Open interest, long/short and taker ratios:** `data.binance.vision` 5-minute metrics for BTCUSDT from 2020-09-01. They are published as daily files only.
- **Candles:** at every interval, back to each contract's listing.
- **Archive lag:** daily files arrive a day or two late.
- **Live API:** `openInterestHist` serves only the last 30 days, with or without a key.

## Behavior

1. **API.** `GET /api/heatmap` gains two optional parameters:
   - `asof`: unix seconds, or ISO-8601 with `Z` or an offset. A time without a zone is refused. Empty means live, as today.
   - `bar`: one of `5m`, `15m`, `1h`, `4h`, `12h`, `1d`. Empty means the range's current default bar.

   Both apply only to `exchange=binance` in pair mode. Any other exchange or mode that receives either parameter returns a `DataError` that says history is Binance-only.
2. **Window.**
   - The number of bars is `range_seconds / bar_seconds`. Below 2 or above 3,000, the request is refused with a message.
   - With `asof = T`, the last bar is the last one that has fully closed by T: `end_open = floor(T / bar) * bar - bar`.
   - A T in the future is treated as live.
   - A window that ends before 2020-09-01 is refused, with a message naming the 2020-09-01 start of the metrics. A window that only partly covers the metrics gets the existing coverage note.
3. **The window is anchored to T.**
   - Every Binance loader takes `end_open` as an argument. With `asof`, nothing calls `time.time()` to place the window.
   - The archive path skips `_latest_archive_day`, so a delisted contract still loads.
4. **No lookahead.**
   - The folds already drop rows past the grid.
   - Before `fold_oi`, archive metrics keep only prints stamped exactly at a bar open (`ts % bar_ms == 0`). Live and archive open interest then mean the same thing: open interest at the bar open.
   - No candle or open-interest row stamped at or after `end_open + bar` may reach the model.
5. **Source, one per view.**
   - With `asof`, use the archive.
   - Use the live API instead only when the archive ends before `end_open` and the window starts after now − 29 days.
   - No stitching of the two sources.
   - Live klines page through `startTime` as the OI loop does, so a window over 1,500 bars is complete.
6. **Staleness.**
   - With `asof`, `finalize_series` gets `now_s = end_open + bar`.
   - A past view is stale only when the archive ends before `end_open`.
   - Its note reads "Historical view from the Binance archive", not "live futures data was unavailable".
7. **Local store.** The existing `.cache/` zip cache. Every archive file is downloaded at most once. No new storage layer.
8. **Coins.** Any ticker that `normalize_symbol` accepts can be typed. The existing datalist stays a suggestion list. No `exchangeInfo` call.
9. **Page.**
   - An "As of (COT)" `datetime-local` field, converted from UTC−5 to unix seconds in the page, and a "Live" button that clears it.
   - A "Bar" select with "Auto" and the six bars.
   - Both are disabled unless Binance is selected in pair mode. While disabled they are left out of the request.
   - If the range and bar give fewer than 2 or more than 3,000 bars, the bar resets to Auto.
   - The as-of value is not saved. The bar choice is saved.
   - Auto-refresh pauses while `asof` is set.
   - `identityChanged` includes `bar` and `asof`.
   - A past view's banner shows "As of <time> COT".
10. **Cache.**
    - The series cache key is `(exchange, coin, range, mode, bar, end_open or None for live)`.
    - The per-range TTLs stay.
    - Expired entries, and their locks, are removed on insert.
    - `load_heatmap` reports the bar actually used.

## Out of scope

The backtester, mark-price sweeps, other venues, a bulk prefetch command, margin brackets, a symbol list from `exchangeInfo`, and persisting or sharing past views.

## Checks

- **Offline tests:**
  - window arithmetic: `end_open` for several T and bars, the 2 and 3,000 limits, a future T, and the pre-2020-09-01 refusal;
  - no lookahead: stub archive loaders return candles and metrics past T, and the series must not contain them;
  - the open-only metrics filter;
  - source selection, with stubs: `asof` uses the archive, a short archive within 29 days uses live, and a short archive older than that is stale;
  - a non-Binance exchange with `asof` or `bar` raises `DataError`;
  - live kline paging past 1,500 rows;
  - past views are not marked stale;
  - cache eviction and the key.
- **One `network` test** (skipped unless `RUN_NETWORK=1`): BTCUSDT 24h at `asof=2021-05-19T23:59:59Z` on 15m bars loads at least 90 bars from the archive.
- **The full suite passes, including `test_supply_chain.py`.** No new hosts.

## Delivery

Branch `feat/time-travel` from `main`. Local feature commits. No push, no PR.

## Steps

1. `data.py`: `end_open` through the loaders, the `asof` and `bar` spec, the open-only filter, kline paging, the source rule, staleness. Tests first.
2. `service.py` and `server.py`: parameters, cache key and eviction, the bar reported. Tests.
3. `web/`: controls, request, auto-refresh pause, banner.
4. Full suite, a manual check through the server on a 2021 window, then commit.
