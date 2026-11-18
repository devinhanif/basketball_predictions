// The time module: naive-UTC parsing, ET/CT labels, the ET night boundary around midnight and tip, both DST changes.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  CT_ZONE,
  ET_ZONE,
  UNKNOWN_TIME,
  ageNote,
  before,
  deskStamp,
  etDateOf,
  instantMs,
  longDate,
  parseInstant,
  previousDate,
  stamp,
  tipTime,
  todayET,
  zoneAbbr,
} from "./time";
import { isStale } from "./topParlays";
import { treatAsFinal } from "./live";

describe("parseInstant: stored times", () => {
  it("reads a naive time as UTC, never as the viewer's local time", () => {
    expect(parseInstant("2026-10-11T23:20:10.735352")?.toISOString()).toBe("2026-10-11T23:20:10.735Z");
    expect(parseInstant("2026-11-09 18:02:11")?.toISOString()).toBe("2026-11-09T18:02:11.000Z");
    expect(parseInstant("2026-11-09T18:02")?.toISOString()).toBe("2026-11-09T18:02:00.000Z");
  });
  it("keeps explicit zones", () => {
    expect(parseInstant("2026-11-17T00:52:13+00:00")?.toISOString()).toBe("2026-11-17T00:52:13.000Z");
    expect(parseInstant("2026-11-17T00:35:06.624065Z")?.toISOString()).toBe("2026-11-17T00:35:06.624Z");
    expect(parseInstant("2026-11-16T19:00:00-05:00")?.toISOString()).toBe("2026-11-17T00:00:00.000Z");
  });
  it("a bare date is its UTC midnight", () => {
    expect(parseInstant("2024-11-20")?.toISOString()).toBe("2024-11-20T00:00:00.000Z");
  });
  it("unreadable input is null / NaN, never a throw", () => {
    for (const x of ["", "garbage", "2024-13-45T99:99:99", null, undefined, "   "]) {
      expect(parseInstant(x)).toBeNull();
      expect(Number.isNaN(instantMs(x))).toBe(true);
    }
    expect(stamp("garbage")).toBe(UNKNOWN_TIME);
    expect(tipTime("")).toBe(UNKNOWN_TIME);
    expect(deskStamp("nope")).toBe(UNKNOWN_TIME);
    expect(ageNote("nope")).toBeNull();
    expect(before("nope", "2026-01-01T00:00:00Z")).toBe(false);
    expect(etDateOf("nope")).toBe("");
  });
});

describe("labels", () => {
  it("prints ET for Eastern and CT for Central, in both standard and daylight time", () => {
    expect(tipTime("2026-11-17T00:30:00Z")).toBe("7:30 PM ET"); // EST
    expect(tipTime("2026-10-21T23:30:00Z")).toBe("7:30 PM ET"); // EDT
    expect(tipTime("2026-11-17T00:30:00Z", CT_ZONE)).toBe("6:30 PM CT"); // CST
    expect(tipTime("2026-10-21T23:30:00Z", CT_ZONE)).toBe("6:30 PM CT"); // CDT
  });
  it("names the exact abbreviation in force", () => {
    expect(zoneAbbr(new Date("2026-11-17T00:30:00Z"), ET_ZONE)).toBe("EST");
    expect(zoneAbbr(new Date("2026-10-21T23:30:00Z"), ET_ZONE)).toBe("EDT");
    expect(zoneAbbr(new Date("2026-11-17T00:30:00Z"), CT_ZONE)).toBe("CST");
    expect(zoneAbbr(new Date("2026-10-21T23:30:00Z"), CT_ZONE)).toBe("CDT");
  });
  it("the made_at a fixture stores naive prints at its UTC instant in ET", () => {
    // 23:20 UTC on Oct 11 2026 is 7:20 PM EDT. Read as local time it would print hours off.
    expect(stamp("2026-10-11T23:20:10.735352")).toBe("Oct 11, 7:20 PM ET");
  });
  it("a night's long date does not shift with any zone", () => {
    expect(longDate("2024-11-20")).toBe("Wed, Nov 20, 2024");
    expect(longDate("2026-03-08")).toBe("Sun, Mar 8, 2026");
    expect(longDate("garbage")).toBe("garbage");
  });
});

