# Reuse from the strategy repositories

Audit date: 2026-10-27. Read-only. Nothing was cloned, committed, or pushed on either source repository.

Both repositories are private. They are readable with Robert's GitHub account. The GitHub App credential on this cloud VM is installed only on `open-liquidation-heatmap`, so a token local to the VM returns 404 for them. That is why an earlier draft of this note reported the trees as missing. This revision replaces that draft with the inventory from the branches below.

| Repository | Branch to read | What that branch contains |
| --- | --- | --- |
| [crypto-strategy-lab](https://github.com/Robertzu43/crypto-strategy-lab) | `feat/kronos-strategy` | Includes `feat/slice1-flush`. Take the data client and the data-quality helpers. Leave the Kronos study and the flush strategy. |
| [liquidation-strategy-research](https://github.com/Robertzu43/liquidation-strategy-research) | `feature/robust-strategy-lab` | Take the pressure model, `models.py`, and the public download scripts. Leave the CoinGlass notes and `real-trades/`. |

## License gate

Neither repository has a `LICENSE` file yet. This heatmap is MIT. Do not copy any module, test, or script into it until Robert confirms an MIT license (or another license that can sit inside this MIT tree) on the source commit being ported. A private repo without a license is all-rights-reserved by default. Reading it for this audit is not permission to redistribute the code.

## Reusable from crypto-strategy-lab

Branch: `feat/kronos-strategy` (this branch includes `feat/slice1-flush`). Port individual files. Do not merge the branch. Merging it would bring the Kronos study and the killed flush strategy along with the useful data code.

### `crypto_lab/data.py`

Binance public-archive client. It caches downloads, retries failed requests, and checks checksums.

This is the fetcher the heatmap should port first. Windows past about 30 days cannot be served by `GET /futures/data/openInterestHist`; they need the public `data.binance.vision` metric and kline archives, and those archives are zip files that fail quietly when a download is truncated. Cache, retries, and checksums are the difference between a 90-day map and a corrupted one. Keep the client pointed at the public archive. Do not add API keys, and do not generalize it into a trading client.

### `crypto_lab/events.py`, data-quality helpers only

Port these helpers:

| Symbol | Role in the heatmap |
| --- | --- |
| `to_5m` | Put open interest and candles on the 5-minute grid the Binance metrics archive already uses. |
| `_asof` | Align a slower series onto that grid by the last observation available at each bar. |
| `taker_share` | Taker-buy share for the long/short tilt described in [HOW_IT_WORKS.md](HOW_IT_WORKS.md). |
| gap guard | Stop the book when timestamps are missing, instead of treating a hole as a real open-interest change. A zero or missing print must not scale the whole map to nothing. |

Do not port `detect()`. It belongs to the flush strategy, which is killed. The helpers above are data quality. `detect()` is a strategy.

### `diagnostics.timestamp_check`, and the tests

`diagnostics.timestamp_check` checks that archive timestamps line up with the bar grid before any liquidation price is computed. Port it with the tests that cover `to_5m`, `_asof`, `taker_share`, the gap guard, the archive client, and the timestamp check. The tests are the contract. Porting the helpers without them will let a later edit look one bar into the future or drop a gap.

## Reusable from liquidation-strategy-research

Branch: `feature/robust-strategy-lab`.

| Path | Why it is on the port list |
| --- | --- |
| `liquidation/pressure.py` | Liquidation-pressure signal. Port it only as a consumer of the estimated book this heatmap builds from public open interest. It has to keep the same caveat as the map: a bright cell is an estimated trigger, not a resting order and not a price target. |
| `models.py` | Liquidation model code on this branch. Port the price and book math that is consistent with the isolated formula in [HOW_IT_WORKS.md](HOW_IT_WORKS.md). Re-read the file at the license gate and drop anything that hard-codes a vendor's levels. |
| `scripts/download_*.py` | Download scripts for the public inputs. Use them as offline archive builders next to `crypto_lab/data.py`, after a file-by-file check that each one hits a public exchange archive and does not import `free_notes`, `free_path`, or `real-trades`. |

## Explicitly excluded

Do not port any of the following, even though they live on the same branches as the files above.

| Path or body of work | Why it stays out |
| --- | --- |
| `liquidation/free_notes.py` | Records CoinGlass chart observations as data. This heatmap does not scrape CoinGlass, does not reverse-engineer it, and does not use its data. |
| `liquidation/free_path.py` | Same exclusion. Chart observations from CoinGlass are not a public-API input. |
| `real-trades/` | Personal trading data. Not a market-data source and not something to publish inside this repo. |
| Flush strategy, including `detect()` in `crypto_lab/events.py` | Killed. `feat/slice1-flush` is inside `feat/kronos-strategy` only so the data helpers on that line of history can be read. The strategy itself is not reused. |
| Kronos study | Not part of the heatmap. Do not port the study, its notebooks, or its model weights because they share a branch with `crypto_lab/data.py`. |

## Porting plan

The order is fixed by the license and by what the heatmap can run without the strategy code.

1. **License.** Robert adds or confirms MIT on both source commits. Until that confirmation, the list above is a reading list, not a diff.
2. **Archive client.** Port `crypto_lab/data.py` into this repo's data layer. Keep cache, retries, and checksums. Target the public Binance vision metrics (`sum_open_interest`, not the USDT value) and klines, including taker-buy volume.
3. **Alignment and tests.** Port `to_5m`, `_asof`, `taker_share`, the gap guard, and `diagnostics.timestamp_check`, plus their tests. Leave `detect()` behind. Wire taker share into the long/short tilt, and wire the gap guard so a missing or non-positive open-interest print does not wipe the book.
4. **Download scripts.** Port `scripts/download_*.py` from `feature/robust-strategy-lab` only after each script is confirmed to use public exchange URLs. They should fill the cache the archive client reads. They should not be a second, unchecked downloader.
5. **Model and pressure last.** Port `models.py` and `liquidation/pressure.py` once the public book exists. They sit downstream of that book. They do not replace it, and they do not become an order router.
6. **Leave the exclusions on the source repos.** No `free_notes.py`, no `free_path.py`, no `real-trades/`, no flush strategy, no Kronos study. Do not push anything back to `crypto-strategy-lab` or `liquidation-strategy-research`.
