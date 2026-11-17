// The betting tools' end-to-end journey in headless Chrome over CDP (a developer tool; nothing here ships). It needs a
// web server built with the betting switch on, proxying an API with the switch on (see app/README.md).
//
//   node scripts/bet-journey.mjs http://127.0.0.1:5230/ <outdir>        # widths 375 and 1280, dark and light
//
// Journey: open a real player from the newest night that has stored quotes -> "Add to slip" on the player page -> the
// slip page -> Compare -> type two venue prices (in the dialog) -> add both -> "Mark slip as placed" (cancel once: focus
// returns; then confirm: focus lands on the Hedge Finder link) -> the Hedge Finder shows that position -> reload -> the
// slip still holds it -> save the slip as an idea with a thesis and two tags -> refresh the stored prices -> the idea's
// saved prices are unchanged -> open the idea -> record outcomes and settle -> the labelled paper net -> load a copy into
// the slip (cancel once, then confirm) -> export the ideas -> import the file into a fresh browser scope -> an invalid
// file is refused and nothing changes -> prop analysis. On every step: no uncaught exception, no page-level horizontal
// scroll, and on every priced screen the money-safety note; every dialog takes focus and gives it back. Exits non-zero
// on the first failed assertion and prints what failed.
import { spawn } from "node:child_process";
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [base, outDir] = process.argv.slice(2);
if (!base || !outDir) throw new Error("usage: bet-journey.mjs <base url> <outdir>");
mkdirSync(outDir, { recursive: true });
const NOTE = "Analysis only, not advice: no orders are placed.";
const REMOVE_NOTE = "Removing a recorded position deletes this browser's note only. It does not cancel or change anything at the venue.";
const PAPER = "Paper net: arithmetic on the prices you saved and the outcomes you entered";
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
await send("DOM.enable");

