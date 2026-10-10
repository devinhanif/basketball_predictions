"""Play-by-play tokenizer for the decoder-only "basketball GPT" (as-of safe, streamed export).

One token sequence per game::

    HEADER (pre-tip only, loss-masked)   BOS, home/away team, [playoff], injury-Elo bucket, trailing
                                         margin buckets, rest buckets, pre-tip OUTs, starting
                                         lineups,
                                         as-of rotation roster, GO
    STATE BLOCK (forced, loss-masked)    M_STATE, score-diff bucket, period, clock bucket, bonus
                                         [+ M_LINEUP + home five + away five]
    EVENT RECORDS (learned)              DT  EVT  payload...   (payload by event kind, see below)
    ... a state block is inserted after any event that crosses a 120 s clock-grid line, and a state
    block with a lineup refresh opens every period. PERIOD_END is a learned event; after it the
    stream is forced: a new-period block, or EOS (period >= 4 and not tied).

Event kinds (``EVT_*`` ids; one token carries side + kind + zone + assisted flag)::

    {H,A}_MISS_{rim,mid,three}            payload ACTOR
    {H,A}_MAKE_{zone}_U                   payload ACTOR            (unassisted)
    {H,A}_MAKE_{zone}_A                   payload ACTOR ASSIST
    {H,A}_FT_MAKE / FT_MISS               payload ACTOR
    {H,A}_REB_O / REB_D                   payload ACTOR ; *_TEAM no payload
    {H,A}_TOV                             payload ACTOR ; TOV_TEAM no payload
    {H,A}_FOUL_D / FOUL_O / FOUL_X        payload ACTOR (defensive / offensive / technical-other)
    {H,A}_SUB                             payload OUT IN
    {H,A}_TIMEOUT                         no payload
    PERIOD_END                            no payload

Time: ``DT_k`` is a 16-bucket time delta. The bucket representative values accumulate into a token
clock (``tclock``) that is kept within one bucket of the true clock by error diffusion (the chosen
bucket minimises ``|max(tclock - rep, 0) - true_clock|``), so a sampler that only sees tokens
reproduces the same clock process the model was trained on.

Players: ``P_<id>`` for every player with >= ``MIN_APPEARANCES`` games in the TRAIN split; everyone
else is ``NEW_<k>`` where k is the player's rank among the game's unknown players sorted by id
(a unique, reversible slot inside a game; the embedding is shared = the cold-start token).

As-of discipline (mirrors ``nba/features/player_possession_features.py``): the header uses ONLY
(a) the game's own starters flag (lineups are announced before tip), (b) the pre-tip injury report
rows that ``pretip_status`` admits (reports stamped after tip-off minus the lead are ignored),
(c) the injury-Elo out-of-fold probability, and (d) quantities from STRICTLY EARLIER games (roster =
last ten team games' minutes, trailing margin, rest, player averages). Nothing from the game's own
box score or play-by-play enters the header; ``AsOfHistory`` is updated AFTER a game is tokenized.

Splits: train = season 2022 + the first 80% (by date) of season 2023; val = last 20% of 2023;
report = season 2024. Season 2025 is refused.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.parse.lineups import _clock_to_seconds, _normalize_name
from nba.parse.possessions import _build_name_lookup as _assist_name_lookup
from nba.parse.possessions import _parse_assist, _shot_zone


def _lineup_name_lookup(
    pbp: pl.DataFrame,
) -> tuple[dict[tuple[int, str], int | None], dict[tuple[int, str], int | None]]:
    """(team_id, name) -> player_id, exact and diacritic/suffix-normalised; None if ambiguous.

    This was the v1 tracker's substitution resolver. The v2 tracker
    (``nba.parse.lineups._NameBook``) replaced it; the copy lives here so this
    research module keeps its frozen behaviour without reaching into the parser.
    """
    pairs = (
        pbp.filter((pl.col("player_id") != 0) & (pl.col("player_name") != ""))
        .select(["team_id", "player_name", "player_id"])
        .unique()
    )
    exact: dict[tuple[int, str], int | None] = {}
    normalized: dict[tuple[int, str], int | None] = {}
    for team_id, name, player_id in pairs.iter_rows():
        key = (team_id, name)
        exact[key] = None if key in exact else player_id
        norm_key = (team_id, _normalize_name(name))
        normalized[norm_key] = None if norm_key in normalized else player_id
    return exact, normalized


def _resolve_name(
    team_id: int,
    name: str,
    exact: dict[tuple[int, str], int | None],
    normalized: dict[tuple[int, str], int | None],
) -> int | None:
    resolved = exact.get((team_id, name))
    if resolved is not None:
        return resolved
    return normalized.get((team_id, _normalize_name(name)))


SEED = 20261008
FROZEN_SEASON = 2025
EXPORT_SEASONS: tuple[int, ...] = (2022, 2023, 2024)
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SLICE_FRAC = 0.2
CTX = 2304  # max tokens per game fed to the model
MIN_APPEARANCES = 10

DT_REP: tuple[int, ...] = (0, 1, 2, 3, 5, 7, 9, 12, 15, 19, 24, 30, 40, 55, 80, 120)
BLOCK_SECONDS = 120
LINEUP_EVERY = 3  # a lineup refresh in every 3rd in-period block (and at every period start)
CLK_BIN_SECONDS = 30
N_ELO, N_RAT, N_REST = 24, 12, 4
N_SC, N_PER, N_CLK, N_BON, N_NEW = 47, 6, 24, 4, 16
RM = 15  # roster slots per team (5 starters + up to 10 rotation players)
MAX_OUT = 6
N_CKPT = 5
CKPT_NAMES = ("tip", "q2", "q3", "q4", "q4_5min")

# slot (role) codes stored per token; 0 = header / forced (excluded from the loss)
SL_NONE, SL_DT, SL_EVT, SL_ACTOR, SL_ASSIST, SL_OUT, SL_IN = 0, 1, 2, 3, 4, 5, 6
SLOT_NAMES = {1: "dt", 2: "evt", 3: "actor", 4: "assist", 5: "sub_out", 6: "sub_in"}

ZONES = ("rim", "mid", "three")
SPECIALS = ("PAD", "BOS", "EOS", "GO")
MARKERS = (
    "M_HOME",
    "M_AWAY",
    "M_PLAYOFF",
    "M_OUT_H",
    "M_OUT_A",
    "M_START_H",
    "M_START_A",
    "M_ROST_H",
    "M_ROST_A",
    "M_STATE",
    "M_LINEUP",
)
FOUL_D_SUBTYPES = (
    "Shooting",
    "Personal",
    "Loose Ball",
    "Personal Take",
    "Transition Take",
    "Away From Play",
    "Inbound",
    "Block",
    "Punching",
)
FOUL_O_SUBTYPES = ("Offensive", "Offensive Charge")


def _evt_specs() -> list[tuple[str, int, tuple[int, ...], int, str]]:
    """(name, side, payload slots, actor points, kind); side 0 home / 1 away / -1 none."""
    specs: list[tuple[str, int, tuple[int, ...], int, str]] = []
    for side, sn in ((0, "H"), (1, "A")):
        for z in ZONES:
            pts = 3 if z == "three" else 2
            specs.append((f"{sn}_MISS_{z}", side, (SL_ACTOR,), 0, "miss"))
            specs.append((f"{sn}_MAKE_{z}_U", side, (SL_ACTOR,), pts, "make"))
            specs.append((f"{sn}_MAKE_{z}_A", side, (SL_ACTOR, SL_ASSIST), pts, "make_ast"))
        specs.append((f"{sn}_FT_MAKE", side, (SL_ACTOR,), 1, "ft_make"))
        specs.append((f"{sn}_FT_MISS", side, (SL_ACTOR,), 0, "ft_miss"))
        specs.append((f"{sn}_REB_O", side, (SL_ACTOR,), 0, "reb"))
        specs.append((f"{sn}_REB_D", side, (SL_ACTOR,), 0, "reb"))
        specs.append((f"{sn}_REB_O_TEAM", side, (), 0, "reb_team"))
        specs.append((f"{sn}_REB_D_TEAM", side, (), 0, "reb_team"))
        specs.append((f"{sn}_TOV", side, (SL_ACTOR,), 0, "tov"))
        specs.append((f"{sn}_TOV_TEAM", side, (), 0, "tov_team"))
        specs.append((f"{sn}_FOUL_D", side, (SL_ACTOR,), 0, "foul_d"))
        specs.append((f"{sn}_FOUL_O", side, (SL_ACTOR,), 0, "foul_o"))
        specs.append((f"{sn}_FOUL_X", side, (SL_ACTOR,), 0, "foul_x"))
        specs.append((f"{sn}_SUB", side, (SL_OUT, SL_IN), 0, "sub"))
        specs.append((f"{sn}_TIMEOUT", side, (), 0, "timeout"))
    specs.append(("PERIOD_END", -1, (), 0, "period_end"))
    return specs


def sc_bucket(diff: int) -> int:
    """Signed score-difference bucket in [-23, 23] (exact to 20, then 21-25, 26-35, 36+)."""
    a = abs(int(diff))
    n = a if a <= 20 else 21 if a <= 25 else 22 if a <= 35 else 23
    return n if diff >= 0 else -n


def elo_bucket(p: float) -> int:
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    logit = math.log(p / (1 - p))
    return int(min(max((logit + 3.0) / 6.0 * N_ELO, 0), N_ELO - 1))


def rat_bucket(m: float) -> int:
    return int(min(max((float(m) + 12.0) / 2.0, 0), N_RAT - 1))


def rest_bucket(days: int | None) -> int:
    """0 = back-to-back (1 day), 1 = one day off, 2 = two days off, 3 = three or more / unknown."""
    if days is None:
        return N_REST - 1
    return int(min(max(days - 1, 0), N_REST - 1))


def period_len(period: int) -> float:
    return 720.0 if period <= 4 else 300.0


# ----------------------------------------------------------------------------- vocabulary


class Vocab:
    """Fixed token layout + the player vocabulary (``player_ids`` -> ``P_`` tokens)."""

    def __init__(self, player_ids: Sequence[int], team_ids: Sequence[int]) -> None:
        self.player_ids = [int(p) for p in player_ids]
        self.team_ids = sorted({int(t) for t in team_ids})
        names: list[str] = list(SPECIALS) + list(MARKERS)
        names += [f"TEAM_{t}" for t in self.team_ids] + ["TEAM_UNK"]
        self.elo0 = len(names)
        names += [f"ELO_{i}" for i in range(N_ELO)]
        self.rat0 = len(names)
        names += [f"RAT_{i}" for i in range(N_RAT)]
        self.rest0 = len(names)
        names += [f"REST_{i}" for i in range(N_REST)]
        self.dt0 = len(names)
        names += [f"DT_{i}" for i in range(len(DT_REP))]
        self.sc0 = len(names)
        names += [f"SC_{i - 23:+d}" for i in range(N_SC)]
        self.per0 = len(names)
        names += [f"PER_{i + 1}" for i in range(N_PER)]
        self.clk0 = len(names)
        names += [f"CLK_{i}" for i in range(N_CLK)]
        self.bon0 = len(names)
        names += [f"BON_{i}" for i in range(N_BON)]
        self.evt0 = len(names)
        self.evt_specs = _evt_specs()
        names += [f"EVT_{s[0]}" for s in self.evt_specs]
        self.new0 = len(names)
        names += [f"NEW_{i}" for i in range(N_NEW)]
        self.pl0 = len(names)
        names += [f"P_{p}" for p in self.player_ids]
        self.names = names
        self.size = len(names)
        self.id_of = {n: i for i, n in enumerate(names)}
        self.player_tok = {p: self.pl0 + i for i, p in enumerate(self.player_ids)}
        self.team_tok = {t: self.id_of[f"TEAM_{t}"] for t in self.team_ids}
        self.n_evt = len(self.evt_specs)
        self.evt_index = {s[0]: i for i, s in enumerate(self.evt_specs)}
        self.evt_side = np.array([s[1] for s in self.evt_specs], dtype=np.int8)
        self.evt_pay = np.zeros((self.n_evt, 2), dtype=np.int8)
        for i, s in enumerate(self.evt_specs):
            for j, c in enumerate(s[2]):
                self.evt_pay[i, j] = c
        self.evt_pts = np.array([s[3] for s in self.evt_specs], dtype=np.int8)
        kinds = [s[4] for s in self.evt_specs]
        self.evt_reb = np.array([k == "reb" for k in kinds], dtype=np.int8)
        self.evt_foul_d = np.array([k == "foul_d" for k in kinds], dtype=np.int8)
        self.evt_sub = np.array([k == "sub" for k in kinds], dtype=np.int8)
        self.evt_pe = np.array([k == "period_end" for k in kinds], dtype=np.int8)

    # --- helpers
    def tok(self, name: str) -> int:
        return self.id_of[name]

    def evt_tok(self, name: str) -> int:
        return self.evt0 + self.evt_index[name]

    def is_player_tok(self, t: int) -> bool:
        return t >= self.new0

    def category(self, t: int) -> str:
        """Coarse family of a token id (for documentation / diagnostics)."""
        for lo, hi, name in (
            (self.dt0, self.sc0, "dt"),
            (self.sc0, self.per0, "score"),
            (self.evt0, self.new0, "event"),
            (self.new0, self.pl0, "new_player"),
            (self.pl0, self.size, "player"),
        ):
            if lo <= t < hi:
                return name
        return "other"

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "size": self.size,
            "player_ids": self.player_ids,
            "team_ids": self.team_ids,
            "layout": {
                "elo0": self.elo0,
                "rat0": self.rat0,
                "rest0": self.rest0,
                "dt0": self.dt0,
                "sc0": self.sc0,
                "per0": self.per0,
                "clk0": self.clk0,
                "bon0": self.bon0,
                "evt0": self.evt0,
                "new0": self.new0,
                "pl0": self.pl0,
                "n_evt": self.n_evt,
                "n_new": N_NEW,
                "n_dt": len(DT_REP),
                "n_sc": N_SC,
                "n_per": N_PER,
                "n_clk": N_CLK,
                "n_bon": N_BON,
                "pad": self.tok("PAD"),
                "bos": self.tok("BOS"),
                "eos": self.tok("EOS"),
                "go": self.tok("GO"),
                "m_state": self.tok("M_STATE"),
                "m_lineup": self.tok("M_LINEUP"),
            },
            "dt_rep": list(DT_REP),
            "block_seconds": BLOCK_SECONDS,
            "lineup_every": LINEUP_EVERY,
            "clk_bin_seconds": CLK_BIN_SECONDS,
            "ctx": CTX,
            "rm": RM,
            "evt": {
                "names": [s[0] for s in self.evt_specs],
                "side": self.evt_side.tolist(),
                "pay": self.evt_pay.tolist(),
                "pts": self.evt_pts.tolist(),
                "reb": self.evt_reb.tolist(),
                "foul_d": self.evt_foul_d.tolist(),
                "sub": self.evt_sub.tolist(),
                "period_end": self.evt_pe.tolist(),
            },
            "slot_names": {str(k): v for k, v in SLOT_NAMES.items()},
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> Vocab:
        return Vocab(d["player_ids"], d["team_ids"])


@dataclass
class GamePlayers:
    """Per-game player-id <-> token mapping (known players vs unique NEW slots)."""

    vocab: Vocab
    pids: Sequence[int]
    new_pids: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        unknown = sorted({int(p) for p in self.pids if int(p) not in self.vocab.player_tok})
        self.new_pids = unknown[:N_NEW]
        self._new = {p: i for i, p in enumerate(self.new_pids)}
        self._overflow = set(unknown[N_NEW:])

    def tok(self, pid: int) -> int:
        pid = int(pid)
        t = self.vocab.player_tok.get(pid)
        if t is not None:
            return t
        k = self._new.get(pid, N_NEW - 1)
        return self.vocab.new0 + k

    def pid_of(self, tok: int) -> int | None:
        v = self.vocab
        if tok >= v.pl0:
            return v.player_ids[tok - v.pl0]
        if v.new0 <= tok < v.pl0:
            k = tok - v.new0
            return self.new_pids[k] if k < len(self.new_pids) else None
        return None


# ----------------------------------------------------------------------------- event extraction


@dataclass
class Ev:
    """One normalised play-by-play event (before quantisation)."""

    period: int
    clock: float  # seconds remaining in the period
    name: str  # EVT name without the "EVT_" prefix
    actor: int | None = None
    assist: int | None = None
    out_p: int | None = None
    in_p: int | None = None


def _team_side(team: int, home: int, away: int) -> int | None:
    return 0 if team == home else 1 if team == away else None


def extract_events(
    pbp: pl.DataFrame, home_team: int, away_team: int
) -> tuple[list[Ev], dict[str, int]]:
    """Normalise one game's V3 play-by-play into :class:`Ev` rows (period, then action number).

    Skipped rows (replays, violations, ejections, jump balls, block rows, period starts) are counted
    in the returned stats. Unresolvable substitutions are skipped and counted.
    """
    stats: defaultdict[str, int] = defaultdict(int)
    df = pbp.sort(["period", "action_number"])
    exact, normalized = _lineup_name_lookup(df)
    ast_lookup = _assist_name_lookup(df)
    events: list[Ev] = []
    last_shot_side: int | None = None
    for row in df.iter_rows(named=True):
        at = str(row["action_type"] or "")
        st = str(row["sub_type"] or "")
        desc = str(row["description"] or "")
        team = int(row["team_id"] or 0)
        pid = int(row["player_id"] or 0)
        period = int(row["period"])
        clock = _clock_to_seconds(str(row["clock"]))
        side = _team_side(team, home_team, away_team)
        if at == "period":
            if st == "end":
                events.append(Ev(period, 0.0, "PERIOD_END"))
            continue
        if at in ("Made Shot", "Missed Shot"):
            if side is None:
                stats["skipped_no_side"] += 1
                continue
            zone = _shot_zone(int(row["shot_value"]), int(row["shot_distance"]), st, desc)
            zone = "three" if zone in ("corner3", "above3") else zone
            sn = "H" if side == 0 else "A"
            last_shot_side = side
            if at == "Missed Shot":
                events.append(Ev(period, clock, f"{sn}_MISS_{zone}", actor=pid))
                continue
            ast = _parse_assist(desc, team, ast_lookup)
            if ast is not None:
                events.append(Ev(period, clock, f"{sn}_MAKE_{zone}_A", actor=pid, assist=ast))
            else:
                events.append(Ev(period, clock, f"{sn}_MAKE_{zone}_U", actor=pid))
            continue
        if at == "Free Throw":
            if side is None:
                stats["skipped_no_side"] += 1
                continue
            sn = "H" if side == 0 else "A"
            last_shot_side = side
            miss = desc.strip().upper().startswith("MISS")
            events.append(Ev(period, clock, f"{sn}_FT_{'MISS' if miss else 'MAKE'}", actor=pid))
            continue
        if at == "Rebound":
            rside = side
            is_team = team == 0 or pid in (home_team, away_team)
            if is_team:
                rside = _team_side(pid if team == 0 else team, home_team, away_team)
            if rside is None:
                stats["skipped_no_side"] += 1
                continue
            sn = "H" if rside == 0 else "A"
            od = "O" if last_shot_side is not None and rside == last_shot_side else "D"
            if is_team:
                events.append(Ev(period, clock, f"{sn}_REB_{od}_TEAM"))
            else:
                events.append(Ev(period, clock, f"{sn}_REB_{od}", actor=pid))
            continue
        if at == "Turnover":
            tside = side
            is_team = team == 0 or pid in (home_team, away_team) or pid == 0
            if is_team:
                tside = _team_side(pid if team == 0 else team, home_team, away_team)
                if tside is None:
                    tside = side
            if tside is None:
                stats["skipped_no_side"] += 1
                continue
            sn = "H" if tside == 0 else "A"
            events.append(
                Ev(period, clock, f"{sn}_TOV_TEAM")
                if is_team
                else Ev(period, clock, f"{sn}_TOV", actor=pid)
            )
            continue
        if at == "Foul":
            if side is None:
                stats["skipped_no_side"] += 1
                continue
            sn = "H" if side == 0 else "A"
            kind = "D" if st in FOUL_D_SUBTYPES else "O" if st in FOUL_O_SUBTYPES else "X"
            events.append(Ev(period, clock, f"{sn}_FOUL_{kind}", actor=pid))
            continue
        if at == "Timeout":
            tside = _team_side(pid if team == 0 else team, home_team, away_team)
            if tside is None:
                stats["skipped_no_side"] += 1
                continue
            events.append(Ev(period, clock, f"{'H' if tside == 0 else 'A'}_TIMEOUT"))
            continue
        if at == "Substitution":
            if side is None or not desc.startswith("SUB:"):
                stats["sub_skipped"] += 1
                continue
            body = desc[4:].strip()
            if " FOR " not in body:
                stats["sub_skipped"] += 1
                continue
            in_name = body.split(" FOR ", 1)[0].strip()
            in_pid = _resolve_name(team, in_name, exact, normalized)
            if in_pid is None or pid == 0 or in_pid == pid:
                stats["sub_unresolved"] += 1
                continue
            events.append(
                Ev(period, clock, f"{'H' if side == 0 else 'A'}_SUB", out_p=pid, in_p=int(in_pid))
            )
            continue
        stats["skipped_other"] += 1
    return events, dict(stats)


# ----------------------------------------------------------------------------- stream state machine


class StreamState:
    """Reference (python) state machine over a token stream.

    Feed header-free stream tokens one at a time (initial block onwards). Forced tokens (state
    blocks, lineup refreshes, EOS) are computed from the state when an event completes and
    verified when they arrive; a mismatch is counted in ``n_mismatch`` (0 for every tokenizer
    output). ``nba/colab/jobs/pbp_gpt/pbp_gpt.py`` holds the vectorised twin used for sampling and
    a test proves both produce identical forced tokens.
    """

    def __init__(
        self,
        v: Vocab,
        start_h: Sequence[int],
        start_a: Sequence[int],
        *,
        record: bool = False,
    ) -> None:
        self.v = v
        self.onc: list[list[int]] = [list(start_h), list(start_a)]
        self.last_act: list[dict[int, int]] = [{}, {}]
        self.clock_i = 0
        self.period = 1
        self.tclock = period_len(1)
        self.grid = int(self.tclock // BLOCK_SECONDS)
        self.score = [0, 0]
        self.fouls = [0, 0]
        self.lb = 0
        self.pending: list[int] = []
        self.phase = "dt"
        self.cur = -1
        self.k = 0
        self.out_tok = -1
        self.cur_actor = -1
        self.done = False
        self.n_mismatch = 0
        self.n_corr = 0
        self.n_actor = 0
        self.n_actor_on = 0
        self.box: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0])
        self.record = record
        self.log: list[tuple[Any, ...]] = []
        self.pending = self._block(True)

    # --- forced block construction
    def _block(self, force_lineup: bool) -> list[int]:
        v = self.v
        diff = self.score[0] - self.score[1]
        per = min(self.period, N_PER) - 1
        clk = min(int(self.tclock // CLK_BIN_SECONDS), N_CLK - 1)
        bon = int(self.fouls[1] >= 5) + 2 * int(self.fouls[0] >= 5)
        blk = [
            v.tok("M_STATE"),
            v.sc0 + sc_bucket(diff) + 23,
            v.per0 + per,
            v.clk0 + clk,
            v.bon0 + bon,
        ]
        if force_lineup or self.lb >= LINEUP_EVERY - 1:
            self.lb = 0
            blk += [v.tok("M_LINEUP")] + sorted(self.onc[0]) + sorted(self.onc[1])
        else:
            self.lb += 1
        return blk

    # --- on-court tracking (stream-derived, so the sampler can reproduce it)
    def _touch(self, side: int, tok: int) -> bool:
        """Mark ``tok`` as acting for ``side``; repair the tracked five if needed."""
        self.clock_i += 1
        self.n_actor += 1
        five = self.onc[side]
        if tok in five:
            self.n_actor_on += 1
            self.last_act[side][tok] = self.clock_i
            return True
        other = self.onc[1 - side]
        if tok in other:  # actor attributed to the wrong team: leave tracking alone
            return False
        victim = min(five, key=lambda t: self.last_act[side].get(t, -1))
        five[five.index(victim)] = tok
        self.last_act[side][tok] = self.clock_i
        self.n_corr += 1
        return False

    def _sub(self, side: int, out_t: int, in_t: int) -> None:
        self.clock_i += 1
        five = self.onc[side]
        if in_t in five:
            return
        if out_t in five:
            five[five.index(out_t)] = in_t
        else:
            victim = min(five, key=lambda t: self.last_act[side].get(t, -1))
            five[five.index(victim)] = in_t
        self.last_act[side][in_t] = self.clock_i

    # --- event completion
    def _complete(self) -> None:
        v = self.v
        e = self.cur
        side = int(v.evt_side[e])
        self.phase = "dt"
        if v.evt_pts[e] and side >= 0:
            self.score[side] += int(v.evt_pts[e])
        if v.evt_foul_d[e] and side >= 0:
            self.fouls[side] += 1
        if v.evt_pe[e]:
            if self.period >= 4 and self.score[0] != self.score[1]:
                self.pending = [v.tok("EOS")]
                return
            self.period += 1
            self.tclock = period_len(self.period)
            self.grid = int(self.tclock // BLOCK_SECONDS)
            self.fouls = [0, 0]
            self.pending = self._block(True)
            return
        g = int(self.tclock // BLOCK_SECONDS)
        if g < self.grid:
            self.grid = g
            self.pending = self._block(False)

    # --- token consumption
    def feed(self, tok: int) -> None:
        v = self.v
        if self.done:
            return
        if self.pending:
            exp = self.pending.pop(0)
            if tok != exp:
                self.n_mismatch += 1
            if exp == v.tok("EOS"):
                self.done = True
                self.phase = "done"
            return
        if self.phase == "dt":
            k = tok - v.dt0
            if not 0 <= k < len(DT_REP):
                self.n_mismatch += 1
                return
            self.tclock = max(0.0, self.tclock - DT_REP[k])
            self.phase = "evt"
            return
        if self.phase == "evt":
            e = tok - v.evt0
            if not 0 <= e < v.n_evt:
                self.n_mismatch += 1
                return
            self.cur = e
            self.k = 0
            if self.record:
                self.log.append(("evt", e, self.period, self.tclock))
            if v.evt_pay[e, 0] == 0:
                self._complete()
            else:
                self.phase = "payload"
            return
        # payload
        e = self.cur
        side = int(v.evt_side[e])
        code = int(v.evt_pay[e, self.k])
        if code == SL_ACTOR:
            self._touch(side, tok)
            self.cur_actor = tok
            b = self.box[tok]
            b[0] += int(v.evt_pts[e])
            b[1] += int(v.evt_reb[e])
        elif code == SL_ASSIST:
            self._touch(side, tok)
            self.box[tok][2] += 1
        elif code == SL_OUT:
            self.out_tok = tok
        elif code == SL_IN:
            self._sub(side, self.out_tok, tok)
        if self.record:
            self.log.append(("pay", code, tok))
        self.k += 1
        if self.k >= 2 or v.evt_pay[e, self.k] == 0:
            self._complete()

    def snapshot(self) -> dict[str, Any]:
        return {
            "score_h": self.score[0],
            "score_a": self.score[1],
            "period": self.period,
            "tclock": float(self.tclock),
            "grid": self.grid,
            "lb": self.lb,
            "fouls_h": self.fouls[0],
            "fouls_a": self.fouls[1],
            "onc": [list(self.onc[0]), list(self.onc[1])],
            "box": {t: list(b) for t, b in self.box.items()},
        }


def quantise_dt(tclock: float, clock: float) -> int:
    """Error-diffusion bucket: the one whose resulting token clock is nearest the true clock."""
    cand = np.maximum(tclock - np.asarray(DT_REP, dtype=np.float64), 0.0)
    return int(np.argmin(np.abs(cand - clock)))


# ----------------------------------------------------------------------------- headers & game build


@dataclass
class GameHeader:
    """Pre-tip information for one game (pids; converted to tokens at tokenization)."""

    game_id: str
    home_team: int
    away_team: int
    playoff: bool
    elo_p: float
    rat_h: float
    rat_a: float
    rest_h: int | None
    rest_a: int | None
    out_h: list[int]
    out_a: list[int]
    start_h: list[int]
    start_a: list[int]
    roster_h: list[int]  # as-of rotation excluding starters, OUT players removed (<= RM-5)
    roster_a: list[int]


class AsOfHistory:
    """Strictly-earlier-games state: team rosters, margins, rest, player rolling means.

    ``update`` is called only AFTER a game has been tokenized, so every query for game g sees
    games dated before g (the export loop walks games in (date, game_id) order).
    """

    def __init__(self) -> None:
        self.team_games: defaultdict[int, deque[dict[int, float]]] = defaultdict(
            lambda: deque(maxlen=10)
        )
        self.team_margins: defaultdict[int, deque[float]] = defaultdict(lambda: deque(maxlen=20))
        self.team_last_date: dict[int, Any] = {}
        self.player_hist: defaultdict[int, deque[tuple[float, float, float, float]]] = defaultdict(
            lambda: deque(maxlen=10)
        )
        self.player_team: dict[int, int] = {}

    def rest_days(self, team: int, date: Any) -> int | None:
        last = self.team_last_date.get(team)
        return None if last is None else int((date - last).days)

    def rating(self, team: int) -> float:
        m = self.team_margins.get(team)
        return float(np.mean(m)) if m is not None and len(m) >= 5 else 0.0

    def rotation(self, team: int) -> list[int]:
        """Players by summed minutes over the last <=10 team games (desc), ties by id."""
        tot: defaultdict[int, float] = defaultdict(float)
        for gm in self.team_games.get(team, ()):
            for p, mins in gm.items():
                tot[p] += mins
        return [p for p, _ in sorted(tot.items(), key=lambda kv: (-kv[1], kv[0]))]

    def player_avg(self, pid: int) -> tuple[float, float, float, float]:
        h = self.player_hist.get(pid)
        if not h:
            return (0.0, 0.0, 0.0, 0.0)
        a = np.asarray(h, dtype=np.float64).mean(axis=0)
        return (float(a[0]), float(a[1]), float(a[2]), float(a[3]))

    def update(
        self, date: Any, home: int, away: int, margin_home: float, rows: list[dict[str, Any]]
    ) -> None:
        for team, mg in ((home, margin_home), (away, -margin_home)):
            self.team_margins[team].append(mg)
            self.team_last_date[team] = date
            self.team_games[team].append(
                {
                    int(r["player_id"]): float(r["minutes"])
                    for r in rows
                    if int(r["team_id"]) == team and (r["minutes"] or 0) > 0
                }
            )
        for r in rows:
            if (r["minutes"] or 0) > 0:
                pid = int(r["player_id"])
                self.player_hist[pid].append(
                    (
                        float(r["pts"] or 0),
                        float(r["reb"] or 0),
                        float(r["ast"] or 0),
                        float(r["minutes"] or 0),
                    )
                )
                self.player_team[pid] = int(r["team_id"])


def build_header(
    hist: AsOfHistory,
    game: dict[str, Any],
    starters: dict[int, list[int]],
    out_pids: set[int],
    elo_p: float,
) -> GameHeader:
    """Pre-tip header from strictly earlier history + the game's own starters/OUT report."""
    home, away = int(game["home_team"]), int(game["away_team"])
    date = game["game_date"]
    out_by_team: dict[int, list[int]] = {home: [], away: []}
    for p in sorted(out_pids):
        t = hist.player_team.get(p)
        if t in out_by_team:
            out_by_team[t].append(p)

    def order(team: int, ps: list[int]) -> list[int]:
        return sorted(ps, key=lambda p: (-hist.player_avg(p)[3], p))

    def side_info(team: int) -> tuple[list[int], list[int], list[int]]:
        outs = out_by_team[team]
        outs_top = order(team, outs)[:MAX_OUT]
        start = order(team, [int(p) for p in starters.get(team, [])])
        rot = [p for p in hist.rotation(team) if p not in set(start) and p not in set(outs)]
        return outs_top, start, rot[: RM - 5]

    out_h, st_h, ro_h = side_info(home)
    out_a, st_a, ro_a = side_info(away)
    return GameHeader(
        game_id=str(game["game_id"]),
        home_team=home,
        away_team=away,
        playoff=str(game["game_id"])[2:3] in ("4", "5"),
        elo_p=float(elo_p),
        rat_h=hist.rating(home),
        rat_a=hist.rating(away),
        rest_h=hist.rest_days(home, date),
        rest_a=hist.rest_days(away, date),
        out_h=out_h,
        out_a=out_a,
        start_h=st_h,
        start_a=st_a,
        roster_h=ro_h,
        roster_a=ro_a,
    )


