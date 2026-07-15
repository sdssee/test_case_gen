# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import subprocess
import sys
import unittest
from pathlib import Path


def iter_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_tests(item)
        else:
            yield item


def run_command(args: list[str], cwd: Path) -> None:
    completed = subprocess.run(args, cwd=cwd, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fast local or full release validation.")
    parser.add_argument("--mode", choices=["fast", "full"], default="full")
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    run_command([sys.executable, str(repo_root / "scripts" / "validate-test-design.py")], repo_root)

    discovered = unittest.defaultTestLoader.discover(str(repo_root / "tests"))
    selected = unittest.TestSuite()
    for test in iter_tests(discovered):
        selected.addTest(test)
    print(f"Running {selected.countTestCases()} test(s) in {args.mode} mode.")
    result = unittest.TextTestRunner(verbosity=2).run(selected)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
