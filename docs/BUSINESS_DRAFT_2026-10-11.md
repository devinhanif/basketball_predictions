# Autonomous business, draft (2026-10-11; Devin's brainstorm, Claude's thoughts; nothing decided)

## The product the structure actually supports
Not picks. The repo's defensible thing is honesty with receipts: every forecast is stamped before tip, every claim has n and a CI,
the market comparison is published (the market is better on winners; props are a small, unproven edge), and the explainer can
show "what was known, and when" for any game. The product is a **truth layer for bettors**: track what you bet, see what it was
worth at the price you paid, see what the forecast and the market said at that minute, learn where you are fooling yourself.
Nobody else sells that, because it is bad for a tout's business. It is good for ours because we do not bet and do not take orders.

## Bettor data (the one genuinely new information source)
A PWA where people photograph their slips. OCR → legs, lines, prices, book, time → settled against our box scores. For the user:
an honest ROI and calibration page ("your overs on stars hit 44%; the book priced them at 52%"). For us: a time-stamped record of
what the public actually bets. Our own finding says the public leans over on respected names and the under is the mispriced side
by 1-3 points; slip flow before tip is a pre-tip fact and may sharpen that. It gets a pre-registration (BETTOR_FLOW) and the live
season as its test, like everything else: no assumption that it works. Caveats stated up front: who uploads is not who bets
(selection), n starts tiny, screenshots lie (crop, edit), and slips carry personal data.

## Agents and posting
Content agents draft; a posting agent publishes on a schedule with a kill switch. Devin approves every post for the first
weeks (the agent-team email's "dry run on 20 real items" gate), then the gate opens for the formats that passed. Formats that are
honest and different: the nightly miss explained (a real hit and a real miss, every day); the weekly calibration card; "no bet
tonight" as a first-class post; the craziest parlay that hit, with the footnote; the market-vs-model scoreboard, updated live.
Rules already in force: no orders, no credentials, Claude drafts and Devin owns the accounts, no AI attribution in repo content
(the posts can say the system is automated; that is a disclosure, not attribution), responsible-gambling language and an age
statement on everything, no picks sold as certainties.

## Revenue to break even (costs are tiny)
Costs: hosting a PWA + small API ~$0-20/month; OCR per slip ~$0.01; X API posting ~$0.015/post; the odds history already bought.
Break-even is roughly ten paying users. Candidate models, in the order I would try them:
1. Free tracking, paid "honest edge" ($4-6/month): calibration of YOUR bets, best-EV-at-$20 answers, the explainer per game.
   DECIDED 2026-10-11 (Devin): guidance is the free mode (explainer, what the forecast and the market say, paper tracking of
   your slips); honesty is the paid layer (realised EV with intervals, "this price is fair, sit out", your calibration, the
   closing-line value of your bets). Benchmark for everything shown: the closing line (DECISIONS 2026-10-11 ~22:40).
2. Tip jar / supporter tier (no features) for people who like the honesty.
3. Aggregated public-lean index (what uploaders bet, anonymised) as a dataset or a feed, later, only with real n.
4. Affiliate links to books: the money is real and the conflict is real; if ever, with a disclosure on the page and never tied
   to a recommendation. Devin's call; my vote is no until the honest tier pays its own hosting.
Never: selling picks, "guaranteed" anything, taking stakes.

## Legal and ethical floor (before any public launch)
Age statement (21+ where that is the law), jurisdiction note (we do not offer betting), privacy policy for slip images (store the
extracted fields, delete the image unless the user opts to keep it), terms of use, no scraping of books beyond the licensed API,
responsible-gambling resources on every page, and the published fee-aware "keep your money" answer stays the default.

## Phases, each with a gate
0. Now → 2026-10-20: nothing changes; the live season starts; the forward log is the product's evidence.
1. PWA MVP (2-3 weeks of agent work): slip upload → OCR → settle → personal ROI and calibration page; explainer pages public; no
   accounts beyond an email; Devin reviews the first 20 real slips by hand. Gate: 20 slips parsed correctly end to end.
2. Posting: content agents + Devin-approved posting for 3 weeks; gate: zero factual errors in 20 posts and the calibration card
   matches the ledger; then scheduled posting with a kill switch.
3. BETTOR_FLOW pre-registration once ≥ 500 slips from ≥ 50 uploaders exist; scored on the live log.
4. Revenue tier switched on only when phases 1-2 have run a month clean.

## Decisions only Devin can make
Name and domain; which platforms (X, Reddit, Discord, TikTok); affiliate yes/no; the monthly budget ceiling; whether posting is
auto or approved after the dry run; the age/jurisdiction stance; what the app does for a user outside the US.
