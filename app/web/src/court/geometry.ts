/** Half-court geometry in the NBA shot-chart coordinate system: tenths of a foot, basket at (0, 0),
 *  x across (-250..250), y away from the basket (-52.5 baseline .. 417.5 half-court line). */

export interface ShotRow {
  game_id: string;
  action_number: number;
  period: number;
  clock_seconds: number;
  player_id: number;
  player: string;
  team: string;
  loc_x: number;
  loc_y: number;
  shot_distance: number;
  zone: string;
  action_type: string;
  shot_type: string;
  made: boolean;
  description: string;
}

export interface GameRow {
  game_id: string;
  game_date: string;
  home: string;
  away: string;
  home_pts: number | null;
  away_pts: number | null;
}

export const VIEWBOX = "-250 -52.5 500 470";
export const ZONES = ["Restricted Area", "In The Paint (Non-RA)", "Mid-Range", "Corner 3", "Above the Break 3"] as const;
export type Zone = (typeof ZONES)[number];

const THREE_R = 237.5;
const CORNER_X = 220;
const CORNER_Y = Math.sqrt(THREE_R * THREE_R - CORNER_X * CORNER_X);
const TOP = 365; // screen y = TOP - court y, so the basket is at the bottom of the picture

export const sy = (y: number): number => TOP - y;
const f = (n: number): string => (Math.round(n * 100) / 100).toString();

const OUTER = `M-250 ${f(sy(-52.5))}H250V${f(sy(417.5))}H-250Z`;
const THREE_REGION = `M-${CORNER_X} ${f(sy(-52.5))}V${f(sy(CORNER_Y))}A${THREE_R} ${THREE_R} 0 0 1 ${CORNER_X} ${f(sy(CORNER_Y))}V${f(sy(-52.5))}Z`;
const PAINT = `M-80 ${f(sy(-52.5))}H80V${f(sy(137.5))}H-80Z`;
const RA = `M-40 ${f(sy(0))}A40 40 0 1 1 40 ${f(sy(0))}A40 40 0 1 1 -40 ${f(sy(0))}Z`;
const CORNERS = `M-250 ${f(sy(-52.5))}H-${CORNER_X}V${f(sy(CORNER_Y))}H-250ZM${CORNER_X} ${f(sy(-52.5))}H250V${f(sy(CORNER_Y))}H${CORNER_X}Z`;

/** Disjoint filled regions (even-odd), one per zone, so shading never double-paints. */
export const ZONE_PATH: Record<Zone, string> = {
  "Restricted Area": RA,
  "In The Paint (Non-RA)": PAINT + RA,
  "Mid-Range": THREE_REGION + PAINT,
  "Corner 3": CORNERS,
  "Above the Break 3": OUTER + THREE_REGION + CORNERS,
};

/** Where each zone's label sits (court coordinates). */
export const ZONE_LABEL: Record<Zone, [number, number]> = {
  "Restricted Area": [0, 62],
  "In The Paint (Non-RA)": [0, 108],
  "Mid-Range": [-150, 150],
  "Corner 3": [-232, 20],
  "Above the Break 3": [0, 320],
};

export const LINES: string[] = [
  `M-250 ${f(sy(-52.5))}H250V${f(sy(417.5))}H-250Z`, // boundary
  PAINT,
  `M-60 ${f(sy(137.5))}A60 60 0 0 0 60 ${f(sy(137.5))}`, // free-throw circle, near half
  `M-60 ${f(sy(137.5))}A60 60 0 0 1 60 ${f(sy(137.5))}`,
  `M-40 ${f(sy(0))}A40 40 0 0 1 40 ${f(sy(0))}`, // restricted arc
  `M-30 ${f(sy(-7.5))}H30`, // backboard
  `M-${CORNER_X} ${f(sy(-52.5))}V${f(sy(CORNER_Y))}A${THREE_R} ${THREE_R} 0 0 1 ${CORNER_X} ${f(sy(CORNER_Y))}V${f(sy(-52.5))}`,
  `M-60 ${f(sy(417.5))}A60 60 0 0 1 60 ${f(sy(417.5))}`, // centre circle, our half
];
export const HOOP = { cx: 0, cy: sy(0), r: 7.5 };

