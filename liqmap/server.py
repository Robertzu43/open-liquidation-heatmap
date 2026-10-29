"""Small threaded HTTP server for the heatmap UI and JSON API."""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import threading
import time
from importlib import resources
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from liqmap import __version__
from liqmap.data import DataError, RequestDeadlineExceeded, check_deadline, request_deadline
from liqmap.market import context_payload, liquidations_payload, watchlist_payload
from liqmap.service import ReplayExpired, SourceUnavailable, features_payload, levels_payload, load_heatmap, readiness_payload, replay_payload, status_payload, symbols_payload

log = logging.getLogger("liqmap.server")
MAX_HTTP_WORKERS = 16
SOCKET_IDLE_TIMEOUT_S = 10
REQUEST_DEADLINE_S = 60
OVERLOAD_SEND_TIMEOUT_S = 0.1
MAX_EXPENSIVE_REQUESTS = 4
_expensive = threading.BoundedSemaphore(MAX_EXPENSIVE_REQUESTS)
# server_address holds the bound IP, so --host localhost reads 127.0.0.1 here.
LOOPBACK_BINDS = {"127.0.0.1", "::1"}
LOOPBACK_HOST = re.compile(r"(127\.0\.0\.1|localhost|\[::1\])(:\d+)?")


class ClientDisconnected(Exception):
    """The browser closed its connection while a response was being sent."""


def web_root() -> Path:
    # An installed copy sits in site-packages, where a top-level "web" may belong to another package.
    checkout = Path(__file__).resolve().parent.parent / "web"
    return checkout if (checkout / "index.html").is_file() else Path(resources.files("liqmap_web"))


