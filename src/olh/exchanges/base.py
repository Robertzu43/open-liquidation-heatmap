"""Exchange adapter surface. One venue failing must not stop the others."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from olh.core.symbols import ParsedSymbol


@dataclass(frozen=True)
class Capabilities:
    """What a public adapter can actually supply."""

    klines: bool = True
    open_interest: bool = True
    mark_price: bool = False
    taker_volume: bool = False
    account_ratio: bool = False
    risk_tiers: bool = False
    oi_history: str = ""


@dataclass
class MarketSeries:
    exchange: str
    candles: pd.DataFrame
    oi: pd.DataFrame
    max_leverage: float
    tick_size: float | None = None
    warnings: list[str] = field(default_factory=list)
    trust_oi_deltas: bool = True


class ExchangeAdapter(Protocol):
    name: str
    capabilities: Capabilities

    def fetch(self, symbol: ParsedSymbol, start, end, interval: str) -> MarketSeries:
        """Public candles and open interest for ``[start, end)``."""
