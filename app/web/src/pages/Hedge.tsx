import { useEffect, useMemo, useState } from "preact/hooks";
import { api, getTopContracts, type TopContract } from "../api";
import { Avatar } from "../Avatar";
import { Loading } from "../components";
import { deskOf, useDesk } from "../deskStore";
import { latestPlayDate, playLabel, playsOn, type DeskPlay } from "../desk";
import type { EdgeContract } from "../edges";
import { Icon } from "../icons";
import { STAT_LABEL, type Stat } from "../types";
import {
  candidatesFor, centsText, clampLeg, legCost, legFromContract, legFromPlay, legWords, outcomes, plainCents, summarize, totalCost,
  type Candidate, type GameCtx, type HedgeLeg,
} from "../hedge";
import { useLoad } from "../useLoad";
import { statLabelOf } from "./DiscoverParts";
import { fairAgeMin, STALE_MIN } from "../discover";

const NOTE =
  "Paper arithmetic on the prices shown. Settlement rules, fees and execution are unverified. Nothing here places an order, and no size is suggested.";

interface Pick {
  matchup: string;
  game: GameCtx;
  player: string;
  playerId: number | null;
  team: string | null;
  stat: Stat;
  statLabel: string;
  base: HedgeLeg;
  stored: EdgeContract[];
}

function gameFromMatchup(m: string | null): GameCtx {
  const [away, home] = (m ?? "").split(/@| at /).map((x) => x.trim());
  return { home: home || "Home", away: away || "Away" };
}

async function storedFor(gameId: string, playerId: number, stat: string): Promise<EdgeContract[]> {
  try {
    return (await api.edges(gameId, playerId, stat)).data.contracts;
  } catch {
    return [];
  }
}

async function pickFromPlay(p: DeskPlay): Promise<Pick | null> {
  if (p.threshold === null || p.price_cents === null || p.stat === null) return null;
  const stat = p.stat as Stat;
  const label = statLabelOf(p.stat);
  const stored = p.game_id && p.player_id !== null ? await storedFor(p.game_id, p.player_id, p.stat) : [];
  // The shared desk cut carries no fee: take it from the export's own contract row for this threshold and side, else the
  // viewer enters one (0 until then). The ask is the desk's stored ask.
  const side = p.side === "no" ? "no" : "yes";
  const row = stored.find((s) => s.threshold === p.threshold && s.side === (side === "yes" ? "over" : "under"));
  const fee = p.fee_cents ?? (row ? row.fees * 100 : null);
  const base = legFromPlay(p, label.toLowerCase(), fee, fee !== null ? "stored" : "entered");
  if (!base) return null;
  return { matchup: p.matchup ?? "game not stored", game: gameFromMatchup(p.matchup), player: p.player_name ?? "Unnamed player", playerId: p.player_id, team: p.team, stat, statLabel: label, stored, base };
}

async function pickFromContract(c: TopContract): Promise<Pick> {
  const stat = c.stat as Stat;
  const label = statLabelOf(c.stat);
  const stored = await storedFor(c.game_id, c.player_id, c.stat);
  const row = stored.find((s) => s.ticker === c.ticker && s.side === c.side) ?? { ...c, order_type: "", price_ts: c.price_ts };
  return { matchup: c.matchup, game: gameFromMatchup(c.matchup), player: c.player, playerId: c.player_id, team: c.team, stat, statLabel: label, stored, base: legFromContract(row, label.toLowerCase(), 1, c.player) };
}

