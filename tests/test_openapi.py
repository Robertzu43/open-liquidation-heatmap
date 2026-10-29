"""docs/openapi.json must describe what the server actually returns (offline, demo data)."""
import json
import re
from pathlib import Path

import pytest

from liqmap import market, service

SPEC = json.loads((Path(__file__).resolve().parents[1] / "docs" / "openapi.json").read_text())
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def problems(value, schema, path="$"):
    """The subset of JSON Schema the spec uses: $ref, type, properties, required, items, enum, const, pattern, maxItems."""
    if "$ref" in schema:
        return problems(value, SPEC["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]], path)
    found = []
    types = schema.get("type")
    if types is not None:
        allowed = [types] if isinstance(types, str) else types
        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if not any(isinstance(value, TYPES[t]) if t in TYPES else is_number and (t == "number" or isinstance(value, int)) for t in allowed):
            return [f"{path}: {value!r} is not {allowed}"]
    if "const" in schema and value != schema["const"]:
        found.append(f"{path}: {value!r} != {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{path}: {value!r} not in {schema['enum']}")
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        found.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, dict):
        found += [f"{path}.{key}: missing" for key in schema.get("required", []) if key not in value]
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                found += problems(value[key], sub, f"{path}.{key}")
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", len(value)):
            found.append(f"{path}: more than {schema['maxItems']} items")
        found += [p for i, item in enumerate(value) for p in problems(item, schema.get("items", {}), f"{path}[{i}]")]
    return found


@pytest.mark.parametrize("name,payload", [
    ("Status", lambda: service.status_payload()),
    ("ResearchEnvelope", lambda: service.features_payload("demo", "BTC", "24h", 1)),
    ("ResearchEnvelope", lambda: service.load_heatmap("demo", "ETH", "12h", 3, visible_bars=40)),
    ("ContextResponse", lambda: market.context_payload("demo", "BTC")),
    ("LiquidationsResponse", lambda: market.liquidations_payload("demo", "BTC", "pair", "24h")),
    ("LiquidationsResponse", lambda: market.liquidations_payload("bybit", "BTC", "pair", "24h")),
    ("WatchlistResponse", lambda: market.watchlist_payload("demo", "24h", "1", "pair", "BTC,ETH,BAD!")),
])
def test_responses_match_the_published_schema(name, payload):
    body = json.loads(json.dumps(payload(), allow_nan=False))
    assert problems(body, {"$ref": f"#/components/schemas/{name}"}) == []


def test_checker_reports_real_mismatches():
    bad = {"schema_version": "2.0", "venues": "x"}
    found = problems(bad, {"$ref": "#/components/schemas/ContextResponse"})
    assert any("2.0" in line for line in found) and any("missing" in line for line in found) and any("venues" in line for line in found)
