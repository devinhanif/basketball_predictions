// The betting switch's source-level guarantees (the build-level ones are scripts/flag-parity.mjs):
//   1. the money-safety grep over both betting directories (web and API), with a fixed allow-list;
//   2. only src/features.ts (web) and parlayhitters_api/features.py (API) name the switch's environment variables;
//   3. the router: #/bet... is not-found with the switch off; with it on, only registered screens parse.
// The suite runs with the switch off by default and on under app/scripts/check.sh --betting (which sets it for vitest).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { BETTING, BETTING_ENV_NAME } from "./features";
import { parse } from "./router";

const SRC = fileURLToPath(new URL(".", import.meta.url));
const APP = join(SRC, "..", "..");
const WEB_BETTING = join(SRC, "betting");
const API_SRC = join(APP, "api", "src");
const API_BETTING = join(API_SRC, "parlayhitters_api", "betting");
const WEB_NAME = BETTING_ENV_NAME; // the web build's switch
const API_NAME = BETTING_ENV_NAME.replace(/^VITE_/, ""); // the API's switch (spelled without the literal here)

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (n === "node_modules" || n === "__pycache__" || n === ".venv") return [];
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

/**
 * Phrases that may contain an otherwise-forbidden word. Exactly one: the money-safety sentence. Each is removed (case
 * sensitive, whole phrase) before the forbidden patterns run, so any other use of the word still fails.
 */
export const ALLOWED_PHRASES: readonly string[] = ["no orders are placed"];