function Picker(props: { onPick: (p: Pick) => void }) {
  const desk = useDesk();
  const d = deskOf(desk);
  const [top] = useLoad(() => getTopContracts(8), []);
  const date = d ? latestPlayDate(d) : null;
  const plays = d && date ? playsOn(d, date).filter((p) => p.price_cents !== null && p.threshold !== null).slice(0, 6) : [];
  const [busy, setBusy] = useState(false);
  const go = (f: () => Promise<Pick | null>): void => {
    setBusy(true);
    void f().then((p) => {
      setBusy(false);
      if (p) props.onPick(p);
    });
  };
  return (
    <section class="panel" aria-labelledby="pick-h">
      <h2 id="pick-h">Choose a position to start from</h2>
      <p class="small muted">Prices and fees come from stored rows. You then set how many contracts, and can change any price.</p>
      {plays.length > 0 ? (
        <>
          <div class="label">Desk plays, {date}</div>
          <ul class="pick-list">
            {plays.map((p) => (
              <li key={p.play_id}>
                <button type="button" class="pick-row" disabled={busy} onClick={() => { go(() => pickFromPlay(p)); }}>
                  <Avatar name={p.player_name ?? "Unnamed"} playerId={p.player_id} size="sm" />
                  <span class="pick-main">
                    <strong>{playLabel(p)}</strong>
                    <span class="small muted">
                      {p.matchup ?? ""} · ask {plainCents(p.price_cents ?? 0)}
                      {fairAgeMin(p.reason) !== null && (fairAgeMin(p.reason) ?? 0) > STALE_MIN ? ` · fair price ${String(fairAgeMin(p.reason))} min old (stale)` : ""}
                    </span>
                  </span>
                  <Icon name="plus" />
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <div class="label">Best-priced stored contracts</div>
      {top.status === "loading" ? <Loading /> : null}
      {top.status === "error" ? <p class="muted">No stored contracts are available in this data.</p> : null}
      {top.status === "ready" && top.data.contracts.length === 0 ? <p class="muted">No contract was priced for this night.</p> : null}
      {top.status === "ready" ? (
        <ul class="pick-list">
          {top.data.contracts.map((c) => (
            <li key={`${c.ticker}-${c.side}`}>
              <button type="button" class="pick-row" disabled={busy} onClick={() => { go(() => pickFromContract(c)); }}>
                <Avatar name={c.player} team={c.team} playerId={c.player_id} size="sm" />
                <span class="pick-main">
                  <strong>
                    {c.player}: {legWords(c.side === "over" ? "yes" : "no", c.threshold, statLabelOf(c.stat).toLowerCase())}
                  </strong>
                  <span class="small muted num">
                    {c.matchup} · ask {plainCents(c.price * 100)} + fee {plainCents(c.fees * 100)}
                  </span>
                </span>
                <Icon name="plus" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Field(props: { label: string; value: number | null; min: number; max: number; step?: number; onChange: (n: number, empty: boolean) => void; id: string }) {
  return (
    <label class="field" for={props.id}>
      <span class="label">{props.label}</span>
      <input
        id={props.id}
        type="number"
        inputMode="decimal"
        min={props.min}
        max={props.max}
        step={props.step ?? 1}
        value={props.value === null ? "" : props.value}
        placeholder="-"
        onInput={(e) => {
          props.onChange(Number(e.currentTarget.value), e.currentTarget.value === "");
        }}
      />
    </label>
  );
}

function LegEditor(props: { leg: HedgeLeg; removable: boolean; onChange: (l: HedgeLeg) => void; onRemove: () => void }) {
  const { leg } = props;
  const set = (patch: Partial<HedgeLeg>): void => {
    props.onChange(clampLeg({ ...leg, ...patch }));
  };
  return (
    <div class="leg-ed">
      <div class="row between">
        <div>
          <strong>{leg.label}</strong>
          <div class="small muted">{leg.source === "stored" ? "Ask and fee from a stored row" : "Your own entered price"}</div>
        </div>
        {props.removable ? (
          <button type="button" class="icon-btn" aria-label={`Remove ${leg.label}`} onClick={props.onRemove}>
            <Icon name="trash" />
          </button>
        ) : null}
      </div>
      <div class="fields">
        <Field id={`${leg.id}-n`} label="Contracts" value={leg.contracts === 0 ? null : leg.contracts} min={0} max={1000} onChange={(n) => { set({ contracts: n }); }} />
        <Field id={`${leg.id}-a`} label="Ask (c)" value={leg.ask_c === 0 ? null : leg.ask_c} min={1} max={99} onChange={(n) => { set({ ask_c: n, source: "entered" }); }} />
        <Field id={`${leg.id}-f`} label="Fee (c)" value={leg.fee_set ? leg.fee_c : null} min={0} max={50} step={0.1} onChange={(n, empty) => { set({ fee_c: n, fee_set: !empty }); }} />
      </div>
      <div class="small muted num leg-cost">Cost: {plainCents(legCost(leg))}{leg.fee_set ? "" : " before fee (fee unknown: enter it)"}</div>
    </div>
  );
}

function Lab({ pick, onReset }: { pick: Pick; onReset: () => void }) {
  const [added, setAdded] = useState<HedgeLeg[]>([]);
  const [base, setBase] = useState<HedgeLeg>(pick.base);
  const legs = [base, ...added];
  const cands = useMemo<Candidate[]>(() => candidatesFor(pick.base, pick.stored, pick.statLabel.toLowerCase(), pick.game).filter((c) => !added.some((a) => a.label === c.leg.label)), [pick, added]);
  const sum = summarize(legs, pick.game);
  const os = outcomes(legs, pick.game);
  const maxAbs = Math.max(1, ...os.map((o) => Math.abs(o.net_c)));
  const origCost = legCost(base);
  const addedCost = totalCost(added.filter((a) => a.contracts > 0 && a.ask_c > 0));
  return (
    <>
      <header class="page-head">
        <div class="kicker">Position lab</div>
        <h1 class="answer">
          {sum
            ? `Worst case ${centsText(sum.worst_c)}, best case ${centsText(sum.best_c)}${sum.before_fee ? " before fee" : ""}, across ${os.length.toString()} outcomes at the prices entered`
            : "Enter contracts and an ask to see every outcome"}
        </h1>
        <p class="muted answer-src">{NOTE}</p>
      </header>
      <section class="panel hero-pos" aria-label="Your paper position">
        <Avatar name={pick.player} team={pick.team} playerId={pick.playerId} size="lg" />
        <div class="hp-main">
          <div class="label">Your paper position</div>
          <h2>{base.label}</h2>
          <p class="muted small num">
            {pick.matchup} · {base.contracts} contract{base.contracts === 1 ? "" : "s"} · {plainCents(origCost)} total cost
          </p>
        </div>
        <div class="hp-pay num">
          <div class="label">If every contract pays</div>
          <div class="hp-v">{plainCents(base.contracts * 100)}</div>
        </div>
      </section>
      <div class="two-col wide-left">
        <div class="stack">
          <section class="panel" aria-labelledby="wc-h">
            <div class="label" id="wc-h">Worst-case net outcome</div>
            <div class={`wc-v num ${sum && sum.worst_c >= 0 ? "pos" : "neg"}`}>{sum ? centsText(sum.worst_c) : "-"}</div>
            <p class="muted small">Across all {os.length} modeled outcomes (stat count x game result), cost included.</p>
            {sum ? (
              <div class="range-bar num small" role="img" aria-label={`Original cost ${plainCents(origCost)}; best outcome ${centsText(sum.best_c)}`}>
                <span>Original cost {plainCents(origCost)}</span>
                <span>Best outcome {centsText(sum.best_c)}</span>
              </div>
            ) : null}
            <h3 class="space-top">Every outcome, accounted for</h3>
            <p class="small muted">
              {pick.player}'s {pick.statLabel.toLowerCase()} {legs.some((l) => l.kind === "team") ? `x ${pick.game.away} at ${pick.game.home} result` : ""}
            </p>
            <ul class="outcomes">
              {os.map((o) => (
                <li key={o.id}>
                  <span class="oc-label">
                    <strong>{o.label}</strong>
                    <span class="small muted">{o.paying === 0 ? "no position pays" : `${o.paying.toString()} paying position${o.paying === 1 ? "" : "s"}`}</span>
                  </span>
                  <span class={`oc-net num ${o.net_c >= 0 ? "pos" : "neg"}`}>{centsText(o.net_c)}</span>
                  <span class="oc-bar" aria-hidden="true">
                    <i class={o.net_c >= 0 ? "pos" : "neg"} style={{ width: `${(Math.abs(o.net_c) / maxAbs) * 50}%`, [o.net_c >= 0 ? "left" : "right"]: "50%" }} />
                  </span>
                </li>
              ))}
            </ul>
            {sum ? (
              <p class="note-box small">
                {sum.before_fee ? "Fee unknown: every number here is before fee. " : ""}
                {sum.positive_everywhere
                  ? "Positive across every modeled outcome at the prices entered. "
                  : "Not positive in every outcome at the prices entered. "}
                Execution, fees and matching settlement rules remain unverified.
              </p>
            ) : null}
          </section>
          <details class="panel" open>
            <summary>
              <strong>How the math works</strong>
            </summary>
            <p class="small">
              Each contract pays 100c if it wins and nothing if it loses. Cost is contracts x (ask + fee). The net of an outcome is what
              the winning contracts pay minus the cost of every contract you hold. Kalshi rounds its fee on a whole order, so contracts x the
              per-contract fee can be a little high.
            </p>
            <div class="math num">
              <div>
                <div class="label">Original position</div>
                <b>{plainCents(origCost)}</b>
              </div>
              <div>
                <div class="label">Added scenario cost</div>
                <b>{plainCents(addedCost)}</b>
              </div>
              <div>
                <div class="label">Combined debit</div>
                <b>{plainCents(origCost + addedCost)}</b>
              </div>
            </div>
          </details>
        </div>
        <aside class="stack side-col">
          <section class="panel" aria-labelledby="sc-h">
            <div class="row between">
              <h2 id="sc-h">Your scenario</h2>
              <span class="chip num">{added.length} added</span>
            </div>
            <LegEditor leg={base} removable={false} onChange={setBase} onRemove={() => undefined} />
            {added.map((a) => (
              <LegEditor
                key={a.id}
                leg={a}
                removable
                onChange={(l) => {
                  setAdded((xs) => xs.map((x) => (x.id === l.id ? l : x)));
                }}
                onRemove={() => {
                  setAdded((xs) => xs.filter((x) => x.id !== a.id));
                }}
              />
            ))}
            <div class="label space-top">Same-game candidates</div>
            <ul class="cand-list">
              {cands.slice(0, 7).map((c) => (
                <li key={c.leg.id}>
                  <button
                    type="button"
                    class="cand"
                    onClick={() => {
                      setAdded((xs) => [...xs, { ...c.leg, contracts: c.leg.ask_c > 0 ? base.contracts : c.leg.contracts }]);
                    }}
                  >
                    <span>
                      <strong>{c.leg.label}</strong>
                      <span class="small muted">
                        {c.leg.ask_c > 0 ? ` · ask ${plainCents(c.leg.ask_c)} + fee ${plainCents(c.leg.fee_c)}` : ""}
                      </span>
                      <span class="small muted block">{c.note}</span>
                    </span>
                    <Icon name="plus" />
                  </button>
                </li>
              ))}
            </ul>
            <button
              type="button"
              class="btn wide"
              onClick={() => {
                setAdded([]);
                setBase(pick.base);
                onReset();
              }}
            >
              <Icon name="reset" /> Reset scenario
            </button>
          </section>
          <section class="panel flat">
            <p class="small muted">Paper only. A favorite can still lose alongside your original position; compare the settlement conditions, not just the chance of a win.</p>
          </section>
        </aside>
      </div>
    </>
  );
}

export function Hedge(props: { game: string | null; player: number | null; stat: string | null }) {
  const [pick, setPick] = useState<Pick | null>(null);
  useEffect(() => {
    // A link from a leg page names the player and stat: start from that player's best stored contract.
    if (props.game && props.player !== null && props.stat) {
      void storedFor(props.game, props.player, props.stat).then(async (rows) => {
        const best = [...rows].sort((a, b) => b.ev - a.ev)[0];
        if (!best) return;
        try {
          const g = (await api.game(props.game as string)).data;
          const pl = g.players.find((p) => p.player_id === props.player);
          const stat = props.stat as Stat;
          setPick({
            matchup: `${g.away.abbr} @ ${g.home.abbr}`, game: { home: g.home.abbr, away: g.away.abbr }, player: pl?.name ?? "Unnamed player", playerId: props.player, team: pl ? (pl.side === "home" ? g.home.abbr : g.away.abbr) : null,
            stat, statLabel: STAT_LABEL[stat], stored: rows, base: legFromContract(best, STAT_LABEL[stat].toLowerCase(), 1, pl?.name),
          });
        } catch {
          /* the picker stays */
        }
      });
    }
  }, [props.game, props.player, props.stat]);
  return (
    <div class="stack">
      {pick ? (
        <Lab key={pick.base.id} pick={pick} onReset={() => undefined} />
      ) : (
        <>
          <header class="page-head">
            <div class="kicker">Position lab</div>
            <h1 class="answer">Choose a stored contract to see what it pays in every outcome</h1>
            <p class="muted answer-src">{NOTE}</p>
          </header>
          <Picker onPick={setPick} />
        </>
      )}
      {pick ? (
        <button
          type="button"
          class="btn"
          onClick={() => {
            setPick(null);
          }}
        >
          Choose another position
        </button>
      ) : null}
    </div>
  );
}
