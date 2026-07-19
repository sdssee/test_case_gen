# -*- coding: utf-8 -*-
from __future__ import annotations

import py_compile
import json
import re
import sys
from pathlib import Path


REQUIRED = [
    "scripts/test_design/session_runtime.py", "scripts/test_design/formal_assembler.py", "scripts/test_design_cli.py",
    ".codebuddy/skills/test-design/SKILL.md", ".codebuddy/rules/test-design-rule.md",
    "docs/test-design/rules/page-discovery.md", "docs/test-design/rules/case-design.md",
    "docs/test-design/rules/artifact-contract.md",
    "docs/test-design/codebuddy-test-design-template.xlsx", "docs/test-design/测试用例模板.xlsx",
    ".codebuddy/agents/test-page-explorer.md", ".codebuddy/agents/test-design-planner.md",
    ".codebuddy/agents/test-case-author.md", ".codebuddy/agents/test-review-delivery.md",
    ".codebuddy/skills/test-page-exploration/SKILL.md", ".codebuddy/skills/test-design-planning/SKILL.md",
    ".codebuddy/skills/test-case-authoring/SKILL.md", ".codebuddy/skills/test-review-delivery/SKILL.md",
]
REMOVED_NAMES = {
    "page-discovery.csv", "page-element-inventory.csv", "selection-option-observations.csv",
    "configuration-variant-observations.csv", "interaction-branch-observations.csv",
    "test-data-lifecycle.csv", "element-case-plan.csv", "function_cases_manifest.json",
}


def _frontmatter(source: str) -> dict[str, str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", source, re.DOTALL)
    if not match:
        raise ValueError("missing YAML frontmatter")
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
    return result


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
    expected_agents = {
        "test-page-explorer", "test-design-planner", "test-case-author", "test-review-delivery",
    }
    actual_agents = {path.stem for path in (root / ".codebuddy/agents").glob("*.md")}
    if actual_agents != expected_agents:
        raise ValueError(f"manual stage agents differ from the final architecture: {sorted(actual_agents)}")
    for path in (root / ".codebuddy/agents").glob("*.md"):
        source = path.read_text(encoding="utf-8")
        metadata = _frontmatter(source)
        if metadata.get("name") != path.stem or not metadata.get("description"):
            raise ValueError(f"invalid CodeBuddy agent frontmatter: {path}")
        if metadata.get("model"):
            raise ValueError(f"project agents must not bind a model; use the environment subagent default: {path}")
        if "不得调用其他 Agent" not in source:
            raise ValueError(f"stage agent must explicitly forbid recursive delegation: {path}")
    for path in (root / ".codebuddy/skills").glob("*/SKILL.md"):
        source = path.read_text(encoding="utf-8")
        metadata = _frontmatter(source)
        if metadata.get("name") != path.parent.name or not metadata.get("description"):
            raise ValueError(f"invalid CodeBuddy skill frontmatter: {path}")
        if metadata.get("context") == "fork" or metadata.get("model"):
            raise ValueError(f"project skills must run inline without a fork-model dependency: {path}")
        if "TODO" in source or "TBD" in source:
            raise ValueError(f"unfinished CodeBuddy skill: {path}")
    router = (root / ".codebuddy/skills/test-design/SKILL.md").read_text(encoding="utf-8")
    if "Agent 不可用" not in router or "execute_request" not in router:
        raise ValueError("test-design router lacks the same-quality Agent fallback")
    schema = json.loads((root / "docs/test-design/schemas/product-facts.schema.json").read_text(encoding="utf-8"))
    item_required = schema["properties"]["facts"]["additionalProperties"]["items"]["required"]
    if "evidence" in item_required:
        raise ValueError("product fact archive still requires evidence")
    for path in (root / "scripts").rglob("*.py"):
        py_compile.compile(str(path), doraise=True)
    print("OK: compact manual-stage-agent architecture and single-session fallback are structurally valid.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
