// Where the slip shows outside its own page, as a plain decision (tested without a browser). The shell's shared side panel
// does the showing: a rail beside the page from 1280 px (the page narrows; nothing is covered), a bottom sheet below that
// (the shell's own "Slip · N" button opens it; it traps focus, closes on Escape and returns focus). On a wide screen the
// viewer can hide the rail; the module's floating "Slip N" button brings it back, and that choice is remembered.

export type SlipPanelPlan = "none" | "rail" | "launcher" | "sheet";

export function slipPanelPlan(x: {
  lens: string;
  /** The viewer is known and the workspace has loaded, or failed to load (the panel then says so). */
  ready: boolean;
  wide: boolean;
  /** The remembered open/closed choice for the wide rail. */
  open: boolean;
  /** On #/bet/slip the page is the slip; a second copy beside it would only repeat it. */
  onSlipPage: boolean;
}): SlipPanelPlan {
  if (x.lens !== "market" || !x.ready || x.onSlipPage) return "none";
  if (!x.wide) return "sheet";
  return x.open ? "rail" : "launcher";
}

/** True for the slip page's own address (with or without a query). */
export function isSlipPage(hash: string): boolean {
  return /^#\/bet\/slip(?:\?.*)?$/.test(hash);
}
