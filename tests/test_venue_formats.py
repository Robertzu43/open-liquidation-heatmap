"""Binance and Bybit parsers fed the exchanges' own documented and captured responses.

Every fixture below is copied verbatim from the cited source (retrieved
2026-10-29). Offline tests monkeypatch ``data._get_json`` (and ``data._window``
where a fetcher builds its own grid); nothing here contacts an exchange unless
RUN_NETWORK=1 selects the live tests at the end of the file.
"""
from __future__ import annotations

import json
import math
import os
import time
from contextlib import contextmanager

import numpy as np
import pytest

from liqmap import data, market
from liqmap.data import DataError, range_spec

# ---------------------------------------------------------------------------
# Bybit v5 fixtures
# ---------------------------------------------------------------------------

# https://bybit-exchange.github.io/docs/v5/market/kline (Response Example; identical in
# github.com/bybit-exchange/docs/blob/main/docs/v5/market/kline.mdx), retrieved 2026-10-29.
# The example answers an inverse BTCUSD request, so list[5] is USD and list[6] is BTC there.
BYBIT_KLINE_DOC = json.loads("""
{
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "symbol": "BTCUSD",
        "category": "inverse",
        "list": [
            [
                "1670608800000",
                "17071",
                "17073",
                "17027",
                "17055.5",
                "268611",
                "15.74462667"
            ],
            [
                "1670605200000",
                "17071.5",
                "17071.5",
                "17061",
                "17071",
                "4177",
                "0.24469757"
            ],
            [
                "1670601600000",
                "17086.5",
                "17088",
                "16978",
                "17071.5",
                "6356",
                "0.37288112"
            ]
        ]
    },
    "retExtInfo": {},
    "time": 1672025956592
}
""")

# https://bybit-exchange.github.io/docs/v5/market/open-interest (Response Example, rendered
# page), retrieved 2026-10-29. openInterest is documented as the sum of both sides and
# singleOpenInterest as one side (changelog 2026-06-11).
BYBIT_OI_DOC = json.loads("""
{
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "symbol": "BTCUSD",
        "category": "inverse",
        "list": [
            {
                "openInterest": "63910691.00000000",
                "singleOpenInterest": "31955346",
                "timestamp": "1780963200000"
            },
            {
                "openInterest": "63910691.00000000",
                "singleOpenInterest": "31955346",
                "timestamp": "1780876800000"
            },
            {
                "openInterest": "63910691.00000000",
                "singleOpenInterest": "31955346",
                "timestamp": "1780790400000"
            },
            {
                "openInterest": "63942311.00000000",
                "singleOpenInterest": "31971156",
                "timestamp": "1780704000000"
            },
            {
                "openInterest": "63942311.00000000",
                "singleOpenInterest": "31971156",
                "timestamp": "1780617600000"
            }
        ],
        "nextPageCursor": "lastid%3D19408935%26lasttime%3D1780617600"
    },
    "retExtInfo": {},
    "time": 1780994051392
}
""")

