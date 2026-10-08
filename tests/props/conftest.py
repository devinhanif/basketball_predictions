"""Import LightGBM before anything can import torch.

Both ship their own OpenMP runtime; on macOS, loading torch first makes the
first LightGBM ``fit`` segfault (seen when ``tests/props`` runs before other
modules that import torch). conftest files load before test modules are
collected, so importing here fixes the order for the whole session.
"""

import lightgbm  # noqa: F401
