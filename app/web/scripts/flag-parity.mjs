// The betting switch's parity check, on real builds. It builds the app four times into a fresh temp directory (never
// dist/ or dist-artifact/): the regular build and the single-file artifact, each with the switch off and on. Then:
//
//   OFF: no file of either build contains any string in src/betting/markers.json (the module was dropped whole);
//   ON:  each build contains every one of them (so the OFF result is not an accident of a broken marker list).
//
// It prints the sizes (js, css, artifact bytes) of each build and exits non-zero on any violation. The artifact half is
// skipped (and says so) when artifact-data/baked.json is absent, as app/scripts/check.sh does for the artifact step.
//
//   node scripts/flag-parity.mjs
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;
const SWITCH = "VITE_PH_BETTING"; // the build-time switch src/features.ts reads
const { markers } = JSON.parse(readFileSync(join(root, "src", "betting", "markers.json"), "utf8"));
if (!Array.isArray(markers) || markers.length === 0 || !markers.every((m) => typeof m === "string" && m.length > 0)) {
  throw new Error("src/betting/markers.json has no usable markers");
}
const haveBaked = existsSync(join(root, "artifact-data", "baked.json"));
const work = mkdtempSync(join(tmpdir(), "ph-flag-parity-"));

/** Every file under a directory, recursively. */
function files(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? files(join(dir, e.name)) : [join(dir, e.name)]));
}

function build(state) {
  const env = { ...process.env };
  delete env[SWITCH];
  if (state === "on") env[SWITCH] = "1";
  const web = join(work, state, "web");
  execFileSync("npx", ["vite", "build", "--outDir", web, "--emptyOutDir", "--logLevel", "warn"], { cwd: root, env, stdio: "inherit" });
  const webFiles = files(web).filter((f) => /\.(js|css|html)$/.test(f));
  // Sizes are of the bundle itself (assets/), as the budgets count it; the marker scan covers every file.
  const size = (re) => webFiles.filter((f) => f.includes("/assets/") && re.test(f)).reduce((n, f) => n + statSync(f).size, 0);
  const out = { state, sizes: { js: size(/\.js$/), css: size(/\.css$/), artifact: null }, groups: [["regular build", webFiles]] };
  if (haveBaked) {
    const art = join(work, state, "artifact");
    execFileSync("node", ["scripts/build-artifact.mjs", "--out", art], { cwd: root, env, stdio: "inherit" });
    const page = join(art, "parlayhitters.html");
    out.sizes.artifact = statSync(page).size;
    out.groups.push(["artifact", [page]]);
  }
  return out;
}

const problems = [];
const results = [];
try {
  for (const state of ["off", "on"]) {
    const r = build(state);
    results.push(r);
    for (const [what, list] of r.groups) {
      const text = list.map((f) => readFileSync(f, "utf8")).join("\n");
      for (const m of markers) {
        const found = text.includes(m);
        if (state === "off" && found) problems.push(`OFF ${what} contains the betting marker ${JSON.stringify(m)}`);
        if (state === "on" && !found) problems.push(`ON ${what} lacks the betting marker ${JSON.stringify(m)}`);
      }
    }
  }
} finally {
  rmSync(work, { recursive: true, force: true });
}

console.log("\nflag parity: sizes in bytes");
for (const r of results) {
  const a = r.sizes.artifact === null ? "skipped (no artifact-data/baked.json)" : r.sizes.artifact.toString();
  console.log(`  ${r.state.padEnd(3)}  js ${r.sizes.js}  css ${r.sizes.css}  artifact ${a}`);
}
if (!haveBaked) console.log("  note: the artifact half was SKIPPED: artifact-data/baked.json is absent");
console.log(`  markers checked: ${markers.length}`);
if (problems.length > 0) {
  console.error("flag parity FAILED:\n - " + problems.join("\n - "));
  process.exit(1);
}
console.log("flag parity passed: the OFF builds carry no betting marker; the ON builds carry every one");
