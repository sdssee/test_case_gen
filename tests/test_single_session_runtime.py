# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_design.formal_assembler import SHEETS, complete_deliverables
from test_design.session_runtime import (
    append_events,
    artifact_paths,
    compile_facts,
    ensure_run,
    pipeline_status,
    review_run,
    save_cases,
    save_plan,
    validate_cases,
    validate_discovery,
    validate_plan,
)


class SingleSessionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temporary.name) / "run-001"
        ensure_run(self.run_dir, "告警管理>告警列表", "大数据平台", "需求说明")
        append_events(self.run_dir, [
            {"kind": "page", "fact_id": "PAGE-LIST", "data": {
                "name": "告警列表", "final_scan_status": "stable", "unhandled_element_refs": []
            }},
            {"kind": "function", "fact_id": "FN-PAGE", "data": {"name": "分页"}},
            {"kind": "element", "fact_id": "EL-PAGE-SIZE", "data": {
                "function_ref": "FN-PAGE", "page_ref": "PAGE-LIST", "name": "每页条数",
                "type": "下拉框", "interactive": True, "option_set": "finite", "options": [10, 20, 30]
            }},
            {"kind": "element", "fact_id": "EL-PAGER", "data": {
                "function_ref": "FN-PAGE", "page_ref": "PAGE-LIST", "name": "翻页控件", "interactive": True
            }},
            {"kind": "transaction", "fact_id": "TX-PAGE", "data": {
                "function_ref": "FN-PAGE", "element_refs": ["EL-PAGE-SIZE", "EL-PAGER"],
                "transaction_type": "pagination", "recovery_result": "恢复第一页和初始条数",
                "checks": [
                    {"element_ref": "EL-PAGE-SIZE", "action": "选择10条/页", "option_value": 10, "result": "列表与页数按10条重算"},
                    {"element_ref": "EL-PAGE-SIZE", "action": "选择20条/页", "option_value": 20, "result": "列表与页数按20条重算"},
                    {"element_ref": "EL-PAGE-SIZE", "action": "选择30条/页", "option_value": 30, "result": "列表与页数按30条重算"},
                    {"element_ref": "EL-PAGER", "action": "进入下一页", "result": "页码增加并显示下一页数据"},
                    {"element_ref": "EL-PAGER", "action": "返回上一页", "result": "页码减少并恢复上一页数据"},
                    {"element_ref": "EL-PAGER", "action": "观察第一页边界", "result": "上一页按钮禁用"},
                    {"element_ref": "EL-PAGER", "action": "进入末页并观察边界", "result": "下一页按钮禁用"},
                ],
            }},
        ])
        compile_facts(self.run_dir)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_plan_and_cases(self) -> None:
        refs = ["FN-PAGE", "EL-PAGE-SIZE", "EL-PAGER", "TX-PAGE"]
        plan = {
            "schema_version": "2.0", "source": "facts.json", "risks": [], "non_case_checks": [],
            "functions": [{"function_ref": "FN-PAGE", "name": "分页", "cases": [
                {"case_id": "TC-PAGE-001", "title": "每页条数切换", "strategy": "baseline",
                 "dfx_dimension": "DFT功能", "dfx_scenario": "正向流程", "fact_refs": refs,
                 "covered_checks": {"TX-PAGE": [1, 2, 3]}},
                {"case_id": "TC-PAGE-002", "title": "上一页与下一页切换", "strategy": "DFX",
                 "dfx_dimension": "DFR可靠", "dfx_scenario": "状态一致性", "fact_refs": refs,
                 "covered_checks": {"TX-PAGE": [4, 5]}},
                {"case_id": "TC-PAGE-003", "title": "首页与末页边界状态", "strategy": "DFX",
                 "dfx_dimension": "DFT功能", "dfx_scenario": "边界值", "fact_refs": refs,
                 "covered_checks": {"TX-PAGE": [6, 7]}},
            ]}],
        }
        save_plan(self.run_dir, plan)
        navigation = {"action": "进入告警管理-告警列表", "expected": "显示告警查询区、告警列表和分页区域"}
        cases = {"schema_version": "2.0", "source_plan": "case-plan.json", "cases": [
            {"case_id": "TC-PAGE-001", "function_ref": "FN-PAGE", "title": "分页-每页条数切换",
             "priority": "P1", "test_type": "功能测试", "dfx_dimension": "DFT功能", "dfx_scenario": "正向流程",
             "preconditions": ["告警列表存在超过30条可查看数据"], "test_data": "每页条数：10、20、30",
             "steps": [navigation,
                       {"action": "依次选择10条/页、20条/页和30条/页", "expected": "每次选择后列表上限和总页数分别按对应条数重新计算"}],
             "fact_refs": refs, "automation": True},
            {"case_id": "TC-PAGE-002", "function_ref": "FN-PAGE", "title": "分页-上一页与下一页切换",
             "priority": "P1", "test_type": "功能测试", "dfx_dimension": "DFR可靠", "dfx_scenario": "状态一致性",
             "preconditions": ["告警列表至少存在两页可查看数据"], "test_data": "第一页与第二页告警记录",
             "steps": [navigation,
                       {"action": "单击下一页后再单击上一页", "expected": "页码和列表先切换至下一页，再恢复第一页数据"}],
             "fact_refs": refs, "automation": True},
            {"case_id": "TC-PAGE-003", "function_ref": "FN-PAGE", "title": "分页-首页与末页边界状态",
             "priority": "P1", "test_type": "功能测试", "dfx_dimension": "DFT功能", "dfx_scenario": "边界值",
             "preconditions": ["告警列表存在多页可查看数据"], "test_data": "第一页和末页",
             "steps": [navigation,
                       {"action": "分别停留在第一页和末页观察翻页按钮", "expected": "第一页的上一页按钮禁用，末页的下一页按钮禁用"}],
             "fact_refs": refs, "automation": True},
        ]}
        save_cases(self.run_dir, cases)

    def test_one_pagination_transaction_dynamically_forms_three_cases_not_seven(self) -> None:
        self.assertEqual([], validate_discovery(self.run_dir))
        facts = compile_facts(self.run_dir)
        self.assertEqual(1, len(facts["transactions"]))
        self.assertEqual(7, len(facts["transactions"][0]["checks"]))
        self._write_plan_and_cases()
        self.assertEqual(3, sum(len(item["cases"]) for item in json.loads(artifact_paths(self.run_dir)["plan"].read_text(encoding="utf-8"))["functions"]))

    def test_review_is_ready_and_has_no_evidence_dependency(self) -> None:
        self._write_plan_and_cases()
        self.assertEqual([], validate_plan(self.run_dir))
        self.assertEqual([], validate_cases(self.run_dir))
        facts_before = artifact_paths(self.run_dir)["facts"].read_bytes()
        self.assertEqual("ready", review_run(self.run_dir)["status"])
        self.assertEqual(facts_before, artifact_paths(self.run_dir)["facts"].read_bytes())
        self.assertFalse(artifact_paths(self.run_dir)["diagnostics"].exists())
        self.assertEqual("ready", pipeline_status(self.run_dir)["state"])

    def test_scope_binding_resumes_same_target_and_rejects_a_different_target(self) -> None:
        resumed = ensure_run(self.run_dir, "告警管理>告警列表")
        self.assertEqual("告警管理>告警列表", resumed["module_path"])
        with self.assertRaisesRegex(ValueError, "choose a new run directory"):
            ensure_run(self.run_dir, "告警管理>告警详情")

    def test_open_items_use_review_notes_or_real_fact_blocking_without_retry_loop(self) -> None:
        self._write_plan_and_cases()
        append_events(self.run_dir, [{"kind": "open_item", "fact_id": "OPEN-RISK", "data": {
            "category": "observed_risk", "description": "末页返回响应较慢", "material": False
        }}])
        compile_facts(self.run_dir)
        review = review_run(self.run_dir)
        self.assertEqual("ready_with_notes", review["status"])
        append_events(self.run_dir, [{"kind": "open_item", "fact_id": "OPEN-QUESTION", "data": {
            "category": "external_question", "description": "告警确认后的外部处置语义待确认", "material": True
        }}])
        compile_facts(self.run_dir)
        self.assertEqual("blocked_by_fact", review_run(self.run_dir)["status"])
        append_events(self.run_dir, [{"kind": "open_item", "fact_id": "OPEN-QUESTION", "status": "resolved", "data": {
            "category": "external_question", "description": "用户已确认外部处置语义", "material": True
        }}])
        compile_facts(self.run_dir)
        self.assertEqual("ready_with_notes", review_run(self.run_dir)["status"])

    def test_delivery_detects_a_stale_review_without_returning_to_discovery(self) -> None:
        self._write_plan_and_cases()
        self.assertEqual("ready", review_run(self.run_dir)["status"])
        append_events(self.run_dir, [{"kind": "open_item", "fact_id": "OPEN-NOTE", "data": {
            "category": "observed_risk", "description": "非阻塞提示", "material": False
        }}])
        compile_facts(self.run_dir)
        with self.assertRaisesRegex(ValueError, "stale"):
            complete_deliverables(self.run_dir, ROOT)

    def test_configuration_covers_default_and_each_single_factor_value(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "config-run"
            ensure_run(run_dir, "告警管理>新增告警规则")
            checks = []
            for option in ["不配置", "邮件", "短信"]:
                checks.append({
                    "element_ref": "EL-CONFIG", "action": f"设置通知方式为{option}", "option_value": option,
                    "result": f"页面接受{option}", "commit_result": "保存成功", "persistence_result": "重开后回显一致",
                    "effect_result": f"规则按{option}生效", "recovery_result": "恢复基线",
                })
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "新增告警规则", "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN-CONFIG", "data": {"name": "通知方式配置"}},
                {"kind": "test_object", "fact_id": "OBJ", "data": {"name": "AI_TEST_RULE_001", "owner": "current_run", "state": "cleaned"}},
                {"kind": "element", "fact_id": "EL-CONFIG", "data": {
                    "function_ref": "FN-CONFIG", "name": "通知方式", "interactive": True,
                    "configuration": True, "option_set": "finite", "options": ["不配置", "邮件", "短信"], "default_value": "不配置"
                }},
                {"kind": "transaction", "fact_id": "TX-CONFIG", "data": {
                    "function_ref": "FN-CONFIG", "element_refs": ["EL-CONFIG"], "transaction_type": "configuration",
                    "test_object_ref": "OBJ", "outcome": "success", "combination": False, "checks": checks
                }},
            ])
            compile_facts(run_dir)
            self.assertEqual([], validate_discovery(run_dir))

    def test_delivery_has_two_independent_workbooks_and_no_blank_case_rows(self) -> None:
        self._write_plan_and_cases()
        self.assertEqual("ready", review_run(self.run_dir)["status"])
        receipt = complete_deliverables(self.run_dir, ROOT)
        delivery = artifact_paths(self.run_dir)["delivery"]
        formal = delivery / receipt["formal_workbook"]
        import_file = delivery / receipt["import_workbook"]
        self.assertEqual("正式测试设计.xlsx", formal.name)
        self.assertEqual("测试系统导入.xlsx", import_file.name)
        self.assertEqual({"正式测试设计.xlsx", "测试系统导入.xlsx"}, {path.name for path in delivery.iterdir()})
        workbook = load_workbook(formal, data_only=False)
        self.assertEqual(SHEETS, workbook.sheetnames)
        ws = workbook["功能测试用例"]
        self.assertEqual("TC-PAGE-001", ws.cell(2, 1).value)
        self.assertEqual("TC-PAGE-003", ws.cell(4, 1).value)
        self.assertIsNone(ws.cell(5, 1).value)
        self.assertGreaterEqual(ws.row_dimensions[2].height, 40)
        matrix = workbook["测试场景矩阵"]
        matrix_text = "\n".join(str(matrix.cell(row, column).value or "") for row in range(2, matrix.max_row + 1) for column in range(1, matrix.max_column + 1))
        self.assertNotIn("TX-PAGE", matrix_text)
        self.assertNotIn("EL-PAGE", matrix_text)
        coverage = workbook["页面元素覆盖清单"]
        coverage_text = "\n".join(str(coverage.cell(row, column).value or "") for row in range(2, coverage.max_row + 1) for column in range(1, coverage.max_column + 1))
        self.assertNotIn("DOM", coverage_text)
        self.assertNotIn("EL-PAGE", coverage_text)
        import_book = load_workbook(import_file, data_only=False)
        import_ws = import_book[import_book.sheetnames[0]]
        self.assertEqual(4, import_ws.max_row)
        self.assertGreaterEqual(import_ws.row_dimensions[2].height, 40)
        validated = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate-test-design-deliverable.py"),
             "--workbook", str(formal), "--import-workbook", str(import_file)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, validated.returncode, validated.stderr)


if __name__ == "__main__":
    unittest.main()
