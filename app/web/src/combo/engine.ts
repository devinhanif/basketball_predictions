// Combo mode engine: a line-for-line TypeScript port of nba/parlay/combo.py (the reference, standard library only),
// tested against its golden vectors (engine.test.ts, contract section 5.6). Pure probability: no prices, no EV, no
// fees, no stakes. Plain functions, no UI imports, no I/O. The names follow the reference so a diff reads side by side.
import type {
  ComboGame,
  ComboPlayer,
  ComboResult,
  ComboShared,
  CorrRow,
  Leg,
  PlayerLeg,
  RankedCombo,
  StatGrid,
  TargetCombo,
  TargetResult,
} from "./types";

export const SCHEMA = "combo_export_v1";
export const ESTIMATE_LABEL = "model estimate, not checked against results";
export const STAT_ORDER = ["pts", "reb", "ast", "fg3m"] as const;
export const SCRIPT_ORDER = ["blowout", "close", "shootout", "grind"] as const;
const INF = Infinity;
const ZCLIP = 9.0;
const PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53];
export const QMC_POINTS = 1500;
export const QMC_SHIFTS = 8;
export const SIMPSON_N = 2000;
const RHO_MAX = 0.9999;
const SCREEN_BIV_N = 120;
const SCREEN_QMC: readonly [number, number] = [150, 4];
export const TESTED_P_MAX = 0.75;
export const TESTED_RAW_MAX = 0.8;
export const TARGET_P_MIN = 0.35;
export const TARGET_P_MAX = 0.75;

// ---------------------------------------------------------------- normal distribution

/** Phi(x) to about 1e-15 relative (Hart 1968, algorithm 5666, as in West 2005); exact at +-inf. */
export function normCdf(x: number): number {
  if (x === INF) return 1;
  if (x === -INF) return 0;
  const ax = Math.abs(x);
  if (ax > 38.4) return x > 0 ? 1 : 0;
  const e = Math.exp((-ax * ax) / 2);
  let c: number;
  if (ax < 7.07106781186547) {
    let b = 3.52624965998911e-2 * ax + 0.700383064443688;
    b = b * ax + 6.37396220353165;
    b = b * ax + 33.912866078383;
    b = b * ax + 112.079291497871;
    b = b * ax + 221.213596169931;
    b = b * ax + 220.206867912376;
    c = e * b;
    b = 8.83883476483184e-2 * ax + 1.75566716318264;
    b = b * ax + 16.064177579207;
    b = b * ax + 86.7807322029461;
    b = b * ax + 296.564248779674;
    b = b * ax + 637.333633378831;
    b = b * ax + 793.826512519948;
    b = b * ax + 440.413735824752;
    c = c / b;
  } else {
    let b = ax + 0.65;
    b = ax + 4 / b;
    b = ax + 3 / b;
    b = ax + 2 / b;
    b = ax + 1 / b;
    c = e / b / 2.506628274631;
  }
  return x > 0 ? 1 - c : c;
}

/** Phi^-1(p); +-inf at 0 and 1. Agrees with Wichura AS241 (the reference's NormalDist.inv_cdf) to about 1e-12. */
export function normPpf(p: number): number {
  if (p <= 0) return -INF;
  if (p >= 1) return INF;
  if (p > 0.5) return -normPpf(1 - p); // 1 - p is exact for p in [0.5, 1]
  if (p === 0.5) return 0;
  // Abramowitz and Stegun 26.2.23 start, then Halley steps on the accurate cdf (tails stay relative-accurate).
  const t = Math.sqrt(-2 * Math.log(p));
  let x = -(t - (2.515517 + 0.802853 * t + 0.010328 * t * t) / (1 + 1.432788 * t + 0.189269 * t * t + 0.001308 * t * t * t));
  for (let i = 0; i < 6; i++) {
    const err = normCdf(x) - p;
    const u = (err * Math.sqrt(2 * Math.PI)) / Math.exp((-x * x) / 2);
    const step = u / (1 + (x * u) / 2);
    x -= step;
    if (Math.abs(step) < 1e-15 * Math.max(1, Math.abs(x))) break;
  }
  return x;
}

function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);
}

// ---------------------------------------------------------------- marginals

/** S(k) = P(Y >= k) from a stat grid; 1 below the stored range, 0 above. */
export function survAt(grid: Pick<StatGrid, "k0" | "surv">, k: number): number {
  if (k <= 0) return 1;
  if (k < grid.k0) return 1;
  const j = k - grid.k0;
  if (j >= grid.surv.length) return 0;
  return grid.surv[j] ?? 0;
}

