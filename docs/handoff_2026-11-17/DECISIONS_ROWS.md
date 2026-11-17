# Decision rows from the autonomous run of 2026-11-16/17

Rows for Devin to review and copy into `docs/DECISIONS.md` (the main session appends there; this run does not).
Each row: what, why, what would reverse it.

| # | When (CST) | What | Why | What would reverse it |
|---|---|---|---|---|
| 1 | 2026-11-16 18:48 | The run works in fresh worktrees made with `git worktree add` (no `data/` symlink), integration branch `agent/integration-2026-11-17`. The app reads the real export only through an absolute `APP_EXPORT_DIR`, read-only. | A symlinked `data/` would let a test or script write into production state; the app needs only the export files. | A lane that needs more than the export (the replay oracle) gets a DB copy under a scratch directory, never a symlink. |
| 2 | 2026-11-16 18:50 | Ports 8010/8011 (API) and 5183/5184 (web) for this run; the app already running from the main checkout on 8000/5173 was left alone. | It was started at 18:45 from the main checkout and is not this run's process to stop. | Devin stops it; then the documented default ports are free. |
| 3 | 2026-11-16 18:55 | In the single-file artifact, player avatars show initials, not NBA headshots (`headshot.static.ts`, swapped in by `vite.config.artifact.ts`). The served app is unchanged. | The artifact build refuses any external address, and the headshot host made the build fail on `main`. Dropping the address from that build is the conservative way to make it pass; loosening the check would be an outward-facing choice. | Devin decides the artifact may load from `cdn.nba.com`: remove the alias and allow that one host in `scripts/build-artifact.mjs`. |
