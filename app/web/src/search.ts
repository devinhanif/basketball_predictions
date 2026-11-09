// Search (Ctrl/Cmd+K): screens, games and players. The index is built from what the page already holds; nothing is looked up
// elsewhere. Matching is plain: every word typed must appear (start of a word scores higher).
export type SearchKind = "screen" | "game" | "player";

export interface SearchItem {
  kind: SearchKind;
  label: string;
  sub: string;
  href: string;
  /** Extra words to match (team names, abbreviations). */
  words?: string;
}

export const KIND_LABEL: Record<SearchKind, string> = { screen: "Screens", game: "Games", player: "Players" };

function norm(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Score one item for a query: 0 means no match. Every word must be found; word starts count double. */
export function scoreItem(item: SearchItem, query: string): number {
  const words = norm(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return 1;
  const hay = norm(`${item.label} ${item.sub} ${item.words ?? ""}`);
  let score = 0;
  for (const w of words) {
    const at = hay.indexOf(w);
    if (at < 0) return 0;
    score += at === 0 || /[^a-z0-9]/.test(hay[at - 1] ?? "") ? 2 : 1;
  }
  if (norm(item.label).startsWith(norm(query).trim())) score += 3;
  return score;
}

/** Best matches first; at most `limit`, at most `perKind` of each kind. An empty query lists the screens. */
export function search(index: readonly SearchItem[], query: string, limit = 12, perKind = 6): SearchItem[] {
  const q = query.trim();
  if (q === "") return index.filter((i) => i.kind === "screen").slice(0, limit);
  const scored = index
    .map((item, order) => ({ item, s: scoreItem(item, q), order }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.order - b.order);
  const counts: Record<SearchKind, number> = { screen: 0, game: 0, player: 0 };
  const out: SearchItem[] = [];
  for (const { item } of scored) {
    if (counts[item.kind] >= perKind) continue;
    counts[item.kind]++;
    out.push(item);
    if (out.length >= limit) break;
  }
  return out;
}

/** Keyboard selection: wraps around. */
export function moveSelection(current: number, delta: number, count: number): number {
  if (count <= 0) return 0;
  return (((current + delta) % count) + count) % count;
}
