# ruff: noqa
# mypy: ignore-errors
# ----------------------------------------------------------------------------------------
# SEQ_LEARNING_CURVE_2 training programs (docs/prereg/SEQ_LEARNING_CURVE_2.md). TAIL appended by build_notebook.py to the model code of
# ../seq_props/seq_props_train.py (everything before its own `main`), so the transformer, the loss, `fit` and `predict` are exactly the RT / SEQ_LEARNING_CURVE ones.
#
# One UNIT = one window N (the N seasons ending 2022-23 as training target rows), scored walk-forward on 2023-24 (labels == 2023) ONLY:
#   1. cold fit per seed 0..5 (early stopping on the common 2022 validation set; best epoch recorded). Cold nets are saved, so a restart resumes.
#   2. U_N  : unmatched warm walk (before every calendar month of 2023-24: WARM epochs over the whole pool), exactly SEQ_LEARNING_CURVE's.
#   3. M_N  : step-matched warm walk, N = 6, 8, 9, 10: at each refit the number of gradient steps = 3 * ceil(pool_4 / 1024) (what U_4 takes at that refit); batches are
#             drawn from the full window-N pool by consecutive random-permutation sweeps. Same lr, fresh AdamW per refit, same warm seeds as U.
#   4. UP_4 : N = 4 only: window-4 data and nets, warm steps = 3 * ceil(pool_8 / 1024) (what U_8 takes at that refit).
# M_N and UP_4 start from a copy of the same cold nets as U_N (cold fit "unchanged" in the rule; the contrasts are paired on the cold fit). M_4 = U_4 by definition (no separate arm).
# The job refuses to start without (1) the staged frozen rule document and (2) the loader-review attestation written by the Mac-side `stage` command after
# `research.eval.seq_learning_curve_2_rule.require_review`. It scores nothing; the CPU scorer does. Ceiling 240 T4 minutes cumulative across restarts; a unit not finished is
# reported unfinished, never cut silently.
# ----------------------------------------------------------------------------------------
import copy
import hashlib

RULE_ID = "SEQ_LEARNING_CURVE_2"
RULE_SHA = "__RULE_SHA__"  # sha256 of the frozen document's text above the marker
RULE_DOC = "SEQ_LEARNING_CURVE_2.md"
ATTESTATION = "loader_review_attestation.json"
RESULTS_MARKER = "=== RESULTS BELOW ===\n"
CAP_SEASON = 2023  # the select season: nothing later is ever loaded
TEST_LABEL = 2023
COLD = dict(epochs=25, patience=4, lr=1e-3, batch=1024)
WARM = dict(epochs=3, lr=3e-4, batch=1024)
COLD_END = "2023-04-01"  # 2022-season rows on/after this are the early-stopping slice (the same set for every N)
SEEDS = (0, 1, 2, 3, 4, 5)
CEILING_MIN = 240.0
CAP_SHARE_MAX = 1.0 / 3.0
SEGS = ("S1", "S2", "S3")
S1_LABELS = (2013, 2014, 2015, 2016, 2017, 2018)
S2_LABELS = (2019, 2020, 2021)
EXPORT_FMT = "seq_learning_curve_2_norm_{s}.npz"
# units in priority order (the primary contrast needs U4 and M8, the second E needs M10 and M8); each unit lists the arms it writes
UNITS = (
    (4, ("U_4", "UP_4")),
    (8, ("U_8", "M_8")),
    (10, ("U_10", "M_10")),
    (6, ("U_6", "M_6")),
    (9, ("U_9", "M_9")),
)
ARM_NAMES = tuple(a for _, arms in UNITS for a in arms)  # 10 arms
ROW_KEYS = (
    "seq", "mask", "opp_step", "ctx", "player_id", "team_idx", "opp_idx", "date_days", "season",
    "game_id", "y", "base", "n_prior", "norm_c", "date_str",
)  # fmt: skip
TEST_INPUT_KEYS = ("seq", "mask", "opp_step", "ctx", "team_idx", "opp_idx", "y", "base", "norm_c")
EST = "about 90-170 T4 minutes (estimate, not measured): 5 units, cold fits shared by U/M/UP, 9 monthly warm refits per walk; ceiling 240"


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
            f"{a} not staged: the adversary's committed loader review (docs/reviews/seq_learning_curve_2_loader_<date>.md, a descendant of the freeze commit) is a hard stop; "
            "run `python -m research.eval.seq_learning_curve_2_rule stage` on the Mac"
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