# Real responses captured by ccxt: github.com/ccxt/ccxt/blob/master/ts/src/test/static/response/bybit.json,
# retrieved 2026-10-29. Case "swap ohlcv with since and limit" (BTCUSDT linear, 1h).
CCXT_BYBIT_KLINE_LINEAR = json.loads("""
{
  "retCode": "0",
  "retMsg": "OK",
  "result": {
    "symbol": "BTCUSDT",
    "category": "linear",
    "list": [
      ["1706637600000", "43376.1", "43474.1", "43264.8", "43352.8", "1575.305", "68345898.1407"],
      ["1706634000000", "43488", "43579.5", "43286.8", "43376.1", "3199.431", "138878389.2519"],
      ["1706630400000", "43350.7", "43492.5", "43204.5", "43488", "1341.261", "58117391.4117"]
    ]
  },
  "retExtInfo": {},
  "time": "1706803708087"
}
""")
# Same file, case "linear OI" (fetchOpenInterest BTC/USDT:USDT, before singleOpenInterest existed).
CCXT_BYBIT_OI_LINEAR = json.loads("""
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "symbol": "BTCUSDT",
    "category": "linear",
    "list": [{"openInterest": "62129.45500000", "timestamp": "1739725200000"}],
    "nextPageCursor": "lastid%3D10860016%26lasttime%3D1739725200"
  },
  "retExtInfo": {},
  "time": 1739725954780
}
""")
# Same file, case "swap ticker" (fetchTicker BTC/USDT:USDT), from /v5/market/tickers.
CCXT_BYBIT_TICKER_LINEAR = json.loads("""
{
  "retCode": "0",
  "retMsg": "OK",
  "result": {
    "category": "linear",
    "list": [
      {
        "symbol": "BTCUSDT",
        "lastPrice": "51444.40",
        "indexPrice": "51469.95",
        "markPrice": "51444.40",
        "prevPrice24h": "50147.50",
        "price24hPcnt": "0.025861",
        "highPrice24h": "51444.40",
        "lowPrice24h": "48381.60",
        "prevPrice1h": "50999.90",
        "openInterest": "225766.793",
        "openInterestValue": "11614437205.81",
        "turnover24h": "2352063794.0035",
        "volume24h": "47293.8870",
        "fundingRate": "-0.00046295",
        "nextFundingTime": "1707926400000",
        "predictedDeliveryPrice": "",
        "basisRate": "",
        "deliveryFeeRate": "",
        "deliveryTime": "0",
        "ask1Size": "491.691",
        "bid1Price": "51444.30",
        "ask1Price": "51444.40",
        "bid1Size": "55.841",
        "basis": ""
      }
    ]
  },
  "retExtInfo": {},
  "time": "1707905549267"
}
""")
# Error bodies quoted in ccxt's Bybit error map: github.com/ccxt/ccxt/blob/master/ts/src/bybit.ts
# ("broad" exceptions), retrieved 2026-10-29.
CCXT_BYBIT_UNKNOWN_SYMBOL = json.loads('{"retCode":10001,"retMsg":"Not supported symbols","result":{},"retExtInfo":{},"time":1726147060461}')
CCXT_BYBIT_TIMEOUT = json.loads('{"retCode":10016,"retMsg":"Request timeout, please try again later","result":{},"retExtInfo":{},"time":1675307914985}')
# Body api.bybit.com served this container with HTTP 403 (captured 2026-10-29T17:13Z). It is not JSON.
BYBIT_REGION_BLOCK_BODY = b'{\n    error:The Amazon CloudFront distribution is configured to block access from your country\n}'


def bybit_wire(capture: dict) -> dict:
    """Undo ccxt's recorder quoting the two numeric envelope fields.

    ccxt records with ``quoteJsonNumbers`` (numbers become strings). Bybit documents
    ``retCode`` and ``time`` as JSON integers, and its pybit SDK relies on that
    (``if s_json.get("retCode")``), so the wire values are integers.
    """
    return {**capture, "retCode": int(capture["retCode"]), "time": int(capture["time"])}


# ---------------------------------------------------------------------------
# Binance USD-M fixtures
# ---------------------------------------------------------------------------

