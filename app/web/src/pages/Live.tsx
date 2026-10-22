import { api } from "../api";
import { num } from "../format";
import { teamColor } from "../teams";
import { FC_STATS, type BoxPlayer, type LiveGame, type LiveGameRow, type LiveStatus } from "../liveTypes";
import { usePoll } from "../usePoll";

export function statusText(s: LiveStatus, startUtc: string): string {
  if (s.state === "pre") {
    const d = new Date(startUtc);
    return Number.isNaN(d.getTime())
      ? s.detail
      : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  }
  if (s.state === "post") return s.detail || "Final";
  return s.detail || `Q${s.period.toString()} ${s.clock}`;
}

export function SourceLine(props: { source: string; stale: boolean; ageS: number; updatedAt: Date | null; error: string | null; onRefresh: () => void; loading: boolean }) {
  return (
    <div class="live-source small muted">
      <span class="tag-src">{props.source}</span>{" "}
      {props.updatedAt ? `Updated ${props.updatedAt.toLocaleTimeString()}. ` : ""}Refreshes every 30 seconds while this page is open.{" "}
      <button type="button" class="linkbtn" onClick={props.onRefresh} disabled={props.loading}>
        {props.loading ? "Refreshing" : "Refresh now"}
      </button>
      {props.stale ? (
        <p class="live-warn" role="status">
          The feed did not answer just now, so these numbers are {Math.round(props.ageS).toString()} seconds old.
        </p>
      ) : null}
      {props.error ? (
        <p class="live-warn" role="alert">
          {props.error.includes("502")
            ? "The live feed is not answering right now. What is shown is the last copy we got."
            : "Could not refresh. What is shown is the last copy we got."}
        </p>
      ) : null}
    </div>
  );
}

function TeamLine(props: { abbr: string; name: string; score: number | null; lead: boolean }) {
  return (
    <div class={`live-team${props.lead ? " lead" : ""}`}>
      <span class="swatch" style={{ background: teamColor(props.abbr) }} aria-hidden="true" />
      <span class="live-abbr">{props.abbr}</span>
      <span class="muted small live-name">{props.name}</span>
      <span class="live-score num">{props.score === null ? "–" : props.score.toString()}</span>
    </div>
  );
}

function GameCard({ g }: { g: LiveGameRow }) {
  const hs = g.home.score ?? 0;
  const as = g.away.score ?? 0;
  const started = g.status.state !== "pre";
  return (
    <a class="panel live-card" href={`#/live/${encodeURIComponent(g.event_id)}`}>
      <div class="row between">
        <span class={`live-chip s-${g.status.state}`}>{g.status.state === "in" ? "LIVE" : g.status.state === "post" ? "FINAL" : "UPCOMING"}</span>
        <span class="small muted num">{statusText(g.status, g.start_utc)}</span>
      </div>
      <TeamLine abbr={g.away.abbr} name={g.away.name} score={g.away.score} lead={started && as > hs} />
      <TeamLine abbr={g.home.abbr} name={g.home.name} score={g.home.score} lead={started && hs > as} />
    </a>
  );
}

export function LivePage({ eventId }: { eventId: string | null }) {
  return eventId ? <LiveBox eventId={eventId} /> : <LiveList />;
}

function LiveList() {
  const p = usePoll(async () => (await api.liveScoreboard()).data, []);
  const sb = p.data;
  return (
    <div class="stack">
      <div class="page-head">
        <h1>Live</h1>
        <p class="muted">Today's NBA games with the score, period and clock. Tap a game for the live box score.</p>
      </div>
      {sb ? (
        <SourceLine source="ESPN unofficial feed" stale={sb.stale} ageS={sb.age_s} updatedAt={p.updatedAt} error={p.error} onRefresh={p.reload} loading={p.loading} />
      ) : null}
      {sb === null && p.loading ? <div class="skeleton" aria-busy="true" /> : null}
      {sb === null && p.error ? (
        <div class="panel error" role="alert">
          <h2>The live feed is not answering</h2>
          <p>ESPN's public feed did not respond, so there are no live scores to show. Nothing is guessed in its place.</p>
          <button type="button" class="btn" onClick={p.reload}>Try again</button>
        </div>
      ) : null}
      {sb?.games.length === 0 ? (
        <div class="panel empty">
          <h2>No games on the scoreboard</h2>
          <p class="muted">ESPN lists no NBA games right now.</p>
        </div>
      ) : null}
      {sb?.games.map((g) => <GameCard key={g.event_id} g={g} />)}
      <p class="small muted">
        Scores come from ESPN's unofficial public feed and can lag the arena by a minute or more. It is shown for
        information only; a public deployment of this feed would need its licence checked first.
      </p>
    </div>
  );
}

