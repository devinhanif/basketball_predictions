// The live score card's pure rules: the pace sentence, the plain states, sorting, what-changed marks, freshness words, the
// in-game line, and the source-level locks (no chance computed in the app, no price outside the Market lens).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  CHANGED_FOR_MS,
  agoText,
  asOfText,
  bar,
  emptyBook,
  inGameText,
  isRecent,
  minutesText,
  noteChanges,
  paceSentence,
  sortSlate,
  standingWord,
  type CardRow,
  type LiveCard,
  type SlateRow,
} from "./liveCard";

const row = (o: Partial<CardRow> = {}): CardRow => ({
  key: "1|pts",
  player_id: 1,
  name: "Luka Doncic",
  team: "LAL",
  side: "away",
  stat: "pts",
  line: 24.5,
  cur: 18,
  need: 7,
  cleared: false,
  minutes: 28,
  fouls: 2,
  starter: true,
  status: "playing",
  reason: null,
  chance: null,
  chance_source: null,
  price: null,
  ...o,
});
type G = Pick<LiveCard, "state" | "regulation_left_min" | "overtime" | "status">;
const status = { period: 3, clock: "4:12", detail: "", elapsed_min: 33.8 };
const game = (o: Partial<G> = {}): G => ({ state: "in", regulation_left_min: 14.2, overtime: false, status, ...o });

describe("the pace sentence is arithmetic on displayed facts", () => {
  it("says the count, the line, what is needed, and the game time left from the clock", () => {
    expect(paceSentence(row(), game())).toBe("18 of 24.5 points. Needs 7 more in about 14 minutes of game time left.");
    expect(paceSentence(row({ stat: "reb", line: 9.5, cur: 8, need: 2 }), game({ regulation_left_min: 0.5 }))).toBe(
      "8 of 9.5 rebounds. Needs 2 more in less than a minute of game time left.",
    );
    expect(minutesText(1.2)).toBe("about 1 minute");
  });
  it("never claims a usual share: the time is game time left, nothing about the player's own minutes", () => {
    expect(paceSentence(row(), game())).not.toMatch(/usual|share|projected|expected/i);
  });
  it("a line already cleared", () => {
    expect(paceSentence(row({ cur: 26, need: 0, cleared: true }), game())).toBe("Over: 26, line 24.5 points.");
  });
  it("regulation over and the line not reached is a fact, not a verdict", () => {
    const s = paceSentence(row(), game({ regulation_left_min: 0 }));
    expect(s).toBe("Not reached in regulation: 18 of 24.5 points.");
    expect(s).not.toMatch(/lose|lost|bet|miss/i);
  });
  it("overtime", () => {
    expect(paceSentence(row(), game({ overtime: true, regulation_left_min: 0 }))).toBe("Overtime. 18 of 24.5 points. Needs 7 more.");
  });
  it("not entered, did not play, fouled out, no box", () => {
    expect(paceSentence(row({ cur: 0, need: 25, status: "not_entered" }), game())).toBe("Has not entered the game yet. Line 24.5 points.");
    expect(paceSentence(row({ cur: null, status: "dnp", reason: "COACH'S DECISION" }), game())).toBe("Did not play (COACH'S DECISION). Line 24.5 points.");
    expect(paceSentence(row({ cur: null, status: "dnp", reason: "did not play" }), game())).toBe("Did not play. Line 24.5 points.");
    expect(paceSentence(row({ status: "fouled_out" }), game())).toContain("Fouled out.");
    expect(paceSentence(row({ cur: null, status: "no_box" }), game())).toBe("Not in the live box score. Line 24.5 points.");
  });
  it("before tip and after the final", () => {
    expect(paceSentence(row({ cur: null, status: "no_box" }), game({ state: "pre", regulation_left_min: 48 }))).toBe("Line 24.5 points. Game not started.");
    expect(paceSentence(row({ cur: 26, cleared: true }), game({ state: "post", regulation_left_min: 0 }))).toBe("Final: over, 26 against a line of 24.5 points.");
    expect(paceSentence(row(), game({ state: "post", regulation_left_min: 0 }))).toBe("Final: 18, line 24.5 points. Under the line.");
  });
  it("no sentence in any state calls anything likely or unlikely or prints a percentage", () => {
    const states: G[] = [game(), game({ overtime: true, regulation_left_min: 0 }), game({ regulation_left_min: 0 }), game({ state: "pre" }), game({ state: "post" }), game({ regulation_left_min: null })];
    const rows = [row(), row({ cleared: true, cur: 30, need: 0 }), row({ status: "dnp", cur: null }), row({ status: "not_entered" }), row({ status: "fouled_out" }), row({ status: "no_box", cur: null })];
    for (const g of states)
      for (const r of rows) {
        const s = paceSentence(r, g);
        expect(s).not.toMatch(/likely|unlikely|chance|probab|odds|should|will |expect|%/i);
      }
  });
});

