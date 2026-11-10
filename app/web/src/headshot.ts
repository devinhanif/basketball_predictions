// Player headshots: the browser loads the NBA's public image for a player id directly. Nothing is copied, proxied or stored
// by this app (Devin's decision, 2026-11-10: the photos are NBA-owned and on a public link; revisit before any revenue).
// Only numeric ids from the data become an address, so no text from anywhere else is ever put in the URL.
export const HEADSHOT_HOST = "https://cdn.nba.com";

export function headshotUrl(playerId: number): string | null {
  if (!Number.isInteger(playerId) || playerId <= 0 || playerId > 99_999_999_999) return null;
  return `${HEADSHOT_HOST}/headshots/nba/latest/260x190/${playerId.toString()}.png`;
}
