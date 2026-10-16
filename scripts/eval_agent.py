"""Compatibility entry; options and execution belong to eval_ch06."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_ch06 import main  # noqa: E402

if __name__ == "__main__":
    main()
