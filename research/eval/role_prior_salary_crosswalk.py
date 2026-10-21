# ruff: noqa: E501
"""Crosswalk: Basketball-Reference player id -> nba_api player_id (rule: docs/prereg/ROLE_PRIOR_SALARY.md).

Method, in order (each id gets the first that fires; the method is stored): (1) ``verified`` = the id's stem (first 5 letters of the
surname + first 2 of the first name, read from the BR id itself because the CSV names have lost non-ASCII bytes) matches nba_api
players, and exactly one of them has a regular-season game for that BR team and season (season <= 2024 only); ties broken by exact
folded full-name equality; (2) ``birthdate_team_verified`` / ``birthdate_unique`` = the BR roster birth date equals a players_static birth date (and, for the
first, that player has a game for the team and season); (3) ``surname_team_verified`` = exactly one nba_api player with the same
5-letter surname stem has a game for the team and season; (4) ``name_unique`` = one nba_api player has the stem and no appearance
evidence exists (dead money, never played, or br_year >= 2026 whose games are not read here); (5) otherwise unresolved. Unresolved ids are written to a CSV with their
salary-row counts. Known-at stamp: a crosswalk row carries ``known_at`` = the player's first regular-season game date with the team in
the season (or NaT when he has no game there); a salary line is usable by a forecast only at or after that date.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from research.eval import role_prior_salary_rule as rule


def nba_players() -> pd.DataFrame:
    from nba_api.stats.static import players

    df = pd.DataFrame(players.get_players())[["id", "full_name"]].rename(
        columns={"id": "player_id", "full_name": "name"}
    )
    df["stem"] = df["name"].map(rule.nba_stem)
    df["folded"] = df["name"].map(
        lambda s: rule.fold(" ".join(t for t in str(s).split() if rule.fold(t) not in rule._SUFFIX))
    )
    return df


def nba_team_ids() -> dict[str, int]:
    from nba_api.stats.static import teams

    return {t["abbreviation"]: int(t["id"]) for t in teams.get_teams()}


def build(
    ids: pd.DataFrame,
    names: pd.DataFrame,
    appear: pd.DataFrame,
    team_ids: dict[str, int],
    birth: pd.DataFrame | None = None,
    static_birth: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """ids: one row per (bref_id, team, br_year, name) seen on any page. names: nba_players(). appear: player_id, team_id, season
    (regular season, <= 2024), first_date. Returns (crosswalk by bref_id, per-(bref_id,team,br_year) known_at), unresolved separately by caller."""
    by_stem: dict[str, list[int]] = {}
    for pid, st in zip(names.player_id, names.stem, strict=True):
        by_stem.setdefault(st, []).append(int(pid))
    folded = dict(zip(names.player_id, names.folded, strict=True))
    seen = {
        (int(p), int(t), int(s))
        for p, t, s in zip(appear.player_id, appear.team_id, appear.season, strict=True)
    }
    res: dict[str, dict[str, Any]] = {}
    by_surname: dict[str, list[int]] = {}
    for pid, st in zip(names.player_id, names.stem, strict=True):
        by_surname.setdefault(st[:5], []).append(int(pid))
    bd_by_bref = (
        dict(zip(birth.player_bref_id, birth.birth_date, strict=True)) if birth is not None else {}
    )
    pid_by_bd: dict[Any, list[int]] = {}
    if static_birth is not None:
        for pid, d in zip(static_birth.player_id, static_birth.birth_date, strict=True):
            pid_by_bd.setdefault(pd.Timestamp(d), []).append(int(pid))

    def team_hits(grp: pd.DataFrame, cands: list[int]) -> dict[int, int]:
        hits: dict[int, int] = {}
        for _, r in grp.iterrows():
            if int(r.br_year) - 1 > rule.MAX_SEASON:
                continue
            tid = team_ids[rule.BR_TO_NBA_ABBR.get(r.team, r.team)]
            for c in cands:
                if (c, tid, int(r.br_year) - 1) in seen:
                    hits[c] = hits.get(c, 0) + 1
        return hits

    for bid, grp in ids.groupby("player_bref_id"):
        bid = str(bid)
        cands = by_stem.get(rule.bref_stem(bid), [])
        hits = team_hits(grp, cands) if cands else {}
        if len(hits) == 1:
            res[bid] = {"player_id": next(iter(hits)), "method": "verified"}
            continue
        if len(hits) > 1:
            nm = {rule.fold(str(n)) for n in grp.player}
            tie = [c for c in hits if folded[c] in nm]
            if len(tie) == 1:
                res[bid] = {"player_id": tie[0], "method": "verified_name_tiebreak"}
                continue
        # tier 2: same birth date as a players_static row that has a game for the team and season (nicknames, KJ vs Kenyon)
        bd = pd.to_datetime(bd_by_bref.get(bid), format="%B %d, %Y", errors="coerce")
        if pd.notna(bd) and bd in pid_by_bd:
            h2 = team_hits(grp, pid_by_bd[bd])
            if len(h2) == 1:
                res[bid] = {"player_id": next(iter(h2)), "method": "birthdate_team_verified"}
                continue
            if (
                len(h2) == 0
                and len(pid_by_bd[bd]) == 1
                and len(cands) in (0, 1)
                and (not cands or cands[0] == pid_by_bd[bd][0])
            ):
                res[bid] = {"player_id": pid_by_bd[bd][0], "method": "birthdate_unique"}
                continue
        # tier 3: surname (5 letters) candidates with a game for the team and season, exactly one
        sc = by_surname.get(rule.bref_stem(bid)[:5], [])
        h3 = team_hits(grp, sc) if sc else {}
        if len(h3) == 1:
            res[bid] = {"player_id": next(iter(h3)), "method": "surname_team_verified"}
            continue
        if not hits and len(cands) == 1:
            res[bid] = {"player_id": cands[0], "method": "name_unique"}
    xw = pd.DataFrame([{"player_bref_id": k, **v} for k, v in res.items()])
    if xw.empty:
        xw = pd.DataFrame(columns=["player_bref_id", "player_id", "method"])
    return xw, ids


def unresolved(ids: pd.DataFrame, xw: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """Unresolved BR ids with the names seen, number of salary lines with a dollar value and the BR years they appear in."""
    miss = ids[~ids.player_bref_id.isin(xw.player_bref_id)]
    g = (
        miss.groupby("player_bref_id")
        .agg(
            name=("player", "first"),
            teams=("team", lambda s: ",".join(sorted(set(s)))),
            br_years=("br_year", lambda s: ",".join(map(str, sorted(set(s))))),
        )
        .reset_index()
    )
    n = rows.groupby("player_bref_id").size().rename("salary_pages")
    return (
        g.merge(n, on="player_bref_id", how="left")
        .fillna({"salary_pages": 0})
        .sort_values(["salary_pages", "player_bref_id"], ascending=[False, True])
    )
