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

    def add_configuration_variant(
        self,
        variant_id: str,
        category: str,
        value: str,
        *,
        timing: str = "编辑后",
        strategy: str = "复用测试对象修改",
        data_id: str = "CODEX_TEST_CONFIG_001",
    ) -> None:
        path = self.run_dir / "configuration-variant-observations.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = list(reader.fieldnames or [])
            rows = [row for row in reader if row.get("交互实例ID")]
        row = {header: "" for header in headers}
        row.update({
            "批次ID": "BATCH-001",
            "最小标题路径": "一级>二级>页面",
            "交互实例ID": "INTR-001",
            "页面/入口": "编辑配置页面",
            "配置项": "名称",
            "变体ID": variant_id,
            "变体类别": category,
            "配置值/组合": value,
            "生效时机": timing,
            "执行策略": strategy,
            "测试数据ID/名称": data_id,
            "组合覆盖策略": "单项全量+依赖互斥边界+Pairwise",
        })
        rows.append(row)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def add_finite_options(self, *values: str) -> None:
        path = self.run_dir / "selection-option-observations.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            headers = next(csv.reader(stream))
        rows = []
        for index, value in enumerate(values, start=1):
            row = {header: "" for header in headers}
            row.update({
                "批次ID": "BATCH-001",
                "最小标题路径": "一级>二级>页面",
                "交互实例ID": "INTR-001",
                "页面/入口": "编辑配置页面",
                "元素名称/文案": "模式",
                "元素类型": "下拉框",
                "选项集合类型": "有限",
                "可用选项总数": str(len(values)),
                "选项序号": str(index),
                "选项值": value,
            })
            rows.append(row)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

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

    def test_windows_hook_wrapper_works_without_project_environment(self) -> None:
        self.write_inventory()
        obligation = discovery_status(self.run_dir)["next_obligation"]
        begin_obligation(self.run_dir, obligation["obligation_id"])
        hook_dir = self.root / ".codebuddy" / "hooks"
        hook_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO_ROOT / ".codebuddy" / "hooks", hook_dir)
        environment = dict(os.environ)
        environment.pop("CODEBUDDY_PROJECT_DIR", None)
        completed = subprocess.run(
            ["cmd", "/c", str(hook_dir / "run-discovery-recorder.cmd")],
            input=json.dumps({
                "tool_name": "Browser",
                "tool_input": {"request": {"action": "snapshot"}},
                "tool_response": {"page": "before"},
                "session_id": "session-wrapper",
                "transcript_path": "history.jsonl",
            }),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=environment,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        events = self.run_dir / "artifacts" / "discovery-control" / "action-events.jsonl"
        self.assertTrue(events.is_file())

    def test_hook_unavailable_falls_back_to_artifact_evidence(self) -> None:
        self.write_inventory()
        obligation = discovery_status(self.run_dir)["next_obligation"]
        begin_obligation(self.run_dir, obligation["obligation_id"])
        evidence_dir = self.run_dir / "artifacts" / "screenshots"
        for name, value in (("before.txt", "empty input"), ("after.txt", "validation shown"), ("recovery.txt", "input restored")):
            (evidence_dir / name).write_text(value, encoding="utf-8")
        completion = complete_obligation(
            self.run_dir,
            obligation["obligation_id"],
            evidence_mode="artifact",
            before_evidence_path="artifacts/screenshots/before.txt",
            before_evidence_location="before input state",
            after_evidence_path="artifacts/screenshots/after.txt",
            after_evidence_location="validation result state",
            recovery_evidence_path="artifacts/screenshots/recovery.txt",
            recovery_evidence_location="restored input state",
            before_state="输入框为空且可编辑",
            executed_action="输入有效测试值并触发校验",
            observed_result="页面展示该输入对应的确定结果",
            recovery_result="输入框已经恢复初始状态",
        )
        self.assertEqual("ARTIFACT_VERIFIED", completion["evidence_mode"])
        self.assertEqual([], completion["mutation_record_ids"])

    def test_auto_mode_recovers_from_missing_hook_records(self) -> None:
        self.write_inventory()
        obligation = discovery_status(self.run_dir)["next_obligation"]
        begin_obligation(self.run_dir, obligation["obligation_id"])
        evidence_dir = self.run_dir / "artifacts" / "screenshots"
        for name, value in (("auto-before.txt", "before"), ("auto-after.txt", "after"), ("auto-recovery.txt", "recovery")):
            (evidence_dir / name).write_text(value, encoding="utf-8")
        completion = complete_obligation(
            self.run_dir,
            obligation["obligation_id"],
            before_record_id="missing-before",
            mutation_record_id="missing-action",
            after_record_id="missing-after",
            before_evidence_path="artifacts/screenshots/auto-before.txt",
            before_evidence_location="before state",
            after_evidence_path="artifacts/screenshots/auto-after.txt",
            after_evidence_location="after state",
            recovery_evidence_path="artifacts/screenshots/auto-recovery.txt",
            recovery_evidence_location="recovery state",
            before_state="输入框处于初始状态",
            executed_action="输入测试值并触发页面校验",
            observed_result="页面展示输入值对应的明确结果",
            recovery_result="输入框恢复到初始状态",
        )
        self.assertEqual("ARTIFACT_VERIFIED", completion["evidence_mode"])
        self.assertIn("unknown action-event", completion["hook_fallback_reason"])

    def test_trace_evidence_closes_without_hook_events(self) -> None:
        self.write_inventory()
        obligation = discovery_status(self.run_dir)["next_obligation"]
        begin_obligation(self.run_dir, obligation["obligation_id"])
        trace = self.run_dir / "artifacts" / "trace.json"
        trace.write_text('{"events":["before","action","after","recovery"]}', encoding="utf-8")
        completion = complete_obligation(
            self.run_dir,
            obligation["obligation_id"],
            evidence_mode="trace",
            trace_evidence_path="artifacts/trace.json",
            trace_before_location="event=before",
            trace_action_location="event=action",
            trace_after_location="event=after",
            trace_recovery_location="event=recovery",
            before_state="输入框为空且可编辑",
            executed_action="输入边界值并提交校验",
            observed_result="页面展示边界值对应的明确结果",
            recovery_result="页面恢复到可继续测试状态",
        )
        self.assertEqual("TRACE_VERIFIED", completion["evidence_mode"])

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

    def test_editable_configuration_uses_one_transaction_per_variant(self) -> None:
        self.write_inventory(page="编辑配置页面")
        self.assertEqual("CONFIGURATION_VARIANT_PLAN_REQUIRED", discovery_status(self.run_dir)["state"])
        self.add_configuration_variant("CFG-DEFAULT", "默认不配置", "保持默认")
        self.add_configuration_variant("CFG-VALUE", "单值", "新名称")
        effects = [item for item in build_obligations(self.run_dir) if item["kind"] == "configuration-variant"]
        self.assertEqual(2, len(effects))
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

    def test_save_button_is_not_misclassified_as_editable_configuration(self) -> None:
        self.write_inventory(
            page="编辑配置页面", element_type="按钮", interaction="点击", element_name="保存"
        )
        obligations = build_obligations(self.run_dir)
        self.assertEqual(1, len(obligations))
        self.assertEqual("interaction", obligations[0]["kind"])
        self.assertEqual("DISCOVERY_EXECUTION_REQUIRED", discovery_status(self.run_dir)["state"])

    def test_finite_configuration_requires_every_observed_value(self) -> None:
        self.write_inventory(
            page="编辑配置页面", element_type="下拉框", interaction="选择", element_name="模式"
        )
        self.add_finite_options("标准", "增强")
        self.add_configuration_variant("CFG-DEFAULT", "默认不配置", "保持默认")
        self.add_configuration_variant("CFG-STANDARD", "单值", "标准")
        status = discovery_status(self.run_dir)
        self.assertEqual("CONFIGURATION_VARIANT_PLAN_REQUIRED", status["state"])
        self.assertTrue(any("增强" in issue for issue in status["configuration_plan_issues"]))
        self.add_configuration_variant("CFG-ENHANCED", "单值", "增强")
        status = discovery_status(self.run_dir)
        self.assertNotEqual("CONFIGURATION_VARIANT_PLAN_REQUIRED", status["state"])
        variants = [item for item in build_obligations(self.run_dir) if item["kind"] == "configuration-variant"]
        self.assertEqual(3, len(variants))

    def test_completed_configuration_variant_survives_local_plan_extension(self) -> None:
        self.write_inventory(page="编辑配置页面")
        self.add_configuration_variant("CFG-DEFAULT", "默认不配置", "保持默认")
        self.add_configuration_variant("CFG-VALUE", "单值", "新名称")
        obligation = next(
            item for item in build_obligations(self.run_dir)
            if item.get("configuration_variant_id") == "CFG-DEFAULT"
        )
        begin_obligation(self.run_dir, obligation["obligation_id"])
        evidence_dir = self.run_dir / "artifacts" / "screenshots"
        for name, value in (
            ("cfg-before.txt", "old configuration"),
            ("cfg-after.txt", "save succeeded"),
            ("cfg-recovery.txt", "baseline restored"),
            ("cfg-effect.txt", "dependent behavior observed"),
        ):
            (evidence_dir / name).write_text(value, encoding="utf-8")
        complete_obligation(
            self.run_dir,
            obligation["obligation_id"],
            evidence_mode="artifact",
            before_evidence_path="artifacts/screenshots/cfg-before.txt",
            before_evidence_location="配置前详情",
            after_evidence_path="artifacts/screenshots/cfg-after.txt",
            after_evidence_location="保存成功详情",
            recovery_evidence_path="artifacts/screenshots/cfg-recovery.txt",
            recovery_evidence_location="恢复基线详情",
            effect_evidence_path="artifacts/screenshots/cfg-effect.txt",
            effect_evidence_location="依赖功能实际效果",
            before_state="配置项显示原始值",
            executed_action="保持默认值并提交保存",
            observed_result="保存成功并重新进入显示默认值",
            recovery_result="测试对象已经恢复到基线状态",
            commit_result="页面明确提示保存成功",
            persistence_result="重新进入详情仍回显默认值",
            effect_result="依赖功能按默认值产生预期效果",
        )
        self.add_configuration_variant("CFG-BOUNDARY", "边界值", "最大长度名称")
        completed_ids = {
            json.loads(line)["obligation_id"]
            for line in (self.run_dir / "artifacts" / "discovery-control" / "completions.jsonl")
            .read_text(encoding="utf-8").splitlines()
        }
        self.assertIn(obligation["obligation_id"], completed_ids)
        self.assertEqual(1, discovery_status(self.run_dir)["completed_count"])


if __name__ == "__main__":
    unittest.main()
