# Migrating from 0.2.0

Application version is **0.3.0**, API schema is **1.0**, and the corrected model is **2.0.0**. These versions describe different contracts.

## Model outputs change

Native contract-quantity changes replace price-sensitive dollar-OI changes on supported adapter paths. Close-created cohorts cannot be swept by earlier wicks. Exact cohorts drive liquidation decisions independently of viewport bins, with consistent post-update profiles. Venue gaps and invalid OI restart baselines without artificial recovery flow. Time decay is elapsed-time based.

Do not reuse thresholds calibrated on 0.2.0—or CoinGlass—without re-running out-of-sample evaluation. Similar pictures do not imply interchangeable datasets.

## API clients

Existing `/api/heatmap`, `/api/levels`, `/api/status`, `/api/symbols` and replay routes remain available. New clients should use the documented `/api/v1/` routes and inspect `schema_version`, `model_version`, `quality`, `data_fingerprint`, `time_semantics` and `feature_as_of`.

`times` and `as_of` identify source bar opens. Economic features refer to closed-bar state. A new `generated_at` timestamp does not make a stale source fresh. Unknown/insufficient fields are not trading zeros. Use exact `features` rather than treating binned analysis shelves as resolution-independent quantities.

See [API.md](API.md) for actual fields, limits, routes and error semantics. The `liqmap.research` tools consume v1 envelopes; normalized reference JSON is not a raw CoinGlass API response.

## Python callers

`MarketSeries` now accepts native OI, observed masks and provenance. The old quote-only constructor remains a labeled legacy path; it is not equivalent to native accounting. `build_heatmap` retains existing chart fields and adds metadata and exact economic features. OI records align to their containing bar rather than to the nearest open.

## Installation and hosting

Use `python -m pip install '.[dev]'` from a clone, or install a built wheel. `liqmap` serves the app, and `liqmap-research --help` lists research commands. The server defaults to loopback. Deliberately exposed deployments need the controls in [OPERATIONS.md](OPERATIONS.md).

Existing localStorage preferences may be restored, while URL settings make a shared view explicit. UTC is the default display timezone; local time can be selected.

## Archive cache location

Archives now use `LIQMAP_CACHE_DIR` when set, otherwise `$XDG_CACHE_HOME/open-liq` or `~/.cache/open-liq`. Existing repository `.cache` files are not automatically moved. To reuse them, point `LIQMAP_CACHE_DIR` at the old cache directory or copy its contents into the new directory. Apply an operator retention policy; per-file size bounds are not a total disk quota.
