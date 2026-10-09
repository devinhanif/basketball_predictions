"""Process exit codes shared by the CLIs and ``ops/*.sh``.

argparse exits 2 on a usage error, so 2 must never double as an "informational" result.
"""

from __future__ import annotations

#: ran fine, but some result is informational (tipped games refused; officials unmapped).
RC_INFORMATIONAL = 4
#: ran and wrote what it could, but a non-primary dependency failed (ingest / schedule fetch).
RC_DEGRADED = 5
#: nba.duckdb is still write-locked by another process after the bounded wait (EX_TEMPFAIL).
RC_DB_BUSY = 75
