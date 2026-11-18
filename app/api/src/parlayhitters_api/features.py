"""The API's feature switches. This is the ONLY API file that reads the betting switch.

``PH_BETTING=1`` turns on the betting tools module (``parlayhitters_api.betting``); unset or any
other value is off. It is read once, when ``create_app`` builds the app, so a test can build an app
in either state and a running server never changes state under a request.
"""

from __future__ import annotations

import os

#: The environment variable that switches the betting module on ("1") for the API.
BETTING_ENV = "PH_BETTING"


def betting_enabled() -> bool:
    """True only when the betting switch is exactly "1" in this process's environment."""
    return os.environ.get(BETTING_ENV) == "1"
