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
    normalize_symbol,
    range_spec,
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

_cache: dict[tuple, tuple[float, object]] = {}
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


def get_series(exchange: str, symbol: str, range_key: str, mode: str = "pair"):
    exchange_key, coin, range_name, mode_key = _canonical(exchange, symbol, range_key, mode)
    key = (exchange_key, coin, range_name, mode_key)
    lock = _key_lock(key)
    with lock:
        now = time.time()
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < SERIES_TTL.get(range_name, 120):
            return hit[1]
        loaded = load_market(exchange_key, coin, range_name, mode_key)
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
        "decay": float(spec["decay"]),
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


def load_heatmap(exchange: str, symbol: str, range_key: str, model, mode: str = "pair") -> dict:
    try:
        model_id = int(model)
    except (TypeError, ValueError):
        raise DataError("Model must be 1, 2, or 3.") from None
    model_meta = public_model(model_id)
    loaded = get_series(exchange, symbol, range_key, mode)
    heat = build_heatmap(loaded.series, model_id)
    series = loaded.series
    _exchange, _coin, range_name, mode_key = _canonical(exchange, symbol, range_key, mode)
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
        "bar": RANGES[range_name]["bar"],
        "bar_seconds": int(series.bar_seconds),
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
        "price_view": [float(view_lo), float(view_hi)],
        "analysis": _scrub(heat.analysis),
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
