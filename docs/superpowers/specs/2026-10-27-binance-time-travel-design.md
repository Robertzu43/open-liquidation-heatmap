# Binance time travel

Approved 2026-10-27.

## Goal

Show the Binance heatmap for any USDT-M perpetual as it stood at any past moment, with range and bar size chosen independently. Free public data only. No account and no API key. This is the base for a later backtesting spec.

## Data

Verified on 2026-10-27 by listing the archive bucket:

- **Open interest, long/short and taker ratios:** `data.binance.vision` 5-minute metrics for BTCUSDT from 2020-09-01.
- **Candles:** at every interval, back to each contract's listing.
- **Archive lag:** daily files arrive a day or two late.
- **Live API:** `openInterestHist` serves only the last 30 days, with or without a key.

## Behavior

1. **API.** `GET /api/heatmap` gains two optional parameters:
   - `asof`: UTC ISO-8601 or unix seconds. Empty means live, as today.
   - `bar`: one of `5m`, `15m`, `1h`, `4h`, `12h`, `1d`. Empty means the range's current default bar.

   Both apply only to `exchange=binance` in pair mode. Any other exchange or mode that receives either parameter returns a `DataError` that says history is Binance-only.
2. **Window.**
   - The number of bars is `range_seconds / bar_seconds`. Below 2 or above 3,000, the request is refused with a message.
   - With `asof = T`, the last bar is the last one that has fully closed by T: `end_open = floor(T / bar) * bar - bar`.
   - No candle or open-interest row stamped at or after `end_open + bar` may reach the model.
   - A T in the future is treated as live.
3. **Source.**
   - **Live API:** used when the window starts within the last 30 days, the live API answers, and the window ends no later than now. The API is given explicit start and end times (`startTime`/`endTime`).
   - **Archive:** used otherwise. It reads kline files at the chosen interval and the 5-minute metrics files, and `fold_oi` maps the metrics onto bars.
   - **Archive ends early:** the existing stale warning names the archive's last bar.
   - **No data at all:** the error says the contract may not have been listed yet for that window.
4. **Local store.** The existing `.cache/` zip cache. Every archive file is downloaded at most once. No new storage layer.
5. **Coins.**
   - Any ticker that `normalize_symbol` accepts can be requested.
   - `/api/symbols` returns the trading USDT perpetuals from Binance's public `exchangeInfo` when it answers. Otherwise it returns today's 20 `COINS`.
   - The symbol list is cached for an hour.
6. **Page.**
   - An "As of (COT)" `datetime-local` field and a "Live" button that clears it.
   - A "Bar" select with "Auto" and the six bars.
   - Both are disabled unless Binance is selected in pair mode.
   - The as-of value is not saved to `localStorage`. The bar choice is saved.
   - A past view shows "As of <time> COT" in the banner.
7. **Cache.** The series cache key includes the resolved `end_open` and the bar. A window that ends before the live 30-day edge never changes, so it is kept for 24 hours.

## Out of scope

The backtester, mark-price sweeps, other venues, a bulk prefetch command, margin brackets, and persisting or sharing past views.

## Checks

- **Offline tests:**
  - window arithmetic: `end_open` for several T and bars, the 2 and 3,000 limits, and a future T;
  - no lookahead: stub loaders return rows past T, and the series must not contain them;
  - source selection between live and archive, with stubbed loaders;
  - a non-Binance exchange with `asof` raises `DataError`;
  - `/api/symbols` falls back when `exchangeInfo` fails.
- **One `network` test** (skipped unless `RUN_NETWORK=1`): BTCUSDT 24h at `asof=2021-05-19T23:59Z` on 15m bars loads at least 90 bars from the archive.
- **The full suite passes, including `test_supply_chain.py`.** No new hosts: `fapi.binance.com` and `data.binance.vision` are already allowed.

## Delivery

Branch `feat/time-travel` from `main`. Local feature commits. No push, no PR.
