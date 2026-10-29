# Operating Open Liq

Start locally with `python -m liqmap.server` (or the installed console command). Default bind is **127.0.0.1:8765**. Demo is offline: `/api/v1/heatmap?exchange=demo&range=12h`. Installed wheels include frontend assets; serving does not require the source checkout. No exchange API credentials are required for public market data, and source failures never fall back silently to demo.

## Outbound hosts

Open Liq makes outbound HTTPS requests only to public market endpoints:

| Host | Used for |
| --- | --- |
| `www.okx.com` | OKX candles, open interest, taker flow, funding, observed liquidation orders |
| `fapi.binance.com` | Binance live USDT-M candles, open interest, taker flow, funding |
| `data.binance.vision` | Binance public archive: fallback, as-of views and replay |
| `api.bybit.com` | Bybit candles, open interest and tickers |

Allow these in an egress firewall. Demo views make no outbound requests. The exchanges see normal requests from your IP address and may rate-limit or block it.

## Regional blocking

Some exchanges block some regions. Open Liq reports a block; it never replaces the data with another venue or with demo.

- **Binance.** When the live API is blocked, a Binance pair view falls back to the public archive. The archive is delayed, so the view can be marked stale. The aggregate leaves out Binance archive data that lags the live market and says so in `warnings`.
- **Bybit.** There is no archive fallback. A blocked Bybit API (for example HTTP 403) makes a Bybit pair view return 503 `source_unavailable` with a message naming the block. The aggregate leaves Bybit out and reports degraded coverage. `/api/v1/context` shows Bybit with `ok: false` and still returns the other venues.
- **OKX.** OKX has no fallback either. A blocked OKX also removes observed liquidations, because they come only from OKX.

## Exposure and probes

For a trusted local network, explicitly select a bind address with `--host`. An Internet-facing deployment needs a maintained reverse proxy providing TLS, authentication/access controls, request-rate limits, connection/header timeouts and response/body size limits. The built-in threaded server is intended for local use; it does not supply user authentication or a complete Internet edge. Put proxy concurrency/rate limits in front of it and run under a supervisor with restart and memory limits. The archive cache uses `LIQMAP_CACHE_DIR` when set, otherwise `$XDG_CACHE_HOME/open-liq`, otherwise `~/.cache/open-liq`. Keep that directory writable; monitor and apply a disk-retention policy for downloaded archives and captured research artifacts.

Any page open in your browser can send requests to a local server. Open Liq answers 403 `forbidden` to an `/api/` request the browser marks `Sec-Fetch-Site: cross-site`, on every bind. On a loopback bind (the default `127.0.0.1`, `localhost` or `::1`) it also answers 403 unless the `Host` header is `127.0.0.1`, `localhost` or `[::1]`, with any port. This stops DNS rebinding, where a hostile domain resolves to 127.0.0.1 to read the replies. The app's own page, curl and scripts are unaffected, and a link to the server from another website still opens the page, because static files carry no data or work. A proxy or editor preview that forwards another host name to a loopback bind must send `Host: 127.0.0.1`, or start Open Liq with `--host`.

- `/healthz`: process liveness only, always independent of exchange access.
- `/api/v1/status`: configuration and capabilities. An HTTP 200 status response means the process can describe its configuration.

Live source quality, including archive fallback, checks the age of the latest bar with both observed price and OI against twice the bar interval; historical/as-of/replay assessments retain their requested-boundary semantics. Aggregate quality is degraded when expected venues or observations are missing, and stale venues are excluded. Root `stale` agrees with `quality.stale`. This two-bar budget is not a freshness SLA. Set a tighter external event-age check for the strategy's own decision horizon. Historical archive completion and historical first-public-availability are different facts.

## Bounded work

The server admits at most **4 expensive requests** at a time across heatmap, features, levels, replay span loading, context, liquidations and watchlist, including legacy aliases. A watchlist request holds one slot for the whole request. It loads its symbols' data with up to **3 worker threads** and builds the models one at a time; threads would only contend for Python's interpreter lock on that CPU-bound work. Admission is immediate: excess work receives HTTP 429 `busy` with `Retry-After: 5`. Slots are released on success and errors. Health, status and static assets remain outside the expensive-work gate. The HTTP transport has a separate maximum of **16 connection workers**, including keepalive and partial requests. Further accepted connections receive immediate HTTP 503 `server_busy`, `Retry-After: 5` and connection close, without another worker thread. At this transport limit probes can also be rejected. Sockets have a **10-second idle read/write timeout**; this is an idle timeout, not a total header-duration limit. Use proxy connection and header limits when exposing it.

Series cache: at most **16 entries**, evicted by oldest load time. TTLs are 30 seconds (`12h`), 45 (`24h`), 120 (`3d`), 300 (`7d`), 600 (`30d`), 900 (`90d`), 1200 (`180d`) and 1800 (`1y`, `2y`). Requests for the same key coalesce behind a per-key lock. Locks are reference-counted and removed when their users finish, so past keys do not accumulate permanent lock objects. Cache identity includes exchange, base asset, range, mode, bar and historical cutoff. Models share source-series entries. Expiry cleanup is lazy on subsequent activity; no background worker claims ongoing data freshness.

