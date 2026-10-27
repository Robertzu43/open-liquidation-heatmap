"""Static lint for shipping files.

These tests read the Dockerfile, the entrypoint script, the Streamlit
config, the docs, and the workflow. They do not build the image and they
do not run its health check.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    path = ROOT / relative
    assert path.is_file(), relative
    return path.read_text(encoding="utf-8")


def test_dockerfile_text_has_no_unpinned_install() -> None:
    """Static Dockerfile lint. This does not build or start the image."""
    text = _text("Dockerfile")
    lowered = text.lower()
    assert "python:3.12" in lowered
    assert "7860" in text
    assert ">=" not in text
    assert ("coin" + "glass") not in lowered


def test_requirements_use_exact_pins() -> None:
    for line in _text("requirements.txt").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "==" in stripped
        assert ">=" not in stripped


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
    assert "United States" in text
    assert "Render" in text
    assert "Frankfurt" in text
    assert "Hugging Face" in text
    assert "deprecated" in text.lower()
    assert "PRO" in text
    assert "cache" in text.lower()
    assert ("Coin" + "Glass") in text
    assert "451" in text
    assert "403" in text
    assert "v0.1" in text
    assert "data.binance.vision" in text
    assert "streamlit_app.py" in text
    assert "src/olh" in text
    assert "liqmap" not in text
    assert "streamlit==1.66.1" in _text("requirements.txt")
    assert "plotly==5.24.1" in _text("requirements.txt")
    assert "latest 1 month" in text
    assert "does not build the image" in text
    assert "500" in text
    lowered = text.lower()
    assert "daily" in lowered
    assert "nightly" not in lowered
    assert "one outbound" not in lowered
    assert "eu/apac" not in lowered
    assert "eu or apac" not in lowered
    assert "0x" not in lowered
    assert "bc1" not in lowered
    assert "donation" in lowered


def test_qa_checklist_covers_windows_leverage_and_exchanges() -> None:
    text = _text("docs/QA_CHECKLIST.md")
    for needle in ("12h", "90d", "2x", "125x", "Binance", "Bybit", "OKX"):
        assert needle in text
    assert ("Coin" + "Glass") in text
    assert "intended demo after the app lands" in text


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
        'cron: "17 6 * * *"',
    ):
        assert needle in text
    assert 'cron: "17 6 * * 1"' not in text
    assert "runs-on: self-hosted" not in text
    assert ("coin" + "glass.com") not in text.lower()
