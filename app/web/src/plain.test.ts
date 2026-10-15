import { describe, expect, it } from "vitest";
import { evCautious, evVerdict, plainModel, plainPlay, plainText, testCodesIn, ticketBook } from "./plain";

describe("F22: internal names become words, numbers stay", () => {
  it("names the model without codes", () => {
    expect(plainModel("avg")).toBe("our main props model");
    expect(plainModel("fallback_stale")).toContain("standard props model");
    expect(plainModel("fixture")).not.toMatch(/out-of-fold|AVG/);
  });
  it("replaces availability-model and production names", () => {
    expect(plainPlay("Chance to play: from the availability model R.")).toBe("Chance to play: from our availability model.");
    expect(plainPlay("Chance to play: from the production model; availability model R had no number (R stored no number for him).")).toBe(
      "Chance to play: from our standard model; our availability model had no number (it stored no number for him).",
    );
  });
  it("drops test ids from front text and lists them for the details", () => {
    const t = "hit 9.8 points less often than the raw model said (95% CI 6.8 to 12.9, n 3,337 parlays, date-clustered; T490). Three-leg: 8.7 points. Came in lower at DraftKings (T495) (n 1,149)";
    const out = plainText(t);
    expect(out).not.toMatch(/T\d{3}|\bCI\b|clustered/);
    expect(out).toContain("95% range 6.8 to 12.9");
    expect(out).toContain("n 3,337 parlays");
    expect(out).toContain("n 1,149");
    expect(testCodesIn(t)).toEqual(["T490", "T495"]);
  });
  it("a gap between two chances says percentage points, not points", () => {
    const t = "hit 9.8 points less often than the raw model said. Three-leg: 8.7 points (5.5 to 11.9). came in about 3 to 5 points lower than shown";
    expect(plainText(t)).toBe(
      "hit 9.8 percentage points less often than the raw model said. Three-leg: 8.7 percentage points (5.5 to 11.9). came in about 3 to 5 percentage points lower than shown",
    );
  });
  it("capitalises book names and fixes a doubled period, never touching numbers", () => {
    expect(plainText("against 83% implied by draftkings at -500..")).toBe("against 83% implied by DraftKings at -500.");
    expect(plainText("p_cal is the map applied to the P(over)")).toBe("adjusted chance is the map applied to the chance of the over");
  });
});

describe("F25: a ticket leads with the verdict in cents", () => {
  it("states a loss at the book's price, and the cautious end", () => {
    expect(evVerdict(-0.0448, "draftkings")).toBe("Loses about 4 cents per $1 at DraftKings' price");
    expect(evVerdict(-0.0448, "fanduel")).toBe("Loses about 4 cents per $1 at FanDuel's price");
    expect(evCautious(-0.379)).toBe("cautious end: loses about 38 cents");
  });
  it("handles a gain, a wash and mixed books", () => {
    expect(evVerdict(0.052, null)).toBe("Gains about 5 cents per $1 at the prices shown");
    expect(evVerdict(0.001, "kalshi")).toContain("Roughly breaks even");
    expect(ticketBook([{ book: "draftkings" }, { book: "fanduel" }])).toBeNull();
    expect(ticketBook([{ book: "draftkings" }, { book: "draftkings" }])).toBe("draftkings");
  });
});
