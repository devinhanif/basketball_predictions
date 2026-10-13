# ruff: noqa
# mypy: ignore-errors
# ----------------------------------------------------------------------------------------
# SEQ_LEARNING_CURVE training programs (docs/prereg/SEQ_LEARNING_CURVE.md, frozen). TAIL appended by build_notebook.py to the model code of
# ../seq_props/seq_props_train.py (everything before its own `main`), so the transformer, the loss, `fit` and `predict` are exactly the RT ones.
#
# Arms (one policy for every N: same epochs cap, patience, learning rates, batch and seeds; no window is tuned):
#   NORM_<N>  N = 1..8      normalized export (past-only league-rate factor + era/rule channels), seeds 0,1,2
#   RAW_<N>   N = 1, 4, 8   the RT construction on the raw export (no normalizer, 3 covid era channels), seeds 0,1,2
#   NREP_<N>  N = 4, 6, 8   a second independent 3-seed set on the NORM export, seeds 3,4,5
# Window N = the N seasons ending 2022-23 as training labels; scored walk-forward on 2023-24 (labels == 2023) ONLY: cold fit, then a warm refit before each calendar
# month of 2023-24 on every earlier window row (n_prior >= 3) plus earlier 2023-24 months. Nothing after 2023 can be loaded (asserted on every input).
# The job refuses to start without (1) the staged frozen rule document and (2) the loader-review attestation written by the Mac-side `stage` command after
# `research.eval.seq_learning_curve_rule.require_review` (a committed docs/reviews/seq_learning_curve_loader_<date>.md descended from the freeze commit).
# It scores nothing; the CPU scorer does. Ceiling: 240 T4 minutes cumulative across restarts; an arm not finished is reported as unfinished, never cut silently.
# ----------------------------------------------------------------------------------------
import hashlib

RULE_ID = "SEQ_LEARNING_CURVE"
RULE_SHA = "__RULE_SHA__"  # sha256 of the frozen document's text above the marker
RULE_DOC = "SEQ_LEARNING_CURVE.md"
ATTESTATION = "loader_review_attestation.json"
RESULTS_MARKER = "=== RESULTS BELOW ===\n"
CAP_SEASON = 2023  # the select season: nothing later is ever loaded
TEST_LABEL = 2023
COLD = dict(epochs=25, patience=4, lr=1e-3, batch=1024)
WARM = dict(epochs=3, lr=3e-4, batch=1024)
COLD_END = "2023-04-01"  # 2022-season rows on/after this are the early-stopping slice (the same set for every N)
SEEDS_MAIN = (0, 1, 2)
SEEDS_REP = (3, 4, 5)
CEILING_MIN = 240.0
CAP_SHARE_MAX = 1.0 / 3.0
SEGS = ("S1", "S2", "S3")
# priority order (the ceiling stops the least valuable arms last; an unfinished arm makes the verdict INCONCLUSIVE regardless)
ARMS = (
    [("RAW_1", "raw", 1, SEEDS_MAIN), ("RAW_4", "raw", 4, SEEDS_MAIN)]
    + [(f"NORM_{n}", "norm", n, SEEDS_MAIN) for n in range(1, 9)]
    + [("RAW_8", "raw", 8, SEEDS_MAIN)]
    + [(f"NREP_{n}", "norm", n, SEEDS_REP) for n in (4, 6, 8)]
)
ROW_KEYS = (
    "seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx", "date_days", "season",
    "game_id", "y", "base", "n_prior", "norm_c", "date_str",
)  # fmt: skip
TEST_INPUT_KEYS = ("seq", "mask", "opp_step", "ctx", "team_idx", "opp_idx", "y", "base", "norm_c")
EST = "about 60-150 T4 minutes (estimate, not measured): 18 arms, 9 monthly warm refits each; ceiling 240"


class CeilingReached(RuntimeError):
    pass


