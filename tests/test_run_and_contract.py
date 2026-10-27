"""Runner isolation, the heatmap.v1 cube, and view filters that do not refetch."""

import numpy as np
import pandas as pd
import pytest

from olh.core.errors import ExchangeBlocked
from olh.core.text import DISCLAIMER, ESTIMATE_LABEL, SCHEMA
from olh.exchanges.base import MarketSeries
from olh.exchanges.okx import coarsen_interval
from olh.features.binning import make_price_grid
from olh.features.contract import aggregate
from olh.run import run_heatmap


class _Blocked:
    name = "binance"

    def fetch(self, symbol, start, end, interval):
        raise ExchangeBlocked(
            "HTTP 451 from https://fapi.binance.com/fapi/v1/klines. "
            "Public REST refused the request from this US host."
        )


class _Ok:
    name = "okx"

    def fetch(self, symbol, start, end, interval):
        start = pd.Timestamp(start)
        candles = pd.DataFrame(
            {
                "open_time": [start, start + pd.Timedelta(minutes=15)],
                "open": [100.0, 100.0],
                "high": [101.0, 101.0],
                "low": [99.0, 99.0],
                "close": [100.0, 100.0],
                "mark": [100.0, 100.0],
            }
        )
        oi = pd.DataFrame(
            {
                "timestamp": [start, start + pd.Timedelta(minutes=15)],
                "oi_contracts": [10.0, 12.0],
                "oi_usd": [1000.0, 1200.0],
            }
        )
        return MarketSeries("okx", candles, oi, max_leverage=100, tick_size=0.1, warnings=["okx note"])


def test_one_blocked_venue_does_not_drop_the_other():
    contract = run_heatmap(
        symbol="BTCUSDT",
        window="24h",
        interval="15m",
        adapters=[_Blocked(), _Ok()],
        now="2026-10-27T00:00:00Z",
        leverages=[10],
        mmr_mode="constant",
        mmr=0.005,
    )
    assert contract["schema"] == SCHEMA
    assert contract["model"] == "oi_leverage_v1"
    assert contract["label"] == ESTIMATE_LABEL
    assert DISCLAIMER in contract["disclaimer"]
    assert contract["axes"]["exchange"] == ["okx"]
    assert contract["metadata"]["exchanges"] == ["okx"]
    assert any("451" in warning for warning in contract["metadata"]["warnings"])
    assert contract["cells"]
    assert {"t", "p", "side", "leverage", "exchange", "notional"} <= set(contract["cells"][0])
    assert {"time", "close", "mark", "high", "low"} <= set(contract["price_series"][0])
    full, _, _ = aggregate(contract)
    longs, _, _ = aggregate(contract, sides=["long"])
    assert longs.sum() < full.sum()
    assert longs.sum() > 0
    assert len(contract["axes"]["price"]) * len(contract["axes"]["time"]) <= 150_000


def test_okx_coarsens_only_when_the_window_exceeds_about_1440_points():
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    name, note = coarsen_interval("15m", start, start + pd.Timedelta(hours=24))
    assert name == "15m"
    assert note is None
    coarse, warning = coarsen_interval("15m", start, start + pd.Timedelta(days=90))
    assert coarse != "15m"
    assert "1440" in warning


def test_mark_history_stays_on_the_mark_endpoint():
    from olh.exchanges.okx import OkxAdapter

    calls = []
    stamp = str(int(pd.Timestamp("2026-10-25T00:00:00Z").timestamp() * 1000))

    class Session:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None):
            calls.append(url)
            if len(calls) == 1:
                body = '{"code":"0","data":[["%s","1","2","0.5","1.5","1"]]}' % stamp
            else:
                body = '{"code":"0","data":[]}'
            return _Response(200, body.encode())

    class _Response:
        def __init__(self, status, body):
            self.status_code = status
            self.content = body
            self.text = body.decode()

        def json(self):
            import json
            return json.loads(self.text)

    from olh.infra.http import HttpClient

    adapter = OkxAdapter(http=HttpClient(min_interval=0, session=Session(), sleep=lambda _s: None))
    rows, note = adapter._mark(
        "BTC-USDT-SWAP",
        "1H",
        "2026-10-20T00:00:00Z",
        "2026-10-25T12:00:00Z",
    )
    assert note is None
    assert rows
    assert calls
    assert all("mark-price-candles" in url for url in calls)
    assert not any(url.endswith("/market/history-candles") for url in calls)


def test_price_grid_widens_to_stay_under_the_cell_cap():
    edges = make_price_grid(100, 101, "absolute", 0.0001, n_time=100_000, max_cells=150_000)
    assert (len(edges) - 1) * 100_000 <= 150_000


def test_cli_rejects_unknown_lookback(capsys):
    from olh.cli import main

    assert main(["--lookback", "2w"]) == 2
    assert "2w" in capsys.readouterr().err


def test_plotly_figure_has_heatmap_and_price_line():
    pytest.importorskip("plotly")
    from olh.viz.plotly_plot import plotly_figure

    contract = run_heatmap(
        symbol="BTCUSDT",
        window="24h",
        interval="15m",
        adapters=[_Ok()],
        now="2026-10-27T00:00:00Z",
        leverages=[10],
        ladder="standard",
    )
    figure = plotly_figure(contract)
    kinds = {trace.type for trace in figure.data}
    assert "heatmap" in kinds
    assert "scatter" in kinds
    assert ESTIMATE_LABEL in figure.layout.title.text
    assert DISCLAIMER in figure.layout.title.text
