# rung_cal export fields v1 (nba/parlay -> nba/export -> app)

Per prop row in the app export (pts, reb, ast, fg3m), two OPTIONAL fields:
- `book_main_line`: number | null, the newest pre-tip two-sided main line (DraftKings preferred, else FanDuel), stamped before made_at; null when none.
- `rung_cal`: null, or `{"book": "draftkings", "main_line": 24.5, "price_at": "<ISO Z>", "map_version": "rung_cal_map_v1", "map_sha8": "<8 hex>",
   "lines": [{"line": 19.5, "p_over": 0.83}, ...]}`: one entry per half-point line from the prop's 5th to 95th model percentile (rounded
   outward to .5), p_over = the frozen CAL-rung map applied to the model's own P(over) at that line, with the rung's distance from the book
   main line. Monotone non-increasing in line (enforced; a violation drops the whole field and is counted).
Raw model P(over) at any line stays derivable from the existing distribution grid. No EV, no price per rung (rung prices are not held live).
The app labels `rung_cal` values "calibrated using the book's main line (T494)"; lines outside `lines` or props without `rung_cal` are "model only".

## Amendment 2026-10-15 (map build finding)
The map was fit only on rungs with raw model P(over) >= 0.5 (rule ALT_RUNG_CAL's fit set). `lines` therefore contains ONLY lines whose raw
model P(over) >= 0.5; lines below that are outside the fit support and are omitted (the app shows them as "model only"). The export must
supply the continuous 199-point grid's median and spread (`grid_med_spr`) for exact parity with the frozen harness; no fallback from the
integer survival. map_version rung_cal_map_v1, map_sha8 116f4480, DraftKings only.
