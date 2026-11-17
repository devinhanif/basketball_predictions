# NBA Prediction: Project Status

_Living document. Rewritten 2026-11-16 (evening, Central) from a read-only survey of `main` at `2cc4e5c9` (the "2026-11-16 survey"), re-checked the same night against `ops/`, `configs/`, `~/Library/LaunchAgents` and `docs/`. Anything marked "per the survey" was not re-checked. Anything with an "as of" date will age: read the date before trusting it. The ledger (`docs/TEST_LEDGER.md`) wins on facts; `CLAUDE.md` wins on how we work._

## Bottom line (as of 2026-11-16)

The market beats the model on game winners and on every props cell measured so far. The 2025-26 replay put the model's Brier score 0.0064 worse than Kalshi's price (`CLAUDE.md`); the ODDS_HISTORY comparison (T203-T237, 2023-25) found the market better on every props cell. The parlay engine's model weight has been **0** since 2026-11-02 (paper track record flat: n = 17,571, paired Brier gain +0.0006 with lower bound -0.0024; the upper bound is cut off in `docs/NEXT_SESSION.md`), so the fair price the tools show is the market's, not ours. The honest answer to "what should I bet?" is **keep your money** until the live forward log proves skill. Nothing in the repo places orders. The live 2026-27 forward log is the only clean test; season 2025 is burned.

Production has not changed since the October restructure. The newest commit on `main` is `2cc4e5c9`, dated 2026-11-10 (checked with `git log` at 19:03 CST on 11-16).

## 1. In production

| Target | Model | What it is | Headline (out-of-fold 2022-24, real-tip gating) |
|---|---|---|---|
| Game winner | `rung0_injury_elo` v2 | MOV-Elo logit plus terms for players OUT or DOUBTFUL on the official injury report, using the latest report at least 60 min before the real tip; refit every run. Fallback and comparison: `rung0_mov_elo` v1 | Log loss vs MOV-Elo -0.0081 [-0.0121, -0.0038], n = 3,951 games (T128). Win accuracy about 64%, calibrated |
| Player props: pts, reb, ast, fg3m | `props_context_residual` v2 | Recency-weighted mean, a LightGBM residual mean head and a LightGBM scale head with conformal residuals; stores a 19-point quantile grid, `p_play` and projected minutes | CRPS vs `recency_conformal`: pts -0.0862, reb -0.0313, ast -0.0229, fg3m -0.0102 (T092-T095, reproduced in T128; clustered CIs in the ledger) |
| Minutes and P(play) | `nba/props/minutes.py` hurdle model | Picks the roster; stored inside every prop row; not scored as its own target | n/a |

- These are backtests, not live results. A model that beats a baseline can still lose to the market, and this one does.
- The 2025 holdout figures on v1 are not re-measurable and are history only. Red team (`redteam_production_2026-10-09`): injury-Elo SURVIVES; context-residual WOUNDED (minor, the proxy defect, since fixed).
- About a third of primary prop rows settle as DNP (per the survey). Most prop error is minutes.

## 2. Shadow and forward arms (logged nightly, not used for decisions)

The pretip and early-forecast commands in `ops/nba_daily.sh` pass `--log-int-variant --log-lower-tail-variant --log-pplay-shadow --log-full-shadow --log-avg-shadow --log-combo-shadow` (checked 2026-11-16).

| Arm | What | Notes |
|---|---|---|
| `props_context_residual_int` | integer-support quantiles | a scoring-convention gain, not model skill (2023 OOF, T124-T127) |
| `props_context_residual_lt` | pts short-minutes mixture | passed 2023 and 2024 (T136-T159) |
| `props_context_residual_t30`, `_t15` | confirmed-lineup arms at T-30 and T-15 | run by the 5-minute lineups tick. Per the survey, 211 of 212 T-30 decisions were skipped because lineups are rarely confirmed by T-30, while T-15 logged 172 of 200 (as of 2026-11-16) |
| `props_context_residual_late` | T-15 injury report, OUT and DOUBTFUL both excluded | per the survey |
| pplay / full / avg / combo shadows | own tables `forward_{pplay,full,avg,combo}_shadow` | AVG is a 50/50 pmf mix of a torch sequence model and FULL; weights are trained monthly on Colab |
| `llm_shadow` | Claude via `claude -p` | off, 0 rows (per the survey; `NBA_LLM_SHADOW` is not in the lineups plist) |

