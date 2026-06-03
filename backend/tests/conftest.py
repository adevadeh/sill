"""Shared pytest fixtures and path setup for the sill-memory backend tests.

The backend exposes top-level modules (see ``py-modules`` in pyproject.toml)
rather than a package, so make sure the backend root is importable whether
tests are run from the repo root or from ``backend/``.
"""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
