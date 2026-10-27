"""Streamlit viewer for a heatmap.v1 document.

Sidebar filters re-slice the cube already in memory. They do not call an
exchange. Build fetches once.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from olh.core.text import DISCLAIMER, ESTIMATE_LABEL
from olh.core.windows import WINDOW_PRESETS
from olh.run import run_heatmap
from olh.viz.plotly_plot import plotly_figure

_BG = "#0B0E13"
_TEAL = "#2DD4BF"


def main() -> None:
    st.set_page_config(page_title="Liquidation heatmap", layout="wide")
    st.markdown(
        f"""
        <style>
        .stApp {{ background-color: {_BG}; color: #E6E8EB; }}
        h1, h2, h3, label, p {{ color: #E6E8EB; }}
        a {{ color: {_TEAL}; }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("Liquidation heatmap")
    st.caption(f"{ESTIMATE_LABEL}. {DISCLAIMER}")
    symbols = _symbols()
    with st.sidebar:
        symbol = st.selectbox("Symbol", symbols, index=symbols.index("BTCUSDT") if "BTCUSDT" in symbols else 0)
        window = st.selectbox("Window", list(WINDOW_PRESETS), index=1)
        exchanges = st.multiselect("Exchanges", ["binance", "bybit", "okx"], default=["binance", "bybit", "okx"])
        profile = st.selectbox("Leverage profile", ["lognormal", "power", "uniform", "classic"], index=0)
        ladder = st.selectbox("Ladder", ["full", "standard", "classic"], index=0)
        split = st.selectbox("Long/short split", ["even", "metrics"], index=0)
        fade = st.checkbox("Fade when price trades through a level", value=True)
        decay = st.slider("Intensity kept after a sweep", 0.0, 1.0, 0.0, 0.05)
        age = st.number_input("Age half-life in bars (0 = off)", min_value=0.0, value=0.0, step=1.0)
        bin_mode = st.selectbox("Price bins", ["percent", "absolute", "log", "tick"], index=0)
        bin_size = st.number_input("Bin size", min_value=0.01, value=0.25, step=0.05)
        smooth = st.slider("Vertical smooth", 0.0, 2.0, 0.0, 0.1)
        build = st.button("Build", type="primary")
    if build:
        if not exchanges:
            st.error("Pick at least one exchange.")
        else:
            with st.spinner("Reading public market data…"):
                try:
                    st.session_state["contract"] = run_heatmap(
                        symbol=symbol,
                        window=window,
                        exchanges=exchanges,
                        profile=profile,
                        ladder=ladder,
                        split=split,
                        fade=fade,
                        decay=float(decay),
                        age_half_life_bars=None if age <= 0 else float(age),
                        bin_mode=bin_mode,
                        bin_size=float(bin_size),
                    )
                    st.session_state["build_key"] = (symbol, window, tuple(exchanges), profile, ladder, split)
                except (RuntimeError, ValueError) as exc:
                    st.error(str(exc))
    contract = st.session_state.get("contract")
    if not contract:
        st.info("Choose a window and press Build. BTCUSDT is the default view.")
        return
    meta = contract["metadata"]
    st.write(
        f"**{meta.get('symbol')}** · {meta.get('window')} · {meta.get('interval')} · "
        f"model `{contract.get('model')}` v{contract.get('model_version')}"
    )
    for warning in meta.get("warnings") or []:
        st.warning(warning)
    leverages = [float(item) for item in contract["axes"]["leverage"]]
    with st.sidebar:
        st.divider()
        st.caption("These filters read the cube already built.")
        side_filter = st.multiselect("Side", contract["axes"]["side"], default=list(contract["axes"]["side"]))
        lev_filter = st.multiselect(
            "Leverage",
            leverages,
            default=leverages,
            format_func=lambda value: f"{value:g}x",
        )
        ex_filter = st.multiselect(
            "Exchange breakdown",
            contract["axes"]["exchange"],
            default=list(contract["axes"]["exchange"]),
        )
        y_span = st.slider("Price view ±%", 0, 40, 0)
    figure = plotly_figure(
        contract,
        sides=side_filter or None,
        leverages=lev_filter or None,
        exchanges=ex_filter or None,
        smooth=float(smooth),
        y_span=float(y_span) if y_span else None,
    )
    st.plotly_chart(figure, use_container_width=True)
    st.download_button(
        "Download heatmap.v1.json",
        data=json.dumps(contract, indent=2),
        file_name="heatmap.v1.json",
        mime="application/json",
    )
    st.caption(
        f"Notional placed {meta.get('notional_placed'):,.0f}. "
        f"Outside the grid {meta.get('notional_outside_grid'):,.0f}. "
        f"{len(contract['cells'])} cells."
    )


def _symbols() -> list[str]:
    for candidate in (Path("data/universe_top20.json"), Path(__file__).resolve().parent / "data" / "universe_top20.json"):
        if candidate.is_file():
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            symbols = [str(item) for item in payload.get("symbols") or []]
            if "BTCUSDT" in symbols:
                return symbols
            return ["BTCUSDT", *symbols]
    return ["BTCUSDT"]


if __name__ == "__main__":
    main()