def header_pids(h: GameHeader) -> list[int]:
    return [
        *h.out_h,
        *h.out_a,
        *h.start_h,
        *h.start_a,
        *h.roster_h,
        *h.roster_a,
    ]


def header_tokens(v: Vocab, h: GameHeader, gp: GamePlayers) -> list[int]:
    """Header token list (pre-tip information only)."""
    t = v.tok
    toks = [
        t("BOS"),
        t("M_HOME"),
        v.team_tok.get(h.home_team, t("TEAM_UNK")),
        t("M_AWAY"),
        v.team_tok.get(h.away_team, t("TEAM_UNK")),
    ]
    if h.playoff:
        toks.append(t("M_PLAYOFF"))
    toks += [
        v.elo0 + elo_bucket(h.elo_p),
        v.rat0 + rat_bucket(h.rat_h),
        v.rat0 + rat_bucket(h.rat_a),
        v.rest0 + rest_bucket(h.rest_h),
        v.rest0 + rest_bucket(h.rest_a),
        t("M_OUT_H"),
        *[gp.tok(p) for p in h.out_h],
        t("M_OUT_A"),
        *[gp.tok(p) for p in h.out_a],
        t("M_START_H"),
        *[gp.tok(p) for p in h.start_h],
        t("M_START_A"),
        *[gp.tok(p) for p in h.start_a],
        t("M_ROST_H"),
        *[gp.tok(p) for p in h.roster_h],
        t("M_ROST_A"),
        *[gp.tok(p) for p in h.roster_a],
        t("GO"),
    ]
    return toks


