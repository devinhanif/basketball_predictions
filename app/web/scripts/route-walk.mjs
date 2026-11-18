// Route walker: seeded simulated visitors over every hash route in headless Chrome, with optional failure injection.
//
//   node scripts/route-walk.mjs <base url> [--seed 1] [--visitors 6] [--steps 25] [--inject] [--width 390]
//                               [--garbage] [--set fixtures|artifact] [--snapshot out.json] [--json out.json]
//
// <base url> is a dev server over the fixtures API (see app/README.md: API on 8020 with APP_LIVE_DIR=app/fixtures/live,
// `API_ORIGIN=http://127.0.0.1:8020 npx vite --port 5190`), or `file://.../dist-artifact/parlayhitters.html` with
// `--set artifact`. Chrome is started here (headless, a throwaway profile) and stopped at the end.
//
// After every step it checks: no uncaught exception; <main> has text (no blank page); no page-level horizontal scroll; no
// private/money desk text (this walker never runs a private session); the LINE_CAL_LIVE lock wording is present whenever a
// locked night's finished games are on screen; in the Learn lens, no price words on the routes the Learn lens promises
// that for (the same set as scripts/learn-dom.mjs); routes that should be "not found" say so; a failure panel always
// offers a way on (a button or a link). With --inject, steps randomly run under API down, a 503 with a reason (old and new
// body shapes), a 503 that starts mid-step, slow answers, or a killed live stream; a shown 503 panel must show the reason.
// --garbage instead answers 200 with bodies of the wrong shape (the error boundary must hold; no blank page).
//
// Exit code 1 on any violation. Not part of `npm run check`: it needs Chrome and servers. No dependency: plain Node + CDP.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { clearTimeout, setTimeout } from "node:timers";
import { setTimeout as sleep } from "node:timers/promises";

const args = process.argv.slice(2);
const base = args[0];
if (!base || base.startsWith("--")) throw new Error("usage: route-walk.mjs <base url> [--seed N] [--visitors N] [--steps N] [--inject] [--width px] [--set fixtures|artifact] [--snapshot file] [--json file]");
const opt = (name, dflt) => {
  const i = args.indexOf(`--${name}`);
  return i > 0 && i + 1 < args.length ? args[i + 1] : dflt;
};
const flag = (name) => args.includes(`--${name}`);
const SEED = Number(opt("seed", "1"));
const VISITORS = Number(opt("visitors", "6"));
const STEPS = Number(opt("steps", "25"));
const WIDTH = Number(opt("width", "390"));
const GARBAGE = flag("garbage");
const INJECT = flag("inject") || GARBAGE;
const SET = opt("set", base.startsWith("file:") ? "artifact" : "fixtures");
const SNAPSHOT = opt("snapshot", null);
const JSON_OUT = opt("json", null);
const CHROME = process.env.CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

/** mulberry32: a small seeded generator, so a failing walk can be replayed exactly. */
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rand = rng(SEED);
const pick = (xs) => xs[Math.floor(rand() * xs.length)];

