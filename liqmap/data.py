"""Public-market loaders for the liquidation heatmap.

Candles and open interest come from OKX and Binance. Open interest is read in
coins, one side of each contract, so a price move alone does not look like new
positions. Binance tries the
live USDT-M API first and, if that is blocked, the public data.binance.vision
archive (cached under .cache/).

Symbols are restricted to a short ticker pattern before they are placed into a
URL, so a request cannot be turned into an SSRF fetch.
"""

from __future__ import annotations

import io
import logging
import math
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from liqmap.model import MarketSeries

log = logging.getLogger("liqmap.data")

USER_AGENT = "open-liqmap/0.2"
COINS = (
    "BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "LTC",
    "DOT", "TON", "SUI", "APT", "ARB", "OP", "NEAR", "FIL", "PEPE", "WIF",
)
EXCHANGE_NAMES = {
    "okx": "OKX",
    "binance": "Binance",
    "demo": "Demo",
    "aggregate": "Aggregated",
}
# Window length and the candle size used to build it.
RANGES: dict[str, dict] = {
    "12h": {"seconds": 12 * 3600, "bar": "5m", "bar_seconds": 300},
    "24h": {"seconds": 24 * 3600, "bar": "5m", "bar_seconds": 300},
    "3d": {"seconds": 3 * 86400, "bar": "15m", "bar_seconds": 900},
    "7d": {"seconds": 7 * 86400, "bar": "1h", "bar_seconds": 3600},
    "30d": {"seconds": 30 * 86400, "bar": "4h", "bar_seconds": 4 * 3600},
    "90d": {"seconds": 90 * 86400, "bar": "4h", "bar_seconds": 4 * 3600},
    "180d": {"seconds": 180 * 86400, "bar": "12h", "bar_seconds": 12 * 3600},
    "1y": {"seconds": 365 * 86400, "bar": "1d", "bar_seconds": 86400},
    "2y": {"seconds": 730 * 86400, "bar": "1d", "bar_seconds": 86400},
}
OKX_BAR = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "12h": "12H", "1d": "1D"}
STALE_AFTER_S = 18 * 3600
_SYMBOL_COIN = re.compile(r"^[A-Z0-9]{2,12}$")
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT})
_HEAD_CACHE: dict[str, tuple[float, bool]] = {}
_NEG_ZIP: dict[str, float] = {}
_HEAD_LOCK = threading.Lock()
_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


class DataError(Exception):
    """The request was understood, but the public data cannot build a map."""


@dataclass
class LoadedSeries:
    series: MarketSeries
    source: str
    as_of: int
    stale: bool
    venues: list[str] = field(default_factory=list)


def exchange_name(exchange: str) -> str:
    return EXCHANGE_NAMES.get(exchange, exchange.upper())


def normalize_symbol(raw: str) -> tuple[str, str]:
    """Return (coin, BTCUSDT-style pair). Rejects anything that is not a ticker."""
    text = (raw or "").strip().upper().replace("-", "").replace("_", "").replace("/", "")
    if text.endswith("USDT"):
        coin = text[:-4]
    else:
        coin = text
    if not _SYMBOL_COIN.fullmatch(coin) or coin == "USDT":
        raise DataError("Symbol must be 2–12 letters or digits, like BTC or ETHUSDT.")
    return coin, coin + "USDT"


def range_spec(range_key: str) -> dict:
    key = (range_key or "").strip().lower()
    spec = RANGES.get(key)
    if spec is None:
        choices = ", ".join(RANGES)
        raise DataError(f"Unknown range '{range_key}'. Use one of: {choices}.")
    return {"key": key, **spec}


def make_grid(end_open_s: int, n: int, bar_s: int) -> np.ndarray:
    """Bar-open timestamps in milliseconds, oldest first, length n."""
    if n < 2:
        raise DataError("The requested window is too short to build a map.")
    start = int(end_open_s) - (n - 1) * int(bar_s)
    return (start + np.arange(n, dtype=np.int64) * int(bar_s)) * 1000


def is_stale(last_open_ms: int, bar_seconds: int, now_s: float | None = None, limit_s: int = STALE_AFTER_S) -> bool:
    """True when the last bar's close is older than the live market by `limit_s`."""
    now = time.time() if now_s is None else float(now_s)
    covered_until = (int(last_open_ms) / 1000.0) + int(bar_seconds)
    return (now - covered_until) > limit_s


def ratio_to_share(ratio: float | str | None) -> float:
    """Convert a long/short ratio into the long fraction. Non-positive is missing."""
    try:
        value = float(ratio)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan
    if not math.isfinite(value) or value <= 0:
        return math.nan
    return value / (1.0 + value)


def _host(url: str) -> str:
    return url.split("/")[2] if "://" in url else url


def _cache_root() -> Path:
    root = Path(__file__).resolve().parent.parent / ".cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _file_lock(rel: str) -> threading.Lock:
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(rel)
        if lock is None:
            lock = threading.Lock()
            _FILE_LOCKS[rel] = lock
        return lock


