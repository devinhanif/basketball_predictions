import { useEffect } from "preact/hooks";
import { api, warmGames } from "../api";
import { ModelNote, TeamTag, View, WinBars, WinLegend } from "../components";
import { lineText, longDate, num, pct, pctBounded, tipTime } from "../format";
import { chronological, defaultNight } from "../nights";
import { useLoad } from "../useLoad";
import type { GameSummary } from "../types";
import { STAT_LABEL } from "../types";
import { VerdictStrip } from "../VerdictStrip";
import { compareLine, doesText, rangeSentence } from "../words";

function KeyProps({ game }: { game: GameSummary }) {
  if (game.key_props.length === 0) return null;
  return (
    <div class="props">
      <div class="label">Top scorers, by our forecast</div>
      {game.key_props.map((p) => {
        const reference = p.line_source === "reference";
        const compare = reference ? null : compareLine(p.model_p_over, p.market_p_over);
        return (
          <div class="prop-line" key={p.player_id}>
            <div class="prop-main">
              <div class="prop-name">
                {p.name} <span class="muted">{p.team}</span>
              </div>
              <div class="prop-say">
                {reference
                  ? `${rangeSentence(p.stat, num(p.q50), num(p.q25), num(p.q75))}.`
                  : `${STAT_LABEL[p.stat]} over ${lineText(p.line)}: ${doesText(p.stat, p.line, "over")}`}
              </div>
              {reference ? (
                <div class="muted small">
                  {STAT_LABEL[p.stat]} reference line {lineText(p.line)}
                </div>
              ) : null}
              {compare ? <div class="small prop-compare">{compare}</div> : null}
            </div>
            <div class="prop-nums num">
              {reference ? (
                <>
                  <span class="big-num">{num(p.q50)}</span>
                  <span class="muted small">typical night</span>
                </>
              ) : (
                <>
                  <span class="big-num">{pctBounded(p.model_p_over, p.extrapolated)}</span>
                  <span class="muted small">model</span>
                  {p.market_p_over !== null ? <span class="muted small">book {pct(p.market_p_over)}</span> : null}
                </>
              )}
            </div>
          </div>
        );
      })}
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
        {(raw) => {
          const slates = chronological(raw);
          const date = props.date ?? defaultNight(slates);
          return (
            <>
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
              {date ? <VerdictStrip date={date} /> : null}
              {date ? <SlateBody date={date} onStale={props.onStale} /> : <p>No nights are loaded.</p>}
            </>
          );
        }}
      </View>
    </div>
  );
}

/** Is the model still without a track record? Null when the engine's state cannot be read. */
function useGateClosed(): boolean | null {
  const [eng] = useLoad(() => api.parlay(), []);
  if (eng.status !== "ready" || eng.data.gate === null) return null;
  return !eng.data.gate.gate_open;
}

function WarmCache({ ids }: { ids: string[] }) {
  const key = ids.join(",");
  useEffect(() => {
    void warmGames(ids);
  }, [key]);
  return null;
}

function SlateBody({ date, onStale }: { date: string; onStale: (s: boolean) => void }) {
  const [slate, reload] = useLoad(() => api.slate(date), [date]);
  const gateClosed = useGateClosed();
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
            {gateClosed ? (
              <p class="small muted">Our model has no track record yet, so nothing on this page is a pick. Keep your money.</p>
            ) : null}
            <ModelNote items={s.games.flatMap((g) => g.key_props.map((p) => ({ source: p.model_source, label: p.model_label })))} />
          </section>
          <div class="games">
            {s.games.map((g) => (
              <GameRow key={g.game_id} g={g} />
            ))}
          </div>
          <WinLegend />
          <WarmCache ids={s.games.map((g) => g.game_id)} />
        </>
      )}
    </View>
  );
}
