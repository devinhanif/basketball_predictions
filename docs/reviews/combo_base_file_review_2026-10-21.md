# Review: COMBO_STOCKS_SHADOW base normals as a committed file (agent/steward-combo-base-file, d1afeb6)

Reviewer: adversary. Date 2026-10-21. Base of the branch: main 60d1e12. Scope: `configs/combo_base_normals_08ddb2a2.f64` (new, 480,000 bytes),
`nba/daily/combo_shadow/config.py` (load_base, BASE_PATH, BASE_DTYPE), `tests/daily/test_combo_shadow.py`, `tests/props/test_combo_p1.py`, `docs/DAILY_PIPELINE.md`.
Nothing here reads season 2025; no holdout touch. Model class (playbook): Gaussian-copula joint model ("Set and joint models"); this change is infrastructure, not a model change.

Verdict: APPROVED

Top finding: the file is the frozen bytes exactly (file sha == array sha == FROZEN_BASE_SHA == yaml base_sha256 == 08ddb2a2, and equal bit for bit to
`base_normals(10000, 20261014)` regenerated on this Mac), so the stamped `base_id` and `model_id` cannot move; and the residual BLAS risk in P1
(`base @ chol(R).T` then integer quantiles) is about eight orders of magnitude below the nearest flip: the closest any of the 60,000 uniforms comes to a
CDF step on the P1 reference is 5.8e-8, against an ulp of 1.1e-16, and +-64 ulp noise on every z changes 0 grid cells.

## Check table

| # | Check | Command / evidence | n | Result | Pass |
|---|---|---|---|---|---|
| 1a | File sha | `git show agent/steward-combo-base-file:configs/combo_base_normals_08ddb2a2.f64 \| shasum -a 256` (exit 0) | 480,000 bytes | 08ddb2a22c08...5a3a | PASS |
| 1b | Array sha, frozen equality | scratch `adv_combo/bytes_check.py` (exit 0): `np.fromfile(p, "<f8").tobytes()` sha; `== FROZEN_BASE_SHA == yaml copula.base_sha256` | 60,000 floats | True | PASS |
| 1c | Bit-for-bit vs regeneration on this Mac (arm64 Accelerate) | same script: `np.array_equal(base_normals(10000, seed=20261014).view(u64), file.view(u64))` | 60,000 | True | PASS |
| 1d | Sanity of content | same script: antithetic halves exact; max abs(cov - I) 1.4e-15; all finite | 10,000 x 6 | as designed | PASS |
| 1e | Live path never calls `base_normals()` | `grep -rn base_normals nba/ scripts/ tests/` | n/a | only the definition (combo_copula.py:47) and docstrings; callers only in tests/props/test_combo_copula.py (tolerance or self-consistency tests, platform-safe) | PASS |
| 2a | Dtype / byte order fixed | config.py `BASE_DTYPE = "<f8"`, `np.frombuffer(buf, dtype=BASE_DTYPE)` | n/a | explicit little-endian; a big-endian misread would hash 597fae70 and be refused | PASS |
| 2b | Size check | `load_base(5000, 6)` -> refused "480000 bytes, expected 240000" | n/a | refused | PASS |
| 2c | Tamper / short / missing | branch test `test_a_tampered_base_file_is_refused` | 3 cases | refused | PASS |
| 2d | cwd independence | script `os.chdir("/")` then `load_config().base_id` | n/a | `BASE_PATH` absolute (`ROOT = Path(__file__).resolve().parents[3]`), 08ddb2a2 | PASS |
| 3a | Frozen yaml unchanged | `shasum -a 256 configs/combo_stocks_shadow.yaml`; `git diff main...branch --stat -- configs/combo_stocks_shadow.yaml docs/prereg/ reports/ nba/props/` empty | n/a | 8932ccec; empty diff | PASS |
| 3b | Rule text unchanged | same diff; `docs/prereg/COMBO_STOCKS_SHADOW.md` sha 17b8d830 equal on main and branch | n/a | unchanged | PASS |
| 3c | model_id / base_id inputs | run.py:81-93: `model_id_of` hashes recipe, params, lightgbm version, `cfg.config_sha`, `cfg.base_id`; run.py:580 stamps `cfg.base_id` | n/a | base_id is sha of identical bytes, so both ids are unchanged on the Mac | PASS |
| 4 | Residual BLAS risk in P1 | script: min distance of u = ndtr(base @ chol(R).T) to any distinct CDF value in p1_reference, per stat; regenerate grids with three summation orders (BLAS, einsum, reversed scalar loop) and with +-1/4/64 ulp noise on z | 200 rows x 10,000 draws x 6 stats | min margin 5.8e-8 (fg3m) vs ulp 1.1e-16; summation orders differ by <= 8.9e-16 and all reproduce P1 exactly; ulp noise: 0 differing cells | PASS (risk negligible) |
| 5 | Tests read | see findings | n/a | two untested branches, both minor | PASS with notes |
| 6 | Tests, lint, types | `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -p no:cacheprovider tests/daily/test_combo_shadow.py tests/daily/test_combo_shadow_reader.py tests/props/test_combo_p1.py tests/test_layering.py --no-cov -q` in wt_ci | 68 tests | "68 passed, 2 warnings in 115.75s", exit 0 | PASS |
| 6b | ruff / format / mypy | `ruff check` (3 files) exit 0; `ruff format --check` exit 0; `mypy nba/daily/combo_shadow/config.py` exit 0 | n/a | clean | PASS |

