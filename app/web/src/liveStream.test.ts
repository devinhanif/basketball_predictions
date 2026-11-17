// The live stream's lifecycle beyond the basics in live.test.ts: backoff with jitter and a cap, "the feed stopped" with
// Retry, unreadable messages, gap storms, one stream at a time, and nothing left running after stop.
import { describe, expect, it } from "vitest";
import { ATTRIBUTION, type Snapshot } from "./live";
import { BACKOFF_MS, CLOSED, MAX_FAILS, MAX_QUICK_GAPS, backoffMs, connectLive, type LiveConn, type SourceLike, type StreamEnv } from "./useLive";

class FakeSource implements SourceLike {
  readyState = 1;
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
    this.fns.get(t)?.({ data });
  }
}

const SNAP: Snapshot = {
  seq: 3,
  game_id: "401909952",
  date: "2026-11-09",
  state: "in",
  tip_utc: null,
  away: { abbr: "LAL", name: "Lakers", score: 50 },
  home: { abbr: "ATL", name: "Hawks", score: 48 },
  status: { period: 2, clock: "6:00", detail: "6:00 - 2nd", elapsed_min: 18 },
  box: { away: [], home: [] },
  plays: [],
  n_plays: 40,
  first_n: 1,
  meta: { source: "capture", fetched_at: "2026-11-10T01:40:00+00:00", last_play_at: null, play_lag_s: 30, server_now: "x", age_s: 5 },
  attribution: ATTRIBUTION,
  delay_note: "",
};

function harness(random = 0) {
  const sources: FakeSource[] = [];
  const timers = new Map<number, { fn: () => void; ms: number }>();
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
    random: () => random,
  };
  const seen: LiveConn[] = [];
  const handle = connectLive("401909952", env, (c) => seen.push(c));
  const fire = (): number => {
    const [id, t] = [...timers.entries()][0] ?? [];
    if (id === undefined || t === undefined) return -1;
    timers.delete(id);
    t.fn();
    return t.ms;
  };
  const refuse = (): void => {
    const s = sources[sources.length - 1] as FakeSource;
    s.readyState = CLOSED;
    s.onerror?.();
  };
  const open = (): FakeSource[] => sources.filter((s) => !s.closed);
  return { sources, timers, seen, handle, fire, refuse, open, last: () => seen[seen.length - 1] as LiveConn };
}

describe("backoff", () => {
  it("grows step by step to a cap, with at most a quarter more as jitter", () => {
    expect(backoffMs(0, 0)).toBe(BACKOFF_MS[0]);
    expect(backoffMs(99, 0)).toBe(30000);
    expect(backoffMs(99, 0.999)).toBeLessThanOrEqual(37500);
    expect(backoffMs(1, 0.5)).toBe(4000 + 500);
  });
  it("refusals wait longer each time and never more than the capped step", () => {
    const h = harness(0.999);
    const waits: number[] = [];
    for (let i = 0; i < 9; i++) {
      h.refuse();
      waits.push(h.fire());
    }
    expect(waits.slice(0, 5)).toEqual(BACKOFF_MS.map((b) => b + Math.floor(0.999 * b * 0.25)));
    expect(Math.max(...waits)).toBeLessThanOrEqual(37500);
  });
});

describe("the feed stopped", () => {
  it(`after ${MAX_FAILS.toString()} failures in a row says so, keeps retrying at the cap, and Retry reconnects at once`, () => {
    const h = harness();
    for (let i = 0; i < MAX_FAILS; i++) {
      h.refuse();
      if (i < MAX_FAILS - 1) h.fire();
    }
    expect(h.last().phase).toBe("stopped");
    expect(h.timers.size).toBe(1); // still retrying by itself
    const before = h.sources.length;
    h.handle.retry();
    expect(h.sources.length).toBe(before + 1);
    expect(h.timers.size).toBe(0); // the pending retry was replaced, not doubled
    (h.sources[h.sources.length - 1] as FakeSource).send("snapshot", JSON.stringify(SNAP));
    expect(h.last().phase).toBe("live");
  });
  it("a connection the browser keeps dropping is taken over after enough drops in a row", () => {
    const h = harness();
    const s = h.sources[0] as FakeSource;
    s.readyState = 0;
    for (let i = 0; i < MAX_FAILS; i++) s.onerror?.();
    expect(h.last().phase).toBe("stopped");
    expect(s.closed).toBe(true);
    expect(h.timers.size).toBe(1);
  });
  it("a message resets the count", () => {
    const h = harness();
    for (let i = 0; i < MAX_FAILS - 1; i++) {
      h.refuse();
      h.fire();
    }
    (h.sources[h.sources.length - 1] as FakeSource).send("snapshot", JSON.stringify(SNAP));
    h.refuse();
    expect(h.last().phase).toBe("reconnecting");
  });
});

describe("hostile messages", () => {
  it("an unreadable snapshot or delta never throws; it counts as a failure or a gap", () => {
    const h = harness();
    const s = h.sources[0] as FakeSource;
    expect(() => {
      s.send("snapshot", "{not json");
    }).not.toThrow();
    expect(() => {
      s.send("snapshot", "{}");
    }).not.toThrow();
    expect(h.last().phase).toBe("error");
    h.fire();
    const s2 = h.sources[h.sources.length - 1] as FakeSource;
    s2.send("snapshot", JSON.stringify(SNAP));
    expect(() => {
      s2.send("delta", "nope");
    }).not.toThrow();
    expect(s2.closed).toBe(true); // repaired with a fresh snapshot
  });
  it(`gaps are repaired at once ${MAX_QUICK_GAPS.toString()} times, then wait like a refusal (no reconnect storm)`, () => {
    const h = harness();
    for (let i = 0; i < 10; i++) {
      const s = h.sources[h.sources.length - 1] as FakeSource;
      s.send("snapshot", JSON.stringify(SNAP));
      s.send("delta", JSON.stringify({ seq: 99, game_id: "401909952", state: "in", meta: SNAP.meta, changed: true }));
    }
    // Each snapshot opens the way for one quick repair; a server that only ever sends gaps still cannot make us spin:
    // at most one stream is open and every repair past the quick ones goes through the timer.
    expect(h.open().length).toBeLessThanOrEqual(1);
  });
  it("a gap storm without snapshots goes to the timer", () => {
    const h = harness();
    (h.sources[0] as FakeSource).send("snapshot", JSON.stringify(SNAP));
    for (let i = 0; i <= MAX_QUICK_GAPS; i++) {
      const s = h.sources[h.sources.length - 1] as FakeSource;
      s.send("delta", "garbage");
    }
    expect(h.sources.length).toBe(1 + MAX_QUICK_GAPS);
    expect(h.timers.size).toBe(1);
  });
});

describe("one stream, cleaned up", () => {
  it("never has two streams open, and stop leaves nothing running", () => {
    const h = harness();
    for (let i = 0; i < 20; i++) {
      if (i % 3 === 0) h.refuse();
      if (i % 3 === 1) h.fire();
      if (i % 3 === 2) h.handle.retry();
      expect(h.open().length).toBeLessThanOrEqual(1);
    }
    h.handle();
    expect(h.open().length).toBe(0);
    const n = h.sources.length;
    h.fire(); // a timer that was pending when we stopped
    h.handle.retry();
    expect(h.sources.length).toBe(n);
  });
  it("messages from a stream that was replaced are ignored", () => {
    const h = harness();
    const old = h.sources[0] as FakeSource;
    h.handle.retry();
    old.send("snapshot", JSON.stringify(SNAP));
    expect(h.last().state).toBeNull();
  });
});
