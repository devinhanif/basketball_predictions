import { describe, expect, it } from "vitest";
import { LEARNED, STATUS_LABEL, evidenceText, isShowable } from "./learned";

describe("learned panes", () => {
  it("shows every item, each with n and an interval", () => {
    expect(LEARNED.length).toBe(10);
    for (const l of LEARNED) expect(isShowable(l), l.id).toBe(true);
  });
  it("uses only known statuses and unique ids", () => {
    expect(new Set(LEARNED.map((l) => l.id)).size).toBe(LEARNED.length);
    for (const l of LEARNED) expect(STATUS_LABEL[l.status], l.id).toBeTruthy();
  });
  it("puts the holdout pass first and the failures last", () => {
    expect(LEARNED[0]?.id).toBe("avg-sequence");
    expect(LEARNED[LEARNED.length - 1]?.status).toBe("failed_informative");
  });
  it("renders the interval in the ledger's own digits", () => {
    const e = LEARNED.find((l) => l.id === "avg-sequence")?.evidence[0];
    expect(e && evidenceText(e)).toBe("-0.0133 [-0.0158, -0.0109]");
  });
  it("rejects an item with no n", () => {
    const first = LEARNED[0];
    const ev = first?.evidence[0];
    expect(first && ev).toBeTruthy();
    if (!first || !ev) return;
    expect(isShowable({ ...first, evidence: [{ ...ev, n: "" }] })).toBe(false);
  });
});
