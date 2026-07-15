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
    append_events, build_plan_skeleton, checkpoint_facts, ensure_run, save_cases, save_plan,
)


class QualityRuleTests(unittest.TestCase):
    def test_persisted_facts_reject_unmasked_url_or_ip(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "sensitive"
            ensure_run(run_dir, "网络>网络诊断")
            with self.assertRaisesRegex(ValueError, "mask URLs and IP"):
                append_events(run_dir, [{"kind": "element", "data": {
                    "name": "目标地址", "type": "文本输入框", "default_value": "192.168.1.1"
                }}])

    def test_required_input_branches_and_trigger_are_enforced_before_recording(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "diagnostics"
            ensure_run(run_dir, "网络>网络诊断")
            base = [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "网络诊断", "menu_path": ["网络", "网络诊断"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "Traceroute"}},
                {"kind": "element", "fact_id": "EL-INPUT", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "目标地址", "type": "文本输入框", "interactive": True, "required": True}},
                {"kind": "element", "fact_id": "EL-RUN", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "Traceroute", "type": "按钮", "interactive": True}},
            ]
            incomplete = {"kind": "transaction", "fact_id": "TX", "data": {
                "function_ref": "FN", "element_refs": ["EL-INPUT", "EL-RUN"], "checks": [
                    {"element_ref": "EL-INPUT", "used_element_refs": ["EL-INPUT", "EL-RUN"], "trigger_element_ref": "EL-RUN",
                     "input_class": "valid_domain", "action": "输入有效域名并点击Traceroute", "result": "显示路由追踪结果", "result_anchor": {"assertion": "contains", "value": "路由追踪结果"}},
                ]
            }}
            with self.assertRaisesRegex(ValueError, "onsite branches.*empty"):
                append_events(run_dir, [*base, incomplete])
            complete = json.loads(json.dumps(incomplete, ensure_ascii=False))
            complete["data"]["checks"].append(
                {"element_ref": "EL-INPUT", "used_element_refs": ["EL-INPUT", "EL-RUN"], "trigger_element_ref": "EL-RUN",
                 "input_class": "empty", "action": "清空目标地址并点击Traceroute", "result": "显示地址不合法提示", "result_anchor": {"assertion": "contains", "value": "不合法"}}
            )
            append_events(run_dir, [*base, complete])
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            hints = build_plan_skeleton(run_dir)["functions"][0]["dfx_hints"]
            empty = next(item for item in hints if item["code"] == "empty")
            self.assertEqual([{"transaction_ref": "TX", "check_index": 2}], empty["related_checks"])
            save_plan(run_dir, {
                "schema_version": "2.0", "functions": [{"function_ref": "FN", "name": "Traceroute", "cases": [
                    {"case_id": "TC-VALID", "page_ref": "PAGE", "title": "正常域名追踪", "strategy": "baseline"},
                    {"case_id": "TC-EMPTY", "page_ref": "PAGE", "title": "空地址校验", "strategy": "DFX", "dfx_dimension": "DFT功能", "dfx_scenario": "必填项为空"},
                ]}], "check_assignments": [
                    {"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-VALID"},
                    {"transaction_ref": "TX", "check_index": 2, "disposition": "case", "case_id": "TC-EMPTY"},
                ],
            })
            navigation = {"action": "进入网络-网络诊断", "expected": "显示网络诊断页面"}
            invalid_cases = {"schema_version": "2.0", "cases": [
                {"case_id": "TC-VALID", "function_ref": "FN", "title": "Traceroute-正常域名追踪", "priority": "P1", "test_type": "功能测试",
                 "preconditions": ["已登录管理界面"], "test_data": "受控有效域名", "steps": [navigation, {"action": "输入有效域名", "expected": "显示路由追踪结果"}]},
                {"case_id": "TC-EMPTY", "function_ref": "FN", "title": "Traceroute-空地址校验", "priority": "P2", "test_type": "功能测试",
                 "dfx_dimension": "DFT功能", "dfx_scenario": "必填项为空", "preconditions": ["已登录管理界面"], "test_data": "目标地址为空",
                 "steps": [navigation, {"action": "清空目标地址并点击Traceroute", "expected": "显示地址不合法提示"}]},
            ]}
            with self.assertRaisesRegex(ValueError, "omits the observed submit/execute trigger"):
                save_cases(run_dir, invalid_cases)

    def test_declared_trigger_control_must_be_used(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "trigger"
            ensure_run(run_dir, "网络>网络诊断")
            with self.assertRaisesRegex(ValueError, "declared but not actually used"):
                append_events(run_dir, [
                    {"kind": "page", "fact_id": "PAGE", "data": {"name": "网络诊断", "menu_path": ["网络", "网络诊断"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                    {"kind": "function", "fact_id": "FN", "data": {"name": "Ping"}},
                    {"kind": "element", "fact_id": "EL-OPTION", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "协议", "type": "下拉框", "interactive": True, "option_set": "finite", "options": ["IPv4"]}},
                    {"kind": "element", "fact_id": "EL-RUN", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "Ping", "type": "按钮", "interactive": True}},
                    {"kind": "transaction", "data": {"function_ref": "FN", "element_refs": ["EL-OPTION", "EL-RUN"], "checks": [
                        {"element_ref": "EL-OPTION", "used_element_refs": ["EL-OPTION"], "option_value": "IPv4", "action": "选择IPv4", "result": "使用IPv4", "result_anchor": {"assertion": "contains", "value": "IPv4"}}
                    ]}},
                ])

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
