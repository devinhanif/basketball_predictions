"""Per-team Discord live cards (formatting and delivery only; DECISIONS 2026-11-09).

Nothing in this package prices, sizes, labels or decides anything: every number on a card is read
from a value another JARVIS module already computed (a ``Play``, a halftime-scorer row, the paper
book), and every sentence is a deterministic template over those numbers. It never imports the
model, truth or daily packages and never places an order.
"""
