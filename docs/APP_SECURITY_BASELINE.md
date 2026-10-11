# App security baseline (design only, 2026-10-11; nothing deployed)

Applies to everything under `app/`, the VM, Cloudflare and the social accounts. Architecture: `docs/APP_ARCHITECTURE.md`.
The rule behind every line: the app holds email addresses and betting slips, so collect little, keep it briefly, and
make the worst day boring. "Enterprise-safe" here means least privilege, rotation, and an off switch, not paperwork.

## 1. Secrets
- On the Mac: 1Password CLI (`op run --env-file`) preferred; macOS Keychain acceptable. In deployment: Cloudflare
  Pages/Workers secrets and the VM's secret store (systemd `LoadCredential` files, mode 0400, owned by the service user).
- Never in the repo, logs, chat, hand-backs, screenshots or the PWA bundle. Pre-commit secret scan (gitleaks or
  equivalent) on `app/`; `.env*` ignored. Keys by environment only, as the repo already requires.
- One token per platform per purpose, least privilege. **The posting token can post only**: X write scope without DMs
  or account settings; Discord channel webhook (no bot admin); Meta/TikTok publish scopes only. The LLM key is a
  dedicated project key with a provider-side monthly cap. The VM has no cloud-admin credentials; the deploy token for
  Pages can deploy only that project.
- Rotation: every 90 days for platform tokens, 180 days for the LLM key and SSH keys, immediately on any suspicion, on
  any agent-session transcript that may have shown one, and when a collaborator leaves. A calendar entry owns each date.
- Agents never see secret values: they call adapters that read them at run time. Hand-backs name a secret by label only.

## 2. Transport and auth
- HTTPS only; HTTP redirects; HSTS (1 year, includeSubDomains once all subdomains are TLS); TLS 1.2+; Cloudflare
  "Full (strict)" to the VM with an origin certificate; VM firewall accepts only Cloudflare ranges on 443 plus SSH
  restricted by key (and by IP if Devin has a stable one).
- Magic links: 256-bit random token, stored hashed, single-use, expires in 15 minutes, bound to the requesting
  browser via a short nonce cookie, generic response whether or not the email exists, no token in logs or referrers.
- Sessions: opaque random id, server-side; cookie `HttpOnly; Secure; SameSite=Lax` (Strict for the ledger-write
  routes), 30-day sliding expiry, rotation on login, revoke-all on "log out everywhere".