# Kline example on https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#kline-candlestick-data
# (the old .../usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data page), retrieved 2026-10-29.
BINANCE_KLINE_DOC = [
    [
        1499040000000,       # Open time
        "0.01634790",        # Open
        "0.80000000",        # High
        "0.01575800",        # Low
        "0.01577100",        # Close
        "148976.11427815",   # Volume
        1499644799999,       # Close time
        "2434.19055334",     # Quote asset volume
        308,                 # Number of trades
        "1756.87402397",     # Taker buy base asset volume
        "28.46694368",       # Taker buy quote asset volume
        "17928899.62484339"  # Ignore
    ]
]
# Real fapi /fapi/v1/klines response captured by ccxt: github.com/ccxt/ccxt/blob/master/ts/src/test/static/response/binance.json,
# case "swap ohlcv" (BTC/USDT:USDT, 1m), retrieved 2026-10-29.
CCXT_BINANCE_KLINES = json.loads("""
[
  [1712738460000, "71481.90", "71481.90", "71481.90", "71481.90", "0.002", 1712738519999, "142.96380", 1, "0.002", "142.96380", "0"],
  [1712738520000, "71471.00", "71471.00", "70459.40", "70459.40", "0.120", 1712738579999, "8518.17600", 9, "0.061", "4359.72600", "0"],
  [1712738580000, "71470.70", "71470.90", "70459.40", "70459.40", "1.542", 1712738639999, "108661.55840", 8, "0.013", "929.12130", "0"]
]
""")
# Current OpenAPI examples. The docs page (market-data#open-interest-statistics, #mark-price,
# #open-interest, #get-funding-rate-info) shows these values; Binance's generated Go connector
# carries them as JSON: github.com/binance/binance-connector-go/blob/master/clients/derivativestradingusdsfutures/tests/unit/restapi/api_market_data_test.go,
# retrieved 2026-10-29. openInterestHist timestamp = end of the period; takerlongshortRatio
# timestamp = start of the period (github.com/binance/binance-connector-java/blob/master/clients/
# derivatives-trading-usds-futures/docs/{OpenInterestStatistics,TakerBuySellVolume}ResponseInner.md).
BINANCE_OI_HIST_DOC = json.loads('[{"symbol":"BTCUSDT","sumOpenInterest":"20403.12345678","sumOpenInterestValue":"176196512.12345678","CMCCirculatingSupply":"165880.538","timestamp":1591261042378}]')
BINANCE_TAKER_DOC = json.loads('[{"buySellRatio":"1.5586","buyVol":"387.3300","sellVol":"248.5030","timestamp":1591261042378}]')
BINANCE_PREMIUM_DOC = json.loads('{"symbol":"BTCUSDT","markPrice":"11793.63104562","indexPrice":"11781.80495970","estimatedSettlePrice":"11781.16138815","lastFundingRate":"0.00038246","interestRate":"0.00010000","nextFundingTime":1597392000000,"time":1597370495002}')
BINANCE_OI_DOC = json.loads('{"openInterest":"10659.509","symbol":"BTCUSDT","time":1589437530011}')
BINANCE_FUNDING_INFO_DOC = json.loads('[{"symbol":"BLZUSDT","adjustedFundingRateCap":"0.02500000","adjustedFundingRateFloor":"-0.02500000","fundingIntervalHours":8,"disclaimer":false}]')
# The previous docs example for openInterestHist, kept as a mock by ccxt:
# github.com/ccxt/binance-rs/blob/master/tests/mocks/futures/market/open_interest_statistics.json, retrieved 2026-10-29.
BINANCE_OI_HIST_OLD_DOC = json.loads("""
[
    {
        "symbol": "BTCUSDT",
        "sumOpenInterest": "20403.63700000",
        "sumOpenInterestValue": "150570784.07809979",
        "timestamp": 1583127900000
    },
    {
        "symbol": "BTCUSDT",
        "sumOpenInterest": "20401.36700000",
        "sumOpenInterestValue": "149940752.14464448",
        "timestamp": 1583128200000
    }
]
""")
# https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/error-code.md, retrieved 2026-10-29.
BINANCE_ERROR_DOC = json.loads('{"code":-1121,"msg":"Invalid symbol."}')
# Body fapi.binance.com served this container with HTTP 451 (captured 2026-10-29T17:13Z):
#   {"code": 0, "msg": "Service unavailable from a restricted location according to 'b. Eligibility' in
#    https://www.binance.com/en/terms. Please contact customer service if you believe you received this message in error."}
# The terms URL is left out of the literal: tests/validation/test_supply_chain.py allows only exchange
# API hosts in Python strings, and the reader never looks at msg.
BINANCE_REGION_BLOCK = {"code": 0, "msg": "Service unavailable from a restricted location according to 'b. Eligibility'"}

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def recorder(handler):
    """A data._get_json stand-in that records (endpoint, params) and delegates to ``handler``."""
    calls = []

    def get_json(url, params=None, **kwargs):
        calls.append((url.rsplit("/", 1)[-1], dict(params or {})))
        return handler(url, dict(params or {}))

    get_json.calls = calls
    return get_json


