// The rolling slip: picks waiting for a price, recorded positions (locked into exposure), planned legs (switch on/off,
// amount, price), the cost and outcome table and the coverage block from evaluateDraft, the note fields, "Mark slip as
// placed" (a local record, confirmed in a dialog), the way into the Hedge Finder, export/import and the labelled demo.
// Shown in two places: the persistent panel (compact) and the page #/bet/slip (full).
import { useEffect, useRef, useState } from "preact/hooks";
import { Button, Dialog, PageHead, Skeleton, clearSidePanel, setSidePanel } from "../ui";
import { Icon } from "../icons";
import { count } from "../format";
import { saveTextFile } from "../saveFile";
import { useLens } from "../prefs";
import { DEMO_LABEL, DEMO_NOTE, PLACED_CONFIRM, TITLE_SLIP } from "./copy";
import { activeLegs, evaluateDraft, type SlipEvaluation } from "./slipModel";
import { scopeLabel } from "./store";
import type { SlipPick, SlipWorkspace } from "./types";
import { compareHashFor } from "./viewModel";
import { localId } from "./ids";
import { ideaHref } from "./Ideas";
import { CoverageBlock, LegCard, MarketGate, MoneyNote, OutcomeTable, SaveState, Warnings, marketWords, money, providerLabel } from "./parts";
import { isSlipPage, slipPanelPlan } from "./panel";
import { RAIL_MIN, dismissNotice, ensureBetStyles, say, setPanelOpen, useBet, useHash, useNotices, useNow, usePanelOpen, useWide, type BetHook } from "./useBet";

type Ready = Extract<BetHook, { ready: true }>;

/** How many things the slip holds that count or wait: included planned legs, recorded positions, unpriced picks. */
export function slipCount(ws: SlipWorkspace): number {
  return activeLegs(ws.draft).length + ws.picks.length;
}

function PickRow(props: { pick: SlipPick; bet: Ready }) {
  const { pick } = props;
  return (
    <li class="ph-bet-pick">
      <div>
        <strong>{marketWords(pick.market, pick.side)}</strong>
        <span class="small muted"> · {pick.market.eventLabel || pick.market.eventId}</span>
        {pick.market.origin === "sample" ? <span class="small muted"> · sample</span> : null}
      </div>
      <div class="row wrap">
        <a class="btn small" href={compareHashFor(pick.market, pick.side)}>
          Compare prices
        </a>
        <button
          type="button"
          class="icon-btn ph-bet-x"
          aria-label={`Remove ${marketWords(pick.market, pick.side)} from the slip`}
          onClick={() => {
            void props.bet.actions.removePick(pick.id).then((d) => say(d));
          }}
        >
          ×
        </button>
      </div>
    </li>
  );
}

function Totals(props: { ev: SlipEvaluation }) {
  const { ev } = props;
  const providers = Object.keys(ev.providerTotals);
  if (!ev.groups.length) return null;
  return (
    <div class="ph-bet-totals">
      <dl>
        {providers.map((p) => (
          <div key={p}>
            <dt>{providerLabel(p)}</dt>
            <dd class="num">{money(ev.providerTotals[p])}</dd>
          </div>
        ))}
        <div class="total">
          <dt>Total cost</dt>
          <dd class="num">{ev.totalCost === null ? "not complete" : money(ev.totalCost)}</dd>
        </div>
      </dl>
    </div>
  );
}

