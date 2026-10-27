# Heatmap QA checklist

Manual pass for the Streamlit heatmap after the model rewrite is wired up. Run it against public Binance, Bybit, and OKX data. CoinGlass is out of scope: the page has no CoinGlass toggle, and the network panel shows no CoinGlass host.

A Streamlit Community Cloud app would run in the United States, so Binance and Bybit REST answer HTTP 451 or 403 from there. Not in this tree: a v0.1 page that shows live OKX plus the `data.binance.vision` archive. That is the intended demo after the app lands. Live Binance and Bybit need the planned non-US recorder. See `docs/DEPLOY.md`.

Use one liquid symbol with a known trend, **BTCUSDT** (OKX: **BTC-USDT-SWAP**), and repeat a shorter pass on a quieter alt if the app offers one. Record the symbol, window, leverage filter, and exchanges with every screenshot.

The model is an estimate from open interest and an assumed leverage distribution. These checks look for shape, side, and failure behavior. They do not ask the picture to match another vendor's heatmap.

## 1. App comes up

- [ ] The page loads without a traceback. The title names the symbol.
- [ ] A price series is visible on the heatmap (line or candles).
- [ ] The color scale has a legend with units or a label for estimated liquidation intensity.
- [ ] The first fetch shows a spinner or other progress, then settles. A second interaction with the same symbol and window does not start a long refetch (cache).
- [ ] The UI lists Binance, Bybit, and OKX. It does not list CoinGlass or any other exchange.
- [ ] Liquidation prices come from the `olh` package (`src/olh`) used by `streamlit_app.py`. The page does not contain a second formula. Neither file is on main yet.

## 2. Time windows

Check every window the control offers. Cover at least **12h, 24h, 3d, 7d, 30d, and 90d** when those choices exist.

- [ ] **12h**: bins are readable, timestamps match the last twelve hours, and the price path is the recent session.
- [ ] **24h**: the axis lengthens and earlier levels appear. Levels from the 12h view are still in the right place.
- [ ] **3d and 7d**: older columns stay time-ordered. The newest column lines up with the latest price.
- [ ] **30d**: history renders or the page explains that the venue's public history is shorter.
- [ ] **90d**: same rule. Binance public open-interest history is about a month, so a 90-day Binance view should say the retained range instead of drawing an empty or crashed chart.
- [ ] Switching windows does not leave a stale color scale from the previous window (a 12h spike should not lock the 30d scale).
- [ ] An in-progress window change can be cancelled by picking another window without stacking errors.

## 3. Leverage filter

The ladder runs from **2x through 125x** (2, 3, 5, 10, 20, 25, 50, 75, 100, 125, or the subset the control exposes).

- [ ] **All leverages**: long intensity is below the price path and short intensity is above it.
- [ ] **2x only**: bands sit far from the current price.
- [ ] **10x and 25x**: bands sit between the 2x bands and the price.
- [ ] **125x only**: bands sit closest to the price, still with longs below and shorts above.
- [ ] Turning a single leverage off removes that band and leaves the others in place.
- [ ] The picture does not flip sides when the filter changes. High leverage never draws a long liquidation above entry.

## 4. Compare with price action

- [ ] A selloff whose low trades through a bright long band fades that band on later columns. The same band is still visible in columns from before the sweep.
- [ ] A rally whose high trades through a bright short band fades that band afterward.
- [ ] A level the price has not touched keeps its intensity across later columns.
- [ ] A wick counts as a sweep when its low (long) or high (short) touches the level. The close does not have to settle beyond it.
- [ ] Fading removes intensity. It does not move that intensity into a neighboring price bin.
- [ ] The brightest remaining band is a plausible cluster (round leverage, recent open-interest increase), not a single-pixel speckle from one bad print.

## 5. Exchange toggles

- [ ] **Binance only**, **Bybit only**, and **OKX only** each produce a chart, or a clear message when that venue returns nothing.
- [ ] **All three**: intensity adds. A bin that is bright on one venue only gets brighter or stays put; it does not jump to another price.
- [ ] Turning one venue off dims or removes only that venue's contribution. Spot-check one bright bin before and after.
- [ ] **None selected**: empty state or a prompt to pick an exchange. The page does not throw.
- [ ] Symbol naming follows the venue (BTCUSDT on Binance and Bybit, BTC-USDT-SWAP on OKX) without requiring the user to retype it when they only toggle the venue.

## 6. Failure handling

- [ ] An unknown symbol shows an error that names the symbol. The sidebar still accepts a new symbol.
- [ ] One venue failing (timeout, HTTP 429, HTTP 451, or empty body) names that venue. The other venues still plot. HTTP 451 from Binance, or HTTP 403 from Bybit, on Community Cloud is the US hosting limit (`docs/DEPLOY.md`), not a plotting bug. The page keeps OKX and, for Binance history, `data.binance.vision`.
- [ ] If Binance REST is geo-blocked, historical klines may come from `data.binance.vision`. That series is still Binance. The page does not substitute a different exchange.
- [ ] A rate-limit response backs off. The UI does not sit in a spinner that never ends.
- [ ] After an error, changing the window or the leverage filter works. The app is not stuck on the failed request.
- [ ] Very small windows and the longest window either render or explain why history is missing. They do not return a partial grid with silent zeros and no message.
- [ ] Browser refresh returns to a usable default (symbol, window, and at least one exchange).

## 7. Sign-off notes

Write down the git revision, the three venue statuses, and any window that was shorter than requested because the exchange history ended. Attach the 12h, 7d, and 90d screenshots at 125x and at 2x.
