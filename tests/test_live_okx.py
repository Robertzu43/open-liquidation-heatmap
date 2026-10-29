"""Opt-in live checks against OKX's public API: RUN_NETWORK=1 python -m pytest -q -m network."""
import os
import time

import pytest

from liqmap import market, service

pytestmark = [pytest.mark.network, pytest.mark.skipif(os.environ.get("RUN_NETWORK") != "1", reason="network test; set RUN_NETWORK=1")]


def test_okx_live_map_is_observed_fresh_and_has_market_context():
    payload = service.features_payload("okx", "BTC", "24h", 1)
    assert payload["quality"]["status"] == "observed" and not payload["stale"]
    assert payload["features"]["resting_long_usd"] > 0 and payload["market_context"]["oi_usd"] > 0


def test_okx_live_context_reports_funding_and_open_interest():
    venue = market.context_payload("okx", "BTC")["venues"][0]
    assert venue["ok"] and abs(venue["funding_rate"]) < 0.01
    assert venue["next_funding_time"] > time.time() * 1000 and venue["open_interest_usd"] > 0


def test_okx_live_liquidations_are_ordered_real_orders():
    payload = market.liquidations_payload("okx", "BTC", "pair", "12h")
    events = payload["events"]
    assert payload["available"] and events and [e["ts"] for e in events] == sorted(e["ts"] for e in events)
    assert all(e["price"] > 0 and e["usd"] > 0 and e["side"] in {"long", "short"} for e in events)


def test_okx_live_watchlist_brackets_price_with_clusters():
    for item in market.watchlist_payload("okx", "24h", "1", "pair", "BTC,ETH")["items"]:
        assert item["ok"], item
        if item["above"] and item["below"]:
            assert item["below"]["price"] < item["price"] < item["above"]["price"]
