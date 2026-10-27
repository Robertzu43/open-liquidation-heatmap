"""Shared GET client: spacing, pagination-friendly retries, a clear 451.

Backoff on 429, 418, and 5xx is 1s, 2s, 4s, 8s by default. 451 and 403 raise
``ExchangeBlocked`` immediately so one venue cannot stall the others.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests

from olh.core.errors import ExchangeBlocked, ExchangeError

logger = logging.getLogger(__name__)

_RETRY_STATUS = {418, 429, 500, 502, 503, 504}


class HttpClient:
    def __init__(
        self,
        min_interval: float = 0.2,
        timeout: float = 20.0,
        backoff: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0),
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval = min_interval
        self.timeout = timeout
        self.backoff = backoff
        self.sleep = sleep
        self.now = now
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent",
            "open-liquidation-heatmap/0.2 (educational; public market data only)",
        )
        self.session.headers.setdefault("Accept", "application/json")
        self._last = 0.0

    def get(self, url: str, params: dict | None = None) -> requests.Response:
        attempts = len(self.backoff) + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            self._wait()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= len(self.backoff):
                    break
                self.sleep(self.backoff[attempt])
                continue
            if response.status_code in _RETRY_STATUS and attempt < len(self.backoff):
                logger.info("HTTP %s from %s; backing off %.0fs", response.status_code, url, self.backoff[attempt])
                self.sleep(self.backoff[attempt])
                continue
            if response.status_code in (401, 403, 451):
                raise ExchangeBlocked(_blocked_message(url, response))
            return response
        raise ExchangeError(f"request failed for {url}: {last_error}")

    def get_json(self, url: str, params: dict | None = None) -> Any:
        response = self.get(url, params=params)
        if response.status_code == 202 and not response.content:
            raise ExchangeBlocked(
                f"empty HTTP 202 from {url}. Treating that as a blocked public REST host."
            )
        if response.status_code >= 400:
            raise ExchangeError(
                f"HTTP {response.status_code} from {url}: {response.text[:180].strip()}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ExchangeError(f"non-JSON response from {url}") from exc

    def _wait(self) -> None:
        if self.min_interval <= 0:
            self._last = self.now()
            return
        elapsed = self.now() - self._last
        if elapsed < self.min_interval:
            self.sleep(self.min_interval - elapsed)
        self._last = self.now()


def _blocked_message(url: str, response: requests.Response) -> str:
    body = (response.text or "").strip().replace("\n", " ")[:180]
    return (
        f"HTTP {response.status_code} from {url}. Public REST refused the request. "
        "Binance (fapi.binance.com) and Bybit (api.bybit.com) return 451 or 403 from "
        "many US hosts. This venue is skipped; archive files and any other exchange "
        f"still run. Response: {body}"
    )
