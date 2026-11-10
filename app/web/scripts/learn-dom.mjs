// DOM test: the Learn lens shows no price, edge, fee or book quote on any route.
//
//   node scripts/learn-dom.mjs <web url> <chrome devtools port>
//
// Runs in headless Chrome over CDP against a dev server whose API serves the committed fixtures (so a tickets file for
// 2024-01-01 exists and Swipe has a deck): `uv run uvicorn ... --port 8021` and `API_ORIGIN=http://127.0.0.1:8021 npx vite
// --port 5181`, then `chrome --headless=new --remote-debugging-port=9660`. It opens every <details>, clicks through card
// detail and the guess game, then greps all visible text and every aria-label / title / alt for price words. Exit code 1 on
// any hit. There is no DOM library in this project (no new dependencies), so this is a script, not a vitest test; it is
// not part of `npm run check` because it needs Chrome and two servers. Explore is checked too: only the opt-in price block
// may hold price words there.
const [base, port] = process.argv.slice(2);
if (!base || !port) throw new Error("usage: learn-dom.mjs <web url> <devtools port>");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const GAME = "0022400252";
const PLAYER = "1628378";
const NIGHT = "2024-11-20";

const RE = [
  /(?<![\w])[+\-−]\d{3,4}\b/, // -110, +150
  /\d\s?¢/, // 55c as a cent sign
  /\$\s?\d/,
  /\bcents? per\b/i,
  /\b(EV|vig|vigorish|odds|payout|moneyline|implied|break-even)\b/i,
  /\bedges?\b/i,
  /\bfees?\b/i,
  /\bprices?\b/i,
  /\b(DraftKings|FanDuel|BetMGM|Caesars|ESPN ?BET|Fanatics|Kalshi|Polymarket|Novig)\b/i,
];

const tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const page = tabs.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0;
const pend = {};
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pend[m.id]) {
    pend[m.id](m);
    delete pend[m.id];
  }
};
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (js) => {
  const r = await send("Runtime.evaluate", { expression: js, awaitPromise: true, returnByValue: true });
  return r.result?.result?.value ?? r.result?.exceptionDetails?.exception?.description;
};
await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 375, height: 812, deviceScaleFactor: 1, mobile: true });

const click = (sel, text) => `(()=>{const els=[...document.querySelectorAll(${JSON.stringify(sel)})]; const e=${text ? `els.find(x=>x.textContent.includes(${JSON.stringify(text)}))` : "els[0]"}; if(e) e.click();})()`;
const wait = (ms) => `await new Promise(r=>setTimeout(r,${ms}))`;
const OPEN_ALL = `document.querySelectorAll('details').forEach(d=>d.open=true)`;
const ledger = JSON.stringify(
  [0, 1, 2].map((i) => ({
    id: `${GAME}:${PLAYER}:pts:${i}`, prop_id: `${GAME}:${PLAYER}:pts`, slate_date: NIGHT, game_id: GAME, player: "Donovan Mitchell",
    team: "CLE", opponent: "NOP", stat: "pts", line: 25.5, over_price: -110, under_price: -120, tip_utc: "2024-11-21T00:30:00Z",
    side: i % 2 ? "under" : "over", made_at: `2024-11-20T18:00:0${i}.000Z`, slate_clock: "2024-11-20T18:00:00.000Z", response_ms: 1500,
    saw_model: false, source: "swipe",
  })),
);

// name, hash, lens, setup actions (async js), extra localStorage
const ROUTES = [
  ["today", "#/", ""],
  ["today-night", `#/?date=${NIGHT}`, ""],
  ["game", `#/game/${GAME}`, `${wait(700)}; ${click("details.player summary")}; ${click("details.player details.alt summary")}`],
  ["leg", `#/leg/${GAME}/${PLAYER}/pts`, `${wait(900)}`],
  ["swipe", `#/swipe?date=${NIGHT}`, `${wait(900)}; ${click("details.card-detail summary")}; ${wait(900)}`],
  ["picks", "#/picks", `${wait(900)}`],
  ["parlay-with-tickets", "#/parlay?date=2024-01-01", `${wait(900)}`],
  ["signals-note", "#/signals", ""],
  ["hedge-note", "#/hedge", ""],
  ["history-note", "#/history", ""],
  ["record-replay", `#/record?date=${NIGHT}`, `${wait(900)}; ${click(".rp-toggle")}; ${wait(900)}; ${click(".rp-reveal")}; ${wait(500)}`],
  ["record-calibration", "#/record?tab=calibration", `${wait(900)}`],
  ["record-guess", "#/record?tab=guess", `${wait(900)}; ${click(".gs-actions .btn.over")}; ${wait(900)}`],
];

async function crawl(lens, name, hash, action) {
  await send("Page.navigate", { url: base });
  await sleep(700);
  await ev(`localStorage.clear(); localStorage.setItem('ph.welcomed','1'); localStorage.setItem('ph.tour','1'); localStorage.setItem('ph.name','T'); localStorage.setItem('ph.lens','${lens}'); localStorage.setItem('ph.ledger.v1', ${JSON.stringify(ledger)}); 0`);
  await send("Page.navigate", { url: base + hash });
  await sleep(250);
  await send("Page.reload");
  await sleep(1200);
  if (action) await ev(`(async()=>{ ${action} })()`);
  await ev(`(async()=>{ ${OPEN_ALL}; ${wait(300)}; ${OPEN_ALL}; })()`);
  await sleep(500);
  const text = await ev(`(()=>{
    const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); const parts=[]; while(w.nextNode()) parts.push(w.currentNode.nodeValue);
    const out=[parts.join(' ')];
    document.querySelectorAll('[aria-label],[title],[alt],[aria-valuetext]').forEach(e=>{for(const a of ['aria-label','title','alt','aria-valuetext']){const v=e.getAttribute(a); if(v) out.push(v)}});
    return out.join('\\n');
  })()`);
  const clean = String(text).replace(/1-800-GAMBLER/g, "").replace(/\s+/g, " ");
  const hits = [];
  for (const re of RE) {
    const g = new RegExp(re.source, re.flags.includes("i") ? "gi" : "g");
    for (const m of clean.matchAll(g)) hits.push(`${m[0]} ... ${clean.slice(Math.max(0, m.index - 40), m.index + 50)}`);
  }
  return { lens, name, hits, chars: clean.length };
}

let bad = 0;
const rows = [];
for (const [name, hash, action] of ROUTES) rows.push(await crawl("learn", name, hash, action));
// Explore: game and leg only (the opt-in price block is the one allowed place), others as Learn.
for (const [name, hash, action] of ROUTES.filter((r) => ["today", "game", "leg"].includes(r[0]))) rows.push(await crawl("explore", name, hash, action));
for (const r of rows) {
  const learn = r.lens === "learn";
  const status = r.chars < 200 ? "EMPTY?" : r.hits.length === 0 ? "ok" : learn ? "FAIL" : "hits (explore, informational)";
  console.log(`${r.lens.padEnd(8)} ${r.name.padEnd(22)} ${String(r.chars).padStart(6)} chars  ${status}`);
  for (const h of r.hits.slice(0, 8)) console.log(`      ${h}`);
  if (learn && (r.hits.length > 0 || r.chars < 200)) bad++;
}
console.log(bad === 0 ? "\nLearn lens: no price words on any route." : `\nLearn lens: ${bad} route(s) failed.`);
ws.close();
process.exit(bad === 0 ? 0 : 1);