@dataclass
class TokGame:
    tokens: np.ndarray  # int16
    slots: np.ndarray  # int8
    header_len: int
    ckpts: list[dict[str, Any] | None]
    final: dict[str, Any]
    quality: dict[str, int]
    players: GamePlayers
    inconsistent: bool


def tokenize_game(
    v: Vocab,
    h: GameHeader,
    events: list[Ev],
    extra_pids: Sequence[int] = (),
) -> TokGame:
    """Tokenize one game. ``extra_pids``: other ids seen (events), for NEW-slot assignment."""
    ev_pids: list[int] = []
    for e in events:
        for p in (e.actor, e.assist, e.out_p, e.in_p):
            if p is not None:
                ev_pids.append(int(p))
    gp = GamePlayers(v, [*header_pids(h), *ev_pids, *extra_pids])
    htoks = header_tokens(v, h, gp)
    st = StreamState(v, [gp.tok(p) for p in h.start_h], [gp.tok(p) for p in h.start_a])
    toks = list(htoks)
    slots = [SL_NONE] * len(htoks)
    ckpts: list[dict[str, Any] | None] = [None] * N_CKPT
    inconsistent = False

    def flush() -> None:
        while st.pending:
            nxt = st.pending[0]
            toks.append(nxt)
            slots.append(SL_NONE)
            st.feed(nxt)

    def maybe_ckpt() -> None:
        flush_needed = not st.pending
        if not flush_needed:
            return
        s = st.snapshot()
        s["pos"] = len(toks)
        if ckpts[0] is None:
            ckpts[0] = s
        for k in (1, 2, 3):
            if ckpts[k] is None and st.period == k + 1:
                ckpts[k] = s
        if ckpts[4] is None and st.period == 4 and st.tclock <= 300.0:
            ckpts[4] = s

    flush()
    maybe_ckpt()
    tclock_ref = st.tclock
    cur_period = 1
    for e in events:
        if st.done:
            continue  # stamped after the final PERIOD_END (subs/timeouts); dropped
        if e.period != cur_period:
            if e.period < cur_period:
                continue
            # the PBP moved to a period the state machine has not reached (missing PERIOD_END)
            inconsistent = True
            break
        tclock_ref = st.tclock
        b = quantise_dt(tclock_ref, e.clock)
        toks.append(v.dt0 + b)
        slots.append(SL_DT)
        st.feed(v.dt0 + b)
        toks.append(v.evt_tok(e.name))
        slots.append(SL_EVT)
        st.feed(toks[-1])
        pay = v.evt_pay[v.evt_index[e.name]]
        ids = {
            SL_ACTOR: e.actor,
            SL_ASSIST: e.assist,
            SL_OUT: e.out_p,
            SL_IN: e.in_p,
        }
        for c in pay:
            if c == 0:
                break
            pid = ids[int(c)]
            tk = gp.tok(pid if pid is not None else -1)
            toks.append(tk)
            slots.append(int(c))
            st.feed(tk)
        flush()
        cur_period = st.period
        maybe_ckpt()
    if not st.done:
        toks.append(v.tok("EOS"))
        slots.append(SL_NONE)
        inconsistent = True
    arr = np.asarray(toks, dtype=np.int16)
    sl = np.asarray(slots, dtype=np.int8)
    return TokGame(
        tokens=arr,
        slots=sl,
        header_len=len(htoks),
        ckpts=ckpts,
        final={
            "score_h": st.score[0],
            "score_a": st.score[1],
            "box": {t: list(b) for t, b in st.box.items()},
            "n_mismatch": st.n_mismatch,
        },
        quality={
            "n_actor": st.n_actor,
            "n_actor_on": st.n_actor_on,
            "n_corr": st.n_corr,
            "n_mismatch": st.n_mismatch,
        },
        players=gp,
        inconsistent=inconsistent,
    )


