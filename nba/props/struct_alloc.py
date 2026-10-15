# ruff: noqa: E501
"""STRUCT: allocate the vacated production of tonight's OUT rotation players by a structural rule (DRAFT, WOWY family).

No player's own with/without history is used. For a target game g of team T:

* the OUT set is the production one (``wowy.out_rotation_players``); each OUT player's as-of state is the
  exponentially weighted (half-life 10 games) mean of his minutes and stats over games strictly before g's date;
* ``Vmax_s`` = the largest single OUT player's as-of per-game stat (G5 star out);
* ``V_s`` = sum over the OUT set of the as-of per-game stat (``= vac_s`` of production, parity-tested);
* the remaining players are the as-of rotation pool of T (appeared in >= 5 of T's previous 10 games with a mean
  of >= 12 minutes: ``wowy.asof_rotation_pool``) minus the OUT set, plus the scored player himself if he is not in
  the pool (never tonight's box);
* ``struct_d_s(i) = V_s x w_i / sum_k w_k`` over the remaining players; ``w`` is the as-of per-game stat itself
  (= per-minute rate x expected minutes, version RATE) or, for pts and fg3m, the usage proxy FGA + 0.44 FTA + TOV
  (version USAGE). Per-100-possession rates are not built: the closest as-of equivalent is per minute.
* minutes version: each OUT player's as-of minutes are handed out pro rata to the remaining players' as-of minutes
  (``struct_dmin_role``), or with weight 2 for players of the OUT player's position group (``struct_dmin_pos``;
  position group = first letter of ``players_static.position``).

Missing/empty: 0 when nobody is OUT. Only rows strictly before g enter any state.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

HALF_LIFE = 10.0
STATE_COLS = (
    "minutes",
    "pts",
    "reb",
    "ast",
    "fg3m",
    "fga",
    "fta",
    "tov",
    "fg3a",
    "starter",
    "touches",
    "passes",
    "sec_ast",
    "ft_ast",
    "rbc",
    "unc",
    "con",
)
#: as-of traits for the PROP propensity (all from the decayed states; ratios of decayed means)
TRAITS = (
    "tr_use",
    "tr_a2u",
    "tr_f3",
    "tr_ftr",
    "tr_start",
    "tr_min",
    "tr_touch",
    "tr_ppt",
    "tr_sec",
    "tr_rbc",
    "tr_unc",
    "tr_posG",
    "tr_posC",
)
STATS = ("pts", "reb", "ast", "fg3m")
USAGE_STATS = ("pts", "fg3m")


def decayed_states(box: pl.DataFrame, half_life: float = HALF_LIFE) -> pl.DataFrame:
    """State of every player AFTER each played game: player_id, game_date, n_after, st_<col> (decayed means).
    ``box``: played rows with player_id, game_id, game_date and the ``STATE_COLS``."""
    b = box.sort(["player_id", "game_date", "game_id"])
    x = np.column_stack(
        [
            b[c].cast(pl.Float64).fill_null(0.0).to_numpy()
            if c in b.columns
            else np.zeros(b.height)
            for c in STATE_COLS
        ]
    )
    pid = b["player_id"].to_numpy()
    dec = 0.5 ** (1.0 / half_life)
    n = len(pid)
    post = np.empty_like(x)
    cnt = np.zeros(n, dtype=np.int32)
    s = np.zeros(x.shape[1])
    d = 0.0
    c = 0
    for i in range(n):
        if i == 0 or pid[i] != pid[i - 1]:
            s = np.zeros(x.shape[1])
            d = 0.0
            c = 0
        s = dec * s + x[i]
        d = dec * d + 1.0
        c += 1
        post[i] = s / d
        cnt[i] = c
    cols: dict[str, Any] = {
        "player_id": b["player_id"].cast(pl.Int64),
        "game_date": b["game_date"],
        "n_after": pl.Series(cnt),
    }
    for k, name in enumerate(STATE_COLS):
        cols[f"st_{name}"] = pl.Series(post[:, k])
    return pl.DataFrame(cols).sort("game_date")


def _asof(pairs: pl.DataFrame, states: pl.DataFrame) -> pl.DataFrame:
    """Attach the state after the player's latest game strictly before ``game_date`` (pairs: player_id, game_date)."""
    left = pairs.with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key")).sort(
        "_key"
    )
    return left.join_asof(
        states.rename({"game_date": "_pd"}),
        left_on="_key",
        right_on="_pd",
        by="player_id",
        strategy="backward",
    ).drop("_key", "_pd")


