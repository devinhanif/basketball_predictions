import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { Court } from "../court/Court";
import {
  NO_FILTER,
  applyFilters,
  nbaGameUrl,
  orderShots,
  periodLabel,
  playersOf,
  shotSentence,
  whenText,
  zoneStats,
  type Filters,
  type GameRow,
  type ShotRow,
} from "../court/geometry";
import { ErrorPanel, Loading } from "../components";

interface StudioShots {
  meta: { note: string; n_shots: number };
  games: GameRow[];
  shots: ShotRow[];
}

function Seg<T extends string | number>(props: {
  label: string;
  value: T;
  options: { v: T; t: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div class="st-seg" role="group" aria-label={props.label}>
      <span class="label">{props.label}</span>
      <div class="st-seg-row">
        {props.options.map((o) => (
          <button key={String(o.v)} type="button" class="st-chip" aria-pressed={o.v === props.value} onClick={() => { props.onChange(o.v); }}>
            {o.t}
          </button>
        ))}
      </div>
    </div>
  );
}

const reducedMotion = (): boolean => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function CourtPage(props: { gameId: string | null }) {
  const [data, setData] = useState<StudioShots | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    import("../fixtures/studio_shots.json").then(
      (m) => { setData(m.default); },
      (e: unknown) => { setErr(e instanceof Error ? e.message : String(e)); },
    );
  }, []);
  if (err) return <ErrorPanel message={err} retry={() => { window.location.reload(); }} />;
  if (!data) return <Loading />;
  return <CourtBody data={data} gameId={props.gameId} />;
}

function CourtBody({ data, gameId }: { data: StudioShots; gameId: string | null }) {
  const game = data.games.find((g) => g.game_id === gameId) ?? data.games[0];
  if (!game) return <p class="muted">No games in this data.</p>;
  return <CourtGame data={data} game={game} />;
}

