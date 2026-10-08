"""Read-mostly operator helpers for the local registry.

Everything here either reads the ``experiments`` table through a
*read-only* DuckDB connection (DuckDB is single-writer; a concurrent eval
may hold the write lock) or delegates a single, short write to
:class:`nba.registry.local.LocalRegistry`.

Promotion is explicit and manual. :func:`check_promotable` defines the rule
a version's stored evidence must satisfy before ``promote`` is allowed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import duckdb

from nba.registry.local import LocalRegistry
from nba.registry.metadata import build_run_metadata
from nba.registry.model_gate import GateFailure, evaluate_gate

#: Minimum distinct seasons a backtest must span to count as "multi-season".
MIN_BACKTEST_SEASONS = 3
#: Minimum scored rows (games, or player-games for props) behind the metrics.
MIN_BACKTEST_N = 1000
#: Fallback (no explicit marker): date span of the backtest must be at least
#: this many days. Three NBA seasons span >= ~2.7 years; 900 days is a
#: deliberately lenient floor that every one- or two-season run fails.
MIN_BACKTEST_SPAN_DAYS = 900

#: Metrics where lower is better (used by ``compare`` and the local gate).
LOWER_IS_BETTER = frozenset({"log_loss", "brier", "ece", "crps", "mae", "rmse"})


@dataclass(frozen=True)
class VersionRow:
    model_name: str
    version: str
    stage: str
    alias: str | None
    rung: int | None
    created_at: dt.datetime | None
    metrics: dict[str, Any]
    metadata: dict[str, Any]
    tags: dict[str, Any]
    artifact_path: str | None


def _loads(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except ValueError:
            return {}
        return cast("dict[str, Any]", val) if isinstance(val, dict) else {}
    return {}


def _version_key(v: str) -> int:
    return int(v[1:]) if v.startswith("v") and v[1:].isdigit() else -1


def open_readonly(db_path: Path | str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path), read_only=True)


def fetch_versions(
    con: duckdb.DuckDBPyConnection, model_name: str | None = None
) -> list[VersionRow]:
    """All registered versions (optionally one model), sorted by name then version."""
    sql = (
        "SELECT model_name, version, stage, alias, rung, created_at, metrics, metadata, "
        "tags, artifact_path FROM experiments WHERE model_name IS NOT NULL "
        "AND version IS NOT NULL"
    )
    params: list[object] = []
    if model_name is not None:
        sql += " AND model_name = ?"
        params.append(model_name)
    rows = con.execute(sql, params).fetchall()
    out = [
        VersionRow(
            model_name=r[0],
            version=r[1],
            stage=r[2] or "candidate",
            alias=r[3],
            rung=r[4],
            created_at=r[5],
            metrics=_loads(r[6]),
            metadata=_loads(r[7]),
            tags=_loads(r[8]),
            artifact_path=r[9],
        )
        for r in rows
    ]
    out.sort(key=lambda v: (v.model_name, _version_key(v.version)))
    return out


def get_version(
    con: duckdb.DuckDBPyConnection,
    model_name: str,
    version: str | None = None,
    alias: str | None = None,
) -> VersionRow:
    """Resolve one version by explicit ``version`` or by ``alias`` (default production)."""
    rows = fetch_versions(con, model_name)
    if version is not None:
        for r in rows:
            if r.version == version:
                return r
        raise LookupError(f"no version {version!r} for model {model_name!r}")
    wanted = alias or "production"
    matches = [r for r in rows if r.alias == wanted or r.stage == wanted]
    if not matches:
        raise LookupError(f"no {wanted!r} version for model {model_name!r}")
    return matches[-1]


def key_metrics(metrics: Mapping[str, Any]) -> dict[str, float]:
    """Headline scalar metrics for tabular display (flat or props-style ``stats``)."""
    out: dict[str, float] = {}
    for k in ("log_loss", "brier", "ece"):
        v = metrics.get(k)
        if isinstance(v, int | float):
            out[k] = float(v)
    n = metrics.get("n_games")
    if isinstance(n, int | float):
        out["n"] = float(n)
    stats = metrics.get("stats")
    if isinstance(stats, list) and stats:
        crps = [s["crps"] for s in stats if isinstance(s, dict) and "crps" in s]
        if crps:
            out["crps_mean"] = float(sum(crps) / len(crps))
        ns = [s["n"] for s in stats if isinstance(s, dict) and "n" in s]
        if ns:
            out["n"] = float(min(ns))
    return out


def _fmt(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.0f}" if abs(v) >= 100 else f"{v:.4f}"


def format_list(rows: list[VersionRow]) -> str:
    header = ["model", "ver", "stage", "ll", "brier", "ece", "crps", "n"]
    table: list[list[str]] = [header]
    for r in rows:
        km = key_metrics(r.metrics)
        table.append(
            [
                r.model_name,
                r.version,
                r.stage,
                _fmt(km.get("log_loss")),
                _fmt(km.get("brier")),
                _fmt(km.get("ece")),
                _fmt(km.get("crps_mean")),
                _fmt(km.get("n")),
            ]
        )
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in table]
    return "\n".join(lines)


def compare_versions(a: VersionRow, b: VersionRow) -> list[tuple[str, float, float, float, str]]:
    """Return ``(metric, a, b, b-a, verdict)`` for every shared flat numeric metric."""
    out: list[tuple[str, float, float, float, str]] = []
    for k, va in a.metrics.items():
        vb = b.metrics.get(k)
        if isinstance(va, bool) or isinstance(vb, bool):
            continue
        if not isinstance(va, int | float) or not isinstance(vb, int | float):
            continue
        delta = float(vb) - float(va)
        if k in LOWER_IS_BETTER:
            verdict = "better" if delta < 0 else ("worse" if delta > 0 else "same")
        else:
            verdict = ""
        out.append((k, float(va), float(vb), delta, verdict))
    return out


# -- promotion rule ------------------------------------------------------


def _sample_size(metrics: Mapping[str, Any]) -> int:
    n = metrics.get("n_games")
    if isinstance(n, int | float):
        return int(n)
    stats = metrics.get("stats")
    if isinstance(stats, list):
        ns = [int(s["n"]) for s in stats if isinstance(s, dict) and "n" in s]
        if ns:
            return min(ns)
    return 0


def _span_days(metadata: Mapping[str, Any]) -> int | None:
    lo, hi = metadata.get("game_date_min"), metadata.get("game_date_max")
    if not isinstance(lo, str) or not isinstance(hi, str):
        return None
    try:
        return (dt.date.fromisoformat(hi) - dt.date.fromisoformat(lo)).days
    except ValueError:
        return None


def _all_finite(metrics: Mapping[str, Any]) -> bool:
    for v in metrics.values():
        if isinstance(v, float) and not math.isfinite(v):
            return False
        if isinstance(v, list):
            for s in v:
                if isinstance(s, dict) and not _all_finite(s):
                    return False
    return True


def check_promotable(row: VersionRow) -> tuple[bool, str]:
    """Decide whether a version carries a full multi-season backtest.

    Rule (all must hold):

    1. not a ``--scenario`` run (``tags.scenario`` falsy);
    2. at least ``MIN_BACKTEST_N`` scored rows (``n_games`` or min props ``n``);
    3. every float metric is finite;
    4. multi-season evidence, either
       a. explicit marker: ``metrics.backtest_scope == "multi_season"`` and
          ``metrics.n_seasons >= 3``; or
       b. derived: ``metadata.game_date_max - game_date_min >= 900`` days.
    """
    if row.tags.get("scenario"):
        return False, "version is tagged as a --scenario run; scenarios are never promoted"
    n = _sample_size(row.metrics)
    if n < MIN_BACKTEST_N:
        return False, f"only n={n} scored rows (< {MIN_BACKTEST_N}); not a full backtest"
    if not _all_finite(row.metrics):
        return False, "metrics contain NaN/inf"
    scope = row.metrics.get("backtest_scope")
    n_seasons = row.metrics.get("n_seasons")
    if scope == "multi_season" and isinstance(n_seasons, int | float):
        if n_seasons >= MIN_BACKTEST_SEASONS:
            return True, f"explicit marker: multi_season, n_seasons={int(n_seasons)}, n={n}"
        return False, f"marker says n_seasons={int(n_seasons)} (< {MIN_BACKTEST_SEASONS})"
    span = _span_days(row.metadata)
    if span is not None and span >= MIN_BACKTEST_SPAN_DAYS:
        return True, f"derived: data span {span} days (>= {MIN_BACKTEST_SPAN_DAYS}), n={n}"
    return False, (
        "no multi-season evidence: need metrics.backtest_scope='multi_season' with "
        f"n_seasons>={MIN_BACKTEST_SEASONS}, or metadata date span >= {MIN_BACKTEST_SPAN_DAYS} days"
    )


def promote_checked(
    registry: LocalRegistry, con: duckdb.DuckDBPyConnection, model_name: str, version: str
) -> str:
    """Promote after verifying the evidence rule. Returns a human-readable summary."""
    row = get_version(con, model_name, version=version)
    if row.stage == "production":
        return f"{model_name} {version} is already production (no change)"
    ok, reason = check_promotable(row)
    if not ok:
        raise PermissionError(f"refusing to promote {model_name} {version}: {reason}")
    prev = [
        r.version
        for r in fetch_versions(con, model_name)
        if r.stage == "production" and r.version != version
    ]
    registry.promote(model_name, version)
    msg = f"promoted {model_name} {version} -> production ({reason})"
    if prev:
        msg += f"; deprecated previous production: {', '.join(prev)}"
    return msg


# -- import-artifacts ----------------------------------------------------


def parse_tags(pairs: list[str]) -> dict[str, object]:
    """Parse ``key=value`` pairs; ints and JSON literals are decoded."""
    tags: dict[str, object] = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"tag {p!r} must be key=value")
        k, v = p.split("=", 1)
        try:
            tags[k] = json.loads(v)
        except ValueError:
            tags[k] = v
    return tags


def _dir_digest(src: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(src)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def import_artifacts(
    registry: LocalRegistry,
    con: duckdb.DuckDBPyConnection,
    model_name: str,
    src: Path,
    tags: dict[str, object],
    seed: int = 0,
) -> tuple[str, bool]:
    """Register an externally-trained artifact dir as a ``candidate``.

    The directory must hold ``metrics.json``; ``config.json``/``config.yaml``,
    ``metadata.json`` and weights are optional. Size and forbidden-suffix
    checks are done by ``LocalRegistry.log_model``. Re-importing identical
    content is a no-op. Returns ``(version, created)``.
    """
    if not src.is_dir():
        raise NotADirectoryError(f"{src} is not a directory")
    metrics_file = src / "metrics.json"
    if not metrics_file.exists():
        raise FileNotFoundError(f"{src} has no metrics.json")
    metrics = _loads(metrics_file.read_text())
    if not metrics:
        raise ValueError("metrics.json must be a non-empty JSON object")
    digest = _dir_digest(src)
    for r in fetch_versions(con, model_name):
        if r.metadata.get("artifact_sha256") == digest:
            return r.version, False
    meta_file = src / "metadata.json"
    extra = _loads(meta_file.read_text()) if meta_file.exists() else {}
    metadata: dict[str, object] = {
        **build_run_metadata(game_date_min=None, game_date_max=None, possession_count=0, seed=seed),
        **extra,
        "artifact_sha256": digest,
        "imported_from": src.name,
        "import": True,
    }
    full_tags: dict[str, object] = {"model_method": "external", "cold_start_flags": [], **tags}
    version = registry.next_version(model_name)
    registry.log_model(model_name, version, str(src), metrics, metadata, full_tags)
    return version, True


# -- local gate ----------------------------------------------------------


def gate_local(
    con: duckdb.DuckDBPyConnection,
    tolerances: dict[str, float],
    model_name: str | None = None,
    candidate_version: str | None = None,
) -> tuple[list[GateFailure], list[str]]:
    """Compare candidate version(s) against the REAL production version.

    For each model that has a production version, the candidate is
    ``candidate_version`` or else the newest non-production, non-deprecated
    version. Models without both sides are listed in the returned notes.
    """
    failures: list[GateFailure] = []
    notes: list[str] = []
    rows = fetch_versions(con, model_name)
    for name in sorted({r.model_name for r in rows}):
        mine = [r for r in rows if r.model_name == name]
        prod = [r for r in mine if r.stage == "production"]
        if not prod:
            notes.append(f"{name}: no production version (skipped)")
            continue
        if candidate_version is not None:
            cands = [r for r in mine if r.version == candidate_version]
        else:
            cands = [r for r in mine if r.stage == "candidate"]
        if not cands:
            notes.append(f"{name}: no candidate to gate against {prod[0].version} (skipped)")
            continue
        cand = cands[-1]
        base_m = {k: float(v) for k, v in key_metrics(prod[0].metrics).items() if k != "n"}
        cand_m = {k: float(v) for k, v in key_metrics(cand.metrics).items() if k != "n"}
        if "crps_mean" in base_m:
            base_m["crps"] = base_m.pop("crps_mean")
        if "crps_mean" in cand_m:
            cand_m["crps"] = cand_m.pop("crps_mean")
        notes.append(f"{name}: candidate {cand.version} vs production {prod[0].version}")
        failures.extend(evaluate_gate({name: cand_m}, {name: base_m}, tolerances))
    return failures, notes