def _get_json(url: str, params: dict | None = None, timeout: tuple[float, float] = (5, 25), retries: int = 3):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            response = _SESSION.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            last = exc
            time.sleep(0.35 * (2 ** attempt))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            last = DataError(f"{_host(url)} returned HTTP {response.status_code}")
            time.sleep(0.35 * (2 ** attempt))
            continue
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response.status_code, payload
    raise DataError(f"Could not reach {_host(url)} after {retries} tries ({last}).")


def _head_ok(url: str) -> bool:
    now = time.time()
    with _HEAD_LOCK:
        hit = _HEAD_CACHE.get(url)
        if hit and now - hit[0] < (6 * 3600 if hit[1] else 600):
            return hit[1]
    ok = False
    try:
        response = _SESSION.head(url, timeout=(4, 15), allow_redirects=True)
        length = int(response.headers.get("content-length") or "0")
        ok = response.status_code == 200 and length > 32
    except requests.RequestException:
        ok = False
    with _HEAD_LOCK:
        _HEAD_CACHE[url] = (now, ok)
    return ok


def _cached_zip(url: str, rel: str) -> bytes | None:
    now = time.time()
    with _HEAD_LOCK:
        missed = _NEG_ZIP.get(url)
        if missed is not None and now - missed < 900:
            return None
    path = _cache_root() / rel
    if path.is_file() and path.stat().st_size > 32:
        blob = path.read_bytes()
        if blob[:2] == b"PK":
            return blob
    with _file_lock(rel):
        if path.is_file() and path.stat().st_size > 32:
            blob = path.read_bytes()
            if blob[:2] == b"PK":
                return blob
        try:
            response = _SESSION.get(
                url,
                timeout=(5, 90),
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            )
        except requests.RequestException as exc:
            raise DataError(f"Could not download the Binance archive ({exc.__class__.__name__}).") from exc
        if response.status_code == 404:
            with _HEAD_LOCK:
                _NEG_ZIP[url] = time.time()
            return None
        if response.status_code != 200 or response.content[:2] != b"PK":
            raise DataError(f"Binance archive returned HTTP {response.status_code} for {path.name}.")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(response.content)
        tmp.replace(path)
        return response.content


