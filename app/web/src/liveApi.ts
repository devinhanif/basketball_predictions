// The live screens' own API calls (kept out of api.ts so the live lane touches no shared file). All GET.
import { getJson } from "./api";
import type { Snapshot } from "./live";

export const liveApi = {
  /** The same first message the stream sends, over plain HTTP: the page paints from this and never waits on the stream. */
  snapshot: (eventId: string) => getJson<Snapshot>(`/api/live/${encodeURIComponent(eventId)}/snapshot`),
};
