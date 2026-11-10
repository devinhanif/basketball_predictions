# JARVIS team cards: Discord setup (Devin)

What you get: one Discord channel per NBA team (30). Every game produces ONE card that goes to BOTH teams'
channels. The card is posted silently before tip, then **edited in place** at halftime and at the final (and once
more when the box score settles the overs). The main channel keeps a short nightly overview and the existing
act-on pings; nothing about prices, EV, fees, labels or alert conditions changed (formatting and delivery only,
DECISIONS 2026-11-09).

Paper only. Not advice. A card never says how much to bet.

## 1. Create the channels (about 10 minutes, once)

1. Open your Discord server on a computer (the desktop app or discord.com). Webhooks are easiest there.
2. Right-click the server name (top left) and choose **Create Category**. Name it `NBA teams`. Optional: switch on
   **Private Category** so only you can see it, then **Create Category**.
3. Right-click the `NBA teams` category and choose **Create Channel**. Channel type **Text**. Type the name from the
   table below and click **Create Channel**. Repeat 30 times (new channels created from the category's menu stay inside it).
4. Mute the category on your phone if you want quiet by default: long-press the category, **Mute Category**. Cards are
   already posted silently and edits never notify, so this is only for taste.

| Abbr | Team | Suggested channel name |
|---|---|---|
| ATL | Atlanta Hawks | `atl-hawks` |
| BKN | Brooklyn Nets | `bkn-nets` |
| BOS | Boston Celtics | `bos-celtics` |
| CHA | Charlotte Hornets | `cha-hornets` |
| CHI | Chicago Bulls | `chi-bulls` |
| CLE | Cleveland Cavaliers | `cle-cavaliers` |
| DAL | Dallas Mavericks | `dal-mavericks` |
| DEN | Denver Nuggets | `den-nuggets` |
| DET | Detroit Pistons | `det-pistons` |
| GSW | Golden State Warriors | `gsw-warriors` |
| HOU | Houston Rockets | `hou-rockets` |
| IND | Indiana Pacers | `ind-pacers` |
| LAC | LA Clippers | `lac-clippers` |
| LAL | Los Angeles Lakers | `lal-lakers` |
| MEM | Memphis Grizzlies | `mem-grizzlies` |
| MIA | Miami Heat | `mia-heat` |
| MIL | Milwaukee Bucks | `mil-bucks` |
| MIN | Minnesota Timberwolves | `min-timberwolves` |
| NOP | New Orleans Pelicans | `nop-pelicans` |
| NYK | New York Knicks | `nyk-knicks` |
| OKC | Oklahoma City Thunder | `okc-thunder` |
| ORL | Orlando Magic | `orl-magic` |
| PHI | Philadelphia 76ers | `phi-76ers` |
| PHX | Phoenix Suns | `phx-suns` |
| POR | Portland Trail Blazers | `por-blazers` |
| SAC | Sacramento Kings | `sac-kings` |
| SAS | San Antonio Spurs | `sas-spurs` |
| TOR | Toronto Raptors | `tor-raptors` |
| UTA | Utah Jazz | `uta-jazz` |
| WAS | Washington Wizards | `was-wizards` |

You do not need all 30 on day one. A team with no webhook is skipped (counted in the tick log line as
`cards_no_webhook=N`, never a failure). Start with the teams you follow.

## 2. One webhook per channel

For each channel (labels can differ slightly between Discord versions):

1. Hover the channel in the left list and click the **gear** (Edit Channel).
2. In the left menu click **Integrations**, then **Webhooks**, then **Create Webhook** (older versions say **New Webhook**).
3. Click the new webhook, set its name to `JARVIS`, then click **Copy Webhook URL**. Click **Save Changes**.
4. Paste the URL into the file in step 3 next to that team's abbreviation. Do not paste it anywhere else (not in a
   chat, not in the repo, not in a terminal command that is saved in history).

A webhook URL is a password: anyone who has it can post into that channel. If one leaks, open the same screen and
click **Delete Webhook**, make a new one, and update step 3 (section 7).

## 3. Put the thirty URLs in one JSON object (outside the repo)

```
cd /Users/devin/Downloads/nba-prediction
uv run python -m nba.jarvis cards --template > ~/jarvis_team_webhooks.json
chmod 600 ~/jarvis_team_webhooks.json
open -e ~/jarvis_team_webhooks.json
```

