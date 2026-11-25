// The display lock on the script view (Devin, 2026-11-17, the strict reading of LINE_CAL_LIVE): the History tab shows no model chance,
// the Model tab shows no count, and the History order does not follow the model's ranking.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import night from "../../../fixtures/combo/combo_2024-11-12.json";
import { SEPARATE_HISTORY, histCompact, historyText, historyView, modelRowText, scriptView } from "./script";
import type { ComboPayload } from "./types";

const P = night as unknown as ComboPayload;
const G = P.games[0];
if (G === undefined) throw new Error("fixture has no game");
const read = (f: string): string => readFileSync(join(import.meta.dirname, f), "utf8");
const code = (f: string): string => read(f).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("History tab: counts only", () => {
  it("is separate by default (one constant merges it back after the look)", () => {
    expect(SEPARATE_HISTORY).toBe(true);
    expect(code("ComboScript.tsx")).toContain("SEPARATE_HISTORY");
  });
  it("its rows are sorted by player name, never by the model's change", () => {
    const names = historyView(G).rows.map((r) => r.player.name);
    expect(names.length).toBeGreaterThan(3);
    expect(names).toEqual([...names].sort());
    // the model's order differs, so the History order carries nothing of it
    const model = scriptView(G, P.shared, "blowout").more.map((r) => r.player.name);
    expect(model).not.toEqual(names.slice(0, model.length));
  });
  it("every text it produces has no chance: no percent sign, and no model field is read", () => {
    for (const s of ["blowout", "close", "shootout", "grind"] as const) {
      for (const r of historyView(G).rows) {
        expect(historyText(r.hist, s, r.line).text).not.toMatch(/%|percent|chance/i);
        expect(histCompact(r.hist, s, "over") ?? "").not.toMatch(/%|percent|chance/i);
      }
    }
    const src = code("ComboScriptHistory.tsx");
    expect(src).not.toMatch(/p_now|p_script|\.change\b|<Bar|scriptView|scriptPrior|evaluate|\bprior\b|\bpc\(|\.surv\b|normCdf/);
    expect(src).not.toMatch(/ModelView|ComboScriptModel/);
  });
  it("the script chooser drops tonight's chance on the History tab", () => {
    expect(code("ComboScript.tsx")).toContain("chance={!showHistory}");
  });
});

describe("Model tab: chances only", () => {
  it("every text it produces has no count (no 'k of n', no 'k/n')", () => {
    const v = scriptView(G, P.shared, "close");
    expect(v.more.length + v.less.length).toBeGreaterThan(0);
    for (const r of [...v.more, ...v.less]) {
      const t = modelRowText(r);
      expect(t).not.toMatch(/\b\d+ of \d+\b|\b\d+\/\d+\b|games|this season/);
      expect(t).toMatch(/%/);
    }
  });
  it("its source reads no history field and renders no past-games control", () => {
    const src = code("ComboScriptModel.tsx");
    expect(src).not.toMatch(/\bhist\b|hist_short|readHist|histLine|histCompact|HistoryLine|Trend|n_all|k_all|historyText|ComboScriptHistory/);
  });
});
