# Moving the daily pipeline from the Mac to an Oracle Always-Free VM

Prepared 2026-10-09. Nothing here has been run against a real VM. Read-only w.r.t. markets, no trading
credentials anywhere. The rule for the whole migration: **never two primaries.** Exactly one machine writes the
production DB and the forward-prediction log at any time; the other is either off or in shadow (separate DB).

Files: `ops/vm/bootstrap.sh` (on the VM), `ops/vm/install_cron.sh` (on the VM), `ops/vm/sync_from_mac.sh`
(on the Mac). `ops/nba_daily.sh` and `ops/nba_lineups.sh` read `NBA_ROOT` and `NBA_UV` from the environment,
defaulting to the Mac paths, so Mac behaviour is unchanged.

## Decisions

- **Timezone:** the VM is set to `America/Chicago`. The cron lines are then literally the Mac's launchd
  times (pretip 09:30-21:30 at :30, morning 08:00, lineups every 5 min, kalshi every 15 min) and log
  timestamps are comparable. Scripts needing Eastern time already use `TZ=America/New_York` explicitly.
- **Cron, not systemd:** a one-line-per-job equivalent of launchd, a managed block that install/uninstall
  can replace without touching other entries.
- **No macOS-only tools needed:** `osascript` notifications and `caffeinate` are used only when present;
  on the VM, failures land in `data/ops/ALERTS.md` (check it, or add your own notifier later).

## Steps

1. **Create the VM.** Oracle Cloud console, Compute, Create instance: image Ubuntu 24.04 (aarch64), shape
   `VM.Standard.A1.Flex` (start with 2 OCPU / 12 GB; Always-Free allows up to 4 / 24 GB), boot volume 100-150 GB
   (the DB, caches and registry are ~1 GB; the Colab directory is not copied). Add your SSH public key. Note the
   public IP. Region capacity for A1 is sometimes exhausted; retry later or another availability domain.
2. **Avoid idle reclaim.** Always-Free instances idle for 7 days (low CPU/network/memory) can be reclaimed.
   Upgrade the tenancy to Pay-As-You-Go (Always-Free resources stay free) and create a budget with a $1 alert
   (Billing, Budgets, threshold on actual spend). Stay within the Always-Free shapes and volumes.
3. **SSH.** `ssh -i <key> ubuntu@<ip>`. Open nothing inbound beyond SSH (22); the pipeline only makes outbound calls.
4. **Repo access (private repo, never embed credentials).** On the VM:
   `ssh-keygen -t ed25519 -N '' -f ~/.ssh/nba_deploy -C nba-vm`, print `~/.ssh/nba_deploy.pub`, add it as a
   read-only deploy key at GitHub, repo `devinhanif/basketball_predictions`, Settings, Deploy keys (write access
   off), then
   `printf 'Host github.com\n  IdentityFile ~/.ssh/nba_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config`.
   (Alternative: `gh auth login` and `REPO_URL=https://github.com/devinhanif/basketball_predictions.git`.)
5. **Bootstrap.** Copy the script over and run it (it clones the repo itself):
   `scp ops/vm/bootstrap.sh ubuntu@<ip>: && ssh ubuntu@<ip> sh bootstrap.sh`.
   It installs apt deps, sets the timezone, installs uv, clones to `~/basketball_predictions`, **runs
   `ops/check_vm_reachability.sh` and stops with a clear message if stats.nba.com fails**, then `uv sync --frozen`.
   If it stops on stats.nba.com: do not migrate (cloud IPs may be blocked or throttled). Retry later (a throttle can
   clear), try another region, or keep the Mac as primary.
6. **rclone (Drive backups and Colab remotes).** On the VM: `rclone config`, create remotes `gdrive` (backups
   written by the morning job: `gdrive:nba_backups/...`) and `nbacolab` (Colab exchange). Use **your own Google
   Cloud OAuth client_id/secret** (Drive API enabled, OAuth consent screen, scope `drive`); rclone's shared
   default client is being retired in 2026. The VM is headless: use `rclone authorize "drive"` on the Mac and paste
   the token when asked, or copy the relevant sections of `~/.config/rclone/rclone.conf` over SSH (it holds
   tokens; treat as a secret, never commit it). Check: `rclone lsd gdrive:` and `rclone lsd nbacolab:`.
