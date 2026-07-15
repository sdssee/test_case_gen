# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_design.session_runtime import (
    append_events, checkpoint_facts, ensure_run, save_cases, save_plan,
)


class QualityRuleTests(unittest.TestCase):
    def test_crud_cannot_finish_without_persistence_and_effect_facts(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "crud"
            ensure_run(run_dir, "告警管理>新增告警规则")
            with self.assertRaisesRegex(ValueError, "closure fields"):
                append_events(run_dir, [
                    {"kind": "transaction", "fact_id": "TX", "data": {
                        "function_ref": "FN", "element_refs": ["EL"], "transaction_type": "create",
                        "test_object_ref": "OBJ", "outcome": "success", "checks": [
                            {"element_ref": "EL", "action": "单击保存", "result": "页面提示保存成功", "result_anchor": {"assertion": "contains", "value": "保存成功"}, "commit_result": "保存成功"}
                        ]
                    }},
                ])

    def test_case_quality_is_rejected_during_write_not_deferred_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "run"
            ensure_run(run_dir, "告警管理>告警列表")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "告警列表", "menu_path": ["告警管理", "告警列表"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "查询"}},
                {"kind": "element", "fact_id": "EL", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "查询", "interactive": True}},
                {"kind": "transaction", "fact_id": "TX", "data": {"function_ref": "FN", "element_refs": ["EL"], "checks": [
                    {"element_ref": "EL", "action": "输入有效条件后点击查询", "result": "列表刷新并显示匹配数据", "result_anchor": {"assertion": "contains", "value": "匹配数据"}},
                    {"element_ref": "EL", "action": "清空条件后点击查询", "result": "列表刷新并显示全部数据", "result_anchor": {"assertion": "contains", "value": "全部数据"}},
                ]}},
            ])
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            plan = {"schema_version": "2.0", "functions": [
                {"function_ref": "FN", "name": "查询", "cases": [
                    {"case_id": "TC-1", "page_ref": "PAGE", "title": "正常查询", "strategy": "baseline"},
                    {"case_id": "TC-2", "page_ref": "PAGE", "title": "空条件查询", "strategy": "DFX", "dfx_dimension": "DFT功能", "dfx_scenario": "边界值"},
                ]}
            ], "check_assignments": [
                {"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-1"},
                {"transaction_ref": "TX", "check_index": 2, "disposition": "case", "case_id": "TC-2"},
            ]}
            save_plan(run_dir, plan)
            bad_step = {"action": "点击查询并截图UID", "expected": "列表刷新"}
            cases = {"schema_version": "2.0", "cases": [
                {"case_id": "TC-1", "function_ref": "FN", "title": "查询-正常查询", "steps": [bad_step]},
                {"case_id": "TC-2", "function_ref": "FN", "title": "查询-空条件查询", "steps": [bad_step]},
            ]}
            with self.assertRaisesRegex(ValueError, "grouped local correction"):
                save_cases(run_dir, cases)


if __name__ == "__main__":
    unittest.main()
