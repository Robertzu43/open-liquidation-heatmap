"""First-principles isolated liquidation price.

This module is the expected-value oracle for the validation tests. It does
not import the heatmap package.

Binance USD-M isolated one-way positions:

    LP = (WB + cum - side * qty * entry) / (qty * mmr - side * qty)

with ``side = +1`` for a long and ``side = -1`` for a short, ``WB`` the
isolated wallet balance, and ``cum`` the bracket maintenance amount.
See https://www.binance.com/en/support/faq/detail/b3c689c1f50a44cabb3a84e663b81d93

With ``qty = 1``, ``cum = 0`` and no extra margin this reduces to::

    long:  entry * (1 - 1/leverage) / (1 - mmr)
    short: entry * (1 + 1/leverage) / (1 + mmr)
"""

from __future__ import annotations

import math


def liq_price(
    entry: float,
    leverage: float,
    side: str,
    mmr: float,
    *,
    qty: float = 1.0,
    cum: float = 0.0,
    extra_margin: float = 0.0,
) -> float:
    """Binance isolated liquidation price for one position.

    ``side`` is ``"long"`` or ``"short"``. ``mmr`` is a decimal (``0.004``
    is 0.4%). ``cum`` is the maintenance amount. ``qty`` is base size.
    ``extra_margin`` is quote added to the isolated wallet (negative when
    funding is taken from position margin).
    """
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
    if cum < 0:
        raise ValueError("cum must be >= 0")
    if not all(math.isfinite(value) for value in (entry, leverage, mmr, qty, cum, extra_margin)):
        raise ValueError("inputs must be finite")
    # Wallet balance is initial margin plus extra margin. Liquidation is where
    # margin balance equals ``mark_notional * mmr - cum``.
    wallet = entry * qty / leverage + extra_margin
    side_sign = 1.0 if side == "long" else -1.0
    denominator = qty * mmr - side_sign * qty
    if denominator == 0:
        raise ValueError("Binance denominator is zero")
    return (wallet + cum - side_sign * qty * entry) / denominator
