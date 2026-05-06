"""
Test configuration and shared stubs.

Stubs system-level libraries that may not be available in all environments
(e.g. libmagic) so that test collection succeeds without the native dependency.
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.gettempdir()}/reelpush-test-{os.getpid()}.db"

if "magic" not in sys.modules:
    sys.modules["magic"] = MagicMock()
