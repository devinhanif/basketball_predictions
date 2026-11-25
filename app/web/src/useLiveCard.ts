// The score card's data: one game's card, or every game's cards for the night(s), re-read every few seconds while the
// tab is visible, with the rows whose count moved marked. Plain reads of GET routes (no stream): the route answers in
// milliseconds, and a card must keep working behind a proxy that holds event streams.
import { useEffect, useRef, useState } from "preact/hooks";
import { liveApi } from "./liveApi";
import { emptyBook, noteChanges, type ChangeBook, type LiveCard, type SlateRow } from "./liveCard";

export const CARD_POLL_MS = 10000;

export interface CardState {
  loading: boolean;
  error: boolean;
  card: LiveCard | null;
  /** The reason a read failed, plain words (the screen shows it as a note, not an error page). */
  receivedMs: number;
  book: ChangeBook;
}
export interface SlateState {
  loading: boolean;
  error: boolean;
  rows: SlateRow[];
  cards: LiveCard[];
  receivedMs: number;
  book: ChangeBook;
}

/** A clock that ticks once a second, so ages and the changed-row marks run down without any request. */
export function useNow(everyMs = 1000): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const t = window.setInterval(() => {
      setNow(performance.now());
    }, everyMs);
    return () => {
      window.clearInterval(t);
    };
  }, [everyMs]);
  return now;
}

function every(pull: () => void, ms: number): () => void {
  pull();
  const t = window.setInterval(() => {
    if (!document.hidden) pull();
  }, ms);
  const onVis = (): void => {
    if (!document.hidden) pull();
  };
  document.addEventListener("visibilitychange", onVis);
  return () => {
    window.clearInterval(t);
    document.removeEventListener("visibilitychange", onVis);
  };
}

export function useLiveCard(eventId: string, everyMs = CARD_POLL_MS): CardState {
  const [st, setSt] = useState<CardState>({ loading: true, error: false, card: null, receivedMs: 0, book: emptyBook() });
  const book = useRef<ChangeBook>(emptyBook());
  useEffect(() => {
    let live = true;
    book.current = emptyBook();
    setSt({ loading: true, error: false, card: null, receivedMs: 0, book: book.current });
    const stop = every(() => {
      liveApi.card(eventId).then(
        (r) => {
          if (!live) return;
          book.current = noteChanges(book.current, r.data.rows, performance.now());
          setSt({ loading: false, error: false, card: r.data, receivedMs: performance.now(), book: book.current });
        },
        () => {
          if (live) setSt((s) => ({ ...s, loading: false, error: true }));
        },
      );
    }, everyMs);
    return () => {
      live = false;
      stop();
    };
  }, [eventId, everyMs]);
  return st;
}

export function useLiveCards(dates: string[], everyMs = CARD_POLL_MS + 2000): SlateState {
  const key = dates.join(",");
  const [st, setSt] = useState<SlateState>({ loading: true, error: false, rows: [], cards: [], receivedMs: 0, book: emptyBook() });
  const book = useRef<ChangeBook>(emptyBook());
  useEffect(() => {
    let live = true;
    book.current = emptyBook();
    const stop = every(() => {
      Promise.all(dates.map((d) => liveApi.cards(d).then((r) => r.data))).then(
        (lists) => {
          if (!live) return;
          const cards = lists.flatMap((l) => l.games);
          const rows = cards.flatMap((card) => card.rows.map((row) => ({ row, card })));
          book.current = noteChanges(
            book.current,
            rows.map((x) => ({ key: `${x.card.game_id}|${x.row.key}`, cur: x.row.cur })),
            performance.now(),
          );
          setSt({ loading: false, error: false, rows, cards, receivedMs: performance.now(), book: book.current });
        },
        () => {
          if (live) setSt((s) => ({ ...s, loading: false, error: true }));
        },
      );
    }, everyMs);
    return () => {
      live = false;
      stop();
    };
  }, [key, everyMs]);
  return st;
}