def _zip_text(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = archive.namelist()
        if not names:
            raise DataError("Binance archive zip was empty.")
        return archive.read(names[0]).decode("utf-8", "replace")


def fold_candles(records: list[tuple], grid_ms: np.ndarray, bar_ms: int) -> tuple[np.ndarray, ...]:
    """Aggregate (open_ms, o, h, l, c, volume) rows into the grid. Empty bins stay NaN."""
    n = len(grid_ms)
    open_ = np.full(n, np.nan)
    high = np.full(n, np.nan)
    low = np.full(n, np.nan)
    close = np.full(n, np.nan)
    volume = np.zeros(n, dtype=np.float64)
    seen = np.zeros(n, dtype=bool)
    if n == 0:
        return open_, high, low, close, volume
    starts = np.asarray(grid_ms, dtype=np.int64)
    for open_ms, o, h, l, c, vol in records:
        ts = int(open_ms)
        index = int(np.searchsorted(starts, ts, side="right") - 1)
        if index < 0 or index >= n or ts >= int(starts[index]) + bar_ms:
            continue
        if not (math.isfinite(o) and math.isfinite(h) and math.isfinite(l) and math.isfinite(c)):
            continue
        if not seen[index]:
            open_[index] = o
            high[index] = h
            low[index] = l
            seen[index] = True
        else:
            high[index] = max(high[index], h)
            low[index] = min(low[index], l)
        close[index] = c
        if math.isfinite(vol) and vol > 0:
            volume[index] += vol
    return open_, high, low, close, volume


def _nearest_open_index(starts: np.ndarray, ts: int) -> int:
    """Index of the candle open nearest ``ts``. A tie stays on the earlier open."""
    index = int(np.searchsorted(starts, ts, side="left"))
    if index <= 0:
        return 0
    if index >= len(starts):
        return len(starts) - 1
    if abs(int(starts[index]) - ts) < abs(ts - int(starts[index - 1])):
        return index
    return index - 1


def fold_oi(records: list[tuple], grid_ms: np.ndarray, bar_ms: int) -> tuple[np.ndarray, np.ndarray]:
    """Last open interest and last long-share on the nearest candle open.

    A print more than half a bar from every open is left out. On hourly
    candles, ten minutes after an open stays on that candle, and fifty
    minutes after that open lands on the next candle.
    """
    n = len(grid_ms)
    oi = np.full(n, np.nan)
    share = np.full(n, np.nan)
    if n == 0:
        return oi, share
    starts = np.asarray(grid_ms, dtype=np.int64)
    half = bar_ms / 2.0
    for open_ms, oi_coin, long_share in records:
        ts = int(open_ms)
        index = _nearest_open_index(starts, ts)
        if abs(int(starts[index]) - ts) > half:
            continue
        try:
            oi_value = float(oi_coin)
        except (TypeError, ValueError):
            oi_value = math.nan
        if math.isfinite(oi_value) and oi_value >= 0:
            oi[index] = oi_value
        try:
            share_value = float(long_share)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            share_value = math.nan
        if math.isfinite(share_value):
            share[index] = share_value
    return oi, share


def _limited_ffill(values: np.ndarray, limit: int) -> np.ndarray:
    out = values.astype(np.float64, copy=True)
    last = math.nan
    run = 0
    for i in range(len(out)):
        if math.isfinite(out[i]):
            last = float(out[i])
            run = 0
            continue
        if math.isfinite(last) and run < limit:
            run += 1
            out[i] = last
        else:
            last = math.nan
            run = 0
    return out


def finalize_series(
    *,
    times: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    oi: np.ndarray,
    share: np.ndarray,
    exchange: str,
    symbol: str,
    coin: str,
    bar_seconds: int,
    source: str,
    note: str,
    warnings: list[str],
    venues: list[str] | None = None,
    now_s: float | None = None,
) -> LoadedSeries:
    finite = np.isfinite(close) & (close > 0) & np.isfinite(high) & np.isfinite(low) & np.isfinite(open_)
    if int(finite.sum()) < 2:
        raise DataError(f"Not enough {exchange_name(exchange)} candles for {symbol} in this window.")
    idx = np.flatnonzero(finite)
    sl = slice(int(idx[0]), int(idx[-1]) + 1)
    times_s = np.asarray(times[sl], dtype=np.int64)
    open_s = np.asarray(open_[sl], dtype=np.float64)
    high_s = np.asarray(high[sl], dtype=np.float64)
    low_s = np.asarray(low[sl], dtype=np.float64)
    close_s = np.asarray(close[sl], dtype=np.float64)
    volume_s = np.nan_to_num(np.asarray(volume[sl], dtype=np.float64), nan=0.0)
    oi_s = _limited_ffill(np.asarray(oi[sl], dtype=np.float64), limit=3)
    share_s = _limited_ffill(np.asarray(share[sl], dtype=np.float64), limit=3)
    for i in range(len(close_s)):
        if math.isfinite(close_s[i]) and close_s[i] > 0:
            continue
        if i == 0:
            continue
        close_s[i] = close_s[i - 1]
        open_s[i] = close_s[i]
        high_s[i] = close_s[i]
        low_s[i] = close_s[i]
    # A crossed candle breaks the price range check in the model.
    high_s = np.maximum(high_s, np.maximum(open_s, close_s))
    low_s = np.minimum(low_s, np.minimum(open_s, close_s))
    if np.nanmax(high_s) <= np.nanmin(low_s):
        high_s = np.maximum(high_s, close_s * 1.0001)
        low_s = np.minimum(low_s, close_s * 0.9999)
    coverage = float(np.isfinite(oi_s).mean()) if len(oi_s) else 0.0
    if coverage <= 0:
        raise DataError(f"{exchange_name(exchange)} returned candles for {symbol} but no open interest.")
    notes = list(warnings)
    if coverage < 0.5:
        notes.append(f"Open interest history covers about {coverage:.0%} of this window.")
    as_of = int(times_s[-1])
    stale = is_stale(as_of, bar_seconds, now_s=now_s)
    series = MarketSeries(
        times=times_s,
        open=open_s,
        high=high_s,
        low=low_s,
        close=close_s,
        volume_usd=volume_s,
        oi_coin=oi_s,
        long_share=share_s,
        exchange=exchange,
        symbol=symbol,
        coin=coin,
        bar_seconds=int(bar_seconds),
        note=note,
        warnings=notes,
    )
    return LoadedSeries(
        series=series,
        source=source,
        as_of=as_of,
        stale=stale,
        venues=list(venues or [exchange]),
    )


def _fmt_cot(ms: int) -> str:
    stamp = datetime.fromtimestamp(ms / 1000.0, tz=timezone(timedelta(hours=-5)))
    return stamp.strftime("%d %b %Y, %H:%M COT")


def _records_from_series(series: MarketSeries) -> tuple[list[tuple], list[tuple]]:
    candles = []
    ois = []
    for i in range(len(series.times)):
        candles.append(
            (
                int(series.times[i]),
                float(series.open[i]),
                float(series.high[i]),
                float(series.low[i]),
                float(series.close[i]),
                float(series.volume_usd[i]),
            )
        )
        share = float(series.long_share[i])
        ois.append((int(series.times[i]), float(series.oi_coin[i]), share))
    return candles, ois


def combine_series(parts: list[MarketSeries], grid_ms: np.ndarray, bar_seconds: int, coin: str) -> MarketSeries:
    """Sum open interest onto one grid. Candles come from the freshest, busiest venue."""
    if not parts:
        raise DataError("No exchange series were available to combine.")
    bar_ms = int(bar_seconds) * 1000
    n = len(grid_ms)

    def rank(series: MarketSeries) -> tuple:
        return (int(series.times[-1]), float(np.nansum(series.volume_usd)))

    donor = max(parts, key=rank)
    donor_candles, _ = _records_from_series(donor)
    open_, high, low, close, _donor_vol = fold_candles(donor_candles, grid_ms, bar_ms)
    oi_sum = np.zeros(n, dtype=np.float64)
    oi_seen = np.zeros(n, dtype=bool)
    share_acc = np.zeros(n, dtype=np.float64)
    share_w = np.zeros(n, dtype=np.float64)
    volume = np.zeros(n, dtype=np.float64)
    used = []
    for part in parts:
        candles, ois = _records_from_series(part)
        _o, _h, _l, _cl, vol = fold_candles(candles, grid_ms, bar_ms)
        oi, share = fold_oi(ois, grid_ms, bar_ms)
        present = np.isfinite(oi)
        if not present.any() and not np.isfinite(_cl).any():
            continue
        used.append(part.exchange)
        oi_sum[present] += oi[present]
        oi_seen |= present
        volume += vol
        both = present & np.isfinite(share)
        share_acc[both] += share[both] * oi[both]
        share_w[both] += oi[both]
    if not used:
        raise DataError("The exchanges returned no overlapping candles or open interest.")
    oi_out = np.where(oi_seen, oi_sum, np.nan)
    share_out = np.full(n, np.nan)
    good = share_w > 0
    share_out[good] = share_acc[good] / share_w[good]
    names = ", ".join(exchange_name(item) for item in used)
    return MarketSeries(
        times=np.asarray(grid_ms, dtype=np.int64),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume_usd=volume,
        oi_coin=oi_out,
        long_share=share_out,
        exchange="aggregate",
        symbol=f"{coin}USDT",
        coin=coin,
        bar_seconds=int(bar_seconds),
        note=f"Aggregated from {names}.",
        warnings=[],
    )


def _window(spec: dict, end_open_s: int | None = None) -> np.ndarray:
    bar = int(spec["bar_seconds"])
    n = int(spec["seconds"]) // bar
    if end_open_s is None:
        end_open_s = int(time.time()) // bar * bar
    return make_grid(int(end_open_s), n, bar)


def _okx_pages(path: str, params: dict, start_ms: int, limit: int, cursor_key: str) -> list:
    rows: list = []
    seen: set[int] = set()
    cursor: int | None = None
    instrument_error = False
    for _ in range(48):
        query = dict(params)
        query["limit"] = str(limit)
        if cursor is not None:
            query[cursor_key] = str(cursor)
        status, payload = _get_json(f"https://www.okx.com{path}", query)
        if not isinstance(payload, dict):
            raise DataError("OKX returned an unexpected response.")
        code = str(payload.get("code"))
        if code == "51001":
            instrument_error = True
            break
        if status != 200 or code != "0":
            message = str(payload.get("msg") or f"HTTP {status}")
            if rows:
                break
            raise DataError(f"OKX rejected the request ({message}).")
        batch = payload.get("data") or []
        if not batch:
            break
        oldest = None
        added = 0
        for item in batch:
            ts = int(item[0])
            if ts in seen:
                continue
            seen.add(ts)
            rows.append(item)
            added += 1
            oldest = ts if oldest is None else min(oldest, ts)
        if added == 0 or oldest is None or oldest <= start_ms:
            break
        if oldest == cursor:
            break
        cursor = oldest
    if instrument_error and not rows:
        inst = params.get("instId") or params.get("ccy") or "that symbol"
        raise DataError(f"OKX has no {inst} perpetual swap.")
    rows.sort(key=lambda item: int(item[0]))
    return rows


def fetch_okx(coin: str, pair: str, spec: dict) -> LoadedSeries:
    bar = OKX_BAR[spec["bar"]]
    inst = f"{coin}-USDT-SWAP"
    grid = _window(spec)
    start_ms = int(grid[0]) - int(spec["bar_seconds"]) * 1000
    end_ms = int(grid[-1]) + int(spec["bar_seconds"]) * 1000
    candles = _okx_pages(
        "/api/v5/market/candles",
        {"instId": inst, "bar": bar},
        start_ms,
        300,
        "after",
    )
    if not candles or int(candles[0][0]) > start_ms:
        try:
            older = _okx_pages(
                "/api/v5/market/history-candles",
                {"instId": inst, "bar": bar},
                start_ms,
                300,
                "after",
            )
        except DataError as exc:
            log.info("okx history candles unavailable: %s", exc)
            older = []
        merged = {int(row[0]): row for row in older}
        for row in candles:
            merged[int(row[0])] = row
        candles = [merged[key] for key in sorted(merged)]
    oi_rows = _okx_pages(
        "/api/v5/rubik/stat/contracts/open-interest-history",
        {"instId": inst, "period": bar},
        start_ms,
        100,
        "end",
    )
    taker_rows = []
    try:
        taker_rows = _okx_pages(
            "/api/v5/rubik/stat/taker-volume-contract",
            {"instId": inst, "period": bar},
            start_ms,
            100,
            "end",
        )
    except DataError as exc:
        log.info("okx taker unavailable: %s", exc)
    if not candles:
        raise DataError(f"OKX returned no {inst} candles for this window.")
    if not oi_rows:
        raise DataError(f"OKX returned no {inst} open interest for this window.")
    taker_at: dict[int, float] = {}
    for row in taker_rows:
        # [ts, sellVol, buyVol]
        try:
            sell = float(row[1])
            buy = float(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        total = buy + sell
        if total > 0:
            taker_at[int(row[0])] = buy / total
    taker_times = sorted(taker_at)
    bar_ms = int(spec["bar_seconds"]) * 1000

    def share_near(ts: int) -> float:
        if not taker_times:
            return math.nan
        pos = int(np.searchsorted(taker_times, ts))
        best = None
        best_dist = bar_ms + 1
        for candidate in (pos - 1, pos):
            if 0 <= candidate < len(taker_times):
                dist = abs(taker_times[candidate] - ts)
                if dist < best_dist:
                    best_dist = dist
                    best = taker_times[candidate]
        if best is None or best_dist > bar_ms:
            return math.nan
        return taker_at[best]

    candle_records = []
    for row in candles:
        ts = int(row[0])
        if ts < start_ms or ts >= end_ms:
            continue
        try:
            candle_records.append(
                (ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[7]))
            )
        except (TypeError, ValueError, IndexError):
            continue
    oi_records = []
    for row in oi_rows:
        ts = int(row[0])
        if ts < start_ms or ts >= end_ms:
            continue
        try:
            # [ts, oi contracts, oiCcy coins, oiUsd]
            oi_coin = float(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        oi_records.append((ts, oi_coin, share_near(ts)))
    open_, high, low, close, volume = fold_candles(candle_records, grid, bar_ms)
    oi, share = fold_oi(oi_records, grid, bar_ms)
    loaded = finalize_series(
        times=grid,
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        oi=oi,
        share=share,
        exchange="okx",
        symbol=pair,
        coin=coin,
        bar_seconds=int(spec["bar_seconds"]),
        source="okx",
        note="",
        warnings=[],
    )
    log.info("okx %s %s bars=%s", pair, spec["key"], len(loaded.series.times))
    return loaded



def _binance_kline_url(symbol: str, interval: str, year: int, month: int, day: int | None = None) -> tuple[str, str]:
    if day is None:
        name = f"{symbol}-{interval}-{year}-{month:02d}.zip"
        url = f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/{interval}/{name}"
        rel = f"binance/klines/{symbol}/{name}"
    else:
        name = f"{symbol}-{interval}-{year}-{month:02d}-{day:02d}.zip"
        url = f"https://data.binance.vision/data/futures/um/daily/klines/{symbol}/{interval}/{name}"
        rel = f"binance/klines/{symbol}/{name}"
    return url, rel


def _binance_metrics_url(symbol: str, day: datetime) -> tuple[str, str]:
    name = f"{symbol}-metrics-{day:%Y-%m-%d}.zip"
    url = f"https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{name}"
    return url, f"binance/metrics/{symbol}/{name}"


def _parse_kline_csv(text: str) -> list[tuple]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line[0].isdigit():
            continue
        parts = line.split(",")
        if len(parts) < 8:
            continue
        try:
            open_ms = int(float(parts[0]))
            rows.append(
                (open_ms, float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]), float(parts[7]))
            )
        except ValueError:
            continue
    return rows


