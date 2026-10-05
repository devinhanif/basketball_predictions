"""Shared CLI-contract guard for every ``run_<rung>.py`` entry point.

CLAUDE.md's "Registry retrofit" section fixes a standard flag contract
(``--register``, ``--promote``, ``--backtest``, ``--debug``, ``--scenario``)
across every model script, with one explicit invariant: ``--scenario`` and
``--promote`` can never be combined (a what-if run must never also be a
production promotion). This module owns that invariant so every rung's CLI
can import and call it without re-deriving the rule.
"""

from __future__ import annotations


def validate_cli_flags(*, promote: bool, scenario: bool) -> None:
    """Raise ``ValueError`` if ``--promote`` and ``--scenario`` are both set."""
    if promote and scenario:
        raise ValueError(
            "--promote and --scenario cannot be combined: a what-if scenario run "
            "must never be promoted to production"
        )
