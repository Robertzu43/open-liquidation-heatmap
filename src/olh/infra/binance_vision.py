"""Binance USD-M public archive client (data.binance.vision).

Adapted from the author's earlier research code and republished under this
repository's MIT license. Daily and monthly klines, mark-price klines,
metrics, and funding files are read from the public archive. Zip bytes are
checked against the sidecar SHA-256 when Binance publishes one, then cached
as a zip and a parquet table.

Metrics keep both ``sum_open_interest`` and ``sum_open_interest_value``.
Open-interest deltas elsewhere in this project use the contract count. The
USD column is retained so a later check can see that a price move, not new
contracts, changed the USD figure.

The symbol universe helper ranks USDT perpetuals by trailing quote volume
as of a timestamp. Bars at or after that timestamp are excluded.
"""

from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from olh.core.errors import ExchangeError
from olh.infra.http import HttpClient

logger = logging.getLogger(__name__)

VISION = "https://data.binance.vision/data/futures/um"
_KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]


class BinanceVisionClient:
    def __init__(self, http: HttpClient | None = None, cache_dir: Path | str | None = None) -> None:
        self.http = http or HttpClient(min_interval=0.05, backoff=(1, 2, 4, 8))
        self.cache_dir = Path(cache_dir) if cache_dir else Path(".cache") / "binance-vision"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def klines(self, symbol: str, interval: str, start, end) -> pd.DataFrame:
        frames = [
            self._table(url, "klines")
            for url in iter_archive_urls("klines", symbol, interval, start, end)
        ]
        return _concat(frames, "open_time", start, end)

    def mark_klines(self, symbol: str, interval: str, start, end) -> pd.DataFrame:
        frames = [
            self._table(url, "klines")
            for url in iter_archive_urls("markPriceKlines", symbol, interval, start, end)
        ]
        return _concat(frames, "open_time", start, end)

    def metrics(self, symbol: str, start, end) -> pd.DataFrame:
        frames = [
            self._table(url, "metrics")
            for url in iter_archive_urls("metrics", symbol, None, start, end)
        ]
        return _concat(frames, "timestamp", start, end)

    def funding(self, symbol: str, start, end) -> pd.DataFrame:
        frames = [
            self._table(url, "funding")
            for url in iter_archive_urls("fundingRate", symbol, None, start, end)
        ]
        return _concat(frames, "timestamp", start, end)

    def _table(self, url: str, kind: str) -> pd.DataFrame | None:
        key = hashlib.sha256(url.encode()).hexdigest()
        parquet = self.cache_dir / f"{key}.parquet"
        zpath = self.cache_dir / f"{key}.zip"
        if parquet.is_file():
            return pd.read_parquet(parquet)
        if zpath.is_file():
            payload = zpath.read_bytes()
        else:
            response = self.http.get(url)
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                raise ExchangeError(f"HTTP {response.status_code} from {url}")
            payload = response.content
            self._check_hash(url, payload)
            zpath.write_bytes(payload)
        frame = parse_zip_csv(payload, kind)
        frame.to_parquet(parquet, index=False)
        return frame

    def _check_hash(self, url: str, payload: bytes) -> None:
        response = self.http.get(url + ".CHECKSUM")
        if response.status_code == 404:
            logger.info("no checksum sidecar for %s", url)
            return
        if response.status_code >= 400:
            raise ExchangeError(f"HTTP {response.status_code} fetching checksum for {url}")
        expected = response.text.strip().split()[0]
        actual = hashlib.sha256(payload).hexdigest()
        if expected and actual != expected:
            raise ExchangeError(f"checksum mismatch for {url}")


def iter_archive_urls(kind: str, symbol: str, interval: str | None, start, end):
    """Daily URLs for klines and metrics, monthly URLs for funding.

    The current UTC day is omitted. Binance publishes that file after the day
    closes, and the REST client is responsible for the incomplete period.
    """
    symbol = quote(symbol, safe="")
    start_ts = _utc(start)
    end_ts = _utc(end)
    today = datetime.now(timezone.utc).date()
    last = (end_ts - pd.Timedelta(milliseconds=1)).date()
    if last >= today:
        last = today - timedelta(days=1)
    if kind == "fundingRate":
        cursor = date(start_ts.year, start_ts.month, 1)
        stop = date(last.year, last.month, 1)
        while cursor <= stop:
            stamp = f"{cursor.year:04d}-{cursor.month:02d}"
            name = f"{symbol}-fundingRate-{stamp}.zip"
            yield f"{VISION}/monthly/fundingRate/{symbol}/{name}"
            cursor = date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)
        return
    day = start_ts.date()
    while day <= last:
        stamp = day.isoformat()
        if kind == "metrics":
            name = f"{symbol}-metrics-{stamp}.zip"
            yield f"{VISION}/daily/metrics/{symbol}/{name}"
        else:
            name = f"{symbol}-{interval}-{stamp}.zip"
            yield f"{VISION}/daily/{kind}/{symbol}/{interval}/{name}"
        day += timedelta(days=1)