// ---- page helpers (run inside the page)
const H = `
const T = (el) => (el?.textContent ?? "").replace(/\\s+/g, " ").trim();
const byText = (sel, start, root = document) => [...root.querySelectorAll(sel)].find((e) => T(e).startsWith(start) && e.offsetParent !== null);
const fill = (el, v) => { const p = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; const d = Object.getOwnPropertyDescriptor(p, "value"); d.set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); };
const row = (venue) => [...document.querySelectorAll('.ph-bet-board tbody tr')].find((r) => T(r.querySelector('.ph-bet-b-name')) === venue);
const ideasIn = () => { const k = Object.keys(localStorage).find((x) => x.startsWith('ph.betting.workspace')); if (!k) return null; const find = (o) => { if (o && typeof o === 'object') { if (Array.isArray(o.ideas) && o.draft) return o.ideas; for (const v of Object.values(o)) { const r = find(v); if (r) return r; } } return null; }; return find(JSON.parse(localStorage.getItem(k))); };
`;
async function until(js, what, ms = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await ev(`${H} return !!(${js});`)) return;
    await sleep(200);
  }
  const seen = await ev(`return (document.querySelector('main')?.innerText ?? document.body.innerText).replace(/\\s+/g, ' ').slice(0, 900);`).catch(() => "");
  throw new Error(`timed out waiting for: ${what} (page shows: ${seen})`);
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
const inDialog = async (what) => {
  await until(`document.querySelector('[role=dialog][aria-modal=true]')`, what);
  await sleep(150);
  if (!(await ev(`return !!document.activeElement?.closest('[role=dialog]');`))) throw new Error(`focus did not move into the dialog: ${what}`);
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

  // 3. Compare: type two venue prices (each in the dialog) and add both from their rows
  await until(`document.querySelector('.ph-bet-board tbody tr')`, "the venue board");
  await check("compare", w, theme, true);
  for (const [venue, price, fee] of [["Kalshi", "48", "1"], ["DraftKings", "-110", "0"]]) {
    await until(`row(${JSON.stringify(venue)})`, `the ${venue} row`);
    await ev(`${H} const b = [...row(${JSON.stringify(venue)}).querySelectorAll('button')].find((x) => x.getAttribute('aria-label') === 'Type a ${venue} price'); b.focus(); b.click(); return 0;`);
    await inDialog(`the ${venue} price dialog`);
    await ev(`${H} const ins = document.querySelectorAll('[role=dialog] form input'); fill(ins[0], ${JSON.stringify(price)}); fill(ins[1], ${JSON.stringify(fee)}); return 0;`);
    await sleep(250);
    await ev(`document.querySelector('[role=dialog] form button[type=submit]').click(); return 0;`);
    await until(`!document.querySelector('[role=dialog]') && T(row(${JSON.stringify(venue)})).includes('manual')`, `${venue}'s typed price on its row`);
    await sleep(200);
    if (!(await ev(`${H} return !!document.activeElement?.closest('tr') && document.activeElement.closest('tr') === row(${JSON.stringify(venue)});`))) throw new Error(`focus did not land on the ${venue} row after typing a price`);
    await ev(`${H} [...row(${JSON.stringify(venue)}).querySelectorAll('button')].find((x) => x.getAttribute('aria-label') === 'Add ${venue} to slip').click(); return 0;`);
    await until(`T(row(${JSON.stringify(venue)})).includes("In your slip at")`, `${venue} added`);
  }
  console.log("  ok  typed prices in dialogs; focus back on each row");
  await check("compare-typed", w, theme, true);

  // 3b. select / deselect: leave a planned leg out and the total moves; switch it back on and the total returns
  await go("#/bet/slip");
  await until(`document.querySelector('main .ph-bet-leg input[type=checkbox]')`, "the include boxes on the slip");
  const total = `(() => { const dt = [...document.querySelectorAll('main dt')].find((e) => T(e) === 'Total cost'); return dt ? T(dt.nextElementSibling) : null; })()`;
  const total0 = await ev(`${H} return ${total};`);
  if (!total0 || total0 === "not complete") throw new Error(`the slip total is not a figure before deselecting: ${total0}`);
  await ev(`document.querySelector('main .ph-bet-leg input[type=checkbox]').click(); return 0;`);
  await until(`document.querySelector('main .ph-bet-leg.off') && ${total} !== ${JSON.stringify(total0)}`, "the total to change after leaving a leg out");
  const total1 = await ev(`${H} return ${total};`);
  if (!(await ev(`return document.querySelector('main .ph-bet-leg.off').innerText.includes('Left out');`))) throw new Error("a left-out leg does not say so");
  await check("slip-deselected", w, theme, true);
  await ev(`document.querySelector('main .ph-bet-leg.off input[type=checkbox]').click(); return 0;`);
  await until(`!document.querySelector('main .ph-bet-leg.off') && ${total} === ${JSON.stringify(total0)}`, "the total to return after switching the leg back on");
  console.log(`  ok  deselect moved the total ${total0} -> ${total1}; reselect restored ${total0}`);

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
  await until(`document.querySelector('.ph-bet-board tbody tr.priced, .ph-bet-board tbody tr.unavailable')`, "the hedge venue board");
  await check("hedge", w, theme, true);

  // 6. reload: the slip is still there
  await send("Page.reload");
  await sleep(1500);
  await go("#/bet/slip");
  await until(`document.body.innerText.includes("Recorded positions") && document.body.innerText.includes(${JSON.stringify(PROP.name)})`, "the slip after reload");
  const n = await ev(`return document.querySelectorAll('main .ph-bet-leg.recorded').length;`);
  if (n !== 2) throw new Error(`expected 2 recorded legs after reload, saw ${n}`);
  await check("slip-reloaded", w, theme, true);
  // a recorded position has no include box: it always counts
  if (await ev(`return document.querySelectorAll('main .ph-bet-leg.recorded input[type=checkbox]').length;`)) throw new Error("a recorded position offers an include box");
  if (!(await ev(`return [...document.querySelectorAll('main .ph-bet-leg.recorded')].every((l) => l.innerText.includes('Recorded'));`))) throw new Error("a recorded position is not labelled Recorded");
  console.log("  ok  recorded positions cannot be switched off");
  await ideasJourney(w, theme);
}

