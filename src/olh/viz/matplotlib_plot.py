"""Static PNG of the same heatmap.v1 document the interactive chart reads."""

from __future__ import annotations

import numpy as np

from olh.core.text import DISCLAIMER, ESTIMATE_LABEL
from olh.features.contract import aggregate, reference_close, reference_mark


def save_png(
    contract: dict,
    path: str,
    *,
    sides: list[str] | None = None,
    leverages: list[float] | None = None,
    exchanges: list[str] | None = None,
    smooth: float = 0.0,
    y_span: float | None = None,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    heat, prices, _breakdown = aggregate(
        contract,
        sides=sides,
        leverages=leverages,
        exchanges=exchanges,
        smooth_sigma=smooth,
    )
    close = reference_close(contract)
    mark = reference_mark(contract)
    line = np.where(np.isfinite(close), close, mark)
    if y_span and y_span > 0 and np.isfinite(line).any():
        last = float(line[np.isfinite(line)][-1])
        span = last * (float(y_span) / 100.0)
        mask = (prices >= last - span) & (prices <= last + span)
        if mask.any():
            heat = heat[mask]
            prices = prices[mask]
    symbol = contract.get("metadata", {}).get("symbol", "")
    title = f"{symbol}  {ESTIMATE_LABEL}" if symbol else ESTIMATE_LABEL
    fig, ax = plt.subplots(figsize=(11, 6.2))
    fig.patch.set_facecolor("#0B0E13")
    ax.set_facecolor("#0B0E13")
    if heat.size:
        image = ax.imshow(
            heat,
            origin="lower",
            aspect="auto",
            cmap=_cmap(sides),
            extent=(0, heat.shape[1], float(prices[0]), float(prices[-1]) if len(prices) else 1),
            interpolation="nearest",
        )
        bar = fig.colorbar(image, ax=ax, pad=0.02)
        bar.ax.yaxis.label.set_color("#E6E8EB")
        bar.ax.tick_params(colors="#E6E8EB")
    xs = np.linspace(0, max(len(line) - 1, 0), num=max(len(line), 1))
    ax.plot(xs[: len(line)], line, color="#2DD4BF", linewidth=1.4)
    ax.set_title(title, color="#E6E8EB", loc="left")
    ax.set_xlabel("time", color="#E6E8EB")
    ax.set_ylabel("price", color="#E6E8EB")
    ax.tick_params(colors="#E6E8EB")
    fig.text(0.01, 0.01, DISCLAIMER, color="#9AA4B2", fontsize=7)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _cmap(sides: list[str] | None) -> str:
    chosen = {item.lower() for item in sides} if sides else set()
    if chosen == {"long"}:
        return "Blues"
    if chosen == {"short"}:
        return "Oranges"
    return "cividis"
