# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import test_design_excel_tools as excel_tools
from test_design.contracts.function_cases import FUNCTION_CASE_REQUIRED_FIELDS
from test_design.orchestration.case_merge import traceability_expectations
from test_design.sensitive_data import binary_evidence_audit_path
from test_design.validators.batch_ledgers import (
    BRANCH_ACTIONS,
    risk_page_verification_state,
    validate_branch_case_grounding,
    validate_branch_plan_links,
    validate_interaction_branch_rows,
    validate_lifecycle_rows,
    validate_operation_plan_rows,
)
from test_design.validators.function_cases import validate_function_case_schema


class InteractionBranchGateTests(unittest.TestCase):
    def discovery(self, category: str) -> dict[str, str]:
        types = {
            "输入": ("名称", "文本输入框", "输入文本"),
            "动态选择": ("集群", "远程搜索下拉框", "搜索并滚动加载"),
            "分页": ("分页器", "分页控件", "点击分页"),
            "弹窗": ("编辑弹窗", "弹窗", "打开并操作弹窗"),
        }
        name, element_type, interaction = types[category]
        return {
            "批次ID": "BATCH-001",
            "最小标题路径": "模块>页面",
            "交互实例ID": f"INT-{category}",
            "页面/入口": "目标页面",
            "元素名称/文案": name,
            "元素类型": element_type,
            "交互方式": interaction,
        }

    def branches(self, discovery: dict[str, str], category: str) -> list[dict[str, str]]:
        return [
            {
                **discovery,
                "分支类别": category,
                "分支动作": action,
                "执行前状态": f"{action}前页面稳定",
                "执行动作": f"实际执行{action}",
                "执行后结果": f"观察到{action}独立结果",
                "恢复结果": f"{action}后恢复初始状态",
                "操作步骤锚点": f"实际执行{action}",
                "预期结果锚点": f"观察到{action}独立结果",
                "是否实际执行": "是",
                "证据路径": f"artifacts/evidence/{category}-{index}.trace",
                "证据定位": f"event-{category}-{index}",
                "关联用例ID": f"TC-{category}-{index:02d}",
            }
            for index, action in enumerate(sorted(BRANCH_ACTIONS[category]), start=1)
        ]

    def test_each_compound_control_rejects_a_missing_required_branch(self) -> None:
        for category in ["输入", "动态选择", "分页", "弹窗"]:
            with self.subTest(category=category):
                discovery = self.discovery(category)
                option_rows = (
                    [{**discovery, "选项集合类型": "动态"}]
                    if category == "动态选择"
                    else []
                )
                rows = self.branches(discovery, category)[:-1]
                with self.assertRaisesRegex(ValueError, "missing independently executed branch"):
                    validate_interaction_branch_rows([discovery], option_rows, rows, lambda _: True)

    def test_complete_branches_require_unique_evidence_and_unique_owned_cases(self) -> None:
        discovery = self.discovery("输入")
        rows = self.branches(discovery, "输入")
        validate_interaction_branch_rows([discovery], [], rows, lambda _: True)
        case_ids = [row["关联用例ID"] for row in rows]
        plan = {
            **discovery,
            "应生成用例数": str(len(case_ids)),
            "计划用例ID": ",".join(case_ids),
        }
        validate_branch_plan_links(rows, [plan], lambda value: value.split(","))

        rows[1]["证据路径"] = rows[0]["证据路径"]
        rows[1]["证据定位"] = rows[0]["证据定位"]
        with self.assertRaisesRegex(ValueError, "reuses evidence"):
            validate_interaction_branch_rows([discovery], [], rows, lambda _: True)

    def test_one_case_cannot_claim_two_independent_branches(self) -> None:
        discovery = self.discovery("输入")
        rows = self.branches(discovery, "输入")
        rows[1]["关联用例ID"] = rows[0]["关联用例ID"]
        plan = {
            **discovery,
            "应生成用例数": "4",
            "计划用例ID": ",".join(f"TC-输入-{index:02d}" for index in range(1, 5)),
        }
        with self.assertRaisesRegex(ValueError, "each branch needs a distinct case"):
            validate_branch_plan_links(rows, [plan], lambda value: value.split(","))

    def test_renamed_screenshot_cannot_prove_multiple_branches(self) -> None:
        discovery = self.discovery("弹窗")
        rows = self.branches(discovery, "弹窗")
        for index, row in enumerate(rows, start=1):
            row["证据路径"] = f"artifacts/screenshots/modal-{index}.png"
        with self.assertRaisesRegex(ValueError, "reuses static image content"):
            validate_interaction_branch_rows(
                [discovery],
                [],
                rows,
                lambda _: True,
                lambda _: "image:identical-content",
            )

    def test_environment_blocker_cannot_claim_an_unexecuted_branch(self) -> None:
        discovery = self.discovery("输入")
        for actual_flag, message in (
            ("否", "must be actually attempted"),
            ("是", "cannot count as an executed branch"),
        ):
            with self.subTest(actual_flag=actual_flag):
                rows = self.branches(discovery, "输入")
                rows[0].update(
                    {
                        "是否实际执行": actual_flag,
                        "阻塞原因": "环境不可用",
                        "执行动作": "环境不可用，未执行输入",
                        "执行后结果": "环境不可用，未执行输入",
                        "操作步骤锚点": "未执行输入",
                        "预期结果锚点": "未执行输入",
                    }
                )
                with self.assertRaisesRegex(ValueError, message):
                    validate_interaction_branch_rows([discovery], [], rows, lambda _: True)

    def test_bare_environment_or_data_blocker_cannot_claim_an_executed_branch(self) -> None:
        discovery = self.discovery("输入")
        for blocker in (
            "环境不可用",
            "服务不可用",
            "第三方不可用",
            "无测试数据",
            "数据不足",
            "environment unavailable",
            "insufficient test data",
        ):
            with self.subTest(blocker=blocker):
                rows = self.branches(discovery, "输入")
                rows[0].update(
                    {
                        "是否实际执行": "是",
                        "阻塞原因": blocker,
                        "执行动作": "点击输入框并输入 AI_TEST_name",
                        "执行后结果": blocker,
                        "恢复结果": blocker,
                        "操作步骤锚点": "输入 AI_TEST_name",
                        "预期结果锚点": blocker,
                    }
                )
                with self.assertRaisesRegex(ValueError, "cannot count as an executed branch"):
                    validate_interaction_branch_rows([discovery], [], rows, lambda _: True)

    def test_branch_taxonomy_cannot_replace_a_concrete_execution_anchor(self) -> None:
        discovery = self.discovery("输入")
        rows = self.branches(discovery, "输入")
        rows[0]["执行动作"] = "点击输入框后键入 AI_TEST_name"
        rows[0]["操作步骤锚点"] = rows[0]["分支动作"]
        with self.assertRaisesRegex(ValueError, "concrete phrase copied from the executed action"):
            validate_interaction_branch_rows([discovery], [], rows, lambda _: True)

    def test_branch_step_anchor_must_appear_in_operation_steps(self) -> None:
        discovery = self.discovery("输入")
        rows = self.branches(discovery, "输入")
        cases = [
            {
                "用例 ID": row["关联用例ID"],
                "用例标题": f"输入校验-{row['操作步骤锚点']}",
                "测试数据": row["操作步骤锚点"],
                "操作步骤": "1. 打开系统入口\n2. 进入目标页面\n3. 查看输入框\n4. 返回页面",
                "预期结果": f"1. 页面加载完成\n2. 输入框可见\n3. {row['预期结果锚点']}",
            }
            for row in rows
        ]
        with self.assertRaisesRegex(ValueError, "does not ground branch 操作步骤锚点"):
            validate_branch_case_grounding(rows, cases, lambda value: [value])

    def test_traceability_binds_branch_id_and_deduplicated_observation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            batch_runs = Path(value) / "batch-runs"
            run_dir = batch_runs / "RUN-001"
            templates_dir = batch_runs / "templates"
            evidence_dir = run_dir / "artifacts" / "evidence"
            templates_dir.mkdir(parents=True)
            evidence_dir.mkdir(parents=True)
            source_templates = REPO_ROOT / "docs" / "test-assets" / "batch-runs" / "templates"
            template_by_ledger = {
                "page-discovery.csv": "page-discovery-template.csv",
                "selection-option-observations.csv": "selection-option-observations-template.csv",
                "interaction-branch-observations.csv": "interaction-branch-observations-template.csv",
                "test-data-lifecycle.csv": "test-data-lifecycle-template.csv",
            }
            for template_name in template_by_ledger.values():
                shutil.copy2(source_templates / template_name, templates_dir / template_name)

            def write_ledger(name: str, rows: list[dict[str, str]]) -> None:
                template_name = template_by_ledger[name]
                with (templates_dir / template_name).open("r", encoding="utf-8-sig", newline="") as stream:
                    headers = next(csv.reader(stream))
                with (run_dir / name).open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=headers)
                    writer.writeheader()
                    writer.writerows(rows)

            identity = {
                "批次ID": "BATCH-001",
                "最小标题路径": "告警>列表",
                "交互实例ID": "INT-SEARCH-001",
                "页面/入口": "告警列表",
                "元素名称/文案": "告警名称",
                "元素类型": "搜索框",
            }
            discovery_evidence = evidence_dir / "discovery.png"
            selection_evidence = evidence_dir / "selection.trace"
            branch_evidence = evidence_dir / "branch.trace"
            discovery_evidence.write_bytes(b"discovery-fact")
            discovery_audit = binary_evidence_audit_path(discovery_evidence)
            discovery_audit.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "evidence_sha256": hashlib.sha256(b"discovery-fact").hexdigest(),
                        "inspection_method": "model_visual_review",
                        "visible_text": "<no_visible_text>",
                        "address_bar_cropped_or_masked": True,
                        "environment_identifiers_masked": True,
                        "credentials_masked": True,
                        "status": "PASSED",
                        "notes": "sanitized test evidence",
                    }
                ),
                encoding="utf-8",
            )
            selection_evidence.write_bytes(b"shared-observation-fact")
            branch_evidence.write_bytes(b"shared-observation-fact")
            write_ledger(
                "page-discovery.csv",
                [{**identity, "证据路径": "artifacts/evidence/discovery.png"}],
            )
            write_ledger(
                "selection-option-observations.csv",
                [
                    {
                        **identity,
                        "选项值": "严重",
                        "关联用例ID": "TC-SEARCH-001",
                        "证据路径": "artifacts/evidence/selection.trace",
                    }
                ],
            )
            write_ledger(
                "interaction-branch-observations.csv",
                [
                    {
                        **identity,
                        "分支类别": "输入",
                        "分支动作": "正常输入",
                        "关联用例ID": "TC-SEARCH-001",
                        "证据路径": "artifacts/evidence/branch.trace",
                    }
                ],
            )
            write_ledger("test-data-lifecycle.csv", [])
            plan = {
                **identity,
                "功能点": "告警搜索",
                "计划用例ID": "TC-SEARCH-001",
            }
            records = traceability_expectations(
                run_dir,
                [plan],
                "TASK-CASE-SEARCH",
                "a" * 64,
            )
            record = records["TC-SEARCH-001"]
            self.assertEqual(1, len(record.selection_observation_ids))
            self.assertEqual(1, len(record.branch_observation_ids))
            self.assertTrue(record.branch_observation_ids[0].startswith("BRANCH-"))
            self.assertEqual(
                (
                    hashlib.sha256(b"discovery-fact").hexdigest(),
                    hashlib.sha256(discovery_audit.read_bytes()).hexdigest(),
                    hashlib.sha256(b"shared-observation-fact").hexdigest(),
                ),
                record.evidence_hashes,
            )