## 3. Frozen forward tests and their scheduled reads

Rules live in `docs/FORWARD_PREREG_2026_27.md` and `docs/prereg/`. A read happens once, on the date or size the rule names; nothing is read early.

| Test | Scheduled read (as of 2026-11-16) |
|---|---|
| `FORWARD_PREREG_2026_27` (T30, INT, LT arms; win-model tripwire) | looks with alpha spending (its section 5) |
| `LINE_CAL_LIVE` (T524) | one look on the first date the window holds at least 200 games and 5,000 legs, projected 2026-12-01; backstop 2027-01-31 (`docs/prereg/LINE_CAL_LIVE.md`). The app hides raw outcome vs chance until then |
| `BEST_PRICE_FORWARD` | forward window opened after its freeze; per the survey, 2026-11-10 |
| `LINE_MOVE_TOWARD_MODEL` (T510) | per the survey, at least 300 markets and 14 dates |
| `FAV_DIP_PAPER` (T511) | scored once at 100 trades over 14 distinct dates (`docs/prereg/FAV_DIP_PAPER.md`) |
| `STAR_FLOOR_PAPER` (T514) | scored once at 100 settled trades over 14 distinct dates (`docs/prereg/STAR_FLOOR_PAPER.md`); no result yet |
| `INGAME_WINPROB_V2` (T513) | stage 1 (per the survey; not re-checked) |
| `AVG_SHADOW_LIVE_2` | per the survey, about 2027-01-10; stale weights push it toward INCONCLUSIVE (section 7) |
| `FULL_SHADOW_LIVE_2026_27` | kill-only interim at 7,300 rows, final look at 14,600 rows (`docs/prereg/FULL_SHADOW_LIVE_2026_27.md`); per the survey, about Feb 2027 |
| `COMBO_STOCKS_SHADOW` | later in the season |
| `GAME_SCRIPTS_FORWARD` rev 2 | drafted (rule `563a1c67`); the freeze waits for its branch to merge (DECISIONS 2026-11-10 16:29) |

## 4. What runs automatically (launchd, one Mac, as of 2026-11-16)

Every job runs the main checkout through `/opt/homebrew/bin/uv run --no-sync`, except where noted. Plist hours are on launchd's cached clock, which on this Mac is a fixed UTC-4 clock (see "Clock"). "Installed" means a plist is in `~/Library/LaunchAgents` (listed 2026-11-16); whether each is currently loaded was not checked.

| Job | Schedule | Does | Source |
|---|---|---|---|
| `local.nba.daily-pretip` | :20 and :50, hours 09-22 (28 slots, 09:20 to 22:50) | `ops/nba_daily.sh pretip`: `nba.daily run` with the shadow flags, `nba.explain slate` (report edition), referees, `nba.markets capture`, `nba.parlay evaluate` and `analyze`, `nba.ops.coverage` | `ops/install_launchd.sh`, `ops/nba_daily.sh` |
| `local.nba.daily-morning` | 08:00; `NBA_FACTS_NIGHTLY` is set in the installed plist | `ops/nba_daily.sh morning`: ingest, settle, checkpoint, report, early forecast and early briefing, yesterday's coverage, scorecard, facts rebuild, parlay settle, post-game fetch and load, stints, datamanifest snapshot/diff/check, DB backup (7 kept) and Drive copies | same |
| `local.nba.lineups` | every 300 s | lineup poll inside [tip-60, tip+5); sportsbook odds capture when `NBA_CLOSE_CAPTURE=1`; LLM shadow if enabled; `run-t30` and `run-t15` when due | `ops/nba_lineups.sh` |
| `local.nba.pulls` | every 120 s | `nba.pulls` tick: serves Pinnacle refresh requests within budget | `ops/launchd/local.nba.pulls.plist`; `ops/install_launchd.sh` also writes it (defined twice, per the survey) |
| `local.nba.watchdog` | every 600 s | `nba.ops.watchdog check` | `ops/install_launchd.sh` |
| `local.nba.jarvis` | every 120 s | `nba.jarvis watch --once` (Discord posts, paper book) | `ops/launchd/local.nba.jarvis.plist` |
| `local.nba.app-export` | :05 and :35, hours 12-23 | `nba.ops.app_export`: rebuilds `data/export/latest`, gated on a clean morning run | `ops/launchd/local.nba.app-export.plist` |
| `local.nba.live-capture-start` | every 600 s | starts `nba.live capture` during game windows | `ops/launchd/local.nba.live-capture-start.plist` |
| `local.nba.kalshi-candles` | every 600 s | starts the 15-second Kalshi quote poller | `ops/launchd/local.nba.kalshi-candles.plist` |
| `local.nba.kalshi-snapshot` | every 900 s | `nba.kalshi snapshot` into `data/kalshi/kalshi.duckdb` | **only in `~/Library/LaunchAgents`; no plist in the repo, and it runs `uv run` without `--no-sync`** |
| `local.nba.fastarb` | none | cross-venue arbitrage alerts | not installed (template only; needs `.env.fastarb`, which does not exist per the survey) |
| `local.nba.reporters` | none | X reporter feed | not installed (sketch; empty allow-list per the survey) |

