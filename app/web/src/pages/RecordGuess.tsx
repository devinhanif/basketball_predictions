// Track record, the guess game: you against the outcomes, scored on this device only (split from Record.tsx; behaviour unchanged).
import { useEffect, useState } from "preact/hooks";
import { api } from "../api";
import { CalTag, View } from "../components";
import { longDate, lineText, num, pct } from "../format";
import { LOCK_NOTE, loadRounds, makeRound, newSeed, saveRounds, scoreOf, type Guess, type GuessRound, type Score } from "../record";
import { STAT_LABEL, type GuessCard, type GuessDeck, type GuessReveal } from "../types";
import { useLoad } from "../useLoad";
import { didText, sideText } from "../words";

const S = (n: number): string => n.toString();

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

export function Guess() {
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
