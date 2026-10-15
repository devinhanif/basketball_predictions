# ruff: noqa
# mypy: ignore-errors
# ----------------------------------------------------------------------------------------
# AVG_SHADOW_LIVE_2 MONTHLY refit (docs/prereg/AVG_SHADOW_LIVE_2.md, "What the steward builds" 3: "warm refit on every row dated strictly before the month").
# TAIL appended by build_notebook.py to the model code of ../seq_props/seq_props_train.py (everything before its own `main`), so the transformer, loss, fit and
# predict are exactly T478's (and the October rebuild's). ONE warm block x 3 seeds: the nets start from the PREVIOUS month's saved weights (the next block of the
# same chain), warm config (3 epochs, lr 3e-4, batch 1024), seed 1000 + k, on every row dated before the month start with n_prior >= 3. Vocabulary unchanged.
# Nothing is predicted, scored or read: the output is weights, a vocabulary copy and bundle_parts.json. The gate is fit on the Mac (fit_gate.py), not here.
# ----------------------------------------------------------------------------------------
import hashlib

RULE_SHA = "__RULE_SHA__"  # sha256 of the frozen rule text above the results marker (baked in at build time, checked on the VM)
RULE_ID = "AVG_SHADOW_LIVE_2"
RULE_SHA_PIN = "9afbfeb6"  # written by hand, not derived from the staged document, so a different rule cannot build or run
RULE_DOC = "AVG_SHADOW_LIVE_2.md"
LOG_DOC = "HOLDOUT_ACCESS_LOG.md"
LOG_ROW_KEY = "AVG_SHADOW_LIVE_2 monthly refit"  # a logged row naming the month block must be in the staged log (the archivist writes it first)
RESULTS_MARKER = "=== RESULTS BELOW ===\n"
HIST_SEASONS = (2019, 2020, 2021)
CUR_SEASONS = (2022, 2023, 2024, 2025)
LIVE_SEASON = 2026
SEEDS = (0, 1, 2)
WARM = dict(epochs=3, lr=3e-4, batch=1024)
BUDGET_S = 20 * 60  # 20 T4-minutes (configs/cost.yaml / the rule); the job prints a warning past it
CEILING_S = 60 * 60  # ceiling 60 T4-minutes: halt, never cut an arm silently
EST = "about 2-4 minutes on a T4 (one warm block x 3 seeds); budget 20, ceiling 60"
DATA_FILES = ("seed0.pt", "seed1.pt", "seed2.pt", "vocab.json", "gate.json")
ROW_KEYS = (
    "seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx", "date_days", "season",
    "game_id", "y", "base", "n_prior",
)  # fmt: skip

__COPIED_FUNCTIONS__


def rule_text_sha(text):
    head = text.split(RESULTS_MARKER)[0]
    if head == text:
        raise ValueError("results marker missing from the rule document")
    return hashlib.sha256(head.encode()).hexdigest()


