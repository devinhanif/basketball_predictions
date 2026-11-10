// Pure maths for the Explore lens. Nothing here fetches or draws.
//
// The stored forecast is a distribution over whole counts, p_ge[k] = P(Y >= k). Two what-ifs move it.
//
// Minutes. "If he plays M minutes" rescales the count by s = M / baseline, keeping his per-minute rate fixed:
//   Y' = s * Y. This is an approximation and every screen says so: the rate is not really fixed (a starter's rate
//   differs from a bench player's) and the forecast already holds the usual wobble in minutes. Optionally the
//   spread is also narrowed by the share of variance minutes explain (Y'' = m' + c (Y' - m'), c = sqrt(1 - share)),
//   which is what "minutes known exactly" would leave. Both are one affine map a*Y + b, so one function does both.
//
// Price and fees. Break-even is the price of one $1 contract plus its fee; EV per $1 spent is q / cost - 1 for a
// chance q. Arithmetic on numbers the visitor types, not a forecast and not a recommendation.

import { devigOver } from "./stats";
import { pGe, quantile } from "./slider";

/** P(a*Y + b >= k) for each whole count k, from P(Y >= k). Needs a > 0. Result starts at 1 and ends at 0. */
export function affineCounts(pge: readonly number[], a: number, b: number): number[] {
  if (!(a > 0)) return [1, 0];
  const top = Math.min(300, Math.ceil(a * (pge.length - 1) + Math.max(0, b)) + 2);
  const out: number[] = [1];
  for (let k = 1; k <= top; k++) {
    // a*Y + b > k - 0.5  <=>  Y > (k - 0.5 - b) / a  <=>  Y >= floor((k - 0.5 - b) / a) + 1
    out.push(pGe(pge, Math.floor((k - 0.5 - b) / a + 1e-12) + 1));
  }
  while (out.length > 2 && (out[out.length - 1] ?? 0) < 1e-9) out.pop();
  out.push(0);
  return out;
}

/** The minutes what-if: scale by m / baseline; with `known`, also narrow the spread by the minutes share. */
export function minutesWhatIf(
  pge: readonly number[],
  baseline: number,
  m: number,
  known: { share: number; mean: number } | null,
): number[] {
  const s = baseline > 0 ? m / baseline : 1;
  if (known === null) return affineCounts(pge, s, 0);
  const c = Math.sqrt(Math.max(0, 1 - known.share));
  const mid = s * known.mean;
  return affineCounts(pge, s * c, mid * (1 - c));
}

/** Mean and standard deviation of a count from P(Y >= k). */
export function momentsOf(pge: readonly number[]): { mean: number; sd: number } {
  let ey = 0;
  let ey2 = 0;
  for (let k = 1; k < pge.length; k++) {
    const p = pge[k] ?? 0;
    ey += p;
    ey2 += (2 * k - 1) * p;
  }
  return { mean: ey, sd: Math.sqrt(Math.max(0, ey2 - ey * ey)) };
}

/** The raw model chance of the over at a half-point line. */
export function overAt(pge: readonly number[], line: number): number {
  return pGe(pge, Math.floor(line) + 1);
}

/** The model's 80% range: the 10th and 90th percentile counts, as the distribution strip shades them. */
export function range80(pge: readonly number[]): [number, number] {
  return [quantile(pge, 0.1), quantile(pge, 0.9)];
}

/* ------------------------------------------------------------------ price and fees */

/** The chance the price implies (margin not removed), or null when it is not an American price. */
export function impliedFromAmerican(odds: number): number | null {
  if (!Number.isFinite(odds) || Math.abs(odds) < 100) return null;
  return odds > 0 ? 100 / (odds + 100) : -odds / (-odds + 100);
}

export type FeeKind = "none" | "schedule" | "custom";

/**
 * Fee for one $1-payout contract bought at price `c` (0..1), in dollars. "schedule" is the stored exchange formula
 * coefficient * c * (1 - c) (not rounded up to the cent, so a shade low); "custom" is cents the visitor types.
 */
export function feeFor(kind: FeeKind, c: number, coefficient: number | null, customCents: number): number {
  if (kind === "schedule") return coefficient === null ? 0 : coefficient * c * (1 - c);
  if (kind === "custom") return Math.max(0, customCents) / 100;
  return 0;
}

export interface PriceRead {
  /** What the price alone says, no fee. */
  implied: number;
  fee: number;
  /** Cost of one $1-payout contract with the fee: the chance you need for a break-even bet. */
  breakEven: number;
  /** EV per $1 spent if the model's chance q were exactly right. */
  ev: number;
  /** The model's chance minus the break-even chance, in percentage points as a fraction. */
  gap: number;
}

export function priceRead(q: number, odds: number, kind: FeeKind, coefficient: number | null, customCents: number): PriceRead | null {
  const implied = impliedFromAmerican(odds);
  if (implied === null) return null;
  const fee = feeFor(kind, implied, coefficient, customCents);
  const breakEven = implied + fee;
  return { implied, fee, breakEven, ev: q / breakEven - 1, gap: q - breakEven };
}

/** The chance the book itself implies after removing its margin, from both sides' prices. */
export function bookChance(overOdds: number, underOdds: number): number | null {
  if (impliedFromAmerican(overOdds) === null || impliedFromAmerican(underOdds) === null) return null;
  return devigOver(overOdds, underOdds);
}

/** Parses what a visitor typed as American odds ("-110", "+150", "150"); null when it is not a price. */
export function parseOdds(text: string): number | null {
  const t = text.trim().replace(/^\+/, "");
  if (!/^-?\d{3,5}$/.test(t)) return null;
  const n = Number(t);
  return Math.abs(n) >= 100 ? n : null;
}
