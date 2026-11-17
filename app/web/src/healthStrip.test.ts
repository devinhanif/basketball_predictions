// The health strip's plain-language wording: the same stored fields as before, no new numbers, and a strip that never reads
// as if the app itself were stale.
import { describe, expect, it } from "vitest";
import { modelName, staleSentences, staleShort } from "./HealthStrip";

describe("health strip wording", () => {
  const marks = { source: "prop_grids", by_model: { props_avg_live2: { writer_flag: 15276 } }, primary_fallback_stale: 6604 };
  it("says what the stale marks mean in plain words, from the stored counts only", () => {
    expect(staleSentences(marks)).toEqual([
      "The AVG comparison model (props_avg_live2) is set aside on 15,276 rows because its own run flagged its weights as out of date.",
      "6,604 shown forecasts use the production model instead.",
    ]);
    expect(staleShort(marks)).toBe("6,604 on the production model");
  });
  it("never reads as if the app were stale when nothing is marked", () => {
    const none = { source: "s", by_model: {}, primary_fallback_stale: 0 };
    expect(staleSentences(none)).toEqual(["No model is marked out of date in this export.", "No shown forecast needed the fallback."]);
    expect(staleShort(none)).toBeNull();
    expect(staleShort(null)).toBeNull();
  });
  it("shows an unknown reason verbatim, never mapped", () => {
    const odd = { source: "s", by_model: { other_model: { new_reason: 1 } }, primary_fallback_stale: null };
    expect(staleSentences(odd)).toEqual(['Model other_model is set aside on 1 row because it is marked "new reason".']);
    expect(modelName("props_avg_live3")).toBe("the AVG comparison model (props_avg_live3)");
  });
});
