import { useEffect, useMemo, useState } from "preact/hooks";
import { api, type LiveCheckpoint } from "./api";
import { CalTag } from "./components";
import { pctBounded, tipTime } from "./format";
import {
  ATTRIBUTION,
  CHECKPOINT_LABEL,
  STALE_AFTER_S,
  ageNow,
  ageText,
  buildTrackers,
  clockLabel,
  lagText,
  sortTrackers,
  chanceLocked,
  treatAsFinal,
  type BoxRow,
  type GameState,
  type LiveMeta,
  type LiveState,
  type Play,
  type Tracker,
} from "./live";
import { LOCK_NOTE } from "./record";
import type { GameDetail } from "./types";
import { STAT_SHORT } from "./types";
import { liveHref } from "./router";
import { useLiveGame, type LiveConn } from "./useLive";
import { useLiveFor } from "./useLiveData";

/** How old the data on screen is. Keeps its own one-second clock so nothing else re-renders on a tick. */
export function AsOf(props: { meta: LiveMeta; receivedMs: number; state: GameState }) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const t = window.setInterval(() => {
      setNow(performance.now());
    }, 1000);
    return () => {
      window.clearInterval(t);
    };
  }, []);
  const age = ageNow(props.meta, props.receivedMs, now);
  const stale = props.state === "in" && age > STALE_AFTER_S;
  const lag = lagText(props.meta.play_lag_s);
  return (
    <p class={`live-asof small${stale ? " stale" : ""}`} role="status">
      <span class="num">
        <b>As of {ageText(age)} ago.</b>
      </span>{" "}
      {stale ? "No new data for a while: the feed may be stuck. " : ""}
      {lag ? <span class="muted">{lag}</span> : null}
    </p>
  );
}

export function Attribution(props: { note?: string }) {
  return (
    <p class="live-attrib small muted">
      <b>{ATTRIBUTION}</b> {props.note ?? "A delayed feed, not for betting. Plays can be corrected after the fact."}
    </p>
  );
}

function PhaseNote({ conn }: { conn: LiveConn }) {
  if (conn.phase === "live" || conn.phase === "connecting" || conn.phase === "final") return null;
  return (
    <p class={`live-phase ${conn.phase}`} role="status">
      {conn.message ?? (conn.phase === "error" ? "Could not reach the live feed." : "")}
    </p>
  );
}

// ------------------------------------------------------------------------------------------ play feed

function PlayRow({ p, away, home }: { p: Play; away: string; home: string }) {
  return (
    <li class={`play${p.s ? " score" : ""}`}>
      <span class="play-t num">
        {p.p <= 4 ? `Q${p.p.toString()}` : `OT${(p.p - 4).toString()}`} {p.c}
      </span>
      <span class="play-x">{p.t}</span>
      <span class="play-s num" title={`${away} ${p.a.toString()}, ${home} ${p.h.toString()}`}>
        {p.a}-{p.h}
      </span>
    </li>
  );
}

const PAGE = 40;

export function PlayFeed(props: { live: LiveState }) {
  const { live } = props;
  const [shown, setShown] = useState(PAGE);
  const [older, setOlder] = useState<Play[]>([]);
  const [busy, setBusy] = useState(false);
  const [oldErr, setOldErr] = useState(false);
  const all = useMemo(() => (older.length > 0 ? [...older, ...live.plays] : live.plays), [older, live.plays]);
  const newest = all.slice(Math.max(0, all.length - shown)).reverse();
  const first = all.length > 0 ? (all[0] as Play).n : 0;
  const hasMoreLocal = shown < all.length;
  const hasMoreRemote = !hasMoreLocal && live.nPlays > all.length && first > 0;
  const more = (): void => {
    if (hasMoreLocal) {
      setShown((n) => n + PAGE);
      return;
    }
    setBusy(true);
    setOldErr(false);
    api.livePlays(live.gameId, first, 100).then(
      (r) => {
        setOlder((o) => [...r.data.plays, ...o]);
        setShown((n) => n + PAGE);
        setBusy(false);
      },
      () => {
        setOldErr(true);
        setBusy(false);
      },
    );
  };
  return (
    <section class="panel live-feed" aria-labelledby="lf-h">
      <h2 id="lf-h">Play by play</h2>
      {newest.length === 0 ? (
        <p class="muted">No plays yet.</p>
      ) : (
        <ol class="plays" aria-live="off">
          {newest.map((p) => (
            <PlayRow key={p.n} p={p} away={live.away.abbr} home={live.home.abbr} />
          ))}
        </ol>
      )}
      {hasMoreLocal || hasMoreRemote ? (
        <button type="button" class="btn" disabled={busy} onClick={more}>
          {busy ? "Loading..." : "Show earlier plays"}
        </button>
      ) : null}
      {oldErr ? <p role="alert" class="small">Could not load earlier plays.</p> : null}
      <p class="small muted">Scores in each row are away-home after that play.</p>
    </section>
  );
}

