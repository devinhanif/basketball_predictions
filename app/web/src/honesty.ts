// The honesty routes (app/api, 2026-11-17; contracts in docs/contracts/*_v1.md): where a forecast came from, its stored
// distribution, the scorecard, the forward tests, the parlay engine's stored joints and the health strip. Types name only
// the fields the pages show. Nothing here computes a number: every value on these pages is a field of an answer.
import { getRaw } from "./transport";
import { ApiError, failureFromAnswer, failureFromNetwork } from "./errors";
import type { Loaded } from "./api";

/** An answer, or a plain "this copy of the data does not carry it" (a saved snapshot, or a server that predates it). */
export type Maybe<T> = { kind: "ok"; data: T } | { kind: "absent"; why: string };

/** The artifact's answer for a path it did not bake (bakedStore.ts). */
export const NOT_IN_SNAPSHOT = "that page is not part of this snapshot";

/**
 * Is this 404 "the route or the baked answer is not here" rather than a refusal about the thing asked for? A refusal
 * names its own code (no_forecast, unknown_game, ...); an absent route answers FastAPI's plain not_found.
 */
export function isAbsent(status: number, body: unknown): boolean {
  if (status !== 404) return false;
  const o = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
  if (o["detail"] === NOT_IN_SNAPSHOT) return true;
  const code = o["code"];
  return code === undefined || code === "not_found";
}

/**
 * One answer as a Maybe. `quiet` maps refusal codes that are a normal state of the data (not a fault) to an absent
 * answer with the given words, for example "no stored parlay check for this night".
 */
export async function getMaybe<T>(path: string, quiet: Readonly<Record<string, string>> = {}): Promise<Loaded<Maybe<T>>> {
  let r;
  try {
    r = await getRaw(path);
  } catch (e) {
    const online = typeof navigator === "undefined" || navigator.onLine;
    throw new ApiError(failureFromNetwork(path, online, e instanceof Error ? e.message : null));
  }
  if (isAbsent(r.status, r.body)) {
    const o = r.body as { detail?: unknown } | null;
    const snap = o !== null && o.detail === NOT_IN_SNAPSHOT;
    return {
      data: { kind: "absent", why: snap ? "This saved snapshot does not carry it." : "The data server this page talks to does not serve it yet." },
      fromCache: r.fromCache,
    };
  }
  const code = typeof r.body === "object" && r.body !== null ? (r.body as Record<string, unknown>)["code"] : undefined;
  if (typeof code === "string" && Object.hasOwn(quiet, code)) return { data: { kind: "absent", why: quiet[code] ?? "" }, fromCache: r.fromCache };
  if (r.status < 200 || r.status >= 300) throw new ApiError(failureFromAnswer(r.status, r.body, path, r.requestId ?? null));
  if (typeof r.body !== "object" || r.body === null) {
    throw new ApiError({ ...failureFromAnswer(r.status, r.body, path, r.requestId ?? null), code: "unexpected_error", detail: "the answer was not in the expected form" });
  }
  return { data: { kind: "ok", data: r.body as T }, fromCache: r.fromCache };
}

// ------------------------------------------------------------------------------------------------ record_v1
export interface Snapshot {
  made_at: string | null;
  lead_minutes: number | null;
  meets_t60: boolean | null;
  model_name: string | null;
  version: string | null;
  is_primary: boolean | null;
  comparison_only: boolean | null;
  is_latest: boolean | null;
}
export interface ProvRecord {
  kind: "prop" | "game_winner";
  game_id: string;
  player_id: number | null;
  stat: string | null;
  available: boolean;
  reason: string | null;
  model_name: string | null;
  version: string | null;
  weights_sha256: string | null;
  weights_sha256_short: string | null;
  weights_scope?: string | null;
  fit_through: string | null;
  n_train: number | null;
  made_at: string | null;
  tipoff: string | null;
  lead_minutes: number | null;
  meets_t60: boolean | null;
  t60_rule?: string | null;
  data_version: string | null;
  git_sha: string | null;
  git_dirty: boolean | null;
  stamped_at: string | null;
  primary_source: string | null;
  primary_detail: string | null;
  fallback_reason: string | null;
  snapshots: Snapshot[];
  snapshots_note?: string | null;
  missing: string[];
}

// ------------------------------------------------------------------------------------------------ distribution_v1
export interface Distribution {
  game_id: string;
  player_id: number;
  stat: string;
  available: boolean;
  locked: boolean;
  lock_text: string | null;
  model_name: string | null;
  version: string | null;
  made_at: string | null;
  taus: number[];
  q_grid: number[] | null;
  mean: number | null;
  std: number | null;
  support: { q_grid: string | null; survival: string | null; words?: string | null } | null;
  p_ge_full: { n: number; counts: number[]; k_from: number; meaning?: string } | null;
  p_play: { value: number | null; source: string | null; detail: string | null } | null;
  proj_minutes: number | null;
  conditional: {
    p_play_used: number | null;
    p_ge_given_plays: Record<string, number> | null;
    p_ge_unconditional: Record<string, number> | null;
    mean_given_plays: number | null;
    mean_unconditional: number | null;
    source?: string | null;
  } | null;
  kalshi_dnp_note: string | null;
  missing: string[];
}