- **Clock.** launchd fires on a fixed UTC-4 clock, so since the 2026-11-01 fall back the jobs run an hour earlier on the Eastern wall clock than the plists read. `data/ops/schedule_tz` contains `Etc/GMT+4` (read 2026-11-16) and tells the watchdog. After a reboot or relogin, delete that file by hand and recheck the first morning start (`docs/DAILY_PIPELINE.md`, "launchd calendar timezone").
- **Roster source.** `ops/roster_source.sh` uses official rosters through **2026-11-30**, then recent-games rosters. Whether to keep that flip is a decision for Devin (section 8).
- **Guards.** One job at a time (`data/ops/lock`, stale after 3 h); `nba.duckdb` is single-writer with a bounded wait; per-step timeouts; failures go to `data/ops/ALERTS.md` and macOS notifications. Exit 4 is informational, 5 degraded, 75 database busy. Details: `docs/DAILY_PIPELINE.md`.

## 5. Market layer (read-only; no orders, no credentials)

- **Kalshi** public API: snapshot every 15 min plus the 15-second poller for the app's live chart. **Prop DNP rule: verified 2026-11-10** from the contract terms. A player who withdraws before tip settles at the last fair price, not as No (DECISIONS 2026-11-10 13:20 and 16:36; merge `e921f7ed`). Props are priced as P(plays) times P(side | plays). Unders are shown on cards and never alerted: all three replayed under alerts were Pinnacle lagging news Kalshi had already priced, and 14 of 17 shown unders saw Pinnacle move toward Kalshi (0.82, game-clustered 95% CI [0.68, 1.00], 11 games; `docs/reviews/cards_unders_2026-11-10.md`).
- **Sportsbook odds** from the-odds-api.com v4 (`THE_ODDS_API_KEY`; Pinnacle in the `eu` region), captured by the lineups tick into `data/markets/market_asof.duckdb`. The earlier vendor (theoddsapi.com) was cancelled 2026-10-10 and its pretip step removed.
- **Parlay engine** (`docs/PARLAY_ENGINE.md`): Gaussian copula; model weight 0, so verdicts follow the market. `no_positive_ev_found` is a first-class answer.
- **JARVIS** (Discord, paper book, alert-only): labels edges strong (3 cents or more after fees), thin, none or arbitrage. Per-stat model weights start at 0 and are earned from closing-line value. Stake rules are Devin's, in `docs/TRADING_CONTEXT.md`.
- **fastarb** (arbitrage alerts) is built but not installed.
- Fee model and settlement rules: `configs/parlay.yaml`, `docs/PARLAY_ENGINE.md`.

## 6. The app

`app/` ("parlayhitters"): a read-only FastAPI service and a Preact PWA that read only the nightly export (`nba.export build`, then `nba.ops.app_export`). Not deployed; per the survey, Devin shared a static snapshot privately. Run instructions: `app/README.md`. `docs/APP_ARCHITECTURE.md` describes a planned hosting setup that does not exist (per the survey) and is stale; it is not edited here. Per the survey, a display lock hides model chance beside outcomes for slates from 2026-10-30 until the `LINE_CAL_LIVE` look.

