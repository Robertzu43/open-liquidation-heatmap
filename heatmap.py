#!/usr/bin/env python3
"""
Open-source liquidation heatmap model.
Uses public Binance Futures data only.
Educational implementation of the commonly described OI + assumed-leverage approach.
"""

import argparse
import time
from datetime import datetime, timedelta, timezone

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

BINANCE_FAPI = "https://fapi.binance.com"

# Common leverage tiers and relative weights (lower leverage more common)
LEVERAGE_WEIGHTS = {
    10: 0.40,
    20: 0.30,
    50: 0.20,
    100: 0.10,
}
MMR = 0.005  # 0.5% maintenance margin rate (adjust per exchange/tier)


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int):
    """Fetch klines from Binance Futures."""
    url = f"{BINANCE_FAPI}/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1500,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["open_time", "open", "high", "low", "close", "volume"]]


def fetch_oi_hist(symbol: str, period: str, start_ms: int, end_ms: int):
    """Fetch open interest history. period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d"""
    url = f"{BINANCE_FAPI}/futures/data/openInterestHist"
    params = {
        "symbol": symbol,
        "period": period,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 500,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["sumOpenInterest"] = pd.to_numeric(df["sumOpenInterest"])
    df["sumOpenInterestValue"] = pd.to_numeric(df["sumOpenInterestValue"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df[["timestamp", "sumOpenInterest", "sumOpenInterestValue"]]


def liq_price_long(entry: float, leverage: float, mmr: float = MMR) -> float:
    # Approximate isolated long liquidation price
    return entry * (1 - 1 / leverage) / (1 - mmr)


def liq_price_short(entry: float, leverage: float, mmr: float = MMR) -> float:
    return entry * (1 + 1 / leverage) / (1 + mmr)


def build_heatmap(price_df: pd.DataFrame, oi_df: pd.DataFrame, price_bins: int = 100):
    """
    Simple model: for each OI increase, distribute notional across leverage tiers
    and accumulate at the corresponding liquidation prices.
    """
    if price_df.empty or oi_df.empty:
        return None, None, None

    # Align roughly on time
    oi_df = oi_df.sort_values("timestamp")
    oi_df["oi_change"] = oi_df["sumOpenInterestValue"].diff().fillna(0)

    # Price range for bins
    min_p = price_df["low"].min() * 0.95
    max_p = price_df["high"].max() * 1.05
    price_edges = np.linspace(min_p, max_p, price_bins + 1)
    price_centers = (price_edges[:-1] + price_edges[1:]) / 2

    # Time bins from price data
    times = price_df["open_time"].values
    heat = np.zeros((price_bins, len(times)))

    for _, row in oi_df.iterrows():
        delta = row["oi_change"]
        if delta <= 0:
            continue
        # Approximate entry price as nearest close
        ts = row["timestamp"]
        nearest = price_df.iloc[(price_df["open_time"] - ts).abs().argsort()[:1]]
        if nearest.empty:
            continue
        entry = nearest["close"].iloc[0]

        for lev, weight in LEVERAGE_WEIGHTS.items():
            notional = delta * weight
            # Longs liquidate below
            lp_long = liq_price_long(entry, lev)
            # Shorts liquidate above (assume half long / half short for simplicity)
            lp_short = liq_price_short(entry, lev)

            for lp, half in [(lp_long, notional / 2), (lp_short, notional / 2)]:
                if min_p <= lp <= max_p:
                    bin_idx = np.searchsorted(price_edges, lp) - 1
                    bin_idx = max(0, min(price_bins - 1, bin_idx))
                    # Add to the time column nearest to this OI timestamp
                    t_idx = np.argmin(np.abs(times - np.datetime64(ts)))
                    heat[bin_idx, t_idx] += half

    return heat, price_centers, times


def plot_heatmap(heat, price_centers, times, symbol, outfile="heatmap.png"):
    fig, ax = plt.subplots(figsize=(14, 8))
    # Use log or just intensity; clip for visibility
    vmax = np.percentile(heat[heat > 0], 95) if np.any(heat > 0) else 1
    im = ax.imshow(
        heat,
        aspect="auto",
        origin="lower",
        cmap="magma",
        extent=[0, len(times) - 1, price_centers[0], price_centers[-1]],
        vmin=0,
        vmax=vmax,
    )
    ax.set_ylabel("Price")
    ax.set_xlabel("Time")
    ax.set_title(f"{symbol} Estimated Liquidation Heatmap (Open Model)")
    plt.colorbar(im, ax=ax, label="Estimated Liquidation Notional")
    # Simple time labels
    tick_idx = np.linspace(0, len(times) - 1, 8, dtype=int)
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([pd.Timestamp(times[i]).strftime("%m-%d %H:%M") for i in tick_idx], rotation=45)
    plt.tight_layout()
    plt.savefig(outfile, dpi=150)
    print(f"Saved {outfile}")
    return outfile


def main():
    parser = argparse.ArgumentParser(description="Open liquidation heatmap")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h", help="Kline interval")
    parser.add_argument("--oi-period", default="1h", help="OI period")
    parser.add_argument("--lookback", default="3d", help="e.g. 12h, 1d, 3d, 7d")
    parser.add_argument("--outfile", default="heatmap.png")
    args = parser.parse_args()

    # Parse lookback
    unit = args.lookback[-1]
    val = int(args.lookback[:-1])
    if unit == "h":
        delta = timedelta(hours=val)
    elif unit == "d":
        delta = timedelta(days=val)
    else:
        delta = timedelta(days=3)

    end = datetime.now(timezone.utc)
    start = end - delta
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    print(f"Fetching data for {args.symbol} from {start} to {end}...")
    price_df = fetch_klines(args.symbol, args.interval, start_ms, end_ms)
    oi_df = fetch_oi_hist(args.symbol, args.oi_period, start_ms, end_ms)

    print(f"Price rows: {len(price_df)}, OI rows: {len(oi_df)}")
    if price_df.empty or oi_df.empty:
        print("No data returned. Check symbol or time range.")
        return

    heat, centers, times = build_heatmap(price_df, oi_df)
    if heat is not None:
        plot_heatmap(heat, centers, times, args.symbol, args.outfile)
        # Optional: save grid
        np.savez("heatmap_grid.npz", heat=heat, prices=centers, times=times)
        print("Also saved heatmap_grid.npz")


if __name__ == "__main__":
    main()
