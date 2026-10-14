# ruff: noqa
# mypy: ignore-errors
# ----------------------------------------------------------------------------------------
# AVG_SHADOW_LIVE chain rebuild (docs/prereg/AVG_SHADOW_LIVE_2.md). TAIL appended by build_notebook.py to the model code of
# ../seq_props/seq_props_train.py (everything before its own `main`), so transformer, loss, fit and predict are exactly T478's.
# The T478 walk-forward functions (load_npz_25, concat_rows, month_blocks, to_dev, oof_dict, run_walk_forward) are NOT written here:
# build_notebook.py copies them verbatim from ../seq_full_mix_rt/seq_full_mix_rt_tail.py (AST extraction) and splices ONE block into
# run_walk_forward (the P1b weight saves and the install fit, marked "AVG_SHADOW_LIVE install fit"). Anything else differing is a build error.
# Program E2 only, window WALL (W_ALL: 2019-20, 2020-21, 2021-22 rows in the cold fit and every warm pool), 3 seeds, hidden 64.
# ----------------------------------------------------------------------------------------
import hashlib

MODE = "held"  # the T478 held-out construction (test seasons 2023, 2024, 2025); its code paths are the ones copied below
WINDOW = "WALL"
RULE_SHA = "__RULE_SHA__"  # sha256 of the frozen rule text above the results marker (baked in at build time, checked on the VM)
RULE_ID = "AVG_SHADOW_LIVE_2"
RULE_SHA_PIN = "9afbfeb6"  # AVG_SHADOW_LIVE_2 (frozen 2026-10-14): written here by hand, not derived from the staged document, so a different rule cannot build or run
RULE_DOC = "AVG_SHADOW_LIVE_2.md"
LOG_DOC = "HOLDOUT_ACCESS_LOG.md"
LOG_ROW_KEY = "Colab chain rebuild for AVG_SHADOW_LIVE"
RESULTS_MARKER = "=== RESULTS BELOW ===\n"
FROZEN_SEASON = 2026  # the live season: nothing from 2026-27 is in the input
HIST_SEASONS = (2019, 2020, 2021)
SEEDS = (0, 1, 2)
COLD = dict(epochs=25, patience=4, lr=1e-3, batch=1024)
WARM = dict(epochs=3, lr=3e-4, batch=1024)
COLD_END = "2023-04-01"
TEST_SEASONS = (2023, 2024, 2025)
INSTALL_BEFORE = (
    "2026-10-01"  # the install fit's pool: every row dated before the month block it serves
)
INSTALL_MONTH = "2026-10"
# proof P1b: weights saved at these month blocks (all predict rows with season label <= 2024): 2023-12, 2024-02 (season 2023), 2024-12, 2025-02 (season 2024)
P1B_MONTHS = ("2023-12", "2024-02", "2024-12", "2025-02")
ROW_KEYS = (
    "seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx", "date_days", "season",
    "game_id", "y", "base", "n_prior",
)  # fmt: skip
EST = (
    "about 12 minutes on a T4 (T478 measured 599 s for the whole E2 program); budget 45, ceiling 60"
)

__COPIED_FUNCTIONS__


def rule_text_sha(text):
    head = text.split(RESULTS_MARKER)[0]
    if head == text:
        raise ValueError("results marker missing from the rule document")
    return hashlib.sha256(head.encode()).hexdigest()


def check_rule(run_dir):
    """Refuse to fit unless (1) the staged rule is the frozen one this notebook was built for and (2) the staged holdout log carries the rebuild row."""
    p = Path(run_dir) / RULE_DOC
    if not p.exists():
        raise FileNotFoundError(f"{p} not staged: the rule document must travel with the inputs")
    text = p.read_text()
    head = text.split(RESULTS_MARKER)[0]
    if not any(line.replace("*", "").startswith("Status: FROZEN") for line in head.splitlines()):
        raise RuntimeError("the rule document is not FROZEN; refusing to fit")
    got = rule_text_sha(text)
    if not (got.startswith(RULE_SHA_PIN) and RULE_SHA.startswith(RULE_SHA_PIN)):
        raise RuntimeError(
            f"rule sha {got[:8]} (notebook {RULE_SHA[:8]}) is not {RULE_ID} {RULE_SHA_PIN}; refusing"
        )
    if got != RULE_SHA:
        raise RuntimeError(f"rule sha mismatch: document {got} vs notebook {RULE_SHA}; refusing")
    lg = Path(run_dir) / LOG_DOC
    if not lg.exists():
        raise FileNotFoundError(f"{lg} not staged: the holdout log must travel with the inputs")
    if not [ln for ln in lg.read_text().splitlines() if LOG_ROW_KEY in ln and "2026-10-13" in ln]:
        raise RuntimeError(
            f"no holdout-log row '{LOG_ROW_KEY}' (2026-10-13) in the staged log; refusing"
        )
    return got


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_id_of(files):
    """Same function as nba.daily.avg_shadow.bundle.model_id_of (the Mac verifies it again at install)."""
    return hashlib.sha256("\n".join(f"{k}:{files[k]}" for k in sorted(files)).encode()).hexdigest()


