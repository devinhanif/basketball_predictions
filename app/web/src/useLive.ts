// The live stream for one game: EventSource with a snapshot, then deltas. Closes when the tab is hidden, reopens
// (to a fresh snapshot) when it is shown again, backs off (with jitter, capped) and retries after a refusal, and stops at
// the final. After MAX_FAILS failures in a row with no message between them it says the feed stopped (phase "stopped")
// and keeps retrying at the cap; Retry reconnects at once. One stream at a time: every open closes the one before.
// The connection logic is a plain function over an injected environment so it can be tested without a browser.
//
// Two things keep the page from sitting empty behind a proxy that holds an event stream (a quick tunnel did: headers,
// then no bytes for 45 s). (1) The page asks /snapshot at once and renders from it; the stream is opened beside it, only
// for updates. (2) If no stream event of any kind arrives within SILENT_MS of opening, or the stream errors before
// it ever spoke, the page polls /snapshot every POLL_MS, says so plainly (transport "polling"), and tries the stream
// again now and then with a growing wait. Both need env.fetchSnapshot; an env without it keeps the stream-only behaviour.
import { useCallback, useEffect, useRef, useState } from "preact/hooks";
import { liveApi } from "./liveApi";
import { applyDelta, followsOn, fromSnapshot, type Delta, type LiveState, type Snapshot } from "./live";

export type Phase = "connecting" | "live" | "reconnecting" | "paused" | "final" | "error" | "stopped";

export interface LiveConn {
  phase: Phase;
  state: LiveState | null;
  /** performance.now() when the newest message arrived, so the age keeps counting between messages. */
  receivedMs: number;
  message: string | null;
  /** How updates arrive: the event stream, or snapshot polling because the stream is silent or refused. */
  transport: "stream" | "polling";
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
  /** GET /api/live/<id>/snapshot. Optional so a test or an environment without it keeps the stream-only path. */
  fetchSnapshot?: (eventId: string) => Promise<Snapshot>;
}

export const CLOSED = 2; // EventSource.CLOSED
export const BACKOFF_MS = [2000, 4000, 8000, 15000, 30000];
/** Failures in a row (refusals, drops, unreadable messages) before the screen says the feed stopped. */
export const MAX_FAILS = 6;
/** Gaps in a row repaired at once before the repairs wait like a refusal (a server sending bad deltas cannot spin us). */
export const MAX_QUICK_GAPS = 3;
/** No stream event of any kind this long after opening: the stream is being held (a proxy) and the page polls instead. */
export const SILENT_MS = 5000;
/** The polling interval while the stream is not delivering (the capture lands a new body about every 60 s). */
export const POLL_MS = 15000;
/** While polling, the wait before trying the stream again; each failed try moves one step, then stays at the last. */
export const STREAM_RETRY_MS = [30000, 60000, 120000, 300000];

/** The connection's stop function, which also carries Retry (reconnect now, forgetting the failures). */
export type LiveHandle = (() => void) & { retry: () => void };

/** The wait before retry number `tries`: the step's base plus up to a quarter more, so viewers do not retry in step. */
export function backoffMs(tries: number, random: number): number {
  const base = BACKOFF_MS[Math.min(tries, BACKOFF_MS.length - 1)] ?? 30000;
  return base + Math.floor(random * base * 0.25);
}

