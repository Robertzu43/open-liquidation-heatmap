"""Bybit v5 public linear data. api.bytick.com is tried after api.bybit.com.

A 403 or 451 from both hosts is a blocked venue, not a fatal run.
"""

from __future__ import annotations

import logging

import pandas as pd

from olh.core.errors import ExchangeBlocked, ExchangeError
from olh.exchanges.base import Capabilities, MarketSeries
from olh.infra.http import HttpClient

logger = logging.getLogger(__name__)

HOSTS = ("https://api.bybit.com", "https://api.bytick.com")
_KLINE = {
    "5m": "5", "15m": "15", "30m": "30", "1h": "60", "2h": "120",
    "4h": "240", "6h": "360", "12h": "720", "1d": "D",
}
_OI = {
    "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "2h": "1h",
    "4h": "4h", "6h": "1h", "12h": "4h", "1d": "1d",
}


class BybitAdapter:
    name = "bybit"
    capabilities = Capabilities(
        klines=True,
        open_interest=True,
        mark_price=True,
        account_ratio=True,
        risk_tiers=False,
        oi_history="linear open interest history is available for the life of the symbol when REST is reachable",
    )

    def __init__(self, http: HttpClient | None = None, max_pages: int = 40) -> None:
        self.http = http or HttpClient(min_interval=0.12)
        self.max_pages = max_pages

    def fetch(self, symbol, start, end, interval: str) -> MarketSeries:
        if interval not in _KLINE:
            raise ExchangeError(f"Bybit has no interval {interval}")
        errors = []
        for host in HOSTS:
            try:
                return self._fetch_host(host, symbol.bybit, start, end, interval)
            except ExchangeBlocked as exc:
                errors.append(str(exc))
                logger.warning("Bybit host blocked: %s", exc)
        raise ExchangeBlocked(errors[-1] if errors else "Bybit REST is blocked")

    def _fetch_host(self, host: str, symbol: str, start, end, interval: str) -> MarketSeries:
        warnings = [
            "Bybit linear open interest is published in base coin. New positions are that contract change valued at the entry price."
        ]
        candles = self._pages_kline(host, "/v5/market/kline", symbol, _KLINE[interval], start, end)
        mark = self._pages_kline(host, "/v5/market/mark-price-kline", symbol, _KLINE[interval], start, end)
        candles = _attach_mark(parse_bybit_klines(candles), parse_bybit_klines(mark))
        oi = parse_bybit_oi(self._pages_oi(host, symbol, _OI[interval], start, end))
        max_leverage, tick, note = self._instrument(host, symbol)
        if note:
            warnings.append(note)
        ratio_note, ratio = self._ratio(host, symbol, _OI[interval], start, end)
        if ratio_note:
            warnings.append(ratio_note)
        if not ratio.empty:
            oi = oi.merge(ratio, on="timestamp", how="left")
        candles = _clip(candles, "open_time", start, end)
        oi = _clip(oi, "timestamp", start, end)
        if candles.empty or oi.empty:
            raise ExchangeError("Bybit returned no candles or open interest in this window")
        return MarketSeries(
            exchange=self.name,
            candles=candles,
            oi=oi,
            max_leverage=max_leverage or 100.0,
            tick_size=tick,
            warnings=warnings,
        )

    def _pages_kline(self, host, path, symbol, interval, start, end) -> list:
        end_ms = int(_utc(end).timestamp() * 1000) - 1
        start_ms = int(_utc(start).timestamp() * 1000)
        rows: list = []
        for _ in range(self.max_pages):
            payload = _result(self.http.get_json(
                f"{host}{path}",
                {"category": "linear", "symbol": symbol, "interval": interval, "start": start_ms, "end": end_ms, "limit": 1000},
            ))
            batch = payload.get("list") or []
            if not batch:
                break
            rows.extend(batch)
            oldest = min(int(item[0]) for item in batch)
            if oldest <= start_ms or len(batch) < 1000 or oldest >= end_ms:
                break
            end_ms = oldest - 1
        return rows

    def _pages_oi(self, host, symbol, interval, start, end) -> list:
        params = {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": interval,
            "startTime": int(_utc(start).timestamp() * 1000),
            "endTime": int(_utc(end).timestamp() * 1000) - 1,
            "limit": 200,
        }
        rows: list = []
        cursor = None
        for _ in range(self.max_pages):
            if cursor:
                params["cursor"] = cursor
            payload = _result(self.http.get_json(f"{host}/v5/market/open-interest", params))
            batch = payload.get("list") or []
            rows.extend(batch)
            cursor = payload.get("nextPageCursor") or ""
            if not batch or not cursor or len(batch) < 200:
                break
        return rows

    def _instrument(self, host, symbol):
        try:
            payload = _result(self.http.get_json(
                f"{host}/v5/market/instruments-info",
                {"category": "linear", "symbol": symbol},
            ))
        except ExchangeError as exc:
            return None, None, f"Bybit instrument info unavailable ({exc})."
        items = payload.get("list") or []
        if not items:
            return None, None, "Bybit instrument info was empty."
        item = items[0]
        leverage = (item.get("leverageFilter") or {}).get("maxLeverage")
        tick = (item.get("priceFilter") or {}).get("tickSize")
        return (float(leverage) if leverage else None), (float(tick) if tick else None), None

    def _ratio(self, host, symbol, interval, start, end):
        period = {"5min": "5min", "15min": "15min", "30min": "30min", "1h": "1h", "4h": "4h", "1d": "1d"}.get(interval)
        if period is None:
            return "Bybit account ratio has no matching period; using an even long/short split.", pd.DataFrame()
        try:
            payload = _result(self.http.get_json(
                f"{host}/v5/market/account-ratio",
                {"category": "linear", "symbol": symbol, "period": period, "limit": 500},
            ))
        except ExchangeError as exc:
            return f"Bybit account ratio unavailable ({exc}).", pd.DataFrame()
        items = payload.get("list") or []
        if not items:
            return "Bybit account ratio returned no rows.", pd.DataFrame()
        frame = pd.DataFrame(items)
        out = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(pd.to_numeric(frame["timestamp"]), unit="ms", utc=True),
                "count_long_short_ratio": pd.to_numeric(frame["buyRatio"]) / pd.to_numeric(frame["sellRatio"]).replace(0, pd.NA),
            }
        )
        return "Bybit metrics split uses the public account long/short ratio, not taker volume.", out