// ------------------------------------------------------------------------------------------------ routes
const G = "0022400252";
const P = "1628378";
const N = "2024-11-20";
// [name, hash, expect]; expect: "ok" (a real page), "notfound" (must say it could not find the page), "any" (must not crash).
const FIXTURE_ROUTES = [
  ["discover", "#/", "ok"],
  ["discover-night", `#/?date=${N}`, "ok"],
  ["discover-night-2", "#/?date=2024-11-12", "ok"],
  ["game", `#/game/${G}`, "ok"],
  ["leg-pts", `#/leg/${G}/${P}/pts`, "ok"],
  ["leg-reb-under", `#/leg/${G}/${P}/reb?side=under`, "ok"],
  ["leg-ast", `#/leg/${G}/${P}/ast`, "ok"],
  ["swipe", "#/swipe", "ok"],
  ["swipe-night", `#/swipe?date=${N}`, "ok"],
  ["picks", "#/picks", "ok"],
  ["parlay", "#/parlay", "ok"],
  ["parlay-tickets", "#/parlay?date=2024-01-01", "ok"],
  ["prefs", "#/prefs", "ok"],
  ["signals", "#/signals", "ok"],
  ["hedge", "#/hedge", "ok"],
  ["hedge-prefill", `#/hedge?game=${G}&player=${P}&stat=pts`, "ok"],
  ["history", "#/history", "ok"],
  ["record", "#/record", "ok"],
  ["record-night", `#/record?date=${N}`, "ok"],
  ["record-cal", "#/record?tab=calibration", "ok"],
  ["record-guess", "#/record?tab=guess", "ok"],
  ["live", "#/live", "ok"],
  ["live-night", "#/live?date=2026-11-09", "ok"],
  ["live-final", "#/live/401909952?date=2026-11-09", "ok"],
  ["live-pre", "#/live/401909955?date=2026-11-09", "ok"],
  ["alerts", "#/alerts", "ok"],
  ["admin", "#/admin", "ok"],
  ["terms", "#/terms", "ok"],
  ["terms-anchor", "#/terms?t=holdout", "ok"],
  ["terms-market-anchor", "#/terms?t=label-strong", "ok"],
  ["terms-search", "#/terms?q=interval", "ok"],
  ["terms-bad", "#/terms?t=%3Cb%3E", "notfound"],
  ["source-prop", `#/source/${G}/${P}/pts`, "ok"],
  ["source-game", `#/source/${G}`, "ok"],
  ["source-bad", `#/source/${G}/abc/pts`, "notfound"],
  ["scorecard", "#/scorecard", "ok"],
  ["tests", "#/tests", "ok"],
  ["help-errors", "#/help/errors", "ok"],
  ["help-code", "#/help/errors/stale_data", "ok"],
  ["help-search", "#/help/errors?q=forecast", "ok"],
  ["help-bad", "#/help/errors/%3Cb%3E", "notfound"],
  // hostile
  ["unknown-hash", "#/zzz", "notfound"],
  ["unknown-deep", "#/zzz/yyy?x=1", "notfound"],
  ["bad-game", "#/game/nope", "notfound"],
  ["bad-leg-player", `#/leg/${G}/999/pts`, "notfound"],
  ["bad-leg-stat", `#/leg/${G}/${P}/zzz`, "notfound"],
  ["bad-leg-pid", `#/leg/${G}/abc/pts`, "notfound"],
  ["bad-date", "#/?date=garbage", "any"],
  ["far-date", "#/?date=2099-01-01", "any"],
  ["bad-record-date", "#/record?date=1999-01-01", "any"],
  ["bad-live", "#/live/zzz", "any"],
  ["hostile-hedge", "#/hedge?game=%3Cscript%3Ealert(1)%3C%2Fscript%3E&player=abc&stat=%22onload%3D", "any"],
  ["bad-swipe-date", "#/swipe?date=2099-01-01", "any"],
  ["encoded-junk", "#/game/%E0%A4%A", "any"],
];
// The artifact build reads plain names; detail pages open by hash at start.
const ARTIFACT_ROUTES = [
  ["discover", "#discover", "ok"],
  ["swipe", "#swipe", "ok"],
  ["picks", "#picks", "ok"],
  ["signals", "#signals", "ok"],
  ["hedge", "#hedge", "ok"],
  ["history", "#history", "ok"],
  ["record", "#record", "lock"],
  ["live", "#live", "ok"],
  ["alerts", "#alerts", "ok"],
  ["prefs", "#prefs", "ok"],
  ["terms", "#terms", "ok"],
  ["scorecard", "#scorecard", "ok"],
  ["tests", "#tests", "ok"],
  ["help", "#help", "ok"],
  ["record-cal", "#/record?tab=calibration", "ok"],
  ["record-guess", "#/record?tab=guess", "ok"],
  ["unknown", "#zzz", "notfound"],
];
const ROUTES = SET === "artifact" ? [...ARTIFACT_ROUTES] : FIXTURE_ROUTES;
// The Learn lens promise covers these (scripts/learn-dom.mjs); elsewhere hits are reported, not failed.
const LEARN_CHECKED = /^(discover|game|leg|swipe|picks|parlay|signals|hedge$|history|record|terms|source|scorecard|tests|help)/;

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
  /\bbook \d{1,3}(\.\d)?%/i, // a book's chance beside ours ("book 51%")
];
// Desk money that only a private session may show (History's balance tile and P&L column).
const MONEY_RE = [/Paper balance/i, /Net P&L/i, /\$\d[\d,]*(\.\d\d)? start, \$\d/];
const LOCK_FROM = "2026-10-30";
const LOCK_WORDS = ["part of a locked test until about Dec 1", "Result hidden while the model's number for this night is a locked test"];
const NOT_FOUND_RE = /could not find that page|There is nothing at that address/i;
// API 404s say what was missing in the registry's words (a game, a forecast); any of these is a not-found screen.
const NOT_FOUND_CODES = ["bad_route", "not_found", "unknown_game", "unknown_slate", "unknown_player", "unknown_stat", "no_forecast", "not_a_date"];

