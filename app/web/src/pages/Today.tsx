import { api } from "../api";
import { Explain, TeamTag, View, WinBars, WinLegend } from "../components";
import { lineText, longDate, pct, pctBounded, signed, tipTime } from "../format";
import { useLoad } from "../useLoad";
import type { Evidence, GameSummary } from "../types";
import { STAT_LABEL } from "../types";
import { doesText, inTen } from "../words";
import { ParlayCard } from "./Parlay";

function sentenceCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function KeyProps({ game }: { game: GameSummary }) {
  if (game.key_props.length === 0) return null;
  return (
    <div class="props">
      <div class="label">Top scorers, by our forecast</div>
      {game.key_props.map((p) => (
        <div class="prop-line" key={p.player_id}>
          <div class="prop-main">
            <div class="prop-name">
              {p.name} <span class="muted">{p.team}</span>
            </div>
            <div class="prop-say">
              {sentenceCase(doesText(p.stat, p.line, "over"))} {inTen(p.model_p_over, p.extrapolated)}
            </div>
            <div class="muted small">
              {STAT_LABEL[p.stat]} line {lineText(p.line)}
            </div>
          </div>
          <div class="prop-nums num">
            <span class="big-num">{pctBounded(p.model_p_over, p.extrapolated)}</span>
            <span class="muted small">book {pct(p.market_p_over)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function GameRow({ g }: { g: GameSummary }) {
  return (
    <a class="game" href={`#/game/${g.game_id}`} aria-label={`${g.away.city} ${g.away.name} at ${g.home.city} ${g.home.name}, open game`}>
      <div class="game-top">
        <div class="matchup">
          <TeamTag abbr={g.away.abbr} big />
          <span class="at">at</span>
          <TeamTag abbr={g.home.abbr} big />
        </div>
        <span class="tip num">{tipTime(g.tip_utc)}</span>
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
              <ParlayCard compact />
              <div class="page-head">
                <h1>{date ? longDate(date) : "Slate"}</h1>
                <nav class="tabs" aria-label="Choose a night">
                  {slates.map((s) => (
                    <a key={s.date} class="tab" href={`#/?date=${s.date}`} aria-current={s.date === date ? "true" : undefined}>
                      {longDate(s.date).replace(/, \d{4}$/, "")}
                    </a>
                  ))}
                </nav>
              </div>
              {date ? <SlateBody date={date} onStale={props.onStale} /> : <p>No nights are loaded.</p>}
            </>
          );
        }}
      </View>
    </div>
  );
}

function HowGood({ ev }: { ev: Evidence }) {
  const g = ev.games_t60;
  const bookAhead = g.ci_lo > 0;
  const modelAhead = g.ci_hi < 0;
  return (
    <>
      <p class="small">
        <strong>How good are we?</strong>{" "}
        {bookAhead
          ? `Over all ${g.n.toLocaleString()} games in the ${ev.seasonLabel} season, the sportsbook's odds were closer to what happened than our model's. The gap was small, but it was there.`
          : modelAhead
            ? `Over all ${g.n.toLocaleString()} games in the ${ev.seasonLabel} season, our model was closer to what happened than the sportsbook's odds.`
            : `Over all ${g.n.toLocaleString()} games in the ${ev.seasonLabel} season, we could not separate our model from the sportsbook's odds.`}{" "}
        <a href="#/method">More on how it works</a>.
      </p>
      <Explain>
        <p>
          We score each forecast by how surprised it was by the real result (the score is called log loss, and lower is
          better). Averaged over {g.n.toLocaleString()} games: our model {g.log_loss_model}, the sportsbook {g.log_loss_market}.
        </p>
        <p>
          The model minus the market is {signed(g.delta)}, and the 95% range for that gap is {signed(g.ci_lo)} to{" "}
          {signed(g.ci_hi)}. A positive number means the market did better. When the range stays above zero, the gap is
          unlikely to be luck.
        </p>
      </Explain>
    </>
  );
}

function SlateBody({ date, onStale }: { date: string; onStale: (s: boolean) => void }) {
  const [slate, reload] = useLoad(() => api.slate(date), [date]);
  const [ev] = useLoad(() => api.evidence(), []);
  return (
    <View state={slate} reload={reload} onStale={onStale}>
      {(s) => (
        <>
          <section class="panel flat">
            <p class="small">
              <strong>
                {s.counts.games} games, {s.counts.props} player lines.
              </strong>{" "}
              This is a replay of a real night from the 2024-25 season, not tonight's slate. Every forecast was made before
              tip, using only what was known then.
            </p>
            {ev.status === "ready" ? <HowGood ev={ev.data} /> : null}
          </section>
          <div class="games">
            {s.games.map((g) => (
              <GameRow key={g.game_id} g={g} />
            ))}
          </div>
          <WinLegend />
        </>
      )}
    </View>
  );
}
