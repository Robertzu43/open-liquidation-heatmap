"""Small threaded HTTP server for the heatmap UI and JSON API."""

from __future__ import annotations

import argparse
import gzip
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from liqmap.data import DataError
from liqmap.service import ReplayExpired, levels_payload, load_heatmap, replay_payload, status_payload, symbols_payload

log = logging.getLogger("liqmap.server")
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "open-liqmap/0.2"

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802 — stdlib handler name
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if parsed.path == "/api/heatmap":
                self._heatmap(parsed.query)
                return
            if parsed.path == "/api/levels":
                self._levels(parsed.query)
                return
            if parsed.path == "/api/replay":
                self._replay(parsed.query)
                return
            if parsed.path == "/api/status":
                self._send_json(status_payload())
                return
            if parsed.path == "/api/symbols":
                self._send_json(symbols_payload())
                return
            self._static(parsed.path)
        except ReplayExpired as exc:
            self._send_json({"error": str(exc), "replay_expired": True}, status=409)
        except DataError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except BrokenPipeError:
            return
        except Exception:
            log.exception("request failed")
            self._send_json({"error": "The server failed while building this map."}, status=500)

    @staticmethod
    def _params(query: str):
        params = parse_qs(query, keep_blank_values=False)

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
        self._send_json(load_heatmap(*self._map_args(self._params(query))))

    def _levels(self, query: str) -> None:
        one = self._params(query)
        self._send_json(levels_payload(*self._map_args(one), one("t"), one("price")))

    def _replay(self, query: str) -> None:
        one = self._params(query)
        self._send_json(replay_payload(one("symbol", "BTC"), one("range", "24h"), one("bar"), one("from"), one("to")))

    def _static(self, url_path: str) -> None:
        if url_path in {"", "/"}:
            url_path = "/index.html"
        rel = unquote(url_path).lstrip("/")
        if not rel or any(part in {"", ".", ".."} for part in rel.split("/")):
            self._send_json({"error": "Not found."}, status=404)
            return
        root = WEB_ROOT.resolve()
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self._send_json({"error": "Not found."}, status=404)
            return
        if not candidate.is_file():
            self._send_json({"error": "Not found."}, status=404)
            return
        body = candidate.read_bytes()
        mime = MIME.get(candidate.suffix.lower(), "application/octet-stream")
        self._send_bytes(body, status=200, content_type=mime, cache="no-cache")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
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
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        self.wfile.write(body)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the open liquidation heatmap")
    # Localhost only by default. Pass --host 0.0.0.0 to share on a trusted network.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"Open liquidation heatmap at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
