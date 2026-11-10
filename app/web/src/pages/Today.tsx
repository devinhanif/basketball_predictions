import { useEffect } from "preact/hooks";
import { api, warmGames } from "../api";
import { CalTag, ModeToggle, ModelNote, TeamTag, View, WinBars, WinLegend } from "../components";
import { lineText, longDate, num, pct, pctBounded, tipTime } from "../format";
import { headline, learnHeadline } from "../headline";
import { LENS_LABEL, LensSwitch } from "../Lens";
import { liveHref } from "../router";
import { clockLabel, type LiveGameSummary } from "../live";
import { useLiveList } from "../useLiveData";
import { chronological, defaultNight, engineSpeaksFor } from "../nights";
import { useLens, useMode, type Mode } from "../prefs";
import { useLoad } from "../useLoad";
import type { GameSummary, ParlayState, Slate } from "../types";
import { STAT_LABEL, STAT_SHORT } from "../types";
import { VerdictStrip } from "../VerdictStrip";
import { pickHero } from "../discover";
import { Court, DeskTiles, Discipline, Hero, Insight, Radar, useRadar } from "./DiscoverParts";
import { compareLine, doesText, rangeSentence } from "../words";
import { hasTickets, type TpResult } from "../topParlays";
import { TodaysPicks } from "../TodaysPicks";

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
                  <CalTag source={p.model_p_source} />
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
                  over {lineText(p.line)} {STAT_SHORT[p.stat]} <b>{pctBounded(p.model_p_over, p.extrapolated)}</b> <CalTag source={p.model_p_source} />
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

/** The live state of one game, when the live feed has it: a word and the clock, never a score (the score would sit next to chances). */
function StateChip({ live }: { live: LiveGameSummary | null }) {
  if (live === null || live.state === "pre") return null;
  return (
    <span class={`game-state ${live.state}`}>{live.state === "in" ? `Live ${clockLabel(live.status, live.state)}` : "Final"}</span>
  );
}

/** Live made findable from Discover: one tap from tonight's games to the live view. */
function LiveStrip({ date, games }: { date: string; games: LiveGameSummary[] }) {
  if (games.length === 0) return null;
  const inPlay = games.filter((g) => g.state === "in");
  const pre = games.filter((g) => g.state === "pre").length;
  const post = games.filter((g) => g.state === "post").length;
  const only = inPlay.length === 1 ? inPlay[0] : undefined;
  const href = only ? liveHref(only.game_id, date) : liveHref(null, date);
  const rest = [post > 0 ? `${post.toString()} final` : "", pre > 0 ? `${pre.toString()} not started` : ""].filter(Boolean).join(", ");
  return (
    <a class={`live-strip${inPlay.length === 0 ? " quiet" : ""}`} href={href}>
      <span class="ls-text">
        <strong>
          <i class="live-dot" aria-hidden="true" />
          {inPlay.length > 0
            ? `${inPlay.length.toString()} ${inPlay.length === 1 ? "game" : "games"} live now`
            : post > 0 && pre === 0
              ? "Tonight's games are over"
              : "Live scores and play by play"}
        </strong>
        <span>{rest !== "" ? `${rest}. ` : ""}Plays and player stats.</span>
      </span>
      <span class="btn primary">{inPlay.length > 0 ? "Watch live" : "Plays"}</span>
    </a>
  );
}

