"""Offline browser acceptance. Start python -m liqmap.server, then run this file.

Uses only the deterministic demo API. --url and --output point it at a local server and a capture folder.
Requires Playwright and Chromium (python -m playwright install chromium);
--chromium points at a specific Chromium build when the bundled one is missing.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from playwright.sync_api import sync_playwright

# Every data route the page can call. Only exchange=demo reaches the server.
DATA_ROUTES = ("**/api/heatmap?*", "**/api/v1/context?*", "**/api/v1/liquidations?*", "**/api/v1/watchlist?*")


def offline_only(route):
    exchange = parse_qs(urlparse(route.request.url).query).get("exchange", [""])[0]
    if exchange != "demo":
        route.fulfill(status=400, json={"error": "Browser smoke permits only offline demo data"})
    else:
        route.continue_()


def offline_context(browser, **options):
    context = browser.new_context(**options)
    for pattern in DATA_ROUTES:
        context.route(pattern, offline_only)
    return context


def next_frame(page) -> None:
    page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def replay_regressions(context, url: str, demo: dict) -> None:
    """Late responses must retain their original context; zoom always keeps eight bars."""
    first = int(demo["as_of"] / 1000)
    failures = []
    for case in ("model response", "frame order", "live cancels span", "live cancels error", "minimum time span", "shared auto bar"):
        page = context.new_page()
        page.add_init_script("""
            const originalFetch = window.fetch;
            window.heldResponses = [];
            window.fetch = async (...args) => {
                const response = await originalFetch(...args);
                const url = new URL(args[0], location.href);
                const hold = window.holdSpan && url.pathname === '/api/replay' ||
                    window.holdFrame && url.searchParams.get('replay') === '1' && url.searchParams.get('model') === '1';
                if (hold) return new Promise(resolve => window.heldResponses.push(() => resolve(response)));
                return response;
            };
        """)
        def frame(route):
            params = parse_qs(urlparse(route.request.url).query)
            model = int(params.get("model", ["1"])[0])
            payload = dict(demo)
            payload.update(exchange="binance", model={**demo["model"], "id": model},
                           title=f"Offline scenario {model}",
                           asof=int(params["asof"][0]) * 1000 if "asof" in params else None,
                           replay=params.get("replay") == ["1"])
            route.fulfill(json=payload)
        page.route("**/api/heatmap?*", frame)
        page.route("**/api/replay?*", lambda route, *, case=case: route.fulfill(status=503, json={"error": "Expired old replay context"}) if case == "live cancels error" else route.fulfill(json={
            "first_asof": first * 1000, "bar_seconds": 300, "frames": 3,
            "days": 1, "note": "Offline delayed-response fixture",
        }))
        try:
            if case == "shared auto bar":
                page.goto(url + "/?exchange=demo&coin=BTC&range=24h&model=1")
                page.wait_for_function("state.data !== null")
                page.evaluate("localStorage.setItem(STORE, JSON.stringify({exchange:'binance', range:'24h', bar:'5m', coin:'BTC', mode:'pair'}))")
                page.goto(url + "/?exchange=binance&mode=pair&coin=BTC&range=2y&model=1")
            else:
                page.goto(url + f"/?exchange=binance&mode=pair&coin=BTC&range=24h&model=1&asof={first}")
            page.wait_for_function("state.data !== null")
            if case in ("model response", "frame order"):
                page.evaluate("() => { void startReplay(false); }")
                page.wait_for_function("state.replay && state.data.replay")
                page.evaluate("() => { state.replay.cache.clear(); window.holdFrame = true; void showFrame(1).finally(() => window.frameDone = true); }")
                page.wait_for_function("window.heldResponses.length > 0")
                if case == "model response":
                    page.locator("#model-settings > summary").click()
                    page.locator('[data-model="2"]').click()
                    page.wait_for_function("state.data.model.id === 2")
                else:
                    page.evaluate("() => { window.holdFrame = false; void showFrame(2); }")
                    page.wait_for_function(f"state.data.asof === {(first + 600) * 1000}")
                page.evaluate("() => { window.holdFrame = false; window.heldResponses.splice(0).forEach(release => release()); }")
                page.wait_for_function("window.frameDone === true")
                if case == "model response":
                    assert page.evaluate("state.model === 2 && state.data.model.id === 2"), "late replay frame replaced selected model"
                else:
                    assert page.evaluate(f"state.replay.idx === 2 && state.data.asof === {(first + 600) * 1000}"), "late replay frame replaced latest scrub position"
            elif case in ("live cancels span", "live cancels error"):
                page.evaluate("() => { window.holdSpan = true; void startReplay(false).finally(() => window.spanDone = true); }")
                page.wait_for_function("window.heldResponses.length === 1")
                page.locator("#replay-settings > summary").click()
                page.locator("#live").click()
                page.wait_for_function("state.asof === null && state.data && state.data.asof === null")
                page.evaluate("window.heldResponses.shift()()")
                page.wait_for_function("window.spanDone === true")
                assert page.evaluate("state.replay === null && state.asof === null && state.data.asof === null && !state.data.replay"), "late replay span resurrected history after Live"
            elif case == "minimum time span":
                page.locator("#zoom-axis").select_option("time")
                page.locator("#chart").focus()
                for _ in range(36):
                    page.keyboard.press("+")
                assert page.evaluate("state.view.i1 - state.view.i0 >= Math.min(8, state.data.times.length) - 1e-8"), "time zoom shrank below eight bars"
            else:
                assert page.evaluate("state.bar === '' && document.getElementById('bar').value === ''"), "shared Auto bar inherited saved 5m interval"
        except AssertionError as error:
            failures.append(f"{case}: {error}")
        finally:
            page.close()
    assert not failures, "; ".join(failures)


def export_identity_regressions(context, url: str, demo: dict, output: Path) -> None:
    """Exports retain displayed identity while model/market/replay requests wait."""
    failures = []
    for case in ("model", "market", "replay"):
        page = context.new_page()
        page.add_init_script("""
            const originalFetch = window.fetch;
            window.exportResponses = [];
            window.fetch = async (...args) => {
                const response = await originalFetch(...args);
                if (window.holdExports && new URL(args[0], location.href).pathname === '/api/heatmap')
                    return new Promise(resolve => window.exportResponses.push(() => resolve(response)));
                return response;
            };
        """)
        first = int(demo["as_of"] / 1000)
        exchange = "binance" if case == "replay" else "demo"
        def payload_route(route, *, exchange=exchange, first=first, case=case):
            params = parse_qs(urlparse(route.request.url).query)
            if params.get("model") == ["2"] or params.get("symbol") == ["ETH"]:
                route.fulfill(status=503, json={"error": "Held selection failed deterministically"})
                return
            payload = dict(demo)
            payload.update(exchange=exchange, coin="BTC", range="24h", mode="pair",
                title="Displayed fixture BTC", asof=first*1000 if case == "replay" else None,
                replay=params.get("replay") == ["1"])
            route.fulfill(json=payload)
        page.route("**/api/heatmap?*", payload_route)
        page.route("**/api/replay?*", lambda route, *, first=first: route.fulfill(json={
            "first_asof": first*1000, "bar_seconds": 300, "frames": 3, "days": 1, "note": "Export identity fixture"}))
        try:
            page.goto(url + f"/?exchange={exchange}&mode=pair&coin=BTC&range=24h&model=1" + (f"&asof={first}" if case == "replay" else ""))
            page.wait_for_function("state.data !== null")
            if case == "replay":
                page.evaluate("() => { void startReplay(false); }")
                page.wait_for_function("state.replay && state.data.replay")
            page.evaluate("window.holdExports = true")
            if case == "market":
                page.evaluate("() => { state.coin = 'ETH'; state.exchange = 'okx'; state.range = '7d'; persist(); void load('coin'); }")
            else:
                page.locator("#model-settings").evaluate("e => e.open = true")
                page.locator('[data-model="2"]').click()
            page.wait_for_function("window.exportResponses.length > 0")
            assert page.evaluate("state.data.model.id === 1 && state.data.coin === 'BTC'")
            page.evaluate("""() => {
                window.exportDrawnLabels = [];
                const original = CanvasRenderingContext2D.prototype.fillText;
                CanvasRenderingContext2D.prototype.fillText = function(text, ...args) {
                    if (this.canvas !== document.getElementById('chart')) window.exportDrawnLabels.push(text);
                    return original.call(this, text, ...args);
                };
            }""")
            with page.expect_download() as png:
                page.locator('#shot').click()
            expected = f"{exchange}-btc-24h-model1"
            png.value.save_as(output / f"pending-{case}.png")
            assert png.value.suggested_filename == expected + '.png', "PNG filename describes pending selection"
            labels = page.evaluate("window.exportDrawnLabels")
            assert 'Displayed fixture BTC' in labels, "PNG title does not describe displayed payload"
            assert any('Wide clusters scenario' in label for label in labels), "PNG scenario describes pending selection"
            assert not any('Tight magnets scenario' in label for label in labels)
            page.locator('#research-settings').evaluate('e => e.open = true')
            with page.expect_download() as exported:
                page.locator('#export').click()
            target = output / f"pending-{case}.json"
            exported.value.save_as(target)
            assert exported.value.suggested_filename == 'open-liq-' + expected + '-research.json', "JSON filename describes pending selection"
            document = json.loads(target.read_text())
            assert document['payload']['coin'] == 'BTC' and document['payload']['model']['id'] == 1
            displayed_settings = parse_qs(urlparse(document['settings_url']).query)
            assert displayed_settings['model'] == ['1'] and displayed_settings['coin'] == ['BTC']
            assert displayed_settings['exchange'] == [exchange] and displayed_settings['range'] == ['24h']
            assert document['selected_settings_url'] != document['settings_url']
            page.evaluate("window.holdExports = false; window.exportResponses.splice(0).forEach(release => release())")
            page.wait_for_function("state.data === null")
            assert page.evaluate("""() => {
                let downloads = 0;
                downloadFile = () => { downloads += 1; };
                snapshot(); exportResearch();
                return downloads === 0;
            }"""), "Failed selection exports without a displayed dataset"
        except AssertionError as error:
            failures.append(f"{case}: {error}")
        finally:
            page.close()
    assert not failures, '; '.join(failures)


def replay_timeline_regression(context, url: str, demo: dict, output: Path) -> None:
    """Replay starts on the left; later prices and exposure appear only as reached."""
    first = int(demo["as_of"] / 1000)
    page = context.new_page()
    page.set_viewport_size({"width": 1440, "height": 1000})
    def frame(route):
        params = parse_qs(urlparse(route.request.url).query)
        asof = int(params.get("asof", [str(first)])[0])
        offset = max(0, (asof - first) // 300) if params.get("replay") == ["1"] else 0
        payload = dict(demo)
        payload.update(exchange="binance", asof=asof * 1000, replay=params.get("replay") == ["1"],
                       times=[t + offset * 300000 for t in demo["times"]], as_of=demo["as_of"] + offset * 300000)
        route.fulfill(json=payload)
    page.route("**/api/heatmap?*", frame)
    replay_url = "**/api/replay?*"
    page.route(replay_url, lambda route: route.fulfill(status=400, json={"error": "Choose a shorter replay interval"}))
    try:
        page.goto(url + f"/?exchange=binance&mode=pair&coin=BTC&range=24h&model=1&asof={first}")
        page.wait_for_function("state.data !== null")
        page.evaluate("() => { void startReplay(false); }")
        page.wait_for_function("document.getElementById('banner').textContent.includes('Choose a shorter replay interval')")
        assert page.evaluate("state.data !== null"), "Replay errors must leave the current map visible"
        page.unroute(replay_url)
        page.route(replay_url, lambda route: route.fulfill(json={
            "first_asof": first * 1000, "bar_seconds": 300, "frames": 51, "days": 1, "note": "Offline timeline fixture"}))
        page.evaluate("() => { void startReplay(false); }")
        page.wait_for_function("state.replay && state.data.replay && state.replay.anchorN && state.layout")
        position = "(xOf(replayOffset(state.data) + state.data.times.length - 1, state.layout) - state.layout.plotX) / state.layout.plotW"
        initial = page.evaluate(position)
        assert initial < .3, f"Replay started too far right: {initial}"
        assert page.evaluate("state.view.i1 > replayOffset(state.data) + state.data.times.length + 10"), "Future time should initially be blank"
        assert "empty area is future time" in page.locator("#banner").inner_text()
        page.evaluate("() => {const rect=canvas.getBoundingClientRect(); updateHover(rect.left+state.layout.plotX+state.layout.plotW*.9, rect.top+state.layout.plotY+state.layout.plotH*.5)}")
        assert page.evaluate("tip.hidden"), "Unreached future must not show a fabricated value"
        page.screenshot(path=str(output / "replay-start.png"), full_page=True)
        page.evaluate("() => { void showFrame(10); }")
        page.wait_for_function(f"state.data.asof === {(first + 3000) * 1000}")
        advanced = page.evaluate(position)
        assert advanced > initial + .1, (initial, advanced)
        page.screenshot(path=str(output / "replay-advanced.png"), full_page=True)
        page.evaluate("() => { void showFrame(0); }")
        page.wait_for_function(f"state.data.asof === {first * 1000}")
        assert abs(page.evaluate(position) - initial) < .01, "Scrubbing backward should restore the timeline"
        page.evaluate("() => { void showFrame(50); }")
        page.wait_for_function(f"state.data.asof === {(first + 15000) * 1000}")
        assert 0 < page.evaluate(position) < 1, "Playback should follow the latest reached bar"
    finally:
        page.close()


def chip_coins(page) -> list[str]:
    return page.locator("#watch-list .chip-main").evaluate_all("els => els.map(e => e.dataset.coin)")


def trader_ux_checks(browser, url: str, output: Path) -> None:
    """Venues, reading line, market strip, observed liquidations, watchlist,
    alerts and disclaimers on the real demo endpoints, in a fresh profile."""
    context = offline_context(browser, viewport={"width": 1440, "height": 1000}, timezone_id="UTC")
    page = context.new_page()
    errors: list[str] = []
    log: list[tuple[str, str]] = []
    side_urls: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    def path_of(request) -> str:
        return urlparse(request.url).path.replace("/api/v1/", "/api/")
    def on_request(request) -> None:
        log.append(("start", path_of(request)))
        if "/api/v1/" in request.url:
            side_urls.append(request.url)
    page.on("request", on_request)
    page.on("requestfinished", lambda request: log.append(("done", path_of(request))))
    try:
        page.goto(url + "/?exchange=demo&coin=BTC&range=24h&model=1&tz=utc")
        page.wait_for_function("state.data !== null")
        # Venue choices: Bybit and a selectable All venues.
        options = page.locator("#exchange option").evaluate_all("els => els.map(e => [e.value, e.textContent.trim(), e.disabled || e.hidden])")
        venues = {value: (label, off) for value, label, off in options}
        assert {"okx", "binance", "bybit", "aggregate", "demo"} <= set(venues), options
        assert venues["bybit"] == ("Bybit", False) and venues["aggregate"] == ("All venues", False), options
        assert page.evaluate("state.auto === 60 && document.getElementById('auto').value === '60'"), "Auto-refresh defaults to 1m"
        # Side requests wait for the map; the watchlist waits for both side requests.
        page.wait_for_function("document.querySelectorAll('#watch-list .chip-main').length === 3 && !document.querySelector('.chip-main[data-coin=ETH]').textContent.includes('—')")
        order = [entry for entry in log if entry[1] in ("/api/heatmap", "/api/context", "/api/liquidations", "/api/watchlist")]
        first_done = order.index(("done", "/api/heatmap"))
        assert order.index(("start", "/api/context")) > first_done, order
        assert order.index(("start", "/api/liquidations")) > first_done, order
        watch_start = order.index(("start", "/api/watchlist"))
        assert watch_start > order.index(("done", "/api/context")) and watch_start > order.index(("done", "/api/liquidations")), order
        # Reading line straight from the demo analysis.
        analysis = page.evaluate("state.data.analysis")
        short_text = page.locator("#read-short").inner_text()
        long_text = page.locator("#read-long").inner_text()
        assert "Nearest short liq cluster" in short_text and f"{analysis['above']['price']:,.0f}" in short_text, short_text
        assert f"{abs(analysis['above']['distance_pct']):.2f}%" in short_text, short_text
        assert "Nearest long liq cluster" in long_text and f"{analysis['below']['price']:,.0f}" in long_text, long_text
        ratio = next(row for row in analysis["imbalance"] if row["window_pct"] == 5)["short_to_long"]
        assert f"{ratio:.2f}×" in page.locator("#read-ratio").inner_text()
        page.locator("#read-long").click()
        assert page.evaluate(f"state.priceDirty && state.view.lo < {analysis['below']['price']} && state.view.hi > {analysis['below']['price']}"), "Reading line focuses its price"
        page.locator("#reset").click()
        # Market strip: window change from the map, funding from the demo context.
        page.wait_for_function("!document.getElementById('mk-fund').hidden")
        market = page.evaluate("state.data.market_context")
        assert f"{abs(market['price_change_pct']):.2f}%" in page.locator("#mk-chg").inner_text()
        assert "/8h" in page.locator("#mk-fund").inner_text()
        assert re.fullmatch(r"\d\d:\d\d:\d\d", page.locator("#mk-fund-t").inner_text()), page.locator("#mk-fund-t").inner_text()
        # Synthetic demo liquidations sit inside the drawn bars, and the toggle removes them.
        page.wait_for_function("!document.getElementById('legend-liq').hidden")
        assert "Synthetic demo liquidations" in page.locator("#liq-note").inner_text()
        assert page.evaluate("liqEvents(state.data).length > 0 && liqEvents(state.data).every(e => { const i = liqIndex(e.ts, state.data); return i >= -0.5 && i <= state.data.times.length - 0.5; })")
        next_frame(page)
        page.evaluate("""() => { const L = state.layout; window.liqProbe = liqEvents(state.data).map(e => [xOf(liqIndex(e.ts, state.data), L), yOf(e.price, L)])
            .filter(([x, y]) => x > L.plotX + 2 && x < L.plotX + L.plotW - 2 && y > L.plotY + 2 && y < L.plotY + L.plotH - 2).slice(0, 12); }""")
        sample = "() => { const c = canvas.getContext('2d'); return window.liqProbe.map(([x, y]) => Array.from(c.getImageData(Math.round(x * state.dpr), Math.round(y * state.dpr), 1, 1).data).join()); }"
        with_dots = page.evaluate(sample)
        page.locator("#display-settings > summary").click()
        page.locator("#liqs").uncheck()
        next_frame(page)
        without = page.evaluate(sample)
        assert len(with_dots) >= 3 and sum(a != b for a, b in zip(with_dots, without, strict=True)) >= len(with_dots) // 2, "Dots are drawn at their time and price"
        assert page.locator("#legend-liq").is_hidden() and page.locator("#liq-note").is_hidden()
        page.locator("#liqs").check()
        page.wait_for_function("!document.getElementById('legend-liq').hidden")
        # Price-proximity alert: the demo short cluster is within 0.5%, and alerts once.
        page.locator("#alert-pct").fill("0.5")
        page.locator("#alert-on").check()
        page.wait_for_selector("#toast:not([hidden])")
        assert "near short liq cluster" in page.locator("#toast-text").inner_text()
        page.keyboard.press("Escape")
        assert page.locator("#toast").is_hidden()
        generated = page.evaluate("state.data.generated_at")
        page.locator("#refresh").click()
        page.wait_for_function(f"state.data && state.data.generated_at !== {generated}")
        page.wait_for_timeout(200)
        assert page.locator("#toast").is_hidden(), "The same cluster must not alert again on refresh"
        page.locator("#alert-on").uncheck()
        page.keyboard.press("Escape")
        # Watchlist: click switches the chart, add/remove persist, keyboard removal works.
        assert chip_coins(page) == ["BTC", "ETH", "SOL"]
        page.locator(".chip-main[data-coin=ETH]").click()
        page.wait_for_function("state.data && state.data.coin === 'ETH'")
        assert page.evaluate("state.coin === 'ETH' && document.getElementById('coin').value === 'ETH'")
        assert page.locator(".chip.on .chip-main").get_attribute("data-coin") == "ETH"
        assert page.locator("#watch-add").is_hidden()
        page.locator("#coin").fill("XRP")
        page.locator("#coin").press("Enter")
        page.wait_for_function("state.data && state.data.coin === 'XRP'")
        assert page.locator("#watch-add").inner_text() == "+ Add XRP"
        page.locator("#watch-add").click()
        assert chip_coins(page) == ["BTC", "ETH", "SOL", "XRP"]
        assert page.evaluate("document.activeElement.dataset.coin") == "XRP", "Focus moves to the added chip"
        assert page.evaluate("JSON.parse(localStorage.getItem(STORE)).watchlist.join()") == "BTC,ETH,SOL,XRP"
        page.keyboard.press("Delete")
        assert chip_coins(page) == ["BTC", "ETH", "SOL"]
        assert page.evaluate("document.activeElement.classList.contains('chip-main')"), "Focus stays in the watchlist"
        page.locator(".chip-x[data-coin=SOL]").click()
        assert chip_coins(page) == ["BTC", "ETH"]
        assert page.evaluate("""() => { const saved = state.watchlist.slice(); state.watchlist = ['ETH', 'SOL', 'XRP', 'DOGE', 'BNB', 'ADA']; renderWatch();
            const full = document.getElementById('watch-add').hidden; state.watchlist = saved; renderWatch(); return full; }"""), "Watchlist holds at most six"
        page.reload()
        page.wait_for_function("state.data !== null")
        assert chip_coins(page) == ["BTC", "ETH"], "Watchlist persists"
        page.locator(".chip-main[data-coin=BTC]").click()
        page.wait_for_function("state.data && state.data.coin === 'BTC'")
        for request_url in side_urls:
            params = parse_qs(urlparse(request_url).query)
            assert "asof" not in params and "replay" not in params and params["exchange"] == ["demo"], request_url
            if "/watchlist" in request_url:
                assert 1 <= len(params["symbols"][0].split(",")) <= 6, request_url
        # All venues is a real choice: aggregate mode, kept in the link.
        page.locator("#exchange").select_option("aggregate")
        assert page.evaluate("state.exchange === 'aggregate' && state.mode === 'symbol'")
        assert "mode=symbol" in page.url and "exchange=aggregate" in page.url
        page.wait_for_function("document.getElementById('loading').hidden")
        page.locator("#exchange").select_option("bybit")
        assert page.evaluate("state.exchange === 'bybit' && state.mode === 'pair'")
        page.wait_for_function("document.getElementById('loading').hidden")
        page.locator("#exchange").select_option("demo")
        page.wait_for_function("state.data && state.data.exchange === 'demo' && state.data.mode === 'pair'")
        # One concise disclaimer, a trader caption and closed provenance.
        assert "not exchange position records or financial advice" in page.locator("footer").inner_text()
        assert "Above price: short liquidation clusters" in page.locator(".caption").inner_text()
        assert page.locator("#source-note").evaluate("e => e.tagName === 'DETAILS' && !e.open")
        assert "Synthetic" in page.locator("#provenance").text_content()
        assert page.locator(".cards, #above, #below").count() == 0, "Nearest clusters show once, in the reading line"
        assert page.locator("#data-status").inner_text() == "Synthetic demo"
        page.locator("#how-link").click()
        assert page.locator("#how").evaluate("e => e.open"), "Footer link opens the model notes"
        # Replay panel: presets and the archive note.
        page.evaluate("scrollTo(0, 0)")
        page.locator("#replay-settings > summary").click()
        presets = page.locator("#replay-settings .preset")
        assert presets.evaluate_all("els => els.map(e => e.textContent.trim())") == ["Last 24h", "Last 3 days", "Last 7 days"]
        assert all(presets.nth(index).is_visible() for index in range(3))
        assert "Binance's public archive" in page.locator("#replay-settings .settings-intro").inner_text()
        page.keyboard.press("Escape")
        page.screenshot(path=str(output / "trader-desktop.png"))
        assert not errors, errors
    finally:
        context.close()


def replay_preset_regression(context, url: str, demo: dict) -> None:
    """A preset moves to Binance, asks for the right span and plays it; side data stays live-only."""
    first = int(demo["as_of"] / 1000)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    replay_requests: list[dict] = []
    side_urls: list[str] = []
    page.on("request", lambda request: side_urls.append(request.url) if "/api/v1/" in request.url else None)
    def heatmap(route):
        params = parse_qs(urlparse(route.request.url).query)
        payload = dict(demo)
        if params.get("exchange") == ["binance"]:
            payload["exchange"] = "binance"
            payload["asof"] = int(params["asof"][0]) * 1000 if "asof" in params else None
            payload["replay"] = params.get("replay") == ["1"]
        route.fulfill(json=payload)
    def replay(route):
        replay_requests.append({key: values[0] for key, values in parse_qs(urlparse(route.request.url).query).items()})
        route.fulfill(json={"first_asof": first * 1000, "last_asof": (first + 600) * 1000, "bar_seconds": 300, "frames": 3, "days": 1, "note": "Offline preset fixture"})
    page.route("**/api/heatmap?*", heatmap)
    page.route("**/api/replay?*", replay)
    try:
        page.goto(url + "/?exchange=demo&coin=BTC&range=24h&model=1")
        page.wait_for_function("state.data !== null && document.getElementById('mk-fund') && !document.getElementById('legend-liq').hidden")
        page.locator("#replay-settings > summary").click()
        page.locator("#replay-settings .preset", has_text="Last 3 days").click()
        page.wait_for_function("state.replay && state.data.replay && state.replay.idx === 2 && !state.replay.playing")
        assert page.evaluate("state.exchange === 'binance' && state.mode === 'pair' && document.getElementById('exchange').value === 'binance'")
        assert len(replay_requests) == 1, replay_requests
        request = replay_requests[0]
        # Presets ask the server for the newest replayable span instead of assuming the archive reaches now.
        assert request.get("span") == "259200" and "from" not in request and "to" not in request, request
        assert request.get("bar") == "15m", request
        assert page.evaluate(f"state.asof === {first}"), "As of shows the span the server chose"
        assert not page.locator("#replay-settings").evaluate("e => e.open"), "The panel closes so the chart is visible"
        assert page.locator("#replay-bar").is_visible() and "Replay" in page.locator("#replay-bar").text_content()
        assert page.locator("#legend-liq").is_hidden() and page.locator("#mk-fund").is_hidden(), "Live-only overlays hide during replay"
        # Auto-refresh must not fire while a replay is active.
        seq = page.evaluate("state.seq")
        page.evaluate("setAuto(0.05)")
        page.wait_for_timeout(300)
        assert page.evaluate(f"state.seq === {seq} && state.replay !== null"), "Auto-refresh fired during a replay"
        page.evaluate("setAuto(0)")
        page.locator("#rb-live").click()
        page.wait_for_function("state.replay === null && state.data && state.data.asof === null")
        assert page.locator("#replay-bar").is_hidden()
        for request_url in side_urls:
            params = parse_qs(urlparse(request_url).query)
            assert "asof" not in params and "replay" not in params, request_url
        assert not errors, errors
    finally:
        page.close()


def auto_refresh_guard(context, url: str, demo: dict) -> None:
    """Auto-refresh waits while a replay span is still loading."""
    first = int(demo["as_of"] / 1000)
    page = context.new_page()
    page.add_init_script("""
        const originalFetch = window.fetch;
        window.heldSpans = [];
        window.fetch = async (...args) => {
            const response = await originalFetch(...args);
            if (window.holdSpan && new URL(args[0], location.href).pathname === '/api/replay')
                return new Promise(resolve => window.heldSpans.push(() => resolve(response)));
            return response;
        };
    """)
    def heatmap(route):
        params = parse_qs(urlparse(route.request.url).query)
        payload = dict(demo)
        payload.update(exchange="binance", asof=int(params["asof"][0]) * 1000 if "asof" in params else None, replay=params.get("replay") == ["1"])
        route.fulfill(json=payload)
    page.route("**/api/heatmap?*", heatmap)
    page.route("**/api/replay?*", lambda route: route.fulfill(json={"first_asof": first * 1000, "bar_seconds": 300, "frames": 3, "days": 1, "note": "Guard fixture"}))
    try:
        page.goto(url + "/?exchange=binance&mode=pair&coin=BTC&range=24h&model=1")
        page.wait_for_function("state.data !== null")
        page.evaluate(f"() => {{ window.holdSpan = true; state.asof = {first}; void startReplay(false); }}")
        page.wait_for_function("window.heldSpans.length === 1 && state.replayController !== null")
        seq = page.evaluate("state.seq")
        page.evaluate("setAuto(0.05)")
        page.wait_for_timeout(300)
        assert page.evaluate(f"state.seq === {seq} && state.replayController !== null"), "Auto-refresh fired while a replay span loaded"
        page.evaluate("setAuto(0); window.heldSpans.shift()()")
        page.wait_for_function("state.replay && state.data.replay")
    finally:
        page.close()


def background_refresh_checks(browser, url: str) -> None:
    """Hidden tabs refresh only while alerts are on; a stale tab refreshes on return; quiet refreshes stay quiet."""
    context = offline_context(browser, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.add_init_script("Object.defineProperty(document, 'hidden', {configurable: true, get: () => Boolean(window.fakeHidden)})")
    show = "() => { window.fakeHidden = false; document.dispatchEvent(new Event('visibilitychange')); return state.seq; }"
    try:
        page.goto(url + "/?exchange=demo&coin=BTC&range=24h&model=1")
        page.wait_for_function("state.data !== null && document.getElementById('loading').hidden")
        seq = page.evaluate("() => { window.fakeHidden = true; setAuto(0.1); return state.seq; }")
        page.wait_for_timeout(400)
        assert page.evaluate("state.seq") == seq, "A hidden tab without alerts must not auto-refresh"
        page.evaluate("state.alertOn = true")
        page.wait_for_function(f"state.seq > {seq}", timeout=3000)
        seq = page.evaluate("() => { state.alertOn = false; return state.seq; }")
        page.wait_for_timeout(400)
        assert page.evaluate(show) == seq + 1, "Returning to a view older than the auto interval refreshes at once"
        page.evaluate("setAuto(60)")
        page.wait_for_function("document.getElementById('loading').hidden")
        seq = page.evaluate("state.seq")
        assert page.evaluate(show) == seq, "A fresh view does not reload on return"
        page.evaluate("setAuto(0)")
        # Screen readers hear user loads and status changes, not every quiet auto refresh.
        assert page.locator("#data-status").get_attribute("aria-live") is None and page.locator("#data-status").get_attribute("role") is None
        live = {"stale": False}
        page.route("**/api/heatmap?*", lambda route: route.fulfill(json={**route.fetch().json(), "source": "okx", "stale": live["stale"]}))
        said = "() => document.getElementById('announcement').textContent"
        page.evaluate("load('refresh')")
        assert page.evaluate(said).startswith("Loaded"), page.evaluate(said)
        page.evaluate("document.getElementById('announcement').textContent = 'quiet'")
        page.evaluate("load('auto')")
        assert page.evaluate(said) == "quiet", "Quiet auto refresh announced: " + page.evaluate(said)
        live["stale"] = True
        page.evaluate("load('auto')")
        assert "Stale source" in page.evaluate(said), "Becoming stale must be announced"
        # A failed quiet refresh keeps the map on screen and warns instead.
        page.unroute("**/api/heatmap?*")
        page.route("**/api/heatmap?*", lambda route: route.fulfill(status=503, json={"error": "Fixture source is down."}))
        page.evaluate("load('auto')")
        assert page.evaluate("state.data !== null && state.heatCanvas !== null"), "A failed auto refresh wiped the map"
        banner = page.locator("#banner")
        assert "warn" in banner.get_attribute("class") and "Refresh failed: Fixture source is down" in banner.inner_text(), banner.inner_text()
    finally:
        context.close()


def coin_input_checks(browser, url: str) -> None:
    """The asset box reads tickers like the server; + Add only offers a loaded, valid coin."""
    context = offline_context(browser, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    try:
        page.goto(url + "/?exchange=demo&coin=solusdt&range=24h&model=1")
        page.wait_for_function("state.data !== null")
        assert page.evaluate("state.coin === 'SOL' && state.data.coin === 'SOL'"), "URL coin is normalized"
        page.locator("#coin").fill("xrp-usdt")
        page.locator("#coin").press("Enter")
        page.wait_for_function("state.data && state.data.coin === 'XRP' && document.getElementById('loading').hidden")
        assert page.evaluate("state.coin === 'XRP' && document.getElementById('coin').value === 'XRP'"), page.evaluate("state.coin")
        assert page.locator("#watch-add").inner_text() == "+ Add XRP"
        assert "coin=XRP" in page.url
        page.locator("#coin").fill("ethusdt")
        page.locator("#coin").press("Enter")
        page.wait_for_function("state.data && state.data.coin === 'ETH' && document.getElementById('loading').hidden")
        assert page.locator(".chip.on .chip-main").get_attribute("data-coin") == "ETH", "ETHUSDT highlights the ETH chip"
        assert page.locator("#watch-add").is_hidden(), "ETH is already watched: no + Add"
        page.locator("#coin").fill("ABCDEFGHIJKLMNOPQ")
        page.locator("#coin").press("Enter")
        page.wait_for_function("document.getElementById('loading').hidden")
        assert page.evaluate("state.coin === 'ETH' && state.data && state.data.coin === 'ETH'"), "An invalid ticker is rejected in the browser"
        assert page.locator("#watch-add").is_hidden() and page.locator("#coin").input_value() == "ETH"
        assert "1–16 letters or digits" in page.locator("#banner").inner_text()
        page.route("**/api/heatmap?*", lambda route: route.fulfill(status=503, json={"error": "Held coin failed deterministically"}))
        page.locator("#coin").fill("DOGE")
        page.locator("#coin").press("Enter")
        page.wait_for_function("state.data === null && document.getElementById('loading').hidden")
        assert page.locator("#watch-add").is_hidden(), "A failed coin is not offered for the watchlist"
        page.unroute("**/api/heatmap?*")
        page.evaluate("localStorage.setItem(STORE, JSON.stringify({coin: 'BTC', watchlist: ['BTC', 'ethusdt', 'ABCDEFGHIJKLMNOPQ', 'USDT', 'B@D', 'ETH', 'sol/usdt']}))")
        page.goto(url + "/?exchange=demo&range=24h&model=1")
        page.wait_for_function("state.data !== null")
        assert chip_coins(page) == ["BTC", "ETH", "SOL"], chip_coins(page)
    finally:
        context.close()


def venue_side_checks(browser, url: str, demo: dict) -> None:
    """No liquidation request where the server has none; funding names its venue coverage."""
    context = offline_context(browser, viewport={"width": 1440, "height": 1000}, timezone_id="UTC")
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    liq_requests: list[str] = []
    page.on("request", lambda request: liq_requests.append(parse_qs(urlparse(request.url).query)["exchange"][0]) if "/api/v1/liquidations" in request.url else None)
    sources = {"okx": "okx", "binance": "binance-live", "bybit": "bybit", "aggregate": "aggregate"}
    def heatmap(route):
        params = parse_qs(urlparse(route.request.url).query)
        exchange = params["exchange"][0]
        route.fulfill(json={**demo, "exchange": exchange, "mode": params["mode"][0], "source": sources[exchange], "title": f"{exchange} fixture"})
    now = int(time.time() * 1000)
    page.route("**/api/heatmap?*", heatmap)
    page.route("**/api/v1/context?*", lambda route: route.fulfill(json={
        "venues": [
            {"exchange": "okx", "ok": True, "funding_rate": 0.0001, "funding_interval_hours": 8, "next_funding_time": now + 3600000},
            {"exchange": "binance", "ok": False, "error": "Offline fixture"},
            {"exchange": "bybit", "ok": True, "funding_rate": None},
        ],
        "funding_rate_oi_weighted": 0.0001, "funding_basis_hours": 8}))
    note = "document.getElementById('liq-note').textContent.includes('come from OKX')"
    try:
        page.goto(url + "/?exchange=bybit&coin=BTC&range=24h&model=1")
        page.wait_for_function(f"state.data && state.data.exchange === 'bybit' && {note}")
        assert page.locator("#asof").inner_text().startswith("Bybit live ·"), page.locator("#asof").inner_text()
        page.locator("#exchange").select_option("binance")
        page.wait_for_function(f"state.data && state.data.exchange === 'binance' && {note}")
        page.route("**/api/v1/liquidations?*", lambda route: route.fulfill(status=404, json={"error": "Not found."}))
        page.locator("#exchange").select_option("okx")
        page.wait_for_function("state.data && state.data.exchange === 'okx' && document.getElementById('liq-note').textContent.includes('unavailable right now')")
        page.locator("#exchange").select_option("aggregate")
        page.wait_for_function("state.data && state.data.mode === 'symbol' && !document.getElementById('mk-fund').hidden")
        assert page.locator("#mk-fund-k").inner_text().lower() == "funding · oi-wtd · 1/3 venues", page.locator("#mk-fund-k").inner_text()
        page.set_viewport_size({"width": 320, "height": 700})
        page.wait_for_timeout(150)
        assert page.evaluate("document.getElementById('mk-fund').getBoundingClientRect().right <= document.getElementById('market').getBoundingClientRect().right + .5"), "The longer funding pill fits a phone"
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_function("document.getElementById('loading').hidden")
        assert liq_requests == ["okx", "aggregate"], liq_requests
        assert page.evaluate("() => { state.fundingNext = Date.now() - 1000; tickFunding(); return document.getElementById('mk-fund-t').textContent; }") == "settling"
        assert not errors, errors
    finally:
        context.close()


def run(url: str, output: Path, chromium: str | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"], executable_path=chromium or None)
        context = offline_context(browser, viewport={"width": 1440, "height": 1000}, accept_downloads=True, timezone_id="America/New_York")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url + "/?exchange=demo&coin=BTC&range=24h&model=1&tz=utc")
        assert page.locator("#exchange").input_value() == "demo", "Shared URL must select offline demo"
        page.wait_for_function("state.data !== null")
        assert "Synthetic" in page.locator("#data-status").inner_text()
        assert page.locator("#timezone").input_value() == "utc"
        page.locator("#model-settings").evaluate("e => e.open = true")
        for model in (2, 3, 1):
            page.locator(f'[data-model="{model}"]').click()
            page.wait_for_function(f"state.data && state.data.model.id === {model}")
        page.locator("#model-settings").evaluate("e => e.open = false")
        page.locator("#display-settings").evaluate("e => e.open = true")
        page.locator("#liqmap").check()
        page.locator("#candles").uncheck()
        assert page.evaluate("state.showMap && !state.showCandles")
        page.locator("#timezone").select_option("local")
        assert "UTC" not in page.locator("#timezone-note").inner_text() or page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone === 'UTC'")
        page.locator("#liqmap").uncheck()
        page.locator("#candles").check()
        page.locator("#timezone").select_option("utc")
        assert page.locator("#auto").input_value() == "60", "Live views refresh every minute by default"
        page.locator("#auto").select_option("0")
        page.locator("#display-settings").evaluate("e => e.open = false")
        original = page.evaluate("state.view.hi - state.view.lo")
        page.locator("#chart").focus()
        page.keyboard.press("+")
        assert page.evaluate("state.view.hi - state.view.lo") < original
        page.keyboard.press("0")
        assert abs(page.evaluate("state.view.hi - state.view.lo") - original) < .01
        with page.expect_download() as download:
            page.locator("#shot").click()
        download.value.save_as(output / "snapshot.png")
        assert (output / "snapshot.png").stat().st_size > 1000
        page.locator("#full").click()
        page.wait_for_function("document.fullscreenElement !== null")
        page.evaluate("document.exitFullscreen()")
        page.wait_for_function("document.fullscreenElement === null")
        page.locator("#research-settings > summary").focus()
        page.keyboard.press("Enter")
        with page.expect_download() as research_download:
            page.locator("#export").click()
        research_download.value.save_as(output / "research.json")
        exported = json.loads((output / "research.json").read_text())
        assert exported["payload"]["source"] == "demo"
        assert "generated_at" in exported["payload"]
        assert "model_version" in exported["payload"]
        assert "data_fingerprint" in exported["payload"]
        page.locator("#share").click()
        assert "exchange=demo" in page.url and "model=1" in page.url
        page.keyboard.press("Escape")
        assert not page.locator("#research-settings").evaluate("e => e.open")
        page.reload()
        page.wait_for_function("state.data !== null")
        assert page.evaluate("state.auto === 0"), "A stored auto-refresh choice wins over the default"
        page.route("**/api/heatmap?*", lambda route: route.fulfill(status=503, json={"error": "Demo source temporarily unavailable. Refresh to retry."}))
        page.locator("#refresh").click()
        page.wait_for_function("state.data === null && document.getElementById('loading').hidden")
        assert "temporarily unavailable" in page.locator("#banner").inner_text()
        assert "unavailable" in page.locator("#data-status").inner_text().lower()
        page.unroute("**/api/heatmap?*")
        page.locator("#refresh").click()
        page.wait_for_function("state.data !== null")
        assert not errors, errors
        for width, height, name in ((1440, 1000, "desktop"), (1280, 900, "desktop-1280"), (1920, 1080, "desktop-1920"), (768, 1024, "tablet"), (390, 844, "mobile-390"), (360, 800, "mobile-360"), (320, 700, "mobile-320")):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(150)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Horizontal overflow at {width}"
            if width < 400:
                assert page.locator("#chart").bounding_box()["y"] <= 300
                # Both cluster chips show in full; the ±5% ratio is either whole or hidden.
                clipped = page.evaluate("""() => { const host = document.getElementById('reading').getBoundingClientRect();
                    const parts = ['#read-short', '#read-short .rl-u', '#read-long', '#read-long .rl-u', '#read-ratio'].map(s => document.querySelector(s));
                    return parts.filter(e => e.getClientRects().length).filter(e => { const b = e.getBoundingClientRect(); const box = e.closest('.read-btn') && e.closest('.read-btn') !== e ? e.closest('.read-btn').getBoundingClientRect() : host;
                        return b.left < box.left - .5 || b.right > box.right + .5; }).map(e => e.id || e.className); }""")
                assert not clipped, (width, clipped)
                for selector in ("#coin", "#exchange", "#range", "#refresh", "#zoom-in", "#zoom-out", "#reset"):
                    box = page.locator(selector).bounding_box()
                    assert box["height"] >= 44, (selector, box)
                assert page.locator("#chart").evaluate("e => getComputedStyle(e).touchAction") == "pan-y"
                for selector in (".chip-main", ".chip-x", "#read-short", "#read-long"):
                    box = page.locator(selector).first.bounding_box()
                    assert box["height"] >= 44 and box["width"] >= 44, (selector, box)
                assert page.locator("#watch").bounding_box()["y"] > page.locator("#chart").bounding_box()["y"], "Phones show the watchlist below the chart"
                assert page.locator(".watch-scroll").evaluate("e => e.getBoundingClientRect().right <= innerWidth"), "Watchlist scrolls inside its own strip"
                for selector in ("#model-settings", "#display-settings", "#research-settings", "#replay-settings"):
                    page.locator(selector + " > summary").click()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Panel overflow at {width}: {selector}"
                    page.keyboard.press("Escape")
            page.evaluate("document.activeElement.blur()")
            page.screenshot(path=str(output / f"{name}.png"), full_page=True)
        page.set_viewport_size({"width": 640, "height": 450})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "200% desktop zoom equivalent"
        # The replay protocol is exercised offline with real demo frames. No archive download.
        demo = page.request.get(url + "/api/heatmap?exchange=demo&symbol=BTC&range=24h&model=1").json()
        replay_regressions(context, url, demo)
        replay_timeline_regression(context, url, demo, output)
        export_identity_regressions(context, url, demo, output)
        replay_preset_regression(context, url, demo)
        auto_refresh_guard(context, url, demo)
        trader_ux_checks(browser, url, output)
        background_refresh_checks(browser, url)
        coin_input_checks(browser, url)
        venue_side_checks(browser, url, demo)
        replay_page = context.new_page()
        replay_page.on("pageerror", lambda error: errors.append(str(error)))
        first = int(demo["as_of"] / 1000)
        def replay_heatmap(route):
            params = parse_qs(urlparse(route.request.url).query)
            payload = dict(demo)
            payload["exchange"] = "binance"
            payload["asof"] = int(params.get("asof", [first])[0]) * 1000
            payload["replay"] = params.get("replay") == ["1"]
            route.fulfill(json=payload)
        replay_page.route("**/api/heatmap?*", replay_heatmap)
        replay_page.route("**/api/replay?*", lambda route: route.fulfill(json={
            "first_asof": first * 1000, "bar_seconds": 300, "frames": 3,
            "days": 1, "note": "Offline replay protocol fixture",
        }))
        replay_page.goto(url + f"/?exchange=binance&mode=pair&coin=BTC&range=24h&model=1&asof={first}")
        replay_page.wait_for_function("state.data !== null")
        replay_page.locator("#replay-settings > summary").click()
        assert replay_page.locator("#history-controls").is_visible()
        replay_page.locator("#rp-load").click()
        replay_page.wait_for_function("state.replay && state.data.replay")
        replay_page.locator("#rp-fwd").click()
        replay_page.wait_for_function("state.replay.idx === 1")
        replay_page.locator("#rp-back").click()
        replay_page.wait_for_function("state.replay.idx === 0")
        replay_page.locator("#rp-speed").select_option("10")
        replay_page.locator("#rp-play").click()
        replay_page.wait_for_function("state.replay.idx === 2 && !state.replay.playing")
        replay_page.locator("#rp-scrub").fill("1")
        replay_page.wait_for_function("state.replay.idx === 1")
        assert not errors, errors
        mobile = offline_context(browser, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        touch_page = mobile.new_page()
        touch_page.goto(url + "/?exchange=demo&mode=pair&coin=BTC&range=24h&model=1")
        touch_page.wait_for_function("state.data !== null")
        touch_page.locator("#zoom-axis").select_option("time")
        touch_page.locator("#zoom-in").tap()
        touch_page.evaluate("scrollTo(0, 0)")
        initial = touch_page.evaluate("state.view.i0")
        cdp = mobile.new_cdp_session(touch_page)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 170, "y": 460}]})
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 230, "y": 460}]})
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        assert touch_page.evaluate("state.view.i0") != initial, "Touch swipe must pan zoomed time range"
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 190, "y": 550}]})
        for y in (500, 440, 370, 300):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 190, "y": y}]})
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        touch_page.wait_for_function("scrollY > 0")
        mobile.close()
        browser.close()
    print(f"Browser smoke passed; captures: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--output", type=Path, default=None, help="Capture folder (default: a new temporary directory)")
    parser.add_argument("--chromium", default=None, help="Optional Chromium executable path")
    args = parser.parse_args()
    run(args.url.rstrip("/"), args.output or Path(tempfile.mkdtemp(prefix="open-liq-smoke-")), args.chromium)
