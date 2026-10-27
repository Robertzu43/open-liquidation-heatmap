"""Fail the build on a CoinGlass URL, import, or key, and on a stray host.

Documentation may name CoinGlass when saying the project does not use it.
A URL, an import, or a key name is a failure. Python code strings, excluding
docstrings, may only use ``ALLOWED_EXCHANGE_HOSTS``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.validation.net_allowlist import ALLOWED_EXCHANGE_HOSTS

ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".txt",
    ".sh",
    ".cfg",
    ".ini",
}
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
URL_RE = re.compile(r"https?://([^/\s\"'`<>]+)")


def _brand() -> str:
    return "coin" + "glass"


def forbidden_hits(text: str) -> list[str]:
    """Return reasons ``text`` contains a forbidden URL, import, or key."""
    name = _brand()
    lowered = text.lower()
    hits: list[str] = []
    for suffix in (".com", ".io", ".net"):
        token = name + suffix
        if token in lowered:
            hits.append(f"url:{token}")
    import_re = rf"(?m)^\s*(?:import|from)\s+{name}\b"
    if re.search(import_re, text, re.IGNORECASE):
        hits.append("import")
    if re.search(rf"import_module\(\s*['\"]{name}\b", text):
        hits.append("import")
    for fragment in _key_fragments(name):
        if fragment in lowered:
            hits.append("key")
            break
    return hits


def _key_fragments(name: str) -> tuple[str, ...]:
    header = "cg" + "-api-key"
    env = "cg" + "_api_key"
    return (
        name + "_api_key",
        name + "-api-key",
        name + "_apikey",
        name + "_secret",
        name + "_token",
        header,
        env,
    )


def unlisted_python_hosts(text: str) -> list[str]:
    """Hosts in URL literals that are outside the exchange allowlist.

    Format placeholders (``https://{host}``) are skipped. Real literals are not.
    """
    allowed = {host.lower() for host in ALLOWED_EXCHANGE_HOSTS}
    found: list[str] = []
    for match in URL_RE.finditer(text):
        host = match.group(1).split("@")[-1].split(":")[0].lower().rstrip(".")
        if "{" in host or "}" in host or not host:
            continue
        if host not in allowed:
            found.append(host)
    return found


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name == "Dockerfile":
            files.append(path)
    return files


def test_repository_has_no_forbidden_url_key_or_import() -> None:
    problems: list[str] = []
    for path in _iter_files():
        text = path.read_text(encoding="utf-8")
        for hit in forbidden_hits(text):
            problems.append(f"{path.relative_to(ROOT)}: {hit}")
    assert not problems, "forbidden CoinGlass reference:\n" + "\n".join(problems)


def _code_strings(path: Path) -> list[str]:
    """String literals that are not module, class, or function docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            found.append(node.value)
    return found


def test_python_urls_use_the_exchange_allowlist() -> None:
    problems: list[str] = []
    for path in _iter_files():
        if path.suffix != ".py":
            continue
        for literal in _code_strings(path):
            for host in unlisted_python_hosts(literal):
                problems.append(f"{path.relative_to(ROOT)}: {host}")
    assert not problems, "host outside the exchange allowlist:\n" + "\n".join(problems)


def test_detector_flags_a_url_an_import_and_a_key() -> None:
    name = _brand()
    scheme = "https" + "://"
    url_text = f"API = '{scheme}open-api.{name}.com/public/v2'\n"
    assert "url:" in " ".join(forbidden_hits(url_text))

    import_text = "import " + name + "\n"
    assert "import" in forbidden_hits(import_text)

    key_text = name.upper() + "_API_KEY = 'secret'\n"
    assert "key" in forbidden_hits(key_text)


def test_detector_ignores_a_prose_mention() -> None:
    prose = _brand().title() + " is out of scope for this project.\n"
    assert forbidden_hits(prose) == []


def test_allowlist_rejects_a_stray_host() -> None:
    scheme = "https" + "://"
    text = f"url = '{scheme}example.invalid/v1'\n"
    assert unlisted_python_hosts(text) == ["example.invalid"]
    listed = f"url = '{scheme}{ALLOWED_EXCHANGE_HOSTS[0]}/fapi/v1/ping'\n"
    assert unlisted_python_hosts(listed) == []


def test_allowlist_is_the_three_public_venues() -> None:
    assert ALLOWED_EXCHANGE_HOSTS == (
        "fapi.binance.com",
        "api.bybit.com",
        "www.okx.com",
    )