def _parse_metrics_csv(text: str) -> list[tuple]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = [cell.strip() for cell in lines[0].split(",")]
    index = {name: pos for pos, name in enumerate(header)}
    if "create_time" not in index or "sum_open_interest" not in index:
        return []
    rows = []
    for line in lines[1:]:
        parts = line.split(",")
        try:
            stamp = parts[index["create_time"]].strip()
            if stamp.isdigit():
                open_ms = int(stamp)
                if open_ms < 10_000_000_000:
                    open_ms *= 1000
            else:
                parsed = datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                open_ms = int(parsed.timestamp() * 1000)
            oi_coin = float(parts[index["sum_open_interest"]])
            taker = parts[index["sum_taker_long_short_vol_ratio"]] if "sum_taker_long_short_vol_ratio" in index else ""
            account = parts[index["count_long_short_ratio"]] if "count_long_short_ratio" in index else ""
            share = ratio_to_share(taker)
            if not math.isfinite(share):
                share = ratio_to_share(account)
            rows.append((open_ms, oi_coin, share))
        except (ValueError, IndexError, KeyError):
            continue
    return rows


def _months_between(start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    start = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).date().replace(day=1)
    end = datetime.fromtimestamp(max(start_ms, end_ms - 1) / 1000.0, tz=timezone.utc).date().replace(day=1)
    months = []
    cursor = start
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return months


