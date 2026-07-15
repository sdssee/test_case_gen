# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_design.formal_assembler import SHEETS, complete_deliverables
from test_design.session_runtime import (
    append_events,
    artifact_paths,
    build_plan_skeleton,
    checkpoint_facts,
    compile_facts,
    ensure_run,
    load_facts,
    load_cases,
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
                "name": "告警列表", "menu_path": ["告警管理", "告警列表"], "result_anchor": "告警列表",
                "final_scan_status": "stable", "unhandled_element_refs": []
            }},
            {"kind": "function", "fact_id": "FN-VIEW", "data": {"name": "告警视图模式"}},
            {"kind": "element", "fact_id": "EL-VIEW-MODE", "data": {
                "function_ref": "FN-VIEW", "page_ref": "PAGE-LIST", "name": "视图模式",
                "type": "下拉框", "interactive": True, "option_set": "finite",
                "options": ["精简", "标准", "详细", "仅未确认", "仅已确认", "仅严重", "全部"]
            }},
            {"kind": "transaction", "fact_id": "TX-VIEW", "data": {
                "function_ref": "FN-VIEW", "element_refs": ["EL-VIEW-MODE"],
                "transaction_type": "selection", "recovery_result": "恢复标准视图",
                "checks": [
                    {"element_ref": "EL-VIEW-MODE", "action": "选择精简视图", "option_value": "精简", "result": "列表仅显示核心告警字段",
                     "result_anchor": {"assertion": "field_visible", "target": "列表", "field": "核心告警字段", "tokens": ["核心告警字段"]}},
                    {"element_ref": "EL-VIEW-MODE", "action": "选择标准视图", "option_value": "标准", "result": "列表显示默认告警字段", "result_anchor": {"assertion": "contains", "value": "默认告警字段"}},
                    {"element_ref": "EL-VIEW-MODE", "action": "选择详细视图", "option_value": "详细", "result": "列表显示全部可见告警字段", "result_anchor": {"assertion": "contains", "value": "全部可见告警字段"}},
                    {"element_ref": "EL-VIEW-MODE", "action": "选择仅未确认", "option_value": "仅未确认", "result": "列表只显示未确认告警", "result_anchor": {"assertion": "contains", "value": "未确认告警"}},
                    {"element_ref": "EL-VIEW-MODE", "action": "选择仅已确认", "option_value": "仅已确认", "result": "列表只显示已确认告警", "result_anchor": {"assertion": "contains", "value": "已确认告警"}},
                    {"element_ref": "EL-VIEW-MODE", "action": "选择仅严重", "option_value": "仅严重", "result": "列表只显示严重告警", "result_anchor": {"assertion": "contains", "value": "严重告警"}},
                    {"element_ref": "EL-VIEW-MODE", "action": "选择全部", "option_value": "全部", "result": "列表恢复显示全部告警", "result_anchor": {"assertion": "contains", "value": "全部告警"}},
                ],
            }},
        ])
        checkpoint_facts(self.run_dir)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_plan_and_cases(self) -> None:
        refs = ["FN-VIEW", "EL-VIEW-MODE", "TX-VIEW"]
        plan = {
            "schema_version": "2.0", "risks": [],
            "functions": [{"function_ref": "FN-VIEW", "name": "告警视图模式", "cases": [
                {"case_id": "TC-VIEW-001", "title": "显示密度逐项切换", "strategy": "baseline",
                 "page_ref": "PAGE-LIST",
                 "dfx_dimension": "DFT功能", "dfx_scenario": "正向流程"},
                {"case_id": "TC-VIEW-002", "title": "确认状态逐项筛选", "strategy": "DFX",
                 "page_ref": "PAGE-LIST",
                 "dfx_dimension": "DFR可靠", "dfx_scenario": "状态一致性"},
                {"case_id": "TC-VIEW-003", "title": "严重与全部范围切换", "strategy": "DFX",
                 "page_ref": "PAGE-LIST",
                 "dfx_dimension": "DFT功能", "dfx_scenario": "边界值"},
            ]}],
            "check_assignments": [
                *[{"transaction_ref": "TX-VIEW", "check_index": index, "disposition": "case", "case_id": "TC-VIEW-001"} for index in (1, 2, 3)],
                *[{"transaction_ref": "TX-VIEW", "check_index": index, "disposition": "case", "case_id": "TC-VIEW-002"} for index in (4, 5)],
                *[{"transaction_ref": "TX-VIEW", "check_index": index, "disposition": "case", "case_id": "TC-VIEW-003"} for index in (6, 7)],
            ],
        }
        save_plan(self.run_dir, plan)
        navigation = {"action": "进入告警管理-告警列表", "expected": "显示告警列表和查询区"}
        cases = {"schema_version": "2.0", "source_plan": "case-plan.json", "cases": [
            {"case_id": "TC-VIEW-001", "function_ref": "FN-VIEW", "title": "告警视图模式-显示密度逐项切换",
             "priority": "P1", "test_type": "功能测试", "dfx_dimension": "DFT功能", "dfx_scenario": "正向流程",
             "preconditions": ["告警列表存在包含完整字段的可查看数据"], "test_data": "视图模式：精简、标准、详细",
             "steps": [navigation,
                       {"action": "选择精简视图", "expected": "核心告警字段保持可见"},
                       {"action": "选择标准视图", "expected": "列表显示默认告警字段"},
                       {"action": "选择详细视图", "expected": "列表显示全部可见告警字段"}],
             "fact_refs": refs, "automation": True},
            {"case_id": "TC-VIEW-002", "function_ref": "FN-VIEW", "title": "告警视图模式-确认状态逐项筛选",
             "priority": "P1", "test_type": "功能测试", "dfx_dimension": "DFR可靠", "dfx_scenario": "状态一致性",
             "preconditions": ["告警列表同时存在已确认和未确认数据"], "test_data": "确认状态：仅未确认、仅已确认",
             "steps": [navigation,
                       {"action": "选择仅未确认", "expected": "列表只显示未确认告警"},
                       {"action": "选择仅已确认", "expected": "列表只显示已确认告警"}],
             "fact_refs": refs, "automation": True},
            {"case_id": "TC-VIEW-003", "function_ref": "FN-VIEW", "title": "告警视图模式-严重与全部范围切换",
             "priority": "P2", "test_type": "功能测试", "dfx_dimension": "DFT功能", "dfx_scenario": "边界值",
             "preconditions": ["告警列表同时存在严重和其他级别数据"], "test_data": "告警范围：仅严重、全部",
             "steps": [navigation,
                       {"action": "选择仅严重", "expected": "列表只显示严重告警"},
                       {"action": "选择全部", "expected": "列表恢复显示全部告警"}],
             "fact_refs": refs, "automation": True},
        ]}
        save_cases(self.run_dir, cases)

    def test_one_transaction_can_assign_seven_checks_to_three_test_intents(self) -> None:
        self.assertEqual([], validate_discovery(self.run_dir))
        facts = load_facts(self.run_dir)
        self.assertEqual(1, len(facts["transactions"]))
        self.assertEqual(7, len(facts["transactions"][0]["checks"]))
        skeleton = build_plan_skeleton(self.run_dir)
        self.assertEqual(7, len(skeleton["functions"][0]["checks"]))
        self._write_plan_and_cases()
        self.assertEqual(3, sum(len(item["cases"]) for item in json.loads(artifact_paths(self.run_dir)["plan"].read_text(encoding="utf-8"))["functions"]))
        written = load_cases(self.run_dir)
        self.assertEqual({"transaction_ref": "TX-VIEW", "check_index": 1}, written["cases"][0]["steps"][1]["source_check"])
        self.assertIn("EL-VIEW-MODE", written["cases"][0]["fact_refs"])

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
        self.assertNotIn("menu_path", resumed)
        with self.assertRaisesRegex(ValueError, "choose a new run directory"):
            ensure_run(self.run_dir, "告警管理>告警详情")

    def test_cli_rejects_a_run_directory_outside_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "test_design_cli.py"), "status", "--run-dir", outside],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("must stay inside the current project root", result.stderr)

    def test_resume_automatically_rebuilds_a_stale_facts_view(self) -> None:
        append_events(self.run_dir, [{"kind": "open_item", "data": {
            "category": "observed_risk", "description": "非阻塞提示", "material": False,
            "affected_function_refs": ["FN-VIEW"],
        }}])
        resumed = ensure_run(self.run_dir, "告警管理>告警列表")
        self.assertEqual("告警管理>告警列表", resumed["module_path"])
        self.assertEqual(1, len(load_facts(self.run_dir)["open_items"]))

    def test_recording_defers_fact_rebuild_until_checkpoint(self) -> None:
        facts_path = artifact_paths(self.run_dir)["facts"]
        before = json.loads(facts_path.read_text(encoding="utf-8"))["event_count"]
        append_events(self.run_dir, [{"kind": "open_item", "data": {
            "category": "observed_risk", "description": "页面响应偶发波动", "material": False,
            "affected_function_refs": ["FN-VIEW"],
        }}])
        self.assertEqual(before, json.loads(facts_path.read_text(encoding="utf-8"))["event_count"])
        checkpoint_facts(self.run_dir)
        self.assertEqual(before + 1, json.loads(facts_path.read_text(encoding="utf-8"))["event_count"])

    def test_recompile_without_business_changes_keeps_review_valid(self) -> None:
        self._write_plan_and_cases()
        self.assertEqual("ready", review_run(self.run_dir)["status"])
        time.sleep(1.1)
        compile_facts(self.run_dir)
        paths = artifact_paths(self.run_dir)
        plan = json.loads(paths["plan"].read_text(encoding="utf-8"))
        plan["generated_at"] = "2099-01-01T00:00:00Z"
        paths["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        cases = json.loads(paths["cases"].read_text(encoding="utf-8"))
        cases["updated_at"] = "2099-01-02T00:00:00Z"
        paths["cases"].write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
        self.assertEqual("ready", pipeline_status(self.run_dir)["state"])
        receipt = complete_deliverables(self.run_dir, ROOT)
        self.assertEqual(3, receipt["import_cases"])

    def test_crud_lifecycle_dfx_hint_is_transaction_scoped_once(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "lifecycle"
            ensure_run(run_dir, "告警管理>告警规则")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "告警规则", "menu_path": ["告警管理", "告警规则"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "新建规则"}},
                {"kind": "test_object", "fact_id": "OBJ", "data": {"name": "AI_TEST_RULE_001", "owner": "current_run", "state": "cleaned"}},
                {"kind": "element", "fact_id": "EL-NAME", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "规则名称", "interactive": True}},
                {"kind": "element", "fact_id": "EL-SAVE", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "保存", "interactive": True}},
                {"kind": "transaction", "fact_id": "TX-CREATE", "data": {
                    "function_ref": "FN", "element_refs": ["EL-NAME", "EL-SAVE"], "transaction_type": "create",
                    "test_object_ref": "OBJ", "outcome": "success", "commit_result": "保存成功",
                    "persistence_result": "重新打开后规则名称一致", "effect_result": "规则出现在列表中",
                    "checks": [
                        {"element_ref": "EL-NAME", "action": "输入AI_TEST_RULE_001", "result": "名称字段显示AI_TEST_RULE_001", "result_anchor": {"assertion": "field_equals", "field": "规则名称", "value": "AI_TEST_RULE_001"}},
                        {"element_ref": "EL-SAVE", "action": "单击保存", "result": "页面提示保存成功", "result_anchor": {"assertion": "contains", "value": "保存成功"}},
                    ],
                }},
            ])
            checkpoint = checkpoint_facts(run_dir)
            self.assertTrue(checkpoint["ready"])
            hints = build_plan_skeleton(run_dir)["functions"][0]["dfx_hints"]
            lifecycle = [item for item in hints if item["code"] == "lifecycle"]
            self.assertEqual(1, len(lifecycle))
            self.assertEqual("transaction", lifecycle[0]["scope"])
            self.assertEqual([
                {"transaction_ref": "TX-CREATE", "check_index": 1},
                {"transaction_ref": "TX-CREATE", "check_index": 2},
            ], lifecycle[0]["related_checks"])

    def test_runtime_allocates_ids_and_resolves_batch_local_references(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "runtime-ids"
            ensure_run(run_dir, "告警管理>告警列表")
            recorded = append_events(run_dir, [
                {"kind": "page", "local_ref": "page", "data": {
                    "name": "告警列表", "menu_path": ["告警管理", "告警列表"],
                    "final_scan_status": "stable", "unhandled_element_refs": [],
                }},
                {"kind": "function", "local_ref": "function", "data": {"name": "刷新"}},
                {"kind": "element", "local_ref": "element", "data": {
                    "page_ref": "@page", "function_ref": "@function", "name": "刷新", "interactive": True,
                }},
                {"kind": "transaction", "data": {
                    "function_ref": "@function", "element_refs": ["@element"], "checks": [
                        {"element_ref": "@element", "action": "单击刷新", "result": "列表数据重新加载", "result_anchor": {"assertion": "contains", "value": "列表数据"}}
                    ],
                }},
            ])
            self.assertTrue(all(item["fact_id"] for item in recorded))
            facts = compile_facts(run_dir)
            self.assertEqual(facts["pages"][0]["fact_id"], facts["elements"][0]["page_ref"])
            self.assertEqual(facts["functions"][0]["fact_id"], facts["transactions"][0]["function_ref"])

    def test_resume_drops_only_a_truncated_final_event_line(self) -> None:
        path = artifact_paths(self.run_dir)["events"]
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write('{"kind":"element","data":')
        events_before = len(path.read_text(encoding="utf-8").splitlines())
        facts = compile_facts(self.run_dir)
        self.assertGreater(facts["event_count"], 0)
        self.assertEqual(events_before - 1, len(path.read_text(encoding="utf-8").splitlines()))

    def test_open_items_use_review_notes_or_real_fact_blocking_without_retry_loop(self) -> None:
        self._write_plan_and_cases()
        append_events(self.run_dir, [{"kind": "open_item", "fact_id": "OPEN-RISK", "data": {
            "category": "observed_risk", "description": "末页返回响应较慢", "material": False,
            "affected_function_refs": ["FN-VIEW"]
        }}])
        compile_facts(self.run_dir)
        review = review_run(self.run_dir)
        self.assertEqual("ready_with_notes", review["status"])
        append_events(self.run_dir, [{"kind": "open_item", "fact_id": "OPEN-QUESTION", "data": {
            "category": "external_question", "description": "告警确认后的外部处置语义待确认", "material": True,
            "affected_function_refs": ["FN-VIEW"]
        }}])
        compile_facts(self.run_dir)
        self.assertEqual("blocked_by_fact", review_run(self.run_dir)["status"])
        append_events(self.run_dir, [{"kind": "open_item", "fact_id": "OPEN-QUESTION", "status": "resolved", "data": {
            "category": "external_question", "description": "用户已确认外部处置语义", "material": True,
            "affected_function_refs": ["FN-VIEW"]
        }}])
        compile_facts(self.run_dir)
        self.assertEqual("ready_with_notes", review_run(self.run_dir)["status"])

    def test_delivery_detects_a_stale_review_without_returning_to_discovery(self) -> None:
        self._write_plan_and_cases()
        self.assertEqual("ready", review_run(self.run_dir)["status"])
        append_events(self.run_dir, [{"kind": "open_item", "fact_id": "OPEN-NOTE", "data": {
            "category": "observed_risk", "description": "非阻塞提示", "material": False,
            "affected_function_refs": ["FN-VIEW"]
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
                    "result": f"页面接受{option}", "result_anchor": {"assertion": "contains", "value": option}, "commit_result": "保存成功", "persistence_result": "重开后回显一致",
                    "effect_result": f"规则按{option}生效", "recovery_result": "恢复基线",
                })
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "新增告警规则", "menu_path": ["告警管理", "告警规则", "新增告警规则"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN-CONFIG", "data": {"name": "通知方式配置"}},
                {"kind": "test_object", "fact_id": "OBJ", "data": {"name": "AI_TEST_RULE_001", "owner": "current_run", "state": "cleaned"}},
                {"kind": "element", "fact_id": "EL-CONFIG", "data": {
                    "function_ref": "FN-CONFIG", "page_ref": "PAGE", "name": "通知方式", "interactive": True,
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
        formal_template = load_workbook(ROOT / "docs" / "test-design" / "codebuddy-test-design-template.xlsx", data_only=False)
        self.assertEqual(SHEETS, workbook.sheetnames)
        for name in SHEETS:
            self.assertEqual(
                len(formal_template[name].data_validations.dataValidation),
                len(workbook[name].data_validations.dataValidation),
            )
            self.assertEqual(set(formal_template[name].tables), set(workbook[name].tables))
        ws = workbook["功能测试用例"]
        self.assertEqual("TC-VIEW-001", ws.cell(2, 1).value)
        self.assertEqual("TC-VIEW-003", ws.cell(4, 1).value)
        self.assertIsNone(ws.cell(5, 1).value)
        self.assertGreaterEqual(ws.row_dimensions[2].height, 40)
        matrix = workbook["测试场景矩阵"]
        matrix_text = "\n".join(str(matrix.cell(row, column).value or "") for row in range(2, matrix.max_row + 1) for column in range(1, matrix.max_column + 1))
        self.assertNotIn("TX-VIEW", matrix_text)
        self.assertNotIn("EL-VIEW", matrix_text)
        matrix_headers = {cell.value: cell.column for cell in matrix[1]}
        self.assertEqual("P2", matrix.cell(4, matrix_headers["优先级"]).value)
        self.assertEqual("告警范围：仅严重、全部", matrix.cell(4, matrix_headers["输入数据/状态条件"]).value)
        self.assertIn("严重告警", matrix.cell(4, matrix_headers["观察点"]).value)
        coverage = workbook["页面元素覆盖清单"]
        coverage_text = "\n".join(str(coverage.cell(row, column).value or "") for row in range(2, coverage.max_row + 1) for column in range(1, coverage.max_column + 1))
        self.assertNotIn("DOM", coverage_text)
        self.assertNotIn("EL-VIEW", coverage_text)
        import_book = load_workbook(import_file, data_only=False)
        import_template = load_workbook(ROOT / "docs" / "test-design" / "测试用例模板.xlsx", data_only=False)
        import_ws = import_book[import_book.sheetnames[0]]
        import_template_ws = import_template[import_template.sheetnames[0]]
        self.assertEqual(len(import_template_ws.data_validations.dataValidation), len(import_ws.data_validations.dataValidation))
        self.assertEqual(set(import_template_ws.tables), set(import_ws.tables))
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
