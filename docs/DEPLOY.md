# Deploying the heatmap

The app reads public market data. CoinGlass is out of scope: do not add CoinGlass hosts, API keys, or exported datasets to the deployment.

The Streamlit entrypoint (`app.py`, `streamlit_app.py`, or `heatmap_app.py`) lands with the model rewrite. This document is the hosting side. Set `STREAMLIT_APP` if the file uses another name.

The Streamlit app and the browser chart both call `liqmap.model`. `heatmap.liq_price`, `heatmap.fade_swept`, and `heatmap.bin_intensity` re-export that module. Do not keep a second liquidation formula in the UI.

Public open interest does not need an exchange API key. Do not put a key in the repository, in CI, or in the image. A person who connects a read-only exchange account does that on their own machine, through the process environment. The app does not store that key, and it does not use the key as a second heatmap.

## What v0.1 can reach

[Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud/status) hosts every app in the United States. That region is not configurable.

From a US address, `fapi.binance.com` returns **HTTP 451** and Bybit often returns **HTTP 403**. A Community Cloud demo cannot call Binance or Bybit REST. **v0.1 uses live OKX plus the public `data.binance.vision` archive.** That archive is Binance historical klines and needs no API key. It is still Binance. It is not a live Binance order book or a live open-interest feed.

Live Binance and Bybit REST needs a host outside the United States. That host is a planned recorder (Frankfurt is the example region). It is not a self-hosted GitHub Actions runner. The nightly smoke job stays on GitHub-hosted runners, which are typically in the US, and skips a venue that answers 451 or 403. A skip is not a healthy result. See `.github/workflows/ci.yml`.

Allowed hosts are `fapi.binance.com`, `data.binance.vision`, `api.bybit.com`, and `www.okx.com` (`tests/validation/net_allowlist.py`). Websocket hosts wait until the recorder exists. No other exchange is a heatmap source.

When a venue returns 451 or a country block, the app should name that venue and keep drawing the venues that responded.

## Streamlit Community Cloud

Community Cloud installs `requirements.txt` and ignores the `Dockerfile`.

1. Push the repository to GitHub. The default branch needs the Streamlit file and `requirements.txt`.
2. Open [share.streamlit.io](https://share.streamlit.io), choose **Create app**, and pick this repository and branch.
3. Set **Main file path** to the entrypoint (`app.py` or `streamlit_app.py`).
4. In **Advanced settings**, select **Python 3.12**.
5. Leave secrets empty. Public klines and open interest do not need an exchange API key.
6. Add `streamlit` and `plotly` to `requirements.txt` before the first Cloud deploy. The Docker image installs them when they are missing; Community Cloud will not.

Expect this deploy to show OKX and the Binance archive. Do not treat a Binance or Bybit 451/403 as a bug in the plot.

The [status page](https://docs.streamlit.io/deploy/streamlit-community-cloud/status) lists several egress addresses for Community Cloud, and it warns that the list can change without notice. This doc does not copy that list or state a count. Caching (below) is what keeps those shared egress addresses inside exchange rate limits.

## Render Free (Frankfurt)

Render Free is the free container host. Pick **Frankfurt** during service creation ([regions](https://render.com/docs/regions)). Free web services are not a production plan: they spin down after 15 minutes without inbound traffic and spin back up on the next request ([free instances](https://render.com/docs/free)).

1. In the Render dashboard, create a **Web Service** from this repository.
2. Choose the **Docker** runtime so Render builds the `Dockerfile`. The process must listen on Render's `PORT`. The image reads `PORT` and defaults it to 7860 only when the variable is unset.
3. Set the region to **Frankfurt** and the instance type to **Free**.
4. Leave environment secrets empty.

Frankfurt is outside the United States, so this host is a candidate for live Binance and Bybit REST once the recorder exists. v0.1 on Community Cloud does not get that path by changing a Cloud setting.

To run the same image locally:

```bash
docker build -t open-liquidation-heatmap .
docker run --rm -p 7860:7860 open-liquidation-heatmap
```

Override the port when the host injects one:

```bash
docker run --rm -e PORT=8501 -p 8501:8501 open-liquidation-heatmap
```

The container starts as a non-root user and probes `http://127.0.0.1:$PORT/_stcore/health`.

## Hugging Face Spaces

Spaces is not a free option for this app.

- The built-in Streamlit SDK is [deprecated](https://huggingface.co/docs/hub/spaces-sdks-streamlit). A Streamlit Space is a Docker Space.
- Creating a Docker Space on CPU Basic requires a paid plan: PRO for a personal account, Team or Enterprise for an organization ([Spaces overview](https://huggingface.co/docs/hub/spaces-overview)). CPU Basic has no hourly rate, but the plan is still required.
- Without Team or Enterprise, Hub repositories, including Spaces storage and runtime, stay in the United States ([storage regions](https://huggingface.co/docs/hub/storage-regions)). That is the same 451/403 limit as Community Cloud.

## Caching and rate limits

Official limit references, which change without notice:

- Binance USD-M: [General info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info) (request weight, HTTP 429, and the follow-up 418 ban)
- Binance open-interest history: [`GET /futures/data/openInterestHist`](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics). `limit` defaults to 30 and the maximum is **500** rows per call. Only about the **latest 1 month** is available.
- Bybit v5: [Rate limit](https://bybit-exchange.github.io/docs/v5/rate-limit)
- OKX v5: [Rate limits](https://www.okx.com/docs-v5/en/#overview-rate-limits)

Client behavior that stays inside those limits:

- Cache each response with `@st.cache_data` on the key `(exchange, symbol, interval, window)`. A TTL of 60–300 seconds is enough for a heatmap and collapses a rerun, a leverage toggle, and a second viewer onto one fetch.
- Paginate once per cache fill. Binance klines return at most 1500 rows. Binance open-interest history returns at most 500 rows per call and only about the latest month, so a 90-day Binance window must say that the rest is outside public retention instead of failing the page. Bybit and OKX pages are smaller, and their retention differs; treat a short history the same way.
- On HTTP 429 or 418, or on an exchange rate-limit code, wait and render the cached frame with the time it was fetched. Do not retry in a tight loop.
- Keep the cache process-wide so every session shares it.
- A venue that returns HTTP 451 or a country block is unreachable from a US host. Name the venue and keep the venues that responded. On Community Cloud that means OKX plus `data.binance.vision`.

```python
@st.cache_data(ttl=120, show_spinner=False)
def load_klines(exchange: str, symbol: str, interval: str, window: str):
    ...
```

## Donation QR

No donation address is configured.

When a maintainer adds one later:

1. Publish a single public receive address in this section.
2. Store the QR image at `docs/assets/donation-qr.png` and link it here.
3. Keep seed phrases, private keys, and payment-processor secrets out of the repository.

The image and the address are intentionally absent.
