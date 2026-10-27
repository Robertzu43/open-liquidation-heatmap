"""Cache public series separately from the model so a model switch stays cheap."""

from __future__ import annotations

import base64
import math
import threading
import time

import numpy as np

from liqmap.data import (
    COINS,
    EXCHANGE_NAMES,
    RANGES,
    DataError,
    exchange_name,
    load_market,
    load_span,
    normalize_symbol,
    parse_asof,
    range_spec,
    resolve_view,
)
from liqmap.model import MODELS, MMR, build_heatmap, suggest_view

# Short windows move quickly. Long windows are expensive to refetch.
SERIES_TTL = {
    "12h": 30,
    "24h": 45,
    "3d": 120,
    "7d": 300,
    "30d": 600,
    "90d": 900,
    "180d": 1200,
    "1y": 1800,
    "2y": 1800,
}

# A replay span lives this long after its last frame.
SPAN_TTL = 1800

_cache: dict[tuple, tuple[float, object]] = {}
_spans: dict[tuple, list] = {}
_locks: dict[tuple, threading.Lock] = {}
_guard = threading.Lock()


def _key_lock(key: tuple) -> threading.Lock:
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _canonical(exchange: str, symbol: str, range_key: str, mode: str) -> tuple[str, str, str, str]:
    mode_key = (mode or "pair").strip().lower()
    exchange_key = (exchange or "okx").strip().lower()
    if mode_key not in {"pair", "symbol"}:
        raise DataError("Mode must be pair or symbol.")
    spec = range_spec(range_key)
    coin, _pair = normalize_symbol(symbol)
    if mode_key == "symbol" or exchange_key == "aggregate":
        return "aggregate", coin, spec["key"], "symbol"
    return exchange_key, coin, spec["key"], "pair"


def _evict(now: float) -> None:
    """Drop expired series so one entry per past view does not pile up."""
    with _guard:
        for key, (stamp, _value) in list(_cache.items()):
            if now - stamp >= SERIES_TTL.get(key[2], 120):
                _cache.pop(key, None)
                lock = _locks.get(key)
                if lock is not None and not lock.locked():
                    _locks.pop(key, None)


class ReplayExpired(DataError):
    """A replay frame whose span is no longer loaded. Frames never fall back to the network."""


def _span_for(coin: str, spec: dict, end_open: int):
    now = time.time()
    with _guard:
        for key, (used, _span) in list(_spans.items()):
            if now - used >= SPAN_TTL:
                _spans.pop(key, None)
        hit = _spans.get((coin, spec["bar"], spec["key"]))
        if hit is None or not hit[1].covers(end_open):
            return None
        hit[0] = now
        return hit[1]


def replay_payload(symbol: str, range_key: str, bar: str, start: str, end: str = "") -> dict:
    """Load a replay span once; frames are then /api/heatmap with asof and replay=1."""
    coin, pair = normalize_symbol(symbol)
    spec, first = resolve_view("binance", "pair", range_key, start, bar)
    if first is None:
        raise DataError("Replay needs a start time in the past.")
    span = load_span(coin, pair, spec, parse_asof(start), parse_asof(end))
    with _guard:
        _spans[(coin, spec["bar"], spec["key"])] = [time.time(), span]
    bar_ms = int(spec["bar_seconds"]) * 1000
    return {
        "coin": coin,
        "range": spec["key"],
        "bar": spec["bar"],
        "bar_seconds": int(spec["bar_seconds"]),
        "first_asof": span.first_end_open * 1000 + bar_ms,
        "last_asof": span.last_end_open * 1000 + bar_ms,
        "frames": span.frames,
        "days": span.days,
        "note": span.note,
    }


def get_series(
    exchange: str,
    symbol: str,
    range_key: str,
    mode: str = "pair",
    asof: str | None = None,
    bar: str | None = None,
    replay: bool = False,
):
    exchange_key, coin, range_name, mode_key = _canonical(exchange, symbol, range_key, mode)
    spec, end_open = resolve_view(exchange_key, mode_key, range_name, asof, bar)
    if replay:
        if end_open is None:
            raise DataError("A replay frame needs asof.")
        span = _span_for(coin, spec, end_open)
        if span is None:
            raise ReplayExpired("This replay is no longer loaded. Load it again.")
        # Frames are cut from the span on demand; they are not cached one by one.
        return span.frame(end_open)
    key = (exchange_key, coin, range_name, mode_key, spec["bar"], end_open)
    lock = _key_lock(key)
    with lock:
        now = time.time()
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < SERIES_TTL.get(range_name, 120):
            return hit[1]
        loaded = load_market(exchange_key, coin, range_name, mode_key, asof=asof, bar=bar)
        _evict(now)
        _cache[key] = (time.time(), loaded)
        return loaded


def public_model(model_id: int) -> dict:
    spec = MODELS.get(int(model_id))
    if spec is None:
        raise DataError("Model must be 1, 2, or 3.")
    return {
        "id": spec["id"],
        "name": spec["name"],
        "blurb": spec["blurb"],
        "weights": [{"leverage": int(lev), "weight": float(weight)} for lev, weight in spec["weights"]],
        "half_life_hours": float(spec["half_life_hours"]) if spec.get("half_life_hours") else None,
        "churn": float(spec["churn"]),
        "kernel": [float(value) for value in spec["kernel"]],
        "flow": bool(spec["flow"]),
        "mmr": MMR,
        "long_formula": "entry * (1 - 1/leverage) / (1 - MMR)",
        "short_formula": "entry * (1 + 1/leverage) / (1 + MMR)",
        "entry": "close of the candle whose open time is nearest the open-interest print",
    }