def rule_text_sha(text):
    head = text.split(RESULTS_MARKER)[0]
    if head == text:
        raise ValueError("results marker missing from the rule document")
    return hashlib.sha256(head.encode()).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_gates(run_dir):
    """Refuse unless the staged rule document is the frozen one this notebook was built for AND the loader-review attestation matches it, its staged review text, and the staged exports."""
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
    a = Path(run_dir) / ATTESTATION
    if not a.exists():
        raise FileNotFoundError(
            f"{a} not staged: the adversary's committed loader review (docs/reviews/seq_learning_curve_loader_<date>.md, a descendant of the freeze commit) is a hard stop; "
            "run `python -m research.eval.seq_learning_curve_rule stage` on the Mac"
        )
    att = json.loads(a.read_text())
    for k in (
        "rule_sha256",
        "freeze_commit",
        "review_path",
        "review_commit",
        "review_sha256",
        "review_file",
        "review_descends_from_freeze",
        "exports",
    ):
        if k not in att:
            raise RuntimeError(f"attestation lacks {k!r}; refusing")
    if att["rule_sha256"] != RULE_SHA:
        raise RuntimeError("the attestation is for a different rule sha; refusing")
    if att["review_descends_from_freeze"] is not True:
        raise RuntimeError("the loader review does not descend from the freeze commit; refusing")
    r = Path(run_dir) / att["review_file"]
    if not r.exists() or file_sha256(r) != att["review_sha256"]:
        raise RuntimeError(
            "the staged loader review is missing or differs from the attested one; refusing"
        )
    for name, sha in att["exports"].items():
        f = Path(run_dir) / name
        if not f.exists() or file_sha256(f) != sha:
            raise RuntimeError(
                f"staged export {name} is missing or differs from the attested sha; refusing"
            )
    return got, att


# --------------------------------------------------------------------------- windows (identical to research.features.seq_learning_curve_export.window_masks; a test pins them equal)
def window_labels(n):
    if not 1 <= n <= 8:
        raise ValueError("windows 1..8 only")
    return tuple(range(2022 - n + 1, 2023))


def window_masks(season, date_days, n_prior, n):
    season = np.asarray(season)
    date = np.asarray(date_days).astype("datetime64[D]")
    labs = window_labels(n)
    in_win = np.isin(season, labs)
    cold_end = np.datetime64(COLD_END)
    fit = (n_prior >= 3) & in_win & (((season == 2022) & (date < cold_end)) | (season < 2022))
    val = (n_prior >= 3) & (season == 2022) & (date >= cold_end)
    test = season == TEST_LABEL
    pool_elig = (n_prior >= 3) & (in_win | test)
    return {"fit": fit, "val": val, "test": test, "pool_eligible": pool_elig}


def best_epoch(hist):
    """Index of the last epoch that improved the validation loss by more than 1e-5 (the `fit` rule)."""
    best, ep = float("inf"), -1
    for h in hist:
        if "val" in h and h["val"] < best - 1e-5:
            best, ep = h["val"], h["epoch"]
    return ep


# --------------------------------------------------------------------------- data
def load_capped(path):
    d = np.load(path, allow_pickle=False)
    out = {k: d[k] for k in d.files}
    if int(out["season"].max()) > CAP_SEASON:
        raise ValueError(f"{path}: a season after {CAP_SEASON} is in the input; refusing")
    return out


def subset(d, rows):
    n = len(d["y"])
    return {
        k: (v[rows] if isinstance(v, np.ndarray) and v.shape[:1] == (n,) and k in ROW_KEYS else v)
        for k, v in d.items()
    }


def concat_all(parts):
    """Segments in chronological order. Non-row keys (stat scale, feature names) must EQUAL across the parts."""
    a = parts[0]
    for b in parts[1:]:
        for k in a:
            if k in ROW_KEYS:
                continue
            if k not in b or not np.array_equal(np.asarray(a[k]), np.asarray(b[k])):
                raise AssertionError(f"non-row key {k!r} differs between segments")
    out = {k: np.concatenate([p[k] for p in parts]) for k in a if k in ROW_KEYS}
    for k in a:
        if k not in out:
            out[k] = a[k]
    return out


