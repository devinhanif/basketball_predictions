# ruff: noqa: E501
"""INJURY_REASON features (docs/prereg/INJURY_REASON.md; DRAFT until frozen).

Two layers, both deterministic, regex-only, no learning:

1. :func:`parse_reason` turns the report's free-text ``reason`` into
   (class, body-part group, type, side). The raw strings come in two spellings
   ("Injury/Illness-LeftAnkle;Sprain" and "Injury/Illness - Left Ankle; Sprain") and sometimes
   carry a stray prefix from a neighbouring field ("Management Injury/Illness-RightAnkle;Sprain");
   whitespace is removed and the first marker found by priority decides the class, so a stray
   prefix never changes it.
2. :func:`build_snapshot_reasons` / :func:`build_episode_features` / :func:`build_return_features`
   attach per-(game, player) as-of features. Known-at = ``as_of``: a report row is used for a game
   only if ``as_of <= tip - 60`` on the real tip (``nba.features.injury_report.usable_report_rows``
   is the one place that rule lives; ``tip_source="real"``), and only rows of the game's LATEST
   usable snapshot count (the OWN_TAG convention). Nothing from tonight's box score enters a
   feature; ``game_back`` and the spell are defined on PRIOR appearances only.

Research module: ``nba/`` untouched.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

import numpy as np
import polars as pl

from nba.features.game_tipoff import attach_real_tips
from nba.features.injury_report import ReportTriggerConfig, usable_report_rows

CLASSES: tuple[str, ...] = (
    "injury",
    "illness",
    "rest_or_management",
    "g_league",
    "personal",
    "suspension",
    "not_with_team",
    "unknown",
)
BODY_GROUPS: tuple[str, ...] = (
    "ankle",
    "knee",
    "hamstring",
    "calf",
    "groin_hip",
    "back",
    "shoulder_arm_hand",
    "foot_toe",
    "head_concussion",
    "achilles",
    "other",
    "none",
)
TYPES: tuple[str, ...] = (
    "sprain",
    "strain",
    "soreness",
    "contusion",
    "surgery",
    "tear_fracture",
    "management",
    "illness",
    "other",
    "none",
)
SIDES: tuple[str, ...] = ("left", "right", "both", "none")
STATUS_CODE: dict[str, int] = {
    "available": 1,
    "probable": 2,
    "questionable": 3,
    "doubtful": 4,
    "out": 5,
}
KEYS = ["game_id", "player_id"]
EPISODE_GAP_DAYS = (
    14  # a gap of more than this many calendar days between listings starts a new episode
)
DAYS_CAP = 365.0
RETURN_MIN_MISSED = 3  # team games missed before a "return" (matches production returning_3plus)
RETURN_MAX_BACK = 3  # 1st/2nd/3rd game back
SPELL_CAP = 60.0

CLASS_CODE = {c: i for i, c in enumerate(CLASSES)}
BODY_CODE = {c: i for i, c in enumerate(BODY_GROUPS)}
TYPE_CODE = {c: i for i, c in enumerate(TYPES)}
SIDE_CODE = {c: i for i, c in enumerate(SIDES)}

# --------------------------------------------------------------------------- the parser


def _squash(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "").lower()


# class markers, checked IN THIS ORDER on the squashed string (a stray prefix like "management" is
# deliberately not a marker on its own: only whole phrases are)
_CLASS_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("g_league", re.compile(r"gleague")),
    ("injury", re.compile(r"injury/illness|concussion")),
    ("not_with_team", re.compile(r"notwithteam|tradepending|returntocompetition")),
    ("suspension", re.compile(r"suspension|ineligible")),
    ("personal", re.compile(r"personal")),
    ("rest_or_management", re.compile(r"^rest|rest$|coach'?sdecision|loadmanagement|^management$")),
    ("illness", re.compile(r"healthandsafety|covid|illness")),
)

# body part, first match wins (patterns on the squashed body token with the side stripped)
_BODY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("achilles", re.compile(r"achilles")),
    (
        "head_concussion",
        re.compile(
            r"concuss|head|face|facial|nose|nasal|eye|orbital|jaw|ear|mouth|teeth|tooth|skull"
        ),
    ),
    ("ankle", re.compile(r"ankle")),
    ("hamstring", re.compile(r"hamstring")),
    ("calf", re.compile(r"calf|soleus|gastroc")),
    ("knee", re.compile(r"knee|acl|mcl|pcl|patell|menisc|tibialplateau|femoralcondyle|lcl")),
    ("groin_hip", re.compile(r"groin|adductor|hip|pelvi|iliac|glute|sacro|pubic")),
    (
        "back",
        re.compile(
            r"back|lumbar|spine|spinal|disc|cervical|neck|thoracic|facet|tailbone|coccyx|sciatic"
        ),
    ),
    (
        "foot_toe",
        re.compile(
            r"foot|toe|heel|plantar|metatars|lisfranc|midfoot|mid-foot|sesamoid|navicular|arch"
        ),
    ),
    (
        "shoulder_arm_hand",
        re.compile(
            r"shoulder|arm|elbow|wrist|hand|finger|thumb|bicep|tricep|ulnar|scaphoid|metacarp|mallet|acjoint|clavicle|pectoral|rotator|labrum|lat(?:eral)?$"
        ),
    ),
)
_BODY_NONE = re.compile(r"^(?:illness|n/?a|non-?covid|general|--?|)$")

# type, first match wins, most specific first
_TYPE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "surgery",
        re.compile(r"surg|repair|recover|rehab|procedure|reconstruct|reconditioning|postop"),
    ),
    (
        "tear_fracture",
        re.compile(r"tear|torn|fractur|broken|rupture|avulsion|dislocat|subluxat|break"),
    ),
    ("sprain", re.compile(r"sprain")),
    ("strain", re.compile(r"strain")),
    ("management", re.compile(r"manage|maintenance|rest")),
    ("contusion", re.compile(r"contus|bruise|laceration|abrasion|cut")),
    ("illness", re.compile(r"illness|covid|virus|flu|infection|sick|stomach|health")),
    (
        "soreness",
        re.compile(
            r"sore|tight|spasm|discomfort|pain|irritat|inflam|tendin|tendon|bursitis|effusion|impinge|stress|fasciitis|swelling|cramp|stiff"
        ),
    ),
)


def parse_reason(raw: str | None) -> dict[str, str]:
    """Reason string -> ``{cls, body, type, side, matched}``. ``matched`` is "1" when the class is
    not unknown, "0" otherwise (string-typed so the dict is homogeneous)."""
    s = _squash(raw)
    out = {"cls": "unknown", "body": "none", "type": "none", "side": "none", "matched": "0"}
    if s in ("", "-", "--", "n/a", "na"):
        return out
    cls = "unknown"
    for name, pat in _CLASS_RULES:
        if pat.search(s):
            cls = name
            break
    if cls == "unknown":
        return out
    out["cls"] = cls
    out["matched"] = "1"
    if cls == "illness" and "injury/illness" not in s:
        out.update({"body": "none", "type": "illness"})
    elif cls in ("injury", "illness"):
        # tail of the string after the first "injury/illness" marker; the free-text prefix is dropped
        tail = s.split("injury/illness", 1)[1] if "injury/illness" in s else s
        tail = tail.lstrip("-")
        body_tok, _, type_tok = tail.partition(";")
        if "concussion" in s and "injury/illness" not in s:
            body_tok, type_tok = "concussion", "concussion"
        side = "none"
        m = re.match(r"(left|right|bilateral)(.*)", body_tok)
        if m:
            side = {"left": "left", "right": "right", "bilateral": "both"}[m.group(1)]
            body_tok = m.group(2)
        out["side"] = side
        body = "other"
        if _BODY_NONE.match(body_tok):
            body = "none"
        else:
            for name, pat in _BODY_RULES:
                if pat.search(body_tok):
                    body = name
                    break
        typ = "other"
        for name, pat in _TYPE_RULES:
            if pat.search(type_tok):
                typ = name
                break
        if (
            cls == "illness"
            or body_tok in ("illness",)
            or type_tok == "illness"
            or "non-covid" in s
        ):
            cls, body, typ = "illness", "none", "illness"
        elif type_tok in ("concussion",) or "concussion" in body_tok:
            body, typ = "head_concussion", "other" if typ == "other" else typ
        if not type_tok.strip("-") and not body_tok and cls == "injury":
            typ = "none"
        out.update({"cls": cls, "body": body, "type": typ})
    elif cls == "rest_or_management":
        out["type"] = "management"
        if "gleague" not in s and "injury" in s:
            out["cls"] = "rest_or_management"
    return out


def parse_table(reasons: Iterable[str | None]) -> pl.DataFrame:
    """One row per distinct reason string with its parse (deterministic order)."""
    uniq = sorted({r for r in reasons if r is not None}, key=str)
    rows = [{"reason": r, **{k: v for k, v in parse_reason(r).items()}} for r in uniq]
    return pl.DataFrame(
        rows,
        schema={
            "reason": pl.Utf8,
            "cls": pl.Utf8,
            "body": pl.Utf8,
            "type": pl.Utf8,
            "side": pl.Utf8,
            "matched": pl.Utf8,
        },
    )


def coverage_report(reasons: pl.Series, top: int = 50, include_null: bool = True) -> dict[str, Any]:
    """Share of rows mapped to a non-unknown class, the top unmatched strings by count, and the
    injury strings whose body part or type fell to ``other`` (diagnostic, not the gate)."""
    counts = Counter(reasons.to_list())
    n = sum(counts.values())
    parsed = {r: parse_reason(r) for r in counts}
    matched = sum(c for r, c in counts.items() if parsed[r]["matched"] == "1")
    unmatched = sorted(
        ((r, c) for r, c in counts.items() if parsed[r]["matched"] == "0"),
        key=lambda t: (-t[1], str(t[0])),
    )
    body_other: Counter[str] = Counter()
    type_other: Counter[str] = Counter()
    for r, c in counts.items():
        p = parsed[r]
        if p["cls"] == "injury":
            if p["body"] == "other":
                body_other[r] += c
            if p["type"] == "other":
                type_other[r] += c
    by_class: Counter[str] = Counter()
    for r, c in counts.items():
        by_class[parsed[r]["cls"]] += c
    return {
        "n_rows": n,
        "n_distinct": len(counts),
        "coverage": matched / n if n else float("nan"),
        "by_class": dict(by_class),
        "unmatched_top": [[str(r), int(c)] for r, c in unmatched[:top]],
        "unmatched_rows": int(sum(c for _, c in unmatched)),
        "injury_body_other_top": [[r, int(c)] for r, c in body_other.most_common(25)],
        "injury_type_other_top": [[r, int(c)] for r, c in type_other.most_common(25)],
        "injury_rows": int(sum(c for r, c in counts.items() if parsed[r]["cls"] == "injury")),
        "injury_body_other_rows": int(sum(body_other.values())),
        "injury_type_other_rows": int(sum(type_other.values())),
    }


# --------------------------------------------------------------------------- snapshot rows


def build_snapshot_reasons(
    avail: pl.DataFrame,
    games: pl.DataFrame,
    cfg: ReportTriggerConfig,
    tips: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Latest usable (tip - 60, real tip) snapshot rows with the parsed reason.

    ``avail``: game_id, player_id, status, reason, as_of, source. One row per (game, player) (the
    highest status code in the snapshot; ties by reason text). Columns: game_id, player_id,
    game_date, status_code, cls, body, type, side (codes are added by :func:`code_columns`).
    """
    if cfg.tip_source != "real":
        raise ValueError("INJURY_REASON uses real tips only")
    rows = (
        avail.filter(pl.col("source").is_in(list(cfg.sources)) & pl.col("player_id").is_not_null())
        .join(games.select(["game_id", "game_date"]), on="game_id")
        .select(
            "game_id",
            pl.col("player_id").cast(pl.Int64),
            pl.col("status").str.to_lowercase().alias("status"),
            "reason",
            pl.col("as_of").cast(pl.Datetime("us")),
            pl.col("game_date").cast(pl.Date),
        )
    )
    rows = attach_real_tips(rows, tips)
    usable = usable_report_rows(rows, cfg)
    latest = usable.group_by("game_id").agg(pl.col("as_of").max().alias("_snap"))
    snap = usable.join(latest, on="game_id").filter(pl.col("as_of") == pl.col("_snap"))
    ptab = parse_table(snap["reason"].to_list())
    snap = (
        snap.join(ptab, on="reason", how="left")
        .with_columns(
            pl.col("status")
            .replace_strict(STATUS_CODE, default=0, return_dtype=pl.Int64)
            .alias("status_code"),
            pl.col("cls").fill_null("unknown"),
            pl.col("body").fill_null("none"),
            pl.col("type").fill_null("none"),
            pl.col("side").fill_null("none"),
        )
        .sort(
            ["game_id", "player_id", "status_code", "reason"],
            descending=[False, False, True, False],
        )
        .unique(subset=KEYS, keep="first", maintain_order=True)
    )
    return snap.select(
        "game_id", "player_id", "game_date", "status_code", "cls", "body", "type", "side", "_snap"
    )