// ------------------------------------------------------------------------------------------------ chrome
const profile = mkdtempSync(join(tmpdir(), "ph-walk-"));
// A free port from the system, never one derived from the seed: two walkers with the same seed used to pick the same
// port, and the second one then drove the first one's browser (hangs and false violations when lanes ran at once).
const port = await new Promise((resolve, reject) => {
  const probe = createServer();
  probe.once("error", reject);
  probe.listen(0, "127.0.0.1", () => {
    const { port: free } = probe.address();
    probe.close(() => {
      resolve(free);
    });
  });
});
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--no-first-run", "--disable-gpu", "--allow-file-access-from-files", "about:blank"], { stdio: "ignore" });
let target;
for (let i = 0; i < 60 && !target; i++) {
  try {
    const l = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
    target = l.find((t) => t.type === "page");
  } catch {
    /* not up yet */
  }
  if (!target) await sleep(250);
}
if (!target) {
  chrome.kill();
  throw new Error("chrome did not start");
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let msgId = 0;
const pend = new Map();
let exceptions = [];
let failedRequests = [];
let scenario = "none";
let midCount = 0;
const injected = new Map();
// A watchdog for the browser itself: headless Chrome can stall under load (seen with several walkers on one small
// machine). If no CDP answer arrives for STALL_MS the walk stops with exit code 3: an infrastructure failure, not a page
// violation, so the caller (app/scripts/walk.sh) retries it once.
const STALL_MS = 90_000;
let stallTimer = null;
const armStall = () => {
  if (stallTimer) clearTimeout(stallTimer);
  stallTimer = setTimeout(() => {
    console.error(`route-walk: the browser gave no answer for ${String(STALL_MS / 1000)} s; stopping (exit 3: a stalled browser, not a page violation)`);
    try {
      chrome.kill();
    } catch {
      /* already gone */
    }
    process.exit(3);
  }, STALL_MS);
};
const send = (method, params = {}) =>
  new Promise((r) => {
    const i = ++msgId;
    pend.set(i, (m) => {
      armStall();
      r(m);
    });
    armStall();
    ws.send(JSON.stringify({ id: i, method, params }));
  });
const b64 = (s) => Buffer.from(s).toString("base64");
async function onPaused(p) {
  const url = p.request.url;
  const isStream = /\/stream(\?|$)/.test(url);
  const go = () => send("Fetch.continueRequest", { requestId: p.requestId });
  const s = scenario;
  injected.set(s, (injected.get(s) ?? 0) + 1);
  if (s === "down") return send("Fetch.failRequest", { requestId: p.requestId, errorReason: "ConnectionRefused" });
  if (s === "sse-kill") return isStream ? send("Fetch.failRequest", { requestId: p.requestId, errorReason: "ConnectionReset" }) : go();
  if (s === "garbage-200") {
    // A well-formed answer of the wrong shape: the error boundary, not a blank page, must catch what breaks.
    const body = pick(['{"unexpected":true}', "[]", "null", '{"games":"nope","counts":null}', "not json"]);
    return send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 200, responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: b64(body) });
  }
  if (s === "slow") {
    await sleep(2500);
    return go();
  }
  const refuse503 = s === "503" || s === "503-new" || (s === "mid-503" && ++midCount > 2);
  if (refuse503) {
    const body = s === "503-new"
      ? JSON.stringify({ ok: false, code: "export_refused", text: "Today's data failed its checks", field: null, request_id: "walkreq42", detail: "injected reason R456" })
      : JSON.stringify({ detail: "injected reason R123" });
    const headers = [{ name: "Content-Type", value: "application/json" }];
    if (s === "503-new") headers.push({ name: "X-Request-Id", value: "walkreq42" });
    return send("Fetch.fulfillRequest", { requestId: p.requestId, responseCode: 503, responseHeaders: headers, body: b64(body) });
  }
  return go();
}
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) {
    pend.get(m.id)(m);
    pend.delete(m.id);
    return;
  }
  if (m.method === "Runtime.exceptionThrown") exceptions.push((m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text).slice(0, 300));
  if (m.method === "Network.responseReceived" && m.params.response.status >= 400 && m.params.response.url.includes("/api/")) {
    failedRequests.push(`${m.params.response.status} ${new URL(m.params.response.url).pathname}`);
  }
  if (m.method === "Fetch.requestPaused") void onPaused(m.params);
};
const ev = async (js) => {
  const r = await send("Runtime.evaluate", { expression: js, awaitPromise: true, returnByValue: true });
  return r.result?.result?.value ?? r.result?.exceptionDetails?.exception?.description;
};
await send("Page.enable");
await send("Runtime.enable");
await send("Network.enable");
await send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: 844, deviceScaleFactor: 1, mobile: WIDTH < 700 });
if (INJECT) await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*" }] });

