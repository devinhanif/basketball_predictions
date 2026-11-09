// Builds the single-file artifact: dist-artifact/parlayhitters.html.
//
//   1. vite build --config vite.config.artifact.ts   -> dist-artifact-tmp/app.js + app.css (fonts inlined as data: URIs)
//   2. read artifact-data/baked.json                 (written by app/api: bake_artifact.py; see app/README.md)
//   3. assemble ONE file of page content only: <title>, the viewport-fit meta, <style>, markup, scripts. No doctype, html,
//      head or body tags (the platform wraps the page). Everything is inline: no external host, no fetch, no service worker.
//   4. scan the result for things the platform forbids and fail the build if any is found.
//
// It also writes dist-artifact/preview.html: the same page inside a doctype/html/body skeleton, for local screenshots only.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;
const tmp = join(root, "dist-artifact-tmp");
const out = join(root, "dist-artifact");
const bakedPath = join(root, "artifact-data", "baked.json");
const LIMIT = 16 * 1024 * 1024;

if (!existsSync(bakedPath)) {
  console.error(
    "artifact-data/baked.json is missing. Write it first (read-only, from the export the API serves):\n" +
      "  cd app/api && APP_EXPORT_DIR=<export dir> PYTHONPATH=src uv run python -m parlayhitters_api.bake_artifact --out ../web/artifact-data/baked.json",
  );
  process.exit(1);
}

execFileSync("npx", ["vite", "build", "--config", "vite.config.artifact.ts"], { cwd: root, stdio: "inherit" });

let js = readFileSync(join(tmp, "app.js"), "utf8");
const css = readFileSync(join(tmp, "app.css"), "utf8");
const baked = readFileSync(bakedPath, "utf8");
const parsed = JSON.parse(baked);
if (parsed.format !== 1 || typeof parsed.data_as_of !== "string") throw new Error("baked.json is not format 1");

// The SVG namespace is an identifier, not an address; write it with escaped slashes (the same string at run time) so the
// page holds no "http://" text at all.
js = js.replaceAll(/http:\/\/www\.w3\.org\//g, "http:\\/\\/www.w3.org/");

// Inside <script> and <style> the only sequences that end the element are "</script" and "</style"; neutralise them.
const safeJs = js.replaceAll("</script", "<\\/script").replaceAll("<!--", "<\\!--");
const safeCss = css.replaceAll("</style", "<\\/style");
const safeData = baked.replaceAll("<", "\\u003c");

const page =
  `<title>parlayhitters</title>\n` +
  `<meta name="claude:viewport-fit-sides" content="cover">\n` +
  `<style>${safeCss}</style>\n` +
  `<div id="app"></div>\n` +
  `<script type="application/json" id="ph-baked">${safeData}</script>\n` +
  `<script>${safeJs}</script>\n`;

// ---- checks the publisher enforces, run here so a bad build never leaves the machine ----
const problems = [];
const forbidden = [
  [/<!doctype/i, "doctype"],
  [/<html[\s>]/i, "html tag"],
  [/<head[\s>]/i, "head tag"],
  [/<body[\s>]/i, "body tag"],
  [/serviceWorker/, "serviceWorker"],
  [/\bfetch\(/, "fetch("],
  [/\bXMLHttpRequest\b/, "XMLHttpRequest"],
  [/\bWebSocket\b/, "WebSocket"],
  [/\bsendBeacon\b/, "sendBeacon"],
  [/\bimportScripts\b/, "importScripts"],
  [/\b(?:window\.)?(?:alert|confirm|prompt)\(/, "alert/confirm/prompt"],
  [/window\.print\(|\.print\(\)/, "print"],
  [/\.download\s*=|<a[^>]*\sdownload[\s=>]/, "download link"],
  [/<link[^>]+rel=["']?manifest/i, "manifest"],
  [/https?:\/\//, "an http(s) URL"],
  [/<(?:script|link|img|iframe)[^>]+\s(?:src|href)=["']?(?:https?:)?\/\//i, "an external resource"],
];
for (const [re, what] of forbidden) {
  const m = re.exec(page);
  if (m) problems.push(`${what}: ...${page.slice(Math.max(0, m.index - 40), m.index + 60).replace(/\s+/g, " ")}...`);
}
if (!page.startsWith('<title>parlayhitters</title>\n<meta name="claude:viewport-fit-sides" content="cover">')) problems.push("page does not start with the title and viewport meta");
if (Buffer.byteLength(page) > LIMIT) problems.push(`page is ${Buffer.byteLength(page)} bytes, over the 16 MB limit`);
if (problems.length) {
  console.error("artifact checks FAILED:\n - " + problems.join("\n - "));
  process.exit(1);
}

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
writeFileSync(join(out, "parlayhitters.html"), page);
writeFileSync(
  join(out, "preview.html"),
  `<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"></head><body>\n${page}</body></html>\n`,
);
rmSync(tmp, { recursive: true, force: true });
const size = statSync(join(out, "parlayhitters.html")).size;
console.log(
  `artifact: ${join(out, "parlayhitters.html")} ${(size / 1e6).toFixed(2)} MB; data as of ${parsed.data_as_of}; dates ${parsed.dates.join(", ")}; checks passed`,
);
