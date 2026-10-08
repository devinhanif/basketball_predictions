# Colab GPU hand-off workflow

Agents never run Colab. They stage a job into Google Drive; the human opens the notebook and
presses Run all; agents pull the artifacts back and register them as a candidate.

```
 agent                         Drive (synced)                    human / Colab
 make colab-push JOB=x  --->  nba_colab/x/<run_id>/          
   (inputs + staged .ipynb       in.parquet, x__<run_id>.ipynb,   open notebook, T4 GPU,
    + RUN.md)                    RUN.md                    --->   Runtime > Run all
 make colab-status      <---  nba_colab/x/<run_id>/artifacts/<ts>/ (weights.pt, config.json,
 make colab-pull JOB=x         metrics.json)              <---   written by the notebook
   [COLAB_ARGS=--register]  -> data/colab/runs/x/<run_id>/ ; registry candidate (never promoted)
```

## One-time setup
1. Install Google Drive for desktop, sign in. It mounts at
   `~/Library/CloudStorage/GoogleDrive-<account>/My Drive/` (auto-detected; override with
   `NBA_COLAB_DRIVE_DIR=<path to My Drive>`; the CLI fails with a clear message if absent).
2. Stream vs Mirror: Stream is fine. After the first push, right-click `nba_colab` in Finder
   and choose Offline access > Available offline so artifacts download without being opened.
3. Drive sync can lag; `status` shows "waiting" until `metrics.json` lands.

## Commands
- `uv run python -m nba.colab jobs` - list jobs.
- `make colab-push JOB=rung4_stepheads` - run producer command if the input is missing or older
  than its `depends_on` (read-only DB), copy inputs, write a run-stamped notebook and `RUN.md`
  (exact clicks for browser and VS Code), print them.
- `make colab-status [JOB=...]`, `make colab-pull JOB=... [RUN=<run_id>] [COLAB_ARGS=--register]`.
  Pull checks the expected files exist and metrics are finite, copies to
  `data/colab/runs/<job>/<run_id>/`, prints per-head skill. `--register` calls
  `python -m nba.registry import-artifacts` (stage=candidate). Promote manually, never from here.

## Staged-notebook behavior
`push` inserts a config cell before the notebook's first code cell. It finds the run folder
(Drive mount first, then the notebook's own folder), sets `NBA_PARQUET`, `NBA_ARTIFACT_ROOT`
and forced env (e.g. `NBA_SCORE_HOLDOUT`), and makes `drive.mount` non-fatal so VS Code's Colab
kernel can use files next to the notebook. The original notebook in the repo is never edited.
If the VS Code kernel cannot mount Drive, artifacts land on the Colab VM and must be downloaded
into `<run>/artifacts/<ts>/`; the browser route avoids this.

## Holdout rule
`job.yaml` has `touches_holdout` (default false) and `preregistration_id`. `touches_holdout: true`
without an id is rejected at load. When false the staged copy forces `NBA_SCORE_HOLDOUT=False`
(for `rung4_stepheads`, whose notebook default is True; the 2025 holdout is already spent).

## Creating a job
1. `nba/colab/jobs/<name>/job.yaml` (directory name == `name`):
   `description`, `notebook` (repo-relative), `inputs` (`path`, `produce` command, optional
   `depends_on`), `artifacts` (must include `metrics.json`), `registry: {model_name, tags}`,
   `touches_holdout`, `preregistration_id`, optional `env`.
2. The notebook must honor `NBA_PARQUET` (first input) and `NBA_ARTIFACT_ROOT`, and write
   `<root>/<timestamp>/` with the listed artifacts. Keep weights under ~100 MB.
3. `uv run python -m nba.colab push <name>` against a temp `NBA_COLAB_DRIVE_DIR` to dry-run.

## Colab or local?
Use Colab for neural training that exceeds ~10 min of CPU or needs more than the 8 GB Mac
allows. CPU-bound sims, LightGBM, and feature builds stay local. (The rung-4 step heads fit in
about 50 s on CPU at hidden 128; Colab only pays off if the model grows.)
