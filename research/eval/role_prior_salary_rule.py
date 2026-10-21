# ruff: noqa: E501
"""ROLE_PRIOR_SALARY rule constants and pure functions (rule: docs/prereg/ROLE_PRIOR_SALARY.md, DRAFT).

Nothing here reads an outcome. Salary facts are contract facts (Basketball-Reference team-season pages, research use only, never
committed). Season convention of this repo: ``season`` = start year (2023 = 2023-24); Basketball-Reference ``br_year`` = end year
(2024 = 2023-24), so ``br_year = season + 1``. Season 2025 is the frozen holdout: it is never joined to game data here.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ID = "ROLE_PRIOR_SALARY"
SELECT = 2023
REPORT = 2024
MAX_SEASON = 2024
K_MAIN = 10
K_SENS = (5, 10)
STATS = ("pts", "reb", "ast", "fg3m")
FLOORS = {"pts": 0.005, "reb": 0.003, "ast": 0.002, "fg3m": 0.001}
N_BOOT = 2000
SEED = 0
ALPHA = 0.05
TOP5_SHARE_MAX = 0.50
W_MAX = 0.30  # fixed blend weight at game 0, faded by (1 - g / K); not tuned
MIN_ROWS = 400
MIN_SALARY_COVER = 0.70
MIN_RESOLVED = 0.95
DISAGREE_PLACES = 5

SALARY_DIR = Path("data/raw/bref_salaries_2026-10-21/teams")
BR_YEARS = (2023, 2024, 2025, 2026, 2027)
BR_TEAMS = [
    "ATL",
    "BOS",
    "BRK",
    "CHI",
    "CHO",
    "CLE",
    "DAL",
    "DEN",
    "DET",
    "GSW",
    "HOU",
    "IND",
    "LAC",
    "LAL",
    "MEM",
    "MIA",
    "MIL",
    "MIN",
    "NOP",
    "NYK",
    "OKC",
    "ORL",
    "PHI",
    "PHO",
    "POR",
    "SAC",
    "SAS",
    "TOR",
    "UTA",
    "WAS",
]
BR_TO_NBA_ABBR = {"BRK": "BKN", "CHO": "CHA", "PHO": "PHX"}

# Cap, tax line and aprons in dollars, by BR end-year. Each figure was read from a published source on 2026-10-21 (not from memory).
# A secondary-source figure is marked; 2025-26 and 2026-27 lie outside the select/report seasons and are used for coverage counts only.
CAP_TABLE: dict[int, dict[str, Any]] = {
    2023: {
        "cap": 123_655_000,
        "tax": 150_267_000,
        "apron1": 156_983_000,  # the single pre-2023-CBA apron (Spotrac); there is no second apron this season
        "apron2": None,
        "source": "NBA press release pr.nba.com/nba-salary-cap-2022-23-season (cap, tax); apron: spotrac.com/nba/cap/2022 (secondary)",
        "status": "published",
    },
    2024: {
        "cap": 136_021_000,
        "tax": 165_294_000,
        "apron1": 172_346_000,
        "apron2": 182_794_000,
        "source": "NBA press release pr.nba.com/nba-salary-cap-for-2023-24-season-set-at-136-021-million (all four, via web search confirmation)",
        "status": "published",
    },
    2025: {
        "cap": 140_588_000,
        "tax": 170_814_000,
        "apron1": 178_132_000,
        "apron2": 188_931_000,
        "source": "NBA press release pr.nba.com/2024-25-nba-season-salary-cap; hoopsrumors.com 2024/06 (all four, via web search confirmation)",
        "status": "published",
    },
    2026: {
        "cap": 154_647_000,
        "tax": 187_895_000,
        "apron1": 195_945_000,
        "apron2": 207_824_000,
        "source": "sportcal.com (secondary; the NBA release was not opened). Coverage counts only.",
        "status": "secondary_source",
    },
    2027: {
        "cap": 164_961_000,
        "tax": 200_428_000,
        "apron1": 209_015_000,
        "apron2": 221_686_000,
        "source": "bleacherreport.com 2026-27 cap/apron/tax articles (secondary; tax line from a separate article). Coverage counts only.",
        "status": "secondary_source",
    },
}

TAX_STATES = ("under_tax", "over_tax", "over_apron1", "over_apron2")
CLASSES = (
    "minimum",
    "rookie_scale",
    "low_exception",
    "mid_exception",
    "rotation_salary",
    "near_max",
    "max_range",
)


def br_year_of(season: int) -> int:
    return season + 1


def check_season(season: int) -> int:
    if season > MAX_SEASON:
        raise ValueError(
            f"season {season}: 2025 is the frozen holdout, never joined to game data by this rule"
        )
    return season


# --------------------------------------------------------------------------- parsing


def parse_salary(x: Any) -> float:
    """'$55,761,216' -> 55761216.0; blank, NaN or non-numeric -> NaN."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return float("nan")
    s = re.sub(r"[^0-9.]", "", str(x))
    return float(s) if s else float("nan")


