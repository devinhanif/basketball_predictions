import { useState } from "preact/hooks";
import { api } from "../api";
import { DistBar, DistLegend, View, WinBars, WinLegend } from "../components";
import { american, lineText, num, pct, pctBounded, tipTime } from "../format";
import { useLoad } from "../useLoad";
import type { GameDetail, GameResult, PlayerView, PropView, Stat } from "../types";
import { STATS, STAT_LABEL } from "../types";

function PropBlock(props: {
  player: string;
  prop: PropView;
  actual: number | undefined;
}) {
  const { prop, player, actual } = props;
  const q = prop.q;
  return (
    <div class="prop-block">
      <div class="row between wrap" style={{ gap: 6 }}>
        <strong>{STAT_LABEL[prop.stat]}</strong>
        <span class="small muted num">
          line {lineText(prop.line)} · over {american(prop.over_price)} · under {american(prop.under_price)}
        </span>
      </div>
      <DistBar player={player} prop={prop} actual={actual} />
      <div class="kv num">
        <span>
          Model over: <b style={{ color: "var(--model)" }}>{pctBounded(prop.model_p_over, prop.extrapolated)}</b>
        </span>
        <span>
          Book over: <b style={{ color: "var(--market)" }}>{pct(prop.market_p_over)}</b>
        </span>
        <span>
          Median <b>{num(q[9] ?? 0)}</b>, 5% to 95%: <b>{num(q[0] ?? 0)}</b> to <b>{num(q[18] ?? 0)}</b>
        </span>
        {prop.model_p_push > 0.005 ? <span>Push chance: {pct(prop.model_p_push)}</span> : null}
      </div>
      {actual !== undefined ? (
        <div class="kv">
          <span class="tag result">result</span>
          <span>
            Actual <b>{actual}</b> against a line of <b>{lineText(prop.line)}</b>:{" "}
            <b>{actual === prop.line ? "push" : actual > prop.line ? "went over" : "went under"}</b>
          </span>
        </div>
      ) : null}
    </div>
  );
}

function PlayerRow(props: { p: PlayerView; result: GameResult | null }) {
  const first = STATS.map((s) => props.p.props[s]).find((x) => x !== undefined);
  const res = props.result?.players.find((r) => r.player_id === props.p.player_id);
  const pts = props.p.props.pts;
  const lead = pts ?? first;
  return (
    <details class="player">
      <summary>
        <span class="row between" style={{ width: "100%" }}>
          <span>
            <strong>{props.p.name}</strong>
            {res?.starter ? <span class="tag" style={{ marginLeft: 8 }}>starter</span> : null}
          </span>
          {lead ? (
            <span class="small num muted">
              {lead.stat} {lineText(lead.line)}: model{" "}
              <b style={{ color: "var(--model)" }}>{pctBounded(lead.model_p_over, lead.extrapolated)}</b> / book{" "}
              <b style={{ color: "var(--market)" }}>{pct(lead.market_p_over)}</b>
            </span>
          ) : null}
        </span>
      </summary>
      {res ? (
        <p class="small muted" style={{ marginTop: 0 }}>
          <span class="tag result">result</span> Played <b>{res.minutes === null ? "n/a" : `${num(res.minutes)} min`}</b>
          {res.starter === null ? "" : res.starter ? ", started" : ", off the bench"}. Minutes are not forecast in this fixture.
        </p>
      ) : (
        <p class="small muted" style={{ marginTop: 0 }}>
          Minutes are not forecast in this fixture; the actual minutes appear only after you show the result.
        </p>
      )}
      {STATS.map((s: Stat) => {
        const prop = props.p.props[s];
        return prop ? <PropBlock key={s} player={props.p.name} prop={prop} actual={res?.actual[s]} /> : null;
      })}
    </details>
  );
}

