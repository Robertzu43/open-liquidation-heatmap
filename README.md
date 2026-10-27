# Open Liquidation Heatmap

An independent estimate of where leveraged perpetual positions would liquidate. The program reads public market data from Binance, Bybit, and OKX. It is not affiliated with CoinGlass, and it does not use CoinGlass data.

This is a model, not a feed of exchange positions. It is not financial advice.

## heatmap.v1

Every run writes one document, `heatmap.v1`. The chart, the Streamlit app, and a later web client all read this document. Filters do not fetch again.

| Field | Meaning |
| --- | --- |
| `schema` | `heatmap.v1` |
| `model`, `model_version` | `oi_leverage_v1`, `1` |
| `label`, `disclaimer` | Shown on every chart |
| `axes.time` | Candle open times, ISO-8601 |
| `axes.price_edges`, `axes.price` | Bin edges and centers. The grid spans every estimated level, not a band around the last price |
| `axes.side`, `axes.leverage`, `axes.exchange` | Breakdown dimensions stored on each cell |
| `cells[]` | Sparse `{t, p, side, leverage, exchange, notional}` |
| `price_series[]` | `{time, close, mark, high, low}` for the same time axis |
| `metadata` | Symbol, window, assumptions, bracket version, notional placed, notional outside the grid, warnings |

`notional` is the estimated quote value still attributed to that level. Summing cells for one timestamp gives the open book. Summing across the whole window counts the same band again on later candles, which is what a heatmap is for.

A `--y-span` percent only hides rows in the picture. It does not delete cells.

## Model `oi_leverage_v1`

For each exchange, for each candle:

1. New positions are the increase in **contract count** (`sum_open_interest` on Binance, coin open interest on OKX, base-coin open interest on Bybit). The USD open-interest column is kept and is not differenced, so a price rally does not invent positions.
2. Those contracts are valued at the **same candle's** mark price when a mark print exists, otherwise at that candle's close. The next candle is not used.
3. The increase is split long/short and across a leverage ladder. The default split is 50/50. `--split metrics` blends public `count_long_short_ratio` and `sum_taker_long_short_vol_ratio` when both exist, and falls back to 50/50 when they do not. Bybit's public ratio is an account ratio, not taker volume, and the warning says so.
4. The default prior is log-normal over every integer leverage from 2x through the symbol maximum (125x on BTC in the bundled schedule), median 15x, sigma 0.55. It includes 25x. `--profile` also accepts `power`, `uniform`, and `classic`. `--ladder standard` keeps a shorter set of rungs. Passing `--leverages` hides rungs and does **not** renormalize the ones that remain.
5. Isolated liquidation prices, with maintenance rate `mmr` and maintenance amount `cum`:

    long:  `P = (entry * (1 - 1/L) - cum/qty) / (1 - mmr)`
    short: `P = (entry * (1 + 1/L) + cum/qty) / (1 + mmr)`

   `cum` is applied only when `--assumed-qty` is set. Otherwise it is 0. A long price at or above the entry is dropped. Brackets come from `data/brackets/binance_usdm_v1.json`, a partial snapshot of Binance's public leverage and margin page. The signed `GET /fapi/v1/leverageBracket` endpoint is not called. `--mmr-mode bracket` (the default) uses the first bracket, the lowest notional band, which is the tier for a typical position. BTC at 10x therefore uses that band's maintenance rate, 0.40% in the bundled snapshot: entry 100 liquidates near 90.3614 long and 109.5618 short. `--assumed-qty` selects the band that contains quantity times the entry price, and any rung above that band's maximum leverage is capped at the maximum. `--mmr-mode constant --mmr 0.005` forces the 0.50% check: entry 100 at 10x is about 90.4523 long and 109.4527 short.

6. A level stays on later candles until price trades through it or the book shrinks. An open-interest decrease scales every still-open level by `new_contracts / previous_contracts`. With fade on and `--decay 0` (the default), the sweep candle keeps full intensity and later candles drop that level. The entry candle does not sweep a level opened on it. `--age-half-life` is off unless you set it.

