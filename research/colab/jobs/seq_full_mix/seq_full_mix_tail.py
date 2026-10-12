# ruff: noqa
# mypy: ignore-errors
# ----------------------------------------------------------------------------------------
# SEQ_FULL_MIX training programs (docs/prereg/SEQ_FULL_MIX.md). This file is the TAIL appended by
# build_notebook.py to the model code of ../seq_props/seq_props_train.py (everything before its
# own `main`), so the transformer, loss, fit and predict are exactly the specified ones. The walk-forward
# (`run_walk_forward`) is ENSEMBLE's E2 program line for line with ONE change: the test seasons are
# 2023, 2024 AND 2025. Programs:
#   E2   the real sequence transformer (cold fit on 2022 before April, warm refit before every month 2023-10 .. 2025 season)
#   E2P  past-only placebo: every row's model inputs (window, context, opponent, team) are those of ONE uniformly drawn
#        STRICTLY EARLIER row of the same player-season (the row itself when it has none); labels and `base` stay put
#   E2S  ENSEMBLE's shuffled-input guard: inputs permuted among the players of one date
# 3 seeds each, full budgets, no arm cut. Season 2025 INPUTS include box scores of earlier games only; the warm refit
# before each 2025 month trains on every row dated strictly before that month (earlier 2025 months' outcomes as labels).
# ----------------------------------------------------------------------------------------
import hashlib

RULE_SHA = "__RULE_SHA__"  # replaced by build_notebook.py from the frozen document
RULE_ID = "SEQ_FULL_MIX"
RULE_DOC = "SEQ_FULL_MIX.md"
LOG_DOC = "HOLDOUT_ACCESS_LOG.md"
RESULTS_MARKER = "=== RESULTS BELOW ===\n"

FROZEN_SEASON = 2026  # this rule's input legitimately holds season 2025 (committed, Devin-confirmed holdout row); nothing later
SEEDS = (0, 1, 2)
COLD = dict(epochs=25, patience=4, lr=1e-3, batch=1024)
WARM = dict(epochs=3, lr=3e-4, batch=1024)
COLD_END = "2023-04-01"  # 2022-season rows on/after this are the early-stopping slice
TEST_SEASONS = (2023, 2024, 2025)
PROGRAMS = ("E2", "E2P", "E2S")
PLACEBO_SEED = 0
REPORT_CTX = (
    "has_report", "st_available", "st_probable", "st_questionable", "st_doubtful", "st_out",
    "n_rep_out", "vac_rep", "n_return", "vac_return",
)  # fmt: skip
ROW_KEYS = (
    "seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx", "date_days", "season",
    "game_id", "y", "base", "n_prior",
)  # fmt: skip
INPUT_KEYS = (
    "seq",
    "mask",
    "opp_step",
    "ctx",
    "team_idx",
    "opp_idx",
)  # what the placebo replaces (player_id stays: same player)
EST = "about 15 minutes on a T4 (range 10-30): 3 programs, 3 seeds, 27 monthly warm refits each"


def rule_text_sha(text):
    head = text.split(RESULTS_MARKER)[0]
    if head == text:
        raise ValueError("results marker missing from the rule document")
    return hashlib.sha256(head.encode()).hexdigest()


