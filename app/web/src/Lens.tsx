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
