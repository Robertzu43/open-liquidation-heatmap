"""HTTP handler tests. The demo exchange needs no network."""

from __future__ import annotations

import gzip
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest import mock

import pytest

from liqmap import server
from liqmap.server import Handler, build_parser

DEMO = "/api/heatmap?exchange=demo&symbol=BTC&range=24h&model=1"


@pytest.fixture(scope="module")
def port():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def get(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def get_json(port, path):
    status, _, body = get(port, path)
    return status, json.loads(body)


def test_status_ok(port):
    status, body = get_json(port, "/api/status")
    assert status == 200
    assert body["ok"] is True


def test_demo_heatmap_has_360_bins(port):
    status, body = get_json(port, DEMO)
    assert status == 200
    assert body["bins"] == 360


def test_bad_range_is_400(port):
    status, body = get_json(port, "/api/heatmap?exchange=demo&symbol=BTC&range=bad")
    assert status == 400
    assert body["error"]


def test_asof_on_other_exchange_is_400(port):
    status, body = get_json(port, "/api/heatmap?exchange=okx&symbol=BTC&range=24h&asof=1700000000")
    assert status == 400
    assert "Binance-only" in body["error"]


def test_expired_replay_is_409(port):
    path = "/api/heatmap?exchange=binance&symbol=BTC&range=24h&asof=1700000000&replay=1"
    status, body = get_json(port, path)
    assert status == 409
    assert body["replay_expired"] is True


@pytest.mark.parametrize("path", ["/../liqmap/model.py", "/%2e%2e/README.md"])
def test_path_traversal_is_404(port, path):
    status, _, _ = get(port, path)
    assert status == 404


def test_gzip_when_asked(port):
    status, headers, body = get(port, DEMO, {"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(body))["bins"] == 360


def test_levels_for_a_demo_bar(port):
    _, heat = get_json(port, DEMO)
    t = heat["times"][len(heat["times"]) // 2]
    price = sum(heat["price_view"]) / 2
    status, body = get_json(port, f"/api/levels?exchange=demo&symbol=BTC&range=24h&model=1&t={t}&price={price}")
    assert status == 200
    assert body["tiers"]
    for tier in body["tiers"]:
        assert {"leverage", "long_usd", "short_usd"} <= tier.keys()
    assert "long_usd" in body and "short_usd" in body


@pytest.mark.parametrize("t", ["", "&t=abc"])
def test_levels_bad_t_is_400(port, t):
    status, body = get_json(port, f"/api/levels?exchange=demo&symbol=BTC&range=24h&model=1{t}&price=100")
    assert status == 400
    assert body["error"]


def test_page_url_uses_bound_host():
    scheme = "http" + "://"
    assert server.page_url("10.0.0.5", 80) == scheme + "10.0.0.5:80"
    assert server.page_url("::1", 80) == scheme + "[::1]:80"
    for wildcard in ("0.0.0.0", "::", ""):
        assert server.page_url(wildcard, 9) == "http://127.0.0.1:9"


def test_default_host_is_loopback():
    assert build_parser().parse_args([]).host == "127.0.0.1"


def test_open_flag_defaults_off():
    assert build_parser().parse_args([]).open is False
    assert build_parser().parse_args(["--open"]).open is True


def test_open_flag_opens_browser_without_blocking():
    opened = threading.Event()
    with mock.patch.object(server.webbrowser, "open", side_effect=lambda url: opened.set()) as opener:
        with mock.patch.object(server.ThreadingHTTPServer, "serve_forever", side_effect=KeyboardInterrupt):
            server.main(["--port", "0", "--open"])
        assert opened.wait(timeout=5)
    assert opener.call_args[0][0].startswith("http://127.0.0.1:")