Context cache: **30-second** TTL, at most **32 entries**. Liquidations cache: **30-second** TTL. The watchlist has no cache of its own. Each of its symbols goes through the series cache above, so a six-symbol watchlist can take six of the 16 series entries and evict older views. Live browser views refresh every **60 seconds** by default, and each open tab repeats its requests. Several open tabs or a large watchlist can fill the 4 expensive slots; the page then receives 429 `busy` until a slot frees.

Replay cache: at most **2 spans**, with **30-minute idle expiry**. Loading is limited to **730 days** of frame progression, **12000 total bars**, and **1100 days of source history**, including the lookback window, before archive fetching/allocation. Each visible frame remains capped at **3000 bars**. Frames are sliced from a loaded span and never issue network requests; missing/evicted spans return 409 and must be explicitly loaded again. The normal view ranges are finite (up to 2 years). Large cold archive queries can exceed the 60-second cooperative request budget and are not cancelled when a client disconnects. Expected browser disconnects are handled without server error tracebacks; keep-alive timeouts appear at debug level.

Queries are limited to 4096 characters and 20 fields; repeated parameters are rejected. Responses serialize before headers with strict finite JSON. Unexpected nonfinite chart data causes an explicit build error rather than a corrupted binary grid. This bounds request-driven series/span memory, not total process RSS or disk usage. File/archive caches are persistent on disk; their retention requires operator monitoring. Archive locking uses a fixed **64 lock stripes**; HEAD and missing-ZIP metadata caches hold at most **1024 entries each**. Network responses and cached ZIP files are limited to **64 MiB**, and an inflated ZIP member to **128 MiB**. These per-item limits do not replace a total disk-retention policy. Each handler receives a **60-second cooperative budget**, carried in a ContextVar through source loading and copied into source worker pools. Outbound connect/read timeouts, archive streams/locks and retry delays use the remaining budget; checks stop subsequent source calls, and a final check rejects a late successful response before headers with HTTP 503 `deadline_exceeded`. Outbound JSON calls permit up to three attempts, with an exponential delay between attempts and none after the last, within that budget. In the all-venues view each venue gets the request budget minus up to 10 seconds; a venue that runs out is left out and named in `warnings`, so the other venues still answer. Binance archive days download 32 at a time over reused connections; a cold 2-year view took about 30 seconds here. A cached or downloaded ZIP that fails the ZIP check is fetched again, and partial downloads are named per process, so several servers can share one cache. The budget begins after request headers have been parsed. An individual in-flight socket operation, CPU calculation or OS/filesystem call cannot be forcibly preempted; checks happen at cooperative boundaries, so this is not a hard wall-clock guarantee. There is no queue/worker service.

## Failures and observability

Validation/bounds failures return 400 `invalid_request`, including a watchlist with more than six symbols. Replays can return 409 `replay_expired`, expensive-work overload returns 429 `busy`, connection-worker saturation returns 503 `server_busy`, exhausted cooperative budgets return 503 `deadline_exceeded`, public-source failures return 503 `source_unavailable`, and unexpected internal errors return 500 `internal_error`. Public error messages retain a string `error` for legacy clients. Apply a limited retry budget with exponential backoff and jitter for 429/503; honor `Retry-After`, then surface failure. Retrying an invalid request or expired replay without changing/reloading it does not help.

Venue failures are isolated where a response covers several sources. The aggregate leaves a failed or stale venue out and marks coverage degraded. `/api/v1/context` returns 503 `source_unavailable` only when every requested live venue fails; otherwise the failed venue has `ok: false` and an `error`. `/api/v1/watchlist` reports a failed symbol inside its item with `ok: false`.

The server logs request path/status through Python logging, actionable source failures and unexpected exception traces. Source metadata, model version, parameter digest, quality and a stable data fingerprint are returned with each research response. Log/capture those fields when diagnosing a strategy input. `generated_at` is response generation time, not source freshness; missing availability boundaries remain unknown. Avoid placing credentials in query strings, because HTTP access logs contain paths. No credentials are needed for documented public sources.

Run offline contracts with:

```sh
python -m pytest tests/test_api.py tests/test_runtime.py tests/test_history.py -q
python -m pytest -q
```

Live-network tests are opt-in and excluded from default deterministic verification. Use a declared workload and environment when measuring cold versus warm latency, payload and memory; a local demo timing does not establish live uptime or provider-replacement readiness. Production reliability needs an observed shadow run with gap/outage accounting, source freshness checks and a strategy-specific acceptance policy.


## Local benchmark scope

`python scripts/benchmark.py --output /tmp/open-liq-benchmark` times the model and payload build for nine synthetic BTC cases (ranges 24h, 7d and 30d, models 1 to 3), seven runs each from a cached source, and records the JSON and gzip payload sizes. It makes no HTTP or exchange requests, so it is not a live-data SLA, load test or memory measurement. Shared-host contention or virtualization can change results. Regenerate published numbers from the final reviewed revision rather than treating a scratch run as a published benchmark.
