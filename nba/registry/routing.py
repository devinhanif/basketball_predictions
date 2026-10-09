"""Routing table: which registered model (or learned router) predicts each target.

A *route spec* is a small JSON document, stored as a versioned registry artifact
(``registry_store/route_<target>/<version>/route.json``) and as one ``experiments``
row (``model_name = route_<target>``, ``tags.kind = "route"``). It says, for one
target, either

* ``kind = "champion"``: a single candidate wins; or
* ``kind = "router"``: a frozen softmax-linear gate (:mod:`nba.stack.frozen`) maps
  as-of context features to weights over the candidates.

It also records, per candidate, the registry coordinates, the walk-forward
evidence the route was built from and the holdout status:

``clean-confirmed``  confirmatory first touch on the frozen season is logged in
                     docs/HOLDOUT_ACCESS_LOG.md and met its pre-registered rule;
``repeated-use``     the frozen season was evaluated before (also as a comparator);
``untouched``        never evaluated on the frozen season.

``validate_spec`` is the structural / leakage / ledger-consistency check; it never
needs a database. Nothing here writes to ``nba.duckdb`` except
:func:`register_spec` / :func:`promote_route`, which the CLI calls with a
maintainer-opened write connection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from nba.registry.local import LocalRegistry
from nba.registry.protocol import RegistryAdapter

FROZEN_SEASON = 2025
SCHEMA_VERSION = 1
ROUTE_FILE = "route.json"
TARGET_KINDS = ("win", "pts", "reb", "ast", "fg3m", "thr_pts", "thr_reb", "thr_ast", "thr_fg3m")
JOINT_TARGET = "joint_parlay"
ALL_TARGETS = (*TARGET_KINDS, JOINT_TARGET)
HOLDOUT_STATUSES = ("clean-confirmed", "repeated-use", "untouched")
DEFAULT_LEDGER = Path(__file__).resolve().parents[2] / "docs" / "HOLDOUT_ACCESS_LOG.md"
DEFAULT_DRAFT_DIR = Path(__file__).resolve().parents[2] / "registry_store" / "routes_draft"
PREREG_DOC = "docs/ROUTING.md"


def route_model_name(target: str) -> str:
    return f"route_{target}"


@dataclass(frozen=True)
class CandidateDef:
    """Static facts about one candidate predictor."""

    id: str
    label: str
    registry_model: str | None  # registry name if registered
    registry_version: str | None
    oof_model: str  # OOF-store model name; ``{stat}`` is substituted
    oof_version: str
    seasons: tuple[int, ...]  # seasons with walk-forward OOF rows
    holdout_status: str
    holdout_basis: str
    ledger_key: str  # substring that must (status != untouched) / must not (untouched) appear
    note: str = ""

    def oof_name(self, stat: str) -> str:
        return self.oof_model.replace("{stat}", stat)


CANDIDATES: dict[str, CandidateDef] = {
    c.id: c
    for c in [
        CandidateDef(
            "context_residual",
            "LightGBM residual over recency (production props)",
            "props_context_residual",
            "v1",
            "props_context_residual",
            "v1",
            (2023, 2024),
            "clean-confirmed",
            "ledger 2026-10-08: CONFIRMED all 4 stats vs recency_conformal",
            "context_residual",
        ),
        CandidateDef(
            "recency",
            "recency-weighted played-games average (daily comparison model)",
            None,
            None,
            "season_avg_{stat}",
            "v1-hl10",
            (2023, 2024),
            "repeated-use",
            "comparator inside the context_residual 2025 touch",
            "recency",
            "unregistered; forward name props_recency_v1",
        ),
        CandidateDef(
            "seq_props",
            "sequence transformer over raw recent games",
            "seq_props",
            "v1",
            "seq_props",
            "v1",
            (2024,),
            "untouched",
            "2025 refused by loader/builder/trainer/evaluator",
            "seq_props",
            "2024-only OOF; pre-registered result: worse than production on pts/reb/ast",
        ),
        CandidateDef(
            "ctxres_v2",
            "ctxres_v2 exp-2 candidate xgb_v12_poisson_nb",
            None,
            None,
            "ctxres_v2_xgb_v12_poisson_nb",
            "exp2-20261008_171446",
            (2024,),
            "untouched",
            "exp-2 verdict is a 2024 result; 2025 never loaded",
            "ctxres_v2",
            "unregistered artifact; exp-2 verdict: reb/ast/fg3m kept, pts failed",
        ),
        CandidateDef(
            "injury_elo",
            "MOV-Elo + pre-tip injury adjustment (production win)",
            "rung0_injury_elo",
            "v1",
            "rung0_injury_elo",
            "v1",
            (2023, 2024),
            "clean-confirmed",
            "ledger 2026-10-08: CONFIRMED, log loss 0.6010 -> 0.5908 on 1316 games",
            "rung0_injury_elo",
        ),
        CandidateDef(
            "mov_elo",
            "tuned MOV-Elo (win baseline)",
            "rung0_mov_elo",
            "v1",
            "rung0_mov_elo",
            "v1",
            (2023, 2024),
            "repeated-use",
            "comparator inside the injury_elo 2025 touch",
            "rung0_mov_elo",
        ),
    ]
}

#: Candidate sets per route family. ``full`` = candidates with 2023 AND 2024 OOF (router fit on
#: 2023->2024). ``ext`` = adds 2024-only candidates (router fit inside 2024 only).
CANDIDATE_SETS: dict[str, dict[str, list[str]]] = {
    "props": {
        "full": ["context_residual", "recency"],
        "ext": ["context_residual", "recency", "seq_props", "ctxres_v2"],
    },
    "win": {"full": ["injury_elo", "mov_elo"]},
}

#: Candidates that a daily forward run can currently produce (everything else cannot be
#: applied forward until a pipeline step exists for it).
FORWARD_AVAILABLE = ("context_residual", "recency")

JOINT_CANDIDATES: list[dict[str, Any]] = [
    {
        "id": "gaussian_copula",
        "holdout_status": "untouched",
        "registered": False,
        "evidence": "reports/parlay/joint_calibration.json: 2023-24 log loss -0.00053 "
        "[-0.00106,-0.00003] vs independence (15798 parlays, 2633 games)",
    },
    {
        "id": "independence",
        "holdout_status": "untouched",
        "registered": False,
        "evidence": "floor baseline",
    },
    {
        "id": "t_copula",
        "holdout_status": "untouched",
        "registered": False,
        "evidence": "same as gaussian within noise (t vs gaussian -0.00005 [-0.00021,+0.00011])",
    },
    {
        "id": "joint_game_set",
        "holdout_status": "untouched",
        "registered": False,
        "evidence": "pre-registered (docs/JOINT_GAME_SET.md); no Colab result yet",
    },
]


def load_extra_candidates(path: Path) -> list[str]:
    """Extend the candidate table from a JSON file (for example a new experiment's OOF).

    Format: ``{"candidates": [CandidateDef fields...], "sets": {"props": {"<set>": [ids]}},
    "fit_seasons": {"<set>": [2024]}}``. Returns the new set names. A candidate may not claim
    an OOF season >= the frozen season, and may not be marked ``clean-confirmed`` without a
    ledger mention (checked later by ``validate_spec``).
    """
    raw = json.loads(path.read_text())
    for c in raw.get("candidates", []):
        c = dict(c)
        c["seasons"] = tuple(c["seasons"])
        if max(c["seasons"]) >= FROZEN_SEASON:
            raise ValueError(f"candidate {c['id']}: OOF seasons must be < {FROZEN_SEASON}")
        if c["holdout_status"] not in HOLDOUT_STATUSES:
            raise ValueError(f"candidate {c['id']}: bad holdout_status")
        CANDIDATES[c["id"]] = CandidateDef(**c)
    new: list[str] = []
    for fam, sets in raw.get("sets", {}).items():
        for name, ids in sets.items():
            unknown = [i for i in ids if i not in CANDIDATES]
            if unknown:
                raise ValueError(f"set {name}: unknown candidates {unknown}")
            CANDIDATE_SETS.setdefault(fam, {})[name] = list(ids)
            new.append(name)
    for name, seasons in raw.get("fit_seasons", {}).items():
        EXTRA_FIT_SEASONS[name] = tuple(int(x) for x in seasons)
    return new


#: fit seasons for sets added through :func:`load_extra_candidates`
EXTRA_FIT_SEASONS: dict[str, tuple[int, ...]] = {}


def target_family(target: str) -> str:
    return "win" if target == "win" else "props"


def candidate_ids_for(target: str, cset: str) -> list[str]:
    fam = CANDIDATE_SETS[target_family(target)]
    if cset not in fam:
        raise ValueError(f"no candidate set {cset!r} for target {target!r}")
    return list(fam[cset])


@dataclass
class RouteSpec:
    """In-memory route spec; ``to_dict`` / ``from_dict`` are the JSON form."""

    target: str
    cset: str
    kind: str  # 'champion' | 'router'
    champion: str
    candidates: list[dict[str, Any]]
    holdout_season: int
    fit_window: dict[str, Any]
    gate: dict[str, Any] | None = None
    context_features: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    cells: list[dict[str, Any]] = field(default_factory=list)
    walk_forward: dict[str, Any] = field(default_factory=dict)
    parent_route_hash: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "route_model": route_model_name(self.target),
            "target": self.target,
            "set": self.cset,
            "kind": self.kind,
            "champion": self.champion,
            "candidates": self.candidates,
            "holdout_season": self.holdout_season,
            "fit_window": self.fit_window,
            "gate": self.gate,
            "context_features": self.context_features,
            "evidence": self.evidence,
            "cells": self.cells,
            "walk_forward": self.walk_forward,
            "parent_route_hash": self.parent_route_hash,
            "preregistration": PREREG_DOC,
            "created_at": self.created_at or datetime.now(UTC).isoformat(timespec="seconds"),
        }
        d["route_hash"] = route_hash(d)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RouteSpec:
        return cls(
            target=d["target"],
            cset=d["set"],
            kind=d["kind"],
            champion=d["champion"],
            candidates=list(d["candidates"]),
            holdout_season=int(d["holdout_season"]),
            fit_window=dict(d["fit_window"]),
            gate=d.get("gate"),
            context_features=list(d.get("context_features", [])),
            evidence=dict(d.get("evidence", {})),
            cells=list(d.get("cells", [])),
            walk_forward=dict(d.get("walk_forward", {})),
            parent_route_hash=d.get("parent_route_hash"),
            created_at=str(d.get("created_at", "")),
        )


def route_hash(d: dict[str, Any]) -> str:
    """Identity of what the route DOES: kind, champion, candidate ids, frozen gate content,
    holdout season, fit window. Evidence / cells / timestamps do not change the hash."""
    gate = d.get("gate") or {}
    core = {
        "target": d["target"],
        "set": d["set"],
        "kind": d["kind"],
        "champion": d["champion"],
        "candidates": [c["id"] for c in d["candidates"]],
        "holdout_season": d["holdout_season"],
        "fit_seasons": d["fit_window"].get("seasons"),
        "gate": {k: gate.get(k) for k in ("target", "candidates", "pool", "l2", "encoder", "coef")},
    }
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def write_spec(spec: RouteSpec, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ROUTE_FILE
    path.write_text(json.dumps(spec.to_dict(), indent=1, sort_keys=True))
    return path


def read_spec(path: Path) -> dict[str, Any]:
    p = path / ROUTE_FILE if path.is_dir() else path
    d: dict[str, Any] = json.loads(p.read_text())
    return d


# --------------------------------------------------------------------------- validation


def _ledger_text(ledger: Path) -> str:
    return ledger.read_text() if ledger.exists() else ""


def validate_spec(
    d: dict[str, Any],
    *,
    ledger: Path = DEFAULT_LEDGER,
    oof_path: str | None = None,
    db_path: str | None = None,
) -> list[str]:
    """Return a list of problems (empty = valid)."""
    errs: list[str] = []
    for k in (
        "schema",
        "target",
        "set",
        "kind",
        "champion",
        "candidates",
        "holdout_season",
        "fit_window",
        "route_hash",
    ):
        if k not in d:
            errs.append(f"missing key {k!r}")
    if errs:
        return errs
    if d["schema"] != SCHEMA_VERSION:
        errs.append(f"unsupported schema {d['schema']}")
    if d["target"] not in ALL_TARGETS:
        errs.append(f"unknown target {d['target']!r}")
    if d["kind"] not in ("champion", "router"):
        errs.append(f"kind must be champion|router, got {d['kind']!r}")
    ids = [c.get("id") for c in d["candidates"]]
    if len(set(ids)) != len(ids) or not ids:
        errs.append("candidate ids must be unique and non-empty")
    if d["champion"] not in ids:
        errs.append(f"champion {d['champion']!r} not among candidates")
    if d["route_hash"] != route_hash(d):
        errs.append("route_hash does not match content (spec edited after freezing?)")
    hs = int(d["holdout_season"])
    seasons = [int(s) for s in d["fit_window"].get("seasons", [])]
    if not seasons:
        errs.append("fit_window.seasons empty")
    elif max(seasons) >= hs:
        errs.append(f"fit window includes season {max(seasons)} >= holdout season {hs}")
    if hs < FROZEN_SEASON:
        errs.append(f"holdout_season {hs} < {FROZEN_SEASON}")
    if hs > FROZEN_SEASON and db_path is not None:
        n = _games_in_season(db_path, hs)
        if n < 20:
            errs.append(f"rollover to holdout {hs} needs >= 20 games ingested, found {n}")
    for c in d["candidates"]:
        if c.get("holdout_status") not in HOLDOUT_STATUSES:
            errs.append(f"candidate {c.get('id')}: bad holdout_status {c.get('holdout_status')!r}")
    errs += _ledger_consistency(d, _ledger_text(ledger))
    if d["kind"] == "router":
        g = d.get("gate")
        if not g:
            errs.append("router without a frozen gate")
        else:
            if g.get("candidates") != ids:
                errs.append("gate candidates differ from spec candidates")
            if len(ids) < 2:
                errs.append("router needs >= 2 candidates")
            feats = list(g["encoder"]["num"]) + [c for c, _ in g["encoder"]["cat"]]
            if feats != list(d.get("context_features", [])):
                errs.append("context_features differ from the gate's encoder features")
    elif d.get("gate"):
        errs.append("champion route must not carry a gate")
    if oof_path is not None and d["target"] != JOINT_TARGET:
        errs += _oof_consistency(d, oof_path)
    return errs


def _ledger_consistency(d: dict[str, Any], ledger: str) -> list[str]:
    errs: list[str] = []
    if not ledger:
        return errs
    for c in d["candidates"]:
        cdef = CANDIDATES.get(str(c.get("id")))
        if cdef is None:
            continue
        present = cdef.ledger_key in ledger
        if c.get("holdout_status") != "untouched" and not present:
            errs.append(f"{c['id']}: status {c['holdout_status']} but not in the holdout ledger")
        if c.get("holdout_status") == "untouched" and present:
            errs.append(f"{c['id']}: marked untouched but the holdout ledger mentions it")
    return errs


def _oof_consistency(d: dict[str, Any], oof_path: str) -> list[str]:
    errs: list[str] = []
    p = Path(oof_path)
    if not p.exists():
        return [f"OOF store {oof_path} not found"]
    con = duckdb.connect(str(p), read_only=True)
    try:
        for c in d["candidates"]:
            cdef = CANDIDATES.get(str(c["id"]))
            if cdef is None:
                continue
            row = con.execute(
                "SELECT count(*), max(season) FROM oof_predictions "
                "WHERE model = ? AND model_version = ? AND target = ?",
                [
                    c.get("oof_model", cdef.oof_name(d["target"].removeprefix("thr_"))),
                    c["oof_version"],
                    _oof_target(d["target"]),
                ],
            ).fetchone()
            if not row or row[0] == 0:
                errs.append(f"{c['id']}: no OOF rows in the store")
            elif row[1] is not None and int(row[1]) >= FROZEN_SEASON:
                errs.append(f"{c['id']}: OOF store holds season {row[1]} >= {FROZEN_SEASON}")
    finally:
        con.close()
    return errs


def _oof_target(target: str) -> str:
    return target.removeprefix("thr_")


def _games_in_season(db_path: str, season: int) -> int:
    con = duckdb.connect(db_path, read_only=True)
    try:
        row = con.execute(
            "SELECT count(*) FROM games WHERE season = ? AND home_pts > 0", [season]
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


# --------------------------------------------------------------------------- registry I/O


@dataclass
class RegisteredRoute:
    model_name: str
    version: str
    stage: str
    artifact_path: str
    tags: dict[str, Any]
    metrics: dict[str, Any]

    def spec(self) -> dict[str, Any]:
        return read_spec(Path(self.artifact_path))


def fetch_routes(
    con: duckdb.DuckDBPyConnection, target: str | None = None
) -> list[RegisteredRoute]:
    """Registered route rows (read-only query on ``experiments``)."""
    rows = con.execute(
        "SELECT model_name, version, stage, artifact_path, tags, metrics FROM experiments "
        "WHERE model_name LIKE 'route\\_%' ESCAPE '\\' ORDER BY model_name, created_at"
    ).fetchall()
    out: list[RegisteredRoute] = []
    for name, version, stage, path, tags, metrics in rows:
        t = json.loads(tags) if isinstance(tags, str) else (tags or {})
        if t.get("kind") != "route":
            continue
        if target is not None and name != route_model_name(target):
            continue
        m = json.loads(metrics) if isinstance(metrics, str) else (metrics or {})
        out.append(RegisteredRoute(name, str(version), str(stage), str(path), t, m))
    return out


def route_metrics(spec: RouteSpec) -> dict[str, Any]:
    ev = spec.evidence
    return {
        "n": ev.get("n_scored"),
        "n_games": ev.get("n_games"),
        "backtest_scope": "multi_season"
        if len(spec.fit_window.get("seasons", [])) >= 2
        else "single",
        "n_seasons": len(spec.fit_window.get("seasons", [])),
        "score_router_walk_forward": ev.get("router_mean_score"),
        "score_best_single": ev.get("best_single_mean_score"),
        "best_single": ev.get("best_single"),
        "router_kept_2023_24": ev.get("router_kept"),
    }


def register_spec(
    registry: RegistryAdapter,
    spec: RouteSpec,
    *,
    seed: int = 0,
    work_dir: Path,
) -> str:
    """Log the spec as a new ``candidate`` version of ``route_<target>``. Returns the version."""
    d = spec.to_dict()
    errs = validate_spec(d)
    if errs:
        raise ValueError("refusing to register an invalid route spec: " + "; ".join(errs))
    name = route_model_name(spec.target)
    version = registry.next_version(name)
    folder = work_dir / f"{name}_{version}"
    write_spec(spec, folder)
    tags: dict[str, Any] = {
        "kind": "route",
        "rung": 6,
        "target": spec.target,
        "set": spec.cset,
        "route_kind": spec.kind,
        "route_hash": d["route_hash"],
        "holdout_season": spec.holdout_season,
        "model_method": "softmax_gate" if spec.kind == "router" else "champion",
        "cold_start_flags": "log_career,games_played_season",
        "source_config": PREREG_DOC,
    }
    meta: dict[str, Any] = {
        "game_date_min": spec.fit_window.get("date_min"),
        "game_date_max": spec.fit_window.get("date_max"),
        "row_count": spec.fit_window.get("n_rows"),
        "seed": seed,
        "created_at": d["created_at"],
    }
    registry.log_model(name, version, str(folder), route_metrics(spec), meta, tags)
    return version


def promote_route(
    registry: LocalRegistry,
    con: duckdb.DuckDBPyConnection,
    target: str,
    version: str,
    *,
    verdict_path: Path | None = None,
    ledger: Path = DEFAULT_LEDGER,
) -> str:
    """Promote a registered route after validation.

    A ``champion`` route needs only to validate. A ``router`` route additionally needs
    a verdict JSON (from :mod:`nba.stack.holdout`) with ``kept: true`` whose
    ``route_hash`` equals the spec's hash or its ``parent_route_hash``.
    """
    routes = [r for r in fetch_routes(con, target) if r.version == version]
    if not routes:
        raise LookupError(f"no registered route {route_model_name(target)} {version}")
    r = routes[0]
    d = r.spec()
    errs = validate_spec(d, ledger=ledger)
    if errs:
        raise ValueError("route fails validation: " + "; ".join(errs))
    if d["kind"] == "router":
        if verdict_path is None:
            raise PermissionError(
                "router routes need --verdict (the pre-registered holdout verdict)"
            )
        v = json.loads(verdict_path.read_text())
        ok_hash = v.get("route_hash") in (d["route_hash"], d.get("parent_route_hash"))
        if not (ok_hash and v.get("kept") is True and v.get("target") == target):
            raise PermissionError("verdict does not show kept=true for this route / its parent")
    registry.promote(route_model_name(target), version)
    return f"promoted {route_model_name(target)} {version} (kind={d['kind']}, set={d['set']})"


# --------------------------------------------------------------------------- show


def format_table(specs: list[tuple[str, dict[str, Any]]]) -> str:
    """Human routing table: one block per (label, spec dict)."""
    lines: list[str] = []
    for label, d in specs:
        ev = d.get("evidence", {})
        lines.append(
            f"{d['target']:<10} set={d['set']:<4} kind={d['kind']:<8} champion={d['champion']:<16}"
            f" holdout={d['holdout_season']} [{label}] hash={d['route_hash']}"
        )
        lines.append(
            f"  fit {d['fit_window'].get('date_min')}..{d['fit_window'].get('date_max')} "
            f"seasons={d['fit_window'].get('seasons')} n={d['fit_window'].get('n_rows')}"
        )
        cand_ev = ev.get("candidates", {})
        for c in d["candidates"]:
            e = cand_ev.get(c["id"], {})
            lines.append(
                f"    {c['id']:<18} {c['holdout_status']:<15} "
                f"registry={c.get('registry_model') or '-'}:{c.get('registry_version') or '-'}"
                f"  score={e.get('mean_score', float('nan')):.4f} n={e.get('n', '-')}"
            )
        if d["kind"] == "router" or ev.get("router_mean_score") is not None:
            lines.append(
                f"  walk-forward router={ev.get('router_mean_score')} best_single="
                f"{ev.get('best_single_mean_score')} kept_2023_24={ev.get('router_kept')}"
            )
        claims = [c for c in d.get("cells", []) if c.get("claim")]
        if claims:
            for c in claims[:8]:
                lines.append(
                    f"  cell {c['dimension']}={c['group']} n={c['n']}: {c['claim']} beats "
                    f"champion by {c['delta_vs_champion']:+.4f} (q={c['q']:.3f})"
                )
        elif d.get("cells"):
            lines.append("  cells: no per-cell best-model claim survives the guardrails")
    return "\n".join(lines)
