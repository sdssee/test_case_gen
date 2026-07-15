# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_design.session_runtime import append_events, artifact_paths, compile_facts, init_run, validate_cases


class QualityRuleTests(unittest.TestCase):
    def test_rejects_duplicate_prose_screenshot_and_internal_ids(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "run"
            init_run(run_dir, "模块>功能")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "P", "data": {"page_id": "P1", "name": "页面"}},
                {"kind": "function", "fact_id": "F", "data": {"function_id": "F1", "name": "查询"}},
                {"kind": "element", "fact_id": "E", "data": {"element_id": "E1", "function_id": "F1", "name": "查询", "interactive": True}},
                {"kind": "observation", "fact_id": "O", "element_id": "E1", "data": {"function_id": "F1", "action": "点击查询", "result": "列表刷新"}},
            ])
            compile_facts(run_dir)
            plan = {"functions": [{"function_id": "F1", "name": "查询", "cases": [
                {"case_id": "TC-1", "title": "正常查询", "strategy": "baseline", "fact_ids": ["F", "E", "O"]},
                {"case_id": "TC-2", "title": "空条件查询", "strategy": "DFX", "dfx_dimension": "DFT功能", "dfx_scenario": "边界值", "fact_ids": ["F", "E", "O"]},
            ]}]}
            artifact_paths(run_dir)["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            cases = {"cases": [
                {"case_id": "TC-1", "function_id": "F1", "title": "查询-正常查询", "steps": ["点击查询并截图UID"], "expected_results": ["列表刷新"], "fact_ids": ["F", "E", "O"]},
                {"case_id": "TC-2", "function_id": "F1", "title": "查询-空条件查询", "steps": ["点击查询并截图UID"], "expected_results": ["列表刷新"], "fact_ids": ["F", "E", "O"]},
            ]}
            artifact_paths(run_dir)["cases"].write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
            errors = validate_cases(run_dir)
            self.assertTrue(any("screenshots" in error for error in errors))
            self.assertTrue(any("identifiers" in error for error in errors))
            self.assertTrue(any("duplicates steps" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
