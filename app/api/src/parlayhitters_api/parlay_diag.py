"""Parlay diagnostics: the joint chances the parlay engine stored, as stored. The independent-legs
and copula values are filled only from a record whose engine is that one; nothing is multiplied
here, and no price, stake or expected value is served. Contract:
docs/contracts/parlay_diagnostics_v1.md.
"""

from __future__ import annotations

from typing import Any

from .honesty_data import FIXTURE_REASON
from .honesty_models import ParlayDiag, ParlayDiagnostics, ParlayLeg

Json = dict[str, Any]


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v)


def _leg(x: Any) -> ParlayLeg:
    d = x if isinstance(x, dict) else {}
    pid = d.get("player_id")
    return ParlayLeg(
        game_id=None if d.get("game_id") is None else str(d["game_id"]),
        player_id=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        stat=None if d.get("stat") is None else str(d["stat"]),
        side=None if d.get("side") is None else str(d["side"]),
        threshold=_num(d.get("threshold")),
    )


INDEPENDENT_ENGINES = ("independence",)
COPULA_ENGINES = ("gaussian_copula", "t_copula")


def _diag(rec: Json) -> ParlayDiag:
    legs = rec.get("legs") if isinstance(rec.get("legs"), list) else []
    engine = rec.get("engine") if isinstance(rec.get("engine"), str) else None
    joint = _num(rec.get("raw_model_prob"))
    indep = joint if engine in INDEPENDENT_ENGINES else None
    cop = joint if engine in COPULA_ENGINES else None
    missing = []
    if indep is None:
        missing.append("independent-legs joint probability (not stored for this record)")
    if cop is None:
        missing.append("copula joint probability (not stored for this record)")
    if joint is None:
        missing.append("raw_model_prob")
    return ParlayDiag(
        ticker=rec.get("ticker") if isinstance(rec.get("ticker"), str) else None,
        title=rec.get("title") if isinstance(rec.get("title"), str) else None,
        engine=engine, n_legs=len(legs or []), legs=[_leg(x) for x in legs or []],
        joint_stored=joint, joint_stored_field="raw_model_prob", joint_independent=indep,
        joint_copula=cop, shrunk_prob=_num(rec.get("model_prob")),
        model_weight=_num(rec.get("model_weight")),
        verdict=rec.get("verdict") if isinstance(rec.get("verdict"), str) else None,
        missing=missing,
    )  # fmt: skip


PARLAY_NOTE = (
    "Each record's raw_model_prob is the joint chance from the engine it names; the independent "
    "and copula values are filled only when that record's engine is that one. Nothing is "
    "multiplied here. shrunk_prob is the engine's chance after it shrinks toward the market by "
    "model_weight. "
    "No price, stake or expected value is served on this route. A one-leg record's stored "
    "chance is that leg's own; there is nothing joint about it. Records come in the file's "
    "order, a page at a time (offset, limit)."
)


def parlay_diagnostics(
    date: str, raw: Json | None, records: list[Json] | None, offset: int = 0, limit: int = 100
) -> ParlayDiagnostics:
    if raw is None or records is None:
        return ParlayDiagnostics(
            date=date, available=False, reason=FIXTURE_REASON, source=None, status=None,
            verdict=None, n_records=0, offset=offset, limit=limit, n_returned=0, engines={},
            n_with_independent=0, n_with_copula=0, records=[], note=PARLAY_NOTE,
        )  # fmt: skip
    diags = [_diag(r) for r in records if isinstance(r, dict)]
    engines: dict[str, int] = {}
    for d in diags:
        engines[d.engine or "unstated"] = engines.get(d.engine or "unstated", 0) + 1
    return ParlayDiagnostics(
        date=date, available=True, reason=None,
        source=None if raw.get("source") is None else str(raw["source"]),
        status=None if raw.get("status") is None else str(raw["status"]),
        verdict=None if raw.get("verdict") is None else str(raw["verdict"]),
        n_records=len(diags), offset=offset, limit=limit,
        n_returned=len(diags[offset : offset + limit]), engines=dict(sorted(engines.items())),
        n_with_independent=sum(1 for d in diags if d.joint_independent is not None),
        n_with_copula=sum(1 for d in diags if d.joint_copula is not None),
        records=diags[offset : offset + limit], note=PARLAY_NOTE,
    )  # fmt: skip
