import { useMemo, useState } from "preact/hooks";
import { api, getTopContracts } from "../api";
import { Avatar } from "../Avatar";
import { TeamTag, WinBars } from "../components";
import { clvRate, newestFirst, signedCents, winRate, type Desk } from "../desk";
import { CertaintyChip } from "../DeskUI";
import { deskOf, useDesk } from "../deskStore";
import { edgeSentence, radarRows, watchKey, type RadarRow } from "../discover";
import { longDate, pct, tipTime } from "../format";
import { Icon } from "../icons";
import { useLoad } from "../useLoad";
import { toggleWatch, useWatch } from "../watch";
import type { GameSummary, ParlayState } from "../types";
import { STAT_LABEL, type Stat } from "../types";
import { engineSpeaksFor } from "../nights";
import { plainCents } from "../hedge";

const LEAGUES = ["NFL", "MLB", "NHL"] as const;

/** NBA is covered; the other leagues are shown disabled and say so. */
export function LeagueTabs() {
  return (
    <div class="leagues" role="group" aria-label="League">
      <span class="league on" aria-current="true">
        NBA
      </span>
      {LEAGUES.map((l) => (
        <button key={l} type="button" class="league" disabled aria-disabled="true" title="Not covered yet">
          {l} <span class="small">not covered yet</span>
        </button>
      ))}
    </div>
  );
}

export function useRadar(date: string, engine: ParlayState | null): { rows: RadarRow[]; loading: boolean; desk: Desk | null; nPriced: number | null } {
  const dh = useDesk();
  const desk = deskOf(dh);
  const [top] = useLoad(() => getTopContracts(10), []);
  const speaks = engine !== null && engineSpeaksFor(date, engine.report_date);
  const rows = useMemo(
    () => radarRows(desk, date, speaks && top.status === "ready" ? top.data : null),
    [desk, date, speaks, top],
  );
  return { rows, loading: dh.kind === "loading" || top.status === "loading", desk, nPriced: top.status === "ready" ? top.data.n_priced : null };
}

function fmtModel(m: number | null): string {
  return m === null ? "-" : pct(m);
}

/** "The closest look": the best row by edge after fees. Honest about a negative edge, and about what the edge is made of. */
export function Hero({ row, nPriced, fixture, wModel }: { row: RadarRow | null; nPriced: number | null; fixture: boolean; wModel: number | null }) {
  if (!row) {
    return (
      <section class="panel hero" aria-label="The closest look">
        <div class="label">The closest look</div>
        <h2>No priced contract is stored for this night</h2>
        <p class="muted">That is a normal state before the pre-tip run. There is nothing to look at, and nothing to bet.</p>
      </section>
    );
  }
  const neg = row.edge_c !== null && row.edge_c <= 0;
  const desk = row.source === "desk";
  return (
    <section class="panel hero" aria-label="The closest look">
      <div class="hero-main">
        <div class="label">The closest look{desk ? "" : nPriced ? ` · best of ${nPriced.toLocaleString("en-US")} priced player contracts` : ""}</div>
        <div class="hero-who">
          <Avatar name={row.name} team={row.team} playerId={row.playerId} size="lg" />
          <div class="hero-text">
            <h2 class="hero-name">{row.name}</h2>
            <p class="muted">
              {row.market}
              {row.matchup ? ` · ${row.matchup}` : ""}
            </p>
          </div>
        </div>
        <div class="hero-nums num">
          <div>
            <div class="hero-v">{desk ? fmtModel(row.fair) : fmtModel(row.model)}</div>
            <div class="small muted">{desk ? "fair price (sharp book)" : `model chance${fixture ? " (fixture)" : ""}`}</div>
          </div>
          <div>
            <div class="hero-v">{row.ask_c !== null ? plainCents(row.ask_c) : "-"}</div>
            <div class="small muted">ask</div>
          </div>
          <div>
            <div class={`hero-v ${neg ? "neg" : "pos"}`}>{row.edge_c !== null ? signedCents(row.edge_c, 1) : "-"}</div>
            <div class="small muted">edge after fees</div>
          </div>
        </div>
        <p class="small num edge-line">{edgeSentence(row)}</p>
        {desk ? (
          <p class="small muted num">
            Separately, our model says {fmtModel(row.model)}. Model weight {wModel === null ? "is not stored" : wModel.toString()}
            {wModel === 0 ? ": the model is not used in this edge." : "."}
          </p>
        ) : null}
        <p class="small muted">
          {desk ? "Source: JARVIS's price check against the sharp book's fair price. " : "Source: the parlay engine's stored contracts. "}
          {neg ? "The edge is not positive after fees, so this is not a bet: keep your money. " : "Unproven: the model has no track record yet. "}Paper only.
        </p>
        {row.href ? (
          <a class="btn" href={row.href}>
            Open the breakdown <Icon name="arrow" />
          </a>
        ) : null}
      </div>
    </section>
  );
}

