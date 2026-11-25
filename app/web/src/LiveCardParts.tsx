// The live score card: one board per game, and a slate view of every tracked player across tonight's games.
// Facts only: the count so far against the stored line, minutes, fouls, and one plain sentence of arithmetic. No chance
// is computed here. Where the export stored a calibrated pre-game chance the server passes it on (and withholds it where a
// display lock applies); a raw number never reaches this file. The in-game model's chance (step 2) renders only if the
// payload carries it, under its own label, and nothing here keeps a score of it.
import "./liveCard.css";
import { useMemo, useState } from "preact/hooks";
import { Avatar } from "./Avatar";
import { CalTag } from "./components";
import { american, bookName, count, pct } from "./format";
import {
  SORT_LABEL,
  STAT_CAP,
  STAT_WORD,
  asOfText,
  bar,
  gameLabel,
  inGameText,
  isRecent,
  paceSentence,
  playerWord,
  sortSlate,
  standingWord,
  type CardRow,
  type ChangeBook,
  type LiveCard,
  type SlateRow,
  type SortKey,
} from "./liveCard";
import { liveHref } from "./router";
import { LOCK_NOTE } from "./record";
import { useLens, useMode } from "./prefs";
import { StateBlock } from "./ui";
import { Loading } from "./components";
import { useLiveCard, useLiveCards, useNow } from "./useLiveCard";

const SIMPLE_ROWS = 8;

function Delta({ book, k, now }: { book: ChangeBook; k: string; now: number }) {
  const by = book.by[k];
  if (!isRecent(book, k, now) || by === undefined) return null;
  const word = `${by > 0 ? "up" : "down"} ${Math.abs(by).toString()}`;
  return (
    <span class="lcd-delta num" role="status">
      {word}
    </span>
  );
}

/** One tracked player and stat. */
export function CardRowView(props: { r: CardRow; card: LiveCard; book: ChangeBook; bookKey: string; now: number; showGame?: boolean }) {
  const { r, card } = props;
  const lens = useLens();
  const mode = useMode();
  const recent = isRecent(props.book, props.bookKey, props.now);
  const tone = r.cleared ? "over" : r.status === "fouled_out" || r.status === "dnp" ? "out" : r.status === "playing" ? "live" : "idle";
  const b = bar(r.line, r.cur);
  const w = STAT_WORD[r.stat];
  const price = lens === "market" && r.price !== null ? r.price : null;
  return (
    <li class={`lcd-row lcd-${tone}${recent ? " lcd-changed" : ""}`} data-key={props.bookKey}>
      <div class="lcd-top">
        <span class="lcd-who">
          <Avatar name={r.name} team={r.team} playerId={r.player_id} size={mode === "simple" ? "md" : "sm"} remote />
          <span class="lcd-name">
            <b>{r.name}</b> <span class="muted small">{r.team}</span>
            {props.showGame ? (
              <a class="lcd-game small" href={liveHref(card.game_id, card.date)}>
                {" "}
                {gameLabel(card)}
              </a>
            ) : null}
          </span>
        </span>
        <span class="lcd-chip">{standingWord(r, card.state)}</span>
      </div>
      <div class="lcd-line num">
        <span class="lcd-cur">{r.cur ?? "-"}</span>
        <span class="lcd-of">
          {" "}
          of {r.line} {STAT_CAP[r.stat]}
        </span>
        <Delta book={props.book} k={props.bookKey} now={props.now} />
      </div>
      <div class="lcd-bar" role="img" aria-label={`${r.cur === null ? "No count" : r.cur.toString()} of ${r.line.toString()} ${w}`}>
        <span class="lcd-fill" style={{ width: `${b.fill.toFixed(1)}%` }} />
        <i class="lcd-tick" style={{ left: `${b.tick.toFixed(1)}%` }} title={`line ${r.line.toString()}`} />
      </div>
      <p class="lcd-say">{paceSentence(r, card)}</p>
      <p class="lcd-meta small muted num">
        {r.minutes !== null && r.status !== "dnp" ? `${r.minutes.toString()} min` : "no minutes"}
        {r.fouls !== null && r.status !== "dnp" ? ` · ${r.fouls.toString()} ${r.fouls === 1 ? "foul" : "fouls"}` : ""}
        {r.chance !== null ? (
          <>
            {" · "}before the game: <b>{pct(r.chance)}</b> {r.chance_source !== null ? <CalTag source={r.chance_source} /> : null}
          </>
        ) : null}
      </p>
      {price !== null ? (
        <p class="lcd-price small muted num">
          Stored price before tip: over {american(price.over)}, under {american(price.under)}
          {price.book ? ` (${bookName(price.book)})` : ""}. Not live.
        </p>
      ) : null}
      {r.in_game ? <p class="lcd-model small num">{inGameText(r.in_game)}</p> : null}
    </li>
  );
}