7. **Initial sync (Mac).** With no eval/backfill writing `nba.duckdb`:
   `NBA_VM_HOST=ubuntu@<ip> sh ops/vm/sync_from_mac.sh --dry-run`, review the sizes, then without `--dry-run`.
   It prints sizes first, refuses while any `*.duckdb` has a writer, never deletes on the VM, and excludes
   `data/colab`, `data/history`, `data/backups`, `data/ops`. For optional `NBA_SSH_KEY=<path>` and `NBA_VM_DIR`.
8. **Shadow period (2-3 days).** On the VM: `sh ops/vm/install_cron.sh`. The VM now runs all jobs against **its
   own** `nba.duckdb`/`registry_store`/`data/lineups`, writing its own `forward_predictions`. The Mac stays primary
   and keeps all its launchd jobs. Rules during shadow:
   - re-sync only with `--no-db` (otherwise the VM's DB is overwritten);
   - the VM's rclone Drive uploads write the same `gdrive:nba_backups/` paths as the Mac's: either leave the
     `gdrive` remote unconfigured on the VM during shadow (the backup steps then log an alert, harmless), or
     accept that the last writer wins. Preferred: skip `gdrive` until cutover;
   - the Kalshi snapshot on the VM and Mac both append to their own `data/kalshi/kalshi.duckdb`; fine.
   Daily comparison: for each slate date, the VM's pre-tip predictions vs the Mac's (same model version, same
   inputs should agree to numerical noise); check `data/ops/ALERTS.md` on both, `crontab -l` on the VM and
   `tail data/ops/pretip_$(date +%Y%m%d).log`. Differences caused by timing (different injury-report snapshot at the
   moment of the run) are expected; systematic differences are not. Acceptance: 2-3 consecutive days with no
   unexplained alerts, same game coverage, comparable pretip timestamps, and stats.nba.com pulls not throttled.
9. **Cutover (never two primaries).**
   a. On the Mac: `sh ops/install_launchd.sh --uninstall` (removes pretip, morning, lineups). **Keep
      `local.nba.kalshi-snapshot`** until the VM's kalshi job has produced snapshots for a day (compare row
      counts in `data/kalshi/kalshi.duckdb`), then
      `launchctl bootout gui/$(id -u)/local.nba.kalshi-snapshot && rm ~/Library/LaunchAgents/local.nba.kalshi-snapshot.plist`.
   b. Stop the VM jobs: `sh ops/vm/install_cron.sh --uninstall` (so nothing writes while the final copy lands).
   c. Final sync from the Mac, **with** the DB so the VM inherits the full forward-prediction history:
      `NBA_VM_HOST=ubuntu@<ip> sh ops/vm/sync_from_mac.sh`. Note this overwrites the VM's shadow DB; archive it
      first on the VM if the shadow rows matter (`cp nba.duckdb nba_shadow.duckdb`).
   d. Configure the `gdrive` remote on the VM if skipped, then `sh ops/vm/install_cron.sh`.
   e. Next morning: confirm `data/ops/morning_*.log` completed, the backup was written and uploaded to Drive.
   Rollback: uninstall the VM cron, `sh ops/install_launchd.sh` on the Mac, sync `nba.duckdb` back
   (`rsync` the other way) so no forward predictions are lost.
10. **Ongoing.** `uv sync --frozen` after pulls (`git pull --ff-only`). Ingest-queue restart on boot is optional:
    `sh ops/vm/install_cron.sh --with-queue` (leave off unless a backfill must survive reboots).

## Known caveats

- Not verified: wheels for every dependency on aarch64 (torch, lightgbm have them), cloud-IP access to
  stats.nba.com (the bootstrap gate exists precisely for this), Oracle A1 capacity.
- Cron has no catch-up: unlike launchd, jobs missed while the VM is down are skipped. The morning job is
  idempotent, so re-run by hand: `cd ~/basketball_predictions && sh ops/nba_daily.sh morning`.
- `rclone` from Ubuntu's apt is an older release; it works for Drive copy. Upgrade via rclone.org's install
  script if a feature is missing.