def check_rule(run_dir):
    """Refuse to run unless (1) the staged rule document is the frozen one this notebook was built for and (2) the staged holdout log carries
    the PRE-REGISTERED, DEVIN CONFIRMED row for this rule (the Mac-side export already verified both against git before producing the input)."""
    p = Path(run_dir) / RULE_DOC
    if not p.exists():
        raise FileNotFoundError(f"{p} not staged: the rule document must travel with the inputs")
    text = p.read_text()
    head = text.split(RESULTS_MARKER)[0]
    if not any(line.replace("*", "").startswith("Status: FROZEN") for line in head.splitlines()):
        raise RuntimeError("the rule document is not FROZEN; refusing to fit")
    got = rule_text_sha(text)
    if got != RULE_SHA:
        raise RuntimeError(f"rule sha mismatch: document {got} vs notebook {RULE_SHA}; refusing")
    lg = Path(run_dir) / LOG_DOC
    if not lg.exists():
        raise FileNotFoundError(f"{lg} not staged: the holdout log must travel with the inputs")
    rows = [
        ln for ln in lg.read_text().splitlines()
        if RULE_ID in ln and got[:8] in ln and "PRE-REGISTERED" in ln and "DEVIN CONFIRMED" in ln
    ]  # fmt: skip
    if not rows:
        raise RuntimeError(
            f"no holdout row for {RULE_ID} {got[:8]} (PRE-REGISTERED, DEVIN CONFIRMED) in the staged log; refusing"
        )
    return got


def load_npz_25(path):
    """Like `load_npz` but admits season 2025 (and nothing later)."""
    d = np.load(path, allow_pickle=False)
    out = {k: d[k] for k in d.files}
    if int(out["season"].max()) >= FROZEN_SEASON:
        raise ValueError("a season after 2025 is in the input")
    return out


def shuffle_within_date(d, seed=0):
    """Permute the model inputs among the players of one date; labels and base stay put (ENSEMBLE's S arm, unchanged)."""
    rng = np.random.default_rng(seed)
    n = len(d["y"])
    date = d["date_days"]
    d["pid_true"] = d[
        "player_id"
    ].copy()  # bookkeeping only: the OOF rows are keyed by the TARGET's player, not the shuffled input's
    idx = np.arange(n)
    for dd in np.unique(date):
        w = np.flatnonzero(date == dd)
        idx[w] = rng.permutation(w)
    for k in ("seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx"):
        d[k] = d[k][idx]
    return d


def placebo_index(player, season, date, seed=PLACEBO_SEED):
    """Per row, the index of ONE uniformly drawn row of the same player-season dated STRICTLY EARLIER (the row itself when there is none).
    Strictly earlier by date, so a same-day row is never drawn and the draw is as-of."""
    n = len(player)
    order = np.lexsort((date, season, player))
    p, s, dt = player[order], season[order], date[order]
    cut = np.flatnonzero((p[1:] != p[:-1]) | (s[1:] != s[:-1])) + 1
    bounds = np.concatenate([[0], cut, [n]])
    rng = np.random.default_rng(seed)
    out = np.arange(n)
    for a, b in zip(bounds[:-1], bounds[1:]):
        dd = dt[a:b]
        earlier = np.searchsorted(
            dd, dd, side="left"
        )  # rows of the group with a strictly earlier date
        pick = np.floor(rng.random(b - a) * np.maximum(earlier, 1)).astype(np.int64)
        src = np.where(earlier > 0, a + pick, np.arange(a, b))
        out[order[a:b]] = order[src]
    return out


def placebo_inputs(d, seed=PLACEBO_SEED):
    idx = placebo_index(d["player_id"], d["season"], d["date_days"], seed)
    for k in INPUT_KEYS:
        d[k] = d[k][idx]
    d["_placebo_self_share"] = np.array(float((idx == np.arange(len(idx))).mean()))
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
        "player_id": d.get("pid_true", d["player_id"])[rows].astype(np.int64),
        "season": d["season"][rows].astype(np.int16),
        "date_days": d["date_days"][rows].astype(np.int32),
        "mean": np.clip(mean, 0.0, None).astype(np.float32),
        "q": np.maximum.accumulate(np.clip(q, 0.0, None), axis=2).astype(np.float32),
    }


