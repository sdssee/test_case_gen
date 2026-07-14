# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import unittest
from pathlib import Path


SLOW_TEST_MARKERS = {
    "test_upgrade_merges_codebuddy_settings_and_removes_legacy_delivery_agent",
    "test_upgrade_failure_restores_framework_and_protected_assets",
    "test_upgrade_rejects_allowlist_suffix_collisions_and_missing_execution_binding",
    "test_upgrade_rejects_invalid_existing_codebuddy_settings_without_data_loss",
    "test_upgrade_migrates_asset_schema_1_to_2_without_losing_excel_facts",
}


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


def skipped_required_upgrade_tests(result: unittest.TestResult, platform_name: str = os.name) -> list[str]:
    if platform_name != "nt":
        return []
    return [test.id() for test, _ in result.skipped if any(marker in test.id() for marker in SLOW_TEST_MARKERS)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fast local or full release validation.")
    parser.add_argument("--mode", choices=["fast", "full"], default="full")
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    if args.mode == "full":
        os.environ.pop("TEST_DESIGN_SKIP_UPGRADE_INTEGRATION", None)
    run_command([sys.executable, str(repo_root / "scripts" / "validate-test-design.py")], repo_root)
    run_command([sys.executable, str(repo_root / "scripts" / "sync-rule-entrypoints.py")], repo_root)

    discovered = unittest.defaultTestLoader.discover(str(repo_root / "tests"))
    selected = unittest.TestSuite()
    for test in iter_tests(discovered):
        if args.mode == "fast" and any(marker in test.id() for marker in SLOW_TEST_MARKERS):
            continue
        selected.addTest(test)
    print(f"Running {selected.countTestCases()} test(s) in {args.mode} mode.")
    result = unittest.TextTestRunner(verbosity=2).run(selected)
    if args.mode == "full":
        skipped_slow = skipped_required_upgrade_tests(result)
        if skipped_slow:
            print(f"ERROR: full mode skipped required upgrade integration tests: {skipped_slow}", file=sys.stderr)
            return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