describe("standing words (colour is never the only signal)", () => {
  it("every state has a word", () => {
    expect(standingWord(row(), "in")).toBe("IN PLAY");
    expect(standingWord(row({ cleared: true }), "in")).toBe("OVER NOW");
    expect(standingWord(row({ cleared: true }), "post")).toBe("OVER");
    expect(standingWord(row(), "post")).toBe("UNDER");
    expect(standingWord(row({ status: "dnp" }), "in")).toBe("DID NOT PLAY");
    expect(standingWord(row({ status: "fouled_out" }), "in")).toBe("FOULED OUT");
    expect(standingWord(row({ status: "not_entered" }), "in")).toBe("NOT ENTERED");
    expect(standingWord(row({ status: "no_box", cur: null }), "pre")).toBe("NOT STARTED");
  });
});

describe("the bar", () => {
  it("puts the tick at the line on a scale that leaves room past it, and never overflows", () => {
    const b = bar(24.5, 18);
    expect(b.tick).toBeCloseTo((24.5 / (24.5 * 1.25)) * 100, 5);
    expect(b.fill).toBeCloseTo((18 / (24.5 * 1.25)) * 100, 5);
    expect(bar(24.5, 40).fill).toBe(100);
    expect(bar(24.5, 40).tick).toBeLessThan(100);
    expect(bar(24.5, null).fill).toBe(0);
  });
});

const card = (id: string, state: LiveCard["state"] = "in"): LiveCard =>
  ({ game_id: id, state, date: "2026-11-09", away: { abbr: "AAA", name: "", score: 0 }, home: { abbr: "BBB", name: "", score: 0 }, status, as_of: { label: "Q3 4:12", source: "capture", fetched_at: "", server_now: "", age_s: 10 } }) as LiveCard;

describe("slate sorting", () => {
  const a: SlateRow = { card: card("2"), row: row({ key: "a|pts", name: "A", line: 20.5, cur: 15, need: 6 }) }; // 6 of 21 still to go
  const b: SlateRow = { card: card("1"), row: row({ key: "b|pts", name: "B", line: 10.5, cur: 9, need: 2 }) }; // 2 of 11
  const c: SlateRow = { card: card("1"), row: row({ key: "c|pts", name: "C", cleared: true, cur: 30, need: 0 }) };
  const d: SlateRow = { card: card("1"), row: row({ key: "d|pts", name: "D", status: "dnp", cur: null, need: null }) };
  const e: SlateRow = { card: card("3", "post"), row: row({ key: "e|pts", name: "E", cur: 10, need: 15 }) };
  const names = (x: SlateRow[]): string => x.map((s) => s.row.name).join("");
  it("closest to the line: share of the line still to go, then over, then did not play, then final", () => {
    expect(names(sortSlate([d, e, c, a, b], "closest"))).toBe("BACDE");
  });
  it("by game keeps a game's rows together", () => {
    expect(names(sortSlate([a, e, b, c, d], "game"))).toBe("BCDAE");
  });
  it("most recently changed first, then closest", () => {
    expect(names(sortSlate([a, b, c], "changed", { "2|a|pts": 500, "1|b|pts": 100 }))).toBe("ABC");
  });
});

describe("what changed since the last update", () => {
  it("marks nothing on the first update, then the rows whose count moved, for a while", () => {
    let book = noteChanges(emptyBook(), [{ key: "g|a", cur: 10 }, { key: "g|b", cur: 3 }], 1000);
    expect(isRecent(book, "g|a", 1000)).toBe(false);
    book = noteChanges(book, [{ key: "g|a", cur: 13 }, { key: "g|b", cur: 3 }], 2000);
    expect(isRecent(book, "g|a", 2500)).toBe(true);
    expect(book.by["g|a"]).toBe(3);
    expect(isRecent(book, "g|b", 2500)).toBe(false);
    expect(isRecent(book, "g|a", 2000 + CHANGED_FOR_MS + 1)).toBe(false);
  });
  it("a row that appears later, or that has no count yet, is not a change", () => {
    let book = noteChanges(emptyBook(), [{ key: "g|a", cur: null }], 0);
    book = noteChanges(book, [{ key: "g|a", cur: 4 }, { key: "g|new", cur: 9 }], 10);
    expect(isRecent(book, "g|a", 11)).toBe(false);
    expect(isRecent(book, "g|new", 11)).toBe(false);
  });
});

