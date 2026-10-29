"""Live market context, observed liquidation orders and a multi-symbol watchlist.

Funding and open interest are exchange snapshots. Liquidation events are filled
orders that OKX publishes: they are observations, not model output. Demo values
are synthetic and never contact an exchange. A failed live source is reported as
failed; it is never replaced with demo data.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import numpy as np

from liqmap import data
from liqmap.data import AGGREGATE_VENUES, DataError, RequestDeadlineExceeded, check_deadline, exchange_name, normalize_symbol, range_spec
from liqmap.model import build_heatmap
from liqmap.service import (
    MAX_WATCHLIST_SYMBOLS,
    SCHEMA_VERSION,
    SourceUnavailable,
    _canonical,
    _key_lock,
    _quality,
    _scrub,
    get_series,
    market_context,
    public_model,
)

log = logging.getLogger("liqmap.market")

OKX_BASE = "https://www.okx.com"
BINANCE_BASE = "https://fapi.binance.com"
# Snapshot calls are small; fail fast so one slow venue cannot hold the request.
FAST_TIMEOUT = (4, 8)
FAST_RETRIES = 1
CONTEXT_TTL_S = 30
LIQUIDATION_TTL_S = 30
MAX_CONTEXT_CACHE = 32
MAX_LIQUIDATION_CACHE = 32
MAX_LIQUIDATION_PAGES = 10
OKX_LIQUIDATION_PAGE = 100
INSTRUMENT_TTL_S = 6 * 3600
MAX_INSTRUMENT_CACHE = 64
# Binance documents 8 hours for symbols absent from /fapi/v1/fundingInfo.
BINANCE_DEFAULT_FUNDING_HOURS = 8
FUNDING_BASIS_HOURS = 8
DEMO_FUNDING_HOURS = 8
OKX_LIQUIDATION_NOTE = "Observed OKX liquidation orders at bankruptcy price. Coverage is limited to the most recent orders OKX publishes."


class _TTLCache:
    """A small time-bounded cache; at capacity the oldest entry is evicted."""

    def __init__(self, ttl_s: float, max_entries: int):
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._items: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, key: tuple):
        now = time.time()
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            if now - hit[0] >= self.ttl_s:
                self._items.pop(key, None)
                return None
            return hit[1]

    def put(self, key: tuple, value) -> None:
        now = time.time()
        with self._lock:
            for old, (stamp, _value) in list(self._items.items()):
                if now - stamp >= self.ttl_s:
                    self._items.pop(old, None)
            self._items.pop(key, None)
            while len(self._items) >= self.max_entries:
                self._items.pop(min(self._items, key=lambda item: self._items[item][0]))
            self._items[key] = (now, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_context_cache = _TTLCache(CONTEXT_TTL_S, MAX_CONTEXT_CACHE)
_liquidation_cache = _TTLCache(LIQUIDATION_TTL_S, MAX_LIQUIDATION_CACHE)
_instruments: dict[str, tuple[float, float]] = {}
_instrument_lock = threading.Lock()


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ms(value) -> int | None:
    number = _num(value)
    return int(number) if number is not None and number > 0 else None


def _hours(value) -> int | float | None:
    hours = _num(value)
    if hours is None or hours <= 0:
        return None
    return int(hours) if hours.is_integer() else round(hours, 3)


def _hours_between(start_ms: int | None, end_ms: int | None) -> int | float | None:
    if start_ms is None or end_ms is None:
        return None
    return _hours((end_ms - start_ms) / 3_600_000)


def _live_only(asof: str, bar: str, replay: bool) -> None:
    if asof or bar or replay:
        raise DataError("This view is live only; asof, bar and replay are not accepted.")


# OKX, Binance and Bybit snapshot readers. Each returns one venue row or raises DataError.


def _okx_data(path: str, params: dict, inst: str) -> list:
    status, payload = data._get_json(f"{OKX_BASE}{path}", params, timeout=FAST_TIMEOUT, retries=FAST_RETRIES)
    if not isinstance(payload, dict):
        raise DataError(f"OKX returned an unexpected response (HTTP {status}).")
    code = str(payload.get("code"))
    # 51001 is an unknown instrument; 51014 an unknown index (underlying).
    if code in {"51001", "51014"}:
        raise DataError(f"OKX has no {inst} perpetual swap.")
    if status != 200 or code != "0":
        raise DataError(f"OKX rejected the request ({payload.get('msg') or f'HTTP {status}'}).")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise DataError("OKX returned an unexpected response.")
    return rows


def _okx_first(path: str, params: dict, inst: str) -> dict:
    rows = _okx_data(path, params, inst)
    if not rows or not isinstance(rows[0], dict):
        raise DataError(f"OKX returned no data for {inst}.")
    return rows[0]


def _venue_row(name: str, *, ok: bool, error: str | None = None, **fields) -> dict:
    row = {
        "exchange": name,
        "name": exchange_name(name),
        "ok": ok,
        "funding_rate": None,
        "next_funding_time": None,
        "funding_interval_hours": None,
        "open_interest_base": None,
        "open_interest_usd": None,
        "mark_price": None,
        "observed_at": None,
        "error": error,
    }
    row.update(fields)
    return row


def _okx_context(coin: str) -> dict:
    inst = f"{coin}-USDT-SWAP"
    funding = _okx_first("/api/v5/public/funding-rate", {"instId": inst}, inst)
    rate = _num(funding.get("fundingRate"))
    if rate is None:
        raise DataError(f"OKX returned no funding rate for {inst}.")
    oi = _okx_first("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}, inst)
    mark: dict = {}
    try:
        mark = _okx_first("/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst}, inst)
    except RequestDeadlineExceeded:
        raise
    except DataError as exc:
        log.info("okx mark price unavailable: %s", exc)
    # fundingTime is the upcoming settlement of the current rate.
    settles = _ms(funding.get("fundingTime"))
    interval = _hours_between(_ms(funding.get("prevFundingTime")), settles) or _hours_between(settles, _ms(funding.get("nextFundingTime")))
    stamps = [stamp for stamp in (_ms(funding.get("ts")), _ms(oi.get("ts")), _ms(mark.get("ts"))) if stamp]
    return _venue_row(
        "okx", ok=True,
        funding_rate=rate,
        next_funding_time=settles,
        funding_interval_hours=interval,
        open_interest_base=_num(oi.get("oiCcy")),
        open_interest_usd=_num(oi.get("oiUsd")),
        mark_price=_num(mark.get("markPx")),
        observed_at=min(stamps) if stamps else None,
    )


def _binance_get(path: str, params: dict | None, pair: str):
    status, payload = data._get_json(f"{BINANCE_BASE}{path}", params, timeout=FAST_TIMEOUT, retries=FAST_RETRIES)
    if status in (401, 403, 451):
        raise DataError(f"Binance is not reachable from this network (HTTP {status}). Its futures API blocks some regions.")
    if status != 200 or payload is None:
        if isinstance(payload, dict) and payload.get("code") == -1121:
            raise DataError(f"Binance has no {pair} perpetual.")
        message = payload.get("msg") if isinstance(payload, dict) else None
        raise DataError(f"Binance rejected the request ({message or f'HTTP {status}'}).")
    return payload


def _binance_interval(pair: str) -> int | float | None:
    try:
        rows = _binance_get("/fapi/v1/fundingInfo", None, pair)
    except RequestDeadlineExceeded:
        raise
    except DataError as exc:
        log.info("binance funding info unavailable: %s", exc)
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("symbol") == pair:
            return _hours(row.get("fundingIntervalHours"))
    return BINANCE_DEFAULT_FUNDING_HOURS


def _binance_context(coin: str) -> dict:
    pair = f"{coin}USDT"
    premium = _binance_get("/fapi/v1/premiumIndex", {"symbol": pair}, pair)
    oi = _binance_get("/fapi/v1/openInterest", {"symbol": pair}, pair)
    if not isinstance(premium, dict) or not isinstance(oi, dict):
        raise DataError("Binance returned an unexpected response.")
    rate = _num(premium.get("lastFundingRate"))
    if rate is None:
        raise DataError(f"Binance returned no funding rate for {pair}.")
    mark = _num(premium.get("markPrice"))
    base = _num(oi.get("openInterest"))
    stamps = [stamp for stamp in (_ms(premium.get("time")), _ms(oi.get("time"))) if stamp]
    return _venue_row(
        "binance", ok=True,
        funding_rate=rate,
        next_funding_time=_ms(premium.get("nextFundingTime")),
        funding_interval_hours=_binance_interval(pair),
        open_interest_base=base,
        open_interest_usd=base * mark if base is not None and mark is not None else None,
        mark_price=mark,
        observed_at=min(stamps) if stamps else None,
    )


def _bybit_context(coin: str) -> dict:
    pair = f"{coin}USDT"
    payload = data._bybit_payload("/v5/market/tickers", {"category": "linear", "symbol": pair}, timeout=FAST_TIMEOUT, retries=FAST_RETRIES)
    rows = payload["result"].get("list") or []
    item = next((row for row in rows if isinstance(row, dict) and row.get("symbol") == pair), None)
    if item is None:
        raise DataError(f"Bybit has no {pair} perpetual.")
    rate = _num(item.get("fundingRate"))
    if rate is None:
        raise DataError(f"Bybit returned no funding rate for {pair}.")
    return _venue_row(
        "bybit", ok=True,
        funding_rate=rate,
        next_funding_time=_ms(item.get("nextFundingTime")),
        funding_interval_hours=_hours(item.get("fundingIntervalHour")),
        open_interest_base=_num(item.get("openInterest")),
        open_interest_usd=_num(item.get("openInterestValue")),
        mark_price=_num(item.get("markPrice")),
        observed_at=_ms(payload.get("time")),
    )


_CONTEXT_READERS = {"okx": _okx_context, "binance": _binance_context, "bybit": _bybit_context}


def _venue_or_error(name: str, coin: str) -> dict:
    try:
        return _CONTEXT_READERS[name](coin)
    except RequestDeadlineExceeded:
        raise
    except DataError as exc:
        log.info("context %s %s unavailable: %s", name, coin, exc)
        return _venue_row(name, ok=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 — one venue must not sink the others
        log.exception("context %s %s failed", name, coin)
        return _venue_row(name, ok=False, error=f"{exchange_name(name)} failed ({exc.__class__.__name__}).")


def _fetch_context(names: tuple[str, ...], coin: str) -> list[dict]:
    if len(names) == 1:
        rows = [_venue_or_error(names[0], coin)]
    else:
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futures = [pool.submit(copy_context().run, _venue_or_error, name, coin) for name in names]
            rows = [future.result() for future in futures]
    if not any(row["ok"] for row in rows):
        detail = " ".join(row["error"] for row in rows if row["error"])
        raise SourceUnavailable(f"No live funding or open interest was available for {coin}. {detail}".strip())
    return rows


def _live_context(names: tuple[str, ...], coin: str) -> list[dict]:
    key = ("context", names, coin)
    rows = _context_cache.get(key)
    if rows is None:
        with _key_lock(key):
            rows = _context_cache.get(key)
            if rows is None:
                rows = _fetch_context(names, coin)
                _context_cache.put(key, rows)
    return [dict(row) for row in rows]


def _demo_context(coin: str) -> dict:
    series = data.fetch_demo(coin, f"{coin}USDT", range_spec("24h")).series
    seed = data.zlib_crc(f"{coin}:funding")
    interval_ms = DEMO_FUNDING_HOURS * 3_600_000
    now_ms = int(time.time() * 1000)
    mark = float(series.close[-1])
    oi_usd = float(series.oi_usd[-1])
    return _venue_row(
        "demo", ok=True,
        funding_rate=((seed % 41) - 10) * 0.00001,
        next_funding_time=(now_ms // interval_ms + 1) * interval_ms,
        funding_interval_hours=DEMO_FUNDING_HOURS,
        open_interest_base=oi_usd / mark,
        open_interest_usd=oi_usd,
        mark_price=mark,
        observed_at=int(series.times[-1]) + int(series.bar_seconds) * 1000,
    )


def _weighted_funding(rows: list[dict]) -> float | None:
    """Open-interest-weighted funding, each rate first scaled to the common basis interval."""
    total = weight = 0.0
    for row in rows:
        rate, usd, hours = row["funding_rate"], row["open_interest_usd"], row["funding_interval_hours"]
        if not row["ok"] or rate is None or usd is None or usd <= 0 or not hours:
            continue
        total += rate * FUNDING_BASIS_HOURS / hours * usd
        weight += usd
    return total / weight if weight > 0 else None


def context_payload(exchange: str = "okx", symbol: str = "BTC", mode: str = "pair", *, asof: str = "", bar: str = "", replay: bool = False) -> dict:
    _live_only(asof, bar, replay)
    exchange_key, coin, _range, mode_key = _canonical(exchange, symbol, "24h", mode)
    synthetic = exchange_key == "demo"
    if synthetic:
        rows = [_demo_context(coin)]
        note = "Demo funding and open interest are synthetic. No exchange was contacted."
    else:
        names = AGGREGATE_VENUES if exchange_key == "aggregate" else (exchange_key,)
        rows = _live_context(names, coin)
        note = (
            "Live exchange snapshots. Funding rates are fractions per funding interval (0.0001 = 0.01%); "
            f"the open-interest-weighted rate is scaled to {FUNDING_BASIS_HOURS} hours."
        )
        missing = [row["name"] for row in rows if not row["ok"]]
        if missing:
            note += f" Totals cover {len(rows) - len(missing)} of {len(rows)} venues; {', '.join(missing)} unavailable."
    usd = [row["open_interest_usd"] for row in rows if row["ok"] and row["open_interest_usd"] is not None]
    return _scrub({
        "schema_version": SCHEMA_VERSION,
        "coin": coin,
        "symbol": f"{coin}USDT",
        "exchange": exchange_key,
        "mode": mode_key,
        "generated_at": int(time.time() * 1000),
        "synthetic": synthetic,
        "venues": rows,
        "funding_rate_oi_weighted": _weighted_funding(rows),
        "funding_basis_hours": FUNDING_BASIS_HOURS,
        "open_interest_usd_total": float(sum(usd)) if usd else None,
        "note": note,
    })


# Observed OKX liquidation orders.


def _okx_contract_base(coin: str) -> float:
    """Base-asset quantity of one OKX USDT swap contract, cached in a bounded dict."""
    inst = f"{coin}-USDT-SWAP"
    now = time.time()
    with _instrument_lock:
        hit = _instruments.get(inst)
        if hit is not None and now - hit[0] < INSTRUMENT_TTL_S:
            return hit[1]
    row = _okx_first("/api/v5/public/instruments", {"instType": "SWAP", "instId": inst}, inst)
    value = _num(row.get("ctVal"))
    multiplier = _num(row.get("ctMult"))
    multiplier = 1.0 if multiplier is None else multiplier
    if value is None or value <= 0 or multiplier <= 0 or str(row.get("ctValCcy") or "").upper() != coin:
        raise DataError(f"OKX did not publish a {coin} contract value for {inst}.")
    with _instrument_lock:
        _instruments.pop(inst, None)
        while len(_instruments) >= MAX_INSTRUMENT_CACHE:
            _instruments.pop(next(iter(_instruments)))
        _instruments[inst] = (now, value * multiplier)
    return value * multiplier


def liquidated_side(pos_side, order_side) -> str | None:
    """Side of the liquidated position: posSide when hedged, else the closing order's opposite."""
    position = str(pos_side or "").lower()
    if position in {"long", "short"}:
        return position
    return {"sell": "long", "buy": "short"}.get(str(order_side or "").lower())


