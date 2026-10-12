# ruff: noqa
# mypy: ignore-errors
# ----------------------------------------------------------------------------------------
# ENSEMBLE training programs (docs/prereg/ENSEMBLE.md). This file is the TAIL appended by
# build_notebook.py to the model code of ../seq_props/seq_props_train.py (everything before its
# own `main`), so the transformer, loss, fit and predict are exactly the specified ones.
# ----------------------------------------------------------------------------------------
import hashlib

RULE_SHA = "__RULE_SHA__"  # replaced by build_notebook.py from the frozen document
RULE_DOC = "ENSEMBLE.md"
RESULTS_MARKER = "=== RESULTS BELOW ===\n"

SEEDS = (0, 1, 2)
COLD = dict(epochs=25, patience=4, lr=1e-3, batch=1024)
WARM = dict(epochs=3, lr=3e-4, batch=1024)
ORIG = dict(epochs=18, patience=3, lr=1e-3, batch=1024, seeds=(0, 1))
COLD_END = "2023-04-01"  # 2022-season rows on/after this are the early-stopping slice
ORIG_VAL_START = "2024-02-01"
TEST_SEASONS = (2023, 2024)
STATE_PER_ERA = 6000
PROGRAMS = ("E2", "E2K", "E2S", "E3", "E3K", "E3S", "E2o")
REPORT_CTX = (
    "has_report", "st_available", "st_probable", "st_questionable", "st_doubtful", "st_out",
    "n_rep_out", "vac_rep", "n_return", "vac_return",
)  # fmt: skip
ROW_KEYS = (
    "seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx", "date_days", "season",
    "game_id", "y", "base", "n_prior",
)  # fmt: skip
EST = "about 75 minutes on a T4 (range 60-120): 7 programs, 3 seeds, 18 monthly warm refits each"


def rule_text_sha(text):
    head = text.split(RESULTS_MARKER)[0]
    if head == text:
        raise ValueError("results marker missing from the rule document")
    return hashlib.sha256(head.encode()).hexdigest()


def check_rule(run_dir):
    """Refuse to run unless the staged rule document is the frozen one this notebook was built for."""
    p = Path(run_dir) / RULE_DOC
    if not p.exists():
        raise FileNotFoundError(f"{p} not staged: the rule document must travel with the inputs")
    text = p.read_text()
    head = text.split(RESULTS_MARKER)[0]
    if not any(line.startswith("Status: FROZEN") for line in head.splitlines()):
        raise RuntimeError("the rule document is not FROZEN; refusing to fit")
    got = rule_text_sha(text)
    if got != RULE_SHA:
        raise RuntimeError(f"rule sha mismatch: document {got} vs notebook {RULE_SHA}; refusing")
    return got


def concat_rows(a, b):
    out = {k: np.concatenate([a[k], b[k]]) for k in ROW_KEYS}
    for k in a:
        if k not in out:
            out[k] = a[k]
    return out


def zero_report(d):
    names = [str(x) for x in d["ctx_features"]]
    idx = [names.index(c) for c in REPORT_CTX]
    d["ctx"] = d["ctx"].copy()
    d["ctx"][:, idx] = 0.0
    return d


def shuffle_within_date(d, seed=0):
    """Permute the model inputs among the players of one date; labels and base stay put."""
    rng = np.random.default_rng(seed)
    n = len(d["y"])
    date = d["date_days"]
    idx = np.arange(n)
    for dd in np.unique(date):
        w = np.flatnonzero(date == dd)
        idx[w] = rng.permutation(w)
    for k in ("seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx"):
        d[k] = d[k][idx]
    return d


def month_blocks(date, season, rows):
    per = pd.Series(date[rows]).dt.to_period("M")
    for p in sorted(per.unique()):
        yield p, rows[(per == p).to_numpy()]


def to_dev(rows, dev):
    return torch.as_tensor(rows, device=dev)


def oof_dict(d, rows, mean, q):
    return {
        "game_id": d["game_id"][rows].astype("U12"),
        "player_id": d["player_id"][rows].astype(np.int64),
        "season": d["season"][rows].astype(np.int16),
        "date_days": d["date_days"][rows].astype(np.int32),
        "mean": np.clip(mean, 0.0, None).astype(np.float32),
        "q": np.maximum.accumulate(np.clip(q, 0.0, None), axis=2).astype(np.float32),
    }


def extract_states(net, ts, rows, cfg, dev):
    store = []
    h = net.head[0].register_forward_pre_hook(lambda m, i: store.append(i[0].float().cpu().numpy()))
    try:
        predict(net, ts, rows, cfg["batch"], dev)
    finally:
        h.remove()
    return np.concatenate(store)


