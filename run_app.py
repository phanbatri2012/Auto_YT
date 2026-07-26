#!/usr/bin/env python3
"""Launch the ChatGPT Auto Login UI."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from auto_yt.app import main

if __name__ == "__main__":
    main()
