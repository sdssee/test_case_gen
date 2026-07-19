# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_design.session_runtime import (
    append_events, artifact_paths, build_plan_skeleton, checkpoint_facts, ensure_run,
    load_facts, pending_exploration_requirements, save_cases, save_plan,
)


def _mark_final_scan(run_dir: Path, page_ref: str = "PAGE") -> None:
    append_events(run_dir, [{
        "kind": "page", "fact_id": page_ref,
        "data": {"final_scan_status": "stable", "unhandled_element_refs": []},
    }])


def _plan_metadata(goal: str) -> dict[str, object]:
    return {
        "design_context": {
            "user_goal": goal, "role": "具备页面访问权限的用户", "business_value": "保证功能按页面规则工作",
            "acceptance_criteria": ["每个独立场景产生明确结果"],
            "business_rules": ["独立等价类分别验证"],
            "dependencies": ["具备页面访问权限"], "postcondition": "页面恢复稳定状态", "basis": ["页面实探"],
        },
        "automation_profile": {
            "level": "UI", "dependency": "受控数据", "stability_risk": "控件变化", "recommendation": "项目UI框架",
        },
    }


def _plan_decisions() -> dict[str, object]:
    return {
        "performance_scenarios": [], "performance_not_applicable_reason": "无可独立定义的性能指标",
        "risks": [], "risk_not_applicable_reason": "实探未发现需单独登记的风险",
    }


