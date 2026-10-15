/** Wilson score interval for a proportion (95% by default). */
export function wilson(k: number, n: number, z = 1.96): [number, number] {
  if (n === 0) return [0, 1];
  const p = k / n;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const centre = (p + z2 / (2 * n)) / denom;
  const half = (z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / denom;
  return [Math.max(0, centre - half), Math.min(1, centre + half)];
}

/** Below this many legs a cut prints "not enough to say" (docs/APP_CONTENT_SPEC.md section 1.6). */
export const MIN_LEGS = 20;

function rawProb(price: number): number {
  return price > 0 ? 100 / (price + 100) : -price / (-price + 100);
}

/** The over chance implied by a two-sided American quote with the book's margin taken out (multiplicative; the API does the same). */
export function devigOver(overPrice: number, underPrice: number): number {
  const po = rawProb(overPrice);
  const pu = rawProb(underPrice);
  return po / (po + pu);
}
