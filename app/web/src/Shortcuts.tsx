import { Dialog } from "./ui";

/** Is the key going into a text field (so a single-letter shortcut must not fire)? */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (typeof HTMLElement === "undefined" || !(t instanceof HTMLElement)) return false;
  return t.isContentEditable || t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT";
}

export const SHORTCUTS: readonly { keys: string; what: string }[] = [
  { keys: "Ctrl K or Command K", what: "Open the command palette: jump to any screen, tonight's games, players or a key term." },
  { keys: "?", what: "Show this sheet." },
  { keys: "Esc", what: "Close a dialog or the palette; focus returns where it was." },
  { keys: "Tab, Shift Tab", what: "Move between controls. Inside a dialog, focus stays in the dialog." },
  { keys: "Arrow keys", what: "Move between tabs in a tab list, and between results in the palette." },
  { keys: "Home, End", what: "First or last tab in a tab list." },
  { keys: "Enter", what: "Open the selected palette result." },
];

/** The `?` sheet: every keyboard shortcut in one place. */
export function ShortcutsDialog(props: { onClose: () => void }) {
  return (
    <Dialog title="Keyboard shortcuts" onClose={props.onClose} id="keys">
      <dl class="keys-list">
        {SHORTCUTS.map((s) => (
          <div key={s.keys} class="keys-row">
            <dt>
              <kbd class="kbd">{s.keys}</kbd>
            </dt>
            <dd>{s.what}</dd>
          </div>
        ))}
      </dl>
      <p class="small muted">A skip link ("Skip to content") is the first stop for Tab on every page.</p>
    </Dialog>
  );
}
