"""First-principles isolated liquidation prices.

This module is the expected-value oracle for the validation tests. It does
not import the heatmap package. Formulas follow the exchange documents cited
on each branch.

Shared margin-balance formula (maintenance amount is zero, no close fee, no
extra margin), used by Binance USD-M isolated one-way positions and, in that
same limit, by OKX USDT swaps and Bybit's unified-account formula::

    long:  entry * (1 - 1/leverage) / (1 - mmr)
    short: entry * (1 + 1/leverage) / (1 + mmr)

Binance's published one-way form is

    LP = (WB + cum - side * qty * entry) / (qty * mmr - side * qty)

with ``side = +1`` for a long and ``side = -1`` for a short, ``WB`` the
isolated wallet balance, and ``cum`` the bracket maintenance amount.
See https://www.binance.com/en/support/faq/detail/b3c689c1f50a44cabb3a84e663b81d93

Maintenance margin on that venue is ``notional * mmr - cum``. Bracket
maintenance amounts are the deduction that keeps maintenance margin
continuous at a tier boundary:

    cum_n = cum_(n-1) + notional_floor_n * (mmr_n - mmr_(n-1))

Bybit states that recurrence directly:
https://www.bybit.com/en/help-center/article/Maintenance-Margin-USDT-Contract

Bybit's older isolated USDT article uses a different, entry-notional formula
(no mark price in the denominator). Worked examples on that page are the
``venue="bybit"`` branch:
https://www.bybit.com/en/help-center/article/Liquidation-Price-USDT-Contract

Bybit unified-account isolated formula (fee applies to extra margin):
https://www.bybit.com/en/help-center/article/UTA-Trading-Rules-Liquidation-Process

OKX USDT-margined isolated estimated liquidation price:
https://www.okx.com/en-us/help/vii-introduction-to-the-isolated-mode-of-single-multi-currency-portfolio-margin
"""

from __future__ import annotations

import bisect
import itertools
import math

VENUES = ("binance", "bybit", "bybit_uta", "okx")

# Venues whose zero-fee, zero-extra, zero-cum price is the margin-balance
# formula above. Bybit's older article is not in this set.
MARGIN_BALANCE_VENUES = ("binance", "bybit_uta", "okx")


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
    """Isolated liquidation price for one position.

    ``side`` is ``"long"`` or ``"short"``. ``mmr`` and ``fee`` are decimals
    (``0.004`` is 0.4%). ``cum`` is the Binance maintenance amount, which is
    the same role as Bybit's maintenance-margin deduction. ``qty`` is base
    size. ``extra_margin`` is quote added to the isolated wallet (negative
    when funding is taken from position margin).
    """
    _validate(entry, leverage, side, mmr, qty, cum, fee, extra_margin, venue)
    if venue == "bybit":
        return _bybit_classic(entry, leverage, side, mmr, qty, cum, extra_margin)
    if venue == "bybit_uta":
        return _bybit_uta(entry, leverage, side, mmr, qty, cum, fee, extra_margin)
    if venue == "okx":
        return _okx(entry, leverage, side, mmr, qty, fee, extra_margin)
    return _binance(entry, leverage, side, mmr, qty, cum, extra_margin)


def _validate(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    cum: float,
    fee: float,
    extra_margin: float,
    venue: str,
) -> None:
    if venue not in VENUES:
        raise ValueError(f"venue must be one of {VENUES}, got {venue!r}")
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    if entry <= 0:
        raise ValueError("entry must be positive")
    if leverage <= 1:
        raise ValueError("leverage must be greater than 1")
    if mmr < 0 or mmr >= 1:
        raise ValueError("mmr must be in [0, 1)")
    if qty <= 0:
        raise ValueError("qty must be positive")
    if fee < 0 or fee >= 1:
        raise ValueError("fee must be in [0, 1)")
    if cum < 0:
        raise ValueError("cum must be >= 0")
    # extra_margin may be negative when funding is charged to the position.
    if not all(
        math.isfinite(value)
        for value in (entry, leverage, mmr, qty, cum, fee, extra_margin)
    ):
        raise ValueError("inputs must be finite")
    if venue == "bybit" and fee != 0:
        raise ValueError(
            "the Bybit classic isolated formula has no close-fee term; "
            "use venue='bybit_uta'"
        )
    if venue == "okx" and cum != 0:
        raise ValueError("OKX isolated tiers adjust mmr; they do not take cum")


def _binance(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    cum: float,
    extra_margin: float,
) -> float:
    """Binance isolated one-way wallet, including bracket cum and extra margin.

    Wallet balance is initial margin plus extra margin. Liquidation is where
    margin balance equals ``mark_notional * mmr - cum``.
    """
    # LP = (WB + cum - side * qty * entry) / (qty * mmr - side * qty)
    # side is +1 long, -1 short. WB is the isolated wallet balance.
    wallet = entry * qty / leverage + extra_margin
    side_sign = 1.0 if side == "long" else -1.0
    numerator = wallet + cum - side_sign * qty * entry
    denominator = qty * mmr - side_sign * qty
    if denominator == 0:
        raise ValueError("Binance denominator is zero")
    return numerator / denominator


