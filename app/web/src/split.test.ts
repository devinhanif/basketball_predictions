// Leg.tsx and Record.tsx were split into sibling files (2026-11-17). Every check an existing test makes on the old file
// by name is repeated here on the pieces, so moving code did not move it out of a test's sight.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (f: string): string => readFileSync(join(__dirname, "pages", f), "utf8");

describe("split pages keep their guards", () => {
  it("the track record pieces state no lean or model call (as calibratedOnly.test.ts checks Record.tsx)", () => {
    for (const f of ["Record.tsx", "RecordGuess.tsx"]) {
      expect(read(f), f).not.toMatch(/we leaned|so it said|model_call|model_right|It was right|It was wrong/);
      expect(read(f), f).not.toMatch(/c\.props\.bins|props\.sources|SeriesView s=\{c\.props\}/);
    }
  });
  it("the guess game still says the lock wording when it reveals a locked night", () => {
    expect(read("RecordGuess.tsx")).toContain("LOCK_NOTE");
  });
  it("leg pieces never ask for a remote photo except the page header (as picks.test.ts checks Leg.tsx)", () => {
    expect(read("Leg.tsx")).toMatch(/<Avatar [^\n]* remote \/>/);
    for (const f of ["LegLine.tsx", "LegHistory.tsx", "LegContext.tsx", "RecordGuess.tsx"]) expect(read(f), f).not.toMatch(/<Avatar [^\n]*\bremote\b/);
  });
  it("routes.test.ts scans src/pages/*.tsx for links, so the pieces live there, not in a subfolder", () => {
    for (const f of ["LegLine.tsx", "LegHistory.tsx", "LegContext.tsx", "RecordGuess.tsx"]) expect(read(f).length).toBeGreaterThan(1000);
  });
});