def named(fake, endpoint):
    return [params for name, params in fake.calls if name == endpoint]


# ---------------------------------------------------------------------------
# Documented request vocabulary
# ---------------------------------------------------------------------------

def test_request_parameters_stay_inside_documented_enums_and_limits():
    # Bybit kline interval enum: 1,3,5,15,30,60,120,240,360,720,D,W,M (limit 1..1000).
    assert set(data.BYBIT_KLINE.values()) <= {"1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"}
    assert data.BYBIT_KLINE["12h"] == "720" and data.BYBIT_KLINE["1d"] == "D"
    # Bybit open-interest intervalTime enum has no 12-hour value: 5min,15min,30min,1h,4h,1d (limit 1..200).
    assert set(data.BYBIT_OI.values()) <= {"5min", "15min", "30min", "1h", "4h", "1d"}
    assert data.BYBIT_OI["12h"] == "4h"
    # Binance kline intervals and the openInterestHist/takerlongshortRatio periods
    # (binance-connector-python models/enums.py, generated from the OpenAPI spec).
    kline = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}
    period = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
    assert set(data.BAR_SECONDS) <= kline and set(data.BAR_SECONDS) <= period
    # openInterestHist serves the latest month only; takerlongshortRatio the latest 30 days.
    assert data.LIVE_OI_DAYS < 30


# ---------------------------------------------------------------------------
# Bybit
# ---------------------------------------------------------------------------

def test_bybit_kline_doc_example_is_requested_once_with_documented_columns(monkeypatch):
    fake = recorder(lambda url, params: (200, BYBIT_KLINE_DOC))
    monkeypatch.setattr(data, "_get_json", fake)
    rows = data._bybit_klines("BTCUSD", "60", 1670601600000, 1670608800000)
    # Rows keep the documented "sort in reverse" order; fold_candles places each by its start time.
    assert rows == BYBIT_KLINE_DOC["result"]["list"]
    [params] = named(fake, "kline")
    assert params == {"category": "linear", "symbol": "BTCUSD", "interval": "60",
                      "start": 1670601600000, "end": 1670608800000, "limit": 1000}


def test_bybit_fetch_parses_captured_linear_klines_with_turnover_as_usd(monkeypatch):
    # Candles: the captured response verbatim. Open interest: the documented current row shape
    # (openInterest is both sides, singleOpenInterest one side) with synthetic values.
    grid = np.array([1706630400000, 1706634000000, 1706637600000])
    single = {int(t): 50_000.0 + i * 100 for i, t in enumerate(grid)}

    def handler(url, params):
        if url.endswith("/v5/market/kline"):
            return 200, bybit_wire(CCXT_BYBIT_KLINE_LINEAR)
        rows = [{"openInterest": f"{2 * base:.8f}", "singleOpenInterest": f"{base:.3f}", "timestamp": str(ts)}
                for ts, base in sorted(single.items(), reverse=True)]
        return 200, {"retCode": 0, "retMsg": "OK", "result": {"symbol": "BTCUSDT", "category": "linear", "list": rows,
                     "nextPageCursor": ""}, "retExtInfo": {}, "time": 1706803708087}

    fake = recorder(handler)
    monkeypatch.setattr(data, "_get_json", fake)
    monkeypatch.setattr(data, "_window", lambda spec, end_open_s=None: grid)
    monkeypatch.setattr(data.time, "time", lambda: 1706803708.087)
    series = data.fetch_bybit("BTC", "BTCUSDT", range_spec("7d")).series
    np.testing.assert_array_equal(series.times, grid)
    np.testing.assert_allclose(series.open, [43350.7, 43488, 43376.1])
    np.testing.assert_allclose(series.high, [43492.5, 43579.5, 43474.1])
    np.testing.assert_allclose(series.low, [43204.5, 43286.8, 43264.8])
    np.testing.assert_allclose(series.close, [43488, 43376.1, 43352.8])
    # list[6] turnover is quote USDT for linear contracts; list[5] (BTC) must not be used.
    np.testing.assert_allclose(series.volume_usd, [58117391.4117, 138878389.2519, 68345898.1407])
    # One side of the book, valued at the bar close.
    np.testing.assert_allclose(series.oi_base, [single[int(t)] for t in grid])
    np.testing.assert_allclose(series.oi_usd, series.oi_base * series.close)
    assert named(fake, "kline")[0]["interval"] == "60" and named(fake, "open-interest")[0]["intervalTime"] == "1h"