def _okx_event(item: dict, contract_base: float) -> dict | None:
    ts = _ms(item.get("ts"))
    price = _num(item.get("bkPx"))
    contracts = _num(item.get("sz"))
    side = liquidated_side(item.get("posSide"), item.get("side"))
    if ts is None or price is None or price <= 0 or contracts is None or contracts <= 0 or side is None:
        return None
    size = contracts * contract_base
    return {"ts": ts, "price": price, "side": side, "size_base": size, "usd": size * price}


def _okx_liquidation_rows(coin: str, start_ms: int) -> tuple[list[dict], bool]:
    """Filled orders newest first back to ``start_ms``, at most MAX_LIQUIDATION_PAGES pages.

    ``after`` returns orders strictly older than its timestamp, so the cursor is
    the oldest timestamp plus one and orders already read at that millisecond are
    skipped. The flag is true when older orders inside the window may exist.
    """
    inst = f"{coin}-USDT-SWAP"
    rows: list[dict] = []
    cursor: int | None = None
    boundary: Counter = Counter()
    for _ in range(MAX_LIQUIDATION_PAGES):
        check_deadline()
        params = {"instType": "SWAP", "uly": f"{coin}-USDT", "state": "filled", "limit": str(OKX_LIQUIDATION_PAGE)}
        if cursor is not None:
            params["after"] = str(cursor + 1)
        try:
            groups = _okx_data("/api/v5/public/liquidation-orders", params, inst)
        except RequestDeadlineExceeded:
            raise
        except DataError:
            if not rows:
                raise
            return rows, True
        details = [item for group in groups if isinstance(group, dict) for item in (group.get("details") or []) if isinstance(item, dict)]
        added = []
        for item in details:
            ts = _ms(item.get("ts"))
            if ts is None:
                continue
            identity = (ts, str(item.get("bkPx")), str(item.get("sz")), str(item.get("side")), str(item.get("posSide")))
            if ts == cursor and boundary[identity] > 0:
                boundary[identity] -= 1
                continue
            added.append((ts, identity, item))
        if not added:
            return rows, False
        rows.extend(item for _ts, _identity, item in added)
        oldest = min(ts for ts, _identity, _item in added)
        if oldest < start_ms or len(details) < OKX_LIQUIDATION_PAGE:
            return rows, False
        if cursor is not None and oldest >= cursor:
            return rows, True  # A full page inside one millisecond cannot advance.
        boundary = Counter(identity for ts, identity, _item in added if ts == oldest)
        cursor = oldest
    return rows, True


