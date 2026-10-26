"""Read-only fast pre-tip poller and cross-venue (Kalshi vs Polymarket) arbitrage / lag checker.

Public GETs only; no credentials, no orders, no execution code. See
docs/reviews/fastarb_2026-10-25.md and configs/fastarb.yaml. The only secret that can be in the
process environment is the existing odds-client key variable, used by that client for Pinnacle.
"""