def test_bybit_open_interest_doc_example_follows_cursor_exactly_as_returned(monkeypatch):
    fake = recorder(lambda url, params: (200, BYBIT_OI_DOC))
    monkeypatch.setattr(data, "_get_json", fake)
    rows = data._bybit_open_interest("BTCUSD", "1d", 1780617600000, 1780963200000)
    assert [row["timestamp"] for row in rows] == sorted(row["timestamp"] for row in BYBIT_OI_DOC["result"]["list"])
    first, second = named(fake, "open-interest")
    assert "cursor" not in first and first["limit"] == 200 and first["intervalTime"] == "1d"
    # The cursor arrives percent-encoded; it is passed on unchanged (the HTTP client encodes it once more).
    assert second["cursor"] == "lastid%3D19408935%26lasttime%3D1780617600"


def test_bybit_single_side_open_interest_matches_documented_rows():
    for row in BYBIT_OI_DOC["result"]["list"]:
        single = data._bybit_single_side(row, "singleOpenInterest", "openInterest")
        assert single == float(row["singleOpenInterest"])
        assert single == pytest.approx(float(row["openInterest"]) / 2, abs=1)
    # A capture from before singleOpenInterest existed: the both-sides figure is halved.
    [old] = CCXT_BYBIT_OI_LINEAR["result"]["list"]
    assert data._bybit_single_side(old, "singleOpenInterest", "openInterest") == pytest.approx(62129.455 / 2)
    assert math.isnan(data._bybit_single_side({"openInterest": ""}, "singleOpenInterest", "openInterest"))


def test_bybit_context_reads_captured_linear_ticker_as_one_side(monkeypatch):
    fake = recorder(lambda url, params: (200, bybit_wire(CCXT_BYBIT_TICKER_LINEAR)))
    monkeypatch.setattr(data, "_get_json", fake)
    venue = market._bybit_context("BTC")
    assert venue["ok"] is True
    assert venue["funding_rate"] == pytest.approx(-0.00046295)
    assert venue["next_funding_time"] == 1707926400000
    assert venue["funding_interval_hours"] is None  # fundingIntervalHour was added to the API later.
    assert venue["mark_price"] == pytest.approx(51444.40)
    assert venue["open_interest_base"] == pytest.approx(225766.793 / 2)
    assert venue["open_interest_usd"] == pytest.approx(11614437205.81 / 2)
    assert venue["observed_at"] == 1707905549267
    assert named(fake, "tickers") == [{"category": "linear", "symbol": "BTCUSDT"}]


def test_bybit_context_prefers_documented_single_side_fields(monkeypatch):
    ticker = {**CCXT_BYBIT_TICKER_LINEAR["result"]["list"][0], "singleOpenInterest": "112883.400",
              "singleOpenInterestValue": "5807218602.91", "fundingIntervalHour": "8", "fundingCap": "0.005"}
    body = {**bybit_wire(CCXT_BYBIT_TICKER_LINEAR), "result": {"category": "linear", "list": [ticker]}}
    monkeypatch.setattr(data, "_get_json", recorder(lambda url, params: (200, body)))
    venue = market._bybit_context("BTC")
    assert venue["open_interest_base"] == pytest.approx(112883.4)
    assert venue["open_interest_usd"] == pytest.approx(5807218602.91)
    assert venue["funding_interval_hours"] == 8


