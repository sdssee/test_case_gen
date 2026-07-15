# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_design.session_runtime import (
    append_events, artifact_paths, compile_facts, ensure_run, review_run, validate_cases, validate_discovery,
)


class QualityRuleTests(unittest.TestCase):
    def test_crud_cannot_finish_without_persistence_and_effect_facts(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "crud"
            ensure_run(run_dir, "告警管理>新增告警规则")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "新增告警规则", "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "新建"}},
                {"kind": "element", "fact_id": "EL", "data": {"function_ref": "FN", "name": "保存", "interactive": True}},
                {"kind": "test_object", "fact_id": "OBJ", "data": {"name": "AI_TEST_RULE_001", "owner": "current_run", "state": "cleaned"}},
                {"kind": "transaction", "fact_id": "TX", "data": {
                    "function_ref": "FN", "element_refs": ["EL"], "transaction_type": "create",
                    "test_object_ref": "OBJ", "outcome": "success", "checks": [
                        {"element_ref": "EL", "action": "单击保存", "result": "页面提示保存成功", "commit_result": "保存成功"}
                    ]
                }},
            ])
            compile_facts(run_dir)
            errors = validate_discovery(run_dir)
            self.assertTrue(any("persistence_result" in error for error in errors))
            self.assertTrue(any("effect_result" in error for error in errors))

    def test_review_routes_case_quality_problems_to_local_fix(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "run"
            ensure_run(run_dir, "告警管理>告警列表")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "告警列表", "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "查询"}},
                {"kind": "element", "fact_id": "EL", "data": {"function_ref": "FN", "name": "查询", "interactive": True}},
                {"kind": "transaction", "fact_id": "TX", "data": {"function_ref": "FN", "element_refs": ["EL"], "checks": [
                    {"element_ref": "EL", "action": "点击查询", "result": "列表刷新"}
                ]}},
            ])
            compile_facts(run_dir)
            refs = ["FN", "EL", "TX"]
            plan = {"schema_version": "2.0", "source": "facts.json", "non_case_checks": [], "functions": [
                {"function_ref": "FN", "name": "查询", "cases": [
                    {"case_id": "TC-1", "title": "正常查询", "strategy": "baseline", "fact_refs": refs, "covered_checks": {"TX": [1]}},
                    {"case_id": "TC-2", "title": "空条件查询", "strategy": "DFX", "dfx_dimension": "DFT功能", "dfx_scenario": "边界值", "fact_refs": refs, "covered_checks": {}},
                ]}
            ]}
            artifact_paths(run_dir)["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            bad_step = {"action": "点击查询并截图UID", "expected": "列表刷新"}
            cases = {"schema_version": "2.0", "source_plan": "case-plan.json", "cases": [
                {"case_id": "TC-1", "function_ref": "FN", "title": "查询-正常查询", "steps": [bad_step], "fact_refs": refs},
                {"case_id": "TC-2", "function_ref": "FN", "title": "查询-空条件查询", "steps": [bad_step], "fact_refs": refs},
            ]}
            artifact_paths(run_dir)["cases"].write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
            errors = validate_cases(run_dir)
            self.assertTrue(any("screenshot" in error.lower() or "截图" in error for error in errors))
            self.assertTrue(any("internal" in error.lower() or "内部" in error for error in errors))
            self.assertTrue(any("duplicates" in error.lower() or "重复" in error for error in errors))
            review = review_run(run_dir)
            self.assertEqual("needs_local_fix", review["status"])
            self.assertTrue(all("local_repair" in issue for issue in review["issues"]))


if __name__ == "__main__":
    unittest.main()
