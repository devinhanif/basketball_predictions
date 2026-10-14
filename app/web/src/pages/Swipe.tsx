import { useCallback, useEffect, useMemo, useRef, useState } from "preact/hooks";
import { api } from "../api";
import { View } from "../components";
import { american, lineText, longDate, tipTime } from "../format";
import { addPick, loadLedger, type Pick } from "../ledger";
import { useLoad } from "../useLoad";
import type { SwipeCardData } from "../types";
import { STAT_LABEL } from "../types";

function newSeed(): number {
  const a = new Uint32Array(1);
  crypto.getRandomValues(a);
  return (a[0] ?? 1) % 2_000_000_000;
}

const THRESHOLD = 90;
const REASON = {
  after_tip: "That game has tipped off, so the pick was refused.",
  duplicate: "You already answered that card.",
} as const;

function Card(props: {
  card: SwipeCardData;
  onChoose: (side: Pick["side"]) => void;
}) {
  const { card } = props;
  const [dx, setDx] = useState(0);
  const [drag, setDrag] = useState(false);
  const start = useRef<number | null>(null);
  const label = `${card.player}, ${STAT_LABEL[card.stat]}, line ${card.line}. Over pays ${american(card.over_price)}, under pays ${american(card.under_price)}.`;
  return (
    <div
      class={`swipe-card${drag ? " dragging" : ""}`}
      role="group"
      aria-label={label}
      style={{
        transform: `translateX(${dx}px) rotate(${dx / 30}deg)`,
        opacity: 1 - Math.min(Math.abs(dx) / 400, 0.4),
        borderColor: dx > 40 ? "var(--model)" : dx < -40 ? "var(--market)" : undefined,
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
      <div class="who">{card.player}</div>
      <div class="muted small">
        {card.team} vs {card.opponent} · {tipTime(card.tip_utc)}
      </div>
      <div class="stat">{STAT_LABEL[card.stat]}</div>
      <div class="line num">{lineText(card.line)}</div>
      <div class="prices num">
        <span>
          Over <b>{american(card.over_price)}</b>
        </span>
        <span>
          Under <b>{american(card.under_price)}</b>
        </span>
      </div>
      <div class="small muted">Blind card: no forecast shown. Drag right for over, left for under.</div>
    </div>
  );
}

function Deck(props: { date: string; clock: string; cards: SwipeCardData[]; reroll: () => void }) {
  const [ledger, setLedger] = useState<Pick[]>(loadLedger);
  const answered = useMemo(() => new Set(ledger.map((p) => p.prop_id)), [ledger]);
  const [initial] = useState(() => props.cards.filter((c) => !answered.has(c.prop_id)));
  const [index, setIndex] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const shownAt = useRef(performance.now());
  const card = initial[index];

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
      );
      setNotice(result.ok ? null : REASON[result.reason]);
      setLedger(rows);
      setIndex((i) => i + 1);
      shownAt.current = performance.now();
    },
    [card, props.clock, props.date],
  );

  useEffect(() => {
    const on = (e: KeyboardEvent): void => {
      if (e.target instanceof HTMLElement && ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "ArrowRight") choose("over");
      else if (e.key === "ArrowLeft") choose("under");
      else if (e.key === "ArrowDown") choose("skip");
    };
    window.addEventListener("keydown", on);
    return () => {
      window.removeEventListener("keydown", on);
    };
  }, [choose]);

  const total = initial.length;
  const mine = ledger.filter((p) => p.slate_date === props.date);
  return (
    <div class="stack">
      <div class="progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={Math.min(index, total)} aria-label="Deck progress">
        <i style={{ width: `${total ? (Math.min(index, total) / total) * 100 : 0}%` }} />
      </div>
      <p class="hint" style={{ margin: 0 }}>
        Card {Math.min(index + 1, total)} of {total}
      </p>
      <div aria-live="polite" class="sr">
        {notice}
      </div>
      {notice ? <p class="hint" role="status">{notice}</p> : null}
      {card ? (
        <div class="deck">
          <Card key={card.prop_id} card={card} onChoose={choose} />
          <div class="swipe-actions">
            <button type="button" class="btn under" onClick={() => { choose("under"); }}>
              Under
            </button>
            <button type="button" class="btn" onClick={() => { choose("skip"); }}>
              Skip
            </button>
            <button type="button" class="btn over" onClick={() => { choose("over"); }}>
              Over
            </button>
          </div>
          <p class="hint">Keys: left arrow under, right arrow over, down arrow skip.</p>
        </div>
      ) : (
        <div class="card empty">
          <h2>Deck finished</h2>
          <p class="muted">
            You have {mine.filter((p) => p.side !== "skip").length} picks and {mine.filter((p) => p.side === "skip").length} skips on{" "}
            {longDate(props.date)}. They are paper picks stored on this device.
          </p>
          <div class="row wrap" style={{ justifyContent: "center" }}>
            <a class="btn primary" href="#/picks">
              See how they settled
            </a>
            <button type="button" class="btn" onClick={props.reroll}>
              Draw another deck
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function Swipe(props: { date: string | null; onStale: (s: boolean) => void }) {
  const [list, reloadList] = useLoad(() => api.slates(), []);
  return (
    <div class="stack">
      <h1>Swipe</h1>
      <p class="muted" style={{ margin: 0 }}>
        Over or under on a random posted prop. You see the line and the price, nothing else: no forecast, no stats. Your
        answer is stamped before tip and settled against the real result on the My picks page. Paper only.
      </p>
      <View state={list} reload={reloadList} onStale={props.onStale}>
        {(slates) => {
          const date = props.date ?? slates[0]?.date ?? null;
          return (
            <>
              <nav class="chips" aria-label="Choose a slate">
                {slates.map((s) => (
                  <a key={s.date} class="chip" href={`#/swipe?date=${s.date}`} aria-current={s.date === date ? "true" : undefined}>
                    {longDate(s.date)}
                  </a>
                ))}
              </nav>
              {date ? <DeckLoader date={date} onStale={props.onStale} /> : null}
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
      {(deck) => (
        <Deck
          key={`${deck.date}-${deck.seed}`}
          date={deck.date}
          clock={deck.clock_utc}
          cards={deck.cards.slice(0, 24)}
          reroll={() => { setSeed(newSeed()); }}
        />
      )}
    </View>
  );
}
