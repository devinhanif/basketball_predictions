# ruff: noqa: E501
"""Weights bundles of the AVG shadow: read-only artifacts with sha256 and ``fit_through``.

Layout (``data/avg_shadow/bundles/<month_block>/``): ``seed0.pt seed1.pt seed2.pt`` (E2 transformer state dicts, hidden 64),
``vocab.json`` (player_id -> embedding row of the fit), ``gate.json`` (T442's SoftmaxGate per stat: encoder and coefficients) and
``manifest.json``. ``model_id`` = sha256 over the sorted ``name:sha256`` lines of the data files. ``install`` stamps ``installed_at``
(the steward's act, before the first game of the month); a bundle whose month does not match the game's month serves STALE.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from nba.daily.avg_shadow import RULE_SHA256

DEFAULT_ROOT = Path("data/avg_shadow/bundles")
DATA_FILES = ("seed0.pt", "seed1.pt", "seed2.pt", "vocab.json", "gate.json")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_id_of(files: dict[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{k}:{files[k]}" for k in sorted(files)).encode()).hexdigest()


@dataclass(frozen=True)
class Bundle:
    path: Path
    manifest: dict[str, object]

    @property
    def model_id(self) -> str:
        return str(self.manifest["model_id"])

    @property
    def month_block(self) -> str:
        return str(self.manifest["month_block"])

    @property
    def fit_through(self) -> date:
        return date.fromisoformat(str(self.manifest["fit_through"]))

    @property
    def installed_at(self) -> datetime | None:
        v = self.manifest.get("installed_at")
        return datetime.fromisoformat(str(v)) if v else None

    @property
    def gate_id(self) -> str:
        return str(self.manifest["gate_id"])

    @property
    def gate_fit_through(self) -> date:
        return date.fromisoformat(str(self.manifest["gate_fit_through"]))

    def gate(self) -> dict[str, dict[str, list[float]]]:
        g: dict[str, dict[str, list[float]]] = json.loads((self.path / "gate.json").read_text())
        return g

    @property
    def n_ctx(self) -> int:
        return int(str(self.manifest["n_ctx"]))


def verify(path: Path) -> Bundle:
    """Load the manifest and check every data file against its recorded sha256 and the model_id. Raises on any mismatch."""
    manifest = json.loads((path / "manifest.json").read_text())
    files: dict[str, str] = manifest["files"]
    for name in DATA_FILES:
        if name not in files:
            raise ValueError(f"{path}: {name} not in the manifest")
        got = file_sha256(path / name)
        if got != files[name]:
            raise ValueError(f"{path}: {name} sha256 {got} != manifest {files[name]}")
    if model_id_of(files) != manifest["model_id"]:
        raise ValueError(f"{path}: model_id does not match the file shas")
    if manifest.get("rule_sha256") != RULE_SHA256:
        raise ValueError(f"{path}: built for rule {manifest.get('rule_sha256')}, not {RULE_SHA256}")
    return Bundle(path, manifest)


def list_bundles(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob("*/manifest.json")) if root.exists() else []


def select_bundle(root: Path, run_date: date, made_at: datetime) -> tuple[Bundle | None, bool]:
    """(bundle, stale). ``None``: no bundle installed by ``made_at`` (the step is ``skipped``). A VALID bundle has ``month_block`` equal to
    the game month, ``installed_at <= made_at`` and ``fit_through`` strictly before the first day of that month; if none is valid, the
    newest installed bundle serves and the rows are written ``stale_model = 1``."""
    block = run_date.strftime("%Y-%m")
    first = run_date.replace(day=1)
    installed: list[Bundle] = []
    for p in list_bundles(root):
        b = verify(p)
        if b.installed_at is not None and b.installed_at <= made_at:
            installed.append(b)
    for b in installed:
        if b.month_block == block and b.fit_through < first:
            return b, False
    if not installed:
        return None, False
    return max(installed, key=lambda b: (b.month_block, b.installed_at or datetime.min)), True


def install(src: Path, root: Path, installed_at: datetime) -> Bundle:
    """Copy a downloaded bundle into ``root/<month_block>/`` after verifying it; stamp ``installed_at`` (naive UTC)."""
    b = verify(src)
    dst = root / b.month_block
    if (dst / "manifest.json").exists():
        raise FileExistsError(
            f"{dst} already holds a bundle; weights are never overwritten (new month_block or a new rule)"
        )
    dst.mkdir(parents=True)
    for name in DATA_FILES:
        shutil.copy2(src / name, dst / name)
    m = dict(b.manifest)
    m["installed_at"] = installed_at.isoformat()
    (dst / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True))
    return verify(dst)


def write_fixture_bundle(
    out: Path, *, n_step: int = 27, n_ctx: int = 34, n_players: int = 40, month_block: str = "2000-01",
    fit_through: str = "1999-12-31", installed_at: datetime | None = None, seed: int = 0,
) -> Bundle:  # fmt: skip
    """RANDOM-INIT weights (3 seeds) for tests and the P4 replay, so inference executes. Never a forecast; the manifest says so."""
    import numpy as np
    import torch

    from nba.daily.avg_shadow.infer import build_net

    out.mkdir(parents=True, exist_ok=True)
    scale = np.array([7.0, 3.0, 2.2, 1.2], dtype=np.float32)
    for s in range(3):
        torch.manual_seed(seed * 10 + s)
        torch.save(build_net(n_step, n_ctx, n_players, scale).state_dict(), out / f"seed{s}.pt")
    (out / "vocab.json").write_text(json.dumps({str(i): i for i in range(1, n_players + 1)}))
    gate = {
        st: {
            "columns": ["log_n_prior", "starter10", "n_rep_out", "has_report", "team_game_no"],
            "med": [0.0] * 5, "mu": [0.0] * 5, "sd": [1.0] * 5,
            "coef": [[0.0, 0.0]] * 6,
        }
        for st in ("pts", "reb", "ast", "fg3m")
    }  # fmt: skip
    (out / "gate.json").write_text(json.dumps(gate))
    files = {n: file_sha256(out / n) for n in DATA_FILES}
    manifest = {
        "rule_sha256": RULE_SHA256, "kind": "FIXTURE_RANDOM_INIT_NOT_A_FORECAST", "model_id": model_id_of(files), "files": files,
        "seeds": [0, 1, 2], "hidden": 64, "n_step": n_step, "n_ctx": n_ctx, "month_block": month_block,
        "fit_through": fit_through, "gate_id": files["gate.json"], "gate_fit_through": fit_through,
    }  # fmt: skip
    if installed_at is not None:
        manifest["installed_at"] = installed_at.isoformat()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return verify(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="verify / install an AVG shadow weights bundle (the maintainer's act)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("path", type=Path)
    i = sub.add_parser("install")
    i.add_argument("src", type=Path)
    i.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    f = sub.add_parser(
        "fixture", help="RANDOM-INIT test bundle (tests and proof P4); not a forecast"
    )
    f.add_argument("out", type=Path)
    f.add_argument("--month-block", default="2000-01")
    f.add_argument("--fit-through", default="1999-12-31")
    f.add_argument("--installed-at", default=None)
    a = ap.parse_args(argv)
    now = datetime.now(UTC).replace(tzinfo=None)  # naive UTC like every shadow timestamp
    if a.cmd == "fixture":
        b = write_fixture_bundle(
            a.out, month_block=a.month_block, fit_through=a.fit_through,
            installed_at=datetime.fromisoformat(a.installed_at) if a.installed_at else None,
        )  # fmt: skip
    else:
        b = verify(a.path) if a.cmd == "verify" else install(a.src, a.root, now)
    print(
        json.dumps(
            {
                "model_id": b.model_id,
                "month_block": b.month_block,
                "fit_through": str(b.fit_through),
                "installed_at": str(b.installed_at),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
