"""Windows, alignment helpers, archive parsing, and the point-in-time universe."""

import io
import zipfile
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from olh.core.errors import ExchangeBlocked, ExchangeError
from olh.core.windows import WINDOW_PRESETS, parse_window
from olh.features.splits import long_fraction
from olh.infra.binance_vision import (
    BinanceVisionClient,
    iter_archive_urls,
    parse_metrics_csv,
    parse_zip_csv,
    rank_usdt_perps,
)
from olh.infra.diagnostics import timestamp_check
from olh.infra.events import asof_join, gap_guard, taker_share, to_5m
from olh.infra.http import HttpClient
from olh.infra.paging import paginate_forward


def test_presets_and_rejected_units():
    assert WINDOW_PRESETS["12h"].interval == "5m"
    assert WINDOW_PRESETS["24h"].interval == "15m"
    assert WINDOW_PRESETS["3d"].interval == "15m"
    assert WINDOW_PRESETS["7d"].interval == "1h"
    assert WINDOW_PRESETS["30d"].interval == "4h"
    assert WINDOW_PRESETS["90d"].interval == "4h"
    assert parse_window("36h").interval == "1h"
    for text in ("2w", "1w", "3", "2m"):
        with pytest.raises(ValueError):
            parse_window(text)


def test_long_fraction_defaults_to_even_and_blends_metrics():
    assert long_fraction("even") == 0.5
    assert long_fraction("metrics", count_long_short_ratio=3) == pytest.approx(0.75)
    blended = long_fraction("metrics", count_long_short_ratio=3, taker_long_short_ratio=1)
    assert blended == pytest.approx((0.75 + 0.5) / 2)
    assert long_fraction("metrics") == 0.5


def test_taker_share_gap_guard_and_asof():
    assert taker_share(buy=3, sell=1) == pytest.approx(0.75)
    assert taker_share(ratio=1) == pytest.approx(0.5)
    stamps = pd.to_datetime(
        ["2026-10-25T00:00:00Z", "2026-10-25T00:15:00Z", "2026-10-25T01:00:00Z"],
        utc=True,
    )
    allowed = gap_guard(pd.Series(stamps), pd.Timedelta(minutes=20))
    assert allowed.tolist() == [False, True, False]
    left = pd.DataFrame(
        {"bar_end": pd.to_datetime(["2026-10-25T00:10:00Z", "2026-10-25T00:20:00Z", "2026-10-25T02:00:00Z"], utc=True)}
    )
    right = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-10-25T00:00:00Z", "2026-10-25T00:16:00Z"], utc=True),
            "oi_contracts": [0.0, 8.0],
        }
    )
    joined = asof_join(left, right, left_on="bar_end", right_on="timestamp", tolerance=pd.Timedelta(minutes=15))
    assert joined["oi_contracts"].isna().tolist() == [True, False, True]
    assert joined.loc[1, "oi_contracts"] == 8
    bucket = to_5m(
        pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-10-25T00:01:00Z", "2026-10-25T00:04:00Z"], utc=True),
                "oi_contracts": [1.0, 4.0],
            }
        )
    )
    assert bucket["oi_contracts"].tolist() == [4.0]


def test_timestamp_kinds():
    start = pd.Timestamp("2026-10-25T00:00:00Z")
    regular = [start + pd.Timedelta(minutes=5 * i) for i in range(6)]
    report = timestamp_check(regular, pd.Timedelta(minutes=5))
    assert report["kind"] == "period_start"
    assert report["trust_deltas"] is True
    mid = [stamp + pd.Timedelta(minutes=2, seconds=30) for stamp in regular]
    snapshot = timestamp_check(mid, pd.Timedelta(minutes=5))
    assert snapshot["kind"] == "snapshot"
    assert snapshot["trust_deltas"] is True
    messy = timestamp_check(
        [start, start + pd.Timedelta(minutes=1), start + pd.Timedelta(hours=3), start + pd.Timedelta(hours=3, minutes=7)],
        pd.Timedelta(minutes=5),
    )
    assert messy["kind"] == "irregular"
    assert messy["trust_deltas"] is False


def test_metrics_parser_keeps_usd_and_ratios():
    text = (
        "create_time,symbol,sum_open_interest,sum_open_interest_value,"
        "count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
        "2026-10-25 00:00:00,BTCUSDT,10,1000,1.5,0.8\n"
    )
    frame = parse_metrics_csv(text)
    assert frame.loc[0, "oi_contracts"] == 10
    assert frame.loc[0, "oi_usd"] == 1000
    assert frame.loc[0, "count_long_short_ratio"] == 1.5
    with pytest.raises(ExchangeError):
        parse_metrics_csv("create_time,sum_open_interest\n2026-10-25 00:00:00,10\n")


