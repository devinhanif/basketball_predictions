// Visual checks over the fixtures: every screen at 375 / 768 / 1280 px, dark and light, Simple and Analyst, in the lens
// each page belongs to. A developer tool (headless Chrome over CDP, plain Node, no dependency); nothing here ships.
//
//   node scripts/visual-check.mjs <base url> [--betting] [--quick] [--only a,b] [--update] [--strict] [--no-pixels]
//                                 [--threshold 0.005] [--tolerance 4] [--out dir]
//
// <base url> is a web dev server proxying the fixtures API with APP_LIVE_DIR=app/fixtures/live. app/scripts/visual.sh
// starts both on spare ports, runs this, and stops them; use that. --betting says the servers run with the betting switch
// ON (the script checks this against the page and refuses a mismatch) and adds the betting screens.
//
// Deterministic input: the fixture replay (static), fixed viewports, prefers-reduced-motion, animations, transitions and
// the caret disabled, fonts loaded before capture, seeded Math.random and crypto randomness, a pinned clock (Date starts at PINNED_AT and runs at real speed, so
// "x h ago" and ET/CT labels cannot drift but timers still work), a fixed time zone and locale, the first-run intro
// dismissed through the app's own localStorage keys, and the headshot host blocked (avatars are always initials).
//
// Layer A, structural assertions on every capture (these gate: exit 1):
//   no page-level horizontal scroll; exactly one visible h1; at 375 px no interactive element under 44 x 44 px (allow-list
//   below, each with its reason); no text under 12 px, no AA contrast failure and no text clipped by an overflow ancestor
//   (all three from scripts/audit-dom.js); no two interactive elements overlapping; the first Tab stop shows a focus ring;
//   on betting screens that show a price, the money-safety note; in the Learn lens, none of learn-dom.mjs's price words.
// Layer B, pixels (never gates unless --strict): each PNG is decoded (scripts/png.mjs) and compared with the baseline of
//   the same name; the fraction of pixels differing by more than --tolerance on any channel is reported with the changed
//   regions as boxes. --strict fails a capture above --threshold. --update writes the baseline. Baselines and outputs
//   live under app/web/.visual/ (git-ignored; PNGs are never committed).
//
// Output: a table (route, width, theme, mode, lens, structural result, pixel delta), report.json beside the PNGs, the
// run time. Exit 0 all gating checks passed, 1 a gating check failed, 2 usage or setup problem, 3 the browser stalled.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { clearTimeout, setTimeout } from "node:timers";
import { setTimeout as sleep } from "node:timers/promises";
import { comparePixels, decodePng, selfTest } from "./png.mjs";

const args = process.argv.slice(2);
const base = args[0];
const usage = "usage: visual-check.mjs <base url> [--betting] [--quick] [--only a,b] [--update] [--strict] [--no-pixels] [--threshold f] [--tolerance n] [--out dir]";
if (!base || base.startsWith("--")) {
  console.error(usage);
  process.exit(2);
}
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i > 0 && i + 1 < args.length ? args[i + 1] : dflt;
};
const flag = (name) => args.includes(`--${name}`);
const BETTING = flag("betting");
const QUICK = flag("quick");
const UPDATE = flag("update");
const STRICT = flag("strict");
const PIXELS = !flag("no-pixels");
const THRESHOLD = Number(opt("threshold", "0.005"));
const TOLERANCE = Number(opt("tolerance", "4"));
const ONLY = opt("only", null) ? new Set(opt("only", "").split(",")) : null;
const ROOT = new URL("..", import.meta.url).pathname;
const OUT = opt("out", join(ROOT, ".visual", BETTING ? "on" : "off"));
const CUR = join(OUT, "current");
const BASE = join(OUT, "baseline");
const CHROME = process.env.CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const ORIGIN = new URL(base).origin;

// The clock every page sees (an instant after the fixture live night, 2026-11-09) and the zone its labels are read in.
const PINNED_AT = "2026-11-10T15:00:00.000Z";
const TIME_ZONE = "America/Chicago";

