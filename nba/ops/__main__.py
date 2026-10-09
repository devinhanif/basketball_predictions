"""``python -m nba.ops`` delegates to the watchdog CLI."""

import sys

from nba.ops.watchdog import main

sys.exit(main())
