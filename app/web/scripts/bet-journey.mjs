// The betting tools' end-to-end journey in headless Chrome over CDP (a developer tool; nothing here ships). It needs a
// web server built with the betting switch on, proxying an API with the switch on (see app/README.md).
//
//   node scripts/bet-journey.mjs http://127.0.0.1:5230/ <outdir>        # widths 375 and 1280, dark and light
//
// Journey: open a real player from the newest night that has stored quotes -> "Add to slip" on the player page -> the
// slip page -> Compare -> type two venue prices -> add both -> "Mark slip as placed" (cancel once: focus returns; then
// confirm: focus lands on the Hedge Finder link) -> the Hedge Finder shows that position -> reload -> the slip still
// holds it. On every step: no uncaught exception, no page-level horizontal scroll, and on every priced screen the
// money-safety note. Exits non-zero on the first failed assertion and prints what failed.
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [base, outDir] = process.argv.slice(2);
if (!base || !outDir) throw new Error("usage: bet-journey.mjs <base url> <outdir>");
mkdirSync(outDir, { recursive: true });
const NOTE = "Analysis only, not advice: no orders are placed.";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9500 + Math.floor(Math.random() * 300);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = async (p) => {
  const r = await fetch(new URL(p, base));
  return r.ok ? r.json() : null;
};

// ---- a real prop: the newest night whose betting slate has a prop with stored quotes (else any prop with a line)
async function choose() {
  const nights = ((await get("/api/slates")) ?? []).map((n) => n.date).sort().reverse();
  for (const d of nights) {
    const bs = await get(`/api/betting/slate/${d}`);
    const p = bs?.props?.find((x) => x.n_providers > 0 && ["pts", "reb", "ast", "fg3m"].includes(x.stat));
    if (p) {
      const g = await get(`/api/games/${p.game_id}`);
      const line = g?.players.find((x) => x.player_id === p.player_id)?.props?.[p.stat]?.line;
      if (line !== undefined) return { game: p.game_id, player: p.player_id, name: p.player, stat: p.stat, stored: true };
    }
  }
  for (const d of nights) {
    const sl = await get(`/api/slates/${d}`);
    for (const g of sl?.games ?? []) {
      const kp = g.key_props?.find((k) => ["pts", "reb", "ast", "fg3m"].includes(k.stat));
      if (kp) {
        const gd = await get(`/api/games/${g.game_id}`);
        const pl = gd?.players.find((x) => x.player_id === kp.player_id);
        if (pl) return { game: g.game_id, player: kp.player_id, name: pl.name, stat: kp.stat, stored: false };
      }
    }
  }
  throw new Error("no prop to use: the API has no slates");
}
const PROP = await choose();
console.log(`prop: ${PROP.name} ${PROP.stat} (game ${PROP.game}; stored quotes: ${PROP.stored})`);

