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
    append_events, artifact_paths, compile_facts, init_run, pipeline_status, review_run,
    validate_cases, validate_discovery, validate_plan,
)


class SingleSessionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temporary.name) / "run-001"
        init_run(self.run_dir, "大数据平台>告警列表", "大数据平台", "需求说明")
        evidence = artifact_paths(self.run_dir)["evidence"] / "pagination.txt"
        evidence.write_text("10/20/30 条及上一页、下一页状态变化已观察", encoding="utf-8")
        append_events(self.run_dir, [
            {"kind": "page", "fact_id": "FACT-PAGE", "data": {"page_id": "PAGE-1", "name": "告警列表"}},
            {"kind": "function", "fact_id": "FACT-FUNCTION", "data": {"function_id": "F-PAGE", "name": "分页"}},
            {"kind": "element", "fact_id": "FACT-ELEMENT", "data": {
                "element_id": "EL-PAGE-SIZE", "function_id": "F-PAGE", "page_id": "PAGE-1",
                "name": "每页条数", "type": "下拉框", "interaction": "选择", "interactive": True,
                "option_set": "finite", "options": [10, 20, 30],
            }},
            {"kind": "observation", "fact_id": "FACT-OBS", "element_id": "EL-PAGE-SIZE", "data": {
                "function_id": "F-PAGE", "action": "依次选择10、20、30条并操作上一页和下一页",
                "option_values": [10, 20, 30], "before": "记录初始总数和当前页",
                "result": "列表条数和总页数分别变化，翻页后页码与按钮状态正确",
                "recovery_result": "恢复为初始每页条数和第一页",
            }, "evidence": [{"path": "artifacts/discovery/evidence/pagination.txt", "location": "全文"}]},
        ])
        compile_facts(self.run_dir)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_plan_and_cases(self) -> None:
        plan = {
            "schema_version": "1.0", "source": "artifacts/discovery/facts.json", "risks": [],
            "functions": [{"function_id": "F-PAGE", "name": "分页", "cases": [
                {"case_id": "TC-PAGE-001", "title": "每页条数切换", "strategy": "baseline",
                 "dfx_dimension": "DFT功能", "dfx_scenario": "正向流程",
                 "fact_ids": ["FACT-FUNCTION", "FACT-ELEMENT", "FACT-OBS"]},
                {"case_id": "TC-PAGE-002", "title": "下一页与上一页切换", "strategy": "DFX",
                 "dfx_dimension": "DFR可靠", "dfx_scenario": "状态一致性",
                 "fact_ids": ["FACT-FUNCTION", "FACT-ELEMENT", "FACT-OBS"]},
            ]}],
        }
        artifact_paths(self.run_dir)["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        cases = {"schema_version": "1.0", "source_plan": "case-plan.json", "cases": [
            {"case_id": "TC-PAGE-001", "function_id": "F-PAGE", "title": "分页-每页条数切换",
             "priority": "P1", "test_type": "功能测试", "dfx_dimension": "DFT功能", "dfx_scenario": "正向流程",
             "preconditions": ["进入告警列表并记录总记录数和初始总页数"], "test_data": "页面存在超过30条告警测试数据",
             "steps": ["打开每页条数下拉框并选择10条", "依次选择20条和30条"],
             "expected_results": ["列表最多展示10条且总页数按10条重新计算", "列表上限和总页数分别按20条、30条重新计算"],
             "fact_ids": ["FACT-FUNCTION", "FACT-ELEMENT", "FACT-OBS"], "automation": True},
            {"case_id": "TC-PAGE-002", "function_id": "F-PAGE", "title": "分页-下一页与上一页切换",
             "priority": "P1", "test_type": "功能测试", "dfx_dimension": "DFR可靠", "dfx_scenario": "状态一致性",
             "preconditions": ["告警列表至少有两页数据且当前位于第一页"], "test_data": "多页告警测试数据",
             "steps": ["点击下一页并记录当前页码和首条告警", "点击上一页返回"],
             "expected_results": ["当前页码增加且列表切换到下一页数据", "返回第一页且列表恢复第一页数据"],
             "fact_ids": ["FACT-FUNCTION", "FACT-ELEMENT", "FACT-OBS"], "automation": True},
        ]}
        artifact_paths(self.run_dir)["cases"].write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    def test_continuous_transaction_covers_finite_options_in_one_observation(self) -> None:
        self.assertEqual([], validate_discovery(self.run_dir))
        facts = compile_facts(self.run_dir)
        self.assertEqual(1, len(facts["observations"]))
        self.assertEqual([10, 20, 30], facts["observations"][0]["option_values"])

    def test_stage_boundaries_and_bidirectional_review(self) -> None:
        self._write_plan_and_cases()
        self.assertEqual([], validate_plan(self.run_dir))
        self.assertEqual([], validate_cases(self.run_dir))
        facts_before = artifact_paths(self.run_dir)["facts"].read_bytes()
        self.assertEqual("passed", review_run(self.run_dir)["status"])
        self.assertEqual(facts_before, artifact_paths(self.run_dir)["facts"].read_bytes())
        self.assertEqual("delivery", pipeline_status(self.run_dir)["stage"])

    def test_configuration_requires_default_and_each_single_factor_closure(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "config-run"
            init_run(run_dir, "平台>新增告警规则")
            evidence_dir = artifact_paths(run_dir)["evidence"]
            events = [
                {"kind": "page", "fact_id": "P", "data": {"page_id": "P1", "name": "新增告警规则"}},
                {"kind": "function", "fact_id": "F", "data": {"function_id": "F1", "name": "通知方式配置"}},
                {"kind": "test_object", "fact_id": "OBJ", "data": {"test_object_id": "AI_TEST_RULE_001", "owner": "current-run"}},
                {"kind": "element", "fact_id": "E", "data": {
                    "element_id": "E1", "function_id": "F1", "name": "通知方式", "interactive": True,
                    "configuration": True, "option_set": "finite", "options": ["不配置", "邮件", "短信"],
                    "default_value": "不配置",
                }},
            ]
            for index, option in enumerate(["不配置", "邮件", "短信"], 1):
                evidence = evidence_dir / f"config-{index}.txt"
                evidence.write_text(f"{option} 保存、重开和效果均已验证", encoding="utf-8")
                events.append({
                    "kind": "observation", "fact_id": f"O{index}", "element_id": "E1",
                    "data": {"function_id": "F1", "closure": "create" if index == 1 else "configuration", "variant": "default" if index == 1 else "configured",
                             "option_value": option, "action": f"设置{option}", "commit_result": "保存成功",
                             "persistence_result": "重开后回显一致", "effect_result": f"实际按{option}生效",
                             "recovery_result": "恢复基线", "outcome": "success", "test_object_id": "AI_TEST_RULE_001"},
                    "evidence": [{"path": f"artifacts/discovery/evidence/config-{index}.txt"}],
                })
            append_events(run_dir, events)
            compile_facts(run_dir)
            self.assertEqual([], validate_discovery(run_dir))

    def test_delivery_has_eight_sheets_import_file_and_no_blank_case_rows(self) -> None:
        self._write_plan_and_cases()
        review_run(self.run_dir)
        receipt = complete_deliverables(self.run_dir, ROOT)
        delivery = artifact_paths(self.run_dir)["delivery"]
        formal = delivery / receipt["formal_workbook"]
        import_file = delivery / receipt["import_workbook"]
        self.assertTrue(formal.is_file())
        self.assertTrue(import_file.is_file())
        workbook = load_workbook(formal, data_only=False)
        self.assertEqual(SHEETS, workbook.sheetnames)
        ws = workbook["功能测试用例"]
        self.assertEqual("TC-PAGE-001", ws.cell(2, 1).value)
        self.assertEqual("TC-PAGE-002", ws.cell(3, 1).value)
        self.assertIsNone(ws.cell(4, 1).value)
        self.assertEqual(1, workbook["性能测试设计"].max_row)
        import_book = load_workbook(import_file, data_only=False)
        import_ws = import_book[import_book.sheetnames[0]]
        self.assertEqual(3, import_ws.max_row)
        validated = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate-test-design-deliverable.py"),
             "--workbook", str(formal), "--import-workbook", str(import_file)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, validated.returncode, validated.stderr)


if __name__ == "__main__":
    unittest.main()
