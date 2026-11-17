// Every time conversion and label in one place. Rules:
//  - A stored time with no zone ("2026-10-11T23:20:10.735352", "2026-11-09 18:02:11") is UTC: the export writes naive
//    UTC (made_at, price_ts, as_of). JavaScript would read it as the viewer's local time, so nothing parses a stored
//    time except parseInstant.
//  - NBA nights are dated in US Eastern time; a page that prints a clock says "ET" and means America/New_York, so the
//    label is right in both EST and EDT. zoneAbbr gives the exact EST/EDT or CST/CDT for an instant where that matters.
//  - Nothing here throws: an unreadable time prints as UNKNOWN_TIME and compares as NaN.

export const ET_ZONE = "America/New_York";
export const CT_ZONE = "America/Chicago";
export const UNKNOWN_TIME = "unknown time";

const NAIVE = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)$/;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** The instant a stored time names, or null when it cannot be read. Naive times are UTC; a bare date is its UTC midnight. */
export function parseInstant(s: string | null | undefined): Date | null {
  if (typeof s !== "string") return null;
  const t = s.trim();
  if (t === "") return null;
  let iso: string;
  const naive = NAIVE.exec(t);
  if (naive) {
    // Keep milliseconds only: some engines refuse more than three fractional digits.
    const time = (naive[2] ?? "").replace(/(\.\d{3})\d+$/, "$1");
    iso = `${naive[1] ?? ""}T${time}Z`;
  } else if (DATE_ONLY.test(t)) {
    iso = `${t}T00:00:00Z`;
  } else {
    iso = t.replace(" ", "T").replace(/(\.\d{3})\d+(?=Z|[+-]\d{2}:?\d{2}$)/, "$1");
  }
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : new Date(ms);
}

/** A real calendar date in YYYY-MM-DD ("2024-02-30" is not one). */
export function isIsoDate(s: string): boolean {
  if (!DATE_ONLY.test(s)) return false;
  const d = parseInstant(s);
  return d !== null && d.toISOString().slice(0, 10) === s;
}

/** Milliseconds since the epoch for a stored time; NaN when unreadable (so comparisons are false, never wrong). */
export function instantMs(s: string | null | undefined): number {
  return parseInstant(s)?.getTime() ?? Number.NaN;
}

const fmtCache = new Map<string, Intl.DateTimeFormat>();
function fmt(zone: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${zone}|${JSON.stringify(opts)}`;
  let f = fmtCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", { timeZone: zone, ...opts });
    fmtCache.set(key, f);
  }
  return f;
}

/** The calendar date (YYYY-MM-DD) of an instant in a zone. */
export function zoneDate(d: Date, zone: string = ET_ZONE): string {
  const parts = fmt(zone, { year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d);
  const get = (t: string): string => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** The Eastern calendar date of an instant (or a stored time): slate dates are ET dates. "" when unreadable. */
export function etDateOf(when: string | Date): string {
  const d = typeof when === "string" ? parseInstant(when) : when;
  return d === null || Number.isNaN(d.getTime()) ? "" : zoneDate(d, ET_ZONE);
}

/** Today's date in US Eastern time. */
export function todayET(now: Date = new Date()): string {
  return zoneDate(now, ET_ZONE);
}

/** The day before a YYYY-MM-DD date (calendar arithmetic, no zone). */
export function previousDate(date: string): string {
  const t = new Date(`${date}T12:00:00Z`);
  if (Number.isNaN(t.getTime())) return date;
  t.setUTCDate(t.getUTCDate() - 1);
  return t.toISOString().slice(0, 10);
}

/** "EST" or "EDT" (ET_ZONE), "CST" or "CDT" (CT_ZONE): the exact abbreviation in force at that instant. */
export function zoneAbbr(d: Date, zone: string = ET_ZONE): string {
  return fmt(zone, { timeZoneName: "short" }).formatToParts(d).find((p) => p.type === "timeZoneName")?.value ?? "";
}

const LABEL: Record<string, string> = { [ET_ZONE]: "ET", [CT_ZONE]: "CT" };

function labelled(utc: string, zone: string, opts: Intl.DateTimeFormatOptions): string {
  const d = parseInstant(utc);
  if (d === null) return UNKNOWN_TIME;
  return `${fmt(zone, opts).format(d)} ${LABEL[zone] ?? zoneAbbr(d, zone)}`;
}

/** "7:30 PM ET": a tip time. */
export function tipTime(utc: string, zone: string = ET_ZONE): string {
  return labelled(utc, zone, { hour: "numeric", minute: "2-digit" });
}

/** "Nov 8, 6:45 PM ET". */
export function stamp(utc: string, zone: string = ET_ZONE): string {
  return labelled(utc, zone, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** "Sun, Nov 8, 6:45 PM ET": the weekday matters, because a desk file can be a day old. */
export function deskStamp(utc: string, zone: string = ET_ZONE): string {
  return labelled(utc, zone, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** "Wed, Nov 20, 2024" for a YYYY-MM-DD night (a calendar date, so no zone shift). */
export function longDate(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const dt = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1, 12));
  if (Number.isNaN(dt.getTime())) return isoDate;
  return fmt("UTC", { weekday: "short", month: "short", day: "numeric", year: "numeric" }).format(dt);
}

/** Past this many hours the data banner says the data is old (architecture section 6: the banner shows its age). */
export const STALE_AFTER_HOURS = 30;

/** Hours between a stored time and now; null when unreadable. */
export function ageHours(stored: string, now: Date = new Date()): number | null {
  const t = instantMs(stored);
  return Number.isNaN(t) ? null : (now.getTime() - t) / 3_600_000;
}

/** "This data is 2 days old." once older than STALE_AFTER_HOURS; null while fresh or unreadable. */
export function ageNote(dataAsOf: string, now: Date = new Date()): string | null {
  const hours = ageHours(dataAsOf, now);
  if (hours === null || hours <= STALE_AFTER_HOURS) return null;
  const days = Math.max(1, Math.floor(hours / 24));
  return `This data is ${days.toString()} ${days === 1 ? "day" : "days"} old.`;
}

/** Has the stored instant `a` happened strictly before `b`? False when either is unreadable. */
export function before(a: string | null | undefined, b: string | null | undefined): boolean {
  const x = instantMs(a);
  const y = instantMs(b);
  return !Number.isNaN(x) && !Number.isNaN(y) && x < y;
}
