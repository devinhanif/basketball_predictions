// The betting screens' fixed sentences, in one place. Every screen that shows prices, cost or hedge output prints
// MONEY_NOTE; where arithmetic on typed prices is shown it also prints ARITHMETIC_NOTE. Tests and the browser journey look
// for these exact strings.

import { IS_ARTIFACT } from "../env";

/** On every screen that shows prices, cost or hedge output. */
export const MONEY_NOTE = "Analysis only, not advice: no orders are placed.";

/** Where the screen shows arithmetic on prices (cost, payouts, outcome rows, hedge sizes). */
export const ARITHMETIC_NOTE = "This is arithmetic on the prices you entered.";

/** The settlement caveat on every side-by-side comparison of venues. */
export const SETTLEMENT_NOTE =
  "The venues' settlement rules (did-not-play, voids, overtime) are not verified as equivalent. A matching player and line does not make two prices the same bet.";

/** What "Mark slip as placed" does, said in the confirmation dialog. */
export const PLACED_CONFIRM =
  "This only writes a note in this browser that you already placed these legs somewhere else. Nothing is sent to any venue, no account is touched and no orders are placed.";

/** A recorded position's remove button: removing the record does not touch the real wager. */
export const REMOVE_RECORD_NOTE = "Removing a recorded position deletes this browser's note only. It does not cancel or change anything at the venue.";

/** With the API's betting switch off (a typed 404), stored prices are simply not offered. */
/** Said where stored quotes cannot be read: the single-file snapshot has no server at all, so it says so plainly. */
export const NO_SNAPSHOTS = IS_ARTIFACT
  ? "Stored snapshots are not available in this snapshot; enter prices by hand."
  : "Stored snapshots are not available from this server; enter prices by hand.";

/** The demo button's label and what it does. */
export const DEMO_LABEL = "Load the labelled demo";
export const DEMO_NOTE = "The demo is invented sample prices on sample games from 2024. Every record it creates stays labelled sample.";

/** The page titles (also the strings the flag-parity check looks for in an ON build). */
export const TITLE_HOME = "Betting tools";
export const TITLE_SLIP = "Your rolling slip";
export const TITLE_COMPARE = "Compare venues";
export const TITLE_HEDGE = "Slip Hedge Finder";
export const TITLE_IDEAS = "Saved ideas";
export const TITLE_ANALYSIS = "Prop analysis";

/** Beside every paper net: what it is made of. */
export const PAPER_NET_NOTE = "Paper net: arithmetic on the prices you saved and the outcomes you entered (typed by hand, unverified); not a venue's settlement.";

/** Provenance words, said the same way everywhere. */
export const PROVENANCE_WORD = {
  live: "live",
  delayed: "delayed",
  manual: "manual",
  sample: "sample",
} as const;

export const PROVENANCE_MEANING = {
  live: "A price read as it changed.",
  delayed: "A stored snapshot from this app's export; it may have moved since.",
  manual: "A price you typed. It is your entry, not a verified quote.",
  sample: "An invented demo price. Not a real quote.",
} as const;
