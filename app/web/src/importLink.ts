// Import of a parlay from a link or a screenshot reading. Same schema and rules as the API's scan.validate_import
// (docs/APP_ARCHITECTURE.md). Strict: unknown fields, wrong types and out-of-range numbers are refused.
import type { LiveGameRow } from "./liveTypes";
import type { Leg } from "./tracker";

export type ImportKind = "player" | "win" | "spread" | "total";
export type ImportStat = "pts" | "reb" | "ast" | "fg3m";

export interface ImportLeg {
  label: string;
  kind: ImportKind;
  player?: string;
  stat?: ImportStat;
  threshold?: number;
  side?: "over" | "under";
  team?: string;
  ticker?: string;
  entry_price?: number;
}

export interface ImportSpec {
  name: string;
  contracts: number;
  paid: number;
  combo_price?: number;
  max_payout?: number;
  current_value?: number;
  legs: ImportLeg[];
}

export type Checked = { ok: true; spec: ImportSpec } | { ok: false; error: string };

const KINDS: readonly string[] = ["player", "win", "spread", "total"];
const STATS: readonly string[] = ["pts", "reb", "ast", "fg3m"];
export const MAX_LINK_CHARS = 20_000;

class Bad extends Error {}

function num(v: unknown, lo: number | null, hi: number | null, open = false): number {
  if (typeof v !== "number" || !Number.isFinite(v)) throw new Bad("a number was expected");
  if (lo !== null && (open ? v <= lo : v < lo)) throw new Bad("number out of range");
  if (hi !== null && v > hi) throw new Bad("number out of range");
  return v;
}

function str(v: unknown, n: number): string {
  if (typeof v !== "string" || v.trim().length < 1 || v.trim().length > n) throw new Bad("text of the wrong length");
  return v.trim();
}

function only(o: Record<string, unknown>, allowed: string[]): void {
  const extra = Object.keys(o).find((k) => !allowed.includes(k));
  if (extra !== undefined) throw new Bad(`unknown field: ${extra.slice(0, 30)}`);
}

function leg(x: unknown): ImportLeg {
  if (typeof x !== "object" || x === null || Array.isArray(x)) throw new Bad("a leg must be an object");
  const o = x as Record<string, unknown>;
  only(o, ["label", "kind", "player", "stat", "threshold", "side", "team", "ticker", "entry_price"]);
  const kind = o["kind"];
  if (typeof kind !== "string" || !KINDS.includes(kind)) throw new Bad("leg kind must be player, win, spread or total");
  const out: ImportLeg = { label: str(o["label"], 120), kind: kind as ImportKind };
  if (kind === "player") {
    out.player = str(o["player"], 60);
    const st = o["stat"];
    if (typeof st !== "string" || !STATS.includes(st)) throw new Bad("player legs need a stat: pts, reb, ast or fg3m");
    out.stat = st as ImportStat;
    out.threshold = num(o["threshold"], 0, 200, true);
  } else if (kind === "total") out.threshold = num(o["threshold"], 0, 1000, true);
  else if (kind === "spread") out.threshold = num(o["threshold"], -100, 100);
  else if ("threshold" in o) throw new Bad("threshold does not apply to a win leg");
  if (kind === "win" || kind === "spread") {
    const t = o["team"];
    if (typeof t !== "string" || !/^[A-Z0-9]{2,5}$/.test(t)) throw new Bad("win and spread legs need a team abbreviation like ATL");
    out.team = t;
  } else if ("team" in o) throw new Bad("team applies to win and spread legs only");
  if (kind === "player" || kind === "total") {
    const side = o["side"] ?? "over";
    if (side !== "over" && side !== "under") throw new Bad("side must be over or under");
    out.side = side;
  } else if ("side" in o) throw new Bad("side applies to player and total legs only");
  if (o["ticker"] !== undefined && o["ticker"] !== null) {
    const t = o["ticker"];
    if (typeof t !== "string" || !/^[A-Za-z0-9._-]{1,80}$/.test(t)) throw new Bad("ticker has unusual characters");
    out.ticker = t;
  }
  if (o["entry_price"] !== undefined && o["entry_price"] !== null) out.entry_price = num(o["entry_price"], 0, 1, true);
  return out;
}