// ------------------------------------------------------------------------------------------------ the matrix
// Fixture ids (the same as scripts/route-walk.mjs): a game, a player in it, a night, the live night.
const G = "0022400252";
const P = "1628378";
const N = "2024-11-20";
const LIVE_NIGHT = "2026-11-09";
const DEMO_LABEL = "Load the labelled demo";
const MONEY_NOTE = "Analysis only, not advice: no orders are placed.";
// [name, hash, lenses, extra]. Lenses: Learn for pages that read the same everywhere, Market for Market-only screens and
// desk pages, both (or all three for the leg) where the page differs by lens.
const SCREENS = [
  ["discover", `#/?date=${N}`, ["learn", "market"]],
  ["game", `#/game/${G}`, ["learn", "market"]],
  ["leg", `#/leg/${G}/${P}/pts`, ["learn", "explore", "market"]],
  ["source", `#/source/${G}/${P}/pts`, ["learn"]],
  ["swipe", `#/swipe?date=${N}`, ["learn"]],
  ["picks", "#/picks", ["learn"]],
  ["parlay", "#/parlay", ["learn", "market"]],
  ["prefs", "#/prefs", ["learn"]],
  ["signals", "#/signals", ["market"]],
  ["hedge", "#/hedge", ["market"]],
  ["history", "#/history", ["market"]],
  ["alerts", "#/alerts", ["market"]],
  ["admin", "#/admin", ["market"]],
  ["record", `#/record?date=${N}`, ["learn"]],
  ["record-cal", "#/record?tab=calibration", ["learn"]],
  ["record-guess", "#/record?tab=guess", ["learn"]],
  ["live", `#/live?date=${LIVE_NIGHT}`, ["learn"]],
  ["terms", "#/terms", ["learn", "market"]],
  ["scorecard", "#/scorecard", ["learn", "market"]],
  ["tests", "#/tests", ["learn"]],
  ["help-errors", "#/help/errors", ["learn"]],
  ["notfound", "#/zzz", ["learn"], { expect: "notfound" }],
  ["api-down", `#/game/${G}`, ["learn"], { apiDown: true, expect: "failure" }],
];
const BET_SCREENS = [
  ["bet-hub", "#/bet", ["market"]],
  ["bet-slip", "#/bet/slip", ["market"]],
  ["bet-slip-demo", "#/bet/slip", ["market"], { action: "demo" }],
  ["bet-compare", `#/bet/compare?game=${G}&player=${P}&stat=pts&side=over`, ["market"]],
  ["bet-hedge", "#/bet/hedge", ["market"]],
  ["bet-hedge-demo", "#/bet/hedge", ["market"], { action: "demo" }],
  ["bet-learn", "#/bet", ["learn"], { expect: "market-screen" }],
];
// Routes the Learn lens promises carry no price words (the same set as learn-dom.mjs and route-walk.mjs).
const LEARN_CHECKED = /^(discover|game|leg|swipe|picks|parlay|signals|hedge$|history|record|terms|source|scorecard|tests|help)/;
const WIDTHS = [
  [375, 812],
  [768, 1024],
  [1280, 800],
];
const COMBOS = [];
for (const [w, h] of WIDTHS) for (const theme of ["dark", "light"]) for (const mode of ["simple", "analyst"]) COMBOS.push({ w, h, theme, mode });

// ------------------------------------------------------------------------------------------------ allow-lists
// Interactive elements under 44 x 44 px at 375 that are accepted, each with its reason. Matched with Element.matches().
// Inline links inside a sentence are exempt by rule (WCAG 2.5.8's inline exception), not listed here.
const SMALL_TARGET_ALLOW = [
  [".skip-link", "visually hidden until focused; it is full size when it shows"],
  ["input[type=file].sr", "the hidden file input behind a visible Import button (the button is the target)"],
];
// Pairs of interactive elements whose boxes may overlap: one of them sits in a fixed layer that floats over the page.
const OVERLAP_RULE = "a fixed layer (launcher, sheet, toast) floats over page content by design; pairs where exactly one side is in a position:fixed layer are not counted";

