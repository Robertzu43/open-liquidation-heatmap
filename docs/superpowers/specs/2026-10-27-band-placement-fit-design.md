# Band placement fit

Approved 2026-10-27. Revised the same day after an independent review.

## Goal

Our Models 1, 2 and 3 should put their strongest shelves at about the same percent distance from price as the vendor heatmap's three models. Our model numbers do not mean the same thing as theirs; the goal is only that ours land near theirs. Placement only. Lifetime, fading and color are out of scope.

This deliberately replaces the rule at `docs/HOW_IT_WORKS.md:280`, which said third-party-calibrated weights must not become defaults. Roberto approved that change on 2026-10-27.

## Method

Black box. We look at the public page the way a visitor does. We do not call CoinGlass APIs, read their scripts or network traffic, or copy code or data into this repository.

1. **Views.** BTC on OKX (BTC-USDT-SWAP), 24h and 7d, three models each: six fit views. ETH on OKX, same six views, taken at the same session, is the holdout. First confirm the vendor page can be pinned to OKX BTC-USDT-SWAP and to the same windows. If it only shows an all-exchange view, compare against our `mode=symbol` and record that gap.
2. **Capture.** One screenshot of the public page per view, from a local browser. Within about a minute of each, fetch `GET /api/heatmap?exchange=okx&symbol=<coin>&range=<r>&model=<m>`. Save the `MarketSeries` to the session scratchpad so tuning runs offline through `build_heatmap`. The server cache is in memory and expires in seconds. Images, JSON and series stay outside the repository.
3. **Measure.** On both sides, take local maxima of the latest-column profile: theirs from the screenshot, ours from `profile_long` and `profile_short`. Do not use `analysis.levels`. That function groups bins into runs and returns each run's USD-weighted center, so it does not measure the same thing as a peak on the screenshot. Keep the top 3 peaks per side and pair each one with its nearest counterpart, not by rank. To read the screenshot, map pixels to price from two y-axis labels, and sample a few pixels left of the live edge.
4. **Tune.** Change only `MODELS[*]["weights"]`: which leverage tiers exist (whole leverages) and their weights (whole percents, summing to 100). Hold MMR, `decay`, `churn` and `kernel` fixed.
5. **Pass rule.** Score BTC (fit) and ETH (holdout).
   - For shelves more than 1% from price, the median absolute error is at most 0.5 percentage points.
   - For shelves within 1% of price, each matched pair maps to the same leverage tier.
   - The new weights must beat the old ones on ETH. If they don't, report the remaining error and keep the old weights.

## Changes

- `liqmap/model.py`: new `weights` for Models 1–3, plus `blurb` if the old wording stops being true.
- `README.md`: model table, both the weights and the "Idea" column.
- `docs/HOW_IT_WORKS.md:280`: say the defaults are calibrated by eye against a public third-party chart, with nothing copied. Fix the stale `heatmap.py` reference in that paragraph.

`tests/test_model.py:59` already checks the weight sums. No new test is needed.

## Checks

- `python -m pytest -q` passes, including `test_supply_chain.py`. Baseline is 176 passed, 1 skipped.
- A comparison table is reported in the session, not committed: theirs, ours before and ours after, for BTC and ETH.

## Delivery

Local branch `feat/band-placement-fit`, feature commits. No push, no PR.

## Steps

1. Pin the vendor view, and capture the 12 screenshots together with our JSON and series.
2. Read the vendor peaks and compute our peaks before the change.
3. Fit the weights offline on BTC, then score on ETH.
4. If the pass rule holds, edit the model, README and docs, run the tests and commit.