describe("the ET night boundary", () => {
  it("a 10:30 PM ET tip is still that ET night though it is the next day in UTC", () => {
    expect(etDateOf("2026-11-17T03:30:00Z")).toBe("2026-11-16");
    expect(etDateOf("2026-11-17T04:59:59Z")).toBe("2026-11-16");
    expect(etDateOf("2026-11-17T05:00:00Z")).toBe("2026-11-17");
    // In EDT midnight is 04:00 UTC.
    expect(etDateOf("2026-10-22T03:59:59Z")).toBe("2026-10-21");
    expect(etDateOf("2026-10-22T04:00:00Z")).toBe("2026-10-22");
  });
  it("todayET at 11:30 PM Central is already the next ET day", () => {
    // 11:30 PM CST on Nov 16 = 05:30 UTC Nov 17 = 12:30 AM EST Nov 17.
    expect(todayET(new Date("2026-11-17T05:30:00Z"))).toBe("2026-11-17");
    expect(todayET(new Date("2026-11-17T04:30:00Z"))).toBe("2026-11-16");
  });
  it("tickets made late on the evening before (UTC next day) are not stale for that night; made the day before are", () => {
    expect(isStale("2026-11-17T01:00:00Z", "2026-11-16")).toBe(false);
    expect(isStale("2026-11-16T04:00:00Z", "2026-11-16")).toBe(true); // 11 PM EST on Nov 15
    expect(isStale("2026-11-16T15:00:00", "2026-11-16")).toBe(false); // naive UTC, 10 AM EST
  });
  it("a game 4 h past tip counts as over even without a final body", () => {
    const tip = "2026-11-17T00:30:00Z";
    expect(treatAsFinal("in", tip, Date.parse("2026-11-17T04:29:00Z"))).toBe(false);
    expect(treatAsFinal("in", tip, Date.parse("2026-11-17T04:31:00Z"))).toBe(true);
    expect(treatAsFinal("in", "garbage", Date.parse("2026-11-17T04:31:00Z"))).toBe(false);
  });
});

describe("both DST changes", () => {
  it("spring forward (Mar 8 2026): a 7 PM ET tip is 23:00 UTC after, 00:00 UTC before", () => {
    expect(tipTime("2026-03-08T23:00:00Z")).toBe("7:00 PM ET"); // EDT
    expect(tipTime("2026-03-08T00:00:00Z")).toBe("7:00 PM ET"); // the evening before, EST
    expect(zoneAbbr(new Date("2026-03-08T06:59:00Z"), ET_ZONE)).toBe("EST");
    expect(zoneAbbr(new Date("2026-03-08T07:00:00Z"), ET_ZONE)).toBe("EDT");
    expect(zoneAbbr(new Date("2026-03-08T07:59:00Z"), CT_ZONE)).toBe("CST");
    expect(zoneAbbr(new Date("2026-03-08T08:00:00Z"), CT_ZONE)).toBe("CDT");
    expect(etDateOf("2026-03-09T03:59:00Z")).toBe("2026-03-08");
  });
  it("fall back (Nov 1 2026): ET midnight moves from 04:00 to 05:00 UTC", () => {
    expect(zoneAbbr(new Date("2026-11-01T05:59:00Z"), ET_ZONE)).toBe("EDT");
    expect(zoneAbbr(new Date("2026-11-01T06:00:00Z"), ET_ZONE)).toBe("EST");
    expect(etDateOf("2026-11-01T04:30:00Z")).toBe("2026-11-01"); // 12:30 AM EDT
    expect(etDateOf("2026-11-02T04:30:00Z")).toBe("2026-11-01"); // 11:30 PM EST
    expect(tipTime("2026-11-02T00:30:00Z")).toBe("7:30 PM ET");
    expect(zoneAbbr(new Date("2026-11-01T06:59:00Z"), CT_ZONE)).toBe("CDT");
    expect(zoneAbbr(new Date("2026-11-01T07:00:00Z"), CT_ZONE)).toBe("CST");
  });
  it("calendar arithmetic does not slip across a DST change", () => {
    expect(previousDate("2026-03-09")).toBe("2026-03-08");
    expect(previousDate("2026-11-02")).toBe("2026-11-01");
    expect(previousDate("2026-03-01")).toBe("2026-02-28");
  });
});

describe("one module owns conversions", () => {
  const files = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? files(join(dir, e.name)) : /\.tsx?$/.test(e.name) && !e.name.includes(".test.") ? [join(dir, e.name)] : [],
    );
  it("no other source builds a zoned formatter or parses a stored time with Date.parse / new Date(string)", () => {
    for (const f of files(__dirname)) {
      if (f.endsWith("time.ts")) continue;
      const text = readFileSync(f, "utf8");
      expect(text, f).not.toMatch(/timeZone:|Date\.parse\(|America\/(New_York|Chicago)/);
    }
  });
});