def code_columns(
    df: pl.DataFrame, prefix: str, cols: tuple[str, ...] = ("cls", "body", "type", "side")
) -> pl.DataFrame:
    """Integer codes (LightGBM / one-hot friendly) for the string columns named ``prefix + col``."""
    tabs = {"cls": CLASS_CODE, "body": BODY_CODE, "type": TYPE_CODE, "side": SIDE_CODE}
    out = df
    for c in cols:
        name = f"{prefix}{c}"
        if name in out.columns:
            out = out.with_columns(
                pl.col(name)
                .replace_strict(tabs[c], default=None, return_dtype=pl.Int64)
                .alias(f"{name}_code")
            )
    return out


# --------------------------------------------------------------------------- episode features


def build_episode_features(snap: pl.DataFrame) -> pl.DataFrame:
    """Per listed (game, player): ``days_since_flag`` = calendar days from the first listing of the
    player's current episode with the same reason class to tonight's date. An episode continues
    while consecutive listings (any status) share the class and are at most ``EPISODE_GAP_DAYS``
    apart. Uses listings of earlier games and tonight's own T-60 snapshot row only. Capped at
    ``DAYS_CAP``. Unlisted rows have no value (they are not in this frame)."""
    s = snap.select("game_id", "player_id", "game_date", "cls").sort(
        ["player_id", "game_date", "game_id"]
    )
    pid = s["player_id"].to_numpy()
    day = s["game_date"].cast(pl.Int32).to_numpy().astype(float)
    cls = s["cls"].to_list()
    n = s.height
    start = np.zeros(n)
    for i in range(n):
        if (
            i > 0
            and pid[i] == pid[i - 1]
            and cls[i] == cls[i - 1]
            and day[i] - day[i - 1] <= EPISODE_GAP_DAYS
        ):
            start[i] = start[i - 1]
        else:
            start[i] = day[i]
    return s.select(KEYS).with_columns(
        pl.Series("days_since_flag", np.minimum(day - start, DAYS_CAP))
    )