function EntryTerms(props: { bet: Ready; ws: SlipWorkspace }) {
  const { entry } = props.ws.draft;
  const picks = props.ws.draft.legs.filter((l) => l.quote.price.kind === "entry-pick");
  if (!picks.length) return null;
  const set = (stake: number, multiplier: number): void => {
    void props.bet.actions.setEntry({ stake, multiplier }).then((d) => say(d));
  };
  return (
    <fieldset class="ph-bet-entry">
      <legend>{providerLabel(picks[0]?.quote.provider ?? "prizepicks")} entry: one stake, one all-correct multiplier</legend>
      <p class="small muted">The picks above form one entry. It pays stake × multiplier only if every pick is right; a pick has no price of its own.</p>
      <div class="ph-bet-fields">
        <label>
          <span>Entry stake ($)</span>
          <input key={`s${String(entry.stake)}`} type="number" min={0.01} step={0.01} defaultValue={String(entry.stake)} onChange={(e) => { set(Number(e.currentTarget.value), entry.multiplier); }} />
        </label>
        <label>
          <span>All-correct multiplier (stake included)</span>
          <input key={`m${String(entry.multiplier)}`} type="number" min={1} step={0.1} defaultValue={String(entry.multiplier)} onChange={(e) => { set(entry.stake, Number(e.currentTarget.value)); }} />
        </label>
      </div>
    </fieldset>
  );
}

function Notes(props: { bet: Ready; ws: SlipWorkspace }) {
  const d = props.ws.draft;
  const set = (n: { title?: string; thesis?: string; tags?: string[] }): void => {
    void props.bet.actions.setNotes(n).then((r) => say(r));
  };
  return (
    <details class="ph-bet-notes" open={d.title !== "" || d.thesis !== "" ? true : undefined}>
      <summary>The thinking behind this slip</summary>
      <p class="small muted">Kept with the slip in this browser, and saved with it when you save the slip as an idea.</p>
      <label>
        <span>Title</span>
        <input key={`t${d.title}`} maxLength={200} defaultValue={d.title} placeholder="e.g. Cover my rebound over" onChange={(e) => { set({ title: e.currentTarget.value }); }} />
      </label>
      <label>
        <span>Thesis</span>
        <textarea key={`h${d.thesis}`} rows={3} maxLength={5000} defaultValue={d.thesis} placeholder="What would make this wrong?" onChange={(e) => { set({ thesis: e.currentTarget.value }); }} />
      </label>
      <label>
        <span>Tags (comma separated)</span>
        <input key={`g${d.tags.join(",")}`} defaultValue={d.tags.join(", ")} placeholder="hedge, rotation" onChange={(e) => { set({ tags: e.currentTarget.value.split(",") }); }} />
      </label>
    </details>
  );
}

/** Saves the working slip as an idea: an independent snapshot of every included leg and its price, taken now. */
function SaveIdea(props: { bet: Ready; ws: SlipWorkspace }) {
  const [status, setStatus] = useState<"watching" | "paper">("watching");
  const [saved, setSaved] = useState<string | null>(null);
  if (!activeLegs(props.ws.draft).length) return null;
  return (
    <section class="ph-bet-saveidea" aria-labelledby="ph-bet-saveidea-h">
      <h2 class="ph-bet-h" id="ph-bet-saveidea-h">
        Save as an idea
      </h2>
      <p class="small muted">The idea keeps the title, thesis and tags above with a frozen copy of every included leg and its price as it is now. Later price changes and edits to the slip never reach it.</p>
      <div class="row wrap ph-bet-saverow">
        <label>
          <span>Status</span>
          <select value={status} onChange={(e) => { setStatus(e.currentTarget.value === "paper" ? "paper" : "watching"); }}>
            <option value="watching">Watching</option>
            <option value="paper">Paper (tracking it as if placed)</option>
          </select>
        </label>
        <Button
          onClick={() => {
            const id = localId("idea");
            void props.bet.actions.saveIdea({ id, status }).then((d) => {
              if (say(d, "Idea")) setSaved(id);
            });
          }}
        >
          Save as an idea
        </Button>
      </div>
      {saved ? (
        <p class="small" role="status">
          Saved. <a href={ideaHref(saved)}>Open the idea</a> or <a href="#/bet/ideas">see all ideas</a>.
        </p>
      ) : null}
    </section>
  );
}