// ------------------------------------------------------------------------------------------------ text rules
const PRICE_RE = [
  /(?<![\w])[+\-−]\d{3,4}\b/,
  /\d\s?¢/,
  /\$\s?\d/,
  /\bcents? per\b/i,
  /\b(EV|vig|vigorish|odds|payout|moneyline|implied|break-even)\b/i,
  /\bedges?\b/i,
  /\bfees?\b/i,
  /\bprices?\b/i,
  /\b(DraftKings|FanDuel|BetMGM|Caesars|ESPN ?BET|Fanatics|Kalshi|Polymarket|Novig)\b/i,
];
const SHOWS_PRICE_RE = /(?<![\w])[+\-−]\d{3,4}\b|\d\s?¢|\$\s?\d/;
const NOT_FOUND_RE = /could not find that page|There is nothing at that address/i;

// ------------------------------------------------------------------------------------------------ page scripts
const auditSrc = readFileSync(join(ROOT, "scripts", "audit-dom.js"), "utf8");
const NO_MOTION_CSS = "*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;animation-iteration-count:1!important;transition:none!important;caret-color:transparent!important;scroll-behavior:auto!important}";
const PRELUDE = `(() => {
  const RealDate = Date;
  const start = RealDate.now();
  const fixed = RealDate.parse(${JSON.stringify(PINNED_AT)});
  const now = () => fixed + (RealDate.now() - start);
  function PinnedDate(...a) {
    if (!new.target) return new RealDate(now()).toString();
    return a.length ? new RealDate(...a) : new RealDate(now());
  }
  PinnedDate.prototype = RealDate.prototype;
  PinnedDate.now = now;
  PinnedDate.parse = RealDate.parse;
  PinnedDate.UTC = RealDate.UTC;
  window.Date = PinnedDate;
  // Seeded randomness (mulberry32, a fresh seed per page load): Swipe's deck order and the guess game's round use it.
  let seed = 0x2545f491;
  const rnd = () => { seed = (seed + 0x6d2b79f5) >>> 0; let t = seed; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  Math.random = rnd;
  if (window.crypto) {
    const fill = (a) => { const b = new Uint8Array(a.buffer, a.byteOffset, a.byteLength); for (let i = 0; i < b.length; i++) b[i] = Math.floor(rnd() * 256); return a; };
    const hex = () => Math.floor(rnd() * 16).toString(16);
    Object.defineProperty(window.crypto, 'getRandomValues', { value: fill, configurable: true });
    Object.defineProperty(window.crypto, 'randomUUID', { value: () => 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, hex), configurable: true });
  }
  const css = () => { if (document.getElementById('ph-visual-still')) return; const s = document.createElement('style'); s.id = 'ph-visual-still'; s.textContent = ${JSON.stringify(NO_MOTION_CSS)}; (document.head || document.documentElement).appendChild(s); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', css); else css();
})();`;
const prefsScript = (theme, mode, lens) => `(() => { try { if (location.origin !== ${JSON.stringify(ORIGIN)}) return; const s = localStorage;
  s.setItem('ph.welcomed', '1'); s.setItem('ph.tour', '1'); s.setItem('ph.name', 'Visual');
  s.setItem('ph.theme', ${JSON.stringify(theme)}); s.setItem('ph.mode', ${JSON.stringify(mode)}); s.setItem('ph.lens', ${JSON.stringify(lens)}); } catch {} })();`;

