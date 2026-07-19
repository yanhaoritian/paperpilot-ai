import sys
from pathlib import Path

# Allow `pytest` from backend/ with `app` package importable
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