/** "Around the court": one game. A final score only when the export stores one; else tip time and the win bar. */
export function Court({ game, final_ }: { game: GameSummary | null; final_: boolean }) {
  const [res] = useLoad(() => (game && final_ ? api.gameResult(game.game_id) : Promise.resolve({ data: null, fromCache: false })), [game?.game_id, final_]);
  if (!game) {
    return (
      <section class="panel court" aria-label="Around the court">
        <div class="label">Around the court</div>
        <p class="muted">No game is stored for this night.</p>
      </section>
    );
  }
  const r = res.status === "ready" ? res.data : null;
  return (
    <section class="panel court" aria-label="Around the court">
      <div class="label">Around the court</div>
      <div class="score-row">
        <div class="score-team">
          <TeamTag abbr={game.away.abbr} big />
          <span class="muted small">{game.away.city}</span>
        </div>
        <div class="score-mid num">
          {r ? (
            <span class="final">
              {r.away_pts}
              <span class="dash">-</span>
              {r.home_pts}
            </span>
          ) : (
            <span class="at">at</span>
          )}
        </div>
        <div class="score-team right">
          <TeamTag abbr={game.home.abbr} big />
          <span class="muted small">{game.home.city}</span>
        </div>
      </div>
      <p class="muted small center num">{r ? "Final" : `Tip-off ${tipTime(game.tip_utc)}`}</p>
      <WinBars wp={game.win_probability} home={game.home} away={game.away} />
      <a class="btn" href={`#/game/${game.game_id}`}>
        Open the game <Icon name="arrow" />
      </a>
    </section>
  );
}

/** Tiles for the desk: signals tonight, watchlist, record and closing-line value (money only in the private cut). */
export function DeskTiles({ desk, date }: { desk: Desk; date: string }) {
  const watch = useWatch();
  const clv = clvRate(desk);
  const rec = winRate(desk.record);
  const signals = desk.observations.filter((o) => o.slate_date === date).length;
  return (
    <section class="tiles" aria-label="The desk in numbers">
      <div class="tile">
        <div class="tile-v num">{signals.toLocaleString("en-US")}</div>
        <div class="tile-l">signals on this night</div>
        <div class="tile-n muted">stored in-game observations</div>
      </div>
      <div class="tile">
        <div class="tile-v num">{watch.mine.length.toLocaleString("en-US")}</div>
        <div class="tile-l">on your watchlist</div>
        <div class="tile-n muted">kept for you</div>
      </div>
      <div class="tile">
        <div class="tile-v num">{desk.record ? `${desk.record.n_won.toString()}-${desk.record.n_lost.toString()}` : "-"}</div>
        <div class="tile-l">paper record</div>
        <div class="tile-n muted num">{rec.n > 0 ? `n = ${rec.n.toLocaleString("en-US")}` : "none settled"}</div>
      </div>
      <div class="tile">
        <div class="tile-v num">{clv.value !== null ? signedCents(clv.value, 2) : "-"}</div>
        <div class="tile-l">closing-line value</div>
        <div class="tile-n muted num">{clv.n > 0 ? `n = ${clv.n.toLocaleString("en-US")}${clv.ci ? `, ${clv.ci[0].toFixed(2)}c to ${clv.ci[1].toFixed(2)}c` : ""}` : "none stored"}</div>
      </div>
    </section>
  );
}

