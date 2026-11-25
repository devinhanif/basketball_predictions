// TEAMS: who wins tonight. The night's games with the win chances the Discover list already shows, and a way into each
// game's props and into Explore. No new data: the same slate the home page reads.
import { useEffect, useRef } from "preact/hooks";
import { api } from "../api";
import { View, WinLegend } from "../components";
import { Door, DoorGroup } from "../Doors";
import { longDate } from "../format";
import { chronological, defaultNight } from "../nights";
import { useLens, useMode } from "../prefs";
import { propsHref, topHref } from "../router";
import { useLiveList } from "../useLiveData";
import { useLoad } from "../useLoad";
import { PageHead, StateBlock } from "../ui";
import type { GameSummary } from "../types";
import { GameRow, LiveStrip } from "./Today";

/** The night chooser shared by the Teams and Props screens: `base` is the screen's own hash. */
export function JobNights(props: { dates: string[]; date: string; base: string }) {
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = nav.current;
    const on = el?.querySelector<HTMLElement>('[aria-current="true"]');
    if (el && on) el.scrollLeft = Math.max(0, on.offsetLeft - el.offsetLeft - el.clientWidth + on.offsetWidth + 8);
  }, [props.date]);
  if (props.dates.length < 2) return null;
  return (
    <nav class="tabs" aria-label="Choose a night" ref={nav}>
      {props.dates.map((d) => (
        <a key={d} class="tab" href={`${props.base}?date=${d}`} aria-current={d === props.date ? "true" : undefined}>
          {longDate(d).replace(/, \d{4}$/, "")}
        </a>
      ))}
    </nav>
  );
}

function TeamsNight(props: { date: string; nights: string[]; onStale: (s: boolean) => void }) {
  const { date } = props;
  const mode = useMode();
  const lens = useLens();
  const market = lens === "market";
  const [slate, reload] = useLoad(() => api.slate(date), [date]);
  const liveSt = useLiveList([date], 20000);
  const liveGames = liveSt.lists[0]?.games ?? [];
  const liveOf = (g: GameSummary) => liveGames.find((l) => l.home.abbr === g.home.abbr && l.away.abbr === g.away.abbr) ?? null;
  return (
    <>
      <PageHead
        kicker={`${longDate(date)} · NBA`}
        title="Team picks"
        description="Who wins, and by how much. Tap a game for its players, minutes and lines."
      />
      <JobNights dates={props.nights} date={date} base="#/teams" />
      <LiveStrip date={date} games={liveGames} />
      <View state={slate} reload={reload} onStale={props.onStale} what="tonight's games" errorLevel={2}>
        {(s) =>
          s.games.length === 0 ? (
            <StateBlock kind="empty" title="No games this night">
              The data holds no games for {longDate(date)}. Nothing is shown in their place.
            </StateBlock>
          ) : (
            <>
              <div class="games">
                {s.games.map((g) => (
                  <div class="teams-game" key={g.game_id}>
                    <GameRow g={g} mode={mode} market={market} learn={!market} live={liveOf(g)} />
                    <DoorGroup label={`Go deeper on ${g.away.abbr} at ${g.home.abbr}`} class="inline">
                      <Door to="props" href={propsHref(g.game_id)}>
                        {g.key_props.length > 0 ? `${g.key_props.length.toString()} ${g.key_props.length === 1 ? "line" : "lines"} in this game` : "player lines in this game"}
                      </Door>
                      <Door to="explore" href={topHref("combo")}>
                        if the game goes a certain way
                      </Door>
                    </DoorGroup>
                  </div>
                ))}
              </div>
              <WinLegend showBook={market} />
            </>
          )
        }
      </View>
    </>
  );
}

export function Teams(props: { date: string | null; onStale: (s: boolean) => void }) {
  const [list, reload] = useLoad(() => api.slates(), []);
  return (
    <div class="stack">
      <View state={list} reload={reload} onStale={props.onStale} what="the list of nights">
        {(raw) => {
          const slates = chronological(raw);
          const date = props.date ?? defaultNight(slates);
          return date ? (
            <TeamsNight date={date} nights={slates.map((s) => s.date)} onStale={props.onStale} />
          ) : (
            <>
              <PageHead title="Team picks" description="Who wins, and by how much." />
              <StateBlock kind="empty" title="No nights are loaded">
                This copy of the data holds no slates, so there is nothing to show yet. Nothing is broken; check back after the nightly run.
              </StateBlock>
            </>
          );
        }}
      </View>
    </div>
  );
}
