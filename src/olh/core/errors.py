"""Errors for public market-data calls."""


class ExchangeError(RuntimeError):
    """The venue responded, but the payload cannot be used."""


class ExchangeBlocked(ExchangeError):
    """REST refused the call. Binance and Bybit often do this from US hosts."""