/** "On your radar": the rows with watch toggles and an All / Watchlist filter. */
export function Radar({ rows, loading }: { rows: RadarRow[]; loading: boolean }) {
  const watch = useWatch();
  const [only, setOnly] = useState(false);
  const watched = new Set(watch.mine.map((i) => i.key));
  const view = only ? rows.filter((r) => watched.has(watchKey(r))) : rows;
  return (
    <section class="panel" aria-labelledby="radar-h">
      <div class="row between wrap">
        <div>
          <h2 id="radar-h">On your radar</h2>
          <p class="small muted">
            {rows[0]?.source === "desk"
              ? "The desk's plays for this night, best edge first. Source: JARVIS's price check against the sharp book's fair price, a different check from the headline above."
              : "The best-priced stored contracts for this night, by edge after fees. Source: the same parlay engine check as the headline."}
          </p>
        </div>
        <div class="seg" role="group" aria-label="Which rows">
          <button type="button" class="seg-btn" aria-pressed={!only} onClick={() => { setOnly(false); }}>
            All signals
          </button>
          <button type="button" class="seg-btn" aria-pressed={only} onClick={() => { setOnly(true); }}>
            Watchlist
          </button>
        </div>
      </div>
      {loading ? <p class="muted">Loading...</p> : null}
      {!loading && view.length === 0 ? (
        <p class="muted empty-line">{only ? "Nothing on your watchlist for this night yet. Use the star on a row to add it." : "No priced rows are stored for this night."}</p>
      ) : (
        <div class="table-wrap">
          <table class="radar num">
            <thead>
              <tr>
                <th>Player / market</th>
                <th class="r">Fair</th>
                <th class="r">Model</th>
                <th class="r">Ask</th>
                <th>Status</th>
                <th>
                  <span class="sr">Watch</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {view.map((r) => {
                const on = watched.has(watchKey(r));
                return (
                  <tr key={r.key}>
                    <td>
                      <span class="who">
                        <Avatar name={r.name} team={r.team} playerId={r.playerId} size="sm" />
                        <span>
                          {r.href ? <a href={r.href}><strong>{r.name}</strong></a> : <strong>{r.name}</strong>}
                          <span class="muted small block">{r.market}{r.matchup ? ` · ${r.matchup}` : ""}</span>
                        </span>
                      </span>
                    </td>
                    <td class="r">{fmtModel(r.fair)}</td>
                    <td class="r">{fmtModel(r.model)}</td>
                    <td class="r">{r.ask_c !== null ? plainCents(r.ask_c) : "-"}</td>
                    <td class="small">{r.status}</td>
                    <td>
                      <button
                        type="button"
                        class={`watch${on ? " on" : ""}`}
                        aria-pressed={on}
                        aria-label={`${on ? "Remove from" : "Add to"} your watchlist: ${r.name}, ${r.market}`}
                        onClick={() => {
                          toggleWatch({ key: watchKey(r), label: `${r.name} ${r.market}`.slice(0, 80), sub: (r.matchup ?? "").slice(0, 80) });
                        }}
                      >
                        <Icon name="flag" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p class="small muted">Stored prices only. "Fair" is the sharp book's price without its margin; "Model" is our own unproven chance. Paper only; nothing here suggests a stake.</p>
    </section>
  );
}

/** The newest observation, said with its certainty. */
export function Insight({ desk }: { desk: Desk | null }) {
  const o = desk ? newestFirst(desk.observations)[0] : undefined;
  return (
    <section class="panel insight" aria-label="Newest observation">
      <div class="label">The desk's newest observation</div>
      {o ? (
        <>
          <div class="obs-top">
            <CertaintyChip o={o} />
          </div>
          <h3>{o.title}</h3>
          <p class={o.text ? "" : "muted"}>{o.text ?? "No further text was stored."}</p>
          <p class="small muted num">{[o.matchup, o.when_text, longDate(o.slate_date)].filter(Boolean).join(" · ")}</p>
          <a class="linklike" href="#/signals">
            See all signals
          </a>
        </>
      ) : (
        <p class="muted">No observation is stored{desk ? "" : ": this build has no desk export"}.</p>
      )}
    </section>
  );
}

/** Discipline: the kill switch and model review, when there is a desk; else what the parlay engine's gate says. */
export function Discipline({ desk, engine }: { desk: Desk | null; engine: ParlayState | null }) {
  const ks = desk?.status.kill_switch;
  return (
    <section class={`panel discipline${ks?.on ? " alarm" : ""}`} aria-label="Discipline">
      <div class="label">Discipline comes first</div>
      {desk && ks ? (
        <>
          <h3>{ks.on ? "Kill switch on: the model is in review" : desk.status.model_review ? "Model in review" : "Kill switch off"}</h3>
          <p class="small num">
            {ks.n !== null && ks.mean_clv_cents !== null
              ? `JARVIS's own rule: mean closing-line value ${signedCents(ks.mean_clv_cents, 2)} across n = ${ks.n.toString()} bets${ks.since ? `, since ${longDate(ks.since.slice(0, 10))}` : ""}.`
              : "No kill-switch sample is stored."}
          </p>
          <a class="linklike" href="#/history">
            Open Model History
          </a>
        </>
      ) : (
        <>
          <h3>{engine ? engine.title : "No discipline check is stored"}</h3>
          <p class="small">{engine ? engine.verdict_line : "The engine's state could not be read."}</p>
          <a class="linklike" href="#/parlay">
            The full story
          </a>
        </>
      )}
    </section>
  );
}

export function statLabelOf(stat: string | null): string {
  return stat !== null && stat in STAT_LABEL ? STAT_LABEL[stat as Stat] : (stat ?? "");
}
