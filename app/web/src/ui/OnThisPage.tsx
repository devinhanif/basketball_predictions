import { useEffect, useRef, useState } from "preact/hooks";

interface Item {
  label: string;
  el: HTMLElement;
}

/** The visible section headings (h2) of the page, in order: not the intro's, not inside a closed fold or a dialog. */
export function sectionHeadings(root: ParentNode): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>("h2")].filter(
    (h) =>
      h.closest(".intro, [role=dialog], .otp") === null &&
      h.getClientRects().length > 0 &&
      h.textContent.trim() !== "",
  );
}

/** Short enough for one chip; the whole heading stays in `title`. */
export function chipLabel(text: string, max = 42): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const sp = cut.lastIndexOf(" ");
  return `${(sp > max * 0.6 ? cut.slice(0, sp) : cut).replace(/[\s,:;.]+$/, "")}…`;
}

/**
 * "On this page": a jump list built from the page's own section headings once they render (so it never names a section
 * that is not there). Buttons, not links: the address is the route, so a jump scrolls and moves focus to the heading.
 * Shown when there are at least `min` sections; open on wide screens, folded on phones.
 */
export function OnThisPage(props: { min?: number }) {
  const box = useRef<HTMLElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [wide] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 760px)").matches,
  );
  useEffect(() => {
    const main =
      box.current?.closest("main") ?? document.getElementById("main");
    if (!main) return;
    let t = 0;
    const read = (): void => {
      window.clearTimeout(t);
      t = window.setTimeout(() => {
        const next = sectionHeadings(main).map((el) => ({
          el,
          label: el.textContent.replace(/\s+/g, " ").trim(),
        }));
        setItems((old) =>
          old.length === next.length &&
          old.every((o, i) => {
            const n = next.at(i);
            return n !== undefined && o.el === n.el && o.label === n.label;
          })
            ? old
            : next,
        );
      }, 120);
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(main, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["open"],
    });
    return () => {
      window.clearTimeout(t);
      mo.disconnect();
    };
  }, []);
  const jump = (el: HTMLElement): void => {
    if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
    // A section inside a closed fold opens it first, so the jump lands on something you can see.
    for (let d = el.closest("details"); d; d = d.parentElement?.closest("details") ?? null) d.open = true;
    el.scrollIntoView({
      block: "start",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
    el.focus({ preventScroll: true });
  };
  const show = items.length >= (props.min ?? 4);
  return (
    <nav class="otp" aria-label="On this page" ref={box} hidden={!show}>
      {show ? (
        <details open={wide}>
          <summary>
            On this page <span class="muted num">({items.length})</span>
          </summary>
          <ul>
            {items.map((it, i) => (
              <li key={`${String(i)}-${it.label}`}>
                <button
                  type="button"
                  class="otp-link"
                  title={it.label}
                  onClick={() => {
                    jump(it.el);
                  }}
                >
                  {chipLabel(it.label)}
                </button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </nav>
  );
}
