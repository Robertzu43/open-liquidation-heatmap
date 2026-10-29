#!/usr/bin/env python3
"""Draw an estimated liquidation heatmap from public exchange data.

The picture is produced by liqmap.model. This script only fetches a series and
renders it. ``--lookback`` is accepted so older commands keep working.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from liqmap.data import DataError, exchange_name, load_market
from liqmap.model import bin_intensity, build_heatmap, fade_swept, liq_price, liq_price_long, liq_price_short

__all__ = [
    "bin_intensity",
    "fade_swept",
    "liq_price",
    "liq_price_long",
    "liq_price_short",
    "plot_heatmap",
]

LOOKBACK_TO_RANGE = {
    "12h": "12h",
    "24h": "24h",
    "1d": "24h",
    "2d": "3d",
    "3d": "3d",
    "7d": "7d",
    "14d": "30d",
    "30d": "30d",
    "90d": "90d",
    "180d": "180d",
    "1y": "1y",
    "2y": "2y",
}

CMAP = LinearSegmentedColormap.from_list(
    "openliq",
    ["#160e22", "#5c2478", "#c45a48", "#f0b429", "#fff1a8"],
)


def _resolve_range(args: argparse.Namespace) -> str:
    if args.range:
        return args.range
    if args.lookback:
        key = args.lookback.strip().lower()
        mapped = LOOKBACK_TO_RANGE.get(key)
        if mapped is None:
            known = ", ".join(LOOKBACK_TO_RANGE)
            raise DataError(f"Unknown --lookback '{args.lookback}'. Use one of: {known}.")
        return mapped
    return "24h"


def plot_heatmap(heat, centers, times_ms, open_, high, low, close, title: str, outfile: str) -> None:
    grid = np.asarray(heat, dtype=np.float64)
    positive = grid[grid > 0]
    vmax = float(np.percentile(positive, 99)) if positive.size else 1.0
    if not np.isfinite(vmax) or vmax <= 0:
        vmax = 1.0
    n = grid.shape[1]
    fig, ax = plt.subplots(figsize=(14, 8), dpi=140, facecolor="#12140f")
    ax.set_facecolor("#12140f")
    image = ax.imshow(
        grid,
        origin="lower",
        aspect="auto",
        cmap=CMAP,
        vmin=0,
        vmax=vmax,
        extent=(-0.5, n - 0.5, float(centers[0]), float(centers[-1])),
        interpolation="nearest",
    )
    xs = np.arange(n)
    for i in range(n):
        up = close[i] >= open_[i]
        color = "#7dcea0" if up else "#e07a72"
        ax.plot([xs[i], xs[i]], [low[i], high[i]], color=color, linewidth=0.6, solid_capstyle="round")
        body_low = min(open_[i], close[i])
        body_high = max(open_[i], close[i])
        if body_high == body_low:
            body_high = body_low * 1.00005
        ax.plot([xs[i], xs[i]], [body_low, body_high], color=color, linewidth=2.2, solid_capstyle="butt")
    ticks = np.linspace(0, n - 1, num=min(8, n), dtype=int)
    labels = []
    for index in ticks:
        stamp = datetime.fromtimestamp(int(times_ms[index]) / 1000, tz=timezone.utc)
        labels.append(stamp.strftime("%m-%d %H:%M"))
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=0, color="#c5cbb8")
    ax.tick_params(colors="#c5cbb8")
    ax.set_ylabel("Price", color="#e7ecdf")
    ax.set_xlabel("UTC", color="#c5cbb8")
    ax.set_title(title, color="#e7ecdf", pad=12)
    for spine in ax.spines.values():
        spine.set_color("#2c3126")
    bar = fig.colorbar(image, ax=ax, pad=0.01)
    bar.set_label("Estimated liquidity (USD, relative)", color="#c5cbb8")
    bar.ax.yaxis.set_tick_params(color="#c5cbb8")
    plt.setp(bar.ax.yaxis.get_ticklabels(), color="#c5cbb8")
    fig.text(
        0.01,
        0.01,
        "Model estimate from open interest and an assumed leverage mix. Not exchange liquidation records.",
        color="#9aa48e",
        fontsize=8,
    )
    fig.tight_layout()
    fig.savefig(outfile, dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open liquidation heatmap from public exchange data")
    parser.add_argument("--exchange", default="okx", help="okx, binance, bybit, demo, or aggregate")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--range", default=None, help="12h, 24h, 3d, 7d, 30d, 90d, 180d, 1y, 2y")
    parser.add_argument("--lookback", default=None, help="Older alias for --range (1d maps to 24h)")
    parser.add_argument("--model", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument("--mode", default="pair", choices=("pair", "symbol"))
    parser.add_argument("--outfile", default="heatmap.png")
    parser.add_argument("--interval", default=None, help="Ignored. Bar size follows --range.")
    parser.add_argument("--oi-period", default=None, help="Ignored. Open interest uses the range bar size.")
    args = parser.parse_args(argv)
    try:
        range_key = _resolve_range(args)
        if args.interval or args.oi_period:
            print("Bar size follows the selected range; --interval and --oi-period are ignored.", file=sys.stderr)
        if args.range and args.lookback:
            print("--range wins over --lookback.", file=sys.stderr)
        loaded = load_market(args.exchange, args.symbol, range_key, args.mode)
        heatmap = build_heatmap(loaded.series, args.model)
        series = loaded.series
        title = f"{exchange_name(series.exchange)} {series.coin}/USDT liquidation heatmap · model {args.model}"
        if series.exchange == "aggregate":
            title = f"{series.coin}/USDT liquidation heatmap · model {args.model}"
        plot_heatmap(
            heatmap.long_heat + heatmap.short_heat,
            heatmap.centers,
            series.times,
            series.open,
            series.high,
            series.low,
            series.close,
            title,
            args.outfile,
        )
    except (DataError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for warning in loaded.series.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"Saved {args.outfile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