/** "Mark slip as placed": a confirmation that says exactly what it does, then a local record. */
function PlaceButton(props: { bet: Ready; ws: SlipWorkspace; ev: SlipEvaluation; onPlaced?: () => void }) {
  const [open, setOpen] = useState(false);
  const planned = props.ws.draft.legs.filter((l) => l.role === "planned" && l.enabled);
  if (!planned.length) return null;
  return (
    <>
      <Button kind="primary" wide onClick={() => { setOpen(true); }}>
        Mark slip as placed
      </Button>
      {open ? (
        <Dialog title="Mark slip as placed?" id="ph-bet-place" onClose={() => { setOpen(false); }}>
          <p>{PLACED_CONFIRM}</p>
          <ul class="small">
            {planned.map((l) => (
              <li key={l.id}>
                {marketWords(l.market, l.quote.side)} at {providerLabel(l.quote.provider)}
              </li>
            ))}
          </ul>
          {!props.ev.complete ? <p class="small ph-bet-err">Some legs are not fully priced (an unknown fee or amount). They are recorded as they are.</p> : null}
          <div class="row wrap">
            <Button onClick={() => { setOpen(false); }}>Cancel</Button>
            <Button
              kind="primary"
              onClick={() => {
                void props.bet.actions.markPlaced().then((d) => {
                  setOpen(false);
                  if (say(d)) props.onPlaced?.();
                });
              }}
            >
              I already placed these
            </Button>
          </div>
        </Dialog>
      ) : null}
    </>
  );
}

/** Export, import and the labelled demo (on the slip page and the #/bet hub). */
export function SlipTools(props: { bet: Ready; ws: SlipWorkspace }) {
  const [confirm, setConfirm] = useState<null | "demo" | { text: string }>(null);
  const [err, setErr] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const { bet, ws } = props;
  return (
    <section class="ph-bet-tools" aria-label="Workspace">
      <h2>Keep or move your slip</h2>
      <p class="small muted">Kept in this browser only, for {scopeLabel(bet.scope)}. Export a file to keep a copy or move it to another browser.</p>
      <div class="row wrap">
        <Button
          onClick={() => {
            void bet.store.exportJson().then(async (r) => {
              if (!r.ok) {
                setErr(r.text);
                return;
              }
              const s = await saveTextFile(r.filename, r.json, "application/json");
              if (!s.ok) setErr(s.reason);
            });
          }}
        >
          <Icon name="download" /> Export
        </Button>
        <Button onClick={() => file.current?.click()}>Import a file</Button>
        <input
          ref={file}
          type="file"
          accept=".json,application/json"
          class="sr"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            const f = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            if (!f) return;
            void f.text().then((text) => { setConfirm({ text }); }, () => { setErr("That file could not be read. Nothing was changed."); });
          }}
        />
        <Button
          onClick={() => {
            if (ws.draft.legs.length) setConfirm("demo");
            else void bet.actions.loadDemo().then((d) => say(d));
          }}
        >
          {DEMO_LABEL}
        </Button>
      </div>
      <p class="small muted">{DEMO_NOTE}</p>
      {err ? (
        <p class="ph-bet-err small" role="alert">
          {err}
        </p>
      ) : null}
      {confirm === "demo" ? (
        <Dialog title="Replace the working slip with the demo?" onClose={() => { setConfirm(null); }}>
          <p>The demo replaces the legs in your working slip. Picks waiting for a price and anything saved stay. {DEMO_NOTE}</p>
          <div class="row wrap">
            <Button onClick={() => { setConfirm(null); }}>Keep my slip</Button>
            <Button kind="primary" onClick={() => { void bet.actions.loadDemo().then((d) => { setConfirm(null); say(d); }); }}>
              Load the demo
            </Button>
          </div>
        </Dialog>
      ) : confirm !== null ? (
        <Dialog title="Replace this browser's workspace with the file?" onClose={() => { setConfirm(null); }}>
          <p>Everything in this browser's betting workspace (slip, typed prices, picks) is replaced by the file. If the file does not pass its check, nothing changes and you are told why.</p>
          <div class="row wrap">
            <Button onClick={() => { setConfirm(null); }}>Cancel</Button>
            <Button
              kind="primary"
              onClick={() => {
                void bet.store.importJson(confirm.text).then((d) => {
                  setConfirm(null);
                  if (d.ok) {
                    setErr(null);
                    say(d);
                  } else setErr(`Not imported: ${d.text}`);
                });
              }}
            >
              Replace with the file
            </Button>
          </div>
        </Dialog>
      ) : null}
    </section>
  );
}

