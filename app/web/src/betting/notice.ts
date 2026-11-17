// The betting screens' toasts, coalesced. One action says one thing; a newer notice of the same kind replaces the older
// one; at most `max` are visible (one on a phone); each dismisses itself; and each is placed away from the control that was
// just used (top or bottom of the screen). Pure state with an injected timer, so it is tested without a browser. The app's
// own toast list (../toast) is untouched: its callers keep their behaviour.

export type NoticeTone = "done" | "failed";
export type NoticePlace = "top" | "bottom";

export interface Notice {
  readonly id: number;
  /** Notices of one kind replace each other ("slip", "idea", "import", ...). */
  readonly kind: string;
  readonly title: string;
  readonly text: string;
  readonly tone: NoticeTone;
  readonly place: NoticePlace;
}

export interface NoticeInput {
  kind: string;
  title: string;
  text: string;
  tone?: NoticeTone;
  /** How long it stays, in ms (default 5 s, or 8 s for a failure). */
  ms?: number;
  place?: NoticePlace;
}

export interface NoticeTimer {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
}

export interface NoticeStore {
  push(n: NoticeInput, max: number): number;
  dismiss(id: number): void;
  get(): readonly Notice[];
  subscribe(fn: () => void): () => void;
}

export function createNoticeStore(timer: NoticeTimer): NoticeStore {
  let list: readonly Notice[] = [];
  let next = 1;
  const timers = new Map<number, unknown>();
  const listeners = new Set<() => void>();
  const emit = (): void => {
    for (const l of listeners) l();
  };
  const drop = (ids: readonly number[]): void => {
    for (const id of ids) {
      const h = timers.get(id);
      if (h !== undefined) timer.clear(h);
      timers.delete(id);
    }
  };
  const dismiss = (id: number): void => {
    if (!list.some((n) => n.id === id)) return;
    drop([id]);
    list = list.filter((n) => n.id !== id);
    emit();
  };
  return {
    push(input, max) {
      const id = next++;
      const tone = input.tone ?? "done";
      const notice: Notice = { id, kind: input.kind, title: input.title, text: input.text, tone, place: input.place ?? "bottom" };
      const kept = list.filter((n) => n.kind !== input.kind);
      const all = [...kept, notice];
      const limit = Math.max(1, Math.floor(max));
      const shown = all.slice(-limit);
      drop(list.filter((n) => !shown.includes(n)).map((n) => n.id));
      list = shown;
      timers.set(
        id,
        timer.set(() => {
          dismiss(id);
        }, input.ms ?? (tone === "failed" ? 8000 : 5000)),
      );
      emit();
      return id;
    },
    dismiss,
    get: () => list,
    subscribe(fn) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
  };
}

/** A box on screen (a DOMRect's fields). */
export interface Box {
  top: number;
  bottom: number;
}

/**
 * Where a notice goes so it does not cover the control just used: the bottom band by default, the top band when the
 * control sits in the bottom band (and back to the bottom if it sits in the top band). `band` is the notice's height
 * plus its margin from the screen edge.
 */
export function placeAwayFrom(control: Box | null, viewportHeight: number, band: { top: number; bottom: number }): NoticePlace {
  if (control === null) return "bottom";
  const inBottom = control.bottom > viewportHeight - band.bottom;
  const inTop = control.top < band.top;
  if (inBottom && !inTop) return "top";
  return "bottom";
}