# --------------------------------------------------------------------------- windows (a test pins these equal to the parent exporter's for N <= 8)
def window_labels(n):
    if not 1 <= n <= 10:
        raise ValueError("windows 1..10 only")
    return tuple(range(2022 - n + 1, 2023))


def window_masks(season, date_days, n_prior, n):
    season = np.asarray(season)
    date = np.asarray(date_days).astype("datetime64[D]")
    in_win = np.isin(season, window_labels(n))
    cold_end = np.datetime64(COLD_END)
    fit = (n_prior >= 3) & in_win & (((season == 2022) & (date < cold_end)) | (season < 2022))
    val = (n_prior >= 3) & (season == 2022) & (date >= cold_end)
    test = season == TEST_LABEL
    pool_elig = (n_prior >= 3) & (in_win | test)
    return {"fit": fit, "val": val, "test": test, "pool_eligible": pool_elig}


def window_data(segs, n):
    """Rows of window N: S1 / S2 rows with season label in the window; S3 whole (2022, 2023)."""
    labs = window_labels(n)
    parts = []
    for s in SEGS:
        if s == "S3":
            parts.append(segs[s])
        elif any(l in labs for l in (S1_LABELS if s == "S1" else S2_LABELS)):
            parts.append(subset(segs[s], np.flatnonzero(np.isin(segs[s]["season"], labs))))
    return concat_all(parts)


def meta_of(segs):
    """(season, date_days, n_prior) over every exported row, for pool-size counts of windows other than the unit's own."""
    return tuple(
        np.concatenate([segs[s][k] for s in SEGS]) for k in ("season", "date_days", "n_prior")
    )


def pool_count(meta, n, start):
    """Rows in window N's warm pool before ``start`` (n_prior >= 3, window seasons or the 2023-24 test season)."""
    season, date_days, nprior = meta
    date = date_days.astype("datetime64[D]")
    return int(
        ((date < start) & (nprior >= 3) & np.isin(season, (*window_labels(n), TEST_LABEL))).sum()
    )


