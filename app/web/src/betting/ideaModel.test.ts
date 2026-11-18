// The donor's ideas and outcomes cases (kit slip-ui "saving a thesis and tags creates an independent idea..." and
// "recorded paper outcomes persist with the saved-price net...") ported to logic level with their exact fixtures (the
// demo: DraftKings YES $40 recorded + Kalshi NO 100 contracts planned; $93 cost; $100 payout either way; +$7.00 net),
// plus: a snapshot survives a quote refresh, the net uses saved prices only, voids, the ideas file, and Compare's
// grouping (by availability, then name; never by price).
import { describe, expect, it } from "vitest";
import { buildCatalog, KNOWN_PROVIDERS, refreshQuotes } from "./catalog";
import { evaluateIdea, filterIdeas, ideaResult, ideasFile, legResult, mergeIdeasFile, paperTotal, statusCounts, VOID_TEXT } from "./ideaModel";
import { createSlipWorkspace, loadIdea, recordOutcomes, replacePlannedQuote, saveIdea, setLegAmount, setNotes, validateSlipWorkspace } from "./slipModel";
import { buildDemoDraft, sampleCatalog, sampleMarketId, SAMPLE_STAMP } from "./sample";
import { betActions, createBetStore } from "./store";
import { typedQuote, venueCards } from "./viewModel";
import type { StorageLike } from "./repository";
import type { Market, ProviderInfo, Quote, SavedIdea, SlipWorkspace } from "./types";
import { instantMs } from "../time";

const NOW = instantMs(SAMPLE_STAMP);
const COLLIN = sampleMarketId("collin");
const memory = (): StorageLike => {
  const m = new Map<string, string>();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) };
};
const withDemo = (): SlipWorkspace => ({ ...createSlipWorkspace(), draft: buildDemoDraft() });
const ok = <T extends { ok: boolean }>(r: T): Extract<T, { ok: true }> => {
  if (!r.ok) throw new Error(JSON.stringify(r));
  return r as Extract<T, { ok: true }>;
};
const idea0 = (ws: SlipWorkspace): SavedIdea => {
  const i = ws.ideas[0];
  if (!i) throw new Error("no idea");
  return i;
};

describe("ideas: a thesis and tags make an independent idea (donor case)", () => {
  it("survives edits to the slip and reloads, and loading gives a copy", () => {
    let ws = withDemo();
    ws = { ...ws, draft: setNotes(ws.draft, { title: "Collin coverage notebook", thesis: "Cover the existing YES position only if both platforms settle the same threshold.", tags: ["hedge", " cross-platform "] }) };
    ws = ok(saveIdea(ws, { now: SAMPLE_STAMP, status: "paper", id: "idea-1" })).workspace;
    const idea = structuredClone(idea0(ws));
    expect(idea.title).toBe("Collin coverage notebook");
    expect(idea.status).toBe("paper");
    expect(idea.tags).toEqual(["hedge", "cross-platform"]);
    // Edit the working slip: a new Kalshi price and a new amount. The idea does not move.
    const kalshiLeg = ws.draft.legs.find((l) => l.quote.provider === "kalshi");
    if (!kalshiLeg) throw new Error("no kalshi leg");
    const kalshi = KNOWN_PROVIDERS[0] as ProviderInfo;
    const q = ok(typedQuote(kalshi, kalshiLeg.market, "no", { price: "61", fee: "1" }, SAMPLE_STAMP, "typed-61"));
    ws = { ...ws, draft: ok(replacePlannedQuote(ws.draft, kalshiLeg.id, q.quote)).draft };
    ws = { ...ws, draft: ok(setLegAmount(ws.draft, kalshiLeg.id, 80)).draft };
    expect(idea0(ws)).toEqual(idea);
    // Through storage and back (a reload): still equal.
    const again = ok(validateSlipWorkspace(JSON.parse(JSON.stringify(ws))));
    expect(idea0(again.workspace)).toEqual(idea);
    // Load a copy: the slip has the saved 52c price again, and editing the copy leaves the idea alone.
    const loaded = ok(loadIdea(ws, "idea-1")).workspace;
    const lk = loaded.draft.legs.find((l) => l.quote.provider === "kalshi");
    expect(lk?.quote.price).toMatchObject({ kind: "binary", askCents: 52 });
    const edited = ok(setLegAmount(loaded.draft, lk?.id ?? "", 80)).draft;
    expect(edited.legs.find((l) => l.id === lk?.id)?.amount).toBe(80);
    expect(idea0(loaded)).toEqual(idea);
  });

  it("through the store: save, clear the slip, then loading the idea brings its legs back and keeps the idea", async () => {
    const store = createBetStore({ storage: memory(), scope: { kind: "device" }, nowIso: () => SAMPLE_STAMP });
    const a = betActions(store, () => SAMPLE_STAMP);
    await store.load();
    await a.loadDemo();
    const n = store.get().workspace?.draft.legs.length ?? 0;
    expect(n).toBeGreaterThan(0);
    expect((await a.saveIdea({ id: "idea-s", status: "watching", title: "Kept" })).ok).toBe(true);
    await a.clearDraft();
    expect(store.get().workspace?.draft.legs.length).toBe(0);
    expect((await a.loadIdea("idea-s")).ok).toBe(true);
    expect(store.get().workspace?.draft.legs.length).toBe(n);
    expect(store.get().workspace?.ideas[0]?.title).toBe("Kept");
  });
});