def window_data(segs, n):
    """Rows of window N: S1 / S2 rows with season label in the window; S3 whole (2022, 2023)."""
    labs = window_labels(n)
    parts = []
    for s in SEGS:
        if s == "S3":
            parts.append(segs[s])
        elif any(
            l in labs for l in ((2015, 2016, 2017, 2018) if s == "S1" else (2019, 2020, 2021))
        ):
            parts.append(subset(segs[s], np.flatnonzero(np.isin(segs[s]["season"], labs))))
    return concat_all(parts)


def month_blocks(date, rows):
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
        "mean": np.clip(mean, 0.0, None).astype(
            np.float32
        ),  # normalized units (NORM / NREP): the scorer divides by norm_c
        "q": np.maximum.accumulate(np.clip(q, 0.0, None), axis=2).astype(np.float32),
    }


def test_sha(d, rows):
    h = hashlib.sha256()
    for k in TEST_INPUT_KEYS:
        h.update(np.ascontiguousarray(d[k][rows]).tobytes())
    return h.hexdigest()


def keys_sha(d, rows):
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(d["game_id"][rows]).astype("U12").tobytes())
    h.update(np.ascontiguousarray(d["player_id"][rows]).astype(np.int64).tobytes())
    return h.hexdigest()


def elapsed_min(t0, prior_s):
    return (prior_s + time.monotonic() - t0) / 60.0


