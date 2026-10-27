"""Versioned maintenance-margin brackets loaded from a data file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_CANDIDATES = [
    Path(__file__).resolve().parents[3] / "data" / "brackets" / "binance_usdm_v1.json",
    Path.cwd() / "data" / "brackets" / "binance_usdm_v1.json",
]


@dataclass(frozen=True)
class Bracket:
    notional_floor: float
    notional_cap: float
    max_leverage: float
    mmr: float
    maintenance_amount: float


@dataclass(frozen=True)
class SymbolBrackets:
    max_leverage: float
    brackets: tuple[Bracket, ...]
    version: str
    source: str


def load_brackets(path: str | Path | None = None) -> dict:
    candidate = Path(path) if path else next((item for item in _CANDIDATES if item.is_file()), None)
    if candidate is None:
        raise FileNotFoundError("binance_usdm_v1.json was not found")
    return json.loads(candidate.read_text(encoding="utf-8"))


def brackets_for(symbol: str, table: dict | None = None) -> SymbolBrackets:
    table = table or load_brackets()
    symbols = table.get("symbols", {})
    raw = symbols.get(symbol) or symbols.get("_default")
    if raw is None:
        raise KeyError(symbol)
    brackets = tuple(
        Bracket(
            notional_floor=float(row["notional_floor"]),
            notional_cap=float(row["notional_cap"]),
            max_leverage=float(row["max_leverage"]),
            mmr=float(row["mmr"]),
            maintenance_amount=float(row["maintenance_amount"]),
        )
        for row in raw["brackets"]
    )
    return SymbolBrackets(
        max_leverage=float(raw["max_leverage"]),
        brackets=brackets,
        version=str(table.get("version", "")),
        source=str(table.get("source", "")),
    )


def mmr_for_leverage(leverage: float, schedule: SymbolBrackets, mode: str, constant_mmr: float | None) -> tuple[float, float]:
    """Return ``(mmr, maintenance_amount)`` for one assumed leverage.

    ``bracket`` uses the tightest published tier that still allows the
    leverage. ``constant`` uses ``constant_mmr`` or, if that is omitted, the
    lowest-notional tier.
    """
    if not schedule.brackets:
        if constant_mmr is None:
            raise ValueError("no brackets and no constant mmr")
        return constant_mmr, 0.0
    key = mode.strip().lower()
    if key == "constant":
        rate = constant_mmr if constant_mmr is not None else schedule.brackets[0].mmr
        return rate, 0.0
    if key != "bracket":
        raise ValueError("mmr mode must be constant or bracket")
    eligible = [row for row in schedule.brackets if row.max_leverage + 1e-9 >= leverage]
    if not eligible:
        return (constant_mmr if constant_mmr is not None else schedule.brackets[0].mmr), 0.0
    tier = min(eligible, key=lambda row: row.max_leverage)
    return tier.mmr, tier.maintenance_amount
