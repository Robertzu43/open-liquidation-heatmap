# Reuse from the strategy repositories

Audit date: 2026-10-27.

Requested sources, read-only:

- https://github.com/Robertzu43/crypto-strategy-lab
- https://github.com/Robertzu43/liquidation-strategy-research

Nothing was cloned, forked, committed, or pushed on either repository. This note does not modify them.

## Access

Both paths are invisible to this run. The checks were:

| Check | `crypto-strategy-lab` | `liquidation-strategy-research` |
| --- | --- | --- |
| `gh api repos/Robertzu43/<name>` | HTTP 404 | HTTP 404 |
| `gh repo view Robertzu43/<name>` | GraphQL: could not resolve the repository | GraphQL: could not resolve the repository |
| `git ls-remote` on the public HTTPS URL | `repository not found` | `repository not found` |
| Unauthenticated fetch of the GitHub page | HTTP 404 | HTTP 404 |
| Name present in `GET /users/Robertzu43/repos?per_page=100&type=all` | No | No |

The GitHub credential on this run is the Cursor integration. It can list Robertzu43's public repositories. GitHub returns the same not-found response for a private repository when the caller was not granted access, so the two possibilities are not separable from here:

1. No repository exists at that path.
2. A private repository exists there, and this credential cannot read it.

Either way, no tree, file, function, or notebook was available to review. A web search for the two names did not turn up a public copy under Robertzu43. Other people publish unrelated repositories that happen to be named `crypto-strategy-lab`. Those were not audited. They are not the repositories this task named, and porting from them would launder someone else's project into this one.

The public repository list for the account, as returned on this run, includes `open-liquidation-heatmap` and does not include either strategy repository. There is no local checkout of them on this machine.

## Inventory

No module can be listed, because none could be opened.

| Kind of code the heatmap might reuse | Path | What it does | Quality | Port? |
| --- | --- | --- | --- | --- |
| Market-data fetchers | not readable | unknown | unknown | no |
| Liquidation-price math | not readable | unknown | unknown | no |
| Backtests | not readable | unknown | unknown | no |
| Plotting | not readable | unknown | unknown | no |
| Signals built on liquidation levels | not readable | unknown | unknown | no |

There is no quality note to write about unseen code, and there is no function worth copying on the evidence of this pass.

## Porting plan

Do not block the heatmap on these two repositories. The methodology it needs is already public and is written up in [HOW_IT_WORKS.md](HOW_IT_WORKS.md): exchange liquidation formulas, open-interest history, and the published reconstruction methods. Waiting on an unreadable tree does not improve that.

Do not fill the gap by cloning a similarly named repository from another owner.

When either URL becomes readable to a later pass (public, or this account granted read access), redo the audit from the tree and only then name files. The pass should look for the items below, in this order, and reject the rest. These are search targets, not files that were found.

1. **Contract-denominated open interest, with pagination.** A fetcher that diffs `sumOpenInterest` (Binance, one side, base-asset units), refuses to diff `sumOpenInterestValue`, and for Bybit uses `singleOpenInterest` rather than the two-sided `openInterest`. For windows past Binance's 30-day REST cap, a reader of `data.binance.vision` `futures/um/daily/metrics` plus the kline archives. Skip any client that needs an API key to do this; the public endpoints do not.
2. **A liquidation-price function with a fixed test vector.** Isolated linear, `cum = 0`, wallet equal to initial margin: long `entry × (1 − 1/L) / (1 − MMR)`. A useful test is entry 100,000, leverage 10, MMR 0.004, long price 90,361.45. Bracket tables should be inputs, not constants baked into the formula. Drop leverages above the contract's bracket-1 maximum and renormalize the weights.
3. **Side split and erasure.** Taker-buy share from the kline, the `min(1, 2b)` tilt or an equally explicit alternative, proportional scaling on open-interest declines, and erasure of bins the mark-price range has traded through. New positions should not be erased by the same bar that opened them. Zero or negative open-interest prints should be treated as missing.
4. **A plot that states its normalization.** One scale for the whole grid, sequential colormap, colorbar labeled as estimated liquidation notional. Carry the book forward so a level is a horizontal band, not a single-column deposit, if the chart is meant to be a stock of surviving risk.
5. **Signals and backtests last, and only if they are explicit about the estimate.** A strategy that treats a bright cell as a resting order, or as a price target, contradicts the public evidence summarized in HOW_IT_WORKS.md (weak acceleration, no magnet). Prefer research that joins the estimated book to subsequent open-interest changes and to the throttled liquidation feed, and that reports the leverage prior as a parameter. Leave execution, order routing, and key management where they are.

Anything in those repositories that scrapes a heatmap vendor, replays a private endpoint, or ships that vendor's levels should not be ported. This heatmap is public-API only.

If a later pass finds the repositories empty, or finds only unrelated strategy code with no liquidation math, the right outcome is still to port nothing and to keep building from the exchange documents.
