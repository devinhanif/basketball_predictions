"""AVG shadow: 0.5 pmf(SEQc) + 0.5 pmf(FULL), served as a shadow next to production.

Rule: docs/prereg/AVG_SHADOW_LIVE.md (frozen, sha256 6e57fc11). Own tables (``forward_avg_*``); the
daily ``forward_predictions`` and every scored production table are never written. Flag
``--log-avg-shadow``, default OFF.
"""

RULE_ID = "AVG_SHADOW_LIVE"
RULE_SHA256 = "6e57fc11b2487cff52bd2c27fd43044882c5ddc20a89726a8de31e9085cf6152"
