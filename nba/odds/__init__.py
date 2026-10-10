"""Read-only odds adapter for theoddsapi.com (api.theoddsapi.com; NOT the-odds-api.com).

Public market data only: GET endpoints, a key read from the ``ODDS_API_KEY`` environment variable
(never stored, logged or echoed), no order or account-mutation paths. The daily job must never
depend on this package: every failure is a log line and an informational exit code.
"""