// ---- ideas: save, refresh, open, outcomes, load a copy, export, import into a fresh scope, refuse a bad file
async function ideasJourney(w, theme) {
  await ev(`${H} const d = document.querySelector('main .ph-bet-notes'); d.open = true; const i = d.querySelectorAll('input, textarea'); fill(i[0], 'Journey idea'); fill(i[1], 'Two venues on one line; checking how each settles.'); fill(i[2], 'hedge, minutes'); return 0;`);
  await sleep(500);
  await click("main button", "Save as an idea", "Save as an idea");
  await until(`byText('main a', 'Open the idea')`, "the saved-idea link");
  const saved = await ev(`${H} return JSON.stringify(ideasIn());`);
  const ideas = JSON.parse(saved);
  if (ideas.length !== 1 || ideas[0].title !== "Journey idea" || ideas[0].tags.join(",") !== "hedge,minutes") throw new Error(`the idea was not saved as typed: ${saved.slice(0, 200)}`);
  console.log("  ok  saved as an idea with a thesis and two tags");

  // refresh stored prices in Compare: the idea's saved prices do not move
  await go(`#/bet/compare?game=${PROP.game}&player=${PROP.player}&stat=${PROP.stat}&side=over`);
  await click("main button", "Refresh stored prices", "Refresh stored prices");
  await until(`document.querySelector('.ph-bet-board tbody tr')`, "the board after refresh");
  await sleep(800);
  if ((await ev(`${H} return JSON.stringify(ideasIn());`)) !== saved) throw new Error("a quote refresh changed the saved idea");
  console.log("  ok  refresh left the idea's saved prices unchanged");

  // the list, then the idea
  await go("#/bet/ideas");
  await until(`document.querySelector('.ph-bet-idea')`, "the ideas list");
  await check("ideas", w, theme, true);
  await click("main .ph-bet-idea h2 a", "Journey idea", "the idea link");
  await until(`document.querySelector('main h1') && T(document.querySelector('main h1')) === 'Journey idea'`, "the idea page");
  const before = await ev(`${H} return [...document.querySelectorAll('.ph-bet-idea-legs tbody .ph-bet-b-price')].map(T).join('|');`);
  await check("idea", w, theme, true);

  // record outcomes: every market "Over ... hit", then settle; the net is labelled as arithmetic on saved prices
  await ev(`${H} for (const fs of document.querySelectorAll('.ph-bet-markset')) fs.querySelectorAll('input[type=radio]')[0].click(); return 0;`);
  await sleep(300);
  await click("main button", "Settle with these outcomes", "Settle");
  await until(`document.body.innerText.includes(${JSON.stringify(PAPER)}) && document.body.innerText.includes('Your recorded outcomes')`, "the labelled paper net");
  const after = await ev(`${H} return [...document.querySelectorAll('.ph-bet-idea-legs tbody .ph-bet-b-price')].map(T).join('|');`);
  if (after !== before) throw new Error(`the idea's saved prices changed on screen: ${before} -> ${after}`);
  console.log("  ok  outcomes recorded; paper net shown with its label");
  await until(`document.body.innerText.includes('History of this record')`, "the history of this record");
  if (!(await ev(`return document.querySelector('.ph-bet-hist-list')?.innerText.includes('Recorded') && document.querySelector('.ph-bet-history').innerText.includes('typed by hand');`))) throw new Error("the history list does not show the recorded entry labelled typed by hand");
  console.log("  ok  history of this record lists the entry, labelled typed by hand");
  await check("idea-settled", w, theme, true);

  // load a copy into the slip: cancel once (focus returns), then confirm
  await click("main button", "Load a copy into the slip", "Load a copy");
  await inDialog("the load-a-copy dialog");
  await click("[role=dialog] button", "Cancel", "Cancel");
  if ((await ev(`${H} return T(document.activeElement);`)) !== "Load a copy into the slip") throw new Error("focus did not return to Load a copy after Cancel");
  await click("main button", "Load a copy into the slip", "Load a copy");
  await click("[role=dialog] button", "Replace and open the slip", "confirm load");
  await until(`location.hash === '#/bet/slip' && document.querySelector('main .ph-bet-notes input')?.value === 'Journey idea'`, "the slip with the loaded copy");
  console.log("  ok  a copy loaded into the slip (cancel returned focus)");
  await check("slip-loaded", w, theme, true);

  // remove a recorded position: the wording says it is the note only, and nothing is removed until it is confirmed
  const recCount = () => ev(`return document.querySelectorAll('main .ph-bet-leg.recorded').length;`);
  const rec0 = await recCount();
  if (rec0 < 1) throw new Error("the loaded copy holds no recorded position to remove");
  await ev(`const b = document.querySelector('main .ph-bet-leg.recorded .ph-bet-x'); b.focus(); b.click(); return 0;`);
  await until(`document.querySelector('main .ph-bet-confirm') && document.querySelector('main .ph-bet-confirm').innerText.includes(${JSON.stringify(REMOVE_NOTE)})`, "the remove-the-record wording");
  if ((await recCount()) !== rec0) throw new Error("a recorded position was removed before it was confirmed");
  await check("remove-record", w, theme, true);
  await click("main .ph-bet-confirm button", "Remove the note", "Remove the note");
  await until(`document.querySelectorAll('main .ph-bet-leg.recorded').length === ${rec0 - 1}`, "the recorded position to go");
  console.log("  ok  removing a recorded position shows its wording first and removes the note only after confirming");
  await longContent(w, theme);

  // export every idea (a real browser download)
  const dl = join(outDir, `dl_${w}_${theme}`);
  mkdirSync(dl, { recursive: true });
  await send("Page.setDownloadBehavior", { behavior: "allow", downloadPath: dl });
  await go("#/bet/ideas");
  await click("main button", "Export all ideas", "Export all ideas");
  let file = null;
  for (let i = 0; i < 50 && !file; i++) {
    const f = readdirSync(dl).find((x) => x.endsWith(".json"));
    if (f) file = join(dl, f);
    else await sleep(200);
  }
  if (!file) throw new Error("the ideas file was not downloaded");
  const exported = JSON.parse(readFileSync(file, "utf8"));
  if (exported.format !== "parlayhitters-saved-ideas" || exported.ideas.length !== 1) throw new Error("the ideas file is not as expected");
  console.log(`  ok  exported ${file.split("/").pop()}`);

  // a fresh browser scope (storage cleared), then import the file, then a bad file
  await ev(`const keep = ['ph.theme','ph.welcomed','ph.name','ph.lens'].map((k) => [k, localStorage.getItem(k)]); localStorage.clear(); for (const [k, v] of keep) if (v !== null) localStorage.setItem(k, v); return 0;`);
  await send("Page.reload");
  await sleep(1500);
  await go("#/bet/ideas");
  await until(`document.body.innerText.includes('No saved ideas yet')`, "the empty ideas list in a fresh scope");
  const setFile = async (path) => {
    const doc = await send("DOM.getDocument", { depth: 0 });
    const q = await send("DOM.querySelector", { nodeId: doc.result.root.nodeId, selector: "input[data-ph-ideas-import]" });
    await send("DOM.setFileInputFiles", { files: [path], nodeId: q.result.nodeId });
  };
  await setFile(file);
  await until(`document.querySelector('.ph-bet-idea') && T(document.querySelector('.ph-bet-idea h2')) === 'Journey idea'`, "the imported idea");
  const imported = await ev(`${H} return JSON.stringify(ideasIn());`);
  if (JSON.stringify(JSON.parse(imported)[0]) !== JSON.stringify(exported.ideas[0])) throw new Error("the imported idea differs from the exported one");
  console.log("  ok  imported into a fresh scope, identical to the export");
  const bad = join(dl, "bad.json");
  writeFileSync(bad, JSON.stringify({ format: "parlayhitters-saved-ideas", version: 1, ideas: [{ id: "x", title: 5 }] }));
  const storeBefore = await ev(`return JSON.stringify(Object.entries(localStorage).filter(([k]) => k.startsWith('ph.betting')));`);
  await setFile(bad);
  await until(`[...document.querySelectorAll('[role=alert]')].some((a) => a.textContent.includes('Nothing was changed'))`, "the refusal of the bad file");
  if ((await ev(`return JSON.stringify(Object.entries(localStorage).filter(([k]) => k.startsWith('ph.betting')));`)) !== storeBefore) throw new Error("a refused file changed the stored workspace");
  console.log("  ok  a bad file was refused and nothing changed");
  await check("ideas-imported", w, theme, true);

  // prop analysis for the prop
  await go(`#/bet/analysis?game=${PROP.game}&player=${PROP.player}&stat=${PROP.stat}&side=over`);
  await until(`document.querySelector('.ph-bet-facts, .ph-bet-panel-card p')`, "the analysis panels");
  await sleep(800);
  await check("analysis", w, theme, true);
}

