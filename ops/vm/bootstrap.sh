#!/bin/sh
# One-command setup of a fresh Ubuntu 24.04 (ARM, Oracle Always-Free A1.Flex) VM. Run ON the VM as the
# normal `ubuntu` user (needs passwordless sudo, the Oracle default):
#
#   curl -LsSf <raw url of this file> | sh     # only if the repo is public; otherwise copy it over:
#   scp ops/vm/bootstrap.sh ubuntu@<vm-ip>: && ssh ubuntu@<vm-ip> sh bootstrap.sh
#
# Steps: apt deps -> timezone America/Chicago -> uv -> clone repo -> reachability gate -> uv sync -> dirs.
# It does NOT install cron (see ops/vm/install_cron.sh) and does NOT copy data (see ops/vm/sync_from_mac.sh).
# Idempotent: safe to re-run. No credentials are embedded or created here.
#
# Repo access (private repo): create a read-only deploy key on the VM, then add the printed public key at
# GitHub repo > Settings > Deploy keys (leave "allow write" OFF):
#     ssh-keygen -t ed25519 -N '' -f ~/.ssh/nba_deploy -C nba-vm
#     cat ~/.ssh/nba_deploy.pub
#     printf 'Host github.com\n  IdentityFile ~/.ssh/nba_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
# Or authenticate with `gh auth login` and run with REPO_URL=https://github.com/devinhanif/basketball_predictions.git
#
# Env overrides: REPO_URL, NBA_ROOT (default ~/basketball_predictions), NBA_TZ (default America/Chicago),
#                NBA_SKIP_REACH=1 (continue even if the reachability gate fails; not recommended).
#
# Timezone decision: the VM clock is set to America/Chicago so the cron schedule is written in exactly the
# same local times as the Mac's launchd jobs (pretip :30 09:30-21:30, morning 08:00) and the scripts' own
# log timestamps match the Mac's. Scripts that need Eastern time already use TZ=America/New_York explicitly.
set -eu

REPO_URL=${REPO_URL:-git@github.com:devinhanif/basketball_predictions.git}
NBA_ROOT=${NBA_ROOT:-$HOME/basketball_predictions}
NBA_TZ=${NBA_TZ:-America/Chicago}

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nSTOP: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as the normal ubuntu user, not root"
command -v sudo >/dev/null 2>&1 || die "sudo is required"

say "apt packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git curl rsync rclone lsof ca-certificates cron tzdata util-linux
sudo systemctl enable --now cron >/dev/null 2>&1 || true

say "timezone -> $NBA_TZ"
if [ "$(timedatectl show -p Timezone --value 2>/dev/null)" != "$NBA_TZ" ]; then
  sudo timedatectl set-timezone "$NBA_TZ"
fi
date '+VM local time now: %Y-%m-%d %H:%M %Z'

say "uv"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv --version

say "clone / update repo at $NBA_ROOT"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
case "$REPO_URL" in
  git@github.com:*)  # trust github's host key on first contact (accept-new never overrides a changed key)
    GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new"; export GIT_SSH_COMMAND ;;
esac
if [ -d "$NBA_ROOT/.git" ]; then
  git -C "$NBA_ROOT" pull --ff-only || die "git pull failed in $NBA_ROOT (local changes? resolve by hand)"
else
  git ls-remote "$REPO_URL" >/dev/null 2>&1 || die "cannot read $REPO_URL. Add a read-only deploy key (see the header of this script) or use gh auth + an https REPO_URL."
  git clone "$REPO_URL" "$NBA_ROOT"
fi
cd "$NBA_ROOT"

say "reachability gate (stats.nba.com must pass from this IP)"
REACH=$(mktemp)
sh ops/check_vm_reachability.sh > "$REACH" 2>&1 || true
cat "$REACH"
if grep -q '^FAIL  stats.nba.com' "$REACH"; then
  if [ "${NBA_SKIP_REACH:-0}" != 1 ]; then
    rm -f "$REACH"
    die "stats.nba.com is NOT reachable from this VM (cloud IPs are often blocked or throttled).
The pipeline cannot run here. Do not migrate. Options: retry in an hour (a throttle clears), try another region/VM
shape, or keep the Mac as primary. Re-run this script after changing something; NBA_SKIP_REACH=1 overrides."
  fi
  echo "WARNING: continuing despite stats.nba.com failure (NBA_SKIP_REACH=1)"
fi
if grep -q '^FAIL' "$REACH"; then
  echo "NOTE: some non-critical checks failed (see FAIL lines above); stats.nba.com passed."
fi
rm -f "$REACH"

say "uv sync (exactly the committed uv.lock)"
uv sync --frozen

say "directories"
mkdir -p data/ops data/backups data/kalshi data/lineups data/schedule data/players_static registry_store

say "done"
cat <<MSG
Bootstrap OK at $NBA_ROOT (timezone $(timedatectl show -p Timezone --value 2>/dev/null)).
Next (see docs/VM_MIGRATION.md):
  1. rclone config                      (your own Drive client_id; remotes gdrive: and nbacolab:)
  2. on the Mac:  NBA_VM_HOST=ubuntu@<ip> sh ops/vm/sync_from_mac.sh
  3. here:        sh ops/vm/install_cron.sh        (SHADOW mode first; see the doc)
MSG
