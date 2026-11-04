"""Fail on a link, import, or key name for the vendor this project is not affiliated with, and on a stray host.

Prose may name the vendor. Python string literals, docstrings excluded, may
only name the hosts in ``ALLOWED_HOSTS``. This file is not scanned, because its
own checks spell out what they look for.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
# The exchange hosts the loaders call, the local server message, and the rejected-symbol fixture in tests/test_model.py.
ALLOWED_HOSTS = {"fapi.binance.com", "data.binance.vision", "www.okx.com", "127.0.0.1", "evil"}
VENDOR = re.compile(
    r"coinglass\.(com|io|net)"
    r"|^\s*(import|from)\s+coinglass\b|import_module\(\s*['\"]coinglass"
    r"|coinglass[-_](api[-_]?key|secret|token)|cg[-_]api[-_]key",
    re.IGNORECASE | re.MULTILINE,
)
URL_HOST = re.compile(r"https?://([^/\s\"'`<>]+)")


def _text_files() -> list[tuple[Path, str]]:
    found = []
    for path in ROOT.rglob("*"):
        parts = path.relative_to(ROOT).parts
        if path == SELF or not path.is_file() or any(p.startswith(".") or p == "__pycache__" for p in parts):
            continue
        try:
            found.append((path, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue
    return found


def vendor_hits(text: str) -> list[str]:
    return [match.group(0) for match in VENDOR.finditer(text)]


def stray_hosts(text: str) -> list[str]:
    hosts = (m.group(1).split("@")[-1].split(":")[0].lower().rstrip(".") for m in URL_HOST.finditer(text))
    return [host for host in hosts if host and "{" not in host and host not in ALLOWED_HOSTS]


def _code_strings(source: str) -> list[str]:
    tree = ast.parse(source)
    scopes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, scopes) and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
    }
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


def test_no_file_links_imports_or_names_a_vendor_key() -> None:
    problems = [f"{path.relative_to(ROOT)}: {hit}" for path, text in _text_files() for hit in vendor_hits(text)]
    assert not problems, "forbidden vendor reference:\n" + "\n".join(problems)


def test_python_strings_name_only_allowed_hosts() -> None:
    problems = [
        f"{path.relative_to(ROOT)}: {host}"
        for path, text in _text_files()
        if path.suffix == ".py"
        for literal in _code_strings(text)
        for host in stray_hosts(literal)
    ]
    assert not problems, "host outside the allowlist:\n" + "\n".join(problems)


def test_detectors_catch_what_they_guard() -> None:
    assert vendor_hits("API = 'https://open-api.coinglass.com/public/v2'")
    assert vendor_hits("import coinglass")
    assert vendor_hits("COINGLASS_API_KEY = 'secret'")
    assert vendor_hits("CoinGlass is out of scope for this project.") == []
    assert stray_hosts("url = 'https://api.gateio.ws/api/v4'") == ["api.gateio.ws"]
    assert stray_hosts("url = 'https://data.binance.vision/data/futures/um/'") == []
