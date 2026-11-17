// The finishing pass: the Learn lens keeps Market-only screens out of the search, and a Market-screen note never repeats an id.
import { describe, expect, it } from "vitest";
import { marketOnlyId } from "./Lens";
import { MARKET_ONLY, SCREEN_ITEMS, screensFor } from "./shellData";
import { topHref } from "./router";

describe("search follows the lens", () => {
  it("lists no Market-only screen outside the Market lens", () => {
    const hrefs = new Set(MARKET_ONLY.map((n) => topHref(n)));
    const learn = screensFor(false).map((i) => i.href);
    for (const h of hrefs) expect(learn).not.toContain(h);
    expect(screensFor(false).some((i) => i.marketOnly === true)).toBe(false);
  });
  it("lists every screen in the Market lens", () => {
    expect(screensFor(true)).toHaveLength(SCREEN_ITEMS.length);
  });
  it("marks exactly the nav's Market-only screens", () => {
    const marked = SCREEN_ITEMS.filter((i) => i.marketOnly === true && i.href.startsWith("#/") && !i.href.startsWith("#/bet")).map((i) => i.href).sort();
    expect(marked).toEqual(MARKET_ONLY.map((n) => topHref(n)).sort());
  });
});

describe("Market-screen note", () => {
  it("takes its id from the name, so two on a page differ", () => {
    expect(marketOnlyId("The parlay engine's card")).toBe("mo-the-parlay-engine-s-card");
    expect(marketOnlyId("The ticket list")).not.toBe(marketOnlyId("The parlay engine's card"));
    expect(marketOnlyId("Signals")).toBe("mo-signals");
  });
});

describe("stacked table labels", () => {
  it("label a cell by its plain-text head, by `label`, and never a lead cell", async () => {
    const { stackLabel } = await import("./ui/Table");
    expect(stackLabel({ key: "n", head: "n rows", cell: () => null })).toBe("n rows");
    expect(stackLabel({ key: "x", head: null, label: "Side", cell: () => null })).toBe("Side");
    expect(stackLabel({ key: "m", head: "Measure", lead: true, cell: () => null })).toBeNull();
  });
});

describe("on this page", () => {
  it("keeps a short heading whole and cuts a long one at a word", async () => {
    const { chipLabel } = await import("./ui/OnThisPage");
    expect(chipLabel("The model")).toBe("The model");
    const long = chipLabel("Rows under a frozen test keep their counts and say why they are hidden");
    expect(long.endsWith("…")).toBe(true);
    expect(long.length).toBeLessThanOrEqual(43);
    expect(long).not.toMatch(/\s…$/);
  });
});