def test_bybit_captured_error_bodies_map_to_clear_errors(monkeypatch):
    monkeypatch.setattr(data, "_get_json", lambda *args, **kwargs: (200, CCXT_BYBIT_UNKNOWN_SYMBOL))
    with pytest.raises(DataError, match=r"Bybit has no NOPEUSDT perpetual \(Not supported symbols\)"):
        data.fetch_bybit("NOPE", "NOPEUSDT", range_spec("24h"))
    with pytest.raises(DataError, match="Bybit has no NOPEUSDT perpetual"):
        market._bybit_context("NOPE")
    monkeypatch.setattr(data, "_get_json", lambda *args, **kwargs: (200, CCXT_BYBIT_TIMEOUT))
    with pytest.raises(DataError, match=r"rejected the request \(Request timeout"):
        data._bybit_result("/v5/market/kline", {"symbol": "BTCUSDT"})


def test_bybit_captured_region_block_and_documented_rate_limit_403_are_explained(monkeypatch):
    class Blocked:
        status_code = 403
        headers = {"content-type": "application/json"}

        def iter_content(self, chunk_size):
            yield BYBIT_REGION_BLOCK_BODY

        def close(self):
            pass

    monkeypatch.setattr(data._SESSION, "get", lambda *args, **kwargs: Blocked())
    with pytest.raises(DataError, match="not reachable") as caught:
        data._bybit_payload("/v5/market/tickers", {"category": "linear", "symbol": "BTCUSDT"})
    # Bybit documents HTTP 403 for a breached IP rate limit as well as for blocked regions.
    message = str(caught.value)
    assert "HTTP 403" in message and "blocks some regions" in message and "rate-limited" in message


# ---------------------------------------------------------------------------
# Binance USD-M
# ---------------------------------------------------------------------------

def binance_range_server(klines, oi_rows, taker_rows):
    """Answers like fapi: rows with startTime <= time <= endTime, oldest first, at most ``limit``."""
    def stamp(row):
        return int(row[0]) if isinstance(row, list) else int(row["timestamp"])

    def handler(url, params):
        table = klines if url.endswith("/klines") else oi_rows if url.endswith("/openInterestHist") else taker_rows
        rows = sorted((row for row in table if params["startTime"] <= stamp(row) <= params["endTime"]), key=stamp)
        return 200, rows[:params["limit"]]

    return recorder(handler)


