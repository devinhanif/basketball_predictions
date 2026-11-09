// Screenshots of every screen at 390x844 and 1440x900, dark and light, plus the contrast/size audit over the rendered DOM.
// A developer tool (headless Chrome over CDP); nothing here ships.
//
//   node scripts/shots.mjs regular http://127.0.0.1:5273   <outdir>     # the regular build against a running dev server/preview
//   node scripts/shots.mjs artifact file://.../dist-artifact/preview.html <outdir>
//
// For the artifact it injects a fake window.claude (a friend's picks in an in-memory db) so the shared-picks screens can be seen.
import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { join } from "node:path";

const [kind, base, outDir] = process.argv.slice(2);
if (!kind || !base || !outDir) throw new Error("usage: shots.mjs <regular|artifact> <url> <outdir>");
const artifact = kind === "artifact";
mkdirSync(outDir, { recursive: true });
const root = new URL("..", import.meta.url).pathname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9400 + Math.floor(Math.random() * 400);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const auditSrc = readFileSync(join(root, "scripts", "audit-dom.js"), "utf8");

// ---- data for seeded picks: cards of a played night, from the baked file ----
const baked = JSON.parse(readFileSync(join(root, "artifact-data", "baked.json"), "utf8"));
const NIGHT = baked.swipe_nights.at(-1);
const nightShard = JSON.parse(gunzipSync(Buffer.from(baked.shards[`d:${NIGHT}`], "base64")).toString("utf8"));
const cards = nightShard[`/api/slates/${NIGHT}/swipe`].b.cards;
const results = nightShard[`/api/slates/${NIGHT}/results`].b.rows;
const slate = baked.simple[`/api/slates/${NIGHT}`].b;
const GAME = slate.games[0].game_id;
// a played prop with a baked breakdown: first key prop of the first game
const kp = slate.games[0].key_props[0];
const picked = results.filter((r) => cards.some((c) => c.prop_id === r.prop_id)).slice(0, 14);
const mk = (c, side, i) => ({
  prop_id: c.prop_id, slate_date: NIGHT, game_id: c.game_id, player: c.player, team: c.team, opponent: c.opponent, stat: c.stat,
  line: c.line, over_price: c.over_price, under_price: c.under_price, tip_utc: c.tip_utc, side,
  made_at: new Date(Date.UTC(2026, 10, 8, 18, 0, i)).toISOString(), saw_model: i % 3 === 0,
});
const myPicks = picked.slice(0, 9).map((r, i) => mk(cards.find((c) => c.prop_id === r.prop_id), i % 2 ? "under" : "over", i));
const samPicks = picked.slice(3, 12).map((r, i) => mk(cards.find((c) => c.prop_id === r.prop_id), i % 3 ? "over" : "under", i + 20));
const ledger = myPicks.map((p) => ({
  ...p, id: p.prop_id, slate_clock: p.made_at, response_ms: 1800, source: "swipe",
}));

const fakeClaude = readFileSync(join(root, "scripts", "fake-claude.js"), "utf8")
  .replace("__SAM__", JSON.stringify({ v: 1, picks: samPicks }));

const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(outDir, ".profile")}`, "--allow-file-access-from-files", "about:blank"], { stdio: "ignore" });
let tabs;
for (let i = 0; i < 60; i++) {
  try {
    tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
    if (tabs.find((t) => t.type === "page")) break;
  } catch { /* chrome not up yet */ }
  await sleep(200);
}
const page = tabs.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0;
const pend = {};
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; }
};
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (js) => {
  const r = await send("Runtime.evaluate", { expression: js, awaitPromise: true, returnByValue: true });
  return r.result?.result?.value ?? r.result?.exceptionDetails?.exception?.description;
};
await send("Page.enable");
if (artifact) await send("Page.addScriptToEvaluateOnNewDocument", { source: fakeClaude });

const KEY = `document.dispatchEvent(new KeyboardEvent('keydown', {key:'k', ctrlKey:true, bubbles:true}))`;
const FIRSTPICK = `await new Promise(r=>setTimeout(r,300)); document.querySelector('.pick-row')?.click()`;
const SCREENS = artifact
  ? [
      ["today", "#discover", ""],
      ["game", "#discover", `document.querySelector('a.game').click()`],
      ["leg", "#discover", `document.querySelector('a.game').click(); await new Promise(r=>setTimeout(r,500)); document.querySelector('a.leg-open').click()`],
      ["parlay", "#discover", `const a=[...document.querySelectorAll('a')].find(x=>x.getAttribute('href')?.startsWith('#/parlay')); (a||document.querySelector('a')).click()`],
      ["swipe", "#swipe", ""],
      ["picks", "#picks", ""],
      ["settings", "#prefs", ""],
      ["signals", "#signals", ""],
      ["hedge", "#hedge", ""],
      ["hedgelab", "#hedge", FIRSTPICK],
      ["history", "#history", ""],
      ["alerts", "#alerts", ""],
      ["admin", "#admin", ""],
      ["search", "#discover", KEY],
      ["about", "#discover", `document.querySelector('.workspace').click()`],
      ["welcome", "#discover", ""],
    ]
  : [
      ["today", "#/", ""],
      ["game", `#/game/${GAME}`, ""],
      ["leg", `#/leg/${GAME}/${kp.player_id}/${kp.stat}`, ""],
      ["parlay", "#/parlay", ""],
      ["swipe", `#/swipe?date=${NIGHT}`, ""],
      ["picks", "#/picks", ""],
      ["settings", "#/prefs", ""],
      ["signals", "#/signals", ""],
      ["hedge", "#/hedge", ""],
      ["hedgelab", "#/hedge", FIRSTPICK],
      ["history", "#/history", ""],
      ["alerts", "#/alerts", ""],
      ["admin", "#/admin", ""],
      ["search", "#/", KEY],
      ["about", "#/", `document.querySelector('.workspace').click()`],
      ["welcome", "#/", ""],
    ];

