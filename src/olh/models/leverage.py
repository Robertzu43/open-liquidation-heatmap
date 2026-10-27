"""Leverage ladders. The default prior is log-normal and includes 25x."""

from __future__ import annotations

import math

# Median near the middle of the 10x–20x cluster. Sigma keeps most of the
# mass between about 8x and 30x while still giving 2x and 125x a tail.
DEFAULT_MEDIAN = 15.0
DEFAULT_SIGMA = 0.55

STANDARD_LADDER: tuple[float, ...] = (
    2, 3, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 125,
)

CLASSIC_WEIGHTS: dict[float, float] = {
    5: 0.08,
    10: 0.18,
    15: 0.14,
    20: 0.14,
    25: 0.12,
    50: 0.14,
    75: 0.08,
    100: 0.08,
    125: 0.04,
}


def resolve_ladder(kind: str, max_leverage: float) -> list[float]:
    cap = float(max_leverage)
    if cap <= 1:
        raise ValueError("max leverage must be greater than 1")
    key = kind.strip().lower()
    if key == "full":
        top = int(math.floor(cap + 1e-9))
        rungs = [float(lev) for lev in range(2, top + 1)]
    elif key == "standard":
        rungs = [lev for lev in STANDARD_LADDER if lev <= cap + 1e-9]
    elif key == "classic":
        rungs = [lev for lev in CLASSIC_WEIGHTS if lev <= cap + 1e-9]
    else:
        raise ValueError("ladder must be full, standard, or classic")
    if not rungs:
        raise ValueError(f"no rungs at or below max leverage {cap}")
    return rungs


def build_leverage_weights(
    ladder: list[float],
    profile: str = "lognormal",
    *,
    alpha: float = 1.0,
    median: float = DEFAULT_MEDIAN,
    sigma: float = DEFAULT_SIGMA,
) -> dict[float, float]:
    """Weights over ``ladder`` that sum to 1.

    ``lognormal`` is the default prior: a log-normal density on leverage,
    median ``median`` (15, inside 10x–20x), width ``sigma``.
    """
    if not ladder:
        raise ValueError("ladder is empty")
    key = profile.strip().lower()
    raw: dict[float, float] = {}
    if key == "lognormal":
        if median <= 1 or sigma <= 0:
            raise ValueError("lognormal median must be > 1 and sigma > 0")
        mu = math.log(median)
        for lev in ladder:
            ln = math.log(float(lev))
            raw[float(lev)] = math.exp(-0.5 * ((ln - mu) / sigma) ** 2) / float(lev)
    elif key == "power":
        if alpha < 0:
            raise ValueError("alpha must be >= 0")
        for lev in ladder:
            raw[float(lev)] = float(lev) ** (-alpha)
    elif key == "uniform":
        for lev in ladder:
            raw[float(lev)] = 1.0
    elif key == "classic":
        for lev in ladder:
            raw[float(lev)] = CLASSIC_WEIGHTS.get(float(lev), 0.0)
        if sum(raw.values()) <= 0:
            raise ValueError("classic profile does not overlap this ladder")
    else:
        raise ValueError("profile must be lognormal, power, uniform, or classic")
    total = sum(raw.values())
    return {lev: weight / total for lev, weight in raw.items()}


def filter_weights(weights: dict[float, float], leverages: list[float] | None) -> dict[float, float]:
    """Hide rungs without giving their weight to the ones that remain."""
    if not leverages:
        return dict(weights)
    wanted = {float(lev) for lev in leverages}
    return {lev: weight for lev, weight in weights.items() if lev in wanted}
