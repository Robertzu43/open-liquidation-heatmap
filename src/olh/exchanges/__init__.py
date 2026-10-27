"""Public adapters. Importing this package does not open a socket."""

from olh.exchanges.binance import BinanceAdapter
from olh.exchanges.bybit import BybitAdapter
from olh.exchanges.okx import OkxAdapter

ADAPTERS = {
    "binance": BinanceAdapter,
    "bybit": BybitAdapter,
    "okx": OkxAdapter,
}


def build_adapters(names: list[str], http=None) -> list:
    built = []
    for name in names:
        key = name.strip().lower()
        if key not in ADAPTERS:
            known = ", ".join(sorted(ADAPTERS))
            raise ValueError(f"unknown exchange {name!r}; choose from {known}")
        built.append(ADAPTERS[key](http=http))
    return built
