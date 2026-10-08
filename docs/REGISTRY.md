# Local model registry: operator guide

State lives in the `experiments` table of `nba.duckdb` plus
`registry_store/<model>/<version>/` (weights, config, report; never data).
All commands: `uv run python -m nba.registry [--db-path P] [--store-dir D] <cmd>`.
Read commands open the DB read-only; write commands take a short write lock,
so run them when no eval is running.

## Daily use
- `make registry` (= `list`): models x versions x stage x key metrics.
- `show <model> [<version> | --alias production]`: tags, metadata, metrics, and
  whether the version is promotable (and why not).
- `compare <model> vA vB`: metric deltas `vB - vA`; log loss / Brier / ECE /
  CRPS are lower-is-better and labelled better/worse.

## Promotion (explicit and manual)
`promote <model> <version>` flips the version to `production` and the previous
production version to `deprecated`. It refuses unless the stored evidence
shows a full multi-season backtest:

1. not a `--scenario` run (`tags.scenario`);
2. at least 1000 scored rows (`metrics.n_games`, or min `n` over props stats);
3. all float metrics finite;
4. either `metrics.backtest_scope == "multi_season"` with `metrics.n_seasons >= 3`,
   or `metadata.game_date_max - game_date_min >= 900` days.

`--scenario` and `--promote` can never be combined (same guard as every
`run_<rung>.py`). Fixture or single-season runs are refused by design.

## One-time bootstrap
`bootstrap-production` registers the GA-tuned MOV-Elo
(`configs/mov_elo_tuned.yaml`) as `rung0_mov_elo`, with walk-forward
out-of-fold metrics recomputed on the tunable seasons (frozen holdout excluded),
then promotes it. The holdout figures (log loss 0.6010, Brier 0.2069) are stored
as documented values from the yaml header and are NOT recomputed (the holdout
touch is spent; see `docs/HOLDOUT_ACCESS_LOG.md`). It also promotes the props
candidate only if it passes the evidence rule and its calibration limits (|bias|
<= 0.5 and 80% interval coverage within 0.10 of 0.80); otherwise it prints
why not. Re-running reuses the existing version (fingerprint of params, seed,
holdout season). `--dry-run` computes and prints without writing.

## Importing an externally trained model (e.g. Colab rung 4)
Put the outputs in one directory: `metrics.json` (required), `config.json`
(or `.yaml`), `weights.pt`, optional `metadata.json` and `report.md`. Include
`backtest_scope`/`n_seasons`/`n_games` in `metrics.json` if you want it
promotable; add `game_date_min`/`game_date_max` to `metadata.json` otherwise.

    uv run python -m nba.registry import-artifacts rung4_deepsets ./colab_out \
        --tags rung=4 model_method=deepsets source_config=configs/rung4.yaml

Copies into `registry_store/rung4_deepsets/vN/`, rejects directories over
100 MB or containing `.parquet`/`.duckdb`, registers as `candidate`.
Re-importing identical content is a no-op. Then `compare` and `promote`.

## Gates
- CI: `make gate` compares the fixture ladder to
  `tests/registry/baselines/fixture_metrics.json` (see `docs/ci_cd.md`).
- Local: `make gate-local` compares the newest candidate of every model that
  has a production version against that real production version, using the
  tighter `nba/registry/model_gate_local_config.yaml` tolerances. Narrow with
  `make gate-local GATE_ARGS="--model rung0_mov_elo --candidate v2"`.
  Exit code 1 on regression. Only flat metrics (log loss, Brier, ECE) and mean
  props CRPS are gated.