The dense price-by-time grid is capped at 150,000 cells. Bin width grows automatically until the product fits. Bin modes are `percent` (default, 0.25 means 0.25%), `absolute`, `tick`, and `log`.

## Windows

| Window | Default interval |
| --- | --- |
| 12h | 5m |
| 24h | 15m |
| 3d | 15m |
| 7d | 1h |
| 30d | 4h |
| 90d | 4h |

`36h` or `2d` also work. `2w`, `1m`, and a bare number are rejected.

## How to run

```bash
pip install -e .
python -m olh --symbol BTCUSDT --window 24h --exchanges okx
python -m olh --symbol BTCUSDT --window 7d --exchanges binance,okx
python heatmap.py --symbol BTCUSDT --interval 1h --lookback 3d
streamlit run streamlit_app.py
```

The default symbol is BTCUSDT. The default window is 24h. Outputs are `heatmap.png`, `heatmap.html`, and `heatmap.v1.json`. Each chart is titled "Independent estimate from public data" and carries the not-financial-advice disclaimer.

`--list-universe` prints `data/universe_top20.json`. That file is a point-in-time top 20 of USDT swaps by trailing 30-day quote volume. The helper that builds it ignores bars at or after the as-of timestamp. The Streamlit symbol list reads the snapshot. BTCUSDT stays the default view.

From a host where Binance or Bybit REST returns HTTP 451 or 403, that venue is skipped with a warning and the others still plot. Binance closed days are read from `data.binance.vision` (klines, mark-price klines, and metrics). The archive usually lags by about a day. REST is used only for the current UTC day.

## Data sources and history limits

- **Binance USD-M.** Vision daily klines, markPriceKlines, and metrics for closed days, with the SHA-256 sidecar when Binance publishes one. Files are cached as zip and parquet under `.cache/binance-vision/`. Metrics keep both `sum_open_interest` and `sum_open_interest_value`. REST open-interest history is about 30 days and is not required for closed days. `fapi.binance.com` often returns HTTP 451 from US hosts.
- **Bybit v5 linear.** `api.bybit.com`, then `api.bytick.com`. Open interest covers the symbol's life when the host answers. Both hosts often return HTTP 403 from US hosts. Klines have no taker-buy volume.
- **OKX swaps.** Public candles, mark-price candles, open-interest history, and contract taker volume (`[ts, sellVol, buyVol]`). Open-interest history keeps about the latest 1440 points, so a long window moves to a coarser bar and says so.

One venue failing does not stop the run. HTTP 429, 418, and 5xx retry with backoff 1s, 2s, 4s, 8s. HTTP 451 and 403 stop that venue immediately.

Symbol scope for the snapshot is the top 20 USDT perpetuals by trailing 30-day quote volume. The ranking function is `rank_usdt_perps`.

## Assumptions and limitations

- Exchanges do not publish who is long, at what leverage, or in which margin mode. The ladder, the 50/50 split, and the maintenance schedule are assumptions.
- The bundled bracket file is a partial public snapshot, shared across venues so the formula is comparable. It is not Bybit's risk-limit table or OKX's position tiers, and it is not the live signed Binance bracket endpoint.
- Without an assumed quantity, the model uses the lowest-notional maintenance tier and a maintenance amount of 0. Pass `--assumed-qty` when the position is large enough to sit in a higher band.
- Positions opened before the window are invisible. A later decrease scales only the book this run has been tracking.
- A printed open interest of 0 is treated as a missing print, not a full unwind.
- Binance vision lags about a day, so a 24h "now" view on a US host is often OKX alone.
- OKX cannot serve a fine bar across 30 or 90 days. The run coarsens and warns.
- Pre-window gaps clear the contract delta so a hole is not booked as new positions.
- Not financial advice.

## Layout

`src/olh/core` holds symbols, windows, and the shared labels. `exchanges` holds the three adapters. `infra` holds the HTTP client, pagination, the vision archive client, and the alignment helpers. `models` holds `oi_leverage_v1`. `features` holds binning, the long/short split, candle alignment, and the view over `heatmap.v1`.

Archive parsing and the alignment helpers were adapted from the author's earlier research code and republished here under this repository's MIT license.

## License

MIT. See [LICENSE](LICENSE).