WEB_ROOT = web_root()
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Bound connection workers, including idle HTTP/1.1 connections."""

    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._worker_slots = threading.BoundedSemaphore(MAX_HTTP_WORKERS)
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(SOCKET_IDLE_TIMEOUT_S)
        return connection, address

    def process_request(self, request, client_address):
        if not self._worker_slots.acquire(blocking=False):
            body = b'{"error":"All HTTP workers are busy. Retry shortly.","code":"server_busy"}'
            response = (b"HTTP/1.1 503 Service Unavailable\r\n"
                        b"Content-Type: application/json; charset=utf-8\r\n"
                        b"Connection: close\r\nCache-Control: no-store\r\nRetry-After: 5\r\n"
                        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
            try:
                request.settimeout(OVERLOAD_SEND_TIMEOUT_S)
                request.sendall(response)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"open-liqmap/{__version__}"

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args) -> None:
        # Browsers routinely leave keep-alive sockets idle after a response.
        if fmt.startswith("Request timed out:"):
            log.debug("%s %s", self.address_string(), fmt % args)
        else:
            super().log_error(fmt, *args)

    def handle(self) -> None:
        try:
            super().handle()
        except (ClientDisconnected, BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The browser can cancel while waiting for or receiving a response.
            self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802 — stdlib handler name
        # Any page the user visits can send requests here; with DNS rebinding it could also read the replies.
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._send_json({"error": "Requests from other websites are refused. Open this address directly.", "code": "forbidden"}, status=403)
            return
        if self.server.server_address[0] in LOOPBACK_BINDS and not LOOPBACK_HOST.fullmatch(self.headers.get("Host", "").lower()):
            self._send_json({"error": "This server answers only Host 127.0.0.1, localhost or [::1]. Start it with --host to serve another name.", "code": "forbidden"}, status=403)
            return
        token = request_deadline.set(time.monotonic() + REQUEST_DEADLINE_S)
        try:
            self._get()
        finally:
            request_deadline.reset(token)

    def _get(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/favicon.ico":
                self._send_bytes(b"", status=204, content_type="image/x-icon", cache="no-cache")
                return
            if parsed.path == "/healthz":
                self._send_json({"ok": True, "process": "healthy"})
                return
            if parsed.path == "/readyz":
                payload = readiness_payload()
                self._send_json(payload, status=200 if payload["ok"] else 503)
                return
            route = parsed.path.replace("/api/v1/", "/api/", 1) if parsed.path.startswith("/api/v1/") else parsed.path
            expensive_routes = {
                "/api/heatmap": self._heatmap, "/api/features": self._features, "/api/levels": self._levels,
                "/api/replay": self._replay, "/api/context": self._context, "/api/liquidations": self._liquidations,
                "/api/watchlist": self._watchlist,
            }
            if route in expensive_routes:
                if not _expensive.acquire(blocking=False):
                    self._send_json({"error": "The server is busy. Retry shortly.", "code": "busy"}, status=429)
                    return
                try:
                    expensive_routes[route](parsed.query)
                finally:
                    _expensive.release()
                return
            if route == "/api/status":
                self._send_json(status_payload())
                return
            if route == "/api/symbols":
                self._send_json(symbols_payload())
                return
            self._static(parsed.path)
        except RequestDeadlineExceeded as exc:
            log.warning("request deadline exceeded path=%s", urlparse(self.path).path)
            self._send_json({"error": str(exc), "code": "deadline_exceeded"}, status=503)
        except ReplayExpired as exc:
            self._send_json({"error": str(exc), "code": "replay_expired", "replay_expired": True}, status=409)
        except SourceUnavailable as exc:
            log.warning("source unavailable path=%s error=%s", urlparse(self.path).path, exc)
            self._send_json({"error": str(exc), "code": "source_unavailable"}, status=503)
        except DataError as exc:
            self._send_json({"error": str(exc), "code": "invalid_request"}, status=400)
        except ClientDisconnected:
            self.close_connection = True
            return
        except Exception:
            log.exception("request failed")
            self._send_json({"error": "The server failed while building this map.", "code": "internal_error"}, status=500)

    @staticmethod
    def _params(query: str):
        if len(query) > 4096:
            raise DataError("Query string is too long.")
        try:
            params = parse_qs(query, keep_blank_values=False, max_num_fields=20)
        except ValueError:
            raise DataError("Too many query parameters.") from None
        if any(len(values) != 1 for values in params.values()):
            raise DataError("Query parameters must occur once.")
        if params.get("replay", ["0"])[0] not in {"0", "1"}:
            raise DataError("replay must be 0 or 1.")

        def one(name: str, default: str = "") -> str:
            values = params.get(name)
            return values[0] if values else default

        return one

    def _map_args(self, one) -> tuple:
        return (
            one("exchange", "okx"),
            one("symbol", "BTC"),
            one("range", "24h"),
            one("model", "1"),
            one("mode", "pair"),
            one("asof"),
            one("bar"),
            one("replay") == "1",
        )

    def _heatmap(self, query: str) -> None:
        one = self._params(query)
        self._send_json(load_heatmap(*self._map_args(one), visible_bars=one("visible_bars")))

    def _features(self, query: str) -> None:
        one = self._params(query)
        self._send_json(features_payload(*self._map_args(one), visible_bars=one("visible_bars")))

    def _levels(self, query: str) -> None:
        one = self._params(query)
        self._send_json(levels_payload(*self._map_args(one), one("t"), one("price"), visible_bars=one("visible_bars")))

    def _replay(self, query: str) -> None:
        one = self._params(query)
        self._send_json(replay_payload(one("symbol", "BTC"), one("range", "24h"), one("bar"), one("from"), one("to"), one("span")))

    @staticmethod
    def _live_args(one) -> dict:
        return {"asof": one("asof"), "bar": one("bar"), "replay": one("replay") == "1"}

    def _context(self, query: str) -> None:
        one = self._params(query)
        self._send_json(context_payload(one("exchange", "okx"), one("symbol", "BTC"), one("mode", "pair"), **self._live_args(one)))

    def _liquidations(self, query: str) -> None:
        one = self._params(query)
        self._send_json(liquidations_payload(one("exchange", "okx"), one("symbol", "BTC"), one("mode", "pair"), one("range", "24h"), **self._live_args(one)))

    def _watchlist(self, query: str) -> None:
        one = self._params(query)
        self._send_json(watchlist_payload(one("exchange", "okx"), one("range", "24h"), one("model", "1"), one("mode", "pair"), one("symbols"), **self._live_args(one)))

    def _static(self, url_path: str) -> None:
        if url_path in {"", "/"}:
            url_path = "/index.html"
        rel = unquote(url_path).lstrip("/")
        if not rel or any(part in {"", ".", ".."} for part in rel.split("/")):
            self._send_json({"error": "Not found.", "code": "not_found"}, status=404)
            return
        root = WEB_ROOT.resolve()
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self._send_json({"error": "Not found.", "code": "not_found"}, status=404)
            return
        if not candidate.is_file():
            self._send_json({"error": "Not found.", "code": "not_found"}, status=404)
            return
        body = candidate.read_bytes()
        mime = MIME.get(candidate.suffix.lower(), "application/octet-stream")
        self._send_bytes(body, status=200, content_type=mime, cache="no-cache")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        encoding = None
        accept = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept and len(raw) > 600:
            raw = gzip.compress(raw, compresslevel=5)
            encoding = "gzip"
        self._send_bytes(
            raw,
            status=status,
            content_type="application/json; charset=utf-8",
            cache="no-store",
            encoding=encoding,
        )

    def _send_bytes(self, body: bytes, *, status: int, content_type: str, cache: str, encoding: str | None = None) -> None:
        if status < 400:
            check_deadline()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if status in {429, 503}:
            self.send_header("Retry-After", "5")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            raise ClientDisconnected() from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve the open liquidation heatmap")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = BoundedThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Open liquidation heatmap at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
