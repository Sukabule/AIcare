"""Ch08 labeled 18-case intent check; use --resume to avoid repeated paid calls."""

import argparse
import asyncio

from eval_prompt_ch06 import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run("intent", 0, False, args.resume, ch08=True)))