const OPEN_ALL = "document.querySelectorAll('details').forEach(d=>{d.open=true})";
/** Wait until nothing is loading (no aria-busy) and main has text, up to a cap. */
async function settle(capMs = 4000) {
  const t0 = Date.now();
  while (Date.now() - t0 < capMs) {
    const ready = await ev(`(()=>{const m=document.querySelector('main'); return !!m && !document.querySelector('[aria-busy="true"]') && (m.innerText||'').trim().length>0})()`);
    if (ready === true) break;
    await sleep(150);
  }
  await sleep(200);
}
async function readPage() {
  const raw = await ev(`(()=>{
    const m=document.querySelector('main');
    const mainText=m?(m.innerText||'').replace(/\\s+/g,' ').trim():'';
    const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT,{acceptNode:(n)=>/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(n.parentElement?n.parentElement.tagName:'')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT}); const parts=[]; while(w.nextNode()) parts.push(w.currentNode.nodeValue);
    const attrs=[]; document.querySelectorAll('main [aria-label],main [title],main [alt],main [aria-valuetext]').forEach(e=>{for(const a of ['aria-label','title','alt','aria-valuetext']){const v=e.getAttribute(a); if(v) attrs.push(v)}});
    const err=[...document.querySelectorAll('.panel.error')].map(p=>({code:p.getAttribute('data-code'), text:(p.innerText||'').replace(/\\s+/g,' '), action: !!p.querySelector('button, a[href]')}));
    return JSON.stringify({hash: location.hash, mainText, mainHtmlLen: m?m.innerHTML.length:0, body: parts.join(' ').replace(/\\s+/g,' '), attrs: attrs.join(' | '), overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, err});
  })()`);
  try {
    return JSON.parse(raw);
  } catch {
    return { hash: "?", mainText: "", body: "", attrs: "", overflow: 0, err: [], evalError: String(raw) };
  }
}

/** Long dates the app prints for nights inside the lock window ("Sat, Nov 14, 2026"). */
function lockedNightOnScreen(text) {
  const months = { Jan: 1, Feb: 2, Mar: 3, Apr: 4, May: 5, Jun: 6, Jul: 7, Aug: 8, Sep: 9, Oct: 10, Nov: 11, Dec: 12 };
  for (const m of text.matchAll(/\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (20\d\d)\b/g)) {
    const iso = `${m[3]}-${String(months[m[1]]).padStart(2, "0")}-${m[2].padStart(2, "0")}`;
    if (iso >= LOCK_FROM) return iso;
  }
  return null;
}

