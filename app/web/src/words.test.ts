import { describe, expect, it } from "vitest";
import { chanceSentence, didText, inTen, rangeSentence, sideText, winSentence } from "./words";

describe("inTen", () => {
  it("turns a probability into everyday words", () => {
    expect(inTen(0.58)).toBe("about 6 times in 10");
    expect(inTen(0.5)).toBe("about half the time");
    expect(inTen(0.12)).toBe("about 1 time in 10");
    expect(inTen(0.97)).toBe("nearly every time");
    expect(inTen(0.02)).toBe("almost never");
  });

  it("does not claim more than a clipped tail supports", () => {
    expect(inTen(0.95, true)).toBe("nearly every time");
    expect(inTen(0.05, true)).toBe("very rarely");
    expect(inTen(0.62, true)).toBe("about 6 times in 10");
  });
});

describe("over and under in words", () => {
  it("handles half lines", () => {
    expect(sideText("pts", 24.5, "over")).toBe("25 or more points");
    expect(sideText("pts", 24.5, "under")).toBe("24 or fewer points");
    expect(sideText("fg3m", 3.5, "over")).toBe("4 or more threes");
  });

  it("handles whole-number lines, where a tie is a push", () => {
    expect(sideText("reb", 8, "over")).toBe("9 or more rebounds");
    expect(sideText("reb", 8, "under")).toBe("7 or fewer rebounds");
  });

  it("handles a line that leaves nothing under it", () => {
    expect(sideText("ast", 0.5, "under")).toBe("no assists");
  });

  it("writes the sentence a fan would say", () => {
    expect(chanceSentence("Jayson Tatum", "pts", 28.5, "over", 0.41)).toBe(
      "Jayson Tatum scores 29 or more points about 4 times in 10",
    );
    expect(chanceSentence("Nikola Jokic", "reb", 12.5, "over", 0.5)).toBe(
      "Nikola Jokic grabs 13 or more rebounds about half the time",
    );
  });

  it("says what happened", () => {
    expect(didText("pts", 28)).toBe("scored 28");
    expect(didText("reb", 8)).toBe("grabbed 8 rebounds");
    expect(didText("ast", 1)).toBe("had 1 assist");
    expect(didText("fg3m", 3)).toBe("hit 3 threes");
  });
});

describe("winSentence", () => {
  it("calls a close game a coin flip and names the favourite otherwise", () => {
    expect(winSentence(0.52, "Celtics", "Hawks")).toBe("Close to a coin flip");
    expect(winSentence(0.87, "Celtics", "Hawks")).toBe("Celtics win about 9 times in 10");
    expect(winSentence(0.3, "Celtics", "Hawks")).toBe("Hawks win about 7 times in 10");
  });
});

describe("F07: a reference line leads with the forecast, not a 50% chance", () => {
  it("says the typical night and the middle half, in the stat's own word", () => {
    expect(rangeSentence("pts", "23", "18", "28")).toBe("A typical night is 23 points; half of his nights land between 18 and 28");
    expect(rangeSentence("fg3m", "2", "1", "3")).toContain("2 threes");
    expect(rangeSentence("pts", "23", "18", "28")).not.toMatch(/%|times in 10|half the time/);
  });
});
