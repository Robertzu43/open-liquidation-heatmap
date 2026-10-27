"""Map a user symbol onto each public API's instrument id."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedSymbol:
    base: str
    quote: str

    @property
    def binance(self) -> str:
        return f"{self.base}{self.quote}"

    @property
    def bybit(self) -> str:
        return f"{self.base}{self.quote}"

    @property
    def okx(self) -> str:
        return f"{self.base}-{self.quote}-SWAP"

    def __str__(self) -> str:
        return self.binance


def parse_symbol(raw: str) -> ParsedSymbol:
    """Accept BTCUSDT, BTC-USDT, BTC/USDT, or BTC-USDT-SWAP."""
    text = raw.strip().upper().replace("/", "-").replace("_", "-")
    if text.endswith("-SWAP"):
        text = text[: -len("-SWAP")]
    if "-" in text:
        base, quote = text.split("-", 1)
    elif text.endswith("USDT"):
        base, quote = text[: -len("USDT")], "USDT"
    elif text.endswith("USDC"):
        base, quote = text[: -len("USDC")], "USDC"
    elif text.endswith("USD"):
        base, quote = text[: -len("USD")], "USD"
    else:
        raise ValueError(f"cannot parse {raw!r}; use a linear perp such as BTCUSDT")
    if not base or not quote or "-" in quote:
        raise ValueError(f"cannot parse {raw!r}")
    return ParsedSymbol(base=base, quote=quote)