/** (lo, hi) integer bounds of a leg. over x.5 -> (x + 1, inf); under x.5 -> (0, x). Whole-number lines throw. */
export function lineToBounds(line: number, side: string): [number, number] {
  if (Math.abs(line - Math.floor(line) - 0.5) > 1e-9) throw new Error(`line ${line.toString()} is not an x.5 line`);
  const x = Math.floor(line);
  if (side === "over") return [x + 1, INF];
  if (side === "under") return [0, x];
  throw new Error(`side must be 'over' or 'under', got ${side}`);
}

export function intervalProb(grid: Pick<StatGrid, "k0" | "surv">, lo: number, hi: number): number {
  if (hi < lo) return 0;
  const sLo = survAt(grid, Math.trunc(lo));
  const sHi = hi === INF ? 0 : survAt(grid, Math.trunc(hi) + 1);
  return Math.max(0, sLo - sHi);
}

// ---------------------------------------------------------------- entities and legs

interface Entity {
  kind: "player" | "margin" | "total";
  player_id: number;
  stat: string;
}
interface Merged {
  entity: Entity;
  lo: number;
  hi: number;
  a: number;
  b: number;
  p: number;
}

const entKey = (e: Entity): string => `${e.kind}|${e.player_id.toString()}|${e.stat}`;

export function playerOf(game: ComboGame, pid: number): ComboPlayer {
  for (const p of game.players) if (p.player_id === pid) return p;
  throw new Error(`player ${pid.toString()} is not in game ${game.game_id}`);
}

function gridOf(game: ComboGame, pid: number, stat: string): StatGrid {
  const g = (playerOf(game, pid).stats as Record<string, StatGrid | undefined>)[stat];
  if (g === undefined) throw new Error(`player ${pid.toString()} has no ${stat} grid`);
  return g;
}

function normIntervalLatent(mu: number, sd: number, lo: number, hi: number): [number, number, number] {
  const a = lo === -INF ? -INF : (lo - mu) / sd;
  const b = hi === INF ? INF : (hi - mu) / sd;
  return [a, b, Math.max(0, normCdf(b) - normCdf(a))];
}

function mergeLegs(legs: readonly Leg[], game: ComboGame): Merged[] {
  const acc = new Map<string, [number, number]>();
  const ents = new Map<string, Entity>();
  const g = game.game;
  for (const leg of legs) {
    let e: Entity;
    let loF: number;
    let hiF: number;
    if (leg.kind === "player") {
      e = { kind: "player", player_id: Math.trunc(leg.player_id), stat: leg.stat };
      const [lo, hi] = lineToBounds(leg.line, leg.side);
      loF = lo;
      hiF = hi;
    } else if (leg.kind === "team_margin") {
      e = { kind: "margin", player_id: 0, stat: "" };
      let gt = leg.gt;
      if (Math.abs(gt - Math.round(gt)) < 1e-9) gt += 0.5; // integer margin: "wins by more than 6" is M > 6.5
      if (leg.team === game.home_team) {
        loF = gt;
        hiF = INF;
      } else if (leg.team === game.away_team) {
        loF = -INF;
        hiF = -gt;
      } else throw new Error("team is not in the game");
    } else {
      e = { kind: "total", player_id: 0, stat: "" };
      const x = leg.line;
      if (Math.abs(x - Math.floor(x) - 0.5) > 1e-9) throw new Error(`total line ${x.toString()} is not an x.5 line`);
      if (leg.side === "over") {
        loF = x;
        hiF = INF;
      } else {
        loF = -INF;
        hiF = x;
      }
    }
    const key = entKey(e);
    ents.set(key, e);
    const cur = acc.get(key);
    if (cur === undefined) acc.set(key, [loF, hiF]);
    else {
      cur[0] = Math.max(cur[0], loF);
      cur[1] = Math.min(cur[1], hiF);
    }
  }
  const out: Merged[] = [];
  for (const [key, [loF, hiF]] of acc) {
    const e = ents.get(key);
    if (e === undefined) continue;
    let a: number;
    let b: number;
    let p: number;
    if (e.kind === "player") {
      const grid = gridOf(game, e.player_id, e.stat);
      p = intervalProb(grid, loF, hiF);
      a = loF <= 0 ? -INF : normPpf(1 - survAt(grid, Math.trunc(loF)));
      b = hiF === INF ? INF : normPpf(1 - survAt(grid, Math.trunc(hiF) + 1));
      if (hiF < loF) {
        a = INF;
        b = -INF;
      }
    } else if (e.kind === "margin") {
      [a, b, p] = normIntervalLatent(g.mu_m, g.sd_m, loF, hiF);
    } else {
      [a, b, p] = normIntervalLatent(g.mu_t, g.sd_t, loF, hiF);
    }
    out.push({ entity: e, lo: loF, hi: hiF, a, b, p });
  }
  return out;
}

// ---------------------------------------------------------------- correlation

