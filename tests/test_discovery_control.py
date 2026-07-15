# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from test_design.batch import init_batch_run
from test_design.discovery_control import (
    begin_obligation,
    build_obligations,
    canonical_element_type,
    canonical_interaction,
    complete_obligation,
    discovery_status,
)
from test_design.validators.batch_ledgers import validate_interaction_branch_rows


class LeftShiftDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        templates = self.root / "docs" / "test-assets" / "batch-runs" / "templates"
        templates.parent.mkdir(parents=True)
        shutil.copytree(REPO_ROOT / "docs" / "test-assets" / "batch-runs" / "templates", templates)
        self.run_dir = init_batch_run(
            self.root,
            "left-shift",
            "一级>二级>页面",
            "BATCH-001",
            "测试产品",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_inventory(
        self,
        *,
        element_type: str = "textbox",
        interaction: str = "填写",
        element_name: str = "名称",
        page: str = "创建对话框",
    ) -> None:
        path = self.run_dir / "page-element-inventory.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            headers = next(csv.reader(stream))
        row = {header: "" for header in headers}
        row.update(
            {
                "批次ID": "BATCH-001",
                "最小标题路径": "一级>二级>页面",
                "页面/入口": page,
                "角色/权限": "管理员",
                "数据状态": "新建",
                "交互实例ID": "INTR-001",
                "采集快照ID": "SNAP-001",
                "元素指纹": "name-field",
                "元素名称/文案": element_name,
                "元素类型": element_type,
                "交互方式": interaction,
                "可交互状态": "是",
                "DOM/可访问性定位": f"role={element_type},name={element_name}",
                "发现来源": "DOM",
                "证据路径": "artifacts/screenshots/inventory.txt",
                "证据定位": "名称输入框",
            }
        )
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerow(row)

    def add_events(
        self, obligation_id: str, *, changed: bool = True, operation: str = "input"
    ) -> tuple[str, str, str]:
        control = self.run_dir / "artifacts" / "discovery-control"
        active = json.loads((control / "active-obligation.json").read_text(encoding="utf-8"))
        first = int(active["first_event_sequence"])
        records = []
        for offset, (kind, operation, response_hash) in enumerate(
            [
                ("read", "read", "before"),
                ("mutation", operation, "mutation"),
                ("read", "read", "after" if changed else "before"),
            ]
        ):
            sequence = first + offset
            records.append(
                {
                    "version": 1,
                    "record_id": f"record-{sequence}",
                    "sequence": sequence,
                    "session_sha256": "session",
                    "transcript_sha256": "transcript",
                    "tool_name": "mcp__browser__tool",
                    "tool_input_sha256": f"input-{sequence}",
                    "tool_response_sha256": response_hash,
                    "response_nonempty": True,
                    "response_error": False,
                    "operation_kind": kind,
                    "operation_name": operation,
                }
            )
        (control / "action-events.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        return tuple(record["record_id"] for record in records)  # type: ignore[return-value]

    def complete_first(self, *, changed: bool = True) -> str:
        status = discovery_status(self.run_dir)
        obligation = status["next_obligation"]
        obligation_id = obligation["obligation_id"]
        branch = obligation["branch"]
        begin_obligation(self.run_dir, obligation_id)
        operation = obligation["required_operation"]
        if operation == "expand":
            operation = "click"
        before, mutation, after = self.add_events(obligation_id, changed=changed, operation=operation)
        evidence = self.run_dir / "artifacts" / "screenshots" / "branch.txt"
        evidence.write_text("before -> input -> validation -> restored", encoding="utf-8")
        complete_obligation(
            self.run_dir,
            obligation_id,
            before,
            mutation,
            after,
            "artifacts/screenshots/branch.txt",
            f"{branch}执行后状态",
            "名称输入框为空且可输入",
            f"在名称输入框执行{branch}并触发校验",
            f"页面展示{branch}对应的确定校验结果",
            "已恢复为空白可继续测试状态",
        )
        return obligation_id

    def write_discovery_observation(self, *, element_name: str, element_type: str, interaction: str) -> None:
        path = self.run_dir / "page-discovery.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            headers = next(csv.reader(stream))
        row = {header: "" for header in headers}
        row.update({
            "批次ID": "BATCH-001",
            "最小标题路径": "一级>二级>页面",
            "页面/入口": "创建对话框",
            "角色/权限": "管理员",
            "数据状态": "新建",
            "交互实例ID": "INTR-001",
            "元素名称/文案": element_name,
            "元素类型": element_type,
            "交互方式": interaction,
            "预期/观察行为": "点击后确认弹窗打开并展示确定、取消和关闭入口",
            "结果分支/后续状态": "确认弹窗打开",
        })
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerow(row)

    def test_semantic_aliases_expand_before_execution(self) -> None:
        self.write_inventory(element_type="textbox", interaction="填写")
        self.assertEqual("textbox", canonical_element_type("文本框"))
        self.assertEqual("input", canonical_interaction("填写"))
        obligations = build_obligations(self.run_dir)
        self.assertEqual(set((item["kind"], item["branch"]) for item in obligations), {
            ("input", "正常输入"),
            ("input", "空值"),
            ("input", "边界输入"),
            ("input", "非法输入"),
        })
        status = discovery_status(self.run_dir)
        self.assertEqual("DISCOVERY_EXECUTION_REQUIRED", status["state"])
        self.assertEqual(4, status["pending_count"])

    def test_completion_requires_changed_read(self) -> None:
        self.write_inventory()
        with self.assertRaisesRegex(ValueError, "identical"):
            self.complete_first(changed=False)

    def test_local_repair_preserves_completed_equivalent_element(self) -> None:
        self.write_inventory(element_type="textbox", interaction="填写")
        completed = self.complete_first()
        self.write_inventory(element_type="文本框", interaction="input")
        status = discovery_status(self.run_dir)
        self.assertEqual(3, status["pending_count"])
        completed_ids = {
            json.loads(line)["obligation_id"]
            for line in (self.run_dir / "artifacts" / "discovery-control" / "completions.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        }
        self.assertIn(completed, completed_ids)

    def test_completion_automatically_materializes_branch_ledger(self) -> None:
        self.write_inventory()
        self.complete_first()
        with (self.run_dir / "interaction-branch-observations.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(1, len(rows))
        self.assertEqual("输入", rows[0]["分支类别"])
        self.assertEqual("正常输入", rows[0]["分支动作"])
        self.assertEqual("是", rows[0]["是否实际执行"])
        self.assertIn("名称输入框", rows[0]["执行动作"])
        self.assertTrue(rows[0]["证据路径"].startswith("artifacts/"))

    def test_all_input_branches_close_before_discovery_gate(self) -> None:
        self.write_inventory()
        for _ in range(4):
            self.complete_first()
        self.assertEqual("DISCOVERY_EXECUTION_COMPLETE", discovery_status(self.run_dir)["state"])
        with (self.run_dir / "interaction-branch-observations.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as stream:
            branch_rows = list(csv.DictReader(stream))
        discovery = {
            "最小标题路径": "一级>二级>页面",
            "交互实例ID": "INTR-001",
            "页面/入口": "创建对话框",
            "元素名称/文案": "名称",
            "元素类型": "文本框",
            "交互方式": "填写",
        }
        validate_interaction_branch_rows(
            [discovery], [], branch_rows,
            lambda raw: (self.run_dir / raw).is_file(),
        )

    def test_codebuddy_hook_records_hashes_without_raw_page_content(self) -> None:
        self.write_inventory()
        obligation = discovery_status(self.run_dir)["next_obligation"]
        begin_obligation(self.run_dir, obligation["obligation_id"])
        hook = REPO_ROOT / ".codebuddy" / "hooks" / "record-discovery-action.py"
        payloads = [
            ("Browser", {"request": {"action": "snapshot"}}, {"page": "before-sensitive-content"}),
            ("ComputerUse", {"request": {"action": "fill"}, "value": "AI_TEST_secret"}, {"ok": True}),
            ("DeferExecuteTool", {"tool_name": "Browser", "args": {"action": "snapshot"}}, {"page": "after-sensitive-content"}),
        ]
        environment = dict(os.environ, CODEBUDDY_PROJECT_DIR=str(self.root))
        for tool_name, tool_input, response in payloads:
            completed = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps({
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_response": response,
                    "session_id": "session-001",
                    "transcript_path": "history.jsonl",
                }, ensure_ascii=False),
                text=True,
                encoding="utf-8",
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
        events_path = self.run_dir / "artifacts" / "discovery-control" / "action-events.jsonl"
        raw = events_path.read_text(encoding="utf-8")
        events = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual(["read", "mutation", "read"], [event["operation_kind"] for event in events])
        self.assertEqual("input", events[1]["operation_name"])
        self.assertNotIn("AI_TEST_secret", raw)
        self.assertNotIn("sensitive-content", raw)

    def test_inventory_is_required_before_free_exploration(self) -> None:
        status = discovery_status(self.run_dir)
        self.assertEqual("INVENTORY_REQUIRED", status["state"])
        self.assertIsNone(status["next_obligation"])

    def test_observed_modal_expands_only_that_control_into_followup_branches(self) -> None:
        self.write_inventory(element_type="button", interaction="点击", element_name="危险操作")
        initial = discovery_status(self.run_dir)
        self.assertEqual("interaction", initial["next_obligation"]["kind"])
        self.complete_first()
        self.write_discovery_observation(element_name="危险操作", element_type="按钮", interaction="点击")
        expanded = discovery_status(self.run_dir)
        self.assertEqual(5, expanded["pending_count"])
        self.assertEqual({"INTR-001": 5}, expanded["pending_by_element"])
        self.assertEqual("modal", expanded["next_obligation"]["kind"])

    def test_editable_field_uses_one_committed_effect_obligation(self) -> None:
        self.write_inventory(page="编辑配置页面")
        effects = [item for item in build_obligations(self.run_dir) if item["kind"] == "mutation-effect"]
        self.assertEqual(1, len(effects))
        self.assertTrue(effects[0]["requires_commit"])
        begin_obligation(self.run_dir, effects[0]["obligation_id"])
        before, mutation, after = self.add_events(effects[0]["obligation_id"], operation="input")
        evidence = self.run_dir / "artifacts" / "screenshots" / "edit.txt"
        evidence.write_text("字段修改后保存并重新打开", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "later save/submit click"):
            complete_obligation(
                self.run_dir,
                effects[0]["obligation_id"],
                before,
                mutation,
                after,
                "artifacts/screenshots/edit.txt",
                "保存后重新打开",
                "名称字段显示修改前值",
                "输入新的测试名称",
                "保存成功且重新打开后回显新名称",
                "页面已恢复可继续测试状态",
            )


if __name__ == "__main__":
    unittest.main()
