import type { ComponentChildren } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { Icon, Logo } from "./icons";
import { trapIndex } from "./ui/logic";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * The phone and tablet menu: a drawer from the left over a scrim (the donor's mobile menu). A modal dialog while open:
 * focus moves to the current screen's link, Tab stays inside, Escape, the close button or the scrim close it, the page
 * behind does not scroll, and focus returns to the menu button. Hidden from 1024 px, where the sidebar shows.
 */
export function MenuDrawer(props: { onClose: () => void; children: ComponentChildren; note?: ComponentChildren }) {
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(props.onClose);
  close.current = props.onClose;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const here = box.current?.querySelector<HTMLElement>('[aria-current="page"]') ?? box.current?.querySelector<HTMLElement>(FOCUSABLE);
    here?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close.current();
        return;
      }
      if (e.key !== "Tab" || !box.current) return;
      const list = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
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
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  return (
    <div
      class="drawer-scrim"
      onClick={(e) => {
        if (e.target === e.currentTarget) props.onClose();
      }}
    >
      <div class="nav-sheet drawer" role="dialog" aria-modal="true" aria-label="Menu" ref={box}>
        <div class="drawer-head">
          <span class="brand" aria-hidden="true">
            <Logo />
            parlayhitters
          </span>
          <button type="button" class="icon-btn" aria-label="Close the menu" onClick={props.onClose}>
            <Icon name="close" />
          </button>
        </div>
        <nav class="drawer-nav" aria-label="All screens">
          {props.children}
        </nav>
        {props.note ? <div class="drawer-note">{props.note}</div> : null}
      </div>
    </div>
  );
}
