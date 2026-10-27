"""Live public market-data smoke, plus an offline catalog check.

HTTP calls are marked ``network`` and skipped unless ``RUN_NETWORK=1``.
Sockets for those calls are limited to ``ALLOWED_EXCHANGE_HOSTS``. A 200 with
the wrong shape fails. HTTP 451 or a country block skips that exchange with
an explicit message: GitHub-hosted runners are usually in the US, and
``fapi.binance.com`` returns 451 from there. A skip is not a healthy result.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.validation.net_allowlist import ALLOWED_EXCHANGE_HOSTS

USER_AGENT = "open-liquidation-heatmap-qa/1.0"
Json = dict[str, Any] | list[Any]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# GET /futures/data/openInterestHist: default 30, maximum 500 rows.
# Only the latest 1 month of history is available.
BINANCE_OPEN_INTEREST_HIST_MAX_ROWS = 500
BINANCE_OPEN_INTEREST_HIST_HISTORY = "latest 1 month"


@dataclass(frozen=True)
class Endpoint:
    exchange: str
    name: str
    url: str
    params: tuple[tuple[str, str], ...]
    check: Callable[[Json], None]


def _klines_binance(payload: Json) -> None:
    assert isinstance(payload, list) and payload, "binance klines"
    row = payload[0]
    assert isinstance(row, list) and len(row) >= 6
    high, low, close = float(row[2]), float(row[3]), float(row[4])
    assert close > 0
    assert high >= low > 0


def _oi_binance(payload: Json) -> None:
    assert isinstance(payload, list) and payload, "binance open interest"
    row = payload[0]
    assert isinstance(row, dict)
    assert float(row["sumOpenInterest"]) > 0
    assert float(row["sumOpenInterestValue"]) > 0
    assert int(row["timestamp"]) > 0


def _ping_binance(payload: Json) -> None:
    assert payload == {}


def _bybit_kline(payload: Json) -> None:
    assert isinstance(payload, dict)
    assert payload["retCode"] == 0
    rows = payload["result"]["list"]
    assert rows
    _start, _open, high, low, close, *_rest = rows[0]
    assert float(close) > 0
    assert float(high) >= float(low) > 0


def _bybit_oi(payload: Json) -> None:
    assert isinstance(payload, dict)
    assert payload["retCode"] == 0
    rows = payload["result"]["list"]
    assert rows
    assert float(rows[0]["openInterest"]) > 0


def _okx_candles(payload: Json) -> None:
    assert isinstance(payload, dict)
    assert payload["code"] == "0"
    rows = payload["data"]
    assert rows
    _ts, _open, high, low, close, *_rest = rows[0]
    assert float(close) > 0
    assert float(high) >= float(low) > 0


def _okx_oi(payload: Json) -> None:
    assert isinstance(payload, dict)
    assert payload["code"] == "0"
    row = payload["data"][0]
    assert row["instId"] == "BTC-USDT-SWAP"
    assert float(row["oi"]) > 0
    assert float(row["oiUsd"]) > 0


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        "binance",
        "ping",
        "https://fapi.binance.com/fapi/v1/ping",
        (),
        _ping_binance,
    ),
    Endpoint(
        "binance",
        "klines",
        "https://fapi.binance.com/fapi/v1/klines",
        (("symbol", "BTCUSDT"), ("interval", "1h"), ("limit", "2")),
        _klines_binance,
    ),
    Endpoint(
        "binance",
        "openInterestHist",
        "https://fapi.binance.com/futures/data/openInterestHist",
        (("symbol", "BTCUSDT"), ("period", "1h"), ("limit", "2")),
        _oi_binance,
    ),
    Endpoint(
        "bybit",
        "kline",
        "https://api.bybit.com/v5/market/kline",
        (
            ("category", "linear"),
            ("symbol", "BTCUSDT"),
            ("interval", "60"),
            ("limit", "2"),
        ),
        _bybit_kline,
    ),
    Endpoint(
        "bybit",
        "open-interest",
        "https://api.bybit.com/v5/market/open-interest",
        (
            ("category", "linear"),
            ("symbol", "BTCUSDT"),
            ("intervalTime", "1h"),
            ("limit", "2"),
        ),
        _bybit_oi,
    ),
    Endpoint(
        "okx",
        "candles",
        "https://www.okx.com/api/v5/market/candles",
        (("instId", "BTC-USDT-SWAP"), ("bar", "1H"), ("limit", "2")),
        _okx_candles,
    ),
    Endpoint(
        "okx",
        "open-interest",
        "https://www.okx.com/api/v5/public/open-interest",
        (("instType", "SWAP"), ("instId", "BTC-USDT-SWAP")),
        _okx_oi,
    ),
)


def _fixture(name: str) -> Json:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_binance_fixtures_match_the_public_shape() -> None:
    klines = _fixture("binance/klines.json")
    _klines_binance(klines)
    history = _fixture("binance/open_interest_hist.json")
    _oi_binance(history)
    assert isinstance(history, list)
    assert len(history) <= BINANCE_OPEN_INTEREST_HIST_MAX_ROWS


def test_binance_open_interest_hist_is_capped_at_500_rows_and_one_month() -> None:
    """The public endpoint allows at most 500 rows and about one month."""
    assert BINANCE_OPEN_INTEREST_HIST_MAX_ROWS == 500
    assert "1 month" in BINANCE_OPEN_INTEREST_HIST_HISTORY
    endpoint = next(item for item in ENDPOINTS if item.name == "openInterestHist")
    assert int(dict(endpoint.params)["limit"]) <= BINANCE_OPEN_INTEREST_HIST_MAX_ROWS
    deploy = (
        Path(__file__).resolve().parents[2] / "docs" / "DEPLOY.md"
    ).read_text(encoding="utf-8")
    assert "500" in deploy
    assert "latest 1 month" in deploy


def test_okx_fixtures_match_the_public_shape() -> None:
    _okx_candles(_fixture("okx/candles.json"))
    _okx_oi(_fixture("okx/open_interest.json"))


def test_okx_fixtures_sit_next_to_the_binance_fixtures() -> None:
    assert (FIXTURES / "binance" / "klines.json").is_file()
    assert (FIXTURES / "binance" / "open_interest_hist.json").is_file()
    assert (FIXTURES / "okx" / "candles.json").is_file()
    assert (FIXTURES / "okx" / "open_interest.json").is_file()


def test_okx_shape_checks_reject_an_empty_or_partial_payload() -> None:
    with pytest.raises(AssertionError):
        _okx_candles({"code": "0", "data": []})
    with pytest.raises((AssertionError, KeyError, IndexError, TypeError, ValueError)):
        _okx_oi({"code": "0", "data": [{"instId": "BTC-USDT-SWAP"}]})


def test_catalog_is_public_binance_bybit_okx_only() -> None:
    exchanges = {item.exchange for item in ENDPOINTS}
    assert exchanges == {"binance", "bybit", "okx"}
    for item in ENDPOINTS:
        parsed = urllib.parse.urlparse(item.url)
        assert parsed.scheme == "https"
        assert parsed.hostname in ALLOWED_EXCHANGE_HOSTS
        lowered = item.url.lower()
        assert ("coin" + "glass") not in lowered
        assert ("coin" + "-glass") not in lowered


@dataclass
class ProbeResult:
    name: str
    status: str
    detail: str


def _fetch(endpoint: Endpoint) -> ProbeResult:
    query = urllib.parse.urlencode(endpoint.params)
    url = endpoint.url if not query else f"{endpoint.url}?{query}"
    label = f"{endpoint.exchange}:{endpoint.name}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return ProbeResult(label, "failed", f"transport error: {exc.reason}")

    if _is_geo_block(status, body):
        return ProbeResult(label, "blocked", f"HTTP {status}")
    if status != 200:
        return ProbeResult(label, "failed", f"HTTP {status}: {body[:180]}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return ProbeResult(label, "failed", f"invalid JSON: {exc}")
    try:
        endpoint.check(payload)
    except (AssertionError, KeyError, IndexError, TypeError, ValueError) as exc:
        return ProbeResult(label, "failed", f"shape: {exc}")
    return ProbeResult(label, "ok", "live public payload matched")


def _is_geo_block(status: int, body: str) -> bool:
    if status in {403, 451}:
        return True
    lowered = body.lower()
    return "restricted location" in lowered or "block access from your country" in lowered


def test_http_451_is_a_per_exchange_geo_block() -> None:
    assert _is_geo_block(451, "Service unavailable from a restricted location")
    assert _is_geo_block(403, "block access from your country")
    assert not _is_geo_block(200, "{}")
    assert not _is_geo_block(500, "upstream error")


def _geo_skip_reason(exchange: str, blocked: list[ProbeResult]) -> str:
    details = "; ".join(f"{item.name}: {item.detail}" for item in blocked)
    return (
        f"{exchange} is geo-blocked from this runner ({details}). "
        "GitHub-hosted runners are typically in the US. "
        "fapi.binance.com returns HTTP 451 from US IPs, and Bybit often "
        "returns HTTP 403. This skip is not a healthy-endpoint result. "
        "GitHub-hosted runners stay in the US. A Community Cloud demo uses "
        "OKX plus data.binance.vision. Live Binance and Bybit smoke waits "
        "for a non-US recorder; see docs/DEPLOY.md."
    )


@pytest.mark.network
@pytest.mark.allow_hosts(list(ALLOWED_EXCHANGE_HOSTS))
@pytest.mark.parametrize("exchange", ["binance", "bybit", "okx"])
def test_live_exchange_public_market_data(exchange: str) -> None:
    endpoints = [item for item in ENDPOINTS if item.exchange == exchange]
    results = [_fetch(endpoint) for endpoint in endpoints]
    failed = [item for item in results if item.status == "failed"]
    blocked = [item for item in results if item.status == "blocked"]
    assert not failed, "; ".join(f"{item.name}: {item.detail}" for item in failed)
    if len(blocked) == len(results):
        pytest.skip(_geo_skip_reason(exchange, blocked))