def check_rule(run_dir, month_block):
    """Refuse unless the staged rule is the frozen one this notebook was built for and the staged holdout log carries the monthly-refit row for ``month_block``."""
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
    if not [ln for ln in lg.read_text().splitlines() if LOG_ROW_KEY in ln and month_block in ln]:
        raise RuntimeError(
            f"no holdout-log row '{LOG_ROW_KEY}' naming {month_block} in the staged log; refusing"
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


def load_prev_bundle(prev_dir, month_block):
    """The previous month's installed bundle: every file's sha256 and the model_id must equal its manifest, and it must be an EARLIER month block."""
    prev_dir = Path(prev_dir)
    man = json.loads((prev_dir / "manifest.json").read_text())
    for name in DATA_FILES:
        if name not in man["files"]:
            raise RuntimeError(f"previous bundle: {name} not in its manifest")
        if name == "gate.json" and not (prev_dir / name).exists():
            continue  # the Colab job does not need the gate; the Mac verifies the full bundle
        got = file_sha256(prev_dir / name)
        if got != man["files"][name]:
            raise RuntimeError(
                f"previous bundle: {name} sha256 {got} != manifest {man['files'][name]}"
            )
    if model_id_of(man["files"]) != man["model_id"]:
        raise RuntimeError("previous bundle: model_id does not match the file shas")
    if not man["month_block"] < month_block:
        raise RuntimeError(
            f"previous bundle serves {man['month_block']}, not a month before {month_block}"
        )
    if man.get("rule_sha256") != RULE_SHA:
        raise RuntimeError("previous bundle was built for another rule")
    vocab = {int(k): int(v) for k, v in json.loads((prev_dir / "vocab.json").read_text()).items()}
    return man, vocab


def load_live(path, month_block, before_date):
    d = np.load(path, allow_pickle=False)
    out = {k: d[k] for k in d.files}
    if set(np.unique(out["season"]).tolist()) != {LIVE_SEASON}:
        raise ValueError(f"the live export must hold season {LIVE_SEASON} only")
    last = out["date_days"].astype("datetime64[D]").max()
    if not last < np.datetime64(before_date):
        raise ValueError(f"a live row dated {last} is not before {before_date}")
    return out


def load_cur_hist(path, seasons):
    d = np.load(path, allow_pickle=False)
    out = {k: d[k] for k in d.files}
    got = set(np.unique(out["season"]).tolist())
    if not got <= set(seasons):
        raise ValueError(f"export holds seasons {sorted(got)} outside {sorted(seasons)}")
    return out


def main():
    smoke = os.environ.get("NBA_SMOKE")
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print("rule", RULE_ID, RULE_SHA, "expected runtime:", EST, flush=True)
    cur_path = Path(os.environ.get("NBA_PARQUET", "seq_ensemble_rt_cur25.npz"))
    run_dir = cur_path.parent
    live_info = json.loads((run_dir / "export_info_live.json").read_text())
    month_block, before_date = live_info["month_block"], live_info["before_date"]
    assert before_date == f"{month_block}-01", (before_date, month_block)
    sha = check_rule(run_dir, month_block)
    man, vocab = load_prev_bundle(run_dir / "prev_bundle", month_block)
    hidden, n_step, n_ctx = int(man["hidden"]), int(man["n_step"]), int(man["n_ctx"])
    seeds = tuple(int(s) for s in man["seeds"])
    assert seeds == SEEDS, seeds
    hist_path, live_path = (
        run_dir / "seq_ensemble_rt_hist.npz",
        run_dir / "seq_ensemble_rt_live.npz",
    )
    shas = {
        "export_npz_sha256": file_sha256(cur_path),
        "hist_npz_sha256": file_sha256(hist_path),
        "live_npz_sha256": file_sha256(live_path),
    }
    if shas["live_npz_sha256"] != live_info["npz_sha256"]:
        raise RuntimeError("the staged live export is not the one export_monthly.py described")
    print(
        "rule sha256",
        sha,
        shas,
        "previous bundle",
        man["month_block"],
        man["model_id"][:8],
        flush=True,
    )
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cur, hist_d = load_cur_hist(cur_path, CUR_SEASONS), load_cur_hist(hist_path, HIST_SEASONS)
    live = load_live(live_path, month_block, before_date)
    if smoke:
        cur, hist_d, live = (
            subsample(cur, int(smoke)),
            subsample(hist_d, int(smoke)),
            subsample(live, int(smoke)),
        )
    assert hist_d["ctx"].shape[1] == cur["ctx"].shape[1] == live["ctx"].shape[1] == n_ctx, (
        "ctx widths differ from the bundle's"
    )
    assert cur["seq"].shape[2] == live["seq"].shape[2] == n_step, (
        "step widths differ from the bundle's"
    )
    t0 = time.monotonic()
    d = concat_rows(
        concat_rows(hist_d, cur), live
    )  # non-row keys (stat_scale, feature names) must be equal, else AssertionError
    date = d["date_days"].astype("datetime64[D]")
    nprior = d["n_prior"]
    start = np.datetime64(before_date)
    assert set(np.unique(d["season"]).tolist()) <= {*HIST_SEASONS, *CUR_SEASONS, LIVE_SEASON}
    pool = np.flatnonzero((date < start) & (nprior >= 3))
    assert date[pool].max() < start, "a pool row is not before the month block"
    warm = dict(WARM)
    if smoke:
        warm.update(epochs=1, batch=256)
    ts = Tensors(d, vocab, dev)
    prow = to_dev(pool, dev)
    for k_, sd in enumerate(seeds):
        net = SeqNet(n_step, n_ctx, len(vocab), d["stat_scale"], "transformer", d=hidden).to(dev)
        net.load_state_dict(torch.load(run_dir / "prev_bundle" / f"seed{sd}.pt", map_location=dev))
        seed_everything(1000 + k_)
        fit(net, ts, prow, None, warm, dev, warm["lr"], warm["epochs"], 99)
        torch.save(
            {n: p.detach().cpu() for n, p in net.state_dict().items()}, out_dir / f"seed{sd}.pt"
        )
        el = time.monotonic() - t0
        print(
            f"  [{month_block}] seed {sd} warm block done: pool={len(pool)} through {date[pool].max()} elapsed {el:.0f}s",
            flush=True,
        )
        if el > CEILING_S:
            raise RuntimeError(
                f"ceiling {CEILING_S}s reached after seed {sd}; halting (no arm is cut silently)"
            )
    (out_dir / "vocab.json").write_text((run_dir / "prev_bundle" / "vocab.json").read_text())
    runtime = round(time.monotonic() - t0, 1)
    if runtime > BUDGET_S:
        print(
            f"WARNING: runtime {runtime}s is over the {BUDGET_S}s budget (ceiling {CEILING_S}s)",
            flush=True,
        )
    files = {n: file_sha256(out_dir / n) for n in (*(f"seed{s}.pt" for s in seeds), "vocab.json")}
    parts = {
        "rule_sha256": sha, "kind": "E2_W_ALL_MONTHLY", "weights_sha256": files, "seeds": list(seeds), "hidden": hidden, "n_step": n_step, "n_ctx": n_ctx,
        "month_block": month_block, "fit_through": str(date[pool].max())[:10], "install_pool_rows": int(len(pool)), "w_all_seasons": "2019-20 on",
        "prev_model_id": man["model_id"], "prev_month_block": man["month_block"], "prev_fit_through": man["fit_through"], "oof_sha256": None,
        "live_rows": int(len(live["y"])), "live_info": live_info, **shas, "smoke": bool(smoke),
    }  # fmt: skip
    (out_dir / "bundle_parts.json").write_text(json.dumps(parts, indent=2, default=str))
    cfg = {"rule": RULE_ID, "rule_sha256": sha, "seeds": list(seeds), "warm": warm, "hidden": hidden, "month_block": month_block, "before_date": before_date, "smoke": bool(smoke)}  # fmt: skip
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2))
    (out_dir / "metrics.json").write_text(
        json.dumps(
            {
                "program": "E2_monthly",
                "runtime_s": runtime,
                "pool_rows": int(len(pool)),
                "rule_sha256": sha,
                "scored": False,
            },
            indent=2,
        )
    )
    print("ARTIFACTS WRITTEN TO", out_dir)
    return parts


if __name__ == "__main__":
    main()