// Runs in the page after the audit: h1 count, small targets, overlaps, page scroll, failure panels, text for the scans.
const STRUCTURE = `(() => {
  const ALLOW = ${JSON.stringify(SMALL_TARGET_ALLOW.map((a) => a[0]))};
  const SEL = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [role=tab], [role=link], [role=checkbox], [role=radio], [role=switch], [role=menuitem], [tabindex]:not([tabindex="-1"])';
  const desc = (el) => { const t = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('title') || '').replace(/\\s+/g, ' ').trim().slice(0, 32); return el.tagName.toLowerCase() + (el.tagName === 'INPUT' ? '[type=' + el.type + ']' : '') + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '') + (t ? ' "' + t + '"' : ''); };
  const shown = (el) => { if (!el.checkVisibility({ visibilityProperty: true })) return false; if (el.closest('[inert], [aria-hidden="true"]')) return false; const r = el.getBoundingClientRect(); return r.width >= 2 && r.height >= 2; };
  const fixedLayer = (el) => { for (let n = el; n && n.nodeType === 1; n = n.parentElement) if (getComputedStyle(n).position === 'fixed') return true; return false; };
  const inlineInSentence = (el) => { if (el.tagName !== 'A' || getComputedStyle(el).display !== 'inline') return false; const p = el.parentElement; if (!p) return false; return [...p.childNodes].some((n) => n !== el && n.nodeType === 3 && n.nodeValue.trim().length > 0); };
  const els = [...document.querySelectorAll(SEL)].filter(shown);
  const small = [];
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width >= 43.5 && r.height >= 43.5) continue;
    if (ALLOW.some((s) => el.matches(s))) continue;
    if (inlineInSentence(el)) continue;
    small.push({ el: desc(el), w: Math.round(r.width), h: Math.round(r.height) });
  }
  const overlaps = [];
  const boxes = els.map((el) => ({ el, r: el.getBoundingClientRect(), fixed: fixedLayer(el) }));
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
    if (a.fixed !== b.fixed) continue;
    const ix = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
    const iy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
    if (ix > 2 && iy > 2) overlaps.push({ a: desc(a.el), b: desc(b.el), px: Math.round(ix) + 'x' + Math.round(iy) });
  }
  const h1 = [...document.querySelectorAll('h1')].filter((e) => e.checkVisibility()).map((e) => (e.textContent || '').trim().slice(0, 40));
  const doc = document.documentElement;
  const failures = [...document.querySelectorAll('.panel.error')].map((p) => ({ code: p.getAttribute('data-code'), wayOn: !!p.querySelector('button, a[href]') }));
  const main = document.querySelector('main');
  return { h1, small, overlaps: overlaps.slice(0, 20), nOverlaps: overlaps.length, scroll: Math.max(doc.scrollWidth, document.body.scrollWidth) - doc.clientWidth, failures,
    mainText: main ? (main.innerText || '').replace(/\\s+/g, ' ').trim() : '', bodyText: (document.body.innerText || '').replace(/\\s+/g, ' '), betScreen: !!document.querySelector('main .ph-bet') };
})()`;
const FOCUS = `(() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return { ok: false, why: 'the first Tab focused nothing' };
  const parse = (c) => { const m = /rgba?\\(([^)]+)\\)/.exec(c); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { a: p.length > 3 ? p[3] : 1 }; };
  const cs = getComputedStyle(el);
  const oc = parse(cs.outlineColor);
  const outline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 && (!oc || oc.a > 0);
  const shadow = !!cs.boxShadow && cs.boxShadow !== 'none';
  const r = el.getBoundingClientRect();
  const inView = r.width > 1 && r.height > 1 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
  const name = el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\\s+/)[0] : '') + ' "' + (el.textContent || '').trim().slice(0, 24) + '"';
  return { ok: (outline || shadow) && inView, el: name, outline: cs.outlineStyle + ' ' + cs.outlineWidth, shadow: shadow ? 'yes' : 'none', inView };
})()`;
const LEARN_TEXT = `(async () => {
  const open = () => document.querySelectorAll('details').forEach((d) => { d.open = true; });
  open(); await new Promise((r) => setTimeout(r, 250)); open(); await new Promise((r) => setTimeout(r, 150));
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: (n) => /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(n.parentElement ? n.parentElement.tagName : '') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  const parts = []; while (w.nextNode()) parts.push(w.currentNode.nodeValue);
  document.querySelectorAll('[aria-label],[title],[alt],[aria-valuetext]').forEach((e) => { for (const a of ['aria-label', 'title', 'alt', 'aria-valuetext']) { const v = e.getAttribute(a); if (v) parts.push(v); } });
  return parts.join(' ').replace(/1-800-GAMBLER/g, '').replace(/\\s+/g, ' ');
})()`;

