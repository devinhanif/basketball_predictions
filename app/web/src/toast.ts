// A tiny toast store. "Test an alert" is a local toast and nothing else: no push, no network.
import { useEffect, useState } from "preact/hooks";

export interface Toast {
  id: number;
  title: string;
  text: string;
}
let list: Toast[] = [];
let next = 1;
const listeners = new Set<() => void>();

export function pushToast(title: string, text: string, ms = 5000): number {
  const id = next++;
  list = [...list, { id, title, text }].slice(-3);
  for (const l of listeners) l();
  window.setTimeout(() => {
    dismissToast(id);
  }, ms);
  return id;
}
export function dismissToast(id: number): void {
  list = list.filter((t) => t.id !== id);
  for (const l of listeners) l();
}
export function useToasts(): Toast[] {
  const [t, setT] = useState(list);
  useEffect(() => {
    const on = (): void => {
      setT(list);
    };
    listeners.add(on);
    return () => {
      listeners.delete(on);
    };
  }, []);
  return t;
}
