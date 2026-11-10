"""Knowledge as the cards show it: lineup rows repeat across snapshots, reasons are shortened,
ages use the card's own clock. Shapes taken from the real lineup store (210 rows per team)."""

from __future__ import annotations

from datetime import datetime

from nba.jarvis.cards import facts as F
from nba.jarvis.cards import text as T
from nba.jarvis.cards.model import OverRow
from nba.jarvis.models import Game


def rows(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"player": n, "pos": "G", "status": s} for n, s in pairs]


def test_repeated_snapshot_rows_collapse_to_five_confirmed_names() -> None:
    raw = {
        "DAL": rows(
            ("Daniel Gafford", "Confirmed"),
            ("Caleb Martin", "Expected"),
            ("Cooper Flagg", "Expected"),
            ("Cooper Flagg", "Confirmed"),
            ("Kyrie Irving", "Expected"),
            ("Kyrie Irving", "Confirmed"),
            ("Caleb Martin", "Confirmed"),
            ("P.J. Washington", "Confirmed"),
            ("Cooper Flagg", "Confirmed"),
            ("Daniel Gafford", "Confirmed"),
        )  # fmt: skip
    }
    names, notes = F._starters(raw)
    assert names["DAL"] == ["Daniel Gafford", "Cooper Flagg", "Kyrie Irving", "Caleb Martin",
                            "P.J. Washington"]  # fmt: skip
    assert notes["DAL"] == "confirmed"


def test_fewer_more_or_none_confirmed_is_said_not_guessed() -> None:
    few, n_few = F._starters({"A": rows(("p1", "Confirmed"), ("p2", "Expected"))})
    assert few["A"] == ["p1"] and "not all five" in n_few["A"]
    many, n_many = F._starters({"A": rows(*[(f"p{i}", "Confirmed") for i in range(7)])})
    assert len(many["A"]) == 5 and "7 names marked confirmed" in n_many["A"]
    none, n_none = F._starters({"A": rows(*[(f"p{i}", "Expected") for i in range(8)])})
    assert len(none["A"]) == 5 and "expected, not confirmed yet" in n_none["A"]


def test_the_starters_line_says_the_state_of_each_team() -> None:
    from nba.jarvis.cards.model import CardData, Knows

    k = Knows(
        starters={"DAL": ["a", "b", "c", "d", "e"], "IND": ["x"]},
        starters_note={"DAL": "confirmed", "IND": "expected, not confirmed yet"},
        lineup_age_min=4.0,
    )
    c = CardData("G", "IND", "DAL", None, "pretip", datetime(2026, 1, 1), knows=k)
    _, v = T.knows_field(c, T.Style(), "IND")
    assert "IND: x (expected, not confirmed yet); DAL: a, b, c, d, e (confirmed)." in v
    assert "Lineups read 4 min ago." in v


def test_reasons_are_the_reports_words_in_short_form() -> None:
    assert F.short_reason("Injury/Illness-RightFingerSprain") == "right finger sprain"
    assert F.short_reason("Injury/Illness-Illness") == "illness"
    assert F.short_reason("") == "" and F.short_reason(None) == ""
    assert len(F.short_reason("x" * 99)) == 28


def test_long_injury_lists_are_capped_with_a_count() -> None:
    capped = F._cap({"LAL": [f"p{i}" for i in range(9)], "LAC": ["a"]})
    assert capped["LAL"] == ["p0", "p1", "p2", "p3", "p4", "+4 more"] and capped["LAC"] == ["a"]


class KB:
    def injuries(self, gid: str) -> dict[str, object]:
        return {
            "report_as_of_utc": "2026-11-09 20:00:00",
            "teams": {
                "LAL": [
                    {"player": "A", "status": "out", "reason": "Injury/Illness-Illness", "mpg": 30},
                    {"player": "B", "status": "questionable", "reason": "Rest", "mpg": 28},
                    {"player": "Bench", "status": "out", "reason": "x", "mpg": 5},  # not rotation
                    {"player": "C", "status": "probable", "reason": "", "mpg": 25},  # not news
                ]
            },
        }

    def lineups(self, gid: str) -> dict[str, object]:
        return {"starters": {}, "snapshot_utc": "2026-11-09 21:30:00"}


def test_knowledge_ages_come_from_the_card_clock_not_the_wall_clock() -> None:
    g = Game("G", 1, 2, "LAL", "LAC", datetime(2026, 11, 10, 0, 0))
    k = F.game_knowledge(KB(), g, datetime(2026, 11, 9, 22, 0))
    assert k.report_age_min == 120.0 and k.lineup_age_min == 30.0
    assert k.out == {"LAL": ["A (illness)"]} and k.gtd == {"LAL": ["B (questionable)"]}

    # an unreadable store is a note, never an error
    class Broken:
        def injuries(self, gid: str) -> dict[str, object]:
            raise RuntimeError("locked")

        def lineups(self, gid: str) -> dict[str, object]:
            return {"error": "x"}

    kb = F.game_knowledge(Broken(), g, datetime(2026, 11, 9, 22, 0))
    assert "injury report not readable" in kb.note and "lineups not readable" in kb.note


def test_a_model_below_the_price_is_said_plainly() -> None:
    o = OverRow(
        "p", "A B", "pts", 15.0, 0.43, 2.0, 0.465, 0.465, 0.36, 1.0, 1.0, "thin", "T", "kalshi", 1
    )
    assert "That is below the 43c price." in T.confidence_words(o, T.Style())
    ok = OverRow(
        "p", "A B", "pts", 15.0, 0.43, 2.0, 0.465, 0.465, 0.50, 1.0, 1.0, "thin", "T", "kalshi", 1
    )
    assert "That is below" not in T.confidence_words(ok, T.Style())
