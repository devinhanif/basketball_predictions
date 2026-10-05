#!/usr/bin/env python3
"""
team.py - a pseudo-team of Claude Code agents with usage guardrails.

Run from inside your project git repo (one commit required), next to CLAUDE.md.

  python team.py init                 create agents, config, tasks, worktrees
  python team.py status               zero-token dashboard (tasks, budget, escalations)
  python team.py run --wave 1         run ready tasks of wave 1 (bounded parallelism)
  python team.py run --task T03       run one task
  python team.py run --ready          run everything whose dependencies are done
  python team.py budget               usage ledger summary (self-tracked, approximate)
  python team.py report [--polish]    write reports/<date>.md (polish = 1 cheap LLM call)
  python team.py merge-plan           show each agent branch and suggested merge order
  python team.py inbox                show questions agents escalated to you
  python team.py lead                 interactive session with the team lead
  python team.py pause | resume       kill switch (checked before every launch)
  python team.py retry T03            re-queue a failed task

Design rules (because Claude Code / claude.ai / Desktop share ONE usage pool):
  * Parallelism makes work FASTER, not CHEAPER. Default max_parallel = 2.
  * Each task is a bounded headless run (--max-turns) that commits and exits.
  * Cheap models by default (see AGENTS). Use opus only on purpose: --model opus.
  * Self-imposed caps live in team/config.json. /usage inside Claude Code is the
    source of truth for your real limits; this script only estimates.
  * Agents work in separate git worktrees on their own branches. They never push
    or merge. You (or the lead) merge, sequentially, with `merge-plan`.
  * No agent can place market orders or handle trading credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths (set in main once the repo root is known)
# --------------------------------------------------------------------------- #
ROOT: Path
TEAM: Path
STATUS_DIR: Path
WORKTREES: Path
LEDGER: Path
STATE: Path
PAUSE: Path
ESCALATIONS: Path
CONFIG_PATH: Path
TASKS_PATH: Path
REPORTS: Path
AGENTS_DIR: Path

LOCK = threading.Lock()

# --------------------------------------------------------------------------- #
# Team definition
# --------------------------------------------------------------------------- #
BUILDER_TOOLS = "Read, Write, Edit, Bash, Glob, Grep"
REVIEW_TOOLS = "Read, Write, Glob, Grep, Bash"

# allow-list used in headless runs (patterns follow Claude Code permission syntax)
ALLOW_BUILDER = [
    "Read", "Write", "Edit", "Glob", "Grep",
    "Bash(git status:*)", "Bash(git add:*)", "Bash(git commit:*)", "Bash(git diff:*)",
    "Bash(git log:*)", "Bash(git branch:*)",
    "Bash(python:*)", "Bash(uv:*)", "Bash(pytest:*)", "Bash(ruff:*)", "Bash(mypy:*)",
    "Bash(make:*)", "Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)",
    "Bash(wc:*)", "Bash(mkdir:*)", "Bash(duckdb:*)",
]
ALLOW_REVIEW = [
    "Read", "Write", "Glob", "Grep",
    "Bash(git log:*)", "Bash(git diff:*)", "Bash(git status:*)", "Bash(pytest:*)",
    "Bash(ls:*)", "Bash(wc:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(python:*)",
]
DENY_ALWAYS = [
    "Bash(git push:*)", "Bash(git merge:*)", "Bash(git rebase:*)", "Bash(git reset:*)",
    "Bash(git checkout:*)", "Bash(rm -rf:*)", "Bash(sudo:*)", "Bash(curl:*)",
    "Bash(wget:*)", "Bash(gh:*)", "Bash(ssh:*)", "Bash(pip install:*)",
]

COMMON_RULES = """\
## Operating rules (all agents)
1. You are a member of a small engineering team. The manager (a human) reads your
   status file, not your chat. You run headless: you cannot ask questions interactively.
2. The project spec is CLAUDE.md in your working directory. Read only the sections you
   need (use Grep / targeted reads). Do not re-read files that have not changed.
3. Work only inside the paths you own (listed below). If you need something changed
   elsewhere, append a request to the escalations file and carry on with what you can.
4. USAGE DISCIPLINE (the team shares a limited subscription pool):
   - keep tool output small: pipe long output through `tail -n 40` or `head -n 40`;
   - never print whole data files or logs; sample them;
   - prefer one well-aimed test run over many exploratory runs;
   - stop when the task's acceptance criteria are met. Do not gold-plate.
5. Quality bar: write tests with the code. Run the relevant tests before you finish.
   If a metric looks too good, assume leakage and audit the as-of logic first.
6. Git: commit small, meaningful commits on your current branch. NEVER push, merge,
   rebase, force, or switch branches.
7. Safety: never write code that places orders or stores/handles trading credentials.
   Never read, copy, or reference any employer (work) code, data, table names, or configs.
   Never commit secrets, parquet files, or the DuckDB file.
8. Before finishing, overwrite your status file (path given in the task) using exactly:
   # <agent> status
   Updated: <ISO timestamp>
   ## Done (this run)
   ## In progress
   ## Blocked / needs manager decision
   ## Key numbers   (always include sample size n and an uncertainty band)
   ## Next
   Your final chat message must be under 120 words.