describe("ideas: recorded paper outcomes and the saved-price net (donor case)", () => {
  const saved = (): SlipWorkspace => {
    const ws = withDemo();
    const idea: SavedIdea = { ...buildDemoDraft(), id: "saved-demo", createdAt: "2026-11-08T12:00:00.000Z", status: "paper", outcomes: {} };
    return { ...ws, ideas: [idea] };
  };

  it("NO on Collin: $100.00 payout minus $93.00 saved cost is +$7.00, and it persists", () => {
    const ws = saved();
    expect(ideaResult(idea0(ws), evaluateIdea(idea0(ws), NOW))).toMatchObject({ kind: "open", recorded: 0, markets: 1 });
    const done = ok(recordOutcomes(ws, "saved-demo", { [COLLIN]: "no" }, "settled")).workspace;
    const idea = idea0(done);
    expect(idea.status).toBe("settled");
    expect(idea.outcomes).toEqual({ [COLLIN]: "no" });
    const r = ideaResult(idea, evaluateIdea(idea, NOW));
    expect(r.kind).toBe("net");
    if (r.kind === "net") {
      expect(r.row.payout).toBe(100);
      expect(r.totalCost).toBe(93);
      expect(r.row.net).toBe(7);
    }
    const back = ok(validateSlipWorkspace(JSON.parse(JSON.stringify(done)))).workspace;
    expect(idea0(back).outcomes).toEqual({ [COLLIN]: "no" });
    expect(paperTotal(back.ideas, NOW)).toEqual({ net: 7, n: 1, excluded: 0 });
    expect(statusCounts(back.ideas)).toEqual({ all: 1, watching: 0, paper: 0, settled: 1 });
  });

  it("each leg's result follows its own side: the DraftKings YES lost, the Kalshi NO won", () => {
    const done = ok(recordOutcomes(saved(), "saved-demo", { [COLLIN]: "no" }, "settled")).workspace;
    const idea = idea0(done);
    expect(idea.legs.map((l) => `${l.quote.provider}:${legResult(idea, l)}`)).toEqual(["draftkings:lost", "kalshi:won"]);
  });

  it("settling needs every active market; reopening to paper clears the outcomes", () => {
    const ws = saved();
    const r = recordOutcomes(ws, "saved-demo", {}, "settled");
    expect(r).toMatchObject({ ok: false, code: "outcomes_incomplete" });
    const done = ok(recordOutcomes(ws, "saved-demo", { [COLLIN]: "no" }, "settled")).workspace;
    const reopened = ok(recordOutcomes(done, "saved-demo", {}, "paper")).workspace;
    expect(idea0(reopened).status).toBe("paper");
    expect(idea0(reopened).outcomes).toEqual({});
  });

  it("a void market settles the idea but no net is computed (venue refund rules are not modelled)", () => {
    const done = ok(recordOutcomes(saved(), "saved-demo", {}, "settled", [COLLIN])).workspace;
    const idea = idea0(done);
    expect(idea.voids).toEqual([COLLIN]);
    expect(idea.legs.map((l) => legResult(idea, l))).toEqual(["void", "void"]);
    expect(ideaResult(idea, evaluateIdea(idea, NOW))).toEqual({ kind: "void", text: VOID_TEXT });
    expect(paperTotal(done.ideas, NOW)).toEqual({ net: 0, n: 0, excluded: 1 });
    // Round trip; and a document that marks one market both void and decided is refused.
    expect(ok(validateSlipWorkspace(JSON.parse(JSON.stringify(done)))).workspace.ideas[0]?.voids).toEqual([COLLIN]);
    const bad = JSON.parse(JSON.stringify(done)) as { ideas: { outcomes: Record<string, string> }[] };
    (bad.ideas[0] as { outcomes: Record<string, string> }).outcomes = { [COLLIN]: "no" };
    expect(validateSlipWorkspace(bad).ok).toBe(false);
  });
});

