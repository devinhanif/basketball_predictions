// The live stream for one game: EventSource with a snapshot, then deltas. Closes when the tab is hidden, reopens
// (to a fresh snapshot) when it is shown again, backs off (with jitter, capped) and retries after a refusal, and stops at
// the final. After MAX_FAILS failures in a row with no message between them it says the feed stopped (phase "stopped")
// and keeps retrying at the cap; Retry reconnects at once. One stream at a time: every open closes the one before.
// The connection logic is a plain function over an injected environment so it can be tested without a browser.
import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { applyDelta, followsOn, fromSnapshot, type Delta, type LiveState, type Snapshot } from "./live";

export type Phase = "connecting" | "live" | "reconnecting" | "paused" | "final" | "error" | "stopped";

export interface LiveConn {
  phase: Phase;
  state: LiveState | null;
  /** performance.now() when the newest message arrived, so the age keeps counting between messages. */
  receivedMs: number;
  message: string | null;
}

/** The slice of EventSource the stream uses. */
export interface SourceLike {
  readyState: number;
  onerror: (() => void) | null;
  addEventListener: (type: string, fn: (e: { data: string }) => void) => void;
  close: () => void;
}
export interface StreamEnv {
  open: (url: string) => SourceLike;
  hidden: () => boolean;
  onVisibility: (fn: () => void) => () => void;
  setTimer: (fn: () => void, ms: number) => number;
  clearTimer: (id: number) => void;
  now: () => number;
  random: () => number;
}

export const CLOSED = 2; // EventSource.CLOSED
export const BACKOFF_MS = [2000, 4000, 8000, 15000, 30000];
/** Failures in a row (refusals, drops, unreadable messages) before the screen says the feed stopped. */
export const MAX_FAILS = 6;
/** Gaps in a row repaired at once before the repairs wait like a refusal (a server sending bad deltas cannot spin us). */
export const MAX_QUICK_GAPS = 3;

/** The connection's stop function, which also carries Retry (reconnect now, forgetting the failures). */
export type LiveHandle = (() => void) & { retry: () => void };

/** The wait before retry number `tries`: the step's base plus up to a quarter more, so viewers do not retry in step. */
export function backoffMs(tries: number, random: number): number {
  const base = BACKOFF_MS[Math.min(tries, BACKOFF_MS.length - 1)] ?? 30000;
  return base + Math.floor(random * base * 0.25);
}

