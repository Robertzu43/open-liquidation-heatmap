"""Heatmap model registry. v1 is oi_leverage_v1. Later models stay out of this package until their own change."""

from olh.models.oi_leverage_v1 import OiLeverageV1

REGISTRY = {
    OiLeverageV1.name: OiLeverageV1,
}


def get_model(name: str = "oi_leverage_v1"):
    try:
        return REGISTRY[name]()
    except KeyError as exc:
        known = ", ".join(sorted(REGISTRY))
        raise KeyError(f"unknown model {name!r}; registered: {known}") from exc


__all__ = ["REGISTRY", "get_model"]