function corrLookup(table: readonly CorrRow[], rel: string, a: string, b: string): number {
  const [x, y] = a <= b ? [a, b] : [b, a];
  for (const r of table) if (r[0] === rel && r[1] === x && r[2] === y) return r[3];
  return 0;
}

const teamOf = (game: ComboGame, pid: number): number => playerOf(game, pid).team;

function pairRho(e1: Entity, e2: Entity, game: ComboGame, table: readonly CorrRow[]): number {
  if (e1.kind === "player" && e2.kind === "player") {
    if (e1.player_id === e2.player_id) return corrLookup(table, "same_player", e1.stat, e2.stat);
    const same = teamOf(game, e1.player_id) === teamOf(game, e2.player_id);
    return corrLookup(table, same ? "teammate" : "opponent", e1.stat, e2.stat);
  }
  if ((e1.kind === "margin" && e2.kind === "total") || (e1.kind === "total" && e2.kind === "margin")) {
    return corrLookup(table, "game", "margin", "total");
  }
  const [pl, other] = e1.kind === "player" ? [e1, e2] : [e2, e1];
  if (other.kind === "total") return corrLookup(table, "game", pl.stat, "total");
  const sg = teamOf(game, pl.player_id) === game.home_team ? 1 : -1;
  return sg * corrLookup(table, "own_margin", pl.stat, "margin");
}

/** Lower Cholesky factor, or null when the matrix is not positive definite (pivot <= 1e-10). */
export function cholesky(m: readonly (readonly number[])[]): number[][] | null {
  const n = m.length;
  const c: number[][] = Array.from({ length: n }, () => new Array<number>(n).fill(0));
  for (let i = 0; i < n; i++) {
    const ci = c[i] as number[];
    for (let j = 0; j <= i; j++) {
      const cj = c[j] as number[];
      let s = (m[i] as readonly number[])[j] as number;
      for (let t = 0; t < j; t++) s -= (ci[t] as number) * (cj[t] as number);
      if (i === j) {
        if (s <= 1e-10) return null;
        ci[i] = Math.sqrt(s);
      } else {
        ci[j] = s / (cj[j] as number);
      }
    }
  }
  return c;
}

/** Shrink toward the identity, (1 - t) R + t I, by bisection on t (20 steps). Returns the matrix and t (0 if already PD). */
export function repairToPd(m: number[][]): [number[][], number] {
  if (cholesky(m) !== null) return [m, 0];
  const n = m.length;
  const shrunk = (t: number): number[][] => m.map((row, i) => row.map((v, j) => (1 - t) * v + (i === j ? t : 0)));
  let lo = 0;
  let hi = 1;
  for (let s = 0; s < 20; s++) {
    const mid = (lo + hi) / 2;
    if (cholesky(shrunk(mid)) === null) lo = mid;
    else hi = mid;
  }
  void n;
  return [shrunk(hi), hi];
}

function buildCorr(ents: readonly Entity[], game: ComboGame, table: readonly CorrRow[]): [number[][], number] {
  const k = ents.length;
  const m: number[][] = Array.from({ length: k }, (_, i) => Array.from({ length: k }, (_, j) => (i === j ? 1 : 0)));
  for (let i = 0; i < k; i++) {
    for (let j = i + 1; j < k; j++) {
      const r = pairRho(ents[i] as Entity, ents[j] as Entity, game, table);
      (m[i] as number[])[j] = r;
      (m[j] as number[])[i] = r;
    }
  }
  return repairToPd(m);
}

// ---------------------------------------------------------------- rectangle probabilities

/** P(a1 < Z1 <= b1, a2 < Z2 <= b2) for a standard bivariate normal, Simpson on the conditional form. */
export function bivariateRect(a1: number, b1: number, a2: number, b2: number, rhoIn: number, n: number = SIMPSON_N): number {
  if (b1 <= a1 || b2 <= a2) return 0;
  const rho = Math.max(-RHO_MAX, Math.min(RHO_MAX, rhoIn));
  const s = Math.sqrt(1 - rho * rho);
  const lo = Math.max(a1, -ZCLIP);
  const hi = Math.min(b1, ZCLIP);
  if (hi <= lo) return 0;
  const h = (hi - lo) / n;
  let tot = 0;
  for (let i = 0; i <= n; i++) {
    const z = lo + i * h;
    const inner = normCdf((b2 - rho * z) / s) - normCdf((a2 - rho * z) / s);
    const w = i === 0 || i === n ? 1 : i % 2 === 1 ? 4 : 2;
    tot += w * normPdf(z) * inner;
  }
  return (tot * h) / 3;
}

function lcgShifts(nShifts: number, dim: number): number[][] {
  let x = 12345;
  const out: number[][] = [];
  for (let s = 0; s < nShifts; s++) {
    const row: number[] = [];
    for (let d = 0; d < dim; d++) {
      x = (1664525 * x + 1013904223) % 4294967296; // exact: 1664525 * 2^32 < 2^53
      row.push(x / 4294967296);
    }
    out.push(row);
  }
  return out;
}