/** The slip itself. `compact` is the panel; the page shows every outcome, the notes and the workspace tools. */
export function SlipBody(props: { bet: Ready; compact?: boolean }) {
  const { bet } = props;
  const { view } = bet;
  const now = useNow();
  const hedgeLink = useRef<HTMLAnchorElement>(null);
  const [placed, setPlaced] = useState(0);
  useEffect(() => {
    if (placed) hedgeLink.current?.focus();
  }, [placed]);
  const save = (
    <SaveState
      view={view}
      onRetry={() => { void bet.store.retry().then((d) => say(d)); }}
      onDiscard={() => { bet.store.discard(); }}
      onReload={() => { void bet.store.load({ discardLocal: true }); }}
    />
  );
  const ws = view.workspace;
  if (!ws) return view.phase === "error" ? save : <Skeleton lines={3} label="Loading your slip" />;
  const ev = evaluateDraft(ws.draft, { now });
  const recorded = ws.draft.legs.filter((l) => l.role === "existing");
  const planned = ws.draft.legs.filter((l) => l.role === "planned");
  const empty = !ws.draft.legs.length && !ws.picks.length;
  return (
    <div class="ph-bet-slip">
      {save}
      <MoneyNote arithmetic />
      {empty ? null : (
        <p class="small muted num">
          {[planned.length ? `${count(planned.filter((l) => l.enabled).length)} of ${count(planned.length)} planned ${planned.length === 1 ? "leg" : "legs"} included` : "", recorded.length ? `${count(recorded.length)} recorded ${recorded.length === 1 ? "position" : "positions"}` : "", ws.picks.length ? `${count(ws.picks.length)} waiting for a price` : ""].filter(Boolean).join(" · ")}.
          {ws.draft.origin === "sample" ? " This slip is the labelled demo: sample prices only." : ""}
        </p>
      )}
      {empty ? (
        <div class="ph-bet-empty">
          <h3>Your slip is empty</h3>
          <p>Start from a line: on a game or player page (Market lens) press "+ Slip" beside it, or open Compare to pick one of tonight's props with stored prices, or type your own market.</p>
          <div class="row wrap">
            <a class="btn primary" href="#/bet/compare">
              Open Compare
            </a>
            {props.compact ? (
              <a class="btn" href="#/bet/slip">
                Full slip page
              </a>
            ) : null}
          </div>
        </div>
      ) : null}
      {ws.picks.length ? (
        <section aria-label="Waiting for a price">
          <h3 class="ph-bet-h">Waiting for a price</h3>
          <ul class="plain ph-bet-picks">
            {ws.picks.map((p) => (
              <PickRow key={p.id} pick={p} bet={bet} />
            ))}
          </ul>
        </section>
      ) : null}
      {recorded.length ? (
        <section aria-label="Recorded positions">
          <h3 class="ph-bet-h">Recorded positions (always counted)</h3>
          {recorded.map((l) => (
            <LegCard key={l.id} leg={l} actions={bet.actions} now={now} compact={props.compact} />
          ))}
        </section>
      ) : null}
      {planned.length ? (
        <section aria-label="Planned legs">
          <h3 class="ph-bet-h">Planned legs</h3>
          {planned.map((l) => (
            <LegCard key={l.id} leg={l} actions={bet.actions} now={now} compact={props.compact} />
          ))}
        </section>
      ) : null}
      <EntryTerms bet={bet} ws={ws} />
      {ws.draft.legs.length ? (
        <>
          <Totals ev={ev} />
          <CoverageBlock coverage={ev.coverage} />
          <Warnings items={ev.warnings} />
          {ev.overlaps.length ? (
            <ul class="ph-bet-overlaps small">
              {ev.overlaps.map((o) => (
                <li key={`${o.kind}-${o.marketIds.join(",")}`}>
                  <strong>{o.label}.</strong> {o.detail}
                </li>
              ))}
            </ul>
          ) : null}
          <OutcomeTable evaluation={ev} limit={props.compact ? 4 : undefined} />
        </>
      ) : null}
      <div class="ph-bet-actions">
        {ws.picks.length && !planned.length ? (
          <a class="btn primary wide" href="#/bet/compare">
            Compare venues to price {ws.picks.length === 1 ? "this pick" : "these picks"}
          </a>
        ) : null}
        <PlaceButton bet={bet} ws={ws} ev={ev} onPlaced={() => { setPlaced((n) => n + 1); }} />
        {recorded.length ? (
          <a ref={hedgeLink} class={planned.some((l) => l.enabled) ? "btn wide" : "btn primary wide"} href="#/bet/hedge">
            Open Hedge Finder with these positions
          </a>
        ) : null}
        {props.compact ? (
          <a class="btn quiet wide" href="#/bet/slip">
            Open the full slip page
          </a>
        ) : null}
      </div>
      {props.compact ? null : (
        <>
          <Notes bet={bet} ws={ws} />
          <SaveIdea bet={bet} ws={ws} />
          <SlipTools bet={bet} ws={ws} />
        </>
      )}
    </div>
  );
}

