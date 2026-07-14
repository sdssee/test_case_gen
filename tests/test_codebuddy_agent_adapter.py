# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / ".codebuddy" / "agents"
COMMAND_PATH = REPO_ROOT / ".codebuddy" / "commands" / "test-design-run.md"
SETTINGS_PATH = REPO_ROOT / ".codebuddy" / "settings.json"
GUARD_PATH = REPO_ROOT / ".codebuddy" / "hooks" / "guard-agent-tool.py"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate-test-design.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "test_design_static_validator", VALIDATOR_PATH
)
assert VALIDATOR_SPEC is not None and VALIDATOR_SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(VALIDATOR_SPEC)
sys.modules[VALIDATOR_SPEC.name] = VALIDATOR
VALIDATOR_SPEC.loader.exec_module(VALIDATOR)

REQUIRED_AGENTS = {
    "test-discovery.md": "test-discovery",
    "test-plan-dfx.md": "test-plan-dfx",
    "test-risk-arbiter.md": "test-risk-arbiter",
    "test-case-worker.md": "test-case-worker",
    "test-reviewer.md": "test-reviewer",
}

REQUIRED_AGENT_BODY_MARKERS = (
    "agent-task.json",
    "source_fingerprint",
    "allowed_output_files",
    "allowed_output_prefixes",
    "AgentResult",
    "contract_input_files",
    "required_gate",
    "produced_files",
    "execution_id",
)

EXPECTED_TOOLS = {
    "test-discovery.md": {
        "Read", "Write", "ToolSearch", "DeferExecuteTool", "WaitForMcpServers",
    },
    "test-plan-dfx.md": {"Read", "Write"},
    "test-risk-arbiter.md": {"Read", "Write"},
    "test-case-worker.md": {"Read", "Write"},
    "test-reviewer.md": {"Read", "Write"},
}