const COLS = ["MIN", "PTS", "REB", "AST", "3PM", "PF", "+/-"] as const;

function fcLine(p: BoxPlayer): string | null {
  if (!p.forecast) return null;
  const parts = FC_STATS.flatMap((s) => {
    const f = p.forecast?.[s];
    return f ? [`${num(f.mean)} ${s === "fg3m" ? "3pm" : s}`] : [];
  });
  return parts.length > 0 ? parts.join(" · ") : null;
}

function BoxTable({ team, players }: { team: string; players: BoxPlayer[] }) {
  const rows = players.filter((p) => p.team === team);
  return (
    <section class="panel" aria-label={`${team} box score`}>
      <h2>{team}</h2>
      <div class="live-scroll">
        <table class="st-table live-box num">
          <thead>
            <tr>
              <th scope="col">Player</th>
              {COLS.map((c) => (
                <th key={c} scope="col">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => {
              const fc = fcLine(p);
              return (
                <tr key={p.player_id} class={p.dnp ? "dnp" : undefined}>
                  <th scope="row">
                    {p.name}
                    <div class="small muted fc">{p.dnp ? "did not play" : fc ? `Before tip: ${fc}` : "no forecast"}</div>
                  </th>
                  <td>{p.min ?? "–"}</td>
                  <td>{p.pts ?? "–"}</td>
                  <td>{p.reb ?? "–"}</td>
                  <td>{p.ast ?? "–"}</td>
                  <td>{p.fg3m ?? "–"}</td>
                  <td>{p.pf ?? "–"}</td>
                  <td>{p.pm ?? "–"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function LiveBox({ eventId }: { eventId: string }) {
  const p = usePoll(async () => (await api.liveGame(eventId)).data, [eventId]);
  const g: LiveGame | null = p.data;
  const teams = g ? [g.away?.abbr, g.home?.abbr].filter((t): t is string => !!t) : [];
  return (
    <div class="stack">
      <p class="small">
        <a href="#/live">All games</a>
      </p>
      {g === null && p.loading ? <div class="skeleton" aria-busy="true" /> : null}
      {g === null && p.error ? (
        <div class="panel error" role="alert">
          <h2>No live box score</h2>
          <p>ESPN's public feed did not return this game. Nothing is guessed in its place.</p>
          <button type="button" class="btn" onClick={p.reload}>Try again</button>
        </div>
      ) : null}
      {g ? (
        <>
          <div class="panel">
            {g.away && g.home ? (
              <>
                <div class="row between">
                  <span class={`live-chip s-${g.status.state}`}>{g.status.state === "in" ? "LIVE" : g.status.state === "post" ? "FINAL" : "UPCOMING"}</span>
                  <span class="small muted num">{g.status.detail}</span>
                </div>
                <TeamLine abbr={g.away.abbr} name={g.away.name} score={g.away.score} lead={(g.away.score ?? 0) > (g.home.score ?? 0)} />
                <TeamLine abbr={g.home.abbr} name={g.home.name} score={g.home.score} lead={(g.home.score ?? 0) > (g.away.score ?? 0)} />
              </>
            ) : (
              <p>ESPN did not return the teams for this game.</p>
            )}
          </div>
          <SourceLine source="ESPN unofficial feed" stale={g.stale} ageS={g.age_s} updatedAt={p.updatedAt} error={p.error} onRefresh={p.reload} loading={p.loading} />
          {!g.box_available ? (
            <div class="panel empty">
              <h2>No box score yet</h2>
              <p class="muted">The game has not started, or ESPN has not posted player lines. Check back after tip.</p>
            </div>
          ) : (
            teams.map((t) => <BoxTable key={t} team={t} players={g.players} />)
          )}
          <p class="small muted">
            {g.forecast_date
              ? `"Before tip" is our pre-game forecast mean for ${g.forecast_date}: an average, not a promise, and it was fixed before the game. Players are matched by name; "no forecast" means the name was not found in that day's forecasts.`
              : (g.forecast_note ?? "No pre-game forecast is shown for this game.")}
          </p>
          <p class="small muted">Analysis only, not advice. Live numbers come from ESPN's unofficial feed.</p>
        </>
      ) : null}
    </div>
  );
}