/** #/bet/slip */
/** True while the slip page is on screen (the single-file snapshot keeps detail routes in memory, so the hash cannot say). */
let slipPageShown = false;
let slipPageListeners: Set<() => void> | null = null;
function slipPageSet(on: boolean): void {
  slipPageShown = on;
  for (const l of slipPageListeners ?? []) l();
}
function useSlipPageShown(): boolean {
  const [on, setOn] = useState(slipPageShown);
  useEffect(() => {
    slipPageListeners ??= new Set();
    const l = (): void => {
      setOn(slipPageShown);
    };
    slipPageListeners.add(l);
    l();
    return () => {
      slipPageListeners?.delete(l);
    };
  }, []);
  return on;
}

export function SlipPage() {
  ensureBetStyles();
  const bet = useBet();
  useEffect(() => {
    slipPageSet(true);
    return () => {
      slipPageSet(false);
    };
  }, []);
  return (
    <MarketGate name="The rolling slip" what="The rolling slip holds prices, costs and payouts.">
      <section class="ph-bet stack" aria-labelledby="ph-bet-slip-h">
        <PageHead
          id="ph-bet-slip-h"
          kicker="Betting tools"
          title={TITLE_SLIP}
          description="The picks and priced legs you are weighing, and the bets you already placed elsewhere, kept in this browser only."
        />
        {bet.ready ? <SlipBody bet={bet} /> : <Skeleton lines={3} label="Waiting to know who is looking" />}
      </section>
    </MarketGate>
  );
}

// ------------------------------------------------------------------------------------------------ the slip beside the page

const OWNER = "bet-slip";
let focusRailNext = false;

/** What the shell's side panel shows: the compact slip, plus a "Hide" control on the wide rail (Escape does the same). */
function SlipSide() {
  ensureBetStyles();
  const bet = useBet();
  const wide = useWide(RAIL_MIN);
  const hide = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (wide && focusRailNext) {
      focusRailNext = false;
      hide.current?.focus();
    }
  }, [wide]);
  const n = bet.ready && bet.view.workspace ? slipCount(bet.view.workspace) : 0;
  return (
    <div
      class="ph-bet ph-bet-side"
      onKeyDown={(e) => {
        if (wide && e.key === "Escape" && !e.defaultPrevented) {
          e.preventDefault();
          setPanelOpen(false);
        }
      }}
    >
      {wide ? (
        <div class="ph-bet-side-tools">
          <span class="small muted num">
            {count(n)} {n === 1 ? "item" : "items"} · kept on this device
          </span>
          <button ref={hide} type="button" class="btn small quiet" aria-label="Hide your slip (it stays saved)" onClick={() => { setPanelOpen(false); }}>
            Hide
          </button>
        </div>
      ) : null}
      {bet.ready ? <SlipBody bet={bet} compact /> : <Skeleton lines={3} label="Waiting to know who is looking" />}
    </div>
  );
}

