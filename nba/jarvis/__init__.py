"""JARVIS (paper): an LLM-enabled betting assistant that is alert-only and read-only.

It always names the best available play with an honest, computed edge label, posts it to the
maintainer through Discord (the default sink, ``delivery.sinks`` in configs/jarvis.yaml; macOS
Messages is an optional second sink), and records every play in its own paper book scored by
outcome and closing-line value. It never places an order, never holds a credential, and the
language model cannot change a number, a label, a stake or whether a message is sent. DECISIONS
2026-10-25.
"""
