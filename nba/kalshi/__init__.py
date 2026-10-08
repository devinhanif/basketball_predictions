"""Read-only Kalshi public-market-data ingestor (see CLAUDE.md Phase 3).

No authentication, no order/trading endpoints -- see ``client.py``'s
module docstring for the exact guarantee.
"""

from nba.kalshi.aliases import UnmatchedKalshiNameError, resolve_kalshi_player_name
from nba.kalshi.cutoff import CutoffInfo, parse_cutoff_response, select_tier
from nba.kalshi.parse import parse_candlesticks_frame, parse_market, parse_markets_frame
from nba.kalshi.sampling import SampleSizeVerdict, check_sample_size
from nba.kalshi.thresholds import UnparseableTitleError, parse_threshold_title

__all__ = [
    "CutoffInfo",
    "SampleSizeVerdict",
    "UnmatchedKalshiNameError",
    "UnparseableTitleError",
    "check_sample_size",
    "parse_candlesticks_frame",
    "parse_cutoff_response",
    "parse_market",
    "parse_markets_frame",
    "parse_threshold_title",
    "resolve_kalshi_player_name",
    "select_tier",
]