def run_arm(name, variant, n, seeds, d, dev, hidden, out_dir, smoke, t0, prior_s, seen):
    """One window: cold fit per seed (best epoch recorded), then the monthly warm refit walk over 2023-24."""
    ta = time.monotonic()
    cold = dict(COLD)
    warm = dict(WARM)
    if smoke:
        seeds = seeds[:1]
        cold.update(epochs=1, batch=256)
        warm.update(epochs=1, batch=256)
    date = d["date_days"].astype("datetime64[D]")
    season, nprior = d["season"], d["n_prior"]
    m = window_masks(season, d["date_days"], nprior, n)
    fit_m, val_m, test_m = m["fit"], m["val"], m["test"]
    assert int(season.max()) <= CAP_SEASON
    assert set(np.unique(season).tolist()) <= {*window_labels(n), TEST_LABEL}, (
        "rows outside the window"
    )
    assert fit_m.any() and val_m.any() and test_m.any(), "empty fit / validation / test set"
    test_rows = np.flatnonzero(test_m)
    ks, ts_ = keys_sha(d, test_rows), test_sha(d, test_rows)
    if seen.setdefault("keys", ks) != ks:
        raise AssertionError(f"{name}: the 2023-24 test row set differs from the first arm's")
    if seen.setdefault("test_" + variant, ts_) != ts_:
        raise AssertionError(
            f"{name}: the 2023-24 test inputs differ from the first {variant} arm's"
        )
    vocab = build_vocab(d["player_id"], np.flatnonzero(fit_m))
    ts = Tensors(d, vocab, dev)
    tr, va = to_dev(np.flatnonzero(fit_m), dev), to_dev(np.flatnonzero(val_m), dev)
    n_step, n_ctx = d["seq"].shape[2], d["ctx"].shape[1]
    nets, colds = [], []
    for sd in seeds:
        if elapsed_min(t0, prior_s) > CEILING_MIN:
            raise CeilingReached(name)
        seed_everything(sd)
        net = SeqNet(n_step, n_ctx, len(vocab), d["stat_scale"], "transformer", d=hidden).to(dev)
        print(
            f"[{name} seed {sd}] cold fit rows {int(fit_m.sum())} val {int(val_m.sum())}",
            flush=True,
        )
        best, hist = fit(net, ts, tr, va, cold, dev, cold["lr"], cold["epochs"], cold["patience"])
        nets.append(net)
        colds.append(
            {
                "seed": int(sd),
                "epochs_run": len(hist),
                "best_epoch": best_epoch(hist),
                "best_val": float(best),
                "reached_cap": len(hist) >= cold["epochs"],
            }
        )
    frames, detail = [], []
    blocks = list(month_blocks(date, test_rows))
    for bi, (per, blk) in enumerate(blocks):
        if elapsed_min(t0, prior_s) > CEILING_MIN:
            raise CeilingReached(f"{name} block {bi}")
        start = np.datetime64(per.start_time.date())
        pool = np.flatnonzero((date < start) & m["pool_eligible"])
        assert date[pool].max() < date[blk].min(), "a pool row is not strictly before the block"
        prow = to_dev(pool, dev)
        for sd, net in zip(seeds, nets):
            seed_everything(1000 + sd)
            fit(net, ts, prow, None, warm, dev, warm["lr"], warm["epochs"], 99)
        mean, q = ensemble([predict(x, ts, to_dev(blk, dev), cold["batch"], dev) for x in nets])
        frames.append(oof_dict(d, blk, mean, q))
        detail.append(
            {
                "block": str(per), "n": int(len(blk)), "pool": int(len(pool)),
                "pool_by_season": {str(int(s)): int((season[pool] == s).sum()) for s in np.unique(season[pool])},
                "max_pool_date": str(date[pool].max()), "min_block_date": str(date[blk].min()),
            }
        )  # fmt: skip
        print(
            f"  [{name}] block {bi + 1}/{len(blocks)} {per}: n={len(blk)} pool={len(pool)} "
            f"through {pd.Timestamp(start) - pd.Timedelta(days=1):%Y-%m-%d} total {elapsed_min(t0, prior_s):.1f} min",
            flush=True,
        )
    oof = {k: np.concatenate([f[k] for f in frames]) for k in frames[0]}
    np.savez_compressed(out_dir / f"oof_{name}.npz", **oof)
    return {
        "arm": name, "variant": variant, "window_n": n, "window_labels": list(window_labels(n)), "seeds": list(seeds),
        "n_fit": int(fit_m.sum()), "n_val": int(val_m.sum()), "n_test": int(test_m.sum()), "n_oof": int(len(oof["game_id"])), "blocks": len(blocks),
        "cold": colds, "cold_fits_reached_cap": int(sum(c["reached_cap"] for c in colds)), "cold_fits": len(colds),
        "test_keys_sha256": ks, "test_inputs_sha256": ts_, "runtime_s": round(time.monotonic() - ta, 1), "blocks_detail": detail,
        "oof_by_season": {str(int(s)): int((oof["season"] == s).sum()) for s in np.unique(oof["season"])},
    }  # fmt: skip


def cap_diagnostic(out_dir):
    """Over every completed cold fit: the share that ran the whole 25-epoch cap (INVALID (budget) when it exceeds one third)."""
    infos = [json.loads(p.read_text()) for p in sorted(Path(out_dir).glob("info_*.json"))]
    fits = [(i["arm"], c) for i in infos for c in i["cold"]]
    reached = sum(c["reached_cap"] for _, c in fits)
    share = reached / len(fits) if fits else float("nan")
    return {
        "cold_fits": len(fits), "reached_cap": int(reached), "share": share, "limit": CAP_SHARE_MAX, "budget_ok": bool(fits and share <= CAP_SHARE_MAX),
        "by_arm": {i["arm"]: {"epochs_run": [c["epochs_run"] for c in i["cold"]], "best_epoch": [c["best_epoch"] for c in i["cold"]]} for i in infos},
    }  # fmt: skip