// ------------------------------------------------------------------------------------------------ chrome over CDP
const freePort = () =>
  new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => {
        resolve(port);
      });
    });
  });
// A free devtools port from the system, never a fixed or derived one (two runs at once would drive one browser).
const port = await freePort();
const profile = mkdtempSync(join(tmpdir(), "ph-visual-"));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-color-profile=srgb", "--font-render-hinting=none", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--no-first-run", "about:blank"], { stdio: "ignore" });
let target;
for (let i = 0; i < 80 && !target; i++) {
  try {
    target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page");
  } catch {
    /* not up yet */
  }
  if (!target) await sleep(250);
}
const stop = async (code) => {
  try {
    chrome.kill();
  } catch {
    /* gone */
  }
  await sleep(300);
  try {
    rmSync(profile, { recursive: true, force: true });
  } catch {
    /* a temp folder; harmless if left */
  }
  process.exit(code);
};
if (!target) {
  console.error("visual-check: Chrome did not start");
  await stop(2);
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let msgId = 0;
const pend = new Map();
const waiters = [];
const inflight = new Set();
let lastNet = Date.now();
let exceptions = [];
let apiDown = false;
const STALL_MS = 90_000;
let stallTimer = null;
const armStall = () => {
  if (stallTimer) clearTimeout(stallTimer);
  stallTimer = setTimeout(() => {
    console.error(`visual-check: the browser gave no answer for ${String(STALL_MS / 1000)} s; stopping (exit 3)`);
    void stop(3);
  }, STALL_MS);
};
const send = (method, params = {}) =>
  new Promise((resolve) => {
    const i = ++msgId;
    pend.set(i, (m) => {
      armStall();
      resolve(m);
    });
    armStall();
    ws.send(JSON.stringify({ id: i, method, params }));
  });
const waitFor = (method, ms = 20000) =>
  new Promise((resolve) => {
    const w = { method, resolve };
    waiters.push(w);
    setTimeout(() => {
      const k = waiters.indexOf(w);
      if (k >= 0) {
        waiters.splice(k, 1);
        resolve(null);
      }
    }, ms);
  });
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) {
    pend.get(m.id)(m);
    pend.delete(m.id);
    return;
  }
  for (let k = waiters.length - 1; k >= 0; k--) {
    if (waiters[k].method === m.method) {
      waiters[k].resolve(m.params);
      waiters.splice(k, 1);
    }
  }
  const p = m.params;
  if (m.method === "Network.requestWillBeSent" && p.type !== "EventSource" && !p.request.url.startsWith("data:")) {
    inflight.add(p.requestId);
    lastNet = Date.now();
  } else if (m.method === "Network.loadingFinished" || m.method === "Network.loadingFailed") {
    inflight.delete(p.requestId);
    lastNet = Date.now();
  } else if (m.method === "Runtime.exceptionThrown") {
    exceptions.push((p.exceptionDetails.exception?.description ?? p.exceptionDetails.text).slice(0, 200));
  } else if (m.method === "Fetch.requestPaused") {
    void (apiDown ? send("Fetch.failRequest", { requestId: p.requestId, errorReason: "ConnectionRefused" }) : send("Fetch.continueRequest", { requestId: p.requestId }));
  }
};
const ev = async (js) => {
  const r = await send("Runtime.evaluate", { expression: js, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(`page script failed: ${(r.result.exceptionDetails.exception?.description ?? r.result.exceptionDetails.text).slice(0, 200)}`);
  return r.result?.result?.value;
};
await send("Page.enable");
await send("Runtime.enable");
await send("Network.enable");
await send("Network.setBlockedURLs", { urls: ["*://cdn.nba.com/*", "*cdn.nba.com*"] });
await send("Emulation.setTimezoneOverride", { timezoneId: TIME_ZONE });
await send("Emulation.setLocaleOverride", { locale: "en-US" });
await send("Page.addScriptToEvaluateOnNewDocument", { source: PRELUDE });
let prefsId = null;

/** Wait until the network is quiet, nothing is busy, main has text, fonts are loaded, and two frames have painted. */
async function settle(cap = 15000) {
  const t0 = Date.now();
  await sleep(250);
  while (Date.now() - t0 < cap) {
    const quiet = inflight.size === 0 && Date.now() - lastNet > 300;
    const ready = quiet && (await ev(`(() => { const m = document.querySelector('main'); return !!m && !document.querySelector('[aria-busy="true"], .skeleton') && (m.innerText || '').trim().length > 0; })()`));
    if (ready) break;
    await sleep(120);
  }
  await ev(`(() => { if (!document.getElementById('ph-visual-still')) { const s = document.createElement('style'); s.id = 'ph-visual-still'; s.textContent = ${JSON.stringify(NO_MOTION_CSS)}; document.head.appendChild(s); } return document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(1))))); })()`);
  await sleep(150);
}

async function open(hash, { w, h, theme, mode }, lens, down) {
  await send("Storage.clearDataForOrigin", { origin: ORIGIN, storageTypes: "local_storage,session_storage,indexeddb,service_workers,cache_storage" });
  if (prefsId) await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: prefsId });
  prefsId = (await send("Page.addScriptToEvaluateOnNewDocument", { source: prefsScript(theme, mode, lens) })).result.identifier;
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }, { name: "prefers-reduced-motion", value: "reduce" }] });
  apiDown = down;
  if (down) await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*" }] });
  await send("Page.navigate", { url: "about:blank" });
  inflight.clear();
  exceptions = [];
  const loaded = waitFor("Page.loadEventFired");
  await send("Page.navigate", { url: base.replace(/\/?$/, "/") + hash });
  await loaded;
  await settle();
}

