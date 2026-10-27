"""Exchange hosts the heatmap is allowed to call.

The supply-chain test fails the build when Python code names any other host,
and when any file contains a CoinGlass URL, import, or API key. Live smoke
tests open sockets only to this list.
"""

from __future__ import annotations

ALLOWED_EXCHANGE_HOSTS: tuple[str, ...] = (
    "fapi.binance.com",
    "api.bybit.com",
    "www.okx.com",
)
