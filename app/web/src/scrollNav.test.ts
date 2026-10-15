import { describe, expect, it } from "vitest";
import { ScrollNav } from "./scrollNav";

describe("F11: scroll position on navigation", () => {
  it("a forward link starts at the top, and Back restores where the slate was left", () => {
    const nav = new ScrollNav("#/");
    nav.onScroll(2500);
    nav.linkClicked();
    expect(nav.onHashChange("#/game/1")).toEqual({ kind: "top" });
    nav.onScroll(0);
    // browser Back: no link click
    expect(nav.onHashChange("#/")).toEqual({ kind: "restore", y: 2500 });
  });

  it("does not record the clamped position while a restore is pending", () => {
    const nav = new ScrollNav("#/");
    nav.onScroll(2500);
    nav.linkClicked();
    nav.onHashChange("#/game/1");
    nav.onHashChange("#/"); // Back, restore pending
    nav.onScroll(300); // page still short while it loads
    nav.linkClicked();
    nav.onHashChange("#/game/1");
    expect(nav.onHashChange("#/")).toEqual({ kind: "restore", y: 2500 }); // 300 was never recorded
    nav.restoreDone();
    nav.onScroll(2400);
    nav.linkClicked();
    nav.onHashChange("#/game/1");
    expect(nav.onHashChange("#/")).toEqual({ kind: "restore", y: 2400 }); // recorded once the restore ended
  });

  it("a page never seen, or left at the top, starts at the top on Back", () => {
    const nav = new ScrollNav("#/a");
    expect(nav.onHashChange("#/b")).toEqual({ kind: "top" });
    nav.onScroll(0);
    expect(nav.onHashChange("#/a")).toEqual({ kind: "top" });
  });

  it("following a link to a page you visited before still starts at the top", () => {
    const nav = new ScrollNav("#/");
    nav.onScroll(900);
    nav.linkClicked();
    nav.onHashChange("#/swipe");
    nav.onScroll(700);
    nav.linkClicked();
    expect(nav.onHashChange("#/")).toEqual({ kind: "top" });
  });
});