def steps_of(pool_n, batch, epochs):
    """Gradient steps `fit` takes over ``epochs`` epochs of ``pool_n`` rows (the last partial batch counts)."""
    return epochs * ((pool_n + batch - 1) // batch)


def fit_steps(net, ts, rows, lr, steps, batch, dev):
    """AdamW (as `fit`), exactly ``steps`` gradient steps drawn by consecutive random-permutation sweeps of ``rows`` (a batch never repeats within a sweep). With
    steps == epochs * ceil(len(rows) / batch) and the same seed this is bit-identical to `fit` with that many epochs and no validation (tested on CPU)."""
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=5e-2)
    scaler = torch.amp.GradScaler("cuda", enabled=dev.type == "cuda")
    net.train(True)
    done = 0
    while done < steps:
        perm = rows[torch.randperm(len(rows), device=rows.device)]
        for i in range(0, len(perm), batch):
            if done >= steps:
                break
            b = ts.batch(perm[i : i + batch])
            with torch.autocast(
                device_type=dev.type, dtype=torch.float16, enabled=dev.type == "cuda"
            ):
                mean, q = net(b)
            loss = loss_fn(mean.float(), q.float(), b["y"], net.scale)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            nn.utils.clip_grad_norm_(net.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            done += 1
    return done


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


def walk(name, kind, d, m, nets, seeds, dev, ts, meta, warm, batch, n, t0, prior_s, out_dir):
    """The monthly warm walk over 2023-24 for one arm. kind: U (epochs), M (steps = U_4's at that refit), UP (window-4 nets, steps = U_8's at that refit)."""
    date = d["date_days"].astype("datetime64[D]")
    season = d["season"]
    test_rows = np.flatnonzero(m["test"])
    frames, detail = [], []
    blocks = list(month_blocks(date, test_rows))
    for bi, (per, blk) in enumerate(blocks):
        if elapsed_min(t0, prior_s) > CEILING_MIN:
            raise CeilingReached(f"{name} block {bi}")
        start = np.datetime64(per.start_time.date())
        pool = np.flatnonzero((date < start) & m["pool_eligible"])
        assert date[pool].max() < date[blk].min(), "a pool row is not strictly before the block"
        prow = to_dev(pool, dev)
        steps = None
        if kind == "M":
            steps = steps_of(pool_count(meta, 4, start), batch, warm["epochs"])
        elif kind == "UP":
            steps = steps_of(pool_count(meta, 8, start), batch, warm["epochs"])
        for sd, net in zip(seeds, nets):
            seed_everything(1000 + sd)
            if kind == "U":
                fit(net, ts, prow, None, warm, dev, warm["lr"], warm["epochs"], 99)
            else:
                fit_steps(net, ts, prow, warm["lr"], steps, batch, dev)
        mean, q = ensemble([predict(x, ts, to_dev(blk, dev), batch, dev) for x in nets])
        frames.append(oof_dict(d, blk, mean, q))
        detail.append(
            {
                "block": str(per), "n": int(len(blk)), "pool": int(len(pool)), "warm_steps": int(steps) if steps is not None else steps_of(len(pool), batch, warm["epochs"]),
                "pool_by_season": {str(int(s)): int((season[pool] == s).sum()) for s in np.unique(season[pool])},
                "max_pool_date": str(date[pool].max()), "min_block_date": str(date[blk].min()),
            }
        )  # fmt: skip
        print(
            f"  [{name}] block {bi + 1}/{len(blocks)} {per}: n={len(blk)} pool={len(pool)} steps={detail[-1]['warm_steps']} total {elapsed_min(t0, prior_s):.1f} min",
            flush=True,
        )
    oof = {k: np.concatenate([f[k] for f in frames]) for k in frames[0]}
    np.savez_compressed(out_dir / f"oof_{name}.npz", **oof)
    return {"arm": name, "kind": kind, "window_n": n, "blocks": len(blocks), "n_oof": int(len(oof["game_id"])), "blocks_detail": detail,
            "oof_by_season": {str(int(s)): int((oof["season"] == s).sum()) for s in np.unique(oof["season"])}}  # fmt: skip


def run_unit(n, arms, segs, dev, hidden, out_dir, smoke, t0, prior_s, seen):
    """One window: cold fits (resumable), then the walks for every arm of the unit that has no OOF yet."""
    ta = time.monotonic()
    cold, warm = dict(COLD), dict(WARM)
    seeds = SEEDS
    if smoke:
        seeds = SEEDS[:2]
        cold.update(epochs=1, batch=256)
        warm.update(epochs=1, batch=256)
    d = window_data(segs, n)
    meta = meta_of(segs)
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
        raise AssertionError(f"window {n}: the 2023-24 test row set differs from the first unit's")
    if seen.setdefault("test_inputs", ts_) != ts_:
        raise AssertionError(f"window {n}: the 2023-24 test inputs differ from the first unit's")
    vocab = build_vocab(d["player_id"], np.flatnonzero(fit_m))
    ts = Tensors(d, vocab, dev)
    tr, va = to_dev(np.flatnonzero(fit_m), dev), to_dev(np.flatnonzero(val_m), dev)
    n_step, n_ctx = d["seq"].shape[2], d["ctx"].shape[1]
    colds, nets = [], []
    for sd in seeds:
        if elapsed_min(t0, prior_s) > CEILING_MIN:
            raise CeilingReached(f"window {n} cold seed {sd}")
        seed_everything(sd)
        net = SeqNet(n_step, n_ctx, len(vocab), d["stat_scale"], "transformer", d=hidden).to(dev)
        ck = out_dir / f"cold_{n}_{sd}.pt"
        if ck.exists():
            blob = torch.load(ck, map_location=dev)
            net.load_state_dict(blob["state"])
            colds.append(blob["record"])
            print(f"[window {n} seed {sd}] cold fit restored from {ck.name}", flush=True)
        else:
            print(
                f"[window {n} seed {sd}] cold fit rows {int(fit_m.sum())} val {int(val_m.sum())}",
                flush=True,
            )
            best, hist = fit(
                net, ts, tr, va, cold, dev, cold["lr"], cold["epochs"], cold["patience"]
            )
            rec = {
                "seed": int(sd),
                "epochs_run": len(hist),
                "best_epoch": best_epoch(hist),
                "best_val": float(best),
                "reached_cap": len(hist) >= cold["epochs"],
            }
            torch.save({"state": net.state_dict(), "record": rec}, ck)
            colds.append(rec)
        nets.append(net)
    infos = {}
    for name in arms:
        if (out_dir / f"oof_{name}.npz").exists():
            print(f"{name}: done, skipping")
            continue
        kind = name.split("_")[0]
        print(f"=== {name} ===", flush=True)
        walk_nets = [copy.deepcopy(x) for x in nets]
        info = walk(
            name,
            kind,
            d,
            m,
            walk_nets,
            seeds,
            dev,
            ts,
            meta,
            warm,
            warm["batch"],
            n,
            t0,
            prior_s,
            out_dir,
        )
        info.update(
            variant="norm", window_labels=list(window_labels(n)), seeds=list(seeds), n_fit=int(fit_m.sum()), n_val=int(val_m.sum()), n_test=int(test_m.sum()),
            cold=colds, cold_fits_reached_cap=int(sum(c["reached_cap"] for c in colds)), cold_fits=len(colds), test_keys_sha256=ks, test_inputs_sha256=ts_,
            runtime_s=round(time.monotonic() - ta, 1),
        )  # fmt: skip
        infos[name] = info
    return infos


def cap_diagnostic(out_dir):
    """Over every completed cold fit: the share that ran the whole 25-epoch cap (INVALID (budget) when it exceeds one third)."""
    infos = [json.loads(p.read_text()) for p in sorted(Path(out_dir).glob("info_*.json"))]
    seen_fits, fits = set(), []
    for (
        i
    ) in infos:  # cold fits are shared by the arms of one window: count each (window, seed) once
        for c in i["cold"]:
            if (i["window_n"], c["seed"]) not in seen_fits:
                seen_fits.add((i["window_n"], c["seed"]))
                fits.append((i["arm"], c))
    reached = sum(c["reached_cap"] for _, c in fits)
    share = reached / len(fits) if fits else float("nan")
    return {
        "cold_fits": len(fits), "reached_cap": int(reached), "share": share, "limit": CAP_SHARE_MAX, "budget_ok": bool(fits and share <= CAP_SHARE_MAX),
        "by_arm": {i["arm"]: {"epochs_run": [c["epochs_run"] for c in i["cold"]], "best_epoch": [c["best_epoch"] for c in i["cold"]]} for i in infos},
    }  # fmt: skip


def main():
    smoke = os.environ.get("NBA_SMOKE")
    hidden = int(os.environ.get("NBA_HIDDEN", 16 if smoke else 64))
    want = tuple(a for a in os.environ.get("NBA_ARMS", ",".join(ARM_NAMES)).split(",") if a)
    assert set(want) <= set(ARM_NAMES), want
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", dev, torch.cuda.get_device_name(0) if dev.type == "cuda" else "")
    print("arms", want, "expected runtime:", EST, flush=True)
    anchor = Path(os.environ.get("NBA_PARQUET", EXPORT_FMT.format(s="S3")))
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
    segs = {s: load_capped(run_dir / EXPORT_FMT.format(s=s)) for s in SEGS}
    if smoke:
        segs = {s: subsample(v, int(smoke)) for s, v in segs.items()}
    assert len({v["ctx"].shape[1] for v in segs.values()}) == 1, (
        "segments have different ctx widths"
    )
    infos, seen, unfinished = {}, {}, []
    t0 = time.monotonic()
    for n, arms in UNITS:
        todo = [a for a in arms if a in want and not (out_dir / f"oof_{a}.npz").exists()]
        for a in arms:
            if a in want and a not in todo:
                print(f"{a}: done, skipping")
        if not todo:
            continue
        if unfinished:
            unfinished.extend(todo)
            continue
        print(f"=== window {n}: {todo} ===", flush=True)
        try:
            if elapsed_min(t0, prior_s) > CEILING_MIN:
                raise CeilingReached(f"window {n}")
            got = run_unit(n, todo, segs, dev, hidden, out_dir, bool(smoke), t0, prior_s, seen)
        except CeilingReached as e:
            print(f"CEILING {CEILING_MIN} min reached at {e}; {todo} UNFINISHED", flush=True)
            unfinished.extend(todo)
            continue
        for name, info in got.items():
            info.update(
                hidden=hidden,
                smoke=bool(smoke),
                cold_cfg=COLD,
                warm_cfg=WARM,
                rule_sha256=sha,
                review_commit=att["review_commit"],
                exports=att["exports"],
            )
            (out_dir / f"info_{name}.json").write_text(json.dumps(info, indent=2, default=str))
            infos[name] = info
            print(
                name,
                json.dumps(
                    {k: v for k, v in info.items() if k not in ("blocks_detail", "exports")}
                ),
                flush=True,
            )
    done = sorted(p.stem[4:] for p in out_dir.glob("oof_*.npz"))
    cfg = {
        "rule": RULE_ID, "rule_sha256": sha, "review_commit": att["review_commit"], "cold": COLD, "warm": WARM, "cold_end": COLD_END, "hidden": hidden, "smoke": smoke,
        "seeds": list(SEEDS), "test_label": TEST_LABEL, "cap_season": CAP_SEASON, "ceiling_min": CEILING_MIN, "units": [[n, list(a)] for n, a in UNITS],
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