def _okx_liquidations(coin: str, spec: dict) -> tuple[list[dict], bool]:
    key = ("liquidations", "okx", coin, spec["key"])
    hit = _liquidation_cache.get(key)
    if hit is None:
        with _key_lock(key):
            hit = _liquidation_cache.get(key)
            if hit is None:
                start_ms = int(time.time() * 1000) - int(spec["seconds"]) * 1000
                try:
                    contract_base = _okx_contract_base(coin)
                    rows, truncated = _okx_liquidation_rows(coin, start_ms)
                except RequestDeadlineExceeded:
                    raise
                except DataError as exc:
                    raise SourceUnavailable(str(exc)) from exc
                events = [event for event in (_okx_event(item, contract_base) for item in rows) if event and event["ts"] >= start_ms]
                events.sort(key=lambda event: event["ts"])
                hit = (events, truncated)
                _liquidation_cache.put(key, hit)
    events, truncated = hit
    return [dict(event) for event in events], truncated


def _demo_liquidations(coin: str, spec: dict) -> list[dict]:
    """Synthetic events inside the demo series: longs near bar lows, shorts near bar highs."""
    series = data.fetch_demo(coin, f"{coin}USDT", spec).series
    n = len(series.times)
    rng = np.random.default_rng(data.zlib_crc(f"{coin}:{spec['key']}:liquidations"))
    bar_ms = int(series.bar_seconds) * 1000
    typical_oi = float(np.nanmedian(series.oi_usd))
    events = []
    for i in np.sort(rng.choice(n, size=min(n, max(4, n // 6)), replace=False)):
        low, high = float(series.low[i]), float(series.high[i])
        side = "long" if series.close[i] < series.open[i] else "short"
        reach = (high - low) * float(rng.uniform(0.0, 0.15))
        price = low + reach if side == "long" else high - reach
        usd = typical_oi * float(rng.uniform(0.00005, 0.002))
        events.append({
            "ts": int(series.times[i]) + int(rng.integers(1, bar_ms)),
            "price": price,
            "side": side,
            "size_base": usd / price,
            "usd": usd,
        })
    return events


def liquidations_payload(exchange: str = "okx", symbol: str = "BTC", mode: str = "pair", range_key: str = "24h", *, asof: str = "", bar: str = "", replay: bool = False) -> dict:
    _live_only(asof, bar, replay)
    exchange_key, coin, range_name, mode_key = _canonical(exchange, symbol, range_key, mode)
    spec = range_spec(range_name)
    truncated = False
    if exchange_key == "demo":
        source, available, synthetic = "demo", True, True
        events = _demo_liquidations(coin, spec)
        note = "Demo liquidation events are synthetic. No exchange was contacted."
    elif exchange_key in {"okx", "aggregate"}:
        source, available, synthetic = "okx", True, False
        events, truncated = _okx_liquidations(coin, spec)
        note = OKX_LIQUIDATION_NOTE
        if exchange_key == "aggregate":
            note += " Binance and Bybit liquidations are not included."
    else:
        source, available, synthetic, events = None, False, False, []
        note = f"Observed liquidations come from OKX only; {exchange_name(exchange_key)} liquidation orders are not available here."
    return _scrub({
        "schema_version": SCHEMA_VERSION,
        "coin": coin,
        "symbol": f"{coin}USDT",
        "exchange": exchange_key,
        "mode": mode_key,
        "range": range_name,
        "source": source,
        "available": available,
        "synthetic": synthetic,
        "events": events,
        "coverage": {
            "from_ms": events[0]["ts"] if events else None,
            "to_ms": events[-1]["ts"] if events else None,
            "count": len(events),
            "truncated": truncated,
        },
        "generated_at": int(time.time() * 1000),
        "note": note,
    })


# Watchlist: the main chart's analysis for a few symbols at once.


def watchlist_symbols(raw: str) -> list[str]:
    parts = [part.strip() for part in (raw or "").split(",")]
    parts = [part for part in parts if part]
    if not parts:
        raise DataError(f"symbols needs 1 to {MAX_WATCHLIST_SYMBOLS} comma-separated symbols, like BTC,ETH,SOL.")
    if len(parts) > MAX_WATCHLIST_SYMBOLS:
        raise DataError(f"symbols accepts at most {MAX_WATCHLIST_SYMBOLS} symbols.")
    coins: list[str] = []
    for part in parts:
        coin, _pair = normalize_symbol(part)
        if coin not in coins:
            coins.append(coin)
    return coins


def _cluster(group: dict | None) -> dict | None:
    if not group:
        return None
    return {"price": group["price"], "distance_pct": group["distance_pct"], "usd": group["usd"]}


def _failed_item(coin: str, error: str) -> dict:
    return {
        "coin": coin, "ok": False, "price": None, "price_change_pct": None, "quality": None,
        "stale": None, "above": None, "below": None, "short_to_long_5pct": None, "as_of": None,
        "error": error,
    }


def _watch_item(exchange: str, coin: str, range_name: str, mode: str, model_id: int) -> dict:
    try:
        loaded = get_series(exchange, coin, range_name, mode)
        heat = build_heatmap(loaded.series, model_id)
        quality = _quality(loaded)
    except RequestDeadlineExceeded:
        raise
    except DataError as exc:
        return _failed_item(coin, str(exc))
    except Exception:  # noqa: BLE001 — one symbol must not sink the others
        log.exception("watchlist %s %s failed", exchange, coin)
        return _failed_item(coin, "The server failed while building this symbol.")
    analysis = heat.analysis
    five = next((row for row in analysis["imbalance"] if row["window_pct"] == 5), None)
    return {
        "coin": coin,
        "ok": True,
        "price": analysis["price"],
        "price_change_pct": market_context(heat.series)["price_change_pct"],
        "quality": quality["status"],
        "stale": quality["stale"],
        "above": _cluster(analysis["above"]),
        "below": _cluster(analysis["below"]),
        "short_to_long_5pct": five["short_to_long"] if five else None,
        "as_of": int(loaded.as_of),
        "error": None,
    }


def watchlist_payload(exchange: str = "okx", range_key: str = "24h", model="1", mode: str = "pair", symbols: str = "", *, asof: str = "", bar: str = "", replay: bool = False) -> dict:
    _live_only(asof, bar, replay)
    try:
        model_id = int(model)
    except (TypeError, ValueError):
        raise DataError("Model must be 1, 2, or 3.") from None
    public_model(model_id)
    coins = watchlist_symbols(symbols)
    exchange_key, _coin, range_name, mode_key = _canonical(exchange, coins[0], range_key, mode)
    with ThreadPoolExecutor(max_workers=min(3, len(coins))) as pool:
        futures = [pool.submit(copy_context().run, _watch_item, exchange_key, coin, range_name, mode_key, model_id) for coin in coins]
        items = [future.result() for future in futures]
    return _scrub({
        "schema_version": SCHEMA_VERSION,
        "exchange": exchange_key,
        "mode": mode_key,
        "range": range_name,
        "model": model_id,
        "generated_at": int(time.time() * 1000),
        "items": items,
    })
