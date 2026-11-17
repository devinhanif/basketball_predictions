import type { ComponentChildren } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { Icon } from "../icons";
import { trapIndex } from "./logic";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A modal dialog: `aria-modal`, labelled by its title, focus moves in on open, Tab and Shift-Tab stay inside, Escape and
 * the backdrop close it, and focus returns to whatever opened it.
 */
export function Dialog(props: { title: string; onClose: () => void; children: ComponentChildren; wide?: boolean; id?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const close = useRef(props.onClose);
  close.current = props.onClose;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = box.current?.querySelector<HTMLElement>("input, select, textarea") ?? box.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close.current();
        return;
      }
      if (e.key !== "Tab" || !box.current) return;
      const list = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
      const at = list.indexOf(document.activeElement as HTMLElement);
      const next = list[trapIndex(list.length, at, e.shiftKey)];
      if (next) {
        e.preventDefault();
        next.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      if (opener?.isConnected) opener.focus();
    };
  }, []);
  const titleId = `${props.id ?? "dlg"}-title`;
  return (
    <div
      class="modal-back"
      onClick={(e) => {
        if (e.target === e.currentTarget) props.onClose();
      }}
    >
      <div class={`modal${props.wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={box} id={props.id}>
        <div class="modal-head">
          <h2 id={titleId}>{props.title}</h2>
          <button type="button" class="icon-btn" aria-label="Close" onClick={props.onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div class="modal-body">{props.children}</div>
      </div>
    </div>
  );
}