## 7. Known live problems (as of 2026-11-16 evening)

1. **Odds key.** Per the survey, the-odds-api key was deactivated from 11-14, every capture returned `401 DEACTIVATED_KEY`, and there were no sportsbook or Pinnacle prices for about 3 nights; without Pinnacle, JARVIS labels collapse to thin or none. Devin replaced the key in `.env` at about 18:22 CST on 11-16. **Not yet confirmed.** `data/ops/ALERTS.md` shows `[lineups] odds capture all rc=4` every 5 min through 18:19 and no later such line by 19:03; whether rows are being written was not checked.
2. **AVG shadow serves October weights** (per the survey): the November Colab refit has not run, rows are marked `stale_model=1`, and each stale day pushes that test toward INCONCLUSIVE.
3. **CI is not green.** Per the survey, none of the last 40 runs on `main` passed (35 cancelled, 5 failed; the latest failure, 11-11, was `ruff check`, and the unit job ran over an hour). The repo's own checks, run in a dev environment, are the real gate until CI is fixed.
4. **Battery near tip.** The watchdog wrote `ACTION NEEDED: next tip in 57 min and the Mac can sleep` at 19:02 CST on 11-16 (`data/ops/ALERTS.md`). A sleeping Mac stops every job.
5. **Daily outputs pile up uncommitted** in the main checkout (reports and the data manifest; `git status` at the start of this session listed dozens of untracked `reports/forward_scorecard/*` files). No commit to `main` since 2026-11-10.
6. **Unmatched Kalshi names**: about 63 as of 11-08 (`docs/NEXT_SESSION.md`), so those players' props never meet the model.
7. **Disk** (per the survey): 57% used after a cleanup (it was 97% on 11-10); about 26 GB in `data/ops`, including two pretip logs of about 9 GB each.

## 8. Decisions waiting on Devin

From `docs/NEXT_SESSION.md` (2026-11-10 block) and the survey. None was re-confirmed as still open tonight.

- AVG November refit (Colab run, about 5 minutes).
- Launchd installs: the afternoon re-export schedule and, optionally, the candles poller (Claude is refused installs). Both plists are already in `~/Library/LaunchAgents`, so this may be done.
- The roster-source flip on 2026-11-30: keep it or change it.
- The T-30 arm's future (it cannot change mid-rule; it needs a DECISIONS row for the next rule).
- D1 tripwire audit content; the AS_OF_IDENTITY gate.
- X API spend ceiling for the reporter feed, or archive it.
- Delete `data/raw/bref_salaries_2026-10-21`; re-source coaches.
- Polymarket terms read and steps 2-3 of SHARP_WALLETS_PERSIST.
- TEAMMATE_SITS display choice (O2).
- The `GAME_SCRIPTS_FORWARD` freeze, after its branch merges.
- Data-source terms (NBA headshots, ESPN play-by-play, nba.com) before any revenue.
- Plug the Mac in and keep it awake on game nights.

## 9. What the experiments taught (short; numbers and CIs are in `docs/TEST_LEDGER.md`)

- Wins came from information known before tip: the official injury report with real-tip gating, confirmed lineups (backtest; live they rarely confirm by T-30), short-minutes behavior, integer-support scoring, and TEAMMATE_SITS (T526: pts survives attack; reb is wounded).
- Architecture alone lost or tied: possession simulator for props, neural step-heads, RAPM, set transformer, play-by-play GPT, tracking features, opponent ridge, stacking, four factors, referee tendencies, minutes hazard models, workload features (T521, null).
- Closed nulls are results, not failures. No variant-chasing.

## 10. Where to look

`docs/NEXT_SESSION.md` (resume here, newest block on top), `docs/DAILY_PIPELINE.md`, `docs/DECISIONS.md`, `docs/TEST_LEDGER.md`, `docs/HOLDOUT_ACCESS_LOG.md`, `docs/BEST_PRACTICES.md`, `docs/FORWARD_PREREG_2026_27.md`.