def detokenize(v: Vocab, tokens: Sequence[int], header_len: int, gp: GamePlayers) -> dict[str, Any]:
    """Parse a stream back into events and per-player box totals (keyed by player id)."""
    toks = [int(t) for t in tokens]
    h = toks[:header_len]
    # starters from the header
    s_h = h.index(v.tok("M_START_H"))
    s_a = h.index(v.tok("M_START_A"))
    r_h = h.index(v.tok("M_ROST_H"))
    start_h = h[s_h + 1 : s_a]
    start_a = h[s_a + 1 : r_h]
    st = StreamState(v, start_h, start_a, record=True)
    for t in toks[header_len:]:
        st.feed(t)
    box: dict[int, dict[str, int]] = {}
    for t, (p, r, a) in st.box.items():
        pid = gp.pid_of(t)
        if pid is None:
            continue
        box[int(pid)] = {"pts": p, "reb": r, "ast": a}
    return {
        "score_h": st.score[0],
        "score_a": st.score[1],
        "box": box,
        "n_mismatch": st.n_mismatch,
        "done": st.done,
        "log": st.log,
    }


# ----------------------------------------------------------------------------- export


@dataclass
class ExportInputs:
    """Everything the exporter needs; built from the DB by :func:`load_inputs` or from a fixture."""

    games: pl.DataFrame  # game_id, game_date, season, home_team, away_team, home_pts, away_pts
    pgs: pl.DataFrame  # game_id, player_id, team_id, minutes, starter, pts, reb, ast
    avail: pl.DataFrame  # raw player_availability rows
    extra: pl.DataFrame  # game_id, p, mu_margin, sd_margin, mu_total, sd_total
    pbp_loader: Callable[[str], pl.DataFrame | None]


