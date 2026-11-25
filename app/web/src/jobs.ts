// Colour as navigation. One colour per job a visitor comes to do; every screen wears exactly one of the five. This file
// is the single map from a route to its job, plus the words and icon each job carries (colour never stands alone: every
// job has a name and an icon). The colour values themselves are tokens in colour.css; nothing here holds a hex.
import type { IconName } from "./icons";
import type { Route } from "./router";

export type Job = "tonight" | "teams" | "props" | "slip" | "explore";
export const JOBS_ORDER: readonly Job[] = ["tonight", "teams", "props", "slip", "explore"];

export interface JobDef {
  /** Tab label (short, upper-case in CSS). */
  tab: string;
  /** The screen title the header wears. */
  title: string;
  /** The question the visitor came with, as one plain sentence on the home door. */
  sentence: string;
  icon: IconName;
  href: string;
}

export const JOBS: Record<Job, JobDef> = {
  tonight: { tab: "Tonight", title: "Tonight", sentence: "What is on tonight, and where do you want to go?", icon: "jobTonight", href: "#/" },
  teams: { tab: "Teams", title: "Team picks", sentence: "Who wins, and by how much.", icon: "jobTeams", href: "#/teams" },
  props: { tab: "Props", title: "Player props", sentence: "One player, one line, one chance.", icon: "jobProps", href: "#/props" },
  slip: { tab: "Slip", title: "Slip check", sentence: "Do your legs help or fight each other?", icon: "jobSlip", href: "#/parlay" },
  explore: { tab: "Explore", title: "Explore", sentence: "If the game goes like this, what changes?", icon: "jobExplore", href: "#/combo" },
};

/**
 * The job each route belongs to. Live tracking has no hue of its own: a live game page is TEAMS (the live player card
 * inside it is wrapped in a PROPS scope by the page). Reference and housekeeping screens (record, scorecard, tests, terms,
 * help, alerts, preferences, admin, not-found, the betting module) are TONIGHT: neutral, with the four-colour bar.
 */
export function jobOfRoute(route: Route): Job {
  switch (route.name) {
    case "teams":
    case "game":
    case "live":
      return "teams";
    case "props":
    case "leg":
    case "swipe":
    case "picks":
    case "signals":
      return "props";
    case "source":
      return route.playerId === null ? "teams" : "props";
    case "parlay":
    case "hedge":
      return "slip";
    case "combo":
      return "explore";
    default:
      return "tonight";
  }
}

/** Every route name the router can produce (the type makes a missing one a compile error), so a test can prove the map is total. */
const ALL: Record<Route["name"], true> = {
  today: true, game: true, leg: true, swipe: true, teams: true, props: true, picks: true, parlay: true, settings: true, combo: true,
  signals: true, hedge: true, history: true, record: true, live: true, alerts: true, admin: true, source: true, scorecard: true,
  tests: true, help: true, terms: true, bet: true, notfound: true,
};
export const ROUTE_NAMES = Object.keys(ALL) as Route["name"][];
