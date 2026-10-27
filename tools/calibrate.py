#!/usr/bin/env python3
"""Fit leverage weights to reference heatmap screenshots.

Each manifest names a screenshot and where its plot, price axis, and color
bar sit, in image pixels. The tool reads the color of a strip of columns at
the right edge back into USD per price row, rebuilds this repository's model
for the same venue and window, and fits non-negative leverage weights. The
resting profile is linear in the weights, so the fit is a non-negative least
squares problem over one profile per candidate leverage.

    python tools/calibrate.py calibration/*.json

Manifest fields: image, exchange, symbol, range, end_utc (ISO time of the
last bar open), plot {top, bottom}, price {top, bottom}, colorbar {x, top,
bottom, max_usd}, columns [x0, x1]. Screenshots and manifests stay out of git.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

import matplotlib.image as mpimg
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from liqmap import data, model  # noqa: E402

CANDIDATES = (2, 3, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 125)
COLOR_TOLERANCE = 30.0


def read_screenshot(m: dict, base: Path) -> tuple[np.ndarray, np.ndarray]:
    """Price and USD per image row, NaN where no pixel matches the color bar."""
    img = mpimg.imread(base / m["image"])[..., :3].astype(float)
    if img.max() <= 1.0:
        img *= 255.0
    cb = m["colorbar"]
    bar = img[cb["top"] : cb["bottom"] + 1, cb["x"]]
    bar_usd = np.linspace(float(cb["max_usd"]), 0.0, len(bar))
    top, bottom = m["plot"]["top"], m["plot"]["bottom"]
    x0, x1 = m["columns"]
    rows = np.arange(top, bottom + 1)
    usd = np.full(len(rows), np.nan)
    for k, y in enumerate(rows):
        px = img[y, x0 : x1 + 1]
        dist = ((px[:, None, :] - bar[None, :, :]) ** 2).sum(-1)
        best = dist.argmin(1)
        ok = dist[np.arange(len(px)), best] <= COLOR_TOLERANCE**2
        if ok.any():
            usd[k] = float(np.median(bar_usd[best[ok]]))
    p_top, p_bot = float(m["price"]["top"]), float(m["price"]["bottom"])
    prices = p_top + (rows - top) / (bottom - top) * (p_bot - p_top)
    return prices, usd


def load_series(m: dict):
    end = datetime.fromisoformat(m["end_utc"]).timestamp()
    with mock.patch.object(data.time, "time", return_value=end + 60):
        return data.load_market(m["exchange"], m["symbol"], m["range"], m.get("mode", "pair")).series


def target_on_bins(centers: np.ndarray, prices: np.ndarray, usd: np.ndarray) -> np.ndarray:
    width = centers[1] - centers[0]
    out = np.full(len(centers), np.nan)
    lo, hi = np.nanmin(prices), np.nanmax(prices)
    for i, c in enumerate(centers):
        if c - width / 2 < lo or c + width / 2 > hi:
            continue
        inside = np.abs(prices - c) <= width / 2
        vals = usd[inside]
        if np.isfinite(vals).any():
            out[i] = float(np.nanmean(vals))
    return out


def tier_profiles(series, model_id: int) -> tuple[np.ndarray, np.ndarray]:
    base = dict(model.MODELS[model_id])
    cols = []
    centers = None
    for lev in CANDIDATES:
        model.MODELS[0] = {**base, "id": 0, "weights": ((lev, 1.0),)}
        try:
            heat = model.build_heatmap(series, 0)
        finally:
            del model.MODELS[0]
        centers = heat.centers
        cols.append(heat.profile_long + heat.profile_short)
    return centers, np.stack(cols, axis=1)


def nnls(a: np.ndarray, b: np.ndarray, iters: int = 20000) -> np.ndarray:
    # Lee-Seung multiplicative updates. a and b are non-negative here.
    x = np.full(a.shape[1], max(float(b.mean()), 1e-9) / max(float(a.mean()), 1e-9) / a.shape[1])
    atb = a.T @ b
    ata = a.T @ a
    for _ in range(iters):
        x *= atb / np.maximum(ata @ x, 1e-12)
    return x


def peaks(centers: np.ndarray, values: np.ndarray, floor: float) -> list[tuple[float, float]]:
    out = []
    for i in range(1, len(values) - 1):
        v = values[i]
        if np.isfinite(v) and v >= floor and v >= np.nan_to_num(values[i - 1]) and v > np.nan_to_num(values[i + 1]):
            out.append((float(centers[i]), float(v)))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifests", nargs="+")
    args = parser.parse_args(argv)
    shots = []
    for path in args.manifests:
        p = Path(path)
        m = json.loads(p.read_text())
        prices, usd = read_screenshot(m, p.parent)
        shots.append((p.name, m, prices, usd, load_series(m)))
        print(f"{p.name}: {np.isfinite(usd).sum()} of {len(usd)} rows read, max {np.nanmax(usd)/1e6:.2f}M")

    fitted = {}
    for model_id in sorted(model.MODELS):
        blocks_a, blocks_b, current = [], [], []
        for name, m, prices, usd, series in shots:
            centers, a = tier_profiles(series, model_id)
            b = target_on_bins(centers, prices, usd)
            keep = np.isfinite(b)
            blocks_a.append(a[keep])
            blocks_b.append(b[keep])
            current.append(model.build_heatmap(series, model_id))
        a = np.vstack(blocks_a)
        b = np.concatenate(blocks_b)
        x = nnls(a, b)
        fit = a @ x
        cur = np.concatenate([
            (h.profile_long + h.profile_short)[np.isfinite(target_on_bins(h.centers, s[2], s[3]))]
            for h, s in zip(current, shots)
        ])
        corr_before = float(np.corrcoef(cur, b)[0, 1])
        corr_after = float(np.corrcoef(fit, b)[0, 1])
        scale = float(x.sum())
        weights = {lev: float(w / scale) for lev, w in zip(CANDIDATES, x) if w / scale >= 0.005}
        fitted[model_id] = {"scale": scale, "weights": weights, "corr_before": corr_before, "corr_after": corr_after}
        print(f"\nmodel {model_id}: correlation {corr_before:.3f} now, {corr_after:.3f} fitted, total weight {scale:.3f}")
        print("  weights " + ", ".join(f"{lev}x {w:.1%}" for lev, w in weights.items()))
        print(f"  peak value: reference {b.max()/1e6:.2f}M, fitted {fit.max()/1e6:.2f}M, current {cur.max()/1e6:.2f}M")
        ref_peaks = peaks(np.arange(len(b)), b, 0.3 * b.max())
        print(f"  reference shelves over 30% of max: {len(ref_peaks)}")

    for name, m, prices, usd, series in shots:
        centers, _ = tier_profiles(series, 1)
        b = target_on_bins(centers, prices, usd)
        print(f"\n{name} shelves (price, reference, model 1 fitted)")
        model.MODELS[0] = {**model.MODELS[1], "id": 0, "weights": tuple(fitted[1]["weights"].items())}
        try:
            fit = model.build_heatmap(series, 0)
        finally:
            del model.MODELS[0]
        f = (fit.profile_long + fit.profile_short) * fitted[1]["scale"]
        for price, v in peaks(centers, b, 0.3 * np.nanmax(b)):
            j = int(np.argmin(np.abs(centers - price)))
            near = f[max(0, j - 2) : j + 3]
            print(f"  {price:10.1f}  {v/1e6:7.2f}M  {near.max()/1e6:7.2f}M at {centers[max(0, j - 2) + int(near.argmax())]:.1f}")
    out = Path("calibration") / "fit.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(fitted, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