/** Genz (1992) separation of variables with a Richtmyer lattice and fixed shifts. Returns [estimate, se]. Deterministic. */
export function genzRect(
  chol: readonly (readonly number[])[],
  a: readonly number[],
  b: readonly number[],
  nPoints: number = QMC_POINTS,
  nShifts: number = QMC_SHIFTS,
): [number, number] {
  const k = a.length;
  const c00 = (chol[0] as readonly number[])[0] as number;
  if (k === 1) return [Math.max(0, normCdf((b[0] as number) / c00) - normCdf((a[0] as number) / c00)), 0];
  const alphas = PRIMES.slice(0, k - 1).map((p) => Math.sqrt(p));
  const shifts = lcgShifts(nShifts, k - 1);
  const means: number[] = [];
  for (const sh of shifts) {
    let acc = 0;
    for (let j = 1; j <= nPoints; j++) {
      const y: number[] = [];
      let d = normCdf((a[0] as number) / c00);
      let e = normCdf((b[0] as number) / c00);
      let f = e - d;
      for (let i = 1; i < k; i++) {
        const w = (j * (alphas[i - 1] as number) + (sh[i - 1] as number)) % 1.0;
        y.push(normPpf(Math.min(Math.max(d + w * (e - d), 1e-300), 1 - 1e-16)));
        const ci = chol[i] as readonly number[];
        let s = 0;
        for (let t = 0; t < i; t++) s += (ci[t] as number) * (y[t] as number);
        const cii = ci[i] as number;
        d = normCdf(((a[i] as number) - s) / cii);
        e = normCdf(((b[i] as number) - s) / cii);
        f *= e - d;
      }
      acc += f;
    }
    means.push(acc / nPoints);
  }
  const est = means.reduce((x, y) => x + y, 0) / nShifts;
  const v = means.reduce((x, m) => x + (m - est) ** 2, 0) / (nShifts - 1);
  return [est, Math.sqrt(v / nShifts)];
}

// ---------------------------------------------------------------- evaluation

function labelled(r: ComboResult): ComboResult {
  let rng: [number, number] | null = null;
  if ((r.method === "qmc" || r.method === "product_of_games") && r.se > 0) {
    rng = [Math.max(0, r.p_joint - 2 * r.se), Math.min(1, r.p_joint + 2 * r.se)];
  }
  return { ...r, label: r.n_entities >= 3 ? ESTIMATE_LABEL : null, range_2se: rng };
}

function firstStat(merged: readonly Merged[], pid: number): string {
  for (const m of merged) if (m.entity.kind === "player" && m.entity.player_id === pid) return m.entity.stat;
  return "";
}

function pAllPlay(merged: readonly Merged[], game: ComboGame): number | null {
  let p = 1;
  for (const m of merged) {
    if (m.entity.kind === "player" && m.entity.stat === firstStat(merged, m.entity.player_id)) {
      const pp = playerOf(game, m.entity.player_id).p_play;
      if (pp === null || pp === undefined) return null; // availability unknown: only the conditional number is shown
      p *= pp;
    }
  }
  return p;
}

export function scriptParams(shared: ComboShared, script: string, stat: string, role: string): [number, number] {
  for (const r of shared.scripts.params) if (r[0] === script && r[1] === stat && r[2] === role) return [r[3], r[4]];
  return [0, 1];
}

const prod = (xs: readonly number[]): number => xs.reduce((a, b) => a * b, 1);