// ------------------------------------------------------------------------------------------------ scorecard_v1
export interface Metric {
  value?: number | null;
  lo?: number | null;
  hi?: number | null;
  n?: number | null;
  n_dates?: number | null;
  call?: string | null;
}
export interface ScoreModel {
  model_name: string;
  role: string;
  shown: boolean;
  withheld_reason: string | null;
  n_scored: number;
  n_dates: number;
  n_dnp: number;
  n_late_excluded?: number;
  n_t60_unknown?: number;
  n_scored_locked: number;
  n_dates_locked: number;
  n_dnp_locked?: number;
  metrics: Record<string, Metric>;
}
export interface ScoreComparison {
  arm: string;
  comparator: string;
  shown: boolean;
  withheld_reason: string | null;
  n_pairs: number;
  n_dates: number;
  n_pairs_locked: number;
  n_dates_locked: number;
  metrics: Record<string, Metric>;
}
export interface ScoreTarget {
  target: string;
  models: ScoreModel[];
  comparisons: ScoreComparison[];
  sentence: string;
}
export interface Scorecard {
  as_of: string | null;
  available: boolean;
  reason: string | null;
  seasons: number[];
  population?: string;
  score_rule: { rule: string; table?: string; source?: string; t60_filter?: string; n_rows_read?: number; n_late_excluded?: number; n_t60_unknown?: number; limitation?: string };
  method?: string;
  lock_from: string | null;
  lock_text: string | null;
  market: { available: boolean; reason: string | null };
  targets: ScoreTarget[];
  not_available: { metric: string; needs: string; why: string }[];
}

// ------------------------------------------------------------------------------------------------ forward_tests_v1
export interface ForwardTest {
  id: string;
  title: string;
  source: string;
  as_of: string | null;
  scheduled_read: string | null;
  n_needed: string | null;
  arms: string[];
  n_so_far: Record<string, { scored: number | null; dnp: number | null; dates: number | null }> | null;
  n_so_far_reason: string | null;
}
export interface ForwardTests {
  version: number;
  source: string;
  as_of: string | null;
  note: string | null;
  count_rule: string | null;
  tests: ForwardTest[];
}

// ------------------------------------------------------------------------------------------------ parlay_diagnostics_v1
export interface DiagRecord {
  ticker: string | null;
  title: string | null;
  engine: string | null;
  n_legs: number | null;
  legs: { game_id: string | null; player_id: number | null; stat: string | null; side: string | null; threshold: number | null }[];
  joint_stored: number | null;
  joint_stored_field: string | null;
  joint_independent: number | null;
  joint_copula: number | null;
  shrunk_prob: number | null;
  model_weight: number | null;
  verdict: string | null;
  missing: string[];
}
export interface ParlayDiag {
  date: string;
  available: boolean;
  reason: string | null;
  source: string | null;
  verdict: string | null;
  n_records: number;
  offset: number;
  limit: number;
  n_returned: number;
  engines: Record<string, number>;
  n_with_independent: number;
  n_with_copula: number;
  records: DiagRecord[];
  note: string | null;
}

// ------------------------------------------------------------------------------------------------ health_strip_v1
export interface Health {
  status: string;
  mode: string;
  data_made_at: string | null;
  data_age_s: number | null;
  data_version: string | null;
  export: { schema_version?: number; export_date?: string } | null;
  newest_quote?: { label: string; by_book: Record<string, string>; pinnacle: string | null; source: string } | null;
  stale_marks?: { source: string; by_model: Record<string, Record<string, number>>; primary_fallback_stale: number | null } | null;
  strip_missing?: string[];
}

export interface Registry {
  version: number;
  codes: { code: string; status: number | null; side: string; title: string; what: string; why: string; todo: string }[];
}

const e = encodeURIComponent;
export const honestyApi = {
  propRecord: (g: string, p: number, s: string) => getMaybe<ProvRecord>(`/api/record/${e(g)}/${p.toString()}/${e(s)}`),
  gameRecord: (g: string) => getMaybe<ProvRecord>(`/api/record/${e(g)}`),
  distribution: (g: string, p: number, s: string) => getMaybe<Distribution>(`/api/distribution/${e(g)}/${p.toString()}/${e(s)}`),
  scorecard: () => getMaybe<Scorecard>("/api/scorecard"),
  forwardTests: () => getMaybe<ForwardTests>("/api/forward-tests"),
  parlayDiag: (date: string, offset: number, limit: number) =>
    getMaybe<ParlayDiag>(`/api/parlay-diagnostics/${e(date)}?offset=${offset.toString()}&limit=${limit.toString()}`, {
      no_parlay_check: "The data holds the engine's check for one night only, and this is not that night.",
    }),
  health: () => getMaybe<Health>("/api/health"),
};

// ------------------------------------------------------------------------------------------------ the Learn lens promise
/** The same price words scripts/learn-dom.mjs and the route walker look for. */
export const PRICE_WORDS: readonly RegExp[] = [
  /(?<![\w])[+\-−]\d{3,4}\b/,
  /\d\s?¢/,
  /\$\s?\d/,
  /\bcents? per\b/i,
  /\b(EV|vig|vigorish|odds|payout|moneyline|implied|break-even)\b/i,
  /\bedges?\b/i,
  /\bfees?\b/i,
  /\bprices?\b/i,
  /\b(DraftKings|FanDuel|BetMGM|Caesars|ESPN ?BET|Fanatics|Kalshi|Polymarket|Novig|Pinnacle)\b/i,
];

/** Does a stored sentence speak of prices, fees, edges or books? Such text shows only in the Market lens. */
export function hasPriceWords(text: string | null | undefined): boolean {
  return typeof text === "string" && PRICE_WORDS.some((re) => re.test(text));
}
