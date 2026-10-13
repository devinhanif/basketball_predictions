# ruff: noqa
# mypy: ignore-errors
# ----------------------------------------------------------------------------------------
# T478 era-embedding probe (reports/t478_era_probe/PLAN.md). Appended by build_notebook.py AFTER the SEQ_FULL_MIX_RT tail (sel mode), which sits on the model code of
# ../seq_props/seq_props_train.py. The rt tail's `run_walk_forward` is called UNCHANGED (program WALL_E2, seed 0 only, test seasons 2023-2024): the only additions are a
# wrapper around `fit` that remembers the network and tensors, and the dump of head-input states of the FINAL model (after the last warm refit), as ENSEMBLE did for E3.
# Season 2025 cannot be read: the base loader refuses it and this file asserts it again. Scores nothing.
# ----------------------------------------------------------------------------------------
STATE_PER_ERA = 6000
PROBE_SEASONS = (2019, 2020, 2021, 2022, 2023, 2024)
MAX_SEASON = 2024  # as ENSEMBLE's committed P-emb: select 2023-24 and report-season 2024-25; season 2025 (holdout) is refused
TEST_SEASONS = (2023, 2024)
SEEDS = (
    0,
)  # net 0 of the 3-seed run: same cold init and warm seeds as in run #1 (cold fit seed 0, warm seed 1000)
PLAN_SHA = "__PLAN_SHA__"
ERA_COLS = ("era_bubble", "era_nofans", "era_limited")

_orig_fit = globals().get("_orig_fit", fit)
_CAP = {}


def fit(net, ts, train_rows, val_rows, cfg, dev, lr, epochs, patience):  # noqa: F811
    _CAP["net"], _CAP["ts"] = net, ts
    return _orig_fit(net, ts, train_rows, val_rows, cfg, dev, lr, epochs, patience)


def drop_after(d, last):
    """Remove every row of a season after `last` (the 2024-25 rows of the selection export), from every per-row array."""
    keep = d["season"] <= last
    n = len(keep)
    return {
        k: (
            v[keep] if (k in ROW_KEYS or k == "date_str") and np.asarray(v).shape[:1] == (n,) else v
        )
        for k, v in d.items()
    }


def probe_extract_states(net, ts, rows, batch, dev):
    store = []
    h = net.head[0].register_forward_pre_hook(lambda m, i: store.append(i[0].float().cpu().numpy()))
    try:
        predict(net, ts, rows, batch, dev)
    finally:
        h.remove()
    return np.concatenate(store)


def probe_sample(d, rng):
    keep = []
    for s in PROBE_SEASONS:
        w = np.flatnonzero((d["season"] == s) & (d["n_prior"] >= 5))
        if len(w):
            keep.append(np.sort(rng.choice(w, size=min(len(w), STATE_PER_ERA), replace=False)))
    return np.concatenate(keep) if keep else np.zeros(0, dtype=np.int64)


def main_probe():
    smoke = os.environ.get("NBA_SMOKE")
    hidden = int(os.environ.get("NBA_HIDDEN", 16 if smoke else 64))
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print(
        "T478 era probe; plan sha256",
        PLAN_SHA,
        "seed 0 only; expected: about 5 T4 minutes (4-9)",
        flush=True,
    )
    cur_path = Path(os.environ.get("NBA_PARQUET", "seq_ensemble_rt_cur.npz"))
    hist_path = cur_path.parent / "seq_ensemble_rt_hist.npz"
    export_sha, hist_sha = file_sha256(cur_path), file_sha256(hist_path)
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cur, hist_d = load_npz_25(cur_path), load_npz_25(hist_path)
    if smoke:
        cur, hist_d = subsample(cur, int(smoke)), subsample(hist_d, int(smoke))
    cur = drop_after(cur, MAX_SEASON)
    assert int(cur["season"].max()) <= MAX_SEASON and int(hist_d["season"].max()) <= 2021, (
        "the probe reads seasons <= 2024 only"
    )
    assert set(np.unique(cur["season"]).tolist()) <= {2022, 2023, 2024}
    assert set(np.unique(hist_d["season"]).tolist()) <= set(HIST_SEASONS)
    t0 = time.monotonic()
    d = prepare("E2", cur, hist_d, "WALL")
    assert int(d["season"].max()) <= MAX_SEASON
    info = run_walk_forward("WALL_E2", d, dev, hidden, out_dir, bool(smoke), "WALL")
    net, ts = (
        _CAP["net"],
        _CAP["ts"],
    )  # the final network of the single seed, after its last warm refit
    rng = np.random.default_rng(0)
    sample = probe_sample(d, rng)
    z = probe_extract_states(net, ts, to_dev(sample, dev), 1024, dev)
    names = [str(x) for x in d["ctx_features"]]
    ctx = d["ctx"][sample]
    era = np.stack([ctx[:, names.index(c)] for c in ERA_COLS], axis=1)
    np.savez_compressed(
        out_dir / "states_WALL_E2.npz",
        z=z.astype(np.float16), season=d["season"][sample].astype(np.int16), game_id=d["game_id"][sample].astype("U12"),
        player_id=d["player_id"][sample], era_zero=(era == 0).all(axis=1), has_report=ctx[:, names.index("has_report")] > 0.5,
        n_prior=d["n_prior"][sample].astype(np.int32),
    )  # fmt: skip
    info.update(
        states=int(len(sample)), state_dim=int(z.shape[1]), plan_sha256=PLAN_SHA, export_npz_sha256=export_sha,
        hist_npz_sha256=hist_sha, hidden=hidden, smoke=bool(smoke), cold=COLD, warm=WARM, season_2025_used=False,
        states_by_season={str(int(s)): int((d["season"][sample] == s).sum()) for s in np.unique(d["season"][sample])},
    )  # fmt: skip
    (out_dir / "info_WALL_E2.json").write_text(json.dumps(info, indent=2, default=str))
    (out_dir / "config.json").write_text(
        json.dumps(
            {
                "job": "t478_era_probe",
                "plan_sha256": PLAN_SHA,
                "seeds": [0],
                "season_2025_used": False,
                "max_season": MAX_SEASON,
            },
            indent=2,
        )
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(
            {
                "programs": {"WALL_E2": info},
                "plan_sha256": PLAN_SHA,
                "runtime_s": round(time.monotonic() - t0, 1),
            },
            indent=2,
            default=str,
        )
    )
    print(
        "states", json.dumps(info["states_by_season"]), "ARTIFACTS WRITTEN TO", out_dir, flush=True
    )
    return {"WALL_E2": info}
