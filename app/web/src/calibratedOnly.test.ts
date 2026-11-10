// Devin, 2026-11-10: "only show the calibrated number to me."
// Raw P(over) is never rendered where a calibrated one exists; the 2026 prop calibration is not shown.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { calibratedLine, read, type SliderInput } from "./slider";
import { sourceWord } from "./words";
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
  p_over_raw: 0.5,
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
  it("at a listed rung it is rung_cal; elsewhere the model's own number, labelled model only", () => {
    expect(read(input, 2.5).p_over).toBe(0.6);
    const m = read(input, 6.5);
    expect(m.source).toBe("model");
    expect(m.plain).toMatch(/Model only/);
    expect(m.p_cal).toBeNull();
    expect(calibratedLine(m)).toBeNull();
  });
  it("the source word is calibrated or model only", () => {
    expect(sourceWord("line_cal")).toBe("calibrated");
    expect(sourceWord("rung_cal")).toBe("calibrated");
    expect(sourceWord("model")).toBe("model only");
  });
});
