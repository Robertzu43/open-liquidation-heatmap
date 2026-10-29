/* Open liquidation heatmap. The server sends little-endian float32 grids.
   Threshold only remaps color; it does not refetch. */

const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const MODEL_NAMES = { 1: "Wide clusters", 2: "Tight magnets", 3: "Flow skew" };
const timeZone = () => state.timezone === "local" ? localZone : "UTC";
const zoneLabel = () => state.timezone === "local" ? localZone : "UTC";
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
  showLiqs: true,
  alertOn: false,
  alertPct: 0.5,
  auto: 60,
  timezone: "utc",
  watchlist: ["BTC", "ETH", "SOL"],
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
  requestedAt: 0,
  frame: 0,
  dpr: 1,
  cssW: 800,
  cssH: 500,
  layout: null,
  hover: null,
  drag: null,
  models: [],
  replay: null,
  replaySeq: 0,
  replayController: null,
  // Side requests (context, liquidations, watchlist) never replace the chart.
  sideSeq: 0,
  sideController: null,
  context: null,
  liqs: null,
  liqSources: ["okx"],
  watchSeq: 0,
  watchBusy: false,
  watchQueued: null,
  watch: { key: "", items: {}, at: 0, sig: "" },
  alerts: {},
  fundingNext: null,
};

const $ = (id) => document.getElementById(id);
const canvas = $("chart");
const wrap = $("wrap");
const tip = $("tip");
const banner = $("banner");

// Past views and bar choice are Binance pair-only; bars must give 2 to 3,000 per range.
const BAR_S = { "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "12h": 43200, "1d": 86400 };
const RANGE_S = { "12h": 43200, "24h": 86400, "3d": 259200, "7d": 604800, "30d": 2592000, "90d": 7776000, "180d": 15552000, "1y": 31536000, "2y": 63072000 };
const DEFAULT_BAR = { "12h": "5m", "24h": "5m", "3d": "15m", "7d": "1h", "30d": "4h", "90d": "4h", "180d": "12h", "1y": "1d", "2y": "1d" };
const VENUES = ["okx", "binance", "bybit", "demo", "aggregate"];
const VENUE_NAMES = { okx: "OKX", binance: "Binance", bybit: "Bybit", demo: "Demo", aggregate: "All venues" };
const AUTO_CHOICES = [0, 30, 60, 300];
const WATCH_MAX = 6;
const WATCH_MIN_MS = 20000;
const COIN_RE = /^[A-Z0-9]{1,16}$/;
const COIN_HINT = "Use 1–16 letters or digits, like BTC or ETHUSDT.";

// The server's rule: BTC, eth-usdt and SOL/USDT name a coin; anything else is "".
function normalizeCoin(raw) {
  const coin = String(raw || "").trim().toUpperCase().replace(/[-_/]/g, "").replace(/USDT$/, "");
  return COIN_RE.test(coin) && coin !== "USDT" ? coin : "";
}


function barFits(range, bar) {
  if (!bar) return true;
  const bars = Math.floor(RANGE_S[range] / BAR_S[bar]);
  return bars >= 2 && bars <= 3000;
}

function historyOn() {
  return state.exchange === "binance" && state.mode === "pair";
}

function pastView() {
  return historyOn() && state.asof != null;
}

// "All venues" is the aggregate: exchange "aggregate" always travels with mode "symbol".
function normalizeVenue() {
  if (state.mode === "symbol" || state.exchange === "aggregate") {
    state.exchange = "aggregate";
    state.mode = "symbol";
  } else {
    state.mode = "pair";
  }
}

function setVenue(value) {
  state.exchange = VENUES.includes(value) ? value : "okx";
  state.mode = state.exchange === "aggregate" ? "symbol" : "pair";
}

function reducedMotion() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function toInputValue(seconds) {
  const date = new Date(seconds * 1000);
  if (state.timezone !== "local") return date.toISOString().slice(0, 16);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function parseInputValue(text) {
  const ms = Date.parse(text + (state.timezone === "local" ? "" : "Z"));
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
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
  if (abs >= 1e9) return sign + "$" + (abs / 1e9).toFixed(1) + "B";
  if (abs >= 1e6) return sign + "$" + (abs / 1e6).toFixed(1) + "M";
  if (abs >= 1e3) return sign + "$" + (abs / 1e3).toFixed(1) + "K";
  return sign + "$" + abs.toFixed(0);
}

function num(value) {
  return typeof value === "number" && Number.isFinite(value);
}

// Signed percent with a true minus sign, e.g. +1.56% / −0.24%.
function fmtPct(value, digits) {
  if (!num(value)) return "—";
  const d = digits == null ? 2 : digits;
  const text = Math.abs(value).toFixed(d);
  if (Number(text) === 0) return (0).toFixed(d) + "%";
  return (value < 0 ? "−" : "+") + text + "%";
}

// Funding is a fraction per interval: 0.0001 is +0.0100%.
function fmtRate(rate) {
  if (!num(rate)) return "—";
  const text = Math.abs(rate * 100).toFixed(4);
  if (Number(text) === 0) return "0.0000%";
  return (rate < 0 ? "−" : "+") + text + "%";
}

function fmtCountdown(ms) {
  if (!(ms > 0)) return "settling";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

// Clock time, with the date when it is not today in the chosen zone.
function fmtClock(ms) {
  const zone = timeZone();
  const day = (value) => new Intl.DateTimeFormat("en-GB", { timeZone: zone, day: "2-digit", month: "short" }).format(new Date(value));
  const clock = new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(ms));
  const prefix = day(ms) === day(Date.now()) ? "" : day(ms) + " ";
  return prefix + clock + " " + (state.timezone === "local" ? "local" : "UTC");
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
    timeZone: timeZone(),
    day: "2-digit",
    month: "short",
    year: "numeric",
    hourCycle: "h23",
  };
  if (withTime) {
    opts.hour = "2-digit";
    opts.minute = "2-digit";
  }
  return new Intl.DateTimeFormat("en-GB", opts).format(new Date(ms)) + " " + zoneLabel();
}

function fmtAxis(ms, withTime, narrow) {
  const opts = { timeZone: timeZone(), hourCycle: "h23" };
  if (narrow && withTime) {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: timeZone(), day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
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
      showLiqs: state.showLiqs,
      alertOn: state.alertOn,
      alertPct: state.alertPct,
      autoRefresh: state.auto,
      timezone: state.timezone,
      watchlist: state.watchlist,
    }));
  } catch (_err) {
    /* private mode */
  }
  updateUrl();
}

function restore() {
  try {
    const raw = localStorage.getItem(STORE);
    if (!raw) return;
    const saved = JSON.parse(raw);
    const ranges = ["12h", "24h", "3d", "7d", "30d", "90d", "180d", "1y", "2y"];
    if (VENUES.includes(saved.exchange)) state.exchange = saved.exchange;
    state.coin = normalizeCoin(saved.coin) || state.coin;
    if (ranges.includes(saved.range)) state.range = saved.range;
    if ([1, 2, 3].includes(Number(saved.model))) state.model = Number(saved.model);
    if (saved.mode === "pair" || saved.mode === "symbol") state.mode = saved.mode;
    if (saved.bar in BAR_S && barFits(state.range, saved.bar)) state.bar = saved.bar;
    if (THEMES[saved.theme]) state.theme = saved.theme;
    if (Number.isFinite(saved.threshold)) state.threshold = clamp(saved.threshold, 0, 1);
    if (typeof saved.showMap === "boolean") state.showMap = saved.showMap;
    if (typeof saved.showCandles === "boolean") state.showCandles = saved.showCandles;
    if (typeof saved.showLiqs === "boolean") state.showLiqs = saved.showLiqs;
    if (typeof saved.alertOn === "boolean") state.alertOn = saved.alertOn;
    if (Number.isFinite(saved.alertPct)) state.alertPct = clamp(saved.alertPct, 0.05, 10);
    if (["utc", "local"].includes(saved.timezone)) state.timezone = saved.timezone;
    // A stored choice wins, Off included. Older builds always stored auto: 0,
    // so only a positive legacy value counts as a choice.
    if (AUTO_CHOICES.includes(Number(saved.autoRefresh))) state.auto = Number(saved.autoRefresh);
    else if (AUTO_CHOICES.includes(Number(saved.auto)) && Number(saved.auto) > 0) state.auto = Number(saved.auto);
    if (Array.isArray(saved.watchlist)) {
      const list = [];
      saved.watchlist.forEach((coin) => {
        const id = normalizeCoin(coin);
        if (id && !list.includes(id) && list.length < WATCH_MAX) list.push(id);
      });
      state.watchlist = list;
    }
  } catch (_err) {
    /* ignore broken storage */
  }
  normalizeVenue();
}

