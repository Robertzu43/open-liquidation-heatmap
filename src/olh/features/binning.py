"""Price bins. Percent and log are both geometric. The dense grid stays under 150k cells."""

from __future__ import annotations

import numpy as np

from olh.core.text import MAX_CELLS


def make_price_grid(
    min_price: float,
    max_price: float,
    mode: str,
    bin_size: float,
    *,
    n_time: int = 1,
    tick_size: float | None = None,
    max_cells: int = MAX_CELLS,
) -> np.ndarray:
    """Return strictly increasing edges.

    ``bin_size`` is an absolute price step, a percent width (0.1 means 0.1%),
    a tick multiple, or the requested number of log-spaced bins. The width is
    increased until ``n_bins * n_time`` is at most ``max_cells``.
    """
    if min_price <= 0 or max_price <= 0:
        raise ValueError("prices must be positive")
    if max_price < min_price:
        raise ValueError("max_price must be >= min_price")
    if bin_size <= 0:
        raise ValueError("bin_size must be positive")
    key = mode.strip().lower()
    lo, hi = float(min_price), float(max_price)
    if hi == lo:
        hi = lo * 1.001
    width = bin_size
    for _ in range(24):
        edges = _edges(lo, hi, key, width, tick_size)
        if len(edges) - 1 <= 1 or (len(edges) - 1) * max(n_time, 1) <= max_cells:
            return edges
        width = _widen(key, width)
    return _edges(lo, hi, key, width, tick_size)


def bin_centers(edges: np.ndarray) -> np.ndarray:
    return (edges[:-1] + edges[1:]) / 2.0


def bin_index(edges: np.ndarray, price: float) -> int | None:
    if not np.isfinite(price) or price < edges[0] or price > edges[-1]:
        return None
    if price == edges[-1]:
        return int(len(edges) - 2)
    idx = int(np.searchsorted(edges, price, side="right") - 1)
    if idx < 0 or idx >= len(edges) - 1:
        return None
    return idx


def smooth_price_axis(heat: np.ndarray, sigma: float) -> np.ndarray:
    if sigma is None or sigma <= 0 or heat.size == 0:
        return np.array(heat, dtype=float, copy=True)
    radius = max(1, int(round(3 * sigma)))
    offsets = np.arange(-radius, radius + 1, dtype=float)
    kernel = np.exp(-0.5 * (offsets / sigma) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(heat, ((radius, radius), (0, 0)), mode="edge")
    smoothed = np.zeros_like(heat, dtype=float)
    for offset, weight in enumerate(kernel):
        smoothed += weight * padded[offset : offset + heat.shape[0]]
    return smoothed


def _edges(lo: float, hi: float, mode: str, width: float, tick_size: float | None) -> np.ndarray:
    if mode == "absolute":
        return _linear(lo, hi, width)
    if mode == "percent":
        step = width / 100.0
        return _geometric(lo, hi, step)
    if mode == "log":
        count = max(1, int(round(width)))
        return np.geomspace(lo, hi, count + 1)
    if mode == "tick":
        if tick_size is None or tick_size <= 0:
            raise ValueError("tick mode needs a positive tick size")
        return _linear(lo, hi, width * tick_size)
    raise ValueError("bin mode must be absolute, percent, log, or tick")


def _geometric(lo: float, hi: float, step: float) -> np.ndarray:
    if step <= 0:
        raise ValueError("percent step must be positive")
    n_bins = max(1, int(np.ceil(np.log(hi / lo) / np.log(1.0 + step))))
    edges = lo * (1.0 + step) ** np.arange(n_bins + 1)
    if edges[-1] < hi:
        edges = np.append(edges, edges[-1] * (1.0 + step))
    return np.asarray(edges, dtype=float)


def _linear(lo: float, hi: float, width: float) -> np.ndarray:
    start = np.floor(lo / width) * width
    end = np.ceil(hi / width) * width
    if end <= start:
        end = start + width
    count = int(round((end - start) / width))
    edges = start + width * np.arange(count + 1)
    if edges[-1] < hi:
        edges = np.append(edges, edges[-1] + width)
    return np.asarray(edges, dtype=float)


def _widen(mode: str, width: float) -> float:
    if mode == "log":
        return max(1.0, width / 1.5)
    return width * 1.5
