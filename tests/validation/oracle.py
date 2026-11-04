"""First-principles isolated liquidation price.

This module is the expected-value oracle for the validation tests. It does
not import the heatmap package.

Binance USD-M isolated one-way positions:

    LP = (WB + cum - side * qty * entry) / (qty * mmr - side * qty)

with ``side = +1`` for a long and ``side = -1`` for a short, ``WB`` the
isolated wallet balance, and ``cum`` the bracket maintenance amount.
See https://www.binance.com/en/support/faq/detail/b3c689c1f50a44cabb3a84e663b81d93

With ``qty = 1``, ``cum = 0`` and no extra margin, ``WB`` is ``entry / leverage``
and this reduces to::

    long:  entry * (1 - 1/leverage) / (1 - mmr)
    short: entry * (1 + 1/leverage) / (1 + mmr)
"""

from __future__ import annotations


def liq_price(entry: float, leverage: float, side: str, mmr: float) -> float:
    """Binance isolated liquidation price for one unit, no extra margin, no maintenance amount."""
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    side_sign = 1.0 if side == "long" else -1.0
    return (entry / leverage - side_sign * entry) / (mmr - side_sign)