function Body({ g }: { g: GameDetail }) {
  const [show, setShow] = useState(false);
  const [result, reloadResult] = useLoad(() => (show ? api.gameResult(g.game_id) : Promise.resolve({ data: null, fromCache: false })), [g.game_id, show]);
  const res: GameResult | null = result.status === "ready" ? result.data : null;
  const [side, setSide] = useState<"away" | "home">("away");
  const team = side === "home" ? g.home : g.away;
  const players = g.players.filter((p) => p.side === side);
  return (
    <div class="stack">
      <p class="small">
        <a href="#/">Back to the slate</a>
      </p>
      <section class="card">
        <div class="teams">
          <h1>
            {g.away.abbr} <span class="at muted" style={{ fontWeight: 500 }}>at</span> {g.home.abbr}
          </h1>
          <span class="muted small num">{tipTime(g.tip_utc)} scheduled tip</span>
        </div>
        <div class="muted small">
          {g.away.city} {g.away.name} at {g.home.city} {g.home.name}
          {g.arena ? `, ${g.arena}` : ""}
        </div>
        <WinBars wp={g.win_probability} home={g.home} away={g.away} />
        <WinLegend />
        <p class="small muted" style={{ marginBottom: 0 }}>
          Model stamp: {g.win_probability.model_stamp ?? "none"}. n = 1 game; see the Method page for how the model does over
          a season.
        </p>
      </section>

      <section class="card">
        <div class="row between wrap">
          <div>
            <h2>Result</h2>
            <p class="small muted" style={{ margin: 0 }}>
              Hidden by default so you can read the forecast first.
            </p>
          </div>
          <button type="button" class="btn" aria-pressed={show} onClick={() => { setShow(!show); }}>
            {show ? "Hide result" : "Show result"}
          </button>
        </div>
        {show && result.status === "loading" ? <p class="muted">Loading...</p> : null}
        {show && result.status === "error" ? (
          <p role="alert">
            Could not load the result. <button type="button" class="btn" onClick={reloadResult}>Retry</button>
          </p>
        ) : null}
        {res ? (
          <p class="num" style={{ marginTop: 12, marginBottom: 0 }}>
            <span class="tag result">result</span> Final: {g.away.abbr} <b>{res.away_pts}</b>, {g.home.abbr}{" "}
            <b>{res.home_pts}</b>. {res.home_won ? g.home.name : g.away.name} won
            {g.win_probability.model_home !== null
              ? `; the model had them at ${pct(res.home_won ? g.win_probability.model_home : 1 - g.win_probability.model_home)}`
              : ""}
            .
          </p>
        ) : null}
      </section>

      <section class="card">
        <div class="row between wrap">
          <h2>Who plays, and what they produce</h2>
          <div class="chips" role="tablist" aria-label="Team">
            {(["away", "home"] as const).map((s) => (
              <button
                key={s}
                type="button"
                role="tab"
                aria-selected={side === s}
                class="chip"
                aria-current={side === s ? "true" : undefined}
                onClick={() => { setSide(s); }}
              >
                {(s === "home" ? g.home : g.away).abbr}
              </button>
            ))}
          </div>
        </div>
        <p class="small muted">
          {team.city} {team.name}: {players.length} players with a posted line, ordered by forecast points. Forecasts are
          conditional on the player playing. <span class="tag forecast">forecast</span>
        </p>
        <DistLegend />
        <div style={{ marginTop: 8 }}>
          {players.length === 0 ? (
            <p class="muted">No posted lines for this team.</p>
          ) : (
            players.map((p) => <PlayerRow key={p.player_id} p={p} result={res} />)
          )}
        </div>
        <ul class="plain small muted">
          {g.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export function Game({ id, onStale }: { id: string; onStale: (s: boolean) => void }) {
  const [state, reload] = useLoad(() => api.game(id), [id]);
  return (
    <View state={state} reload={reload} onStale={onStale}>
      {(g) => <Body g={g} />}
    </View>
  );
}
