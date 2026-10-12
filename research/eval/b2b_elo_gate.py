# ruff: noqa: E501
"""B2B_ELO minimum-data gate (docs/prereg/B2B_ELO.md, DRAFT). Seasons 2023-24 only.

    .venv/bin/python -W ignore -m research.eval.b2b_elo_gate

Builds the as-of back-to-back flags from the schedule (``games``: a team is on the second night of a back-to-back
iff it has a game dated the previous calendar day) for every stored production injury-Elo OOF game
(``data/injury_elo_realtip/oof_predictions.parquet``) and sizes the rule outcome-free: the hypothesised arm is the
production logit plus ``beta * (b2b_home - b2b_away)`` with beta from the descriptive look already seen (-0.35) and a
halved value (-0.175, winner's-curse shrinkage); the expected log-loss gain if it were the truth is KL(p_arm || p_prod)
per game, and the per-game sd is the Bernoulli-implied sd of the same contrast (plus the stored injury-minus-MOV sd as a
conservative bound). Nothing is fitted or scored. Writes ``reports/prereg_b2b_elo/gate.json``.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from typing import Any

import numpy as np
import polars as pl

from research.eval import prereg_gates_common as C

OUT = C.ROOT / "reports/prereg_b2b_elo/gate.json"
OOF = C.ROOT / "data/injury_elo_realtip/oof_predictions.parquet"
FLOOR = (
    -0.002
)  # win log loss, a term added to injury-Elo (INJURY_ELO_T30's floor); -0.004 also reported
ALT_FLOOR = -0.004
BETAS = (-0.35, -0.175)
CLOSE = 0.2
EARLY_GAMES = 25


def build_b2b(games: pl.DataFrame) -> pl.DataFrame:
    """Per game: b2b_home, b2b_away (1 if that team has any game dated the previous calendar day), b2b_diff.
    Reads only game dates and team ids of games dated strictly before the game (a schedule fact)."""
    tg = pl.concat(
        [
            games.select("game_id", "game_date", pl.col("home_team").alias("team_id")),
            games.select("game_id", "game_date", pl.col("away_team").alias("team_id")),
        ]
    )
    played = {(int(t), d) for t, d in tg.select("team_id", "game_date").rows()}
    one = dt.timedelta(days=1)
    rows = [
        (str(g), int((int(h), d - one) in played), int((int(a), d - one) in played))
        for g, d, h, a in games.select("game_id", "game_date", "home_team", "away_team").rows()
    ]
    out = pl.DataFrame(
        rows, schema={"game_id": pl.Utf8, "b2b_home": pl.Int64, "b2b_away": pl.Int64}, orient="row"
    )
    return out.with_columns((pl.col("b2b_home") - pl.col("b2b_away")).alias("b2b_diff"))


def main() -> dict[str, Any]:
    t0 = time.time()
    con = C.connect()
    games = C.load_games(con)
    b2b = build_b2b(games)
    box = C.load_box(con)
    tg = C.team_game_index(box, games)
    oof = pl.read_parquet(OOF)
    if int(np.max(oof["season"].to_numpy())) > C.MAX_SEASON:
        raise RuntimeError("season > 2024 in the injury-Elo OOF file")
    oof = oof.filter(pl.col("season").is_in(C.SEASONS))
    prod = oof.filter(pl.col("model") == "rung0_injury_elo").select(
        "game_id", pl.col("p").alias("p_prod"), "season"
    )
    mov = oof.filter(pl.col("model") == "rung0_mov_elo").select(
        "game_id", pl.col("p").alias("p_mov")
    )
    f = (
        prod.join(mov, on="game_id")
        .join(b2b, on="game_id", how="left")
        .join(
            games.select("game_id", "game_date", "home_team", "away_team", "home_pts", "away_pts"),
            on="game_id",
        )
    )
    kidx = tg.select("game_id", "team_id", "k")
    f = f.join(
        kidx.rename({"team_id": "home_team", "k": "k_home"}),
        on=["game_id", "home_team"],
        how="left",
    ).join(
        kidx.rename({"team_id": "away_team", "k": "k_away"}),
        on=["game_id", "away_team"],
        how="left",
    )
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "seasons": list(C.SEASONS),
        "null_b2b": int(f["b2b_diff"].is_null().sum()),
    }
    p0 = np.clip(f["p_prod"].to_numpy(), 1e-6, 1 - 1e-6)
    z0 = np.log(p0 / (1 - p0))
    bd = f["b2b_diff"].to_numpy().astype(float)
    either = (f["b2b_home"].to_numpy() + f["b2b_away"].to_numpy()) > 0
    y = (f["home_pts"].to_numpy() > f["away_pts"].to_numpy()).astype(float)
    pm = np.clip(f["p_mov"].to_numpy(), 1e-6, 1 - 1e-6)

    def ll(p: np.ndarray) -> np.ndarray:
        return np.asarray(-(y * np.log(p) + (1 - y) * np.log(1 - p)))

    d_proxy = ll(p0) - ll(pm)  # stored, frozen T128 contrast; only its spread is used
    season = f["season"].to_numpy()
    dates = f["game_date"].cast(pl.Utf8).to_numpy()
    close = np.abs(p0 - 0.5) < CLOSE
    early = (f["k_home"].fill_null(99).to_numpy() < EARLY_GAMES) | (
        f["k_away"].fill_null(99).to_numpy() < EARLY_GAMES
    )
    per: dict[str, Any] = {}
    for s in C.SEASONS:
        m = season == s
        row: dict[str, Any] = {
            "games": int(m.sum()),
            "either_b2b": int((m & either).sum()),
            "b2b_diff_nonzero": int((m & (bd != 0)).sum()),
            "home_only": int((m & (bd > 0)).sum()),
            "away_only": int((m & (bd < 0)).sum()),
            "close_games": int((m & close).sum()),
            "early_games": int((m & early).sum()),
            "proxy_sd_injury_minus_mov": float(np.std(d_proxy[m], ddof=1)),
        }
        cd = C.cluster_stats(d_proxy[m], dates[m])
        for beta in BETAS:
            p1 = 1 / (1 + np.exp(-(z0 + beta * bd)))
            bg = C.bernoulli_gain(p0, p1)
            sd_b = float(np.sqrt(bg["var"][m].mean()))
            e = float(-bg["kl"][m].mean())
            row[f"beta_{beta}"] = {
                "expected_delta_all": e,
                "expected_delta_close": float(-bg["kl"][m & close].mean()),
                "expected_delta_early": float(-bg["kl"][m & early].mean()),
                "primary_bernoulli_sd": C.sizing_row(
                    "all games, floor -0.002, sd if arm true",
                    sd_b,
                    max(1.0, cd["deff"]),
                    m.sum(),
                    FLOOR,
                    e,
                ),
                "primary_proxy_sd": C.sizing_row(
                    "all games, floor -0.002, stored injury-minus-MOV sd",
                    row["proxy_sd_injury_minus_mov"],
                    max(1.0, cd["deff"]),
                    m.sum(),
                    FLOOR,
                    e,
                ),
                "alt_floor_bernoulli_sd": C.sizing_row(
                    "all games, floor -0.004", sd_b, max(1.0, cd["deff"]), m.sum(), ALT_FLOOR, e
                ),
            }
        row["deff_date"] = cd["deff"]
        per[str(s)] = row
    out["sizing"] = per
    gate: list[dict[str, Any]] = []
    for s in C.SEASONS:
        b = per[str(s)]["beta_-0.35"]["primary_bernoulli_sd"]
        gate.append(
            {
                "check": f"{s} half-width <= 0.002 (arm-implied sd)",
                "value": b["halfwidth95"],
                "pass": b["hw_le_floor"],
            }
        )
        gate.append(
            {
                "check": f"{s} power >= 0.5 at beta -0.35",
                "value": b["power_one_season"],
                "pass": b["power_one_season"] >= 0.5,
            }
        )
        gate.append(
            {
                "check": f"{s} >= 300 games with a b2b difference",
                "value": per[str(s)]["b2b_diff_nonzero"],
                "pass": per[str(s)]["b2b_diff_nonzero"] >= 300,
            }
        )
    out["gate"] = gate
    out["gate_pass"] = all(x["pass"] for x in gate)
    out["wall_s"] = time.time() - t0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps(out, indent=1, default=float))
    return out


if __name__ == "__main__":
    main()