/** Click a button by its text in main, then wait for the page to settle. */
async function clickButton(text) {
  const t0 = Date.now();
  while (Date.now() - t0 < 10000) {
    const done = await ev(`(() => { const b = [...document.querySelectorAll('main button')].find((x) => (x.textContent || '').trim() === ${JSON.stringify(text)} && x.offsetParent !== null); if (!b) return false; b.click(); return true; })()`);
    if (done) {
      await settle();
      return;
    }
    await sleep(200);
  }
  throw new Error(`no button "${text}" on the page`);
}

// ------------------------------------------------------------------------------------------------ the run
if (PIXELS) selfTest();
mkdirSync(CUR, { recursive: true });
if (UPDATE) mkdirSync(BASE, { recursive: true });

// Sanity: the switch the page reports must match --betting (a mismatch would mix ON and OFF baselines).
await open("#/bet", COMBOS[0], "market", false);
const betOn = await ev(`!!document.querySelector('main .ph-bet')`);
if (betOn !== BETTING) {
  console.error(`visual-check: the web server's betting switch is ${betOn ? "ON" : "off"} but --betting was ${BETTING ? "given" : "not given"}; refusing to mix baselines`);
  await stop(2);
}

const screens = [...SCREENS, ...(BETTING ? BET_SCREENS : [])];
const jobs = [];
screens.forEach(([name, hash, lenses, extra = {}], si) => {
  if (ONLY && !ONLY.has(name)) return;
  for (const lens of lenses) {
    COMBOS.forEach((c, ci) => {
      // --quick: a representative third; every screen keeps 4 of its 12 combinations, rotated so all widths, themes and
      // modes still appear for each screen across its lenses.
      if (QUICK && (ci + si + lenses.indexOf(lens)) % 3 !== 0) return;
      jobs.push({ name, hash, lens, extra, ...c });
    });
  }
});

