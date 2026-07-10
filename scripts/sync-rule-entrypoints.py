# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or regenerate mirrored CodeBuddy rule entrypoints.")
    parser.add_argument("--write", action="store_true", help="Rewrite rule mirrors from the canonical rule file.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    contract_path = repo_root / "docs" / "test-design" / "rules" / "entry-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    canonical_path = repo_root / contract["canonical_rule"]
    canonical_text = canonical_path.read_text(encoding="utf-8")

    for relative in contract["rule_mirrors"]:
        mirror = repo_root / relative
        if args.write:
            mirror.write_text(canonical_text, encoding="utf-8")
        elif mirror.read_text(encoding="utf-8") != canonical_text:
            raise ValueError(f"Rule mirror drifted from canonical rule: {relative}")

    max_chars = int(contract["max_chars"])
    for relative, markers in contract["entries"].items():
        path = repo_root / relative
        text = path.read_text(encoding="utf-8")
        if len(text) > max_chars:
            raise ValueError(f"Entry exceeds {max_chars} characters: {relative}")
        missing = [marker for marker in markers if marker not in text]
        if missing:
            raise ValueError(f"Entry is missing contract markers: {relative}: {missing}")

    print("OK: rule mirrors and lightweight entry contracts are aligned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
