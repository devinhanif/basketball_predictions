"""Paper robo-trader: build, monitor and settle paper positions against public Kalshi prices.

Log only. Nothing here can place, cancel or simulate placing an order; entries and exits are rows in
``data/papertrade/papertrade.duckdb`` priced at the real ask or bid seen at ``made_at``.
"""