const t0 = Date.now();
const rows = [];
let gatingFailures = 0;
let pixelFailures = 0;
let pixelCompared = 0;
for (const j of jobs) {
  const file = `${j.name}_${j.lens}_${j.w}_${j.theme}_${j.mode}.png`;
  const findings = [];
  const info = [];
  try {
    await open(j.hash, j, j.lens, !!j.extra.apiDown);
    if (j.extra.action === "demo") {
      if (j.hash !== "#/bet/slip") {
        await ev(`(() => { location.hash = '#/bet/slip'; return 0; })()`);
        await settle();
      }
      await clickButton(DEMO_LABEL);
      if (j.hash !== "#/bet/slip") {
        await ev(`(() => { location.hash = ${JSON.stringify(j.hash)}; return 0; })()`);
        await settle();
      }
    }
    await ev(`(() => { window.scrollTo(0, 0); return 0; })()`);
    const audit = await ev(`(${auditSrc})()`);
    const s = await ev(STRUCTURE);
    if (s.scroll > 1) findings.push(`horizontal scroll: page is ${s.scroll}px wider than the viewport`);
    if (s.h1.length !== 1) findings.push(`h1 count ${s.h1.length}${s.h1.length ? ` (${s.h1.map((t) => `"${t}"`).join(", ")})` : ""}`);
    if (j.w === 375) for (const x of s.small) findings.push(`target under 44px: ${x.el} ${x.w}x${x.h}`);
    for (const x of audit.small) findings.push(`text under 12px: ${x.px}px "${x.text}" (${x.sel})`);
    for (const x of audit.lowContrast) findings.push(`contrast ${x.ratio}:1 "${x.text}" (${x.sel}) fg ${x.fg} bg ${x.bg}`);
    for (const x of audit.clipped) findings.push(`clipped by ${x.box} (${x.over}px): "${x.text}" (${x.sel})`);
    for (const x of s.overlaps) findings.push(`overlap ${x.px}: ${x.a} / ${x.b}`);
    if (s.nOverlaps > s.overlaps.length) findings.push(`... ${s.nOverlaps - s.overlaps.length} more overlaps`);
    if ((audit.broken ?? []).length) info.push(`${audit.broken.length} word(s) broken mid-word (informational)`);
    if (exceptions.length) findings.push(`uncaught exception: ${exceptions[0]}`);
    if (j.extra.expect === "notfound" && !NOT_FOUND_RE.test(s.mainText)) findings.push("expected a not-found screen");
    if (j.extra.expect === "failure" && (s.failures.length === 0 || !s.failures.every((f) => f.wayOn))) findings.push(s.failures.length ? "failure panel with no way on" : "expected a failure panel with the API down");
    if (j.extra.expect === "market-screen" && (!/is a Market screen/.test(s.mainText) || s.betScreen)) findings.push("expected the Market-screen note and no betting UI in the Learn lens");
    if (j.name.startsWith("bet-") && s.betScreen && SHOWS_PRICE_RE.test(s.mainText) && !s.bodyText.includes(MONEY_NOTE)) findings.push("betting screen shows a price without the money-safety note");
    // The screenshot (full page, capped).
    const metrics = await send("Page.getLayoutMetrics");
    const fullH = Math.ceil(metrics.result.cssContentSize.height);
    const ch = Math.min(fullH, 8000);
    if (fullH > ch) info.push(`page ${fullH}px tall; captured the first ${ch}px`);
    const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: j.w, height: ch, scale: 1 } });
    const png = Buffer.from(shot.result.data, "base64");
    writeFileSync(join(CUR, file), png);
    // Focus: reset the sequential-focus start point to the top, press Tab once, read the ring.
    await ev(`(() => { const d = document.documentElement; d.tabIndex = -1; d.focus(); d.blur(); d.removeAttribute('tabindex'); window.scrollTo(0, 0); return 0; })()`);
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
    await sleep(60);
    const f = await ev(FOCUS);
    if (!f.ok) findings.push(`focus ring not visible on the first Tab stop: ${f.why ?? `${f.el} outline ${f.outline} shadow ${f.shadow} in view ${f.inView}`}`);
    // Learn lens: no price words (every <details> opened, all text and accessible attributes).
    if (j.lens === "learn" && LEARN_CHECKED.test(j.name) && !j.extra.apiDown) {
      const text = await ev(LEARN_TEXT);
      for (const re of PRICE_RE) {
        const m = text.match(re);
        if (m) findings.push(`Learn lens price word "${m[0]}": ...${text.slice(Math.max(0, m.index - 30), m.index + 30)}...`);
      }
    }
    // Pixels.
    let delta = "no baseline";
    const basePath = join(BASE, file);
    if (PIXELS && existsSync(basePath)) {
      const c = comparePixels(decodePng(png), decodePng(readFileSync(basePath)), { tolerance: TOLERANCE });
      pixelCompared++;
      delta = c.sizeChanged ? `size ${c.note}` : `${(c.fraction * 100).toFixed(3)}%`;
      if (c.changed > 0 && !c.sizeChanged) delta += ` in ${c.nRegions} region(s) ${c.regions.map((r) => `${r.x},${r.y} ${r.w}x${r.h}`).join("; ")}`;
      if (c.fraction > THRESHOLD) {
        pixelFailures++;
        if (STRICT) findings.push(`pixels: ${delta} above ${(THRESHOLD * 100).toFixed(2)}%`);
      }
      j.pixel = { fraction: c.fraction, sizeChanged: c.sizeChanged, regions: c.regions };
    } else if (!PIXELS) delta = "skipped";
    if (UPDATE) writeFileSync(basePath, png);
    j.delta = delta;
  } catch (e) {
    findings.push(`capture failed: ${e.message}`);
  } finally {
    if (j.extra.apiDown) {
      apiDown = false;
      await send("Fetch.disable");
    }
  }
  if (findings.length) gatingFailures++;
  rows.push({ route: j.name, hash: j.hash, width: j.w, theme: j.theme, mode: j.mode, lens: j.lens, file: join(CUR, file), findings, info, pixel: j.pixel ?? null, delta: j.delta ?? "-" });
  const r = rows.at(-1);
  console.log(`${r.route.padEnd(15)} ${String(r.width).padStart(4)} ${r.theme.padEnd(5)} ${r.mode.padEnd(7)} ${r.lens.padEnd(7)} ${(findings.length ? `FAIL ${findings.length}` : "ok").padEnd(7)} ${r.delta}`);
  for (const x of findings.slice(0, 6)) console.log(`      ${x}`);
  if (findings.length > 6) console.log(`      ... ${findings.length - 6} more (report.json)`);
}
const seconds = Math.round((Date.now() - t0) / 1000);

