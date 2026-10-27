"""OKX public swap data.

Open-interest history keeps about the latest 1440 points, so a long window
is moved to a coarser bar instead of being truncated without a warning.
Contract rows are ``[ts, contracts, coin, usd]``. Taker rows are
``[ts, sellVol, buyVol]``.
"""

from __future__ import annotations

import logging

import pandas as pd

from olh.core.errors import ExchangeError
from olh.core.windows import interval_seconds
from olh.exchanges.base import Capabilities, MarketSeries
from olh.infra.http import HttpClient

logger = logging.getLogger(__name__)

BASE = "https://www.okx.com"
_BAR = {
    "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H",
    "4h": "4H", "6h": "6H", "12h": "12H", "1d": "1D",
}
_ORDER = ["5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]
_OI_LIMIT = 1440


class OkxAdapter:
    name = "okx"
    capabilities = Capabilities(
        klines=True,
        open_interest=True,
        mark_price=True,
        taker_volume=True,
        risk_tiers=False,
        oi_history="about the latest 1440 open-interest points; long windows use a coarser bar",
    )

    def __init__(self, http: HttpClient | None = None, max_pages: int = 40) -> None:
        self.http = http or HttpClient(min_interval=0.22)
        self.max_pages = max_pages

    def fetch(self, symbol, start, end, interval: str) -> MarketSeries:
        chosen, note = coarsen_interval(interval, start, end)
        bar = _BAR[chosen]
        warnings = [note] if note else []
        candles = parse_okx_candles(self._candles(
            f"{BASE}/api/v5/market/candles",
            symbol.okx,
            bar,
            start,
            end,
            recent_limit=300,
            history_url=f"{BASE}/api/v5/market/history-candles",
        ))
        mark_rows, mark_note = self._mark(symbol.okx, bar, start, end)
        if mark_note:
            warnings.append(mark_note)
        candles = _attach_mark(candles, parse_okx_candles(mark_rows))
        oi = parse_okx_oi(self._rubrik(
            f"{BASE}/api/v5/rubik/stat/contracts/open-interest-history",
            {"instId": symbol.okx, "period": bar},
            start,
            end,
        ))
        taker, taker_note = self._taker(symbol.okx, bar, start, end)
        if taker_note:
            warnings.append(taker_note)
        if not taker.empty:
            oi = oi.merge(taker, on="timestamp", how="left")
        max_leverage, tick, inst_note = self._instrument(symbol.okx)
        if inst_note:
            warnings.append(inst_note)
        candles = _clip(candles, "open_time", start, end)
        oi = _clip(oi, "timestamp", start, end)
        if candles.empty or oi.empty:
            raise ExchangeError("OKX returned no candles or open interest in this window")
        return MarketSeries(
            exchange=self.name,
            candles=candles,
            oi=oi,
            max_leverage=max_leverage or 100.0,
            tick_size=tick,
            warnings=warnings,
        )

    def _candles(self, url, inst_id, bar, start, end, recent_limit, history_url: str | None = None) -> list:
        """Page one candle endpoint. Older pages stay on ``history_url``.

        Last-price history lives on ``/market/history-candles``. Mark history
        stays on ``/market/mark-price-candles``. Mixing those endpoints would
        write last-price bars into the mark series.
        """
        history = history_url or url
        end_ms = int(_utc(end).timestamp() * 1000)
        start_ms = int(_utc(start).timestamp() * 1000)
        rows = list(self._data(self.http.get_json(url, {"instId": inst_id, "bar": bar, "limit": str(recent_limit), "after": str(end_ms)})))
        oldest = min((int(item[0]) for item in rows), default=end_ms)
        for _ in range(self.max_pages):
            if oldest <= start_ms:
                break
            batch = self._data(self.http.get_json(
                history,
                {"instId": inst_id, "bar": bar, "limit": "100", "after": str(oldest)},
            ))
            if not batch:
                break
            new_oldest = min(int(item[0]) for item in batch)
            if new_oldest >= oldest:
                break
            rows.extend(batch)
            oldest = new_oldest
            if len(batch) < 100:
                break
        return rows

    def _mark(self, inst_id, bar, start, end):
        try:
            rows = self._candles(f"{BASE}/api/v5/market/mark-price-candles", inst_id, bar, start, end, recent_limit=100)
        except ExchangeError as exc:
            return [], f"OKX mark price unavailable ({exc}); entry uses the last price."
        return rows, None

    def _rubrik(self, url, params, start, end) -> list:
        start_ms = int(_utc(start).timestamp() * 1000)
        cursor = int(_utc(end).timestamp() * 1000)
        rows: list = []
        for _ in range(self.max_pages):
            query = dict(params)
            query["end"] = str(cursor)
            batch = self._data(self.http.get_json(url, query))
            if not batch:
                break
            rows.extend(batch)
            oldest = min(int(item[0]) for item in batch)
            if oldest <= start_ms or len(batch) < 100 or oldest >= cursor:
                break
            cursor = oldest
        return rows

    def _taker(self, inst_id, bar, start, end):
        try:
            rows = self._rubrik(
                f"{BASE}/api/v5/rubik/stat/taker-volume-contract",
                {"instId": inst_id, "period": bar},
                start,
                end,
            )
        except ExchangeError as exc:
            return pd.DataFrame(), f"OKX taker volume unavailable ({exc})."
        return parse_okx_taker(rows), None

    def _instrument(self, inst_id):
        try:
            data = self._data(self.http.get_json(
                f"{BASE}/api/v5/public/instruments",
                {"instType": "SWAP", "instId": inst_id},
            ))
        except ExchangeError as exc:
            return None, None, f"OKX instrument info unavailable ({exc})."
        if not data:
            return None, None, "OKX instrument info was empty."
        item = data[0]
        leverage = float(item["lever"]) if item.get("lever") else None
        tick = float(item["tickSz"]) if item.get("tickSz") else None
        return leverage, tick, None

    @staticmethod
    def _data(payload: dict) -> list:
        if not isinstance(payload, dict):
            raise ExchangeError("unexpected OKX payload")
        if str(payload.get("code", "0")) != "0":
            raise ExchangeError(payload.get("msg") or str(payload))
        return payload.get("data") or []


def coarsen_interval(interval: str, start, end) -> tuple[str, str | None]:
    """Pick the finest bar at least as coarse as ``interval`` that fits in 1440 points."""
    if interval not in _ORDER:
        raise ExchangeError(f"OKX has no interval {interval}")
    start_index = _ORDER.index(interval)
    span = (_utc(end) - _utc(start)).total_seconds()
    for name in _ORDER[start_index:]:
        points = span / interval_seconds(name)
        if points <= _OI_LIMIT:
            if name == interval:
                return name, None
            return name, (
                f"OKX open-interest history keeps about {_OI_LIMIT} points. "
                f"Using {name} instead of {interval} so the window is not silently cut off."
            )
    return "1d", (
        f"OKX open-interest history keeps about {_OI_LIMIT} points. Using 1d bars."
    )


def parse_okx_candles(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "open_time": pd.to_datetime(pd.to_numeric(frame[0]), unit="ms", utc=True),
            "open": pd.to_numeric(frame[1]),
            "high": pd.to_numeric(frame[2]),
            "low": pd.to_numeric(frame[3]),
            "close": pd.to_numeric(frame[4]),
            "volume": pd.to_numeric(frame[6] if frame.shape[1] > 6 else frame[5]),
        }
    )
    return out.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def parse_okx_oi(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["timestamp", "oi_contracts", "oi_usd"])
    frame = pd.DataFrame(rows)
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(pd.to_numeric(frame[0]), unit="ms", utc=True),
            "oi_contracts": pd.to_numeric(frame[2]) if frame.shape[1] > 2 else pd.to_numeric(frame[1]),
            "oi_usd": pd.to_numeric(frame[3]) if frame.shape[1] > 3 else float("nan"),
        }
    ).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def parse_okx_taker(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["timestamp", "sum_taker_long_short_vol_ratio"])
    frame = pd.DataFrame(rows)
    sell = pd.to_numeric(frame[1])
    buy = pd.to_numeric(frame[2])
    ratio = buy / sell.where(sell > 0)
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(pd.to_numeric(frame[0]), unit="ms", utc=True),
            "sum_taker_long_short_vol_ratio": ratio,
        }
    ).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def _attach_mark(candles, mark):
    if candles.empty:
        return candles
    if mark.empty:
        candles = candles.copy()
        candles["mark"] = float("nan")
        return candles
    renamed = mark[["open_time", "close"]].rename(columns={"close": "mark"})
    return candles.merge(renamed, on="open_time", how="left")


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