function evaluateCore(
  legs: readonly Leg[],
  game: ComboGame,
  shared: ComboShared,
  script: string | null,
  qmc: readonly [number, number],
  bivN: number,
): ComboResult {
  if (legs.length === 0) throw new Error("no legs");
  const merged = mergeLegs(legs, game);
  const table = shared.corr;
  if (script !== null) {
    if (!(SCRIPT_ORDER as readonly string[]).includes(script)) throw new Error(`unknown script ${script}`);
    if (merged.some((m) => m.entity.kind !== "player")) throw new Error("game legs are not supported inside a script");
    for (const m of merged) {
      const role = playerOf(game, m.entity.player_id).starter ? "starter" : "bench";
      const [mu, sg] = scriptParams(shared, script, m.entity.stat, role);
      m.a = Number.isFinite(m.a) ? (m.a - mu) / sg : m.a; // infinite bounds stay infinite
      m.b = Number.isFinite(m.b) ? (m.b - mu) / sg : m.b;
      m.p = Math.max(0, normCdf(m.b) - normCdf(m.a));
    }
  }
  const marg = merged.map((m) => m.p);
  const pInd = prod(marg);
  const pPlay = pAllPlay(merged, game);
  const base = { se: 0, repair_t: 0, script, label: null, range_2se: null, above_tested_range: [] as boolean[] };
  if (marg.some((p) => p <= 0) || merged.some((m) => m.b <= m.a)) {
    return labelled({
      p_joint: 0,
      p_independent: pInd,
      lift: pInd === 0 ? null : 0,
      marginals: marg,
      method: "empty",
      p_all_play: pPlay,
      p_joint_incl_dnp: pPlay === null ? null : 0,
      n_entities: merged.length,
      ...base,
    });
  }
  const k = merged.length;
  let repairT = 0;
  let p: number;
  let se: number;
  let method: ComboResult["method"];
  if (k === 1) {
    p = marg[0] as number;
    se = 0;
    method = "marginal";
  } else {
    const [r, t] = buildCorr(
      merged.map((m) => m.entity),
      game,
      table,
    );
    repairT = t;
    const a = merged.map((m) => m.a);
    const b = merged.map((m) => m.b);
    if (k === 2) {
      p = bivariateRect(a[0] as number, b[0] as number, a[1] as number, b[1] as number, (r[0] as number[])[1] as number, bivN);
      se = 0;
      method = "exact_bivariate";
    } else {
      const chol = cholesky(r);
      if (chol === null) throw new Error("correlation matrix did not factor after repair");
      [p, se] = genzRect(chol, a, b, qmc[0], qmc[1]);
      method = "qmc";
    }
    p = Math.min(p, Math.min(...marg)); // Frechet bounds
    p = Math.max(p, Math.max(0, marg.reduce((x, y) => x + y, 0) - (k - 1)));
  }
  return labelled({
    p_joint: p,
    p_independent: pInd,
    lift: pInd <= 0 ? null : p / pInd,
    marginals: marg,
    se,
    method,
    p_all_play: pPlay,
    p_joint_incl_dnp: pPlay === null ? null : pPlay * p,
    repair_t: repairT,
    script,
    n_entities: k,
    label: null,
    range_2se: null,
    above_tested_range: [],
  });
}

/** Joint P of `legs` (all in `game`) with the independent P, the lift and per-leg `above_tested_range` flags. */
export function evaluateGameLegs(
  legs: readonly Leg[],
  game: ComboGame,
  shared: ComboShared,
  script: string | null = null,
  qmc: readonly [number, number] = [QMC_POINTS, QMC_SHIFTS],
  bivN: number = SIMPSON_N,
): ComboResult {
  const res = evaluateCore(legs, game, shared, script, qmc, bivN);
  return { ...res, above_tested_range: legs.map((x) => legAboveTestedRange(x, game)) };
}

/** Framing 4 (build your own): any legs, any games. Legs of different games are independent, so their joints multiply. */
export function evaluateCombo(
  legs: readonly Leg[],
  games: Readonly<Record<string, ComboGame>>,
  shared: ComboShared,
  script: string | null = null,
): ComboResult {
  const byGame = new Map<string, Leg[]>();
  for (const leg of legs) {
    const cur = byGame.get(leg.game_id);
    if (cur === undefined) byGame.set(leg.game_id, [leg]);
    else cur.push(leg);
  }
  const ids = [...byGame.keys()].sort((x, y) => (x < y ? -1 : x > y ? 1 : 0));
  const parts = ids.map((id) => {
    const game = games[id];
    if (game === undefined) throw new Error(`game ${id} is not in the payload`);
    return evaluateGameLegs(byGame.get(id) as Leg[], game, shared, script);
  });
  if (parts.length === 1) return parts[0] as ComboResult;
  const p = prod(parts.map((r) => r.p_joint));
  const pInd = prod(parts.map((r) => r.p_independent));
  const rel2 = parts.reduce((s, r) => s + (r.p_joint > 0 ? (r.se / r.p_joint) ** 2 : 0), 0);
  const marg = parts.flatMap((r) => r.marginals);
  const plays = parts.map((r) => r.p_all_play);
  const play = plays.some((x) => x === null) ? null : prod(plays.map((x) => x as number));
  const method = parts.every((r) => r.method === "marginal") ? "product" : "product_of_games";
  const res = labelled({
    p_joint: p,
    p_independent: pInd,
    lift: pInd <= 0 ? null : p / pInd,
    marginals: marg,
    se: p * Math.sqrt(rel2),
    method,
    p_all_play: play,
    p_joint_incl_dnp: play === null ? null : play * p,
    repair_t: Math.max(...parts.map((r) => r.repair_t)),
    script,
    n_entities: parts.reduce((s, r) => s + r.n_entities, 0),
    label: null,
    range_2se: null,
    above_tested_range: [],
  });
  return { ...res, above_tested_range: parts.flatMap((r) => r.above_tested_range) };
}

