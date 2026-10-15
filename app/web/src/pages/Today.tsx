import { api } from "../api";
import { TeamTag, View, WinBars, WinLegend } from "../components";
import { lineText, longDate, num, pct, pctBounded, tipTime } from "../format";
import { chronological, defaultNight } from "../nights";
import { useLoad } from "../useLoad";
import type { GameSummary } from "../types";
import { STAT_LABEL } from "../types";
import { doesText, inTen, rangeSentence } from "../words";
import { TopParlaysPanel } from "../TopParlaysPanel";
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
              {p.line_source === "reference"
                ? `${rangeSentence(p.stat, num(p.q50), num(p.q25), num(p.q75))}.`
                : `${sentenceCase(doesText(p.stat, p.line, "over"))} ${inTen(p.model_p_over, p.extrapolated)}`}
            </div>
            <div class="muted small">
              {STAT_LABEL[p.stat]} {p.line_source === "reference" ? "reference line" : "line"} {lineText(p.line)}
            </div>
            <div class="muted small">{p.model_label}</div>
          </div>
          <div class="prop-nums num">
            {p.line_source === "reference" ? (
              <>
                <span class="big-num">{num(p.q50)}</span>
                <span class="muted small">typical night</span>
              </>
            ) : (
              <>
                <span class="big-num">{pctBounded(p.model_p_over, p.extrapolated)}</span>
                {p.market_p_over !== null ? <span class="muted small">book {pct(p.market_p_over)}</span> : null}
              </>
            )}
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
        {(list) => {
          const slates = chronological(list);
          const date = props.date ?? defaultNight(slates);
          return (
            <>
              <ParlayCard compact />
              {date ? <TopParlaysPanel date={date} /> : null}
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

function SlateBody({ date, onStale }: { date: string; onStale: (s: boolean) => void }) {
  const [slate, reload] = useLoad(() => api.slate(date), [date]);
  return (
    <View state={slate} reload={reload} onStale={onStale}>
      {(s) => (
        <>
          <section class="panel flat">
            <p class="small">
              <strong>
                {s.counts.games} games, {s.counts.props} player {s.mode === "fixture_replay" ? "lines" : "forecasts"}.
              </strong>{" "}
              {s.mode === "fixture_replay"
                ? "This is a replay of a real night from the 2024-25 season, not tonight's slate. Every forecast was made before tip, using only what was known then."
                : "Every forecast is the last one stored before tip. A prop's line is the one most books carried; where none had a usable quote, it shows a reference line at the middle of our own forecast."}
            </p>
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