function SortBar(props: { value: SortKey; keys: SortKey[]; onChange: (k: SortKey) => void }) {
  return (
    <div class="seg lcd-sort" role="group" aria-label="Sort">
      {props.keys.map((k) => (
        <button
          key={k}
          type="button"
          class="seg-btn"
          aria-pressed={props.value === k}
          onClick={() => {
            props.onChange(k);
          }}
        >
          {SORT_LABEL[k]}
        </button>
      ))}
    </div>
  );
}

function Fresh({ card, receivedMs, now }: { card: LiveCard; receivedMs: number; now: number }) {
  const age = card.as_of.age_s + Math.max(0, now - receivedMs) / 1000;
  return (
    <p class="lcd-asof num" role="status">
      <b>{asOfText(card, age)}</b>
    </p>
  );
}

function LockNote({ cards }: { cards: LiveCard[] }) {
  if (!cards.some((c) => c.chance_locked)) return null;
  return <p class="lcd-lock small muted">Pre-game chances are not shown for games under way or finished. {LOCK_NOTE}</p>;
}

/** Why a card has no rows, as its own first-class state (never an error screen). */
function Empty({ card }: { card: LiveCard }) {
  return (
    <StateBlock kind="empty" inline title={card.lines_state === "loading" ? "Loading the stored lines" : "No lines to track"}>
      {card.tracked.text}
    </StateBlock>
  );
}

// ------------------------------------------------------------------------------------------ one game

/** The board for one game: first thing on a live game's page. */
export function ScoreBoard({ eventId }: { eventId: string }) {
  const st = useLiveCard(eventId);
  const now = useNow();
  const mode = useMode();
  const [sort, setSort] = useState<SortKey>("closest");
  const [all, setAll] = useState(false);
  const card = st.card;
  const items: SlateRow[] = useMemo(() => (card ? card.rows.map((row) => ({ row, card })) : []), [card]);
  const sorted = useMemo(() => sortSlate(items, sort, Object.fromEntries(Object.entries(st.book.at).map(([k, v]) => [`${eventId}|${k}`, v]))), [items, sort, st.book, eventId]);
  if (card === null) {
    if (st.loading) return <Loading what="the score card" />;
    return (
      <StateBlock kind="reconnecting" inline title="Could not read the score card.">
        Retrying every few seconds.
      </StateBlock>
    );
  }
  const limit = mode === "simple" && !all ? SIMPLE_ROWS : sorted.length;
  const shown = sorted.slice(0, limit);
  return (
    <section class={`panel lcd-board lcd-${mode}`} aria-labelledby={`lcd-h-${eventId}`}>
      <div class="lcd-head">
        <h2 id={`lcd-h-${eventId}`}>Score card</h2>
        <span class="lcd-state num">
          {card.away.abbr} {card.state === "pre" ? "" : card.away.score} · {card.home.abbr} {card.state === "pre" ? "" : card.home.score}
        </span>
      </div>
      <Fresh card={card} receivedMs={st.receivedMs} now={now} />
      <p class="small muted lcd-feed">{card.feed_note}</p>
      <p class="small lcd-tracked">{card.tracked.text}</p>
      {st.error ? <p class="small lcd-warn" role="status">Could not refresh just now. This is the last copy.</p> : null}
      {card.rows.length === 0 ? (
        <Empty card={card} />
      ) : (
        <>
          <SortBar value={sort} keys={["closest", "changed"]} onChange={setSort} />
          <ul class="lcd-rows">
            {shown.map((x) => (
              <CardRowView key={x.row.key} r={x.row} card={card} book={st.book} bookKey={x.row.key} now={now} />
            ))}
          </ul>
          {limit < sorted.length ? (
            <button
              type="button"
              class="btn lcd-more"
              onClick={() => {
                setAll(true);
              }}
            >
              Show all {count(sorted.length)} lines
            </button>
          ) : null}
        </>
      )}
      <LockNote cards={[card]} />
    </section>
  );
}

