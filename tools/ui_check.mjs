// Headless check of the page: time zones, color scale, past view, replay, hover tiers, model switch, Live.
// UI_BASE=http://127.0.0.1:8791 PLAYWRIGHT_CORE=/path/index.mjs CHROME=/path/chrome node tools/ui_check.mjs
const { chromium } = await import(process.env.PLAYWRIGHT_CORE);
const base = process.env.UI_BASE || "http://127.0.0.1:8791";
const browser = await chromium.launch({ executablePath: process.env.CHROME });
const page = await browser.newPage({ viewport: { width: 1500, height: 1150 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const ev = (fn) => page.evaluate(fn);
let failed = 0;
const check = (name, ok, detail = "") => {
  if (!ok) failed += 1;
  console.log(ok ? "PASS" : "FAIL", name, detail);
};
try {
  await page.goto(base + "/");
  await page.waitForTimeout(2500);
  await page.selectOption("#exchange", "binance");
  await page.waitForTimeout(2500);
  await page.selectOption("#bar", "15m");
  await page.selectOption("#tz", "UTC");
  const dst = await ev(() => {
    state.tz = "America/New_York";
    const out = { fallBack: parseZonedInput("2026-11-01T01:30"), gap: parseZonedInput("2026-03-08T02:30"), before: parseZonedInput("2026-03-08T01:30") };
    state.tz = "UTC";
    return out;
  });
  check("New York fall-back hour is its daylight instant", dst.fallBack === 1793511000, JSON.stringify(dst));
  check("New York spring-forward gap maps to 01:30", dst.gap === 1772951400 && dst.before === 1772951400, JSON.stringify(dst));
  await page.fill("#asof-input", "2021-05-19T00:00");
  await page.dispatchEvent("#asof-input", "change");
  await page.waitForTimeout(2500);
  check("past view loads", await ev(() => Boolean(state.data && state.data.asof)));
  const scale = await ev(() => {
    const L = state.layout, tick = L.ticks.find((t) => t.frac === 0.5);
    return { at: tick && 1 - (tick.y - L.plotY) / (L.plotH - 1), want: Math.pow(0.5, state.gamma) };
  });
  check("color-bar 50% tick sits at 0.5^gamma", scale.at != null && Math.abs(scale.at - scale.want) < 1e-9, JSON.stringify(scale));
  const zoneBefore = await ev(() => ({ asof: state.asof, axis: fmtAxis(state.data.times[0], true, false), input: $("asof-input").value }));
  await page.selectOption("#tz", "America/New_York");
  await page.waitForTimeout(300);
  const zoneAfter = await ev(() => ({ asof: state.asof, axis: fmtAxis(state.data.times[0], true, false), input: $("asof-input").value }));
  check("zone switch keeps As of and moves the axis", zoneAfter.asof === zoneBefore.asof && zoneAfter.axis !== zoneBefore.axis && zoneAfter.input === "2021-05-18T20:00", JSON.stringify({ zoneBefore, zoneAfter }));
  await page.selectOption("#tz", "UTC");
  await page.waitForTimeout(300);
  await page.fill("#to-input", "2021-05-21T00:00");
  await page.click("#rp-load");
  await page.waitForFunction(() => state.replay && state.data && state.data.replay, null, { timeout: 120000 });
  const r0 = await ev(() => ({ frames: state.replay.frames, asof: state.data.asof }));
  check("replay loads", r0.frames > 100, JSON.stringify(r0));
  const box = await page.locator("canvas").first().boundingBox();
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.wheel(0, -400);
  await page.waitForTimeout(300);
  const view = await ev(() => [state.view.lo, state.view.hi]);
  for (let i = 0; i < 3; i++) { await page.click("#rp-fwd"); await page.waitForTimeout(250); }
  const after = await ev(() => ({ idx: state.replay.idx, lo: state.view.lo, hi: state.view.hi }));
  check("steps keep zoom", after.idx === 3 && after.lo === view[0] && after.hi === view[1], JSON.stringify(after));
  await page.selectOption("#rp-speed", "10");
  await page.click("#rp-play");
  await page.waitForTimeout(2000);
  await page.click("#rp-play");
  await page.waitForTimeout(400);
  const played = await ev(() => ({ idx: state.replay.idx, shown: state.data.asof, want: state.replay.first + state.replay.idx * state.replay.step }));
  check("play never skips", played.idx > 8 && played.shown === played.want, JSON.stringify(played));
  const spot = await ev(() => {
    const d = state.data, L = state.layout, n = d.times.length;
    let best = -1, bi = 0, bj = 0;
    for (let b = 0; b < d.bins; b++) {
      if (d.centersA[b] < state.view.lo || d.centersA[b] > state.view.hi) continue; // stay inside the zoomed view
      for (let j = 0; j < n; j++) { const v = d.heatA[b * n + j]; if (v > best) { best = v; bi = b; bj = j; } }
    }
    return { x: xOf(bj, L), y: yOf(d.centersA[bi], L) };
  });
  const cbox = await page.locator("canvas").first().boundingBox();
  await page.mouse.move(cbox.x + spot.x, cbox.y + spot.y);
  await page.waitForTimeout(900);
  check("hover lists tiers", /\d+x (L|S) \$/.test(await page.textContent("#tip")));
  await page.click('.tab[data-model="2"]');
  await page.waitForTimeout(800);
  check("model switch keeps replay", await ev(() => Boolean(state.replay) && state.data.model.id === 2));
  await page.click("#live");
  await page.waitForTimeout(4000);
  check("Live leaves replay", await ev(() => !state.replay && state.data && !state.data.asof));
  const sent = page.waitForRequest((req) => req.url().includes("/api/heatmap?") && req.url().includes("asof="));
  await page.fill("#asof-input", "2021-05-19T23:59");
  await page.dispatchEvent("#asof-input", "change");
  const asofParam = new URL((await sent).url()).searchParams.get("asof");
  check("UTC As of 2021-05-19T23:59 sends asof=1621468740", asofParam === "1621468740", asofParam);
  await page.waitForTimeout(2500);
  check("no page errors", errors.length === 0, JSON.stringify(errors));
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
