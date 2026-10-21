/** Reason strings for a card ("why this forecast"), produced by the matchup/availability work and carried by the export.
 *  Until the export carries them this stays empty and the panel is not drawn. */
export type ReasonKind = "matchup" | "archetype" | "availability" | "other";

export interface Reason {
  kind: ReasonKind;
  text: string;
  /** Table or report the string was built from; shown under the text. */
  source: string | null;
  as_of: string | null;
}

export const KIND_LABEL: Record<ReasonKind, string> = {
  matchup: "Matchup",
  archetype: "Player type",
  availability: "Who is playing",
  other: "Other",
};

const KINDS = new Set<string>(["matchup", "archetype", "availability", "other"]);
export const MAX_REASONS = 8;

/** Accepts anything the API sends; keeps only well-formed, non-empty reasons. Strings are text, never HTML. */
export function parseReasons(x: unknown): Reason[] {
  if (!Array.isArray(x)) return [];
  const out: Reason[] = [];
  for (const r of x as unknown[]) {
    if (typeof r !== "object" || r === null) continue;
    const o = r as Record<string, unknown>;
    const text = typeof o["text"] === "string" ? o["text"].trim() : "";
    if (text === "" || text.length > 400) continue;
    const kind = typeof o["kind"] === "string" && KINDS.has(o["kind"]) ? (o["kind"] as ReasonKind) : "other";
    out.push({
      kind,
      text,
      source: typeof o["source"] === "string" && o["source"] !== "" ? o["source"] : null,
      as_of: typeof o["as_of"] === "string" && o["as_of"] !== "" ? o["as_of"] : null,
    });
    if (out.length >= MAX_REASONS) break;
  }
  return out;
}
