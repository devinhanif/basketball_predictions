// Devin, 2026-11-10: "only show the calibrated number to me."
// Raw P(over) is never rendered where a calibrated one exists; the 2026 prop calibration is not shown.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { calibratedLine, read, type SliderInput } from "./slider";
import { sourceWord } from "./words";
import { LOCK_NOTE, MODEL_LOCK_FROM, isLockedNight } from "./record";
import type { LineCal, RungCal } from "./types";

const SRC = __dirname;
const files = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? files(join(dir, e.name)) : /\.tsx$/.test(e.name) ? [join(dir, e.name)] : [],
  );

describe("no screen renders a raw chance", () => {
  it("no component reads the raw fields or prints a raw label", () => {
    for (const f of files(SRC)) {
      const text = readFileSync(f, "utf8");
      expect(text, f).not.toMatch(/\bp_raw\b|\bp_over_raw\b|p_joint_raw|\.raw\b|Model, raw|Raw chance|Before adjusting|ls-raw/);
    }
  });

  it("the calibration tab renders the published record, not computed bins, for props", () => {
    const text = readFileSync(join(SRC, "pages/Record.tsx"), "utf8");
    expect(text).toContain("PublishedProps");
    expect(text).toContain("this_season");
    expect(text).not.toMatch(/c\.props\.bins|props\.sources|SeriesView s=\{c\.props\}/);
  });
});

const PGE = [1, 0.95, 0.8, 0.65, 0.5, 0.35, 0.2, 0.1, 0.05, 0.02, 0.01];
const LC: LineCal = {
  book: "draftkings",
  main_line: 3.5,
  price_at: null,
  p_over_cal: 0.43,
  map: "LINE_CALIBRATION",
  map_sha8: "443743a1",
  fit_through: null,
};
const RUNG: RungCal = {
  book: "draftkings",
  main_line: 3.5,
  price_at: null,
  map_version: "rung_cal_map_v1",
  map_sha8: "116f4480",
  lines: [{ line: 2.5, p_over: 0.6 }],
};
const input: SliderInput = {
  p_ge: PGE,
  method: "exact",
  posted_line: 3.5,
  book_main_line: 3.5,
  rung_cal: RUNG,
  line_cal: LC,
};

describe("what the number on screen is", () => {
  it("at the main line it is line_cal, and the raw value is not in any printed text", () => {
    const r = read(input, 3.5);
    expect(r.source).toBe("line_cal");
    expect(r.p_over).toBe(0.43);
    const printed = [calibratedLine(r), r.plain, r.label].join(" ");
    expect(printed).not.toContain("50%");
  });
  it("at a listed rung it is rung_cal; at another line of a calibrated prop no percentage is given", () => {
    expect(read(input, 2.5).p_over).toBe(0.6);
    const m = read(input, 6.5);
    expect(m.source).toBe("model");
    expect(m.no_cal_here).toBe(true);
    expect(m.p_over).toBeNull();
    expect(m.p_under).toBeNull();
    expect(m.plain).toBe("No calibrated number at this line.");
    expect(m.p_cal).toBeNull();
    expect(calibratedLine(m)).toBeNull();
  });
  it("a prop with no calibrated number anywhere keeps its model-only chances, labelled", () => {
    const none: SliderInput = { ...input, line_cal: null, rung_cal: null };
    const m = read(none, 6.5);
    expect(m.no_cal_here).toBe(false);
    expect(m.p_over).toBeCloseTo(PGE[7] ?? 0, 12);
    expect(m.plain).toMatch(/Model only/);
  });
  it("no ladder of lines mixes calibrated and model-only percentages", () => {
    const lines = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5];
    for (const inp of [input, { ...input, line_cal: null }, { ...input, rung_cal: null }]) {
      const rs = lines.map((l) => read(inp, l));
      const shown = rs.filter((r) => r.p_over !== null);
      // every percentage on a calibrated prop is a calibrated one
      expect(shown.every((r) => r.source !== "model")).toBe(true);
      // and the others carry the plain "no calibrated number" line, not a percentage
      expect(rs.filter((r) => r.p_over === null).every((r) => r.no_cal_here && r.plain === "No calibrated number at this line.")).toBe(true);
    }
    // a prop with no calibrated number at all shows only model-only percentages, and they are monotone
    const none = lines.map((l) => read({ ...input, line_cal: null, rung_cal: null }, l));
    expect(none.every((r) => r.p_over !== null && r.source === "model")).toBe(true);
    const ps = none.map((r) => r.p_over ?? 0);
    expect(ps.every((p, i) => i === 0 || p <= (ps[i - 1] ?? 1))).toBe(true);
  });
  it("the source word is calibrated or model only", () => {
    expect(sourceWord("line_cal")).toBe("calibrated");
    expect(sourceWord("rung_cal")).toBe("calibrated");
    expect(sourceWord("model")).toBe("model only");
  });
});

describe("the locked test window (LINE_CAL_LIVE, until about Dec 1)", () => {
  it("nights from 2026-10-30 are locked, earlier ones are not", () => {
    expect(MODEL_LOCK_FROM).toBe("2026-10-30");
    expect(isLockedNight("2026-10-29")).toBe(false);
    expect(isLockedNight("2024-11-05")).toBe(false);
    expect(isLockedNight("2026-10-30")).toBe(true);
    expect(isLockedNight("2026-11-09")).toBe(true);
    expect(LOCK_NOTE).toBe("The model's number for this night is part of a locked test until about Dec 1.");
  });
  it("the track record page states no lean or model call and keeps no model call in a round", () => {
    const text = readFileSync(join(SRC, "pages/Record.tsx"), "utf8");
    expect(text).not.toMatch(/we leaned|so it said|model_call|model_right|It was right|It was wrong/);
    expect(readFileSync(join(SRC, "record.ts"), "utf8")).not.toMatch(/model_call|model_right/);
    expect(text).toContain("LOCK_NOTE");
    expect(text).toContain("isLockedNight");
  });
});