GUARD_MATCHER = (
    "^(?:Read|Write|Edit|MultiEdit|NotebookEdit|Grep|Glob|Bash|PowerShell|"
    "ToolSearch|DeferExecuteTool|WaitForMcpServers|mcp__.*)$"
)


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    """Parse the deliberately flat YAML subset used by project Agent files."""

    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError(f"{path} must start with YAML frontmatter")
    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise AssertionError(f"{path} has no closing YAML frontmatter delimiter") from exc

    values: dict[str, str] = {}
    for line in lines[1:closing_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise AssertionError(f"{path} contains unsupported frontmatter line: {line!r}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        if key in values:
            raise AssertionError(f"{path} contains duplicate frontmatter key: {key}")
        values[key] = value.strip().strip("\"'")
    return values, "\n".join(lines[closing_index + 1 :])


class CodeBuddyAgentAdapterTests(unittest.TestCase):
    def test_required_project_agents_have_complete_runtime_contracts(self) -> None:
        self.assertTrue(AGENTS_DIR.is_dir(), ".codebuddy/agents must exist")

        for filename, expected_name in REQUIRED_AGENTS.items():
            with self.subTest(agent=expected_name):
                path = AGENTS_DIR / filename
                self.assertTrue(path.is_file(), f"missing project Agent definition: {filename}")
                frontmatter, body = parse_frontmatter(path)

                self.assertEqual(expected_name, frontmatter.get("name"))
                self.assertTrue(frontmatter.get("description", "").strip())
                self.assertEqual("inherit", frontmatter.get("model", "").lower())
                self.assertNotIn("skills", frontmatter)
                tools = {
                    value.strip()
                    for value in frontmatter.get("tools", "").split(",")
                    if value.strip()
                }
                self.assertEqual(EXPECTED_TOOLS[filename], tools)
                self.assertNotIn("Bash", tools)
                self.assertNotIn("Edit", tools)
                self.assertNotIn("Browser", tools)
                self.assertNotIn("ComputerUse", tools)

                for marker in REQUIRED_AGENT_BODY_MARKERS:
                    self.assertIn(marker, body, f"{filename} must document {marker}")
                if filename == "test-discovery.md":
                    self.assertNotIn(
                        "APPROVED_PAGE_MCP=mcp__",
                        body,
                        "Agent body must not inject a fake approved MCP into the transcript",
                    )

    def test_delivery_remains_deterministic_and_is_not_a_model_agent(self) -> None:
        self.assertFalse(
            (AGENTS_DIR / "test-delivery.md").exists(),
            "Delivery must remain the deterministic single-writer command",
        )

        if not AGENTS_DIR.is_dir():
            self.fail(".codebuddy/agents must exist")
        declared_names = {
            parse_frontmatter(path)[0].get("name", "")
            for path in AGENTS_DIR.glob("*.md")
        }
        self.assertNotIn("test-delivery", declared_names)

    def test_coordinator_command_covers_native_parallel_and_safe_fallbacks(self) -> None:
        self.assertTrue(COMMAND_PATH.is_file(), "missing project coordinator command")
        command = COMMAND_PATH.read_text(encoding="utf-8-sig")

        for marker in (
            "$1",
            "agent-run",
            "agent-claim",
            "agent-submit",
            "agent-release",
            "execution_id",
            "executor_id",
            "wave_id",
            "confirm-no-side-effects",
            "runnable_tasks",
            "task_id",
            "Case",
            "Reviewer",
            "complete-deliverables",
            "/agents",
            "/hooks",
            "guard-agent-tool.py",
            "codebuddy-main-session",
            "全部释放成功后",
            "PAGE_PROBE_RECORD",
            "page-probe-commit",
            "--page-probe-receipt-id",
            "--page-probe-receipt-fingerprint",
            "不得把页面 MCP allowlist",
            "不得 claim Discovery",
            "没有也不得新增 `execution_id`",
            "本批预计使用的全部 exact page tools",
            "不得逐个执行后立即提交",
            "收齐整个 wave",
        ):
            self.assertIn(marker, command, f"coordinator command must document {marker}")

        for marker in ("冻结", "波次", "去重", "并行", "串行", "降级", "独立"):
            self.assertIn(marker, command, f"coordinator command must document {marker}")

    def test_project_hook_has_exact_guard_configuration(self) -> None:
        self.assertTrue(SETTINGS_PATH.is_file(), "missing .codebuddy/settings.json")
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
        entries = settings.get("hooks", {}).get("PreToolUse", [])
        guard_entries = []
        for entry in entries:
            guard_hooks = [
                hook
                for hook in entry.get("hooks", [])
                if hook.get("type") == "command"
                and "guard-agent-tool.py" in hook.get("command", "")
            ]
            if guard_hooks:
                self.assertEqual(GUARD_MATCHER, entry.get("matcher"))
                self.assertEqual(1, len(guard_hooks))
                guard_entries.append(entry)
        self.assertEqual(1, len(guard_entries), "exactly one test-design guard hook is required")
        guard_hooks = [
            hook
            for hook in guard_entries[0].get("hooks", [])
            if hook.get("type") == "command"
            and "guard-agent-tool.py" in hook.get("command", "")
        ]
        self.assertEqual(1, len(guard_hooks))
        command = guard_hooks[0].get("command", "")
        self.assertIn("python3", command)
        self.assertIn("elif python ", command)
        self.assertGreaterEqual(command.count("exit 2"), 2)
        self.assertIn("$CODEBUDDY_PROJECT_DIR", command)
        self.assertIn("guard-agent-tool.py", command)

        self.assertTrue(GUARD_PATH.is_file(), "missing deterministic Agent tool guard")
        guard = GUARD_PATH.read_text(encoding="utf-8-sig")
        for marker in (
            "TEST_DESIGN_AGENT_GUARD",
            "allowed_output_files",
            "allowed_output_prefixes",
            "permissionDecision",
            "subagents",
            "casefold",
            "input_snapshot_fingerprint",
            "events.jsonl",
            "page_probe_receipt_fingerprint",
            "approved_page_mcp_tools",
            "_validate_page_probe_receipt",
            "_deferred_input_selects",
            "mcp__",
        ):
            self.assertIn(marker, guard)

    def test_formal_codebuddy_files_reject_legacy_prompt_page_authority(self) -> None:
        legacy_marker = "APPROVED" + "_PAGE_MCP"
        formal_files = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "CODEBUDDY.md",
            REPO_ROOT / "docs" / "CODEBUDDY_AGENT_ADAPTER.md",
            COMMAND_PATH,
            *sorted(AGENTS_DIR.glob("*.md")),
        ]
        for path in formal_files:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertNotIn(
                    legacy_marker,
                    path.read_text(encoding="utf-8-sig"),
                    "formal adapter files must derive page authority from a durable receipt",
                )

    def test_adapter_document_distinguishes_registry_tasks_and_runtime_modes(self) -> None:
        path = REPO_ROOT / "docs" / "CODEBUDDY_AGENT_ADAPTER.md"
        text = path.read_text(encoding="utf-8-sig")
        for marker in (
            "CodeBuddy Code 的 `/agents`",
            "IDE 的 Agent 页面主要展示任务/会话",
            "不会因为导入项目就自动变成 5 个已运行任务卡片",
            "不能内嵌",
            "不支持后台并行",
            "冻结 wave",
            "实验性",
            "正式流程阻断",
        ):
            self.assertIn(marker, text, f"adapter document must explain: {marker}")

    def test_static_validator_allows_other_pretooluse_hooks_but_rejects_duplicate_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPO_ROOT / ".codebuddy", root / ".codebuddy")
            settings_path = root / ".codebuddy" / "settings.json"
            settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
            settings["hooks"]["PreToolUse"].insert(
                0,
                {
                    "matcher": "^Read$",
                    "hooks": [{"type": "command", "command": "echo unrelated"}],
                },
            )
            settings_path.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            VALIDATOR.read_text.cache_clear()
            VALIDATOR.validate_codebuddy_agent_adapter(root)

            guard_entries = [
                entry
                for entry in settings["hooks"]["PreToolUse"]
                if any(
                    hook.get("type") == "command"
                    and "guard-agent-tool.py" in hook.get("command", "")
                    for hook in entry.get("hooks", [])
                )
            ]
            self.assertEqual(1, len(guard_entries))
            settings["hooks"]["PreToolUse"].append(
                json.loads(json.dumps(guard_entries[0]))
            )
            settings_path.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            VALIDATOR.read_text.cache_clear()
            with self.assertRaisesRegex(AssertionError, "exactly one test-design guard entry"):
                VALIDATOR.validate_codebuddy_agent_adapter(root)


if __name__ == "__main__":
    unittest.main()
