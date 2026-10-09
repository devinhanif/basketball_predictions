"""Populate the OOF store / build router inputs from ``nba.duckdb`` (read-only).

    uv run python -m nba.stack.populate season-avg --stats pts reb ast fg3m
    uv run python -m nba.stack.populate context --out data/stack/context.parquet

Never opens ``nba.duckdb`` for writing; never emits season >= 2025.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from nba.stack import FROZEN_SEASON
from nba.stack.adapters import season_avg_oof
from nba.stack.oof import write_oof

SEASON_AVG_VERSION = "v1-hl10"
CTXRES_MODEL = "props_context_residual"
CTXRES_VERSION = "v1"
EXP2_VERSION = "exp2-20261008_171446"
ELO_VERSION = "v1"

_SQL = """
SELECT s.game_id, s.player_id, g.game_date, g.season, s.minutes, s.starter,
       s.pts, s.reb, s.ast, s.fg3m
FROM player_game_stats s JOIN games g USING (game_id)
WHERE g.season <= ?
"""


def load_history(db: str, max_season: int) -> pd.DataFrame:
    con = duckdb.connect(db, read_only=True)
    try:
        df = con.execute(_SQL, [max_season]).df()
    finally:
        con.close()
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["played"] = df["minutes"].fillna(0) > 0
    return df


def history_from_con(con: duckdb.DuckDBPyConnection, before: Any) -> pd.DataFrame:
    """Same shape as :func:`load_history` from an open (read-only) connection, restricted to
    games strictly before ``before`` (a date); used for forward / daily application."""
    df = con.execute(_SQL.replace("WHERE g.season <= ?", "WHERE g.game_date < ?"), [before]).df()
    df["game_date"] = pd.to_datetime(df["game_date"])
    df["played"] = df["minutes"].fillna(0) > 0
    return df


def basic_context(hist: pd.DataFrame) -> pd.DataFrame:
    """As-of context per player-game (strictly prior games only).

    ``games_played_season``, ``career_played`` (prior), ``min_avg10`` (mean minutes of
    the last 10 prior played games), ``min_trend`` (last-3 minus last-10 mean minutes),
    ``log_career``, ``start_rate10`` (share of last 10 prior played
    games started), ``cold_start_bucket`` (career_played < 20 -> 'cold').
    The actual ``starter`` flag is returned too but is a SLICE label only; do not
    feed it to the gate.
    """
    h = hist.sort_values(["player_id", "game_date", "game_id"]).copy()
    pl = h["played"].astype(float)
    h["_p"] = pl
    g = h.groupby("player_id", sort=False)
    h["career_played"] = g["_p"].cumsum() - h["_p"]
    h["games_played_season"] = (
        h.groupby(["player_id", "season"], sort=False)["_p"].cumsum() - h["_p"]
    )
    mins = h["minutes"].where(h["played"])
    starts = h["starter"].astype(float).where(h["played"])
    h["_m"], h["_s"] = mins, starts
    h["min_avg10"] = g["_m"].transform(lambda s: s.shift(1).rolling(10, min_periods=1).mean())
    h["min_avg3"] = g["_m"].transform(lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    h["min_trend"] = h["min_avg3"] - h["min_avg10"]
    h["log_career"] = np.log1p(h["career_played"])
    h["start_rate10"] = g["_s"].transform(lambda s: s.shift(1).rolling(10, min_periods=1).mean())
    h["cold_start_bucket"] = np.where(h["career_played"] < 20, "cold", "established")
    h["starter"] = h["starter"].astype(float)
    return h[
        [
            "game_id",
            "player_id",
            "games_played_season",
            "career_played",
            "min_avg10",
            "min_trend",
            "log_career",
            "start_rate10",
            "cold_start_bucket",
            "starter",
        ]
    ]


#: Gate features for the props routers: all as-of, computed identically for
#: training rows and for forward (daily) rows. ``starter`` is a slice label only.
PROPS_GATE_FEATURES = [
    "log_career",
    "games_played_season",
    "min_avg10",
    "min_trend",
    "start_rate10",
    "cold_start_bucket",
]
#: Optional extra gate features (archetype / position group, as-of season start).
PROPS_GATE_FEATURES_ARCH = [*PROPS_GATE_FEATURES, "archetype", "pos_group"]
WIN_GATE_FEATURES = ["abs_logit", "logit_gap", "team_game_no"]


ARCHETYPE_K = 5  # fixed in the pre-registration; not tuned on any OOF score
ARCHETYPE_FIT_SEASON = 2023


def archetype_position_table(db: str, seasons: list[int], k: int = ARCHETYPE_K) -> pd.DataFrame:
    """``player_id, season, archetype, pos_group`` (strings), strictly as-of.

    KMeans is fit ONCE on the feature frame as of ``{ARCHETYPE_FIT_SEASON}-10-01`` and
    FROZEN; every season is scored by that frozen model on the features as of its own
    ``{season}-10-01`` (so labels are comparable across seasons and nothing later than
    the season start is used). ``pos_group`` = argmax of the guard/forward/center
    fractions (static ``players_static.position``). Players absent from
    ``players_static`` get ``'unk'`` after the merge.
    """
    from nba.coldstart.archetypes import (
        assign_archetypes,
        build_player_archetypes,
        build_player_feature_frame,
    )

    con = duckdb.connect(db, read_only=True)
    try:
        fit_date = f"{ARCHETYPE_FIT_SEASON}-10-01"
        model = build_player_archetypes(con, fit_date, k=k, seed=0)
        rows: list[pd.DataFrame] = []
        for season in seasons:
            ff = build_player_feature_frame(con, f"{season}-10-01")
            if ff.height == 0:
                continue
            lab = assign_archetypes(model, ff).to_pandas()
            fr = ff.select(["player_id", "is_guard", "is_forward", "is_center"]).to_pandas()
            pos = np.array(["guard", "forward", "center"])[
                fr[["is_guard", "is_forward", "is_center"]].to_numpy().argmax(axis=1)
            ]
            out = lab.merge(fr[["player_id"]].assign(pos_group=pos), on="player_id")
            out["archetype"] = "a" + out["archetype"].astype(str)
            out["season"] = season
            rows.append(out)
    finally:
        con.close()
    return pd.concat(rows, ignore_index=True)


def attach_archetypes(
    gate: pd.DataFrame, sl: pd.DataFrame, hist: pd.DataFrame, table: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add ``archetype`` / ``pos_group`` to both the gate context and slice table."""
    key = hist.loc[hist["played"], ["game_id", "player_id", "season"]].drop_duplicates(
        ["game_id", "player_id"]
    )
    t = key.merge(table, on=["player_id", "season"], how="left")[
        ["game_id", "player_id", "archetype", "pos_group"]
    ]
    t["archetype"] = t["archetype"].fillna("unk")
    t["pos_group"] = t["pos_group"].fillna("unk")
    return (
        gate.merge(t, on=["game_id", "player_id"], how="left"),
        sl.merge(t, on=["game_id", "player_id"], how="left"),
    )