export function connectLive(eventId: string, env: StreamEnv, emit: (c: LiveConn) => void): LiveHandle {
  let conn: LiveConn = { phase: "connecting", state: null, receivedMs: env.now(), message: null };
  let src: SourceLike | null = null;
  let timer: number | null = null;
  let tries = 0;
  let fails = 0;
  let gaps = 0;
  let finished = false;
  let stopped = false;

  const set = (patch: Partial<LiveConn>): void => {
    conn = { ...conn, ...patch };
    emit(conn);
  };
  const close = (): void => {
    if (src) src.close();
    src = null;
    if (timer !== null) env.clearTimer(timer);
    timer = null;
  };
  /** Count a failure; past MAX_FAILS the screen says the feed stopped (and the retries go on at the cap). */
  const failed = (message: string): void => {
    fails += 1;
    if (fails >= MAX_FAILS) set({ phase: "stopped", message: "The live feed stopped. Retrying now and then." });
    else set({ phase: conn.state === null ? "error" : "reconnecting", message });
  };
  const later = (): void => {
    close();
    timer = env.setTimer(open, backoffMs(tries, env.random()));
    tries += 1;
  };
  const parsed = (data: string): unknown => {
    try {
      return JSON.parse(data) as unknown;
    } catch {
      return null;
    }
  };
  const open = (): void => {
    close();
    if (stopped || finished) return;
    const s = env.open(`/api/live/${encodeURIComponent(eventId)}/stream`);
    src = s;
    s.addEventListener("snapshot", (e) => {
      if (src !== s) return;
      const snap = parsed(e.data) as Snapshot | null;
      if (snap === null || typeof snap !== "object") {
        failed("The live feed sent something unreadable. Retrying.");
        later();
        return;
      }
      let st: LiveState;
      try {
        st = fromSnapshot(snap);
      } catch {
        failed("The live feed sent something unreadable. Retrying.");
        later();
        return;
      }
      tries = 0;
      fails = 0;
      set({ phase: st.state === "post" ? "final" : "live", state: st, receivedMs: env.now(), message: null });
    });
    s.addEventListener("delta", (e) => {
      if (src !== s) return;
      const d = parsed(e.data) as Delta | null;
      if (d === null || typeof d !== "object" || conn.state === null || !followsOn(conn.state, d)) {
        // A gap: a fresh snapshot is the only safe repair. Repeated gaps wait like a refusal.
        gaps += 1;
        if (gaps > MAX_QUICK_GAPS) {
          failed("The live feed is out of step. Retrying.");
          later();
        } else {
          open();
        }
        return;
      }
      let next: LiveState;
      try {
        next = applyDelta(conn.state, d);
      } catch {
        open(); // an unreadable delta is repaired like a gap
        return;
      }
      gaps = 0;
      fails = 0;
      set({ phase: "live", state: next, receivedMs: env.now(), message: null });
    });
    s.addEventListener("final", () => {
      if (src !== s) return;
      finished = true;
      close();
      set({ phase: "final", message: null });
    });
    s.onerror = () => {
      if (finished || stopped || src !== s) return;
      if (s.readyState === CLOSED) {
        // The server refused (unknown game, too many viewers): the browser will not retry by itself.
        failed("Could not reach the live feed. Retrying.");
        later();
      } else if (fails + 1 >= MAX_FAILS) {
        // The browser keeps retrying a dropped connection on its own; after enough drops in a row, take over at the cap.
        failed("Connection dropped. Reconnecting.");
        later();
      } else {
        fails += 1;
        set({ phase: "reconnecting", message: "Connection dropped. Reconnecting." });
      }
    };
  };
  const off = env.onVisibility(() => {
    if (stopped || finished) return;
    if (env.hidden()) {
      close();
      set({ phase: "paused", message: "Paused while this tab is hidden." });
    } else {
      set({ phase: "reconnecting", message: "Catching up." });
      open();
    }
  });
  if (env.hidden()) set({ phase: "paused", message: "Paused while this tab is hidden." });
  else open();
  emit(conn);
  const stop = (): void => {
    stopped = true;
    close();
    off();
  };
  const retry = (): void => {
    if (stopped || finished) return;
    tries = 0;
    fails = 0;
    gaps = 0;
    set({ phase: conn.state === null ? "connecting" : "reconnecting", message: "Reconnecting." });
    open();
  };
  return Object.assign(stop, { retry });
}

const browserEnv: StreamEnv = {
  open: (url) => new EventSource(url) as unknown as SourceLike,
  hidden: () => document.hidden,
  onVisibility: (fn) => {
    document.addEventListener("visibilitychange", fn);
    return () => {
      document.removeEventListener("visibilitychange", fn);
    };
  },
  setTimer: (fn, ms) => window.setTimeout(fn, ms),
  clearTimer: (id) => {
    window.clearTimeout(id);
  },
  now: () => performance.now(),
  random: () => Math.random(),
};

/** The live connection for a game, and Retry. One stream per mounted game; it closes on unmount or a new game id. */
export function useLiveGame(eventId: string | null): LiveConn & { retry: () => void } {
  const [conn, setConn] = useState<LiveConn>({ phase: "connecting", state: null, receivedMs: 0, message: null });
  const handle = useRef<LiveHandle | null>(null);
  useEffect(() => {
    if (eventId === null || typeof EventSource === "undefined") return;
    const h = connectLive(eventId, browserEnv, setConn);
    handle.current = h;
    return () => {
      h();
      if (handle.current === h) handle.current = null;
    };
  }, [eventId]);
  const retry = useCallback(() => {
    handle.current?.retry();
  }, []);
  return { ...conn, retry };
}
