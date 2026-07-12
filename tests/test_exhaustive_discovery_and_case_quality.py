# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

DELIVERABLE_SPEC = importlib.util.spec_from_file_location(
    "validate_test_design_deliverable",
    REPO_ROOT / "scripts/validate-test-design-deliverable.py",
)
assert DELIVERABLE_SPEC and DELIVERABLE_SPEC.loader
DELIVERABLE_VALIDATOR = importlib.util.module_from_spec(DELIVERABLE_SPEC)
DELIVERABLE_SPEC.loader.exec_module(DELIVERABLE_VALIDATOR)

from test_design.validators.case_collection import (
    transfer_counter,
    validate_case_collection,
    validate_case_field_parity,
    validate_plan_function_point_alignment,
)
from test_design.validators.batch_ledgers import (
    is_selection_control,
    risk_page_verification_state,
    validate_operation_plan_rows,
    validate_selection_case_grounding,
    validate_selection_option_rows,
    validate_selection_plan_links,
)
from test_design.fact_store import (
    PRODUCT_MAP_SHEETS,
    document_from_rows,
    module_document_name,
    save_module_document,
    validate_catalog,
)
import test_design_excel_tools as TOOLS


class CaseCollectionQualityTests(unittest.TestCase):
    def case(self, case_id: str, title: str, steps: str, expected: str) -> dict[str, str]:
        return {
            "用例 ID": case_id,
            "用例标题": title,
            "操作步骤": steps,
            "预期结果": expected,
        }

    def test_rejects_different_titles_with_identical_execution_body(self) -> None:
        steps = (
            "1. 打开系统登录入口\n"
            "2. 登录后进入告警列表\n"
            "3. 打开每页条数下拉框\n"
            "4. 在20条/页和30条/页中选择目标条数并观察列表"
        )
        expected = "1. 页面加载成功\n2. 下拉框展示20条/页和30条/页\n3. 列表按目标条数刷新"
        rows = [
            self.case("TC-020", "分页-每页条数-20条/页验证", steps, expected),
            self.case("TC-030", "分页-每页条数-30条/页验证", steps, expected),
        ]
        with self.assertRaisesRegex(ValueError, r"duplicate 操作步骤\+预期结果"):
            validate_case_collection(rows, label="function cases")

    def test_case_ids_embedded_in_the_body_cannot_bypass_duplicate_detection(self) -> None:
        rows = [
            self.case(
                "TC-001",
                "危险操作-关闭确认弹窗",
                "1. 打开系统\n2. 进入危险操作页面\n3. 使用标识 TC-001 点击按钮\n4. 关闭弹窗",
                "1. 页面打开\n2. TC-001 对应弹窗关闭\n3. 数据不变",
            ),
            self.case(
                "TC-002",
                "危险操作-再次关闭确认弹窗",
                "1. 打开系统\n2. 进入危险操作页面\n3. 使用标识 TC-002 点击按钮\n4. 关闭弹窗",
                "1. 页面打开\n2. TC-002 对应弹窗关闭\n3. 数据不变",
            ),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_case_collection(rows, label="function cases")

    def test_allows_shared_navigation_when_specific_action_and_outcome_differ(self) -> None:
        shared = "1. 打开系统登录入口\n2. 登录后进入告警列表\n"
        rows = [
            self.case(
                "TC-020",
                "分页-每页条数-20条/页验证",
                shared + "3. 打开每页条数下拉框\n4. 选择20条/页",
                "1. 告警列表加载完成\n2. 选中20条/页\n3. 当前页最多展示20条记录",
            ),
            self.case(
                "TC-030",
                "分页-每页条数-30条/页验证",
                shared + "3. 打开每页条数下拉框\n4. 选择30条/页",
                "1. 告警列表加载完成\n2. 选中30条/页\n3. 当前页最多展示30条记录",
            ),
        ]
        validate_case_collection(rows, label="function cases")

    def test_page_size_value_must_appear_in_steps_and_expected(self) -> None:
        row = self.case(
            "TC-020",
            "分页-每页条数-20条/页验证",
            "1. 打开系统登录入口\n2. 登录后进入告警列表\n3. 打开每页条数下拉框\n4. 选择目标条数",
            "1. 页面加载成功\n2. 列表按目标条数刷新\n3. 分页信息更新",
        )
        with self.assertRaisesRegex(ValueError, "page-size parameters"):
            validate_case_collection([row], label="function cases")

    def test_target_page_and_explicit_status_segments_must_be_grounded(self) -> None:
        page_case = self.case(
            "TC-PAGE-003",
            "分页-页码跳转-第3页",
            "1. 打开系统\n2. 进入告警列表\n3. 打开页码跳转\n4. 跳转到目标页",
            "1. 页面打开\n2. 列表刷新\n3. 当前页更新",
        )
        with self.assertRaisesRegex(ValueError, "target-page parameters"):
            validate_case_collection([page_case], label="function cases")
        page_case["操作步骤"] += " 3"
        page_case["预期结果"] += "并显示第3页"
        validate_case_collection([page_case], label="function cases")

        status_case = self.case(
            "TC-STATUS-001",
            "告警状态-已确认",
            "1. 打开系统\n2. 进入告警列表\n3. 打开状态筛选\n4. 选择目标状态",
            "1. 页面打开\n2. 列表刷新\n3. 仅显示目标状态记录",
        )
        with self.assertRaisesRegex(ValueError, "status parameters"):
            validate_case_collection([status_case], label="function cases")
        status_case["操作步骤"] = status_case["操作步骤"].replace("目标状态", "已确认")
        status_case["预期结果"] = status_case["预期结果"].replace("目标状态", "已确认")
        validate_case_collection([status_case], label="function cases")

        non_page_case = self.case(
            "TC-THRESHOLD-003",
            "告警阈值-达到第3次后触发",
            "1. 打开系统\n2. 进入告警规则\n3. 连续触发规则\n4. 观察结果",
            "1. 页面打开\n2. 规则执行\n3. 告警按阈值触发",
        )
        validate_case_collection([non_page_case], label="function cases")

    def test_normalized_duplicate_rejects_numbering_and_whitespace_only_differences(self) -> None:
        rows = [
            self.case(
                "TC-001",
                "筛选-名称-正常查询",
                "1. 打开系统\n2. 输入告警名称\n3. 点击搜索",
                "1. 页面打开\n2. 列表显示匹配告警\n3. 数据不变",
            ),
            self.case(
                "TC-002",
                "筛选-名称-重复查询",
                "1、打开系统\n2、 输入告警名称\n3、点击搜索。",
                "1、页面打开\n2、列表显示匹配告警\n3、数据不变。",
            ),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_case_collection(rows, label="function cases")

    def test_duplicate_normalization_preserves_decimal_and_identifier_punctuation(self) -> None:
        rows = [
            self.case(
                "TC-001",
                "版本筛选-输入小数版本",
                "1. 进入版本筛选\n2. 输入 1.2\n3. 点击搜索",
                "1. 列表仅显示版本 1.2 的记录",
            ),
            self.case(
                "TC-002",
                "版本筛选-输入整数版本",
                "1. 进入版本筛选\n2. 输入 12\n3. 点击搜索",
                "1. 列表仅显示版本 12 的记录",
            ),
            self.case(
                "TC-003",
                "编码筛选-输入连字符编码",
                "1. 进入编码筛选\n2. 输入 A-B\n3. 点击搜索",
                "1. 列表仅显示编码 A-B 的记录",
            ),
            self.case(
                "TC-004",
                "编码筛选-输入紧凑编码",
                "1. 进入编码筛选\n2. 输入 AB\n3. 点击搜索",
                "1. 列表仅显示编码 AB 的记录",
            ),
        ]
        validate_case_collection(rows, label="function cases")

    def test_json_to_workbook_parity_compares_standard_fields_by_id(self) -> None:
        source = [{"用例 ID": "TC-001", "用例标题": "筛选-按名称查询", "操作步骤": "步骤A"}]
        target = [{"用例 ID": "TC-001", "用例标题": "筛选-按名称查询", "操作步骤": "步骤B"}]
        with self.assertRaisesRegex(ValueError, "standard fields differ"):
            validate_case_field_parity(
                source,
                target,
                fields=["用例 ID", "用例标题", "操作步骤"],
                source_label="JSON",
                target_label="Excel",
            )

    def test_formal_to_import_counter_preserves_duplicate_multiplicity(self) -> None:
        formal = [
            {"用例标题": "筛选-按名称查询", "操作步骤": "步骤", "预期结果": "结果", "前置条件": "前置"},
            {"用例标题": "筛选-按名称查询", "操作步骤": "步骤", "预期结果": "结果", "前置条件": "前置"},
        ]
        imported = [
            {"测试用例名称": "筛选-按名称查询", "测试步骤描述": "步骤", "测试步骤预期结果": "结果", "前置条件": "前置"},
        ]
        formal_counter = transfer_counter(
            formal,
            {"用例标题": "用例标题", "操作步骤": "操作步骤", "预期结果": "预期结果", "前置条件": "前置条件"},
        )
        import_counter = transfer_counter(
            imported,
            {"用例标题": "测试用例名称", "操作步骤": "测试步骤描述", "预期结果": "测试步骤预期结果", "前置条件": "前置条件"},
        )
        self.assertNotEqual(formal_counter, import_counter)


class ExhaustiveSelectionAndRiskTests(unittest.TestCase):
    def discovery(self) -> dict[str, str]:
        return {
            "最小标题路径": "告警管理>告警列表",
            "页面/入口": "告警列表",
            "元素名称/文案": "每页条数",
            "元素类型": "下拉框",
            "交互方式": "点击后选择",
            "选项取值/输入值": "10条/页,20条/页,30条/页",
        }

    def option(self, value: str, sequence: int, case_id: str) -> dict[str, str]:
        return {
            "最小标题路径": "告警管理>告警列表",
            "页面/入口": "告警列表",
            "元素名称/文案": "每页条数",
            "元素类型": "下拉框",
            "选项值": value,
            "选项序号": str(sequence),
            "可用选项总数": "3",
            "选项集合类型": "有限",
            "是否实际选择": "是",
            "选择前状态": "当前为10条/页",
            "选择后页面变化": f"列表切换为{value}",
            "联动/依赖变化": "分页总数随页容量重新计算",
            "结果分支/后续状态": f"下拉框回显{value}",
            "恢复/清空结果": "恢复为10条/页成功",
            "覆盖策略": "有限集合逐项全量选择",
            "证据路径": "artifacts/screenshots/page-size.png",
            "证据定位": f"option-{sequence}",
            "阻塞原因": "",
            "关联用例ID": case_id,
        }

    @staticmethod
    def split_ids(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    def test_finite_selection_requires_every_option_row(self) -> None:
        rows = [self.option("10条/页", 1, "TC-010"), self.option("20条/页", 2, "TC-020")]
        with self.assertRaisesRegex(ValueError, r"records 2 option\(s\), expected 3"):
            validate_selection_option_rows([self.discovery()], rows, lambda _: True)

    def test_finite_selection_cannot_underreport_total_below_discovery_summary(self) -> None:
        rows = [self.option("10条/页", 1, "TC-010"), self.option("20条/页", 2, "TC-020")]
        for row in rows:
            row["可用选项总数"] = "2"
        with self.assertRaisesRegex(ValueError, "exactly cover every option"):
            validate_selection_option_rows([self.discovery()], rows, lambda _: True)

    def test_date_picker_is_excluded_but_time_named_dropdown_is_not(self) -> None:
        self.assertFalse(
            is_selection_control(
                {"元素名称/文案": "首次告警时间", "元素类型": "日期范围选择器", "交互方式": "选择日期"}
            )
        )
        self.assertTrue(
            is_selection_control(
                {"元素名称/文案": "告警时间范围", "元素类型": "下拉框", "交互方式": "点击下拉并选择"}
            )
        )
        self.assertTrue(
            is_selection_control(
                {"元素名称/文案": "所属集群", "元素类型": "选择器", "交互方式": "点击选择集群"}
            )
        )
        self.assertTrue(
            is_selection_control(
                {"元素名称/文案": "告警级别", "元素类型": "Combobox", "交互方式": "Select an option"}
            )
        )

    def test_finite_selection_accepts_actual_unique_per_option_evidence(self) -> None:
        rows = [
            self.option("10条/页", 1, "TC-010"),
            self.option("20条/页", 2, "TC-020"),
            self.option("30条/页", 3, "TC-030"),
        ]
        counts = validate_selection_option_rows([self.discovery()], rows, lambda _: True)
        self.assertEqual([3], list(counts.values()))

    def test_disabled_option_requires_a_concrete_observed_blocker_and_still_counts(self) -> None:
        rows = [
            self.option("10条/页", 1, "TC-010"),
            self.option("20条/页", 2, "TC-020"),
            self.option("30条/页", 3, "TC-030"),
        ]
        rows[2].update(
            {
                "是否实际选择": "否",
                "选择后页面变化": "30条/页选项保持置灰，列表未刷新",
                "结果分支/后续状态": "当前账号无权限，选项不可选",
                "阻塞原因": "页面实际显示置灰且当前账号无权限",
            }
        )
        counts = validate_selection_option_rows([self.discovery()], rows, lambda _: True)
        self.assertEqual([3], list(counts.values()))
        rows[2]["阻塞原因"] = "权限未知，待确认"
        with self.assertRaisesRegex(ValueError, "concretely observed disabled/unselectable"):
            validate_selection_option_rows([self.discovery()], rows, lambda _: True)

    def test_finite_selection_rejects_reused_evidence_locator(self) -> None:
        rows = [
            self.option("10条/页", 1, "TC-010"),
            self.option("20条/页", 2, "TC-020"),
            self.option("30条/页", 3, "TC-030"),
        ]
        rows[1]["证据定位"] = rows[0]["证据定位"]
        with self.assertRaisesRegex(ValueError, "unique .*证据路径, 证据定位"):
            validate_selection_option_rows([self.discovery()], rows, lambda _: True)

    def test_finite_page_size_cannot_be_mislabeled_dynamic(self) -> None:
        row = self.option("10条/页", 1, "TC-010")
        row.update({"可用选项总数": "动态", "选项集合类型": "动态", "覆盖策略": "搜索、分页、清空"})
        with self.assertRaisesRegex(ValueError, "visibly enumerated lists must use 有限"):
            validate_selection_option_rows([self.discovery()], [row], lambda _: True)

    def test_real_remote_search_selection_can_use_dynamic_strategy(self) -> None:
        discovery = {
            **self.discovery(),
            "元素名称/文案": "集群远程搜索",
            "元素类型": "远程搜索下拉框",
            "交互方式": "输入搜索并滚动加载",
            "选项取值/输入值": "集群A",
        }
        row = {
            **self.option("集群A", 1, "TC-A"),
            "元素名称/文案": "集群远程搜索",
            "元素类型": "远程搜索下拉框",
            "可用选项总数": "动态",
            "选项集合类型": "动态",
            "覆盖策略": "搜索、滚动、无结果、清空",
        }
        validate_selection_option_rows([discovery], [row], lambda _: True)

    def test_locally_searchable_finite_dropdown_cannot_claim_dynamic_coverage(self) -> None:
        discovery = {
            **self.discovery(),
            "元素名称/文案": "告警类型",
            "元素类型": "可搜索下拉框",
            "交互方式": "输入搜索词过滤本地有限选项",
            "选项取值/输入值": "类型A,类型B,类型C",
        }
        row = {
            **self.option("类型A", 1, "TC-TYPE-A"),
            "元素名称/文案": "告警类型",
            "元素类型": "可搜索下拉框",
            "可用选项总数": "动态",
            "选项集合类型": "动态",
            "覆盖策略": "搜索、无结果、清空",
        }
        with self.assertRaisesRegex(ValueError, "actual remote/search/lazy-loading option source"):
            validate_selection_option_rows([discovery], [row], lambda _: True)

    def test_plan_budget_and_ids_are_bound_to_each_option(self) -> None:
        rows = [
            self.option("10条/页", 1, "TC-010"),
            self.option("20条/页", 2, "TC-020"),
            self.option("30条/页", 3, "TC-030"),
        ]
        plan = {
            **{key: self.discovery()[key] for key in ["最小标题路径", "页面/入口", "元素名称/文案", "元素类型"]},
            "应生成用例数": "2",
            "计划用例ID": "TC-010,TC-020,TC-030",
        }
        with self.assertRaisesRegex(ValueError, "3 observed option"):
            validate_selection_plan_links(rows, [plan], self.split_ids)
        plan["应生成用例数"] = "3"
        validate_selection_plan_links(rows, [plan], self.split_ids)
        rows[1]["关联用例ID"] = "TC-010"
        with self.assertRaisesRegex(ValueError, "reuses TC-010"):
            validate_selection_plan_links(rows, [plan], self.split_ids)

    def test_generated_case_must_ground_exact_option_in_steps_and_expected(self) -> None:
        option = self.option("20条/页", 2, "TC-020")
        valid_case = {
            "用例 ID": "TC-020",
            "操作步骤": "1. 进入告警列表\n2. 打开每页条数\n3. 选择20条/页",
            "预期结果": "1. 下拉框回显20条/页\n2. 当前页最多展示20条",
        }
        validate_selection_case_grounding([option], [valid_case], self.split_ids)
        invalid_case = {**valid_case, "预期结果": "1. 列表按目标条数刷新"}
        with self.assertRaisesRegex(ValueError, "exact option value"):
            validate_selection_case_grounding([option], [invalid_case], self.split_ids)

    def test_short_option_cannot_be_grounded_only_as_part_of_a_longer_sibling_option(self) -> None:
        confirm = self.option("确认", 1, "TC-CONFIRM")
        unconfirmed = self.option("未确认", 2, "TC-UNCONFIRMED")
        unconfirmed["关联用例ID"] = ""
        case = {
            "用例 ID": "TC-CONFIRM",
            "操作步骤": "1. 进入告警列表\n2. 选择未确认状态",
            "预期结果": "1. 列表仅展示未确认告警",
        }
        with self.assertRaisesRegex(ValueError, "exact option value"):
            validate_selection_case_grounding([confirm, unconfirmed], [case], self.split_ids)
        case["操作步骤"] += "\n3. 再选择确认状态"
        case["预期结果"] += "\n2. 随后仅展示确认告警"
        validate_selection_case_grounding([confirm, unconfirmed], [case], self.split_ids)

    def test_page_verifiable_question_returns_to_discovery(self) -> None:
        row = {
            "风险ID": "RISK-001",
            "模型不理解内容/待确认问题": "点击屏蔽后弹窗展示什么字段",
            "页面可验证性": "可直接验证",
        }
        state, reasons = risk_page_verification_state([row])
        self.assertEqual("discovery_required", state)
        self.assertTrue(any("directly page-verifiable" in reason for reason in reasons))

    def test_external_dependency_can_reach_user_confirmation(self) -> None:
        row = {
            "风险ID": "RISK-002",
            "模型不理解内容/待确认问题": "异步导出文件的后台编码规则是什么",
            "已完成深探依据": "已点击导出并观察页面任务提示",
            "页面可验证性": "受外部阻塞",
            "页面验证动作": "点击导出并等待页面状态更新",
            "页面验证结果": "页面仅显示任务已创建，不展示后台文件编码",
            "不可验证/外部依赖原因": "需要后端异步任务日志或接口响应",
            "证据路径": "artifacts/screenshots/export.png",
        }
        state, reasons = risk_page_verification_state([row], evidence_exists=lambda _: True)
        self.assertEqual("ready", state, reasons)

    def test_documented_business_semantic_ambiguity_can_reach_user_confirmation(self) -> None:
        row = {
            "风险ID": "RISK-SEMANTIC-001",
            "模型不理解内容/待确认问题": "同级告警同时发生时业务优先级如何判定",
            "已完成深探依据": "已触发多个同级告警并观察列表排序和详情展示",
            "页面可验证性": "不可直接验证",
            "页面验证动作": "逐项切换告警级别并比较列表排序与详情字段",
            "页面验证结果": "页面仅展示排序结果，不说明同级告警业务优先级规则来源",
            "不可验证/外部依赖原因": "需求文档和验收标准未定义同级告警业务优先级规则",
            "证据路径": "artifacts/screenshots/alarm-priority.png",
        }
        state, reasons = risk_page_verification_state([row], evidence_exists=lambda _: True)
        self.assertEqual("ready", state, reasons)

    def test_unexplored_options_and_unknown_permission_cannot_be_external_blocker(self) -> None:
        row = {
            "风险ID": "RISK-003",
            "模型不理解内容/待确认问题": "下拉选项选择后页面如何变化",
            "已完成深探依据": "只展开看到选项",
            "页面可验证性": "受外部阻塞",
            "页面验证动作": "看到选项但未逐项点击",
            "页面验证结果": "尚未观察选择后的页面变化",
            "不可验证/外部依赖原因": "权限未知，待确认",
            "证据路径": "artifacts/screenshots/dropdown.png",
        }
        state, reasons = risk_page_verification_state([row], evidence_exists=lambda _: True)
        self.assertEqual("discovery_required", state)
        self.assertTrue(any("still incomplete" in reason for reason in reasons))
        self.assertTrue(any("uncertain rather than an observed blocker" in reason for reason in reasons))

    def test_plan_function_point_cannot_drift_to_next_case_block(self) -> None:
        plan = [{"功能点": "分页-每页条数", "计划用例ID": "TC-107,TC-108"}]
        cases = [
            {"用例 ID": "TC-107", "功能点": "分页-每页条数"},
            {"用例 ID": "TC-108", "功能点": "分页-翻页"},
        ]
        with self.assertRaisesRegex(ValueError, "mismatched case IDs"):
            validate_plan_function_point_alignment(plan, cases, split_ids=self.split_ids)

    def test_temporary_selection_and_cancel_cannot_be_declared_persisted_mutations(self) -> None:
        common = {
            "适用DFX维度": "DFT功能",
            "适用DFX场景": "正向流程",
            "测试设计方向": "验证交互结果",
            "验证要求": "回显,持久化,实际生效",
            "数据策略": "本次创建测试数据",
            "执行状态": "已完成",
        }
        selection = {
            **common,
            "页面/入口": "告警列表",
            "功能点": "操作-行选择",
            "元素名称/文案": "行复选框",
            "元素类型": "复选框",
            "交互方式": "勾选",
            "操作类别": "状态变更",
        }
        with self.assertRaisesRegex(ValueError, "temporary UI state"):
            validate_operation_plan_rows([selection])
        cancel = {
            **common,
            "页面/入口": "维护经验弹窗",
            "功能点": "弹窗-取消",
            "元素名称/文案": "编辑维护经验弹窗-取消按钮",
            "元素类型": "按钮",
            "交互方式": "点击",
            "操作类别": "编辑",
        }
        with self.assertRaisesRegex(ValueError, "cancel/close/back"):
            validate_operation_plan_rows([cancel])
        cancel_without_button_suffix = {
            **cancel,
            "元素名称/文案": "编辑维护经验弹窗-取消",
        }
        with self.assertRaisesRegex(ValueError, "cancel/close/back"):
            validate_operation_plan_rows([cancel_without_button_suffix])
        query_reset = {
            **common,
            "页面/入口": "告警列表查询条件",
            "功能点": "查询条件重置",
            "元素名称/文案": "重置",
            "元素类型": "按钮",
            "交互方式": "点击",
            "操作类别": "状态变更",
        }
        with self.assertRaisesRegex(ValueError, "filter reset"):
            validate_operation_plan_rows([query_reset])
        close_alarm = {
            **common,
            "页面/入口": "告警详情",
            "功能点": "告警状态变更",
            "元素名称/文案": "关闭告警",
            "元素类型": "按钮",
            "交互方式": "点击后确认",
            "操作类别": "状态变更",
        }
        validate_operation_plan_rows([close_alarm])


class BatchScopeAndCatalogTests(unittest.TestCase):
    def rows(self, product: str) -> dict[str, list[dict[str, str]]]:
        rows = {sheet: [] for sheet in PRODUCT_MAP_SHEETS}
        rows["产品模块地图"] = [{"产品/系统": product, "页面/入口": "告警列表"}]
        return rows

    def test_delivery_reuses_product_preserved_at_batch_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            project_root = Path(value)
            source = REPO_ROOT / "docs/test-assets/batch-runs/templates"
            target = project_root / "docs/test-assets/batch-runs/templates"
            shutil.copytree(source, target)
            run_dir = TOOLS.init_batch_run(
                project_root,
                "product-scope",
                "集群管理>告警管理>告警列表",
                "BATCH-001",
                "DataEngine",
            )
            resolved = TOOLS.validate_delivery_scope(
                run_dir / "batch-status.csv",
                "BATCH-001",
                "集群管理>告警管理>告警列表",
                None,
            )
            self.assertEqual("DataEngine", resolved)
            with self.assertRaisesRegex(ValueError, "does not match the batch scope product"):
                TOOLS.validate_delivery_scope(
                    run_dir / "batch-status.csv",
                    "BATCH-001",
                    "集群管理>告警管理>告警列表",
                    "集群管理",
                )

    def test_resume_adds_scope_and_selection_ledger_to_legacy_run(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            project_root = Path(value)
            source = REPO_ROOT / "docs/test-assets/batch-runs/templates"
            target = project_root / "docs/test-assets/batch-runs/templates"
            shutil.copytree(source, target)
            run_dir = TOOLS.init_batch_run(
                project_root,
                "legacy-selection",
                "集群管理>告警管理>告警列表",
                "BATCH-001",
                "DataEngine",
            )
            (run_dir / "batch-scope.json").unlink()
            (run_dir / "selection-option-observations.csv").unlink()
            resumed = TOOLS.init_batch_run(
                project_root,
                "legacy-selection",
                "集群管理>告警管理>告警列表",
                "BATCH-001",
                "DataEngine",
                resume=True,
            )
            self.assertEqual(run_dir, resumed)
            scope = json.loads((run_dir / "batch-scope.json").read_text(encoding="utf-8-sig"))
            self.assertEqual("DataEngine", scope["product_name"])
            self.assertTrue((run_dir / "selection-option-observations.csv").is_file())

    def test_resume_refuses_to_silently_overwrite_a_corrupt_batch_scope(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            project_root = Path(value)
            source = REPO_ROOT / "docs/test-assets/batch-runs/templates"
            target = project_root / "docs/test-assets/batch-runs/templates"
            shutil.copytree(source, target)
            run_dir = TOOLS.init_batch_run(
                project_root,
                "corrupt-scope",
                "集群管理>告警管理>告警列表",
                "BATCH-001",
                "DataEngine",
            )
            scope_path = run_dir / "batch-scope.json"
            scope_path.write_text("{not-json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "is invalid and cannot be overwritten"):
                TOOLS.init_batch_run(
                    project_root,
                    "corrupt-scope",
                    "集群管理>告警管理>告警列表",
                    "BATCH-001",
                    "DataEngine",
                    resume=True,
                )
            self.assertEqual("{not-json", scope_path.read_text(encoding="utf-8"))

    def test_catalog_rejects_empty_module_document(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            product_map = root / "product-map.xlsx"
            shutil.copy2(REPO_ROOT / "docs/test-assets/product-map.xlsx", product_map)
            save_module_document(
                product_map,
                "DataEngine>集群管理>告警管理>告警列表",
                "DataEngine",
                "集群管理>告警管理>告警列表",
                "archive.xlsx",
                self.rows("DataEngine"),
                {"type": "test", "source": "unit"},
            )
            empty_key = "DataEngine>空模块"
            empty = document_from_rows(
                empty_key,
                "DataEngine",
                "空模块",
                {"type": "test", "source": "unit"},
                {sheet: [] for sheet in PRODUCT_MAP_SHEETS},
            )
            empty_path = root / "catalog/modules" / module_document_name(empty_key)
            empty_path.write_text(json.dumps(empty, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must not be empty"):
                validate_catalog(product_map, require_existing=True)

    def test_catalog_rejects_conflicting_products_for_same_module_path(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            product_map = root / "product-map.xlsx"
            shutil.copy2(REPO_ROOT / "docs/test-assets/product-map.xlsx", product_map)
            module_path = "集群管理>告警管理>告警列表"
            for product in ["DataEngine", "集群管理"]:
                save_module_document(
                    product_map,
                    f"{product}>{module_path}" if product != "集群管理" else module_path,
                    product,
                    module_path,
                    f"{product}.xlsx",
                    self.rows(product),
                    {"type": "test", "source": "unit"},
                )
            with self.assertRaisesRegex(ValueError, "conflicting products"):
                validate_catalog(product_map, require_existing=True)

    def test_catalog_allows_legitimate_same_named_module_paths_across_products(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            product_map = root / "product-map.xlsx"
            shutil.copy2(REPO_ROOT / "docs/test-assets/product-map.xlsx", product_map)
            module_path = "系统管理>用户管理"
            for product in ["DataEngine", "DataLake"]:
                save_module_document(
                    product_map,
                    f"{product}>{module_path}",
                    product,
                    module_path,
                    f"{product}.xlsx",
                    self.rows(product),
                    {"type": "test", "source": f"{product}.xlsx"},
                )
            validate_catalog(product_map, require_existing=True)

    def test_sensitive_gate_rejects_internal_hostname_but_allows_reserved_example_domain(self) -> None:
        with self.assertRaisesRegex(AssertionError, "environment address/account"):
            DELIVERABLE_VALIDATOR.assert_no_unmasked_value(
                "告警来源 management-4c9aa898.hde.com",
                "batch-plan.md line 1",
            )
        DELIVERABLE_VALIDATOR.assert_no_unmasked_value(
            "测试域名 api.example.com",
            "safe placeholder",
        )

    def test_completed_batch_review_cannot_remain_an_empty_template(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value)
            status_path = run_dir / "batch-status.csv"
            status_path.write_text("placeholder", encoding="utf-8")
            row = {
                "批次ID": "BATCH-001",
                "状态": "已完成",
                "页面数": "3",
                "元素总数": "44",
                "已覆盖元素数": "44",
                "功能用例数": "211",
                "性能场景数": "2",
                "归档路径": "docs/test-assets/modules/alarm.xlsx",
                "导入文件路径": "docs/test-assets/imports/alarm.xlsx",
                "覆盖质量自检": "通过",
            }
            review_path = run_dir / "batch-review.md"
            review_path.write_text(
                "| BATCH-001 |  |  |  |  |  |  |  |  |  |  |\n"
                "docs/test-assets/modules/alarm.xlsx\n"
                "docs/test-assets/imports/alarm.xlsx\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(AssertionError, "must match batch-status.csv"):
                DELIVERABLE_VALIDATOR.validate_batch_review(status_path, [row])
            review_path.write_text(
                "| BATCH-001 | 已完成 | 3 | 44 | 44 | 211 | 2 | docs/test-assets/modules/alarm.xlsx | "
                "docs/test-assets/imports/alarm.xlsx | 通过 | 无 |\n",
                encoding="utf-8",
            )
            DELIVERABLE_VALIDATOR.validate_batch_review(status_path, [row])

    def test_delivery_path_sync_populates_the_batch_review_completion_row(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            project_root = Path(value)
            source = REPO_ROOT / "docs/test-assets/batch-runs/templates"
            target = project_root / "docs/test-assets/batch-runs/templates"
            shutil.copytree(source, target)
            run_dir = TOOLS.init_batch_run(
                project_root,
                "review-sync",
                "集群管理>告警管理>告警列表",
                "BATCH-001",
                "DataEngine",
            )
            status_path = run_dir / "batch-status.csv"
            with status_path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                fieldnames = list(reader.fieldnames or [])
                rows = list(reader)
            rows[0].update(
                {
                    "状态": "已完成",
                    "页面数": "3",
                    "元素总数": "44",
                    "已覆盖元素数": "44",
                    "功能用例数": "211",
                    "性能场景数": "2",
                    "归档路径": "docs/test-assets/modules/alarm.xlsx",
                    "导入文件路径": "docs/test-assets/imports/alarm.xlsx",
                    "覆盖质量自检": "通过",
                    "待确认问题": "需确认 A | B\n第二行",
                }
            )
            with status_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            TOOLS.sync_batch_markdown_paths(
                status_path,
                [
                    {
                        "批次ID": "BATCH-001",
                        "旧归档路径": "",
                        "归档路径": rows[0]["归档路径"],
                        "旧导入文件路径": "",
                        "导入文件路径": rows[0]["导入文件路径"],
                    }
                ],
            )

            review_text = (run_dir / "batch-review.md").read_text(encoding="utf-8-sig")
            self.assertIn(
                "| BATCH-001 | 已完成 | 3 | 44 | 44 | 211 | 2 | "
                "docs/test-assets/modules/alarm.xlsx | docs/test-assets/imports/alarm.xlsx | "
                "通过 | 需确认 A ｜ B 第二行 |",
                review_text,
            )
            self.assertNotIn("## 交付收口路径", review_text)
            DELIVERABLE_VALIDATOR.validate_batch_review(status_path, rows)


if __name__ == "__main__":
    unittest.main()