class QualityRuleTests(unittest.TestCase):
    def test_final_scan_must_follow_the_last_transaction_and_remains_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "final-scan"
            ensure_run(run_dir, "工具>执行页")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {
                    "name": "执行页", "menu_path": ["工具", "执行页"],
                    "final_scan_status": "stable", "unhandled_element_refs": [],
                }},
                {"kind": "function", "fact_id": "FN", "data": {"name": "执行"}},
                {"kind": "element", "fact_id": "EL", "data": {
                    "page_ref": "PAGE", "function_ref": "FN", "name": "执行", "type": "button",
                }},
                {"kind": "transaction", "fact_id": "TX", "data": {
                    "function_ref": "FN", "element_refs": ["EL"], "checks": [{
                        "element_ref": "EL", "action": "点击执行", "result": "显示处理结果",
                        "result_anchor": {"assertion": "contains", "value": "处理结果"},
                    }],
                }},
            ])
            self.assertFalse(checkpoint_facts(run_dir)["ready"])
            _mark_final_scan(run_dir)
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            events_path = artifact_paths(run_dir)["events"]
            before = len(events_path.read_text(encoding="utf-8").splitlines())
            _mark_final_scan(run_dir)
            after = len(events_path.read_text(encoding="utf-8").splitlines())
            self.assertEqual(before, after)

    def test_independent_branches_cannot_reuse_one_physical_action(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "physical-actions"
            ensure_run(run_dir, "工具>选择页")
            with self.assertRaisesRegex(ValueError, "reuse the same physical action"):
                append_events(run_dir, [
                    {"kind": "page", "fact_id": "PAGE", "data": {"name": "选择页", "menu_path": ["工具", "选择页"]}},
                    {"kind": "function", "fact_id": "FN", "data": {"name": "选择"}},
                    {"kind": "element", "fact_id": "EL-A", "data": {
                        "page_ref": "PAGE", "function_ref": "FN", "name": "选项A", "type": "select", "options": ["A"],
                    }},
                    {"kind": "element", "fact_id": "EL-B", "data": {
                        "page_ref": "PAGE", "function_ref": "FN", "name": "选项B", "type": "select", "options": ["B"],
                    }},
                    {"kind": "transaction", "fact_id": "TX", "data": {
                        "function_ref": "FN", "element_refs": ["EL-A", "EL-B"], "checks": [
                            {"element_ref": "EL-A", "option_value": "A", "action": "执行同一操作", "result": "显示A结果", "result_anchor": {"assertion": "contains", "value": "A结果"}},
                            {"element_ref": "EL-B", "option_value": "B", "action": "执行同一操作", "result": "显示B结果", "result_anchor": {"assertion": "contains", "value": "B结果"}},
                        ],
                    }},
                ])

    def test_observed_anomaly_cannot_be_silently_marked_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "anomaly"
            ensure_run(run_dir, "工具>处理页")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "处理页", "menu_path": ["工具", "处理页"]}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "处理"}},
                {"kind": "element", "fact_id": "EL", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "执行", "type": "button"}},
                {"kind": "transaction", "fact_id": "TX", "data": {"function_ref": "FN", "element_refs": ["EL"], "checks": [
                    {"element_ref": "EL", "action": "首次点击执行", "result": "显示完成结果", "result_anchor": {"assertion": "contains", "value": "完成结果"}},
                    {"element_ref": "EL", "action": "再次点击执行", "result": "显示非预期错误", "outcome": "unexpected", "result_anchor": {"assertion": "contains", "value": "错误"}},
                ]}},
            ])
            _mark_final_scan(run_dir)
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            plan = {
                "schema_version": "2.0", **_plan_decisions(),
                "functions": [{"function_ref": "FN", "name": "处理", **_plan_metadata("执行页面处理"), "cases": [
                    {"case_id": "TC-BASE", "page_ref": "PAGE", "title": "正常执行", "strategy": "baseline"},
                ]}],
                "check_assignments": [
                    {"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-BASE"},
                    {"transaction_ref": "TX", "check_index": 2, "disposition": "not_applicable", "reason": "暂不处理"},
                ],
            }
            with self.assertRaisesRegex(ValueError, "unexpected observed check"):
                save_plan(run_dir, plan)

    def test_each_valid_input_equivalence_class_requires_an_independent_baseline_case(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "valid-classes"
            ensure_run(run_dir, "工具>诊断")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "诊断", "menu_path": ["工具", "诊断"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "目标诊断"}},
                {"kind": "element", "fact_id": "EL", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "目标", "type": "文本输入框", "interactive": True, "valid_input_classes": ["domain", "ip"]}},
                {"kind": "element", "fact_id": "RUN", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "执行", "type": "按钮", "interactive": True}},
                {"kind": "transaction", "fact_id": "TX", "data": {"function_ref": "FN", "element_refs": ["EL", "RUN"], "checks": [
                    {"element_ref": "EL", "used_element_refs": ["EL", "RUN"], "trigger_element_ref": "RUN", "input_class": "valid_domain", "action": "输入有效域名并点击执行", "result": "显示域名诊断结果", "result_anchor": {"assertion": "contains", "value": "域名诊断结果"}},
                    {"element_ref": "EL", "used_element_refs": ["EL", "RUN"], "trigger_element_ref": "RUN", "input_class": "valid_ip", "action": "输入有效IP并点击执行", "result": "显示IP诊断结果", "result_anchor": {"assertion": "contains", "value": "IP诊断结果"}},
                ]}},
            ])
            _mark_final_scan(run_dir)
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            combined = {
                "schema_version": "2.0", **_plan_decisions(), "functions": [{"function_ref": "FN", "name": "目标诊断", **_plan_metadata("使用不同目标执行诊断"), "cases": [
                    {"case_id": "TC-COMBINED", "page_ref": "PAGE", "title": "有效目标诊断", "strategy": "baseline"}
                ]}], "check_assignments": [
                    {"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-COMBINED"},
                    {"transaction_ref": "TX", "check_index": 2, "disposition": "case", "case_id": "TC-COMBINED"},
                ],
            }
            with self.assertRaisesRegex(ValueError, "independent branches"):
                save_plan(run_dir, combined)
            separate = {
                "schema_version": "2.0", **_plan_decisions(), "functions": [{"function_ref": "FN", "name": "目标诊断", **_plan_metadata("使用不同目标执行诊断"), "cases": [
                    {"case_id": "TC-DOMAIN", "page_ref": "PAGE", "title": "有效域名诊断", "strategy": "baseline"},
                    {"case_id": "TC-IP", "page_ref": "PAGE", "title": "有效IP诊断", "strategy": "baseline"},
                ]}], "check_assignments": [
                    {"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-DOMAIN"},
                    {"transaction_ref": "TX", "check_index": 2, "disposition": "case", "case_id": "TC-IP"},
                ],
            }
            saved = save_plan(run_dir, separate)
            self.assertEqual(2, len(saved["functions"][0]["cases"]))
            focuses = [row["verification_focus"] for row in saved["functions"][0]["cases"]]
            self.assertEqual(2, len(set(focuses)))
            self.assertTrue(any("valid_domain" in value for value in focuses))
            self.assertTrue(any("valid_ip" in value for value in focuses))

    def test_page_plan_derives_focus_light_performance_risk_and_ui_layer_once(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "derived-design"
            ensure_run(run_dir, "业务中心>执行页面")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "执行页面", "menu_path": ["业务中心", "执行页面"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "任务执行"}},
                {"kind": "element", "fact_id": "EL-INPUT", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "任务参数", "type": "input", "valid_input_classes": ["text"]}},
                {"kind": "element", "fact_id": "EL-RUN", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "执行", "type": "trigger"}},
                {"kind": "transaction", "fact_id": "TX", "data": {"function_ref": "FN", "element_refs": ["EL-INPUT", "EL-RUN"], "checks": [{
                    "element_ref": "EL-INPUT", "used_element_refs": ["EL-INPUT", "EL-RUN"], "trigger_element_ref": "EL-RUN",
                    "input_class": "valid_text", "action": "输入受控内容并点击执行", "result": "显示任务完成提示",
                    "intermediate_states": ["执行中"], "completion_state": "任务完成",
                    "result_anchor": {"assertion": "contains", "value": "任务完成提示"},
                }]}},
            ])
            _mark_final_scan(run_dir)
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            saved = save_plan(run_dir, {
                "schema_version": "2.0", **_plan_decisions(), "functions": [{
                    "function_ref": "FN", **_plan_metadata("使用受控参数执行任务"),
                    "automation_profile": {"level": "CLI", "dependency": "受控数据", "stability_risk": "外部服务响应波动", "recommendation": "现有自动化框架"},
                    "cases": [{"case_id": "TC-1", "page_ref": "PAGE", "title": "有效参数执行", "strategy": "baseline"}],
                }],
                "check_assignments": [{"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-1"}],
            })
            function = saved["functions"][0]
            self.assertEqual("任务执行", function["name"])
            self.assertEqual("UI", function["automation_profile"]["level"])
            self.assertIn("任务参数", function["cases"][0]["verification_focus"])
            self.assertEqual("单次响应与超时体验", saved["performance_scenarios"][0]["test_type"])
            self.assertNotIn("performance_not_applicable_reason", saved)
            self.assertEqual(["外部服务响应波动"], [row["description"] for row in saved["risks"]])
            self.assertNotIn("risk_not_applicable_reason", saved)

            deduplicated = save_plan(run_dir, {
                "schema_version": "2.0",
                "risks": [{
                    "description": "外部依赖不可用可能导致响应超时", "impact": "任务执行",
                    "level": "中", "recommendation": "使用受控数据并提供明确超时反馈", "status": "已识别",
                    "dfx_dimension": "DFR可靠",
                }],
                "functions": [{
                    "function_ref": "FN", **_plan_metadata("使用受控参数执行任务"),
                    "automation_profile": {"level": "UI", "dependency": "受控数据", "stability_risk": "外部服务响应波动", "recommendation": "现有自动化框架"},
                    "cases": [{"case_id": "TC-1", "page_ref": "PAGE", "title": "有效参数执行", "strategy": "baseline"}],
                }],
                "check_assignments": [{"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-1"}],
            })
            self.assertEqual(1, len(deduplicated["risks"]))
            self.assertEqual("外部依赖不可用可能导致响应超时", deduplicated["risks"][0]["description"])

            quantified = {
                "schema_version": "2.0",
                "performance_scenarios": [{
                    "flow": "执行受控任务", "test_type": "响应时间", "concurrency": "单用户",
                    "throughput": "不适用", "response_time": "5秒内完成", "data_scale": "单条受控数据",
                    "duration": "单次执行", "metrics": "开始与完成时间", "pass_criteria": "5秒内完成",
                    "data_strategy": "复用受控数据", "risk": "外部依赖波动", "included": "是",
                }],
                "risks": [], "risk_not_applicable_reason": "实探未发现需单独登记的风险",
                "functions": [{
                    "function_ref": "FN", **_plan_metadata("使用受控参数执行任务"),
                    "cases": [{"case_id": "TC-1", "page_ref": "PAGE", "title": "有效参数执行", "strategy": "baseline"}],
                }],
                "check_assignments": [{"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-1"}],
            }
            with self.assertRaisesRegex(ValueError, "quantified time target"):
                save_plan(run_dir, quantified)

            conflicting_legacy = dict(quantified)
            conflicting_legacy["performance_scenarios"] = [dict(
                quantified["performance_scenarios"][0],
                response_time="记录实际响应时间",
                pass_criteria="页面给出明确完成反馈",
                included="是",
                included_in_current_test="否",
            )]
            with self.assertRaisesRegex(ValueError, "included conflicts"):
                save_plan(run_dir, conflicting_legacy)

    def test_trigger_reference_alone_does_not_force_a_performance_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "trigger-only"
            ensure_run(run_dir, "业务中心>操作页面")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "操作页面", "menu_path": ["业务中心", "操作页面"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "普通操作"}},
                {"kind": "element", "fact_id": "EL", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "内容", "type": "input", "valid_input_classes": ["text"]}},
                {"kind": "element", "fact_id": "RUN", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "执行", "type": "trigger"}},
                {"kind": "transaction", "fact_id": "TX", "data": {"function_ref": "FN", "element_refs": ["EL", "RUN"], "checks": [{
                    "element_ref": "EL", "used_element_refs": ["EL", "RUN"], "trigger_element_ref": "RUN",
                    "input_class": "valid_text", "action": "输入受控内容并点击执行", "result": "立即显示处理结果",
                    "result_anchor": {"assertion": "contains", "value": "处理结果"},
                }]}} ,
            ])
            _mark_final_scan(run_dir)
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            saved = save_plan(run_dir, {
                "schema_version": "2.0", **_plan_decisions(),
                "functions": [{
                    "function_ref": "FN", **_plan_metadata("执行普通页面操作"),
                    "cases": [{"case_id": "TC-1", "page_ref": "PAGE", "title": "有效内容执行", "strategy": "baseline"}],
                }],
                "check_assignments": [{"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-1"}],
            })
            self.assertEqual([], saved["performance_scenarios"])
            self.assertIn("performance_not_applicable_reason", saved)

    def test_persisted_facts_reject_unmasked_url_or_ip(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "sensitive"
            ensure_run(run_dir, "工具>目标校验")
            with self.assertRaisesRegex(ValueError, "mask URLs and IP"):
                append_events(run_dir, [{"kind": "element", "data": {
                    "name": "目标地址", "type": "文本输入框", "default_value": "192.168.1.1"
                }}])

    def test_element_properties_compile_a_dynamic_dfx_exploration_plan(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "dynamic-plan"
            ensure_run(run_dir, "工具>诊断")
            element = append_events(run_dir, [{"kind": "element", "fact_id": "EL", "data": {
                "name": "目标", "type": "input", "interactive": True,
                "input_format": "domain", "unique": True, "min_length": 1, "max_length": 253,
            }}])[0]
            requirements = element["data"]["exploration_requirements"]
            self.assertEqual(
                ["valid", "invalid_format", "duplicate", "boundary_min", "boundary_max"],
                [item["value"] for item in requirements],
            )
            self.assertNotIn("empty", [item["value"] for item in requirements])
            self.assertEqual("baseline", requirements[0]["strategy"])
            self.assertTrue(all(item["strategy"] == "DFX" for item in requirements[1:]))

    def test_declared_valid_input_classes_are_known_before_interaction(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "valid-input-plan"
            ensure_run(run_dir, "工具>诊断")
            element = append_events(run_dir, [{"kind": "element", "fact_id": "EL", "data": {
                "name": "目标", "type": "input", "valid_input_classes": ["domain", "ip"]
            }}])[0]
            self.assertEqual(
                ["valid_domain", "valid_ip"],
                [row["value"] for row in element["data"]["exploration_requirements"]],
            )
            self.assertTrue(all(row["independent_case"] for row in element["data"]["exploration_requirements"]))

    def test_auxiliary_use_does_not_complete_an_elements_primary_branch(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "primary-coverage"
            ensure_run(run_dir, "工具>筛选")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "筛选", "menu_path": ["工具", "筛选"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "联动筛选"}},
                {"kind": "element", "fact_id": "EL-A", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "主筛选", "type": "下拉框", "option_set": "finite", "options": ["全部"]}},
                {"kind": "element", "fact_id": "EL-B", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "辅助筛选", "type": "下拉框", "option_set": "finite", "options": ["全部"]}},
                {"kind": "transaction", "fact_id": "TX", "data": {"function_ref": "FN", "element_refs": ["EL-A", "EL-B"], "checks": [{
                    "element_ref": "EL-A", "used_element_refs": ["EL-A", "EL-B"], "option_value": "全部",
                    "action": "主筛选和辅助筛选均选择全部", "result": "显示全部记录", "result_anchor": {"assertion": "contains", "value": "全部记录"},
                }]}}
            ])
            pending = pending_exploration_requirements(run_dir)
            self.assertEqual(["EL-B"], [row["element_ref"] for row in pending])

    def test_observed_control_relationships_create_only_valid_case_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "constrained-relationships"
            ensure_run(run_dir, "工具>处理")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {
                    "name": "处理", "menu_path": ["工具", "处理"],
                    "final_scan_status": "stable", "unhandled_element_refs": [],
                }},
                {"kind": "function", "fact_id": "FN", "data": {"name": "目标处理"}},
                {"kind": "element", "fact_id": "EL-TARGET", "data": {
                    "page_ref": "PAGE", "function_ref": "FN", "name": "目标输入", "type": "input",
                    "valid_input_classes": [
                        {"value": "type_a", "description": "有效类型A"},
                        {"value": "type_b", "description": "有效类型B"},
                    ],
                }},
                {"kind": "element", "fact_id": "EL-MODE", "data": {
                    "page_ref": "PAGE", "function_ref": "FN", "name": "执行模式", "type": "select",
                    "options": ["模式一", "模式二"], "default_value": "模式一",
                }},
                {"kind": "element", "fact_id": "EL-RUN", "data": {
                    "page_ref": "PAGE", "function_ref": "FN", "name": "执行", "type": "trigger",
                }},
                {"kind": "transaction", "fact_id": "TX", "data": {
                    "function_ref": "FN", "element_refs": ["EL-TARGET", "EL-MODE", "EL-RUN"], "checks": [
                        {"element_ref": "EL-TARGET", "used_element_refs": ["EL-TARGET", "EL-MODE", "EL-RUN"],
                         "trigger_element_ref": "EL-RUN", "input_class": "type_a", "option_value": "模式一",
                         "action": "输入受控类型A数据，选择模式一并点击执行", "result": "显示模式一类型A结果",
                         "result_anchor": {"assertion": "contains", "stable_tokens": ["类型A结果"]}},
                        {"element_ref": "EL-TARGET", "used_element_refs": ["EL-TARGET", "EL-MODE", "EL-RUN"],
                         "trigger_element_ref": "EL-RUN", "input_class": "type_a", "option_value": "模式二",
                         "action": "输入受控类型A数据，选择模式二并点击执行", "result": "显示模式二类型A结果",
                         "result_anchor": {"assertion": "contains", "stable_tokens": ["类型A结果"]}},
                        {"element_ref": "EL-TARGET", "used_element_refs": ["EL-TARGET", "EL-MODE", "EL-RUN"],
                         "trigger_element_ref": "EL-RUN", "input_class": "type_b", "option_value": "模式一",
                         "action": "输入受控类型B数据，先选择模式二，再切回模式一并点击执行", "result": "显示模式一类型B结果",
                         "result_anchor": {"assertion": "contains", "stable_tokens": ["类型B结果"]}},
                    ],
                }},
            ])
            self.assertEqual([], pending_exploration_requirements(run_dir))
            _mark_final_scan(run_dir)
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            checks = load_facts(run_dir)["transactions"][0]["checks"]
            self.assertTrue(all(len(check["branch_bindings"]) == 2 for check in checks))
            skeleton = build_plan_skeleton(run_dir)
            required = skeleton["functions"][0]["required_case_branches"]
            self.assertEqual(3, len(required))
            self.assertTrue(all(row["kind"] == "relationship" for row in required))
            self.assertEqual(3, len({row["scenario_signature"] for row in required}))
            self.assertEqual(
                {
                    "目标输入-有效类型A与执行模式-模式一",
                    "目标输入-有效类型A与执行模式-模式二",
                    "目标输入-有效类型B与执行模式-模式一",
                },
                {row["test_point_hint"] for row in required},
            )

            plan = {
                "schema_version": "2.0", **_plan_decisions(),
                "functions": [{"function_ref": "FN", "name": "目标处理", **_plan_metadata("处理受控目标"),
                               "cases": [
                                   {"case_id": f"TC-{index}", "page_ref": "PAGE", "title": f"有效关联{index}", "strategy": "baseline"}
                                   for index in range(1, 4)
                               ]}],
                "check_assignments": [
                    {"transaction_ref": "TX", "check_index": index, "disposition": "case", "case_id": f"TC-{index}"}
                    for index in range(1, 4)
                ],
            }
            saved = save_plan(run_dir, plan)
            cases = saved["functions"][0]["cases"]
            self.assertEqual(3, len({row["test_point"] for row in cases}))
            self.assertTrue(all(row["scenario_signature"].startswith("SCN-") for row in cases))
            written = save_cases(run_dir, {"schema_version": "2.0", "cases": [
                {
                    "case_id": f"TC-{index}", "function_ref": "FN",
                    "preconditions": ["具备页面访问权限"],
                    "test_data": f"受控类型{input_type}数据；{mode}",
                    "automation_value": "稳定关联回归", "automation_priority": "P1",
                    "steps": [{"action": action, "expected": expected}],
                }
                for index, (input_type, mode, action, expected) in enumerate([
                    ("A", "模式一", "输入受控类型A数据，选择模式一并点击执行", "页面显示模式一对应的类型A结果"),
                    ("A", "模式二", "输入受控类型A数据，选择模式二并点击执行", "页面显示模式二对应的类型A结果"),
                    ("B", "模式一", "输入受控类型B数据，先选择模式二，再切回模式一并点击执行", "页面显示模式一对应的类型B结果"),
                ], 1)
            ]})
            self.assertEqual(
                [row["test_point"] for row in cases],
                [row["test_point"] for row in written["cases"]],
            )
            self.assertEqual(
                [1, 2, 3],
                [row["steps"][1]["source_check"]["check_index"] for row in written["cases"]],
            )

    def test_relationship_bindings_do_not_cross_satisfy_similar_controls(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "binding-scope"
            ensure_run(run_dir, "工具>双输入")
            append_events(run_dir, [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "双输入", "menu_path": ["工具", "双输入"]}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "双输入处理"}},
                {"kind": "element", "fact_id": "EL-A", "data": {
                    "page_ref": "PAGE", "function_ref": "FN", "name": "输入一", "type": "input",
                    "valid_input_classes": ["type_a", "type_b"],
                }},
                {"kind": "element", "fact_id": "EL-B", "data": {
                    "page_ref": "PAGE", "function_ref": "FN", "name": "输入二", "type": "input",
                    "valid_input_classes": ["type_a", "type_b"],
                }},
                {"kind": "transaction", "fact_id": "TX", "data": {
                    "function_ref": "FN", "element_refs": ["EL-A", "EL-B"], "checks": [{
                        "element_ref": "EL-A", "used_element_refs": ["EL-A", "EL-B"],
                        "branch_bindings": [
                            {"element_ref": "EL-A", "kind": "input_class", "value": "type_a"},
                            {"element_ref": "EL-B", "kind": "input_class", "value": "type_b"},
                        ],
                        "action": "输入一使用类型A，输入二使用类型B并执行处理",
                        "result": "显示双输入处理结果",
                        "result_anchor": {"assertion": "contains", "stable_tokens": ["处理结果"]},
                    }]},
                },
            ])
            pending = {row["element_ref"]: row["requirements"] for row in pending_exploration_requirements(run_dir)}
            self.assertEqual(["valid_type_b"], [row["value"] for row in pending["EL-A"]])
            self.assertEqual(["valid_type_a"], [row["value"] for row in pending["EL-B"]])

    def test_dfx_input_branches_are_declared_before_interaction_without_rejecting_progress(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "diagnostics"
            ensure_run(run_dir, "工具>目标校验")
            base = [
                {"kind": "page", "fact_id": "PAGE", "data": {"name": "目标校验", "menu_path": ["工具", "目标校验"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                {"kind": "function", "fact_id": "FN", "data": {"name": "目标校验"}},
                {"kind": "element", "fact_id": "EL-INPUT", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "目标地址", "type": "文本输入框", "interactive": True, "required": True}},
                {"kind": "element", "fact_id": "EL-RUN", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "执行", "type": "按钮", "interactive": True}},
            ]
            recorded = append_events(run_dir, base)
            input_element = next(item for item in recorded if item["fact_id"] == "EL-INPUT")
            self.assertEqual(
                ["valid", "empty"],
                [item["value"] for item in input_element["data"]["exploration_requirements"]],
            )
            incomplete = {"kind": "transaction", "fact_id": "TX", "data": {
                "function_ref": "FN", "element_refs": ["EL-INPUT", "EL-RUN"], "checks": [
                    {"element_ref": "EL-INPUT", "used_element_refs": ["EL-INPUT", "EL-RUN"], "trigger_element_ref": "EL-RUN",
                     "input_class": "valid_text", "action": "输入有效内容并点击执行", "result": "显示校验结果", "result_anchor": {"assertion": "contains", "value": "校验结果"}},
                ]
            }}
            append_events(run_dir, [incomplete])
            pending = pending_exploration_requirements(run_dir)
            self.assertEqual(["empty"], [item["value"] for item in pending[0]["requirements"]])
            self.assertFalse(checkpoint_facts(run_dir)["ready"])
            append_events(run_dir, [{"kind": "transaction", "fact_id": "TX-EMPTY", "data": {
                "function_ref": "FN", "element_refs": ["EL-INPUT", "EL-RUN"], "checks": [
                    {"element_ref": "EL-INPUT", "used_element_refs": ["EL-INPUT", "EL-RUN"], "trigger_element_ref": "EL-RUN",
                     "input_class": "empty", "action": "清空目标内容并点击执行", "result": "显示内容不能为空提示", "result_anchor": {"assertion": "contains", "value": "不能为空"}}
                ]
            }}])
            _mark_final_scan(run_dir)
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            hints = build_plan_skeleton(run_dir)["functions"][0]["dfx_hints"]
            empty = next(item for item in hints if item["code"] == "empty")
            self.assertEqual([{"transaction_ref": "TX-EMPTY", "check_index": 1}], empty["related_checks"])
            save_plan(run_dir, {
                "schema_version": "2.0", **_plan_decisions(), "functions": [{"function_ref": "FN", "name": "目标校验", **_plan_metadata("执行目标校验"), "cases": [
                    {"case_id": "TC-VALID", "page_ref": "PAGE", "title": "有效内容校验", "strategy": "baseline"},
                    {"case_id": "TC-EMPTY", "page_ref": "PAGE", "title": "空内容校验", "strategy": "DFX", "dfx_dimension": "DFT功能", "dfx_scenario": "必填项为空"},
                ]}], "check_assignments": [
                    {"transaction_ref": "TX", "check_index": 1, "disposition": "case", "case_id": "TC-VALID"},
                    {"transaction_ref": "TX-EMPTY", "check_index": 1, "disposition": "case", "case_id": "TC-EMPTY"},
                ],
            })
            navigation = {"action": "进入工具-目标校验", "expected": "显示目标校验页面"}
            invalid_cases = {"schema_version": "2.0", "cases": [
                {"case_id": "TC-VALID", "function_ref": "FN", "title": "目标校验-有效内容校验", "priority": "P1", "test_type": "功能测试",
                 "preconditions": ["已进入目标页面"], "test_data": "受控有效内容", "steps": [navigation, {"action": "输入有效内容", "expected": "显示校验结果"}]},
                {"case_id": "TC-EMPTY", "function_ref": "FN", "title": "目标校验-空内容校验", "priority": "P2", "test_type": "功能测试",
                 "dfx_dimension": "DFT功能", "dfx_scenario": "必填项为空", "preconditions": ["已登录管理界面"], "test_data": "目标地址为空",
                 "steps": [navigation, {"action": "清空目标内容并点击执行", "expected": "显示内容不能为空提示"}]},
            ]}
            with self.assertRaisesRegex(ValueError, "omits the observed submit/execute trigger"):
                save_cases(run_dir, invalid_cases)

    def test_declared_trigger_control_must_be_used(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value) / "trigger"
            ensure_run(run_dir, "工具>选项执行")
            with self.assertRaisesRegex(ValueError, "declared but not actually used"):
                append_events(run_dir, [
                    {"kind": "page", "fact_id": "PAGE", "data": {"name": "选项执行", "menu_path": ["工具", "选项执行"], "final_scan_status": "stable", "unhandled_element_refs": []}},
                    {"kind": "function", "fact_id": "FN", "data": {"name": "选项执行"}},
                    {"kind": "element", "fact_id": "EL-OPTION", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "模式", "type": "下拉框", "interactive": True, "option_set": "finite", "options": ["模式A"]}},
                    {"kind": "element", "fact_id": "EL-RUN", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "执行", "type": "按钮", "interactive": True}},
                    {"kind": "transaction", "data": {"function_ref": "FN", "element_refs": ["EL-OPTION", "EL-RUN"], "checks": [
                        {"element_ref": "EL-OPTION", "used_element_refs": ["EL-OPTION"], "option_value": "模式A", "action": "选择模式A", "result": "使用模式A", "result_anchor": {"assertion": "contains", "value": "模式A"}}
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
                {"kind": "element", "fact_id": "EL", "data": {"page_ref": "PAGE", "function_ref": "FN", "name": "查询", "type": "按钮", "interactive": True}},
                {"kind": "transaction", "fact_id": "TX", "data": {"function_ref": "FN", "element_refs": ["EL"], "checks": [
                    {"element_ref": "EL", "action": "输入有效条件后点击查询", "result": "列表刷新并显示匹配数据", "result_anchor": {"assertion": "contains", "value": "匹配数据"}},
                    {"element_ref": "EL", "action": "清空条件后点击查询", "result": "列表刷新并显示全部数据", "result_anchor": {"assertion": "contains", "value": "全部数据"}},
                ]}},
            ])
            _mark_final_scan(run_dir)
            self.assertTrue(checkpoint_facts(run_dir)["ready"])
            plan = {"schema_version": "2.0", **_plan_decisions(), "functions": [
                {"function_ref": "FN", "name": "查询", **_plan_metadata("按条件筛选列表"), "cases": [
                    {"case_id": "TC-1", "page_ref": "PAGE", "title": "有效条件", "strategy": "baseline"},
                    {"case_id": "TC-2", "page_ref": "PAGE", "title": "空条件", "strategy": "DFX", "dfx_dimension": "DFT功能", "dfx_scenario": "边界值"},
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
