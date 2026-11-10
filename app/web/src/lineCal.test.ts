import { describe, expect, it } from "vitest";
import {
  LINE_CAL_REASON_GENERIC,
  LINE_CAL_REASONS,
  calibrationFooter,
  lineCalReason,
  calibratedLine,
  read,
  type SliderInput,
} from "./slider";
import type { LineCal, RungCal } from "./types";

// Y = 0:5, 1:10, 2:20, 3:30, 4:20, 5:10, 6:5 out of 100, so P(over 2.5) = 0.65 and P(over 3.5) = 0.35.
const PGE = [1, 0.95, 0.85, 0.65, 0.35, 0.15, 0.05];
const LC: LineCal = {
  book: "draftkings",
  main_line: 2.5,
  price_at: "2024-11-12T22:55:00Z",
  p_over_cal: 0.58,
  map: "LINE_CALIBRATION",
  map_sha8: "443743a1",
  fit_through: "2025-06-30",
};
const RUNG: RungCal = {
  book: "draftkings",
  main_line: 2.5,
  price_at: null,
  map_version: "rung_cal_map_v1",
  map_sha8: "116f4480",
  lines: [
    { line: 1.5, p_over: 0.8 },
    { line: 2.5, p_over: 0.6 },
  ],
};
const input: SliderInput = {
  p_ge: PGE,
  method: "exact",
  posted_line: 2.5,
  book_main_line: 2.5,
  rung_cal: RUNG,
  line_cal: LC,
  line_cal_null_reason: null,
};

describe("line_cal at the book's main line", () => {
  it("uses p_over_cal for the calibrated number and carries no raw one", () => {
    const r = read(input, 2.5);
    expect(r.source).toBe("line_cal");
    expect(r.p_cal).toBe(0.58);
    expect(r.p_over).toBe(0.58);
    expect("p_raw" in r).toBe(false);
    expect(r.cal_note).toBeNull();
    expect(r.label).toContain("LINE_CALIBRATION (T493)");
    expect(r.label).toContain("443743a1");
    expect(r.is_book_main).toBe(true);
  });

  it("is distinguishable from the rung map in the wording, and never prints the raw number", () => {
    const cal = calibratedLine(read(input, 2.5));
    expect(cal).toBe(
      "Model, calibrated at the book's line: 58% (how often chances like this came in, DraftKings line, T493)",
    );
    expect(cal).not.toContain("65%");
    const rung = calibratedLine(read({ ...input, line_cal: null }, 2.5));
    expect(rung).toContain("Model, calibrated: 60%");
    expect(rung).not.toContain("T493");
    expect(rung).not.toContain("65%");
  });

  it("the under side is 1 - p_over_cal", () => {
    expect(read(input, 2.5).p_under).toBeCloseTo(0.42);
  });

  it("away from the main line the rung map is used, never line_cal", () => {
    const r = read(input, 1.5);
    expect(r.source).toBe("rung_cal");
    expect(r.p_cal).toBe(0.8);
    const m = read(input, 3.5);
    expect(m.source).toBe("model");
    expect(m.p_over).toBeNull();
    expect(m.p_cal).toBeNull();
  });

  it("does not apply line_cal at a line next to the main line", () => {
    expect(read({ ...input, rung_cal: null }, 3.5).source).toBe("model");
    expect(read({ ...input, rung_cal: null }, 1.5).p_cal).toBeNull();
  });
});

describe("line_cal absent or null", () => {
  it("an older export without either field behaves as before (rung map at the main line)", () => {
    const old: SliderInput = { p_ge: PGE, method: "exact", posted_line: 2.5, book_main_line: 2.5, rung_cal: RUNG };
    const r = read(old, 2.5);
    expect(r.source).toBe("rung_cal");
    expect(r.p_cal).toBe(0.6);
    const none = read({ ...old, rung_cal: null }, 2.5);
    expect(none.p_cal).toBeNull();
    expect(none.p_over).toBeCloseTo(0.65);
  });

  it.each(Object.keys(LINE_CAL_REASONS))("reason %s shows the model number alone with its sentence", (code) => {
    // no calibrated number anywhere on the prop (no rung map either): the model-only number, with the reason
    const r = read({ ...input, line_cal: null, rung_cal: null, line_cal_null_reason: code }, 2.5);
    expect(r.p_cal).toBeNull();
    expect(r.source).toBe("model");
    expect(r.p_over).toBeCloseTo(0.65);
    expect(r.cal_note).toBe(LINE_CAL_REASONS[code]);
    expect(calibratedLine(r)).toBeNull();
  });

  it("no_book_line is the plain sentence", () => {
    expect(lineCalReason("no_book_line")).toBe("No calibrated number: no DraftKings line was posted before tip.");
  });

  it.each([
    "grid_mismatch",
    "no_production_row",
    "no_production_grid",
    "no_survival",
    "no_starter_rate",
    "map_unavailable",
    "no_quartiles",
  ])("%s gets the could-not-be-reproduced sentence", (code) => {
    expect(lineCalReason(code)).toBe(LINE_CAL_REASON_GENERIC);
    expect(LINE_CAL_REASON_GENERIC).toContain("could not be reproduced for this row");
  });

  it("an unknown code reads as no reason, as the API treats it", () => {
    expect(lineCalReason("something_new")).toBeNull();
  });

  it("no reason code, no sentence", () => {
    expect(lineCalReason(null)).toBeNull();
    expect(lineCalReason(undefined)).toBeNull();
  });

  it("a null at the main line hides the rung number there, but away from it the rung map stays", () => {
    const n: SliderInput = { ...input, line_cal: null, line_cal_null_reason: "grid_mismatch" };
    expect(read(n, 2.5).p_cal).toBeNull();
    expect(read(n, 1.5).source).toBe("rung_cal");
    expect(read(n, 1.5).p_cal).toBe(0.8);
  });

  it("with no rung map, the reason explains every line", () => {
    const n: SliderInput = { ...input, rung_cal: null, line_cal: null, line_cal_null_reason: "no_book_line", book_main_line: null };
    expect(read(n, 1.5).cal_note).toBe(LINE_CAL_REASONS.no_book_line);
  });
});

describe("the calibration footer", () => {
  it("names both maps when both are present", () => {
    expect(calibrationFooter(LC, RUNG)).toBe(
      "Calibration: LINE_CALIBRATION 443743a1 at the book's line; rung map rung_cal_map_v1 elsewhere",
    );
  });
  it("names only the maps that are present", () => {
    expect(calibrationFooter(LC, null)).toBe("Calibration: LINE_CALIBRATION 443743a1 at the book's line");
    expect(calibrationFooter(null, RUNG)).toBe("Calibration: rung map rung_cal_map_v1 (DraftKings only)");
    expect(calibrationFooter(undefined, null)).toBeNull();
  });
});