describe("freshness", () => {
  it("says the game clock it is as of and how old the data is", () => {
    const c = card("1");
    expect(asOfText(c, 66)).toBe("as of Q3 4:12, about 1 min ago");
    expect(asOfText(c, 12)).toBe("as of Q3 4:12, 12 s ago");
    expect(agoText(44.4)).toBe("44 s ago");
    expect(asOfText(card("1", "post"), 5000)).toMatch(/^Game over/);
    expect(asOfText(card("1", "pre"), 30)).toBe("Not started. Last copy 30 s ago.");
  });
});

describe("step 2 line", () => {
  it("carries the exact label, the stored chance and its age, and nothing else", () => {
    const s = inGameText({ chance: 0.62, label: "Experimental, not yet tested", known_at: "", age_s: 90, as_of: "Q3 3:39" });
    expect(s).toBe("Experimental, not yet tested: 62% (model as of Q3 3:39, about 2 min ago)");
  });
});

// ---------------------------------------------------------------------------- source-level locks

const read = (f: string): string => readFileSync(join(__dirname, f), "utf8");
const FILES = ["liveCard.ts", "LiveCardParts.tsx", "useLiveCard.ts", "liveApi.ts"];

describe("what the card's sources may and may not do", () => {
  it("compute no probability (no distribution maths, no 'likely' wording)", () => {
    for (const f of FILES) {
      const t = read(f).replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
      expect(t, f).not.toMatch(/Math\.(exp|log|erf|tanh)\b|\bcdf\b|\bnormal\b|\bsigma\b|\bbinomial\b|\bpoisson\b/i);
      expect(t, f).not.toMatch(/\blikely\b|\bunlikely\b|\bprobably\b/i);
    }
  });
  it("read no raw chance field and keep no model record", () => {
    for (const f of FILES) {
      const t = read(f);
      expect(t, f).not.toMatch(/\bp_raw\b|\bp_over_raw\b|\.raw\b|hit_rate|hitRate|\btally\b|\brecord\b.*model/i);
    }
  });
  it("price words and numbers appear only behind the Market lens", () => {
    const t = read("LiveCardParts.tsx");
    expect(t).toContain('lens === "market"');
    // the only call that formats a price sits inside the block guarded by that variable
    const uses = [...t.matchAll(/american\(/g)].length;
    expect(uses).toBe(2);
    expect(t.indexOf("american(")).toBeGreaterThan(t.indexOf("price !== null"));
    const noPrice = t
      .replace(/^import .*$/gm, "")
      .replace(/^\s*\/\/.*$/gm, "")
      .replace(/^\s*const price = .*$/m, "")
      .replace(/\{price !== null \? \([\s\S]*?\) : null\}/, "");
    expect(noPrice).not.toMatch(/\bprice\b|american\(|bookName\(|DraftKings|FanDuel|Kalshi/i);
  });
  it("use only GET reads", () => {
    const t = read("liveApi.ts");
    expect(t).not.toMatch(/method:|POST|PUT|DELETE/);
  });
});

describe("the Learn lens: facts only, no price word in anything the card prints", () => {
  // the same patterns scripts/learn-dom.mjs applies to the rendered page
  const RE = [/(?<![\w])[+\-−]\d{3,4}\b/, /\d\s?¢/, /\$\s?\d/, /\bcents? per\b/i, /\b(EV|vig|vigorish|odds|payout|moneyline|implied|break-even)\b/i, /\bedges?\b/i, /\bfees?\b/i, /\bprices?\b/i, /\b(DraftKings|FanDuel|BetMGM|Caesars|Kalshi|Polymarket)\b/i];
  it("no sentence, word or note matches", () => {
    const g = [game(), game({ overtime: true, regulation_left_min: 0 }), game({ state: "pre" }), game({ state: "post" })];
    const rs = [row(), row({ cleared: true }), row({ status: "dnp", cur: null }), row({ status: "not_entered" })];
    const text = [
      ...g.flatMap((x) => rs.map((r) => paceSentence(r, x) + " " + standingWord(r, x.state))),
      asOfText(card("1"), 20),
    ].join("\n");
    for (const re of RE) expect(text).not.toMatch(re);
  });
});
