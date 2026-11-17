// Data the shell needs on every screen: the notices (for the bell and the Alerts badge) and the search index.
import { useEffect, useMemo, useState } from "preact/hooks";
import { api } from "./api";
import { buildNotices, unreadCount, withOlderRead, type Notice } from "./alerts";
import { deskOf, useDesk } from "./deskStore";
import { canSeeAdmin, useIdentity } from "./identity";
import { gameHref, legHref, topHref, type TopName } from "./router";
import type { SearchItem } from "./search";
import { tipTime } from "./format";
import { useUserState } from "./userState";
import { IS_ARTIFACT } from "./env";
import { useLens } from "./prefs";
import { termHref, termsFor } from "./terms";
import { isLegStat } from "./legTypes";
import { BETTING } from "./features";
import { BETTING_MODULE } from "./betting";

export interface Notices {
  list: Notice[];
  unread: number;
  hasDesk: boolean;
}

export function useNotices(): Notices {
  const desk = useDesk();
  const us = useUserState();
  return useMemo(() => {
    const d = deskOf(desk);
    if (!d) return { list: [], unread: 0, hasDesk: false };
    const list = buildNotices(d);
    return { list, unread: unreadCount(list, withOlderRead(list, us.read), us.notify), hasDesk: true };
  }, [desk, us]);
}

/** Is the Admin studio visible to this viewer (owner/editor in the shared page; the private build locally)? */
export function useIsAdmin(): boolean {
  const id = useIdentity();
  const desk = useDesk();
  return canSeeAdmin(id, deskOf(desk)?.privacy === "private");
}

/** Screens whose whole subject is prices and edges: they leave the nav and the search outside the Market lens. */
export const MARKET_ONLY: readonly TopName[] = ["signals", "hedge", "history"];

export const SCREEN_ITEMS: SearchItem[] = [
  { kind: "screen", label: "Discover", sub: "Tonight's answer, the closest look, the games", href: topHref("today"), words: "home today" },
  { kind: "screen", label: "Signals", sub: "What the desk noticed, with certainty labels", href: topHref("signals"), marketOnly: true },
  { kind: "screen", label: "Hedge Finder", sub: "Position lab: what a paper position pays in every outcome", href: topHref("hedge"), words: "position lab scenario", marketOnly: true },
  { kind: "screen", label: "Model History", sub: "Closing-line value, record, calibration, ledger", href: topHref("history"), words: "clv ledger record", marketOnly: true },
  { kind: "screen", label: "Alerts", sub: "Notification center", href: topHref("alerts"), words: "notifications bell" },
  { kind: "screen", label: "Swipe", sub: "Over or under, graded after", href: topHref("swipe") },
  { kind: "screen", label: "My picks", sub: "Your paper picks and your friends'", href: topHref("picks") },
  { kind: "screen", label: "Preferences", sub: "Theme, detail level, odds format, density, notifications", href: topHref("settings"), words: "settings theme mode odds decimal american compact" },
  { kind: "screen", label: "Key terms", sub: "Plain definitions of every word used here", href: topHref("terms"), words: "glossary definitions help" },
  ...(IS_ARTIFACT
    ? []
    : [
        { kind: "screen" as const, label: "Live", sub: "Scores, plays and player stats while games are on", href: topHref("live"), words: "in game score" },
        { kind: "screen" as const, label: "Track record", sub: "Replay, calibration and guess the line", href: topHref("record"), words: "history replay calibration guess" },
        { kind: "screen" as const, label: "Parlay of the day", sub: "The engine's verdict for the newest night", href: "#/parlay", words: "parlay combo ticket" },
      ]),
  // The betting module's screens (none in a build with the switch off).
  ...(BETTING ? BETTING_MODULE.nav.map((n): SearchItem => ({ kind: "screen", label: n.label, sub: n.sub, href: n.href, words: n.words, marketOnly: n.marketOnly })) : []),
];

/** The screens the search lists in a lens: the Market-only ones are left out of Learn and Explore, as in the nav. */
export function screensFor(market: boolean, items: readonly SearchItem[] = SCREEN_ITEMS): SearchItem[] {
  return items.filter((i) => market || i.marketOnly !== true);
}

/** The index: screens now; games and players once the nights load (and the newest night's whole rosters after that). */
export function useSearchIndex(active: boolean, isAdmin: boolean): SearchItem[] {
  const [items, setItems] = useState<SearchItem[]>([]);
  const market = useLens() === "market";
  useEffect(() => {
    if (!active) return;
    const run = { live: true };
    void (async () => {
      const out: SearchItem[] = [];
      const seen = new Set<string>();
      const add = (i: SearchItem): void => {
        const k = `${i.kind}|${i.href}|${i.label}`;
        if (!seen.has(k)) {
          seen.add(k);
          out.push(i);
        }
      };
      try {
        const nights = (await api.slates()).data;
        const newest = [...nights].sort((a, b) => b.date.localeCompare(a.date));
        for (const n of newest) {
          const s = (await api.slate(n.date)).data;
          for (const g of s.games) {
            add({ kind: "game", label: `${g.away.abbr} at ${g.home.abbr}`, sub: `${n.date} · ${tipTime(g.tip_utc)}`, href: gameHref(g.game_id), words: `${g.away.city} ${g.away.name} ${g.home.city} ${g.home.name}` });
            for (const p of g.key_props) add({ kind: "player", label: p.name, sub: `${p.team} · ${n.date}`, href: legHref(g.game_id, p.player_id, "pts"), words: g.away.name + " " + g.home.name });
          }
        }
        if (run.live) setItems([...out]);
        const first = newest[0];
        if (first) {
          const s = (await api.slate(first.date)).data;
          for (const g of s.games) {
            try {
              const d = (await api.game(g.game_id)).data;
              for (const p of d.players) {
                const stat = Object.keys(p.props)[0] ?? "pts";
                add({ kind: "player", label: p.name, sub: `${p.side === "home" ? d.home.abbr : d.away.abbr} · ${first.date}`, href: isLegStat(stat) ? legHref(g.game_id, p.player_id, stat) : gameHref(g.game_id) });
              }
            } catch {
              /* a game page that is not loaded simply adds no players */
            }
          }
          if (run.live) setItems([...out]);
        }
      } catch {
        /* the screens stay searchable */
      }
    })();
    return () => {
      run.live = false;
    };
  }, [active]);
  return useMemo(() => {
    // Key terms follow the lens: the market's terms are listed in the Market lens only.
    const terms: SearchItem[] = termsFor(market).map((t) => ({ kind: "term", label: t.term, sub: t.says.length > 70 ? `${t.says.slice(0, 68)}...` : t.says, href: termHref(t.id), words: t.also ?? "" }));
    return [...screensFor(market), ...(isAdmin ? [{ kind: "screen" as const, label: "Admin studio", sub: "Photos, activity, people, data tools", href: topHref("admin") }] : []), ...items, ...terms];
  }, [items, isAdmin, market]);
}