def forward_context(hist: pd.DataFrame, slate: pd.DataFrame) -> pd.DataFrame:
    """Gate features for not-yet-played rows. ``hist``: rows of :func:`load_history`
    shape strictly before the slate; ``slate``: ``game_id, player_id, game_date, season``.
    Placeholder rows are appended with no minutes, so every feature is computed from
    prior games only (the same code path as training)."""
    ph = slate[["game_id", "player_id", "game_date", "season"]].copy()
    ph["game_date"] = pd.to_datetime(ph["game_date"])
    for c in ("minutes", "pts", "reb", "ast", "fg3m", "starter"):
        ph[c] = np.nan
    ph["played"] = False
    if (hist["game_date"] >= ph["game_date"].min()).any():
        raise ValueError("hist must be strictly before the slate (as-of)")
    both = pd.concat([hist, ph], ignore_index=True)
    ctx = basic_context(both)
    ctx = ctx.merge(ph[["game_id", "player_id"]], on=["game_id", "player_id"], how="inner")
    return ctx


def props_outcomes(hist: pd.DataFrame, stat: str) -> pd.DataFrame:
    """``game_id, player_id, y`` for played rows only (conditional-on-play contract)."""
    h = hist[hist["played"]]
    return h.rename(columns={stat: "y"})[["game_id", "player_id", "y"]].reset_index(drop=True)


