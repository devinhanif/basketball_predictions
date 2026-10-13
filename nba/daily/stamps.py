"""Model stamps: which fitted weights, on which data, produced a forward prediction run.

One row per (run_id, model_name) in the NEW table ``forward_model_stamps``, written by the daily
run right after its prediction rows. It is a separate table so ``forward_predictions`` and the
canonical dump (replay oracle) cannot see it; join to the predictions on ``run_id`` + model_name.

``weights_sha256`` is a sha256 of a canonical serialization of the fitted state
(``canonical_digest``): sorted dict keys, arrays as dtype + shape + bytes, LightGBM boosters as
their text dump, dataclasses and plain objects field by field. Nothing is pickled (pickle bytes
are not canonical). Rule-based models (recency average, rolling baseline) have no fitted weights:
their digest covers the rule's name and version and ``weights_scope`` says so.

Writing a stamp must never block or fail a prediction run: ``write_stamps`` has its own
try/except and returns a status string for the run summary; the daily job also raises a
shadow-error alert on failure.
"""

from __future__ import annotations

import dataclasses
import hashlib
import pickle
import struct
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

TABLE = "forward_model_stamps"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    run_id VARCHAR,
    model_name VARCHAR,
    version VARCHAR,
    made_at TIMESTAMP,
    weights_sha256 VARCHAR,
    weights_scope VARCHAR,     -- what the digest covers (and what it cannot)
    data_version VARCHAR,      -- repo data manifest at stamp time (nba.datamanifest), NULL if none
    fit_through DATE,          -- newest training date (strictly before the slate date)
    n_train BIGINT,
    git_sha VARCHAR,
    git_dirty BOOLEAN,
    stamped_at TIMESTAMP
);
"""

_MAX_DEPTH = 12


class _Digest:
    def __init__(self) -> None:
        self.h = hashlib.sha256()
        self.approx = 0  # objects hashed by repr (not canonical by construction)

    def tag(self, t: str) -> None:
        self.h.update(t.encode() + b"\x00")

    def feed(self, obj: Any, depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            self.approx += 1
            self.tag("deep")
            return
        if obj is None:
            self.tag("N")
        elif isinstance(obj, bool):
            self.tag("b1" if obj else "b0")
        elif isinstance(obj, int | np.integer):
            self.tag(f"i{int(obj)}")
        elif isinstance(obj, float | np.floating):
            self.tag("f")
            self.h.update(struct.pack("<d", float(obj)))
        elif isinstance(obj, str):
            self.tag("s")
            self.h.update(obj.encode() + b"\x00")
        elif isinstance(obj, bytes):
            self.tag(f"B{len(obj)}")
            self.h.update(obj)
        elif isinstance(obj, np.ndarray):
            arr = np.ascontiguousarray(obj)
            self.tag(f"a{arr.dtype.str}{arr.shape}")
            self.h.update(arr.tobytes() if arr.dtype != object else repr(arr.tolist()).encode())
        elif isinstance(obj, date | datetime):
            self.tag("d" + obj.isoformat())
        elif isinstance(obj, dict):
            self.tag(f"D{len(obj)}")
            for k in sorted(obj, key=lambda x: (type(x).__name__, str(x))):
                self.feed(k, depth + 1)
                self.feed(obj[k], depth + 1)
        elif isinstance(obj, list | tuple):
            self.tag(f"L{len(obj)}")
            for v in obj:
                self.feed(v, depth + 1)
        elif hasattr(obj, "booster_") and hasattr(obj.booster_, "model_to_string"):  # LGBMModel
            self.tag("lgbm")
            self.feed(obj.booster_.model_to_string(), depth + 1)
        elif hasattr(obj, "model_to_string"):  # lightgbm.Booster
            self.tag("booster")
            self.feed(obj.model_to_string(), depth + 1)
        elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            self.tag("dc:" + type(obj).__qualname__)
            self.feed(
                {f.name: getattr(obj, f.name, None) for f in dataclasses.fields(obj)}, depth + 1
            )
        elif hasattr(obj, "__dict__"):
            self.tag("o:" + type(obj).__qualname__)
            self.feed(dict(vars(obj)), depth + 1)
        else:
            self.approx += 1
            self.tag("r:" + type(obj).__qualname__)
            self.h.update(repr(obj).encode())


def canonical_digest(obj: Any) -> tuple[str, int]:
    """(sha256 hex, number of sub-objects hashed by repr). 0 means fully canonical."""
    d = _Digest()
    d.feed(obj)
    return d.h.hexdigest(), d.approx


@dataclasses.dataclass(frozen=True)
class Stamp:
    model_name: str
    version: str
    weights_sha256: str
    weights_scope: str
    fit_through: date | None
    n_train: int | None


def elo_stamp(
    model_name: str, version: str, ratings: dict[int, float], params: dict[str, float],
    fit_through: date | None, n_train: int,
) -> Stamp:  # fmt: skip
    sha, _ = canonical_digest({"kind": "mov_elo", "ratings": ratings, "params": params})
    return Stamp(
        model_name, version, sha, "mov-elo ratings after fit + elo params", fit_through, n_train
    )


def payload_stamp(
    model_name: str, version: str, payload: dict[str, Any], keys: Iterable[str], scope: str,
    fit_through: date | None, n_train: int | None,
) -> Stamp:  # fmt: skip
    """Digest of selected fitted-parameter fields a model exposes on its prediction payload."""
    sha, _ = canonical_digest({k: payload.get(k) for k in sorted(keys)})
    return Stamp(model_name, version, sha, scope, fit_through, n_train)


def rule_stamp(model_name: str, version: str) -> Stamp:
    sha, _ = canonical_digest({"kind": "rule", "model": model_name, "version": version})
    return Stamp(
        model_name, version, sha, "rule-based, no fitted weights (name + version only)", None, None
    )


def models_digest(models: dict[str, Any]) -> tuple[str, int]:
    """Digest of the per-stat ContextResidualModel dict (heads, conformal arrays, lower tail)."""
    return canonical_digest({"kind": "context_residual", "models": models})


def load_cached_models(cache_dir: Path) -> dict[str, Any] | None:
    p = cache_dir / "models.pkl"
    if not p.exists():
        return None
    with p.open("rb") as f:
        loaded = pickle.load(f)  # noqa: S301  (local cache the pipeline itself wrote)
    return loaded if isinstance(loaded, dict) else None


def current_data_version() -> str | None:
    try:
        from nba.datamanifest.manifest import latest_data_version

        return latest_data_version()
    except Exception:
        return None


def _parse_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def collect_stamps(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    preds: list[Any],
    summary: Any,
    model_cache: Path | None,
) -> list[Stamp]:
    """One Stamp per distinct model_name in ``preds``, from what the run stashed in
    ``summary.stamp_inputs`` (Elo ratings + params; the props fit facts) and the model cache."""
    from nba.daily.predict import (
        CONTEXT_INT_PROPS_MODEL_NAME,
        CONTEXT_LT_PROPS_MODEL_NAME,
        CONTEXT_PROPS_MODEL_NAME,
        DEFAULT_CONTEXT_CACHE,
        ELO_MODEL_NAME,
        INJURY_ELO_MODEL_NAME,
    )

    inputs: dict[str, Any] = summary.stamp_inputs
    first: dict[str, Any] = {}
    for p in preds:
        first.setdefault(p.model_name, p)
    elo = inputs.get("elo")
    fit_through = None
    if elo is not None:
        row = con.execute(
            "SELECT max(game_date) FROM games "
            "WHERE game_date < ? AND home_pts > 0 AND away_pts > 0",
            [run_date],
        ).fetchone()
        fit_through = row[0] if row else None
    ctx_names = {
        CONTEXT_PROPS_MODEL_NAME,
        CONTEXT_INT_PROPS_MODEL_NAME,
        CONTEXT_LT_PROPS_MODEL_NAME,
    }
    ctx_digest: str | None = None
    ctx_scope = ""
    if ctx_names & first.keys():
        info = inputs.get("props_info")
        cache_dir = (model_cache or DEFAULT_CONTEXT_CACHE) / run_date.isoformat()
        models = load_cached_models(cache_dir) if info is not None else None
        if models is None:
            ctx_scope = (
                "unavailable: no fitted context model this run (recency fallback) or cache missing"
            )
        else:
            ctx_digest, approx = models_digest(models)
            ctx_scope = (
                "per-stat LightGBM mean+scale heads, conformal z arrays, lower-tail state, cfg"
                + (f"; {approx} sub-objects hashed by repr" if approx else "")
            )
    out: list[Stamp] = []
    for name, p in first.items():
        version = str(p.version)
        if name == ELO_MODEL_NAME and elo is not None:
            out.append(
                elo_stamp(name, version, elo["ratings"], elo["params"], fit_through, elo["n_train"])
            )
        elif name == INJURY_ELO_MODEL_NAME and elo is not None:
            out.append(
                payload_stamp(
                    name, version, {**p.prediction, "elo_params": elo["params"]},
                    ("coef_out", "coef_doubt", "n_train_games", "n_signal_games", "elo_params"),
                    "ridge coefficients + train counts + elo params (Elo ratings are refit "
                    "inside predict_games and not exposed)",
                    fit_through, int(p.prediction.get("n_train_games") or 0),
                )
            )  # fmt: skip
        elif name in ctx_names:
            info = inputs.get("props_info") or {}
            n = info.get("n_train_rows")
            out.append(
                Stamp(name, version, ctx_digest or "unavailable", ctx_scope,
                      _parse_date(info.get("train_through")), int(n) if n is not None else None)
            )  # fmt: skip
        elif name in {ELO_MODEL_NAME, INJURY_ELO_MODEL_NAME}:
            out.append(
                Stamp(name, version, "unavailable", "fit inputs not captured this run", None, None)
            )
        else:
            out.append(rule_stamp(name, version))
    return out


def write_stamps(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    made_at: datetime,
    stamps: list[Stamp],
    *,
    git_sha: str,
    git_dirty: bool | None,
    data_version: str | None,
    stamped_at: datetime,
) -> str:
    """Insert the stamps; returns ``"ok: N rows"`` or ``"failed: ..."``. Never raises."""
    try:
        con.execute(DDL)
        rows = [
            (
                run_id, s.model_name, s.version, made_at, s.weights_sha256, s.weights_scope,
                data_version, s.fit_through, s.n_train, git_sha, git_dirty, stamped_at,
            )
            for s in stamps
        ]  # fmt: skip
        con.executemany(f"INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return f"ok: {len(rows)} rows"
    except Exception as exc:
        return f"failed: {type(exc).__name__}: {exc}"[:200]
