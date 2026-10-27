"""Guards for the shipping files added alongside the validation suite."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    path = ROOT / relative
    assert path.is_file(), relative
    return path.read_text(encoding="utf-8")


def test_dockerfile_runs_streamlit_on_the_space_port() -> None:
    text = _text("Dockerfile").lower()
    assert "python:3.12" in text
    assert "streamlit" in text
    assert "plotly" in text
    assert "7860" in text
    assert "coinglass" not in text


def test_entrypoint_shell_syntax() -> None:
    script = ROOT / "docker" / "streamlit-entrypoint.sh"
    subprocess.run(["sh", "-n", str(script)], check=True)


def test_streamlit_config_disables_usage_stats() -> None:
    text = _text(".streamlit/config.toml")
    assert "gatherUsageStats = false" in text
    assert "headless = true" in text


def test_deploy_doc_covers_hosts_cache_and_has_no_wallet() -> None:
    text = _text("docs/DEPLOY.md")
    assert "Streamlit Community Cloud" in text
    assert "Hugging Face" in text
    assert "cache" in text.lower()
    assert "CoinGlass" in text
    assert "451" in text
    assert "region" in text.lower()
    lowered = text.lower()
    assert "0x" not in lowered
    assert "bc1" not in lowered
    assert "donation" in lowered


def test_qa_checklist_covers_windows_leverage_and_exchanges() -> None:
    text = _text("docs/QA_CHECKLIST.md")
    for needle in ("12h", "90d", "2x", "125x", "Binance", "Bybit", "OKX"):
        assert needle in text
    assert "CoinGlass" in text


def test_ci_runs_ruff_mypy_and_the_python_matrix() -> None:
    text = _text(".github/workflows/ci.yml")
    for needle in (
        "3.11",
        "3.12",
        "3.13",
        "ruff check",
        "mypy",
        "RUN_NETWORK",
        "--disable-socket",
        "--cov",
    ):
        assert needle in text
    assert ("coin" + "glass.com") not in text.lower()