def main():
    smoke = os.environ.get("NBA_SMOKE")
    hidden = int(os.environ.get("NBA_HIDDEN", 16 if smoke else 64))
    want = tuple(
        a for a in os.environ.get("NBA_ARMS", ",".join(a[0] for a in ARMS)).split(",") if a
    )
    assert set(want) <= {a[0] for a in ARMS}, want
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print("arms", want, "expected runtime:", EST, flush=True)
    anchor = Path(os.environ.get("NBA_PARQUET", "seq_learning_curve_norm_S3.npz"))
    run_dir = anchor.parent
    sha, att = check_gates(run_dir)  # hard stops: frozen rule + committed loader review
    print(
        "rule sha256",
        sha,
        "review",
        att["review_path"],
        att["review_commit"][:8],
        "exports manifest",
        att.get("exports_manifest_sha256"),
        flush=True,
    )
    root = Path(os.environ.get("NBA_ARTIFACT_ROOT", "data/colab/artifacts"))
    stamp = os.environ.get("NBA_RUN_STAMP") or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    prior_s = sum(
        json.loads(p.read_text()).get("runtime_s", 0.0) for p in out_dir.glob("info_*.json")
    )
    cache = {}

    def segs_of(variant):
        if variant not in cache:
            cache[variant] = {
                s: load_capped(run_dir / f"seq_learning_curve_{variant}_{s}.npz") for s in SEGS
            }
            if smoke:
                cache[variant] = {s: subsample(v, int(smoke)) for s, v in cache[variant].items()}
            w = {v["ctx"].shape[1] for v in cache[variant].values()}
            assert len(w) == 1, f"segments of the {variant} variant have different ctx widths {w}"
        return cache[variant]

    infos, seen, unfinished = {}, {}, []
    t0 = time.monotonic()
    for name, variant, n, seeds in ARMS:
        if name not in want:
            continue
        if (out_dir / f"oof_{name}.npz").exists():
            print(f"{name}: done, skipping")
            continue
        if unfinished:  # the ceiling was reached: the remaining arms are reported unfinished
            unfinished.append(name)
            continue
        print(f"=== {name} ===", flush=True)
        try:
            if elapsed_min(t0, prior_s) > CEILING_MIN:
                raise CeilingReached(name)
            d = window_data(segs_of(variant), n)
            infos[name] = run_arm(
                name, variant, n, seeds, d, dev, hidden, out_dir, bool(smoke), t0, prior_s, seen
            )
        except CeilingReached as e:
            print(f"CEILING {CEILING_MIN} min reached at {e}; {name} is UNFINISHED", flush=True)
            unfinished.append(name)
            continue
        infos[name].update(
            hidden=hidden,
            smoke=bool(smoke),
            cold_cfg=COLD,
            warm_cfg=WARM,
            rule_sha256=sha,
            review_commit=att["review_commit"],
            exports=att["exports"],
        )
        (out_dir / f"info_{name}.json").write_text(json.dumps(infos[name], indent=2, default=str))
        print(
            name,
            json.dumps(
                {k: v for k, v in infos[name].items() if k not in ("blocks_detail", "exports")}
            ),
            flush=True,
        )
    done = sorted(p.stem[4:] for p in out_dir.glob("oof_*.npz"))
    cfg = {
        "rule": RULE_ID, "rule_sha256": sha, "review_commit": att["review_commit"], "cold": COLD, "warm": WARM, "cold_end": COLD_END, "hidden": hidden, "smoke": smoke,
        "seeds_main": list(SEEDS_MAIN), "seeds_rep": list(SEEDS_REP), "test_label": TEST_LABEL, "cap_season": CAP_SEASON, "ceiling_min": CEILING_MIN,
        "arms_requested": list(want), "arms_done": done, "arms_unfinished": unfinished or [a for a in want if a not in done], "exports": att["exports"],
    }  # fmt: skip
    (out_dir / "config.json").write_text(json.dumps(cfg, indent=2, default=str))
    diag = cap_diagnostic(out_dir)
    (out_dir / "cap_diagnostic.json").write_text(json.dumps(diag, indent=2, default=str))
    metrics = {
        "arms": done,
        "unfinished": cfg["arms_unfinished"],
        "cap_diagnostic": diag,
        "rule_sha256": sha,
        "runtime_s": round(prior_s + time.monotonic() - t0, 1),
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    print("ARTIFACTS WRITTEN TO", out_dir)
    return infos


if __name__ == "__main__":
    main()