// ---------------------------------------------------------------- script priors

export function scriptDefinitions(game: ComboGame, shared: ComboShared): Record<string, { rule: string; min_total?: number; max_total?: number }> {
  const d = shared.scripts.definitions;
  const muT = game.game.mu_t;
  const minT = Math.ceil(muT + d.total_delta);
  const maxT = Math.floor(muT - d.total_delta);
  return {
    blowout: { rule: `|final margin| >= ${d.blowout_abs_margin.toString()}` },
    close: { rule: `|final margin| <= ${d.close_abs_margin.toString()}` },
    shootout: { rule: `final total >= ${minT.toString()}`, min_total: minT },
    grind: { rule: `final total <= ${maxT.toString()}`, max_total: maxT },
  };
}

/** P(script) from the game's margin and total normals, integer-support corrected (a margin of 15 is M >= 14.5). */
export function scriptPrior(script: string, game: ComboGame, shared: ComboShared): number {
  const { mu_m: muM, sd_m: sdM, mu_t: muT, sd_t: sdT } = game.game;
  const d = shared.scripts.definitions;
  if (script === "blowout") {
    const t = d.blowout_abs_margin - 0.5;
    return normCdf((-t - muM) / sdM) + (1 - normCdf((t - muM) / sdM));
  }
  if (script === "close") {
    const t = d.close_abs_margin + 0.5;
    return normCdf((t - muM) / sdM) - normCdf((-t - muM) / sdM);
  }
  if (script === "shootout") return 1 - normCdf((Math.ceil(muT + d.total_delta) - 0.5 - muT) / sdT);
  if (script === "grind") return normCdf((Math.floor(muT - d.total_delta) + 0.5 - muT) / sdT);
  throw new Error(`unknown script ${script}`);
}

// ---------------------------------------------------------------- framing 1: game-script lift

const REASONS: Record<string, string> = {
  "pts|reb": "same player: points and rebounds both rise and fall with minutes played",
  "ast|pts": "same player: points and assists both rise and fall with minutes and usage",
  "fg3m|pts": "same player: threes are points, so a hot or cold shooting night moves both",
  "ast|reb": "same player: assists and rebounds both rise and fall with minutes",
  "ast|fg3m": "same player: minutes and touches move both",
  "fg3m|reb": "same player: both need minutes on the floor",
};

const signed = (x: number): string => `${x < 0 ? "-" : "+"}${Math.abs(x).toFixed(2)}`;

export function pairReason(e1: { player_id: number; stat: string }, e2: { player_id: number; stat: string }, rho: number): string {
  if (e1.player_id === e2.player_id) {
    const [x, y] = e1.stat <= e2.stat ? [e1.stat, e2.stat] : [e2.stat, e1.stat];
    const base = REASONS[`${x}|${y}`] ?? "same player: shared minutes";
    return `${base} (latent r = ${signed(rho)})`;
  }
  if (Math.abs(rho) < 0.05) return `different players: no reliable link (latent r = ${signed(rho)}), treated as near independent`;
  return `different players: weak link through game pace and margin (latent r = ${signed(rho)})`;
}

/** True when a player leg lies above the tested range: calibrated P > 0.75, or at the posted main line with raw P >= 0.80. */
export function legAboveTestedRange(leg: Leg, game: ComboGame): boolean {
  if (leg.kind !== "player") return false;
  const grid = gridOf(game, leg.player_id, leg.stat);
  const [lo, hi] = lineToBounds(leg.line, leg.side);
  if (intervalProb(grid, lo, hi) > TESTED_P_MAX) return true;
  const ml = grid.main_line;
  const raw = grid.raw_at_main;
  if (ml !== null && ml !== undefined && raw !== null && raw !== undefined && Math.abs(leg.line - ml) < 1e-9) {
    const rawLeg = leg.side === "over" ? raw : 1 - raw;
    return rawLeg >= TESTED_RAW_MAX;
  }
  return false;
}

/** Python's repr of the leg dicts, for the reference's tie-break on `str(legs)` (keys in insertion order). */
export function pyRepr(x: unknown): string {
  if (Array.isArray(x)) return `[${x.map(pyRepr).join(", ")}]`;
  if (typeof x === "string") return `'${x}'`;
  if (typeof x === "number") return Number.isInteger(x) ? x.toString() : x.toString();
  if (typeof x === "object" && x !== null) {
    return `{${Object.entries(x as Record<string, unknown>)
      .map(([k, v]) => `'${k}': ${pyRepr(v)}`)
      .join(", ")}}`;
  }
  return String(x);
}

const cmpStr = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/** A player's stat grids in the fixed order pts, reb, ast, fg3m (JSON key order is not meaningful: the reference's order is this one). */
export function statEntries(p: ComboPlayer): [string, StatGrid][] {
  const out: [string, StatGrid][] = [];
  for (const s of STAT_ORDER) {
    const g = p.stats[s];
    if (g !== undefined) out.push([s, g]);
  }
  return out;
}