Each team has an empty slot: `"LAL": ""`. Paste the URL between the quotes. Leave a slot empty to skip that team.

## 4. Store it (pick ONE; recommended: A)

JARVIS reads the JSON from the environment variable `JARVIS_DISCORD_TEAM_WEBHOOKS`; if that is unset and
`team_cards.keychain_service` is set in `configs/jarvis.yaml`, from one macOS Keychain item.

**A. Environment variable in your installed launchd copy (recommended).** One variable, the same pattern as the
existing main-channel webhook, no prompt that could hang a 2-minute job. The value lives only in
`~/Library/LaunchAgents/local.nba.jarvis.plist`, outside the repo.

```
plutil -replace EnvironmentVariables.JARVIS_DISCORD_TEAM_WEBHOOKS -string "$(cat ~/jarvis_team_webhooks.json)" ~/Library/LaunchAgents/local.nba.jarvis.plist
```

(Keep the file until step 5 has passed; step 5 then deletes it.)

(Honest cost: the value is visible to your own user through `launchctl print`; the same is true of the existing
main webhook. Nothing else on the machine can read a 600 file or your LaunchAgents folder.)

**B. Keychain.** Keeps the URLs out of any file. Cost: macOS may show an access dialog the first time a background job
reads the item; click **Always Allow** once (run the check in step 5 from Terminal first so the dialog appears while
you are looking), otherwise a tick can wait on a dialog.

```
security add-generic-password -a "$USER" -s jarvis-team-webhooks -U -w "$(cat ~/jarvis_team_webhooks.json)"
```

then set `team_cards.keychain_service: jarvis-team-webhooks` in `configs/jarvis.yaml` (the variable, when set, still
wins). (The value is briefly visible in the process list of your own user while that one command runs.)

## 5. Check without sending anything

```
# the check reads the file you filled in (the variable is not in your shell under option A):
export JARVIS_DISCORD_TEAM_WEBHOOKS="$(cat ~/jarvis_team_webhooks.json)"
uv run python -m nba.jarvis cards --check-webhooks
unset JARVIS_DISCORD_TEAM_WEBHOOKS
```

It prints counts and team abbreviations only, never a URL, for example
`30/30 team webhooks configured (source: env)`. It names invalid slots (`invalid: BOS` = not a Discord webhook URL)
and any two teams pasted with the same URL (`same URL for: LAC, LAL`: both teams would share a channel). Exit 0
means every non-empty slot is valid. When it passes, delete the file: `rm ~/jarvis_team_webhooks.json`.
Re-installing the plist from the repo template silently drops the team variable; the tick line then shows
`cards=off(no webhooks)`.

## 6. See the cards before going live

```
# prints the embeds as redacted JSON, sends nothing, writes nothing
uv run python -m nba.jarvis cards --dry-run

# also writes data/jarvis/card_preview/<date>/cards.html and cards.json: open the HTML in a browser
uv run python -m nba.jarvis cards --preview
open data/jarvis/card_preview/$(date +%F)/cards.html
```

Useful variants:

