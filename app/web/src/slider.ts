// Pure maths and honesty labels for the line slider. Nothing here fetches or draws.
//
// The stored forecast is a distribution over whole numbers, held as p_ge[k] = P(Y >= k) for
// k = 0, 1, 2, ... (p_ge[0] = 1). A half-point line k + 0.5 therefore has
// P(over) = P(Y > k + 0.5) = P(Y >= k + 1) = p_ge[k + 1], and P(under) = 1 - P(over).
// No expected value, no price maths and no advice word belongs in this file.

import type { RungCal } from "./types";

export const MODEL_ONLY =
  "model only, not calibrated off the main line (alt-line test T494)";
export const TAIL_NOTE = "tail: low confidence";

export const PLAIN_MODEL_ONLY = "Model only: straight from the model, not adjusted.";
export const PLAIN_CALIBRATED = "Calibrated: adjusted using how past lines like this one actually finished.";

export function calibratedLabel(version: string): string {
  return `calibrated using the book's main line (T494), map ${version}`;
}

/** P(Y >= k) from the stored grid: certain at k <= 0, zero beyond the table. */
export function pGe(pge: readonly number[], k: number): number {
  if (k <= 0) return 1;
  return k < pge.length ? (pge[k] ?? 0) : 0;
}

/** Snap any number to the half-point grid (23.5, 24.5, ...). */
export function snapHalf(x: number): number {
  return Math.round(x - 0.5) + 0.5;
}

export function isHalfPoint(line: number): boolean {
  return Math.abs(line - Math.floor(line) - 0.5) < 1e-9;
}

/** Raw model P(over) at a half-point line. */
export function pOver(pge: readonly number[], line: number): number {
  if (!isHalfPoint(line)) throw new Error(`not a half-point line: ${line.toString()}`);
  return pGe(pge, Math.floor(line) + 1);
}

/** Raw model P(under) at a half-point line: no push is possible, so it is 1 - P(over). */
export function pUnder(pge: readonly number[], line: number): number {
  return 1 - pOver(pge, line);
}

/** Smallest whole count k whose cumulative probability P(Y <= k) reaches p. */
export function quantile(pge: readonly number[], p: number): number {
  for (let k = 0; k < pge.length; k++) {
    if (1 - pGe(pge, k + 1) >= p - 1e-12) return k;
  }
  return Math.max(0, pge.length - 1);
}

/** True when a half-point line sits outside the model's 10th to 90th percentile. */
export function isTail(pge: readonly number[], line: number): boolean {
  return line < quantile(pge, 0.1) || line > quantile(pge, 0.9);
}

/** The half-point line where the model is closest to 50/50 (lower line on a tie). */
export function modelLine(pge: readonly number[]): number {
  let best = 0.5;
  let gap = Infinity;
  for (let k = 0; k < pge.length; k++) {
    const g = Math.abs(pOver(pge, k + 0.5) - 0.5);
    if (g < gap - 1e-12) {
      gap = g;
      best = k + 0.5;
    }
  }
  return best;
}

export interface Reading {
  line: number;
  p_over: number;
  p_under: number;
  source: "rung_cal" | "model";
  /** The exact stored-source sentence (test ids, map version). Shown in the details, not in front. */
  label: string;
  /** The honesty label in plain words, which sits beside the number: "Calibrated: ..." or "Model only: ...". */
  plain: string;
  tail: boolean;
  tail_note: string | null;
  /** The line is the posted main line (the only line with a stored price). */
  is_posted: boolean;
  is_book_main: boolean;
  is_model_line: boolean;
}

export interface SliderInput {
  p_ge: readonly number[];
  method: "exact" | "quantile";
  posted_line: number | null;
  book_main_line: number | null;
  rung_cal: RungCal | null;
}

/** What to print at a line. A rung_cal value is used only when that exact line is listed. */
export function read(input: SliderInput, line: number): Reading {
  const rung = input.rung_cal?.lines.find((r) => Math.abs(r.line - line) < 1e-9);
  const raw = pOver(input.p_ge, line);
  const calibrated = rung !== undefined;
  const p_over = rung !== undefined ? rung.p_over : raw;
  const tail = isTail(input.p_ge, line);
  const approx = input.method === "quantile" ? " Read off a 19-point quantile grid, so rougher." : "";
  const base =
    input.rung_cal !== null && calibrated ? calibratedLabel(input.rung_cal.map_version) : MODEL_ONLY;
  return {
    line,
    p_over,
    p_under: 1 - p_over,
    source: calibrated ? "rung_cal" : "model",
    label: base + (calibrated ? "" : approx),
    plain: calibrated ? PLAIN_CALIBRATED : PLAIN_MODEL_ONLY + (input.method === "quantile" ? " A rougher estimate." : ""),
    tail,
    tail_note: tail ? TAIL_NOTE : null,
    is_posted: input.posted_line !== null && Math.abs(line - input.posted_line) < 1e-9,
    is_book_main: input.book_main_line !== null && Math.abs(line - input.book_main_line) < 1e-9,
    is_model_line: Math.abs(line - modelLine(input.p_ge)) < 1e-9,
  };
}

/** The slider's reach: the grid's own range, widened to hold the posted and book lines. */
export function sliderRange(
  line_min: number,
  line_max: number,
  marks: readonly (number | null)[],
): { min: number; max: number } {
  let min = snapHalf(line_min);
  let max = snapHalf(line_max);
  for (const m of marks) {
    if (m === null) continue;
    min = Math.min(min, snapHalf(m));
    max = Math.max(max, snapHalf(m));
  }
  return { min: Math.max(0.5, min), max: Math.max(min, max) };
}

/** Position of a value along a track, 0 to 100. */
export function percentAlong(value: number, min: number, max: number): number {
  return max === min ? 0 : Math.min(100, Math.max(0, ((value - min) / (max - min)) * 100));
}