/** What no file in a betting directory may contain, with why. */
export const FORBIDDEN: readonly (readonly [RegExp, string])[] = [
  [/authorization/i, "an Authorization header"],
  [/api[_-]?key/i, "an API key"],
  [/secret/i, "a secret"],
  [/place_order/i, "place_order"],
  [/execute/i, "execute"],
  [/\btokens?\b/i, "the word token"],
  [/\borders?\b/i, "the word order"],
  // fetch only to a same-origin relative path: fetch("/api/...") or fetch(`/api/...`), never "//host" or a variable.
  [/\bfetch\s*\(\s*(?!["'`]\/(?!\/))/, "a fetch to anything but a same-origin relative path"],
  [/https?:\/\/|wss?:\/\//i, "an absolute URL"],
  [/\b(?:XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b/, "another network channel"],
  [/^\s*(?:import|from)\s+(?:requests|httpx|aiohttp|urllib3?|socket|http\.client)\b/m, "a Python network client"],
  // A FastAPI route that is not a GET (a decorator, or add_api_route with methods): the API stays read-only.
  [/@\w+\.(?:post|put|patch|delete|api_route|websocket)\s*\(|add_api_route|methods\s*=/, "a write route"],
];

/** The violations in one file's text. */
export function violations(text: string): string[] {
  let t = text;
  for (const p of ALLOWED_PHRASES) t = t.split(p).join(" ");
  return FORBIDDEN.filter(([re]) => re.test(t)).map(([, why]) => why);
}

describe("money-safety grep over the betting directories", () => {
  const files = [...walk(WEB_BETTING), ...walk(API_BETTING)];

  it("finds the files it is meant to scan (web and API)", () => {
    expect(files.some((f) => f.startsWith(WEB_BETTING) && f.endsWith("index.ts"))).toBe(true);
    expect(files.some((f) => f.startsWith(API_BETTING) && f.endsWith("__init__.py"))).toBe(true);
  });

  it("no file contains a forbidden word, credential, network channel or write route", () => {
    const bad = files.flatMap((f) => violations(readFileSync(f, "utf8")).map((v) => `${relative(APP, f)}: ${v}`));
    expect(bad).toEqual([]);
  });

  it("the scan is not vacuous: it catches each forbidden thing and allows only the listed phrase", () => {
    const planted = [
      'headers: { Authorization: "x" }',
      "const api_key = 1",
      "apikey",
      "client_secret",
      "place_order()",
      "executeNow()",
      "a token here",
      "an order here",
      "the orders here",
      'fetch("https://example.invalid/x")',
      'fetch("//example.invalid/x")',
      "fetch(url)",
      "new WebSocket(u)",
      "import httpx",
      "@router.post(",
      "@app.delete(",
      'router.add_api_route("/x", f, methods=["POST"])',
    ];
    for (const p of planted) expect(violations(p), p).not.toEqual([]);
    expect(violations("Analysis only, not advice; no orders are placed.")).toEqual([]);
    expect(violations('getJson("/api/betting/status"); fetch("/api/betting/status")')).toEqual([]);
    expect(violations("No orders are placed")).not.toEqual([]); // the allow-list is exact, case included
  });
});

describe("only the two features files name the switch", () => {
  it("web: only src/features.ts names either variable", () => {
    const named = walk(SRC)
      .filter((f) => /\.(ts|tsx|js|mjs|json|css)$/.test(f))
      .filter((f) => {
        const t = readFileSync(f, "utf8");
        return t.includes(WEB_NAME) || t.includes(API_NAME);
      })
      .map((f) => relative(SRC, f));
    expect(named).toEqual(["features.ts"]);
  });

  it("API: only parlayhitters_api/features.py names either variable", () => {
    const named = walk(API_SRC)
      .filter((f) => f.endsWith(".py"))
      .filter((f) => {
        const t = readFileSync(f, "utf8");
        return t.includes(WEB_NAME) || t.includes(API_NAME);
      })
      .map((f) => relative(API_SRC, f));
    expect(named).toEqual([join("parlayhitters_api", "features.py")]);
  });
});

describe("the marker list", () => {
  const { markers } = JSON.parse(readFileSync(join(WEB_BETTING, "markers.json"), "utf8")) as { markers: string[] };

  it("starts with the placeholder page's exact sentence and lists the reserved names", () => {
    expect(markers.slice(0, 4)).toEqual([
      "Betting tools: analysis only, not advice, no orders are placed. Nothing is built here yet.",
      "/api/betting",
      "#/bet",
      "ph-bet",
    ]);
    expect(readFileSync(join(WEB_BETTING, "Placeholder.tsx"), "utf8")).toContain(markers[0]);
  });

  it("no marker appears in source outside the betting module, so the OFF build cannot carry one by accident", () => {
    const outside = walk(SRC)
      .filter((f) => !f.startsWith(WEB_BETTING) && /\.(ts|tsx|css)$/.test(f) && !f.endsWith(".test.ts"))
      .flatMap((f) => {
        const t = readFileSync(f, "utf8");
        return markers.filter((m) => t.includes(m)).map((m) => `${relative(SRC, f)}: ${m}`);
      });
    expect(outside).toEqual([]);
  });
});

describe(`the #/bet prefix (switch ${BETTING ? "ON" : "OFF"} in this run)`, () => {
  const hostile = [
    "#/bet/compare", "#/bet/slip", "#/bet/ideas", "#/bet/hedge", "#/bet/analysis", "#/bet/zzz", "#/bet/a/b", "#/BET", "#/Bet",
    "#/bet/%2e%2e", "#/bet/..", "#/bet/" + "a".repeat(30), "#/bet?x=\u0000", "#/bet?" + "q".repeat(300), "#/bet/compare?x=1", "#/bet/1",
    "#/bet/%F0%9F%8F%80", "#/bets", "#/betting",
  ];

  it("#/bet is the placeholder with the switch on and not-found with it off", () => {
    expect(parse("#/bet")).toEqual(BETTING ? { name: "bet", path: [], query: "" } : { name: "notfound", hash: "#/bet" });
    expect(parse("#/bet/")).toEqual(BETTING ? { name: "bet", path: [], query: "" } : { name: "notfound", hash: "#/bet/" });
  });

  it("every other #/bet... address is not-found in both states (no screen is registered for it yet), and none throws", () => {
    for (const h of hostile) expect(parse(h), h).toEqual({ name: "notfound", hash: h });
  });

  it("existing routes are untouched, including the Hedge Finder", () => {
    expect(parse("#/hedge")).toEqual({ name: "hedge", game: null, player: null, stat: null });
    expect(parse("#/")).toEqual({ name: "today", date: null });
  });
});
