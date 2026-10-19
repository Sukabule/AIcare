"""Run the same service-independent checks locally and in GitHub Actions."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Keep integration tests requiring MySQL, Milvus or paid APIs outside this list.
TEST_FILES = (
    "test_ch05_routing.py",
    "test_ch06_logic.py",
    "test_ch09_confidence.py",
    "test_ch04_retrieval.py",
    "test_context_memory.py",
    "test_tool_budget.py",
    "test_ch08_registry.py",
    "test_ch08_engine.py",
    "test_pages.py",
    "test_removed_topics.py",
    "test_container_config.py",
)


def main() -> int:
    commands = (
        ("ruff", "check", "."),
        ("ruff", "format", "--check", "."),
        ("pytest", *(f"tests/{name}" for name in TEST_FILES), "-q"),
    )
    for args in commands:
        print(f"> {sys.executable} -m {' '.join(args)}", flush=True)
        result = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
