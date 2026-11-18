// The live screens' own API calls (kept out of api.ts so the live lane touches no shared file). All GET.
import { getJson } from "./api";
import type { Snapshot } from "./live";
import type { LiveCard, LiveCardList } from "./liveCard";

export const liveApi = {
  /** The same first message the stream sends, over plain HTTP: the page paints from this and never waits on the stream. */
  snapshot: (eventId: string) => getJson<Snapshot>(`/api/live/${encodeURIComponent(eventId)}/snapshot`),
  /** One game's score card: the live box joined to the stored lines. */
  card: (eventId: string) => getJson<LiveCard>(`/api/live/${encodeURIComponent(eventId)}/card`),
  /** Every game's cards for one night: the slate view of every tracked player. */
  cards: (date: string) => getJson<LiveCardList>(`/api/live/${encodeURIComponent(date)}/cards`),
};
