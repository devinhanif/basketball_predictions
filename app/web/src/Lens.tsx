import { LENSES, setPref, useLens, type Lens } from "./prefs";

/** The two settings of "Market side", in the words on the switch. */
export const LENS_LABEL: Record<Lens, string> = { base: "Hidden", market: "Shown" };

/** One line each, in words a first-time visitor can act on. */
export const LENS_SAY: Record<Lens, string> = {
  base: "Market side hidden: how the game and the forecast work, and what changes if the game goes a certain way.",
  market: "Market side shown: posted prices, edges and fees, as stored.",
};

/** "Market side: hidden / shown", a two-way switch. It lives in the profile menu, not in the page bodies. The choice is remembered on this device. */
export function LensSwitch() {
  const lens = useLens();
  return (
    <div class="seg lens-seg" role="group" aria-label="Market side">
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
  );
}

/** A stable id from the screen's name, so two of these on one page never share an id. */
export function marketOnlyId(name: string): string {
  return `mo-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
}

/**
 * Shown in place of a screen that is all prices and edges while the market side is hidden. It is the page's h1 when it
 * stands in for the whole screen (the default); a page that has its own h1 passes `level={2}`.
 */
export function MarketOnly(props: { name: string; what: string; level?: 1 | 2 | 3 }) {
  const H = props.level === 2 ? "h2" : props.level === 3 ? "h3" : "h1";
  const hid = marketOnlyId(props.name);
  return (
    <section class="panel empty market-only" aria-labelledby={hid}>
      <H id={hid} class="market-only-title">{props.name} is a Market screen</H>
      <p>{props.what} It stays out of view while the market side is hidden.</p>
      <button
        type="button"
        class="btn primary"
        onClick={() => {
          setPref("lens", "market");
        }}
      >
        Switch to Market
      </button>
      <p class="small muted">Nothing is lost by switching. You can switch back from the profile menu.</p>
    </section>
  );
}