9. If you are blocked on a decision only the manager can make, append one line to the
   escalations file: `- [<agent>] <question> | options: A / B | default I will use: X`
   and proceed with the stated default if it is safe and reversible.
"""

AGENTS = {
    "lead": {
        "model": "sonnet",
        "tools": "Read, Write, Edit, Bash, Glob, Grep, Task",
        "desc": ("Tech lead and chief of staff. Use for planning, prioritising, delegating to "
                 "specialist agents, reviewing their branches, and reporting to the manager."),
        "owns": ["team/", "reports/", "docs/"],
        "body": """\
# Role: Tech Lead / chief of staff
You report to the manager (the human). You translate their goals into tasks for the team,
keep the work inside the usage budget, and give plain-language reports with numbers.

Responsibilities
- Read CLAUDE.md, `python team.py status`, and agent status files before planning.
- Prefer queuing bounded tasks through `team/tasks.json` + `python team.py run ...` over
  spawning many in-session subagents; in-session subagents burn the same shared pool.
- Spawn at most 2 subagents at a time, and only for independent work.
- Before approving a merge, check: tests pass, no leakage risk, no scope creep.
- Report format for the manager: what shipped, what is blocked and needs their decision,
  key metrics WITH uncertainty, budget used vs. remaining, and the next proposed wave.
- Push back honestly: if a result is not statistically distinguishable from a baseline,
  say so. If the data does not support a claim, say so.
""",
    },
    "devops-ci": {
        "model": "sonnet",
        "tools": BUILDER_TOOLS,
        "desc": ("Builds repo scaffolding, CI/CD, Docker, Makefile, pre-commit, and the local "
                 "model registry adapter. Use for anything about pipelines, tests infra, releases."),
        "owns": [".github/", "Makefile", "Dockerfile", "pyproject.toml", ".pre-commit-config.yaml",
                 "nba/registry/", "tests/registry/", "docs/"],
        "body": """\
# Role: DevOps / CI-CD and registry engineer
Deliver a reproducible, tested, shippable pipeline: lint+type (ruff, mypy), pytest with a
coverage ratchet, data-contract tests, smoke backtest on a tiny fixture, model-promotion
gate, Docker build, tagged release, nightly job. Implement the local registry adapter
following the "Registry retrofit" section of CLAUDE.md (generic patterns only).
Keep CI fast and free-tier friendly. Pin dependencies with a lockfile.
""",
    },
    "data-engineer": {
        "model": "sonnet",
        "tools": BUILDER_TOOLS,
        "desc": ("Owns data ingestion, DuckDB schema, play-by-play parsing into possessions and "
                 "stints, and fixture datasets. Use for nba_api pulls and data-quality work."),
        "owns": ["nba/ingest/", "nba/parse/", "nba/db/", "tests/data/", "tests/fixtures/"],
        "body": """\
# Role: Data engineer
Build resumable, cached, rate-limited ingestion from nba_api into DuckDB following the
schema in CLAUDE.md. Build the possession/stint parser and prove it with reconciliation
tests (possessions vs FGA + 0.44*FTA + TOV - OREB; mean error <= 1 per team-game).
Create a tiny committed fixture dataset (a few games) so CI never hits the network.
Never refetch cached data. Treat the parser as the highest-risk component: validate hard.
""",
    },
    "ml-engineer": {
        "model": "sonnet",
        "tools": BUILDER_TOOLS,
        "desc": ("Builds as-of features, baseline and GBM models, the walk-forward backtest "
                 "harness, and cold-start methods. Use for model training and evaluation."),
        "owns": ["nba/features/", "nba/models/", "nba/coldstart/", "nba/eval/", "nba/sim/",
                 "tests/ml/", "configs/"],
        "body": """\
# Role: ML engineer
Implement the architecture ladder (rungs 0-2 first) with strictly as-of features and
walk-forward validation, a frozen final holdout season that is never tuned on, and paired
per-game bootstrap comparisons. Implement cold-start methods 1-4 (shrinkage, archetypes,
rookie priors, carryover) and report metrics split by cold-start bucket.
Every rung must implement the 3-method contract: predict(), get_metrics(), get_config().
Report log loss, Brier, calibration, with confidence intervals. Do not start rung 3+
until rungs 0-2 are logged and the manager has approved.
""",
    },
    "props-modeler": {
        "model": "sonnet",
        "tools": BUILDER_TOOLS,
        "desc": ("Builds minutes models and per-player stat distributions for points, rebounds, "
                 "assists, threes and combos, with calibration and conformal intervals."),
        "owns": ["nba/props/", "tests/props/"],
        "body": """\