# --------------------------------------------------------------------------- return features

RETURN_COLS: tuple[str, ...] = (
    "game_back",
    "spell_missed",
    "spell_listed_games",
    "spell_cls",
    "spell_body",
    "spell_type",
    "spell_found",
)


def build_return_features(played: pl.DataFrame, snap: pl.DataFrame) -> pl.DataFrame:
    """Per PLAYED row (rows of ``played``: game_id, player_id, game_date, season, team_game_no):

    * ``game_back`` 0 / 1 / 2 / 3: 1 = the row's player missed >= ``RETURN_MIN_MISSED`` team games
      since his previous appearance (same season; production ``returning_3plus``); 2 / 3 = the next
      consecutive appearances with no further such absence. Defined from PRIOR appearances only.
    * ``spell_missed``: team games missed in the spell just ended (carried onto games 2 and 3).
    * ``spell_listed_games``: of those, games on which the player was listed with the spell's class.
    * ``spell_cls`` / ``spell_body`` / ``spell_type``: the LAST listing in (previous appearance,
      tonight] - tonight's own T-60 snapshot row included, earlier games' latest snapshots - or
      class ``unknown`` and ``spell_found`` 0 when he was never listed in that window.
    Non-return rows get game_back 0, spell_missed 0, spell_listed_games 0, none/none/none, found 0.
    """
    p = (
        played.select("game_id", "player_id", "game_date", "season", "team_game_no")
        .with_columns(pl.col("player_id").cast(pl.Int64))
        .sort(["player_id", "game_date", "game_id"])
    )
    n = p.height
    pid = p["player_id"].to_numpy()
    seas = p["season"].to_numpy()
    tgn = p["team_game_no"].to_numpy().astype(float)
    dates = p["game_date"].cast(pl.Int32).to_numpy()
    gb = np.zeros(n, dtype=np.int64)
    missed = np.zeros(n)
    prev_idx = np.full(n, -1, dtype=np.int64)
    for i in range(n):
        if i > 0 and pid[i] == pid[i - 1] and seas[i] == seas[i - 1]:
            m = tgn[i] - tgn[i - 1] - 1
            if m >= RETURN_MIN_MISSED:
                gb[i] = 1
                missed[i] = m
                prev_idx[i] = i - 1
            elif gb[i - 1] in (1, 2) and m < RETURN_MIN_MISSED:
                gb[i] = gb[i - 1] + 1
                missed[i] = missed[i - 1]
                prev_idx[i] = prev_idx[i - 1]
    # listings per player, chronological
    ls = snap.select(
        pl.col("player_id").cast(pl.Int64), "game_date", "game_id", "cls", "body", "type"
    ).sort(["player_id", "game_date", "game_id"])
    lp = ls["player_id"].to_numpy()
    ld = ls["game_date"].cast(pl.Int32).to_numpy()
    lc, lb, lt = ls["cls"].to_list(), ls["body"].to_list(), ls["type"].to_list()
    bounds: dict[int, tuple[int, int]] = {}
    for i in range(ls.height):
        a, _ = bounds.get(int(lp[i]), (i, i))
        bounds[int(lp[i])] = (a, i + 1)
    sp_cls = ["unknown"] * n
    sp_body = ["none"] * n
    sp_type = ["none"] * n
    found = np.zeros(n, dtype=np.int64)
    listed = np.zeros(n)
    # the spell's reason is fixed on the first-game-back row (window: previous appearance, tonight]
    anchor: dict[int, int] = {}
    for i in range(n):
        if gb[i] == 0:
            continue
        j = i if gb[i] == 1 else anchor[i - 1]
        anchor[i] = j
        if gb[i] == 1:
            lo_date = dates[prev_idx[i]]
            hi_date = dates[i]
            a, b = bounds.get(int(pid[i]), (0, 0))
            last = -1
            for k in range(a, b):
                if lo_date < ld[k] <= hi_date:
                    last = k
            if last >= 0:
                sp_cls[i], sp_body[i], sp_type[i] = lc[last], lb[last], lt[last]
                found[i] = 1
                listed[i] = sum(
                    1 for k in range(a, b) if lo_date < ld[k] < hi_date and lc[k] == lc[last]
                )
        else:
            sp_cls[i], sp_body[i], sp_type[i] = sp_cls[j], sp_body[j], sp_type[j]
            found[i], listed[i] = found[j], listed[j]
    return p.select(KEYS).with_columns(
        pl.Series("game_back", gb),
        pl.Series("spell_missed", np.minimum(missed, SPELL_CAP)),
        pl.Series("spell_listed_games", np.minimum(listed, SPELL_CAP)),
        pl.Series("spell_cls", sp_cls),
        pl.Series("spell_body", sp_body),
        pl.Series("spell_type", sp_type),
        pl.Series("spell_found", found),
    )


# --------------------------------------------------------------------------- placebo


def shuffle_within(
    frame: pl.DataFrame, cols: list[str], by: list[str], seed: int = 0
) -> pl.DataFrame:
    """Jointly permute ``cols`` (one permutation per row) within groups of ``by``; keys and the
    other columns stay. The placebo for 'the categories carry nothing beyond date (and status)'."""
    rng = np.random.default_rng(seed)
    d = frame.with_row_index("_row").with_columns(pl.Series("_r", rng.random(frame.height)))
    base = d.sort([*by, "_row"])
    shuf = d.sort([*by, "_r", "_row"])
    perm = base.select("_row")
    for c in cols:
        perm = perm.with_columns(shuf[c].alias(c))
    perm = perm.sort("_row")
    return frame.with_columns([perm[c].alias(c) for c in cols])