def test_rank_excludes_the_asof_bar_and_non_usdt_names():
    asof = pd.Timestamp("2026-10-25T00:00:00Z")
    earlier = asof - pd.Timedelta(days=1)
    bars = {
        "BTCUSDT": pd.DataFrame({"open_time": [earlier, asof], "quote_volume": [5, 100]}),
        "ETHUSDT": pd.DataFrame({"open_time": [earlier], "quote_volume": [9]}),
        "BTCUSD": pd.DataFrame({"open_time": [earlier], "quote_volume": [50]}),
    }
    ranked = rank_usdt_perps(bars, asof, lookback_days=30, limit=20)
    assert ranked["symbol"].tolist() == ["ETHUSDT", "BTCUSDT"]
    assert ranked.loc[ranked["symbol"] == "BTCUSDT", "quote_volume"].item() == 5


def test_archive_checksum_and_current_day_is_omitted(tmp_path):
    csv = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
    csv += "1761350400000,1,2,0.5,1.5,3,1761354000000,4,1,1,2,0\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-1h-2026-10-25.csv", csv)
    payload = buffer.getvalue()
    digest = __import__("hashlib").sha256(payload).hexdigest()

    class Session:
        def __init__(self, checksum_status, checksum_text):
            self.headers = {}
            self.checksum_status = checksum_status
            self.checksum_text = checksum_text

        def get(self, url, params=None, timeout=None):
            if url.endswith(".CHECKSUM"):
                text = self.checksum_text
                body = text if isinstance(text, bytes) else text.encode()
                return _Response(self.checksum_status, body)
            return _Response(200, payload)

    good = BinanceVisionClient(
        http=HttpClient(min_interval=0, session=Session(200, f"{digest}  file.zip\n"), sleep=lambda _s: None),
        cache_dir=tmp_path / "good",
    )
    frame = good._table("https://data.binance.vision/example.zip", "klines")
    assert frame.loc[0, "close"] == 1.5
    assert "taker_buy_volume" in frame.columns

    bad = BinanceVisionClient(
        http=HttpClient(min_interval=0, session=Session(200, "deadbeef  file.zip\n"), sleep=lambda _s: None),
        cache_dir=tmp_path / "bad",
    )
    with pytest.raises(ExchangeError, match="checksum"):
        bad._table("https://data.binance.vision/example.zip", "klines")

    missing = BinanceVisionClient(
        http=HttpClient(min_interval=0, session=Session(404, b""), sleep=lambda _s: None),
        cache_dir=tmp_path / "missing",
    )
    kept = missing._table("https://data.binance.vision/example.zip", "klines")
    assert kept.loc[0, "quote_volume"] == 4
    parsed = parse_zip_csv(payload, "klines")
    assert len(parsed) == 1

    today = datetime.now(timezone.utc).date().isoformat()
    urls = list(iter_archive_urls(
        "klines",
        "BTCUSDT",
        "1h",
        "2026-10-25T00:00:00Z",
        "2026-10-28T00:00:00Z",
    ))
    assert urls
    assert all(today not in url for url in urls)
    assert "BTCUSDT-1h-2026-10-25.zip" in urls[0]


class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self.content = body
        raw = body if isinstance(body, bytes) else str(body).encode()
        self.text = raw.decode("utf-8", errors="replace")

    def json(self):
        import json
        return json.loads(self.text)


def test_http_retries_and_blocked_status():
    sleeps = []

    class Session:
        def __init__(self, codes):
            self.headers = {}
            self.codes = list(codes)

        def get(self, url, params=None, timeout=None):
            code = self.codes.pop(0)
            body = b'{"ok": true}' if code == 200 else b"no"
            return _Response(code, body)

    client = HttpClient(min_interval=0, backoff=(1, 2, 4, 8), session=Session([429, 200]), sleep=sleeps.append)
    assert client.get_json("https://example.test/a") == {"ok": True}
    assert sleeps == [1]

    sleeps.clear()
    again = HttpClient(min_interval=0, backoff=(1, 2, 4, 8), session=Session([418, 200]), sleep=sleeps.append)
    assert again.get("https://example.test/b").status_code == 200
    assert sleeps == [1]

    blocked = HttpClient(min_interval=0, session=Session([451]), sleep=sleeps.append)
    with pytest.raises(ExchangeBlocked, match="451") as exc:
        blocked.get("https://fapi.binance.com/fapi/v1/klines")
    assert "US" in str(exc.value)
    missing = HttpClient(min_interval=0, session=Session([404]), sleep=sleeps.append)
    assert missing.get("https://data.binance.vision/missing").status_code == 404


def test_paginate_forward_stitches_pages_and_stops():
    pages = {
        0: [{"t": 0}, {"t": 10}],
        11: [{"t": 11}],
    }

    def fetch(cursor, end):
        return pages.get(cursor, [])

    rows = paginate_forward(fetch, 0, 100, page_limit=2, time_of=lambda row: row["t"])
    assert [row["t"] for row in rows] == [0, 10, 11]
