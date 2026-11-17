// The betting tools module: its routes, its nav entry, and the two slots other code renders (add-to-slip beside a leg,
// a persistent slip panel in the shell). Imported ONLY by code guarded by BETTING (../features): router.ts, App.tsx,
// Shell.tsx, shellData.ts and BetSlots.tsx. With the switch off the bundler drops this file and all it imports.
//
// To add a screen: write it under src/betting/, add a BettingRouteDef to ROUTES (its path is the segment after #/bet/),
// and, if it belongs in the nav, a BettingNavEntry to NAV. Add a distinctive string of it to markers.json so the parity
// check proves the OFF build leaves it out.
import { h } from "preact";
import { BetHome } from "./Home";
import { AddToSlip } from "./AddToSlip";
import { ComparePage } from "./Compare";
import { HedgePage } from "./Hedge";
import { SlipPage, SlipPanel } from "./Slip";
import { IdeasPage } from "./Ideas";
import { BET_PREFIX, type BetRoute, type BettingModule, type BettingNavEntry, type BettingRouteDef } from "./seam";

export type { AddToSlipProps, BetRoute, BettingModule, BettingNavEntry, BettingRouteDef, ShellPanelProps } from "./seam";
export { BET_PREFIX } from "./seam";

/** The hash for a betting screen; `path` "" is #/bet. */
export function betHref(path: string = ""): string {
  return path === "" ? `#/${BET_PREFIX}` : `#/${BET_PREFIX}/${path}`;
}

const ROUTES: readonly BettingRouteDef[] = [
  { path: "", title: "Betting tools", render: () => h(BetHome, null) },
  { path: "slip", title: "Your rolling slip", render: () => h(SlipPage, null) },
  { path: "compare", title: "Compare venues", render: (r) => h(ComparePage, { route: r }) },
  { path: "hedge", title: "Slip Hedge Finder", render: (r) => h(HedgePage, { route: r }) },
  { path: "ideas", title: "Saved ideas", param: true, render: (r) => h(IdeasPage, { route: r }) },
];

const NAV: readonly BettingNavEntry[] = [
  {
    href: "#/bet", // a literal, not betHref(): a call here would survive tree-shaking in a build with the switch off
    label: "Betting tools",
    icon: "flag",
    sub: "Analysis only, not advice: compare venues, keep a slip, size a hedge",
    words: "bet slip compare",
    marketOnly: true,
  },
  {
    href: "#/bet/compare",
    label: "Compare venues",
    icon: "swap",
    sub: "One prop's prices at each venue, side by side, with cost per $1 of payout",
    words: "compare venues prices kalshi draftkings prizepicks",
    marketOnly: true,
  },
  {
    href: "#/bet/hedge",
    label: "Slip Hedge Finder",
    icon: "hedge",
    sub: "Size the other side of a position you recorded as placed",
    words: "hedge placed positions cover",
    marketOnly: true,
  },
  {
    href: "#/bet/ideas",
    label: "Saved ideas",
    icon: "book",
    sub: "Slips saved with a thesis: prices frozen when saved, paper outcomes you record",
    words: "ideas thesis saved notebook outcomes paper",
    marketOnly: true,
  },
  {
    href: "#/bet/slip",
    label: "Your slip",
    icon: "picks",
    sub: "Planned legs, recorded positions, cost and every listed outcome",
    words: "slip rolling legs placed",
    marketOnly: true,
  },
];

export const BETTING_MODULE: BettingModule = {
  routes: ROUTES,
  nav: NAV,
  AddToSlip: (props) => h(AddToSlip, props),
  ShellPanel: () => h(SlipPanel, null),
};

/** An id segment a screen with `param` takes: plain letters, digits and - _ . : (local ids are uuids). */
const PARAM = /^[A-Za-z0-9_.:-]{1,160}$/;

/** The registered screen for a route, if there is one. */
export function betRouteDef(route: BetRoute): BettingRouteDef | null {
  const [first = "", second] = route.path;
  const def = BETTING_MODULE.routes.find((r) => r.path === first) ?? null;
  if (def === null || route.path.length > 2) return null;
  if (second === undefined) return def;
  return def.param === true && PARAM.test(second) ? def : null;
}

/**
 * The betting route for the segments after "bet" and the query text, or null (the router then shows not-found). Only
 * registered screens parse; a segment is plain lowercase letters, and the query is short and free of control characters.
 */
export function parseBet(rest: readonly string[], query: string): BetRoute | null {
  if (rest.length > 2 || (rest[0] !== undefined && !/^[a-z]{1,24}$/.test(rest[0]))) return null;
  // eslint-disable-next-line no-control-regex -- the point is to refuse control characters
  if (query.length > 200 || /[\u0000-\u001f\u007f]/.test(query)) return null;
  const route: BetRoute = { name: "bet", path: rest, query };
  return betRouteDef(route) === null ? null : route;
}

/** The breadcrumb under "Betting tools" for a route (none for #/bet itself). */
export function betCrumbs(route: BetRoute): string[] {
  const def = betRouteDef(route);
  if (def === null || def.path === "") return ["Betting tools"];
  return route.path.length > 1 ? ["Betting tools", def.title, "Idea"] : ["Betting tools", def.title];
}

/** The page for a parsed betting route (parseBet only returns registered ones). */
export function renderBet(route: BetRoute) {
  return betRouteDef(route)?.render(route) ?? null;
}
