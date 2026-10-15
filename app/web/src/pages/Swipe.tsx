import { useCallback, useMemo, useRef, useState } from "preact/hooks";
import { api } from "../api";
import { CardDetail } from "../CardDetail";
import { Explain, RecordBoard, ResetPicks, TeamTag, View } from "../components";
import { american, bookName, lineText, longDate, tipTime } from "../format";
import { addPick, loadLedger, type Pick } from "../ledger";
import { useLoad } from "../useLoad";
import { chronological, defaultNight } from "../nights";
import { useSettled } from "../useSettled";
import type { SwipeCardData } from "../types";
import { STAT_LABEL } from "../types";
import { didText, sideText } from "../words";
import { swipeKeyChoice } from "../swipeKeys";

function newSeed(): number {
  const a = new Uint32Array(1);
  crypto.getRandomValues(a);
  return (a[0] ?? 1) % 2_000_000_000;
}

const THRESHOLD = 90;
const REASON = {
  after_tip: "That game has already tipped off, so we could not take the pick.",
  duplicate: "You already answered this one.",
} as const;

function Arrow({ up }: { up: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="currentColor">
      {up ? <path d="M12 4 21 19H3z" /> : <path d="M12 20 3 5h18z" />}
    </svg>
  );
}

function Card(props: {
  card: SwipeCardData;
  onChoose: (side: Pick["side"]) => void;
}) {
  const { card } = props;
  const [dx, setDx] = useState(0);
  const [drag, setDrag] = useState(false);
  const start = useRef<number | null>(null);
  const label = `${card.player}, ${STAT_LABEL[card.stat]}, line ${card.line.toString()}. Over pays ${american(card.over_price)}, under pays ${american(card.under_price)}.`;
  const lean = dx > 40 ? "over" : dx < -40 ? "under" : null;
  return (
    <div
      class={`swipe-card${drag ? " dragging" : ""}${lean ? ` lean-${lean}` : ""}`}
      role="group"
      aria-label={label}
      style={{
        transform: `translateX(${dx.toString()}px) rotate(${(dx / 40).toString()}deg)`,
      }}
      onPointerDown={(e) => {
        start.current = e.clientX;
        setDrag(true);
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (start.current !== null) setDx(e.clientX - start.current);
      }}
      onPointerUp={() => {
        const moved = dx;
        start.current = null;
        setDrag(false);
        setDx(0);
        if (moved > THRESHOLD) props.onChoose("over");
        else if (moved < -THRESHOLD) props.onChoose("under");
      }}
      onPointerCancel={() => {
        start.current = null;
        setDrag(false);
        setDx(0);
      }}
    >
      {lean ? <div class={`stamp ${lean}`}>{lean === "over" ? "Over" : "Under"}</div> : null}
      <div class="who">{card.player}</div>
      <div class="matchup-line muted">
        <TeamTag abbr={card.team} /> vs <TeamTag abbr={card.opponent} /> <span class="num">· {tipTime(card.tip_utc)}</span>
      </div>
      <div class="stat">{STAT_LABEL[card.stat]}</div>
      <div class="line num">{lineText(card.line)}</div>
      {card.book ? <div class="muted small">Price from {bookName(card.book)}</div> : null}
      <div class="muted small">
        Over means {sideText(card.stat, card.line, "over")}. Under means {sideText(card.stat, card.line, "under")}.
      </div>
    </div>
  );
}