def era_sample(d, rows, rng):
    """Up to STATE_PER_ERA rows per era (2019, 2020, 2022, 2023, 2024) with n_prior >= 5."""
    keep = []
    for s in (2019, 2020, 2022, 2023, 2024):
        w = rows[(d["season"][rows] == s) & (d["n_prior"][rows] >= 5)]
        if len(w):
            keep.append(np.sort(rng.choice(w, size=min(len(w), STATE_PER_ERA), replace=False)))
    return np.concatenate(keep) if keep else np.zeros(0, dtype=np.int64)


def run_walk_forward(name, d, hist, dev, hidden, out_dir, smoke):
    """E2 / E3 and their K, S variants: cold fit on 2022, warm refit before each 2023-24 month."""
    t0 = time.monotonic()
    seeds = (0,) if smoke else SEEDS
    cold = dict(COLD)
    warm = dict(WARM)
    if smoke:
        cold.update(epochs=1, batch=256)
        warm.update(epochs=1, batch=256)
    date = d["date_days"].astype("datetime64[D]")
    season, nprior = d["season"], d["n_prior"]
    cold_end = np.datetime64(COLD_END)
    is_hist = season <= 2020
    fit_m = (nprior >= 3) & (((season == 2022) & (date < cold_end)) | (is_hist if hist else False))
    val_m = (nprior >= 3) & (season == 2022) & (date >= cold_end)
    test_m = np.isin(season, TEST_SEASONS)
    assert not (season >= FROZEN_SEASON).any()
    assert hist or not is_hist.any()
    assert not (season == 2021).any(), "2021-22 must never be present"
    vocab = build_vocab(d["player_id"], np.flatnonzero(fit_m))
    ts = Tensors(d, vocab, dev)
    tr, va = to_dev(np.flatnonzero(fit_m), dev), to_dev(np.flatnonzero(val_m), dev)
    n_step, n_ctx = d["seq"].shape[2], d["ctx"].shape[1]
    nets, vals = [], []
    for sd in seeds:
        seed_everything(sd)
        net = SeqNet(n_step, n_ctx, len(vocab), d["stat_scale"], "transformer", d=hidden).to(dev)
        print(
            f"[{name} seed {sd}] cold fit rows {int(fit_m.sum())} val {int(val_m.sum())}",
            flush=True,
        )
        best, _ = fit(net, ts, tr, va, cold, dev, cold["lr"], cold["epochs"], cold["patience"])
        nets.append(net)
        vals.append(float(best))
    frames = []
    test_rows = np.flatnonzero(test_m)
    blocks = list(month_blocks(date, season, test_rows))
    for bi, (per, blk) in enumerate(blocks):
        start = np.datetime64(per.start_time.date())
        pool = np.flatnonzero((date < start) & (nprior >= 3))
        prow = to_dev(pool, dev)
        for k_, net in enumerate(nets):
            seed_everything(1000 + k_)
            fit(net, ts, prow, None, warm, dev, warm["lr"], warm["epochs"], 99)
        m, q = ensemble([predict(x, ts, to_dev(blk, dev), cold["batch"], dev) for x in nets])
        frames.append(oof_dict(d, blk, m, q))
        print(
            f"  [{name}] block {bi + 1}/{len(blocks)} {per}: n={len(blk)} pool={len(pool)} "
            f"through {pd.Timestamp(start) - pd.Timedelta(days=1):%Y-%m-%d} "
            f"elapsed {time.monotonic() - t0:.0f}s",
            flush=True,
        )
    oof = {k: np.concatenate([f[k] for f in frames]) for k in frames[0]}
    np.savez_compressed(out_dir / f"oof_{name}.npz", **oof)
    info = {
        "program": name, "n_fit": int(fit_m.sum()), "n_val": int(val_m.sum()),
        "n_oof": int(len(oof["game_id"])), "blocks": len(blocks), "seeds": list(seeds),
        "val_pinball_cold": vals, "runtime_s": round(time.monotonic() - t0, 1),
    }  # fmt: skip
    if name == "E3":  # states for the P-emb era probe, from the final model, eval mode
        rng = np.random.default_rng(0)
        sample = era_sample(d, np.flatnonzero(np.isin(season, (2019, 2020, 2022, 2023, 2024))), rng)
        z = extract_states(nets[0], ts, to_dev(sample, dev), warm, dev)
        np.savez_compressed(
            out_dir / "states_E3.npz",
            z=z.astype(np.float16), season=season[sample].astype(np.int16),
            game_id=d["game_id"][sample].astype("U12"), player_id=d["player_id"][sample],
        )  # fmt: skip
        info["states"] = int(len(sample))
    return info


