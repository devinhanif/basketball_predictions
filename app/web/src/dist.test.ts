import { describe, expect, it } from "vitest";
import { DistBar, DistLegend } from "./components";
import { lineMarkLabel } from "./words";
import type { PropView } from "./types";

function prop(source: "posted" | "reference"): PropView {
  const q = Array.from({ length: 19 }, (_, i) => 10 + i);
  return {
    prop_id: "p",
    stat: "pts",
    line: 19.5,
    line_source: source,
    over_price: null,
    under_price: null,
    price_book: null,
    line_snapshot_utc: null,
    forecast_mean: 19,
    q,
    taus: [],
    model_p_over: 0.49,
    model_p_under: 0.51,
    model_p_push: 0,
    extrapolated: false,
    market_p_over: null,
    forecast_stamp: "x",
    model_source: "x",
    model_label: "x",
    model_detail: null,
    p_play: null,
    p_play_label: null,
  };
}

const dump = (v: unknown): string => JSON.stringify(v);

describe("F01: label a line by its real source", () => {
  it("never calls a reference line a book line", () => {
    expect(lineMarkLabel("reference")).toBe("our reference line");
    expect(lineMarkLabel("posted")).toBe("line");
    const s = dump(DistBar({ player: "A B", prop: prop("reference") }));
    expect(s).toContain("our reference line");
    expect(s.toLowerCase()).not.toContain("book line");
  });

  it("says the same in the accessible label, and 'line' for a posted one", () => {
    const s = dump(DistBar({ player: "A B", prop: prop("posted") }));
    expect(s).toMatch(/; line 19\.5/);
    expect(s.toLowerCase()).not.toContain("book line");
  });

  it("legend names marks by shape and does not call a reference line a sportsbook number", () => {
    const ref = dump(DistLegend({ reference: true }));
    expect(ref).toContain("not a number any book set");
    expect(ref).not.toMatch(/white|black/i);
    expect(dump(DistLegend({}))).toContain("the number the sportsbook set");
  });
});