def run_walk_forward(name, d, dev, hidden, out_dir, smoke):
    """E2 and its P, S variants: cold fit on 2022, warm refit before each 2023-25 month (ENSEMBLE's E2 loop, test seasons extended)."""
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
    fit_m = (nprior >= 3) & (season == 2022) & (date < cold_end)
    val_m = (nprior >= 3) & (season == 2022) & (date >= cold_end)
    test_m = np.isin(season, TEST_SEASONS)
    assert not (season >= FROZEN_SEASON).any()
    assert not (season == 2021).any(), "2021-22 must never be present"
    assert set(np.unique(season).tolist()) <= {2022, *TEST_SEASONS}
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
    frames, detail = [], []
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
        detail.append(
            {
                "block": str(per), "n": int(len(blk)), "pool": int(len(pool)),
                "pool_by_season": {str(int(s)): int((season[pool] == s).sum()) for s in np.unique(season[pool])},
                "max_pool_date": str(date[pool].max()), "min_block_date": str(date[blk].min()),
            }
        )  # fmt: skip
        assert date[pool].max() < date[blk].min(), "a pool row is not strictly before the block"
        print(
            f"  [{name}] block {bi + 1}/{len(blocks)} {per}: n={len(blk)} pool={len(pool)} "
            f"through {pd.Timestamp(start) - pd.Timedelta(days=1):%Y-%m-%d} "
            f"elapsed {time.monotonic() - t0:.0f}s",
            flush=True,
        )
    oof = {k: np.concatenate([f[k] for f in frames]) for k in frames[0]}
    np.savez_compressed(out_dir / f"oof_{name}.npz", **oof)
    return {
        "program": name, "n_fit": int(fit_m.sum()), "n_val": int(val_m.sum()),
        "n_oof": int(len(oof["game_id"])), "blocks": len(blocks), "seeds": list(seeds),
        "val_pinball_cold": vals, "runtime_s": round(time.monotonic() - t0, 1),
        "oof_by_season": {str(int(s)): int((oof["season"] == s).sum()) for s in np.unique(oof["season"])},
        "placebo_self_share": float(d["_placebo_self_share"]) if "_placebo_self_share" in d else None,
        "blocks_detail": detail,
    }  # fmt: skip


def prepare(program, cur):
    base = {
        k: (v.copy() if isinstance(v, np.ndarray) and k in ROW_KEYS else v) for k, v in cur.items()
    }
    if program == "E2P":
        base = placebo_inputs(base)
    if program == "E2S":
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
    cur_path = Path(os.environ.get("NBA_PARQUET", "seq_ensemble_cur25.npz"))
    run_dir = cur_path.parent
    sha = check_rule(run_dir)
    print("rule sha256", sha, flush=True)
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    cur = load_npz_25(cur_path)
    if smoke:
        cur = subsample(cur, int(smoke))
    assert set(np.unique(cur["season"]).tolist()) <= {2022, 2023, 2024, 2025}
    infos = {}
    t0 = time.monotonic()
    for prog in programs:
        if (out_dir / f"oof_{prog}.npz").exists():
            print(f"{prog}: done, skipping")
            continue
        print(f"=== program {prog} ===", flush=True)
        d = prepare(prog, cur)
        infos[prog] = run_walk_forward(prog, d, dev, hidden, out_dir, bool(smoke))
        print(
            prog,
            json.dumps({k: v for k, v in infos[prog].items() if k != "blocks_detail"}),
            flush=True,
        )
        write_metrics(out_dir, infos, sha, t0, hidden, smoke)
    write_metrics(out_dir, infos, sha, t0, hidden, smoke)
    print("ARTIFACTS WRITTEN TO", out_dir)
    return infos


def write_metrics(out_dir, infos, sha, t0, hidden, smoke):
    cfg = {
        "rule": RULE_ID, "rule_sha256": sha, "seeds": list(SEEDS), "cold": COLD, "warm": WARM,
        "cold_end": COLD_END, "hidden": hidden, "smoke": smoke, "test_seasons": list(TEST_SEASONS),
        "season_2025_used": True, "placebo_seed": PLACEBO_SEED, "programs": list(infos),
    }  # fmt: skip
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2, default=str))
    metrics = {"programs": infos, "rule_sha256": sha, "runtime_s": round(time.monotonic() - t0, 1)}
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
