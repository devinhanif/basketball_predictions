// The side panel's one slot: a module puts something in it, the shell's host shows it. Pure state (no DOM), so it can be
// tested directly. Only one thing at a time: a newer owner replaces an older one; an owner can only clear its own.
import type { ComponentChildren } from "preact";

export interface SidePanelDef {
  /** Who filled the slot (a module name); `clearSidePanel` only clears its own. */
  owner: string;
  /** The panel's heading and its accessible name. */
  title: string;
  /** What the closed sheet's button says on a phone, e.g. "Your slip · 2". Defaults to the title. */
  openLabel?: string;
  content: () => ComponentChildren;
}

let slot: SidePanelDef | null = null;
const listeners = new Set<() => void>();

const emit = (): void => {
  for (const l of listeners) l();
};

export function setSidePanel(def: SidePanelDef): void {
  slot = def;
  emit();
}

export function clearSidePanel(owner: string): void {
  if (slot?.owner !== owner) return;
  slot = null;
  emit();
}

export function getSidePanel(): SidePanelDef | null {
  return slot;
}

export function subscribeSidePanel(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
