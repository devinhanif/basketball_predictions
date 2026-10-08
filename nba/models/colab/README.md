# Rung-4 step heads — Colab training (turnkey)

This box is memory-bound (8GB RAM) and GPU-less (see `NEXT_SESSION.md`),
so the rung-4 neural possession-outcome step heads
(`nba/models/rung4_stepheads.py`) are trained on Google Colab instead.
Everything here is prepared so training is a four-step copy/paste once
you add Colab/GPU credentials — nothing here runs long jobs locally.

Scope reminder (`NEXT_SESSION.md` item 4): this is the possession-outcome
step heads (outcome / zone / make / rebound / duration), **not** the
DeepSets lineup/shooter encoder — that was found to carry negligible
signal in a prior experiment and is out of scope. Once trained, this
becomes a **third router candidate** alongside the possession sim (rung 3)
and the season-average baseline, specifically targeted at **points** (the
one stat the sim doesn't currently beat season-average on).

## Step 1 — export the training set locally

```
uv run python -m nba.models.colab.export_training_data \
    --db nba/db/nba.duckdb \
    --out nba/models/colab/possession_steps.parquet
```

- Opens `nba.duckdb` `read_only=True` (never a write connection — DuckDB
  is single-writer and an eval/backtest may be running concurrently).
- Streams the join (`possessions` + `games` + as-of team ratings) straight
  to parquet via DuckDB's own `COPY ... TO ... (FORMAT PARQUET)` — the
  full result set is never materialized in Python, so this is safe to run
  even over the full multi-season `possessions` table on this box.
- Optional `--start-date`/`--end-date YYYY-MM-DD` to export a smaller
  slice first (e.g. one season) for a quick Colab smoke run before
  committing to the full export.
- Prints the row count. A run over the real multi-season DB should print
  roughly 1M rows (the possession table is ~1.05M rows as of the rung-3
  possession-model docstring); 0 rows means the parser hasn't been run
  against the real DB yet.

The output parquet is **not** committed (no parquet files in git per
CLAUDE.md) — it lives only in your working tree / Drive.

## Step 2 — upload to Colab

Either:
- Upload `possession_steps.parquet` directly into the Colab runtime
  (Colab's file browser, drag-and-drop — fine for files under a few
  hundred MB), or
- Upload to Google Drive and `drive.mount()` + copy the path into
  `PARQUET_PATH` in the notebook's config cell (better for repeat runs —
  avoids re-uploading after a runtime restart).

Also upload `rung4_stepheads.ipynb` itself to Colab (File > Upload
notebook), or open it from Drive.

## Step 3 — run the notebook

Open `rung4_stepheads.ipynb` in Colab. Runtime > Change runtime type > GPU
(T4 is plenty for this model size — a two-layer MLP, not a transformer).
Edit the config cell (`PARQUET_PATH`, `SEED`, `HIDDEN`, `LR`, `N_EPOCHS`,
`BATCH_SIZE`, `HOLDOUT_SEASON`), then Runtime > Run all.

The notebook is fully self-contained: every constant/class it needs
(`OUTCOME_CLASSES`, `StepHeadsNet`, `frame_to_tensors`, `compute_losses`,
etc.) is defined inline, duplicated from (and must be kept in sync with)
`nba/features/possession_step_features.py` and
`nba/models/rung4_stepheads.py` in this repo — no git clone of the private
repo into Colab is required, only the parquet.

Leakage discipline enforced by the notebook (do not change without
re-reading `nba/features/possession_step_features.py`'s module docstring):
- The **frozen final holdout** is the last full season in the export
  (`HOLDOUT_SEASON`, defaults to `df["season"].max()`) — scored once at
  the end, never used to pick hyperparameters.
- The **tunable walk-forward split** (train vs. a within-tunable
  validation window) is chronological by `game_date` — never a random
  split.
- Every feature is either a strictly-prior as-of team rating or
  intra-game state known before the possession's own outcome (see the
  possession-step features module docstring for the no-leakage proof).

At the end it writes, to `rung4_stepheads_artifacts/` in the Colab
runtime:
- `rung4_stepheads_weights.pt` — `state_dict()`, printed size (must stay
  well under the registry's ~100MB cap — a two-layer, 64-hidden-unit MLP
  with five small heads is on the order of tens of KB).
- `config.json` — hyperparameters, feature column order, class label
  order, `feature_mean`/`feature_std` (needed to standardize new data the
  same way at inference time), holdout season.
