import { api } from "../api";
import { View, WinBars, WinLegend } from "../components";
import { american, lineText, longDate, pct, pctBounded, signed, tipTime } from "../format";
import { useLoad } from "../useLoad";
import type { GameSummary } from "../types";
import { STAT_SHORT } from "../types";
import { ParlayCard } from "./Parlay";

function KeyProps({ game }: { game: GameSummary }) {
  if (game.key_props.length === 0) return null;
  return (
    <div class="props">
      <div class="small muted">
        Top scorers by forecast. Chance of going over the book's line: <span class="tag forecast">model</span>{" "}
        <span class="tag market">book</span>
      </div>
      {game.key_props.map((p) => (
        <div class="prop-line" key={p.player_id}>
          <span>
            <strong>{p.name}</strong> <span class="muted">{p.team}</span>
            <br />
            <span class="muted small num">
              {STAT_SHORT[p.stat]} over {lineText(p.line)} ({american(p.over_price)} / {american(p.under_price)})
            </span>
          </span>
          <span class="num" style={{ textAlign: "right" }}>
            <span style={{ color: "var(--model)", fontWeight: 700 }}>{pctBounded(p.model_p_over, p.extrapolated)}</span>
            {" / "}
            <span style={{ color: "var(--market)", fontWeight: 700 }}>{pct(p.market_p_over)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

function GameCard({ g }: { g: GameSummary }) {
  return (
    <a class="card game-card" href={`#/game/${g.game_id}`} aria-label={`${g.away.name} at ${g.home.name}, open game`}>
      <div class="teams">
        <span class="t">
          {g.away.abbr} <span class="at">at</span> {g.home.abbr}
        </span>
        <span class="muted small num">{tipTime(g.tip_utc)}</span>
      </div>
      <div class="muted small">
        {g.away.city} {g.away.name} at {g.home.city} {g.home.name}
      </div>
      <WinBars wp={g.win_probability} home={g.home} away={g.away} />
      <KeyProps game={g} />
    </a>
  );
}

export function Today(props: { date: string | null; onStale: (s: boolean) => void }) {
  const [list, reloadList] = useLoad(() => api.slates(), []);
  return (
    <div class="stack">
      <View state={list} reload={reloadList} onStale={props.onStale}>
        {(slates) => {
          const date = props.date ?? slates[0]?.date ?? null;
          return (
            <>
              <div class="row between wrap">
                <h1>Today's slate</h1>
                <nav class="chips" aria-label="Choose a slate">
                  {slates.map((s) => (
                    <a key={s.date} class="chip" href={`#/?date=${s.date}`} aria-current={s.date === date ? "true" : undefined}>
                      {longDate(s.date)}
                    </a>
                  ))}
                </nav>
              </div>
              <ParlayCard compact />
              {date ? <SlateBody date={date} onStale={props.onStale} /> : <p>No slates in the fixture.</p>}
            </>
          );
        }}
      </View>
    </div>
  );
}

function SlateBody({ date, onStale }: { date: string; onStale: (s: boolean) => void }) {
  const [slate, reload] = useLoad(() => api.slate(date), [date]);
  const [ev] = useLoad(() => api.evidence(), []);
  return (
    <View state={slate} reload={reload} onStale={onStale}>
      {(s) => (
        <>
          <div class="card flat">
            <p class="small" style={{ margin: 0 }}>
              <strong>{longDate(s.date)}</strong>: {s.counts.games} games, {s.counts.props} posted props. This is a{" "}
              <strong>historical replay</strong> of a 2024-25 slate. Every forecast was made out-of-fold, before the game was
              known to the model, so none of it is hindsight.
              {ev.status === "ready" ? (
                <>
                  {" "}
                  How good is it? On all {ev.data.games_t60.n.toLocaleString()} 2024-25 games the model's log loss was{" "}
                  {ev.data.games_t60.log_loss_model} against the market's {ev.data.games_t60.log_loss_market} (difference{" "}
                  {signed(ev.data.games_t60.delta)}, 95% CI {signed(ev.data.games_t60.ci_lo)} to {signed(ev.data.games_t60.ci_hi)}
                  ): the market was better. <a href="#/method">Details</a>.
                </>
              ) : null}
            </p>
          </div>
          <div class="grid two">
            {s.games.map((g) => (
              <GameCard key={g.game_id} g={g} />
            ))}
          </div>
          <WinLegend />
        </>
      )}
    </View>
  );
}
