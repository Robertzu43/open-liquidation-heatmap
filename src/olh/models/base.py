"""Heatmap model interface."""

from __future__ import annotations

from typing import Protocol

import pandas as pd


class HeatmapModel(Protocol):
    name: str
    version: str

    def build(self, books: list[tuple[str, pd.DataFrame, float]], config: dict) -> dict:
        """Return a heatmap.v1 document from per-exchange aligned books."""
