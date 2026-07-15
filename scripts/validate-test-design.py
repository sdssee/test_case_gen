# -*- coding: utf-8 -*-
from __future__ import annotations

import py_compile
import json
import sys
from pathlib import Path


REQUIRED = [
    "scripts/test_design/session_runtime.py", "scripts/test_design/formal_assembler.py", "scripts/test_design_cli.py",
    ".codebuddy/skills/test-design/SKILL.md", ".codebuddy/rules/test-design-rule.md",
    "docs/test-design/rules/page-discovery.md", "docs/test-design/rules/case-design.md",
    "docs/test-design/rules/artifact-contract.md",
    "docs/test-design/codebuddy-test-design-template.xlsx", "docs/test-design/测试用例模板.xlsx",
]
REMOVED_NAMES = {
    "page-discovery.csv", "page-element-inventory.csv", "selection-option-observations.csv",
    "configuration-variant-observations.csv", "interaction-branch-observations.csv",
    "test-data-lifecycle.csv", "element-case-plan.csv", "function_cases_manifest.json",
}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    missing = [path for path in REQUIRED if not (root / path).is_file()]
    if missing:
        raise ValueError(f"required final-architecture files are missing: {missing}")
    forbidden_files = [path for path in root.rglob("*") if path.is_file() and path.name in REMOVED_NAMES]
    if forbidden_files:
        raise ValueError(f"legacy ledger artifacts still exist: {forbidden_files}")
    forbidden_runtime = [
        root / "scripts/test_design/batch.py", root / "scripts/test_design/discovery_control.py",
        root / "scripts/test_design/pipeline.py", root / ".codebuddy/hooks",
    ]
    remaining = [path for path in forbidden_runtime if path.exists()]
    if remaining:
        raise ValueError(f"legacy obligation/Hook runtime still exists: {remaining}")
    cli_source = (root / "scripts/test_design_cli.py").read_text(encoding="utf-8")
    forbidden_commands = ("init-run", "agent-run", "validate-stage", "record-observation", "complete-deliverables")
    leaked_commands = [command for command in forbidden_commands if command in cli_source]
    if leaked_commands:
        raise ValueError(f"legacy user-facing commands still exist: {leaked_commands}")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    if "TD-GATE" in agents or "TEST-DESIGN-GENERATED" in agents:
        raise ValueError("AGENTS.md still contains generated legacy gate rules")
    schema = json.loads((root / "docs/test-design/schemas/product-facts.schema.json").read_text(encoding="utf-8"))
    item_required = schema["properties"]["facts"]["additionalProperties"]["items"]["required"]
    if "evidence" in item_required:
        raise ValueError("product fact archive still requires evidence")
    for path in (root / "scripts").rglob("*.py"):
        py_compile.compile(str(path), doraise=True)
    print("OK: compact single-session architecture is structurally valid.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
