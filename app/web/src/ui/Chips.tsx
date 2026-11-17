export interface ChipOption {
  value: string;
  label: string;
  /** An already-formatted count shown after the label (from stored rows; never computed here). */
  count?: string;
}

/**
 * Filter pills: a labelled group of toggle buttons (`aria-pressed`), one chosen at a time. The chosen pill is marked by
 * a tick as well as by colour.
 */
export function FilterChips(props: { label: string; options: readonly ChipOption[]; value: string; onChange: (v: string) => void; class?: string }) {
  return (
    <div class={`fchips${props.class ? ` ${props.class}` : ""}`} role="group" aria-label={props.label}>
      {props.options.map((o) => {
        const on = o.value === props.value;
        return (
          <button
            key={o.value}
            type="button"
            class="fchip"
            aria-pressed={on}
            onClick={() => {
              props.onChange(o.value);
            }}
          >
            {on ? (
              <svg class="fchip-tick" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 12l4 4 10-10" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            ) : null}
            <span>{o.label}</span>
            {o.count !== undefined ? <span class="fchip-n num">{o.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
