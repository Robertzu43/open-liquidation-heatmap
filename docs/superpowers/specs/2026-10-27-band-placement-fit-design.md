# Band placement fit

Approved 2026-10-27.

## Goal

Models 1, 2 and 3 put their strongest shelves at about the same percent distance from price as CoinGlass's public heatmap, Models 1, 2 and 3. Placement only. Lifetime, fading and color are out of scope.

## Method

Black box. We look at the public page the way a visitor does. We do not call CoinGlass APIs, read their scripts or network traffic, or copy code or data into this repository.

1. **Views.** OKX BTC-USDT-SWAP, 24h and 7d, Models 1–3: six views.
2. **Capture.** One screenshot of the public heatmap page per view, from a local browser. Within about a minute, `GET /api/heatmap?exchange=okx&symbol=BTCUSDT&range=<r>&model=<m>` from the local server. Images and JSON stay outside the repository.
3. **Measure.** For each view, take the latest column. Find the top 3 shelves above price (short) and below price (long) as percent distance from price. Read them from the screenshot by eye. If reading by eye is too coarse, extract the column's pixel profile with a scratch script.
4. **Tune.** Change `MODELS[*]["weights"]` in `liqmap/model.py`, and `MMR` only if the gaps call for it. Re-run on cached series. Target: each top-3 shelf within 0.5 percentage points of the reference, or the closest explainable mix.

## Changes

- `liqmap/model.py`: new weights for Models 1–3 (and `MMR` if needed).
- `README.md`: model table.
- `docs/HOW_IT_WORKS.md`: line 280 says the defaults are now tuned so band distances resemble a widely used public chart. Nothing is copied from it. The weights themselves remain our own assumption.
- `tests/test_model.py`: each model's weights sum to 1 (extend the existing check if it already covers this).

## Checks

- `pip install -r requirements-dev.txt`, then `python -m pytest -q` passes, including `test_supply_chain.py`.
- Comparison table (theirs, ours before, ours after) reported in the session, not committed.

## Delivery

Local branch `feat/band-placement-fit`, feature commits. No push, no PR.