def _f32(values: np.ndarray) -> str:
    array = np.ascontiguousarray(values, dtype="<f4")
    return base64.b64encode(array.tobytes()).decode("ascii")


def _scrub(value):
    if isinstance(value, dict):
        return {str(key): _scrub(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, int):
        return value
    return value


def _title(exchange: str, coin: str, mode: str) -> str:
    if mode == "symbol" or exchange == "aggregate":
        return f"{coin}/USDT Liquidation Heatmap"
    return f"{exchange_name(exchange)} {coin}/USDT Liquidation Heatmap"


def load_heatmap(
    exchange: str,
    symbol: str,
    range_key: str,
    model,
    mode: str = "pair",
    asof: str | None = None,
    bar: str | None = None,
    replay: bool = False,
) -> dict:
    try:
        model_id = int(model)
    except (TypeError, ValueError):
        raise DataError("Model must be 1, 2, or 3.") from None
    model_meta = public_model(model_id)
    loaded = get_series(exchange, symbol, range_key, mode, asof, bar, replay)
    heat = build_heatmap(loaded.series, model_id)
    series = loaded.series
    exchange_key, _coin, range_name, mode_key = _canonical(exchange, symbol, range_key, mode)
    spec, end_open = resolve_view(exchange_key, mode_key, range_name, asof, bar)
    view_lo, view_hi = suggest_view(series)
    grid = np.ascontiguousarray(heat.long_heat + heat.short_heat, dtype=np.float64)
    return {
        "title": _title(series.exchange, series.coin, mode_key),
        "exchange": series.exchange,
        "exchange_name": exchange_name(series.exchange),
        "symbol": series.symbol,
        "coin": series.coin,
        "mode": mode_key,
        "range": range_name,
        "bar": spec["bar"],
        "bar_seconds": int(series.bar_seconds),
        "asof": None if end_open is None else (end_open + int(spec["bar_seconds"])) * 1000,
        "replay": bool(replay),
        "model": model_meta,
        "source": loaded.source,
        "venues": list(loaded.venues),
        "as_of": int(loaded.as_of),
        "stale": bool(loaded.stale),
        "note": series.note,
        "warnings": list(series.warnings),
        "generated_at": int(time.time() * 1000),
        "times": [int(item) for item in series.times],
        "open": _f32(series.open),
        "high": _f32(series.high),
        "low": _f32(series.low),
        "close": _f32(series.close),
        "volume": _f32(series.volume_usd),
        "centers": _f32(heat.centers),
        "heat": _f32(grid),
        "bins": int(grid.shape[0]),
        "profile_long": _f32(heat.profile_long),
        "profile_short": _f32(heat.profile_short),
        "tiers": [int(lev) for lev in heat.tiers],
        "profile_long_tiers": _f32(heat.profile_long_tiers),
        "profile_short_tiers": _f32(heat.profile_short_tiers),
        "price_view": [float(view_lo), float(view_hi)],
        "analysis": _scrub(heat.analysis),
    }


def levels_payload(
    exchange: str,
    symbol: str,
    range_key: str,
    model,
    mode: str,
    asof: str | None,
    bar: str | None,
    replay: bool,
    t: str,
    price: str,
) -> dict:
    """Leverage tiers at one bar and price, from the same series as the map."""
    try:
        model_id = int(model)
        t_ms = int(t)
        price_f = float(price)
    except (TypeError, ValueError):
        raise DataError("levels needs model, t (bar open in ms) and price.") from None
    if model_id not in MODELS or not math.isfinite(price_f):
        raise DataError("levels needs a known model and a finite price.")
    series = get_series(exchange, symbol, range_key, mode, asof, bar, replay).series
    hits = np.flatnonzero(series.times == t_ms)
    if not len(hits):
        raise DataError("t is not a bar open in this map.")
    column = int(hits[0])
    heat = build_heatmap(series, model_id, tier_column=column)
    b = int(np.argmin(np.abs(heat.centers - price_f)))
    tiers = [
        {"leverage": int(lev), "long_usd": float(heat.long_tier_column[k, b]), "short_usd": float(heat.short_tier_column[k, b])}
        for k, lev in enumerate(heat.tiers)
    ]
    return {
        "t": t_ms,
        "price": float(heat.centers[b]),
        "tiers": tiers,
        "long_usd": float(heat.long_heat[b, column]),
        "short_usd": float(heat.short_heat[b, column]),
    }


def status_payload() -> dict:
    from liqmap import __version__

    return {
        "ok": True,
        "version": __version__,
        "default": {"exchange": "okx", "symbol": "BTC", "range": "24h", "model": 1, "mode": "pair"},
        "exchanges": [
            {"id": "okx", "name": EXCHANGE_NAMES["okx"], "live": True},
            {"id": "binance", "name": EXCHANGE_NAMES["binance"], "live": True},
            {"id": "demo", "name": EXCHANGE_NAMES["demo"], "live": False},
        ],
        "ranges": [
            {"id": key, "bar": spec["bar"], "seconds": spec["seconds"]}
            for key, spec in RANGES.items()
        ],
        "models": [public_model(model_id) for model_id in (1, 2, 3)],
        "mmr": MMR,
        "coins": list(COINS),
    }


def symbols_payload() -> dict:
    return {
        "coins": list(COINS),
        "exchanges": [
            {"id": key, "name": value}
            for key, value in EXCHANGE_NAMES.items()
            if key != "aggregate"
        ],
        "quote": "USDT",
    }
