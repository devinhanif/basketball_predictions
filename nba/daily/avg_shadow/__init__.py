"""AVG shadow: 0.5 pmf(SEQc) + 0.5 pmf(FULL), served as a shadow next to production.

Rule: docs/prereg/AVG_SHADOW_LIVE_2.md (frozen, sha256 9afbfeb6). It supersedes AVG_SHADOW_LIVE
(6e57fc11) and changes only G1/P3. Own tables (``forward_avg_*``); the daily
``forward_predictions`` and every scored production table are never written. Flag
``--log-avg-shadow``, default OFF.
"""

RULE_ID = "AVG_SHADOW_LIVE_2"
RULE_SHA256 = "9afbfeb6e16449f334c159ab9457167b760c03e31692962469b85ee32c92fa87"
