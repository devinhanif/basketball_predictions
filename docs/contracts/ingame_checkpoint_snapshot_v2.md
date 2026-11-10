# In-game checkpoint snapshot, v2 (JARVIS writes it, the app only reads it)

Status 2026-11-10: the writer is built (`nba/markets/checkpoint_snapshot.py`, called from
`nba/jarvis/ingame_checkpoints.py::score_checkpoints`); v1 of this contract is
`docs/contracts/ingame_checkpoint_snapshot_v1.md` and is unchanged: every v1 field keeps its name, type and meaning,
the file just says `"version": 2` and each game MAY carry one extra key, `script`. A v1 reader that ignores unknown
keys keeps working. Everything below is additive.

Path, atomicity, size, caching and the Learn-lens rule are v1's: `data/jarvis/ingame_checkpoints_latest.json`
(override with `ingame_checkpoints.snapshot` in `configs/jarvis.yaml`), written to a temp file in the same directory and
renamed into place, limit 5 MB, the app caches 15 s per process and never writes it. The file holds no price, ask, bid,
fee or edge (a test greps for them).

## When it is written

Once per JARVIS tick, after the checkpoint rows of that tick are stored, only when `ingame_checkpoints.enabled` is true
(the writer is never reached otherwise) and never in a dry run. The write is contained: any exception comes back as
`{"ok": false, "error": ...}` in the tick report (`ingame_checkpoints.snapshot`) and costs only the snapshot. It does not
take the checkpoint store's lock (the rows were already read), so it cannot block or fail the tick. A game appears when
it has stored checkpoint rows (the v1 rule); a game in play with no Kalshi quotes has no entry and no script block.

## The `script` block (new)

```json
"script": {
  "status": "exploratory",
  "method": "independent_increments_v1",
  "label": "EXPLORATORY and UNPROVEN: script chances from the live score and clock on the pre-game game model (independent increments; pace is not extrapolated). No live record yet.",
  "elapsed_min": 25.0, "margin": 22, "total": 102, "state_at": "2026-11-10T01:40:00+00:00",
  "home_abbr": "PHI", "away_abbr": "DET",
  "pregame": {"mu_m": 3.0, "sd_m": 13.63, "mu_t": 228.0, "sd_t": 18.82},
  "p":     {"blowout": 0.93, "close": 0.01, "shootout": 0.12, "grind": 0.21},
  "prior": {"blowout": 0.40, "close": 0.19, "shootout": 0.27, "grind": 0.27},
  "rules": {"blowout": "|final margin| >= 15", "close": "|final margin| <= 5", "shootout": "final total >= 241", "grind": "final total <= 216"}
}
```

- `p`: P(script) given the score and the clock NOW, 4 decimals. `prior`: the same four numbers before the game (the
  `scripts` block of `combo_<night>.json`). Show both: the movement is the information.
- `margin` = home minus away points, `total` = points scored so far, `elapsed_min` = regulation minutes played (from the
  ESPN summary's period and clock), `state_at` = when the capture received that summary (the number is that old).
- `pregame` and `rules` come from the night's `combo_<night>.json` (`data/export/latest/`; the directory is
  `ingame_checkpoints.script_priors_dir`). If that file is missing, the game has no entry in it (matched by `AWAY@HOME`
  abbreviations, never guessed), the score is missing, the game is in overtime or past regulation, then the block is
  ABSENT and the rest of the entry is unchanged. The app shows nothing in place of it, not a guess.
- The block refreshes every tick (the newest summary the live capture holds), not only on a 5-minute checkpoint; the
  checkpoint rows next to it still update only at their checkpoints. `elapsed_min` and `state_at` say which state the
  number is from.

### Method, plainly (so nobody mistakes it for more)

With `tau = elapsed / 48` and `rem = max(1 - tau, 0.005)`: the final margin is Normal(`margin + mu_m * rem`,
`sd_m^2 * rem`) and the final total Normal(`total + mu_t * rem`, `sd_t^2 * rem`); `mu_m, sd_m, mu_t, sd_t` are the
pre-game numbers of the game model (`docs/PARLAY_ENGINE.md`). The scripts are the combo contract's definitions on those
normals with the same half-point corrections as the pre-game prior. Properties, all tested: at tip the numbers equal the
prior exactly; as the clock runs out they collapse onto the score; a big lead raises blowout and lowers close; a
fast first half raises shootout and lowers grind.

What it is not: validated. The model is independent increments (Stern 1994), a textbook baseline, with the variance of
the rest of the game proportional to the time left. Observed pace is NOT extrapolated beyond the points already scored,
so a hot or cold start is treated as noise; real pace persists, so shootout / grind are probably under-reacting. It has
not been checked against play-by-play and has no live record (the forward test is a separate, pre-registered piece,
DECISIONS 2026-11-10 12:52). Every number carries "exploratory" and the `label` above. Overtime is out of scope.
