import { useEffect, useState } from "preact/hooks";
import { Attribution, LiveGame } from "../LiveParts";
import { ageNow, ageText, clockLabel, etDate, previousDate, type LiveGameSummary } from "../live";
import { liveHref } from "../router";
import { tipTime } from "../format";
import { Loading } from "../components";
import { useAppGame, useLiveList } from "../useLiveData";

function GameCard({ g, date, receivedMs }: { g: LiveGameSummary; date: string; receivedMs: number }) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const t = window.setInterval(() => {
      setNow(performance.now());
    }, 1000);
    return () => {
      window.clearInterval(t);
    };
  }, []);
  const age = ageNow(g, receivedMs, now);
  return (
    <li>
      <a class="live-card" href={liveHref(g.game_id, date)} aria-label={`${g.away.name} at ${g.home.name}`}>
        <span class="lc-teams num">
          <span class="lc-row">
            <b>{g.away.abbr}</b>
            <span class="lc-pts">{g.state === "pre" ? "" : g.away.score}</span>
          </span>
          <span class="lc-row">
            <b>{g.home.abbr}</b>
            <span class="lc-pts">{g.state === "pre" ? "" : g.home.score}</span>
          </span>
        </span>
        <span class="lc-side">
          <span class={`ls-state ${g.state}`}>{g.state === "in" ? "LIVE" : g.state === "post" ? "FINAL" : "SOON"}</span>
          {g.state === "post" ? null : <span class="small num">{g.state === "pre" && g.tip_utc ? tipTime(g.tip_utc) : clockLabel(g.status, g.state)}</span>}
          {g.state === "in" ? <span class="small muted num">as of {ageText(age)} ago</span> : null}
        </span>
      </a>
    </li>
  );
}

function Section(props: { title: string; games: { g: LiveGameSummary; date: string }[]; receivedMs: number }) {
  if (props.games.length === 0) return null;
  return (
    <section class="panel">
      <h2>{props.title}</h2>
      <ul class="live-cards">
        {props.games.map(({ g, date }) => (
          <GameCard key={g.game_id} g={g} date={date} receivedMs={props.receivedMs} />
        ))}
      </ul>
    </section>
  );
}

function List({ date }: { date: string | null }) {
  const today = etDate(new Date());
  const dates = date !== null ? [date] : [today, previousDate(today)];
  const st = useLiveList(dates);
  if (st.loading) {
    return (
      <Loading what="tonight's live games" />
    );
  }
  const all = st.lists.flatMap((l) => l.games.map((g) => ({ g, date: l.date })));
  const byTip = (a: { g: LiveGameSummary }, b: { g: LiveGameSummary }): number => (a.g.tip_utc ?? "").localeCompare(b.g.tip_utc ?? "");
  const now = all.filter((x) => x.g.state === "in").sort(byTip);
  const later = all.filter((x) => x.g.state === "pre").sort(byTip);
  const done = all.filter((x) => x.g.state === "post").sort(byTip);
  return (
    <div class="stack">
      {st.error ? <p role="alert">Could not reach the live feed. Retrying.</p> : null}
      {all.length === 0 && !st.error ? (
        <section class="panel empty">
          <h2>No games on the live feed</h2>
          <p>
            Nothing was captured for {dates.join(" or ")}. Games appear here a few minutes before tip-off and stay after the final.
          </p>
          <a class="btn primary" href="#/">
            See tonight's games and forecasts
          </a>
        </section>
      ) : null}
      <Section title="Live now" games={now} receivedMs={st.receivedMs} />
      <Section title="Later tonight" games={later} receivedMs={st.receivedMs} />
      <Section title="Final" games={done} receivedMs={st.receivedMs} />
    </div>
  );
}

function Detail({ id, date }: { id: string; date: string | null }) {
  const today = etDate(new Date());
  const dates = date !== null ? [date] : [today, previousDate(today)];
  const st = useLiveList(dates, 30000);
  const hit = st.lists.flatMap((l) => l.games.map((g) => ({ g, date: l.date }))).find((x) => x.g.game_id === id) ?? null;
  const app = useAppGame(hit?.date ?? null, hit?.g.home.abbr ?? null, hit?.g.away.abbr ?? null);
  return (
    <div class="stack">
      <p class="back-row" style={{ margin: 0 }}>
        <a class="back-link" href={liveHref()}>
          All live games
        </a>
      </p>
      <h1 class="sr">Live game</h1>
      <LiveGame eventId={id} game={app.game} gameLoading={app.loading || st.loading} />
    </div>
  );
}

export function Live({ id, date }: { id: string | null; date: string | null }) {
  if (id !== null) return <Detail id={id} date={date} />;
  return (
    <div class="stack">
      <section class="panel">
        <h1>Live</h1>
        <p class="small muted">
          Pick a game to follow the score, plays, box score and player trackers. A few moments behind the arena; each screen says how old it is.
        </p>
      </section>
      <List date={date} />
      <Attribution />
    </div>
  );
}
