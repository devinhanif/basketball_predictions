# Rung-4 step heads -- Colab training (Drive flow)

This box is 8GB / GPU-less, so the rung-4 possession-outcome heads
(`nba/models/rung4_stepheads.py`: outcome, zone, make, rebound, duration; no
shooter/DeepSets head) are trained on Colab. Target use: a third points-prop
router candidate. Holdout discipline: `docs/ACCEPTANCE_CRITERIA_2026-10-08.md`
section 3.

## What you click

1. **Pre-register** the one confirmatory `season=2025` evaluation in
   `docs/HOLDOUT_ACCESS_LOG.md` (before running; it is scored once, at the end).
2. In Google Drive create `My Drive/nba_colab/` and upload
   `data/colab/possession_steps.parquet` (about 7.5 MB, ~1.05M rows, seasons
   2022-2025) into it. (Re-export: `uv run python -m nba.models.colab.export_training_data
   --db nba/db/nba.duckdb --out data/colab/possession_steps.parquet`.)
3. Upload `nba/models/colab/rung4_stepheads.ipynb` to Colab (File > Upload) or
   open it from Drive.
4. Runtime > Change runtime type > **T4 GPU** > Save.
5. Runtime > **Run all**. Approve the Drive-mount popup. It takes a few minutes.
6. Results appear in `My Drive/nba_colab/artifacts/<timestamp>/`
   (`weights.pt`, `config.json`, `metrics.json`). Sync/download that folder.

No GPU still works (tiny model) but the first cell prints a loud warning.

## What it does

- Reads the raw parquet and derives labels inline (`made_shot`, `duration_s`
  clamped to [0, 60]s, zone masked to shot attempts) -- the same code as
  `possession_step_features.derive_step_labels`; a test keeps them in sync.
- Holdout = `HOLDOUT_SEASON = 2025` (explicit, not `season.max()`). Training
  and early stopping use `season < 2025` only; validation = last 15% of dates
  of that window (chronological). Seed is logged (`SEED = 0`).
- `metrics.json` has, per head, the net's loss vs a frequency baseline built
  from TRAIN-window class priors (log loss for outcome/zone/make/rebound, MSE
  for duration; `skill = 1 - net/baseline`, positive means the net learned
  something), for both validation (`val_heads`) and the holdout
  (`holdout_heads`). Expect small skill: a local 1-epoch dry run on 2022-2024
  gave about +0.2% outcome, +0.4% make, ~0 zone/rebound, duration worse than
  mean until trained longer.

## Back on this machine

```python
from nba.models.rung4_stepheads import StepHeadsRung
model = StepHeadsRung.load_weights("path/to/artifacts/<timestamp>")
```

Registering the candidate (never promote automatically):

```python
import json
from pathlib import Path
from nba.db.connect import connect
from nba.registry.factory import get_registry

art = Path("path/to/artifacts/<timestamp>")
registry = get_registry(connect("nba/db/nba.duckdb"))  # writes `experiments`
version = registry.next_version("rung4_stepheads")
registry.log_model(
    model_name="rung4_stepheads", version=version, model_path=str(art),
    metrics=json.loads((art / "metrics.json").read_text()),
    metadata={"trained_on": "colab", "config": json.loads((art / "config.json").read_text())},
    tags={"rung": 4, "model_method": "pytorch_stepheads", "cold_start_flags": []},
)
```

`StepHeadsRung.predict()` is still the documented Normal-CDF approximation
(not a possession-by-possession sim); wiring the heads into `nba.sim.engine`
and the points router is a separate follow-up gated by the usual paired
bootstrap.
