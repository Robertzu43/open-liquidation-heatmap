"""Cache public series separately from the model so a model switch stays cheap."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from contextlib import contextmanager
import math
import threading
import time

import numpy as np

from liqmap.data import (
    COINS,
    EXCHANGE_NAMES,
    MAX_BARS,
    RANGES,
    DataError,
    RequestDeadlineExceeded,
    check_deadline,
    exchange_name,
    is_stale,
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
MAX_SERIES_CACHE = 16
MAX_SPAN_CACHE = 2
MAX_REPLAY_DAYS = 31
MAX_REPLAY_BARS = 12000
SCHEMA_VERSION = "1.0"

_cache: dict[tuple, tuple[float, object]] = {}
_spans: dict[tuple, list] = {}
_locks: dict[tuple, list] = {}
_guard = threading.Lock()


@contextmanager
def _key_lock(key: tuple):
    # Retain references before waiting, so eviction cannot split one key's lock.
    with _guard:
        entry = _locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        remaining = check_deadline()
        acquired = entry[0].acquire() if remaining is None else entry[0].acquire(timeout=remaining)
        if not acquired:
            raise RequestDeadlineExceeded("Market data request timed out waiting for a cached series.")
        try:
            check_deadline()
            yield
        finally:
            entry[0].release()
    finally:
        with _guard:
            entry[1] -= 1
            if entry[1] == 0:
                _locks.pop(key, None)


def _canonical(exchange: str, symbol: str, range_key: str, mode: str) -> tuple[str, str, str, str]:
    mode_key = (mode or "pair").strip().lower()
    exchange_key = (exchange or "okx").strip().lower()
    if exchange_key not in EXCHANGE_NAMES:
        raise DataError("Unknown exchange. Use okx, binance, demo, or aggregate.")
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
        while len(_cache) >= MAX_SERIES_CACHE:
            _cache.pop(min(_cache, key=lambda item: _cache[item][0]))


class SourceUnavailable(DataError):
    """A validated request could not be fulfilled by public market sources."""


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
    from_s = parse_asof(start)
    to_s = parse_asof(end)
    effective_end = int(time.time()) if to_s is None else to_s
    if effective_end < from_s:
        raise DataError("Replay end must be after its start.")
    if effective_end - from_s > MAX_REPLAY_DAYS * 86400:
        raise DataError("Replay duration is limited to 31 days. Choose a shorter interval.")
    total_bars = (effective_end - from_s) // int(spec["bar_seconds"]) + int(spec["seconds"]) // int(spec["bar_seconds"]) + 1
    if total_bars > MAX_REPLAY_BARS:
        raise DataError("Replay exceeds the 12000-bar span limit. Choose a shorter interval or larger bars.")
    try:
        span = load_span(coin, pair, spec, from_s, to_s)
    except RequestDeadlineExceeded:
        raise
    except DataError as exc:
        raise SourceUnavailable(str(exc)) from exc
    with _guard:
        now = time.time()
        for key, (used, _) in list(_spans.items()):
            if now - used >= SPAN_TTL:
                _spans.pop(key, None)
        while len(_spans) >= MAX_SPAN_CACHE:
            _spans.pop(min(_spans, key=lambda key: _spans[key][0]))
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
    with _key_lock(key):
        now = time.time()
        with _guard:
            hit = _cache.get(key)
        if hit is not None and now - hit[0] < SERIES_TTL.get(range_name, 120):
            return hit[1]
        try:
            loaded = load_market(exchange_key, coin, range_name, mode_key, asof=asof, bar=bar)
        except RequestDeadlineExceeded:
            raise
        except DataError as exc:
            raise SourceUnavailable(str(exc)) from exc
        _evict(time.time())
        with _guard:
            # Other keys can finish while this one loads.
            while len(_cache) >= MAX_SERIES_CACHE:
                _cache.pop(min(_cache, key=lambda item: _cache[item][0]))
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
        "entry": "close of the fully closed candle; positions become active after that candle closes",
    }


def _f32(values: np.ndarray) -> str:
    array = np.ascontiguousarray(values, dtype="<f4")
    if not np.isfinite(array).all():
        raise ValueError("Cannot encode non-finite chart data.")
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


def _fingerprint_value(value):
    """Canonical source values, including masks and venue components."""
    if dataclasses.is_dataclass(value):
        return {field.name: _fingerprint_value(getattr(value, field.name)) for field in dataclasses.fields(value) if field.name not in {"note", "warnings"}}
    if isinstance(value, np.ndarray):
        array = np.array(value, dtype=value.dtype.newbyteorder("<"), order="C", copy=True)
        if np.issubdtype(array.dtype, np.floating):
            array[np.isnan(array)] = np.nan  # Canonicalize NaN payload bits.
        return {"dtype": array.dtype.str, "shape": list(array.shape), "data": base64.b64encode(array.tobytes()).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _fingerprint_value(item) for key, item in value.items() if key != "received_at_ms"}
    if isinstance(value, (tuple, list)):
        return [_fingerprint_value(item) for item in value]
    return _scrub(value)


def data_fingerprint(loaded) -> str:
    content = {"series": _fingerprint_value(loaded.series), "source": loaded.source, "venues": list(loaded.venues), "as_of": int(loaded.as_of)}
    raw = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _quality(loaded, *, historical: bool = False) -> dict:
    series = loaded.series
    provenance = getattr(series, "provenance", {})
    synthetic = loaded.source == "demo" or series.exchange == "demo" or provenance.get("history_kind") == "synthetic"
    stale = bool(loaded.stale)
    if not historical and loaded.source in {"okx", "binance-live", "aggregate"} and provenance.get("history_kind") != "reconstructed_archive":
        stale = stale or is_stale(loaded.as_of, series.bar_seconds, limit_s=2 * int(series.bar_seconds))
    coverage = {}
    for name in ("candle_observed", "oi_observed", "flow_observed"):
        mask = getattr(series, name, None)
        coverage[name] = None if mask is None else {"observed": int(np.count_nonzero(mask)), "total": len(mask)}
    degraded = any(coverage[name] is not None and coverage[name]["observed"] < coverage[name]["total"] for name in ("candle_observed", "oi_observed"))
    status = "synthetic" if synthetic else "stale" if stale else "unknown" if not provenance else "degraded" if degraded else "observed"
    availability = getattr(series, "oi_available_at", None)
    known = availability is not None and len(availability) > 0 and bool(np.all(np.isfinite(availability) & (availability > 0)))
    trusted_availability = provenance.get("availability") in {"recorded_receipt", "known_publication"} and provenance.get("history_kind") != "reconstructed_archive"
    return {
        "status": status,
        "synthetic": synthetic,
        "stale": stale,
        "degraded": degraded,
        "source_as_of": int(loaded.as_of),
        "coverage": coverage,
        "coverage_basis": "source_masks" if provenance else "inferred_from_series",
        "historical_availability": "known" if known and trusted_availability and not synthetic else "unknown",
        "provenance": _scrub(getattr(series, "provenance", {})),
        "warnings": list(series.warnings),
    }


def _metadata(loaded, heat, *, historical: bool = False) -> dict:
    from liqmap import model as model_module

    metadata = getattr(heat, "metadata", {})
    return {
        "schema_version": SCHEMA_VERSION,
        "model_version": getattr(model_module, "MODEL_VERSION", "legacy-0.2"),
        "data_fingerprint": data_fingerprint(loaded),
        "quality": _quality(loaded, historical=historical),
        "assumptions": _scrub(metadata.get("assumptions", ["Estimated positions from assumed leverage; not observed account positions."])),
        "time_semantics": {
            "times": "bar_open_unix_ms",
            "as_of": "last_source_bar_open_unix_ms",
            "generated_at": "response_generation_unix_ms_not_source_freshness",
            "state": metadata.get("state_timestamp", "bar_close_post_update"),
            "feature_as_of": "last_grid_bar_close_unix_ms",
            "historical_availability": "unknown_unless_source_metadata_proves_it",
        },
        "units": {
            "price": "USDT per base asset", "exposure": "USD entry notional estimate",
            "time": "unix milliseconds", "distance": "percent", "imbalance": "ratio",
            "window_pct": "percent", "short_to_long": "ratio",
            "concentration": "fraction", "persistence": "seconds",
        },
        "features": _scrub(getattr(heat, "features", None)),
        "feature_as_of": int(loaded.series.times[-1]) + int(loaded.series.bar_seconds) * 1000,
        "model_metadata": _scrub(metadata),
    }


def readiness_payload() -> dict:
    """Observe the default live market cache without issuing a network request."""
    key = ("okx", "BTC", "24h", "pair", RANGES["24h"]["bar"], None)
    with _guard:
        hit = _cache.get(key)
    freshness_budget = 2 * int(RANGES["24h"]["bar_seconds"])
    data = {"status": "unknown", "market": {"exchange": "okx", "symbol": "BTC", "range": "24h"}, "freshness_budget_seconds": freshness_budget}
    ready = False
    if hit is not None:
        stamp, loaded = hit
        quality = _quality(loaded)
        expired = time.time() - stamp >= SERIES_TTL["24h"]
        stale = quality["stale"] or is_stale(loaded.as_of, loaded.series.bar_seconds, limit_s=freshness_budget)
        data.update({"status": "expired" if expired else "stale" if stale else quality["status"], "source": loaded.source, "source_as_of": int(loaded.as_of)})
        ready = not expired and not stale and quality["status"] == "observed"
    return {"ok": ready, "process": "healthy", "data": data}


def features_payload(*args, **kwargs) -> dict:
    payload = load_heatmap(*args, **kwargs)
    chart_fields = {"open", "high", "low", "close", "volume", "centers", "heat", "bins", "profile_long", "profile_short", "profile_long_tiers", "profile_short_tiers", "tiers", "price_view", "analysis"}
    return {key: value for key, value in payload.items() if key not in chart_fields}


def _visible_bars(value: int | str | None, available: int | None = None) -> int | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str, np.integer)):
            raise ValueError
        count = int(value)
    except (TypeError, ValueError):
        raise DataError("visible_bars must be an integer between 2 and 3000.") from None
    if not 2 <= count <= MAX_BARS or (available is not None and count > available):
        raise DataError("visible_bars must be between 2 and the available history bars (maximum 3000).")
    return count


def load_heatmap(
    exchange: str,
    symbol: str,
    range_key: str,
    model,
    mode: str = "pair",
    asof: str | None = None,
    bar: str | None = None,
    replay: bool = False,
    *,
    visible_bars: int | str | None = None,
) -> dict:
    visible_bars = _visible_bars(visible_bars)
    try:
        model_id = int(model)
    except (TypeError, ValueError):
        raise DataError("Model must be 1, 2, or 3.") from None
    model_meta = public_model(model_id)
    loaded = get_series(exchange, symbol, range_key, mode, asof, bar, replay)
    visible_bars = _visible_bars(visible_bars, len(loaded.series.times))
    heat = build_heatmap(loaded.series, model_id, visible_bars=visible_bars)
    series = heat.series
    exchange_key, _coin, range_name, mode_key = _canonical(exchange, symbol, range_key, mode)
    spec, end_open = resolve_view(exchange_key, mode_key, range_name, asof, bar)
    view_lo, view_hi = suggest_view(series)
    grid = np.ascontiguousarray(heat.long_heat + heat.short_heat, dtype=np.float64)
    metadata = _metadata(loaded, heat, historical=end_open is not None)
    return {
        **metadata,
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
        "stale": metadata["quality"]["stale"],
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
    *,
    visible_bars: int | str | None = None,
) -> dict:
    """Leverage tiers at one visible bar, retaining the full history state."""
    visible_bars = _visible_bars(visible_bars)
    try:
        model_id = int(model)
        t_ms = int(t)
        price_f = float(price)
    except (TypeError, ValueError):
        raise DataError("levels needs model, t (bar open in ms) and price.") from None
    if model_id not in MODELS or not math.isfinite(price_f):
        raise DataError("levels needs a known model and a finite price.")
    series = get_series(exchange, symbol, range_key, mode, asof, bar, replay).series
    visible_bars = _visible_bars(visible_bars, len(series.times))
    visible_times = series.times[-visible_bars:] if visible_bars is not None else series.times
    hits = np.flatnonzero(visible_times == t_ms)
    if not len(hits):
        raise DataError("t is not a bar open in this map.")
    column = int(hits[0])
    heat = build_heatmap(series, model_id, tier_column=column, visible_bars=visible_bars)
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
        "schema_version": SCHEMA_VERSION,
        "readiness": readiness_payload(),
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
