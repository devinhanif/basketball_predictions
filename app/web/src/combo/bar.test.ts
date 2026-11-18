import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { BAR_MAX, BAR_MIN, barLabel } from "./Bar";
import { histCompact, readHist } from "./script";

const read = (f: string): string => readFileSync(join(import.meta.dirname, f), "utf8");

describe("the square bar", () => {
  it("runs on a fixed 30% to 70% scale", () => {
    expect([BAR_MIN, BAR_MAX]).toEqual([0.3, 0.7]);
  });
  it("its label carries both numbers, the signed move in words and says when a value is off the scale", () => {
    const a = barLabel(0.525, 0.587, "it happens");
    expect(a.text).toContain("52.5 percent");
    expect(a.text).toContain("58.7 percent");
    expect(a.text).toContain("up 6.2 points");
    expect(a.clamped).toBe(false);
    const b = barLabel(0.72, 0.65, "it happens");
    expect(b.clamped).toBe(true);
    expect(b.text).toContain("drawn at the edge");
    expect(b.text).toContain("down 7.0 points");
  });
  it("has square ends, no gradients, no rings and no glow in its CSS, and is never colour alone (the change is printed)", () => {
    const css = read("combo.css");
    const section = css.slice(css.indexOf("/* ---- the square bar ---- */"), css.indexOf("/* ---- Analyst"));
    expect(section.length).toBeGreaterThan(200);
    expect(section).not.toMatch(/border-radius|gradient|box-shadow|outline|filter|blur/);
    const row = read("ComboScriptModel.tsx");
    expect(row).toContain("cb-delta");
    expect(row).toMatch(/sign\b/);
  });
});

describe("terminal counts and photos", () => {
  it("writes the compact count as 'blow 2/4  all 6/12' for the side", () => {
    const h = readHist({ n_all: 12, k_all: 6, blowout: [6, 2] });
    expect(histCompact(h, "blowout", "over")).toBe("blow 2/6  all 6/12");
    expect(histCompact(h, "blowout", "under")).toBe("blow 4/6  all 6/12");
    // n under 5 adds "few" (Devin's rule: say "few games" when n < 5)
    expect(histCompact(readHist({ n_all: 12, k_all: 6, blowout: [4, 2] }), "blowout", "over")).toBe("blow 2/4 few  all 6/12");
    expect(histCompact(readHist({ n_all: 12, k_all: 7, blowout: [3, 2] }), "blowout", "over")).toBe("blow 2/3 few  all 7/12");
    expect(histCompact(readHist({ n_all: 12, k_all: 7, blowout: [0, 0] }), "blowout", "over")).toBe("blow none yet  all 7/12");
    expect(histCompact(null, "blowout", "over")).toBeNull();
  });
  it("every screen that names a player uses the app's Avatar, with the remote headshot and its fallback, and no image file is copied", () => {
    for (const f of ["ComboScriptModel.tsx", "ComboScriptHistory.tsx", "Bits.tsx", "ComboBuild.tsx"]) expect(read(f)).toContain('from "../Avatar"');
    expect(read("Bits.tsx")).toMatch(/<Avatar [^>]*remote/);
    expect(read("ComboLift.tsx")).toContain("<Faces");
    expect(read("ComboTarget.tsx")).toContain("<Faces");
    expect(read("combo.css")).not.toMatch(/url\(/);
  });
  it("Analyst is a terminal (monospace, square corners, no glow) and Simple keeps larger type and photos", () => {
    const css = read("combo.css");
    expect(css).toMatch(/\.cb-term \{[^}]*font-family: var\(--mono\)/);
    expect(css).toMatch(/\.cb-simple\.cb-term \{\s*font-size: var\(--fs-lg\)/);
    expect(css).toMatch(/\.cb-term \.btn,[\s\S]*?border-radius: 0;[\s\S]*?box-shadow: none/);
    expect(css).toMatch(/\.cb-simple \.cb-row \.cb-av \.avatar \{\s*width: 56px;\s*height: 68px/);
    expect(read("../pages/Combo.tsx")).toContain("cb-term");
  });
});