function restoreUrl() {
  const params = new URLSearchParams(location.search);
  if (VENUES.includes(params.get("exchange"))) setVenue(params.get("exchange"));
  state.coin = normalizeCoin(params.get("coin")) || state.coin;
  if (params.get("range") in RANGE_S) state.range = params.get("range");
  if ([1, 2, 3].includes(Number(params.get("model")))) state.model = Number(params.get("model"));
  // Older links used ?mode=symbol for the aggregate; it still means All venues.
  if (params.get("mode") === "symbol") setVenue("aggregate");
  normalizeVenue();
  if (THEMES[params.get("theme")]) state.theme = params.get("theme");
  if (["utc", "local"].includes(params.get("tz"))) state.timezone = params.get("tz");
  if (params.has("threshold") && Number.isFinite(Number(params.get("threshold")))) state.threshold = clamp(Number(params.get("threshold")), 0, 1);
  if (params.has("profile")) state.showMap = params.get("profile") === "1";
  if (params.has("candles")) state.showCandles = params.get("candles") !== "0";
  if (params.has("range") || params.has("bar")) state.bar = "";
  if (params.get("bar") in BAR_S && barFits(state.range, params.get("bar"))) state.bar = params.get("bar");
  if (!barFits(state.range, state.bar)) state.bar = "";
  const asof = Number(params.get("asof"));
  if (historyOn() && asof > 0 && Number.isSafeInteger(asof)) state.asof = asof;
}

function updateUrl() {
  const params = mapParams();
  params.delete("symbol");
  params.set("coin", state.coin);
  params.set("tz", state.timezone);
  params.set("theme", state.theme);
  params.set("threshold", String(state.threshold));
  params.set("profile", state.showMap ? "1" : "0");
  params.set("candles", state.showCandles ? "1" : "0");
  history.replaceState(null, "", "?" + params.toString());
}

function announce(message) { $("announcement").textContent = message; }

