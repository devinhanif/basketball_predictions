// Part 1 of the live card: the page never sits empty behind a proxy that holds the event stream.
// (a) the first paint comes from /snapshot; (b) a silent or refused stream falls back to polling with a plain note,
// and the stream is retried with a growing wait; (c) a stream that speaks again ends the polling.
import { describe, expect, it } from "vitest";
import { ATTRIBUTION, type Snapshot } from "./live";
import { CLOSED, POLL_MS, SILENT_MS, STREAM_RETRY_MS, connectLive, type LiveConn, type SourceLike, type StreamEnv } from "./useLive";

class FakeSource implements SourceLike {
  readyState = 0;
  onerror: (() => void) | null = null;
  closed = false;
  private fns = new Map<string, (e: { data: string }) => void>();
  addEventListener(t: string, fn: (e: { data: string }) => void): void {
    this.fns.set(t, fn);
  }
  close(): void {
    this.closed = true;
    this.readyState = CLOSED;
  }
  send(t: string, data: string): void {
    this.readyState = 1;
    this.fns.get(t)?.({ data });
  }
}

const snap = (fetchedAt: string, away = 50): Snapshot => ({
  seq: 0,
  game_id: "401909952",
  date: "2026-11-09",
  state: "in",
  tip_utc: null,
  away: { abbr: "LAL", name: "Lakers", score: away },
  home: { abbr: "ATL", name: "Hawks", score: 48 },
  status: { period: 2, clock: "6:00", detail: "6:00 - 2nd", elapsed_min: 18 },
  box: { away: [], home: [] },
  plays: [{ n: 1, p: 1, c: "11:40", t: "Jump ball", h: 0, a: 0, tm: null, s: 0, k: "Jump Ball" }],
  n_plays: 1,
  first_n: 1,
  meta: { source: "capture", fetched_at: fetchedAt, last_play_at: null, play_lag_s: 30, server_now: "x", age_s: 5 },
  attribution: ATTRIBUTION,
  delay_note: "",
});

interface Pending {
  resolve: (s: Snapshot) => void;
  reject: (e: Error) => void;
}

function harness(opts: { withFetch?: boolean } = {}) {
  const sources: FakeSource[] = [];
  const timers = new Map<number, { fn: () => void; ms: number }>();
  const fetches: Pending[] = [];
  let next = 0;
  const env: StreamEnv = {
    open: () => {
      const s = new FakeSource();
      sources.push(s);
      return s;
    },
    hidden: () => false,
    onVisibility: () => () => undefined,
    setTimer: (fn, ms) => {
      next += 1;
      timers.set(next, { fn, ms });
      return next;
    },
    clearTimer: (id) => {
      timers.delete(id);
    },
    now: () => 0,
    random: () => 0,
    ...(opts.withFetch === false
      ? {}
      : {
          fetchSnapshot: () =>
            new Promise<Snapshot>((resolve, reject) => {
              fetches.push({ resolve, reject });
            }),
        }),
  };
  const seen: LiveConn[] = [];
  const handle = connectLive("401909952", env, (c) => seen.push(c));
  /** Fire the pending timer with this wait (exactly), once. */
  const fireMs = (ms: number): void => {
    const hit = [...timers.entries()].find(([, t]) => t.ms === ms);
    if (!hit) throw new Error(`no timer of ${ms.toString()} ms; have ${[...timers.values()].map((t) => t.ms.toString()).join(",")}`);
    timers.delete(hit[0]);
    hit[1].fn();
  };
  const has = (ms: number): boolean => [...timers.values()].some((t) => t.ms === ms);
  const flush = async (): Promise<void> => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  };
  return { sources, timers, fetches, seen, handle, fireMs, has, flush, last: () => seen[seen.length - 1] as LiveConn };
}

describe("the first paint comes from /snapshot (a)", () => {
  it("asks for the snapshot the moment it starts, before the stream has said anything", async () => {
    const h = harness();
    expect(h.fetches.length).toBe(1);
    expect(h.sources.length).toBe(1); // the stream is opened beside it, for updates
    expect(h.last().state).toBeNull();
    (h.fetches[0] as Pending).resolve(snap("2026-11-10T01:40:00+00:00"));
    await h.flush();
    // The stream opened and stayed silent, and no timer has fired: the page already has its plays.
    expect(h.last().state?.plays.length).toBe(1);
    expect(h.last().phase).toBe("live");
    expect(h.last().transport).toBe("stream");
    expect(h.sources[0]?.closed).toBe(false);
  });
  it("a snapshot that fails does not stop the stream from filling the page", async () => {
    const h = harness();
    (h.fetches[0] as Pending).reject(new Error("net"));
    await h.flush();
    expect(h.last().state).toBeNull();
    (h.sources[0] as FakeSource).send("snapshot", JSON.stringify(snap("2026-11-10T01:40:00+00:00", 52)));
    expect(h.last().state?.away.score).toBe(52);
  });
  it("an older copy never replaces a newer one on screen", async () => {
    const h = harness();
    (h.sources[0] as FakeSource).send("snapshot", JSON.stringify(snap("2026-11-10T01:41:00+00:00", 60)));
    (h.fetches[0] as Pending).resolve(snap("2026-11-10T01:40:00+00:00", 50));
    await h.flush();
    expect(h.last().state?.away.score).toBe(60);
  });
  it("a final game seen in the snapshot closes the stream and stops", async () => {
    const h = harness();
    const done = { ...snap("2026-11-10T03:00:00+00:00"), state: "post" as const };
    (h.fetches[0] as Pending).resolve(done);
    await h.flush();
    expect(h.last().phase).toBe("final");
    expect(h.sources[0]?.closed).toBe(true);
    expect(h.timers.size).toBe(0);
  });
});

