#!/usr/bin/env python3
"""Entry point. The implementation lives in scripts/autopost/.

    python scripts/publish.py --help

Kept at this path so existing workflows and bookmarks keep working.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autopost.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
