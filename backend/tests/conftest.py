"""
Test configuration and shared stubs.

Stubs system-level libraries that may not be available in all environments
(e.g. libmagic) so that test collection succeeds without the native dependency.
"""

import sys
from unittest.mock import MagicMock

if "magic" not in sys.modules:
    sys.modules["magic"] = MagicMock()