def fold(s: str) -> str:
    """Lower-case ASCII letters only; accents folded; Jr/Sr/II/III/IV suffixes handled by the callers."""
    t = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", t.lower())


_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


def nba_stem(full_name: str) -> str:
    """Basketball-Reference id stem built from an nba_api name: first 5 letters of the surname + first 2 of the first name."""
    toks = [t for t in re.split(r"\s+", str(full_name).strip()) if t]
    toks = [t for t in toks if fold(t.rstrip(".")) not in _SUFFIX] or toks
    if len(toks) < 2:
        return fold(toks[0])[:7] if toks else ""
    return fold("".join(toks[1:]))[:5] + fold(toks[0])[:2]


def bref_stem(bref_id: str) -> str:
    return re.sub(r"\d+$", "", str(bref_id))


def read_team_year(root: Path, team: str, br_year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = root / SALARY_DIR
    s = pd.read_csv(d / f"{team}_{br_year}_salaries.csv", dtype=str)
    r = pd.read_csv(d / f"{team}_{br_year}_roster.csv", dtype=str)
    s = s[s["player_bref_id"].notna()].copy()
    s["salary"] = s["salary"].map(parse_salary)
    s["team"], s["br_year"] = team, br_year
    r = r[r["player_bref_id"].notna()].copy()
    r["team"], r["br_year"] = team, br_year
    return s[["team", "br_year", "player", "salary", "player_bref_id"]], r[
        ["team", "br_year", "player", "pos", "birth_date", "years_experience", "player_bref_id"]
    ]


def load_all(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    ss, rr = [], []
    for y in BR_YEARS:
        for t in BR_TEAMS:
            s, r = read_team_year(root, t, y)
            ss.append(s)
            rr.append(r)
    return pd.concat(ss, ignore_index=True), pd.concat(rr, ignore_index=True)


def salary_rows(sal: pd.DataFrame, roster: pd.DataFrame) -> pd.DataFrame:
    """One row per (team, br_year, player): the LARGEST dollar value among his contract lines on that page (10-day and repeated lines
    are separate contracts; the largest is the one that implies his stature), the line count, and ``alive`` = he is on the same page's
    roster table (otherwise the line is dead money: waived, stretched, bought out or traded after the season, per BR's table)."""
    v = sal[sal["salary"].notna()]
    g = (
        v.groupby(["team", "br_year", "player_bref_id"])
        .agg(salary=("salary", "max"), n_lines=("salary", "size"), player=("player", "first"))
        .reset_index()
    )
    key = roster[["team", "br_year", "player_bref_id"]].assign(alive=True).drop_duplicates()
    g = g.merge(key, on=["team", "br_year", "player_bref_id"], how="left")
    g["alive"] = g["alive"].fillna(False).astype(bool)
    return g


# --------------------------------------------------------------------------- cap, tax state, contract class


def tax_state(payroll: float, br_year: int) -> str:
    c = CAP_TABLE[br_year]
    if c["apron2"] is not None and payroll >= c["apron2"]:
        return "over_apron2"
    if payroll >= c["apron1"]:
        return "over_apron1"
    if payroll >= c["tax"]:
        return "over_tax"
    return "under_tax"


def contract_class(cap_share: float, exp: str | float | None) -> str:
    """Inferred, not observed (BR gives dollars and years of service, not contract type). Rookie scale = years of service R..3 with a
    salary above the minimum band; minimum = share < 0.025; the exception bands follow the 2023-24 mid-level (about 9% of the cap),
    taxpayer mid-level and bi-annual (about 3.5-4%); near_max 0.20-0.30; max_range >= 0.30. Constants declared here, never tuned."""
    e = (
        99
        if exp in (None, "") or (isinstance(exp, float) and np.isnan(exp))
        else (0 if str(exp) == "R" else int(str(exp)))
    )
    if e <= 3 and cap_share >= 0.012:
        return "rookie_scale"
    if cap_share < 0.025:
        return "minimum"
    if cap_share < 0.045:
        return "low_exception"
    if cap_share < 0.10:
        return "mid_exception"
    if cap_share < 0.20:
        return "rotation_salary"
    if cap_share < 0.30:
        return "near_max"
    return "max_range"


def team_year_features(rows: pd.DataFrame, roster: pd.DataFrame) -> pd.DataFrame:
    """Feature frame per (team, br_year, player_bref_id) with cap share, payroll share, within-team salary rank (alive rows only; rank 1
    = highest), the team's tax state (payroll of ALL lines incl. dead money against that year's tax line and aprons), the inferred
    contract class and age at 1 October of the season start. As-of: the BR page at pull time (2026-10-21); see 'Known-at' in the doc."""
    out = rows.copy()
    pay = out.groupby(["team", "br_year"]).salary.sum().rename("payroll").reset_index()
    out = out.merge(pay, on=["team", "br_year"])
    out["cap"] = out.br_year.map(lambda y: CAP_TABLE[y]["cap"])
    out["cap_share"] = out.salary / out.cap
    out["payroll_share"] = out.salary / out.payroll
    out["tax_state"] = [tax_state(p, y) for p, y in zip(out.payroll, out.br_year, strict=True)]
    out["over_tax_by_cap_share"] = (
        out.payroll - out.br_year.map(lambda y: CAP_TABLE[y]["tax"])
    ) / out.cap
    live = out[out.alive]
    rk = (
        live.groupby(["team", "br_year"])
        .salary.rank(ascending=False, method="min")
        .rename("salary_rank")
    )
    out = out.join(rk)
    r = roster.drop_duplicates(["team", "br_year", "player_bref_id"])[
        ["team", "br_year", "player_bref_id", "birth_date", "years_experience", "pos"]
    ]
    out = out.merge(r, on=["team", "br_year", "player_bref_id"], how="left")
    bd = pd.to_datetime(out.birth_date, format="%B %d, %Y", errors="coerce")
    start = pd.to_datetime((out.br_year - 1).astype(str) + "-10-01")
    out["age"] = (start - bd).dt.days / 365.25
    out["contract_class"] = [
        contract_class(a, b) for a, b in zip(out.cap_share, out.years_experience, strict=True)
    ]
    return out


# --------------------------------------------------------------------------- blend (rule; no outcome is an argument)


def lam_blend(
    role_value: np.ndarray, base_value: np.ndarray, g: np.ndarray, K: int, w_max: float = W_MAX
) -> np.ndarray:
    """Multiplier on the served grid: ((1-w') base + w' role) / base with w' = w_max (1 - g/K), clipped to [0.5, 2]. Rows with g >= K,
    non-finite inputs or base <= 0 are served as production (1.0)."""
    w = w_max * np.clip(1.0 - np.asarray(g, float) / K, 0.0, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        lam = ((1.0 - w) * base_value + w * role_value) / base_value
    lam = np.where(np.isfinite(lam) & (base_value > 0), lam, 1.0)
    return np.clip(lam, 0.5, 2.0)


def known_at_ok(game_date: Any, known_at: Any) -> bool:
    """A salary line may be used for a row only at or after its stamp (the player's first regular-season game with the team in the
    season; no stamp = no game evidence, treated as usable only for dead-money / coverage counts, never for a scored row)."""
    if known_at is None or pd.isna(known_at):
        return True
    return bool(pd.Timestamp(game_date) >= pd.Timestamp(known_at))