export function mainLineLegs(game: ComboGame, side: "over" | "under"): PlayerLeg[] {
  const out: PlayerLeg[] = [];
  for (const p of game.players) {
    for (const [stat, grid] of statEntries(p)) {
      const ml = grid.main_line;
      if (ml === null) continue;
      out.push({ kind: "player", game_id: game.game_id, player_id: p.player_id, stat, line: ml, side });
    }
  }
  return out;
}

function* combinations<T>(xs: readonly T[], size: number): Generator<T[]> {
  const n = xs.length;
  if (size > n) return;
  const idx = Array.from({ length: size }, (_, i) => i);
  for (;;) {
    yield idx.map((i) => xs[i] as T);
    let i = size - 1;
    while (i >= 0 && idx[i] === i + n - size) i--;
    if (i < 0) return;
    idx[i] = (idx[i] as number) + 1;
    for (let j = i + 1; j < size; j++) idx[j] = (idx[j - 1] as number) + 1;
  }
}

function hasSamePlayerPair(combo: readonly PlayerLeg[]): boolean {
  return new Set(combo.map((c) => c.player_id)).size < combo.length;
}

function strongestPair(combo: readonly PlayerLeg[], game: ComboGame, table: readonly CorrRow[]): [Entity, Entity, number] {
  let best: [Entity, Entity, number] | null = null;
  for (const [c1, c2] of combinations(combo, 2) as Generator<[PlayerLeg, PlayerLeg]>) {
    const e1: Entity = { kind: "player", player_id: c1.player_id, stat: c1.stat };
    const e2: Entity = { kind: "player", player_id: c2.player_id, stat: c2.stat };
    const r = pairRho(e1, e2, game, table);
    if (best === null || Math.abs(r) > Math.abs(best[2])) best = [e1, e2, r];
  }
  if (best === null) throw new Error("no pair");
  return best;
}

/** Framing 1. Legs at posted MAIN lines (all overs or all unders), pairs of anything and triples holding a same-player pair, ranked by lift. */
export function liftRanking(game: ComboGame, shared: ComboShared, topN = 10, maxLegs = 3, minPJoint = 0.02): RankedCombo[] {
  const rows: { legs: PlayerLeg[]; side: "over" | "under"; lift: number; p_joint: number }[] = [];
  const table = shared.corr;
  for (const side of ["over", "under"] as const) {
    const legs = mainLineLegs(game, side);
    for (let size = 2; size <= maxLegs; size++) {
      for (const combo of combinations(legs, size)) {
        const ids = new Set(combo.map((c) => `${c.player_id.toString()}|${c.stat}`));
        if (ids.size < size) continue;
        if (size >= 3 && !hasSamePlayerPair(combo)) continue;
        const res = evaluateGameLegs(combo, game, shared, null, SCREEN_QMC);
        if (res.lift === null || res.p_joint < minPJoint) continue;
        rows.push({ legs: combo.map((c) => ({ ...c })), side, lift: res.lift, p_joint: res.p_joint });
      }
    }
  }
  rows.sort((a, b) => b.lift - a.lift || b.p_joint - a.p_joint || cmpStr(pyRepr(a.legs), pyRepr(b.legs)));
  const out: RankedCombo[] = [];
  for (const r of rows.slice(0, Math.max(3 * topN, topN))) {
    const res = evaluateGameLegs(r.legs, game, shared);
    if (res.lift === null || res.p_joint < minPJoint) continue;
    const [pe1, pe2, prho] = strongestPair(r.legs, game, table);
    out.push({
      legs: r.legs,
      side: r.side,
      p_joint: res.p_joint,
      p_independent: res.p_independent,
      lift: res.lift,
      se: res.se,
      method: res.method,
      reason: pairReason(pe1, pe2, prho),
      label: res.label,
      range_2se: res.range_2se,
      above_tested_range: r.legs.some((c) => legAboveTestedRange(c, game)),
    });
  }
  out.sort((a, b) => b.lift - a.lift || b.p_joint - a.p_joint || cmpStr(pyRepr(a.legs), pyRepr(b.legs)));
  return out.slice(0, topN);
}

// ---------------------------------------------------------------- framing 2: target probability

