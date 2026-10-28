"""Offline browser acceptance. Start python -m liqmap.server, then run this file.

Uses only the deterministic demo API. --url and --output support CI/local runs.
Requires Playwright and Chromium (python -m playwright install chromium).
"""
from __future__ import annotations

import argparse
import json
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from playwright.sync_api import sync_playwright


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
        page.route("**/api/replay?*", lambda route: route.fulfill(status=503, json={"error": "Expired old replay context"}) if case == "live cancels error" else route.fulfill(json={
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
                page.locator("#research-settings > summary").click()
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
        def payload_route(route):
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
        page.route("**/api/replay?*", lambda route: route.fulfill(json={
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


def run(url: str, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True, timezone_id="America/New_York")
        def offline_only(route):
            exchange = parse_qs(urlparse(route.request.url).query).get("exchange", [""])[0]
            if exchange != "demo":
                route.fulfill(status=400, json={"error": "Browser smoke permits only offline demo data"})
            else:
                route.continue_()
        context.route("**/api/heatmap?*", offline_only)
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
        page.route("**/api/heatmap?*", lambda route: route.fulfill(status=503, json={"error": "Demo source temporarily unavailable. Refresh to retry."}))
        page.locator("#refresh").click()
        page.wait_for_function("state.data === null && document.getElementById('loading').hidden")
        assert "temporarily unavailable" in page.locator("#banner").inner_text()
        assert "unavailable" in page.locator("#data-status").inner_text().lower()
        page.unroute("**/api/heatmap?*")
        page.locator("#refresh").click()
        page.wait_for_function("state.data !== null")
        assert not errors, errors
        for width, height, name in ((1440, 1000, "desktop"), (1280, 900, "desktop-1280"), (1920, 1080, "desktop-1920"), (768, 1024, "tablet"), (390, 844, "mobile-390"), (360, 800, "mobile-360")):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(150)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Horizontal overflow at {width}"
            if width < 400:
                assert page.locator("#chart").bounding_box()["y"] <= 300
                for selector in ("#coin", "#exchange", "#range", "#refresh", "#zoom-in", "#zoom-out", "#reset"):
                    box = page.locator(selector).bounding_box()
                    assert box["height"] >= 44, (selector, box)
                assert page.locator("#chart").evaluate("e => getComputedStyle(e).touchAction") == "pan-y"
                for selector in ("#model-settings", "#display-settings", "#research-settings"):
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
        export_identity_regressions(context, url, demo, output)
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
        replay_page.locator("#research-settings > summary").click()
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
        mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        mobile.route("**/api/heatmap?*", offline_only)
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
    parser.add_argument("--output", type=Path, default=Path(".superpowers/sdd/2026-10-28-product-excellence/screenshots"))
    args = parser.parse_args()
    run(args.url.rstrip("/"), args.output)