def split_of(games: pl.DataFrame) -> dict[str, str]:
    """game_id -> train / val / report (val = last 20% of 2023 by date)."""
    g23 = games.filter(pl.col("season") == SELECT_SEASON).sort(["game_date", "game_id"])
    ids23 = g23["game_id"].to_list()
    n_val = int(round(SLICE_FRAC * len(ids23)))
    val = set(ids23[len(ids23) - n_val :]) if n_val else set()
    out: dict[str, str] = {}
    for gid, season in zip(games["game_id"].to_list(), games["season"].to_list(), strict=True):
        if season == REPORT_SEASON:
            out[gid] = "report"
        elif gid in val:
            out[gid] = "val"
        else:
            out[gid] = "train"
    return out


def build_player_vocab(
    games: pl.DataFrame, pgs: pl.DataFrame, splits: dict[str, str], min_games: int = MIN_APPEARANCES
) -> list[int]:
    """Players with >= ``min_games`` played box rows in TRAIN games (never val/report)."""
    train_ids = [g for g, s in splits.items() if s == "train"]
    cnt = (
        pgs.filter(pl.col("game_id").is_in(train_ids) & (pl.col("minutes") > 0))
        .group_by("player_id")
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") >= min_games)
        .sort("player_id")
    )
    return [int(p) for p in cnt["player_id"].to_list()]