def run_original(d, dev, hidden, out_dir, smoke):
    """E2o (descriptive): the literal T065 protocol on the fresh export, 2024 predicted once."""
    t0 = time.monotonic()
    cfg = dict(ORIG)
    seeds = (0,) if smoke else cfg["seeds"]
    if smoke:
        cfg.update(epochs=1, batch=256)
    date = d["date_days"].astype("datetime64[D]")
    season, nprior = d["season"], d["n_prior"]
    vs = np.datetime64(ORIG_VAL_START)
    fit_m = (season <= 2023) & (date < vs) & (nprior >= 3)
    val_m = (season == 2023) & (date >= vs) & (nprior >= 3)
    test_m = season == 2024
    vocab = build_vocab(d["player_id"], np.flatnonzero(fit_m))
    ts = Tensors(d, vocab, dev)
    tr, va = to_dev(np.flatnonzero(fit_m), dev), to_dev(np.flatnonzero(val_m), dev)
    nets = []
    for sd in seeds:
        seed_everything(sd)
        net = SeqNet(
            d["seq"].shape[2],
            d["ctx"].shape[1],
            len(vocab),
            d["stat_scale"],
            "transformer",
            d=hidden,
        ).to(dev)
        print(f"[E2o seed {sd}] fit rows {int(fit_m.sum())} val {int(val_m.sum())}", flush=True)
        fit(net, ts, tr, va, cfg, dev, cfg["lr"], cfg["epochs"], cfg["patience"])
        nets.append(net)
    rows = np.flatnonzero(test_m)
    m, q = ensemble([predict(x, ts, to_dev(rows, dev), cfg["batch"], dev) for x in nets])
    np.savez_compressed(out_dir / "oof_E2o.npz", **oof_dict(d, rows, m, q))
    return {
        "program": "E2o", "n_fit": int(fit_m.sum()), "n_val": int(val_m.sum()),
        "n_oof": int(len(rows)), "seeds": list(seeds), "runtime_s": round(time.monotonic() - t0, 1),
    }  # fmt: skip


def prepare(program, cur, hist_d):
    base = concat_rows(hist_d, cur) if program.startswith("E3") else dict(cur)
    base = {
        k: (v.copy() if isinstance(v, np.ndarray) and k in ROW_KEYS else v) for k, v in base.items()
    }
    if program.endswith("K"):
        base = zero_report(base)
    if program.endswith("S"):
        base = shuffle_within_date(base)
    return base


def main():
    smoke = os.environ.get("NBA_SMOKE")
    hidden = int(os.environ.get("NBA_HIDDEN", 16 if smoke else 64))
    programs = tuple(p for p in os.environ.get("NBA_PROGRAMS", ",".join(PROGRAMS)).split(",") if p)
    assert set(programs) <= set(PROGRAMS), programs
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print("expected runtime:", EST, flush=True)
    cur_path = Path(os.environ.get("NBA_PARQUET", "seq_ensemble_cur.npz"))
    run_dir = cur_path.parent
    sha = check_rule(run_dir)
    print("rule sha256", sha, flush=True)
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cur = load_npz(cur_path)
    hist_d = load_npz(run_dir / "seq_ensemble_hist.npz")
    if smoke:
        cur, hist_d = subsample(cur, int(smoke)), subsample(hist_d, int(smoke))
    assert set(np.unique(hist_d["season"]).tolist()) <= {2019, 2020}
    assert set(np.unique(cur["season"]).tolist()) <= {2022, 2023, 2024}
    infos = {}
    t0 = time.monotonic()
    for prog in programs:
        if (out_dir / f"oof_{prog}.npz").exists():
            print(f"{prog}: done, skipping")
            continue
        print(f"=== program {prog} ===", flush=True)
        if prog == "E2o":
            infos[prog] = run_original(cur, dev, hidden, out_dir, bool(smoke))
        else:
            d = prepare(prog, cur, hist_d)
            infos[prog] = run_walk_forward(
                prog, d, prog.startswith("E3"), dev, hidden, out_dir, bool(smoke)
            )
        print(prog, json.dumps(infos[prog]), flush=True)
        write_metrics(out_dir, infos, sha, t0, hidden, smoke)
    write_metrics(out_dir, infos, sha, t0, hidden, smoke)
    print("ARTIFACTS WRITTEN TO", out_dir)
    return infos


def write_metrics(out_dir, infos, sha, t0, hidden, smoke):
    cfg = {
        "rule": "ENSEMBLE", "rule_sha256": sha, "seeds": list(SEEDS), "cold": COLD, "warm": WARM,
        "orig": {k: (list(v) if isinstance(v, tuple) else v) for k, v in ORIG.items()},
        "cold_end": COLD_END, "hidden": hidden, "smoke": smoke, "season_2025_used": False,
        "programs": list(infos),
    }  # fmt: skip
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2, default=str))
    metrics = {"programs": infos, "rule_sha256": sha, "runtime_s": round(time.monotonic() - t0, 1)}
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
