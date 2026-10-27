// Browser smoke test for the page. Run by hand where Playwright is installed:
//
//   LIQMAP_PYTHON=.venv/bin/python node tests/ui/smoke.cjs
//
// It starts `python -m liqmap.server` on a free port, drives the page at desk
// and phone width on the synthetic demo exchange, and saves the README
// screenshots to docs/img/hero.png and docs/img/phone.png. It needs no network:
// requests for other venues are answered with the demo map, because this test
// checks the page, not the exchanges.

const { chromium } = require("playwright");
const { spawn } = require("child_process");
const net = require("net");
const path = require("path");
const fs = require("fs");

const ROOT = path.resolve(__dirname, "..", "..");
const IMG = path.join(ROOT, "docs", "img");
const PYTHON = process.env.LIQMAP_PYTHON || "python3";
const STORE = "open-liqmap-v1";

const failures = [];
function check(ok, label) {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}`);
  if (!ok) failures.push(label);
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function startServer() {
  const port = await freePort();
  const proc = spawn(PYTHON, ["-m", "liqmap.server", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  proc.stdout.on("data", (chunk) => { log += chunk; });
  proc.stderr.on("data", (chunk) => { log += chunk; });
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 100; i++) {
    if (proc.exitCode != null) break;
    try {
      const response = await fetch(base + "/api/status");
      if (response.ok) return { proc, base };
    } catch (_err) {
      /* not listening yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  proc.kill();
  throw new Error("server did not start:\n" + log);
}

async function newPage(browser, base, viewport, scale) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: scale });
  // Start on the demo exchange; leave the first-visit flag alone.
  await context.addInitScript(([key]) => {
    try {
      if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ exchange: "demo" }));
    } catch (_err) {
      /* storage blocked */
    }
  }, [STORE]);
  // Offline: answer every venue with the demo map. Each request is recorded as the page sent it.
  const requests = [];
  await context.route("**/api/heatmap?**", (route) => {
    const url = new URL(route.request().url());
    requests.push(new URL(url.toString()));
    if (url.searchParams.get("exchange") !== "demo" || url.searchParams.get("mode") !== "pair") {
      url.searchParams.set("exchange", "demo");
      url.searchParams.set("mode", "pair");
      url.searchParams.delete("bar");
      url.searchParams.delete("asof");
    }
    route.continue({ url: url.toString() });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  const errors = [];
  page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
  page.on("pageerror", (err) => errors.push(String(err)));
  await page.goto(base + "/");
  await waitLoaded(page);
  return { context, page, errors, requests };
}

async function waitLoaded(page) {
  await page.waitForFunction(() => {
    const loading = document.getElementById("loading");
    return loading && loading.hidden && /Liquidation Heatmap/.test(document.getElementById("title").textContent)
      && !/^Loading/.test(document.getElementById("asof").textContent);
  }, null, { timeout: 30000 });
}

async function pickExchange(page, value) {
  await page.selectOption("#exchange", value);
  await waitLoaded(page);
}

async function noSideScroll(page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
}

async function desk(browser, base) {
  const { context, page, errors, requests } = await newPage(browser, base, { width: 1440, height: 900 }, 1);

  check(await page.isVisible("#toolbar"), "desk: one toolbar");
  check(await page.isVisible("#intro"), "desk: first-visit card shows on first load");
  check(!(await page.isVisible("#drawer")), "desk: drawer hidden until More");
  await page.click("#more");
  check(await page.isVisible("#drawer"), "desk: More opens the drawer");

  await pickExchange(page, "okx");
  check(!(await page.isVisible("#player")) && !(await page.isVisible("#bar-field")), "desk: replay and bar hidden for OKX");
  await pickExchange(page, "binance");
  check(await page.isVisible("#player") && await page.isVisible("#bar-field") && await page.isVisible("#asof-field"),
    "desk: replay, bar and as-of visible for a Binance pair");
  // Pick a bar and a past time on Binance; other venues must not send them.
  await page.selectOption("#bar", "1h");
  await waitLoaded(page);
  await page.fill("#asof-input", "2026-10-20T12:00");
  await waitLoaded(page);
  const sent = (exchange, mode) => requests.filter((u) => u.searchParams.get("exchange") === exchange && u.searchParams.get("mode") === mode);
  const binance = sent("binance", "pair");
  check(binance.some((u) => u.searchParams.get("bar") === "1h" && u.searchParams.has("asof")),
    "desk: Binance pair sends the chosen bar and as-of");
  const before = requests.length;
  await pickExchange(page, "okx");
  await pickExchange(page, "all");
  const okx = requests.slice(before).filter((u) => u.searchParams.get("exchange") === "okx" && u.searchParams.get("mode") === "pair");
  const all = requests.slice(before).filter((u) => u.searchParams.get("mode") === "symbol");
  check(okx.length > 0 && okx.every((u) => !u.searchParams.has("bar") && !u.searchParams.has("asof")),
    `desk: OKX requests carry no bar or asof (${okx.map((u) => u.search).join(" ")})`);
  check(all.length > 0 && all.every((u) => !u.searchParams.has("bar") && !u.searchParams.has("asof")),
    `desk: All-venues requests carry no bar or asof (${all.map((u) => u.search).join(" ")})`);
  check(!(await page.isVisible("#player")), "desk: replay hidden for All venues");
  await pickExchange(page, "demo");
  check(!(await page.isVisible("#player")), "desk: replay hidden for Demo");

  // Replay shortcuts must not fire from a field. A stand-in replay gets the handler
  // past its "no replay" exit; its next frame never resolves, so nothing loads.
  await page.evaluate(() => {
    state.replay = {
      gen: 1, first: Date.now(), step: 300000, frames: 3, idx: 0, playing: false, speed: 2,
      cache: new Map([[1, new Promise(() => {})]]), controller: new AbortController(), reloaded: false,
    };
  });
  await page.focus("#coin");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Space");
  await page.evaluate(() => {
    document.getElementById("auto").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    document.getElementById("auto").dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
  });
  const inField = await page.evaluate(() => ({ idx: state.replay.idx, playing: state.replay.playing }));
  check(inField.idx === 0 && inField.playing === false,
    `desk: arrow and space in an input or select leave replay alone (idx ${inField.idx}, playing ${inField.playing})`);
  await page.evaluate(() => {
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  });
  const outside = await page.evaluate(() => state.replay.idx);
  check(outside === 1, `desk: the same arrow outside a field steps replay (idx ${outside})`);
  await page.evaluate(() => stopReplay());
  await page.fill("#coin", "BTC");

  // Ratios read plainly: above 10 and below 0.1 are capped.
  const ratios = await page.evaluate(() => ["553.84×", "2.07×", "0.02×"].map((text) => readableRatios(text)));
  check(ratios[0] === "> 10×" && ratios[1] === "2.07×" && ratios[2] === "< 0.1×",
    `desk: readableRatios gives > 10×, 2.07×, < 0.1× (${ratios.join(", ")})`);

  await page.check("#liqmap");
  await page.click("#more");
  check(!(await page.isVisible("#drawer")), "desk: More closes the drawer");

  await page.click("#intro-close");
  check(!(await page.isVisible("#intro")), "desk: first-visit card dismisses");
  await page.reload();
  await waitLoaded(page);
  check(!(await page.isVisible("#intro")), "desk: first-visit card stays dismissed after reload");

  const split = await page.textContent("#imbalance tbody");
  check(/Short \d+% \/ Long \d+%/.test(split), "desk: imbalance reads Short x% / Long y%");

  check(errors.length === 0, "desk: no console errors" + (errors.length ? " — " + errors.join(" | ") : ""));
  await page.screenshot({ path: path.join(IMG, "hero.png") });

  const good = await page.textContent("#title");
  await page.fill("#coin", "!!");
  await page.press("#coin", "Enter");
  await page.waitForSelector("#banner.error", { state: "visible", timeout: 15000 });
  check((await page.textContent("#title")) === good, `desk: after a bad coin the title still names "${good}"`);
  const coinAfter = await page.inputValue("#coin");
  check(coinAfter === "BTC", `desk: after a bad coin the coin field reverts to BTC (${coinAfter})`);
  const unexpected = errors.filter((text) => !/status of 400/.test(text));
  check(unexpected.length === 0, "desk: only the expected 400 after a bad coin");
  await context.close();
}

async function phone(browser, base) {
  const { context, page, errors } = await newPage(browser, base, { width: 390, height: 844 }, 2);
  check(await page.isVisible("#intro"), "phone: first-visit card shows");
  check(await noSideScroll(page), "phone: no horizontal scroll");
  await page.screenshot({ path: path.join(IMG, "phone.png") });
  await page.click("#more");
  check(await page.isVisible("#drawer"), "phone: More opens the drawer");
  check(await noSideScroll(page), "phone: no horizontal scroll with the drawer open");
  await pickExchange(page, "binance");
  check(await page.isVisible("#player"), "phone: replay visible for a Binance pair");
  check(await noSideScroll(page), "phone: no horizontal scroll with replay shown");
  check(errors.length === 0, "phone: no console errors" + (errors.length ? " — " + errors.join(" | ") : ""));
  await context.close();
}

(async () => {
  fs.mkdirSync(IMG, { recursive: true });
  const { proc, base } = await startServer();
  const browser = await chromium.launch();
  try {
    await desk(browser, base);
    await phone(browser, base);
  } catch (err) {
    failures.push(String(err && err.message ? err.message.split("\n")[0] : err));
    console.log("FAIL " + failures[failures.length - 1]);
  } finally {
    await browser.close();
    proc.kill();
  }
  console.log(failures.length ? `\n${failures.length} failed` : "\nall passed");
  process.exit(failures.length ? 1 : 0);
})();
