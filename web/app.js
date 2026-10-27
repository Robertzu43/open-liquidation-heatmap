/* Open liquidation heatmap. The server sends little-endian float32 grids.
   Threshold only remaps color; it does not refetch. */

const TZ = "America/Bogota";
const STORE = "open-liqmap-v1";
const THEMES = {
  ember: {
    name: "Ember",
    stops: [[22, 12, 36], [92, 36, 140], [176, 70, 78], [232, 164, 48], [255, 236, 140]],
  },
  tide: {
    name: "Tide",
    stops: [[8, 22, 36], [14, 78, 104], [20, 150, 156], [176, 224, 214], [240, 255, 250]],
  },
  magma: {
    name: "Magma",
    stops: [[24, 8, 10], [120, 28, 24], [196, 72, 22], [246, 156, 48], [255, 240, 214]],
  },
  glacier: {
    name: "Glacier",
    stops: [[10, 16, 36], [42, 68, 150], [78, 176, 220], [190, 240, 232], [255, 255, 255]],
  },
};

const state = {
  exchange: "okx",
  coin: "BTC",
  range: "24h",
  model: 1,
  mode: "pair",
  bar: "",
  asof: null,
  theme: "ember",
  threshold: 0.85,
  showMap: false,
  showCandles: true,
  auto: 0,
  data: null,
  view: { i0: -0.5, i1: 10, lo: 0, hi: 1 },
  timeFull: true,
  priceDirty: false,
  vmax: 1,
  gamma: 3.5,
  heatCanvas: null,
  heatDirty: true,
  seq: 0,
  controller: null,
  timer: 0,
  frame: 0,
  dpr: 1,
  cssW: 800,
  cssH: 500,
  layout: null,
  hover: null,
  drag: null,
  models: [],
};

const $ = (id) => document.getElementById(id);
const canvas = $("chart");
const wrap = $("wrap");
const tip = $("tip");
const banner = $("banner");

// Past views and bar choice are Binance pair-only; bars must give 2 to 3,000 per range.
const BAR_S = { "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "12h": 43200, "1d": 86400 };
const RANGE_S = { "12h": 43200, "24h": 86400, "3d": 259200, "7d": 604800, "30d": 2592000, "90d": 7776000, "180d": 15552000, "1y": 31536000, "2y": 63072000 };
const COT_OFFSET_S = 5 * 3600;

function barFits(range, bar) {
  if (!bar) return true;
  const bars = Math.floor(RANGE_S[range] / BAR_S[bar]);
  return bars >= 2 && bars <= 3000;
}

function historyOn() {
  return state.exchange === "binance" && state.mode === "pair";
}

function cotInputValue(seconds) {
  // datetime-local text for a unix time, read on the COT (UTC−5) clock.
  return new Date((seconds - COT_OFFSET_S) * 1000).toISOString().slice(0, 16);
}

function parseCotInput(text) {
  const ms = Date.parse(text + ":00Z");
  return Number.isFinite(ms) ? Math.floor(ms / 1000) + COT_OFFSET_S : null;
}

function clamp(value, lo, hi) {
  return Math.min(hi, Math.max(lo, value));
}

function gammaOf(threshold) {
  const t = clamp(Number(threshold) || 0, 0, 1);
  return 0.55 + Math.pow(t, 1.05) * 3.5;
}

function shade(value, vmax, threshold) {
  if (!(value > 0) || !(vmax > 0)) return 0;
  const x = Math.min(1, value / vmax);
  return Math.pow(x, gammaOf(threshold));
}

function colorAt(amount, theme) {
  const stops = theme.stops;
  const x = clamp(amount, 0, 1) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const a = stops[i];
  const b = stops[i + 1];
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
  ];
}

function decodeF32(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const view = new DataView(bytes.buffer);
  const out = new Float32Array(binary.length >> 2);
  for (let i = 0; i < out.length; i++) out[i] = view.getFloat32(i * 4, true);
  return out;
}

function fmtUsd(value) {
  const abs = Math.abs(Number(value) || 0);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e9) return sign + "$" + (abs / 1e9).toFixed(2) + "B";
  if (abs >= 1e6) return sign + "$" + (abs / 1e6).toFixed(2) + "M";
  if (abs >= 1e3) return sign + "$" + (abs / 1e3).toFixed(1) + "K";
  return sign + "$" + abs.toFixed(0);
}

