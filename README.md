# Open Liquidation Heatmap

An educational, open-source implementation of a crypto perpetual futures **liquidation heatmap** model.

This is **not** affiliated with CoinGlass. It does **not** scrape CoinGlass, reverse-engineer their private endpoints, or redistribute their data. It re-implements the *publicly described idea* of estimating liquidation clusters from open interest changes and assumed leverage distributions, using only public exchange APIs.

## What a Liquidation Heatmap Is

A liquidation heatmap visualizes estimated price levels where large amounts of leveraged positions would be force-closed.

- **Y-axis**: Price
- **X-axis**: Time
- **Color intensity**: Estimated density of liquidations at that price/time

Bright bands below current price ≈ clusters of longs that liquidate on a drop.
Bright bands above ≈ clusters of shorts that liquidate on a rise.

**Important**: This is a *model / estimate*, not actual exchange position data. Exchanges do not publish individual entry prices or leverage. The model assumes how open interest is distributed across leverage tiers.

## How the Model Works (Public Methodology)

Based on widely discussed approaches (see independent write-ups of CoinGlass-style maps):

1. Observe changes in aggregate Open Interest (OI) over time, along with price.
2. When OI increases, treat the increase as new positions opened around that price.
3. Distribute those new positions across common leverage levels (e.g. 10x, 20x, 50x, 100x), weighted more heavily toward lower leverage.
4. Calculate the approximate liquidation price for each assumed leverage:
   - Long: `entry * (1 - 1/leverage) / (1 - MMR)`
   - Short: `entry * (1 + 1/leverage) / (1 + MMR)`
   - MMR = maintenance margin rate (typically ~0.5% for BTC on major exchanges)
5. Bin the resulting liquidation notional into a price/time grid.
6. Optionally fade intensity as price later trades through a level (positions assumed cleared).

Different models use different leverage weightings and decay assumptions. This repo starts with a simple, transparent version you can modify.

## Data Sources

- Public Binance Futures endpoints (no API key required for the historical data used here):
  - Klines / candles
  - Open Interest history

You can extend it to Bybit, OKX, etc.

## Quick Start

```bash
pip install -r requirements.txt
python heatmap.py --symbol BTCUSDT --interval 1h --lookback 3d
```

This generates a heatmap image (`heatmap.png`) and saves the raw grid (`heatmap_grid.npz`).

## Limitations

- Estimate only — not real order book or position data.
- Assumes a leverage distribution that is not published by exchanges.
- Ignores funding rates, fees, cross vs isolated, and exact maintenance margin tiers.
- Different exchanges and different model assumptions produce different heatmaps.
- For trading decisions, treat it as one contextual signal among many, never as a precise target.

## License

MIT — use freely, modify, and improve. Contributions welcome.

## Disclaimer

This is for educational and research purposes only. Not financial advice. Crypto futures are high risk.