def _usage(prefix: str = "st_") -> pl.Expr:
    return pl.col(f"{prefix}fga") + 0.44 * pl.col(f"{prefix}fta") + pl.col(f"{prefix}tov")


def _div(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    return pl.when(den > 0).then(num / den).otherwise(0.0)


def trait_exprs() -> list[pl.Expr]:
    """Traits from the as-of state columns ``st_*`` and the position group ``grp``."""
    use = _usage()
    m = pl.col("st_minutes")
    return [
        _div(use * 36.0, m).alias("tr_use"),
        _div(pl.col("st_ast"), use).alias("tr_a2u"),
        _div(pl.col("st_fg3a"), pl.col("st_fga")).alias("tr_f3"),
        _div(pl.col("st_fta"), pl.col("st_fga")).alias("tr_ftr"),
        pl.col("st_starter").alias("tr_start"),
        (m / 36.0).alias("tr_min"),
        _div(pl.col("st_touches") * 36.0, m).alias("tr_touch"),
        _div(pl.col("st_passes"), pl.col("st_touches")).alias("tr_ppt"),
        _div((pl.col("st_sec_ast") + pl.col("st_ft_ast")) * 36.0, m).alias("tr_sec"),
        _div(pl.col("st_rbc") * 36.0, m).alias("tr_rbc"),
        _div(pl.col("st_unc"), pl.col("st_unc") + pl.col("st_con")).alias("tr_unc"),
        (pl.col("grp") == "G").cast(pl.Float64).alias("tr_posG"),
        (pl.col("grp") == "C").cast(pl.Float64).alias("tr_posC"),
    ]


def g_expr(prop: dict[str, Any]) -> pl.Expr:
    """Relative step-up propensity 1 + sum theta_k z_k (floored at 0.05) from fitted ``mu``, ``sd``, ``theta``."""
    z = [
        (pl.col(t) - float(mu)) / float(sd) * float(th)
        for t, mu, sd, th in zip(TRAITS, prop["mu"], prop["sd"], prop["theta"], strict=True)
    ]
    tot = z[0]
    for e in z[1:]:
        tot = tot + e
    return (1.0 + tot).clip(lower_bound=0.05)


def build_struct(
    rows: pl.DataFrame,
    out_players: pl.DataFrame,
    pool: dict[tuple[str, int], list[int]],
    states: pl.DataFrame,
    dates: pl.DataFrame,
    positions: pl.DataFrame,
    prop: dict[str, dict[str, Any]] | None = None,
) -> pl.DataFrame:
    """STRUCT columns for ``rows`` (game_id, team_id, player_id) from the OUT players, the pool and the states.

    ``out_players``: game_id, team_id, player_id (OUT rotation players, wowy.out_rotation_players).
    ``pool``: wowy.asof_rotation_pool. ``dates``: game_id, game_date. ``positions``: player_id, position.
    Returns one row per input row: struct_d_{pts,reb,ast,fg3m} (RATE), struct_u_{pts,fg3m} (USAGE),
    struct_dmin_role, struct_dmin_pos, leader_out_{pts,reb,ast,fg3m}."""
    rows = rows.with_columns(pl.col("player_id").cast(pl.Int64), pl.col("team_id").cast(pl.Int64))
    out_players = out_players.with_columns(
        pl.col("player_id").cast(pl.Int64), pl.col("team_id").cast(pl.Int64)
    )
    pos = positions.select(
        pl.col("player_id").cast(pl.Int64),
        pl.col("position").cast(pl.Utf8).str.slice(0, 1).fill_null("X").alias("grp"),
    ).unique("player_id")
    outs = _asof(
        out_players.join(dates, on="game_id").select(
            "game_id", "team_id", "player_id", "game_date"
        ),
        states,
    )
    outs = outs.join(pos, on="player_id", how="left").with_columns(pl.col("grp").fill_null("X"))
    # the pool minus the OUT set
    recs = [(g, t, k) for (g, t), ks in pool.items() for k in ks]
    pool_df = pl.DataFrame(
        recs, schema={"game_id": pl.Utf8, "team_id": pl.Int64, "player_id": pl.Int64}, orient="row"
    )
    pool_df = (
        pool_df.join(
            out_players.select("game_id", "team_id", "player_id").with_columns(
                pl.lit(1).alias("_o")
            ),
            on=["game_id", "team_id", "player_id"],
            how="left",
        )
        .filter(pl.col("_o").is_null())
        .drop("_o")
    )
    # only (game, team) with OUT players matter
    teams = outs.select("game_id", "team_id").unique()
    pool_df = pool_df.join(teams, on=["game_id", "team_id"], how="inner").join(dates, on="game_id")
    pool_df = (
        _asof(pool_df, states)
        .join(pos, on="player_id", how="left")
        .with_columns(pl.col("grp").fill_null("X"))
    )
    # V per (game, team)
    v = outs.group_by(["game_id", "team_id"]).agg(
        *[pl.col(f"st_{s}").sum().alias(f"V_{s}") for s in STATS],
        *[pl.col(f"st_{s}").max().alias(f"Vmax_{s}") for s in STATS],
        pl.col("st_minutes").sum().alias("V_min"),
    )
    pool_df = pool_df.with_columns(trait_exprs())
    if prop is not None:
        pool_df = pool_df.with_columns(
            [(pl.col(f"st_{st}") * g_expr(prop[st])).alias(f"wp_{st}") for st in STATS]
        )
    agg = pool_df.group_by(["game_id", "team_id"]).agg(
        *([pl.col(f"wp_{st}").sum().alias(f"PP_{st}") for st in STATS] if prop is not None else []),
        *[pl.col(f"st_{s}").sum().alias(f"P_{s}") for s in STATS],
        _usage().sum().alias("P_use"),
        pl.col("st_minutes").sum().alias("P_min"),
    )
    r = rows.select("game_id", "team_id", "player_id").join(dates, on="game_id")
    r = _asof(r, states)
    pool_keys = pool_df.select("game_id", "team_id", "player_id").with_columns(
        pl.lit(1).alias("in_pool")
    )
    r = (
        r.join(pool_keys, on=["game_id", "team_id", "player_id"], how="left")
        .with_columns(pl.col("in_pool").fill_null(0))
        .join(v, on=["game_id", "team_id"], how="left")
        .join(agg, on=["game_id", "team_id"], how="left")
        .join(pos, on="player_id", how="left")
        .with_columns(pl.col("grp").fill_null("X"))
    )
    r = r.with_columns(trait_exprs())
    if prop is not None:
        r = r.with_columns(
            [(pl.col(f"st_{st}") * g_expr(prop[st])).alias(f"wpi_{st}") for st in STATS]
        )
    nz = pl.col("V_min").is_not_null()
    exprs: list[pl.Expr] = []
    for s in STATS:
        den = pl.col(f"P_{s}").fill_null(0.0) + pl.col(f"st_{s}") * (1 - pl.col("in_pool"))
        exprs.append(
            pl.when(nz & (den > 0))
            .then(pl.col(f"V_{s}") * pl.col(f"st_{s}") / den)
            .otherwise(0.0)
            .alias(f"struct_d_{s}")
        )
        # role share: the player's as-of share of the WHOLE rotation's stat (OUT players counted in the denominator,
        # no allocation of anything): the control that says whether STRUCT is more than "who is a big part of the team"
        rden = den + pl.col(f"V_{s}").fill_null(0.0)
        exprs.append(
            pl.when(nz & (rden > 0))
            .then(pl.col(f"st_{s}") / rden)
            .otherwise(0.0)
            .alias(f"role_share_{s}")
        )
    udn = pl.col("P_use").fill_null(0.0) + _usage() * (1 - pl.col("in_pool"))
    for s in USAGE_STATS:
        exprs.append(
            pl.when(nz & (udn > 0))
            .then(pl.col(f"V_{s}") * _usage() / udn)
            .otherwise(0.0)
            .alias(f"struct_u_{s}")
        )
    if prop is not None:
        for st in STATS:
            pden = pl.col(f"PP_{st}").fill_null(0.0) + pl.col(f"wpi_{st}") * (1 - pl.col("in_pool"))
            exprs.append(
                pl.when(nz & (pden > 0))
                .then(pl.col(f"V_{st}") * pl.col(f"wpi_{st}") / pden)
                .otherwise(0.0)
                .alias(f"prop_d_{st}")
            )
    r = r.with_columns(exprs)
    # minutes: per OUT player j, hand his as-of minutes out pro rata (role) or with weight 2 for the same position group
    pj = outs.select(
        "game_id",
        "team_id",
        pl.col("player_id").alias("j"),
        pl.col("st_minutes").alias("mj"),
        pl.col("grp").alias("gj"),
    )
    dj = pool_df.select("game_id", "team_id", "grp", "st_minutes").join(
        pj, on=["game_id", "team_id"]
    )
    for name, bonus in (("role", 0.0), ("pos", 1.0)):
        w = pl.col("st_minutes") * (1.0 + bonus * (pl.col("grp") == pl.col("gj")).cast(pl.Float64))
        dn = dj.group_by(["game_id", "team_id", "j"]).agg(w.sum().alias("Dj"))
        ri = (
            r.select("game_id", "team_id", "player_id", "grp", "st_minutes", "in_pool")
            .join(pj, on=["game_id", "team_id"])
            .join(dn, on=["game_id", "team_id", "j"], how="left")
        )
        wi = pl.col("st_minutes") * (1.0 + bonus * (pl.col("grp") == pl.col("gj")).cast(pl.Float64))
        den = pl.col("Dj").fill_null(0.0) + wi * (1 - pl.col("in_pool"))
        part = ri.filter(pl.col("player_id") != pl.col("j")).with_columns(
            pl.when(den > 0).then(pl.col("mj") * wi / den).otherwise(0.0).alias("_a")
        )
        tot = part.group_by(["game_id", "team_id", "player_id"]).agg(
            pl.col("_a").sum().alias(f"struct_dmin_{name}")
        )
        r = r.join(tot, on=["game_id", "team_id", "player_id"], how="left").with_columns(
            pl.col(f"struct_dmin_{name}").fill_null(0.0)
        )
    # as-of leader (highest as-of per-game stat among pool and OUT) is OUT
    cand = pl.concat(
        [
            pool_df.select(
                "game_id", "team_id", "player_id", *[f"st_{s}" for s in STATS]
            ).with_columns(pl.lit(0).alias("is_out")),
            outs.select(
                "game_id", "team_id", "player_id", *[f"st_{s}" for s in STATS]
            ).with_columns(pl.lit(1).alias("is_out")),
        ]
    )
    lead = cand.group_by(["game_id", "team_id"]).agg(
        *[
            pl.col("is_out")
            .sort_by(pl.col(f"st_{s}"), descending=True)
            .first()
            .alias(f"leader_out_{s}")
            for s in STATS
        ]
    )
    r = r.join(lead, on=["game_id", "team_id"], how="left").with_columns(
        *[pl.col(f"leader_out_{s}").fill_null(0) for s in STATS]
    )
    keep = [
        "game_id",
        "team_id",
        "player_id",
        "V_min",
        *[f"V_{s}" for s in STATS],
        *[f"Vmax_{s}" for s in STATS],
    ]
    cols = [
        c
        for c in r.columns
        if c.startswith(("struct_", "leader_out_", "prop_d_", "tr_", "role_share_"))
    ]
    return r.select(*keep, *cols)


def attach_struct(feats: pl.DataFrame, st: pl.DataFrame) -> pl.DataFrame:
    """Left-join the STRUCT columns onto the production frame with the ``attach_wowy`` / ``vac_*`` missingness rule:
    a game with a usable report and nobody rotation-OUT gets explicit zeros; a game WITHOUT a usable report stays NULL
    (never imputed as 'nobody out'). ``feats`` needs ``has_report``."""
    cols = [
        c
        for c in st.columns
        if c.startswith(("struct_", "leader_out_", "prop_d_", "role_share_", "V_", "Vmax_"))
    ]
    j = feats.join(
        st.select("game_id", "team_id", "player_id", *cols),
        on=["game_id", "team_id", "player_id"],
        how="left",
    )
    return j.with_columns(
        [
            pl.when(pl.col("has_report") == 1)
            .then(pl.col(c).fill_null(0.0))
            .otherwise(None)
            .alias(c)
            for c in cols
        ]
    )


__all__ = ["STATS", "attach_struct", "build_struct", "decayed_states"]