# Role: Props modeler
Phase 2 of CLAUDE.md. Model minutes first (including DNP risk), then stat distributions via
frequency-severity decomposition (Normal/Gamma for points; Negative Binomial for rebounds,
assists, threes). Add role-change detection, hierarchical coherence with team totals, and
split-conformal intervals. Success = mean bias within +/-0.5 (bootstrap CI includes 0),
calibrated P(stat >= N), and beating season-average / last-10 baselines on CRPS and log
loss. Never claim single-game accuracy within 0.5; it is not achievable.
""",
    },
    "markets-engineer": {
        "model": "sonnet",
        "tools": "Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch",
        "desc": ("Builds the READ-ONLY Kalshi ingestor, joint-probability (copula) engine, EV "
                 "calculator with configurable fees, and the paper-trade log."),
        "owns": ["nba/kalshi/", "nba/parlay/", "tests/kalshi/", "tests/parlay/"],
        "body": """\
# Role: Markets engineer (read-only)
Phase 3 of CLAUDE.md. Web access is for reading public API documentation only (docs.kalshi.com).
- Ingest public market data only. Use live endpoints for the recent window and /historical
  endpoints for older settled markets; call the cutoff endpoint to know which tier to use.
- No authentication scopes, no order endpoints, no credentials, ever.
- Tests use recorded JSON fixtures, never the live network.
- Fee model is a config object; never hardcode a fee rate. Include bid/ask spread in EV.
- Output includes probability intervals and a first-class `no_positive_ev_found` verdict.
- Paper-trade logging only. State sample sizes with every comparison.
""",
    },
    "qa-reviewer": {
        "model": "sonnet",
        "tools": REVIEW_TOOLS,
        "desc": ("Independent reviewer: audits leakage, statistical validity, test quality, and "
                 "scope creep. Reviews only; does not modify source code."),
        "owns": [".team/status/qa-reviewer.md", "docs/reviews/"],
        "body": """\
