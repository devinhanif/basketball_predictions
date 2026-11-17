// The single-file artifact's stand-in for headshot.ts (swapped in by vite.config.artifact.ts, like transport.static.ts).
// The artifact page may hold no external address at all (scripts/build-artifact.mjs refuses one), so there is no remote
// headshot there: Avatar falls back to an uploaded photo, then initials on the team colour.
export const HEADSHOT_HOST = "";

/** Same call shape as headshot.ts; no address is ever made here. */
export const headshotUrl: (playerId: number) => string | null = () => null;
