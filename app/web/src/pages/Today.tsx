import { useEffect } from "preact/hooks";
import { api, warmGames } from "../api";
import { ModeToggle, ModelNote, TeamTag, View, WinBars, WinLegend } from "../components";
import { lineText, longDate, num, pct, pctBounded, tipTime } from "../format";
import { headline, learnHeadline } from "../headline";
import { LensSwitch } from "../Lens";
import { chronological, defaultNight, engineSpeaksFor } from "../nights";
import { useLens, useMode, type Mode } from "../prefs";
import { useLoad } from "../useLoad";
import type { GameSummary, ParlayState, Slate } from "../types";
import { STAT_LABEL, STAT_SHORT } from "../types";
import { VerdictStrip } from "../VerdictStrip";
import { pickHero } from "../discover";
import { Court, DeskTiles, Discipline, Hero, Insight, LeagueTabs, Radar, useRadar } from "./DiscoverParts";
import { compareLine, doesText, rangeSentence } from "../words";
import type { TpResult } from "../topParlays";

/** Analyst: every key prop with its sentence, the book's number and a plain comparison. */
function KeyPropsFull({ game, market }: { game: GameSummary; market: boolean }) {
  return (
    <div class="props">
      <div class="label">Top scorers, by our forecast</div>
      {game.key_props.map((p) => {
        const reference = p.line_source === "reference";
        const compare = reference || !market ? null : compareLine(p.model_p_over, p.market_p_over);
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

/** Simple: one line per player. */
function KeyPropsOneLine({ game, market }: { game: GameSummary; market: boolean }) {
  if (game.key_props.length === 0) return null;
  return (
    <ul class="props one-line" aria-label="Top scorers, by our forecast">
      {game.key_props.map((p) => {
        const reference = p.line_source === "reference";
        return (
          <li key={p.player_id}>
            <span class="ol-name">
              {p.name} <span class="muted">{p.team}</span>
            </span>
            <span class="ol-say num">
              {reference ? (
                <>
                  typical {num(p.q50)} {STAT_SHORT[p.stat]}
                </>
              ) : (
                <>
                  over {lineText(p.line)} {STAT_SHORT[p.stat]} <b>{pctBounded(p.model_p_over, p.extrapolated)}</b>
                  {market && p.market_p_over !== null ? <span class="muted"> (book {pct(p.market_p_over)})</span> : null}
                </>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function GameRow({ g, mode, market }: { g: GameSummary; mode: Mode; market: boolean }) {
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
      <WinBars wp={g.win_probability} home={g.home} away={g.away} showBook={market} />
      {mode === "analyst" ? <KeyPropsFull game={g} market={market} /> : <KeyPropsOneLine game={g} market={market} />}
    </a>
  );
}

function Tile(props: { value: string; label: string; note?: string }) {
  return (
    <div class="tile">
      <div class="tile-v num">{props.value}</div>
      <div class="tile-l">{props.label}</div>
      {props.note ? <div class="tile-n muted">{props.note}</div> : null}
    </div>
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
          return date ? <Night date={date} nights={slates.map((s) => s.date)} onStale={props.onStale} /> : <p>No nights are loaded.</p>;
        }}
      </View>
    </div>
  );
}

function Night(props: { date: string; nights: string[]; onStale: (s: boolean) => void }) {
  const { date } = props;
  const mode = useMode();
  const [slate, reloadSlate] = useLoad(() => api.slate(date), [date]);
  const [eng, reloadEng] = useLoad(() => api.parlay(), []);
  const [tp, reloadTp] = useLoad(() => api.topParlays(date), [date]);
  const engine: ParlayState | null = eng.status === "ready" ? eng.data : null;
  const tickets: TpResult | null = tp.status === "ready" ? tp.data : null;
  const speaks = engine !== null && engineSpeaksFor(date, engine.report_date);
  const radar = useRadar(date, engine);
  const featured =
    slate.status === "ready" ? (slate.data.games.find((g) => g.game_id === pickHero(radar.rows)?.gameId) ?? slate.data.games[0] ?? null) : null;
  const played = featured !== null && Date.parse(featured.tip_utc) < Date.now() - 3.5 * 3600 * 1000;
  const lens = useLens();
  const market = lens === "market";
  const head =
    slate.status === "ready" && (!market || (eng.status !== "loading" && tp.status !== "loading"))
      ? market
        ? headline({ games: slate.data.counts.games, engine, speaks, tickets })
        : learnHeadline(slate.data.counts.games)
      : null;
  return (
    <>
      <header class="page-head today-head">
        <div class="kicker">
          <span class="tonight-dot" aria-hidden="true" />
          {longDate(date)} · NBA
        </div>
        {head ? (
          <>
            <h1 class={`answer ${head.verdict}`}>{head.text}</h1>
            <p class="muted answer-src">{head.source}</p>
          </>
        ) : (
          <h1 class="answer pending">Reading the night&hellip;</h1>
        )}
        <LeagueTabs />
        <LensSwitch compact />
        <div class="head-tools">
          <nav class="tabs" aria-label="Choose a night">
            {props.nights.map((d) => (
              <a key={d} class="tab" href={`#/?date=${d}`} aria-current={d === date ? "true" : undefined}>
                {longDate(d).replace(/, \d{4}$/, "")}
              </a>
            ))}
          </nav>
          <ModeToggle />
        </div>
      </header>
      {market ? (
        <VerdictStrip date={date} eng={eng} tp={tp} reloadEng={reloadEng} reloadTp={reloadTp} />
      ) : (
        <section class="panel verdict strip keep" aria-labelledby="strip-h">
          <div class="kicker">Parlays for {longDate(date)}</div>
          <h2 id="strip-h">No parlay tonight. Keep your money.</h2>
          <p class="small muted">Our model has no live track record yet. How the checks work, with prices and fees, is in the Market lens.</p>
        </section>
      )}
      {market ? (
        <div class="two-col">
          <Hero row={pickHero(radar.rows)} nPriced={radar.nPriced} fixture={radar.desk?.fixture != null} desk={radar.desk} />
          <Court game={featured} final_={played} market />
        </div>
      ) : (
        <Court game={featured} final_={played} market={false} />
      )}
      {market && radar.desk ? <DeskTiles desk={radar.desk} date={date} /> : null}
      {market ? (
        <div class="two-col wide-left">
          <Radar rows={radar.rows} loading={radar.loading} />
          <div class="stack side-col">
            <Insight desk={radar.desk} />
            <Discipline desk={radar.desk} engine={engine} />
          </div>
        </div>
      ) : null}
      <View state={slate} reload={reloadSlate} onStale={props.onStale}>
        {(s) => <SlateBody s={s} engine={engine} speaks={speaks} mode={mode} market={market} />}
      </View>
    </>
  );
}

function WarmCache({ ids }: { ids: string[] }) {
  const key = ids.join(",");
  useEffect(() => {
    void warmGames(ids);
  }, [key]);
  return null;
}

function SlateBody(props: { s: Slate; engine: ParlayState | null; speaks: boolean; mode: Mode; market: boolean }) {
  const { s, engine, speaks, mode, market } = props;
  const gateClosed = engine?.gate != null ? !engine.gate.gate_open : null;
  const priced = market && speaks && engine ? engine.counts["n_contracts"] : undefined;
  const flagged = market && speaks && engine ? engine.counts["n_flagged_positive"] : undefined;
  return (
    <>
      <section class="tiles" aria-label="The night in numbers">
        <Tile value={s.counts.games.toLocaleString("en-US")} label={s.counts.games === 1 ? "game" : "games"} />
        <Tile value={s.counts.props.toLocaleString("en-US")} label={s.mode === "fixture_replay" ? "player lines" : "player forecasts"} />
        {priced !== undefined ? <Tile value={priced.toLocaleString("en-US")} label="contracts priced" note="at the ask, with fees" /> : null}
        {flagged !== undefined ? <Tile value={flagged.toLocaleString("en-US")} label="clear an edge after fees" /> : null}
      </section>
      <section class="panel flat note-panel">
        <p class="small">
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
          <GameRow key={g.game_id} g={g} mode={mode} market={market} />
        ))}
      </div>
      <WinLegend showBook={market} />
      <WarmCache ids={s.games.map((g) => g.game_id)} />
    </>
  );
}
