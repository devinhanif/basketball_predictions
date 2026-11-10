import { LENSES, setPref, useLens, type Lens } from "./prefs";

export const LENS_LABEL: Record<Lens, string> = { learn: "Learn", explore: "Explore", market: "Market" };

/** One line each, in words a first-time visitor can act on. */
export const LENS_SAY: Record<Lens, string> = {
  learn: "How the game and the forecast work. No prices, no edges, no fees on this screen.",
  explore: "Move the line, the minutes or a price and watch the forecast respond. Nothing is saved.",
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

/** Shown in place of a screen that is all prices and edges when the lens is Learn or Explore. */
export function MarketOnly(props: { name: string; what: string }) {
  return (
    <section class="panel empty" aria-labelledby="mo-h">
      <h1 id="mo-h">{props.name} is a Market screen</h1>
      <p>
        {props.what} The Learn and Explore lenses leave prices, edges and fees out, so this screen is hidden while you are in them.
      </p>
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