# Role: QA / statistical reviewer (independent)
You do not write feature code. You audit other agents' branches (use git diff/log against the
base branch) and write findings to docs/reviews/<task>.md.
Checklist: as-of leakage; any use of actual lineups/minutes at prediction time; test-set
reuse or tuning on the frozen holdout; claims without sample size or CI; missing negative
tests; flaky or network-dependent tests; metric definitions; calibration method; fee math.
Severity-rank findings (blocker / major / minor) and say plainly what you could not verify.
""",
    },
}

# --------------------------------------------------------------------------- #
# Default backlog (editable in team/tasks.json after init)
# --------------------------------------------------------------------------- #
DEFAULT_TASKS = [
    {"id": "T01", "agent": "devops-ci", "wave": 1, "depends_on": [], "max_turns": 30,
     "prompt": "Scaffold the repo per CLAUDE.md 'Repo layout' and 'CI/CD': pyproject + lockfile, "
               "Makefile (setup test lint backtest smoke report), ruff+mypy config, pre-commit "
               "(ruff, mypy, gitleaks, large-file block), Dockerfile (CPU), .gitignore, and a "
               "GitHub Actions workflow with lint-type and unit jobs that pass on the empty package."},
    {"id": "T02", "agent": "data-engineer", "wave": 1, "depends_on": [], "max_turns": 35,
     "prompt": "Create nba/db/schema.sql from CLAUDE.md 'Data schema' and a resumable, cached, "
               "rate-limited nba_api ingestor for games, box scores, and play-by-play into DuckDB. "
               "Add a script that builds a tiny committed fixture dataset (3-5 games). Tests must "
               "not use the network."},
    {"id": "T03", "agent": "data-engineer", "wave": 2, "depends_on": ["T01", "T02"], "max_turns": 40,
     "prompt": "Implement the play-by-play to possessions/stints parser with reconciliation tests "
               "(mean possession error <= 1 per team-game on the fixture). Document known edge cases."},
    {"id": "T04", "agent": "devops-ci", "wave": 2, "depends_on": ["T01"], "max_turns": 30,
     "prompt": "Implement the local RegistryAdapter (CLAUDE.md 'Registry retrofit'): next_version, "
               "log_model, set_alias, promote (old production -> deprecated), load_model, backed by "
               "the DuckDB experiments table plus ./registry_store. Add tests, including promotion "
               "and deprecation behavior."},
    {"id": "T05", "agent": "ml-engineer", "wave": 2, "depends_on": ["T02"], "max_turns": 40,
     "prompt": "Implement as-of feature builders (SQL-first), rungs 0-2 (baselines, logistic, "
               "LightGBM) with the predict/get_metrics/get_config contract, a walk-forward backtest "
               "harness with paired bootstrap comparisons, and a frozen final holdout season. "
               "Emit report.md with log loss, Brier, calibration, and CIs."},
    {"id": "T06", "agent": "ml-engineer", "wave": 3, "depends_on": ["T05"], "max_turns": 35,
     "prompt": "Implement cold-start methods 1-4 from CLAUDE.md (shrinkage, archetype priors, "
               "rookie priors, carryover) behind flags, with property tests for shrinkage limits, "
               "and report metrics split by cold-start bucket."},
    {"id": "T07", "agent": "qa-reviewer", "wave": 3, "depends_on": ["T03", "T05"], "max_turns": 25,
     "prompt": "Review branches agent/data-engineer and agent/ml-engineer against the 'Risks and "
               "guardrails' section. Write docs/reviews/T07.md with severity-ranked findings."},
    {"id": "T08", "agent": "devops-ci", "wave": 3, "depends_on": ["T04", "T05"], "max_turns": 35,
     "prompt": "Extend CI: data-contract job (schema + no-leakage + possession reconciliation on "
               "fixtures), smoke-backtest job (<3 min), model-gate job comparing candidate vs "
               "production in the registry, Docker build, release on tag, nightly workflow."},
    {"id": "T09", "agent": "markets-engineer", "wave": 3, "depends_on": ["T01", "T02"], "max_turns": 35,
     "prompt": "Build the READ-ONLY Kalshi ingestor (live + historical tiers, cutoff handling, "
               "NBA prop series, 'N+' threshold parsing, reviewed player-name alias table) with "
               "recorded-fixture tests. No auth, no order endpoints."},
    {"id": "T10", "agent": "props-modeler", "wave": 4, "depends_on": ["T05", "T06"], "max_turns": 40,
     "prompt": "Implement the minutes model and stat distributions for points, rebounds, assists, "
               "threes (CLAUDE.md Phase 2) with calibration reporting, mean-bias CIs, and baselines."},
    {"id": "T11", "agent": "props-modeler", "wave": 5, "depends_on": ["T10"], "max_turns": 35,
     "prompt": "Add combos (PRA etc.), hierarchical coherence with team totals, role-change "
               "detection, and split-conformal intervals; report coverage of the 80% interval."},
    {"id": "T12", "agent": "markets-engineer", "wave": 5, "depends_on": ["T09", "T10"], "max_turns": 40,
     "prompt": "Implement the joint-probability engine (independence baseline, then Gaussian copula), "
               "the EV calculator with configurable fee model and bid/ask spread, probability "
               "intervals, the no_positive_ev_found verdict, and the paper_trades log with "
               "auto-settlement."},
    {"id": "T13", "agent": "qa-reviewer", "wave": 5, "depends_on": ["T11", "T12"], "max_turns": 25,
     "prompt": "Review props-modeler and markets-engineer branches: calibration method, copula "
               "validity, fee math, sample-size claims, read-only guarantees. Write docs/reviews/T13.md."},
]

DEFAULT_CONFIG = {
    "max_parallel": 2,
    "default_max_turns": 30,
    "task_timeout_minutes": 45,
    "daily_run_cap": 8,
    # Self-imposed weekly soft cap in weighted tokens. null = not enforced.
    # Read /usage in Claude Code, decide how much of the week this project may use, set it here.
    "weekly_weighted_token_cap": None,
    "reserve_pct": 25,           # keep this % of the cap free for your own chat/coding
    "halt_if_single_run_exceeds": None,   # circuit breaker, weighted tokens
    "cache_read_weight": 0.1,    # cached reads are much cheaper than fresh tokens
    "model_overrides": {},       # e.g. {"qa-reviewer": "opus"}
}

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def say(msg: str):
    with LOCK:
        print(msg, flush=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def git(args, cwd=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd or ROOT, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)


def config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_PATH, {}))
    return cfg


def tasks() -> list:
    return load_json(TASKS_PATH, DEFAULT_TASKS)


def state() -> dict:
    return load_json(STATE, {"tasks": {}})


def task_status(st: dict, tid: str) -> str:
    return st["tasks"].get(tid, {}).get("status", "pending")


def agent_model(cfg: dict, agent: str, cli_model: str | None) -> str:
    return cli_model or cfg.get("model_overrides", {}).get(agent) or AGENTS[agent]["model"]


def default_branch() -> str:
    r = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().split("/", 1)[-1]
    for name in ("main", "master"):
        if git(["rev-parse", "--verify", name], check=False).returncode == 0:
            return name
    return "HEAD"


# --------------------------------------------------------------------------- #
# Ledger / budget
# --------------------------------------------------------------------------- #

def ledger_entries(days: int | None = None) -> list:
    if not LEDGER.exists():
        return []
    cutoff = now_utc() - timedelta(days=days) if days else None
    out = []
    for line in LEDGER.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if cutoff and datetime.fromisoformat(e["ts"]) < cutoff:
            continue
        out.append(e)
    return out


def weighted(usage: dict, cache_read_weight: float) -> float:
    return (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
            + cache_read_weight * usage.get("cache_read_input_tokens", 0))


def budget_summary(cfg: dict) -> dict:
    week = ledger_entries(7)
    day = ledger_entries(1)
    used = sum(e.get("weighted", 0) for e in week)
    cap = cfg.get("weekly_weighted_token_cap")
    usable = cap * (1 - cfg["reserve_pct"] / 100) if cap else None
    by_agent: dict = {}
    for e in week:
        a = by_agent.setdefault(e["agent"], {"runs": 0, "weighted": 0.0})
        a["runs"] += 1
        a["weighted"] += e.get("weighted", 0)
    return {"week_used": used, "week_cap": cap, "week_usable": usable,
            "runs_24h": len(day), "runs_7d": len(week), "by_agent": by_agent}


def gate(cfg: dict, planned: int = 0) -> tuple[bool, str]:
    """planned = runs already queued in the current batch (not yet in the ledger)."""
    if PAUSE.exists():
        return False, "PAUSED (.team/PAUSE exists). Run `python team.py resume`."
    b = budget_summary(cfg)
    if b["runs_24h"] + planned >= cfg["daily_run_cap"]:
        return False, f"daily run cap reached ({cfg['daily_run_cap']})."
    if b["week_usable"] is not None and b["week_used"] >= b["week_usable"]:
        return False, (f"weekly self-imposed budget reached "
                       f"({b['week_used']:.0f} of {b['week_usable']:.0f} usable weighted tokens).")
    return True, ""


def log_run(entry: dict):
    with LOCK:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as f:
            f.write(json.dumps(entry) + "\n")


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #

def write_agent_files():
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, a in AGENTS.items():
        owns = "\n".join(f"- {p}" for p in a["owns"])
        content = (
            f"---\nname: {name}\ndescription: {a['desc']}\ntools: {a['tools']}\n"
            f"model: {a['model']}\n---\n\n{a['body']}\n## Paths you own\n{owns}\n\n{COMMON_RULES}"
        )
        (AGENTS_DIR / f"{name}.md").write_text(content)


def ensure_worktree(agent: str) -> Path:
    path = WORKTREES / agent
    branch = f"agent/{agent}"
    if not path.exists():
        WORKTREES.mkdir(parents=True, exist_ok=True)
        exists = git(["rev-parse", "--verify", branch], check=False).returncode == 0
        if exists:
            git(["worktree", "add", str(path), branch])
        else:
            git(["worktree", "add", "-b", branch, str(path), "HEAD"])
    # make sure the agent definitions and spec are visible even if not yet committed
    dst = path / ".claude" / "agents"
    if not dst.exists() and AGENTS_DIR.exists():
        shutil.copytree(AGENTS_DIR, dst)
    spec = ROOT / "CLAUDE.md"
    if spec.exists() and not (path / "CLAUDE.md").exists():
        shutil.copy2(spec, path / "CLAUDE.md")
    return path


def cmd_init(args):
    if git(["rev-parse", "--verify", "HEAD"], check=False).returncode != 0:
        sys.exit("Repo has no commits yet. Run: git add -A && git commit -m 'initial' then retry.")
    if not (ROOT / "CLAUDE.md").exists():
        print("WARNING: CLAUDE.md not found in repo root. Agents depend on it; add it and commit.")
    write_agent_files()
    for d in (STATUS_DIR, REPORTS, ROOT / "team", ROOT / "docs" / "reviews"):
        d.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_json(CONFIG_PATH, DEFAULT_CONFIG)
    if not TASKS_PATH.exists():
        save_json(TASKS_PATH, DEFAULT_TASKS)
    if not ESCALATIONS.exists():
        ESCALATIONS.write_text("# Escalations (questions agents need you to decide)\n\n")
    for name in AGENTS:
        sp = STATUS_DIR / f"{name}.md"
        if not sp.exists():
            sp.write_text(f"# {name} status\nUpdated: never\n## Done (this run)\n(none yet)\n")
    gi = ROOT / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    add = [l for l in (".team/", "registry_store/", "*.duckdb", "data/") if l not in existing]
    if add:
        gi.write_text(existing.rstrip("\n") + "\n" + "\n".join(add) + "\n")
    # worktrees only for agents that are tasked to build/review
    for name in AGENTS:
        if name != "lead":
            ensure_worktree(name)
    print("Initialized.\n")
    print("Next steps:")
    print("  1. git add -A && git commit -m 'chore: team scaffold'   (so worktrees inherit it)")
    print("  2. open team/config.json and set weekly_weighted_token_cap after checking /usage")
    print("  3. python team.py run --wave 1 --dry-run     (preview, spends nothing)")
    print("  4. python team.py run --wave 1")
    print("  5. python team.py status")


# --------------------------------------------------------------------------- #
# claude invocation
# --------------------------------------------------------------------------- #

REQUIRED_FLAGS = ["--agent", "--max-turns", "--output-format", "--allowedTools",
                  "--disallowedTools", "--permission-mode", "--model"]


def preflight():
    if shutil.which("claude") is None:
        sys.exit("`claude` CLI not found on PATH. Install Claude Code and log in first.")
    r = subprocess.run(["claude", "--help"], text=True, capture_output=True)
    text = (r.stdout or "") + (r.stderr or "")
    missing = [f for f in REQUIRED_FLAGS if f not in text]
    if missing:
        sys.exit(f"Your Claude Code version lacks flags this script needs: {missing}.\n"
                 f"Update Claude Code, or adjust build_command() in team.py. See `claude --help`.")


def build_prompt(task: dict, agent: str, resume: bool) -> str:
    status_path = STATUS_DIR / f"{agent}.md"
    resume_note = ("\nThis is a RESUME of a partially finished task: first read your status file and "
                   "`git log --oneline -n 15`, then continue from where you stopped.\n") if resume else ""
    return (
        f"TASK {task['id']} ({agent}): {task['prompt']}\n{resume_note}\n"
        f"Absolute paths: status file = {status_path} ; escalations file = {ESCALATIONS}\n"
        f"Your working directory is your own git worktree on branch agent/{agent}. "
        f"Finish by committing your work there, then overwrite the status file, then reply in <120 words."
    )


def build_command(task: dict, agent: str, model: str, resume: bool, cfg: dict) -> list:
    allow = ALLOW_REVIEW if agent == "qa-reviewer" else ALLOW_BUILDER
    if agent == "markets-engineer":
        allow = allow + ["WebFetch", "WebSearch"]
    turns = task.get("max_turns") or cfg["default_max_turns"]
    return [
        "claude", "-p", build_prompt(task, agent, resume),
        "--agent", agent,
        "--model", model,
        "--max-turns", str(turns),
        "--output-format", "json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", *allow,
        "--disallowedTools", *DENY_ALWAYS,
    ]


def run_one(task: dict, cfg: dict, cli_model: str | None, dry: bool) -> dict:
    agent = task["agent"]
    model = agent_model(cfg, agent, cli_model)
    st = state()
    resume = task_status(st, task["id"]) == "partial"
    wt = ensure_worktree(agent)
    cmd = build_command(task, agent, model, resume, cfg)
    if dry:
        shown = [c if len(c) < 90 else c[:87] + "..." for c in cmd]
        print(f"[dry-run] cwd={wt}\n          {' '.join(shown)}\n")
        return {"id": task["id"], "status": "dry-run"}

    with LOCK:
        s = state()
        s["tasks"].setdefault(task["id"], {})["status"] = "running"
        s["tasks"][task["id"]]["started"] = now_utc().isoformat()
        save_json(STATE, s)
    say(f"▶ {task['id']} {agent} [{model}] started")
    t0 = now_utc()
    status, usage, result_text, session, turns_used, cost = "failed", {}, "", None, None, None
    try:
        r = subprocess.run(cmd, cwd=wt, text=True, capture_output=True,
                           timeout=cfg["task_timeout_minutes"] * 60)
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            data = {}
        usage = data.get("usage", {}) or {}
        result_text = (data.get("result") or r.stdout[-600:] or r.stderr[-600:]).strip()
        session = data.get("session_id")
        turns_used = data.get("num_turns")
        cost = data.get("total_cost_usd")
        subtype = str(data.get("subtype", ""))
        if r.returncode == 0 and not data.get("is_error") and "max_turns" not in subtype:
            status = "done"
        elif "max_turns" in subtype:
            status = "partial"
    except subprocess.TimeoutExpired:
        status, result_text = "partial", "timed out; will resume from commits and status file"
    w = weighted(usage, cfg["cache_read_weight"])
    log_run({"ts": now_utc().isoformat(), "task": task["id"], "agent": agent, "model": model,
             "status": status, "turns": turns_used, "weighted": w, "usage": usage,
             "cost_usd_notional": cost, "session": session,
             "duration_s": round((now_utc() - t0).total_seconds())})
    with LOCK:
        s = state()
        rec = s["tasks"].setdefault(task["id"], {})
        rec.update({"status": status, "finished": now_utc().isoformat(),
                    "runs": rec.get("runs", 0) + 1, "last_summary": result_text[:500]})
        save_json(STATE, s)
    icon = {"done": "✔", "partial": "◐", "failed": "✘"}.get(status, "?")
    say(f"{icon} {task['id']} {agent}: {status} ({w:.0f} weighted tokens)")
    breaker = cfg.get("halt_if_single_run_exceeds")
    if breaker and w > breaker:
        PAUSE.parent.mkdir(parents=True, exist_ok=True)
        PAUSE.write_text(f"circuit breaker: {task['id']} used {w:.0f} weighted tokens\n")
        say(f"‼ circuit breaker tripped by {task['id']}; team paused.")
    return {"id": task["id"], "status": status}


def cmd_run(args):
    cfg = config()
    all_tasks = tasks()
    by_id = {t["id"]: t for t in all_tasks}
    if not args.dry_run:
        preflight()
    selected = [t for t in all_tasks if t.get("enabled", True)]
    if args.task:
        selected = [by_id[i] for i in args.task if i in by_id]
        unknown = [i for i in args.task if i not in by_id]
        if unknown:
            sys.exit(f"Unknown task id(s): {unknown}")
    elif args.wave is not None:
        selected = [t for t in selected if t["wave"] == args.wave]
    elif not args.ready:
        sys.exit("Choose one of: --wave N | --task ID | --ready")
    parallel = args.parallel or cfg["max_parallel"]
    ran_any = True
    stopped = False
    attempted: set = set()
    while ran_any and not stopped:
        ran_any = False
        st = state()
        ready, seen_agents = [], set()
        for t in sorted(selected, key=lambda x: (x["wave"], x["id"])):
            if t["id"] in attempted or task_status(st, t["id"]) not in ("pending", "partial"):
                continue
            if any(task_status(st, d) != "done" for d in t.get("depends_on", [])):
                continue
            if t["agent"] in seen_agents:   # one task per agent per round (shared worktree)
                continue
            seen_agents.add(t["agent"])
            ready.append(t)
        if not ready:
            break
        batch = []
        for t in ready:
            ok, why = gate(cfg, planned=len(batch))
            if not ok and not args.dry_run:
                say(f"Stopping: {why}")
                stopped = True
                break
            batch.append(t)
            if len(batch) >= parallel * 2:
                break
        if not batch:
            break
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futs = []
            for t in batch:
                attempted.add(t["id"])
                futs.append(pool.submit(run_one, t, cfg, args.model, args.dry_run))
            for f in as_completed(futs):
                f.result()
        ran_any = not args.dry_run
    print()
    print_budget_line(cfg)


# --------------------------------------------------------------------------- #
# Status / budget / reports
# --------------------------------------------------------------------------- #

def print_budget_line(cfg: dict):
    b = budget_summary(cfg)
    cap = (f" / usable {b['week_usable']:.0f} of cap {b['week_cap']:.0f}"
           if b["week_cap"] else " (no weekly cap set in team/config.json)")
    print(f"Budget (self-tracked, approximate): {b['week_used']:.0f} weighted tokens in 7d{cap}; "
          f"{b['runs_24h']}/{cfg['daily_run_cap']} runs in 24h. Real limits: run /usage in Claude Code.")


def escalations_open() -> list:
    if not ESCALATIONS.exists():
        return []
    return [l for l in ESCALATIONS.read_text().splitlines() if l.startswith("- [")]


def cmd_status(args):
    cfg = config()
    st = state()
    print("TASKS")
    for t in sorted(tasks(), key=lambda x: (x["wave"], x["id"])):
        s = task_status(st, t["id"])
        deps = ",".join(t.get("depends_on", [])) or "-"
        print(f"  W{t['wave']} {t['id']:<4} {s:<8} {t['agent']:<17} deps:{deps:<10} {t['prompt'][:52]}...")
    print()
    open_esc = escalations_open()
    print(f"ESCALATIONS ({len(open_esc)} open)  -> python team.py inbox")
    for l in open_esc[:5]:
        print("  " + l[:140])
    print()
    print_budget_line(cfg)
    if PAUSE.exists():
        print("TEAM IS PAUSED:", PAUSE.read_text().strip() or "manual pause")


def cmd_budget(args):
    cfg = config()
    b = budget_summary(cfg)
    print_budget_line(cfg)
    print("\nBy agent (7 days):")
    for a, v in sorted(b["by_agent"].items(), key=lambda kv: -kv[1]["weighted"]):
        print(f"  {a:<17} runs={v['runs']:<3} weighted_tokens={v['weighted']:.0f}")
    print("\nNote: 'weighted' = input + output + cache-creation + "
          f"{cfg['cache_read_weight']} x cache-read tokens. It is an estimate for pacing, not "
          "Anthropic's accounting. The pool is shared with claude.ai and Claude Desktop.")


def cmd_inbox(args):
    print(ESCALATIONS.read_text() if ESCALATIONS.exists() else "(empty)")


def agent_branch_stats(agent: str, base: str) -> tuple[int, str]:
    br = f"agent/{agent}"
    if git(["rev-parse", "--verify", br], check=False).returncode != 0:
        return 0, ""
    n = git(["rev-list", "--count", f"{base}..{br}"], check=False).stdout.strip() or "0"
    stat = git(["diff", "--shortstat", f"{base}...{br}"], check=False).stdout.strip()
    return int(n), stat


def cmd_merge_plan(args):
    base = default_branch()
    order = ["devops-ci", "data-engineer", "ml-engineer", "props-modeler", "markets-engineer", "qa-reviewer"]
    print(f"Base branch: {base}. Merge sequentially, run `make test` after each, resolve conflicts yourself.\n")
    for a in order:
        n, stat = agent_branch_stats(a, base)
        if n:
            print(f"  agent/{a:<17} {n} commit(s)  {stat}")
            print(f"     git merge --no-ff agent/{a}")
    print("\nAfter merging, refresh worktrees: git worktree remove --force .team/worktrees/<agent> "
          "&& python team.py init   (recreates from the new HEAD)")


def cmd_report(args):
    cfg = config()
    st = state()
    base = default_branch()
    day = now_utc().strftime("%Y-%m-%d")
    lines = [f"# Team report {day}", ""]
    done = [t for t in tasks() if task_status(st, t["id"]) == "done"]
    partial = [t for t in tasks() if task_status(st, t["id"]) in ("partial", "running")]
    failed = [t for t in tasks() if task_status(st, t["id"]) == "failed"]
    nxt = [t for t in tasks() if task_status(st, t["id"]) == "pending"
           and all(task_status(st, d) == "done" for d in t.get("depends_on", []))]
    lines += [f"**Shipped:** {len(done)} of {len(tasks())} tasks done; "
              f"{len(partial)} in progress; {len(failed)} failed.", ""]
    lines.append("## Done")
    lines += [f"- {t['id']} ({t['agent']}): {t['prompt'][:90]}" for t in done] or ["- nothing yet"]
    lines.append("\n## In progress / partial")
    lines += [f"- {t['id']} ({t['agent']})" for t in partial] or ["- none"]
    lines.append("\n## Failed (needs your attention)")
    lines += [f"- {t['id']} ({t['agent']}): {st['tasks'][t['id']].get('last_summary','')[:140]}"
              for t in failed] or ["- none"]
    lines.append("\n## Needs your decision")
    esc = escalations_open()
    lines += [f"- {e[2:]}" for e in esc] or ["- nothing open"]
    lines.append("\n## Ready to run next")
    lines += [f"- {t['id']} ({t['agent']}, wave {t['wave']})" for t in nxt] or ["- nothing is unblocked"]
    lines.append("\n## Agent status files")
    for name in AGENTS:
        p = STATUS_DIR / f"{name}.md"
        if p.exists() and "Updated: never" not in p.read_text():
            lines += [f"\n### {name}", p.read_text().strip()]
    lines.append("\n## Branches")
    for a in AGENTS:
        n, stat = agent_branch_stats(a, base)
        if n:
            lines.append(f"- agent/{a}: {n} commit(s) {stat}")
    b = budget_summary(cfg)
    lines.append("\n## Budget (self-tracked, approximate)")
    lines.append(f"- {b['week_used']:.0f} weighted tokens in 7 days; {b['runs_24h']} runs in 24h")
    for a, v in b["by_agent"].items():
        lines.append(f"- {a}: {v['runs']} runs, {v['weighted']:.0f} weighted tokens")
    lines.append("- Real limits: run /usage in Claude Code (pool shared with claude.ai and Desktop).")
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"{day}.md"
    text = "\n".join(lines) + "\n"
    out.write_text(text)
    print(f"Wrote {out}")
    if args.polish:
        preflight()
        prompt = ("Rewrite this engineering status digest as a crisp one-page report for a non-expert "
                  "manager: lead with what shipped and what needs their decision, keep every number "
                  "and uncertainty band, add no new facts.\n\n" + text)
        r = subprocess.run(["claude", "-p", prompt, "--model", "haiku", "--max-turns", "1",
                            "--output-format", "json"], text=True, capture_output=True, cwd=ROOT)
        try:
            data = json.loads(r.stdout)
            polished = data.get("result", "")
            log_run({"ts": now_utc().isoformat(), "task": "report", "agent": "lead", "model": "haiku",
                     "status": "done", "weighted": weighted(data.get("usage", {}) or {},
                                                            cfg["cache_read_weight"]),
                     "usage": data.get("usage", {})})
            if polished:
                (REPORTS / f"{day}.polished.md").write_text(polished)
                print(f"Wrote {REPORTS / f'{day}.polished.md'}")
        except json.JSONDecodeError:
            print("Polish step failed; the plain report is still available.")


def cmd_lead(args):
    preflight()
    cmd = ["claude", "--agent", "lead"]
    if args.model:
        cmd += ["--model", args.model]
    os.chdir(ROOT)
    os.execvp("claude", cmd)


def cmd_pause(args):
    PAUSE.parent.mkdir(parents=True, exist_ok=True)
    PAUSE.write_text("manual pause\n")
    print("Paused. No new runs will start.")


def cmd_resume(args):
    PAUSE.unlink(missing_ok=True)
    print("Resumed.")


def cmd_retry(args):
    s = state()
    for tid in args.ids:
        s["tasks"].setdefault(tid, {})["status"] = "pending"
    save_json(STATE, s)
    print("Re-queued:", ", ".join(args.ids))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    global ROOT, TEAM, STATUS_DIR, WORKTREES, LEDGER, STATE, PAUSE, ESCALATIONS
    global CONFIG_PATH, TASKS_PATH, REPORTS, AGENTS_DIR
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit("Run this inside a git repository.")
    ROOT = Path(r.stdout.strip())
    TEAM = ROOT / ".team"
    STATUS_DIR, WORKTREES = TEAM / "status", TEAM / "worktrees"
    LEDGER, STATE, PAUSE = TEAM / "ledger.jsonl", TEAM / "state.json", TEAM / "PAUSE"
    ESCALATIONS = TEAM / "escalations.md"
    CONFIG_PATH, TASKS_PATH = ROOT / "team" / "config.json", ROOT / "team" / "tasks.json"
    REPORTS, AGENTS_DIR = ROOT / "reports", ROOT / ".claude" / "agents"

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("budget").set_defaults(fn=cmd_budget)
    sub.add_parser("inbox").set_defaults(fn=cmd_inbox)
    sub.add_parser("merge-plan").set_defaults(fn=cmd_merge_plan)
    sub.add_parser("pause").set_defaults(fn=cmd_pause)
    sub.add_parser("resume").set_defaults(fn=cmd_resume)
    rp = sub.add_parser("report"); rp.add_argument("--polish", action="store_true"); rp.set_defaults(fn=cmd_report)
    lp = sub.add_parser("lead"); lp.add_argument("--model"); lp.set_defaults(fn=cmd_lead)
    ry = sub.add_parser("retry"); ry.add_argument("ids", nargs="+"); ry.set_defaults(fn=cmd_retry)
    rn = sub.add_parser("run")
    rn.add_argument("--wave", type=int)
    rn.add_argument("--task", action="append")
    rn.add_argument("--ready", action="store_true")
    rn.add_argument("--parallel", type=int)
    rn.add_argument("--model", help="override model for this run only (e.g. opus)")
    rn.add_argument("--dry-run", action="store_true")
    rn.set_defaults(fn=cmd_run)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
