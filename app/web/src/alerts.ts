// The notification center: what the desk sent (alerts) and what it noticed (observations), as one list with filters and a
// read state. Nothing is invented: the text is the stored text, or the stored fields described in words; a notice with
// no stored text says so.
import type { Desk, DeskAlert, DeskObservation, DeskPlay } from "./desk";
import { CERTAINTY_LABEL, certaintyOf, playLabel } from "./desk";

export type Category = "risk" | "foul" | "rotation" | "correction" | "other";
export type NoticeFilter = "all" | "unread" | "risk" | "foul" | "rotation" | "correction";

export const NOTICE_FILTERS: readonly { value: NoticeFilter; label: string }[] = [
  { value: "all", label: "All alerts" },
  { value: "unread", label: "Unread" },
  { value: "risk", label: "Risk" },
  { value: "foul", label: "Fouls" },
  { value: "rotation", label: "Rotation" },
  { value: "correction", label: "Corrections" },
];

export interface Notice {
  id: string;
  ts: string;
  slate_date: string;
  category: Category;
  /** "alert" (sent), "observation" (noticed) or "status" (the desk's own state). */
  source: "alert" | "observation" | "status";
  title: string;
  /** Stored text; null when none was stored. */
  text: string | null;
  /** Said in words when the text is not stored. */
  fallback: string;
  matchup: string | null;
  when: string | null;
  certainty: string | null;
  link: string | null;
}

export function categoryOfKind(kind: string): Category {
  const k = kind.toLowerCase();
  if (/correct|revis/.test(k)) return "correction";
  if (/foul/.test(k)) return "foul";
  if (/rotation|exit|minutes|bench|return|back|window/.test(k)) return "rotation";
  if (/risk|kill|review|discipline|drawdown|clv/.test(k)) return "risk";
  return "other";
}

function fromObservation(o: DeskObservation): Notice {
  return {
    id: `obs:${o.id}`, ts: o.ts, slate_date: o.slate_date, category: categoryOfKind(o.kind), source: "observation", title: o.title, text: o.text,
    fallback: "No text was stored for this observation: the title is the stored description.",
    matchup: o.matchup, when: o.when_text, certainty: CERTAINTY_LABEL[certaintyOf(o)], link: "#/signals",
  };
}

/** An alert described from what is stored: its kind, and the observations and plays it links. Never from invented text. */
function fromAlert(a: DeskAlert, obs: Map<string, DeskObservation>, plays: Map<string, DeskPlay>): Notice {
  const linkedObs = a.observation_ids.map((id) => obs.get(id)).filter((o): o is DeskObservation => o !== undefined);
  const linkedPlays = a.play_ids.map((id) => plays.get(id)).filter((p): p is DeskPlay => p !== undefined);
  const first = linkedObs[0];
  const bits = [...linkedObs.slice(0, 2).map((o) => o.title), ...linkedPlays.slice(0, 2).map((p) => playLabel(p))];
  const extra = linkedObs.length + linkedPlays.length - bits.length;
  const kindWord = a.kind.replace(/^ingame_/, "").replace(/_/g, " ");
  return {
    id: `alert:${a.id}`, ts: a.ts, slate_date: a.slate_date, category: categoryOfKind(a.kind), source: "alert", title: a.title,
    text: a.text ?? first?.text ?? null,
    fallback: `Sent as a "${kindWord}" alert${a.mode ? ` (${a.mode})` : ""}.${bits.length ? ` Linked: ${bits.join("; ")}${extra > 0 ? ` and ${extra.toString()} more` : ""}.` : ""} The message text was not stored.`,
    matchup: first?.matchup ?? linkedPlays[0]?.matchup ?? null, when: first?.when_text ?? null,
    certainty: first ? CERTAINTY_LABEL[certaintyOf(first)] : null, link: "#/signals",
  };
}

/**
 * Alerts and observations newest first. An observation that an alert already links is shown through that alert, not twice.
 * The kill switch, when it is on, leads as a risk notice, labelled with its own population (JARVIS's rule), which is not
 * the all-settled-plays closing-line value shown on Model History.
 */
export function buildNotices(desk: Desk): Notice[] {
  const obs = new Map(desk.observations.map((o) => [o.id, o]));
  const plays = new Map(desk.plays.map((p) => [p.play_id, p]));
  const linked = new Set(desk.alerts.flatMap((a) => a.observation_ids));
  const list = [
    ...desk.alerts.map((a) => fromAlert(a, obs, plays)),
    ...desk.observations.filter((o) => !linked.has(o.id) && o.kind !== "risk").map(fromObservation),
  ];
  list.sort((a, b) => b.ts.localeCompare(a.ts));
  const ks = desk.status.kill_switch;
  if (ks.on || desk.status.model_review) {
    const clv =
      ks.mean_clv_cents !== null && ks.n !== null
        ? `JARVIS's own kill-switch rule sees mean closing-line value ${ks.mean_clv_cents.toFixed(2)}c across ${ks.n.toString()} bets (its own sample, not the all-settled figure on Model History). `
        : "";
    list.unshift({
      id: `status:review:${ks.since ?? "now"}`, ts: ks.since ?? desk.built_at, slate_date: (ks.since ?? desk.built_at).slice(0, 10), category: "risk", source: "status",
      title: "The model is in review", text: `${clv}${ks.on ? "The kill switch is latched" : "The gate is closed"}; the model does not back a bet.${ks.note ? ` Log: ${ks.note.replace(/[.\s]+$/, "")}.` : ""}`,
      fallback: "", matchup: null, when: null, certainty: "Reported fact", link: "#/history",
    });
  }
  return list;
}

export function filterNotices(list: readonly Notice[], f: NoticeFilter, read: ReadonlySet<string>): Notice[] {
  if (f === "all") return [...list];
  if (f === "unread") return list.filter((n) => !read.has(n.id));
  return list.filter((n) => n.category === f);
}

export interface NotifyPrefs {
  fouls: boolean;
  risk: boolean;
  corrections: boolean;
}
export const NOTIFY_DEFAULTS: NotifyPrefs = { fouls: true, risk: true, corrections: true };

/** Does the bell count this notice? Foul and rotation notices follow the "fouls and rotation" switch. */
export function counts(n: Notice, p: NotifyPrefs): boolean {
  if (n.category === "foul" || n.category === "rotation") return p.fouls;
  if (n.category === "risk") return p.risk;
  if (n.category === "correction") return p.corrections;
  return true;
}

export function unreadCount(list: readonly Notice[], read: ReadonlySet<string>, p: NotifyPrefs): number {
  return list.filter((n) => !read.has(n.id) && counts(n, p)).length;
}

/** Mark ids as read; a copy, capped so the stored list cannot grow without end. */
export function markRead(read: ReadonlySet<string>, ids: readonly string[], cap = 500): Set<string> {
  const next = new Set(read);
  for (const id of ids) next.add(id);
  return new Set([...next].slice(-cap));
}

/**
 * Notices from before the newest night in the list start as read, so a first visit does not open on hundreds of unread
 * alerts. The desk's own status notice (the kill switch) is never marked read for you.
 */
export function withOlderRead(list: readonly Notice[], read: ReadonlySet<string>): Set<string> {
  const out = new Set(read);
  const newest = list.filter((n) => n.source !== "status").map((n) => n.slate_date).sort().at(-1);
  if (newest === undefined) return out;
  for (const n of list) if (n.source !== "status" && n.slate_date < newest) out.add(n.id);
  return out;
}

export function newestNight(list: readonly Notice[]): string | null {
  return list.filter((n) => n.source !== "status").map((n) => n.slate_date).sort().at(-1) ?? null;
}
