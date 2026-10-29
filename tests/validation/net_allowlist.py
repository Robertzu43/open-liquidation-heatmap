"""Hosts the heatmap is allowed to name in Python code.

The supply-chain test fails when Python code names any other host, and when
any file contains a prohibited vendor URL, import, or API key.

Exchange calls on this tree are Binance's live USDT-M host, Binance's public
archive, OKX, and Bybit's public v5 market API. ``http://127.0.0.1`` is the local server message in
``liqmap/server.py``. ``http://evil`` is a rejected symbol in
``tests/test_model.py``.
"""

from __future__ import annotations

ALLOWED_EXCHANGE_HOSTS: tuple[str, ...] = (
    "fapi.binance.com",
    "data.binance.vision",
    "www.okx.com",
    "api.bybit.com",
)

ALLOWED_LOCAL_HOSTS: tuple[str, ...] = (
    "127.0.0.1",
    "evil",
)