- CSRF: SameSite plus a double-submit token on every state-changing request, `Origin` header checked against the domain.
- Security headers: strict CSP (no inline script, `connect-src` only the API), `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `frame-ancestors 'none'`, minimal `Permissions-Policy`.

## 3. Input handling
- **Slip images**: allow jpeg, png, webp, heic only; max 8 MB; max dimensions capped; type decided by content sniffing
  (magic bytes), never by extension or the client's header; re-encode server-side to a fresh jpeg, which also strips
  EXIF/GPS and kills polyglot payloads; ClamAV scan if the VM can spare ~1 GB RAM, otherwise sniff + re-encode +
  decompression-bomb limits is the floor. Random object names; bucket is private, no public listing; uploads go through
  the API (or presigned PUT with size/type conditions), downloads only for the batch worker.
- **LLM output is data.** The vision call returns JSON only, validated against a fixed schema (`pydantic`, extra fields
  forbidden, enums for market/side, numeric ranges for line and price, length limits on strings). It is never
  executed, never used to build SQL (parameterised queries only), never rendered as HTML (escaped text), and never
  given tools. Text inside a slip image ("ignore previous instructions...") is data: the REPORTER_FEED stance applies
  (`docs/REPORTER_FEED.md`, Injection defences): fixed schema, allow-listed names resolved only through reviewed
  tables, an unknown value becomes `unknown`/`needs_review`, provenance recorded, no agent action follows from content.
- A parsed slip never alters a model, a forecast or a post. Slip-derived aggregates (BETTOR_FLOW) are a separate,
  pre-registered, analysis-only path.
- All user-supplied text is length-limited and escaped; free-text fields are never sent to the content agents unreviewed.

## 4. Privacy
- Store extracted fields only (legs, lines, prices, book, stated time, optional stake). The image is deleted after
  extraction; deletion is verified (HEAD returns 404) and logged; images that reach 24 h are deleted unread. Users may
  not opt in to keeping images in phase 1 (the draft's opt-in is deferred; it needs its own consent text).
- Email is the only identifier. No phone, name, location, ad trackers, or third-party analytics; privacy-friendly
  aggregate counts only (Cloudflare analytics without cookies).
- Retention: account data while active; inactive 24 months then deleted after a notice; extracted slips with the
  account; magic-link and rate-limit records 30 days; logs 30 days.
- Export and delete on request: self-serve "download my data" (JSON) and "delete my account" within the app; deletion
  removes ledger rows and slips within 30 days (target: immediate; backups age out within 35 days). Slips already
  folded into anonymised aggregates are stated in the terms (uploaded slips usable for the bettor-flow model under plain
  terms, per DECISIONS); aggregates carry no user id and report only cells with n >= a published minimum.
- Deliverables, drafted by Claude and reviewed by Devin (legal review recommended before launch, see section 12):
  Privacy Policy, Terms of Use, Responsible-Gambling page, Cookie/Storage notice (session cookie only), Data Deletion
  procedure, Support Escalation policy.

## 5. Abuse
- Rate limits at Cloudflare and in the API: signup/magic-link 5 per hour per IP and 3 per hour per email; login
  verify 10 per hour; upload 10 per day per account (quota visible to the user) and a global daily cap sized to the LLM
  cap; read endpoints 60 per minute per IP. Limits return 429 with no detail.
- Signup bot protection: Cloudflare Turnstile on signup and upload; disposable-email domain block list; email
  verification via the magic link itself. Accounts that never verify are purged in 7 days.
- Posting abuse: only approved items publish; adapters refuse any handle not in Devin's allow-list.

## 6. Supply chain
- Python: `uv.lock`-style lockfile with hashes for `app/` extras; TypeScript: `package-lock.json` committed, `npm ci`
  only, `ignore-scripts` on install. Pin versions; no unpinned base images. Minimal dependency list (reviewed on add).
- Dependabot (or Renovate) weekly PRs; CI runs `pip-audit` and `npm audit --omit=dev` and fails on high severity.
- CI for `app/`: ruff, ruff format --check, mypy, pytest (separate invocation, 70% ratchet, fixtures only, no network).
  Pre-commit with the secret scan. Branch protection: no direct deploy from an unreviewed branch.
- Deploys are from a tagged commit by Devin; the VM pulls signed tags, not branches. No `curl | sh` in provisioning.
- The wall: `tests/test_layering.py` keeps `app/` from reaching into `nba/` beyond `nba/export/`, and `nba/` from
  importing `app/`, so an app compromise cannot reach models or the live DB.
- Isolation: the VM holds only the export copy and the app DB. The Mac pushes with a receive-only key restricted to one
  directory; the VM has no key back to the Mac. The live `nba.duckdb` never leaves the Mac.

## 7. Logging without PII
Structured logs: request id, route, status, latency, hashed account id (HMAC with a rotating salt), error class.
Never emails, tokens, IPs beyond Cloudflare's own edge logs (not forwarded), image bytes, extracted slip contents, or
LLM prompts/responses. Support threads live in the inbox, not logs. 30-day retention. A test greps fixtures' log output
for `@`, `Bearer`, and token patterns.

## 8. Backups
Nightly encrypted copy of the app DB (age/gpg key held by Devin, public key on the VM), 7 daily + 4 weekly, stored off
the VM; quarterly restore test, recorded. The export file is rebuildable from the Mac. Backups never contain images.
Deleted accounts age out of backups within 35 days (stated in the privacy policy).

## 9. Incident response
- **Who is told**: Devin first, by the watchdog alert path (`data/ops/ALERTS.md`, notification) and email; users
  within 72 hours if their data was exposed, with what, when, and what to do. Platforms are notified per their terms.
- **Kill switches** (any of them, all reversible): `data/ops/SOCIAL_KILL` stops all posting; `APP_READONLY` flag makes the
  API refuse writes and uploads; Cloudflare "under attack"/maintenance page; revoking a platform token in its console.
  Only Devin lifts them. Agents may set `SOCIAL_KILL` themselves, never clear it.
- **Rollback**: Pages keeps prior deployments (one-click revert); VM deploys are tagged releases with the previous
  one retained; the app DB is migrated forward-only with a pre-migration backup.
- **Credential exposure**: rotate first, investigate second; record in `docs/DECISIONS.md`. A wrong post: delete, post a
  correction, log it in the content error ledger (phase 2 gate counts these).
- Security contact `security@parlayhitters.ai` (forwarded to Devin) and a `/.well-known/security.txt`.

## 10. Responsible gambling, age, and what we never do
- Every page footer: 21+ (US only), "we do not accept bets, sell picks, or guarantee outcomes", "forecasts are
  uncertain and the market is usually better than us on game winners", and the 1-800-GAMBLER helpline (national problem
  gambling hotline) with a link to the resource page. Signup requires a 21+ attestation. Non-US visitors see a notice
  and read-only pages. No state-specific content.
- Never: place or route orders; hold or request sportsbook credentials or logins (a slip is a photo, never a login);
  present a forecast as a certainty or a pick as "sure"; sell picks; take stakes; urge a user to bet more or chase
  losses; use affiliate links (until Devin decides otherwise); send promotional email beyond service messages.
  The default answer when the engine finds nothing is "no parlay today / keep your money".

## 11. Threat model
| Asset | Threat | Control | Residual |
|---|---|---|---|
| Platform tokens (X, Meta, TikTok, Discord) | Leak via repo, log, chat, agent transcript | Secrets manager, scan, post-only scopes, 90-day rotation | A leaked post-only token can still post until revoked; kill switch + revoke |
| Brand accounts | Agent posts a wrong or harmful item | Approval queue, kill switch, allow-lists, error ledger | A human approver can still approve a mistake |
| Slip images | Retention beyond promise; exposure from the bucket | Private bucket, 24 h TTL, verified deletion, no public URLs | Short window of existence; a compromised VM sees images in flight |
| User emails | Breach, enumeration, spam | Minimal data, generic responses, encrypted backups, no marketing mail | Email provider is a third party |
| Accounts | Magic-link interception, session theft | Hashed single-use 15-min tokens, HttpOnly/SameSite, rotation | A compromised mailbox is a compromised account |
| Vision LLM | Prompt injection in an image; data sent to a provider | Schema validation, data-only, no tools, provider zero-retention setting if offered | Misread numbers pass validation; mitigated by user-visible confirm step and spot checks |
| Cost | LLM or upload flood draining money | Per-account quotas, global cap, provider cap, Turnstile | A bill up to the cap is possible |
| Live pipeline | App compromise reaching `nba.duckdb`/models | Layering rule, read-only export, receive-only push key, separate DBs | Mac-side export code is part of the trusted path; reviewed |
| Forecast integrity | App-side change to what was forecast | Export is read-only; pre-tip stamps untouched; page shows the engine's verdict verbatim | None known |
| Availability | VM or Cloudflare outage | Cached PWA, stale banner, no pipeline dependency | App is down; the forward log is unaffected |
| Supply chain | Malicious dependency or action | Lockfiles, audit, Dependabot review, `ignore-scripts`, minimal deps | Zero-day in a pinned package |
| Legal | Betting-adjacent positioning, age, privacy law | 21+ gate, disclaimers, policies, deletion path; legal advice is a reversal condition in DECISIONS | Not a lawyer's opinion; needs Devin to decide |
| Slip data in bettor-flow | Re-identification | Aggregates only, minimum cell n, no user id, plain-terms consent | Small cells; the minimum n is set before any analysis |

## 12. Checklist for Devin (accounts and purchases; none exist yet)
Nothing below is created by Claude. Order is roughly build order.
- [ ] Domain `parlayhitters.ai`: register at a registrar of your choice (exact URL and plan to be confirmed; see memory
      on vendor names); enable registrar lock and 2FA.
- [ ] Cloudflare account (free tier): add the domain, Pages project, Turnstile site, WAF rate rules; 2FA with a hardware key.
- [ ] Oracle Cloud account and VM (Always Free ARM if available, else smallest shape); SSH key; confirm region and the
      payment method on file (a card may be required even for free tier).
- [ ] Object store for slips: Cloudflare R2 or OCI Object Storage (your choice), with the 24 h lifecycle rule.
- [ ] Secrets manager: 1Password (vault `parlayhitters`, service account) or macOS Keychain; plus VM credential files.
- [ ] Vision LLM API key (dedicated project) with a monthly cap of $5; a second key for any other use.
- [ ] Transactional email sender and a sending domain with SPF/DKIM/DMARC; `security@` and `support@` aliases.
- [ ] X developer account and app (write scope, paid tier if required), authorised on your handle.
- [ ] Discord server, support channel, posting webhook, support bot (if used).
- [ ] TikTok developer app and Content Posting API approval/audit (long lead time; manual posting until approved).
- [ ] Instagram Professional account, linked Facebook Page, Meta developer app, App Review for content publishing
      (long lead time; manual posting until approved).
- [ ] GitHub settings: Dependabot, secret scanning, branch protection.
- [ ] Legal: privacy policy and terms (drafts by Claude), recommended one-time review by a lawyer familiar with
      betting-adjacent apps; business-structure decision (sole proprietor under your name for now).
- [ ] Decisions: approval-queue cadence, who covers support when you are away, the posting-after-dry-run switch, and
      any change to the $30/month ceiling.