def test_binance_live_fold_parses_documented_and_captured_rows(monkeypatch):
    # A daily grid spanning every published sample: 2017-07-03 .. 2024-04-10 UTC.
    start, end = 1499040000000, 1712707200000
    grid = np.arange(start, end + 1, DAY_MS, dtype=np.int64)
    fake = binance_range_server(BINANCE_KLINE_DOC + CCXT_BINANCE_KLINES,
                                BINANCE_OI_HIST_OLD_DOC + BINANCE_OI_HIST_DOC, BINANCE_TAKER_DOC)
    monkeypatch.setattr(data, "_get_json", fake)
    raw = data._fold_binance_live("BTCUSDT", "1d", grid)
    assert raw is not None and raw.source == "binance-live"

    def day(ms):
        return int((ms - ms % DAY_MS - start) // DAY_MS)

    doc, captured = day(1499040000000), day(1712738460000)
    assert (raw.open[doc], raw.high[doc], raw.low[doc], raw.close[doc]) == (0.0163479, 0.8, 0.015758, 0.015771)
    assert raw.volume[doc] == pytest.approx(2434.19055334)  # index 7, quote asset volume
    # Three captured one-minute rows fold into their day: first open, extreme high/low, last close.
    assert (raw.open[captured], raw.high[captured], raw.low[captured], raw.close[captured]) == (71481.9, 71481.9, 70459.4, 70459.4)
    assert raw.volume[captured] == pytest.approx(142.9638 + 8518.176 + 108661.5584)
    assert int(np.isfinite(raw.close).sum()) == 2

    old, new = day(1583127900000), day(1591261042378)
    # sumOpenInterestValue is USD, sumOpenInterest native base; the later print in a bar wins.
    assert raw.oi[old] == pytest.approx(149940752.14464448) and raw.oi_base[old] == pytest.approx(20401.367)
    assert raw.oi[new] == pytest.approx(176196512.12345678) and raw.oi_base[new] == pytest.approx(20403.12345678)
    assert math.isnan(raw.share[old])  # No taker row shares that timestamp.
    assert raw.share[new] == pytest.approx(387.33 / (387.33 + 248.503))
    assert int(np.isfinite(raw.oi).sum()) == 2

    for endpoint in ("klines", "openInterestHist", "takerlongshortRatio"):
        first = named(fake, endpoint)[0]
        assert first["symbol"] == "BTCUSDT" and first["startTime"] == start and first["endTime"] == end + DAY_MS
    assert named(fake, "klines")[0]["interval"] == "1d" and named(fake, "klines")[0]["limit"] == 1500
    assert named(fake, "openInterestHist")[0]["period"] == "1d" and named(fake, "openInterestHist")[0]["limit"] == 500
    assert named(fake, "takerlongshortRatio")[0]["period"] == "1d"


def test_binance_context_parses_documented_snapshots(monkeypatch):
    routes = {"premiumIndex": BINANCE_PREMIUM_DOC, "openInterest": BINANCE_OI_DOC, "fundingInfo": BINANCE_FUNDING_INFO_DOC}
    fake = recorder(lambda url, params: (200, routes[url.rsplit("/", 1)[-1]]))
    monkeypatch.setattr(data, "_get_json", fake)
    venue = market._binance_context("BTC")
    assert venue["ok"] is True
    assert venue["funding_rate"] == pytest.approx(0.00038246)
    assert venue["next_funding_time"] == 1597392000000
    assert venue["mark_price"] == pytest.approx(11793.63104562)
    assert venue["open_interest_base"] == pytest.approx(10659.509)
    assert venue["open_interest_usd"] == pytest.approx(10659.509 * 11793.63104562)
    assert venue["observed_at"] == 1589437530011  # The older of the two snapshot times.
    # fundingInfo lists only adjusted symbols; BTCUSDT is absent, so the default interval applies.
    assert venue["funding_interval_hours"] == market.BINANCE_DEFAULT_FUNDING_HOURS == 8
    assert market._binance_interval("BLZUSDT") == 8
    assert named(fake, "premiumIndex") == [{"symbol": "BTCUSDT"}] and named(fake, "openInterest") == [{"symbol": "BTCUSDT"}]


def test_binance_documented_and_captured_errors_are_named(monkeypatch):
    monkeypatch.setattr(data, "_get_json", lambda *args, **kwargs: (400, BINANCE_ERROR_DOC))
    with pytest.raises(DataError, match="Binance has no NOPEUSDT perpetual"):
        market._binance_get("/fapi/v1/premiumIndex", {"symbol": "NOPEUSDT"}, "NOPEUSDT")
    monkeypatch.setattr(data, "_get_json", lambda *args, **kwargs: (451, BINANCE_REGION_BLOCK))
    with pytest.raises(DataError, match=r"not reachable from this network \(HTTP 451\)"):
        market._binance_get("/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"}, "BTCUSDT")
    assert data._fold_binance_live("BTCUSDT", "5m", np.array([300_000, 600_000])) is None
    # Binance documents HTTP 403 for a violated firewall (WAF) limit, not only for regions.
    monkeypatch.setattr(data, "_get_json", lambda *args, **kwargs: (403, None))
    with pytest.raises(DataError, match="rate-limited"):
        market._binance_get("/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"}, "BTCUSDT")


# ---------------------------------------------------------------------------
# Live checks: opt-in with RUN_NETWORK=1. A regional block skips; a working venue must parse.
# ---------------------------------------------------------------------------

live = pytest.mark.skipif(os.environ.get("RUN_NETWORK") != "1", reason="network test; set RUN_NETWORK=1")


@contextmanager
def venue_reachable():
    """Skip when the venue refuses this network (HTTP 451/403 surface as "not reachable")."""
    try:
        yield
    except DataError as exc:
        if "not reachable" in str(exc):
            pytest.skip(str(exc))
        raise


def assert_recent_closed_bars(series, now_ms):
    bar_ms = int(series.bar_seconds) * 1000
    assert (np.diff(series.times) == bar_ms).all()
    last_close = int(series.times[-1]) + bar_ms
    assert last_close <= now_ms + 60_000
    assert now_ms - last_close < 2 * bar_ms + 15 * 60_000