def _dates_between(start_ms: int, end_ms: int) -> list[datetime]:
    start = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).date()
    end = datetime.fromtimestamp(max(start_ms, end_ms - 1) / 1000.0, tz=timezone.utc).date()
    days = []
    cursor = start
    while cursor <= end:
        days.append(datetime(cursor.year, cursor.month, cursor.day, tzinfo=timezone.utc))
        cursor += timedelta(days=1)
    return days


def _load_vision_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[tuple]:
    rows: list[tuple] = []

    def daily_month(year: int, month: int) -> list[tuple]:
        found = []
        for day in _dates_between(
            int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1000),
            int((datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)).timestamp() * 1000),
        ):
            if day.year != year or day.month != month:
                continue
            if int(day.timestamp() * 1000) > end_ms or int((day + timedelta(days=1)).timestamp() * 1000) < start_ms:
                continue
            url, rel = _binance_kline_url(symbol, interval, year, month, day.day)
            blob = _cached_zip(url, rel)
            if blob is None:
                continue
            found.extend(_parse_kline_csv(_zip_text(blob)))
        return found

    for year, month in _months_between(start_ms, end_ms):
        url, rel = _binance_kline_url(symbol, interval, year, month, None)
        blob = _cached_zip(url, rel)
        if blob is None:
            rows.extend(daily_month(year, month))
        else:
            rows.extend(_parse_kline_csv(_zip_text(blob)))
    dedup = {row[0]: row for row in rows if start_ms - 86_400_000 <= row[0] < end_ms}
    return [dedup[key] for key in sorted(dedup)]


