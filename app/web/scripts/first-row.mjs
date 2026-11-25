// The first real row (first game, first player line, first leg) is on the first screen at 375x812, with the app's own banners.
// A developer check (headless Chrome over CDP) against a running dev server or preview; not part of `npm run check`.
//
//   node scripts/first-row.mjs http://127.0.0.1:5231
//
// Exit code 1 when a screen's first row starts lower than the tab bar leaves room for (or does not appear).
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = process.argv[2];
if (!base) throw new Error("usage: first-row.mjs <url>");
const W = 375;
const H = 812;
const TABS = 56; // the bottom tab bar
const NEED = 60; // the top 60px of the row must be on the first screen, above the tab bar
const CHECKS = [
  ["teams", "#/teams", ".teams-game .game"],
  ["props", "#/props", ".prop-row"],
  ["slip", "#/slip", ".cb-row-top"],
  ["explore", "#/combo", ".cb-trow, .cb-script"],
];
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9100 + Math.floor(Math.random() * 300);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const chrome = spawn(CHROME, ["--headless=new", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${PORT}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "firstrow-"))}`, "about:blank"], { stdio: "ignore" });
let tabs;
for (let i = 0; i < 60; i++) {
  try {
    tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
    if (tabs.find((t) => t.type === "page")) break;
  } catch { /* chrome not up yet */ }
  await sleep(200);
}
const ws = new WebSocket(tabs.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0;
const pend = {};
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; }
};
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pend[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
const ev = async (js) => (await send("Runtime.evaluate", { expression: js, awaitPromise: true, returnByValue: true })).result?.result?.value;
await send("Page.enable");
let bad = 0;
for (const theme of ["dark", "light"]) {
  for (const [name, hash, sel] of CHECKS) {
    await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: true });
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] });
    await send("Page.navigate", { url: base });
    await sleep(800);
    await ev(`localStorage.clear(); localStorage.setItem('ph.welcomed','1'); localStorage.setItem('ph.tour','1'); localStorage.setItem('ph.name','T'); 0`);
    await send("Page.navigate", { url: base + hash });
    await sleep(250);
    await send("Page.reload");
    let top = null;
    for (let i = 0; i < 60 && top === null; i++) {
      await sleep(250);
      top = await ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); return e ? Math.round(e.getBoundingClientRect().top) : null; })()`);
    }
    const ok = top !== null && top + NEED <= H - TABS;
    if (!ok) bad++;
    console.log(`${ok ? "ok  " : "FAIL"} ${name.padEnd(8)} ${theme.padEnd(5)} first row top ${top === null ? "not found" : `${top}px`} (limit ${H - TABS - NEED}px)`);
  }
}
ws.close();
chrome.kill();
process.exit(bad === 0 ? 0 : 1);