@pytest.mark.network
@live
@pytest.mark.parametrize("range_key", ["12h", "180d"])
def test_live_bybit_fetch_btc(range_key):
    spec = range_spec(range_key)
    with venue_reachable():
        loaded = data.fetch_bybit("BTC", "BTCUSDT", spec)
    series, now_ms = loaded.series, time.time() * 1000
    assert loaded.source == "bybit" and series.exchange == "bybit"
    assert len(series.times) >= 0.9 * spec["seconds"] // spec["bar_seconds"]
    assert_recent_closed_bars(series, now_ms)
    assert (series.close > 0).all() and (series.high >= series.low).all() and series.volume_usd.sum() > 0
    native = series.oi_base[series.oi_observed]
    # 180d uses 4-hour open interest over about six cursor pages; coverage proves the cursor works.
    assert series.oi_observed.mean() > 0.9
    assert (native > 0).all() and 1_000 < np.median(native) < 1_000_000  # BTC, one side
    assert (series.oi_usd[series.oi_observed] > 0).all()


@pytest.mark.network
@live
def test_live_bybit_open_interest_rows_carry_single_side():
    now_ms = int(time.time() * 1000)
    with venue_reachable():
        rows = data._bybit_open_interest("BTCUSDT", "1h", now_ms - 6 * HOUR_MS, now_ms)
    assert rows
    for row in rows:
        assert abs(int(row["timestamp"]) - now_ms) < 7 * HOUR_MS
        both, single = float(row["openInterest"]), float(row["singleOpenInterest"])
        assert both > 0 and single == pytest.approx(both / 2, rel=0.01)


@pytest.mark.network
@live
@pytest.mark.parametrize("bars", [144, 600])
def test_live_binance_fold(bars):
    with venue_reachable():
        server = market._binance_get("/fapi/v1/time", None, "BTCUSDT")
    now_ms = time.time() * 1000
    assert abs(int(server["serverTime"]) - now_ms) < 5 * 60_000
    grid = data.make_grid(data.history_end_open(int(now_ms // 1000), 300), bars, 300)
    raw = data._fold_binance_live("BTCUSDT", "5m", grid)
    assert raw is not None, "Binance answered /fapi/v1/time but the live fold failed"
    closes = np.isfinite(raw.close)
    assert closes.mean() > 0.98 and (raw.close[closes] > 0).all() and raw.volume[closes].sum() > 0
    # 600 bars need two openInterestHist and takerlongshortRatio pages; coverage shows paging works.
    oi = np.isfinite(raw.oi)
    assert oi.mean() > 0.95 and (raw.oi[oi] > 0).all() and (raw.oi_base[oi] > 0).all()
    assert 1_000 < np.median(raw.oi_base[oi]) < 1_000_000
    shares = raw.share[np.isfinite(raw.share)]
    assert shares.size > 0.9 * bars and ((shares > 0) & (shares < 1)).all()
    assert now_ms - (raw.last_candle_ms + 300_000) < 15 * 60_000


@pytest.mark.network
@live
@pytest.mark.parametrize("exchange", ["binance", "bybit"])
def test_live_context_payload(exchange):
    market._context_cache.clear()
    with venue_reachable():
        payload = market.context_payload(exchange, "BTC")
    [venue] = payload["venues"]
    now_ms = time.time() * 1000
    assert venue["ok"] is True and venue["error"] is None
    assert venue["mark_price"] > 0 and math.isfinite(venue["funding_rate"]) and abs(venue["funding_rate"]) < 0.05
    assert 1_000 < venue["open_interest_base"] < 1_000_000
    assert venue["open_interest_usd"] == pytest.approx(venue["open_interest_base"] * venue["mark_price"], rel=0.05)
    assert venue["funding_interval_hours"] in (1, 2, 4, 8)
    assert now_ms - 60_000 < venue["next_funding_time"] <= now_ms + venue["funding_interval_hours"] * HOUR_MS + 60_000
    assert abs(venue["observed_at"] - now_ms) < 10 * 60_000
    assert payload["open_interest_usd_total"] == pytest.approx(venue["open_interest_usd"])
