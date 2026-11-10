import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { TopContract } from "./api";
import { HEADSHOT_HOST, headshotUrl } from "./headshot";
import { boxPlayerIds, type LiveState } from "./live";
import { betWords, evPerDollar, picksHead, priceCents, runIsForNight, selectPicks, whyText } from "./picksLogic";

const SRC = dirname(fileURLToPath(import.meta.url));

function mk(over: Partial<TopContract> = {}): TopContract {
  return {
    side: "over", threshold: 2, title: "t", ticker: "T1", price: 0.83, fees: 0.01, ev: -0.015, ev_low: null, model_prob: 0.825,
    prob_lo: null, prob_hi: null, price_ts: "2026-11-09 16:05:13", game_id: "G1", player_id: 7, player: "Grayson Allen", team: "PHX",
    stat: "fg3m", matchup: "DEN @ PHX", tip_utc: "2026-11-09T01:00:00Z", ...over,
  };
}

describe("plain words", () => {
  it("says the bet in plain words", () => {
    expect(betWords(mk())).toBe("Grayson Allen 2+ threes");
    expect(betWords(mk({ side: "under", threshold: 24.5, stat: "pts" }))).toBe("Grayson Allen fewer than 24.5 points");
  });
  it("shows price and edge as stored", () => {
    expect(priceCents(0.8299999833106995)).toBe("83c");
    expect(evPerDollar(-0.014999995231628418)).toBe("-$0.015");
    expect(evPerDollar(0.02)).toBe("+$0.020");
  });
});

describe("selecting the closest picks", () => {
  it("takes the best five by edge, one per player and stat", () => {
    const rows = [
      mk({ ticker: "a", ev: -0.05, player_id: 1 }),
      mk({ ticker: "b", ev: -0.015, player_id: 1, side: "under" }),
      mk({ ticker: "c", ev: -0.02, player_id: 2 }),
      mk({ ticker: "d", ev: -0.03, player_id: 3 }),
      mk({ ticker: "e", ev: -0.04, player_id: 4 }),
      mk({ ticker: "f", ev: -0.06, player_id: 5 }),
      mk({ ticker: "g", ev: -0.07, player_id: 6 }),
    ];
    const out = selectPicks(rows);
    expect(out.map((r) => r.ticker)).toEqual(["b", "c", "d", "e", "f"]);
    expect(out).toHaveLength(5);
  });
});

describe("the reason is template text from stored fields", () => {
  it("with only the weight, says the model defers to the market", () => {
    const t = whyText(mk({ context: { model_weight: 0, n_settled_track: 1, spread: 0.01, p_play: null, proj_minutes: null, mean_cond: null, teammates_out: [] } }));
    expect(t).toContain("defers to the market");
    expect(t).toContain("83c");
    expect(t).not.toContain("minutes");
  });
  it("uses minutes, chance of playing, projection and teammates out when stored", () => {
    const t = whyText(mk({ context: { model_weight: 0.3, n_settled_track: 1, spread: 0.01, p_play: 0.97, proj_minutes: 33.6, mean_cond: 2.29, teammates_out: ["Devin Carter"] } }));
    expect(t).toContain("Projected 34 minutes with a 97% chance he plays");
    expect(t).toContain("2.3 threes expected if he plays, against a line of 2");
    expect(t).toContain("Devin Carter listed out");
    expect(t).toContain("Model 83%");
  });
  it("works with an older API that sends no context", () => {
    expect(whyText(mk())).toMatch(/^Model 83% against a 83c price/);
  });
});

describe("the honest no-positive state", () => {
  it("says so plainly at the top", () => {
    const h = picksHead("no_positive_ev_found", 3462, 0, [mk()]);
    expect(h.kind).toBe("negative");
    expect(h.text).toContain("No bet is positive after fees this morning; these are the closest.");
    expect(h.text).toContain("3,462 contracts evaluated, 0 positive");
  });
  it("is plain when nothing is stored, and never claims a pick it does not have", () => {
    expect(picksHead(null, null, null, []).kind).toBe("none");
  });
  it("does not show a run for another night as this night's", () => {
    expect(runIsForNight("2026-11-10", "2026-11-09")).toBe(false);
    expect(runIsForNight("2026-11-09", "2026-11-09")).toBe(true);
    expect(runIsForNight("2026-11-09", null)).toBe(false);
  });
});

describe("the locked test window (LINE_CAL_LIVE): the picks view shows no outcome", () => {
  it("reads no result, final score or actual anywhere", () => {
    const text = readFileSync(join(SRC, "TodaysPicks.tsx"), "utf8") + readFileSync(join(SRC, "picksLogic.ts"), "utf8");
    expect(text).not.toMatch(/\bactual\b|gameResult|home_pts|away_pts|went_over|settled\b/);
  });
});

describe("headshots", () => {
  it("builds the cdn address only from a numeric player id", () => {
    expect(HEADSHOT_HOST).toBe("https://cdn.nba.com");
    expect(headshotUrl(1628401)).toBe("https://cdn.nba.com/headshots/nba/latest/260x190/1628401.png");
    expect(headshotUrl(0)).toBeNull();
    expect(headshotUrl(-3)).toBeNull();
    expect(headshotUrl(1.5)).toBeNull();
    expect(headshotUrl(Number.NaN)).toBeNull();
  });
  it("the avatar loads lazily, sends no referrer, sets its size, falls back to initials, and names the player", () => {
    const text = readFileSync(join(SRC, "Avatar.tsx"), "utf8");
    expect(text).toContain('loading="lazy"');
    expect(text).toContain('referrerpolicy="no-referrer"');
    expect(text).toContain("width={px}");
    expect(text).toContain("height={px}");
    expect(text).toContain("alt={props.name}");
    expect(text).toContain("onError");
    expect(text).toContain("initials");
  });
  it("maps a live box row id to the export player id by name within the team", () => {
    const live = {
      home: { abbr: "PHX" },
      away: { abbr: "DEN" },
      box: { e1: { id: "e1", name: "Grayson Allen", team: "PHX" }, e2: { id: "e2", name: "Grayson Allen", team: "DEN" } },
    } as unknown as LiveState;
    expect(boxPlayerIds([{ player_id: 7, name: "Grayson Allen", side: "home" }], live)).toEqual({ e1: 7 });
  });
  it("the footer credits the photos", () => {
    expect(readFileSync(join(SRC, "components.tsx"), "utf8")).toContain("Player photos: NBA");
  });
});
