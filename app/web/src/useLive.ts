// The live stream for one game: EventSource with a snapshot, then deltas. Closes when the tab is hidden, reopens
// (to a fresh snapshot) when it is shown again, backs off and retries after a refusal, and stops at the final.
// The connection logic is a plain function over an injected environment so it can be tested without a browser.
import { useEffect, useState } from "preact/hooks";
import { applyDelta, followsOn, fromSnapshot, type Delta, type LiveState, type Snapshot } from "./live";

export type Phase = "connecting" | "live" | "reconnecting" | "paused" | "final" | "error";

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

export function connectLive(eventId: string, env: StreamEnv, emit: (c: LiveConn) => void): () => void {
  let conn: LiveConn = { phase: "connecting", state: null, receivedMs: env.now(), message: null };
  let src: SourceLike | null = null;
  let timer: number | null = null;
  let tries = 0;
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
  const open = (): void => {
    close();
    if (stopped || finished) return;
    const s = env.open(`/api/live/${encodeURIComponent(eventId)}/stream`);
    src = s;
    s.addEventListener("snapshot", (e) => {
      tries = 0;
      const st = fromSnapshot(JSON.parse(e.data) as Snapshot);
      set({ phase: st.state === "post" ? "final" : "live", state: st, receivedMs: env.now(), message: null });
    });
    s.addEventListener("delta", (e) => {
      const d = JSON.parse(e.data) as Delta;
      if (conn.state === null || !followsOn(conn.state, d)) {
        open(); // a gap: a fresh snapshot is the only safe repair
        return;
      }
      set({ phase: "live", state: applyDelta(conn.state, d), receivedMs: env.now(), message: null });
    });
    s.addEventListener("final", () => {
      finished = true;
      close();
      set({ phase: "final", message: null });
    });
    s.onerror = () => {
      if (finished || stopped || src !== s) return;
      if (s.readyState === CLOSED) {
        // The server refused (unknown game, too many viewers): the browser will not retry by itself.
        close();
        const wait = BACKOFF_MS[Math.min(tries, BACKOFF_MS.length - 1)] ?? 30000;
        tries += 1;
        set({ phase: conn.state === null ? "error" : "reconnecting", message: "Could not reach the live feed. Retrying." });
        timer = env.setTimer(open, wait + env.random() * 500);
      } else {
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
  return () => {
    stopped = true;
    close();
    off();
  };
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

export function useLiveGame(eventId: string | null): LiveConn {
  const [conn, setConn] = useState<LiveConn>({ phase: "connecting", state: null, receivedMs: 0, message: null });
  useEffect(() => {
    if (eventId === null || typeof EventSource === "undefined") return;
    return connectLive(eventId, browserEnv, setConn);
  }, [eventId]);
  return conn;
}