const ONLY = process.env.ONLY ? new Set(process.env.ONLY.split(",")) : null;
if (ONLY) for (let i = SCREENS.length - 1; i >= 0; i--) if (!ONLY.has(SCREENS[i][0])) SCREENS.splice(i, 1);
const sizes = [["m", 390, 844], ["d", 1440, 900]];
const audit = [];
async function idle(max = 20000) {
  const t0 = Date.now();
  await sleep(500);
  while (Date.now() - t0 < max) {
    const busy = await ev(`!!document.querySelector('.skeleton, [aria-busy="true"]') || !document.querySelector('main h1, main h2')`);
    if (!busy) break;
    await sleep(250);
  }
  await sleep(400);
}
async function shoot(name, w, h, theme, mode, hash, action, full) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
  await send("Page.navigate", { url: base });
  await sleep(900);
  await ev(`localStorage.clear(); localStorage.setItem('ph.theme', '${theme}'); localStorage.setItem('ph.mode', '${mode}'); localStorage.setItem('ph.welcomed', '${name === "welcome" ? "" : "1"}'); localStorage.setItem('ph.name', 'Devin'); localStorage.setItem('ph.ledger.v1', ${JSON.stringify(JSON.stringify(ledger))}); 0`);
  await send("Page.navigate", { url: base + hash });
  await sleep(250);
  await send("Page.reload");
  await idle();
  if (action) { await ev(`(async()=>{ ${action} })()`); await idle(); }
  const a = await ev(`(${auditSrc})()`);
  audit.push({ name: `${name}-${w}-${theme}-${mode}`, ...a });
  const metrics = await send("Page.getLayoutMetrics");
  const ch = Math.min(Math.ceil(metrics.result.cssContentSize.height), 5200);
  const clip = full ? { x: 0, y: 0, width: w, height: ch, scale: 1 } : undefined;
  const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: !!full, ...(clip ? { clip } : {}) });
  writeFileSync(join(outDir, `${kind}_${name}_${w}_${theme}_${mode}.png`), Buffer.from(r.result.data, "base64"));
}

for (const [, w, h] of sizes) {
  for (const theme of ["dark", "light"]) {
    for (const [name, hash, action] of SCREENS) {
      await shoot(name, w, h, theme, "simple", hash, action, true);
    }
    // Analyst on the screens whose depth changes
    for (const [name, hash, action] of SCREENS.filter((s) => ["today", "game", "leg", "signals", "history"].includes(s[0]))) {
      await shoot(name, w, h, theme, "analyst", hash, action, true);
    }
  }
}
writeFileSync(join(outDir, `${kind}_audit.json`), JSON.stringify(audit, null, 1));
const bad = audit.filter((a) => a.small.length || a.lowContrast.length || a.overflowX);
console.log(`${audit.length} screens audited; ${bad.length} with findings`);
for (const a of bad.slice(0, 30)) {
  console.log(`- ${a.name}: pokes=${(a.pokes||[]).join(";")} overflowX=${a.overflowX} small=${a.small.length} lowContrast=${a.lowContrast.length}`);
  for (const s of a.small.slice(0, 4)) console.log(`    small ${s.px}px "${s.text}" (${s.sel})`);
  for (const s of a.lowContrast.slice(0, 5)) console.log(`    contrast ${s.ratio} "${s.text}" (${s.sel}) fg=${s.fg} bg=${s.bg}`);
}
ws.close();
chrome.kill();
