"""Interactive heatmap. The price line is teal on a graphite page."""

from __future__ import annotations

import numpy as np

from olh.core.text import DISCLAIMER, ESTIMATE_LABEL
from olh.features.contract import aggregate, reference_close, reference_mark

_BG = "#0B0E13"
_PRICE = "#2DD4BF"
_INK = "#E6E8EB"


def plotly_figure(
    contract: dict,
    *,
    sides: list[str] | None = None,
    leverages: list[float] | None = None,
    exchanges: list[str] | None = None,
    smooth: float = 0.0,
    y_span: float | None = None,
):
    """Build a Plotly figure from a stored cube. This does not fetch data."""
    import plotly.graph_objects as go

    heat, prices, breakdown = aggregate(
        contract,
        sides=sides,
        leverages=leverages,
        exchanges=exchanges,
        smooth_sigma=smooth,
    )
    close = reference_close(contract)
    mark = reference_mark(contract)
    line = np.where(np.isfinite(close), close, mark)
    heat, prices, breakdown, line = _clip_view(heat, prices, breakdown, line, y_span)
    symbol = contract.get("metadata", {}).get("symbol", "")
    title = f"{symbol}  {ESTIMATE_LABEL}" if symbol else ESTIMATE_LABEL
    figure = go.Figure()
    figure.add_trace(
        go.Heatmap(
            x=contract["axes"]["time"][: heat.shape[1]],
            y=prices,
            z=heat,
            colorscale=_scale(sides),
            hovertext=breakdown,
            hovertemplate="time %{x}<br>price %{y}<br>%{hovertext}<extra></extra>",
            colorbar={"title": "notional"},
            name="estimated notional",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=contract["axes"]["time"][: line.shape[0]],
            y=line,
            mode="lines",
            line={"color": _PRICE, "width": 1.6},
            name="price",
            hovertemplate="price %{y}<extra></extra>",
        )
    )
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        font={"color": _INK},
        title={"text": f"{title}<br><sup>{DISCLAIMER}</sup>"},
        xaxis_title="time",
        yaxis_title="price",
        legend={"orientation": "h"},
        margin={"t": 80},
    )
    return figure


def save_html(contract: dict, path: str, **kwargs) -> None:
    figure = plotly_figure(contract, **kwargs)
    figure.write_html(path, include_plotlyjs=True)


def _scale(sides: list[str] | None) -> str:
    chosen = {item.lower() for item in sides} if sides else set()
    if chosen == {"long"}:
        return "Blues"
    if chosen == {"short"}:
        return "Oranges"
    return "Cividis"


def _clip_view(heat, prices, breakdown, line, y_span):
    """Hide rows outside a percent band around the last price. Cells stay stored."""
    if not y_span or y_span <= 0 or line.size == 0:
        return heat, prices, breakdown, line
    finite = line[np.isfinite(line)]
    if finite.size == 0:
        return heat, prices, breakdown, line
    last = float(finite[-1])
    span = last * (float(y_span) / 100.0)
    mask = (prices >= last - span) & (prices <= last + span)
    if not mask.any():
        return heat, prices, breakdown, line
    return heat[mask], prices[mask], breakdown[mask], line