// ------------------------------------------------------------------------------------------ box score

function sortRows(rows: BoxRow[]): BoxRow[] {
  return [...rows].sort((a, b) => Number(Boolean(a.dnp)) - Number(Boolean(b.dnp)) || Number(b.starter) - Number(a.starter) || (b.min ?? 0) - (a.min ?? 0) || b.pts - a.pts);
}

export function BoxScore(props: { live: LiveState }) {
  const { live } = props;
  const [side, setSide] = useState<"away" | "home">("away");
  const team = side === "home" ? live.home : live.away;
  const rows = useMemo(() => sortRows(Object.values(live.box).filter((r) => r.team === team.abbr)), [live.box, team.abbr]);
  return (
    <section class="panel live-box" aria-labelledby="lb-h">
      <div class="row between wrap">
        <h2 id="lb-h">Box score</h2>
        <div class="tabs" role="tablist" aria-label="Team">
          {(["away", "home"] as const).map((s) => (
            <button
              key={s}
              type="button"
              role="tab"
              class="tab"
              aria-selected={side === s}
              aria-current={side === s ? "true" : undefined}
              onClick={() => {
                setSide(s);
              }}
            >
              {(s === "home" ? live.home : live.away).abbr}
            </button>
          ))}
        </div>
      </div>
      {rows.length === 0 ? (
        <p class="muted">No box score yet.</p>
      ) : (
        <div class="box-wrap">
          <table class="box num">
            <thead>
              <tr>
                <th scope="col">Player</th>
                <th scope="col">MIN</th>
                <th scope="col">PTS</th>
                <th scope="col">REB</th>
                <th scope="col">AST</th>
                <th scope="col">3PM</th>
                <th scope="col">PF</th>
                <th scope="col">FG</th>
                <th scope="col">STL</th>
                <th scope="col">BLK</th>
                <th scope="col">TO</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) =>
                r.dnp ? (
                  <tr key={r.id} class="dnp">
                    <th scope="row">{r.name}</th>
                    <td colSpan={10}>{r.dnp}</td>
                  </tr>
                ) : (
                  <tr key={r.id} class={r.pf >= 5 ? "foul-trouble" : undefined}>
                    <th scope="row">
                      {r.name}
                      {r.starter ? <span class="sr"> (starter)</span> : null}
                    </th>
                    <td>{r.min ?? "-"}</td>
                    <td>{r.pts}</td>
                    <td>{r.reb}</td>
                    <td>{r.ast}</td>
                    <td>{r.fg3m}</td>
                    <td>{r.pf}</td>
                    <td>{r.fg}</td>
                    <td>{r.stl}</td>
                    <td>{r.blk}</td>
                    <td>{r.tov}</td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------------------------------ prop trackers

const PACE_WORD: Record<Tracker["pace"], string> = {
  cleared: "Over the line",
  "on pace": "On pace",
  behind: "Behind pace",
  "not started": "Not started",
  "no box": "Not in the box yet",
};

function TrackerRow({ t, locked }: { t: Tracker; locked: boolean }) {
  const frac = t.cur === null || t.line <= 0 ? 0 : Math.min(1, t.cur / t.line);
  return (
    <li class={`tracker pace-${t.pace.replace(" ", "-")}`}>
      <div class="row between wrap">
        <span>
          <b>{t.player}</b> <span class="muted small">{t.team}</span>
        </span>
        <span class="pace-chip small">{PACE_WORD[t.pace]}</span>
      </div>
      <div class="num tr-line">
        Over {t.line} {STAT_SHORT[t.stat]}: {t.cur === null ? "no stat yet" : `has ${t.cur.toString()}`}
        {t.need !== null && t.need > 0 ? `, needs ${t.need.toString()} more` : ""}
      </div>
      <div class="meter" role="img" aria-label={`${(frac * 100).toFixed(0)} percent of the line`}>
        <span style={{ width: `${(frac * 100).toFixed(1)}%` }} />
        {t.expected !== null && t.line > 0 ? <i style={{ left: `${(Math.min(1, t.expected / t.line) * 100).toFixed(1)}%` }} title="where an even pace is now" /> : null}
      </div>
      <div class="small muted num tr-meta">
        {t.minutes !== null ? `${t.minutes.toString()} min` : "no minutes yet"}
        {t.fouls !== null ? ` · ${t.fouls.toString()} fouls` : ""}
        {t.expected !== null ? ` · even pace ${t.expected.toFixed(1)}` : ""}
        {t.chance !== null ? (
          <>
            {" · "}pre-game chance of the over <b>{pctBounded(t.chance, t.extrapolated)}</b> {t.source !== null ? <CalTag source={t.source} /> : null}
          </>
        ) : locked ? null : (
          " · no calibrated pre-game number"
        )}
      </div>
    </li>
  );
}

export function Trackers(props: { live: LiveState; game: GameDetail | null; loading: boolean }) {
  const { live, game } = props;
  const ts = useMemo(() => (game ? sortTrackers(buildTrackers(game.players, live)) : []), [game, live]);
  const [stat, setStat] = useState<"all" | "pts" | "reb" | "ast" | "fg3m">("all");
  const shown = stat === "all" ? ts : ts.filter((t) => t.stat === stat);
  return (
    <section class="panel live-track" aria-labelledby="lt-h">
      <div class="row between wrap">
        <h2 id="lt-h">Prop trackers</h2>
        <div class="seg" role="group" aria-label="Stat">
          {(["all", "pts", "reb", "ast", "fg3m"] as const).map((s) => (
            <button
              key={s}
              type="button"
              class="seg-btn"
              aria-pressed={stat === s}
              onClick={() => {
                setStat(s);
              }}
            >
              {s === "all" ? "All" : STAT_SHORT[s].toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      {props.loading ? (
        <p class="muted">Loading the night's lines...</p>
      ) : !game ? (
        <p class="muted">The lines for this game are not loaded, so there is nothing to track yet.</p>
      ) : shown.length === 0 ? (
        <p class="muted">No lines to track here.</p>
      ) : (
        <ul class="trackers">
          {shown.map((t) => (
            <TrackerRow key={t.key} t={t} locked={chanceLocked(live.date)} />
          ))}
        </ul>
      )}
      <p class="small muted">
        Needs counts what is left to go over the line. The tick on the bar is an even pace (half the line by the half).{" "}
        {chanceLocked(live.date) ? `Pre-game chances are not shown here. ${LOCK_NOTE}` : "Chances are the calibrated pre-game numbers; they do not update during the game."}
      </p>
    </section>
  );
}

// ------------------------------------------------------------------------------------------ checkpoint (flag off today)

export function CheckpointPanel(props: { eventId: string; state: GameState }) {
  const [ck, setCk] = useState<LiveCheckpoint | null>(null);
  useEffect(() => {
    if (props.state !== "in") {
      setCk(null);
      return;
    }
    let live = true;
    const pull = (): void => {
      if (document.hidden) return;
      api.liveCheckpoint(props.eventId).then(
        (r) => {
          if (live) setCk(r.data);
        },
        () => {
          if (live) setCk(null);
        },
      );
    };
    pull();
    const t = window.setInterval(pull, 30000);
    return () => {
      live = false;
      window.clearInterval(t);
    };
  }, [props.eventId, props.state]);
  if (!ck || !ck.available || !ck.rows || ck.rows.length === 0) return null;
  return (
    <section class="panel live-ck" aria-labelledby="lck-h">
      <h2 id="lck-h">In-game model checkpoint</h2>
      <p class="live-unproven small">
        <b>{CHECKPOINT_LABEL}</b>
      </p>
      <p class="small muted num">
        Checkpoint at {ck.checkpoint} game minutes (read at {ck.e_used?.toFixed(1)}), {ck.support}. Chance of reaching the number, from the model.
      </p>
      <ul class="plain small num">
        {ck.rows.slice(0, 40).map((r) => (
          <li key={`${r.player}|${r.stat}|${r.k.toString()}`}>
            {r.player}: {r.k}+ {r.stat === "fg3m" ? "3PM" : r.stat} <b>{Math.round(r.p_model * 100)}%</b> (has {r.cur}, {r.minutes} min, {r.fouls} fouls)
          </li>
        ))}
      </ul>
    </section>
  );
}

// ------------------------------------------------------------------------------------------ the whole live game

export function LiveScoreHead(props: { live: LiveState }) {
  const { live } = props;
  return (
    <section class="panel live-head">
      <div class="live-score num">
        <div class="ls-team">
          <span class="ls-abbr">{live.away.abbr}</span>
          <span class="ls-pts">{live.away.score}</span>
        </div>
        <div class="ls-mid">
          <span class={`ls-state ${live.state}`}>{live.state === "in" ? "LIVE" : live.state === "post" ? "FINAL" : "SOON"}</span>
          <span class="ls-clock">{live.state === "pre" && live.tipUtc ? tipTime(live.tipUtc) : clockLabel(live.status, live.state)}</span>
        </div>
        <div class="ls-team right">
          <span class="ls-abbr">{live.home.abbr}</span>
          <span class="ls-pts">{live.home.score}</span>
        </div>
      </div>
    </section>
  );
}

/** The live view of one game: score, how old it is, feed, box score, trackers, and the checkpoint block if there is one. */
export function LiveGame(props: { eventId: string; game: GameDetail | null; gameLoading: boolean }) {
  const conn = useLiveGame(props.eventId);
  const [view, setView] = useState<"plays" | "box" | "props">("plays");
  const live = conn.state;
  if (live === null) {
    return (
      <div class="stack" aria-busy={conn.phase === "connecting"}>
        <PhaseNote conn={conn} />
        {conn.phase === "connecting" ? <div class="skeleton" /> : null}
        <Attribution />
      </div>
    );
  }
  return (
    <div class="stack live-game">
      <LiveScoreHead live={live} />
      <AsOf meta={live.meta} receivedMs={conn.receivedMs} state={live.state} />
      <PhaseNote conn={conn} />
      <div class="tabs live-tabs" role="tablist" aria-label="Live views">
        {(["plays", "box", "props"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            class="tab"
            aria-selected={view === t}
            aria-current={view === t ? "true" : undefined}
            onClick={() => {
              setView(t);
            }}
          >
            {t === "plays" ? "Plays" : t === "box" ? "Box score" : "Prop trackers"}
          </button>
        ))}
      </div>
      {view === "plays" ? <PlayFeed live={live} /> : null}
      {view === "box" ? <BoxScore live={live} /> : null}
      {view === "props" ? <Trackers live={live} game={props.game} loading={props.gameLoading} /> : null}
      <CheckpointPanel eventId={live.gameId} state={live.state} />
      <Attribution />
    </div>
  );
}

/** The live panel on a game page: appears once the game has started on the capture. A finished game stays behind a
 *  spoiler button, like the final score on the same page. */
export function GameLive({ g }: { g: GameDetail }) {
  const lv = useLiveFor(g);
  const [open, setOpen] = useState(false);
  if (lv === null || lv.state === "pre") return null;
  const final = treatAsFinal(lv.state, lv.tip_utc, Date.now());
  return (
    <div class="stack live-embed">
      <div class="row between wrap">
        <h2 style={{ margin: 0 }}>{final ? "Play by play" : "Live now"}</h2>
        <span class="row wrap">
          {final ? (
            <button
              type="button"
              class="btn"
              aria-pressed={open}
              onClick={() => {
                setOpen(!open);
              }}
            >
              {open ? "Hide play by play" : "Show play by play (shows the final score)"}
            </button>
          ) : null}
          <a class="btn" href={liveHref(lv.game_id, g.date)}>
            Open the full live view
          </a>
        </span>
      </div>
      {final && !open ? null : <LiveGame eventId={lv.game_id} game={g} gameLoading={false} />}
    </div>
  );
}