export type ResultFilter = "all" | "made" | "missed";
export type TypeFilter = "all" | "2PT" | "3PT";
export interface Filters {
  period: number | "all";
  type: TypeFilter;
  result: ResultFilter;
  playerId: number | "all";
}
export const NO_FILTER: Filters = { period: "all", type: "all", result: "all", playerId: "all" };

export function orderShots(shots: ShotRow[]): ShotRow[] {
  return shots
    .slice()
    .sort((a, b) => a.period - b.period || b.clock_seconds - a.clock_seconds || a.action_number - b.action_number);
}

export function typeOf(s: ShotRow): "2PT" | "3PT" {
  return s.shot_type.startsWith("3") ? "3PT" : "2PT";
}

export function applyFilters(shots: ShotRow[], f: Filters, ignoreResult = false): ShotRow[] {
  return shots.filter(
    (s) =>
      (f.period === "all" || s.period === f.period) &&
      (f.type === "all" || typeOf(s) === f.type) &&
      (f.playerId === "all" || s.player_id === f.playerId) &&
      (ignoreResult || f.result === "all" || (f.result === "made") === s.made),
  );
}

export interface ZoneStat {
  zone: Zone;
  att: number;
  made: number;
  pct: number | null;
}
/** Below this many attempts a zone is not shaded: a percentage on 3 shots is not a percentage. */
export const MIN_ZONE_ATT = 5;

export function zoneStats(shots: ShotRow[]): ZoneStat[] {
  return ZONES.map((zone) => {
    const z = shots.filter((s) => s.zone === zone);
    const made = z.filter((s) => s.made).length;
    return { zone, att: z.length, made, pct: z.length > 0 ? made / z.length : null };
  });
}

/** Fill opacity for a zone: none when the sample is too small. */
export function zoneShade(z: ZoneStat): number {
  if (z.pct === null || z.att < MIN_ZONE_ATT) return 0;
  return 0.12 + 0.55 * Math.min(1, Math.max(0, z.pct));
}

export function periodLabel(p: number): string {
  return p <= 4 ? `Q${p.toString()}` : `OT${(p - 4).toString()}`;
}

export function clockText(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString()}:${s.toString().padStart(2, "0")}`;
}

export function whenText(s: ShotRow): string {
  return `${periodLabel(s.period)} ${clockText(s.clock_seconds)}`;
}

/** One plain sentence for a shot, built only from the row. */
export function shotSentence(s: ShotRow): string {
  return `${s.player} (${s.team}) ${s.made ? "made" : "missed"} a ${s.shot_distance.toString()}-foot ${s.action_type.toLowerCase()}`;
}

/** Index of the nearest shot to a tap (court coordinates) within maxDist, or -1. */
export function nearestShot(shots: ShotRow[], x: number, y: number, maxDist = 45): number {
  let best = -1;
  let bd = maxDist * maxDist;
  shots.forEach((s, i) => {
    const d = (s.loc_x - x) ** 2 + (s.loc_y - y) ** 2;
    if (d <= bd) {
      bd = d;
      best = i;
    }
  });
  return best;
}

/** Converts a point inside the rendered SVG box to court coordinates. */
export function toCourt(px: number, py: number, width: number, height: number): [number, number] {
  const x = -250 + (px / width) * 500;
  const screenY = -52.5 + (py / height) * 470;
  return [x, TOP - screenY];
}

export function playersOf(shots: ShotRow[]): { id: number; name: string; team: string; n: number }[] {
  const m = new Map<number, { id: number; name: string; team: string; n: number }>();
  for (const s of shots) {
    const e = m.get(s.player_id) ?? { id: s.player_id, name: s.player, team: s.team, n: 0 };
    e.n += 1;
    m.set(s.player_id, e);
  }
  return [...m.values()].sort((a, b) => b.n - a.n || a.name.localeCompare(b.name));
}

/** Official game page on NBA.com. Outbound link only: no NBA video is fetched, stored or embedded. */
export function nbaGameUrl(gameId: string): string {
  return `https://www.nba.com/game/${encodeURIComponent(gameId)}`;
}