describe("a stream that opens and stays silent (b)", () => {
  it(`falls back to polling after ${SILENT_MS.toString()} ms, says so as a note and not an error, and retries the stream later`, async () => {
    const h = harness();
    (h.fetches[0] as Pending).resolve(snap("2026-11-10T01:40:00+00:00"));
    await h.flush();
    h.fireMs(SILENT_MS);
    expect(h.last().transport).toBe("polling");
    expect(h.last().phase).toBe("live"); // not "error", not "reconnecting"
    expect(h.sources[0]?.closed).toBe(true);
    expect(h.fetches.length).toBe(2); // polling started at once
    (h.fetches[1] as Pending).resolve(snap("2026-11-10T01:41:00+00:00", 57));
    await h.flush();
    expect(h.last().state?.away.score).toBe(57);
    expect(h.last().phase).toBe("live");
    expect(h.has(POLL_MS)).toBe(true); // the next poll is queued
    expect(h.has(STREAM_RETRY_MS[0] as number)).toBe(true); // and the stream will be tried again
  });
  it("with nothing on screen the poll alone fills the page (the tunnel case: the stream never speaks)", async () => {
    const h = harness();
    (h.fetches[0] as Pending).reject(new Error("slow")); // even the first fetch can fail once
    await h.flush();
    h.fireMs(SILENT_MS);
    (h.fetches[1] as Pending).resolve(snap("2026-11-10T01:40:00+00:00"));
    await h.flush();
    expect(h.last().state?.plays.length).toBe(1);
    expect(h.last().transport).toBe("polling");
  });
  it("retries the stream with a growing wait, and a stream that speaks ends the polling", async () => {
    const h = harness();
    h.fireMs(SILENT_MS);
    (h.fetches[1] as Pending).resolve(snap("2026-11-10T01:40:00+00:00"));
    await h.flush();
    h.fireMs(STREAM_RETRY_MS[0] as number);
    expect(h.sources.length).toBe(2);
    h.fireMs(SILENT_MS); // silent again
    expect(h.has(STREAM_RETRY_MS[1] as number)).toBe(true); // the wait grew
    expect(h.sources.filter((s) => !s.closed).length).toBe(0);
    h.fireMs(STREAM_RETRY_MS[1] as number);
    expect(h.sources.length).toBe(3);
    (h.sources[2] as FakeSource).send("snapshot", JSON.stringify(snap("2026-11-10T01:42:00+00:00", 61)));
    expect(h.last().transport).toBe("stream");
    expect(h.last().state?.away.score).toBe(61);
    expect(h.has(POLL_MS)).toBe(false); // polling stopped
    expect(h.has(STREAM_RETRY_MS[2] as number)).toBe(false);
  });
  it("a stream that errors before it ever spoke also falls back to polling", () => {
    const h = harness();
    const s = h.sources[0] as FakeSource;
    s.onerror?.();
    expect(h.last().transport).toBe("polling");
    expect(h.fetches.length).toBe(2);
    expect(s.closed).toBe(true);
  });
  it("a stream that spoke keeps the browser's own reconnect on a first drop", () => {
    const h = harness();
    const s = h.sources[0] as FakeSource;
    s.send("snapshot", JSON.stringify(snap("2026-11-10T01:40:00+00:00")));
    s.readyState = 0;
    s.onerror?.();
    expect(h.last().phase).toBe("reconnecting");
    expect(h.last().transport).toBe("stream");
  });
  it("stop leaves no timer and no open stream behind", async () => {
    const h = harness();
    h.fireMs(SILENT_MS);
    (h.fetches[1] as Pending).resolve(snap("2026-11-10T01:40:00+00:00"));
    await h.flush();
    h.handle();
    expect(h.timers.size).toBe(0);
    expect(h.sources.filter((s) => !s.closed).length).toBe(0);
  });
});

describe("without fetchSnapshot the old stream-only path is unchanged", () => {
  it("no silent watch is set", () => {
    const h = harness({ withFetch: false });
    expect(h.timers.size).toBe(0);
    expect(h.fetches.length).toBe(0);
  });
});