function check(route, lens, page, scen) {
  const v = [];
  const [name, , expect] = route;
  if (exceptions.length) v.push(`uncaught exception: ${exceptions[0]}`);
  if (page.evalError) v.push(`page did not answer: ${page.evalError.slice(0, 120)}`);
  if (page.mainText.length === 0) v.push("blank page: <main> has no text");
  if (page.overflow > 1) v.push(`horizontal scroll: ${page.overflow}px wider than the viewport`);
  for (const re of MONEY_RE) if (re.test(page.body)) v.push(`private money text in a non-private session: ${re}`);
  // On a snapshot whose every night is inside the lock window, the replay must carry the lock wording and a finished
  // game page must hide its per-prop results.
  if (allLocked && expect === "lock" && !LOCK_WORDS.some((w) => page.mainText.includes(w))) v.push("LINE_CAL lock: locked replay without the lock wording");
  if (allLocked && /How it ended/.test(page.mainText) && /went (over|under)|\bhit\b.*\d+%/i.test(page.mainText) && !LOCK_WORDS.some((w) => page.mainText.includes(w))) {
    v.push("LINE_CAL lock: a finished game on a locked night shows results without the lock wording");
  }
  if (allLocked) lockChecks.n += 1;
  const locked = lockedNightOnScreen(page.mainText);
  // "went over" alone is not enough: a leg page lists earlier games' box-score results (history, not the locked night's
  // own outcome). A finished game says "Final".
  if (locked && /\bFinal\b/.test(page.mainText) && /\b\d{1,2}%/.test(page.mainText) && !LOCK_WORDS.some((w) => page.mainText.includes(w))) {
    v.push(`LINE_CAL lock: locked night ${locked} shows finished games and a percentage but no lock wording`);
  }
  if (lens === "learn" && LEARN_CHECKED.test(name) && scen === "none") {
    const clean = `${page.body} ${page.attrs}`.replace(/1-800-GAMBLER/g, "");
    for (const re of PRICE_RE) {
      const m = clean.match(re);
      if (m) v.push(`Learn lens price word "${m[0]}": ...${clean.slice(Math.max(0, m.index - 40), m.index + 40)}...`);
    }
  }
  if (expect === "notfound" && scen === "none" && !NOT_FOUND_RE.test(page.mainText) && !page.err.some((e) => NOT_FOUND_CODES.includes(e.code))) v.push("expected a not-found screen");
  for (const e of page.err) if (!e.action) v.push("failure panel with no way on (no button or link)");
  if ((scen === "503" || scen === "503-new" || scen === "mid-503") && page.err.length > 0) {
    const want = scen === "503-new" ? "R456" : "R123";
    for (const e of page.err) {
      const refusal = e.code === "export_refused" || /failed its checks/.test(e.text);
      if (refusal && !e.text.includes(want)) v.push(`503 panel without its reason (${want})`);
    }
  }
  return v;
}

// ------------------------------------------------------------------------------------------------ walk
let allLocked = false;
const lockChecks = { n: 0 };
const LENSES = ["learn", "explore", "market"];
const SCENARIOS = GARBAGE ? ["garbage-200"] : ["down", "503", "503-new", "mid-503", "slow", "sse-kill"];
const stats = { steps: 0, violations: 0, byKind: {}, routes: {}, failedRequests: {}, scenarios: {} };
const log = [];
async function visitorStart(lens) {
  await send("Page.navigate", { url: base });
  await sleep(600);
  if (SET === "artifact") {
    const dates = await ev(`(()=>{try{return JSON.parse(document.getElementById('ph-baked').textContent).dates}catch(e){return null}})()`);
    allLocked = Array.isArray(dates) && dates.length > 0 && dates.every((d) => d >= LOCK_FROM);
    if (!ROUTES.some((r) => r[0].startsWith("art-game"))) {
      // The snapshot's own game pages (ids change nightly, so they are read from Discover, not listed here).
      await ev(`localStorage.setItem('ph.welcomed','1'); localStorage.setItem('ph.tour','1'); 0`);
      await send("Page.navigate", { url: `${base}#discover` });
      await sleep(400);
      await send("Page.reload");
      await settle(5000);
      const hrefs = await ev(`JSON.stringify([...new Set([...document.querySelectorAll('a[href^="#/game/"]')].map(a=>a.getAttribute('href')))].slice(0,4))`);
      for (const [i, h] of JSON.parse(typeof hrefs === "string" ? hrefs : "[]").entries()) ROUTES.push([`art-game-${i}`, h, "ok"]);
    }
  }
  await ev(`localStorage.clear(); sessionStorage.clear(); localStorage.setItem('ph.welcomed','1'); localStorage.setItem('ph.tour','1'); localStorage.setItem('ph.name','Walker'); localStorage.setItem('ph.lens','${lens}'); 0`);
}
async function go(hash, how) {
  if (how === "reload") {
    await send("Page.navigate", { url: base + hash });
    await sleep(100);
    await send("Page.reload");
  } else {
    await ev(`(()=>{location.hash=${JSON.stringify(hash)}; 0})()`);
  }
}

