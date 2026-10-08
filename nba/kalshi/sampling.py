"""Sample-size guardrail for Kalshi market comparisons.

CLAUDE.md, Phase 3: "Expect thin history ... Report sample sizes with every
comparison and refuse to claim superiority from fewer than the configured
minimum number of settled markets." This module is the single place that
rule is enforced so every caller (parlay EV reports, calibration reports,
model-vs-market comparisons) gets the same refusal behavior instead of each
reimplementing (and potentially forgetting) the check.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Default floor below which a comparison is reported but not trusted.
#: Deliberately small and overridable -- Kalshi NBA player-prop history is
#: shallow (new product, limited players); this is a config value, not a
#: statistical claim.
DEFAULT_MIN_SETTLED_MARKETS = 30


@dataclass(frozen=True)
class SampleSizeVerdict:
    """Result of checking a comparison's sample size against the configured floor."""

    n: int
    minimum: int
    sufficient: bool

    def require_sufficient(self, context: str = "this comparison") -> None:
        """Raise loudly if ``n`` is below the floor.

        Call this at the point a caller is about to assert
        superiority/inferiority (e.g. "model beats market price"); use the
        bare ``sufficient`` flag instead where a softer
        ``no_positive_ev_found``-style verdict is more appropriate than an
        exception.
        """
        if not self.sufficient:
            raise InsufficientSampleError(
                f"refusing to draw a conclusion for {context}: n={self.n} settled "
                f"markets < configured minimum {self.minimum}"
            )


class InsufficientSampleError(RuntimeError):
    """Raised when a comparison is attempted on too few settled markets."""


def check_sample_size(n: int, *, minimum: int = DEFAULT_MIN_SETTLED_MARKETS) -> SampleSizeVerdict:
    """Check ``n`` settled markets against ``minimum`` (config-driven, no hardcoded rate)."""
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    return SampleSizeVerdict(n=n, minimum=minimum, sufficient=n >= minimum)


def describe(n: int, *, minimum: int = DEFAULT_MIN_SETTLED_MARKETS) -> str:
    """Human-readable, always-attached sample-size caveat for reports."""
    verdict = check_sample_size(n, minimum=minimum)
    if verdict.sufficient:
        return f"n={n} settled markets (>= minimum {minimum})"
    return (
        f"n={n} settled markets (< minimum {minimum} -- "
        "sample too thin to claim any edge; reporting for visibility only"
    )