const sideContent = () => <SlipSide />;

/** The coalesced betting notices, placed away from the control just used. */
function Notices() {
  const list = useNotices();
  // From 1024 px every notice sits in one stack at the sidebar's foot (betting.css), so top and bottom are one group.
  const oneStack = useWide(1024);
  if (!list.length) return null;
  const at = (place: "top" | "bottom") => (oneStack ? (place === "bottom" ? list : []) : list.filter((n) => n.place === place));
  return (
    <>
      {(["top", "bottom"] as const).map((place) =>
        at(place).length ? (
          <div key={place} class={`ph-bet ph-bet-toasts ${place}`} aria-live="polite">
            {at(place).map((n) => (
              <div key={n.id} class={`ph-bet-toast${n.tone === "failed" ? " failed" : ""}`} role={n.tone === "failed" ? "alert" : "status"}>
                <div>
                  <strong>{n.title}</strong>
                  <div class="small">{n.text}</div>
                </div>
                <button type="button" class="icon-btn" aria-label="Dismiss" onClick={() => { dismissNotice(n.id); }}>
                  <Icon name="close" />
                </button>
              </div>
            ))}
          </div>
        ) : null,
      )}
    </>
  );
}

/**
 * The persistent slip, Market lens only, through the shell's shared side panel: a rail beside the page from 1280 px (the
 * page narrows), a bottom sheet below. On a wide screen the rail can be hidden; the floating "Slip N" button brings it back.
 */
export function SlipPanel() {
  ensureBetStyles();
  const bet = useBet();
  const lens = useLens();
  const open = usePanelOpen();
  const wide = useWide(RAIL_MIN);
  const hash = useHash();
  const launcher = useRef<HTMLButtonElement>(null);
  const ready = bet.ready && (bet.view.workspace !== null || bet.view.phase === "error");
  const onSlipPage = useSlipPageShown();
  const plan = slipPanelPlan({ lens, ready, wide, open, onSlipPage: isSlipPage(hash) || onSlipPage });
  const n = bet.ready && bet.view.workspace ? slipCount(bet.view.workspace) : 0;
  const unsaved = bet.ready && bet.view.dirty;
  const was = useRef(plan);
  useEffect(() => {
    if (plan === "rail" || plan === "sheet") setSidePanel({ owner: OWNER, title: TITLE_SLIP, openLabel: `Slip · ${String(n)}${unsaved ? " (not saved)" : ""}`, content: sideContent });
    else clearSidePanel(OWNER);
  }, [plan, n, unsaved]);
  useEffect(
    () => () => {
      clearSidePanel(OWNER);
    },
    [],
  );
  useEffect(() => {
    // The floating launcher: the page's foot reserves room for it (the shell's stylesheet reads this class).
    document.documentElement.classList.toggle("has-float-launcher", plan === "launcher");
    return () => {
      document.documentElement.classList.remove("has-float-launcher");
    };
  }, [plan]);
  useEffect(() => {
    // Hiding the rail puts focus back on the button that brings it back.
    if (was.current === "rail" && plan === "launcher") launcher.current?.focus();
    was.current = plan;
  }, [plan]);
  return (
    <>
      <Notices />
      {plan === "launcher" ? (
        <div class="ph-bet">
          <button
            ref={launcher}
            type="button"
            class="ph-bet-launcher"
            aria-expanded="false"
            aria-label={`Show your slip, ${String(n)} ${n === 1 ? "item" : "items"}${unsaved ? ", not saved" : ""}`}
            onClick={() => {
              focusRailNext = true;
              setPanelOpen(true);
            }}
          >
            <Icon name="picks" />
            <span>Slip</span>
            <span class="ph-bet-count num">{n}</span>
            {unsaved ? <span class="ph-bet-dot" aria-hidden="true" /> : null}
          </button>
        </div>
      ) : null}
    </>
  );
}
