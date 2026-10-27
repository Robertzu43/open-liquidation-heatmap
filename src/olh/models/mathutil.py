"""Isolated-margin liquidation prices, including the maintenance amount.

Binance's USD-M identity for one isolated position that posted exactly its
initial margin. Maintenance margin at the liquidation price is
``qty * P * mmr - cum`` (the maintenance amount is deducted). Setting equity
equal to that maintenance margin gives:

    long:  P = (entry * (1 - 1 / leverage) - cum / qty) / (1 - mmr)
    short: P = (entry * (1 + 1 / leverage) + cum / qty) / (1 + mmr)

``cum`` is 0 when the position size is unknown, which is the heatmap's
default. With ``mmr`` 0.005, entry 100, and leverage 10 the prices are
90.452261... and 109.452736..., the 0.50% tier with a zero maintenance amount.

A long result at or above the entry is not a valid liquidation price and
comes back as ``None``.
"""

from __future__ import annotations


def liq_price_long(
    entry: float,
    leverage: float,
    mmr: float = 0.005,
    maintenance_amount: float = 0.0,
    qty: float | None = None,
) -> float | None:
    _check(entry, leverage, mmr, maintenance_amount, qty)
    cum = _cum_per_unit(maintenance_amount, qty)
    price = (entry * (1.0 - 1.0 / leverage) - cum) / (1.0 - mmr)
    if price >= entry or price <= 0:
        return None
    return price


def liq_price_short(
    entry: float,
    leverage: float,
    mmr: float = 0.005,
    maintenance_amount: float = 0.0,
    qty: float | None = None,
) -> float | None:
    _check(entry, leverage, mmr, maintenance_amount, qty)
    cum = _cum_per_unit(maintenance_amount, qty)
    price = (entry * (1.0 + 1.0 / leverage) + cum) / (1.0 + mmr)
    if price <= entry:
        return None
    return price


def _cum_per_unit(maintenance_amount: float, qty: float | None) -> float:
    if not maintenance_amount or qty is None:
        return 0.0
    return maintenance_amount / qty


def _check(entry: float, leverage: float, mmr: float, maintenance_amount: float, qty: float | None) -> None:
    if entry <= 0:
        raise ValueError("entry must be positive")
    if leverage <= 1:
        raise ValueError("leverage must be greater than 1")
    if mmr < 0 or mmr >= 1:
        raise ValueError("mmr must be in [0, 1)")
    if maintenance_amount < 0:
        raise ValueError("maintenance amount must be >= 0")
    if qty is not None and qty <= 0:
        raise ValueError("qty must be positive when it is set")
