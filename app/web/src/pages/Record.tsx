import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { CalTag, Explain, ModelNote, View } from "../components";
import { plainModel } from "../plain";
import { longDate, lineText, num, pct, tipTime } from "../format";
import { TeamTag } from "../components";
import { legHref, recordHref, type RecordTab } from "../router";
import { useLens } from "../prefs";
import { LOCK_NOTE, isLockedNight, loadRounds, makeRound, newSeed, saveRounds, scoreOf, type Guess, type GuessRound, type Score } from "../record";
import { STATS, STAT_LABEL, type CalBin, type CalSeries, type PropsPublished, type GameSummary, type GuessCard, type GuessDeck, type GuessReveal, type PlayerView, type PropView, type SlateListItem, type Stat } from "../types";
import { useLoad } from "../useLoad";
import { didText, sideText } from "../words";

const S = (n: number): string => n.toString();

/* ------------------------------------------------------------------ replay a night */
function PlayerReplay(props: { p: PlayerView; stat: Stat; actual: number | null | undefined; open: boolean; onOpen: () => void; ready: boolean; showSource: boolean; locked: boolean }) {
  const prop: PropView | undefined = props.p.props[props.stat];
  if (!prop) return null;
  const q = prop.q;
  const posted = prop.line_source === "posted";
  const a = props.actual;
  return (
    <li class="rp-player">
      <div class="rp-line">
        <strong>{props.p.name}</strong>
        {props.locked ? null : (
          <span class="small muted num">
            typical {num(q[9] ?? 0)}, middle half {num(q[4] ?? 0)} to {num(q[14] ?? 0)}
            {props.showSource ? `, from ${plainModel(prop.model_source)}` : ""}
          </span>
        )}
      </div>
      <div class="rp-call num">
        {posted ? (
          <>
            {props.locked ? (
              <>Line {lineText(prop.line)}.</>
            ) : (
              <>
                Line {lineText(prop.line)}: we gave the over <b>{pct(prop.model_p_over)}</b> <CalTag source={prop.model_p_source} />.
              </>
            )}
          </>
        ) : (
          <>{props.locked ? "No usable sportsbook line." : `No usable sportsbook line; reference line ${lineText(prop.line)} (the middle of our forecast).`}</>
        )}
      </div>
      {props.open ? (
        !props.ready ? (
          <p class="small muted">Loading what happened...</p>
        ) : a === undefined || a === null ? (
          <p class="small rp-result">Did not play, so this forecast is not scored.</p>
        ) : (
          <p class="small rp-result num">
            He {didText(props.stat, a)}.{" "}
            {posted ? `That is ${a > prop.line ? "over" : "under"} the line.` : null}
          </p>
        )
      ) : (
        <button type="button" class="btn rp-reveal" onClick={props.onOpen}>
          Tap to reveal what happened
        </button>
      )}
    </li>
  );
}