The six attack checks (shuffled labels, planted future, missingness, knockout, replication, integer support) are not applicable: no claim of skill, no
features, no scores. Integer support is relevant only as check 4 (the grids are integer, so tiny float differences matter only at a CDF step).

## Findings (severity-ranked)

No blocker, no major.

1. **minor: the rule text names the method, not just the value.** `docs/prereg/COMBO_STOCKS_SHADOW.md:69` ("What the steward builds", item 2(d)) says
   `joint_grids` runs with "`base_normals()` (one call: all five combos from the same draws)". The frozen-inputs line (`:4`) freezes the base-normal
   VALUE (sha 08ddb2a2), and the file is that value bit for bit, so this is not a rule change and not a new rule id: the rule's object is unchanged and only
   its source moved. But the text is hashed, so it cannot be edited. Fix: a dated DECISIONS row (drafted below) recording that the live writer now reads
   the identical bytes from the file; do not edit the prereg.
2. **minor: untested branch, n_draws disagreeing with the file size.** `config.py:69` (`len(buf) != n_draws * d * 8`) is exercised only by a truncated file,
   never by a config whose `n_draws` differs. Same code path, so low risk. Fix: in `test_a_tampered_base_file_is_refused` add
   `with pytest.raises(CFG.ComboConfigError): CFG.load_base(5_000, 6)`.
3. **minor: a same-size reinterpretation is caught only by the sha.** `load_base(12000, 5)` accepts the 480,000 bytes (12000 x 5 x 8 = 480,000); only
   `load_config`'s sha check plus `d = len(C.STAT_ORDER)` stops it. With `verify=False` (tests only, `config.py:83`) neither the sha nor a reshaped base is
   checked. Acceptable because live always verifies; fix optional: a one-line test that `load_config(verify=False, base_path=<tampered>)` still returns
   a `base_id != FROZEN_BASE_SHA`, so `combo_shadow_live.py:417` (integrity count) would flag it.
4. **nit: the generator's docstring does not say it is no longer the source.** `nba/props/combo_copula.py:50-55` describes `base_normals` as "the frozen
   common random numbers"; the BLAS dependence and "not called on the live path; the committed file is the frozen object" are written only in
   config.py:27-30 and DAILY_PIPELINE. Fix optional (combo_copula.py is not a frozen input): one docstring sentence. Not required for merge.
5. **nit (safe direction): base_id hashes native bytes.** `config.py:85` hashes `base.tobytes()` after `.astype(np.float64)`; on a big-endian host this
   would differ from the file sha and the writer would refuse. False refusal, never a silent pass; no such host exists here. No change needed.

Scope: the diff does what the task says and nothing more (the DAILY_PIPELINE paragraph is the matching doc). No .gitattributes rule touches `.f64`;
git treats it as binary and the blob sha equals the file sha.

## Residual CI risk (check 4) and fallback

What can still differ on ubuntu in P1 is `np.linalg.cholesky(r)` (LAPACK potrf on a 6x6) and `base @ L.T` (BLAS gemm), both at the ulp level; `ndtr`
is scipy's own code; the combo CDF is integer counts / 10,000 compared with `tau - 1e-12`, exact on any IEEE platform. A grid cell flips only if some
uniform sits within an ulp or so of a marginal CDF step; on the P1 reference the closest is 5.8e-8, so the risk is negligible. The INDEP arm uses
`chol(I) = I` exactly. PR #1's ubuntu CI result was not available to me; if P1 still fails there, the cause is not the base and the fix is to freeze
`L = chol(R)` the same way (a committed `<f8` 6x6, sha-checked) rather than any tolerance; a tolerance on integer grids would have to be "zero
differing cells" anyway. The live writer runs only on the Mac, where the bytes are the frozen ones either way.

## Playbook check

Information or capacity: neither; a reproducibility fix. Enough data: n/a. Test validity: the P1 test now compares the frozen grid against the frozen
inputs on any BLAS, which is what it was meant to test; before, ubuntu tested a different base and could only fail. Lineage and inherited guards:
the sha refusal (review B1 of the rule) is kept and now also covers the file. Scored against: nothing changes in scoring; `base_id` and `model_id`
stamps unchanged.

## What I could not verify

- PR #1's ubuntu CI outcome (not available at writing).
- Bit-for-bit P1 on ubuntu itself; my BLAS-difference emulation (alternative summation orders, ulp noise) is a proxy, not the ubuntu LAPACK.
- The live writer end to end on a real slate (not run; the 68 tests include the shadow writer tests on synthetic data).

## Drafted DECISIONS row (not appended)

| 2026-10-21 | COMBO_STOCKS_SHADOW base normals are read from the committed file `configs/combo_base_normals_08ddb2a2.f64` (the frozen 08ddb2a2 bytes, verified equal bit for bit to `base_normals(10000, 20261014)` on the Mac) instead of being regenerated; the rule text's "`base_normals()`" (prereg line 69, item 2(d)) now means "the frozen base those calls produced on the freezing Mac". Not a new rule id: the frozen object is the sha (prereg line 4), unchanged. | Regeneration depends on the BLAS build (ubuntu 588b625d) and kept main's CI red from 10-15. | Any byte of the file differing from 08ddb2a2, or a case where the file and the rule text's intended draws disagree. |
