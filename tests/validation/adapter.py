"""Thin binding from the validation tests to production code.

The tests call only this module. Repoint the candidate tuples below when the
model PR exports the contract under different names. No formula is implemented
here.

Contract
--------
``liq_price(entry, leverage, side, mmr, *, qty=1, cum=0, fee=0,
extra_margin=0, venue="binance") -> float``

    Isolated liquidation price. ``side`` is ``"long"`` or ``"short"``.
    ``mmr`` and ``fee`` are decimals. ``cum`` is the bracket maintenance
    amount (Bybit: maintenance-margin deduction). ``qty`` is base size.
    ``venue`` is ``binance``, ``bybit`` (classic isolated article),
    ``bybit_uta``, or ``okx``.

    The four-argument call ``liq_price(entry, leverage, side, mmr)`` is the
    Binance isolated formula with a zero maintenance amount. With ``cum``,
    ``fee``, and ``extra_margin`` left at zero, OKX and ``bybit_uta`` reduce
    to that same price. ``venue="bybit"`` does not: it is the older
    entry-notional formula.

``fade_swept(levels, bars) -> list[tuple[float, float, str]]``

    ``levels`` are ``(price, intensity, side)`` placed before ``bars``.
    ``bars`` are ``(high, low)`` in time order. A long level is swept when
    any bar has ``low <= price``. A short level is swept when any bar has
    ``high >= price``. Swept levels are removed. Survivors keep their
    intensity and their relative order. A bar with ``high < low`` raises
    ``ValueError``.

``bin_intensity(prices, intensities, edges) -> list[float]``

    Sums intensity into bins ``[edges[i], edges[i + 1])``. The last bin is
    closed on the right, ``[edges[-2], edges[-1]]``. Prices outside the outer
    edges are excluded, so a point is never dumped into the nearest edge bin.
    For prices inside the outer edges, the bin totals add up to the intensity
    that was binned. Uneven ``prices`` and ``intensities``, or edges that are
    not strictly increasing, raise ``ValueError``.

Until ``liq_price`` exists, a legacy fallback calls
``heatmap.liq_price_long`` / ``heatmap.liq_price_short`` for the zero-tier
margin-balance case only. Anything the fallback cannot represent raises
``InterfaceGap``, and the tests skip with that reason.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Sequence
from typing import Any

# First existing attribute wins. After the model PR, leave heatmap.liq_price
# first if that module exports it; otherwise put the real module first and
# delete the legacy fallback at the bottom of ``_resolve_liq``.
LIQ_PRICE_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("heatmap", "liq_price"),
    ("open_liquidation.pricing", "liq_price"),
)
FADE_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("heatmap", "fade_swept"),
    ("open_liquidation.pricing", "fade_swept"),
)
BIN_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("heatmap", "bin_intensity"),
    ("open_liquidation.pricing", "bin_intensity"),
)

LEGACY_MODULE = "heatmap"
LEGACY_LONG = "liq_price_long"
LEGACY_SHORT = "liq_price_short"

# Venues whose price matches the four-argument margin-balance formula when
# cum, fee, and extra margin are all zero.
_FOUR_ARG_VENUES = frozenset({"binance", "bybit_uta", "okx"})

Level = tuple[float, float, str]
Bar = tuple[float, float]


class InterfaceGap(RuntimeError):
    """The production function for this part of the contract is not bound."""


def liq_price(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    *,
    qty: float = 1.0,
    cum: float = 0.0,
    fee: float = 0.0,
    extra_margin: float = 0.0,
    venue: str = "binance",
) -> float:
    """Return the production isolated liquidation price."""
    fn, kind = _resolve_liq()
    extras: dict[str, float | str] = {
        "qty": qty,
        "cum": cum,
        "fee": fee,
        "extra_margin": extra_margin,
        "venue": venue,
    }
    if kind.startswith("legacy:"):
        if not _four_arg_covers(extras):
            raise InterfaceGap(
                "legacy heatmap.liq_price_long/liq_price_short only covers "
                "the zero-tier margin-balance formula "
                f"(venue in {sorted(_FOUR_ARG_VENUES)}, cum=fee=extra_margin=0). "
                "Got "
                f"venue={venue!r}, cum={cum}, fee={fee}, extra_margin={extra_margin}. "
                "Repoint LIQ_PRICE_CANDIDATES in tests/validation/adapter.py "
                "once the model exports liq_price."
            )
        if side == "long":
            return float(fn[0](entry, leverage, mmr))
        if side == "short":
            return float(fn[1](entry, leverage, mmr))
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    return float(_call_liq(fn, entry, leverage, side, mmr, extras))


def fade_swept(
    levels: Sequence[tuple[float, float, str]],
    bars: Sequence[tuple[float, float]],
) -> list[Level]:
    """Return unswept ``(price, intensity, side)`` levels from production."""
    fn = _resolve_named(FADE_CANDIDATES, "fade_swept")
    try:
        raw = fn(list(levels), list(bars))
    except InterfaceGap:
        raise
    except TypeError as exc:
        raise InterfaceGap(
            "fade_swept rejected (price, intensity, side) levels and "
            f"(high, low) bars ({exc}). Repoint FADE_CANDIDATES or the "
            "normalization in tests/validation/adapter.py."
        ) from exc
    if raw is None:
        raise InterfaceGap("fade_swept returned None")
    return [_as_level(item) for item in raw]


def bin_intensity(
    prices: Sequence[float],
    intensities: Sequence[float],
    edges: Sequence[float],
) -> list[float]:
    """Return production bin totals for ``prices`` and ``intensities``."""
    fn = _resolve_named(BIN_CANDIDATES, "bin_intensity")
    try:
        raw = fn(list(prices), list(intensities), list(edges))
    except InterfaceGap:
        raise
    except TypeError as exc:
        raise InterfaceGap(
            "bin_intensity rejected (prices, intensities, edges) "
            f"({exc}). Repoint BIN_CANDIDATES in tests/validation/adapter.py."
        ) from exc
    if raw is None:
        raise InterfaceGap("bin_intensity returned None")
    return [float(value) for value in raw]


def binding_status() -> dict[str, str]:
    """Names the production object each contract currently resolves to."""
    status: dict[str, str] = {}
    try:
        _, kind = _resolve_liq()
        status["liq_price"] = kind
    except InterfaceGap as exc:
        status["liq_price"] = f"unbound: {exc}"
    for label, candidates in (
        ("fade_swept", FADE_CANDIDATES),
        ("bin_intensity", BIN_CANDIDATES),
    ):
        try:
            fn = _resolve_named(candidates, label)
        except InterfaceGap as exc:
            status[label] = f"unbound: {exc}"
        else:
            status[label] = _qualname(fn)
    return status


def _resolve_liq() -> tuple[Any, str]:
    for module_name, attr in LIQ_PRICE_CANDIDATES:
        found = _lookup(module_name, attr)
        if found is not None:
            return found, _qualname(found)
    module = _import_optional(LEGACY_MODULE)
    if module is None:
        raise InterfaceGap(
            f"{LEGACY_MODULE} is not importable and none of "
            f"{LIQ_PRICE_CANDIDATES} export liq_price. Repoint "
            "LIQ_PRICE_CANDIDATES in tests/validation/adapter.py."
        )
    long_fn = getattr(module, LEGACY_LONG, None)
    short_fn = getattr(module, LEGACY_SHORT, None)
    if not callable(long_fn) or not callable(short_fn):
        raise InterfaceGap(
            f"{LEGACY_MODULE}.{LEGACY_LONG}/{LEGACY_SHORT} are gone and "
            f"none of {LIQ_PRICE_CANDIDATES} export liq_price. Repoint "
            "LIQ_PRICE_CANDIDATES in tests/validation/adapter.py."
        )
    return (long_fn, short_fn), (
        f"legacy:{LEGACY_MODULE}.{LEGACY_LONG}/{LEGACY_SHORT}"
    )


def _resolve_named(
    candidates: Sequence[tuple[str, str]], contract: str
) -> Callable[..., Any]:
    for module_name, attr in candidates:
        found = _lookup(module_name, attr)
        if found is not None:
            return found
    raise InterfaceGap(
        f"{contract} is not exported by {tuple(candidates)}. "
        "Repoint the candidate list in tests/validation/adapter.py "
        "once the model PR lands."
    )


def _lookup(module_name: str, attr: str) -> Callable[..., Any] | None:
    module = _import_optional(module_name)
    if module is None:
        return None
    found = getattr(module, attr, None)
    if callable(found):
        return found
    return None


def _import_optional(module_name: str) -> Any | None:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == module_name or module_name.startswith(missing + "."):
            return None
        raise InterfaceGap(
            f"importing {module_name} failed because {missing!r} is not "
            "installed. Install requirements.txt before running validation."
        ) from exc


def _omitted_default(
    key: str, value: float | str, extras: dict[str, float | str]
) -> bool:
    """True when a 4-arg implementation already implies this parameter."""
    if key in {"cum", "fee", "extra_margin"} and float(value) == 0.0:
        return True
    if key == "qty" and _four_arg_covers(extras):
        return True
    return key == "venue" and _four_arg_covers(extras)


def _four_arg_covers(extras: dict[str, float | str]) -> bool:
    if float(extras["cum"]) != 0 or float(extras["fee"]) != 0:
        return False
    if float(extras["extra_margin"]) != 0:
        return False
    return str(extras["venue"]) in _FOUR_ARG_VENUES


def _call_liq(
    fn: Callable[..., Any],
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    extras: dict[str, float | str],
) -> Any:
    signature = inspect.signature(fn)
    accepts_any_keyword = any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    )
    accepted = set(signature.parameters)
    kwargs: dict[str, float | str] = {}
    missing: list[str] = []
    for key, value in extras.items():
        if accepts_any_keyword or key in accepted:
            kwargs[key] = value
        elif _omitted_default(key, value, extras):
            continue
        else:
            missing.append(key)
    if missing:
        raise InterfaceGap(
            f"{_qualname(fn)} does not accept {missing}. Tier, fee, or "
            "venue behavior is not bound yet. Extend the production "
            "signature or repoint tests/validation/adapter.py."
        )
    try:
        return fn(entry, leverage, side, mmr, **kwargs)
    except TypeError as exc:
        raise InterfaceGap(
            f"{_qualname(fn)} rejected the validation contract ({exc}). "
            "Repoint LIQ_PRICE_CANDIDATES in tests/validation/adapter.py."
        ) from exc


def _as_level(item: Any) -> Level:
    if isinstance(item, (tuple, list)) and len(item) == 3:
        price, intensity, side = item
        return (float(price), float(intensity), str(side))
    if isinstance(item, dict) and {"price", "intensity", "side"} <= item.keys():
        return (float(item["price"]), float(item["intensity"]), str(item["side"]))
    price = getattr(item, "price", None)
    intensity = getattr(item, "intensity", None)
    side = getattr(item, "side", None)
    if price is not None and intensity is not None and side is not None:
        return (float(price), float(intensity), str(side))
    raise InterfaceGap(
        f"unrecognized fade level {item!r}. Teach _as_level in "
        "tests/validation/adapter.py about the production level type."
    )


def _qualname(fn: Any) -> str:
    module = getattr(fn, "__module__", "?")
    name = getattr(fn, "__name__", type(fn).__name__)
    return f"{module}.{name}"