def _load_vision_metrics(symbol: str, start_ms: int, end_ms: int) -> list[tuple]:
    days = _dates_between(start_ms, end_ms)

    def one(day: datetime) -> list[tuple]:
        url, rel = _binance_metrics_url(symbol, day)
        blob = _cached_zip(url, rel)
        if blob is None:
            return []
        return _parse_metrics_csv(_zip_text(blob))

    if len(days) <= 2:
        groups = [one(day) for day in days]
    else:
        workers = min(8, len(days))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            groups = list(pool.map(one, days))
    dedup = {}
    for group in groups:
        for row in group:
            if start_ms - 86_400_000 <= row[0] < end_ms:
                dedup[row[0]] = row
    return [dedup[key] for key in sorted(dedup)]


def _latest_archive_day(symbol: str, interval: str) -> datetime | None:
    today = datetime.now(timezone.utc).date()
    for back in range(0, 6):
        day = today - timedelta(days=back)
        stamp = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        kline_url, _rel = _binance_kline_url(symbol, interval, day.year, day.month, day.day)
        metrics_url, _mrel = _binance_metrics_url(symbol, stamp)
        if _head_ok(kline_url) and _head_ok(metrics_url):
            return stamp
    return None


def _try_binance_live(coin: str, pair: str, spec: dict) -> LoadedSeries | None:
    bar = spec["bar"]
    grid = _window(spec)
    start_ms = int(grid[0])
    end_ms = int(grid[-1]) + int(spec["bar_seconds"]) * 1000
    base = "https://fapi.binance.com"
    try:
        status, klines = _get_json(
            f"{base}/fapi/v1/klines",
            {"symbol": pair, "interval": bar, "startTime": start_ms, "endTime": end_ms, "limit": 1500},
            timeout=(4, 8),
            retries=1,
        )
    except DataError:
        return None
    if status in (202, 451) or status >= 400 or not isinstance(klines, list) or len(klines) < 2:
        return None
    oi_rows: list[dict] = []
    cursor = start_ms
    for _ in range(12):
        try:
            status, batch = _get_json(
                f"{base}/futures/data/openInterestHist",
                {"symbol": pair, "period": bar, "startTime": cursor, "endTime": end_ms, "limit": 500},
                timeout=(4, 8),
                retries=1,
            )
        except DataError:
            return None
        if status >= 400 or not isinstance(batch, list) or not batch:
            break
        oi_rows.extend(batch)
        last = int(batch[-1].get("timestamp") or 0)
        if last <= cursor or last >= end_ms:
            break
        cursor = last + 1
    if len(oi_rows) < 2:
        return None
    taker_at: dict[int, float] = {}
    cursor = start_ms
    for _ in range(12):
        try:
            status, batch = _get_json(
                f"{base}/futures/data/takerlongshortRatio",
                {"symbol": pair, "period": bar, "startTime": cursor, "endTime": end_ms, "limit": 500},
                timeout=(4, 8),
                retries=1,
            )
        except DataError:
            break
        if status >= 400 or not isinstance(batch, list) or not batch:
            break
        for row in batch:
            try:
                buy = float(row.get("buyVol"))
                sell = float(row.get("sellVol"))
            except (TypeError, ValueError):
                continue
            total = buy + sell
            ts = int(row.get("timestamp") or 0)
            if total > 0 and ts:
                taker_at[ts] = buy / total
        last = int(batch[-1].get("timestamp") or 0)
        if last <= cursor or last >= end_ms:
            break
        cursor = last + 1
    candle_records = []
    for row in klines:
        try:
            candle_records.append(
                (int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[7]))
            )
        except (TypeError, ValueError, IndexError):
            continue
    oi_records = []
    for row in oi_rows:
        try:
            ts = int(row["timestamp"])
            oi_records.append((ts, float(row["sumOpenInterest"]), taker_at.get(ts, math.nan)))
        except (KeyError, TypeError, ValueError):
            continue
    bar_ms = int(spec["bar_seconds"]) * 1000
    open_, high, low, close, volume = fold_candles(candle_records, grid, bar_ms)
    oi, share = fold_oi(oi_records, grid, bar_ms)
    try:
        loaded = finalize_series(
            times=grid,
            open_=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            oi=oi,
            share=share,
            exchange="binance",
            symbol=pair,
            coin=coin,
            bar_seconds=int(spec["bar_seconds"]),
            source="binance-live",
            note="",
            warnings=[],
        )
    except DataError:
        return None
    log.info("binance live %s %s bars=%s", pair, spec["key"], len(loaded.series.times))
    return loaded