def props_gate_context(
    hist: pd.DataFrame, meta_path: str | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(gate context, slice table) keyed by ``game_id, player_id`` for played rows.

    The slice table carries the evaluation labels: ``cold_start_bucket``,
    ``games_played_season``, ``starter`` (as-of start rate >= 0.5, never a gate
    feature) and, when the seq_props meta parquet exists, ``teammate``
    (reported-OUT rotation teammates; slice only, not a gate feature in v1).
    """
    ctx = basic_context(hist)
    played = hist.loc[hist["played"], ["game_id", "player_id"]]
    ctx = ctx.merge(played, on=["game_id", "player_id"], how="inner")
    sl = ctx[["game_id", "player_id", "cold_start_bucket", "games_played_season"]].copy()
    sl["starter"] = (ctx["start_rate10"].fillna(0.0) >= 0.5).astype(float)
    if meta_path and Path(meta_path).exists():
        meta = pd.read_parquet(
            meta_path, columns=["game_id", "player_id", "has_report", "n_rep_out"]
        )
        sl = sl.merge(meta, on=["game_id", "player_id"], how="left")
        sl["teammate"] = np.where(
            sl["has_report"].fillna(0) == 0,
            "no_report",
            np.where(sl["n_rep_out"].fillna(0) > 0, "teammate_out", "none_out"),
        )
        sl = sl.drop(columns=["has_report", "n_rep_out"])
    gate = ctx[["game_id", "player_id", *PROPS_GATE_FEATURES]]
    return gate, sl


def win_outcomes_and_context(
    db: str, p_by_model: dict[str, pd.DataFrame], max_season: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(outcomes, gate context, slice table) for game-level win routing.

    ``p_by_model``: ``{name: frame with game_id, p}`` (>= 2 models; the first is the
    reference for ``abs_logit``, the gap is first minus second). Everything is as-of:
    ``team_game_no`` counts the team's earlier games in the season.
    """
    from nba.stack.scoring import logit

    con = duckdb.connect(db, read_only=True)
    try:
        g = con.execute(
            """SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts
               FROM games WHERE season <= ? AND home_pts > 0 AND away_pts > 0""",
            [max_season],
        ).df()
    finally:
        con.close()
    g["game_date"] = pd.to_datetime(g["game_date"])
    g = g.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    long = pd.concat(
        [
            g[["game_id", "game_date", "season", "home_team"]].rename(
                columns={"home_team": "team"}
            ),
            g[["game_id", "game_date", "season", "away_team"]].rename(
                columns={"away_team": "team"}
            ),
        ]
    ).sort_values(["team", "game_date", "game_id"])
    long["team_game_no"] = long.groupby(["team", "season"]).cumcount()
    tg = long.groupby("game_id")["team_game_no"].min().rename("team_game_no")
    g = g.merge(tg, on="game_id")
    names = list(p_by_model)
    base = g[["game_id", "team_game_no"]].copy()
    for n in names[:2]:
        base = base.merge(
            p_by_model[n][["game_id", "p"]].rename(columns={"p": f"p_{n}"}), on="game_id"
        )
    la, lb = logit(base[f"p_{names[0]}"].to_numpy()), logit(base[f"p_{names[1]}"].to_numpy())
    ctx = pd.DataFrame(
        {
            "game_id": base["game_id"].to_numpy(),
            "player_id": -1,
            "abs_logit": np.abs(la),
            "logit_gap": la - lb,
            "team_game_no": base["team_game_no"].to_numpy(dtype=float),
        }
    )
    outcomes = pd.DataFrame(
        {
            "game_id": g["game_id"].to_numpy(),
            "player_id": -1,
            "y": (g["home_pts"] > g["away_pts"]).astype(float).to_numpy(),
        }
    )
    sl = pd.DataFrame(
        {
            "game_id": ctx["game_id"],
            "player_id": -1,
            "games_played_season": ctx["team_game_no"],
            "favorite": np.where(la > 0, "home_fav", "away_fav"),
        }
    )
    return outcomes, ctx, sl


def populate_artifacts(
    *,
    ctxres: str,
    seq: str | None,
    exp2_dir: str | None,
    elo: str | None,
    oof_path: str | None,
    exp2_arms: tuple[str, ...] = ("xgb_v12_poisson_nb",),
) -> list[str]:
    """Write existing artifacts into the OOF store (idempotent per model/version)."""
    from nba.stack import artifacts as art

    lines: list[str] = []
    cr = art.context_residual_frame(ctxres)
    n = write_oof(cr, CTXRES_MODEL, CTXRES_VERSION, oof_path)
    lines.append(f"{CTXRES_MODEL} {CTXRES_VERSION}: wrote {n} rows")
    if seq:
        n = write_oof(art.seq_props_frame(seq), "seq_props", "v1", oof_path)
        lines.append(f"seq_props v1: wrote {n} rows")
    if exp2_dir:
        d24 = cr[cr["season"] == 2024]
        for arm in exp2_arms:
            f = art.exp2_arm_frame(Path(exp2_dir) / f"{arm}.parquet", d24)
            n = write_oof(f, f"ctxres_v2_{arm}", EXP2_VERSION, oof_path)
            lines.append(f"ctxres_v2_{arm} {EXP2_VERSION}: wrote {n} rows")
    if elo:
        for model, f in art.elo_frames(elo).items():
            n = write_oof(f, model, ELO_VERSION, oof_path)
            lines.append(f"{model} {ELO_VERSION}: wrote {n} rows")
    return lines


def populate_season_avg(db: str, stats: list[str], oof_path: str | None, max_season: int) -> None:
    if max_season >= FROZEN_SEASON:
        raise SystemExit(f"--max-season must be < {FROZEN_SEASON}")
    hist = load_history(db, max_season)
    for stat in stats:
        h = hist.rename(columns={stat: "y"})[
            ["game_id", "player_id", "game_date", "season", "y", "played"]
        ]
        frame = season_avg_oof(h, stat, max_season=max_season)
        n = write_oof(frame, f"season_avg_{stat}", SEASON_AVG_VERSION, oof_path)
        print(f"season_avg_{stat} {SEASON_AVG_VERSION}: wrote {n} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("season-avg")
    a.add_argument("--db", default="nba.duckdb")
    a.add_argument("--stats", nargs="+", default=["pts", "reb", "ast", "fg3m"])
    a.add_argument("--oof", default=None)
    a.add_argument("--max-season", type=int, default=FROZEN_SEASON - 1)
    r = sub.add_parser("artifacts", help="load existing OOF artifacts (read-only parquet reads)")
    r.add_argument("--ctxres", default="reports/context_residual/oof_context_residual.parquet")
    r.add_argument("--seq", default="data/colab/runs/seq_props/20261008_125041/oof_2024.parquet")
    r.add_argument("--exp2-dir", default="data/colab/runs/ctxres_v2_sweep/20261008_171446/oof")
    r.add_argument("--elo", default="data/injury_elo/oof_predictions.parquet")
    r.add_argument("--oof", default=None)
    c = sub.add_parser("context")
    c.add_argument("--db", default="nba.duckdb")
    c.add_argument("--out", default="data/stack/context.parquet")
    c.add_argument("--max-season", type=int, default=FROZEN_SEASON - 1)
    args = ap.parse_args()
    if args.cmd == "season-avg":
        populate_season_avg(args.db, args.stats, args.oof, args.max_season)
    elif args.cmd == "artifacts":
        for line in populate_artifacts(
            ctxres=args.ctxres,
            seq=args.seq,
            exp2_dir=args.exp2_dir,
            elo=args.elo,
            oof_path=args.oof,
        ):
            print(line)
    else:
        ctx = basic_context(load_history(args.db, args.max_season))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        ctx.to_parquet(args.out)
        print(f"wrote {len(ctx)} context rows -> {args.out}")


if __name__ == "__main__":
    main()
