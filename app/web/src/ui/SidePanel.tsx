import { useEffect, useRef, useState } from "preact/hooks";
import { Icon } from "../icons";
import { SIDEPANEL_RAIL_MIN, panelIsModal, panelLayout, trapIndex } from "./logic";
import { getSidePanel, subscribeSidePanel, type SidePanelDef } from "./sidePanelStore";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function useSlot(): SidePanelDef | null {
  const [def, setDef] = useState(getSidePanel);
  useEffect(
    () =>
      subscribeSidePanel(() => {
        setDef(getSidePanel());
      }),
    [],
  );
  return def;
}

function useWide(): boolean {
  const q = `(min-width: ${SIDEPANEL_RAIL_MIN.toString()}px)`;
  const [wide, setWide] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = (): void => {
      setWide(m.matches);
    };
    m.addEventListener("change", on);
    return () => {
      m.removeEventListener("change", on);
    };
  }, [q]);
  return wide;
}

/**
 * The shell's side panel. Empty slot: renders nothing at all. Wide screens (1280 px and up): a persistent right-hand rail
 * that takes its own column, so the page narrows beside it instead of being covered. Narrower: a button that opens a
 * bottom sheet (a modal dialog: focus moves in and stays in, Escape or the close button closes it, focus returns to the
 * button, the page behind does not scroll, and the sheet keeps clear of the phone's safe areas).
 */
export function SidePanelHost() {
  const def = useSlot();
  const wide = useWide();
  const [open, setOpen] = useState(false);
  const sheet = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const layout = panelLayout(wide ? SIDEPANEL_RAIL_MIN : 0, def !== null, open);
  const modal = panelIsModal(layout);

  useEffect(() => {
    if (def === null) setOpen(false);
  }, [def]);

  useEffect(() => {
    document.documentElement.classList.toggle("has-sidepanel", layout === "rail");
    return () => {
      document.documentElement.classList.remove("has-sidepanel");
    };
  }, [layout]);

  useEffect(() => {
    if (!modal) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const first = sheet.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const btn = opener.current;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        setOpen(false);
        return;
      }
      if (e.key !== "Tab" || !sheet.current) return;
      const list = [...sheet.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
      const next = list[trapIndex(list.length, list.indexOf(document.activeElement as HTMLElement), e.shiftKey)];
      if (next) {
        e.preventDefault();
        next.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = prevOverflow;
      if (btn?.isConnected) btn.focus();
    };
  }, [modal]);

  if (def === null) return null;
  const titleId = "sidepanel-title";
  if (layout === "rail")
    return (
      <aside class="sidepanel rail" aria-labelledby={titleId}>
        <div class="sidepanel-head">
          <h2 id={titleId}>{def.title}</h2>
        </div>
        <div class="sidepanel-body">{def.content()}</div>
      </aside>
    );
  return (
    <>
      <button
        type="button"
        class="sidepanel-open"
        ref={opener}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setOpen(true);
        }}
      >
        <Icon name="panel" />
        <span>{def.openLabel ?? def.title}</span>
      </button>
      {open ? (
        <div
          class="sidepanel-scrim"
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
        >
          <div class="sidepanel sheet" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={sheet}>
            <div class="sidepanel-handle" aria-hidden="true" />
            <div class="sidepanel-head">
              <h2 id={titleId}>{def.title}</h2>
              <button
                type="button"
                class="icon-btn"
                aria-label={`Close ${def.title}`}
                onClick={() => {
                  setOpen(false);
                }}
              >
                <Icon name="close" />
              </button>
            </div>
            <div class="sidepanel-body">{def.content()}</div>
          </div>
        </div>
      ) : null}
    </>
  );
}