* `--game LAL@ATL` one game; `--date YYYY-MM-DD` another night.
* `--phase pretip|halftime|final` renders every game in that phase (the later cards need tonight's data).
* Replay a finished night to see all three cards of a real game (nothing is logged; the overs are priced as of the
  moment you give, into a throwaway log):
  `uv run python -m nba.jarvis cards --date 2026-11-09 --game IND@DAL --pretip-at 2026-11-09T23:30:00 --at 2026-11-10T02:00:00 --phase halftime --preview`
  (times are UTC; `--at` is "now" for the replay; injuries and lineups are read as they are today, not as of then).

The `cards` command never sends. Live delivery happens only inside the 2-minute `watch --once` job.

## 7. Going live (after the branch is merged; your steps)

1. Merge `agent/market-jarvis-team-cards` (needs the replay oracle IDENTICAL and your go; the code touches `nba/jarvis`,
   `configs/jarvis.yaml` and nothing in models, truth or daily). Merge outside a game window.
2. Do step 4 and step 5 (variable in the installed plist, then reload the job):
   `launchctl bootout gui/$(id -u)/local.nba.jarvis; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.nba.jarvis.plist`
3. Watch one tick line in `data/ops/launchd_jarvis.out`. Expect `cards=4posted` when a slate's cards go up, then
   `cards=2edited` style counts at halftime and the final. `cards_error=...` or `cards_no_webhook=N` flags show up there.
4. Rotating a webhook: delete it in Discord, make a new one, update the stored JSON (step 4). JARVIS notices the new URL and
   posts a fresh card for that team's games (it cannot edit a message another webhook posted).

## 8. What happens, and what does not ping you

| When | What the channel sees | Notification |
|---|---|---|
| Within `post_lead_min` (120) minutes of tip | One card per game in each team's channel: top overs by EV after fees (1 to 3), or "No over has positive EV after fees tonight.", and what the game knows | silent post |
| Pre-tip refresh | The same message is edited at most every 15 minutes, only if its content changed (the "N min old" ages and the footer clock do not count as a change) | none |
| While the game is live | Nothing | none |
| Halftime | The same message is edited: each leg on pace or behind, needs N more, live price vs model, Enter or Skip by the halftime scorer's own rule | none (an edit) |
| Final | Edited again: final score, hits and misses, paper P&L in units, season record as n, hits, misses and units, labelled "descriptive, not a tested claim" (no interval); edited once more when the box score settles the overs | none |
| An existing strong or arbitrage alert fires | It goes to the main channel exactly as before; the card gets a line saying so | main channel only |
| First cards of the night | One short overview in the main channel (cards are up, best over or the no-over sentence) | normal (once a night) |

No new alert condition exists. To silence the main channel's halftime check-in and overs lines (the cards carry them), set
`team_cards.main_channel.keep_halftime_posts: false`; it only takes effect for a game whose BOTH team channels have a webhook.

How to read a card:

* `⚪` not started / unknown, `🟢` on pace, `🟠` behind, `✅` hit, `❌` miss. A hit and a miss print in the same layout; a hit
  adds "this one hit; it was a +X% EV bet when shown".
* "EV" is per $1 contract after the exchange fee and the ask (the spread is in the price you would pay), from the same
  computation as the slate preview. "Fair" is the sharp line's price for that bet.
* "Model vs sharp line" is how closely OUR model and the sharp line agree, in percentage points of probability (close, some
  gap, far apart): agreement, not a confidence interval. No interval for props is stored where JARVIS can read it.
* An over shown pre-tip stays on the card when its price ages out or the pre-tip cutoff passes, marked "(price now stale)";
  it never silently turns into "no over". Overs capped for a weak input (old Pinnacle line, no P(plays), Pinnacle newer
  than the price) or held by the pull rule are not shown or logged, exactly as in the slate preview.
* "Units": every over risks one unit; a miss is -1.00, a hit at cost c is (1-c)/c. The season line is per over shown on a card,
  scored at the price first shown, never at the close.
* The model has no live track record yet, and the market usually knows more. Most nights the honest card says no over has positive EV.

## 9. Limits and rate limits

Discord limits are respected in code and tested: title 256, description 4096, 25 fields, field name 256, field value 1024,
footer 2048, 6000 characters in total per embed (verified on docs.discord.com, 2026-11-09). Long fields are cut at a line
with a "+N more lines" note. The card requests wait on a 429 for the `retry_after` it names and on a drained per-webhook
bucket, up to `team_cards.rate_budget_s` (20 s) per tick; anything beyond that is deferred to the next tick (never
failed, never duplicated). The whole card pass also has ONE wall-clock budget, network time included
(`team_cards.tick_budget_s`, 25 s; Discord requests time out at 5 s, connect 3 s), and the first network error or 5xx stops
the pass for the rest of that tick. The card pass runs last in the tick, so alerts, halftime check-ins and in-game posts never
wait on Discord. A deleted card message is replaced by a new post and the new id is recorded.

## 10. Rollback

* Stop the cards, keep everything else: set `team_cards.enabled: false` in `configs/jarvis.yaml` (or remove
  `JARVIS_DISCORD_TEAM_WEBHOOKS` from the installed plist and reload the job). The next tick does nothing for cards.
* Known limit: with `keep_halftime_posts: false` the main halftime lines are dropped for a covered game even if that
  game's card edit is deferred (the main post is decided before the card pass runs). Default is true.
* Back to the old main-channel behaviour completely: `git revert` the merge commit. The main channel posts were never
  removed, so nothing else needs undoing.
* Cards already posted stay in Discord until you delete the channels or messages. Delete `data/jarvis/cards_state.jsonl`
  only if you want JARVIS to post fresh cards (it forgets the message ids); keep `data/jarvis/card_picks.jsonl`, it is the
  paper log the recaps and the season line read.
