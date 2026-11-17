#!/usr/bin/env bash
# cleanup_2026-11-17.sh -- disk and clutter cleanup candidates, for Devin.
#
# REVIEW BEFORE RUNNING. NOTHING HERE WAS RUN when this file was written (2026-11-16, lane CI).
#
# Default is --dry-run: it only READS (git worktree list, git branch --merged, git status with
# optional locks off, ls, du) and prints, for every candidate, its size and the exact command
# that would remove it. It takes no lock, writes nothing, touches no database.
# `--really` runs those same printed commands, one at a time, and stops at the first failure.
# (Must stay parseable by `sh -n`: tests/ops/test_vm_scripts.py parses every ops/*.sh.)
#
# Not touched by design: data/ (except the two named pretip logs), nba.duckdb, any ledger,
# ~/Library/LaunchAgents/*.plist that launchd has loaded, heavy.lock, the main checkout.
# Worktree removal uses `git worktree remove` WITHOUT --force (refuses if the worktree is dirty)
# and `git branch -d` (refuses if the branch is not merged): both are the conservative forms.
#
#   usage: ops/cleanup_2026-11-17.sh [--dry-run | --really]
set -euo pipefail

export GIT_OPTIONAL_LOCKS=0 # git status must not write the index (read-only by construction)
ROOT=/Users/devin/Downloads/nba-prediction
MODE=dry
case "${1:---dry-run}" in
  --dry-run) MODE=dry ;;
  --really) MODE=really ;;
  *) echo "usage: $0 [--dry-run | --really]" >&2; exit 2 ;;
esac
cd "$ROOT"

total_n=0
size_of() { if [ -e "$1" ]; then du -sh -- "$1" 2>/dev/null | cut -f1; else echo "absent"; fi; }

# item "<why>" <size-path-or-> <command...>   : print size + exact command; run it only with --really
item() {
  local why=$1 sp=$2
  shift 2
  total_n=$((total_n + 1))
  local sz="-"
  [ "$sp" != "-" ] && sz=$(size_of "$sp")
  printf '\n[%s] %s\n    size: %s\n    cmd : ' "$total_n" "$why" "$sz"
  printf '%q ' "$@"
  printf '\n'
  if [ "$MODE" = really ]; then
    "$@"
    echo "    done"
  fi
}

echo "cleanup_2026-11-17: mode=$MODE (dry-run prints only; --really executes each printed command)"

# ---- 1. worktrees whose branch is fully merged into main -------------------------------------
# A worktree is listed only if: its branch tip is an ancestor of main, it is not the main
# checkout, it is not a tonight/integration worktree, and `git status` shows it clean.
# Detached-HEAD and dirty worktrees are listed as "review", with no command.
echo
echo "== 1. worktrees with a fully merged branch (git worktree remove, then git branch -d) =="
wt=""
br=""
{ git worktree list --porcelain; echo; } | while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    "worktree "*) wt=${line#worktree }; br="" ;;
    "branch "*) br=${line#branch refs/heads/} ;;
    "detached") br="(detached)" ;;
    "prunable"*) printf '\n[prunable] %s : registered but the directory is gone\n    cmd : git worktree prune\n' "$wt" ;;
    "")
      [ -n "$wt" ] || continue
      if [ "$wt" = "$ROOT" ]; then wt=""; continue; fi
      case "$wt" in
        *nba-wt-1117-*|*nba-wt-int-1117*) wt=""; continue ;; # tonight's lanes: never listed
      esac
      [ -d "$wt" ] || { wt=""; continue; }
      if [ "$br" = "(detached)" ] || [ -z "$br" ]; then
        printf '\n[review] %s : detached HEAD, size %s, no command (decide by hand)\n' "$wt" "$(size_of "$wt")"
      elif git merge-base --is-ancestor "$br" main 2>/dev/null; then
        if [ -n "$(git -C "$wt" status --porcelain 2>/dev/null | head -n 1)" ]; then
          printf '\n[review] %s (%s): merged but DIRTY, size %s, skipped\n' "$wt" "$br" "$(size_of "$wt")"
        else
          item "merged worktree $wt ($br)" "$wt" git worktree remove -- "$wt"
          item "merged branch $br" - git branch -d -- "$br"
        fi
      fi
      wt=""
      ;;
  esac
done

# ---- 2. the registered .team/worktrees (all at the old 90b1cb9f; included by the loop above if merged) ----
# (listed by section 1 when merged; nothing separate to do)

# ---- 3. aside directories left by data-dir swaps ----------------------------------------------
echo
echo "== 3. data_aside directories (each holds only an empty raw/ at the time of writing) =="
for d in "$ROOT"/.claude/worktrees/data_aside_* /Users/devin/Downloads/nba-wt-morning-advisory_data_aside /Users/devin/Downloads/scratchpad_data_aside_36311; do
  [ -e "$d" ] || continue
  item "aside dir (check it holds no real data first: ls -laR $d)" "$d" rm -r -- "$d"
done

# ---- 4. stale environments and artifacts --------------------------------------------------------
echo
echo "== 4. environments and stale artifacts =="
item "old venv .venv-1 (Python-pin tests failed under it; the live env is .venv)" "$ROOT/.venv-1" rm -r -- "$ROOT/.venv-1"
item "stale setuptools egg-info (the editable install uses dist-info + a finder; gitignored)" "$ROOT/nba_prediction.egg-info" rm -r -- "$ROOT/nba_prediction.egg-info"
# .env.save may be the only copy of a key: confirm it duplicates .env before running this one.
item "REVIEW FIRST (not read by this lane): .env.save, a copy of the secrets file" "$ROOT/.env.save" rm -- "$ROOT/.env.save"

# ---- 5. logs --------------------------------------------------------------------------------------
echo
echo "== 5. logs =="
item "launchd_authcheck stdout (2026-10-12, one-off)" "$ROOT/data/ops/launchd_authcheck.out" rm -- "$ROOT/data/ops/launchd_authcheck.out"
item "launchd_authcheck stderr (empty)" "$ROOT/data/ops/launchd_authcheck.err" rm -- "$ROOT/data/ops/launchd_authcheck.err"
item "pretip log 2026-10-21 (about 10 GB, runaway output)" "$ROOT/data/ops/pretip_20261021.log" rm -- "$ROOT/data/ops/pretip_20261021.log"
item "pretip log 2026-10-22 (about 9 GB, runaway output)" "$ROOT/data/ops/pretip_20261022.log" rm -- "$ROOT/data/ops/pretip_20261022.log"

# ---- 6. launchd backup (not loaded: launchd reads *.plist only) ---------------------------------
echo
echo "== 6. launchd backup =="
item "daily-morning plist backup from 2026-10-11 (this does not unload anything)" "$HOME/Library/LaunchAgents/local.nba.daily-morning.plist.bak_20261011" rm -- "$HOME/Library/LaunchAgents/local.nba.daily-morning.plist.bak_20261011"

echo
echo "done listing. mode=$MODE. Nothing was executed unless --really was given."