function Deck(props: { date: string; clock: string; cards: SwipeCardData[]; reroll: () => void }) {
  const [ledger, setLedger] = useState<Pick[]>(loadLedger);
  const answered = useMemo(() => new Set(ledger.map((p) => p.prop_id)), [ledger]);
  const [initial] = useState(() => props.cards.filter((c) => !answered.has(c.prop_id)));
  const [index, setIndex] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastId, setLastId] = useState<string | null>(null);
  const shownAt = useRef(performance.now());
  const revealed = useRef<string | null>(null);
  const card = initial[index];
  const { results, settled } = useSettled(ledger);

  const choose = useCallback(
    (side: Pick["side"]) => {
      if (!card) return;
      const { rows, result } = addPick(
        loadLedger(),
        {
          prop_id: card.prop_id,
          slate_date: props.date,
          game_id: card.game_id,
          player: card.player,
          team: card.team,
          opponent: card.opponent,
          stat: card.stat,
          line: card.line,
          over_price: card.over_price,
          under_price: card.under_price,
          tip_utc: card.tip_utc,
        },
        side,
        props.clock,
        performance.now() - shownAt.current,
        new Date(),
        revealed.current === card.prop_id,
      );
      setNotice(result.ok ? null : REASON[result.reason]);
      setLastId(result.ok && side !== "skip" ? card.prop_id : null);
      setLedger(rows);
      setIndex((i) => i + 1);
      shownAt.current = performance.now();
    },
    [card, props.clock, props.date],
  );

  /* Keys count only when the deck surface itself has focus (F12): never from a button, link, summary or form field. */
  const onDeckKey = useCallback(
    (e: KeyboardEvent) => {
      const choice = swipeKeyChoice(e.key, e.target === e.currentTarget && !e.altKey && !e.ctrlKey && !e.metaKey);
      if (choice === null) return;
      e.preventDefault();
      choose(choice);
    },
    [choose],
  );

  const total = initial.length;
  const mine = ledger.filter((p) => p.slate_date === props.date);
  const last = lastId ? settled.find((s) => s.pick.id === lastId) : undefined;
  return (
    <div class="stack tight">
      <RecordBoard compact settled={settled} loading={results.status === "loading"} picks={ledger.filter((p) => p.side !== "skip").length} />
      <div>
        <div class="progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={Math.min(index, total)} aria-label="Deck progress" aria-valuetext={`Card ${Math.min(index + 1, total).toString()} of ${total.toString()}`}>
          <i style={{ width: `${(total ? (Math.min(index, total) / total) * 100 : 0).toString()}%` }} />
        </div>
        <p class="hint num">
          Card {Math.min(index + 1, total)} of {total}
        </p>
      </div>
      <div aria-live="polite" class="sr">
        {notice}
        {last ? `${last.outcome}: ${last.pick.player} ${didText(last.pick.stat, last.row.actual)}` : ""}
      </div>
      {notice ? <p class="hint" role="status">{notice}</p> : null}
      {card ? (
        <div
          class="deck"
          tabIndex={0}
          role="group"
          aria-label="Card deck. With this focused: left arrow for under, right arrow for over, space or S to skip."
          onKeyDown={onDeckKey}
        >
          <Card key={card.prop_id} card={card} onChoose={choose} />
          <div class="swipe-actions">
            <button type="button" class="btn big under" onClick={() => { choose("under"); }}>
              <span class="btn-top">
                <Arrow up={false} /> Under
              </span>
              <span class="btn-sub num">{american(card.under_price)}</span>
            </button>
            <button type="button" class="btn big over" onClick={() => { choose("over"); }}>
              <span class="btn-top">
                <Arrow up /> Over
              </span>
              <span class="btn-sub num">{american(card.over_price)}</span>
            </button>
          </div>
          <button type="button" class="btn skip" onClick={() => { choose("skip"); }}>
            Skip this one
          </button>
          <p class="hint">Swipe the card or press the buttons. With the keyboard, tab to the deck first: left arrow for under, right arrow for over, space or S to skip.</p>
          <CardDetail
            key={card.prop_id}
            card={card}
            onReveal={() => {
              revealed.current = card.prop_id;
            }}
          />
        </div>
      ) : (
        <div class="panel empty">
          <h2>That's the deck</h2>
          <p class="muted">
            On {longDate(props.date)} you made {mine.filter((p) => p.side !== "skip").length} picks and skipped{" "}
            {mine.filter((p) => p.side === "skip").length}. They are pretend picks, saved only on this device.
          </p>
          <div class="row wrap center">
            <a class="btn primary" href="#/picks">
              See your results
            </a>
            <button type="button" class="btn" onClick={props.reroll}>
              Deal another deck
            </button>
          </div>
        </div>
      )}
      <div class="last-slot">
        {last ? (
          <p class={`last ${last.outcome}`}>
            <b>{last.outcome === "win" ? "Hit" : last.outcome === "loss" ? "Miss" : "Push"}</b> - {last.pick.player}{" "}
            {didText(last.pick.stat, last.row.actual)}. <span class="muted">You took {last.pick.side} {lineText(last.pick.line)}.</span>
          </p>
        ) : null}
      </div>
      <div class="reset-row">
        <ResetPicks
          onReset={() => {
            setLedger([]);
            props.reroll();
          }}
        />
      </div>
    </div>
  );
}

export function Swipe(props: { date: string | null; onStale: (s: boolean) => void }) {
  const [list, reloadList] = useLoad(() => api.slates(), []);
  return (
    <div class="stack">
      <div class="page-head">
        <h1>Over or under?</h1>
        <Explain label="How does this work?">
          <p>
            These are real player lines. By default you see the number and the price, nothing else: no forecast, no
            stats. If you open "Stats and line slider" you see recent games and our forecast, and that pick is saved
            as not blind. Your answer is locked in before tip-off, then graded against what actually happened. They are
            pretend picks, so no money is involved, and your record is saved only on this device.
          </p>
        </Explain>
      </div>
      <View state={list} reload={reloadList} onStale={props.onStale}>
        {(list) => {
          const slates = chronological(list);
          const date = props.date ?? defaultNight(slates);
          return (
            <>
              {date ? <DeckLoader date={date} onStale={props.onStale} /> : null}
              <nav class="tabs night-tabs" aria-label="Choose a night">
                {slates.map((s) => (
                  <a key={s.date} class="tab" href={`#/swipe?date=${s.date}`} aria-current={s.date === date ? "true" : undefined}>
                    {longDate(s.date).replace(/, \d{4}$/, "")}
                  </a>
                ))}
              </nav>
            </>
          );
        }}
      </View>
    </div>
  );
}

function DeckLoader({ date, onStale }: { date: string; onStale: (s: boolean) => void }) {
  const [seed, setSeed] = useState(newSeed);
  const [state, reload] = useLoad(() => api.swipe(date, seed, 60), [date, seed]);
  return (
    <View state={state} reload={reload} onStale={onStale}>
      {(deck) =>
        deck.cards.length === 0 ? (
          <div class="panel empty">
            <h2>No cards for this night</h2>
            <p class="muted">{deck.note}</p>
          </div>
        ) : (
        <Deck
          key={`${deck.date}-${deck.seed.toString()}`}
          date={deck.date}
          clock={deck.clock_utc}
          cards={deck.cards.slice(0, 24)}
          reroll={() => { setSeed(newSeed()); }}
        />
        )
      }
    </View>
  );
}
