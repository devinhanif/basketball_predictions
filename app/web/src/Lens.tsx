import { LENSES, setPref, useLens, type Lens } from "./prefs";

export const LENS_LABEL: Record<Lens, string> = { learn: "Learn", explore: "Explore", market: "Market" };

/** One line each, in words a first-time visitor can act on. */
export const LENS_SAY: Record<Lens, string> = {
  learn: "How the game and the forecast work. The market side stays out of view on this screen.",
  explore: "Move the line or the minutes and watch the forecast respond. Nothing is saved.",
  market: "The market side: posted prices, edges and fees from the stored snapshots.",
};

/** The three lenses on one screen. The choice is remembered on this device (default Learn). */
export function LensSwitch(props: { compact?: boolean }) {
  const lens = useLens();
  return (
    <div class="lens">
      <div class="seg wide lens-seg" role="group" aria-label="Lens">
        {LENSES.map((l) => (
          <button
            key={l}
            type="button"
            class="seg-btn"
            aria-pressed={lens === l}
            onClick={() => {
              setPref("lens", l);
            }}
          >
            {LENS_LABEL[l]}
          </button>
        ))}
      </div>
      {props.compact ? null : (
        <p class="small muted lens-say" aria-live="polite">
          {LENS_SAY[lens]}
        </p>
      )}
    </div>
  );
}

/** A stable id from the screen's name, so two of these on one page never share an id. */
export function marketOnlyId(name: string): string {
  return `mo-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
}

/**
 * Shown in place of a screen that is all prices and edges when the lens is Learn or Explore. It is the page's h1 when it
 * stands in for the whole screen (the default); a page that has its own h1 passes `level={2}`.
 */
export function MarketOnly(props: { name: string; what: string; level?: 1 | 2 | 3 }) {
  const H = props.level === 2 ? "h2" : props.level === 3 ? "h3" : "h1";
  const hid = marketOnlyId(props.name);
  return (
    <section class="panel empty market-only" aria-labelledby={hid}>
      <H id={hid} class="market-only-title">{props.name} is a Market screen</H>
      <p>{props.what} It is hidden in the Learn and Explore lenses. The optional what-if for a number you type, inside Explore, is the one exception.</p>
      <button
        type="button"
        class="btn primary"
        onClick={() => {
          setPref("lens", "market");
        }}
      >
        Switch to Market
      </button>
      <p class="small muted">Nothing is lost by switching. You can switch back from any game or player page, or from the top of Discover.</p>
    </section>
  );
}
