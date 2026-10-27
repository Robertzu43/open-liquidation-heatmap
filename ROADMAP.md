# Roadmap

v1 (`oi_leverage_v1`) is the public demo: one JSON document, a static PNG, an HTML chart, and a Streamlit app. The items below are follow-up changes, not part of this version.

## Public demo host

- Deploy the Streamlit app on Streamlit Community Cloud or Hugging Face Spaces.
- The host is expected to be in the US, where Binance `fapi` and Bybit REST often return HTTP 451 or 403. The demo has to render from `data.binance.vision` plus OKX, and from live Binance or Bybit when those hosts answer.
- Cache archive files and recent REST pages on disk so a free-tier restart does not refetch a week of candles. The vision client already writes zip and parquet under `.cache/binance-vision/`.

## Donation placeholder

If a hosted demo grows a donation link, the address slot is `DONATION_ADDRESS_PLACEHOLDER`. No wallet address is checked in with this repository.

## Later models

- **Model 2.** A second `HeatmapModel` registered beside `oi_leverage_v1`. It should consume the same `heatmap.v1` document so the app does not grow a second file format. The leverage prior and the liquidation identity can change without changing the cube.
- **Model 3.** A stress view on the same cube: shift the maintained book by a funding or volatility scenario and write another `heatmap.v1` document rather than a private grid.
- **Liquidation recorder.** A separate process that stores public liquidation prints where an exchange documents them, then compares those prints with a `heatmap.v1` file after the fact. It is not part of the estimator and it does not belong in the hot path of the demo.

Each of those is its own change. This repository's v1 path does not call them.