def _okx(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    fee: float,
    extra_margin: float,
) -> float:
    """OKX USDT isolated estimated liquidation price.

    ``qty`` is face value times contract count times the contract multiplier,
    in base asset. Margin balance is initial margin plus margin added.
    """
    margin = entry * qty / leverage + extra_margin
    if side == "long":
        denominator = qty * (mmr + fee - 1.0)
        if denominator == 0:
            raise ValueError("OKX long denominator is zero")
        return (margin - qty * entry) / denominator
    denominator = qty * (mmr + fee + 1.0)
    return (margin + qty * entry) / denominator


def _bybit_classic(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    deduction: float,
    extra_margin: float,
) -> float:
    """Bybit isolated formula from the USDT liquidation-price article.

    Maintenance margin uses entry notional, not the mark notional:

        position value = qty * entry
        initial margin = position value / leverage
        maintenance = position value * mmr - deduction
        long:  entry - (initial - maintenance) / qty - extra / qty
        short: entry + (initial - maintenance) / qty + extra / qty
    """
    position_value = qty * entry
    initial = position_value / leverage
    maintenance = position_value * mmr - deduction
    cushion = (initial - maintenance) / qty
    extra = extra_margin / qty
    if side == "long":
        return entry - cushion - extra
    return entry + cushion + extra


def _bybit_uta(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    qty: float,
    deduction: float,
    fee: float,
    extra_margin: float,
) -> float:
    """Bybit unified-account isolated formula.

    The published long form is

        (entry * qty - entry * qty / leverage
            - extra / (1 - fee) - deduction) / (qty * (1 - mmr))
    """
    if side == "long":
        if fee >= 1:
            raise ValueError("fee must be < 1")
        numerator = (
            entry * qty
            - entry * qty / leverage
            - extra_margin / (1.0 - fee)
            - deduction
        )
        denominator = qty * (1.0 - mmr)
        if denominator <= 0:
            raise ValueError("UTA long denominator must be positive")
        return numerator / denominator
    numerator = (
        entry * qty
        + entry * qty / leverage
        + extra_margin / (1.0 + fee)
        + deduction
    )
    return numerator / (qty * (1.0 + mmr))


def fade_swept(
    levels: list[tuple[float, float, str]] | tuple[tuple[float, float, str], ...],
    bars: list[tuple[float, float]] | tuple[tuple[float, float], ...],
) -> list[tuple[float, float, str]]:
    """Drop levels whose price was traded in ``bars``.

    ``levels`` are ``(price, intensity, side)`` already on the book.
    ``bars`` are later ``(high, low)`` prints, oldest first. A long is swept
    when any low is at or below its price. A short is swept when any high is
    at or above its price. Survivors keep their intensity and order.
    """
    parsed_bars: list[tuple[float, float]] = []
    for high, low in bars:
        if not math.isfinite(high) or not math.isfinite(low):
            raise ValueError("bars must be finite")
        if high < low:
            raise ValueError("bar high must be >= low")
        parsed_bars.append((float(high), float(low)))

    kept: list[tuple[float, float, str]] = []
    for price, intensity, side in levels:
        if side not in ("long", "short"):
            raise ValueError(f"side must be 'long' or 'short', got {side!r}")
        if not math.isfinite(price) or not math.isfinite(intensity):
            raise ValueError("levels must be finite")
        if intensity < 0:
            raise ValueError("intensity must be >= 0")
        swept = False
        if side == "long":
            swept = any(low <= price for _high, low in parsed_bars)
        else:
            swept = any(high >= price for high, _low in parsed_bars)
        if not swept:
            kept.append((float(price), float(intensity), side))
    return kept


def bin_intensity(
    prices: list[float] | tuple[float, ...],
    intensities: list[float] | tuple[float, ...],
    edges: list[float] | tuple[float, ...],
) -> list[float]:
    """Sum intensity into ``[edges[i], edges[i + 1])``.

    The last bin is closed on the right. Prices outside the outer edges are
    excluded, so an outlier is not poured into the nearest bin. The sum of
    the returned bins equals the sum of intensities whose price landed inside
    the outer edges, inclusive of the right edge.
    """
    if len(prices) != len(intensities):
        raise ValueError("prices and intensities must have the same length")
    if len(edges) < 2:
        raise ValueError("edges need at least two boundaries")
    bounds = [float(edge) for edge in edges]
    if any(not math.isfinite(edge) for edge in bounds):
        raise ValueError("edges must be finite")
    if any(right <= left for left, right in itertools.pairwise(bounds)):
        raise ValueError("edges must be strictly increasing")

    totals = [0.0] * (len(bounds) - 1)
    for price, intensity in zip(prices, intensities, strict=True):
        if not math.isfinite(price) or not math.isfinite(intensity):
            raise ValueError("prices and intensities must be finite")
        if intensity < 0:
            raise ValueError("intensity must be >= 0")
        if price < bounds[0] or price > bounds[-1]:
            continue
        if price == bounds[-1]:
            totals[-1] += float(intensity)
            continue
        index = bisect.bisect_right(bounds, price) - 1
        totals[index] += float(intensity)
    return totals
