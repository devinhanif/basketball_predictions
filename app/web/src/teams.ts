// Team swatches. Decorative only: a colour next to an abbreviation, never the only way to tell teams apart.
// Dark primaries are swapped for the secondary colour so they stay visible on a dark background.
const COLORS: Record<string, string> = {
  ATL: "#e03a3e",
  BOS: "#00844a",
  BKN: "#6b7078",
  CHA: "#00a3b4",
  CHI: "#ce1141",
  CLE: "#a3174c",
  DAL: "#0f77c4",
  DEN: "#fec524",
  DET: "#d6203a",
  GSW: "#ffc72c",
  HOU: "#ce1141",
  IND: "#fdbb30",
  LAC: "#1d6fd1",
  LAL: "#fdb927",
  MEM: "#5d76a9",
  MIA: "#98002e",
  MIL: "#2f8f4e",
  MIN: "#4a90c9",
  NOP: "#b4975a",
  NYK: "#f58426",
  OKC: "#007ac1",
  ORL: "#0077c0",
  PHI: "#1c78c8",
  PHX: "#e56020",
  POR: "#e03a3e",
  SAC: "#7a4cb0",
  SAS: "#8a949c",
  TOR: "#d4143d",
  UTA: "#f9a01b",
  WAS: "#e31837",
};

export function teamColor(abbr: string): string {
  return COLORS[abbr] ?? "#8a9099";
}
