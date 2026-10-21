# line_cal export field v1 (nba/parlay -> nba/export -> app)

Per prop row in the app export (`prop_grids`; pts, reb, ast, fg3m), two OPTIONAL, additive columns. `rung_cal` and `book_main_line`
are unchanged; `schema_version` stays 2.

- `line_cal`: VARCHAR JSON or NULL.
  `{"book": "draftkings", "main_line": 24.5, "price_at": "<ISO Z>", "p_over_raw": 0.462, "p_over_cal": 0.485, "map": "LINE_CALIBRATION", "map_sha8": "443743a1", "fit_through": "2025-06-30"}`
- `line_cal_null_reason`: VARCHAR or NULL. NULL exactly when `line_cal` is set; otherwise one of the reasons below.

What it is: the frozen LINE_CALIBRATION CAL map (T493, `nba/parlay/line_cal_map_v1.json`, terms `[1, x, z, bench, bench*x]`) applied to the
production model's P(over) at the DraftKings main line. It is the parlay producer's `p_cal` for that leg. Unlike `rung_cal` (ALT_RUNG_CAL,
fit only where raw P(over) >= 0.5) it has no support restriction, so it is present at the main line whatever the raw probability is.
Display only: no EV, no price, no side. P(over) of the under leg is 1 - p_over_cal.

Exactness (for the same production row and the same DraftKings line, the card equals the panel's `p_cal` bit for bit):
- `p_over_raw` = stored `p_ge_full` counts / n at ceil(main_line) (`top_source.p_over`); `p_over_cal` = `CalMap.apply("draftkings", stat,
  p_over_raw, main_line, q50, (q75 - q25) / 1.349, starter_rate)` with (q25, q50, q75) from the production row's stored 19-point `q_grid`
  (`top_source.quartiles`), clipped by the map exactly as in the producer. A test compares both code paths bit for bit.
- The persisted 199-point grid is only a reproduction check (it must reproduce the row's `p_ge_full` exactly, else `grid_mismatch`); its
  quantiles differ from the 19-point grid by up to about 0.005, so it is not the median/spread source here (it is for `rung_cal`).
- Built from the production row (`props_context_residual`) only, even when the displayed primary is AVG; AVG grids are never used.
- One value per (game, player, stat), computed from the PRODUCTION row's own run and made_at (quote received at or before that made_at, `<=`, and before tip) and copied to every row of the prop, so a later row (t15, int, AVG) never carries a line newer than the forecast whose P(over) is used. `main_line`/`price_at`: the newest two-sided non-alternate DraftKings pair under that cutoff.
- `map_sha8`/`fit_through` are read from the loaded map file (its sha is verified on load; a fit after 2025-06-30 is refused).

Fixed null reasons (counted in `manifest.line_cal.null_reasons`, and written per row in `line_cal_null_reason`): `no_book_line`,
`ambiguous_main_line`, `no_production_row`, `no_production_grid`, `grid_mismatch`, `no_survival`, `no_starter_rate`, `map_unavailable`
(map file unreadable or no DraftKings cell for the stat), `no_quartiles` (no usable stored q_grid), `whole_number_line` (the producer
also skips whole-number lines). Reasons are checked in that order of dependency; one reason per row. Manifest also carries `n_props`,
`n_line_cal`, `map_sha8`, `fit_through`; `n_line_cal + sum(null_reasons) == n_props`.

The producer's other leg filters (p_play floor, price present) are offer rules, not part of this display field.
The app labels it "calibrated (T493)"; when NULL it names `line_cal_null_reason`.

Known non-parity with the panel (the value can differ or be absent on a leg the panel builds, or the reverse):
(a) the producer takes every line in the newest board snapshot per (book, market, game) at or before production's made_at, and a player the
newest board dropped has no leg; the export takes the newest two-sided pair per player, so it can still show an older line;
(b) the producer compares `<=` for the board snapshot; the export's `<=` applies to the pair timestamp of the player's own quotes;
(c) the export requires a persisted 199-point grid reproducing `p_ge_full` (deliberate integrity check); a recency-routed row is a panel leg
but `no_production_grid` here;
(d) the producer reads only rows and quotes at or before its run clock (`now`); the export has no `now`, so a panel built at 17:00 and an
export built at 23:00 can use different production rows.

Older exports (built before this field) have neither `line_cal` nor `line_cal_null_reason`; a reader treats a missing column as NULL
(no calibrated value, no reason). The manifest key `line_cal` is likewise absent in older manifests. Counts per row are in `n_props`,
`n_line_cal`, `null_reasons`; per distinct prop in `n_props_distinct`, `n_line_cal_distinct`, `null_reasons_distinct` (a prop with several
rows shares one value).
