import type { ComponentChildren } from "preact";
import { useRef } from "preact/hooks";
import { tabKey } from "./logic";

export interface TabDef<K extends string> {
  key: K;
  label: ComponentChildren;
}

/**
 * A tab list with a roving tabindex: one tab in the Tab order, arrow keys move and select, Home and End jump.
 * The panel is the caller's: give it `id={panelId}` and `role="tabpanel"` (or use TabPanel).
 */
export function Tabs<K extends string>(props: { label: string; tabs: readonly TabDef<K>[]; value: K; onChange: (k: K) => void; idBase: string; class?: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const at = Math.max(0, props.tabs.findIndex((t) => t.key === props.value));
  return (
    <div class={`tabs${props.class ? ` ${props.class}` : ""}`} role="tablist" aria-label={props.label}>
      {props.tabs.map((t, i) => (
        <button
          key={t.key}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="tab"
          class="tab"
          id={`${props.idBase}-tab-${t.key}`}
          aria-selected={i === at}
          aria-current={i === at ? "true" : undefined}
          aria-controls={`${props.idBase}-panel`}
          tabIndex={i === at ? 0 : -1}
          onClick={() => {
            props.onChange(t.key);
          }}
          onKeyDown={(e) => {
            const n = tabKey(e.key, i, props.tabs.length);
            const next = n === null ? undefined : props.tabs[n];
            if (n === null || next === undefined) return;
            e.preventDefault();
            props.onChange(next.key);
            refs.current[n]?.focus();
          }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function TabPanel(props: { idBase: string; value: string; children: ComponentChildren }) {
  return (
    <div role="tabpanel" id={`${props.idBase}-panel`} aria-labelledby={`${props.idBase}-tab-${props.value}`} tabIndex={0} class="tabpanel">
      {props.children}
    </div>
  );
}
