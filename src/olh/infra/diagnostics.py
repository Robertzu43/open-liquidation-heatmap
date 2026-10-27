"""Timestamp diagnostics for archive open-interest prints.

Adapted from the author's earlier research code. The check says whether
``create_time`` behaves like a period start, a regular mid-bar snapshot, or
an irregular series. Neighbor deltas are trusted only for the regular cases,
and the caller still has to drop steps that cross a gap.
"""

from __future__ import annotations

import pandas as pd


def timestamp_check(timestamps, expected_step: pd.Timedelta) -> dict:
    """Classify a create_time series before open-interest deltas are trusted."""
    stamps = pd.to_datetime(pd.Series(list(timestamps)), utc=True).dropna().sort_values()
    n = int(len(stamps))
    if n < 3:
        return {
            "n": n,
            "modal_step_seconds": None,
            "on_boundary_fraction": 0.0,
            "kind": "irregular",
            "trust_deltas": False,
            "max_gap_seconds": None,
        }
    steps = stamps.diff().dropna().dt.total_seconds()
    modal = float(steps.mode().iloc[0])
    expected = float(expected_step.total_seconds())
    step_ok = abs(modal - expected) <= expected * 0.02
    # Period starts sit on the step boundary measured from the Unix epoch.
    offsets = stamps.astype("int64") // 1_000_000  # nanoseconds to milliseconds
    boundary = (offsets % int(expected * 1000)) == 0
    on_boundary = float(boundary.mean())
    max_gap = float(steps.max()) if len(steps) else None
    if step_ok and on_boundary >= 0.9:
        kind = "period_start"
        trust = True
    elif step_ok and on_boundary < 0.5:
        kind = "snapshot"
        trust = True
    else:
        kind = "irregular"
        trust = False
    return {
        "n": n,
        "modal_step_seconds": modal,
        "on_boundary_fraction": on_boundary,
        "kind": kind,
        "trust_deltas": trust,
        "max_gap_seconds": max_gap,
    }