def _pretip_out(avail: pl.DataFrame, games: pl.DataFrame) -> tuple[dict[str, set[int]], set[str]]:
    from nba.props.context_features_v2 import OUT_ORD, pretip_status
    from nba.sim.usage_redistribution import ReportTriggerConfig

    if avail.height == 0:
        return {}, set()
    tab, reported = pretip_status(avail, games, ReportTriggerConfig())
    out: dict[str, set[int]] = defaultdict(set)
    for gid, pid, so in tab.select("game_id", "player_id", "status_ord").iter_rows():
        if so == OUT_ORD:
            out[str(gid)].add(int(pid))
    return dict(out), reported


def _assemble_roster(gp: GamePlayers, hdr_pids: list[int], on_toks: list[int]) -> list[int]:
    """Roster tokens for one team at a checkpoint: on-court first, then header order; <= RM."""
    toks: list[int] = []
    for t in on_toks:
        if t not in toks:
            toks.append(t)
    for p in hdr_pids:
        t = gp.tok(p)
        if t not in toks:
            toks.append(t)
    return toks[:RM]


def export_pbp_tokens(
    inp: ExportInputs, out_dir: str | Path, *, max_games: int | None = None
) -> dict[str, Any]:
    """Tokenize every game of seasons 2022-2024 and write the Colab input bundle.

    Writes ``tokens.npz`` (tokens int16, slots int8, offsets), ``games.parquet``, ``ckpt.npz``
    (per-game checkpoint states, rosters, boxes, as-of player means), ``vocab.json`` and
    ``export_report.json``. Games are processed one at a time in (date, game_id) order.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    games = inp.games.filter(pl.col("season").is_in(list(EXPORT_SEASONS)))
    games = games.sort(["game_date", "game_id"])
    if max_games is not None:
        games = games.head(max_games)
    splits = split_of(games)
    pgs = inp.pgs.filter(pl.col("game_id").is_in(games["game_id"].to_list()))
    pvocab = build_player_vocab(games, pgs, splits)
    team_ids = sorted(set(games["home_team"].to_list()) | set(games["away_team"].to_list()))
    v = Vocab(pvocab, team_ids)
    out_map, _reported = _pretip_out(inp.avail, games)
    extra = {r["game_id"]: r for r in inp.extra.iter_rows(named=True)}
    by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in pgs.iter_rows(named=True):
        by_game[str(r["game_id"])].append(r)
    hist = AsOfHistory()
    # history must also see games before the first exported one: none are exported (2022 opens
    # the window), so early-2022 rosters are thin; that is documented and counted.
    tok_chunks: list[np.ndarray] = []
    slot_chunks: list[np.ndarray] = []
    rows: list[dict[str, Any]] = []
    ck_scal: list[np.ndarray] = []
    ck_rost: list[np.ndarray] = []
    ck_on: list[np.ndarray] = []
    ck_box: list[np.ndarray] = []
    ck_fin: list[np.ndarray] = []
    ck_asof: list[np.ndarray] = []
    pid_maps: list[np.ndarray] = []
    qual: defaultdict[str, int] = defaultdict(int)
    n_skip: defaultdict[str, int] = defaultdict(int)
    for g in games.iter_rows(named=True):
        gid = str(g["game_id"])
        brow = by_game.get(gid, [])
        home, away = int(g["home_team"]), int(g["away_team"])
        ex = extra.get(gid)
        pbp = inp.pbp_loader(gid)
        starters = {
            t: [int(r["player_id"]) for r in brow if int(r["team_id"]) == t and r["starter"]]
            for t in (home, away)
        }
        ok_in = (
            ex is not None
            and pbp is not None
            and pbp.height > 0
            and all(len(s) == 5 for s in starters.values())
            and g["home_pts"] is not None
            and int(g["home_pts"] or 0) > 0
        )
        if not ok_in:
            n_skip["missing_input"] += 1
            if g["home_pts"]:
                hist.update(g["game_date"], home, away, float(g["home_pts"] - g["away_pts"]), brow)
            continue
        assert ex is not None and pbp is not None
        hdr = build_header(hist, g, starters, out_map.get(gid, set()), float(ex["p"]))
        events, estat = extract_events(pbp, home, away)
        for k, val in estat.items():
            qual[k] += val
        tg = tokenize_game(v, hdr, events)
        truncated = len(tg.tokens) > CTX
        toks = tg.tokens[:CTX]
        sls = tg.slots[:CTX]
        tok_chunks.append(toks)
        slot_chunks.append(sls)
        gp = tg.players
        # per-checkpoint rosters / boxes / as-of means
        scal = np.zeros((N_CKPT, 10), dtype=np.float64)
        rost = np.zeros((N_CKPT, 2, RM), dtype=np.int16)
        onm = np.zeros((N_CKPT, 2, RM), dtype=np.bool_)
        boxa = np.zeros((N_CKPT, 2, RM, 3), dtype=np.int16)
        fina = np.zeros((N_CKPT, 2, RM, 3), dtype=np.int16)
        asof = np.zeros((N_CKPT, 2, RM, 4), dtype=np.float32)
        for c, s in enumerate(tg.ckpts):
            if s is None or s["pos"] >= CTX - 64:
                continue
            scal[c] = [
                1,
                s["pos"],
                s["score_h"],
                s["score_a"],
                s["period"],
                s["tclock"],
                s["grid"],
                s["lb"],
                s["fouls_h"],
                s["fouls_a"],
            ]
            for side, (hp, _team) in enumerate(
                ((hdr.start_h + hdr.roster_h, home), (hdr.start_a + hdr.roster_a, away))
            ):
                toks_r = _assemble_roster(gp, hp, s["onc"][side])
                for j, t in enumerate(toks_r):
                    rost[c, side, j] = t
                    onm[c, side, j] = t in s["onc"][side]
                    bx = s["box"].get(t, [0, 0, 0])
                    boxa[c, side, j] = bx
                    fb = tg.final["box"].get(t, [0, 0, 0])
                    fina[c, side, j] = fb
                    pid = gp.pid_of(t)
                    asof[c, side, j] = hist.player_avg(pid) if pid is not None else (0, 0, 0, 0)
        ck_scal.append(scal)
        ck_rost.append(rost)
        ck_on.append(onm)
        ck_box.append(boxa)
        ck_fin.append(fina)
        ck_asof.append(asof)
        pm = np.full(N_NEW, -1, dtype=np.int64)
        pm[: len(gp.new_pids)] = gp.new_pids
        pid_maps.append(pm)
        match = (tg.final["score_h"] == g["home_pts"]) and (tg.final["score_a"] == g["away_pts"])
        rows.append(
            {
                "game_id": gid,
                "game_date": g["game_date"],
                "season": int(g["season"]),
                "split": splits[gid],
                "home_team": home,
                "away_team": away,
                "home_pts": int(g["home_pts"]),
                "away_pts": int(g["away_pts"]),
                "tok_home_pts": int(tg.final["score_h"]),
                "tok_away_pts": int(tg.final["score_a"]),
                "score_match": bool(match),
                "header_len": int(tg.header_len),
                "n_tokens": int(len(toks)),
                "truncated_ctx": bool(truncated),
                "inconsistent": bool(tg.inconsistent),
                "ok": bool(match and not tg.inconsistent and not truncated),
                "is_playoff": bool(hdr.playoff),
                "p_elo": float(ex["p"]),
                "mu_margin": float(ex["mu_margin"]),
                "sd_margin": float(ex["sd_margin"]),
                "mu_total": float(ex["mu_total"]),
                "sd_total": float(ex["sd_total"]),
                "n_roster_h": len(hdr.start_h) + len(hdr.roster_h),
                "n_roster_a": len(hdr.start_a) + len(hdr.roster_a),
                "n_out_h": len(hdr.out_h),
                "n_out_a": len(hdr.out_a),
                "n_actor": tg.quality["n_actor"],
                "n_actor_on": tg.quality["n_actor_on"],
                "n_new_players": len(gp.new_pids),
            }
        )
        for k, val in tg.quality.items():
            qual[k] += val
        hist.update(g["game_date"], home, away, float(g["home_pts"] - g["away_pts"]), brow)
    if not rows:
        raise ValueError("no games exported")
    tokens = np.concatenate(tok_chunks)
    slots = np.concatenate(slot_chunks)
    offsets = np.concatenate([[0], np.cumsum([len(c) for c in tok_chunks])]).astype(np.int64)
    np.savez_compressed(out / "tokens.npz", tokens=tokens, slots=slots, offsets=offsets)
    gdf = pl.DataFrame(rows)
    gdf.write_parquet(out / "games.parquet")
    np.savez_compressed(
        out / "ckpt.npz",
        scal=np.stack(ck_scal),
        rost=np.stack(ck_rost),
        on=np.stack(ck_on),
        box=np.stack(ck_box),
        final=np.stack(ck_fin),
        asof=np.stack(ck_asof),
        new_pids=np.stack(pid_maps),
    )
    train = gdf.filter(pl.col("split") == "train")
    tps = float(int(train["n_tokens"].sum()) / max(1.0, 2880.0 * train.height))
    vj = v.to_json()
    vj["tok_per_sec"] = tps
    (out / "vocab.json").write_text(json.dumps(vj))
    n_act = max(1, int(gdf["n_actor"].sum()))
    report = {
        "n_games": gdf.height,
        "by_split": {s: int((gdf["split"] == s).sum()) for s in ("train", "val", "report")},
        "n_ok": int(gdf["ok"].sum()),
        "n_score_match": int(gdf["score_match"].sum()),
        "n_inconsistent": int(gdf["inconsistent"].sum()),
        "n_truncated_ctx": int(gdf["truncated_ctx"].sum()),
        "n_skipped_missing_input": n_skip["missing_input"],
        "vocab_size": v.size,
        "n_player_tokens": len(pvocab),
        "n_tokens_total": int(len(tokens)),
        "tokens_per_game_quantiles": [
            float(x) for x in np.quantile(gdf["n_tokens"].to_numpy(), [0.05, 0.5, 0.95, 1.0])
        ],
        "lineup_consistency": float(gdf["n_actor_on"].sum() / n_act),
        "new_player_games_frac": float((gdf["n_new_players"].to_numpy() > 0).mean()),
        "extract_stats": dict(qual),
        "tok_per_sec": tps,
        "seed": SEED,
        "seasons": list(EXPORT_SEASONS),
    }
    (out / "export_report.json").write_text(json.dumps(report, indent=1))
    return report


def load_inputs(
    db_path: str | Path,
    elo_oof: str | Path,
    pbp_dir: str | Path,
) -> ExportInputs:
    """Build :class:`ExportInputs` from the (read-only) DB, the Elo OOF file and cached PBP."""
    from nba.features.game_sets import MODEL_NAME_ELO, game_frame, load_db_inputs
    from nba.parlay.game_model import fit_game_model

    games, pgs, _static, avail = load_db_inputs(db_path)
    elo = pl.read_parquet(elo_oof).filter(pl.col("model") == MODEL_NAME_ELO).select("game_id", "p")
    params = fit_game_model(games, elo, exclude_seasons=(2024,))
    gf = game_frame(games, elo, params)
    extra = gf.select("game_id", "p", "mu_margin", "sd_margin", "mu_total", "sd_total")
    root = Path(pbp_dir)

    def loader(gid: str) -> pl.DataFrame | None:
        p = root / f"{gid}.parquet"
        return pl.read_parquet(p) if p.exists() else None

    pgs = pgs.select("game_id", "player_id", "team_id", "minutes", "starter", "pts", "reb", "ast")
    return ExportInputs(games, pgs, avail, extra, loader)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Export tokenized play-by-play for the basketball GPT."
    )
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--elo-oof", default="data/injury_elo/oof_predictions.parquet")
    ap.add_argument("--pbp-dir", default="data/pbp")
    ap.add_argument("--out-dir", default="data/colab/pbp_gpt")
    ap.add_argument("--max-games", type=int, default=None)
    a = ap.parse_args(argv)
    inp = load_inputs(a.db, a.elo_oof, a.pbp_dir)
    rep = export_pbp_tokens(inp, a.out_dir, max_games=a.max_games)
    print(json.dumps({k: rep[k] for k in ("n_games", "n_ok", "vocab_size", "n_tokens_total")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