def prepare(cur, hist_d):
    base0 = concat_rows(hist_d, cur)
    return {
        k: (v.copy() if isinstance(v, np.ndarray) and k in ROW_KEYS else v)
        for k, v in base0.items()
    }


def main():
    smoke = os.environ.get("NBA_SMOKE")
    hidden = int(os.environ.get("NBA_HIDDEN", 16 if smoke else 64))
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print("rule", RULE_ID, RULE_SHA, "expected runtime:", EST, flush=True)
    cur_path = Path(os.environ.get("NBA_PARQUET", "seq_ensemble_rt_cur25.npz"))
    run_dir = cur_path.parent
    sha = check_rule(run_dir)
    hist_path = run_dir / "seq_ensemble_rt_hist.npz"
    export_sha, hist_sha = file_sha256(cur_path), file_sha256(hist_path)
    print("rule sha256", sha, "export npz", export_sha, "hist npz", hist_sha, flush=True)
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cur, hist_d = load_npz_25(cur_path), load_npz_25(hist_path)
    if smoke:
        cur, hist_d = subsample(cur, int(smoke)), subsample(hist_d, int(smoke))
    assert set(np.unique(cur["season"]).tolist()) <= {2022, 2023, 2024, 2025}, (
        "the export holds a season outside the rule"
    )
    assert set(np.unique(hist_d["season"]).tolist()) <= set(HIST_SEASONS), (
        "the history export holds a season outside 2019-2021"
    )
    assert hist_d["ctx"].shape[1] == cur["ctx"].shape[1], "history and current ctx widths differ"
    t0 = time.monotonic()
    d = prepare(cur, hist_d)
    info = run_walk_forward("E2", d, dev, hidden, out_dir, bool(smoke), WINDOW)
    info.update(
        hidden=hidden,
        smoke=bool(smoke),
        cold=COLD,
        warm=WARM,
        rule_sha256=sha,
        export_npz_sha256=export_sha,
        hist_npz_sha256=hist_sha,
        mode=MODE,
    )
    (out_dir / "info_E2.json").write_text(json.dumps(info, indent=2, default=str))
    seeds_run = (0,) if smoke else SEEDS
    files = {
        n: file_sha256(out_dir / n) for n in (*(f"seed{s}.pt" for s in seeds_run), "vocab.json")
    }
    parts = {
        "rule_sha256": sha, "weights_sha256": files, "seeds": list(seeds_run), "hidden": hidden,
        "n_step": int(d["seq"].shape[2]), "n_ctx": int(d["ctx"].shape[1]), "month_block": INSTALL_MONTH,
        "fit_through": info["install_fit"]["fit_through"], "install_pool_rows": info["install_fit"]["pool_rows"],
        "export_npz_sha256": export_sha, "hist_npz_sha256": hist_sha, "w_all_seasons": "2019-20 on",
        "oof_sha256": file_sha256(out_dir / "oof_E2.npz"), "smoke": bool(smoke),
    }  # fmt: skip
    (out_dir / "bundle_parts.json").write_text(json.dumps(parts, indent=2))
    cfg = {"rule": RULE_ID, "rule_sha256": sha, "seeds": parts["seeds"], "cold": COLD, "warm": WARM, "cold_end": COLD_END, "hidden": hidden, "smoke": bool(smoke), "test_seasons": list(TEST_SEASONS), "install_before": INSTALL_BEFORE}  # fmt: skip
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2))
    (out_dir / "metrics.json").write_text(
        json.dumps(
            {
                "programs": {"E2": info},
                "rule_sha256": sha,
                "runtime_s": round(time.monotonic() - t0, 1),
            },
            indent=2,
            default=str,
        )
    )
    print("ARTIFACTS WRITTEN TO", out_dir)
    return info


if __name__ == "__main__":
    main()