// Summary: findings grouped by kind, then by route.
const byKind = {};
for (const r of rows) for (const f of r.findings) {
  const k = f.split(":")[0].replace(/ \d.*$/, "");
  byKind[k] = (byKind[k] ?? 0) + 1;
}
const report = {
  base, betting: BETTING, quick: QUICK, pinnedAt: PINNED_AT, timeZone: TIME_ZONE, tolerance: TOLERANCE, threshold: THRESHOLD, strict: STRICT, updated: UPDATE,
  captures: rows.length, failedCaptures: gatingFailures, findingsByKind: byKind, pixelCompared, pixelAboveThreshold: pixelFailures, seconds,
  allowList: { smallTargets: SMALL_TARGET_ALLOW, inlineLinks: "an inline <a> inside a sentence is exempt from the 44px rule (WCAG 2.5.8 inline exception)", overlaps: OVERLAP_RULE },
  rows,
};
writeFileSync(join(OUT, "report.json"), JSON.stringify(report, null, 1));
console.log("");
console.log(`visual-check: ${rows.length} captures (${BETTING ? "switch ON" : "switch off"}${QUICK ? ", --quick" : ""}) in ${seconds} s; ${gatingFailures} with a gating finding; findings by kind ${JSON.stringify(byKind)}`);
console.log(`pixels: ${PIXELS ? `${pixelCompared} compared with a baseline, ${pixelFailures} above ${(THRESHOLD * 100).toFixed(2)}%${STRICT ? " (gating: --strict)" : " (informational)"}` : "skipped (--no-pixels)"}${UPDATE ? `; baseline written to ${BASE}` : ""}`);
console.log(`screenshots: ${CUR}; report: ${join(OUT, "report.json")}`);
if (stallTimer) clearTimeout(stallTimer);
ws.close();
await stop(gatingFailures > 0 ? 1 : 0);