def fetch_binance_vision(coin: str, pair: str, spec: dict, *, allow_stale: bool) -> LoadedSeries:
    latest = _latest_archive_day(pair, spec["bar"])
    if latest is None:
        raise DataError(f"Binance has no public futures archive for {pair}. Check the coin.")
    bar_s = int(spec["bar_seconds"])
    end_open_now = int(time.time()) // bar_s * bar_s
    day_last = int(latest.timestamp()) + 86400 - bar_s
    end_open = min(end_open_now, day_last)
    if not allow_stale and is_stale(end_open * 1000, bar_s):
        when = _fmt_cot((end_open + bar_s) * 1000)
        raise DataError(
            f"Binance was left out of this aggregate because the public archive lags the live market (last bar ends {when})."
        )
    grid = _window(spec, end_open_s=end_open)
    start_ms = int(grid[0])
    end_ms = int(grid[-1]) + bar_s * 1000
    candles = _load_vision_klines(pair, spec["bar"], start_ms, end_ms)
    metrics = _load_vision_metrics(pair, start_ms, end_ms)
    if len(candles) < 2:
        raise DataError(f"Binance archive has no {pair} {spec['bar']} candles for this window.")
    if len(metrics) < 2:
        raise DataError(f"Binance archive has no {pair} open-interest metrics for this window.")
    bar_ms = bar_s * 1000
    open_, high, low, close, volume = fold_candles(candles, grid, bar_ms)
    oi, share = fold_oi(metrics, grid, bar_ms)
    loaded = finalize_series(
        times=grid,
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        oi=oi,
        share=share,
        exchange="binance",
        symbol=pair,
        coin=coin,
        bar_seconds=bar_s,
        source="binance-vision",
        note="Built from the public Binance market archive because the live futures API was unavailable.",
        warnings=[],
    )
    if loaded.stale:
        when = _fmt_cot(loaded.as_of + bar_s * 1000)
        loaded.series.warnings.append(
            f"Binance live futures data was unavailable, so this is the public archive. Last bar ends {when}."
        )
        if not allow_stale:
            raise DataError(loaded.series.warnings[-1])
    log.info("binance vision %s %s bars=%s stale=%s", pair, spec["key"], len(loaded.series.times), loaded.stale)
    return loaded


def fetch_binance(coin: str, pair: str, spec: dict, *, allow_stale: bool = True) -> LoadedSeries:
    live = _try_binance_live(coin, pair, spec)
    if live is not None and (allow_stale or not live.stale):
        return live
    if live is not None and live.stale and not allow_stale:
        raise DataError("Binance data is behind the live market.")
    return fetch_binance_vision(coin, pair, spec, allow_stale=allow_stale)


