# Deploying the heatmap

The app reads public market data from Binance USD-M, Bybit linear, and OKX swap endpoints. CoinGlass is out of scope for this project: do not add CoinGlass hosts, API keys, or exported datasets to the deployment.

The Streamlit entrypoint (`app.py`, `streamlit_app.py`, or `heatmap_app.py`) lands with the model rewrite. This document is the hosting side. Set `STREAMLIT_APP` if the file uses another name.

Two hosts are covered:

- **Streamlit Community Cloud** installs `requirements.txt` and ignores the `Dockerfile`.
- **Hugging Face Spaces (Docker SDK)** and any other container host build the `Dockerfile`, which listens on port **7860**.

## Region

The demo host has to sit in a region Binance and Bybit serve.

`fapi.binance.com` returns **HTTP 451** to US IP addresses. Bybit often returns **HTTP 403** from the same places (a CloudFront country block). GitHub-hosted Actions runners are typically in the United States, so the scheduled smoke test skips an exchange that answers 451 or 403 instead of treating that response as a healthy check. OKX currently answers from US regions; a US-only demo still loses Binance and Bybit.

Binance publishes historical klines at `data.binance.vision` with no API key. A loader may read that archive when `fapi.binance.com` is geo-blocked. The archive is still Binance. Bybit stays on `api.bybit.com` and OKX on `www.okx.com`. `tests/validation/net_allowlist.py` lists those hosts and no other exchange.

The Streamlit app and the browser chart both call `liqmap.model`. `heatmap.liq_price`, `heatmap.fade_swept`, and `heatmap.bin_intensity` re-export that module. Do not keep a second liquidation formula in the UI.

Public open interest does not need an exchange API key. Do not put a key in the repository, in CI, or in the image. A person who connects a read-only exchange account does that on their own machine, through the process environment. The app does not store that key, and it does not use the key as a second heatmap.

Pick a Community Cloud or Spaces region in the EU or APAC, or any other network those two venues accept. When a venue returns 451 or a country block, the app should name that venue and keep drawing the venues that responded.

## Streamlit Community Cloud

1. Push the repository to GitHub. The default branch needs the Streamlit file and `requirements.txt`.
2. Open [share.streamlit.io](https://share.streamlit.io), choose **Create app**, and pick this repository and branch.
3. Set **Main file path** to the entrypoint (`app.py` or `streamlit_app.py`).
4. In **Advanced settings**, select **Python 3.12**.
5. Leave secrets empty. Public klines and open interest do not need an exchange API key.
6. Add `streamlit` and `plotly` to `requirements.txt` before the first Cloud deploy. The Docker image installs them when they are missing; Community Cloud will not.

Community Cloud shares one outbound IP across viewers of the app. The cache in the next section is what keeps that IP inside exchange rate limits.

## Hugging Face Spaces

1. Create a Space with **Docker** as the SDK. The repository `Dockerfile` is the build.
2. The process must listen on port **7860**. The image defaults `PORT` to 7860. Hugging Face also injects `PORT`; the entrypoint uses that value.
3. CPU basic hardware is enough for a single symbol heatmap. A GPU does not speed up the public HTTP calls.
4. Spaces sleep when idle. The first request after sleep refetches market data; the cache TTL still applies after that.
5. To run the same image locally:

```bash
docker build -t open-liquidation-heatmap .
docker run --rm -p 7860:7860 open-liquidation-heatmap
```

Override the port when the host expects 8501:

```bash
docker run --rm -e PORT=8501 -p 8501:8501 open-liquidation-heatmap
```

The container starts as a non-root user and probes `http://127.0.0.1:$PORT/_stcore/health`.

A Streamlit SDK Space (no Docker) is the other option: set the SDK to Streamlit, point it at the same entrypoint, and put `streamlit` and `plotly` in `requirements.txt`. Use one SDK. A Docker Space uses the `Dockerfile` and does not read a Streamlit YAML header.

## Caching and rate limits

Official limit references, which change without notice:

- Binance USD-M: [General info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info) (request weight, HTTP 429, and the follow-up 418 ban)
- Bybit v5: [Rate limit](https://bybit-exchange.github.io/docs/v5/rate-limit)
- OKX v5: [Rate limits](https://www.okx.com/docs-v5/en/#overview-rate-limits)

Client behavior that stays inside those limits:

- Cache each response with `@st.cache_data` on the key `(exchange, symbol, interval, window)`. A TTL of 60–300 seconds is enough for a heatmap and collapses a rerun, a leverage toggle, and a second viewer onto one fetch.
- Paginate once per cache fill. Binance klines return at most 1500 rows and open-interest history at most 500. Bybit and OKX pages are smaller. A 90-day window is several calls; it happens when the cache misses, not on every widget event.
- On HTTP 429 or 418, or on an exchange rate-limit code, wait and render the cached frame with the time it was fetched. Do not retry in a tight loop.
- Keep the cache process-wide so every session shares it.
- Binance's public open-interest history retains roughly the last month. A 90-day window on Binance should show the history that exists and say the rest is outside the public retention, rather than failing the whole page. Bybit and OKX retention differ; treat a short history the same way.
- A venue that returns HTTP 451 or a country block is unreachable from this host. Name the venue and keep the venues that responded. Move the demo if Binance or Bybit is the one being blocked; see the Region section above.

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