function CourtGame({ data, game }: { data: StudioShots; game: GameRow }) {
  const [filters, setFilters] = useState<Filters>(NO_FILTER);
  const [idx, setIdx] = useState(0);
  const [replay, setReplay] = useState(false);
  const [playing, setPlaying] = useState(false);
  const listRef = useRef<HTMLOListElement>(null);

  const gameShots = useMemo(() => orderShots(data.shots.filter((s) => s.game_id === game.game_id)), [data, game.game_id]);
  const players = useMemo(() => playersOf(gameShots), [gameShots]);
  const shown = useMemo(() => applyFilters(gameShots, filters), [gameShots, filters]);
  const zones = useMemo(() => zoneStats(applyFilters(gameShots, filters, true).filter((s) => s.zone !== "Backcourt")), [gameShots, filters]);
  const periods = useMemo(() => [...new Set(gameShots.map((s) => s.period))].sort((a, b) => a - b), [gameShots]);

  const last = Math.max(0, shown.length - 1);
  const at = Math.min(idx, last);
  const cur = shown[at];
  const set = (patch: Partial<Filters>): void => {
    setFilters({ ...filters, ...patch });
    setIdx(0);
    setPlaying(false);
  };

  useEffect(() => {
    if (!playing) return;
    const t = window.setInterval(() => {
      setIdx((i) => {
        if (i >= last) {
          setPlaying(false);
          return i;
        }
        return i + 1;
      });
    }, reducedMotion() ? 1400 : 750);
    return () => { window.clearInterval(t); };
  }, [playing, last]);

  useEffect(() => {
    const el = listRef.current?.children[at] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [at]);

  const made = shown.filter((s) => s.made).length;
  const play = (): void => {
    if (at >= last) setIdx(0);
    setReplay(true);
    setPlaying(!playing);
  };

  return (
    <div class="stack">
      <div class="page-head">
        <h1>Shot chart and replay</h1>
        <p class="muted">Every shot from a real game, drawn where it was taken. Step through the game in order, or filter to a player.</p>
      </div>

      <div class="st-games" role="group" aria-label="Game">
        {data.games.map((g) => (
          <a key={g.game_id} class="st-chip" href={`#/court/${g.game_id}`} aria-pressed={g.game_id === game.game_id}>
            {g.away} @ {g.home}
          </a>
        ))}
      </div>
      <p class="small muted num">
        {game.away} {game.away_pts ?? ""} at {game.home} {game.home_pts ?? ""}, {game.game_date}. {data.meta.note}
      </p>

      <div class="panel st-filters">
        <Seg
          label="Quarter"
          value={filters.period}
          options={[{ v: "all", t: "All" }, ...periods.map((p) => ({ v: p, t: periodLabel(p) }))]}
          onChange={(v) => { set({ period: v }); }}
        />
        <Seg
          label="Shot"
          value={filters.type}
          options={[{ v: "all", t: "All" }, { v: "2PT", t: "2-pointers" }, { v: "3PT", t: "3-pointers" }]}
          onChange={(v) => { set({ type: v }); }}
        />
        <Seg
          label="Result"
          value={filters.result}
          options={[{ v: "all", t: "All" }, { v: "made", t: "Made" }, { v: "missed", t: "Missed" }]}
          onChange={(v) => { set({ result: v }); }}
        />
        <label class="st-select">
          <span class="label">Player</span>
          <select
            value={String(filters.playerId)}
            onChange={(e) => { set({ playerId: e.currentTarget.value === "all" ? "all" : Number(e.currentTarget.value) }); }}
          >
            <option value="all">Everyone</option>
            {players.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name} ({p.team}, {p.n.toString()} shots)
              </option>
            ))}
          </select>
        </label>
      </div>

      <div class="panel st-courtbox">
        <p class="num st-count">
          <b>{shown.length.toString()}</b> shots shown, <b>{made.toString()}</b> made
          {shown.length > 0 ? ` (${Math.round((100 * made) / shown.length).toString()}%)` : ""}
        </p>
        <Court
          shots={shown}
          zones={zones}
          selected={at}
          upTo={replay ? at : null}
          onPick={(i) => {
            setIdx(i);
            setPlaying(false);
          }}
          label={`Shot chart, ${game.away} at ${game.home}, ${shown.length.toString()} shots`}
        />
        <div class="st-legend small" aria-hidden="true">
          <span>
            <svg viewBox="-10 -10 20 20" width="16" height="16"><circle class="shot made" r="8" /></svg> made
          </span>
          <span>
            <svg viewBox="-10 -10 20 20" width="16" height="16"><path class="shot miss" d="M-7 -7l14 14m0 -14l-14 14" /></svg> missed
          </span>
          <span>Shading: shooting % in the zone (needs 5 or more shots)</span>
        </div>
      </div>

      <div class="panel st-scrub">
        <h2>Play by play</h2>
        {cur ? (
          <div class="st-caption" aria-live="polite">
            <div class="kicker num">
              {whenText(cur)} · shot {(at + 1).toString()} of {shown.length.toString()}
            </div>
            <p class="lead">{cur.description}</p>
            <p class="small muted">{shotSentence(cur)}. {cur.zone}.</p>
          </div>
        ) : (
          <p class="muted">No shots match these filters.</p>
        )}
        <input
          class="st-range"
          type="range"
          min={0}
          max={last}
          value={at}
          disabled={shown.length === 0}
          aria-label="Step through the game's shots in order"
          onInput={(e) => {
            setIdx(Number(e.currentTarget.value));
            setPlaying(false);
          }}
        />
        <div class="row wrap st-controls">
          <button type="button" class="btn" disabled={at <= 0} onClick={() => { setIdx(at - 1); setPlaying(false); }} aria-label="Previous shot">
            Back
          </button>
          <button type="button" class="btn primary" disabled={shown.length === 0} onClick={play}>
            {playing ? "Pause" : replay ? "Play on" : "Replay the game"}
          </button>
          <button type="button" class="btn" disabled={at >= last} onClick={() => { setIdx(at + 1); setPlaying(false); }} aria-label="Next shot">
            Next
          </button>
          <button type="button" class="btn" aria-pressed={replay} onClick={() => { setReplay(!replay); }}>
            {replay ? "Show all shots" : "Hide later shots"}
          </button>
        </div>
        <p class="small muted">
          <a href={nbaGameUrl(game.game_id)} target="_blank" rel="noopener noreferrer">
            Watch this game on NBA.com
          </a>{" "}
          (opens their site; we do not host or copy any NBA video).
        </p>
      </div>

      <div class="panel st-zones">
        <h2>Shooting by zone</h2>
        <table class="st-table num">
          <thead>
            <tr>
              <th scope="col">Zone</th>
              <th scope="col">Made / tried</th>
              <th scope="col">FG%</th>
            </tr>
          </thead>
          <tbody>
            {zones.map((z) => (
              <tr key={z.zone}>
                <th scope="row">{z.zone}</th>
                <td>
                  {z.made.toString()} / {z.att.toString()}
                </td>
                <td>{z.att === 0 ? "-" : `${Math.round((100 * z.made) / z.att).toString()}%${z.att < 5 ? " (n<5)" : ""}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div class="panel st-list">
        <h2>Every shot, in order</h2>
        <ol ref={listRef} class="st-shotlist">
          {shown.map((s, i) => (
            <li key={`${s.game_id}-${s.action_number.toString()}`}>
              <button type="button" aria-current={i === at ? "true" : undefined} onClick={() => { setIdx(i); setPlaying(false); }}>
                <span class={`st-dot ${s.made ? "made" : "miss"}`} aria-hidden="true">{s.made ? "●" : "✕"}</span>
                <span class="num st-when">{whenText(s)}</span>
                <span class="st-text">{s.description}</span>
                <span class="sr">{s.made ? "made" : "missed"}</span>
              </button>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