- `metrics.json` — per-epoch train/val loss history, final losses, frozen
  holdout losses (per head: outcome/zone/make/rebound/duration + total).

Download the whole `rung4_stepheads_artifacts/` folder (zip it in Colab:
`!zip -r artifacts.zip rung4_stepheads_artifacts` then download).

## Step 4 — register the candidate locally

Back on this machine, with the downloaded `rung4_stepheads_artifacts/`
folder placed somewhere local (e.g. `/tmp/rung4_stepheads_artifacts`):

```python
from pathlib import Path
from nba.db.connect import connect
from nba.registry.factory import get_registry

con = connect("nba/db/nba.duckdb")  # NOT read_only: registry writes to `experiments`
registry = get_registry(con)

model_name = "rung4_stepheads"
version = registry.next_version(model_name)  # "v1", "v2", ...

import json

metrics = json.loads(Path("/tmp/rung4_stepheads_artifacts/metrics.json").read_text())
config = json.loads(Path("/tmp/rung4_stepheads_artifacts/config.json").read_text())

registry.log_model(
    model_name=model_name,
    version=version,
    model_path="/tmp/rung4_stepheads_artifacts",  # weights + config + metrics, all < 100MB
    metrics=metrics,
    metadata={"trained_on": "colab", "config": config},
    tags={"rung": 4, "model_method": "pytorch_stepheads", "cold_start_flags": []},
)
# Logs as stage="candidate". Promotion to "production" is a SEPARATE,
# explicit step (CLAUDE.md "promotion is an explicit flag, not automatic")
# and should only happen after the model-gate comparison against the
# current production rung passes:
#   registry.promote(model_name, version)
```

This mirrors the exact `RegistryAdapter` 5-method contract already shipped
in `nba/registry/` (`next_version` / `log_model` / `set_alias` /
`promote` / `load_model`) — no new registry code needed for this rung.

## Pulling the trained weights into `StepHeadsRung`

`nba/models/rung4_stepheads.py::StepHeadsRung` currently trains its own
(tiny, CPU-feasible) net in `fit()` for the local smoke test — it does not
yet load externally-trained weights. To use the Colab-trained weights:

```python
import torch
from nba.models.rung4_stepheads import StepHeadsNet, StepHeadsRung
from nba.features.possession_step_features import POSSESSION_STEP_FEATURE_COLUMNS
import json, numpy as np

config = json.loads(Path("<artifact dir>/config.json").read_text())
net = StepHeadsNet(n_features=len(POSSESSION_STEP_FEATURE_COLUMNS), hidden=config["hidden"])
net.load_state_dict(torch.load("<artifact dir>/rung4_stepheads_weights.pt"))
net.eval()

model = StepHeadsRung(seed=config["seed"], hidden=config["hidden"])
model.net = net
model.feature_mean_ = np.array(config["feature_mean"])
model.feature_std_ = np.array(config["feature_std"])
```

(A small `load_weights(path)` convenience method wrapping the above is a
natural follow-up once real Colab weights exist — not added yet since
there is nothing real to load against today, per the "don't gold-plate"
operating rule.)

## Plugging in as a third router candidate

Once registered, wiring this in as a third points-prop candidate is a
follow-up to `nba/eval/model_routing.py::route_by_bucket` /
`nba/eval/routed_eval.py::evaluate_routing` — add `"rung4_stepheads"` as a
third option alongside the existing `"sim"`/`"season_avg"` candidates for
the `points` stat, and re-run the routing A/B with the usual
hard-no-regression gate (CI must exclude 0 vs. the current best candidate
per bucket) before flipping any flag on. Not done in this change — this
change only ships the training infrastructure.

## What this is NOT (by design, this task)

- Not a full possession-by-possession game simulation using the learned
  heads. `StepHeadsRung.predict()` ships today as a documented
  approximation (predicted per-possession scoring distribution -> expected
  points-per-possession -> Normal-CDF margin win probability at a neutral
  intra-game context) so the rung satisfies the 3-method contract end to
  end; swapping in a true sim integration (feeding the learned outcome/
  zone/make/rebound distributions possession-by-possession into
  `nba.sim.engine`) is a separate, larger follow-up.
- Not run against real data by this change — no multi-minute job was
  launched on this machine (memory-bound, GPU-less). The CPU smoke test
  (`tests/ml/test_rung4_stepheads.py`) proves the code path (forward +
  backward, the 3-method contract, the export script) works end to end on
  the tiny committed fixture only.
