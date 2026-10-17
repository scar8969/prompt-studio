"""Run the studio.

    python -m app.web.server         # or: python run_studio.py
    python -m app.cli init-key       # print a key for APP_ENCRYPTION_KEY

The old entry point (`src/server.py`) is left in place but is no longer what this
project runs: see docs/limits.md for what changed and why.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.web.server import main  # noqa: E402

if __name__ == "__main__":
    main()
