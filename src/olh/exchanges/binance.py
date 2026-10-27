"""Binance USD-M public data.

Closed days come from data.binance.vision. The signed leverage-bracket
endpoint is not called. REST is used only for the current, incomplete UTC
day. HTTP 451 is reported and does not discard archive rows already loaded.
"""

from __future__ import annotations

import logging

import pandas as pd

from olh.core.errors import ExchangeBlocked, ExchangeError
from olh.exchanges.base import Capabilities, MarketSeries
from olh.infra.binance_vision import BinanceVisionClient
from olh.infra.diagnostics import timestamp_check
from olh.infra.http import HttpClient
from olh.infra.paging import paginate_forward

logger = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_buy", "taker_quote", "ignore",
]


class BinanceAdapter:
    name = "binance"
    capabilities = Capabilities(
        klines=True,
        open_interest=True,
        mark_price=True,
        taker_volume=True,
        oi_history="vision archives for closed days; REST open interest is about 30 days and is used only for the current day",
    )

    def __init__(self, http: HttpClient | None = None, vision: BinanceVisionClient | None = None) -> None:
        self.http = http or HttpClient(min_interval=0.12)
        self.vision = vision or BinanceVisionClient(http=self.http)

    def fetch(self, symbol, start, end, interval: str) -> MarketSeries:
        warnings: list[str] = []
        candles = self.vision.klines(symbol.binance, interval, start, end)
        mark = self.vision.mark_klines(symbol.binance, interval, start, end)
        metrics = self.vision.metrics(symbol.binance, start, end)
        trust = True
        if not metrics.empty:
            report = timestamp_check(metrics["timestamp"], pd.Timedelta(minutes=5))
            trust = bool(report["trust_deltas"])
            if report["kind"] != "period_start":
                warnings.append(
                    "Binance metrics create_time looks like "
                    f"{report['kind']} rather than a 5-minute period start; "
                    "contract deltas that are not on a regular grid were dropped."
                )
            elif not trust:
                warnings.append("Binance metrics timestamps were not regular; contract deltas were dropped.")
        try:
            rest_candles, rest_mark, rest_oi = self._rest_today(symbol.binance, start, end, interval)
        except ExchangeBlocked as exc:
            warnings.append(str(exc))
            rest_candles, rest_mark, rest_oi = _empty_candles(), _empty_candles(), _empty_oi()
        candles = _concat_time(candles, rest_candles, "open_time")
        mark = _concat_time(mark, rest_mark, "open_time")
        oi = _concat_time(_metrics_to_oi(metrics), rest_oi, "timestamp")
        candles = _attach_mark(candles, mark)
        if candles.empty or oi.empty:
            raise ExchangeError(
                "Binance returned no candles or open interest in this window. "
                "REST may be blocked (HTTP 451 from many US hosts) and the public "
                "archive usually lags by about a day."
            )
        candles = _clip(candles, "open_time", start, end)
        oi = _clip(oi, "timestamp", start, end)
        if candles.empty or oi.empty:
            raise ExchangeError(
                "Binance archive rows do not overlap this window, and REST did not fill the gap."
            )
        return MarketSeries(
            exchange=self.name,
            candles=candles,
            oi=oi,
            max_leverage=125.0 if symbol.binance == "BTCUSDT" else (100.0 if symbol.binance == "ETHUSDT" else 50.0),
            tick_size=None,
            warnings=warnings,
            trust_oi_deltas=trust,
        )

    def _rest_today(self, symbol: str, start, end, interval: str):
        now = pd.Timestamp.now(tz="UTC")
        today = now.normalize()
        rest_start = max(_utc(start), today)
        rest_end = min(_utc(end), now)
        if rest_start >= rest_end:
            return _empty_candles(), _empty_candles(), _empty_oi()
        start_ms = int(rest_start.timestamp() * 1000)
        end_ms = int(rest_end.timestamp() * 1000) - 1

        def kline_page(cursor, stop):
            payload = self.http.get_json(
                f"{FAPI}/fapi/v1/klines",
                {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": stop, "limit": 1500},
            )
            if isinstance(payload, dict):
                raise ExchangeError(payload.get("msg") or str(payload))
            return payload or []

        def mark_page(cursor, stop):
            payload = self.http.get_json(
                f"{FAPI}/fapi/v1/markPriceKlines",
                {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": stop, "limit": 1500},
            )
            if isinstance(payload, dict):
                raise ExchangeError(payload.get("msg") or str(payload))
            return payload or []

        def oi_page(cursor, stop):
            payload = self.http.get_json(
                f"{FAPI}/futures/data/openInterestHist",
                {"symbol": symbol, "period": interval, "startTime": cursor, "endTime": stop, "limit": 500},
            )
            if isinstance(payload, dict):
                raise ExchangeError(payload.get("msg") or str(payload))
            return payload or []

        krows = paginate_forward(kline_page, start_ms, end_ms, page_limit=1500, time_of=lambda row: int(row[0]))
        mrows = paginate_forward(mark_page, start_ms, end_ms, page_limit=1500, time_of=lambda row: int(row[0]))
        orows = paginate_forward(oi_page, start_ms, end_ms, page_limit=500, time_of=lambda row: int(row["timestamp"]))
        return parse_rest_klines(krows), parse_rest_klines(mrows), parse_rest_oi(orows)


def parse_rest_klines(rows: list) -> pd.DataFrame:
    if not rows:
        return _empty_candles()
    frame = pd.DataFrame(rows, columns=_KLINE_COLUMNS[: len(rows[0])])
    out = pd.DataFrame(
        {
            "open_time": pd.to_datetime(pd.to_numeric(frame["open_time"]), unit="ms", utc=True),
            "open": pd.to_numeric(frame["open"]),
            "high": pd.to_numeric(frame["high"]),
            "low": pd.to_numeric(frame["low"]),
            "close": pd.to_numeric(frame["close"]),
            "volume": pd.to_numeric(frame["volume"]) if "volume" in frame.columns else 0.0,
        }
    )
    return out.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def parse_rest_oi(rows: list) -> pd.DataFrame:
    if not rows:
        return _empty_oi()
    frame = pd.DataFrame(rows)
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(pd.to_numeric(frame["timestamp"]), unit="ms", utc=True),
            "oi_contracts": pd.to_numeric(frame["sumOpenInterest"]),
            "oi_usd": pd.to_numeric(frame["sumOpenInterestValue"]),
        }
    ).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def _metrics_to_oi(metrics: pd.DataFrame) -> pd.DataFrame:
    if metrics is None or metrics.empty:
        return _empty_oi()
    keep = [col for col in ("timestamp", "oi_contracts", "oi_usd", "count_long_short_ratio", "sum_taker_long_short_vol_ratio") if col in metrics.columns]
    return metrics[keep].copy()


def _attach_mark(candles: pd.DataFrame, mark: pd.DataFrame) -> pd.DataFrame:
    if candles.empty:
        return candles
    out = candles.copy()
    if mark is None or mark.empty:
        out["mark"] = float("nan")
        return out
    renamed = mark[["open_time", "close"]].rename(columns={"close": "mark"})
    return out.merge(renamed, on="open_time", how="left")


def _concat_time(left: pd.DataFrame, right: pd.DataFrame, column: str) -> pd.DataFrame:
    frames = [frame for frame in (left, right) if frame is not None and not frame.empty]
    if not frames:
        return left if left is not None else pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(column, keep="last").sort_values(column).reset_index(drop=True)


def _clip(frame: pd.DataFrame, column: str, start, end) -> pd.DataFrame:
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


def _empty_candles() -> pd.DataFrame:
    return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])


def _empty_oi() -> pd.DataFrame:
    return pd.DataFrame(columns=["timestamp", "oi_contracts", "oi_usd"])