/** Over legs on the integer grid whose marginal lies in [pMin, pMax]; `main_line` floor mode also needs line >= posted main line. */
export function candidateLegs(game: ComboGame, pMin = TARGET_P_MIN, pMax = TARGET_P_MAX, floorMode = "p_band"): PlayerLeg[] {
  const out: PlayerLeg[] = [];
  for (const p of game.players) {
    for (const [stat, grid] of statEntries(p)) {
      const ml = grid.main_line;
      const n = grid.surv.length;
      for (let k = Math.max(1, grid.k0); k < grid.k0 + n; k++) {
        const s = survAt(grid, k);
        if (!(pMin <= s && s <= pMax)) continue;
        const line = k - 0.5;
        if (floorMode === "main_line" && (ml === null || line < ml)) continue;
        const leg: PlayerLeg = { kind: "player", game_id: game.game_id, player_id: p.player_id, stat, line, side: "over", p: s };
        if (legAboveTestedRange(leg, game)) continue; // labelled and excluded, never capped
        out.push(leg);
      }
    }
  }
  return out;
}

type Scored = [PlayerLeg[], number, number];

function search(game: ComboGame, shared: ComboShared, cands: readonly PlayerLeg[], kLegs: number, target: number, beam: number, prune: boolean): Scored[] {
  let frontier: PlayerLeg[][] = [[]];
  let scored: Scored[] = [];
  for (let level = 1; level <= kLegs; level++) {
    const nxt = new Map<string, Scored>();
    for (const combo of frontier) {
      const used = new Set(combo.map((c) => `${c.player_id.toString()}|${c.stat}`));
      for (const c of cands) {
        if (used.has(`${c.player_id.toString()}|${c.stat}`)) continue;
        const neu = [...combo, c];
        const key = neu
          .map((x) => [x.player_id, x.stat, x.line] as const)
          .sort((p, q) => p[0] - q[0] || cmpStr(p[1], q[1]) || p[2] - q[2])
          .map((t) => t.join("|"))
          .join(";");
        if (nxt.has(key)) continue;
        const res = evaluateGameLegs(neu, game, shared, null, SCREEN_QMC, SCREEN_BIV_N);
        if (prune && res.p_joint < target) continue;
        nxt.set(key, [neu, res.lift ?? 0, res.p_joint]);
      }
    }
    scored = [...nxt.values()].sort((a, b) => b[1] - a[1] || b[2] - a[2] || cmpStr(pyRepr(a[0]), pyRepr(b[0])));
    const keep = level === 1 ? scored : scored.slice(0, Math.max(beam, 1));
    frontier = keep.map((t) => t[0]);
    if (frontier.length === 0) return [];
  }
  return scored;
}

/**
 * Framing 2. OBJECTIVE (explicit): among combos of `kLegs` over legs on distinct (player, stat) entities of one game, each
 * leg's marginal in [pMin, pMax] (legs above the tested range are excluded), with p_joint >= target, MAXIMISE lift; ties by
 * the smaller p_joint - target. k = 2 is exhaustive; k >= 3 prunes and beam-searches (a model estimate). When no combo
 * reaches the target the answer says so (`feasible: false`) and returns the highest joint found.
 */
export function targetSearch(
  game: ComboGame,
  shared: ComboShared,
  target: number,
  kLegs: number,
  topN = 5,
  beam = 40,
  floorMode = "p_band",
  pMin = TARGET_P_MIN,
  pMax = TARGET_P_MAX,
): TargetResult {
  const cands = candidateLegs(game, pMin, pMax, floorMode);
  let scored = search(game, shared, cands, kLegs, target, beam, true);
  const feasible = scored.length > 0;
  if (!feasible) {
    scored = search(game, shared, cands, kLegs, target, beam, false).sort((a, b) => b[2] - a[2] || cmpStr(pyRepr(a[0]), pyRepr(b[0])));
  }
  const final: TargetCombo[] = [];
  for (const [combo] of scored.slice(0, Math.max(3 * topN, topN))) {
    const res = evaluateGameLegs(combo, game, shared);
    final.push({
      legs: combo.map((c) => {
        const { p: _p, ...rest } = c;
        void _p;
        return rest;
      }),
      p_joint: res.p_joint,
      p_independent: res.p_independent,
      lift: res.lift,
      se: res.se,
      method: res.method,
      label: res.label,
      range_2se: res.range_2se,
    });
  }
  const meta = {
    target,
    k_legs: kLegs,
    band: [pMin, pMax] as [number, number],
    floor_mode: floorMode,
    objective: "max lift s.t. p_joint >= target, marginals in band, above-range legs excluded",
  };
  const ok = final.filter((r) => r.p_joint >= target);
  ok.sort((a, b) => (b.lift ?? 0) - (a.lift ?? 0) || a.p_joint - target - (b.p_joint - target) || cmpStr(pyRepr(a.legs), pyRepr(b.legs)));
  if (ok.length > 0) return { ...meta, feasible: true, combos: ok.slice(0, topN) };
  final.sort((a, b) => b.p_joint - a.p_joint || cmpStr(pyRepr(a.legs), pyRepr(b.legs)));
  return { ...meta, feasible: false, combos: final.slice(0, topN) };
}