def fetch_demo(coin: str, pair: str, spec: dict) -> LoadedSeries:
    bar_s = int(spec["bar_seconds"])
    grid = _window(spec)
    n = len(grid)
    seed = zlib_crc(f"{coin}:{spec['key']}")
    rng = np.random.default_rng(seed)
    base = {"BTC": 82000.0, "ETH": 3200.0, "SOL": 175.0, "XRP": 0.62, "DOGE": 0.19, "BNB": 600.0}.get(coin, 25.0)
    t = np.linspace(0, 4 * math.pi, n)
    shock = np.cumsum(rng.normal(0, 0.00035, size=n))
    rel = 0.03 * np.sin(t) + 0.012 * np.sin(t * 0.47 + 0.8) + shock
    rel = rel - rel[-1] * 0.85
    close = base * (1.0 + rel)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    wick = base * 0.0015 * (0.4 + rng.random(n))
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - wick
    oi0 = max(base * 18000.0, 50_000.0)
    steps = rng.lognormal(mean=math.log(oi0 * 0.006), sigma=0.25, size=n)
    signs = np.where(rng.random(n) < 0.16, -0.55, 1.0)
    oi = np.maximum(oi0 * 0.35, oi0 + np.cumsum(steps * signs))
    share = 0.5 + 0.2 * np.sin(np.linspace(0, 5.5, n))
    volume = steps * rng.uniform(6.0, 16.0, size=n)
    loaded = finalize_series(
        times=grid,
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        oi=oi / close,
        share=share,
        exchange="demo",
        symbol=pair,
        coin=coin,
        bar_seconds=bar_s,
        source="demo",
        note="Demo prices and open interest are synthetic. No exchange was contacted.",
        warnings=["Demo prices and open interest are synthetic. No exchange was contacted."],
    )
    loaded.stale = False
    return loaded


def zlib_crc(text: str) -> int:
    import zlib

    return zlib.crc32(text.encode()) & 0xFFFFFFFF


def fetch_aggregate(coin: str, pair: str, spec: dict) -> LoadedSeries:
    errors: list[str] = []
    loaded_parts: list[LoadedSeries] = []

    def run(name: str):
        if name == "okx":
            return fetch_okx(coin, pair, spec)
        return fetch_binance(coin, pair, spec, allow_stale=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {name: pool.submit(run, name) for name in ("okx", "binance")}
        for name, future in futures.items():
            try:
                loaded_parts.append(future.result())
            except DataError as exc:
                text = str(exc)
                errors.append(text if text.startswith(exchange_name(name)) else f"{exchange_name(name)}: {text}")
            except Exception as exc:  # noqa: BLE001 — one venue must not sink the aggregate
                log.exception("aggregate %s failed", name)
                errors.append(f"{exchange_name(name)}: {exc.__class__.__name__}")
    fresh = []
    for part in loaded_parts:
        if part.source == "binance-vision" and part.stale:
            when = _fmt_cot(part.as_of + part.series.bar_seconds * 1000)
            errors.append(f"Binance was left out of this aggregate because the public archive lags the live market (last bar ends {when}).")
            continue
        fresh.append(part)
    if not fresh:
        detail = " ".join(errors) or "every venue failed"
        raise DataError(f"No fresh open interest was available for {coin}. {detail}")
    # Common grid is the freshest venue's own timestamps when they share a bar size.
    donor = max(fresh, key=lambda item: int(item.series.times[-1]))
    grid = np.asarray(donor.series.times, dtype=np.int64)
    # If the donor is shorter than the requested window, still use its real timestamps.
    combined = combine_series([item.series for item in fresh], grid, int(spec["bar_seconds"]), coin)
    warnings = list(combined.warnings)
    for part in fresh:
        warnings.extend(part.series.warnings)
    for message in errors:
        if message not in warnings:
            warnings.append(message)
    venues = [item.series.exchange for item in fresh]
    names = ", ".join(exchange_name(item) for item in venues)
    note = f"Symbol aggregate of {names}. Price candles are {exchange_name(donor.series.exchange)}; open interest is the sum."
    loaded = finalize_series(
        times=combined.times,
        open_=combined.open,
        high=combined.high,
        low=combined.low,
        close=combined.close,
        volume=combined.volume_usd,
        oi=combined.oi_coin,
        share=combined.long_share,
        exchange="aggregate",
        symbol=pair,
        coin=coin,
        bar_seconds=int(spec["bar_seconds"]),
        source="aggregate",
        note=note,
        warnings=warnings,
        venues=venues,
    )
    return loaded


def load_market(exchange: str, symbol: str, range_key: str, mode: str = "pair") -> LoadedSeries:
    mode_key = (mode or "pair").strip().lower()
    exchange_key = (exchange or "okx").strip().lower()
    if mode_key not in {"pair", "symbol"}:
        raise DataError("Mode must be pair or symbol.")
    if not re.fullmatch(r"[a-z0-9_-]{2,20}", exchange_key):
        raise DataError("Unknown exchange. Use okx, binance, or demo.")
    spec = range_spec(range_key)
    coin, pair = normalize_symbol(symbol)
    if mode_key == "symbol" or exchange_key == "aggregate":
        return fetch_aggregate(coin, pair, spec)
    if exchange_key == "demo":
        return fetch_demo(coin, pair, spec)
    if exchange_key == "okx":
        return fetch_okx(coin, pair, spec)
    if exchange_key == "binance":
        return fetch_binance(coin, pair, spec, allow_stale=True)
    raise DataError("Unknown exchange. Use okx, binance, or demo.")
