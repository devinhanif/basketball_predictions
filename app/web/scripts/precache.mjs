// After `vite build`: list every file in dist/ for the service worker and version the list by content.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";

const dist = new URL("../dist", import.meta.url).pathname;
const skip = new Set(["sw.js", "precache.json"]);

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const files = walk(dist)
  .map((p) => relative(dist, p))
  .filter((p) => !skip.has(p))
  .sort();
const hash = createHash("sha256");
for (const f of files) hash.update(f).update(readFileSync(join(dist, f)));
const urls = ["/", ...files.map((f) => "/" + f)];
writeFileSync(
  join(dist, "precache.json"),
  JSON.stringify({ version: hash.digest("hex").slice(0, 12), urls }, null, 1),
);
console.log(`precache: ${urls.length} urls`);