describe("ideas: immutable snapshots", () => {
  it("a quote refresh never changes a saved idea's prices", () => {
    let ws = withDemo();
    ws = ok(saveIdea(ws, { now: SAMPLE_STAMP, id: "idea-r" })).workspace;
    const before = structuredClone(idea0(ws));
    const cat = sampleCatalog();
    // A refreshed board: every Kalshi price moves to 70c.
    const moved = cat.quotes.map((q) => (q.price.kind === "binary" ? { ...q, id: `${q.id}-new`, price: { ...q.price, askCents: 70 } } : q));
    const fresh = refreshQuotes(cat, moved).catalog;
    expect(fresh.quotes.some((q) => q.price.kind === "binary" && q.price.askCents === 70)).toBe(true);
    const market = cat.markets.find((m) => m.id === COLLIN) as Market;
    venueCards({ providers: KNOWN_PROVIDERS, stored: fresh, workspace: ws, market, side: "no", now: NOW });
    expect(idea0(ws)).toEqual(before);
    expect(idea0(ws).legs.find((l) => l.quote.provider === "kalshi")?.quote.price).toMatchObject({ askCents: 52 });
    expect(Object.isFrozen(idea0(ws).legs[0]?.quote)).toBe(true);
  });

  it("the paper net uses the saved prices only, whatever the slip or typed prices say now", () => {
    let ws = withDemo();
    ws = ok(saveIdea(ws, { now: SAMPLE_STAMP, id: "idea-n" })).workspace;
    const kalshiLeg = ws.draft.legs.find((l) => l.quote.provider === "kalshi");
    const kalshi = KNOWN_PROVIDERS[0] as ProviderInfo;
    const q = ok(typedQuote(kalshi, kalshiLeg?.market as Market, "no", { price: "90", fee: "5" }, SAMPLE_STAMP, "typed-90"));
    ws = { ...ws, draft: ok(replacePlannedQuote(ws.draft, kalshiLeg?.id ?? "", q.quote)).draft, manualQuotes: [q.quote] };
    ws = ok(recordOutcomes(ws, "idea-n", { [COLLIN]: "no" }, "settled")).workspace;
    const r = ideaResult(idea0(ws), evaluateIdea(idea0(ws), NOW));
    expect(r).toMatchObject({ kind: "net", totalCost: 93 });
    if (r.kind === "net") expect(r.row.net).toBe(7);
  });
});

