// The coalesced notices and the slip's place beside the page, tested as plain logic (no browser here).
import { describe, expect, it } from "vitest";
import { createNoticeStore, placeAwayFrom, type NoticeTimer } from "./notice";
import { isSlipPage, slipPanelPlan } from "./panel";

function fakeTimer() {
  let t = 0;
  const due: { at: number; fn: () => void; h: number }[] = [];
  let h = 0;
  const timer: NoticeTimer = {
    set: (fn, ms) => {
      h += 1;
      due.push({ at: t + ms, fn, h });
      return h;
    },
    clear: (handle) => {
      const i = due.findIndex((d) => d.h === handle);
      if (i >= 0) due.splice(i, 1);
    },
  };
  const advance = (ms: number): void => {
    t += ms;
    for (const d of [...due].sort((a, b) => a.at - b.at)) {
      if (d.at <= t) {
        due.splice(due.indexOf(d), 1);
        d.fn();
      }
    }
  };
  return { timer, advance, pending: () => due.length };
}

describe("notices: one per action, coalesced", () => {
  it("a newer notice of the same kind replaces the older one", () => {
    const { timer } = fakeTimer();
    const s = createNoticeStore(timer);
    s.push({ kind: "slip", title: "Slip", text: "Added." }, 3);
    s.push({ kind: "slip", title: "Slip", text: "Removed." }, 3);
    expect(s.get().map((n) => n.text)).toEqual(["Removed."]);
  });

  it("never more than `max` visible: one on a phone, the newest kept", () => {
    const { timer } = fakeTimer();
    const s = createNoticeStore(timer);
    s.push({ kind: "slip", title: "Slip", text: "a" }, 1);
    s.push({ kind: "idea", title: "Idea", text: "b" }, 1);
    s.push({ kind: "import", title: "Import", text: "c" }, 1);
    expect(s.get().map((n) => n.text)).toEqual(["c"]);
    s.push({ kind: "slip", title: "Slip", text: "d" }, 2);
    expect(s.get().map((n) => n.text)).toEqual(["c", "d"]);
  });

  it("dismisses itself (5 s, failures 8 s), and a replaced notice's timer is cancelled", () => {
    const f = fakeTimer();
    const s = createNoticeStore(f.timer);
    s.push({ kind: "slip", title: "Slip", text: "a" }, 2);
    s.push({ kind: "slip", title: "Slip", text: "b" }, 2);
    expect(f.pending()).toBe(1);
    s.push({ kind: "fail", title: "x", text: "c", tone: "failed" }, 2);
    f.advance(5000);
    expect(s.get().map((n) => n.text)).toEqual(["c"]);
    f.advance(3000);
    expect(s.get()).toEqual([]);
    expect(f.pending()).toBe(0);
  });

  it("a notice goes where it does not cover the control just used", () => {
    const band = { top: 170, bottom: 230 };
    expect(placeAwayFrom(null, 800, band)).toBe("bottom");
    expect(placeAwayFrom({ top: 300, bottom: 340 }, 800, band)).toBe("bottom");
    expect(placeAwayFrom({ top: 700, bottom: 744 }, 800, band)).toBe("top");
    expect(placeAwayFrom({ top: 80, bottom: 120 }, 800, band)).toBe("bottom");
  });
});

describe("the slip beside the page (shared side panel)", () => {
  const base = { lens: "market", ready: true, wide: true, open: true, onSlipPage: false };
  it("rail when wide and open, the Slip button when wide and hidden, a sheet below 1280 px", () => {
    expect(slipPanelPlan(base)).toBe("rail");
    expect(slipPanelPlan({ ...base, open: false })).toBe("launcher");
    expect(slipPanelPlan({ ...base, wide: false })).toBe("sheet");
    expect(slipPanelPlan({ ...base, wide: false, open: false })).toBe("sheet");
  });
  it("nothing outside the Market lens, before the workspace is known, or on the slip page itself", () => {
    expect(slipPanelPlan({ ...base, lens: "learn" })).toBe("none");
    expect(slipPanelPlan({ ...base, lens: "explore" })).toBe("none");
    expect(slipPanelPlan({ ...base, ready: false })).toBe("none");
    expect(slipPanelPlan({ ...base, onSlipPage: true })).toBe("none");
    expect(isSlipPage("#/bet/slip")).toBe(true);
    expect(isSlipPage("#/bet/slip?x=1")).toBe(true);
    expect(isSlipPage("#/bet/slips")).toBe(false);
    expect(isSlipPage("#/bet/compare")).toBe(false);
  });
});