export function validateImport(o: unknown): Checked {
  try {
    if (typeof o !== "object" || o === null || Array.isArray(o)) throw new Bad("the import must be an object");
    const r = o as Record<string, unknown>;
    only(r, ["name", "contracts", "paid", "combo_price", "max_payout", "current_value", "legs"]);
    const legs = r["legs"];
    if (!Array.isArray(legs) || legs.length < 1 || legs.length > 12) throw new Bad("legs must be a list of 1 to 12");
    const spec: ImportSpec = {
      name: str(r["name"] ?? "Imported parlay", 60),
      contracts: num(r["contracts"], 0, 1_000_000, true),
      paid: num(r["paid"], 0, 1_000_000),
      legs: legs.map(leg),
    };
    if (r["combo_price"] !== undefined && r["combo_price"] !== null) spec.combo_price = num(r["combo_price"], 0, 1, true);
    if (r["max_payout"] !== undefined && r["max_payout"] !== null) spec.max_payout = num(r["max_payout"], 0, 10_000_000);
    if (r["current_value"] !== undefined && r["current_value"] !== null) spec.current_value = num(r["current_value"], 0, 10_000_000);
    return { ok: true, spec };
  } catch (e) {
    return { ok: false, error: e instanceof Bad ? e.message : "the import could not be read" };
  }
}

/* ------------------------------------------------------------------ link encoding */
export function encodeImport(spec: ImportSpec): string {
  const bytes = new TextEncoder().encode(JSON.stringify(spec));
  let bin = "";
  bytes.forEach((b) => { bin += String.fromCharCode(b); });
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodeImport(param: string): Checked {
  if (param.length === 0 || param.length > MAX_LINK_CHARS) return { ok: false, error: "the link is empty or too long" };
  if (!/^[A-Za-z0-9_-]+$/.test(param)) return { ok: false, error: "the link is not base64url" };
  try {
    const b64 = param.replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4));
    const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    return validateImport(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
  } catch {
    return { ok: false, error: "the link did not decode to JSON" };
  }
}

/* ------------------------------------------------------------------ games and legs */
/** ESPN abbreviation -> Kalshi's. */
const TO_KALSHI: Record<string, string> = { WSH: "WAS", GS: "GSW", NY: "NYK", SA: "SAS", NO: "NOP", UTAH: "UTA", PHO: "PHX" };
const k = (a: string): string => TO_KALSHI[a.toUpperCase()] ?? a.toUpperCase();

/** The scoreboard game a leg belongs to, from its ticker's game code or its team; null when unclear. */
export function gameForLeg(l: ImportLeg, rows: LiveGameRow[]): LiveGameRow | null {
  const parts = (l.ticker ?? "").split("-");
  const code = parts.length > 1 ? (parts[1] ?? "").slice(7) : "";
  if (code !== "") {
    const hit = rows.filter((r) => `${k(r.away.abbr)}${k(r.home.abbr)}` === code);
    if (hit.length === 1) return hit[0] ?? null;
  }
  if (l.team) {
    const hit = rows.filter((r) => k(r.away.abbr) === k(l.team ?? "") || k(r.home.abbr) === k(l.team ?? ""));
    if (hit.length === 1) return hit[0] ?? null;
  }
  return null;
}

export function toLeg(l: ImportLeg, row: LiveGameRow | null, id: string): Leg {
  const teamAbbr = row && l.team ? ([row.away.abbr, row.home.abbr].find((a) => k(a) === k(l.team ?? "")) ?? l.team) : (l.team ?? "");
  return {
    id,
    kind: l.kind,
    eventId: row?.event_id ?? "",
    gameLabel: row ? `${row.away.abbr} @ ${row.home.abbr}` : l.label,
    startUtc: row?.start_utc ?? new Date().toISOString(),
    player: l.player ?? "",
    stat: l.stat ?? "pts",
    side: l.side ?? "over",
    line: l.threshold ?? 0,
    team: teamAbbr,
    ticker: l.ticker ?? "",
    entry: l.entry_price ?? null,
  };
}

/** What POST /api/scan/kalshi-screenshot returns. */
export interface ScanResult {
  parlay: ImportSpec;
  screen: { max_payout?: number; current_value?: number };
  resolved: boolean[];
  source: string;
}