function syncControls() {
  $("exchange").value = state.mode === "symbol" ? "aggregate" : state.exchange;
  $("coin").value = state.coin;
  $("range").value = state.range;
  $("threshold").value = String(state.threshold);
  $("th-val").textContent = state.threshold.toFixed(2);
  $("liqmap").checked = state.showMap;
  $("candles").checked = state.showCandles;
  $("liqs").checked = state.showLiqs;
  $("alert-on").checked = state.alertOn;
  if (document.activeElement !== $("alert-pct")) $("alert-pct").value = String(state.alertPct);
  $("auto").value = String(state.auto);
  $("timezone").value = state.timezone;
  $("timezone-note").textContent = zoneLabel() + " · event times";
  document.querySelectorAll(".tz-label").forEach(el => { el.textContent = state.timezone === "local" ? "local" : "UTC"; });
  $("model-name").textContent = MODEL_NAMES[state.model];
  document.querySelectorAll(".tab").forEach((button) => {
    const on = Number(button.dataset.model) === state.model;
    button.classList.toggle("on", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const canTravel = historyOn();
  $("history-controls").hidden = !canTravel;
  $("history-note").textContent = canTravel ? "Custom span: set As of and an optional To, then Load replay. Archive availability is not proof of point-in-time collection." : "Custom As of and replay need Binance single venue. The presets above switch to it for you.";
  $("bar").value = state.bar;
  $("bar").disabled = !canTravel;
  $("bar-field").classList.toggle("disabled", !canTravel);
  $("asof-input").value = state.asof == null ? "" : toInputValue(state.asof);
  $("asof-input").disabled = !canTravel;
  $("asof-field").classList.toggle("disabled", !canTravel);
  $("live").disabled = !canTravel || state.asof == null;
  if ($("rp-load")) syncReplay();
  document.querySelectorAll(".swatch").forEach((button) => {
    button.classList.toggle("on", button.dataset.theme === state.theme);
    button.setAttribute("aria-pressed", String(button.dataset.theme === state.theme));
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
  wrap.setAttribute("aria-busy", on ? "true" : "false");
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
  stopReplay();
  const seq = ++state.seq;
  state.requestedAt = Date.now();
  if (state.controller) state.controller.abort();
  // The map goes first. Aborting side requests only drops their responses; the server still finishes them.
  cancelSide();
  const controller = new AbortController();
  state.controller = controller;
  const quiet = reason === "auto" && state.data;
  const label = `Loading ${VENUE_NAMES[state.exchange] || state.exchange.toUpperCase()} · ${state.coin} · ${rangeLabel(state.range)}`;
  setLoading(true, quiet ? "Refreshing…" : label);
  const params = mapParams();
  try {
    const response = await fetchRetry("/api/heatmap?" + params.toString(), controller.signal, () => {
      if (seq === state.seq) setLoading(true, quiet ? "Refreshing…" : "Server busy · retrying…");
    });
    const body = await response.json();
    if (seq !== state.seq) return;
    if (!response.ok) throw new Error(body.error || "Request failed");
    applyPayload(body, quiet);
    afterLoad(body, reason);
  } catch (err) {
    if (err.name === "AbortError" || seq !== state.seq) return;
    const message = err.message || "Could not load this map.";
    // A failed quiet refresh keeps the map on screen; only a chosen load clears it.
    if (quiet && state.data) showBanner("warn", [`Refresh failed: ${message.replace(/\.$/, "")}. Showing the map from ${fmtClock(state.data.generated_at)}.`]);
    else fail(message);
  } finally {
    if (seq === state.seq) setLoading(false);
  }
}

function fail(message) {
  tip.hidden = true;
  state.hover = null;
  state.data = null;
  state.heatCanvas = null;
  showBanner("error", [message]);
  $("summary").textContent = message;
  $("imbalance").querySelector("tbody").innerHTML = "";
  $("levels").querySelector("tbody").innerHTML = "";
  $("resting").textContent = "";
  $("tags").innerHTML = "";
  $("asof").textContent = "No map loaded · refresh to retry";
  $("data-status").textContent = "Data unavailable";
  $("data-status").className = "status error";
  $("provenance").textContent = "No current dataset. Refresh to retry the selected source.";
  $("quality-detail").textContent = "";
  $("source-brief").textContent = "No current dataset";
  $("chart-price").textContent = "";
  cancelSide();
  renderReading(null);
  renderMarket();
  renderLiqNote();
  renderWatch();
  syncReplayBar();
  announce("Data unavailable. " + message);
  drawMessage(message);
}

function applyPayload(data, quiet) {
  const prev = state.data;
  const status = $("data-status").className;
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
  const replay = data.replay && state.replay;
  if (replay && replay.anchorFirst == null) {
    replay.anchorFirst = data.times[0];
    replay.anchorN = n;
    state.timeFull = true;
    state.priceDirty = false;
  }
  if (state.timeFull) {
    if (replay) setReplayView(replay);
    else {
      state.view.i0 = -0.5;
      state.view.i1 = n - 0.5;
    }
  }
  if (!state.priceDirty && !keepView) {
    state.view.lo = data.price_view[0];
    state.view.hi = data.price_view[1];
  } else if (!state.priceDirty) {
    // Replay frames only widen the price range, so new candles stay in view without the axis jumping back.
    state.view.lo = Math.min(state.view.lo, data.price_view[0]);
    state.view.hi = Math.max(state.view.hi, data.price_view[1]);
  }
  clampView();
  $("title").textContent = data.title;
  document.title = data.title + " · Open Liq";
  const sourceNames = {
    okx: "OKX live",
    "binance-live": "Binance live",
    "binance-vision": "Binance archive",
    bybit: "Bybit live",
    demo: "Synthetic demo",
    aggregate: "Venue aggregate",
  };
  const withTime = data.bar_seconds < 86400;
  $("asof").textContent = `${sourceNames[data.source] || data.source} · last bar ${fmtStamp(data.as_of, withTime)} · generated ${fmtStamp(data.generated_at, true)}`;
  renderProvenance(data);
  const lines = [];
  if (replay) {
    lines.push("Replay: price and modeled exposure update as each bar arrives; the empty area is future time. Earlier exposure can change as the lookback moves.");
    if (replay.note) lines.push(replay.note);
  }
  if (data.asof) lines.push(`As of ${fmtStamp(data.asof, true)} · ${data.bar} bars`);
  (data.warnings || []).forEach((line) => lines.push(line));
  if (data.note && !lines.includes(data.note)) lines.push(data.note);
  // A past view names the archive on purpose; only its own warnings are serious.
  const serious = data.stale || (!data.asof && lines.some((line) => /lag|left out|archive|synthetic|only go back/i.test(line)));
  showBanner(serious ? "warn" : "info", lines);
  renderAnalysis(data);
  renderReading(data);
  renderMarket();
  renderLiqNote();
  syncReplayBar();
  renderHow(data.model);
  canvas.setAttribute("aria-label", (data.analysis || {}).summary || data.title);
  // Quiet auto refreshes speak only when the status changes, e.g. to stale.
  if (!quiet || $("data-status").className !== status) announce("Loaded " + data.title + ". " + $("data-status").textContent);
  requestDraw();
}

function replayOffset(data) {
  const replay = data.replay && state.replay;
  return replay && replay.anchorFirst != null ? Math.round((data.times[0] - replay.anchorFirst) / replay.step) : 0;
}

function setReplayView(replay) {
  const future = Math.max(1, Math.min(replay.frames - 1, 150));
  const context = Math.min(12, Math.max(1, Math.ceil(future / 5)));
  const follow = Math.max(0, replay.idx - Math.floor(future * 0.85));
  const anchor = replay.anchorN - 1;
  state.view.i0 = Math.max(-0.5, anchor - context + follow - 0.5);
  state.view.i1 = Math.min(replay.anchorN + replay.frames - 1 - 0.5, anchor + future + follow + 0.5);
}

function renderProvenance(data) {
  const demo = data.source === "demo";
  const quality = data.quality || {};
  const status = quality.status || (data.stale ? "stale" : "reported");
  const lag = Math.max(0, Math.round((Date.now() - data.as_of) / 60000));
  const freshness = data.asof || /archive/.test(data.source) ? "Archive history" : data.stale ? "Stale source" : "Last bar " + (lag < 60 ? lag + "m" : Math.floor(lag / 60) + "h") + " ago";
  $("data-status").textContent = demo ? "Synthetic demo" : freshness;
  $("data-status").className = "status" + (demo || data.stale || status === "degraded" ? " warn" : "");
  const model = data.model || {};
  const metadata = data.model_metadata || {};
  const version = data.model_version || model.version || "unversioned";
  const revision = data.data_revision || data.data_fingerprint || (data.provenance || {}).data_revision || "not provided";
  const sources = Array.isArray(data.sources) ? data.sources.join(", ") : (data.source || "unknown");
  const shortRevision = String(revision).length > 28 ? String(revision).slice(0, 19) + "…" + String(revision).slice(-8) : revision;
  $("provenance").title = "Full data revision: " + revision;
  $("provenance").textContent = `${demo ? "Synthetic demonstration · no market observations" : "Source: " + sources} · last event/bar ${fmtStamp(data.as_of, true)} · generated ${fmtStamp(data.generated_at, true)} · model ${version} · revision ${shortRevision}`;
  const coverage = quality.coverage || {};
  const covered = [ ["candle_observed", "candles"], ["oi_observed", "open interest"] ].map(([key, label]) => {
    const item = coverage[key];
    return item && item.total > 0 ? `${Math.round(item.observed / item.total * 100)}% ${label} observed` : null;
  }).filter(Boolean);
  const history = metadata.insufficient_history ? ` Inventory starts at ${fmtStamp(metadata.warmup_anchor_ms || data.times[0], true)}; earlier positions are unknown.` : "";
  $("source-brief").textContent = `${demo ? "Synthetic demo" : sources} · quality ${status}`;
  $("quality-detail").textContent = `Quality: ${status}. ${demo ? "Use the demo to explore controls; its levels are not market evidence." : "Generation time is not observation freshness. " + covered.join(" · ") + ". Historical availability: " + (quality.historical_availability || "unknown") + "."}${history}`;
}

function renderAnalysis(data) {
  const analysis = data.analysis || {};
  $("summary").textContent = analysis.summary || "";
  $("chart-price").textContent = analysis.price == null ? "" : "$" + fmtPx(analysis.price);
  const modelName = (data.model || {}).name || MODEL_NAMES[(data.model || {}).id] || "";
  $("chart-context").textContent = [rangeLabel(data.range || state.range), data.bar || "", modelName, state.timezone === "local" ? "Local" : "UTC"].filter(Boolean).join(" · ");
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
  $("resting").textContent = `Modeled long ${fmtUsd(analysis.resting_long_usd || 0)} · short ${fmtUsd(analysis.resting_short_usd || 0)}`;
  const tags = $("tags");
  tags.innerHTML = "";
  (analysis.levels || []).slice(0, 4).forEach((level) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = level.side === "short" ? "short" : "long";
    button.textContent = `${level.side === "short" ? "Short liqs" : "Long liqs"} ${fmtPx(level.price)} · ${fmtUsd(level.usd)}`;
    button.addEventListener("click", () => focusPrice(level.price));
    tags.appendChild(button);
  });
}

function renderHow(model) {
  if (!model) return;
  const weights = (model.weights || []).map((row) => `<li>${row.leverage}× · ${(row.weight * 100).toFixed(0)}%</li>`).join("");
  const flow = model.flow
    ? "New open interest uses the available flow prior where supported, with a neutral fallback. Account long/short ratios are not taker flow."
    : "New open interest is split evenly between longs and shorts.";
  $("how-body").innerHTML = `
    <p>${escapeHtml(model.blurb || "")}</p>
    <p>Maintenance margin rate (MMR) is ${(model.mmr * 100).toFixed(1)}%. Entry for a bar is <span class="formula">${escapeHtml(model.entry)}</span>.</p>
    <p>Long liquidation = <span class="formula">${escapeHtml(model.long_formula)}</span></p>
    <p>Short liquidation = <span class="formula">${escapeHtml(model.short_formula)}</span></p>
    <p>${flow} A later candle removes a cluster when price trades through it.</p>
    <p>Leverage mix</p>
    <ul>${weights}</ul>
    <p>Decay coefficient ${model.decay} per ${escapeHtml(String(((state.data || {}).model_metadata || {}).decay_reference_seconds || "reference"))} seconds of elapsed time. Volume churn ${model.churn}. Kernel ${(model.kernel || []).map((v) => Number(v).toFixed(2)).join(" · ")}.</p>
    <p>The color scale is relative to this window. It is not a count of real liquidations.</p>
    ${(state.data && Array.isArray(state.data.assumptions) ? state.data.assumptions : []).map(item => `<p>${escapeHtml(item)}</p>`).join("")}`;
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
  const replay = data.replay && state.replay;
  const n = replay && replay.anchorN ? replay.anchorN + replay.frames - 1 : data.times.length;
  let span = state.view.i1 - state.view.i0;
  span = clamp(span, Math.min(8, n), n);
  const timeMid = clamp((state.view.i0 + state.view.i1) / 2, -0.5 + span / 2, n - 0.5 - span / 2);
  state.view.i0 = timeMid - span / 2;
  state.view.i1 = timeMid + span / 2;
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
    const offset = replayOffset(data);
    const x0 = xOf(offset - 0.5, L);
    const x1 = xOf(offset + nT - 0.5, L);
    const yTop = yOf(centers[bins - 1] + half, L);
    const yBot = yOf(centers[0] - half, L);
    // Preserve the overview's soft gradients, but keep the underlying price/time
    // bins distinct when the user zooms in instead of enlarging a blurred image.
    ctx.imageSmoothingEnabled = state.timeFull && !state.priceDirty;
    ctx.drawImage(state.heatCanvas, x0, yTop, x1 - x0, yBot - yTop);
  }
  if (state.showCandles) drawCandles(ctx, L, data);
  drawLiqDots(ctx, L, data);
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
  const offset = replayOffset(data);
  const start = Math.max(0, Math.floor(state.view.i0 - offset) - 1);
  const end = Math.min(nT - 1, Math.ceil(state.view.i1 - offset) + 1);
  const colW = Math.abs(xOf(1, L) - xOf(0, L));
  const bodyW = Math.max(1, Math.min(11, colW * 0.62));
  for (let i = start; i <= end; i++) {
    const open = data.openA[i];
    const high = data.highA[i];
    const low = data.lowA[i];
    const close = data.closeA[i];
    const x = xOf(i + offset, L);
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
    const replay = data.replay && state.replay;
    const snapped = clamp(Math.round(index - replayOffset(data)), 0, data.times.length - 1);
    const x = xOf(index, L);
    const timestamp = replay && replay.anchorFirst != null ? replay.anchorFirst + Math.round(index) * replay.step : data.times[snapped];
    const text = fmtAxis(timestamp, withTime, narrow);
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
    line("rgba(224, 122, 114, 0.95)", spotBin - 1, -1, -1, data.longA);
    line("rgba(125, 206, 160, 0.95)", spotBin, bins, 1, data.shortA);
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
  const index = Math.round(indexAt(x, L)) - replayOffset(data);
  if (index < 0 || index >= data.times.length) {
    state.hover = null;
    tip.hidden = true;
    requestDraw();
    return;
  }
  const price = priceAt(y, L);
  const bin = nearestBin(price, data.centersA);
  const usd = data.heatA[bin * data.times.length + index];
  state.hover = { x, y, index, price, bin };
  const withTime = data.bar_seconds < 86400;
  const liq = nearestLiq(x, y, L, data);
  const liqLine = liq
    ? `<div class="tip-liq ${liq.side === "long" ? "long" : "short"}">${liqWord()} ${liq.side === "long" ? "long" : "short"} liq ${fmtPx(liq.price)} · ${fmtUsd(liq.usd)} · ${fmtClock(liq.ts)}</div>`
    : "";
  tip.hidden = false;
  tip.innerHTML = `
    <div>${fmtStamp(data.times[index], withTime)}</div>
    <div>Price ${fmtPx(price)}</div>
    <div>Est. entry exposure ${fmtUsd(usd || 0)}</div>
    <div>O ${fmtPx(data.openA[index])} H ${fmtPx(data.highA[index])}</div>
    <div>L ${fmtPx(data.lowA[index])} C ${fmtPx(data.closeA[index])}</div>${liqLine}`;
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
  // Each breakdown rebuilds the whole model server-side; on long fine-bar views that costs seconds per hover.
  if (data.times.length > 1000) return;
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
  box.innerHTML = rows || "<div>No modeled tiers here</div>";
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
  if (state.data.replay && state.replay && state.replay.anchorN) setReplayView(state.replay);
  else {
    state.view.i0 = -0.5;
    state.view.i1 = n - 0.5;
  }
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

function provenanceLines() {
  const data = state.data;
  const lines = [
    data.title,
    `Open Liq · ${data.model.name || MODEL_NAMES[data.model.id] || "Unknown"} scenario · estimated USD exposure, not exchange position records`,
    `Source ${data.source} · event/bar ${fmtStamp(data.as_of, true)} · generated ${fmtStamp(data.generated_at, true)}`,
    `Model ${data.model_version || (data.model || {}).version || "unversioned"} · revision ${data.data_revision || data.data_fingerprint || "not provided"}`,
  ];
  const events = liqEvents(data);
  if (events && events.length) lines.push(`Dots: ${liqWord() === "Synthetic" ? "synthetic demo liquidation events" : "observed OKX liquidation orders"} (${events.length}), not modeled.`);
  return lines;
}

function displayedExportStem() {
  const data = state.data;
  return `${data.exchange}-${data.coin}-${data.range}-model${data.model.id}`.toLowerCase();
}

function displayedSettingsUrl() {
  const data = state.data;
  const url = new URL(location.href);
  for (const [key, value] of Object.entries({exchange: data.exchange, coin: data.coin, range: data.range, model: data.model.id, mode: data.mode})) {
    url.searchParams.set(key, String(value));
  }
  if (data.exchange === "binance" && data.mode === "pair" && data.bar) url.searchParams.set("bar", data.bar);
  else url.searchParams.delete("bar");
  if (data.asof != null) url.searchParams.set("asof", String(Math.floor(data.asof / 1000)));
  else url.searchParams.delete("asof");
  return url.toString();
}

function snapshot() {
  if (!state.data) return;
  const out = document.createElement("canvas");
  const width = Math.max(state.cssW, 900);
  const lines = provenanceLines();
  const pad = 24 + 23 * lines.length;
  out.width = Math.round(width * state.dpr);
  out.height = canvas.height + Math.round(pad * state.dpr);
  const ctx = out.getContext("2d");
  ctx.scale(state.dpr, state.dpr);
  ctx.fillStyle = "#0c100f";
  ctx.fillRect(0, 0, width, out.height / state.dpr);
  ctx.drawImage(canvas, 0, pad, state.cssW, state.cssH);
  lines.forEach((line, index) => {
    ctx.fillStyle = index === 0 ? "#e9eee8" : "#b5c2b3";
    ctx.font = index === 0 ? "16px sans-serif" : "11px sans-serif";
    ctx.fillText(line, 16, 25 + index * 23, width - 32);
  });
  downloadFile(out.toDataURL("image/png"), `${displayedExportStem()}.png`);
  announce("PNG snapshot exported with source and model provenance.");
}

function downloadFile(href, name) {
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function exportResearch() {
  if (!state.data) return;
  const payload = Object.fromEntries(Object.entries(state.data).filter(([key]) => !key.endsWith("A")));
  const document = { exported_at: new Date().toISOString(), settings_url: displayedSettingsUrl(), selected_settings_url: location.href, disclaimer: "Estimated exposure; not exchange position records.", payload };
  const href = URL.createObjectURL(new Blob([JSON.stringify(document, null, 2)], {type: "application/json"}));
  downloadFile(href, `open-liq-${displayedExportStem()}-research.json`);
  setTimeout(() => URL.revokeObjectURL(href), 1000);
  announce("Research JSON exported with source payload and provenance.");
}

/* Trader context: reading line, market strip, observed liquidations, watchlist
   and alerts. A failed side request degrades to "—" or a hidden strip and never
   touches the map. */

function isLive(data) {
  return Boolean(data) && data.asof == null && !data.replay;
}

function dataKey(data) {
  return data ? `${data.exchange}|${data.mode}|${data.coin}|${data.range}` : "";
}

function watchKeyOf(data) {
  return data ? `${data.exchange}|${data.mode}|${data.range}|${(data.model || {}).id}` : "";
}

function displayedMatchesState() {
  const data = state.data;
  return Boolean(data) && (data.model || {}).id === state.model && data.exchange === state.exchange &&
    data.coin === state.coin && data.range === state.range && data.mode === state.mode;
}

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

// The server answers 429 when its few expensive slots are taken (other tabs,
// or work a cancelled request left behind). Retry twice before reporting it.
async function fetchRetry(url, signal, onRetry) {
  for (let attempt = 0; ; attempt++) {
    const response = await fetch(url, { signal });
    if (response.status !== 429 || attempt >= 2) return response;
    if (onRetry) onRetry(attempt + 1);
    await wait(1500 * (attempt + 1), signal);
  }
}

async function getJson(url, signal) {
  const response = await fetchRetry(url, signal);
  let body = null;
  try {
    body = await response.json();
  } catch (_err) {
    body = null;
  }
  if (!response.ok || !body || typeof body !== "object") {
    throw new Error((body && body.error) || `HTTP ${response.status}`);
  }
  return body;
}

// Context and liquidation requests are cheap to restart. The watchlist is
// single-flight instead: aborting it would not free the server's work.
function cancelSide() {
  state.sideSeq += 1;
  if (state.sideController) state.sideController.abort();
  state.sideController = null;
  state.watchQueued = null;
}

function afterLoad(data, reason) {
  if (isLive(data)) {
    rememberMain(data);
    checkAlerts(data);
  }
  renderWatch();
  loadSide(data, reason === "auto");
}

// The server runs few expensive requests at once: context and liquidations go
// after the map has applied, the watchlist after both settle.
function loadSide(data, forceWatch) {
  const key = dataKey(data);
  if (!isLive(data)) {
    state.context = null;
    state.liqs = null;
    renderMarket();
    renderLiqNote();
    requestDraw();
    return;
  }
  if (state.context && state.context.key !== key) state.context = null;
  if (state.liqs && state.liqs.key !== key) state.liqs = null;
  renderMarket();
  renderLiqNote();
  const token = ++state.sideSeq;
  if (state.sideController) state.sideController.abort();
  const controller = new AbortController();
  state.sideController = controller;
  const current = () => token === state.sideSeq && isLive(state.data) && dataKey(state.data) === key;
  const params = new URLSearchParams({ exchange: data.exchange, symbol: data.coin, mode: data.mode });
  const context = getJson("/api/v1/context?" + params.toString(), controller.signal).then((body) => {
    if (!current()) return;
    state.context = { key, body };
    renderMarket();
  }, (err) => {
    if (err.name === "AbortError" || !current()) return;
    // Keep this market's last good value through a busy or failed refresh.
    if (!state.context || state.context.key !== key || !state.context.body) state.context = { key, body: null };
    renderMarket();
  });
  let liqs = Promise.resolve();
  if (state.showLiqs && !liqsServed(data)) {
    // The server would only answer available:false here.
    state.liqs = { key, body: { available: false } };
    renderLiqNote();
  } else if (state.showLiqs) {
    params.set("range", data.range);
    liqs = getJson("/api/v1/liquidations?" + params.toString(), controller.signal).then((body) => {
      if (!current()) return;
      state.liqs = { key, body };
      renderLiqNote();
      requestDraw();
    }, (err) => {
      if (err.name === "AbortError" || !current()) return;
      if (!state.liqs || state.liqs.key !== key || !state.liqs.body) state.liqs = { key, body: null };
      renderLiqNote();
      requestDraw();
    });
  }
  Promise.allSettled([context, liqs]).then(() => {
    if (token !== state.sideSeq) return;
    if (state.sideController === controller) state.sideController = null;
    loadWatchlist(forceWatch);
  });
}

// Observed liquidations exist for the venues /api/status lists (OKX), All venues and the demo.
function liqsServed(data) {
  return data.exchange === "demo" || data.mode === "symbol" || state.liqSources.includes(data.exchange);
}

function loadWatchlist(force) {
  const data = state.data;
  if (!isLive(data) || state.replay || state.replayController) return;
  const list = state.watchlist.slice();
  if (!list.length) return;
  const key = watchKeyOf(data);
  const sig = key + "|" + list.join(",");
  if (!force && state.watch.sig === sig && Date.now() - state.watch.at < WATCH_MIN_MS) return;
  if (state.watchBusy) {
    state.watchQueued = { force: Boolean(force || (state.watchQueued && state.watchQueued.force)) };
    return;
  }
  state.watchBusy = true;
  const token = ++state.watchSeq;
  const params = new URLSearchParams({
    exchange: data.exchange, range: data.range, model: String(data.model.id), mode: data.mode, symbols: list.join(","),
  });
  getJson("/api/v1/watchlist?" + params.toString(), new AbortController().signal).then((body) => {
    // A late answer for another venue, range or model is dropped.
    if (token !== state.watchSeq || watchKeyOf(state.data) !== key) return;
    if (state.watch.key !== key) state.watch = { key, items: {}, at: 0, sig: "" };
    (Array.isArray(body.items) ? body.items : []).forEach((item) => {
      const coin = item ? String(item.coin || "").toUpperCase() : "";
      if (COIN_RE.test(coin)) state.watch.items[coin] = item;
    });
    state.watch.at = Date.now();
    state.watch.sig = sig;
    renderWatch();
  }, (err) => {
    if (err.name === "AbortError" || token !== state.watchSeq) return;
    // Back off; chips keep what the main map already told us.
    state.watch.at = Date.now();
    state.watch.sig = sig;
    renderWatch();
  }).finally(() => {
    state.watchBusy = false;
    const queued = state.watchQueued;
    state.watchQueued = null;
    if (queued) loadWatchlist(queued.force);
  });
}

function ratio5(analysis) {
  const rows = analysis && Array.isArray(analysis.imbalance) ? analysis.imbalance : [];
  return rows.find((row) => Number(row.window_pct) === 5) || null;
}

function fillRead(button, cluster, side) {
  const word = side === "short" ? "short" : "long";
  const px = button.querySelector(".rl-px");
  const dist = button.querySelector(".rl-d");
  const usd = button.querySelector(".rl-u");
  if (!cluster || !num(cluster.price)) {
    button.disabled = true;
    button.dataset.price = "";
    px.textContent = state.data ? "none in window" : "—";
    dist.textContent = "";
    usd.textContent = "";
    button.setAttribute("aria-label", `No ${word} liquidation cluster ${side === "short" ? "above" : "below"} price in this window`);
    return;
  }
  button.disabled = false;
  button.dataset.price = String(cluster.price);
  px.textContent = fmtPx(cluster.price);
  dist.textContent = fmtPct(cluster.distance_pct, 2);
  usd.textContent = fmtUsd(cluster.usd);
  button.setAttribute("aria-label", `Nearest ${word} liquidation cluster ${fmtPx(cluster.price)}, ${fmtPct(cluster.distance_pct, 2)} from price, about ${fmtUsd(cluster.usd)} modeled. Focus the chart on this price.`);
}

function renderReading(data) {
  const analysis = data ? data.analysis : null;
  fillRead($("read-short"), analysis ? analysis.above : null, "short");
  fillRead($("read-long"), analysis ? analysis.below : null, "long");
  const row = ratio5(analysis);
  let text = "—";
  if (row) {
    if (num(row.short_to_long)) text = row.short_to_long.toFixed(2) + "×";
    else if (row.short_usd > 0 && !(row.long_usd > 0)) text = "shorts only";
    else if (row.long_usd > 0 && !(row.short_usd > 0)) text = "longs only";
  }
  $("read-ratio").querySelector("b").textContent = text;
  $("read-ratio").title = row
    ? `Within ±5% of price: modeled short liqs ${fmtUsd(row.short_usd)}, long liqs ${fmtUsd(row.long_usd)}. Above 1× means more modeled short liquidations overhead than long liquidations underneath.`
    : "Modeled short liquidation exposure within ±5% of price divided by long exposure";
}

function showMk(id, on) {
  $(id).hidden = !on;
}

function fmtHours(hours) {
  return hours >= 1 ? `${Number(hours.toFixed(2))}h` : `${Math.round(hours * 60)}m`;
}

function fundingOf(body, data) {
  const venues = Array.isArray(body.venues) ? body.venues.filter(Boolean) : [];
  const ok = venues.filter((venue) => venue.ok && num(venue.funding_rate));
  const aggregate = data.mode === "symbol";
  let rate = null;
  let next = null;
  let hours = null;
  if (aggregate) {
    // The weighted rate is already scaled to the basis interval (8 hours).
    rate = num(body.funding_rate_oi_weighted) ? body.funding_rate_oi_weighted : null;
    const times = ok.map((venue) => venue.next_funding_time).filter((t) => num(t) && t > Date.now());
    next = times.length ? Math.min(...times) : null;
    hours = num(body.funding_basis_hours) ? body.funding_basis_hours : 8;
  } else {
    const venue = ok.find((item) => item.exchange === data.exchange) || (ok.length === 1 ? ok[0] : null);
    if (venue) {
      rate = venue.funding_rate;
      next = num(venue.next_funding_time) ? venue.next_funding_time : null;
      hours = num(venue.funding_interval_hours) ? venue.funding_interval_hours : null;
    }
  }
  if (rate == null) return null;
  const lines = venues.map((venue) => {
    const name = venue.name || VENUE_NAMES[venue.exchange] || venue.exchange;
    if (!venue.ok || !num(venue.funding_rate)) return `${name}: unavailable${venue.error ? " (" + venue.error + ")" : ""}`;
    return `${name}: ${fmtRate(venue.funding_rate)}${num(venue.funding_interval_hours) ? " per " + fmtHours(venue.funding_interval_hours) : ""}` +
      `${num(venue.next_funding_time) ? " · next " + fmtClock(venue.next_funding_time) : ""}` +
      `${num(venue.open_interest_usd) ? " · OI " + fmtUsd(venue.open_interest_usd) : ""}`;
  });
  if (aggregate) lines.unshift(`Open-interest-weighted funding ${fmtRate(rate)} per ${fmtHours(hours)} (each venue scaled to ${fmtHours(hours)})`);
  if (body.synthetic) lines.push("Synthetic demo values.");
  if (body.note) lines.push(String(body.note));
  lines.push("Positive funding: longs pay shorts.");
  const label = aggregate ? `Funding · OI-wtd · ${ok.length}/${venues.length} venues` : "Funding";
  return { rate, next, hours, label, detail: lines.join("\n") };
}

function tickFunding() {
  const el = $("mk-fund-t");
  if (!state.fundingNext || $("mk-fund").hidden) {
    el.textContent = "";
    return;
  }
  el.textContent = fmtCountdown(state.fundingNext - Date.now());
}

function renderMarket() {
  const data = state.data;
  const host = $("market");
  state.fundingNext = null;
  if (!data) {
    host.hidden = true;
    return;
  }
  const mc = data.market_context && typeof data.market_context === "object" ? data.market_context : {};
  const win = data.range || state.range;
  const change = mc.price_change_pct;
  showMk("mk-chg", num(change));
  if (num(change)) {
    $("mk-chg-k").textContent = win;
    $("mk-chg-v").textContent = fmtPct(change, 2);
    $("mk-chg-v").className = change >= 0 ? "up" : "down";
    $("mk-chg").title = `Price change over the ${rangeLabel(win)} window`;
  }
  const live = isLive(data);
  const body = live && state.context && state.context.key === dataKey(data) ? state.context.body : null;
  const oi = num(mc.oi_usd) ? mc.oi_usd : body && num(body.open_interest_usd_total) ? body.open_interest_usd_total : null;
  showMk("mk-oi", oi != null);
  if (oi != null) {
    const delta = mc.oi_change_pct;
    $("mk-oi-v").textContent = fmtUsd(oi);
    $("mk-oi-d").textContent = num(delta) ? fmtPct(delta, 1) : "";
    $("mk-oi-d").className = num(delta) ? (delta >= 0 ? "up" : "down") : "";
    $("mk-oi").title = `Open interest${data.mode === "symbol" ? " across venues" : ""} ${fmtUsd(oi)}` +
      (num(delta) ? `; ${fmtPct(delta, 1)} over the ${rangeLabel(win)} window` : "");
  }
  const funding = live && body ? fundingOf(body, data) : null;
  showMk("mk-fund", Boolean(funding));
  if (funding) {
    $("mk-fund-k").textContent = funding.label;
    $("mk-fund-v").textContent = fmtRate(funding.rate) + (funding.hours ? " /" + fmtHours(funding.hours) : "");
    $("mk-fund").title = funding.detail + (funding.next ? `\nNext funding ${fmtClock(funding.next)}` : "");
    $("mk-fund-sr").textContent = ". " + funding.detail.split("\n").map((line) => /[.!?)]$/.test(line) ? line : line + ".").join(" ");
    state.fundingNext = funding.next;
  }
  tickFunding();
  host.hidden = ["mk-chg", "mk-oi", "mk-fund"].every((id) => $(id).hidden);
}

function liqBody() {
  return state.liqs && state.liqs.body ? state.liqs.body : null;
}

function liqWord() {
  const body = liqBody();
  return body && (body.synthetic || body.source === "demo") ? "Synthetic" : "Observed";
}

function liqEvents(data) {
  const l = state.liqs;
  if (!state.showLiqs || !isLive(data) || !l || l.key !== dataKey(data) || !l.body || !l.body.available) return null;
  return Array.isArray(l.body.events) ? l.body.events : null;
}

// Area follows USD: radius grows with the square root, against the feed's
// 95th percentile (at least $100K) so one large order does not shrink the rest.
function liqRef() {
  const l = state.liqs;
  if (!l || !l.body) return 100000;
  if (l.ref == null) {
    const usd = (Array.isArray(l.body.events) ? l.body.events : []).map((event) => Number(event && event.usd) || 0).sort((a, b) => a - b);
    l.ref = Math.max(100000, usd.length ? usd[Math.floor(0.95 * (usd.length - 1))] : 0);
  }
  return l.ref;
}

function liqRadius(usd, ref) {
  return clamp(2 + 7 * Math.sqrt(Math.max(0, Number(usd) || 0) / ref), 2, 9);
}

// Bar i spans [times[i], times[i] + bar) and is drawn over index i - 0.5 .. i + 0.5.
function liqIndex(ts, data) {
  return (ts - data.times[0]) / (data.bar_seconds * 1000) - 0.5;
}

function drawLiqDots(ctx, L, data) {
  const events = liqEvents(data);
  if (!events || !events.length) return;
  const n = data.times.length;
  const scale = L.width < 520 ? 0.8 : 1;
  const ref = liqRef();
  const coverage = state.liqs.body.coverage || {};
  // Only a truncated feed has an uncovered stretch; otherwise blank means none.
  const start = coverage.truncated && num(coverage.from_ms) ? liqIndex(coverage.from_ms, data) : null;
  if (start != null && start > -0.5 + 2 && start < n - 0.5 && start > state.view.i0 && start < state.view.i1) {
    const x = Math.round(xOf(start, L)) + 0.5;
    ctx.save();
    ctx.strokeStyle = "rgba(217, 242, 153, 0.45)";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, L.plotY);
    ctx.lineTo(x, L.plotY + L.plotH);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = "10px ui-monospace, monospace";
    const label = liqWord() === "Synthetic" ? "demo liqs from here →" : "OKX liq feed starts →";
    const w = ctx.measureText(label).width;
    const lx = clamp(x + 5, L.plotX + 2, L.plotX + L.plotW - w - 6);
    ctx.fillStyle = "rgba(12, 16, 15, 0.78)";
    ctx.fillRect(lx - 3, L.plotY + 4, w + 6, 15);
    ctx.fillStyle = "#d9f299";
    ctx.textAlign = "left";
    ctx.fillText(label, lx, L.plotY + 15);
    ctx.restore();
  }
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(12, 16, 15, 0.85)";
  for (const event of events) {
    if (!event || !num(event.ts) || !num(event.price)) continue;
    const index = liqIndex(event.ts, data);
    if (index < -0.5 || index > n - 0.5 || index < state.view.i0 - 1 || index > state.view.i1 + 1) continue;
    const x = xOf(index, L);
    const y = yOf(event.price, L);
    if (y < L.plotY - 12 || y > L.plotY + L.plotH + 12) continue;
    ctx.beginPath();
    ctx.arc(x, y, liqRadius(event.usd, ref) * scale, 0, Math.PI * 2);
    ctx.fillStyle = event.side === "long" ? "rgba(224, 122, 114, 0.78)" : "rgba(125, 206, 160, 0.78)";
    ctx.fill();
    ctx.stroke();
  }
}

function nearestLiq(x, y, L, data) {
  const events = liqEvents(data);
  if (!events || !events.length) return null;
  const n = data.times.length;
  const ref = liqRef();
  let best = null;
  let bestD = Infinity;
  for (const event of events) {
    if (!event || !num(event.ts) || !num(event.price)) continue;
    const index = liqIndex(event.ts, data);
    if (index < -0.5 || index > n - 0.5) continue;
    const d = Math.hypot(xOf(index, L) - x, yOf(event.price, L) - y);
    if (d <= Math.max(10, liqRadius(event.usd, ref) + 3) && d < bestD) {
      best = event;
      bestD = d;
    }
  }
  return best;
}

function renderLiqNote() {
  const note = $("liq-note");
  const data = state.data;
  const l = state.liqs;
  let text = "";
  let drawn = false;
  if (state.showLiqs && isLive(data) && l && l.key === dataKey(data)) {
    const body = l.body;
    if (body && body.available === false) {
      text = "Observed liquidation orders come from OKX. Choose OKX or All venues to plot them.";
    } else if (body && body.available) {
      const coverage = body.coverage || {};
      const events = Array.isArray(body.events) ? body.events : [];
      const count = num(coverage.count) ? coverage.count : events.length;
      const demo = body.synthetic || body.source === "demo";
      const since = num(coverage.from_ms) ? " since " + fmtClock(coverage.from_ms) : "";
      const unit = demo ? (count === 1 ? "event" : "events") : (count === 1 ? "order" : "orders");
      const orders = `${count.toLocaleString("en-US")} ${unit}${coverage.truncated ? ", the most recent OKX returns" : ""}`;
      const gap = coverage.truncated ? " Earlier in the window is not covered, not empty." : "";
      text = demo
        ? `Synthetic demo liquidations${since} (${orders}); not exchange data.`
        : `Observed OKX liquidations${since} (${orders})${data.mode === "symbol" ? "; OKX only, Binance and Bybit not included" : ""}. Dots sit at bankruptcy price, sized by USD.${gap}`;
      drawn = events.length > 0;
    } else if (!body) {
      text = "Observed liquidations are unavailable right now.";
    }
  }
  note.textContent = text;
  note.hidden = !text;
  $("legend-liq").hidden = !drawn;
  $("legend-liq-text").textContent = liqWord() === "Synthetic" ? "Synthetic liqs (demo)" : "Observed liqs (OKX)";
}

function mainItem(data) {
  const analysis = data.analysis || {};
  const mc = data.market_context || {};
  const pick = (cluster) => (cluster && num(cluster.price) ? { price: cluster.price, distance_pct: cluster.distance_pct, usd: cluster.usd } : null);
  return {
    coin: data.coin,
    ok: num(analysis.price),
    price: analysis.price,
    price_change_pct: num(mc.price_change_pct) ? mc.price_change_pct : null,
    above: pick(analysis.above),
    below: pick(analysis.below),
    stale: Boolean(data.stale),
  };
}

function rememberMain(data) {
  const key = watchKeyOf(data);
  if (state.watch.key !== key) state.watch = { key, items: {}, at: 0, sig: "" };
  state.watch.items[data.coin] = mainItem(data);
}

// The chart's own coin always reads from the displayed map.
function watchItem(coin) {
  const data = state.data;
  if (isLive(data) && data.coin === coin && watchKeyOf(data) === state.watch.key) return mainItem(data);
  if (data && watchKeyOf(data) !== state.watch.key) return null;
  return state.watch.items[coin] || null;
}

function chipHtml(coin, item) {
  const parts = [`<b class="c-coin">${escapeHtml(coin)}</b>`];
  if (!item || item.ok === false || !num(item.price)) {
    parts.push('<span class="c-px">—</span>');
    return parts.join(" ");
  }
  parts.push(`<span class="c-px">${fmtPx(item.price)}</span>`);
  if (num(item.price_change_pct)) parts.push(`<span class="c-chg ${item.price_change_pct >= 0 ? "up" : "down"}">${fmtPct(item.price_change_pct, 1)}</span>`);
  if (item.above && num(item.above.price)) parts.push(`<span class="c-up">↑${fmtPx(item.above.price)} ${fmtPct(item.above.distance_pct, 1)}</span>`);
  if (item.below && num(item.below.price)) parts.push(`<span class="c-dn">↓${fmtPx(item.below.price)} ${fmtPct(item.below.distance_pct, 1)}</span>`);
  return parts.join(" ");
}

function chipLabel(coin, item) {
  const tail = coin === state.coin ? "Shown on the chart." : "Show on the chart.";
  if (!item || item.ok === false || !num(item.price)) return `${coin}, no data yet. ${tail}`;
  const parts = [`${coin} ${fmtPx(item.price)}`];
  if (num(item.price_change_pct)) parts.push(`${fmtPct(item.price_change_pct, 1)} over ${rangeLabel(state.range)}`);
  if (item.above && num(item.above.price)) parts.push(`nearest short liq cluster ${fmtPx(item.above.price)}, ${fmtPct(item.above.distance_pct, 1)}`);
  if (item.below && num(item.below.price)) parts.push(`nearest long liq cluster ${fmtPx(item.below.price)}, ${fmtPct(item.below.distance_pct, 1)}`);
  return parts.join(", ") + ". " + tail;
}

function renderWatch() {
  const host = $("watch-list");
  const active = document.activeElement;
  const inside = Boolean(active && host.contains(active));
  const focusCoin = inside ? active.dataset.coin : null;
  const focusX = inside && active.classList.contains("chip-x");
  host.innerHTML = "";
  state.watchlist.forEach((coin) => {
    const item = watchItem(coin);
    const li = document.createElement("li");
    li.className = "chip" + (coin === state.coin ? " on" : "") + (item && item.stale ? " stale" : "");
    const main = document.createElement("button");
    main.type = "button";
    main.className = "chip-main";
    main.dataset.coin = coin;
    if (coin === state.coin) main.setAttribute("aria-current", "true");
    main.innerHTML = chipHtml(coin, item);
    main.setAttribute("aria-label", chipLabel(coin, item));
    if (item && item.ok === false && item.error) main.title = String(item.error);
    else if (item && item.stale) main.title = "Source data is stale";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "chip-x";
    remove.dataset.coin = coin;
    remove.setAttribute("aria-label", `Remove ${coin} from watchlist`);
    remove.title = `Remove ${coin}`;
    remove.textContent = "×";
    li.append(main, remove);
    host.appendChild(li);
  });
  const add = $("watch-add");
  add.hidden = !addable();
  add.textContent = "+ Add " + state.coin;
  add.setAttribute("aria-label", `Add ${state.coin} to watchlist`);
  if (focusCoin) {
    const again = host.querySelector(`${focusX ? ".chip-x" : ".chip-main"}[data-coin="${focusCoin}"]`);
    if (again) again.focus();
  }
}

function switchCoin(next) {
  if (!COIN_RE.test(next || "")) return;
  if (next === state.coin && state.data && !state.replay) return;
  stopReplay();
  state.coin = next;
  $("coin").value = next;
  state.timeFull = true;
  state.priceDirty = false;
  persist();
  renderWatch();
  load("coin");
}

// Only the coin of the map on screen, as the server named it, and never a failed one.
function addable() {
  const coin = state.data ? state.data.coin : "";
  return Boolean(coin) && coin === state.coin && !state.watchlist.includes(coin) && state.watchlist.length < WATCH_MAX;
}

function addWatch() {
  const coin = state.coin;
  if (!addable()) return;
  state.watchlist.push(coin);
  persist();
  renderWatch();
  announce(`${coin} added to watchlist.`);
  const chip = $("watch-list").querySelector(`.chip-main[data-coin="${coin}"]`);
  if (chip) chip.focus();
}

function removeWatch(coin) {
  const index = state.watchlist.indexOf(coin);
  if (index < 0) return;
  state.watchlist.splice(index, 1);
  persist();
  renderWatch();
  announce(`${coin} removed from watchlist.`);
  const chips = $("watch-list").querySelectorAll(".chip-main");
  const next = chips[Math.min(index, chips.length - 1)];
  (next || ($("watch-add").hidden ? $("coin") : $("watch-add"))).focus();
}

let toastTimer = 0;

function showToast(text, side) {
  const toast = $("toast");
  toast.className = "toast " + (side || "");
  toast.hidden = false;
  $("toast-text").textContent = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, 12000);
}

function hideToast() {
  clearTimeout(toastTimer);
  $("toast").hidden = true;
}

function notifySupported() {
  return typeof window.Notification === "function";
}

function syncAlertNote() {
  let text = "";
  if (state.alertOn) {
    if (!notifySupported()) text = "Live views only · in-page alerts.";
    else if (Notification.permission === "granted") text = "Live views only · browser notifications on.";
    else if (Notification.permission === "denied") text = "Live views only · notifications blocked, in-page alerts only.";
    else text = "Live views only · in-page alerts.";
  }
  $("alert-note").textContent = text;
}

function requestNotify() {
  try {
    if (!notifySupported() || Notification.permission !== "default") return;
    const result = Notification.requestPermission(syncAlertNote);
    if (result && typeof result.then === "function") result.then(syncAlertNote, () => {});
  } catch (_err) {
    /* in-page alerts still work */
  }
}

function fireAlert(data, cluster, side) {
  const demo = data.source === "demo";
  const title = `${demo ? "Demo · " : ""}${data.coin} near ${side} liq cluster`;
  const body = `Price ${fmtPx((data.analysis || {}).price)} is ${fmtPct(cluster.distance_pct, 2)} from the modeled ${side} liq cluster at ${fmtPx(cluster.price)} (about ${fmtUsd(cluster.usd)}). Model estimate, not a trade signal.`;
  showToast(`${title}. ${body}`, side);
  try {
    if (notifySupported() && Notification.permission === "granted") {
      new Notification("Open Liq · " + title, { body, tag: `open-liq-${data.coin}-${side}`, icon: "/assets/logo.png" });
    }
  } catch (_err) {
    /* some mobile browsers only notify from a service worker */
  }
}

// One alert per side and cluster: it re-arms once price moves away (twice the
// threshold) or a different cluster becomes the nearest.
function checkAlerts(data) {
  if (!state.alertOn || !isLive(data)) return;
  const analysis = data.analysis || {};
  const limit = state.alertPct;
  [["above", "short"], ["below", "long"]].forEach(([which, side]) => {
    const cluster = analysis[which];
    const id = `${data.exchange}|${data.mode}|${data.coin}|${which}`;
    const dist = cluster && num(cluster.distance_pct) && num(cluster.price) ? Math.abs(cluster.distance_pct) : null;
    if (dist != null && dist <= limit) {
      const last = state.alerts[id];
      if (last != null && Math.abs(cluster.price - last) / last <= 0.0025) return;
      state.alerts[id] = cluster.price;
      fireAlert(data, cluster, side);
    } else if (dist == null || dist > limit * 2) {
      delete state.alerts[id];
    }
  });
}

function openHow() {
  const how = $("how");
  how.open = true;
  how.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  how.querySelector("summary").focus({ preventScroll: true });
}

function goLive() {
  stopReplay();
  state.asof = null;
  syncControls();
  persist();
  load("live");
}

function syncReplayBar() {
  const bar = $("replay-bar");
  const r = state.replay;
  const past = Boolean(state.data && state.data.asof != null && pastView());
  bar.hidden = !(r || past);
  if (bar.hidden) return;
  $("rb-play").hidden = !r;
  if (r) {
    const idx = Math.max(0, r.idx);
    $("rb-badge").textContent = "Replay";
    $("rb-time").textContent = `${fmtStamp(frameAsof(r, idx) * 1000, r.step < 86400000)} · ${idx + 1}/${r.frames}`;
    $("rb-play").textContent = r.playing ? "Pause" : "Play";
    $("rb-play").setAttribute("aria-label", r.playing ? "Pause replay" : "Play replay");
  } else {
    $("rb-badge").textContent = "Past view";
    $("rb-time").textContent = "As of " + fmtStamp(state.data.asof, true) + " · Binance archive";
  }
}

// Presets keep a replay watchable: about 300 frames at most, never finer than
// the range's own bar, and always a bar the range allows.
function presetBar(span, range) {
  const base = DEFAULT_BAR[range] || "5m";
  for (const bar of ["5m", "15m", "1h", "4h", "12h", "1d"]) {
    if (BAR_S[bar] < BAR_S[base] || span / BAR_S[bar] > 300 || !barFits(range, bar)) continue;
    return bar === base ? "" : bar;
  }
  return "";
}

async function replayPreset(span, label) {
  stopReplay();
  const switched = !historyOn();
  setVenue("binance");
  if (RANGE_S[state.range] > RANGE_S["7d"]) state.range = "24h";
  state.bar = presetBar(span, state.range);
  state.asof = null;
  state.timeFull = true;
  state.priceDirty = false;
  syncControls();
  $("to-input").value = "";
  persist();
  const panel = $("replay-settings");
  const summary = panel.querySelector("summary");
  const hadFocus = panel.contains(document.activeElement);
  panel.open = false;
  if (hadFocus) summary.focus();
  announce(`Loading ${label} replay from the Binance archive${switched ? "; switched to Binance single venue" : ""}.`);
  const pending = startReplay(false, span);
  const seq = state.replaySeq;
  await pending;
  if (seq !== state.replaySeq || !state.replay) return;
  if (document.activeElement === summary || document.activeElement === document.body) $("rb-play").focus();
  if (!state.replay.playing && !reducedMotion()) togglePlay();
}

function autoRefresh() {
  // Live views only: never while a replay span loads, plays or is paused.
  // Hidden tabs keep refreshing only so price alerts can fire.
  if ((document.hidden && !state.alertOn) || pastView() || state.replay || state.replayController) return;
  load("auto");
}

function setAuto(seconds) {
  if (state.timer) clearInterval(state.timer);
  state.timer = 0;
  state.auto = seconds;
  if (seconds > 0) state.timer = setInterval(autoRefresh, seconds * 1000);
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
    $("rp-time").textContent = "Replay is Binance single venue only";
  } else {
    $("rp-time").textContent = state.asof == null ? "Set As of, then Load replay" : "Ready to load";
  }
  syncReplayBar();
}

function stopReplay() {
  state.replaySeq += 1;
  if (state.replayController) {
    state.replayController.abort();
    state.replayController = null;
    setLoading(false);
  }
  const r = state.replay;
  if (!r) return;
  r.gen += 1;
  r.playing = false;
  r.controller.abort();
  state.replay = null;
  syncReplay();
}

// span (seconds) asks the server for the newest data Binance can replay; otherwise As of / To are used.
async function startReplay(keepIdx, span) {
  const prev = state.replay;
  const idx = keepIdx && prev ? prev.idx : 0;
  stopReplay();
  const seq = state.replaySeq;
  const controller = new AbortController();
  state.replayController = controller;
  state.seq += 1;
  if (state.controller) state.controller.abort();
  cancelSide();
  const toText = $("to-input").value;
  const params = new URLSearchParams({ symbol: state.coin, range: state.range });
  if (span) params.set("span", String(span));
  else params.set("from", String(state.asof));
  if (state.bar) params.set("bar", state.bar);
  if (toText && !span) params.set("to", String(parseInputValue(toText)));
  setLoading(true, "Loading replay… uncached history can take a while. If the request times out, choose a shorter interval and retry.");
  try {
    const response = await fetch("/api/replay?" + params.toString(), {signal: controller.signal});
    const body = await response.json();
    if (seq !== state.replaySeq) return;
    if (!response.ok) throw new Error(body.error || "Could not load the replay.");
    if (span) {
      // Show the span the server chose, so a reload replays the same bars.
      state.asof = Math.floor(body.first_asof / 1000);
      syncControls();
      $("to-input").value = toInputValue(Math.floor(body.last_asof / 1000));
      persist();
    }
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
      anchorFirst: keepIdx && prev ? prev.anchorFirst : null,
      anchorN: keepIdx && prev ? prev.anchorN : null,
    };
    syncReplay();
    await showFrame(state.replay.idx);
  } catch (err) {
    if (err.name === "AbortError" || seq !== state.replaySeq) return;
    const message = err.message || "Could not load the replay.";
    // A preset may have switched venue; never leave another venue's map labelled as this one.
    if (!displayedMatchesState()) {
      fail(message);
    } else {
      showBanner("error", [message]);
      announce("Replay unavailable. " + message);
    }
  } finally {
    if (seq === state.replaySeq) {
      state.replayController = null;
      setLoading(false);
    }
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
  promise.catch(() => { if (r.cache.get(idx) === promise) r.cache.delete(idx); });
  r.cache.set(idx, promise);
  return promise;
}

async function showFrame(idx) {
  const r = state.replay;
  if (!r) return false;
  const gen = r.gen;
  const context = mapParams().toString();
  const frame = clamp(idx, 0, r.frames - 1);
  r.idx = frame;
  syncReplay();
  try {
    const body = await fetchFrame(r, r.idx);
    if (state.replay !== r || gen !== r.gen || r.idx !== frame || context !== mapParams().toString()) return false;
    // Announce frames a user steps to, not every frame of a playing replay.
    applyPayload(body, r.playing);
    for (const ahead of [r.idx + 1, r.idx + 2]) {
      if (ahead < r.frames) fetchFrame(r, ahead).catch(() => {});
    }
    for (const key of r.cache.keys()) {
      if (key < r.idx - 2 || key > r.idx + 2) r.cache.delete(key);
    }
    return true;
  } catch (err) {
    if (err.name === "AbortError" || state.replay !== r || gen !== r.gen || r.idx !== frame || context !== mapParams().toString()) return false;
    if (err.expired && !r.reloaded) {
      await startReplay(true);
      return false;
    }
    r.playing = false;
    syncReplay();
    const message = err.message || "Could not load this frame.";
    if (!displayedMatchesState()) {
      fail(message);
    } else {
      showBanner("error", [message]);
      announce("Replay unavailable. " + message);
    }
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
        const r = state.replay;
        r.gen += 1;
        r.playing = false;
        r.controller.abort();
        r.controller = new AbortController();
        r.cache.clear();
        showFrame(r.idx);
      } else {
        load("model");
      }
    });
  });
  $("exchange").addEventListener("change", () => {
    stopReplay();
    setVenue($("exchange").value);
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
    state.asof = text ? parseInputValue(text) : null;
    syncControls();
    persist();
    load("asof");
  });
  $("live").addEventListener("click", goLive);
  $("rb-live").addEventListener("click", goLive);
  $("rb-play").addEventListener("click", togglePlay);
  document.querySelectorAll(".preset").forEach((button) => {
    button.addEventListener("click", () => replayPreset(Number(button.dataset.span), button.textContent.trim()));
  });
  const coin = $("coin");
  const commitCoin = () => {
    const next = normalizeCoin(coin.value);
    if (!next && coin.value.trim()) showBanner("error", [`${coin.value.trim()} is not a ticker. ${COIN_HINT}`]);
    if (!next || next === state.coin) {
      coin.value = state.coin;
      return;
    }
    stopReplay();
    state.coin = next;
    coin.value = next;
    state.timeFull = true;
    state.priceDirty = false;
    persist();
    renderWatch();
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
  $("export").addEventListener("click", exportResearch);
  $("share").addEventListener("click", async () => {
    updateUrl();
    try {
      await navigator.clipboard.writeText(location.href);
      $("share").textContent = "Link copied";
      announce("Settings link copied.");
    } catch (_err) {
      $("share").textContent = "Copy link from address bar";
      announce("Settings saved in the address bar. Copy its URL to share.");
    }
  });
  $("timezone").addEventListener("change", () => {
    const to = $("to-input").value ? parseInputValue($("to-input").value) : null;
    state.timezone = $("timezone").value;
    if (to != null) $("to-input").value = toInputValue(to);
    syncControls();
    persist();
    if (state.data) applyPayload(state.data, true);
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    // One layer per press: the alert toast first, then an open panel.
    if (!$("toast").hidden) {
      hideToast();
      return;
    }
    document.querySelectorAll(".advanced > details[open]").forEach(panel => { panel.open = false; panel.querySelector("summary").focus(); });
  });
  document.querySelectorAll(".advanced > details").forEach(panel => panel.addEventListener("toggle", () => {
    if (panel.open) document.querySelectorAll(".advanced > details").forEach(other => { if (other !== panel) other.open = false; });
  }));
  const zoom = factor => {
    if (!state.data || !state.layout) return;
    if ($("zoom-axis").value === "time") zoomTime(state.layout.plotX + state.layout.plotW / 2, factor);
    else zoomPrice(state.layout.plotY + state.layout.plotH / 2, factor);
    requestDraw();
  };
  $("zoom-in").addEventListener("click", () => zoom(1 / 1.25));
  $("zoom-out").addEventListener("click", () => zoom(1.25));
  $("reset").addEventListener("click", resetView);
  canvas.addEventListener("keydown", event => {
    if (!state.data) return;
    if (["+", "=", "-", "0", "Home", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) event.preventDefault();
    if (["+", "=", "-"].includes(event.key)) {
      const factor = event.key === "-" ? 1.25 : 1 / 1.25;
      zoom(factor);
    } else if (["0", "Home"].includes(event.key)) resetView();
    else if (event.key.startsWith("Arrow") && !state.replay) {
      if (["ArrowUp", "ArrowDown"].includes(event.key)) {
        const delta = (state.view.hi - state.view.lo) * .1 * (event.key === "ArrowUp" ? 1 : -1);
        state.view.lo += delta; state.view.hi += delta; state.priceDirty = true;
      } else {
        const delta = (state.view.i1 - state.view.i0) * .1 * (event.key === "ArrowRight" ? 1 : -1);
        state.view.i0 += delta; state.view.i1 += delta; state.timeFull = false;
      }
      clampView();
    }
    requestDraw();
  });
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
  $("liqs").addEventListener("change", () => {
    state.showLiqs = $("liqs").checked;
    persist();
    renderLiqNote();
    requestDraw();
    // Fetch on demand: the overlay is not requested while it is off.
    if (state.showLiqs && isLive(state.data) && !state.replay && !state.replayController &&
        (!state.liqs || state.liqs.key !== dataKey(state.data))) loadSide(state.data, false);
  });
  $("alert-on").addEventListener("change", () => {
    state.alertOn = $("alert-on").checked;
    state.alerts = {};
    persist();
    if (state.alertOn) {
      requestNotify();
      if (state.data) checkAlerts(state.data);
    }
    syncAlertNote();
  });
  $("alert-pct").addEventListener("change", () => {
    const value = Number($("alert-pct").value);
    if (Number.isFinite(value) && value > 0) state.alertPct = Math.round(clamp(value, 0.05, 10) * 100) / 100;
    $("alert-pct").value = String(state.alertPct);
    state.alerts = {};
    persist();
    if (state.alertOn && state.data) checkAlerts(state.data);
  });
  $("toast-close").addEventListener("click", hideToast);
  $("how-link").addEventListener("click", openHow);
  $("caption-info").addEventListener("click", openHow);
  ["read-short", "read-long"].forEach((id) => $(id).addEventListener("click", () => {
    const price = Number($(id).dataset.price);
    if (price > 0) focusPrice(price);
  }));
  $("watch-list").addEventListener("click", (event) => {
    const remove = event.target.closest(".chip-x");
    if (remove) {
      removeWatch(remove.dataset.coin);
      return;
    }
    const chip = event.target.closest(".chip-main");
    if (chip) switchCoin(chip.dataset.coin);
  });
  $("watch-list").addEventListener("keydown", (event) => {
    const chip = event.target.closest(".chip-main");
    if (chip && (event.key === "Delete" || event.key === "Backspace")) {
      event.preventDefault();
      removeWatch(chip.dataset.coin);
    }
  });
  $("watch-add").addEventListener("click", addWatch);
  $("auto").addEventListener("change", () => setAuto(Number($("auto").value) || 0));
  // A tab coming back catches up at once instead of waiting out the interval.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.auto > 0 && Date.now() - state.requestedAt >= state.auto * 1000) autoRefresh();
  });
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
      touch: event.pointerType === "touch",
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
    const dy = state.drag.touch ? 0 : event.clientY - state.drag.y;
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
    const sources = (body.capabilities || {}).observed_liquidations;
    if (Array.isArray(sources)) state.liqSources = sources;
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
restoreUrl();
buildSwatches();
syncControls();
setAuto(state.auto);
wire();
renderWatch();
renderReading(null);
syncAlertNote();
setInterval(tickFunding, 1000);
resize();
bootStatus();
load("boot");