def parse_bybit_klines(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(rows)
    # Documented kline arrays are [start, open, high, low, close, volume, turnover].
    # Mark-price klines omit the volume fields.
    out = pd.DataFrame(
        {
            "open_time": pd.to_datetime(pd.to_numeric(frame[0]), unit="ms", utc=True),
            "open": pd.to_numeric(frame[1]),
            "high": pd.to_numeric(frame[2]),
            "low": pd.to_numeric(frame[3]),
            "close": pd.to_numeric(frame[4]),
            "volume": pd.to_numeric(frame[5]) if frame.shape[1] > 5 else 0.0,
        }
    )
    return out.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def parse_bybit_oi(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["timestamp", "oi_contracts", "oi_usd"])
    frame = pd.DataFrame(rows)
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(pd.to_numeric(frame["timestamp"]), unit="ms", utc=True),
            "oi_contracts": pd.to_numeric(frame["openInterest"]),
            "oi_usd": float("nan"),
        }
    ).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def _attach_mark(candles: pd.DataFrame, mark: pd.DataFrame) -> pd.DataFrame:
    if candles.empty:
        return candles
    if mark.empty:
        candles = candles.copy()
        candles["mark"] = float("nan")
        return candles
    renamed = mark[["open_time", "close"]].rename(columns={"close": "mark"})
    return candles.merge(renamed, on="open_time", how="left")


def _result(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ExchangeError("unexpected Bybit payload")
    if payload.get("retCode") not in (0, "0", None):
        raise ExchangeError(payload.get("retMsg") or str(payload))
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise ExchangeError("unexpected Bybit result")
    return result


def _clip(frame, column, start, end):
    if frame.empty:
        return frame
    start_ts, end_ts = _utc(start), _utc(end)
    mask = (frame[column] >= start_ts) & (frame[column] < end_ts)
    return frame.loc[mask].reset_index(drop=True)


def _utc(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")
