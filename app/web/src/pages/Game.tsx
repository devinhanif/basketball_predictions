import { useState } from "preact/hooks";
import { api } from "../api";
import { AltLine } from "../AltLine";
import { RoleSection } from "../RoleSection";
import { SteadyBadge } from "../RoleCards";
import { DistBar, DistLegend, Explain, TeamTag, View, WinBars, WinLegend } from "../components";
import { american, bookName, lineText, num, pct, pctBounded, tipTime } from "../format";
import { legHref } from "../router";
import { useLoad } from "../useLoad";
import type { GameDetail, GameResult, PlayerView, PropView, Stat } from "../types";
import { STATS, STAT_LABEL, STAT_SHORT } from "../types";
import { chanceSentence, didText } from "../words";

function PropBlock(props: { gameId: string; playerId: number; player: string; prop: PropView; actual: number | undefined }) {
  const { prop, player, actual } = props;
  const q = prop.q;
  return (
    <div class="prop-block">
      <div class="row between wrap">
        <strong>{STAT_LABEL[prop.stat]}</strong>
        <span class="small muted num">
          {prop.line_source === "reference"
            ? `Reference line ${lineText(prop.line)} (no usable sportsbook line)`
            : prop.over_price !== null && prop.under_price !== null
              ? `Line ${lineText(prop.line)} · Over ${american(prop.over_price)} · Under ${american(prop.under_price)}${prop.price_book ? ` (${bookName(prop.price_book)})` : ""}`
              : `Line ${lineText(prop.line)} (no usable price for both sides)`}
        </span>
      </div>
      <p class="row wrap" style={{ margin: "6px 0" }}>
        <a class="btn leg-open" href={legHref(props.gameId, props.playerId, prop.stat)}>
          Full breakdown: last 10, splits, teammates out
        </a>
      </p>
      <p class="prop-say">{chanceSentence(player, prop.stat, prop.line, "over", prop.model_p_over, prop.extrapolated)}.</p>
      <DistBar player={player} prop={prop} actual={actual} />
      <div class="kv num">
        <span>
          Our model: <b>{pctBounded(prop.model_p_over, prop.extrapolated)}</b> to go over
        </span>
        {prop.market_p_over !== null ? (
          <span>
            {prop.price_book ? bookName(prop.price_book) : "Sportsbook"}: <b>{pct(prop.market_p_over)}</b>
          </span>
        ) : null}
        {prop.model_p_push > 0.005 ? <span>Exactly on the line: {pct(prop.model_p_push)}</span> : null}
      </div>
      <div class="kv num">
        <span>
          Typical night: <b>{num(q[9] ?? 0)}</b>
        </span>
        <span>
          Half of nights: <b>{num(q[4] ?? 0)}</b> to <b>{num(q[14] ?? 0)}</b>
        </span>
        <span>
          Nine in ten: <b>{num(q[0] ?? 0)}</b> to <b>{num(q[18] ?? 0)}</b>
        </span>
      </div>
      <p class="small muted" data-model-source={prop.model_source}>
        {prop.model_label}
        {prop.p_play !== null && prop.p_play_label ? ` ${pct(prop.p_play)} chance to play. ${prop.p_play_label}` : ""}
      </p>
      <AltLine gameId={props.gameId} playerId={props.playerId} player={player} prop={prop} />
      {actual !== undefined ? (
        <p class="result-line">
          <span class="label">Final</span> {player} {didText(prop.stat, actual)}:{" "}
          <b>{actual === prop.line ? "right on the line, a push" : actual > prop.line ? `over the ${lineText(prop.line)} line` : `under the ${lineText(prop.line)} line`}</b>
        </p>
      ) : null}
    </div>
  );
}