function fmtPx(value) {
  const v = Number(value);
  if (!Number.isFinite(v)) return "—";
  if (v >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (v >= 1) return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (v >= 0.01) return v.toFixed(4);
  return v.toFixed(6);
}

function fmtStamp(ms, withTime) {
  const opts = {
    timeZone: TZ,
    day: "2-digit",
    month: "short",
    year: "numeric",
    hourCycle: "h23",
  };
  if (withTime) {
    opts.hour = "2-digit";
    opts.minute = "2-digit";
  }
  return new Intl.DateTimeFormat("en-GB", opts).format(new Date(ms)) + " COT";
}

function fmtAxis(ms, withTime, narrow) {
  const opts = { timeZone: TZ, hourCycle: "h23" };
  if (narrow && withTime) {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: TZ, day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(ms));
    const pick = (type) => parts.find((part) => part.type === type).value;
    return pick("day") + " " + pick("hour") + ":" + pick("minute");
  }
  opts.day = "2-digit";
  opts.month = "short";
  if (withTime) {
    opts.hour = "2-digit";
    opts.minute = "2-digit";
  }
  return new Intl.DateTimeFormat("en-GB", opts).format(new Date(ms));
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function layoutFor(w, h) {
  const narrow = w < 520;
  const scaleW = narrow ? 58 : 86;
  const priceW = narrow ? 52 : 82;
  const liqW = state.showMap ? (narrow ? 46 : 124) : 0;
  const timeH = 30;
  const top = 8;
  const plotX = scaleW;
  const plotY = top;
  const plotW = Math.max(20, w - scaleW - priceW - liqW - 8);
  const plotH = Math.max(20, h - timeH - top);
  return {
    scaleW, priceW, liqW, timeH, top, plotX, plotY, plotW, plotH,
    priceX: plotX + plotW,
    liqX: plotX + plotW + priceW,
    width: w,
    height: h,
  };
}

function persist() {
  try {
    localStorage.setItem(STORE, JSON.stringify({
      exchange: state.exchange,
      coin: state.coin,
      range: state.range,
      model: state.model,
      mode: state.mode,
      bar: state.bar,
      theme: state.theme,
      threshold: state.threshold,
      showMap: state.showMap,
      showCandles: state.showCandles,
      auto: state.auto,
    }));
  } catch (_err) {
    /* private mode */
  }
}

function restore() {
  try {
    const raw = localStorage.getItem(STORE);
    if (!raw) return;
    const saved = JSON.parse(raw);
    const exchanges = ["okx", "binance", "demo"];
    const ranges = ["12h", "24h", "3d", "7d", "30d", "90d", "180d", "1y", "2y"];
    if (exchanges.includes(saved.exchange)) state.exchange = saved.exchange;
    if (saved.coin) state.coin = String(saved.coin).toUpperCase();
    if (ranges.includes(saved.range)) state.range = saved.range;
    if ([1, 2, 3].includes(Number(saved.model))) state.model = Number(saved.model);
    if (saved.mode === "pair" || saved.mode === "symbol") state.mode = saved.mode;
    if (saved.bar in BAR_S && barFits(state.range, saved.bar)) state.bar = saved.bar;
    if (THEMES[saved.theme]) state.theme = saved.theme;
    if (Number.isFinite(saved.threshold)) state.threshold = clamp(saved.threshold, 0, 1);
    if (typeof saved.showMap === "boolean") state.showMap = saved.showMap;
    if (typeof saved.showCandles === "boolean") state.showCandles = saved.showCandles;
    if (saved.auto != null) state.auto = Number(saved.auto) || 0;
  } catch (_err) {
    /* ignore broken storage */
  }
}

function syncControls() {
  $("exchange").value = state.exchange;
  $("coin").value = state.coin;
  $("range").value = state.range;
  $("threshold").value = String(state.threshold);
  $("th-val").textContent = state.threshold.toFixed(2);
  $("liqmap").checked = state.showMap;
  $("candles").checked = state.showCandles;
  $("auto").value = String(state.auto);
  document.querySelectorAll(".tab").forEach((button) => {
    const on = Number(button.dataset.model) === state.model;
    button.classList.toggle("on", on);
    button.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll("#mode-toggle button").forEach((button) => {
    button.classList.toggle("on", button.dataset.mode === state.mode);
  });
  const symbolMode = state.mode === "symbol";
  $("exchange").disabled = symbolMode;
  $("exchange-field").classList.toggle("disabled", symbolMode);
  const canTravel = historyOn();
  $("bar").value = state.bar;
  $("bar").disabled = !canTravel;
  $("bar-field").classList.toggle("disabled", !canTravel);
  $("asof-input").value = state.asof == null ? "" : cotInputValue(state.asof);
  $("asof-input").disabled = !canTravel;
  $("asof-field").classList.toggle("disabled", !canTravel);
  $("live").disabled = !canTravel || state.asof == null;
  if ($("rp-load")) syncReplay();
  document.querySelectorAll(".swatch").forEach((button) => {
    button.classList.toggle("on", button.dataset.theme === state.theme);
  });
  const theme = THEMES[state.theme];
  const root = document.documentElement;
  root.style.setProperty("--scale-0", `rgb(${theme.stops[0].join(",")})`);
  root.style.setProperty("--scale-1", `rgb(${theme.stops[2].join(",")})`);
  root.style.setProperty("--scale-2", `rgb(${theme.stops[4].join(",")})`);
}

function buildSwatches() {
  const host = $("themes");
  host.innerHTML = "";
  Object.entries(THEMES).forEach(([id, theme]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "swatch";
    button.dataset.theme = id;
    button.title = theme.name;
    button.setAttribute("aria-label", theme.name + " theme");
    button.style.background = `linear-gradient(135deg, rgb(${theme.stops[0].join(",")}), rgb(${theme.stops[2].join(",")}), rgb(${theme.stops[4].join(",")}))`;
    button.addEventListener("click", () => {
      state.theme = id;
      state.heatDirty = true;
      syncControls();
      persist();
      requestDraw();
    });
    host.appendChild(button);
  });
}

function setLoading(on, label) {
  const el = $("loading");
  el.hidden = !on;
  if (on) el.textContent = label || "Loading…";
}

function showBanner(kind, lines) {
  if (!lines || !lines.length) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  banner.hidden = false;
  banner.className = "banner " + kind;
  banner.innerHTML = lines.map(escapeHtml).join("<br>");
}

function rangeLabel(id) {
  const option = [...$("range").options].find((item) => item.value === id);
  return option ? option.textContent : id;
}

function mapParams(extra) {
  const params = new URLSearchParams({
    exchange: state.exchange,
    symbol: state.coin,
    range: state.range,
    model: String(state.model),
    mode: state.mode,
  });
  if (historyOn()) {
    if (state.bar) params.set("bar", state.bar);
    if (state.asof != null) params.set("asof", String(state.asof));
  }
  Object.entries(extra || {}).forEach(([key, value]) => params.set(key, String(value)));
  return params;
}

async function load(reason) {
  const seq = ++state.seq;
  if (state.controller) state.controller.abort();
  const controller = new AbortController();
  state.controller = controller;
  const quiet = reason === "auto" && state.data;
  const label = `Loading ${state.mode === "symbol" ? "aggregate" : state.exchange.toUpperCase()} · ${state.coin} · ${rangeLabel(state.range)}`;
  setLoading(true, quiet ? "Refreshing…" : label);
  const params = mapParams();
  try {
    const response = await fetch("/api/heatmap?" + params.toString(), { signal: controller.signal });
    const body = await response.json();
    if (seq !== state.seq) return;
    if (!response.ok) throw new Error(body.error || "Request failed");
    applyPayload(body);
  } catch (err) {
    if (err.name === "AbortError" || seq !== state.seq) return;
    fail(err.message || "Could not load this map.");
  } finally {
    if (seq === state.seq) setLoading(false);
  }
}

function fail(message) {
  state.data = null;
  state.heatCanvas = null;
  showBanner("error", [message]);
  $("summary").textContent = message;
  $("above").textContent = "—";
  $("below").textContent = "—";
  $("imbalance").querySelector("tbody").innerHTML = "";
  $("levels").querySelector("tbody").innerHTML = "";
  $("resting").textContent = "";
  $("tags").innerHTML = "";
  $("asof").textContent = "No map loaded";
  drawMessage(message);
}

function applyPayload(data) {
  const prev = state.data;
  const keepView = Boolean(data.replay && prev && prev.replay);
  const identityChanged = !prev || prev.coin !== data.coin || prev.range !== data.range || prev.exchange !== data.exchange || prev.mode !== data.mode || prev.bar !== data.bar || (!keepView && prev.asof !== data.asof);
  data.openA = decodeF32(data.open);
  data.highA = decodeF32(data.high);
  data.lowA = decodeF32(data.low);
  data.closeA = decodeF32(data.close);
  data.volumeA = decodeF32(data.volume);
  data.centersA = decodeF32(data.centers);
  data.heatA = decodeF32(data.heat);
  data.longA = decodeF32(data.profile_long);
  data.shortA = decodeF32(data.profile_short);
  data.tierLongA = decodeF32(data.profile_long_tiers || "");
  data.tierShortA = decodeF32(data.profile_short_tiers || "");
  state.data = data;
  state.heatDirty = true;
  if (identityChanged) {
    state.timeFull = true;
    state.priceDirty = false;
  }
  const n = data.times.length;
  if (state.timeFull) {
    state.view.i0 = -0.5;
    state.view.i1 = n - 0.5;
  }
  if (!state.priceDirty && !keepView) {
    state.view.lo = data.price_view[0];
    state.view.hi = data.price_view[1];
  }
  clampView();
  $("title").textContent = data.title;
  document.title = data.title + " · Open Liq";
  const sourceNames = {
    okx: "OKX live",
    "binance-live": "Binance live",
    "binance-vision": "Binance archive",
    demo: "Synthetic demo",
    aggregate: "Aggregated fresh venues",
  };
  const withTime = data.bar_seconds < 86400;
  $("asof").textContent = `${sourceNames[data.source] || data.source} · last bar ${fmtStamp(data.as_of, withTime)} · updated ${fmtStamp(data.generated_at, true)}`;
  const lines = [];
  if (data.asof) lines.push(`As of ${fmtStamp(data.asof, true)} · ${data.bar} bars`);
  (data.warnings || []).forEach((line) => lines.push(line));
  if (data.note && !lines.includes(data.note)) lines.push(data.note);
  // A past view names the archive on purpose; only its own warnings are serious.
  const serious = data.stale || (!data.asof && lines.some((line) => /lag|left out|archive|synthetic|only go back/i.test(line)));
  showBanner(serious ? "warn" : "info", lines);
  renderAnalysis(data);
  renderHow(data.model);
  canvas.setAttribute("aria-label", data.analysis.summary || data.title);
  requestDraw();
}

function renderAnalysis(data) {
  const analysis = data.analysis || {};
  $("summary").textContent = analysis.summary || "";
  $("above").innerHTML = levelHtml(analysis.above);
  $("below").innerHTML = levelHtml(analysis.below);
  const imb = $("imbalance").querySelector("tbody");
  imb.innerHTML = (analysis.imbalance || []).map((row) => {
    const ratio = row.short_to_long == null ? "—" : Number(row.short_to_long).toFixed(2) + "×";
    return `<tr><td>${row.window_pct}%</td><td>${fmtUsd(row.long_usd)}</td><td>${fmtUsd(row.short_usd)}</td><td>${ratio}</td></tr>`;
  }).join("");
  const levels = $("levels").querySelector("tbody");
  levels.innerHTML = (analysis.levels || []).map((level) => {
    const dist = (level.distance_pct >= 0 ? "+" : "") + Number(level.distance_pct).toFixed(2) + "%";
    return `<tr><td>${escapeHtml(level.side)}</td><td>${fmtPx(level.price)}</td><td>${dist}</td><td>${fmtUsd(level.usd)}</td></tr>`;
  }).join("");
  $("resting").textContent = `Resting long ${fmtUsd(analysis.resting_long_usd || 0)} · resting short ${fmtUsd(analysis.resting_short_usd || 0)}`;
  const tags = $("tags");
  tags.innerHTML = "";
  (analysis.levels || []).slice(0, 4).forEach((level) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = level.side === "short" ? "short" : "long";
    button.textContent = `${level.side === "short" ? "Short" : "Long"} ${fmtPx(level.price)} · ${fmtUsd(level.usd)}`;
    button.addEventListener("click", () => focusPrice(level.price));
    tags.appendChild(button);
  });
}

function levelHtml(level) {
  if (!level) return "None in this window.";
  const dist = (level.distance_pct >= 0 ? "+" : "") + Number(level.distance_pct).toFixed(2) + "%";
  return `<b>${fmtPx(level.price)}</b><span>${dist} · ${fmtUsd(level.usd)}</span>`;
}

function renderHow(model) {
  if (!model) return;
  const weights = (model.weights || []).map((row) => `<li>${row.leverage}× · ${(row.weight * 100).toFixed(0)}%</li>`).join("");
  const flow = model.flow
    ? "New open interest follows taker buy/sell when that print exists. Otherwise the split is even."
    : "New open interest is split evenly between longs and shorts.";
  $("how-body").innerHTML = `
    <p>${escapeHtml(model.blurb || "")}</p>
    <p>Maintenance margin rate (MMR) is ${(model.mmr * 100).toFixed(1)}%. Entry for a bar is <span class="formula">${escapeHtml(model.entry)}</span>.</p>
    <p>Long liquidation = <span class="formula">${escapeHtml(model.long_formula)}</span></p>
    <p>Short liquidation = <span class="formula">${escapeHtml(model.short_formula)}</span></p>
    <p>${flow} A later candle removes a shelf when price trades through it.</p>
    <p>Leverage mix</p>
    <ul>${weights}</ul>
    <p>Per-bar decay ${model.decay}. Volume churn ${model.churn}. Kernel ${model.kernel.map((v) => Number(v).toFixed(2)).join(" · ")}.</p>
    <p>The color scale is relative to this window. It is not a count of real liquidations.</p>`;
}

function focusPrice(price) {
  if (!state.data) return;
  const span = Math.max(state.view.hi - state.view.lo, price * 0.04);
  state.view.lo = price - span * 0.45;
  state.view.hi = price + span * 0.55;
  state.priceDirty = true;
  clampView();
  requestDraw();
}

function robustMax(heat) {
  const vals = [];
  for (let i = 0; i < heat.length; i++) if (heat[i] > 0) vals.push(heat[i]);
  if (!vals.length) return 1;
  vals.sort((a, b) => a - b);
  const idx = Math.min(vals.length - 1, Math.floor(0.985 * (vals.length - 1)));
  return Math.max(vals[idx], 1e-6);
}

function rebuildHeat() {
  const data = state.data;
  if (!data) return;
  const nT = data.times.length;
  const bins = data.bins;
  const off = state.heatCanvas || document.createElement("canvas");
  off.width = nT;
  off.height = bins;
  const ctx = off.getContext("2d", { willReadFrequently: true });
  const img = ctx.createImageData(nT, bins);
  const vmax = robustMax(data.heatA);
  state.vmax = vmax;
  state.gamma = gammaOf(state.threshold);
  const theme = THEMES[state.theme];
  const bg = [20, 22, 15];
  for (let b = 0; b < bins; b++) {
    const y = bins - 1 - b;
    const row = b * nT;
    for (let t = 0; t < nT; t++) {
      const value = data.heatA[row + t];
      const o = (y * nT + t) * 4;
      let rgb = bg;
      if (value > 0) rgb = colorAt(shade(value, vmax, state.threshold), theme);
      img.data[o] = rgb[0];
      img.data[o + 1] = rgb[1];
      img.data[o + 2] = rgb[2];
      img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  state.heatCanvas = off;
}

function xOf(index, L) {
  return L.plotX + ((index - state.view.i0) / (state.view.i1 - state.view.i0)) * L.plotW;
}
function yOf(price, L) {
  const span = state.view.hi - state.view.lo;
  return L.plotY + (1 - (price - state.view.lo) / span) * L.plotH;
}
function indexAt(x, L) {
  return state.view.i0 + ((x - L.plotX) / L.plotW) * (state.view.i1 - state.view.i0);
}
function priceAt(y, L) {
  const t = 1 - (y - L.plotY) / L.plotH;
  return state.view.lo + t * (state.view.hi - state.view.lo);
}

function clampView() {
  const data = state.data;
  if (!data) return;
  const n = data.times.length;
  let span = state.view.i1 - state.view.i0;
  span = clamp(span, Math.min(8, n), n);
  if (state.view.i0 < -0.5) {
    state.view.i0 = -0.5;
    state.view.i1 = state.view.i0 + span;
  }
  if (state.view.i1 > n - 0.5) {
    state.view.i1 = n - 0.5;
    state.view.i0 = state.view.i1 - span;
  }
  if (state.view.i0 < -0.5) state.view.i0 = -0.5;
  const centers = data.centersA;
  const hardLo = centers[0];
  const hardHi = centers[centers.length - 1];
  const maxSpan = Math.max(hardHi - hardLo, hardHi * 0.01) * 1.35;
  const minSpan = Math.max((hardHi - hardLo) * 0.015, data.analysis.price * 0.0015);
  let pspan = state.view.hi - state.view.lo;
  if (!(pspan > 0)) pspan = minSpan;
  pspan = clamp(pspan, minSpan, maxSpan);
  let mid = (state.view.lo + state.view.hi) / 2;
  const boundLo = hardLo - (hardHi - hardLo) * 0.2;
  const boundHi = hardHi + (hardHi - hardLo) * 0.2;
  mid = clamp(mid, boundLo + pspan / 2, boundHi - pspan / 2);
  state.view.lo = mid - pspan / 2;
  state.view.hi = mid + pspan / 2;
}

function niceTicks(lo, hi, count) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  const rough = span / Math.max(2, count);
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const err = rough / mag;
  const step = err >= 7.5 ? 10 * mag : err >= 3.5 ? 5 * mag : err >= 1.5 ? 2 * mag : mag;
  const ticks = [];
  let value = Math.ceil(lo / step) * step;
  for (let guard = 0; value <= hi && guard < 20; guard++) {
    ticks.push(value);
    value += step;
  }
  return ticks;
}

function requestDraw() {
  if (state.frame) return;
  state.frame = requestAnimationFrame(() => {
    state.frame = 0;
    if (state.heatDirty && state.data) {
      rebuildHeat();
      state.heatDirty = false;
    }
    draw();
  });
}

function drawMessage(message) {
  const ctx = canvas.getContext("2d");
  ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
  ctx.clearRect(0, 0, state.cssW, state.cssH);
  ctx.fillStyle = "#14160f";
  ctx.fillRect(0, 0, state.cssW, state.cssH);
  ctx.fillStyle = "#e7c7c2";
  ctx.font = "14px sans-serif";
  wrapText(ctx, message, 24, 48, Math.max(80, state.cssW - 48), 20);
}

function wrapText(ctx, text, x, y, maxW, lineH) {
  const words = String(text).split(" ");
  let line = "";
  words.forEach((word) => {
    const trial = line ? line + " " + word : word;
    if (ctx.measureText(trial).width > maxW && line) {
      ctx.fillText(line, x, y);
      y += lineH;
      line = word;
    } else {
      line = trial;
    }
  });
  if (line) ctx.fillText(line, x, y);
}

function draw() {
  if (!state.data) return;
  const data = state.data;
  const L = layoutFor(state.cssW, state.cssH);
  state.layout = L;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
  ctx.clearRect(0, 0, L.width, L.height);
  ctx.fillStyle = "#14160f";
  ctx.fillRect(0, 0, L.width, L.height);

  const nT = data.times.length;
  const bins = data.bins;
  const centers = data.centersA;
  const half = bins > 1 ? (centers[1] - centers[0]) / 2 : centers[0] * 0.001;

  ctx.save();
  ctx.beginPath();
  ctx.rect(L.plotX, L.plotY, L.plotW, L.plotH);
  ctx.clip();
  if (state.heatCanvas) {
    const x0 = xOf(-0.5, L);
    const x1 = xOf(nT - 0.5, L);
    const yTop = yOf(centers[bins - 1] + half, L);
    const yBot = yOf(centers[0] - half, L);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(state.heatCanvas, x0, yTop, x1 - x0, yBot - yTop);
  }
  if (state.showCandles) drawCandles(ctx, L, data);
  ctx.restore();

  drawScale(ctx, L);
  drawPriceAxis(ctx, L);
  drawTimeAxis(ctx, L, data);
  if (state.showMap) drawLiqBars(ctx, L, data, half);
  drawFrame(ctx, L);
  if (state.hover) drawCrosshair(ctx, L, data);
}

function drawCandles(ctx, L, data) {
  const nT = data.times.length;
  const start = Math.max(0, Math.floor(state.view.i0) - 1);
  const end = Math.min(nT - 1, Math.ceil(state.view.i1) + 1);
  const colW = Math.abs(xOf(1, L) - xOf(0, L));
  const bodyW = Math.max(1, Math.min(11, colW * 0.62));
  for (let i = start; i <= end; i++) {
    const open = data.openA[i];
    const high = data.highA[i];
    const low = data.lowA[i];
    const close = data.closeA[i];
    const x = xOf(i, L);
    const up = close >= open;
    ctx.strokeStyle = up ? "#7dcea0" : "#e07a72";
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, yOf(high, L));
    ctx.lineTo(x, yOf(low, L));
    ctx.stroke();
    const y1 = yOf(Math.max(open, close), L);
    const y2 = yOf(Math.min(open, close), L);
    const h = Math.max(1, y2 - y1);
    ctx.fillRect(x - bodyW / 2, y1, bodyW, h);
  }
}

function drawScale(ctx, L) {
  const theme = THEMES[state.theme];
  const barW = 8;
  const barX = L.plotX - barW - 4;
  for (let y = 0; y < L.plotH; y++) {
    const intensity = 1 - y / Math.max(1, L.plotH - 1);
    const rgb = colorAt(intensity, theme);
    ctx.fillStyle = `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
    ctx.fillRect(barX, L.plotY + y, barW, 1);
  }
  ctx.fillStyle = "#d5dccb";
  ctx.font = "10px ui-monospace, monospace";
  ctx.textAlign = "right";
  const gamma = state.gamma || gammaOf(state.threshold);
  const usdAt = (intensity) => (intensity <= 0 ? 0 : state.vmax * Math.pow(intensity, 1 / gamma));
  ctx.fillText(fmtUsd(usdAt(1)), barX - 4, L.plotY + 10);
  ctx.fillText(fmtUsd(usdAt(0.5)), barX - 4, L.plotY + L.plotH * 0.5);
  ctx.fillText("0", barX - 4, L.plotY + L.plotH - 2);
}

function drawPriceAxis(ctx, L) {
  ctx.fillStyle = "#c5cbb8";
  ctx.font = "11px ui-monospace, monospace";
  ctx.textAlign = "left";
  const ticks = niceTicks(state.view.lo, state.view.hi, state.cssH < 460 ? 4 : 6);
  ticks.forEach((price) => {
    const y = yOf(price, L);
    if (y < L.plotY + 8 || y > L.plotY + L.plotH - 4) return;
    ctx.strokeStyle = "rgba(197, 203, 184, 0.16)";
    ctx.beginPath();
    ctx.moveTo(L.plotX, y);
    ctx.lineTo(L.plotX + L.plotW, y);
    ctx.stroke();
    ctx.fillStyle = "#c5cbb8";
    ctx.fillText(fmtPx(price), L.priceX + 6, y + 4);
  });
}

function drawTimeAxis(ctx, L, data) {
  const withTime = data.bar_seconds < 86400;
  const narrow = L.plotW < 420;
  const count = L.plotW < 240 ? 2 : L.plotW < 560 ? 3 : 5;
  ctx.fillStyle = "#c5cbb8";
  ctx.font = "11px ui-monospace, monospace";
  ctx.textAlign = "center";
  let lastRight = -1e9;
  for (let k = 0; k < count; k++) {
    const index = state.view.i0 + ((k + 0.5) / count) * (state.view.i1 - state.view.i0);
    const snapped = clamp(Math.round(index), 0, data.times.length - 1);
    const x = xOf(index, L);
    const text = fmtAxis(data.times[snapped], withTime, narrow);
    const width = ctx.measureText(text).width;
    if (x - width / 2 < L.plotX + 2 || x + width / 2 > L.plotX + L.plotW - 2) continue;
    if (x - width / 2 < lastRight + 8) continue;
    ctx.fillText(text, x, L.plotY + L.plotH + 18);
    lastRight = x + width / 2;
  }
}

// One color per leverage, the same in every model.
const TIER_COLORS = {
  5: "#7aa2f7", 10: "#8ecae6", 20: "#4cc9f0", 25: "#4361ee",
  50: "#f4c542", 60: "#f9a03f", 75: "#f3722c", 100: "#e85d04",
};

function tierColor(leverage) {
  return TIER_COLORS[leverage] || "#8f9a84";
}

function drawLiqBars(ctx, L, data, half) {
  const bins = data.bins;
  const tiers = data.tiers || [];
  let maxP = 0;
  for (let b = 0; b < bins; b++) maxP = Math.max(maxP, data.longA[b] + data.shortA[b]);
  if (!(maxP > 0)) return;
  const x0 = L.liqX + 6;
  const span = L.liqW - 12;
  ctx.save();
  ctx.beginPath();
  ctx.rect(L.liqX, L.plotY, L.liqW, L.plotH);
  ctx.clip();
  // Bars: the long and short USD at each price, stacked by leverage tier.
  for (let b = 0; b < bins; b++) {
    const total = data.longA[b] + data.shortA[b];
    if (!(total > 0)) continue;
    const y1 = yOf(data.centersA[b] + half, L);
    const y2 = yOf(data.centersA[b] - half, L);
    if (y2 < L.plotY || y1 > L.plotY + L.plotH) continue;
    const bh = Math.max(1, y2 - y1);
    let x = x0;
    tiers.forEach((leverage, k) => {
      const usd = data.tierLongA[k * bins + b] + data.tierShortA[k * bins + b];
      if (!(usd > 0)) return;
      const w = (usd / maxP) * span;
      ctx.fillStyle = tierColor(leverage);
      ctx.fillRect(x, y1, w, bh);
      x += w;
    });
  }
  // Cumulative lines: longs summed from price downward, shorts from price upward.
  const spot = data.analysis ? data.analysis.price : 0;
  let cumLong = 0;
  let cumShort = 0;
  for (let b = 0; b < bins; b++) {
    if (data.centersA[b] < spot) cumLong += data.longA[b];
    else cumShort += data.shortA[b];
  }
  const cumMax = Math.max(cumLong, cumShort);
  if (cumMax > 0) {
    const line = (color, from, to, step, values) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      let run = 0;
      let started = false;
      for (let b = from; b !== to; b += step) {
        run += values[b];
        const px = x0 + (run / cumMax) * span;
        const py = yOf(data.centersA[b], L);
        if (!started) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
        started = true;
      }
      ctx.stroke();
    };
    let spotBin = 0;
    while (spotBin < bins && data.centersA[spotBin] < spot) spotBin++;
    line("rgba(125, 206, 160, 0.95)", spotBin - 1, -1, -1, data.longA);
    line("rgba(224, 122, 114, 0.95)", spotBin, bins, 1, data.shortA);
  }
  ctx.restore();
  if (L.liqW >= 80) {
    ctx.font = "10px ui-monospace, monospace";
    ctx.textAlign = "left";
    tiers.forEach((leverage, k) => {
      const y = L.plotY + 12 + k * 13;
      ctx.fillStyle = tierColor(leverage);
      ctx.fillRect(x0, y - 8, 8, 8);
      ctx.fillStyle = "#8f9a84";
      ctx.fillText(`${leverage}x`, x0 + 12, y);
    });
    ctx.fillStyle = "#8f9a84";
    ctx.fillText("Σ L / S", x0, L.plotY + L.plotH - 6);
  }
}

function drawFrame(ctx, L) {
  ctx.strokeStyle = "#2c3226";
  ctx.strokeRect(L.plotX + 0.5, L.plotY + 0.5, L.plotW - 1, L.plotH - 1);
}

function nearestBin(price, centers) {
  let lo = 0;
  let hi = centers.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (centers[mid] < price) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(centers[lo - 1] - price) < Math.abs(centers[lo] - price)) return lo - 1;
  return lo;
}

function drawCrosshair(ctx, L, data) {
  const hover = state.hover;
  if (!hover) return;
  ctx.save();
  ctx.beginPath();
  ctx.rect(L.plotX, L.plotY, L.plotW, L.plotH);
  ctx.clip();
  ctx.strokeStyle = "rgba(231, 236, 223, 0.45)";
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(hover.x, L.plotY);
  ctx.lineTo(hover.x, L.plotY + L.plotH);
  ctx.moveTo(L.plotX, hover.y);
  ctx.lineTo(L.plotX + L.plotW, hover.y);
  ctx.stroke();
  ctx.restore();
}

function updateHover(clientX, clientY) {
  if (!state.data || !state.layout) {
    tip.hidden = true;
    state.hover = null;
    return;
  }
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  const L = state.layout;
  if (x < L.plotX || x > L.plotX + L.plotW || y < L.plotY || y > L.plotY + L.plotH) {
    if (state.hover) {
      state.hover = null;
      tip.hidden = true;
      requestDraw();
    }
    return;
  }
  const data = state.data;
  const index = clamp(Math.round(indexAt(x, L)), 0, data.times.length - 1);
  const price = priceAt(y, L);
  const bin = nearestBin(price, data.centersA);
  const usd = data.heatA[bin * data.times.length + index];
  state.hover = { x, y, index, price, bin };
  const withTime = data.bar_seconds < 86400;
  tip.hidden = false;
  tip.innerHTML = `
    <div>${fmtStamp(data.times[index], withTime)}</div>
    <div>Price ${fmtPx(price)}</div>
    <div>Liquidity ${fmtUsd(usd || 0)}</div>
    <div>O ${fmtPx(data.openA[index])} H ${fmtPx(data.highA[index])}</div>
    <div>L ${fmtPx(data.lowA[index])} C ${fmtPx(data.closeA[index])}</div>`;
  const left = x + 14;
  const top = y + 14;
  tip.style.left = (left + 220 > rect.width ? x - 200 : left) + "px";
  tip.style.top = (top + 110 > rect.height ? y - 100 : top) + "px";
  hoverLevels(index, bin);
  requestDraw();
}

const levelCache = new Map();

function hoverLevels(index, bin) {
  const data = state.data;
  clearTimeout(state.hoverTimer);
  if (state.replay && state.replay.playing) return;
  const key = `${state.model}|${data.asof || "live"}|${data.generated_at}|${index}|${bin}`;
  state.hoverKey = key;
  if (levelCache.has(key)) {
    showLevels(levelCache.get(key));
    return;
  }
  state.hoverTimer = setTimeout(async () => {
    const extra = { t: data.times[index], price: data.centersA[bin] };
    if (data.asof) extra.asof = Math.round(data.asof / 1000);
    if (data.replay) extra.replay = 1;
    try {
      const response = await fetch("/api/levels?" + mapParams(extra).toString());
      const body = await response.json();
      if (!response.ok) return;
      levelCache.set(key, body);
      if (levelCache.size > 300) levelCache.delete(levelCache.keys().next().value);
      if (state.hoverKey === key) showLevels(body);
    } catch (_err) {
      /* hover detail is optional */
    }
  }, 120);
}

function showLevels(body) {
  if (tip.hidden) return;
  const rows = body.tiers
    .filter((row) => row.long_usd > 0 || row.short_usd > 0)
    .map((row) => {
      const parts = [];
      if (row.long_usd > 0) parts.push("L " + fmtUsd(row.long_usd));
      if (row.short_usd > 0) parts.push("S " + fmtUsd(row.short_usd));
      return `<div class="tier"><i style="background:${tierColor(row.leverage)}"></i>${row.leverage}x ${parts.join(" · ")}</div>`;
    })
    .join("");
  let box = tip.querySelector(".tiers");
  if (!box) {
    box = document.createElement("div");
    box.className = "tiers";
    tip.appendChild(box);
  }
  box.innerHTML = rows || "<div>No resting tiers here</div>";
}

function zoomPrice(y, factor) {
  const L = state.layout;
  if (!L) return;
  const price = priceAt(y, L);
  state.view.lo = price - (price - state.view.lo) * factor;
  state.view.hi = price + (state.view.hi - price) * factor;
  state.priceDirty = true;
  clampView();
}

function zoomTime(x, factor) {
  const L = state.layout;
  if (!L) return;
  const index = indexAt(x, L);
  state.view.i0 = index - (index - state.view.i0) * factor;
  state.view.i1 = index + (state.view.i1 - index) * factor;
  state.timeFull = false;
  clampView();
}

function resetView() {
  if (!state.data) return;
  state.timeFull = true;
  state.priceDirty = false;
  const n = state.data.times.length;
  state.view.i0 = -0.5;
  state.view.i1 = n - 0.5;
  state.view.lo = state.data.price_view[0];
  state.view.hi = state.data.price_view[1];
  clampView();
  requestDraw();
}

function resize() {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.max(1, wrap.clientWidth);
  const h = Math.max(1, wrap.clientHeight);
  state.dpr = dpr;
  state.cssW = w;
  state.cssH = h;
  canvas.width = Math.floor(w * dpr);
  canvas.height = Math.floor(h * dpr);
  requestDraw();
}

function snapshot() {
  if (!state.data) return;
  const pad = 54;
  const out = document.createElement("canvas");
  out.width = canvas.width;
  out.height = canvas.height + Math.floor(pad * state.dpr);
  const ctx = out.getContext("2d");
  ctx.fillStyle = "#10120e";
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(canvas, 0, Math.floor(pad * state.dpr));
  ctx.fillStyle = "#e7ecdf";
  ctx.font = `${15 * state.dpr}px sans-serif`;
  ctx.fillText($("title").textContent, 16 * state.dpr, 22 * state.dpr);
  ctx.fillStyle = "#8f9a84";
  ctx.font = `${12 * state.dpr}px sans-serif`;
  ctx.fillText("Open Liq estimate · not exchange liquidation records", 16 * state.dpr, 42 * state.dpr);
  const link = document.createElement("a");
  const name = `${state.exchange}-${state.coin}-${state.range}-model${state.model}.png`.toLowerCase();
  link.href = out.toDataURL("image/png");
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function setAuto(seconds) {
  if (state.timer) clearInterval(state.timer);
  state.timer = 0;
  state.auto = seconds;
  if (seconds > 0) {
    state.timer = setInterval(() => {
      if (document.hidden || state.asof != null || state.replay) return;
      load("auto");
    }, seconds * 1000);
  }
  persist();
}

// Replay: one span is loaded, then frames are fetched by index, never skipped.
function frameAsof(r, idx) {
  return Math.round((r.first + idx * r.step) / 1000);
}

function syncReplay() {
  const r = state.replay;
  const canTravel = historyOn();
  $("rp-load").disabled = !canTravel || state.asof == null;
  $("to-input").disabled = !canTravel;
  $("to-field").classList.toggle("disabled", !canTravel);
  ["rp-back", "rp-play", "rp-fwd", "rp-scrub"].forEach((id) => {
    $(id).disabled = !r;
  });
  $("rp-play").textContent = r && r.playing ? "Pause" : "Play";
  $("rp-play").setAttribute("aria-label", r && r.playing ? "Pause" : "Play");
  if (r) {
    $("rp-scrub").max = String(r.frames - 1);
    $("rp-scrub").value = String(r.idx);
    $("rp-time").textContent = `${fmtStamp(frameAsof(r, r.idx) * 1000, r.step < 86400000)} · ${r.idx + 1}/${r.frames}`;
  } else if (!canTravel) {
    $("rp-time").textContent = "Replay is Binance pair only";
  } else {
    $("rp-time").textContent = state.asof == null ? "Set As of, then Load replay" : "Ready to load";
  }
}

function stopReplay() {
  const r = state.replay;
  if (!r) return;
  r.gen += 1;
  r.playing = false;
  r.controller.abort();
  state.replay = null;
  syncReplay();
}

async function startReplay(keepIdx) {
  const prev = state.replay;
  const idx = keepIdx && prev ? prev.idx : 0;
  if (prev) {
    prev.gen += 1;
    prev.controller.abort();
  }
  state.replay = null;
  syncReplay();
  const toText = $("to-input").value;
  const params = new URLSearchParams({ symbol: state.coin, range: state.range, from: String(state.asof) });
  if (state.bar) params.set("bar", state.bar);
  if (toText) params.set("to", String(parseCotInput(toText)));
  setLoading(true, "Loading replay… the first load of a long span downloads one file per day and can take minutes");
  try {
    const response = await fetch("/api/replay?" + params.toString());
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Could not load the replay.");
    state.replay = {
      gen: 1,
      first: body.first_asof,
      step: body.bar_seconds * 1000,
      frames: body.frames,
      idx: Math.min(idx, body.frames - 1),
      playing: false,
      speed: Number($("rp-speed").value) || 2,
      cache: new Map(),
      controller: new AbortController(),
      reloaded: Boolean(keepIdx),
      days: body.days,
      note: body.note,
    };
    syncReplay();
    await showFrame(state.replay.idx);
  } catch (err) {
    fail(err.message || "Could not load the replay.");
  } finally {
    setLoading(false);
  }
}

function fetchFrame(r, idx) {
  if (r.cache.has(idx)) return r.cache.get(idx);
  const params = mapParams({ asof: frameAsof(r, idx), replay: 1 });
  const promise = fetch("/api/heatmap?" + params.toString(), { signal: r.controller.signal }).then(async (response) => {
    const body = await response.json();
    if (response.status === 409) {
      const error = new Error(body.error || "Replay expired");
      error.expired = true;
      throw error;
    }
    if (!response.ok) throw new Error(body.error || "Could not load this frame.");
    return body;
  });
  promise.catch(() => r.cache.delete(idx));
  r.cache.set(idx, promise);
  return promise;
}

async function showFrame(idx) {
  const r = state.replay;
  if (!r) return false;
  const gen = r.gen;
  r.idx = clamp(idx, 0, r.frames - 1);
  syncReplay();
  try {
    const body = await fetchFrame(r, r.idx);
    if (state.replay !== r || gen !== r.gen) return false;
    applyPayload(body);
    if (r.note && r.idx === 0) showBanner("info", [r.note]);
    for (const ahead of [r.idx + 1, r.idx + 2]) {
      if (ahead < r.frames) fetchFrame(r, ahead).catch(() => {});
    }
    for (const key of r.cache.keys()) {
      if (key < r.idx - 2 || key > r.idx + 2) r.cache.delete(key);
    }
    return true;
  } catch (err) {
    if (err.name === "AbortError" || state.replay !== r) return false;
    if (err.expired && !r.reloaded) {
      await startReplay(true);
      return false;
    }
    r.playing = false;
    syncReplay();
    fail(err.message || "Could not load this frame.");
    return false;
  }
}

async function playLoop() {
  const r = state.replay;
  if (!r) return;
  const gen = r.gen;
  while (state.replay === r && r.playing && gen === r.gen && r.idx < r.frames - 1) {
    const started = performance.now();
    const shown = await showFrame(r.idx + 1);
    if (!shown) break;
    const wait = 1000 / r.speed - (performance.now() - started);
    if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
  }
  if (state.replay === r && gen === r.gen) {
    r.playing = false;
    syncReplay();
  }
}

function togglePlay() {
  const r = state.replay;
  if (!r) return;
  r.playing = !r.playing;
  // Play from the last frame starts over; playLoop shows idx + 1 first.
  if (r.playing && r.idx >= r.frames - 1) r.idx = -1;
  if (r.idx >= 0) syncReplay();
  if (r.playing) {
    tip.hidden = true;
    playLoop();
  }
}

function stepReplay(delta) {
  const r = state.replay;
  if (!r) return;
  r.playing = false;
  showFrame(r.idx + delta);
}

function wireReplay() {
  $("rp-load").addEventListener("click", () => startReplay(false));
  $("rp-play").addEventListener("click", togglePlay);
  $("rp-back").addEventListener("click", () => stepReplay(-1));
  $("rp-fwd").addEventListener("click", () => stepReplay(1));
  $("rp-speed").addEventListener("change", () => {
    if (state.replay) state.replay.speed = Number($("rp-speed").value) || 2;
  });
  $("rp-scrub").addEventListener("input", () => {
    const r = state.replay;
    if (!r) return;
    r.playing = false;
    showFrame(Number($("rp-scrub").value));
  });
  document.addEventListener("keydown", (event) => {
    if (!state.replay) return;
    const target = event.target;
    if (target && (target.closest("input, select, textarea, button") || target.isContentEditable)) return;
    if (event.key === " ") {
      event.preventDefault();
      togglePlay();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      stepReplay(-1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      stepReplay(1);
    }
  });
}

function wire() {
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.model = Number(button.dataset.model);
      syncControls();
      persist();
      if (state.replay) {
        state.replay.cache.clear();
        showFrame(state.replay.idx);
      } else {
        load("model");
      }
    });
  });
  document.querySelectorAll("#mode-toggle button").forEach((button) => {
    button.addEventListener("click", () => {
      stopReplay();
      state.mode = button.dataset.mode;
      syncControls();
      persist();
      load("mode");
    });
  });
  $("exchange").addEventListener("change", () => {
    stopReplay();
    state.exchange = $("exchange").value;
    state.timeFull = true;
    state.priceDirty = false;
    syncControls();
    persist();
    load("exchange");
  });
  $("range").addEventListener("change", () => {
    stopReplay();
    state.range = $("range").value;
    if (!barFits(state.range, state.bar)) state.bar = "";
    state.timeFull = true;
    state.priceDirty = false;
    syncControls();
    persist();
    load("range");
  });
  $("bar").addEventListener("change", () => {
    stopReplay();
    state.bar = barFits(state.range, $("bar").value) ? $("bar").value : "";
    syncControls();
    persist();
    load("bar");
  });
  $("asof-input").addEventListener("change", () => {
    stopReplay();
    const text = $("asof-input").value;
    state.asof = text ? parseCotInput(text) : null;
    syncControls();
    load("asof");
  });
  $("live").addEventListener("click", () => {
    stopReplay();
    state.asof = null;
    syncControls();
    load("live");
  });
  const coin = $("coin");
  const commitCoin = () => {
    const next = coin.value.trim().toUpperCase();
    if (!next || next === state.coin) return;
    stopReplay();
    state.coin = next;
    coin.value = next;
    state.timeFull = true;
    state.priceDirty = false;
    persist();
    load("coin");
  };
  coin.addEventListener("change", commitCoin);
  coin.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitCoin();
    }
  });
  $("refresh").addEventListener("click", () => load("refresh"));
  $("shot").addEventListener("click", snapshot);
  $("full").addEventListener("click", () => {
    const card = $("chart-card");
    if (!document.fullscreenElement) card.requestFullscreen().catch(() => {});
    else document.exitFullscreen();
  });
  $("threshold").addEventListener("input", () => {
    state.threshold = Number($("threshold").value);
    $("th-val").textContent = state.threshold.toFixed(2);
    state.heatDirty = true;
    persist();
    requestDraw();
  });
  $("liqmap").addEventListener("change", () => {
    state.showMap = $("liqmap").checked;
    persist();
    requestDraw();
  });
  $("candles").addEventListener("change", () => {
    state.showCandles = $("candles").checked;
    persist();
    requestDraw();
  });
  $("auto").addEventListener("change", () => setAuto(Number($("auto").value) || 0));
  wireReplay();
  $("fit").addEventListener("click", () => {
    if (!state.data) return;
    state.view.lo = state.data.price_view[0];
    state.view.hi = state.data.price_view[1];
    state.priceDirty = true;
    clampView();
    requestDraw();
  });
  $("wide").addEventListener("click", () => {
    if (!state.data) return;
    const centers = state.data.centersA;
    state.view.lo = centers[0];
    state.view.hi = centers[centers.length - 1];
    state.priceDirty = true;
    clampView();
    requestDraw();
  });

  canvas.addEventListener("wheel", (event) => {
    if (!state.data) return;
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const factor = event.deltaY > 0 ? 1.12 : 1 / 1.12;
    if (event.shiftKey) zoomTime(x, factor);
    else zoomPrice(y, factor);
    requestDraw();
  }, { passive: false });

  canvas.addEventListener("pointerdown", (event) => {
    if (!state.data || !state.layout) return;
    canvas.setPointerCapture(event.pointerId);
    state.drag = {
      x: event.clientX,
      y: event.clientY,
      i0: state.view.i0,
      i1: state.view.i1,
      lo: state.view.lo,
      hi: state.view.hi,
    };
  });
  canvas.addEventListener("pointermove", (event) => {
    updateHover(event.clientX, event.clientY);
    if (!state.drag || !state.layout) return;
    const dx = event.clientX - state.drag.x;
    const dy = event.clientY - state.drag.y;
    const L = state.layout;
    const spanT = state.drag.i1 - state.drag.i0;
    const spanP = state.drag.hi - state.drag.lo;
    state.view.i0 = state.drag.i0 - (dx / L.plotW) * spanT;
    state.view.i1 = state.drag.i1 - (dx / L.plotW) * spanT;
    state.view.lo = state.drag.lo + (dy / L.plotH) * spanP;
    state.view.hi = state.drag.hi + (dy / L.plotH) * spanP;
    state.timeFull = false;
    state.priceDirty = true;
    clampView();
    requestDraw();
  });
  const endDrag = () => { state.drag = null; };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("pointerleave", () => {
    if (state.drag) return;
    state.hover = null;
    tip.hidden = true;
    requestDraw();
  });
  canvas.addEventListener("dblclick", resetView);

  document.addEventListener("fullscreenchange", () => setTimeout(resize, 50));
  new ResizeObserver(resize).observe(wrap);
}

async function bootStatus() {
  try {
    const response = await fetch("/api/status");
    const body = await response.json();
    state.models = body.models || [];
    (body.models || []).forEach((model) => {
      const button = document.querySelector(`.tab[data-model="${model.id}"]`);
      if (button) button.title = model.name + " — " + model.blurb;
    });
    const list = $("coins");
    (body.coins || []).forEach((coin) => {
      if (![...list.options].some((opt) => opt.value === coin)) {
        const option = document.createElement("option");
        option.value = coin;
        list.appendChild(option);
      }
    });
    if (!state.data) renderHow((body.models || []).find((model) => model.id === state.model) || body.models[0]);
  } catch (_err) {
    /* the heatmap request reports its own error */
  }
}

restore();
buildSwatches();
syncControls();
setAuto(state.auto);
wire();
resize();
bootStatus();
load("boot");
