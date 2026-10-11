# ruff: noqa: E501
"""INJURY_REASON categoriser and as-of features (docs/prereg/INJURY_REASON.md): parser cases, the
tip - 60 rule, planted-future and planted-same-game tests, return-game definition, placebo."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from nba.features.injury_report import ReportTriggerConfig
from research.features.injury_reason import (
    build_episode_features,
    build_return_features,
    build_snapshot_reasons,
    coverage_report,
    parse_reason,
    shuffle_within,
)

CFG = ReportTriggerConfig(tip_source="real")
TIP = dt.datetime(2024, 1, 10, 19, 30)


@pytest.mark.parametrize(
    ("raw", "cls", "body", "typ", "side"),
    [
        ("Injury/Illness-LeftAnkle;Sprain", "injury", "ankle", "sprain", "left"),
        ("Injury/Illness - Left Ankle; Sprain", "injury", "ankle", "sprain", "left"),
        ("Injury/Illness - Right Knee; Soreness", "injury", "knee", "soreness", "right"),
        ("Injury/Illness-LeftKnee;Surgery", "injury", "knee", "surgery", "left"),
        (
            "Injury/Illness-RightAchillesTendon; Tear",
            "injury",
            "achilles",
            "tear_fracture",
            "right",
        ),
        ("Injury/Illness-LeftKnee;Injury Management", "injury", "knee", "management", "left"),
        ("Injury/Illness-LeftHamstring;Strain", "injury", "hamstring", "strain", "left"),
        ("Injury/Illness-RightCalf;Strain", "injury", "calf", "strain", "right"),
        ("Injury/Illness-LeftAdductor;Strain", "injury", "groin_hip", "strain", "left"),
        ("Injury/Illness-LowBack;Soreness", "injury", "back", "soreness", "none"),
        ("Injury/Illness-RightKnee;Contusion", "injury", "knee", "contusion", "right"),
        ("Injury/Illness-LeftKnee;BoneBruise", "injury", "knee", "contusion", "left"),
        ("Injury/Illness-RightWrist;Sprain", "injury", "shoulder_arm_hand", "sprain", "right"),
        ("Injury/Illness-RightFoot;Fracture", "injury", "foot_toe", "tear_fracture", "right"),
        ("Injury/Illness-BilateralAnkle;Sprain", "injury", "ankle", "sprain", "both"),
        ("Injury/Illness-N/A;Illness", "illness", "none", "illness", "none"),
        ("Injury/Illness-Illness;Illness", "illness", "none", "illness", "none"),
        ("Injury/Illness-N/A;Non-COVID Illness", "illness", "none", "illness", "none"),
        ("Health and Safety Protocols", "illness", "none", "illness", "none"),
        ("G League - Two-Way", "g_league", "none", "none", "none"),
        ("GLeague-OnAssignment", "g_league", "none", "none", "none"),
        ("Rest", "rest_or_management", "none", "management", "none"),
        ("Rest-Rest", "rest_or_management", "none", "management", "none"),
        ("Personal Reasons", "personal", "none", "none", "none"),
        ("League Suspension", "suspension", "none", "none", "none"),
        ("NotWithTeam", "not_with_team", "none", "none", "none"),
        ("Trade Pending", "not_with_team", "none", "none", "none"),
        ("-", "unknown", "none", "none", "none"),
        ("", "unknown", "none", "none", "none"),
        (None, "unknown", "none", "none", "none"),
    ],
)
def test_parse_reason_cases(raw: str | None, cls: str, body: str, typ: str, side: str) -> None:
    got = parse_reason(raw)
    assert (got["cls"], got["body"], got["type"], got["side"]) == (cls, body, typ, side)


def test_stray_prefix_never_changes_class() -> None:
    a = parse_reason("Injury/Illness-RightAnkle;Sprain")
    for prefix in ("Management ", "Recovery ", "Tear ", "Sprain "):
        b = parse_reason(prefix + "Injury/Illness-RightAnkle;Sprain")
        assert b["cls"] == "injury"
        assert (b["body"], b["side"]) == (a["body"], a["side"])
    assert parse_reason("Management GLeague-Two-Way")["cls"] == "g_league"
    assert parse_reason("Contusion Rest")["cls"] == "rest_or_management"


def test_concussion_is_head() -> None:
    p = parse_reason("Concussion Protocol")
    assert (p["cls"], p["body"]) == ("injury", "head_concussion")
    q = parse_reason("Injury/Illness-N/A;Concussion")
    assert q["body"] == "head_concussion"


def test_coverage_report_counts_and_lists() -> None:
    s = pl.Series(["Rest"] * 90 + ["-"] * 9 + ["zzz unknown thing"])
    rep = coverage_report(s, top=5)
    assert rep["n_rows"] == 100
    assert rep["coverage"] == pytest.approx(0.90)
    assert rep["unmatched_top"][0] == ["-", 9]
    assert rep["unmatched_top"][1] == ["zzz unknown thing", 1]


# ------------------------------------------------------------------ as-of snapshot rules


def _games() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g3"],
            "game_date": [dt.date(2024, 1, 10), dt.date(2024, 1, 12), dt.date(2024, 1, 14)],
        }
    )


def _tips() -> pl.DataFrame:
    t = [TIP, TIP + dt.timedelta(days=2), TIP + dt.timedelta(days=4)]
    return pl.DataFrame({"game_id": ["g1", "g2", "g3"], "tipoff_utc": t, "tip_et": t})


def _rows(spec: list[tuple[str, int, str, str, int]]) -> pl.DataFrame:
    """(game, player, status, reason, minutes_before_tip)."""
    tip = {"g1": TIP, "g2": TIP + dt.timedelta(days=2), "g3": TIP + dt.timedelta(days=4)}
    return pl.DataFrame(
        {
            "game_id": [r[0] for r in spec],
            "player_id": [r[1] for r in spec],
            "status": [r[2] for r in spec],
            "reason": [r[3] for r in spec],
            "as_of": [tip[r[0]] - dt.timedelta(minutes=r[4]) for r in spec],
            "source": ["nba_official_report"] * len(spec),
        },
        schema_overrides={"player_id": pl.Int64, "as_of": pl.Datetime("us")},
    )


def test_latest_usable_snapshot_and_cutoff() -> None:
    a = _rows(
        [
            ("g1", 1, "Questionable", "Injury/Illness-LeftKnee;Soreness", 300),
            ("g1", 2, "Out", "Rest", 300),
            (
                "g1",
                1,
                "Probable",
                "Injury/Illness-LeftKnee;Soreness",
                120,
            ),  # latest usable snapshot
            ("g1", 1, "Out", "Injury/Illness-LeftKnee;Surgery", 59),  # after tip - 60: refused
            ("g1", 3, "Out", "Injury/Illness-RightAnkle;Sprain", 30),  # refused
        ]
    )
    s = build_snapshot_reasons(a, _games(), CFG, _tips()).sort("player_id")
    assert s["player_id"].to_list() == [1]  # player 2 was only in an earlier snapshot: not carried
    assert s["type"].to_list() == ["soreness"]
    assert s["status_code"].to_list() == [2]
    # boundary: exactly tip - 60 is usable
    b = _rows([("g1", 5, "Questionable", "Rest", 60)])
    assert build_snapshot_reasons(b, _games(), CFG, _tips()).height == 1


def test_planted_same_game_row_changes_nothing() -> None:
    base = _rows([("g1", 1, "Questionable", "Injury/Illness-LeftKnee;Soreness", 120)])
    planted = pl.concat(
        [
            base,
            _rows(
                [
                    ("g1", 1, "Out", "Injury/Illness-LeftKnee;Surgery", 30),
                    ("g1", 9, "Out", "Rest", 10),
                ]
            ),
        ]
    )
    f = lambda a: build_snapshot_reasons(a, _games(), CFG, _tips()).sort(["game_id", "player_id"])  # noqa: E731
    assert f(base).equals(f(planted))
    # non-inert control: the same row at tip - 90 DOES replace the snapshot
    early = pl.concat([base, _rows([("g1", 1, "Out", "Injury/Illness-LeftKnee;Surgery", 90)])])
    assert not f(base).equals(f(early))


def test_planted_future_changes_nothing_earlier() -> None:
    rows = [
        ("g1", 1, "Questionable", "Injury/Illness-LeftKnee;Soreness", 120),
        ("g2", 1, "Questionable", "Injury/Illness-LeftKnee;Soreness", 120),
        ("g3", 1, "Out", "Injury/Illness-LeftKnee;Surgery", 120),
    ]
    a = _rows(rows)
    altered = a.with_columns(
        pl.when(pl.col("game_id") == "g3")
        .then(pl.lit("Rest"))
        .otherwise(pl.col("reason"))
        .alias("reason")
    )
    s0 = build_snapshot_reasons(a, _games(), CFG, _tips())
    s1 = build_snapshot_reasons(altered, _games(), CFG, _tips())
    e0 = build_episode_features(s0)
    e1 = build_episode_features(s1)
    early = ["g1", "g2"]
    assert (
        s0.filter(pl.col("game_id").is_in(early))
        .sort("game_id")
        .drop("_snap")
        .equals(s1.filter(pl.col("game_id").is_in(early)).sort("game_id").drop("_snap"))
    )
    assert (
        e0.filter(pl.col("game_id").is_in(early))
        .sort("game_id")
        .equals(e1.filter(pl.col("game_id").is_in(early)).sort("game_id"))
    )
    assert not s0.filter(pl.col("game_id") == "g3").equals(s1.filter(pl.col("game_id") == "g3"))


def test_episode_days_since_flag() -> None:
    d = lambda n: dt.date(2024, 1, 1) + dt.timedelta(days=n)  # noqa: E731
    snap = pl.DataFrame(
        {
            "game_id": ["a", "b", "c", "d", "e"],
            "player_id": [1, 1, 1, 1, 1],
            "game_date": [d(0), d(3), d(5), d(40), d(41)],
            "cls": [
                "injury",
                "injury",
                "rest_or_management",
                "rest_or_management",
                "rest_or_management",
            ],
        }
    )
    ep = build_episode_features(snap).sort("game_id")
    # a,b same injury episode (3 days); c changes class (new episode, 0); d is >14 days later (0); e is d + 1
    assert ep["days_since_flag"].to_list() == [0.0, 3.0, 0.0, 0.0, 1.0]


# ------------------------------------------------------------------ return features


def _played() -> pl.DataFrame:
    # player 1: team games 1,2,3 played; misses 4,5,6,7 (4 games); plays 8, 9, 10, 11; season stays
    # player 2: plays every game 1..6 (no return)
    rows = []
    for g in (1, 2, 3, 8, 9, 10, 11):
        rows.append((f"g{g:02d}", 1, dt.date(2024, 1, 1) + dt.timedelta(days=g), 2023, g))
    for g in range(1, 7):
        rows.append((f"g{g:02d}", 2, dt.date(2024, 1, 1) + dt.timedelta(days=g), 2023, g))
    return pl.DataFrame(
        rows,
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "game_date": pl.Date,
            "season": pl.Int64,
            "team_game_no": pl.Int64,
        },
        orient="row",
    )


def _snap_for_return() -> pl.DataFrame:
    def r(g: int, cls: str, body: str, typ: str) -> dict[str, object]:
        return {
            "game_id": f"g{g:02d}",
            "player_id": 1,
            "game_date": dt.date(2024, 1, 1) + dt.timedelta(days=g),
            "cls": cls,
            "body": body,
            "type": typ,
        }

    return pl.DataFrame(
        [
            r(4, "injury", "knee", "soreness"),
            r(5, "injury", "knee", "soreness"),
            r(6, "injury", "knee", "surgery"),
            r(7, "rest_or_management", "none", "management"),
            r(8, "injury", "knee", "management"),  # tonight's own T-60 row on the return game
            r(11, "illness", "none", "illness"),  # AFTER the spell: must never be used
        ]
    )


def test_return_features_definition_and_spell() -> None:
    out = build_return_features(_played(), _snap_for_return()).sort(["player_id", "game_id"])
    p1 = out.filter(pl.col("player_id") == 1)
    assert p1["game_back"].to_list() == [0, 0, 0, 1, 2, 3, 0]
    ret = p1.filter(pl.col("game_back") >= 1)
    assert ret["spell_missed"].to_list() == [4.0, 4.0, 4.0]
    # last listing in (game 3, game 8] is tonight's own row: knee / management
    assert ret["spell_type"].to_list() == ["management"] * 3
    assert ret["spell_found"].to_list() == [1, 1, 1]
    # listed games with the final class strictly between: games 4,5,6 are injury -> 3
    assert ret["spell_listed_games"].to_list() == [3.0, 3.0, 3.0]
    assert out.filter(pl.col("player_id") == 2)["game_back"].to_list() == [0] * 6


def test_return_features_ignore_data_after_the_game() -> None:
    full = build_return_features(_played(), _snap_for_return()).sort(["player_id", "game_id"])
    cut_p = _played().filter(pl.col("team_game_no") <= 9)
    cut_s = _snap_for_return().filter(pl.col("game_id") <= "g09")
    cut = build_return_features(cut_p, cut_s).sort(["player_id", "game_id"])
    j = cut.join(full, on=["game_id", "player_id"], suffix="_f")
    for c in (
        "game_back",
        "spell_missed",
        "spell_listed_games",
        "spell_cls",
        "spell_body",
        "spell_type",
        "spell_found",
    ):
        assert j[c].equals(j[f"{c}_f"]), c


def test_no_listing_is_unknown_not_a_guess() -> None:
    out = build_return_features(_played(), _snap_for_return().filter(pl.col("game_id") > "g09"))
    r = out.filter((pl.col("player_id") == 1) & (pl.col("game_back") == 1))
    assert r["spell_cls"].to_list() == ["unknown"]
    assert r["spell_found"].to_list() == [0]


def test_shuffle_within_keeps_multiset_keys_and_group() -> None:
    f = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(12)],
            "player_id": list(range(12)),
            "game_date": [dt.date(2024, 1, 1 + i // 4) for i in range(12)],
            "t": list(range(12)),
            "u": [10 * i for i in range(12)],
        }
    )
    s = shuffle_within(f, ["t", "u"], ["game_date"], seed=0)
    assert s["game_id"].to_list() == f["game_id"].to_list()
    for d in f["game_date"].unique().to_list():
        a = f.filter(pl.col("game_date") == d)
        b = s.filter(pl.col("game_date") == d)
        assert sorted(a["t"].to_list()) == sorted(b["t"].to_list())
        assert b["u"].to_list() == [10 * t for t in b["t"].to_list()]  # columns move jointly
    assert s["t"].to_list() != f["t"].to_list()
    assert s.equals(shuffle_within(f, ["t", "u"], ["game_date"], seed=0))
