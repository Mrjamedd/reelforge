"""
Test configuration and shared stubs.

Stubs system-level libraries that may not be available in all environments
(e.g. libmagic) so that test collection succeeds without the native dependency.
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock

test_dir = tempfile.gettempdir()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{test_dir}/reelpush-test-{os.getpid()}.db"
os.environ["LOCAL_STORAGE_PATH"] = f"{test_dir}/reelpush-test-storage-{os.getpid()}"
os.environ["STORAGE_BACKEND"] = "local"

if "magic" not in sys.modules:
    sys.modules["magic"] = MagicMock()
