"""Tests for the play-by-play tokenizer, the basketball-GPT job (CPU smoke) and its evaluator.

Everything runs on tiny synthetic games (consistent by construction) and the committed 3-game
fixture; nothing touches the real DB, nba_api or the GPU.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import polars as pl
import pytest

from research.features import pbp_tokens as pt
from tests.fixtures.loader import load_pbp

REPO = Path(__file__).resolve().parents[3]
JOB_PATH = REPO / "research" / "colab" / "jobs" / "pbp_gpt" / "pbp_gpt.py"

T1, T2 = 1610612738, 1610612755
R1 = list(range(101, 111))
R2 = list(range(201, 211))
ROOKIE = 999


def _name(pid: int) -> str:
    return "".join(chr(65 + int(d)) for d in str(pid))


def _clock(sec: float) -> str:
    m = int(sec // 60)
    return f"PT{m:02d}M{sec - 60 * m:05.2f}S"


class _Gen:
    """Consistent synthetic play-by-play generator (actors always on court)."""

    def __init__(
        self, game_id: str, rosters: tuple[list[int], list[int]], seed: int, teams: tuple[int, int]
    ) -> None:
        self.gid = game_id
        self.rng = np.random.default_rng(seed)
        self.teams = teams
        self.rosters = rosters
        self.on = [list(rosters[0][:5]), list(rosters[1][:5])]
        self.rows: list[dict[str, Any]] = []
        self.an = 0
        self.score = [0, 0]
        self.box: dict[int, dict[str, int]] = {
            p: {"pts": 0, "reb": 0, "ast": 0} for r in rosters for p in r
        }

    def add(
        self,
        period: int,
        clock: float,
        team: int,
        pid: int,
        at: str,
        st: str,
        desc: str,
        sv: int = 0,
        dist: int = 0,
    ) -> None:
        self.an += 1
        loc = "h" if team == self.teams[0] else "v" if team == self.teams[1] else ""
        self.rows.append(
            {
                "game_id": self.gid,
                "action_number": self.an,
                "period": period,
                "clock": _clock(clock),
                "team_id": team,
                "player_id": pid,
                "player_name": _name(pid) if pid and pid not in (T1, T2) else "",
                "action_type": at,
                "sub_type": st,
                "description": desc,
                "score_home": str(self.score[0]),
                "score_away": str(self.score[1]),
                "points_total": self.score[0] + self.score[1],
                "shot_result": "",
                "shot_value": sv,
                "shot_distance": dist,
                "is_field_goal": int(at in ("Made Shot", "Missed Shot")),
                "location": loc,
            }
        )

    def play(self) -> pl.DataFrame:
        rng = self.rng
        poss = int(rng.integers(2))
        last_shot_side = -1
        for period in (1, 2, 3, 4):
            self.add(period, 720.0, 0, 0, "period", "start", "Start")
            clock = 720.0
            while True:
                clock -= float(rng.integers(6, 28))
                if clock <= 4:
                    break
                u = rng.random()
                tm = self.teams[poss]
                if u < 0.05:
                    self.add(period, clock, 0, tm, "Timeout", "Regular", "Timeout")
                elif u < 0.17:
                    s = int(rng.integers(2))
                    out = int(rng.choice(self.on[s]))
                    bench = [p for p in self.rosters[s] if p not in self.on[s]]
                    inn = int(rng.choice(bench))
                    # a row naming the incoming player keeps the sub resolvable (name lookup)
                    self.add(
                        period, clock, self.teams[s], inn, "Violation", "Delay Of Game", "Violation"
                    )
                    self.add(
                        period,
                        clock,
                        self.teams[s],
                        out,
                        "Substitution",
                        "",
                        f"SUB: {_name(inn)} FOR {_name(out)}",
                    )
                    self.on[s][self.on[s].index(out)] = inn
                elif u < 0.24:
                    actor = int(rng.choice(self.on[poss]))
                    self.add(
                        period,
                        clock,
                        tm,
                        actor,
                        "Turnover",
                        "Bad Pass",
                        f"{_name(actor)} Bad Pass Turnover",
                    )
                    poss = 1 - poss
                elif u < 0.34:
                    d = 1 - poss
                    fouler = int(rng.choice(self.on[d]))
                    self.add(period, clock, self.teams[d], fouler, "Foul", "Shooting", "S.FOUL")
                    shooter = int(rng.choice(self.on[poss]))
                    last = True
                    for k in (1, 2):
                        made = rng.random() < 0.75
                        last = made
                        if made:
                            self.score[poss] += 1
                            self.box[shooter]["pts"] += 1
                        desc = f"{'' if made else 'MISS '}{_name(shooter)} Free Throw {k} of 2"
                        self.add(
                            period, clock, tm, shooter, "Free Throw", f"Free Throw {k} of 2", desc
                        )
                    last_shot_side = poss
                    poss = 1 - poss if last else self._rebound(period, clock, poss, last_shot_side)
                else:
                    actor = int(rng.choice(self.on[poss]))
                    three = rng.random() < 0.35
                    make = rng.random() < 0.45
                    sv = 3 if three else 2
                    dist = 25 if three else int(rng.integers(1, 16))
                    last_shot_side = poss
                    if make:
                        self.score[poss] += sv
                        self.box[actor]["pts"] += sv
                        desc = f"{_name(actor)} {dist}' Jump Shot ({self.box[actor]['pts']} PTS)"
                        if rng.random() < 0.6:
                            ast = int(rng.choice([p for p in self.on[poss] if p != actor]))
                            self.box[ast]["ast"] += 1
                            desc += f" ({_name(ast)} 1 AST)"
                        self.add(period, clock, tm, actor, "Made Shot", "Jump Shot", desc, sv, dist)
                        poss = 1 - poss
                    else:
                        self.add(
                            period,
                            clock,
                            tm,
                            actor,
                            "Missed Shot",
                            "Jump Shot",
                            f"MISS {_name(actor)} Jump Shot",
                            sv,
                            dist,
                        )
                        poss = self._rebound(period, clock, poss, last_shot_side)
            if period == 4 and self.score[0] == self.score[1]:
                self.score[0] += 2
                self.box[self.on[0][0]]["pts"] += 2
                self.add(
                    4,
                    2.0,
                    T1,
                    self.on[0][0],
                    "Made Shot",
                    "Jump Shot",
                    f"{_name(self.on[0][0])} 5' Jump Shot",
                    2,
                    5,
                )
            self.add(period, 0.0, 0, 0, "period", "end", "End")
        return pl.DataFrame(self.rows)

    def _rebound(self, period: int, clock: float, poss: int, shot_side: int) -> int:
        rng = self.rng
        side = shot_side if rng.random() < 0.25 else 1 - shot_side  # 25% offensive
        if rng.random() < 0.08:
            self.add(period, clock, 0, self.teams[side], "Rebound", "Unknown", "Team Rebound")
        else:
            p = int(rng.choice(self.on[side]))
            self.box[p]["reb"] += 1
            self.add(
                period,
                clock,
                self.teams[side],
                p,
                "Rebound",
                "Normal Rebound",
                f"{_name(p)} REBOUND",
            )
        return side


def make_synthetic(
    n22: int = 8, n23: int = 10, n24: int = 6
) -> tuple[pt.ExportInputs, dict[str, dict[str, Any]]]:
    games, pgs, pbps, truth = [], [], {}, {}
    d0 = date(2022, 11, 1)
    i = 0
    for season, n in ((2022, n22), (2023, n23), (2024, n24)):
        for k in range(n):
            gid = f"002{str(season)[2:]}{k + 1:05d}"
            home_first = i % 2 == 0
            h, a = (T1, T2) if home_first else (T2, T1)
            r_h, r_a = (list(R1), list(R2)) if home_first else (list(R2), list(R1))
            if season == 2024:
                r_h = [ROOKIE if p == 110 else p for p in r_h]
                r_a = [ROOKIE + 1 if p == 210 else p for p in r_a]
            gen = _Gen(gid, (r_h, r_a), seed=1000 + i, teams=(h, a))
            pbp = gen.play()
            # team ids in rows must follow this game's home/away assignment
            pbps[gid] = pbp
            dte = (
                d0 + timedelta(days=i)
                if season == 2022
                else date(season, 11, 1) + timedelta(days=k)
            )
            games.append(
                {
                    "game_id": gid,
                    "game_date": dte,
                    "season": season,
                    "home_team": h,
                    "away_team": a,
                    "home_pts": gen.score[0],
                    "away_pts": gen.score[1],
                }
            )
            for team, roster in ((h, r_h), (a, r_a)):
                for j, p in enumerate(roster):
                    b = gen.box[p]
                    pgs.append(
                        {
                            "game_id": gid,
                            "player_id": p,
                            "team_id": team,
                            "minutes": 20.0,
                            "starter": j < 5,
                            "pts": b["pts"],
                            "reb": b["reb"],
                            "ast": b["ast"],
                        }
                    )
            truth[gid] = {"box": gen.box, "score": tuple(gen.score)}
            i += 1
    g_df = pl.DataFrame(games).with_columns(pl.col("game_date").cast(pl.Date))
    extra = pl.DataFrame(
        {
            "game_id": g_df["game_id"],
            "p": [0.6] * g_df.height,
            "mu_margin": [1.0] * g_df.height,
            "sd_margin": [12.0] * g_df.height,
            "mu_total": [220.0] * g_df.height,
            "sd_total": [18.0] * g_df.height,
        }
    )
    avail = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "player_id": pl.Int64,
            "status": pl.Utf8,
            "as_of": pl.Datetime("us"),
            "source": pl.Utf8,
        }
    )
    return pt.ExportInputs(g_df, pl.DataFrame(pgs), avail, extra, lambda gid: pbps.get(gid)), truth


@pytest.fixture(scope="module")
def synth() -> tuple[pt.ExportInputs, dict[str, dict[str, Any]]]:
    return make_synthetic()


@pytest.fixture(scope="module")
def exported(
    tmp_path_factory: pytest.TempPathFactory, synth: tuple[pt.ExportInputs, dict[str, Any]]
) -> Path:
    out = tmp_path_factory.mktemp("pbp_export")
    pt.export_pbp_tokens(synth[0], out)
    return out


# ------------------------------------------------------------------ vocabulary / buckets


def test_vocab_layout_and_size() -> None:
    v = pt.Vocab([1, 2, 3], [T1, T2])
    assert v.size < 8000
    assert len(set(v.names)) == v.size
    assert all(v.evt_pay[i, 1] == 0 or v.evt_pay[i, 0] != 0 for i in range(v.n_evt))
    big = pt.Vocab(list(range(700)), list(range(30)))
    assert big.size <= 1200  # ~700 players + ~280 structure tokens
    j = v.to_json()
    assert pt.Vocab.from_json(j).size == v.size


@pytest.mark.parametrize("d", [-80, -36, -21, -20, -1, 0, 1, 20, 21, 26, 36, 90])
def test_score_bucket_range_and_monotone(d: int) -> None:
    b = pt.sc_bucket(d)
    assert -23 <= b <= 23
    assert pt.sc_bucket(d) <= pt.sc_bucket(d + 1)


def test_time_quantisation_error_stays_bounded() -> None:
    rng = np.random.default_rng(0)
    true = 720.0
    tclock = 720.0
    worst = 0.0
    for _ in range(400):
        true = max(true - float(rng.exponential(10.0)), 0.0)
        b = pt.quantise_dt(tclock, true)
        tclock = max(tclock - pt.DT_REP[b], 0.0)
        worst = max(worst, abs(tclock - true))
        if true <= 0:
            break
    assert worst <= 40.0  # never drifts by more than half the widest bucket gap


# ------------------------------------------------------------------ round trip


def _raw_totals(pbp: pl.DataFrame) -> dict[int, dict[str, int]]:
    box: dict[int, dict[str, int]] = {}
    for r in pbp.iter_rows(named=True):
        pid = int(r["player_id"])
        b = box.setdefault(pid, {"pts": 0, "reb": 0, "ast": 0})
        if r["action_type"] == "Made Shot":
            b["pts"] += int(r["shot_value"])
        elif r["action_type"] == "Free Throw" and not str(r["description"]).startswith("MISS"):
            b["pts"] += 1
        elif r["action_type"] == "Rebound" and r["team_id"] != 0 and pid not in (T1, T2):
            b["reb"] += 1
    return box


def test_fixture_game_round_trip_reconstructs_box_totals() -> None:
    pbp = load_pbp()["0022300001"]
    home, away = T1, T2
    events, _ = pt.extract_events(pbp, home, away)
    start_h = [101, 102, 103, 107, 108]
    start_a = [104, 105, 106, 109, 110]
    v = pt.Vocab([101, 102, 103, 104, 105, 106], [home, away])
    hdr = pt.GameHeader(
        "0022300001", home, away, False, 0.55, 1.0, -1.0, 1, 3, [], [], start_h, start_a, [], []
    )
    tg = pt.tokenize_game(v, hdr, events)
    assert tg.final["n_mismatch"] == 0
    det = pt.detokenize(v, tg.tokens, tg.header_len, tg.players)
    raw = _raw_totals(pbp)
    for pid, b in raw.items():
        if pid in (0, home, away):
            continue
        got = det["box"].get(pid, {"pts": 0, "reb": 0, "ast": 0})
        assert got["pts"] == b["pts"], pid
        assert got["reb"] == b["reb"], pid
    total = sum(b["pts"] for b in det["box"].values())
    assert total == det["score_h"] + det["score_a"]
    assert tg.tokens.dtype == np.int16 and len(tg.tokens) == len(tg.slots)


def test_synthetic_round_trip_box_and_score(
    synth: tuple[pt.ExportInputs, dict[str, Any]], exported: Path
) -> None:
    inp, truth = synth
    z = np.load(exported / "tokens.npz")
    games = pl.read_parquet(exported / "games.parquet")
    voc = pt.Vocab.from_json(json.loads((exported / "vocab.json").read_text()))
    assert games["ok"].all()
    assert games["score_match"].all()
    new_pids = np.load(exported / "ckpt.npz")["new_pids"]
    for gi in (0, 9, 23):
        gid = games["game_id"][gi]
        toks = z["tokens"][z["offsets"][gi] : z["offsets"][gi + 1]]
        gp = pt.GamePlayers(voc, [int(p) for p in new_pids[gi] if p >= 0])
        det = pt.detokenize(voc, toks, int(games["header_len"][gi]), gp)
        assert det["n_mismatch"] == 0 and det["done"]
        assert (det["score_h"], det["score_a"]) == truth[gid]["score"]
        for pid, b in det["box"].items():
            assert b == truth[gid]["box"][pid], (gid, pid)


def test_stream_parses_with_zero_mismatches_everywhere(exported: Path) -> None:
    z = np.load(exported / "tokens.npz")
    games = pl.read_parquet(exported / "games.parquet")
    voc = pt.Vocab.from_json(json.loads((exported / "vocab.json").read_text()))
    new_pids = np.load(exported / "ckpt.npz")["new_pids"]
    for gi in range(games.height):
        toks = z["tokens"][z["offsets"][gi] : z["offsets"][gi + 1]]
        gp = pt.GamePlayers(voc, [int(p) for p in new_pids[gi] if p >= 0])
        det = pt.detokenize(voc, toks, int(games["header_len"][gi]), gp)
        assert det["n_mismatch"] == 0
    rep = json.loads((exported / "export_report.json").read_text())
    assert rep["vocab_size"] < 8000 and rep["n_ok"] == rep["n_games"]
    assert rep["lineup_consistency"] > 0.999  # synthetic actors are always on court


def test_cold_start_players_map_to_unique_new_slots(exported: Path) -> None:
    voc = pt.Vocab.from_json(json.loads((exported / "vocab.json").read_text()))
    assert ROOKIE not in voc.player_tok and 101 in voc.player_tok
    gp = pt.GamePlayers(voc, [ROOKIE, ROOKIE + 1, 101])
    a, b = gp.tok(ROOKIE), gp.tok(ROOKIE + 1)
    assert voc.new0 <= a < voc.pl0 and voc.new0 <= b < voc.pl0 and a != b
    assert gp.pid_of(a) == ROOKIE and gp.pid_of(gp.tok(101)) == 101


def test_split_val_is_last_fifth_of_2023_and_2025_refused(
    synth: tuple[pt.ExportInputs, Any], tmp_path: Path
) -> None:
    inp, _ = synth
    sp = pt.split_of(inp.games)
    g23 = (
        inp.games.filter(pl.col("season") == 2023)
        .sort(["game_date", "game_id"])["game_id"]
        .to_list()
    )
    assert [sp[g] for g in g23] == ["train"] * 8 + ["val"] * 2
    assert all(sp[g] == "report" for g in inp.games.filter(pl.col("season") == 2024)["game_id"])
    bad = pt.ExportInputs(
        inp.games.with_columns(
            pl.when(pl.col("season") == 2024).then(2025).otherwise(pl.col("season")).alias("season")
        ),
        inp.pgs,
        inp.avail,
        inp.extra,
        inp.pbp_loader,
    )
    out = pt.export_pbp_tokens(bad, tmp_path)  # 2025 rows are simply never exported
    assert set(pl.read_parquet(tmp_path / "games.parquet")["season"]) <= {2022, 2023}
    assert out["by_split"]["report"] == 0


# ------------------------------------------------------------------ leakage


def _header_for(inp: pt.ExportInputs, gid: str, **mods: Any) -> tuple[pt.GameHeader, list[int]]:
    games = inp.games.sort(["game_date", "game_id"])
    hist = pt.AsOfHistory()
    by_game: dict[str, list[dict[str, Any]]] = {}
    for r in inp.pgs.iter_rows(named=True):
        by_game.setdefault(r["game_id"], []).append(dict(r))
    for g in games.iter_rows(named=True):
        rows = by_game[g["game_id"]]
        if g["game_id"] == gid:
            starters = {
                t: [r["player_id"] for r in rows if r["team_id"] == t and r["starter"]]
                for t in (g["home_team"], g["away_team"])
            }
            out_pids = mods.get("out", set())
            h = pt.build_header(hist, g, starters, out_pids, 0.6)
            v = pt.Vocab(list(range(101, 111)) + list(range(201, 211)), [T1, T2])
            gp = pt.GamePlayers(v, pt.header_pids(h))
            return h, pt.header_tokens(v, h, gp)
        # planted-future mods apply only to the game itself / later games
        hist.update(
            g["game_date"],
            g["home_team"],
            g["away_team"],
            float(g["home_pts"] - g["away_pts"]),
            rows,
        )
    raise AssertionError("game not found")


def test_header_ignores_own_box_and_future_games(synth: tuple[pt.ExportInputs, Any]) -> None:
    inp, _ = synth
    gid = "00223" + "00005"
    _, base = _header_for(inp, gid)
    # rewrite the game's own box score and every LATER game: header must not move
    own_later = inp.games.filter(pl.col("game_id") >= gid)["game_id"].to_list()
    pgs2 = inp.pgs.with_columns(
        pl.when(pl.col("game_id").is_in(own_later))
        .then(pl.col("pts") + 50)
        .otherwise(pl.col("pts"))
        .alias("pts"),
        pl.when(pl.col("game_id").is_in(own_later))
        .then(pl.col("minutes") + 9.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes"),
    )
    games2 = inp.games.with_columns(
        pl.when(pl.col("game_id").is_in(own_later))
        .then(150)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    inp2 = pt.ExportInputs(games2, pgs2, inp.avail, inp.extra, inp.pbp_loader)
    h1, base1 = _header_for(inp, gid)
    h2, mod = _header_for(inp2, gid)
    # the game's own home_pts is read by update() only AFTER header construction
    assert mod == base == base1
    assert h1.rat_h == h2.rat_h and h1.roster_h == h2.roster_h


def test_header_changes_with_past_game(synth: tuple[pt.ExportInputs, Any]) -> None:
    inp, _ = synth
    gid = "00223" + "00005"
    h1, _ = _header_for(inp, gid)
    past = "00223" + "00003"
    games2 = inp.games.with_columns(
        pl.when(pl.col("game_id") == past).then(60).otherwise(pl.col("home_pts")).alias("home_pts")
    )
    h2, _ = _header_for(pt.ExportInputs(games2, inp.pgs, inp.avail, inp.extra, inp.pbp_loader), gid)
    assert h1.rat_h != h2.rat_h or h1.rat_a != h2.rat_a  # sensitivity: past data does enter


def test_pretip_out_ignores_reports_after_tip(synth: tuple[pt.ExportInputs, Any]) -> None:
    inp, _ = synth
    gid = "00223" + "00005"
    g = inp.games.filter(pl.col("game_id") == gid).row(0, named=True)
    from research.sim.usage_redistribution import OFFICIAL_REPORT_SOURCE

    d = g["game_date"]
    from datetime import datetime

    early = datetime(d.year, d.month, d.day, 12, 0)
    late = datetime(d.year, d.month, d.day, 21, 0)  # after tip minus lead
    avail = pl.DataFrame(
        [
            {
                "game_id": gid,
                "player_id": 101,
                "status": "out",
                "as_of": early,
                "source": OFFICIAL_REPORT_SOURCE,
            },
            {
                "game_id": gid,
                "player_id": 102,
                "status": "out",
                "as_of": late,
                "source": OFFICIAL_REPORT_SOURCE,
            },
        ]
    )
    out_map, reported = pt._pretip_out(avail, inp.games)
    assert out_map.get(gid) == {101}
    assert gid in reported


def test_report_rows_stamped_after_tip_minus_lead_dropped_from_header_tokens(
    synth: tuple[pt.ExportInputs, Any],
) -> None:
    inp, _ = synth
    gid = "00223" + "00005"
    h_with, _ = _header_for(inp, gid, out={101})
    h_none, _ = _header_for(inp, gid)
    assert 101 in (h_with.out_h + h_with.out_a) and h_none.out_h == [] and h_none.out_a == []
    assert 101 not in h_with.roster_h + h_with.roster_a  # an OUT player leaves the rotation list


# ------------------------------------------------------------------ job (CPU smoke)


def _load_job() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pbp_gpt_job", JOB_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def job(exported: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    pytest.importorskip("torch")
    monkeypatch.setenv("NBA_BUDGET", "smoke")
    monkeypatch.setenv("NBA_PARQUET", str(exported / "tokens.npz"))
    m = _load_job()
    m.setup()
    m.load()
    return m


def _tiny_model(job: ModuleType, seed: int = 0) -> Any:
    cfg = dict(d_model=32, layers=2, heads=2, kv_heads=1, ff=2, dropout=0.0, init_seed=seed)
    return job.make_model(cfg, job.C.V.size, job.C.CTX).eval()


def test_kv_cache_matches_full_forward(job: ModuleType) -> None:
    torch = job.C.torch
    m = _tiny_model(job)
    g = torch.Generator().manual_seed(0)
    seqs = torch.randint(5, job.C.V.size, (2, 14), generator=g)
    plen = torch.tensor([8, 6])
    pad = seqs[:, :8].clone()
    pad[1, 6:] = 0
    S, L = 2, 4
    with torch.no_grad():
        full = [m(seqs[i : i + 1]) for i in range(2)]
        logits, kvs, hid = m(pad, return_kv=True)
        l0 = m.head(hid[torch.arange(2), plen - 1])
        for i in range(2):
            assert torch.allclose(l0[i], full[i][0, int(plen[i]) - 1], atol=1e-4)
        blk = m.blocks[0]
        Kn = [torch.zeros(2 * S, blk.hkv, L, blk.dh) for _ in m.blocks]
        Vn = [torch.zeros(2 * S, blk.hkv, L, blk.dh) for _ in m.blocks]
        for t in range(L):
            tok = torch.stack([seqs[0, 8 + t], seqs[0, 8 + t], seqs[1, 6 + t], seqs[1, 6 + t]])
            pos = torch.tensor([8 + t, 8 + t, 6 + t, 6 + t])
            out = m.step(tok, pos, t, kvs, Kn, Vn, 2, S, plen)
            # row 0/1 belong to game 0 (prefix 8 + its own new tokens), 2/3 to game 1
            assert torch.allclose(out[0], full[0][0, 8 + t], atol=1e-4)
            assert torch.allclose(out[1], full[0][0, 8 + t], atol=1e-4)
            assert torch.allclose(out[2], full[1][0, 6 + t], atol=1e-4)


def _ckpt_inputs(
    job: ModuleType, gi: int, c: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    a = int(job.C.offsets[gi])
    pos = int(job.C.ck["scal"][gi, c, 1])
    return (
        job.C.tokens[a : a + pos].astype(np.int64),
        job.C.ck["scal"][gi, c][None],
        job.C.ck["rost"][gi, c][None],
        job.C.ck["on"][gi, c][None],
    )


@pytest.mark.parametrize("c", [0, 1, 3, 4])
def test_vectorised_machine_reproduces_forced_tokens_and_state(job: ModuleType, c: int) -> None:
    torch = job.C.torch
    gi = int(job.C.idx["report"][0])
    prefix, scal, rost, on = _ckpt_inputs(job, gi, c)
    a, b = int(job.C.offsets[gi]), int(job.C.offsets[gi + 1])
    rest = job.C.tokens[a + len(prefix) : b].astype(np.int64)
    m = _tiny_model(job)
    T = job.get_tables()
    forced = torch.tensor(rest, dtype=torch.long)[None]
    gen = torch.Generator().manual_seed(0)
    r = job.sample_units(m, T, [prefix], scal, rost, on, 1, len(rest), gen, forced=forced)
    got = r["tokens"][0][: len(rest)]
    assert (got == rest).all(), np.nonzero(got != rest)[0][:5]
    games = job.C.games
    assert int(r["margin"][0, 0]) == int(games["tok_home_pts"][gi] - games["tok_away_pts"][gi])
    # per-player continuation stats == final box minus box at the checkpoint (roster slots)
    fin = job.C.ck["final"][gi, c].astype(int)
    box = job.C.ck["box"][gi, c].astype(int)
    known = job.C.ck["rost"][gi, c] > 0  # slots present at the checkpoint (new entrants excluded)
    got = r["pstat"][0, 0].astype(int)
    assert (got[known] == (fin - box)[known]).all()


def test_sampled_games_parse_with_zero_mismatches(job: ModuleType) -> None:
    torch = job.C.torch
    gi = int(job.C.idx["report"][1])
    prefix, scal, rost, on = _ckpt_inputs(job, gi, 0)
    voc = pt.Vocab.from_json(job.C.vocab)
    hl = int(job.C.games["header_len"][gi])
    h = [int(t) for t in prefix[:hl]]
    s_h, s_a, r_h = (
        h.index(voc.tok("M_START_H")),
        h.index(voc.tok("M_START_A")),
        h.index(voc.tok("M_ROST_H")),
    )
    m = _tiny_model(job, 3)
    gen = torch.Generator().manual_seed(1)
    r = job.sample_units(m, job.get_tables(), [prefix], scal, rost, on, 6, 700, gen)
    n_done = 0
    for s in range(6):
        st = pt.StreamState(voc, h[s_h + 1 : s_a], h[s_a + 1 : r_h])
        for t in prefix[hl:]:
            st.feed(int(t))
        for t in r["tokens"][s]:
            if int(t) == voc.tok("PAD"):
                break
            st.feed(int(t))
        assert st.n_mismatch == 0
        assert st.n_corr == 0  # sampled actors are always on court / on the bench
        if r["done"][0, s]:
            n_done += 1
            assert st.done
            assert (st.score[0] - st.score[1]) == int(r["margin"][0, s])
    assert r["dead"].mean() < 1.0
    assert n_done >= 0


def test_job_refuses_season_2025(
    exported: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("torch")
    d = tmp_path / "bad"
    d.mkdir()
    for f in ("tokens.npz", "ckpt.npz", "vocab.json"):
        (d / f).write_bytes((exported / f).read_bytes())
    g = pl.read_parquet(exported / "games.parquet").with_columns(pl.lit(2025).alias("season"))
    g.write_parquet(d / "games.parquet")
    monkeypatch.setenv("NBA_BUDGET", "smoke")
    monkeypatch.setenv("NBA_PARQUET", str(d / "tokens.npz"))
    m = _load_job()
    m.setup()
    with pytest.raises(ValueError, match="2025"):
        m.load()


def _run_main(
    job_mod: ModuleType, exported: Path, root: Path, mp: pytest.MonkeyPatch, crash: str | None
) -> None:
    mp.setenv("NBA_BUDGET", "smoke")
    mp.setenv("NBA_PARQUET", str(exported / "tokens.npz"))
    mp.setenv("NBA_ARTIFACT_ROOT", str(root / "artifacts"))
    if crash:
        mp.setenv("NBA_FORCE_CRASH_AT", crash)
    else:
        mp.delenv("NBA_FORCE_CRASH_AT", raising=False)
    job_mod.main()


def test_smoke_run_writes_artifacts_and_resumes_after_forced_crash(
    exported: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest.importorskip("torch")
    m = _load_job()
    with pytest.raises(m.ForcedCrash):
        _run_main(m, exported, tmp_path, monkeypatch, "epoch_done=2")
    ck = next((tmp_path / "checkpoints").glob("*"))
    assert (ck / "trial_0" / "last.pt").exists()
    with pytest.raises(m.ForcedCrash):  # second crash: inside sampling, after a chunk is written
        _run_main(m, exported, tmp_path, monkeypatch, "chunk_done=1")
    assert any((ck / "samples").glob("c*_*.npz"))
    capsys.readouterr()
    _run_main(m, exported, tmp_path, monkeypatch, None)
    out = capsys.readouterr().out
    assert "RESUMED" in out
    art = sorted((tmp_path / "artifacts").glob("*"))[-1]
    for f in (
        "metrics.json",
        "best_config.json",
        "search_log.json",
        "training_log.json",
        "data_summary.json",
        "weights.pt",
        "ppl_by_game.parquet",
        "samples_tip.npz",
        "samples_q2.npz",
        "samples_q3.npz",
        "samples_q4.npz",
        "samples_q4_5min.npz",
    ):
        assert (art / f).exists(), f
    met = json.loads((art / "metrics.json").read_text())
    assert met["complete"] and not met["season_2025_loaded"] and not met["touches_holdout"]
    assert np.isfinite(met["nll"]["report"]["nll"]) and met["sampling"]["rows_per_s"] > 0
    z = np.load(art / "samples_q2.npz")
    assert z["margin"].shape[0] == 2 and z["pstat"].shape[2:] == (2, pt.RM, 3)
    ppl = pl.read_parquet(art / "ppl_by_game.parquet")
    assert set(ppl["split"]) == {"val", "report"}
    assert not (art / "metrics_partial.json").exists()


# ------------------------------------------------------------------ evaluator


def test_eval_module_primitives(exported: Path, tmp_path: Path) -> None:
    from research.eval import pbp_gpt_eval as ev

    # Benjamini-Hochberg
    adj = ev.bh_adjust([0.001, 0.02, 0.5, 0.04])
    assert adj[0] <= adj[1] <= adj[3] <= adj[2] + 1e-12
    assert all(0 <= a <= 1 for a in adj)
    # discrete CRPS vs closed form for a point mass
    assert ev.crps_samples(np.array([3, 3, 3]), 3) == 0.0
    assert ev.crps_samples(np.array([0, 0, 0, 0]), 2) == pytest.approx(2.0)
    # ECE perfect and bad
    y = np.array([1, 0] * 50)
    assert ev.ece(np.full(100, 0.5), y) == pytest.approx(0.0)
    assert ev.ece(np.full(100, 0.9), y) == pytest.approx(0.4)
    # paired clustered bootstrap
    rng = np.random.default_rng(0)
    d = rng.normal(-0.1, 0.05, 200)
    lo, hi, p = ev.cluster_bootstrap_mean(d, np.arange(200), n_boot=400, seed=1)
    assert hi < 0 and p < 0.01
    # n-gram baselines run on exported tokens and return per-game category sums
    z = np.load(exported / "tokens.npz")
    games = pl.read_parquet(exported / "games.parquet")
    voc = json.loads((exported / "vocab.json").read_text())
    tr = np.where(games["split"].to_numpy() == "train")[0]
    va = np.where(games["split"].to_numpy() == "val")[0]
    rp = np.where(games["split"].to_numpy() == "report")[0]
    res = ev.ngram_baselines(z["tokens"], z["slots"], z["offsets"], voc, tr, va, rp)
    assert set(res) == {"unigram", "trigram"}
    s_uni, n_uni = res["unigram"]
    s_tri, n_tri = res["trigram"]
    assert s_tri.shape == (len(rp), len(ev.CATS))
    assert np.isfinite(s_tri).all() and (n_tri == n_uni).all()
    # an n-gram model with context beats the slot-unigram on structured synthetic play
    assert s_tri.sum() / n_tri.sum() < s_uni.sum() / n_uni.sum()


def test_live_baseline_and_keep_rule_logic() -> None:
    from research.eval import pbp_gpt_eval as ev

    rng = np.random.default_rng(0)
    n = 400
    diff = rng.normal(0, 10, n)
    tr_min = rng.uniform(5, 36, n)
    lp = rng.normal(0, 0.7, n)
    z = diff / np.sqrt(tr_min + 0.5) + 0.5 * lp * np.sqrt(tr_min / 48)
    y = (rng.random(n) < 1 / (1 + np.exp(-1.6 * z))).astype(int)
    f = ev.live_features(diff, tr_min, lp)
    w = ev.fit_live_baseline(f, y)
    p = ev.predict_live_baseline(w, f)
    assert p.min() > 0 and p.max() < 1
    assert ev.log_loss(p, y) < ev.log_loss(np.full(n, y.mean()), y)
    # keep rule: 4 checkpoints, only strong improvement + calibration passes
    rows = [
        dict(ckpt="q2", delta=-0.02, lo=-0.03, hi=-0.01, p_adj=0.01, ece_diff=0.0),
        dict(ckpt="q3", delta=-0.001, lo=-0.01, hi=0.008, p_adj=0.6, ece_diff=0.0),
        dict(ckpt="q4", delta=0.002, lo=-0.01, hi=0.01, p_adj=0.7, ece_diff=0.0),
        dict(ckpt="q4_5min", delta=0.0, lo=-0.01, hi=0.01, p_adj=0.9, ece_diff=0.0),
    ]
    v = ev.live_keep_verdict(rows)
    assert v["kept"] and v["passing"] == ["q2"]
    rows[0]["ece_diff"] = 0.05
    assert not ev.live_keep_verdict(rows)["kept"]
    rows[0]["ece_diff"] = 0.0
    rows[1].update(delta=0.05, lo=0.03, hi=0.07, p_adj=0.001)
    assert not ev.live_keep_verdict(rows)["kept"]  # a significantly WORSE checkpoint vetoes


# ------------------------------------------------------------------ checkpoints and learning


def test_checkpoint_states_match_a_replay_of_their_prefix(exported: Path) -> None:
    z = np.load(exported / "tokens.npz")
    ck = np.load(exported / "ckpt.npz")
    games = pl.read_parquet(exported / "games.parquet")
    voc = pt.Vocab.from_json(json.loads((exported / "vocab.json").read_text()))
    go = voc.tok("GO")
    for gi in (0, 12, 20, 23):
        toks = z["tokens"][z["offsets"][gi] : z["offsets"][gi + 1]].astype(int)
        hl = int(games["header_len"][gi])
        assert toks[hl - 1] == go
        h = toks[:hl].tolist()
        s_h, s_a, r_h = (h.index(voc.tok(n)) for n in ("M_START_H", "M_START_A", "M_ROST_H"))
        for c in range(pt.N_CKPT):
            sc = ck["scal"][gi, c]
            assert sc[0] == 1
            pos = int(sc[1])
            st = pt.StreamState(voc, h[s_h + 1 : s_a], h[s_a + 1 : r_h])
            for t in toks[hl:pos]:
                st.feed(int(t))
            assert not st.pending and st.n_mismatch == 0
            assert (st.score[0], st.score[1]) == (int(sc[2]), int(sc[3]))
            assert (st.period, st.tclock, st.grid, st.lb) == (
                int(sc[4]),
                sc[5],
                int(sc[6]),
                int(sc[7]),
            )
            assert (st.fouls[0], st.fouls[1]) == (int(sc[8]), int(sc[9]))
            rost = ck["rost"][gi, c]
            on = ck["on"][gi, c]
            for side in (0, 1):
                assert sorted(int(t) for t in rost[side][on[side]]) == sorted(st.onc[side])
    # the tip prefix is header + the initial state block only: no event token has happened yet
    pos0 = ck["scal"][:, 0, 1].astype(int)
    assert (pos0 - games["header_len"].to_numpy() == 16).all()
    for gi in range(games.height):
        body = z["tokens"][z["offsets"][gi] + games["header_len"][gi] : z["offsets"][gi] + pos0[gi]]
        assert not ((body >= voc.dt0) & (body < voc.sc0)).any()
        assert not ((body >= voc.evt0) & (body < voc.new0)).any()


def test_model_learns_the_synthetic_stream(job: ModuleType, tmp_path: Path) -> None:
    job.C.B = dict(job.C.B, trials=1, max_epochs=14, patience=14, batch=2)
    ck = tmp_path / "ck"
    ck.mkdir()
    res = job.train_trial(0, job.trial_list()[0], ck)
    first, best = res["history"][0]["val"], res["best_val"]
    assert np.isfinite(best) and best < first - 0.15
    # the learned-token NLL beats the uniform-over-vocabulary level by a wide margin
    assert best < 0.6 * np.log(job.C.V.size)


def test_evaluator_end_to_end_on_smoke_run(
    exported: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("torch")
    from research.eval import pbp_gpt_eval as ev

    m = _load_job()
    _run_main(m, exported, tmp_path, monkeypatch, None)
    art = sorted((tmp_path / "artifacts").glob("*"))[-1]
    res = ev.evaluate_run(art, exported)
    assert res["G0_perplexity"]["n_games"] == 6
    assert np.isfinite(res["G0_perplexity"]["trigram"]["gpt_minus_base"])
    rows = res["P1_live_wp"]["rows"]
    assert [r["ckpt"] for r in rows] == list(ev.LIVE)
    assert all(np.isfinite(r["ll_gpt"]) and np.isfinite(r["ll_base"]) for r in rows)
    assert all(0 <= r["p_adj"] <= 1 for r in rows)
    assert res["P1_live_wp"]["verdict"] is not None
    assert len(res["P2_pregame"]["rows"]) == 3
    assert len(res["P3_player_lines"]["cells"]) > 0
    md = ev.render_report(res)
    assert "P1 live win probability" in md and "G0" in md
    # 2025 is refused
    g = pl.read_parquet(exported / "games.parquet").with_columns(pl.lit(2025).alias("season"))
    d = tmp_path / "bad_export"
    d.mkdir()
    for f in ("tokens.npz", "ckpt.npz", "vocab.json"):
        (d / f).write_bytes((exported / f).read_bytes())
    g.write_parquet(d / "games.parquet")
    with pytest.raises(ValueError, match="2025"):
        ev.evaluate_run(art, d)