def parse_zip_csv(payload: bytes, kind: str) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = archive.namelist()[0]
        text = archive.read(name).decode("utf-8")
    if kind == "klines":
        return parse_kline_csv(text)
    if kind == "metrics":
        return parse_metrics_csv(text)
    if kind == "funding":
        return parse_funding_csv(text)
    raise ValueError(kind)


def parse_kline_csv(text: str) -> pd.DataFrame:
    first = text.split(",", 1)[0].strip()
    header = not first.replace(".", "", 1).isdigit()
    frame = pd.read_csv(io.StringIO(text), header=0 if header else None)
    if not header:
        frame.columns = _KLINE_COLUMNS[: frame.shape[1]]
    out = pd.DataFrame(
        {
            "open_time": pd.to_datetime(pd.to_numeric(frame["open_time"]), unit="ms", utc=True),
            "open": pd.to_numeric(frame["open"]),
            "high": pd.to_numeric(frame["high"]),
            "low": pd.to_numeric(frame["low"]),
            "close": pd.to_numeric(frame["close"]),
            "volume": pd.to_numeric(frame["volume"]),
            "quote_volume": pd.to_numeric(frame["quote_volume"]) if "quote_volume" in frame.columns else 0.0,
        }
    )
    if "taker_buy_volume" in frame.columns:
        buy = pd.to_numeric(frame["taker_buy_volume"])
        volume = out["volume"].where(out["volume"] > 0)
        out["taker_buy_volume"] = buy
        out["flow_long_ratio"] = (buy / volume).clip(0, 1)
    return out.sort_values("open_time").reset_index(drop=True)


def parse_metrics_csv(text: str) -> pd.DataFrame:
    """Keep contract open interest and the USD value. Both columns are required."""
    frame = pd.read_csv(io.StringIO(text))
    missing = {"sum_open_interest", "sum_open_interest_value", "create_time"} - set(frame.columns)
    if missing:
        raise ExchangeError(f"metrics file missing {sorted(missing)}")
    out = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame["create_time"], utc=True),
            "oi_contracts": pd.to_numeric(frame["sum_open_interest"]),
            "oi_usd": pd.to_numeric(frame["sum_open_interest_value"]),
        }
    )
    if "count_long_short_ratio" in frame.columns:
        out["count_long_short_ratio"] = pd.to_numeric(frame["count_long_short_ratio"])
    if "sum_taker_long_short_vol_ratio" in frame.columns:
        out["sum_taker_long_short_vol_ratio"] = pd.to_numeric(frame["sum_taker_long_short_vol_ratio"])
    return out.sort_values("timestamp").reset_index(drop=True)


def parse_funding_csv(text: str) -> pd.DataFrame:
    frame = pd.read_csv(io.StringIO(text))
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(pd.to_numeric(frame["calc_time"]), unit="ms", utc=True),
            "funding_interval_hours": pd.to_numeric(frame["funding_interval_hours"]),
            "last_funding_rate": pd.to_numeric(frame["last_funding_rate"]),
        }
    ).sort_values("timestamp").reset_index(drop=True)


def rank_usdt_perps(
    bars: dict[str, pd.DataFrame],
    asof,
    *,
    lookback_days: int = 30,
    limit: int = 20,
) -> pd.DataFrame:
    """Point-in-time top USDT perps by trailing quote volume.

    ``bars[symbol]`` needs ``open_time`` and ``quote_volume``. Rows at or
    after ``asof`` are ignored, so the ranking does not look ahead.
    """
    asof_ts = _utc(asof)
    start = asof_ts - pd.Timedelta(days=lookback_days)
    rows = []
    for symbol, frame in bars.items():
        if frame is None or frame.empty or not str(symbol).endswith("USDT"):
            continue
        times = pd.to_datetime(frame["open_time"], utc=True)
        window = frame.loc[(times >= start) & (times < asof_ts)]
        rows.append({"symbol": symbol, "quote_volume": float(pd.to_numeric(window["quote_volume"]).sum())})
    ranked = pd.DataFrame(rows, columns=["symbol", "quote_volume"])
    if ranked.empty:
        return ranked
    ranked = ranked.sort_values(["quote_volume", "symbol"], ascending=[False, True])
    return ranked.head(limit).reset_index(drop=True)


def _concat(frames: list[pd.DataFrame | None], column: str, start, end) -> pd.DataFrame:
    present = [frame for frame in frames if frame is not None and not frame.empty]
    if not present:
        return pd.DataFrame()
    out = pd.concat(present, ignore_index=True).drop_duplicates(column).sort_values(column)
    start_ts, end_ts = _utc(start), _utc(end)
    mask = (out[column] >= start_ts) & (out[column] < end_ts)
    return out.loc[mask].reset_index(drop=True)


def _utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")
