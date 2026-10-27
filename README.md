# Open Liquidation Heatmap

An open estimate of a crypto perpetual **liquidation heatmap**. Bright shelves are prices where this model thinks leveraged positions would be forced out. The picture is built from public open interest and an assumed leverage mix.

This project is **not** affiliated with CoinGlass. It does not scrape CoinGlass, call their private heatmap APIs, copy their code, or redistribute their data. The leverage split of Models 1 and 2 was calibrated by eye against screenshots of their rendered chart; see [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#the-weights-are-the-model).

Not financial advice. A shelf is a model, not a print of exchange positions.

## Run it

```bash
pip install -r requirements.txt
python -m liqmap.server
```

Open http://127.0.0.1:8765 . The page defaults to a live OKX BTC/USDT pair, 24 hour window, model 1.

Save a still image from the command line (matplotlib Agg backend, no window required):

```bash
python heatmap.py --exchange okx --symbol BTCUSDT --range 24h --model 1 --outfile heatmap.png
```

`--lookback` still works for older commands (`1d` maps to `24h`). `--range` wins if both are set. `--interval` is accepted and ignored, because each range picks its own bar size.

Offline, with no exchange calls:

```bash
python heatmap.py --exchange demo --symbol ETHUSDT --range 7d --model 2
```

## What the chart shows

| Control | Behavior |
| --- | --- |
| Model 1 / 2 / 3 | Three published leverage mixes. Model 1 is the default. Switching models reuses the cached series. |
| Pair / Symbol | Pair is one venue. Symbol sums open interest across venues that are still fresh. |
| Exchange | OKX, Binance, or Demo. |
| Range | 12 hour, 24 hour, 3 day, 7 day, 30 day, 90 day, 180 day, 1 Year, 2 year. |
| Refresh | Fetches again. |
| Snapshot | Downloads a PNG of the canvas. |
| Fullscreen | The chart card. |
| Four color themes | Ember (purple to yellow), Tide, Magma, Glacier. Ember is the default. |
| Liquidity threshold | Default 0.85. Client-side only. It compresses the color ramp so weak cells stay dark and shelves stay bright. It does not blank the chart. |
| Liquidation map | Optional horizontal bars on the right. Green is resting long liquidity, red is resting short liquidity. Off by default. |
| Candles | Green up, red down, drawn on the heat. |
| Axes | USD color scale on the left, price on the right, time along the bottom. Times are America/Bogota, labeled COT (UTC−5). |
| Crosshair | Price, time, estimated liquidity, and the candle. |
| Zoom / pan | Wheel zooms price, shift-wheel zooms time, drag pans, double-click resets. Fit uses the traded range. Wide shows the full model price domain. |
| Analysis | Summary, nearest short shelf above, nearest long shelf below, 1/2/5/10% imbalance, and a level table. |
| Auto-refresh | Off, 30s, 1m, or 5m. Paused while you look at a past moment. |
| As of / Bar | Binance pair only. Pick any past moment since 2020-09-01 (COT) and any bar from 5m to 1d, up to 3,000 bars per view. "Live" returns to now. |
| Replay | Binance pair only. Set As of (and optionally To), then Load replay. Step, play at up to 1/2/5/10 bars per second, or scrub. Each frame is the same map the page shows for that moment. The first load of a long span downloads one archive file per day. Space plays or pauses; the arrow keys step. |
| Leverage | Hover any cell to see which leverage tiers make up its estimate. With Liquidation map on, the side bars stack by tier, with cumulative long and short lines from the current price. |

Settings are stored in `localStorage`.

## How the model works

Exchanges do not publish each account's entry or leverage. `liqmap/model.py` is the whole model:

1. Match each open-interest print to the candle whose open time is nearest that print. Ten minutes after an hourly open stays on that candle. Fifty minutes after that open uses the next candle. The entry is that bar's close.
2. Spread that notional across a leverage mix. The weights in each model sum to 1.
3. Park it at an isolated liquidation price. The zero-tier maintenance margin rate (MMR) is **0.005** (0.5%). Entry 100 at 10x is 90.4523 long and 109.4527 short.

```
long  = entry * (1 - 1/leverage) / (1 - MMR)
short = entry * (1 + 1/leverage) / (1 + MMR)
```

4. A small kernel blurs each deposit across neighboring price bins so a tier reads as a shelf, not a one-pixel line.
5. When a later candle trades through a liquidation price, that resting liquidity is removed. Longs below the bar's low survive. Shorts above the bar's high survive.
6. Color is the resting notional at that price and time. The scale is relative to the window.

| Model | Idea | Weights |
| --- | --- | --- |
| 1 Wide clusters | Most weight on 50×–60×, with a far 5× tier. Shelves sit about 1–2% from price. No fade. | 5× 12%, 20× 10%, 50× 25%, 60× 53% |
| 2 Tight magnets | Two tiers, 20× and 60×, a little volume churn, and a slow fade. | 20× 30%, 60× 70% |
| 3 Flow skew | Same family of tiers, but new positions follow taker buy/sell when that data exists. | 10× 15%, 20× 20%, 25× 20%, 50× 25%, 100× 20% |

Models 1 and 2 split new open interest evenly between longs and shorts. Model 3 uses the taker buy fraction as the long share.

The same description is in the chart's "How this model works" disclosure, read from the model table so the page cannot drift from the code.

## Data sources

Only public market data. No API key.

| Venue | What we use |
| --- | --- |
| OKX | Swap candles, open-interest history, and taker volume for `COIN-USDT-SWAP`. This is the default because it is live. |
| Binance | Live USDT-M klines and open interest when `fapi.binance.com` answers. If it does not (some networks get HTTP 451), the public [data.binance.vision](https://data.binance.vision) archive is used instead. Archive zips are cached under `.cache/`. The archive lags the live market by a day or two; the page shows an as-of banner in COT. |
| Demo | A seeded synthetic series. No network. |
| Symbol mode | OKX and Binance are fetched together. Price candles come from the freshest venue. Open interest is the sum. A stale Binance archive is left out, and the banner says so. |

Symbols are normalized (`btc`, `BTCUSDT`, and `BTC-USDT` all mean BTC) and must match a short ticker pattern before they are placed in a URL.

Bar size by range:

| Range | Bar |
| --- | --- |
| 12h, 24h | 5m |
| 3d | 15m |
| 7d | 1h |
| 30d, 90d | 4h |
| 180d | 12h |
| 1y, 2y | 1d |

Series for the short ranges are cached briefly. Longer ranges are cached longer. The model run itself is not cached as a substitute for the series, so changing model 1 to model 2 does not download the market again.

### HTTP API

- `GET /api/heatmap?exchange&symbol&range&model&mode` — heatmap JSON. Price grids are little-endian float32, base64. `heat` is row-major with the price bin changing slowest.
  - `asof` (Binance pair only): unix seconds, or ISO-8601 with a zone. The map ends at the last bar that had closed by then, and nothing after it is read. Older windows come from the public archive, cached under `.cache/`, so a period is downloaded once.
  - `bar` (Binance pair only): `5m`, `15m`, `1h`, `4h`, `12h`, or `1d`. Empty keeps the range's default.
- `GET /api/replay?symbol&range&bar&from&to` — load a replay span once (Binance pair). Frames are `/api/heatmap` with `asof` and `replay=1`. They never call an exchange, and a frame whose span has expired answers 409.
- `GET /api/levels?<map query>&t&price` — long and short USD per leverage tier at one bar open and price.
- `GET /api/status` — models, ranges, default view.
- `GET /api/symbols` — the coin list and venues.

The server is the Python standard library `ThreadingHTTPServer` on `0.0.0.0:8765`. JSON responses are gzip-compressed when the client asks.

## Tests

```bash
python -m unittest tests.test_model -v
```

The tests check leverage weights, liquidation-price direction, that a bar which cannot reach the shelves keeps the deposited USD, that a later candle clears shorts after trading through them, that model 3 follows taker skew, and that the shelf finder sees a level above and below price. Parser and combine tests do not touch the network.

## Limitations

- This is an estimate. It is not the exchange's liquidation engine and not anyone else's heatmap.
- The leverage mix, the 0.5% zero-tier MMR, and the even long/short split (models 1 and 2) are assumptions.
- Cross margin, tiered maintenance, fees, and funding are available on `heatmap.liq_price` and are not the default map.
- Binance from this kind of hosted network often cannot use the live futures API. The archive is delayed. Symbol mode drops that delayed book rather than painting yesterday's open interest on today's price.
- Different venues and different models produce different shelves. Treat the map as context, not a target.

## License

MIT. See [LICENSE](LICENSE).