export function connectLive(eventId: string, env: StreamEnv, emit: (c: LiveConn) => void): LiveHandle {
  let conn: LiveConn = { phase: "connecting", state: null, receivedMs: env.now(), message: null, transport: "stream" };
  let src: SourceLike | null = null;
  let timer: number | null = null;
  let tries = 0;
  let fails = 0;
  let gaps = 0;
  let finished = false;
  let stopped = false;
  let polling = false;
  let heard = false; // the stream now open has sent an event
  let silentTimer: number | null = null;
  let pollTimer: number | null = null;
  let retryTimer: number | null = null;
  let streamTries = 0;

  const set = (patch: Partial<LiveConn>): void => {
    conn = { ...conn, ...patch };
    emit(conn);
  };
  const clear = (id: number | null): null => {
    if (id !== null) env.clearTimer(id);
    return null;
  };
  const close = (): void => {
    if (src) src.close();
    src = null;
    timer = clear(timer);
    silentTimer = clear(silentTimer);
  };
  const stopPolling = (): void => {
    polling = false;
    pollTimer = clear(pollTimer);
    retryTimer = clear(retryTimer);
  };
  /** Take a snapshot fetched over plain HTTP, unless what is on screen is newer. */
  const take = (snap: Snapshot): boolean => {
    let st: LiveState;
    try {
      st = fromSnapshot(snap);
    } catch {
      return false;
    }
    if (conn.state !== null && st.meta.fetched_at < conn.state.meta.fetched_at) return true; // older: keep ours
    if (st.state === "post") finished = true;
    set({ phase: st.state === "post" ? "final" : "live", state: st, receivedMs: env.now(), message: null });
    if (st.state === "post") {
      close();
      stopPolling();
    }
    return true;
  };
  const poll = (): void => {
    pollTimer = null;
    if (stopped || finished || !polling || env.fetchSnapshot === undefined) return;
    const next = (): void => {
      if (!stopped && !finished && polling && pollTimer === null) pollTimer = env.setTimer(poll, POLL_MS + Math.floor(env.random() * 2000));
    };
    if (env.hidden()) {
      next();
      return;
    }
    env.fetchSnapshot(eventId).then(
      (snap) => {
        if (stopped || !polling) return;
        if (take(snap)) {
          fails = 0;
          set({ transport: "polling" });
        } else failed("The live feed sent something unreadable. Retrying.");
        next();
      },
      () => {
        if (stopped || !polling) return;
        failed("Could not reach the live feed. Retrying.");
        next();
      },
    );
  };
  /** The stream is silent or refused: poll /snapshot, and try the stream again later with a growing wait. */
  const goPolling = (): void => {
    if (env.fetchSnapshot === undefined || stopped || finished) return;
    if (src) src.close();
    src = null;
    silentTimer = clear(silentTimer);
    set({ transport: "polling" });
    if (!polling) {
      polling = true;
      poll();
    }
    retryTimer = clear(retryTimer);
    const wait = STREAM_RETRY_MS[Math.min(streamTries, STREAM_RETRY_MS.length - 1)] ?? 300000;
    streamTries += 1;
    retryTimer = env.setTimer(() => {
      retryTimer = null;
      if (!stopped && !finished && polling) open();
    }, wait + Math.floor(env.random() * wait * 0.25));
  };
  /** Any stream event: the stream works, so polling stops. */
  const heardFrom = (): void => {
    heard = true;
    silentTimer = clear(silentTimer);
    if (polling) {
      stopPolling();
      streamTries = 0;
    }
    if (conn.transport !== "stream") set({ transport: "stream" });
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
    heard = false;
    if (env.fetchSnapshot !== undefined) {
      silentTimer = env.setTimer(() => {
        silentTimer = null;
        if (src === s && !heard) goPolling();
      }, SILENT_MS);
    }
    s.addEventListener("snapshot", (e) => {
      if (src !== s) return;
      heardFrom();
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
      heardFrom();
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
      stopPolling();
      close();
      set({ phase: "final", message: null });
    });
    s.onerror = () => {
      if (finished || stopped || src !== s) return;
      if (env.fetchSnapshot !== undefined && (!heard || s.readyState === CLOSED)) {
        // Refused, or never spoke: the snapshot route still works, so show and poll from it (not an error).
        goPolling();
      } else if (s.readyState === CLOSED) {
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
      stopPolling();
      set({ phase: "paused", message: "Paused while this tab is hidden." });
    } else {
      set({ phase: conn.state === null ? "connecting" : "reconnecting", message: "Catching up." });
      first();
      open();
    }
  });
  /** The page's first paint comes from /snapshot, not from the stream: ask now, and apply it if the stream has not spoken. */
  const first = (): void => {
    if (env.fetchSnapshot === undefined) return;
    env.fetchSnapshot(eventId).then(
      (snap) => {
        if (stopped || (finished && conn.state !== null)) return;
        if (take(snap)) {
          if (conn.state !== null && conn.phase !== "final") set({ phase: "live", message: null });
        }
      },
      () => undefined, // the stream (or the poll) reports a real failure
    );
  };
  if (env.hidden()) set({ phase: "paused", message: "Paused while this tab is hidden." });
  else {
    first();
    open();
  }
  emit(conn);
  const stop = (): void => {
    stopped = true;
    close();
    stopPolling();
    off();
  };
  const retry = (): void => {
    if (stopped || finished) return;
    tries = 0;
    fails = 0;
    gaps = 0;
    stopPolling();
    streamTries = 0;
    set({ phase: conn.state === null ? "connecting" : "reconnecting", message: "Reconnecting." });
    first();
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
  fetchSnapshot: (id) => liveApi.snapshot(id).then((r) => r.data),
};

/** The live connection for a game, and Retry. One stream per mounted game; it closes on unmount or a new game id. */
export function useLiveGame(eventId: string | null): LiveConn & { retry: () => void } {
  const [conn, setConn] = useState<LiveConn>({ phase: "connecting", state: null, receivedMs: 0, message: null, transport: "stream" });
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