class SemanticGateHardeningTests(unittest.TestCase):
    def mutation_plan(self) -> dict[str, str]:
        return {
            "批次ID": "BATCH-001",
            "最小标题路径": "模块>编辑页面",
            "交互实例ID": "INT-EDIT",
            "页面/入口": "对象编辑页面",
            "功能点": "对象编辑",
            "元素名称/文案": "名称",
            "元素类型": "文本输入框",
            "交互方式": "输入并保存",
            "业务路径": "对象编辑>保存",
            "适用DFX维度": "DFT功能",
            "适用DFX场景": "正向流程",
            "测试设计方向": "修改名称并重新进入验证",
            "操作类别": "编辑",
            "验证要求": "回显,持久化,实际生效",
            "数据策略": "本次创建测试数据",
            "执行状态": "已完成",
            "是否必须真实执行": "是",
            "计划用例ID": "TC-CREATE,TC-EDIT",
        }

    def lifecycle(self) -> dict[str, str]:
        return {
            "批次ID": "BATCH-001",
            "最小标题路径": "模块>编辑页面",
            "交互实例ID": "INT-EDIT",
            "关联页面/入口": "对象编辑页面",
            "修改项/元素": "名称",
            "测试数据ID/名称": "AI_TEST_OBJECT",
            "创建步骤关联用例": "TC-CREATE",
            "创建结果": "创建成功并进入详情",
            "查看结果": "详情显示AI_TEST_OBJECT",
            "编辑前值": "AI_TEST_OBJECT",
            "编辑后值": "AI_TEST_OBJECT_UPDATED",
            "编辑结果": "保存成功",
            "保存后回显": "重新进入显示AI_TEST_OBJECT_UPDATED",
            "实际生效结果": "关联列表已生效并显示AI_TEST_OBJECT_UPDATED",
        }

    def test_failed_nonempty_lifecycle_result_is_rejected(self) -> None:
        plan = self.mutation_plan()
        row = self.lifecycle()
        row["实际生效结果"] = "保存提示成功但实际未生效"
        with self.assertRaisesRegex(ValueError, "failure/non-effect"):
            validate_lifecycle_rows([row], True, lambda text, values: any(value in text for value in values), [plan])

    def test_editable_field_on_edit_page_cannot_be_downgraded_to_view(self) -> None:
        plan = self.mutation_plan()
        plan.update(
            {
                "操作类别": "查看",
                "验证要求": "结果分支",
                "数据策略": "无数据变更",
            }
        )
        with self.assertRaisesRegex(ValueError, "cannot be downgraded"):
            validate_operation_plan_rows([plan])

    def test_page_action_question_cannot_be_hidden_behind_log_reason(self) -> None:
        row = {
            "风险ID": "RISK-PAGE-001",
            "模型不理解内容/待确认问题": "点击下拉选项后页面如何变化",
            "已完成深探依据": "已展开下拉",
            "页面可验证性": "不可直接验证",
            "页面验证动作": "点击下拉选项",
            "页面验证结果": "页面变化待确认",
            "不可验证/外部依赖原因": "需要接口日志确认最终语义",
            "证据路径": "artifacts/evidence/dropdown.trace",
        }
        state, reasons = risk_page_verification_state([row], evidence_exists=lambda _: True)
        self.assertEqual("discovery_required", state)
        self.assertTrue(any("verify it on the page" in reason for reason in reasons))

    def case(self) -> dict[str, str]:
        case = {field: "" for field in FUNCTION_CASE_REQUIRED_FIELDS}
        case.update(
            {
                "用例 ID": "TC-001",
                "Story ID/需求 ID": "REQ-001",
                "模块": "模块",
                "功能点": "对象编辑",
                "用例标题": "对象编辑-修改名称",
                "优先级": "P1",
                "测试类型": "功能测试",
                "DFX维度": "DFT功能",
                "DFX场景": "正向流程",
                "前置条件": "1. 已准备测试账号\n2. 已准备AI_TEST_OBJECT测试数据",
                "测试数据": "AI_TEST_OBJECT",
                "操作步骤": "1. 打开系统登录入口\n2. 登录并进入一级模块>对象页面\n3. 打开编辑弹窗\n4. 修改名称",
                "预期结果": "1. 系统登录成功\n2. 对象页面加载完成\n3. 名称输入框可编辑",
                "实际结果": "未执行",
                "执行状态": "未执行",
                "是否适合自动化": "是",
                "关联风险": "无",
                "备注": "页面实探派生",
            }
        )
        return case

    def test_cases_gate_rejects_unclosed_transient_flow(self) -> None:
        case = self.case()
        case.update(
            {
                "功能点": "下拉选择",
                "用例标题": "下拉选择-展开并选择选项",
                "操作步骤": "1. 打开系统登录入口\n2. 登录并进入一级模块>对象页面\n3. 展开下拉框\n4. 选择目标选项",
                "预期结果": "1. 系统登录成功\n2. 对象页面加载完成\n3. 目标选项已被选中",
            }
        )
        with self.assertRaisesRegex(ValueError, "transient UI state"):
            validate_function_case_schema(case, "case")

    def test_cases_gate_rejects_page_two_without_multi_page_data(self) -> None:
        case = self.case()
        case.update(
            {
                "功能点": "分页跳转",
                "用例标题": "分页跳转-跳转第二页",
                "操作步骤": "1. 打开系统登录入口\n2. 登录并进入一级模块>列表页面\n3. 在页码输入框输入2\n4. 点击跳转并返回列表",
                "预期结果": "1. 系统登录成功\n2. 列表页面加载完成\n3. 当前页显示第2页数据",
            }
        )
        with self.assertRaisesRegex(ValueError, "multi-page test data"):
            validate_function_case_schema(case, "case")


class PublicationIsolationTests(unittest.TestCase):
    def test_direct_assembly_is_preview_only(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "run"
            run_dir.mkdir()
            excel_tools.validate_assembly_preview_output(
                run_dir,
                run_dir / "artifacts" / "previews" / "probe.xlsx",
            )
            with self.assertRaisesRegex(ValueError, "preview-only"):
                excel_tools.validate_assembly_preview_output(run_dir, Path(value) / "published.xlsx")

    def test_direct_helpers_cannot_write_protected_publication_paths(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            (root / "docs/test-design").mkdir(parents=True)
            (root / "docs/test-assets").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "protected publication path"):
                excel_tools.reject_direct_protected_output(
                    root / "docs/test-design/deliverables/bypass.xlsx",
                    "assemble-formal-workbook",
                )


if __name__ == "__main__":
    unittest.main()
