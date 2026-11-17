// The read-only stored-snapshot contract (GET /api/betting/providers, /board/{game}/{player}/{stat}, /slate/{date}) and
// the adapter from it to the catalog's quotes. Nothing here reads the network: the screens ask through the app's own
// api.ts and hand the parsed answer in. A stored quote is "delayed" (an export snapshot), its fee is unknown unless the
// export says otherwise (it does not today), and an unusable quote is never put on the board.
import { parseInstant } from "../time";
import { STATS, type Stat } from "../types";
import { buildCatalog, inHoldoutSeason, isFiniteIn, KNOWN_PROVIDERS, type RejectedRecord } from "./catalog";
import type { Catalog, Market, ProviderInfo, Side } from "./types";

// ------------------------------------------------------------------------------------------------ the API contract

export interface ApiProvider {
  id: string;
  label: string;
  kind: "sportsbook" | "exchange";
  price_unit: "american";
  newest_snapshot_utc: string | null;
  n_quotes: number;
  fee: null;
  settlement_rule_id: null;
}
export interface ApiProviders {
  as_of: string;
  data_version: string;
  providers: ApiProvider[];
  unknown: string[];
  note: string;
}
export interface ApiQuote {
  quote_id: string | number;
  provider: string;
  side: "over" | "under";
  /** Null on an unusable quote (shown as unavailable with its reason, never as a price). */
  price_american: number | null;
  implied_prob_raw: number | null;
  snapshot_utc: string;
  minutes_before_tip: number | null;
  is_alternate: boolean;
  usable: boolean;
  unusable_reason: string | null;
}
/** GET /api/betting/slate/{date}: one night's props with stored quotes (home/away are team abbreviations). */
export interface ApiSlate {
  date: string;
  as_of: string;
  games: { game_id: string; home: string; away: string; tip_utc?: string }[];
  props: { game_id: string; player_id: number; player: string; team: string; stat: string; n_providers: number; n_lines: number }[];
}

export interface ApiBoard {
  game_id: string;
  date: string;
  tip_utc: string;
  final: boolean;
  player_id: number;
  player: string;
  team: string;
  opponent: string;
  stat: string;
  as_of: string;
  data_version: string;
  provenance: "delayed";
  lines: { line: number; quotes: ApiQuote[] }[];
  providers_absent: string[];
  /** `props` is the existing props path with ids filled in; that route still needs ?line=<line> appended. */
  model_ref: { props: string; source: string } | null;
  unknown: string[];
  note: string;
}

// ------------------------------------------------------------------------------------------------ identity

export const isStat = (s: string): s is Stat => (STATS as readonly string[]).includes(s);

/** The model's side for the page's side: YES is the over (the stat finishes above the line). */
export const sideOf = (s: "over" | "under"): Side => (s === "over" ? "yes" : "no");
export const overUnder = (s: Side): "over" | "under" => (s === "yes" ? "over" : "under");

/** A stable market id for an export market: the same game, player, stat and line always give the same id. */
export function exportMarketId(gameId: string, playerId: number | string, stat: Stat, line: number): string {
  return `mkt-${gameId}-${String(playerId)}-${stat}-${String(line)}`;
}

export interface MarketFacts {
  gameId: string;
  date: string;
  playerId: number;
  playerName: string;
  stat: string;
  line: number;
  /** "AWY @ HOM". */
  eventLabel: string;
}

export type MarketBuild = { ok: true; market: Market } | { ok: false; text: string };

/** The market a game or player page shows, or why it cannot go on the slip (combos, holdout season, odd lines). */
export function marketFromFacts(f: MarketFacts): MarketBuild {
  if (!isStat(f.stat)) return { ok: false, text: "Only points, rebounds, assists and threes can go on the slip." };
  if (!isFiniteIn(f.line, 0, 1000)) return { ok: false, text: "That line is not a number the slip can hold." };
  if (inHoldoutSeason(f.date)) return { ok: false, text: "Games from season 2025 are the frozen holdout and stay off the slip." };
  const name = f.playerName.trim();
  if (name === "") return { ok: false, text: "The page did not name the player." };
  return {
    ok: true,
    market: {
      id: exportMarketId(f.gameId, f.playerId, f.stat, f.line),
      eventId: f.gameId,
      eventDate: f.date,
      playerId: String(f.playerId),
      stat: f.stat,
      line: f.line,
      period: "full_game",
      settlementRuleId: null,
      playerName: name.slice(0, 100),
      eventLabel: f.eventLabel.slice(0, 120),
      origin: "export",
    },
  };
}

const slug = (s: string): string =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40) || "x";

