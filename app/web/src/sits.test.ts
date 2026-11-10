// 'If a teammate sits' (T526 / T526-a): words follow the evidence, and nothing here carries a price, a chance of an
// outcome, an assist/three number, a per-player size or a stake.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  EVIDENCE_HEADLINE,
  arrow,
  dirWord,
  playerSentence,
  ptsSentence,
  rebSentence,
  sittersFor,
  statusWord,
  type ScenarioSitter,
  type ScenariosView,
} from "./sits";

const mate = (id: number, name: string, pts: "up" | "down" | "flat", reb: "up" | "down" | "flat" | null, likely = true) => ({
  player_id: id,
  name,
  likely_plays: likely,
  pts,
  reb,
});

const SITTER: ScenarioSitter = {
  player_id: 1,
  name: "Star Guard",
  team_id: 10,
  side: "home",
  status: "questionable",
  pts: { direction: "up", total: 2.14, per_player: 0.71, n_likely: 3 },
  reb: { direction: "up", uncertain_size: true },
  teammates: [mate(2, "Wing One", "up", "up"), mate(3, "Big Two", "up", "down"), mate(4, "Bench Three", "flat", null, false)],
};
const VIEW: ScenariosView = {
  game_id: "g1",
  available: true,
  reason: null,
  made_at: "2026-11-10T15:00:00Z",
  evidence: ["Tested on 2023-25 history: helps for points."],
  not_modelled: ["The opponent."],
  sitters: [SITTER, { ...SITTER, player_id: 2, name: "Wing One", teammates: [mate(1, "Star Guard", "up", null)] }],
};

describe("who can be picked", () => {
  it("a player's sitters are his teammates, never himself", () => {
    expect(sittersFor(VIEW, 2).map((s) => s.name)).toEqual(["Star Guard"]);
    expect(sittersFor(VIEW, 3).map((s) => s.name)).toEqual(["Star Guard"]);
    expect(sittersFor(VIEW, 1).map((s) => s.name)).toEqual(["Wing One"]);
  });
  it("an unavailable view offers nobody", () => {
    expect(sittersFor({ ...VIEW, available: false }, 2)).toEqual([]);
  });
});

describe("the sentences", () => {
  it("points: team-level size, with the per-likely-player figure", () => {
    expect(ptsSentence(SITTER)).toBe(
      "If Star Guard sits, teammates' points forecasts rise by about 2.1 in total (about 0.7 per teammate likely to play).",
    );
    const down = { ...SITTER, pts: { direction: "down" as const, total: -1.2, per_player: -0.4, n_likely: 3 } };
    expect(ptsSentence(down)).toContain("fall by about 1.2 in total (about 0.4 per teammate");
    const flat = { ...SITTER, pts: { direction: "flat" as const, total: 0, per_player: null, n_likely: 0 } };
    expect(ptsSentence(flat)).toBe("If Star Guard sits, teammates' points forecasts barely move.");
  });
  it("rebounds: direction only, always 'uncertain size', never a number", () => {
    const s = rebSentence(SITTER);
    expect(s).toBe("Rebounds: teammates' forecasts tend to rise (uncertain size).");
    expect(s).not.toMatch(/\d/);
    expect(rebSentence({ ...SITTER, reb: null })).toBeNull();
  });
  it("the card's own player gets a direction, not a size", () => {
    const s = playerSentence(SITTER, 3, "Big Two");
    expect(s).toBe("Big Two's forecast: points higher; rebounds lower (uncertain size).");
    expect(s).not.toMatch(/\d/);
    expect(playerSentence(SITTER, 99, "Nobody")).toBeNull();
  });
  it("words and arrows", () => {
    expect([dirWord("up"), dirWord("down"), dirWord("flat")]).toEqual(["higher", "lower", "about the same"]);
    expect([arrow("up"), arrow("down"), arrow("flat")]).toEqual(["↑", "↓", "→"]);
    expect(statusWord("questionable")).toBe("listed questionable");
    expect(statusWord("unlisted")).toBe("not on the injury report");
  });
  it("the evidence label says what was tested and for which stats", () => {
    expect(EVIDENCE_HEADLINE).toBe("Tested on 2023-25 history: helps for points; rebounds direction only.");
  });
});

describe("what never appears", () => {
  const shown = [ptsSentence(SITTER), rebSentence(SITTER) ?? "", playerSentence(SITTER, 3, "Big Two") ?? "", EVIDENCE_HEADLINE].join(" ");
  it("no price, odds, stake, chance of an outcome, or assist/three claim in any sentence", () => {
    expect(shown).not.toMatch(/price|odds|stake|bet\b|EV\b|dollar|\$|chance|probab|assist|three|fg3/i);
  });
  it("the source files carry no price, stake or raw-model fields", () => {
    for (const f of ["sits.ts", "TeammateSits.tsx"]) {
      const code = readFileSync(join(__dirname, f), "utf8")
        .split("\n")
        .filter((l) => !/^\s*(\/\/|\/\*|\*)/.test(l))
        .join("\n");
      expect(code, f).not.toMatch(/price|odds|stake|american|kelly|\bEV\b|p_over|scn_|base_mean|delta_mean/i);
    }
  });
});
