"""Compatibility entry point for the production log analyzer."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.scripts.analyze_production_logs import main


if __name__ == "__main__":
    raise SystemExit(main())
