import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "services"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