if (SNAPSHOT) {
  // Fixed order, Market lens and Learn lens, full reload per route, every <details> opened: the before/after text
  // comparison for refactors.
  const snap = {};
  for (const lens of ["market", "learn"]) {
    await visitorStart(lens);
    for (const r of ROUTES) {
      exceptions = [];
      await go(r[1], "reload");
      await settle(5000);
      await ev(`(async()=>{${OPEN_ALL}; await new Promise(r=>setTimeout(r,250)); ${OPEN_ALL}})()`);
      await sleep(300);
      const page = await readPage();
      snap[`${lens} ${r[0]}`] = page.mainText;
    }
  }
  writeFileSync(SNAPSHOT, JSON.stringify(snap, null, 1));
  console.log(`snapshot: ${Object.keys(snap).length} pages -> ${SNAPSHOT}`);
} else {
  for (let vi = 0; vi < VISITORS; vi++) {
    const lens = LENSES[vi % LENSES.length];
    await visitorStart(lens);
    let first = true;
    for (let s = 0; s < STEPS; s++) {
      exceptions = [];
      failedRequests = [];
      midCount = 0;
      scenario = INJECT && !first && rand() < 0.35 ? pick(SCENARIOS) : "none";
      stats.scenarios[scenario] = (stats.scenarios[scenario] ?? 0) + 1;
      const roll = rand();
      let route = pick(ROUTES);
      let how = first ? "reload" : roll < 0.6 ? "hash" : roll < 0.75 ? "reload" : roll < 0.9 ? "link" : "rapid";
      first = false;
      if (how === "link") {
        const href = await ev(`(()=>{const a=[...document.querySelectorAll('main a[href^="#"]')].map(x=>x.getAttribute('href')).filter(h=>h!=='#main'&&h!=='#'); return a.length?a[${Math.floor(rand() * 1000)}%a.length]:null})()`);
        if (typeof href === "string") route = [`link:${href}`, href, "any"];
        else how = "hash";
      }
      if (how === "rapid") {
        const other = pick(ROUTES);
        await go(other[1], "hash");
        await sleep(30);
      }
      await go(route[1], how === "link" ? "hash" : how);
      await settle(scenario === "slow" ? 6000 : 4000);
      if (lens === "learn" && LEARN_CHECKED.test(route[0])) {
        await ev(`(async()=>{${OPEN_ALL}; await new Promise(r=>setTimeout(r,200)); ${OPEN_ALL}})()`);
        await sleep(200);
      }
      const page = await readPage();
      const v = check(route, lens, page, scenario);
      stats.steps++;
      stats.routes[route[0]] = (stats.routes[route[0]] ?? 0) + 1;
      for (const f of failedRequests) stats.failedRequests[f.replace(/\d{4}-\d\d-\d\d/, "{date}")] = (stats.failedRequests[f.replace(/\d{4}-\d\d-\d\d/, "{date}")] ?? 0) + 1;
      if (v.length) {
        stats.violations += v.length;
        for (const x of v) {
          const k = x.split(":")[0];
          stats.byKind[k] = (stats.byKind[k] ?? 0) + 1;
        }
        log.push({ visitor: vi, step: s, lens, how, scenario, route: route[0], hash: route[1], violations: v });
      }
    }
  }
  if (INJECT) await send("Fetch.disable");
  console.log(`route-walk: set=${SET} seed=${SEED} visitors=${VISITORS} steps=${stats.steps} width=${WIDTH} inject=${INJECT}`);
  console.log(`scenarios: ${JSON.stringify(stats.scenarios)}`);
  console.log(`API answers >= 400 seen (informational): ${JSON.stringify(stats.failedRequests)}`);
  const kinds = {};
  for (const [r, n] of Object.entries(stats.routes)) {
    const k = r.startsWith("link:") ? `link:${r.slice(5).replace(/^#\/?/, "").split(/[/?]/)[0] || "discover"}` : r;
    kinds[k] = (kinds[k] ?? 0) + n;
  }
  console.log(`steps by route: ${JSON.stringify(kinds)}`);
  for (const l of log.slice(0, 40)) console.log(`  v${l.visitor} s${l.step} ${l.lens} ${l.how} [${l.scenario}] ${l.route} ${l.hash}\n      ${l.violations.join("\n      ")}`);
  if (log.length > 40) console.log(`  ... ${log.length - 40} more steps with violations`);
  if (SET === "artifact") console.log(`lock window: every baked night locked = ${allLocked}; lock checks run on ${lockChecks.n} steps`);
  console.log(`violations: ${stats.violations} ${JSON.stringify(stats.byKind)}`);
  if (JSON_OUT) writeFileSync(JSON_OUT, JSON.stringify({ stats, log }, null, 1));
}
ws.close();
chrome.kill();
await sleep(300);
try {
  rmSync(profile, { recursive: true, force: true });
} catch {
  /* the profile is in the system temp folder; leaving it is harmless */
}
if (stallTimer) clearTimeout(stallTimer);
process.exit(!SNAPSHOT && stats.violations > 0 ? 1 : 0);