const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${PORT}`, `--user-data-dir=${join(outDir, ".profile")}`, "about:blank"], { stdio: "ignore" });
let tabs;
for (let i = 0; i < 60; i++) {
  try {
    tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
    if (tabs.find((t) => t.type === "page")) break;
  } catch {
    /* chrome not up yet */
  }
  await sleep(200);
}
const ws = new WebSocket(tabs.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0;
const pend = {};
const exceptions = [];
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.method === "Runtime.exceptionThrown") exceptions.push(m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text);
  if (m.id && pend[m.id]) {
    pend[m.id](m);
    delete pend[m.id];
  }
};
const send = (method, params = {}) =>
  new Promise((r) => {
    const i = ++id;
    pend[i] = r;
    ws.send(JSON.stringify({ id: i, method, params }));
  });
const ev = async (js) => {
  const r = await send("Runtime.evaluate", { expression: `(async()=>{${js}})()`, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error(`page script failed: ${r.result.exceptionDetails.exception?.description ?? r.result.exceptionDetails.text}`);
  return r.result?.result?.value;
};
await send("Page.enable");
await send("Runtime.enable");

// ---- page helpers (run inside the page)
const H = `
const T = (el) => (el?.textContent ?? "").replace(/\\s+/g, " ").trim();
const byText = (sel, start, root = document) => [...root.querySelectorAll(sel)].find((e) => T(e).startsWith(start) && e.offsetParent !== null);
const fill = (el, v) => { const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value"); d.set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); };
`;
async function until(js, what, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await ev(`${H} return !!(${js});`)) return;
    await sleep(200);
  }
  throw new Error(`timed out waiting for: ${what}`);
}

let step = 0;
async function check(label, w, theme, priced) {
  const over = await ev(`return document.documentElement.scrollWidth > window.innerWidth + 1;`);
  if (over) throw new Error(`${label}: the page scrolls sideways at ${w}px`);
  if (priced && !(await ev(`return document.body.innerText.includes(${JSON.stringify(NOTE)});`))) throw new Error(`${label}: the money-safety note is missing`);
  if (exceptions.length) throw new Error(`${label}: uncaught exception: ${exceptions[0]}`);
  const metrics = await send("Page.getLayoutMetrics");
  const ch = Math.min(Math.ceil(metrics.result.cssContentSize.height), 6000);
  const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: w, height: ch, scale: 1 } });
  writeFileSync(join(outDir, `${String(++step).padStart(2, "0")}_${label}_${w}_${theme}.png`), Buffer.from(r.result.data, "base64"));
  console.log(`  ok  ${label}`);
}
const go = async (hash) => {
  await ev(`location.hash = ${JSON.stringify(hash)}; return 0;`);
  await sleep(600);
};
const click = async (sel, start, what) => {
  await until(`byText(${JSON.stringify(sel)}, ${JSON.stringify(start)})`, what);
  // focus first, as a real click or a keyboard press does, so a dialog knows where to send focus back
  await ev(`${H} const el = byText(${JSON.stringify(sel)}, ${JSON.stringify(start)}); el.focus(); el.click(); return 0;`);
  await sleep(400);
};

async function journey(w, theme) {
  console.log(`journey at ${w}px, ${theme}`);
  exceptions.length = 0;
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: w < 600 ? 812 : 900, deviceScaleFactor: 1, mobile: w < 600 });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
  await send("Page.navigate", { url: base });
  await sleep(1000);
  await ev(`localStorage.clear(); localStorage.setItem('ph.theme','${theme}'); localStorage.setItem('ph.welcomed','1'); localStorage.setItem('ph.name','Devin'); localStorage.setItem('ph.lens','market'); return 0;`);
  await send("Page.reload");
  await sleep(1200);

  // 1. the player page: add to slip (no price)
  await go(`#/leg/${PROP.game}/${PROP.player}/${PROP.stat}`);
  await until(`document.querySelector('.ph-bet-add button')`, "the Add to slip button");
  await check("leg", w, theme, false);
  await ev(`document.querySelector('.ph-bet-add button').click(); return 0;`);
  await until(`document.body.innerText.includes("Added to your slip")`, "the add toast");

  // 2. the slip: the pick waits for a price
  await go("#/bet/slip");
  await until(`byText('a', 'Compare prices')`, "the pick in the slip");
  await check("slip-pick", w, theme, true);
  await click("a", "Compare prices", "Compare prices");

  // 3. Compare: type two venue prices and add both
  await until(`document.querySelector('.ph-bet-card')`, "the venue cards");
  await check("compare", w, theme, true);
  for (const [venue, price, fee] of [["Kalshi", "48", "1"], ["DraftKings", "-110", "0"]]) {
    const card = `[...document.querySelectorAll('.ph-bet-card')].find((c) => T(c.querySelector('h3')) === ${JSON.stringify(venue)})`;
    await until(card, `the ${venue} card`);
    await ev(`${H} const c = ${card}; byText('button', 'Type a', c).click(); return 0;`);
    await until(`(${card}).querySelector('form input')`, `the ${venue} price form`);
    await ev(`${H} const c = ${card}; const ins = c.querySelectorAll('form input'); fill(ins[0], ${JSON.stringify(price)}); fill(ins[1], ${JSON.stringify(fee)}); return 0;`);
    await ev(`${H} const c = ${card}; c.querySelector('form button[type=submit]').click(); return 0;`);
    await until(`!(${card}).querySelector('form') && T(${card}).includes('manual')`, `${venue}'s typed price on its card`);
    await ev(`${H} byText('button', 'Add ${venue} to slip').click(); return 0;`);
    await until(`document.body.innerText.includes("In your slip at")`, `${venue} added`);
  }
  await check("compare-typed", w, theme, true);
  await ev(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return 0;`);

  // 4. mark placed: cancel once (focus returns), then confirm (focus on the Hedge Finder link)
  await go("#/bet/slip");
  await click("main button", "Mark slip as placed", "Mark slip as placed");
  await until(`document.querySelector('[role=dialog][aria-modal=true]')`, "the confirmation dialog");
  if (!(await ev(`return !!document.activeElement?.closest('[role=dialog]');`))) throw new Error("focus did not move into the dialog");
  await check("place-dialog", w, theme, true);
  await click("[role=dialog] button", "Cancel", "Cancel");
  if ((await ev(`${H} return T(document.activeElement);`)) !== "Mark slip as placed") throw new Error("focus did not return to Mark slip as placed after Cancel");
  console.log("  ok  focus returned after Cancel");
  await click("main button", "Mark slip as placed", "Mark slip as placed");
  await click("[role=dialog] button", "I already placed these", "confirm");
  await until(`document.body.innerText.includes("Recorded positions")`, "the recorded positions");
  await sleep(300);
  if ((await ev(`${H} return T(document.activeElement);`)) !== "Open Hedge Finder with these positions") throw new Error("focus did not land on the Hedge Finder link after confirming");
  console.log("  ok  focus on the Hedge Finder link after confirming");
  await check("slip-placed", w, theme, true);

  // 5. the Hedge Finder carries the position
  await click("main a", "Open Hedge Finder with these positions", "Hedge Finder link");
  await until(`document.querySelector('.ph-bet-held') && document.querySelector('.ph-bet-held').textContent.includes(${JSON.stringify(PROP.name)})`, "the held position chip");
  await until(`document.querySelector('.ph-bet-card')`, "the hedge venue cards");
  await check("hedge", w, theme, true);

  // 6. reload: the slip is still there
  await send("Page.reload");
  await sleep(1500);
  await go("#/bet/slip");
  await until(`document.body.innerText.includes("Recorded positions") && document.body.innerText.includes(${JSON.stringify(PROP.name)})`, "the slip after reload");
  const n = await ev(`return document.querySelectorAll('main .ph-bet-leg.recorded').length;`);
  if (n !== 2) throw new Error(`expected 2 recorded legs after reload, saw ${n}`);
  await check("slip-reloaded", w, theme, true);
}

// In the Learn and Explore lenses no betting UI shows: no add buttons, no slip, and #/bet/... is the "Market screen" note.
async function learnLens() {
  console.log("learn and explore lenses");
  for (const lens of ["learn", "explore"]) {
    await ev(`localStorage.setItem('ph.lens', '${lens}'); return 0;`);
    await send("Page.reload");
    await sleep(1200);
    await go(`#/leg/${PROP.game}/${PROP.player}/${PROP.stat}`);
    await until(`document.querySelector('main h1')`, "the player page");
    await sleep(800);
    if (await ev(`return !!document.querySelector('.ph-bet-add, .ph-bet-launcher, .ph-bet-panel');`)) throw new Error(`${lens}: betting UI on the player page`);
    for (const h of ["#/bet", "#/bet/slip", "#/bet/compare", "#/bet/hedge"]) {
      await go(h);
      await until(`document.body.innerText.includes("is a Market screen")`, `${lens}: ${h} as a Market screen`);
      if (await ev(`return !!document.querySelector('.ph-bet');`)) throw new Error(`${lens}: ${h} rendered betting UI`);
    }
    console.log(`  ok  ${lens}: no betting UI`);
  }
}

let failed = null;
try {
  for (const w of [375, 1280]) for (const theme of ["dark", "light"]) await journey(w, theme);
  await learnLens();
} catch (e) {
  failed = e;
}
ws.close();
chrome.kill();
if (failed) {
  console.error(`JOURNEY FAILED: ${failed.message}`);
  process.exit(1);
}
console.log("JOURNEY PASSED (375 and 1280, dark and light)");
