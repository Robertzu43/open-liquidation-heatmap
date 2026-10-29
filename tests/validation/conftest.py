"""Pytest hooks for the validation suite.

Network tests stay skipped unless ``RUN_NETWORK=1``. Set it to check the
public venues reachable from your network; a normal ``pytest`` run does not.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

settings.register_profile(
    "qa",
    derandomize=True,
    deadline=None,
    max_examples=40,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
settings.load_profile("qa")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "network: calls live public Binance, Bybit, or OKX market endpoints; "
        "skipped unless RUN_NETWORK=1",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    del config
    if os.environ.get("RUN_NETWORK") == "1":
        return
    reason = (
        "network test skipped by default; set RUN_NETWORK=1 to call live "
        "public exchange endpoints"
    )
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(params=["oracle", "model"])
def price_of(request: pytest.FixtureRequest) -> Callable[..., float]:
    """Oracle formula, or the production ``liqmap.model.liq_price``."""
    from liqmap import model
    from tests.validation import oracle

    return oracle.liq_price if request.param == "oracle" else model.liq_price