function ReplayGame({ g, locked }: { g: GameSummary; locked: boolean }) {
  const [want, setWant] = useState(false);
  const [showPlayers, setShowPlayers] = useState(false);
  const [stat, setStat] = useState<Stat>("pts");
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [res] = useLoad(() => (want ? api.gameResult(g.game_id) : Promise.resolve({ data: null, fromCache: false })), [g.game_id, want]);
  const [detail] = useLoad(() => (showPlayers ? api.game(g.game_id) : Promise.resolve({ data: null, fromCache: false })), [g.game_id, showPlayers]);
  const r = res.status === "ready" ? res.data : null;
  const ph = g.win_probability.model_home;
  const fav = ph === null ? null : ph >= 0.5 ? g.home : g.away;
  const favP = ph === null ? null : Math.max(ph, 1 - ph);
  const winner = r ? (r.home_won ? g.home : g.away) : null;
  const d = detail.status === "ready" ? detail.data : null;
  const players = d ? d.players.filter((p) => p.props[stat] !== undefined).slice(0, 12) : [];
  return (
    <article class="panel rp-game">
      <div class="score-row">
        <div class="score-team">
          <TeamTag abbr={g.away.abbr} big />
        </div>
        <div class="score-mid num">{r ? <span class="final">{S(r.away_pts)}<span class="dash">-</span>{S(r.home_pts)}</span> : <span class="at">at</span>}</div>
        <div class="score-team right">
          <TeamTag abbr={g.home.abbr} big />
        </div>
      </div>
      <p class="muted small center num">{r ? "Final" : `Tip-off ${tipTime(g.tip_utc)}`}</p>
      <p class="num">
        {fav && favP !== null ? (
          <>
            Before tip our model favoured the <b>{fav.name}</b> at <b>{pct(favP)}</b>
            <span class="small muted"> (game-winner model {g.win_probability.model_name})</span>.
          </>
        ) : (
          "No win forecast was stored for this game."
        )}
      </p>
      {winner ? (
        <p class="rp-result num">
          The <b>{winner.name}</b> won.{" "}
          {fav ? (winner.id === fav.id ? "Our favourite won." : "Our favourite lost.") : ""}
        </p>
      ) : want && res.status === "error" ? (
        <p class="small muted">No final score is stored for this game, so it may not have been played yet.</p>
      ) : (
        <button type="button" class="btn" onClick={() => { setWant(true); }}>
          Tap to reveal the final score
        </button>
      )}
      <button type="button" class="btn rp-toggle" aria-expanded={showPlayers} onClick={() => { setShowPlayers((v) => !v); }}>
        {showPlayers ? "Hide the player forecasts" : "Show the player forecasts as they stood before tip"}
      </button>
      {showPlayers ? (
        <div class="stack tight">
          <div class="tabs" role="group" aria-label="Stat">
            {STATS.map((s) => (
              <button key={s} type="button" class="tab" aria-current={stat === s ? "true" : undefined} onClick={() => { setStat(s); }}>
                {STAT_LABEL[s]}
              </button>
            ))}
          </div>
          {detail.status === "loading" ? <p class="muted">Loading...</p> : null}
          {detail.status === "error" ? <p role="alert">Could not load the players.</p> : null}
          {d ? (
            <>
              <p class="small muted">
                Each forecast is the latest one stored before tip. Showing {S(players.length)} players, biggest forecast first.{" "}
                <a href={legHref(g.game_id, d.players[0]?.player_id ?? 0, stat)}>Open a full breakdown</a> from the game page.
              </p>
              <ModelNote items={d.players.flatMap((p) => Object.values(p.props).map((x) => ({ source: x.model_source, label: x.model_label })))} />
              <ul class="plain rp-players">
                {players.map((p) => (
                  <PlayerReplay
                    key={p.player_id}
                    p={p}
                    stat={stat}
                    actual={r ? (r.players.find((x) => x.player_id === p.player_id)?.actual[stat] ?? null) : undefined}
                    open={open.has(p.player_id)}
                    ready={r !== null}
                    showSource={new Set(players.map((x) => x.props[stat]?.model_source)).size > 1}
                    locked={locked}
                    onOpen={() => {
                      setWant(true);
                      setOpen((o) => new Set(o).add(p.player_id));
                    }}
                  />
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function Replay(props: { date: string | null }) {
  const [list, reloadList] = useLoad(() => api.slates(), []);
  return (
    <View state={list} reload={reloadList} what="the nights we have played">
      {(all: SlateListItem[]) => {
        const nights = all.filter((s) => (s.finished ?? 0) > 0).sort((a, b) => b.date.localeCompare(a.date)).slice(0, 14);
        if (nights.length === 0) {
          return (
            <section class="panel empty">
              <h2>No finished night to replay yet</h2>
              <p>This data holds no game with a final score, so there is nothing to look back on. Come back after a night has been played.</p>
            </section>
          );
        }
        const date = props.date !== null && nights.some((n) => n.date === props.date) ? props.date : (nights[0]?.date ?? "");
        return <ReplayNight nights={nights} date={date} />;
      }}
    </View>
  );
}

function ReplayNight(props: { nights: SlateListItem[]; date: string }) {
  const [slate, reload] = useLoad(() => api.slate(props.date), [props.date]);
  return (
    <div class="stack">
      <section class="panel">
        <h2>Pick a night</h2>
        <p class="small muted">Each forecast below is the last one stored before that game's tip. Results stay hidden until you tap.</p>
        {isLockedNight(props.date) ? <p class="small muted">{LOCK_NOTE}</p> : null}
        <div class="tabs rp-nights" role="group" aria-label="Night">
          {props.nights.map((n) => (
            <a key={n.date} class="tab" href={recordHref("replay", n.date)} aria-current={n.date === props.date ? "true" : undefined}>
              {longDate(n.date).replace(/, \d{4}$/, "")}
              <span class="muted small num">&nbsp;&nbsp;{S(n.finished ?? 0)} played</span>
            </a>
          ))}
        </div>
      </section>
      <View state={slate} reload={reload}>
        {(s) => (
          <div class="stack">
            {s.games.map((g) => (
              <ReplayGame key={g.game_id} g={g} locked={isLockedNight(props.date)} />
            ))}
          </div>
        )}
      </View>
    </div>
  );
}

/* ------------------------------------------------------------------ calibration */
const pc = (x: number): string => `${(x * 100).toFixed(1)}%`;

function BinRow({ b, series }: { b: CalBin; series: CalSeries }) {
  const range = `${Math.round(b.lo * 100).toString()} to ${Math.round(b.hi * 100).toString()}%`;
  return (
    <li class="cal-row">
      <div class="cal-head">
        <strong>When we said {range}</strong>
        <span class="small muted num">
          n = {S(b.n)} in {S(b.n_games)} {b.n_games === 1 ? "game" : "games"}
        </span>
      </div>
      {b.n === 0 || b.said === null || b.happened === null ? (
        <p class="small muted">No cases in this range.</p>
      ) : (
        <>
          <div class="cal-track" role="img" aria-label={`We said ${pc(b.said)}, it happened ${pc(b.happened)}${b.enough && b.lo95 !== null && b.hi95 !== null ? `, 95 percent range ${pc(b.lo95)} to ${pc(b.hi95)}` : ", too few cases for a range"}`}>
            <i class="cal-said" style={{ left: `${(b.said * 100).toFixed(1)}%` }} />
            {b.enough && b.lo95 !== null && b.hi95 !== null ? (
              <i class="cal-int" style={{ left: `${(b.lo95 * 100).toFixed(1)}%`, width: `${((b.hi95 - b.lo95) * 100).toFixed(1)}%` }} />
            ) : null}
            <i class="cal-dot" style={{ left: `${(b.happened * 100).toFixed(1)}%` }} />
          </div>
          {b.enough && b.lo95 !== null && b.hi95 !== null ? (
            <p class="small num">
              We said {pc(b.said)} on average. {series.what_happened[0]?.toUpperCase()}
              {series.what_happened.slice(1)} {pc(b.happened)} of the time (95% range {pc(b.lo95)} to {pc(b.hi95)}).
            </p>
          ) : (
            <p class="small num muted">
              We said {pc(b.said)} on average; it happened {S(Math.round(b.happened * b.n))} of {S(b.n)}. Not enough cases to say more.
            </p>
          )}
        </>
      )}
    </li>
  );
}

/** Which model made the chances in a series, in plain words; split by model when more than one is mixed in. */
function SourceNote({ s }: { s: CalSeries }) {
  const name = (r: CalSeries["sources"][number]): string => (s.name === "Game winners" ? `our game-winner model (${r.source})` : plainModel(r.source));
  if (s.sources.length === 0) return null;
  if (s.sources.length === 1) {
    const r = s.sources[0];
    return r ? <p class="small muted">All {S(r.n)} chances here come from {name(r)}.</p> : null;
  }
  return (
    <div class="small">
      <p class="muted">These chances come from more than one model, so the bins above mix them. Split by model:</p>
      <ul class="plain num">
        {s.sources.map((r) => (
          <li key={r.source}>
            {name(r)}: n = {S(r.n)}
            {r.overall.said !== null && r.overall.happened !== null
              ? `; we said ${pc(r.overall.said)} on average, it happened ${pc(r.overall.happened)}${r.overall.enough && r.overall.lo95 !== null && r.overall.hi95 !== null ? ` (95% range ${pc(r.overall.lo95)} to ${pc(r.overall.hi95)})` : " (not enough to give a range)"}`
              : ""}
            .
          </li>
        ))}
      </ul>
    </div>
  );
}

function SeriesView({ s }: { s: CalSeries }) {
  const o = s.overall;
  return (
    <div class="stack">
      <section class="panel">
        <h2>{s.name}</h2>
        <p class="small muted">
          What we said: {s.what_said}. What happened: {s.what_happened}. This is the win model's own chance; it has no calibrated version.
        </p>
        {o.n === 0 || o.said === null || o.happened === null ? (
          <p class="muted">No settled cases of this kind in the data.</p>
        ) : (
          <p class="num lead">
            Overall: we said {pc(o.said)} on average and it happened {pc(o.happened)} of the time, n = {S(o.n)} in {S(o.n_games)} games
            {o.enough && o.lo95 !== null && o.hi95 !== null ? ` (95% range ${pc(o.lo95)} to ${pc(o.hi95)})` : " (not enough to give a range)"}.
          </p>
        )}
        <SourceNote s={s} />
        <p class="small muted num">
          Left out:{" "}
          {Object.entries(s.excluded)
            .map(([k, v]) => `${S(v)} ${k.replace(/_/g, " ")}`)
            .join(", ")}
          . {s.interval}.
        </p>
      </section>
      <section class="panel">
        <h2>Said versus happened</h2>
        <p class="small muted">The dashed tick is what we said; the dot is what happened; the bar is the 95% range. Dot and tick close together means they matched.</p>
        <ul class="plain cal-list">
          {s.bins.map((b) => (
            <BinRow key={b.lo} b={b} series={s} />
          ))}
        </ul>
      </section>
    </div>
  );
}

const pp = (x: number): string => {
  const v = Math.round(x * 1000) / 10;
  return `${v > 0 ? "+" : v < 0 ? "-" : ""}${Math.abs(v).toFixed(1)} points`;
};
const N = (n: number): string => n.toLocaleString("en-US");
const ll = (x: number): string => Math.abs(x).toFixed(4);
const BOOK_ORDER = ["pinnacle", "draftkings"];

/** The calibrated map's published record on a finished season. Every number is copied from a committed
 * report (T493); nothing here is computed from this season's games, which are a locked test until it reads. */
function PublishedProps({ p }: { p: PropsPublished }) {
  const lens = useLens();
  const books = BOOK_ORDER.filter((k) => p.books[k] !== undefined);
  return (
    <div class="stack" data-published="props">
      <section class="panel">
        <h2>Player props: the calibrated number's record</h2>
        <p class="lead">
          {p.season_label}, before this season. This is the record the calibrated chances were chosen on; it is not a result for this season.
        </p>
        <p class="small" data-locked-note>
          {p.this_season}
        </p>
        <p class="small muted num">
          From our test log {p.ledger_id} (rule {p.rule_sha8}, verdict {p.verdict.toLowerCase()}). That season was partly looked at while the map was
          built, so read it as the map's own record, not a clean test. {p.interval}.
        </p>
      </section>
      {books.map((k) => {
        const b = p.books[k];
        if (b === undefined) return null;
        return (
          <section class="panel" key={k}>
            <h2>{lens === "market" ? b.label : k === "pinnacle" ? "Primary line source (pre-registered)" : "Second line source"}</h2>
            <p class="num lead">
              n = {N(b.n)} lines in {N(b.games)} games. Calibrated chances ran {pp(b.mean_cal_minus_realised.mean)} from how often the over happened (95% range{" "}
              {pp(b.mean_cal_minus_realised.lo)} to {pp(b.mean_cal_minus_realised.hi)}).
            </p>
            <p class="small num muted">
              Score (log loss, lower is better): {ll(b.log_loss_cal_minus_raw.mean)} better than the unadjusted model, 95% range {ll(b.log_loss_cal_minus_raw.hi)} to{" "}
              {ll(b.log_loss_cal_minus_raw.lo)} better.
            </p>
            <ul class="plain cal-list">
              {b.bins.map((x) => (
                <li class="cal-row" key={x.lo}>
                  <div class="cal-head">
                    <strong>When we said {Math.round(x.lo * 100)} to {Math.round(x.lo * 100) + 10}%</strong>
                    <span class="small muted num">n = {N(x.n)}</span>
                  </div>
                  <div class="cal-track" role="img" aria-label={`We said ${pc(x.said)}, it happened ${pc(x.happened)}`}>
                    <i class="cal-said" style={{ left: `${(x.said * 100).toFixed(1)}%` }} />
                    <i class="cal-dot" style={{ left: `${(x.happened * 100).toFixed(1)}%` }} />
                  </div>
                  <p class={`small num${x.n < 100 ? " muted" : ""}`}>
                    We said {pc(x.said)} on average; it happened {pc(x.happened)} of the time.
                    {x.n < 100 ? " Too few cases to read much into." : ""}
                  </p>
                </li>
              ))}
            </ul>
            <p class="small muted">Ranges for single bins were not published, so none is drawn. The dashed tick is what we said; the dot is what happened.</p>
          </section>
        );
      })}
    </div>
  );
}

function Calibration() {
  const [state, reload] = useLoad(() => api.calibration(), []);
  const [which, setWhich] = useState<"props" | "winners">("props");
  return (
    <View state={state} reload={reload}>
      {(c) => (
        <div class="stack">
          <section class="panel">
            <h2>When we said X%, how often did it happen?</h2>
            <p class="small num">
              {c.n_games_finished === 0
                ? "No finished games are in this data."
                : `${S(c.n_games_finished)} finished games, ${longDate(c.date_from ?? "")} to ${longDate(c.date_to ?? "")}.`}{" "}
              {c.note}
            </p>
            <div class="tabs" role="group" aria-label="What to check">
              {(["props", "winners"] as const).map((k) => (
                <button key={k} type="button" class="tab" aria-current={which === k ? "true" : undefined} onClick={() => { setWhich(k); }}>
                  {k === "props" ? "Player props" : "Game winners"}
                </button>
              ))}
            </div>
          </section>
          {which === "props" ? <PublishedProps p={c.props} /> : <SeriesView s={c.winners} />}
          {which === "winners" ? (
          <Explain label="How the range is worked out">
            <p class="small">
              Players on the same team share a game, so their results are not independent. The range comes from re-drawing whole games with replacement
              1,000 times and recomputing the rate each time; the 2.5th and 97.5th percentiles are the ends. A range is only drawn when a bin has enough
              cases and enough games.
            </p>
          </Explain>
          ) : null}
        </div>
      )}
    </View>
  );
}

/* ------------------------------------------------------------------ guess against the model */
function ScoreBoard({ score, onReset }: { score: Score; onReset: () => void }) {
  const range = (r: [number, number] | null): string => (r === null ? "not enough rounds for a range" : `95% range ${pct(r[0])} to ${pct(r[1])}`);
  return (
    <section class="panel gs-score" aria-label="Score">
      <div class="gs-cols">
        <div>
          <div class="label">You</div>
          <div class="big-num num">{S(score.you)}</div>
        </div>
        <div>
          <div class="label">Rounds</div>
          <div class="big-num num">{S(score.n)}</div>
        </div>
      </div>
      {score.n === 0 ? (
        <p class="small muted">No rounds yet. The score stays on this device.</p>
      ) : (
        <p class="small num">
          Right: you {S(score.you)} of {S(score.n)} ({range(score.youRange)}).
        </p>
      )}
      <p class="small muted">The model's own scorecard for this season is a locked test; it appears after it reads (about Dec 1).</p>
      <button type="button" class="linklike small" onClick={onReset} disabled={score.n === 0}>
        Reset my score
      </button>
    </section>
  );
}

function Round(props: { card: GuessCard; index: number; total: number; onDone: (r: GuessRound) => void }) {
  const { card } = props;
  const [picked, setPicked] = useState<Guess | null>(null);
  const [rev, setRev] = useState<GuessReveal | null>(null);
  const [err, setErr] = useState(false);
  const [done, setDone] = useState<GuessRound | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (picked === null) return;
    let live = true;
    setErr(false);
    api.guessReveal(card.game_id, Number(card.prop_id.split(":")[1]), card.stat).then(
      (r) => {
        if (!live) return;
        setRev(r.data);
        const round = makeRound(card.prop_id, picked, r.data.went_over);
        setDone(round);
        props.onDone(round);
      },
      () => {
        if (live) setErr(true);
      },
    );
    return () => {
      live = false;
    };
  }, [picked, attempt]);
  const f = card.form;
  return (
    <section class="panel gs-card">
      <p class="small muted num">
        Round {S(props.index + 1)} of {S(props.total)} · {longDate(card.date)}
      </p>
      <h2>{card.player}</h2>
      <p class="muted">
        {card.team} {card.home ? "vs" : "at"} {card.opponent}
      </p>
      <p class="gs-q">
        <span class="line-out num">{lineText(card.line)}</span>
        <span class="muted"> {STAT_LABEL[card.stat].toLowerCase()}: over or under?</span>
      </p>
      {f ? (
        <p class="small num">
          His last {S(f.last5.length)} games, newest first: {f.last5.length ? f.last5.join(" · ") : "none on file"}
          {f.avg_minutes5 !== null ? `; about ${f.avg_minutes5.toFixed(0)} minutes a game` : ""}.
          {f.n_prior < 10 ? ` Only ${S(f.n_prior)} earlier games are on file.` : ""}
        </p>
      ) : (
        <p class="small muted">No earlier games are on file for him.</p>
      )}
      {picked === null ? (
        <div class="gs-actions">
          <button type="button" class="btn big under" onClick={() => { setPicked("under"); }}>
            <span class="btn-top">Under</span>
            <span class="btn-sub">{sideText(card.stat, card.line, "under")}</span>
          </button>
          <button type="button" class="btn big over" onClick={() => { setPicked("over"); }}>
            <span class="btn-top">Over</span>
            <span class="btn-sub">{sideText(card.stat, card.line, "over")}</span>
          </button>
        </div>
      ) : err ? (
        <p role="alert">Could not load the result. <button type="button" class="btn" onClick={() => { setAttempt((a) => a + 1); }}>Try again</button></p>
      ) : rev === null || done === null ? (
        <p class="muted">Checking...</p>
      ) : (
        <div class="gs-reveal" aria-live="polite">
          <p class="num">
            You said <b>{picked}</b>. He {didText(card.stat, rev.actual)}
            {rev.minutes !== null ? ` in ${num(rev.minutes, 0)} minutes` : ""}: <b>{rev.went_over ? "over" : "under"}</b>.{" "}
            <span class={done.you_right ? "gs-right" : "gs-wrong"}>{done.you_right ? "You were right." : "You were wrong."}</span>
          </p>
          {rev.model_p_over !== null && rev.model_p_source !== null ? (
            <p class="num">
              The model gave the over <b>{pct(rev.model_p_over)}</b> <CalTag source={rev.model_p_source} />.
            </p>
          ) : (
            <p class="small muted">{LOCK_NOTE}</p>
          )}
          <p class="small muted num">
            {rev.stat_mean !== null ? `Its typical night was ${num(rev.stat_mean)}. ` : ""}One round says very little about either of you.
          </p>
        </div>
      )}
    </section>
  );
}

function GuessPlay(props: { d: GuessDeck; played: ReadonlySet<string>; onDone: (r: GuessRound) => void; onAgain: () => void }) {
  const { d } = props;
  // Props this device has already played are left out of the deck, once, when the deck arrives.
  const [cards] = useState(() => d.cards.filter((c) => !props.played.has(c.prop_id)));
  const [index, setIndex] = useState(0);
  const [answered, setAnswered] = useState(false);
  const card = cards[index];
  if (d.cards.length === 0) {
    return (
      <section class="panel empty">
        <h2>Nothing to guess on yet</h2>
        <p>{d.note}</p>
      </section>
    );
  }
  if (!card) {
    return (
      <section class="panel empty">
        <h2>{cards.length === 0 ? "You have played all of these" : "That was the deck"}</h2>
        <p class="num">
          {S(cards.length)} rounds, drawn from {S(d.n_available)} played props with a posted line. {d.note}
        </p>
        <button type="button" class="btn primary" onClick={props.onAgain}>
          New deck
        </button>
      </section>
    );
  }
  return (
    <>
      <Round
        key={card.prop_id}
        card={card}
        index={index}
        total={cards.length}
        onDone={(r) => {
          props.onDone(r);
          setAnswered(true);
        }}
      />
      {answered ? (
        <button
          type="button"
          class="btn primary wide"
          onClick={() => {
            setIndex((i) => i + 1);
            setAnswered(false);
          }}
        >
          {index + 1 >= cards.length ? "Finish" : "Next player"}
        </button>
      ) : null}
    </>
  );
}

function Guess() {
  const [seed, setSeed] = useState(newSeed);
  const [rounds, setRounds] = useState<GuessRound[]>(loadRounds);
  const [deck, reload] = useLoad(() => api.guessDeck(seed, 12), [seed]);
  const score = scoreOf(rounds);
  return (
    <div class="stack">
      <section class="panel">
        <h2>Guess the line</h2>
        <p class="small muted">
          Real player lines from games already played. You see the line and his recent games; the result and the model's chance stay hidden until you
          choose. The score stays on this device.
        </p>
      </section>
      <ScoreBoard
        score={score}
        onReset={() => {
          setRounds([]);
          saveRounds([]);
        }}
      />
      <View state={deck} reload={reload} what="the cards">
        {(d) => (
          <GuessPlay
            key={seed}
            d={d}
            played={new Set(rounds.map((r) => r.prop_id))}
            onDone={(r) => {
              setRounds((all) => {
                const next = [...all, r];
                saveRounds(next);
                return next;
              });
            }}
            onAgain={() => {
              setSeed(newSeed());
            }}
          />
        )}
      </View>
    </div>
  );
}

/* ------------------------------------------------------------------ the page */
const TAB_LABEL: Record<RecordTab, string> = { replay: "Replay", calibration: "Calibration", guess: "Guess the line" };

export function Record(props: { tab: RecordTab; date: string | null }) {
  return (
    <div class="stack record">
      <header class="page-head">
        <h1>Track record</h1>
        <p class="muted">How the forecasts did. Replay a finished night, check how often the chances held up, or guess a line yourself.</p>
      </header>
      <nav class="tabs" aria-label="Track record">
        {(Object.keys(TAB_LABEL) as RecordTab[]).map((t) => (
          <a key={t} class="tab" href={recordHref(t, null)} aria-current={props.tab === t ? "true" : undefined}>
            {TAB_LABEL[t]}
          </a>
        ))}
      </nav>
      {props.tab === "replay" ? <Replay date={props.date} /> : props.tab === "calibration" ? <Calibration /> : <Guess />}
    </div>
  );
}
