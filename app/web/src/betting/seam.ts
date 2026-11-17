// The betting module's contract with the rest of the app (the "seam"). The app reaches the module only through code
// guarded by BETTING from ../features, so with the switch off none of this ships.
//
// Money-safety rules for everything that plugs in here: analysis only, not advice; nothing places, routes or cancels a
// bet on any venue; no credentials of any kind; all state stays in this browser; requests go only to this app's own
// read-only API (relative paths under /api/).
import type { ComponentChildren, FunctionComponent } from "preact";
import type { IconName } from "../icons";
import type { Side } from "../legTypes";

/** Every betting address starts with this path segment: #/bet, #/bet/compare, #/bet/slip, ... */
export const BET_PREFIX = "bet";

/** The router's route for a betting screen: the segments after "bet" (empty for #/bet) and the raw query text. */
export interface BetRoute {
  name: "bet";
  path: readonly string[];
  query: string;
}

/** One betting screen. `path` is the part after #/bet/ ("" is #/bet itself); one plain lowercase segment or "". */
export interface BettingRouteDef {
  path: string;
  /** The breadcrumb and page title. */
  title: string;
  /** The screen also takes one id segment after its path (#/bet/ideas/<id>). */
  param?: boolean;
  render: (route: BetRoute) => ComponentChildren;
}

/** One entry in the sidebar, the menu sheet and the search palette. */
export interface BettingNavEntry {
  href: string;
  label: string;
  icon: IconName;
  /** The search palette's one-line description. */
  sub: string;
  /** Extra words the search palette matches. */
  words?: string;
  /** Listed only in the Market lens (as the Hedge Finder and Signals are). The route itself is reachable either way. */
  marketOnly: boolean;
}

/** What a page hands the "add to slip" slot: the leg as the page shows it. Nothing here is a price. */
export interface AddToSlipProps {
  gameId: string;
  playerId: number | null;
  stat: string | null;
  side: Side | null;
  line: number | null;
  /** Which page rendered the slot, for the slip's provenance note ("leg", "game", ...). */
  from: string;
}

/** What the shell hands the persistent slip panel slot. */
export interface ShellPanelProps {
  /** True while a betting screen is open. */
  onBetRoute: boolean;
}

/** The module's whole contribution to the app. */
export interface BettingModule {
  routes: readonly BettingRouteDef[];
  nav: readonly BettingNavEntry[];
  /** Rendered by pages next to a leg (through AddToSlipSlot in ../BetSlots). Returns null until a slip exists. */
  AddToSlip: FunctionComponent<AddToSlipProps>;
  /** Rendered once by the shell beside the page (through BetShellPanel in ../BetSlots). Returns null until built. */
  ShellPanel: FunctionComponent<ShellPanelProps>;
}