function GameRow({ g, mode, market, live }: { g: GameSummary; mode: Mode; market: boolean; live: LiveGameSummary | null }) {
  return (
    <a class="game" href={`#/game/${g.game_id}`} aria-label={`${g.away.city} ${g.away.name} at ${g.home.city} ${g.home.name}, open game`}>
      <div class="game-top">
        <div class="matchup">
          <TeamTag abbr={g.away.abbr} big />
          <span class="at">at</span>
          <TeamTag abbr={g.home.abbr} big />
        </div>
        <span class="game-side">
          <span class="tip num">{tipTime(g.tip_utc)}</span>
          <StateChip live={live} />
        </span>
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
      <View state={list} reload={reloadList} onStale={props.onStale} what="the list of nights">
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
  const liveSt = useLiveList([date], 20000);
  const liveGames = liveSt.lists[0]?.games ?? [];
  const market = lens === "market";
  const liveCounts = { live: liveGames.filter((g) => g.state === "in").length, final: liveGames.filter((g) => g.state === "post").length };
  // Without the Market lens the card never claims "no parlay": it says only that a check exists, or that parlays live there.
  const engineHasPick = speaks && engine.headline_verdict !== "no_positive_ev_found";
  const checkExists = engineHasPick || (tickets !== null && tickets.kind === "ok" && hasTickets(tickets.data));
  const learnStrip = checkExists
    ? "A parlay check exists in the Market lens. Keep your money."
    : "Parlays live in the Market lens. Keep your money.";
  const head =
    slate.status === "ready" && (!market || (eng.status !== "loading" && tp.status !== "loading"))
      ? market
        ? headline({ games: slate.data.counts.games, engine, speaks, tickets, live: liveCounts })
        : learnHeadline(slate.data.counts.games, liveCounts)
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
      </header>
      <TodaysPicks date={date} engine={engine} onStale={props.onStale} />
      <LiveStrip date={date} games={liveGames} />
      <nav class="tabs" aria-label="Choose a night">
        {props.nights.map((d) => (
          <a key={d} class="tab" href={`#/?date=${d}`} aria-current={d === date ? "true" : undefined}>
            {longDate(d).replace(/, \d{4}$/, "")}
          </a>
        ))}
      </nav>
      <details class="view-opts">
        <summary>
          View: {LENS_LABEL[lens]} lens, {mode === "analyst" ? "Analyst" : "Simple"} detail (change)
        </summary>
        <LensSwitch />
        <div class="row wrap between">
          <span class="small muted">Detail</span>
          <ModeToggle />
        </div>
      </details>
      {market ? (
        <VerdictStrip date={date} eng={eng} tp={tp} reloadEng={reloadEng} reloadTp={reloadTp} />
      ) : null}
      {market ? (
        <div class="two-col">
          <Hero row={pickHero(radar.rows)} nPriced={radar.nPriced} fixture={radar.desk?.fixture != null} desk={radar.desk} />
          <Court game={featured} final_={played} market />
        </div>
      ) : null}
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
      {market ? null : <p class="keep-line small">{learnStrip}</p>}
      <View state={slate} reload={reloadSlate} onStale={props.onStale} what="tonight's games">
        {(s) => <SlateBody s={s} engine={engine} speaks={speaks} mode={mode} market={market} liveGames={liveGames} />}
      </View>
      <p class="small muted more-leagues">NBA only for now. Other leagues are not covered yet.</p>
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

function SlateBody(props: { s: Slate; engine: ParlayState | null; speaks: boolean; mode: Mode; market: boolean; liveGames: LiveGameSummary[] }) {
  const { s, engine, speaks, mode, market } = props;
  const liveOf = (g: GameSummary): LiveGameSummary | null => props.liveGames.find((l) => l.home.abbr === g.home.abbr && l.away.abbr === g.away.abbr) ?? null;
  const gamesList = (
    <div class="games">
      {s.games.map((g) => (
        <GameRow key={g.game_id} g={g} mode={mode} market={market} live={liveOf(g)} />
      ))}
    </div>
  );
  const gateClosed = engine?.gate != null ? !engine.gate.gate_open : null;
  const priced = market && speaks && engine ? engine.counts["n_contracts"] : undefined;
  const flagged = market && speaks && engine ? engine.counts["n_flagged_positive"] : undefined;
  return (
    <>
      {market ? null : (
        <>
          {gamesList}
          <WinLegend showBook={false} />
        </>
      )}
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
          <p class="small muted">Our model has no track record yet, so nothing on this page is a recommendation, and the picks above are a closest-look list. Keep your money.</p>
        ) : null}
        <ModelNote items={s.games.flatMap((g) => g.key_props.map((p) => ({ source: p.model_source, label: p.model_label })))} />
      </section>
      {market ? (
        <>
          {gamesList}
          <WinLegend showBook />
        </>
      ) : null}
      <WarmCache ids={s.games.map((g) => g.game_id)} />
    </>
  );
}