/** A market the viewer typed (a player and game this app may not carry). Its ids say "manual". */
export function customMarket(input: { playerName: string; eventLabel: string; date: string; stat: string; line: number }): MarketBuild {
  const name = input.playerName.trim();
  if (name === "") return { ok: false, text: "Type the player's name." };
  if (!isStat(input.stat)) return { ok: false, text: "Choose points, rebounds, assists or threes." };
  if (!isFiniteIn(input.line, 0, 1000)) return { ok: false, text: "Type a line from 0 to 1000." };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(input.date) || parseInstant(input.date) === null) return { ok: false, text: "Type the game's date as YYYY-MM-DD." };
  if (inHoldoutSeason(input.date)) return { ok: false, text: "Games from season 2025 are the frozen holdout and stay off the slip." };
  const label = input.eventLabel.trim() || "Custom game";
  const eventId = `manual-${input.date}-${slug(label)}`;
  const playerId = `manual-${slug(name)}`;
  return {
    ok: true,
    market: {
      id: `mkt-${eventId}-${playerId}-${input.stat}-${String(input.line)}`,
      eventId,
      eventDate: input.date,
      playerId,
      stat: input.stat,
      line: input.line,
      period: "full_game",
      settlementRuleId: null,
      playerName: name.slice(0, 100),
      eventLabel: label.slice(0, 120),
      origin: "manual",
    },
  };
}

/** Where a market's stored prices can come from: the export (numeric ids), the demo, or nowhere (typed by hand). */
export function marketSource(m: Market): "export" | "sample" | "manual" {
  return m.origin === "sample" ? "sample" : m.origin === "manual" ? "manual" : "export";
}

// ------------------------------------------------------------------------------------------------ providers

/** The registry: the three known venues, then every provider the API lists (known ids keep their known kind). */
export function providerRegistry(api: ApiProviders | null): ProviderInfo[] {
  const out: ProviderInfo[] = [...KNOWN_PROVIDERS];
  for (const p of api?.providers ?? []) {
    const id = p.id.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9_.-]{0,39}$/.test(id) || out.some((x) => x.id === id)) continue;
    out.push({ id, label: (p.label || id).slice(0, 60), kind: p.kind === "exchange" ? "exchange" : "sportsbook" });
  }
  return out;
}

/** Providers listed by name (the screens' neutral sequence: never by price). */
export function byName(providers: readonly ProviderInfo[]): ProviderInfo[] {
  return [...providers].sort((a, b) => a.label.localeCompare(b.label));
}

// ------------------------------------------------------------------------------------------------ the board

export interface StoredBoard {
  readonly catalog: Catalog;
  readonly rejected: readonly RejectedRecord[];
  /** Unusable quotes left off the board, with the export's reason. */
  readonly unusable: readonly { provider: string; reason: string }[];
  /** Lines the export carries for this player and stat (to offer when the chosen line has no stored price). */
  readonly lines: readonly number[];
  /** The export's own note (on a source with no stored quotes it says so); shown with the empty state. */
  readonly note: string;
}

/** A zone-explicit ISO instant for a stored time (stored naive times are UTC), or null. */
function instant(s: string | null | undefined): string | null {
  const d = parseInstant(s);
  return d === null ? null : d.toISOString();
}

/**
 * Cents for one exchange contract from a stored American price: the export's raw implied chance when it carries one,
 * otherwise the same conversion the cost formula uses. A unit conversion of the stored price, nothing else.
 */
export function centsFromAmerican(q: Pick<ApiQuote, "price_american" | "implied_prob_raw">): number | null {
  if (q.implied_prob_raw !== null && isFiniteIn(q.implied_prob_raw, 0.0001, 1)) return Math.round(q.implied_prob_raw * 1e8) / 1e6;
  const o = q.price_american;
  if (o === null || !Number.isFinite(o) || Math.abs(o) < 100) return null;
  return Math.round((o > 0 ? 10000 / (o + 100) : (-o * 100) / (-o + 100)) * 1e6) / 1e6;
}

/**
 * The stored board for one market and side: the newest usable quote per provider on exactly this line. Each quote is
 * validated by the catalog (a bad one is listed, not shown). Fees are unknown: the export carries none.
 */
export function storedBoard(board: ApiBoard, market: Market, side: Side, providers: readonly ProviderInfo[]): StoredBoard {
  const want = overUnder(side);
  const unusable: { provider: string; reason: string }[] = [];
  const newest = new Map<string, ApiQuote>();
  const row = board.lines.find((l) => l.line === market.line);
  for (const q of row?.quotes ?? []) {
    if (q.side !== want) continue;
    if (!q.usable || q.price_american === null) {
      unusable.push({ provider: q.provider, reason: q.unusable_reason ?? "marked unusable by the export" });
      continue;
    }
    const prev = newest.get(q.provider);
    if (prev === undefined || (parseInstant(q.snapshot_utc)?.getTime() ?? 0) > (parseInstant(prev.snapshot_utc)?.getTime() ?? 0)) newest.set(q.provider, q);
  }
  const quotes: unknown[] = [];
  for (const [provider, q] of newest) {
    const kind = providers.find((p) => p.id === provider)?.kind;
    const observedAt = instant(q.snapshot_utc);
    const base = { id: `stored-${String(q.quote_id)}`, marketId: market.id, provider, side, provenance: "delayed", observedAt, expiresAt: null };
    if (kind === "exchange") {
      const c = centsFromAmerican(q);
      quotes.push({ ...base, price: { kind: "binary", askCents: c, fee: { status: "unknown" } } });
    } else {
      quotes.push({ ...base, price: { kind: "sportsbook", americanOdds: q.price_american, fee: { status: "unknown" } } });
    }
  }
  const built = buildCatalog({ providers, markets: [market], quotes });
  return { catalog: built.catalog, rejected: built.rejected, unusable, lines: board.lines.map((l) => l.line), note: board.note };
}