// ---- long content: a 40-character player name and a 600-character thesis with long tags, on the slip, an idea and the list
async function longContent(w, theme) {
  const NAME = "Maximilian Bartholomew-Featherstonehaugh";
  const THESIS = Array.from({ length: 12 }, (_, i) => `Sentence ${i + 1} explains the thinking behind this idea in plain words.`).join(" ").slice(0, 600);
  const TAGS = ["a-very-long-tag-with-no-spaces-at-all-so-it-cannot-wrap-naturally", "another-extremely-long-tag-written-as-one-word-1234567890"].map((t) => t.slice(0, 60));
  const original = await ev(`const k = Object.keys(localStorage).find((x) => x.startsWith('ph.betting.workspace')); return JSON.stringify([k, localStorage.getItem(k)]);`);
  await ev(`const k = Object.keys(localStorage).find((x) => x.startsWith('ph.betting.workspace'));
    const env = JSON.parse(localStorage.getItem(k));
    const walk = (o) => { if (Array.isArray(o)) o.forEach(walk); else if (o && typeof o === 'object') { if ('playerName' in o) o.playerName = ${JSON.stringify(NAME)}; for (const v of Object.values(o)) walk(v); } };
    walk(env.document);
    for (const d of [env.document.draft, ...env.document.ideas]) { d.title = ${JSON.stringify(`${NAME} and a long idea title`)}; d.thesis = ${JSON.stringify(THESIS)}; d.tags = ${JSON.stringify(TAGS)}; }
    localStorage.setItem(k, JSON.stringify(env)); return 0;`);
  await send("Page.reload");
  await sleep(1500);
  const clipped = async (label) => {
    const bad = await ev(`const W = window.innerWidth; return [...document.querySelectorAll('main button, main a, main input, main textarea, main summary, .sidepanel button')].filter((e) => e.offsetParent !== null).map((e) => [e, e.getBoundingClientRect()]).filter(([e, r]) => r.width > 0 && (r.right > W + 1 || r.left < -1)).map(([e]) => (e.getAttribute('aria-label') || e.textContent || e.tagName).trim().slice(0, 50));`);
    if (bad.length) throw new Error(`${label}: controls outside the ${w}px viewport: ${bad.join(" | ")}`);
  };
  await go("#/bet/slip");
  await until(`document.body.innerText.includes(${JSON.stringify(NAME)})`, "the long name on the slip");
  await clipped("slip with long content");
  await check("long-slip", w, theme, true);
  await go("#/bet/ideas");
  await until(`document.body.innerText.includes(${JSON.stringify(NAME)})`, "the long name in the ideas list");
  await clipped("ideas list with long content");
  await check("long-ideas", w, theme, true);
  await click("main .ph-bet-idea h2 a", NAME, "the long idea link");
  await until(`document.querySelector('main h1') && document.body.innerText.includes('Sentence 1 explains')`, "the long idea page");
  await clipped("idea page with long content");
  await check("long-idea", w, theme, true);
  console.log("  ok  long name, thesis and tags: no sideways scroll, no control outside the viewport");
  // put the workspace back as it was, so the steps after this one are unchanged
  await ev(`const [k, v] = ${original}; localStorage.setItem(k, v); return 0;`);
  await send("Page.reload");
  await sleep(1500);
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
    if (await ev(`return !!document.querySelector('.ph-bet-add, .ph-bet-launcher, .ph-bet-side, .sidepanel');`)) throw new Error(`${lens}: betting UI on the player page`);
    for (const h of ["#/bet", "#/bet/slip", "#/bet/compare", "#/bet/hedge", "#/bet/ideas", "#/bet/analysis"]) {
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
console.log("JOURNEY PASSED (375 and 1280, dark and light; ideas, export/import, analysis)");