describe("ideas: list, search and file", () => {
  const three = (): SlipWorkspace => {
    let ws = withDemo();
    ws = { ...ws, draft: setNotes(ws.draft, { title: "Alpha", tags: ["rotation"] }) };
    ws = ok(saveIdea(ws, { now: "2026-11-01T10:00:00.000Z", id: "a", status: "watching" })).workspace;
    ws = { ...ws, draft: setNotes(ws.draft, { title: "Beta", tags: ["hedge"] }) };
    ws = ok(saveIdea(ws, { now: "2026-11-03T10:00:00.000Z", id: "b", status: "paper" })).workspace;
    ws = { ...ws, draft: setNotes(ws.draft, { title: "Gamma", tags: ["hedge"] }) };
    return ok(saveIdea(ws, { now: "2026-11-02T10:00:00.000Z", id: "c", status: "paper" })).workspace;
  };
  it("search matches title, tags, players and venues; status filters; newest saved first", () => {
    const ws = three();
    expect(filterIdeas(ws.ideas, "", "all").map((i) => i.id)).toEqual(["b", "c", "a"]);
    expect(filterIdeas(ws.ideas, "hedge", "all").map((i) => i.id)).toEqual(["b", "c"]);
    expect(filterIdeas(ws.ideas, "collin kalshi", "watching").map((i) => i.id)).toEqual(["a"]);
    expect(filterIdeas(ws.ideas, "nobody", "all")).toEqual([]);
  });
  it("export then import into a fresh workspace adds every idea; again skips them; an invalid file changes nothing", () => {
    const ws = three();
    const file = ideasFile(ws.ideas, "2026-11-17T08:00:00.000Z");
    const fresh = createSlipWorkspace();
    const m = mergeIdeasFile(fresh, file);
    if (!m.ok) throw new Error(m.text);
    expect(m.added).toBe(3);
    expect(m.workspace.ideas.map((i) => i.id).sort()).toEqual(["a", "b", "c"]);
    expect(m.workspace.ideas.find((i) => i.id === "b")).toEqual(ws.ideas.find((i) => i.id === "b"));
    const again = mergeIdeasFile(m.workspace, file);
    expect(again).toMatchObject({ ok: true, added: 0, skipped: 3 });
    for (const bad of ["not json", JSON.stringify({ format: "other", ideas: [] }), JSON.stringify({ format: "parlayhitters-saved-ideas", version: 1, ideas: [{ id: "x" }] })]) {
      const r = mergeIdeasFile(m.workspace, bad);
      expect(r.ok).toBe(false);
      if (!r.ok) expect(r.text).toMatch(/Nothing was changed/);
    }
  });
});

describe("compare: grouped by availability, then by name, never by price", () => {
  it("cheapest-first prices still list by name; unavailable venues are last and still listed", () => {
    const m: Market = { ...(sampleCatalog().markets[0] as Market), id: "m-g", origin: "manual" };
    const prov: ProviderInfo[] = [
      { id: "zeta", label: "Zeta", kind: "sportsbook" },
      { id: "alpha", label: "Alpha", kind: "sportsbook" },
      { id: "mid", label: "Mid", kind: "sportsbook" },
      { id: "beta", label: "Beta", kind: "sportsbook" },
    ];
    const quote = (p: string, odds: number): Quote => ({ id: `${p}-q`, marketId: "m-g", provider: p, side: "yes", provenance: "delayed", observedAt: SAMPLE_STAMP, expiresAt: null, price: { kind: "sportsbook", americanOdds: odds, fee: { status: "unknown" } } });
    const cat = buildCatalog({ providers: prov, markets: [m], quotes: [quote("zeta", 150), quote("alpha", -200), quote("mid", 110)] }).catalog;
    const cards = venueCards({ providers: prov, stored: cat, workspace: createSlipWorkspace(), market: m, side: "yes", now: NOW });
    expect(cards.map((c) => `${c.provider.label}:${c.active ? "priced" : "unavailable"}`)).toEqual(["Alpha:priced", "Mid:priced", "Zeta:priced", "Beta:unavailable"]);
  });
});
