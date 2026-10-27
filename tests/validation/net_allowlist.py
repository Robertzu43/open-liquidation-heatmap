"""Exchange hosts the heatmap is allowed to call.

The supply-chain test fails the build when Python code names any other host,
and when any file contains a CoinGlass URL, import, or API key. Live smoke
tests open sockets only to this list.

Venues are Binance, Bybit, and OKX. ``data.binance.vision`` is Binance's
public historical-data archive (no API key), for when ``fapi.binance.com``
returns HTTP 451. A fourth exchange is not a heatmap source.
"""

from __future__ import annotations

ALLOWED_EXCHANGE_HOSTS: tuple[str, ...] = (
    "fapi.binance.com",
    "data.binance.vision",
    "api.bybit.com",
    "www.okx.com",
)
