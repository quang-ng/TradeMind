"""Offline historical replay for TradeMind's deterministic trading controls."""

import sys
from pathlib import Path

# The monorepo's runtime packages live below ``services/`` and are copied to
# container roots in production rather than installed as one wheel. Make the
# same imports available to this repository-local CLI without requiring users
# to remember a PYTHONPATH prefix.
_SERVICES = Path(__file__).resolve().parents[2] / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))