function PlayerRow(props: { gameId: string; p: PlayerView; result: GameResult | null }) {
  const first = STATS.map((s) => props.p.props[s]).find((x) => x !== undefined);
  const res = props.result?.players.find((r) => r.player_id === props.p.player_id);
  const lead = props.p.props.pts ?? first;
  return (
    <details class="player">
      <summary>
        <span class="player-sum">
          <span class="player-name">
            {props.p.name}
            {res?.starter ? <span class="tag">Starter</span> : null}
            {props.p.role ? (
              <span class="player-role">
                <SteadyBadge s={props.p.role.steadiness} />
              </span>
            ) : null}
          </span>
          {lead ? (
            <span class="player-lead num">
              <span class="muted small nowrap">
                Over {lineText(lead.line)} {STAT_SHORT[lead.stat]}
              </span>
              <span class="big-num">{pctBounded(lead.model_p_over, lead.extrapolated)}</span>
            </span>
          ) : null}
        </span>
      </summary>
      {res ? (
        <p class="small muted">
          Played {res.minutes === null ? "no recorded minutes" : `${num(res.minutes)} minutes`}
          {res.starter === null ? "" : res.starter ? " and started" : " off the bench"}. We do not forecast minutes yet.
        </p>
      ) : (
        <p class="small muted">We do not forecast minutes yet. Show the final score above to see how long he played.</p>
      )}
      {props.p.role ? <RoleSection role={props.p.role} player={props.p.name} /> : null}
      {STATS.map((s: Stat) => {
        const prop = props.p.props[s];
        return prop ? <PropBlock key={s} gameId={props.gameId} playerId={props.p.player_id} player={props.p.name} prop={prop} actual={res?.actual[s]} /> : null;
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
  const modelHome = g.win_probability.model_home;
  const winner = res ? (res.home_won ? g.home : g.away) : null;
  return (
    <div class="stack">
      <p class="small">
        <a href="#/">Back to the night</a>
      </p>
      <section class="panel scoreboard">
        <div class="score-row">
          <div class="score-team">
            <TeamTag abbr={g.away.abbr} big />
            <span class="muted small">
              {g.away.city} {g.away.name}
            </span>
          </div>
          <div class="score-mid num">
            {res ? (
              <span class="final">
                {res.away_pts}
                <span class="dash">-</span>
                {res.home_pts}
              </span>
            ) : (
              <span class="at">at</span>
            )}
          </div>
          <div class="score-team right">
            <TeamTag abbr={g.home.abbr} big />
            <span class="muted small">
              {g.home.city} {g.home.name}
            </span>
          </div>
        </div>
        <p class="muted small center num">
          {res ? "Final" : `Tip-off ${tipTime(g.tip_utc)}`}
          {g.arena ? ` · ${g.arena}` : ""}
        </p>
        <WinBars wp={g.win_probability} home={g.home} away={g.away} />
        <WinLegend />
        <Explain label="Where did this forecast come from?">
          <p>
            Forecast stamp: <span class="mono">{g.win_probability.model_stamp ?? "none"}</span>. In plain terms, the model was
            built without ever seeing this game or any later one.
          </p>
        </Explain>
      </section>

      {g.final ? (
      <section class="panel">
        <div class="row between wrap">
          <div>
            <h2>How it ended</h2>
            <p class="small muted" style={{ margin: 0 }}>
              Spoilers are hidden so you can read the forecast first.
            </p>
          </div>
          <button type="button" class="btn" aria-pressed={show} onClick={() => { setShow(!show); }}>
            {show ? "Hide the score" : "Show the final score"}
          </button>
        </div>
        {show && result.status === "loading" ? <p class="muted">Loading...</p> : null}
        {show && result.status === "error" ? (
          <p role="alert">
            Could not load the score. <button type="button" class="btn" onClick={reloadResult}>Try again</button>
          </p>
        ) : null}
        {res && winner ? (
          <p class="num result-line">
            <b>
              {g.away.name} {res.away_pts}, {g.home.name} {res.home_pts}.
            </b>{" "}
            The {winner.name} won.
            {modelHome !== null
              ? ` Going in, our model gave them ${pct(res.home_won ? modelHome : 1 - modelHome)}.`
              : ""}
          </p>
        ) : null}
      </section>
      ) : null}

      <section class="panel">
        <div class="row between wrap">
          <h2>Player lines</h2>
          <div class="tabs" role="tablist" aria-label="Team">
            {(["away", "home"] as const).map((s) => (
              <button
                key={s}
                type="button"
                role="tab"
                aria-selected={side === s}
                class="tab"
                aria-current={side === s ? "true" : undefined}
                onClick={() => { setSide(s); }}
              >
                {(s === "home" ? g.home : g.away).abbr}
              </button>
            ))}
          </div>
        </div>
        <p class="small muted">
          {team.city} {team.name}: {players.length} players with {g.players.some((p) => p.props.pts?.line_source === "reference") ? "a forecast" : "a line from the sportsbook"}, highest forecast scorer first.
          Tap a name to open them up.
        </p>
        <DistLegend />
        <div class="players">
          {players.length === 0 ? (
            <p class="muted">No forecasts are stored for this team.</p>
          ) : (
            players.map((p) => <PlayerRow key={p.player_id} gameId={g.game_id} p={p} result={res} />)
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
