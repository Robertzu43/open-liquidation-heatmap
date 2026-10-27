# Ten out of ten

Drafted 2026-10-27. Not yet approved. Open decisions are listed at the end.

## Why

An independent review on 2026-10-27 scored the repository like this:

| Aspect | Score | What held it back |
| --- | --- | --- |
| Engineering | 7 | Model bugs, no HTTP handler tests, no CI, server listens on every interface by default |
| Usability | 8 | README test command runs a fraction of the suite; no one-step "open the page" |
| Relatability | 7 | Ratios like "553.84×" and an uneven color bar confuse traders |
| Overall UX | 7 | Three rows of controls always visible, replay row shown where it cannot work, stale title after an error |
| UI | 8 | Leverage legend overlaps the side bars, 5m heat smears into blobs |
| Reverse-engineering mechanics | 4 | Same-bar sweep, USD-value OI differencing, entry one bar late, bar-size-dependent fade, docs describe an older script |

The goal is a free, open tool a trader can trust instead of a paid, closed one. A closed vendor heatmap gates coins, ranges and refresh behind a subscription and does not publish its method. This project publishes every assumption in code and can show any coin, any range, and Binance history back to 2020. That promise only holds if the mechanics are sound and the page explains itself.

## Goals

1. Every aspect scores at least 9, and the mechanics score at least 8, from a fresh reviewer given the same rubric.
2. The inference engine does what `docs/HOW_IT_WORKS.md` says the target model does, for the four rules below.
3. A first-time visitor understands what a bright band means, and where to look, without reading the docs.
4. The README sells the value in one screen: free, open, no account, every coin and range, method you can read.

## Non-goals

- Mark-price sweeping, per-notional margin brackets, cross margin. They need data the public archive does not carry for every bar, and the review did not cite them.
- A hosted public instance, accounts, or a database.
- A frontend framework or build step. The page stays plain HTML, CSS and JS.
- New exchanges.

## Anti-goals

- Claiming the map predicts price, or that it is more accurate than any vendor. Both are estimates.
- Any vendor URL, API call, scraped data, logo or look-alike branding. `tests/validation/test_supply_chain.py` fails on the vendor URL in any `.md` or `.py` file, so the README names the vendor in text only.
- More controls. Every new control must remove or hide one.

## Mechanics: the four rules

1. **Entry timing.** An open-interest print at bar open *i* is a snapshot. The change from print *i−1* to print *i* happened during bar *i−1*. Those positions enter at bar *i−1*'s typical price, `(high + low + close) / 3`, the Blocklens choice already cited in the docs.
2. **A level survives the bar that opened it.** With rule 1, the deposit at loop step *i* comes from bar *i−1*, and bar *i*'s range is the first one allowed to sweep it. The sweep stays after the column snapshot.
3. **Contracts, not dollars.** New positions are the change in open interest in coin units, times the entry price. A price move with flat contracts adds nothing. Sources: OKX `oiCcy` (row index 2), Binance live `sumOpenInterest`, Binance archive `sum_open_interest`. Demo synthesizes coin units. When a venue gives no coin column, fall back to `oi_usd / close` and add a warning.
4. **Fade by time, not by bar.** Model 2's fade becomes a half-life of 72 hours, which is today's 0.9992 per 5-minute bar. Per-bar factor is `0.5 ** (bar_seconds / (72 * 3600))`.

Also: a sweep splits the boundary bin by the fraction of the bin the wick crossed, instead of zeroing it, as the docs' "How a level dies" says.

The Model 1 and 2 weights were fitted by eye under the old rules. After the mechanics change, re-run the check in `docs/superpowers/specs/2026-10-27-band-placement-fit-design.md` and keep the weights only if they still pass.

## Engineering

- Server binds `127.0.0.1` by default. `--host 0.0.0.0` stays available. A new `--open` flag opens the browser with the standard library.
- HTTP handler tests on an ephemeral port: status, heatmap, levels, 400, 404, 409, path traversal, gzip.
- One GitHub Actions workflow runs `pytest` on Python 3.11, 3.12 and 3.13. Network tests stay skipped.
- README's test command becomes `python -m pytest`.

## UI and UX

Today, three rows of controls sit above the chart. The redesign:

- **One toolbar:** coin, exchange, range, model segmented control, and a single "More" button. Pair/Symbol lives in the exchange select as "All venues".
- **"More" drawer:** bar, as-of, replay, threshold, auto-refresh, liquidation map, candles, themes, snapshot. The replay row appears only when the view is a Binance pair.
- **First-visit card:** three short lines over the chart, dismissible and remembered: bright means many estimated liquidations; above price are shorts, below are longs; it is an estimate from public data.
- **Readable numbers:** imbalance shows "Short 64% / Long 36%" instead of a raw ratio. Ratios above 10 show "> 10×".
- **Color bar:** ticks placed where the threshold curve actually maps them, so the labels are honest.
- **Crisp shelves:** heat is drawn with smoothing off, one canvas pixel per bin at least.
- **Fixes:** title updates only on a successful load; leverage labels sit beside the side bars, not on them.

The look stays dark, mono numbers, Ember default. Polish goes into spacing, one type scale, and fewer borders.

## README

Order: one-line pitch, hero screenshot, "Why this exists" with a dated comparison table, three-line quick start, "Reading the chart" with an annotated screenshot, features, how it works in five lines with a link, FAQ, limitations, license.

The comparison table states only what the vendor's own Prime page said on 2026-10-27: Prime costs $28 a month or $268 a year; without Prime, the heatmap supports BTC and ETH only; auto-refresh needs Prime. The other column states what this repo does, each claim checkable in code.

Screenshots live in `docs/img/`. `.gitignore` ignores `*.png`, so it gets an exception for that folder.

## Left to the builder

Exact spacing, colors within the existing themes, copy of the first-visit card within the three ideas above, test fixture values.

## Open decisions

1. Comparison table: keep it naming the vendor and its prices, or describe "paid heatmap services" generically.
2. Recalibration: the fit compares against screenshots of the vendor's public page taken in a local browser, as in the band-placement spec. Confirm the same person will capture them again, or skip recalibration and say so in the README.
3. Execution: subagent-driven or native.
