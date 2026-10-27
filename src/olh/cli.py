"""Command line for one heatmap.v1 file plus a PNG and an HTML chart."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from olh.core.text import DISCLAIMER, ESTIMATE_LABEL
from olh.core.windows import WINDOW_PRESETS
from olh.run import run_heatmap

_DEFAULTS = {
    "symbol": "BTCUSDT",
    "window": "24h",
    "interval": None,
    "exchanges": ["binance", "bybit", "okx"],
    "profile": "lognormal",
    "alpha": 1.0,
    "median": 15.0,
    "sigma": 0.55,
    "ladder": "full",
    "leverages": None,
    "mmr": None,
    "mmr_mode": "bracket",
    "fade": True,
    "decay": 0.0,
    "age_half_life_bars": None,
    "bin_mode": "percent",
    "bin_size": 0.25,
    "smooth": 0.0,
    "split": "even",
    "y_span": None,
}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    config = _load_config(args.config)
    if args.list_universe:
        _print_universe()
        return 0
    try:
        window = args.window or args.lookback or config.get("window") or _DEFAULTS["window"]
        interval = args.interval or args.oi_period or config.get("interval") or _DEFAULTS["interval"]
        leverages = _floats(args.leverages) if args.leverages else config.get("leverages")
        exchanges = _names(args.exchanges) if args.exchanges else list(config.get("exchanges") or _DEFAULTS["exchanges"])
        fade = _DEFAULTS["fade"] if args.fade is None else args.fade
        if "fade" in config and args.fade is None:
            fade = bool(config["fade"])
        contract = run_heatmap(
            symbol=args.symbol or config.get("symbol") or _DEFAULTS["symbol"],
            window=window,
            interval=interval,
            exchanges=exchanges,
            profile=args.profile or config.get("profile") or _DEFAULTS["profile"],
            alpha=_pick(args.alpha, config, "alpha"),
            median=_pick(args.median if args.median is not None else args.center, config, "median"),
            sigma=_pick(args.sigma, config, "sigma"),
            ladder=args.ladder or config.get("ladder") or _DEFAULTS["ladder"],
            leverages=leverages,
            mmr=args.mmr if args.mmr is not None else config.get("mmr"),
            mmr_mode=args.mmr_mode or config.get("mmr_mode") or _DEFAULTS["mmr_mode"],
            fade=fade,
            decay=_pick(args.decay, config, "decay"),
            age_half_life_bars=args.age_half_life if args.age_half_life is not None else config.get("age_half_life_bars"),
            bin_mode=args.bin_mode or config.get("bin_mode") or _DEFAULTS["bin_mode"],
            bin_size=_pick(args.bin_size, config, "bin_size"),
            split=args.split or config.get("split") or _DEFAULTS["split"],
            assumed_qty=args.assumed_qty if args.assumed_qty is not None else config.get("assumed_qty"),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    meta = contract["metadata"]
    print(ESTIMATE_LABEL)
    print(DISCLAIMER)
    print(
        f"{meta.get('symbol')} {meta.get('window')} {meta.get('interval')} "
        f"exchanges={','.join(meta.get('exchanges') or [])} "
        f"notional_placed={meta.get('notional_placed'):.0f} "
        f"cells={len(contract['cells'])}"
    )
    for warning in meta.get("warnings") or []:
        print(f"warning: {warning}", file=sys.stderr)
    png = args.outfile or "heatmap.png"
    html = args.html if args.html is not None else "heatmap.html"
    blob = args.json if args.json is not None else "heatmap.v1.json"
    view = {
        "smooth": args.smooth if args.smooth is not None else float(config.get("smooth") or 0.0),
        "y_span": args.y_span if args.y_span is not None else config.get("y_span"),
    }
    if png:
        from olh.viz.matplotlib_plot import save_png

        save_png(contract, png, **view)
        print(f"wrote {png}")
    if html:
        from olh.viz.plotly_plot import save_html

        save_html(contract, html, **view)
        print(f"wrote {html}")
    if blob:
        Path(blob).write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {blob}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate a liquidation heatmap from public perpetual-futures data."
    )
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--window", default=None, help="12h, 24h, 3d, 7d, 30d, 90d, or a duration like 36h")
    parser.add_argument("--lookback", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--interval", default=None)
    parser.add_argument("--oi-period", dest="oi_period", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--exchanges", default=None, help="comma-separated: binance,bybit,okx")
    parser.add_argument("--profile", default=None, choices=["lognormal", "power", "uniform", "classic"])
    parser.add_argument("--alpha", type=float, default=None)
    parser.add_argument("--median", type=float, default=None)
    parser.add_argument("--center", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--sigma", type=float, default=None)
    parser.add_argument("--ladder", default=None, choices=["full", "standard", "classic"])
    parser.add_argument("--leverages", default=None, help="comma-separated rungs to keep, without renormalizing")
    parser.add_argument("--mmr", type=float, default=None)
    parser.add_argument(
        "--mmr-mode",
        dest="mmr_mode",
        default=None,
        choices=["constant", "bracket"],
        help="bracket uses the lowest-notional tier unless --assumed-qty selects a higher one",
    )
    parser.add_argument("--fade", dest="fade", action="store_true")
    parser.add_argument("--no-fade", dest="fade", action="store_false")
    parser.add_argument("--decay", type=float, default=None)
    parser.add_argument("--age-half-life", dest="age_half_life", type=float, default=None)
    parser.add_argument("--bin-mode", dest="bin_mode", default=None, choices=["absolute", "percent", "log", "tick"])
    parser.add_argument("--bin-size", dest="bin_size", type=float, default=None)
    parser.add_argument("--smooth", type=float, default=None)
    parser.add_argument("--y-span", dest="y_span", type=float, default=None, help="view-only percent band around the last price")
    parser.add_argument("--split", default=None, choices=["even", "metrics"])
    parser.add_argument(
        "--assumed-qty",
        dest="assumed_qty",
        type=float,
        default=None,
        help="base-asset size; quantity times entry selects the maintenance tier",
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--outfile", default="heatmap.png")
    parser.add_argument("--html", default="heatmap.html")
    parser.add_argument("--json", default="heatmap.v1.json")
    parser.add_argument("--list-universe", dest="list_universe", action="store_true")
    parser.set_defaults(fade=None)
    return parser


def _load_config(path: str | None) -> dict:
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.append(Path("config/default.json"))
    candidates.append(Path(__file__).resolve().parents[2] / "config" / "default.json")
    for candidate in candidates:
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return {}


def _print_universe() -> None:
    path = _universe_path()
    if path is None:
        print("no universe snapshot at data/universe_top20.json")
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(payload.get("method", ""))
    print(f"asof {payload.get('asof')}")
    for symbol in payload.get("symbols") or []:
        print(symbol)
    presets = ", ".join(f"{name}={item.interval}" for name, item in WINDOW_PRESETS.items())
    print(f"windows {presets}")


def _universe_path() -> Path | None:
    for candidate in (
        Path("data/universe_top20.json"),
        Path(__file__).resolve().parents[2] / "data" / "universe_top20.json",
    ):
        if candidate.is_file():
            return candidate
    return None


def _names(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _floats(text: str) -> list[float]:
    return [float(part) for part in text.split(",") if part.strip()]


def _pick(value, config: dict, key: str):
    if value is not None:
        return value
    if key in config and config[key] is not None:
        return config[key]
    return _DEFAULTS[key]
