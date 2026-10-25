Recorded 2026-10-25 from public endpoints (no key), trimmed to DET@PHI (tip 2026-10-25 23:30Z):

* `kalshi_detphi.json`: KXNBAGAME / KXNBASPREAD (lines 1.5, 3.5, 4.5) / KXNBATOTAL (225.5, 227.5) open markets, rules text dropped.
* `pm_us_event_detphi.json`: Polymarket US event (`nba-det-phi-2026-10-25`) filtered by `us_filter_event` to the winner, three spreads, three totals.
* `pm_us_book_ml.json`: the moneyline `/book`, first six levels per side.
* `pm_intl_event_detphi.json`: gamma event trimmed to moneyline, spreads -1.5/-3.5, totals 226.5/227.5.

The Pinnacle body is synthetic (`PIN_BODY` in `test_fastarb.py`); no key was available when this was written.