// ------------------------------------------------------------------------------------------ the slate

/** Every tracked player across tonight's games. */
export function SlateBoard({ dates }: { dates: string[] }) {
  const st = useLiveCards(dates);
  const now = useNow();
  const mode = useMode();
  const [sort, setSort] = useState<SortKey>("closest");
  const [all, setAll] = useState(false);
  const rows = st.rows;
  const keyed = useMemo(() => rows.map((x) => ({ ...x, key: `${x.card.game_id}|${x.row.key}` })), [rows]);
  const sorted = useMemo(() => sortSlate(rows, sort, st.book.at), [rows, sort, st.book]);
  if (st.loading) return <Loading what="every tracked player" />;
  if (st.error && st.cards.length === 0) {
    return (
      <StateBlock kind="reconnecting" inline title="Could not read the score cards.">
        Retrying every few seconds.
      </StateBlock>
    );
  }
  const play = st.cards.filter((c) => c.state === "in");
  const players = new Set(keyed.map((x) => `${x.card.game_id}|${x.row.player_id}`)).size;
  const first = st.cards.find((c) => c.state === "in") ?? st.cards[0];
  if (st.cards.length === 0) {
    return (
      <StateBlock kind="empty" inline title="No games on the live feed">
        No live feed was stored for these nights, so there is nobody to track yet.
      </StateBlock>
    );
  }
  const limit = mode === "simple" && !all ? SIMPLE_ROWS : sorted.length;
  const why = st.cards
    .filter((c) => c.lines_state !== "ok")
    .map((c) => `${gameLabel(c)}: ${c.tracked.text}`);
  return (
    <section class={`panel lcd-board lcd-slate lcd-${mode}`} aria-labelledby="lcd-slate-h">
      <div class="lcd-head">
        <h2 id="lcd-slate-h">Every tracked player</h2>
        <span class="lcd-state num">{count(play.length)} live</span>
      </div>
      {first ? <Fresh card={first} receivedMs={st.receivedMs} now={now} /> : null}
      <p class="small muted lcd-feed">{first?.feed_note}</p>
      <p class="small lcd-tracked">
        {count(rows.length)} posted lines for {playerWord(players)} across {count(st.cards.length)} {st.cards.length === 1 ? "game" : "games"}.
        {rows.length === 0 ? " No stored lines matched these games." : ""}
      </p>
      {why.length > 0 ? (
        <ul class="plain small muted lcd-why">
          {why.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      {rows.length > 0 ? (
        <>
          <SortBar value={sort} keys={["closest", "game", "changed"]} onChange={setSort} />
          <ul class="lcd-rows">
            {sorted.slice(0, limit).map((x) => (
              <CardRowView key={`${x.card.game_id}|${x.row.key}`} r={x.row} card={x.card} book={st.book} bookKey={`${x.card.game_id}|${x.row.key}`} now={now} showGame />
            ))}
          </ul>
          {limit < sorted.length ? (
            <button
              type="button"
              class="btn lcd-more"
              onClick={() => {
                setAll(true);
              }}
            >
              Show all {count(sorted.length)} lines
            </button>
          ) : null}
        </>
      ) : null}
      <LockNote cards={st.cards} />
    </section>
  );
}
