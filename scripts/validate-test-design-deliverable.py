# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import posixpath
import re
import sys
import zipfile
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

from test_design.fact_store import validate_catalog
from test_design.paths import module_names
from test_design.sensitive_data import (
    SENSITIVE_VALUE_PATTERNS,
    SensitiveDataError,
    assert_no_sensitive_batch_files as shared_assert_no_sensitive_batch_files,
    assert_no_sensitive_csv_rows as shared_assert_no_sensitive_csv_rows,
    assert_no_sensitive_text_file as shared_assert_no_sensitive_text_file,
    assert_no_unmasked_value as shared_assert_no_unmasked_value,
)
from test_design.contracts.function_cases import (
    FUNCTION_CASE_FORBIDDEN_FIELDS,
    FUNCTION_CASE_PART_RE,
    FUNCTION_CASE_REQUIRED_FIELDS,
    MAX_FUNCTION_CASES_PER_PART,
)
from test_design.validators.batch_ledgers import (
    validate_discovery_rows,
    validate_lifecycle_rows,
    validate_operation_plan_rows,
    validate_page_element_inventory,
    validate_interaction_branch_rows,
    validate_branch_plan_links,
    validate_branch_case_grounding,
    validate_selection_case_grounding,
    validate_selection_option_rows,
    validate_selection_plan_links,
)
from test_design.validators.case_collection import (
    derived_case_quality_counts,
    transfer_counter,
    validate_case_collection,
    validate_case_field_parity,
    validate_case_order_parity,
    validate_contiguous_function_point_groups,
    validate_discovery_plan_case_alignment,
    validate_function_point_aware_shards,
    validate_plan_case_order_alignment,
    validate_plan_function_point_alignment,
)

NS = {
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

EXPECTED_SHEETS = [
    "测试设计总览",
    "需求用户故事拆解",
    "测试场景矩阵",
    "功能测试用例",
    "性能测试设计",
    "风险与待确认问题",
    "自动化建议",
    "页面元素覆盖清单",
]

BATCH_REQUIRED_HEADERS = [
    "批次ID",
    "状态",
    "页面数",
    "元素总数",
    "已覆盖元素数",
    "待确认元素数",
    "功能用例数",
    "性能场景数",
    "异常用例数",
    "边界用例数",
    "权限/状态用例数",
    "数据一致性用例数",
    "页面遍历完成",
    "功能用例完成",
    "性能设计完成",
    "异常边界权限覆盖完成",
    "页面元素覆盖完成",
    "产品版图已更新",
    "覆盖质量自检",
    "导入文件路径",
    "导入文件已生成",
    "最小标题路径",
]

BATCH_EXPECTED_HEADERS = [
    "批次ID",
    "一级模块",
    "二级菜单",
    "三级菜单/页面域",
    "批次范围",
    "状态",
    "页面数",
    "元素总数",
    "已覆盖元素数",
    "待确认元素数",
    "功能用例数",
    "性能场景数",
    "异常用例数",
    "边界用例数",
    "权限/状态用例数",
    "数据一致性用例数",
    "页面遍历完成",
    "功能用例完成",
    "性能设计完成",
    "异常边界权限覆盖完成",
    "页面元素覆盖完成",
    "产品版图已更新",
    "覆盖质量自检",
    "未覆盖元素清单路径",
    "归档路径",
    "导入文件路径",
    "导入文件已生成",
    "最小标题路径",
    "待确认问题",
    "下一步动作",
]

MULTI_LEAF_SEPARATORS = ["、", "，", ",", "；", ";", "／", "/"]

BATCH_NUMBER_FIELDS = [
    "页面数",
    "元素总数",
    "已覆盖元素数",
    "待确认元素数",
    "功能用例数",
    "性能场景数",
    "异常用例数",
    "边界用例数",
    "权限/状态用例数",
    "数据一致性用例数",
]

BATCH_PASS_BOOLEAN_FIELDS = [
    "页面遍历完成",
    "功能用例完成",
    "性能设计完成",
    "异常边界权限覆盖完成",
    "页面元素覆盖完成",
    "产品版图已更新",
    "导入文件已生成",
]

IMPORT_HEADERS = [
    "一级模块系统编号",
    "一级模块名称",
    "二级模块系统编号",
    "二级模块名称",
    "三级模块系统编号",
    "三级模块名称",
    "四级模块系统编号",
    "四级模块名称",
    "五级模块系统编号",
    "五级模块名称",
    "其他模块系统编号",
    "其他模块名称",
    "测试用例系统编号",
    "测试用例序号",
    "测试用例名称",
    "测试步骤描述",
    "测试步骤预期结果",
    "测试类型",
    "测试用例级别",
    "执行方式",
    "测试用例说明",
    "前置条件",
    "维护人",
    "标签",
    "备注",
    "作者",
]

IMPORT_ALLOWED_VALUES = {
    "测试类型": {"功能测试", "性能规格测试", "可靠性测试", "兼容性测试", "可维护性测试", "安全性测试", "易用性测试"},
    "测试用例级别": {"L1", "L2", "L3", "L4"},
    "执行方式": {"自动化", "手动"},
}

DFX_SCENARIOS = {
    "DFT功能": {"正向流程", "边界值", "异常输入", "逆向操作"},
    "DFP性能": {"响应时间", "并发处理", "大数据量", "资源监控"},
    "DFI接口": {"参数校验", "协议兼容", "错误码", "超时重试"},
    "DFC兼容": {"浏览器", "操作系统", "屏幕适配", "数据格式"},
    "DFS安全": {"身份认证", "权限控制", "数据脱敏", "注入防护"},
    "DFR可靠": {"故障恢复", "数据一致", "幂等性", "断点续传"},
    "DFM维护": {"配置热更新", "灰度发布", "回滚机制", "日志追踪"},
    "DFU可用": {"操作便捷", "错误提示", "用户引导", "操作反馈"},
    "DFD部署": {"全新安装", "版本升级", "卸载回滚", "配置迁移"},
    "DFO运维": {"监控指标", "告警配置", "故障自愈", "容量规划"},
    "DFB业务": {"业务流程", "数据准确", "端到端", "报表统计"},
    "DFX极端": {"压力极限", "破坏性", "资源耗尽", "并发极限"},
}

DEPRECATED_SCENARIO_HEADERS = {"场景类型", "正向/反向"}
FUNCTION_SHEET_FORBIDDEN_TEST_TYPES = {"性能规格测试"}
FUNCTION_SHEET_FORBIDDEN_DFX_DIMENSIONS = {"DFP性能"}
FUNCTION_SHEET_FORBIDDEN_DFX_PAIRS = {
    ("DFX极端", "压力极限"),
    ("DFX极端", "资源耗尽"),
    ("DFX极端", "并发极限"),
}
MUTATING_TEST_DATA_MARKERS = ["AI_TEST", "CODEX_TEST", "本次创建", "本次新增", "用户提供测试数据", "测试标识"]
SAFE_EXISTING_DATA_MARKERS = ["不保存", "不提交", "不确认", "取消", "关闭", "数据不变", "状态不变"]
MUTATION_COMMIT_MARKERS = [
    "保存成功",
    "提交成功",
    "新增成功",
    "创建成功",
    "添加成功",
    "确认删除",
    "删除成功",
    "编辑成功",
    "修改成功",
    "配置成功",
    "启用成功",
    "停用成功",
    "发布成功",
    "下线成功",
    "审批成功",
    "重置成功",
    "撤销成功",
    "归档成功",
    "清空成功",
    "解绑成功",
    "列表刷新",
    "落库",
    "状态变更",
    "状态流转",
    "生效",
]
NON_MUTATING_BLOCK_MARKERS = ["禁用", "不可点击", "置灰", "校验失败", "校验提示", "未保存", "未提交", "不触发保存", "阻止保存"]
MIN_FUNCTION_CASES_PER_GENERATED_ELEMENT = 0.7

IMPORT_REQUIRED_FIELDS = ["一级模块名称", "二级模块名称", "三级模块名称", "测试用例名称", "测试类型", "测试用例级别", "执行方式"]
IMPORT_AUTO_FIELDS = [
    "一级模块系统编号", "二级模块系统编号", "三级模块系统编号", "四级模块系统编号", "五级模块系统编号",
    "其他模块系统编号", "其他模块名称", "测试用例系统编号", "维护人", "作者",
]
IMPORT_MULTILINE_FIELDS = ["测试步骤描述", "测试步骤预期结果", "前置条件", "测试用例说明", "备注"]

FORMAL_MULTILINE_FIELDS = {
    "功能测试用例": ["前置条件", "测试数据", "操作步骤", "预期结果", "备注"],
    "性能测试设计": ["前置条件/数据准备", "执行步骤", "监控指标", "通过标准", "风险备注"],
    "风险与待确认问题": ["描述", "影响范围", "建议处理方式"],
    "自动化建议": ["建议说明", "前置条件", "维护要求"],
    "页面元素覆盖清单": ["业务依据/规则来源", "待确认问题/备注"],
}

RESIDUAL_MARKERS = ["{NAV}", "{NL}", "{Q}", "{E}", "${", "{{", "TODO", "TBD"]

PAGE_DISCOVERY_REQUIRED_HEADERS = [
    "批次ID",
    "最小标题路径",
    "页面/入口",
    "角色/权限",
    "数据状态",
    "交互实例ID",
    "菜单路径/URL",
    "元素名称/文案",
    "元素类型",
    "交互方式",
    "适用DFX维度",
    "适用DFX场景",
    "选项取值/输入值",
    "联动/依赖变化",
    "结果分支/后续状态",
    "完整点击路径",
    "操作步骤锚点",
    "预期结果锚点",
    "是否已生成用例",
    "关联用例ID",
    "覆盖状态",
    "证据路径",
    "证据定位",
]

PAGE_ELEMENT_INVENTORY_EXPECTED_HEADERS = [
    "批次ID", "最小标题路径", "页面/入口", "角色/权限", "数据状态", "交互实例ID", "采集快照ID", "元素指纹", "元素名称/文案", "元素类型",
    "交互方式", "可交互状态", "DOM/可访问性定位", "发现来源", "证据路径", "证据定位", "备注",
]

SELECTION_OPTION_OBSERVATIONS_EXPECTED_HEADERS = [
    "批次ID", "最小标题路径", "交互实例ID", "页面/入口", "元素名称/文案", "元素类型", "选项值",
    "选项序号", "可用选项总数", "选项集合类型", "是否实际选择", "选择前状态", "选择后页面变化",
    "联动/依赖变化", "结果分支/后续状态", "预期结果锚点", "恢复/清空结果", "覆盖策略", "证据路径",
    "证据定位", "阻塞原因", "关联用例ID", "备注",
]

INTERACTION_BRANCH_OBSERVATIONS_EXPECTED_HEADERS = [
    "批次ID", "最小标题路径", "交互实例ID", "页面/入口", "元素名称/文案", "元素类型", "分支类别", "分支动作",
    "执行前状态", "执行动作", "执行后结果", "恢复结果", "操作步骤锚点", "预期结果锚点", "是否实际执行", "阻塞原因", "证据路径", "证据定位", "关联用例ID", "备注",
]

PAGE_DISCOVERY_EXPECTED_HEADERS = [
    "批次ID",
    "一级模块",
    "二级菜单",
    "三级菜单/页面域",
    "最小标题路径",
    "页面/入口",
    "菜单路径/URL",
    "发现方式",
    "角色/权限",
    "数据状态",
    "交互实例ID",
    "元素名称/文案",
    "元素类型",
    "交互方式",
    "适用DFX维度",
    "适用DFX场景",
    "选项取值/输入值",
    "联动/依赖变化",
    "结果分支/后续状态",
    "完整点击路径",
    "预期/观察行为",
    "操作步骤锚点",
    "预期结果锚点",
    "业务依据/规则来源",
    "测试数据来源",
    "是否已生成用例",
    "关联用例ID",
    "覆盖状态",
    "未覆盖/待确认原因",
    "证据路径",
    "证据定位",
    "备注",
]

ELEMENT_CASE_PLAN_EXPECTED_HEADERS = [
    "批次ID",
    "最小标题路径",
    "交互实例ID",
    "页面/入口",
    "功能点",
    "元素名称/文案",
    "元素类型",
    "交互方式",
    "业务路径",
    "数据状态",
    "适用DFX维度",
    "适用DFX场景",
    "测试设计方向",
    "应生成用例数",
    "计划用例ID",
    "实际用例ID",
    "操作类别",
    "验证要求",
    "数据策略",
    "执行状态",
    "是否必须真实执行",
    "是否涉及配置生效",
    "是否涉及CRUD闭环",
    "未生成原因",
    "备注",
]

TEST_DATA_LIFECYCLE_EXPECTED_HEADERS = [
    "批次ID",
    "最小标题路径",
    "交互实例ID",
    "关联页面/入口",
    "修改项/元素",
    "测试数据ID/名称",
    "数据类型",
    "创建入口",
    "创建步骤关联用例",
    "创建结果",
    "查看结果",
    "编辑前值",
    "编辑后值",
    "编辑结果",
    "保存后回显",
    "实际生效结果",
    "配置生效验证点",
    "删除取消结果",
    "删除确认结果",
    "清理状态",
    "保留原因",
    "备注",
]

PRODUCT_MAP_PAGE_ELEMENT_HEADERS = [
    "产品/系统",
    "模块",
    "页面/入口",
    "菜单路径/URL",
    "元素名称/文案",
    "元素类型",
    "交互方式",
    "关联用例ID",
    "覆盖状态",
    "发现来源",
]

PRODUCT_MAP_CASE_INDEX_HEADERS = [
    "产品/系统",
    "模块",
    "功能点",
    "用例ID",
    "用例标题",
    "归档测试设计路径",
]

PRODUCT_MAP_CHANGE_HEADERS = [
    "版本",
    "日期",
    "变更人/来源",
    "变更类型",
    "影响模块",
    "变更内容",
    "是否已同步产品版图",
]

PRODUCT_MAP_REQUIRED_REAL_SHEETS = [
    "产品模块地图",
    "业务对象地图",
    "业务链路地图",
    "页面元素地图",
    "用例资产索引",
    "模块能力索引",
    "跨模块依赖关系",
    "可复用测试数据",
    "变更影响分析",
    "变更记录",
]

def fail(message: str) -> None:
    raise AssertionError(message)


TRANSIENT_STEP_MARKERS = [
    "modal",
    "dialog",
    "drawer",
    "dropdown",
    "select",
    "confirm",
    "edit",
    "delete",
    "add variable",
    "input",
    "弹窗",
    "对话框",
    "抽屉",
    "下拉",
    "选择",
    "确认框",
    "编辑",
    "删除",
    "添加变量",
    "输入",
    "尝试点击",
    "观察",
]

TERMINAL_STEP_MARKERS = [
    "click OK",
    "click Cancel",
    "close",
    "return",
    "back to list",
    "save",
    "submit",
    "not save",
    "no data changed",
    "点击确定",
    "点击「确定」",
    "点击取消",
    "点击「取消」",
    "点击关闭",
    "点击「关闭」",
    "返回",
    "回到列表",
    "返回列表",
    "保存",
    "提交",
    "确认",
    "不保存",
    "关闭弹窗",
    "弹窗关闭",
    "列表不变",
    "数据不变",
    "退出编辑",
]


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(t.text or "" for t in si.findall(".//x:t", NS)) for si in root.findall("x:si", NS)]


def cell_text(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//x:t", NS)).strip()
    value = cell.find("x:v", NS)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        return shared[int(value.text)].strip()
    return value.text.strip()


def cell_style_id(cell: ET.Element) -> int:
    raw = cell.attrib.get("s", "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref)
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def column_number(cell_ref: str) -> int:
    return column_index(cell_ref) + 1


def parse_a1_cell(cell_ref: str) -> tuple[int, int]:
    cleaned = cell_ref.replace("$", "")
    match = re.match(r"([A-Z]+)(\d+)$", cleaned)
    if not match:
        return 0, 0
    return column_number(match.group(1)), int(match.group(2))


def parse_a1_range(range_text: str) -> tuple[int, int, int, int]:
    cleaned = range_text.replace("$", "")
    if ":" in cleaned:
        start, end = cleaned.split(":", 1)
    else:
        start = end = cleaned
    min_col, min_row = parse_a1_cell(start)
    max_col, max_row = parse_a1_cell(end)
    return min_col, min_row, max_col, max_row


def workbook_sheet_paths(zf: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("rel:Relationship", NS)
        if rel.attrib.get("Type", "").endswith("/worksheet")
    }
    paths: dict[str, str] = {}
    for sheet in workbook.findall("x:sheets/x:sheet", NS):
        name = sheet.attrib["name"]
        rel_id = sheet.attrib[f"{{{NS['r']}}}id"]
        target = rel_targets[rel_id]
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        paths[name] = path
    return paths


def relationship_target(base_path: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_path), target))


def cell_reference_to_position(cell_ref: str) -> tuple[int, int]:
    match = re.match(r"^([A-Z]+)(\d+)$", cell_ref)
    if not match:
        return (0, 0)
    col = 0
    for char in match.group(1):
        col = col * 26 + ord(char) - ord("A") + 1
    return (int(match.group(2)), col)


def range_bounds(ref: str) -> tuple[int, int, int, int]:
    cells = ref.split(":")
    if len(cells) == 1:
        start = end = cells[0]
    else:
        start, end = cells[0], cells[-1]
    start_row, start_col = cell_reference_to_position(start)
    end_row, end_col = cell_reference_to_position(end)
    return (start_row, start_col, end_row, end_col)


def range_covers(actual_ref: str, expected_ref: str) -> bool:
    actual_start_row, actual_start_col, actual_end_row, actual_end_col = range_bounds(actual_ref)
    expected_start_row, expected_start_col, expected_end_row, expected_end_col = range_bounds(expected_ref)
    return (
        actual_start_row <= expected_start_row
        and actual_start_col <= expected_start_col
        and actual_end_row >= expected_end_row
        and actual_end_col >= expected_end_col
    )


def assert_no_unmasked_value(value: str, label: str) -> None:
    try:
        shared_assert_no_unmasked_value(value, label)
    except SensitiveDataError as exc:
        fail(str(exc))


def assert_no_sensitive_text_values(path: Path, label: str) -> None:
    if not path.exists():
        return
    try:
        shared_assert_no_sensitive_text_file(path, label)
    except SensitiveDataError as exc:
        fail(str(exc))


def validate_table_ranges(path: Path, sheet_names: list[str] | None = None) -> None:
    with zipfile.ZipFile(path) as zf:
        table_files = [name for name in zf.namelist() if name.startswith("xl/tables/")]
        if table_files:
            fail(
                f"{path} must not contain Excel Table parts: {', '.join(table_files)}. "
                "Use normal cell ranges, styles, and auto filters instead; otherwise Excel may repair "
                "or delete /xl/tables/table*.xml when opening the workbook."
            )
        available_sheets = workbook_sheet_paths(zf)
        target_sheets = sheet_names or list(available_sheets)
        for sheet_name in target_sheets:
            sheet_path = available_sheets.get(sheet_name)
            if not sheet_path:
                continue
            root = ET.fromstring(zf.read(sheet_path))
            table_parts = root.findall("x:tableParts/x:tablePart", NS)
            if table_parts:
                fail(
                    f"{sheet_name} must not contain worksheet tableParts. "
                    "Remove Excel Table objects and keep only normal ranges/styles/auto filters."
                )


@lru_cache(maxsize=None)
def sheet_rows(path: Path, sheet_name: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as zf:
        paths = workbook_sheet_paths(zf)
        if sheet_name not in paths:
            fail(f"Workbook is missing sheet: {sheet_name}")
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read(paths[sheet_name]))
    rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        values: list[str] = []
        for cell in row.findall("x:c", NS):
            index = column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append("")
            values[index] = cell_text(cell, shared)
        rows.append(values)
    return rows


@lru_cache(maxsize=None)
def sheet_cell_rows(path: Path, sheet_name: str) -> list[list[tuple[str, str, int]]]:
    with zipfile.ZipFile(path) as zf:
        paths = workbook_sheet_paths(zf)
        if sheet_name not in paths:
            fail(f"Workbook is missing sheet: {sheet_name}")
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read(paths[sheet_name]))
    rows: list[list[tuple[str, str, int]]] = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        values: list[tuple[str, str, int]] = []
        for cell in row.findall("x:c", NS):
            index = column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append(("", "", 0))
            values[index] = (
                cell.attrib.get("r", ""),
                cell_text(cell, shared),
                cell_style_id(cell),
            )
        rows.append(values)
    return rows


@lru_cache(maxsize=None)
def wrapped_style_ids(path: Path) -> set[int]:
    with zipfile.ZipFile(path) as zf:
        try:
            root = ET.fromstring(zf.read("xl/styles.xml"))
        except KeyError:
            return set()
    wrapped: set[int] = set()
    cell_xfs = root.find("x:cellXfs", NS)
    if cell_xfs is None:
        return wrapped
    for index, xf in enumerate(cell_xfs.findall("x:xf", NS)):
        alignment = xf.find("x:alignment", NS)
        if alignment is not None and alignment.attrib.get("wrapText") in {"1", "true", "True"}:
            wrapped.add(index)
    return wrapped


def assert_multiline_cells_wrapped(path: Path, sheet_name: str, field_names: list[str]) -> None:
    rows = sheet_cell_rows(path, sheet_name)
    if not rows:
        return
    headers = [cell[1] for cell in rows[0]]
    header_index = {header: index for index, header in enumerate(headers) if header}
    target_indexes = [header_index[field] for field in field_names if field in header_index]
    if not target_indexes:
        return
    wrapped = wrapped_style_ids(path)
    for row_number, row in enumerate(rows[1:], start=2):
        for index in target_indexes:
            if index >= len(row):
                continue
            ref, value, style_id = row[index]
            if "\n" in value and style_id not in wrapped:
                field = headers[index]
                cell_ref = ref or f"{field} row {row_number}"
                fail(f"{sheet_name} {cell_ref} contains multiline text but wrapText is not enabled for field {field}")


def assert_data_rows_follow_sample_styles(path: Path, sheet_names: list[str] | None = None) -> None:
    with zipfile.ZipFile(path) as zf:
        available_sheets = workbook_sheet_paths(zf)
    target_sheets = sheet_names or list(available_sheets)
    for sheet_name in target_sheets:
        rows = sheet_cell_rows(path, sheet_name)
        if len(rows) <= 2:
            continue
        sample_styles = {index: cell[2] for index, cell in enumerate(rows[1])}
        for row_number, row in enumerate(rows[2:], start=3):
            if not any(value for _, value, _ in row):
                continue
            for index, (_, _, style_id) in enumerate(row):
                expected = sample_styles.get(index)
                if expected is None:
                    continue
                if style_id != expected:
                    fail(
                        f"{sheet_name} row {row_number} column {index + 1} style must match template sample row 2. "
                        "Only cell content should change; borders, fills, fonts, number formats, and alignment must be preserved."
                    )


def range_covers_column_row(range_text: str, column: int, row: int) -> bool:
    for part in range_text.split():
        min_col, min_row, max_col, max_row = parse_a1_range(part)
        if min_col <= column <= max_col and min_row <= row <= max_row:
            return True
    return False


def assert_dropdown_validations_cover_rows(path: Path, sheet_name: str, field_names: list[str], last_row: int) -> None:
    if last_row < 2:
        return
    rows = sheet_cell_rows(path, sheet_name)
    if not rows:
        return
    headers = [cell[1] for cell in rows[0]]
    header_index = {header: index + 1 for index, header in enumerate(headers) if header}
    target_columns = {field: header_index[field] for field in field_names if field in header_index}
    if not target_columns:
        return
    with zipfile.ZipFile(path) as zf:
        sheet_paths = workbook_sheet_paths(zf)
        root = ET.fromstring(zf.read(sheet_paths[sheet_name]))
    validations = root.findall(".//x:dataValidations/x:dataValidation", NS)
    for field, column in target_columns.items():
        if not any(
            validation.attrib.get("type") == "list"
            and range_covers_column_row(validation.attrib.get("sqref", ""), column, last_row)
            for validation in validations
        ):
            fail(f"{sheet_name} field {field} dropdown validation must cover row {last_row}")


def assert_no_residual_markers(path: Path, sheet_names: list[str] | None = None) -> None:
    with zipfile.ZipFile(path) as zf:
        available_sheets = workbook_sheet_paths(zf)
    target_sheets = sheet_names or list(available_sheets)
    for sheet_name in target_sheets:
        rows = sheet_rows(path, sheet_name)
        for row_number, row in enumerate(rows, start=1):
            for column_number, value in enumerate(row, start=1):
                if not value:
                    continue
                for marker in RESIDUAL_MARKERS:
                    if marker in value:
                        fail(f"{sheet_name} row {row_number} column {column_number} contains unresolved template marker: {marker}")


def assert_no_sensitive_values(path: Path, sheet_names: list[str] | None = None) -> None:
    with zipfile.ZipFile(path) as zf:
        available_sheets = workbook_sheet_paths(zf)
    target_sheets = sheet_names or list(available_sheets)
    for sheet_name in target_sheets:
        rows = sheet_rows(path, sheet_name)
        for row_number, row in enumerate(rows, start=1):
            for column_number, value in enumerate(row, start=1):
                if not value:
                    continue
                assert_no_unmasked_value(value, f"{sheet_name} row {row_number} column {column_number}")


def validate_formal_workbook_styles(workbook: Path) -> None:
    assert_data_rows_follow_sample_styles(workbook, EXPECTED_SHEETS)
    for sheet_name, fields in FORMAL_MULTILINE_FIELDS.items():
        assert_multiline_cells_wrapped(workbook, sheet_name, fields)


def row_dicts(rows: list[list[str]], sheet_name: str) -> list[dict[str, str]]:
    if not rows:
        fail(f"{sheet_name} has no header row")
    headers = rows[0]
    result: list[dict[str, str]] = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        result.append({header: row[i].strip() if i < len(row) else "" for i, header in enumerate(headers) if header})
    return result


def assert_numbered(text: str, label: str) -> None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        fail(f"{label} must not be empty")
    for line in lines:
        if not re.match(r"^\d+\.\s*\S+", line):
            fail(f"{label} must use numbered lines like '1. ...': {line}")


def assert_expected_does_not_repeat_steps(steps: str, expected: str, label: str) -> None:
    normalize = lambda text: [
        re.sub(r"^\d+\.\s*", "", line.strip())
        for line in text.splitlines()
        if line.strip()
    ]
    step_lines = normalize(steps)
    expected_lines = normalize(expected)
    if step_lines[:3] and step_lines[:3] == expected_lines[:3]:
        fail(
            f"{label} repeats navigation/actions from 操作步骤; "
            "write observable page, message, data, and state outcomes"
        )


def assert_complete_operation_steps(text: str, label: str) -> None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        fail(f"{label} must include full navigation and operation steps, not a single short sentence")
    first_steps = "\n".join(lines[:3])
    entry_markers = ["登录", "打开系统", "访问系统", "进入系统", "打开平台", "访问平台", "进入平台", "URL"]
    navigation_markers = ["一级", "二级", "三级", "菜单", "模块", "导航", "路径", ">", "页面"]
    if not any(marker in first_steps for marker in entry_markers):
        fail(f"{label} must start from system/project entry and include navigation path to target function")
    if not any(marker in first_steps for marker in navigation_markers):
        fail(f"{label} must include complete menu/module navigation before operating target controls")
    if re.match(r"^1\.\s*在[^，,。]*页面", lines[0]):
        fail(f"{label} must not assume the tester is already on the target module page")


def assert_transient_flow_closed(steps: str, expected: str, label: str) -> None:
    normalized_steps = re.sub(r"\s+", "", steps or "").lower()
    combined = re.sub(r"\s+", "", f"{steps}\n{expected}").lower()
    if not normalized_steps:
        return
    has_transient_action = any(marker.lower() in normalized_steps for marker in TRANSIENT_STEP_MARKERS)
    if not has_transient_action:
        return
    has_terminal_action = any(marker.lower() in combined for marker in TERMINAL_STEP_MARKERS)
    if not has_terminal_action:
        fail(
            f"{label} opens or changes a transient UI state but does not describe a confirm/cancel/close/return/recovery path"
        )


def parse_ids(text: str) -> set[str]:
    return {item.strip() for item in re.split(r"[,，;；\s]+", text) if item.strip()}


def parse_id_sequence(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，;；、/\s]+", text or "") if item.strip()]


def split_dfx_values(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，;；、/\\\s]+", text or "") if item.strip()]


def assert_dfx_mapping(dimensions_text: str, scenarios_text: str, label: str) -> tuple[set[str], set[str]]:
    dimensions = set(split_dfx_values(dimensions_text))
    scenarios = set(split_dfx_values(scenarios_text))
    if not dimensions:
        fail(f"{label} must fill DFX维度")
    if not scenarios:
        fail(f"{label} must fill DFX场景")
    invalid_dimensions = sorted(dimensions - set(DFX_SCENARIOS))
    if invalid_dimensions:
        fail(f"{label} has invalid DFX维度: {invalid_dimensions}")
    allowed_scenarios: set[str] = set()
    for dimension in dimensions:
        allowed_scenarios.update(DFX_SCENARIOS[dimension])
    invalid_scenarios = sorted(scenarios - allowed_scenarios)
    if invalid_scenarios:
        fail(f"{label} has DFX场景 not allowed by selected DFX维度: {invalid_scenarios}")
    return dimensions, scenarios


def dfx_pairs(dimensions_text: str, scenarios_text: str) -> set[tuple[str, str]]:
    dimensions = split_dfx_values(dimensions_text)
    scenarios = split_dfx_values(scenarios_text)
    if len(dimensions) == len(scenarios):
        return {
            (dimension, scenario)
            for dimension, scenario in zip(dimensions, scenarios)
            if dimension in DFX_SCENARIOS and scenario in DFX_SCENARIOS[dimension]
        }
    return {
        (dimension, scenario)
        for dimension in dimensions
        for scenario in scenarios
        if dimension in DFX_SCENARIOS and scenario in DFX_SCENARIOS[dimension]
    }


def assert_function_sheet_dfx_scope(
    test_type: str,
    dimensions: set[str],
    pairs: set[tuple[str, str]],
    label: str,
) -> None:
    if test_type in FUNCTION_SHEET_FORBIDDEN_TEST_TYPES:
        fail(f"{label} must not use 测试类型={test_type}; move performance scenarios to 性能测试设计")
    forbidden_dimensions = sorted(dimensions & FUNCTION_SHEET_FORBIDDEN_DFX_DIMENSIONS)
    if forbidden_dimensions:
        fail(f"{label} must not use {forbidden_dimensions} in 功能测试用例; move them to 性能测试设计")
    forbidden_pairs = sorted(pairs & FUNCTION_SHEET_FORBIDDEN_DFX_PAIRS)
    if forbidden_pairs:
        fail(f"{label} contains extreme performance-style DFX pairs in 功能测试用例: {forbidden_pairs}")


def contains_any_marker(text: str, markers: list[str]) -> bool:
    return any(marker in (text or "") for marker in markers)


def assert_mutation_case_evidence(row: dict[str, str], label: str) -> None:
    combined = "\n".join(
        [
            row.get("功能点", ""),
            row.get("用例标题", ""),
            row.get("测试数据", ""),
            row.get("操作步骤", ""),
            row.get("预期结果", ""),
            row.get("备注", ""),
        ]
    )
    if not any(
        marker in combined
        for marker in ["新增", "创建", "添加", "新建", "保存", "提交", "编辑", "修改", "删除", "移除", "清空", "解绑", "配置", "启用", "停用", "发布", "下线", "审批", "重置", "撤销", "归档", "状态变更"]
    ):
        return
    commits_change = contains_any_marker(combined, MUTATION_COMMIT_MARKERS)
    if not commits_change and contains_any_marker(combined, NON_MUTATING_BLOCK_MARKERS + SAFE_EXISTING_DATA_MARKERS):
        return
    if any(marker in combined for marker in ["已有数据", "既有数据"]):
        if not contains_any_marker(combined, SAFE_EXISTING_DATA_MARKERS):
            fail(f"{label} touches existing data but does not clearly close with cancel/close/no-save/no-change")
        if any(marker in combined for marker in ["确认删除", "保存修改", "提交修改", "最终确认"]):
            fail(f"{label} must not finally modify or delete existing data")
        return
    if commits_change and not contains_any_marker(combined, MUTATING_TEST_DATA_MARKERS):
        fail(f"{label} mutating case must bind to current test data such as AI_TEST/CODEX_TEST or 用户提供测试数据")
    if any(marker in combined for marker in ["新增", "创建", "添加", "新建", "保存", "提交"]):
        if not any(marker in combined for marker in ["列表", "详情", "下一级", "刷新", "成功", "失败", "校验"]):
            fail(f"{label} create/save flow must verify list/detail/next-page/success/failure state")
    if any(marker in combined for marker in ["编辑", "修改"]):
        if not any(marker in combined for marker in ["编辑前", "编辑后", "变更", "回显", "列表", "详情", "数据不变"]):
            fail(f"{label} edit flow must verify before/after value, echo, list/detail, or unchanged state")
    if "删除" in combined:
        if not any(marker in combined for marker in ["删除取消", "取消删除", "确认删除", "列表不再展示", "搜索不到", "数据不变"]):
            fail(f"{label} delete flow must include cancel/confirm and post-delete or unchanged verification")


def assert_no_deprecated_scenario_headers(rows: list[list[str]], sheet_name: str) -> None:
    if not rows:
        return
    headers = set(rows[0])
    deprecated = sorted(headers & DEPRECATED_SCENARIO_HEADERS)
    if deprecated:
        fail(f"{sheet_name} must use DFX维度/DFX场景 instead of deprecated headers: {deprecated}")


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip().lower()


def normalized_key(*values: str) -> tuple[str, ...]:
    return tuple(normalize(value) for value in values)


def resolved_run_evidence_file(run_dir: Path, value: str) -> Path | None:
    raw = (value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    candidates = [path] if path.is_absolute() else [run_dir / path, run_dir.parents[3] / path]
    allowed_root = (run_dir / "artifacts").resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(allowed_root)
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved.stat().st_size > 0:
            return resolved
    return None


def run_evidence_fingerprint(run_dir: Path, value: str) -> str | None:
    path = resolved_run_evidence_file(run_dir, value)
    if path is None:
        return None
    digest = hashlib.sha256()
    prefix = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if not prefix:
                prefix = chunk[:32]
            digest.update(chunk)
    lowered = prefix.lstrip().lower()
    is_image = bool(
        re.search(r"\.(?:png|jpe?g|gif|bmp|webp|svg|tiff?)$", path.name, re.IGNORECASE)
        or prefix.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*"))
        or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
        or lowered.startswith((b"<svg", b"<?xml")) and b"<svg" in lowered
    )
    return f"{'image' if is_image else 'file'}:{digest.hexdigest()}"


def csv_row_dicts(path: Path, required: list[str], label: str) -> list[dict[str, str]]:
    if not path.exists():
        fail(f"{label} not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        headers = reader.fieldnames or []
        missing = [header for header in required if header not in headers]
        if missing:
            fail(f"{label} is missing headers: {missing}")
        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader, start=2):
            if None in row:
                fail(f"{label} row {index} has more columns than the header; do not append summary rows or shifted CSV data")
            missing_columns = [key for key, value in row.items() if value is None]
            if missing_columns:
                fail(f"{label} row {index} has fewer columns than the header; missing values for: {missing_columns}")
            cleaned = {key: (value or "").strip() for key, value in row.items()}
            if any(value for value in cleaned.values()):
                rows.append(cleaned)
        return rows


def csv_rows_with_exact_header(path: Path, expected: list[str], label: str) -> list[dict[str, str]]:
    if not path.exists():
        fail(f"{label} not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        try:
            headers = next(reader)
        except StopIteration:
            fail(f"{label} has no header row")
        if headers != expected:
            fail(f"{label} header must match the standard template exactly. Expected {expected}, got {headers}")
        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != len(headers):
                fail(f"{label} row {index} column count mismatch: expected {len(headers)}, got {len(row)}")
            rows.append({header: row[col].strip() for col, header in enumerate(headers)})
        return rows


def assert_no_sensitive_csv_values(rows: list[dict[str, str]], label: str) -> None:
    try:
        shared_assert_no_sensitive_csv_rows(rows, label)
    except SensitiveDataError as exc:
        fail(str(exc))


def require_headers(rows: list[list[str]], required: list[str], sheet_name: str) -> None:
    headers = set(rows[0] if rows else [])
    missing = [header for header in required if header not in headers]
    if missing:
        fail(f"{sheet_name} is missing headers: {missing}")


@lru_cache(maxsize=None)
def first_sheet_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as zf:
        paths = workbook_sheet_paths(zf)
        if not paths:
            fail(f"Workbook has no sheets: {path}")
        first_sheet = next(iter(paths))
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read(paths[first_sheet]))
    rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        values: list[str] = []
        for cell in row.findall("x:c", NS):
            index = column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append("")
            values[index] = cell_text(cell, shared)
        rows.append(values)
    return rows


@lru_cache(maxsize=None)
def first_worksheet_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        paths = workbook_sheet_paths(zf)
        if not paths:
            fail(f"Workbook has no sheets: {path}")
        first_sheet = next(iter(paths))
        return zf.read(paths[first_sheet]).decode("utf-8", errors="ignore")


def validate_workbook(workbook: Path) -> dict[str, object]:
    if not workbook.exists():
        fail(f"Workbook not found: {workbook}")
    with zipfile.ZipFile(workbook) as zf:
        sheet_names = list(workbook_sheet_paths(zf))
    if sheet_names != EXPECTED_SHEETS:
        fail(f"Workbook sheets mismatch. Expected {EXPECTED_SHEETS}, got {sheet_names}")
    assert_no_residual_markers(workbook, EXPECTED_SHEETS)
    assert_no_sensitive_values(workbook, EXPECTED_SHEETS)
    validate_table_ranges(workbook, EXPECTED_SHEETS)
    validate_formal_workbook_styles(workbook)
    formal_template_markers = ["TC-LOGIN-001", "STORY-001", "SCN-LOGIN-001", "PT-LOGIN-001", "EL-LOGIN-001", "示例项目"]
    for sheet_name in EXPECTED_SHEETS:
        rows = sheet_rows(workbook, sheet_name)
        for row_index, row in enumerate(rows[1:], start=2):
            combined = "\n".join(str(value or "") for value in row)
            marker = next((item for item in formal_template_markers if item in combined), None)
            if marker:
                fail(
                    f"{sheet_name} row {row_index} still contains formal-template example marker {marker}; "
                    "assemble the workbook from current batch data before delivery"
                )

    scenario_rows_raw = sheet_rows(workbook, "测试场景矩阵")
    assert_no_deprecated_scenario_headers(scenario_rows_raw, "测试场景矩阵")
    require_headers(
        scenario_rows_raw,
        ["场景 ID", "功能点", "测试维度", "DFX维度", "DFX场景", "是否生成用例"],
        "测试场景矩阵",
    )
    scenario_rows = row_dicts(scenario_rows_raw, "测试场景矩阵")
    if not scenario_rows:
        fail("测试场景矩阵 must contain at least one DFX-driven scenario")
    generated_scenario_dfx: set[tuple[str, str]] = set()
    for index, row in enumerate(scenario_rows, start=2):
        dimensions, scenarios = assert_dfx_mapping(
            row.get("DFX维度", ""),
            row.get("DFX场景", ""),
            f"测试场景矩阵 row {index}",
        )
        if row.get("是否生成用例", "") == "是":
            generated_scenario_dfx.update(dfx_pairs(row.get("DFX维度", ""), row.get("DFX场景", "")))

    function_rows_raw = sheet_rows(workbook, "功能测试用例")
    require_headers(function_rows_raw, ["用例 ID", "功能点", "用例标题", "测试类型", "DFX维度", "DFX场景", "操作步骤", "预期结果"], "功能测试用例")
    function_rows = row_dicts(function_rows_raw, "功能测试用例")
    if not function_rows:
        fail("功能测试用例 must contain at least one case")

    case_ids: set[str] = set()
    case_titles: dict[str, str] = {}
    case_function_points: dict[str, str] = {}
    function_dfx: set[tuple[str, str]] = set()
    for index, row in enumerate(function_rows, start=2):
        case_id = row.get("用例 ID", "")
        function_point = row.get("功能点", "")
        title = row.get("用例标题", "")
        if not case_id:
            fail(f"功能测试用例 row {index} is missing 用例 ID")
        if case_id in case_ids:
            fail(f"Duplicate 用例 ID: {case_id}")
        case_ids.add(case_id)
        case_titles[case_id] = title
        case_function_points[case_id] = function_point
        if not function_point:
            fail(f"功能测试用例 row {index} is missing 功能点")
        if not title.startswith(f"{function_point}-"):
            fail(f"功能测试用例 row {index} title must start with 功能点-: {title}")
        dimensions, scenarios = assert_dfx_mapping(
            row.get("DFX维度", ""),
            row.get("DFX场景", ""),
            f"功能测试用例 row {index}",
        )
        pairs = dfx_pairs(row.get("DFX维度", ""), row.get("DFX场景", ""))
        assert_function_sheet_dfx_scope(row.get("测试类型", ""), dimensions, pairs, f"功能测试用例 row {index}")
        function_dfx.update(pairs)
        assert_numbered(row.get("操作步骤", ""), f"功能测试用例 row {index} 操作步骤")
        assert_complete_operation_steps(row.get("操作步骤", ""), f"功能测试用例 row {index} 操作步骤")
        assert_numbered(row.get("预期结果", ""), f"功能测试用例 row {index} 预期结果")
        assert_expected_does_not_repeat_steps(
            row.get("操作步骤", ""),
            row.get("预期结果", ""),
            f"功能测试用例 row {index} 预期结果",
        )
        assert_mutation_case_evidence(row, f"功能测试用例 row {index}")
        assert_pagination_jump_has_data(row, f"功能测试用例 row {index}")
        assert_transient_flow_closed(
            row.get("操作步骤", ""),
            row.get("预期结果", ""),
            f"功能测试用例 row {index}",
        )
        if row.get("前置条件"):
            assert_numbered(row["前置条件"], f"功能测试用例 row {index} 前置条件")

    try:
        validate_case_collection(function_rows, label="功能测试用例")
        validate_contiguous_function_point_groups(function_rows, label="功能测试用例")
    except ValueError as exc:
        fail(str(exc))

    performance_rows_raw = sheet_rows(workbook, "性能测试设计")
    require_headers(performance_rows_raw, ["性能场景 ID", "业务链路", "性能测试类型", "DFX维度", "DFX场景", "是否纳入本轮测试"], "性能测试设计")
    performance_rows = row_dicts(performance_rows_raw, "性能测试设计")
    if not performance_rows:
        fail("性能测试设计 must contain at least one scenario or explicit not-applicable row")
    performance_dfx: set[tuple[str, str]] = set()
    for index, row in enumerate(performance_rows, start=2):
        if row.get("是否纳入本轮测试", "") != "否":
            dimensions, scenarios = assert_dfx_mapping(
                row.get("DFX维度", ""),
                row.get("DFX场景", ""),
                f"性能测试设计 row {index}",
            )
            performance_dfx.update(dfx_pairs(row.get("DFX维度", ""), row.get("DFX场景", "")))
            if not dimensions & {"DFP性能", "DFX极端", "DFO运维", "DFR可靠"}:
                fail(f"性能测试设计 row {index} should map to DFP性能/DFX极端/DFO运维/DFR可靠, got {sorted(dimensions)}")

    risk_rows_raw = sheet_rows(workbook, "风险与待确认问题")
    require_headers(risk_rows_raw, ["编号", "类型", "关联DFX维度", "关联DFX场景", "描述", "影响范围", "建议处理方式"], "风险与待确认问题")
    risk_dfx: set[tuple[str, str]] = set()
    for index, row in enumerate(row_dicts(risk_rows_raw, "风险与待确认问题"), start=2):
        assert_dfx_mapping(row.get("关联DFX维度", ""), row.get("关联DFX场景", ""), f"风险与待确认问题 row {index}")
        risk_dfx.update(dfx_pairs(row.get("关联DFX维度", ""), row.get("关联DFX场景", "")))

    coverage_rows_raw = sheet_rows(workbook, "页面元素覆盖清单")
    require_headers(
        coverage_rows_raw,
        ["元素 ID", "元素名称/文案", "元素类型", "适用DFX维度", "适用DFX场景", "覆盖用例 ID", "覆盖状态", "待确认问题/备注"],
        "页面元素覆盖清单",
    )
    coverage_rows = row_dicts(coverage_rows_raw, "页面元素覆盖清单")
    valid_status = {"已覆盖", "不适用", "不测范围", "待确认"}
    for index, row in enumerate(coverage_rows, start=2):
        element = row.get("元素名称/文案", "")
        if not element:
            fail(f"页面元素覆盖清单 row {index} is missing 元素名称/文案")
        assert_dfx_mapping(row.get("适用DFX维度", ""), row.get("适用DFX场景", ""), f"页面元素覆盖清单 row {index}")
        status = row.get("覆盖状态", "")
        if status not in valid_status:
            fail(f"页面元素覆盖清单 row {index} has invalid 覆盖状态: {status}")
        linked_ids = parse_ids(row.get("覆盖用例 ID", ""))
        if status == "已覆盖":
            if not linked_ids:
                fail(f"页面元素覆盖清单 row {index} is 已覆盖 but missing 覆盖用例 ID")
            unknown = sorted(linked_ids - case_ids)
            if unknown:
                fail(f"页面元素覆盖清单 row {index} references unknown case IDs: {unknown}")
        elif not row.get("待确认问题/备注", ""):
            fail(f"页面元素覆盖清单 row {index} status {status} must explain reason in 待确认问题/备注")

    landed_dfx = function_dfx | performance_dfx | risk_dfx
    missing_landed_dfx = sorted(generated_scenario_dfx - landed_dfx)
    if missing_landed_dfx:
        fail(f"测试场景矩阵 generated DFX scenarios are not reflected in 功能测试用例/性能测试设计/风险与待确认问题: {missing_landed_dfx[:10]}")

    return {
        "case_ids": case_ids,
        "case_titles": case_titles,
        "case_function_points": case_function_points,
        "function_case_count": len(function_rows),
        "function_rows": function_rows,
        "coverage_rows": coverage_rows,
    }


def validate_import_workbook(
    import_workbook: Path,
    workbook_data: dict[str, object],
    expected_module_path: str | None = None,
) -> None:
    if not import_workbook.exists():
        fail(f"Import workbook not found: {import_workbook}")
    with zipfile.ZipFile(import_workbook) as zf:
        sheet_names = list(workbook_sheet_paths(zf))
    if sheet_names == EXPECTED_SHEETS or "测试系统导入用例" in sheet_names:
        fail("Import workbook must be a copy of 测试用例模板.xlsx, not the formal test design workbook")

    rows_raw = first_sheet_rows(import_workbook)
    if not rows_raw:
        fail("Import workbook has no header row")
    headers = rows_raw[0]
    if headers[: len(IMPORT_HEADERS)] != IMPORT_HEADERS:
        fail(f"Import workbook headers mismatch. Expected {IMPORT_HEADERS}, got {headers}")
    assert_no_residual_markers(import_workbook)
    assert_no_sensitive_values(import_workbook)
    validate_table_ranges(import_workbook)
    assert_data_rows_follow_sample_styles(import_workbook)
    first_sheet_name = ""
    with zipfile.ZipFile(import_workbook) as zf:
        sheet_paths = workbook_sheet_paths(zf)
        first_sheet_name = next(iter(sheet_paths))
    assert_multiline_cells_wrapped(import_workbook, first_sheet_name, IMPORT_MULTILINE_FIELDS)

    rows = row_dicts(rows_raw, "测试系统导入文件")
    if not rows:
        fail("Import workbook must contain mapped test cases")
    assert_dropdown_validations_cover_rows(
        import_workbook,
        first_sheet_name,
        list(IMPORT_ALLOWED_VALUES),
        len(rows) + 1,
    )

    for index, row in enumerate(rows, start=2):
        for field in IMPORT_REQUIRED_FIELDS:
            if not row.get(field):
                fail(f"Import workbook row {index} is missing required field: {field}")
        for field in IMPORT_AUTO_FIELDS:
            if row.get(field):
                fail(f"Import workbook row {index} must leave auto-generated field blank: {field}")
        for field, allowed in IMPORT_ALLOWED_VALUES.items():
            value = row.get(field, "")
            if value not in allowed:
                fail(f"Import workbook row {index} has invalid {field}: {value}")
        if row.get("执行方式") == "自动化":
            note = row.get("备注", "") + row.get("标签", "") + row.get("测试用例说明", "")
            if not any(marker in note for marker in ["自动化资产", "脚本", "流水线", "API自动化", "UI自动化"]):
                fail(f"Import workbook row {index} uses 自动化 but does not reference an implemented automation asset")
        title = row.get("测试用例名称", "")
        if "-" not in title or " -" in title or "- " in title:
            fail(f"Import workbook row {index} 测试用例名称 must use 功能点-当前用例标题 without spaces: {title}")
        assert_numbered(row.get("测试步骤描述", ""), f"Import workbook row {index} 测试步骤描述")
        assert_complete_operation_steps(row.get("测试步骤描述", ""), f"Import workbook row {index} 测试步骤描述")
        assert_numbered(row.get("测试步骤预期结果", ""), f"Import workbook row {index} 测试步骤预期结果")
        assert_transient_flow_closed(
            row.get("测试步骤描述", ""),
            row.get("测试步骤预期结果", ""),
            f"Import workbook row {index}",
        )
        if row.get("前置条件"):
            assert_numbered(row["前置条件"], f"Import workbook row {index} 前置条件")

    function_rows_for_mapping = workbook_data["function_rows"]
    assert isinstance(function_rows_for_mapping, list)
    if len(function_rows_for_mapping) != len(rows):
        fail(
            "Import workbook row count must equal formal function cases before deterministic field mapping: "
            f"{len(rows)} != {len(function_rows_for_mapping)}"
        )

    def expected_case_level(priority: str) -> str:
        value = (priority or "").upper()
        if value in {"L1", "L2", "L3", "L4"}:
            return value
        if value in {"P0", "P1", "高", "高优先级"}:
            return "L1"
        if value in {"P2", "中", "中优先级"}:
            return "L2"
        if value in {"P3", "低", "低优先级"}:
            return "L3"
        return "L2"

    def expected_test_type(value: str) -> str:
        if value in IMPORT_ALLOWED_VALUES["测试类型"]:
            return value
        for marker, mapped in [
            ("性能", "性能规格测试"), ("兼容", "兼容性测试"), ("安全", "安全性测试"),
            ("权限", "安全性测试"), ("可靠", "可靠性测试"), ("稳定", "可靠性测试"),
            ("易用", "易用性测试"), ("维护", "可维护性测试"),
        ]:
            if marker in (value or ""):
                return mapped
        return "功能测试"

    expected_modules = module_names(expected_module_path) if expected_module_path else None
    for position, (case, imported) in enumerate(zip(function_rows_for_mapping, rows), start=1):
        dimension = case.get("DFX维度", "")
        scenario = case.get("DFX场景", "")
        tags = ";".join(
            part for part in [case.get("模块", ""), case.get("功能点", ""), dimension, scenario] if part
        )
        dfx_note = f"DFX覆盖：{dimension}-{scenario}" if dimension and scenario else ""
        remarks = "\n".join(part for part in [dfx_note, case.get("备注", "")] if part)
        automation_note = f"{case.get('备注', '')}{case.get('是否适合自动化', '')}"
        execution = "自动化" if (
            "自动化" in automation_note
            and any(marker in automation_note for marker in ["自动化资产", "脚本", "流水线", "API自动化", "UI自动化", "已实现"])
        ) else "手动"
        expected = {
            "测试用例序号": str(position),
            "测试用例名称": case.get("用例标题", ""),
            "测试步骤描述": case.get("操作步骤", ""),
            "测试步骤预期结果": case.get("预期结果", ""),
            "测试类型": expected_test_type(case.get("测试类型", "")),
            "测试用例级别": expected_case_level(case.get("优先级", "")),
            "执行方式": execution,
            "测试用例说明": case.get("功能点", ""),
            "前置条件": case.get("前置条件", ""),
            "标签": tags,
            "备注": remarks,
        }
        if expected_modules is not None:
            module_fields = [
                "一级模块名称",
                "二级模块名称",
                "三级模块名称",
                "四级模块名称",
                "五级模块名称",
            ]
            expected.update(
                {field: expected_modules[index] for index, field in enumerate(module_fields)}
            )
        changed = [field for field, value in expected.items() if imported.get(field, "") != value]
        if changed:
            fail(
                f"Import workbook data row {position} deterministic mapping differs from formal function case "
                f"{case.get('用例 ID', '')}: {changed}"
            )

    try:
        validate_case_collection(
            rows,
            label="Import workbook",
            id_field="测试用例序号",
            title_field="测试用例名称",
            steps_field="测试步骤描述",
            expected_field="测试步骤预期结果",
        )
        function_rows = workbook_data["function_rows"]
        assert isinstance(function_rows, list)
        formal_counter = transfer_counter(
            function_rows,
            {
                "用例标题": "用例标题",
                "操作步骤": "操作步骤",
                "预期结果": "预期结果",
                "前置条件": "前置条件",
            },
        )
        import_counter = transfer_counter(
            rows,
            {
                "用例标题": "测试用例名称",
                "操作步骤": "测试步骤描述",
                "预期结果": "测试步骤预期结果",
                "前置条件": "前置条件",
            },
        )
        validate_case_order_parity(
            function_rows,
            rows,
            source_field_map={
                "用例标题": "用例标题",
                "操作步骤": "操作步骤",
                "预期结果": "预期结果",
                "前置条件": "前置条件",
            },
            target_field_map={
                "用例标题": "测试用例名称",
                "操作步骤": "测试步骤描述",
                "预期结果": "测试步骤预期结果",
                "前置条件": "前置条件",
            },
            fields=["用例标题", "操作步骤", "预期结果", "前置条件"],
            source_label="功能测试用例 Sheet",
            target_label="Import workbook",
        )
    except ValueError as exc:
        fail(str(exc))
    if formal_counter != import_counter:
        missing = formal_counter - import_counter
        unexpected = import_counter - formal_counter
        missing_summary = [(signature[0], count) for signature, count in missing.items()][:10]
        unexpected_summary = [(signature[0], count) for signature, count in unexpected.items()][:10]
        fail(
            "Import workbook title/steps/expected/precondition rows must exactly match formal function cases; "
            f"missing={missing_summary}, unexpected={unexpected_summary}"
        )

    xml = first_worksheet_xml(import_workbook)
    for marker, label in {
        'sqref="R2:R2001"': "测试类型",
        'sqref="S2:S2001"': "测试用例级别",
        'sqref="T2:T2001"': "执行方式",
    }.items():
        if marker not in xml:
            fail(f"Import workbook is missing preserved {label} dropdown validation: {marker}")


def positive_int(value: str, field: str, batch_id: str) -> int:
    if not re.fullmatch(r"\d+", value or ""):
        fail(f"batch {batch_id} field {field} must be a non-negative integer: {value}")
    return int(value)


def has_multiple_leaf_values(value: str) -> bool:
    normalized = (value or "").strip()
    if not normalized or normalized in {"—", "-", "无", "N/A", "NA"}:
        return False
    return any(separator in normalized for separator in MULTI_LEAF_SEPARATORS)


def is_passed_batch(row: dict[str, str]) -> bool:
    return row.get("覆盖质量自检", "").strip() == "通过"


def is_selection_element(row: dict[str, str]) -> bool:
    selection_markers = ["下拉", "级联", "选择", "单选", "复选", "枚举", "树选择"]
    text = " ".join(
        [
            row.get("元素类型", ""),
            row.get("交互方式", ""),
        ]
    )
    return any(marker in text for marker in selection_markers)


def is_pagination_element(row: dict[str, str]) -> bool:
    text = " ".join(
        [
            row.get("元素名称/文案", ""),
            row.get("元素类型", ""),
            row.get("交互方式", ""),
            row.get("选项取值/输入值", ""),
        ]
    )
    return any(marker in text for marker in ["分页", "每页", "页码", "上一页", "下一页", "跳转", "条/页", "条每页"])


def is_input_element(row: dict[str, str]) -> bool:
    non_input_types = ["按钮", "图标", "表格列", "分页", "链接", "开关"]
    element_type = row.get("元素类型", "")
    interaction = row.get("交互方式", "")
    element_name = row.get("元素名称/文案", "")
    if any(marker in element_type for marker in non_input_types):
        return False
    input_markers = ["输入", "文本框", "文本域", "搜索框", "查询框", "数字框", "日期框"]
    name_markers = ["输入框", "搜索框", "查询框", "文本域", "名称字段", "编码字段", "地址字段", "URL字段", "端口字段", "邮箱字段", "手机号字段"]
    return any(marker in f"{element_type} {interaction}" for marker in input_markers) or any(
        marker in element_name for marker in name_markers
    )


def is_create_flow_element(row: dict[str, str]) -> bool:
    create_markers = ["新增", "创建", "添加", "新建", "保存", "提交", "下一步", "完成", "测试连接"]
    non_create_types = ["表格列", "分页", "图标"]
    element_type = row.get("元素类型", "")
    interaction = row.get("交互方式", "")
    if any(marker in element_type for marker in non_create_types) or interaction == "查看":
        return False
    text = " ".join(
        [
            row.get("元素名称/文案", ""),
            row.get("元素类型", ""),
            row.get("交互方式", ""),
        ]
    )
    return any(marker in text for marker in create_markers)


def is_mutating_discovery_element(row: dict[str, str]) -> bool:
    text = " ".join(
        [
            row.get("元素名称/文案", ""),
            row.get("元素类型", ""),
            row.get("交互方式", ""),
            row.get("结果分支/后续状态", ""),
        ]
    )
    return any(marker in text for marker in ["新增", "创建", "添加", "新建", "保存", "提交", "编辑", "修改", "删除"])


def has_create_result_branch(value: str) -> bool:
    result_markers = ["成功", "失败", "校验", "错误", "重复", "为空", "无权限", "停留", "进入", "跳转", "详情", "下一级", "下一步"]
    return any(marker in (value or "") for marker in result_markers)


def assert_selection_has_real_choice(row: dict[str, str], index: int, page: str, element: str) -> None:
    values = row.get("选项取值/输入值", "")
    weak_values = {"查看选项", "展开查看", "查看下拉内容", "全部选项", "下拉选项", "无"}
    if values.strip() in weak_values:
        fail(f"page-discovery.csv row {index} selection element must record actual selected options, not only viewing options: {page} / {element}")
    combined = f"{values} {row.get('联动/依赖变化', '')} {row.get('结果分支/后续状态', '')}"
    if not any(marker in combined for marker in ["选择", "切换", "分别", "代表", "联动", "无联动", "刷新", "禁用", "启用", "校验"]):
        fail(f"page-discovery.csv row {index} selection element must record selection action and dependency/result change: {page} / {element}")


def assert_mutating_discovery_evidence(row: dict[str, str], index: int, page: str, element: str) -> None:
    combined = "\n".join(
        [
            row.get("选项取值/输入值", ""),
            row.get("联动/依赖变化", ""),
            row.get("结果分支/后续状态", ""),
            row.get("预期/观察行为", ""),
            row.get("测试数据来源", ""),
            row.get("备注", ""),
        ]
    )
    commits_change = contains_any_marker(combined, MUTATION_COMMIT_MARKERS)
    if not commits_change and contains_any_marker(combined, NON_MUTATING_BLOCK_MARKERS + SAFE_EXISTING_DATA_MARKERS):
        return
    if any(marker in combined for marker in ["已有数据", "既有数据"]):
        if not contains_any_marker(combined, SAFE_EXISTING_DATA_MARKERS):
            fail(f"page-discovery.csv row {index} existing-data operation must close with cancel/close/no-save/no-change: {page} / {element}")
        return
    if commits_change and not contains_any_marker(combined, MUTATING_TEST_DATA_MARKERS):
        fail(f"page-discovery.csv row {index} mutating operation must record current test data marker/source: {page} / {element}")


def is_template_or_empty_row(row: dict[str, str]) -> bool:
    combined = "".join(row.values())
    if not combined:
        return True
    template_markers = [
        "补充页面元素、DFX扩展方向和计划用例ID",
        "补充本次创建或用户提供测试数据的完整生命周期",
        "仅记录当前独立叶子批次中本次创建或用户提供测试数据的逐修改项生命周期",
    ]
    return any(marker in combined for marker in template_markers)


def has_configuration_effective_evidence(row: dict[str, str]) -> bool:
    combined = "\n".join(
        [
            row.get("测试数据", ""),
            row.get("操作步骤", ""),
            row.get("预期结果", ""),
            row.get("备注", ""),
        ]
    )
    return any(
        marker in combined
        for marker in ["回显", "生效", "重新打开", "详情", "测试Agent", "测试连接", "预览", "调用", "关联", "下游", "工作流"]
    )


def assert_pagination_jump_has_data(row: dict[str, str], label: str) -> None:
    combined = "\n".join([row.get("测试数据", ""), row.get("操作步骤", ""), row.get("预期结果", "")])
    if not any(marker in combined for marker in ["第2页", "第 2 页", "输入2", "输入 2", "跳至页码", "页码输入"]):
        return
    if not any(marker in combined for marker in ["超过一页", "多页", "大于1页", "大于 1 页", "超过10条", "超过 10 条", "造数", "准备超过"]):
        fail(f"{label} jumps to page 2 but does not declare multi-page test data preparation")


def validate_batch_granularity(row: dict[str, str], numbers: dict[str, int]) -> None:
    batch_id = row.get("批次ID", "")
    leaf_path = row.get("最小标题路径", "").strip()
    tertiary_value = row.get("三级菜单/页面域", "").strip()
    if not leaf_path:
        fail(f"batch {batch_id} must declare 最小标题路径 for leaf-level batching")
    if has_multiple_leaf_values(leaf_path):
        fail(f"batch {batch_id} 最小标题路径 must point to exactly one leaf title, not multiple leaves: {leaf_path}")
    if has_multiple_leaf_values(tertiary_value):
        fail(f"batch {batch_id} 三级菜单/页面域 must not contain merged leaves: {tertiary_value}")
    if row.get("拆分/合并原因", "").strip():
        fail(f"batch {batch_id} must not use 拆分/合并原因 because merge/split is forbidden")


def project_root_from_batch_status(batch_status: Path) -> Path:
    resolved = batch_status.resolve()
    for parent in [resolved.parent, *resolved.parents]:
        if (parent / "docs" / "test-assets").exists() or (parent / "docs" / "test-design").exists():
            return parent
    return resolved.parent


def resolve_project_path(raw_path: str, batch_status: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    project_root = project_root_from_batch_status(batch_status)
    root_candidate = project_root / candidate
    if root_candidate.exists():
        return root_candidate
    return batch_status.resolve().parent / candidate


def validate_batch_status(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        fail(f"Batch status file not found: {path}")
    try:
        shared_assert_no_sensitive_batch_files(path.resolve().parent)
    except SensitiveDataError as exc:
        fail(str(exc))
    rows = [
        row
        for row in csv_rows_with_exact_header(path, BATCH_EXPECTED_HEADERS, "batch-status.csv")
        if (row.get("批次ID") or "").strip()
    ]
    assert_no_sensitive_csv_values(rows, "batch-status.csv")
    if not rows:
        fail("batch-status.csv must contain at least one batch row")

    completed_statuses = {"已完成", "完成"}
    passed_leaf_paths: dict[str, str] = {}
    for row in rows:
        batch_id = row["批次ID"]
        numbers = {field: positive_int(row.get(field, ""), field, batch_id) for field in BATCH_NUMBER_FIELDS}
        if numbers["已覆盖元素数"] > numbers["元素总数"]:
            fail(f"batch {batch_id} 已覆盖元素数 cannot exceed 元素总数")
        if numbers["待确认元素数"] > numbers["元素总数"]:
            fail(f"batch {batch_id} 待确认元素数 cannot exceed 元素总数")
        status = row.get("状态", "")
        self_check = row.get("覆盖质量自检", "")
        if status in completed_statuses and self_check != "通过":
            fail(f"batch {batch_id} cannot be marked {status} unless 覆盖质量自检 is 通过")
        if self_check == "通过" and status not in completed_statuses:
            fail(f"batch {batch_id} cannot pass 覆盖质量自检 unless 状态 is 已完成")
        if is_passed_batch(row):
            validate_batch_granularity(row, numbers)
            leaf_path = row.get("最小标题路径", "").strip()
            if leaf_path in passed_leaf_paths:
                fail(
                    f"batch {batch_id} duplicates 最小标题路径 already covered by {passed_leaf_paths[leaf_path]}: {leaf_path}"
                )
            passed_leaf_paths[leaf_path] = batch_id
            for field in ["页面数", "元素总数", "已覆盖元素数", "功能用例数", "性能场景数"]:
                if numbers[field] <= 0:
                    fail(f"batch {batch_id} cannot pass 覆盖质量自检 with {field}=0")
            for field in BATCH_PASS_BOOLEAN_FIELDS:
                if row.get(field) != "是":
                    fail(f"batch {batch_id} cannot pass 覆盖质量自检 when {field} is not 是")
            if not row.get("导入文件路径"):
                fail(f"batch {batch_id} cannot pass 覆盖质量自检 without 导入文件路径")
    return rows


def validate_batch_file_consistency(batch_status: Path, batch_rows: list[dict[str, str]]) -> None:
    project_root = project_root_from_batch_status(batch_status)
    current_dirs = [
        project_root / "docs" / "test-design" / "current",
        project_root / "docs" / "test-design" / "deliverables",
    ]
    completed_statuses = {"已完成", "完成"}
    for row in batch_rows:
        batch_id = row.get("批次ID", "")
        if not batch_id:
            continue
        status = row.get("状态", "")
        if status in completed_statuses:
            continue
        for directory in current_dirs:
            if not directory.exists():
                continue
            matches = list(directory.glob(f"*{batch_id}*.xlsx"))
            if matches:
                fail(
                    f"batch {batch_id} has generated current/deliverable workbook but batch-status.csv status is {status}: {matches[0]}"
                )


def validate_batch_artifacts_location(batch_status: Path) -> None:
    batch_runs_dir = batch_status.resolve().parent.parent
    root_artifacts = batch_runs_dir / "artifacts"
    if root_artifacts.exists() and any(root_artifacts.iterdir()):
        fail(
            "Batch artifacts must be stored under docs/test-assets/batch-runs/<task>/artifacts/, "
            f"not the shared batch-runs/artifacts directory: {root_artifacts}"
        )
    scripts_dir = batch_status.resolve().parent / "artifacts" / "scripts"
    pycache_dir = scripts_dir / "__pycache__"
    if pycache_dir.exists():
        fail(f"Batch artifacts must not keep Python __pycache__ directories. Remove before delivery: {pycache_dir}")


def validate_batch_run_directory_from_page_discovery(page_discovery: Path) -> Path:
    run_dir = page_discovery.resolve().parent
    required_entries = [
        "batch-scope.json",
        "batch-plan.md",
        "batch-status.csv",
        "batch-review.md",
        "page-element-inventory.csv",
        "page-discovery.csv",
        "selection-option-observations.csv",
        "interaction-branch-observations.csv",
        "element-case-plan.csv",
        "test-data-lifecycle.csv",
        "risk-confirmation.csv",
        "artifacts",
    ]
    missing = [name for name in required_entries if not (run_dir / name).exists()]
    if missing:
        fail(
            "A batch run with page-discovery.csv must keep the full standard ledger "
            f"beside it. Missing {missing} in {run_dir}. "
            "Run scripts/test_design_excel_tools.py init-batch-run before page discovery."
        )
    batch_runs_dir = run_dir.parent
    root_artifacts = batch_runs_dir / "artifacts"
    if root_artifacts.exists() and any(root_artifacts.iterdir()):
        fail(
            "Batch artifacts must be stored under docs/test-assets/batch-runs/<task>/artifacts/, "
            f"not the shared batch-runs/artifacts directory: {root_artifacts}"
        )
    scripts_dir = run_dir / "artifacts" / "scripts"
    pycache_dir = scripts_dir / "__pycache__"
    if pycache_dir.exists():
        fail(f"Batch artifacts must not keep Python __pycache__ directories. Remove before delivery: {pycache_dir}")
    stale_workbooks = sorted((run_dir / "artifacts").rglob("*.xlsx"))
    if stale_workbooks:
        fail(
            "Batch artifacts must not keep generated workbook copies after complete-deliverables. "
            f"Use current/deliverables/modules/imports as the workbook destinations: {stale_workbooks[0]}"
        )
    return run_dir / "batch-status.csv"


def is_relative_to_path(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def batch_scope_module_path(batch_status: Path) -> str:
    scope_path = batch_status.resolve().parent / "batch-scope.json"
    try:
        scope = json.loads(scope_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"batch-scope.json is missing or invalid beside batch-status.csv: {exc}")
    module_path = str(scope.get("module_path", "")).strip() if isinstance(scope, dict) else ""
    if not module_path:
        fail("batch-scope.json must contain module_path for deterministic import module mapping")
    return module_path


def validate_batch_import_workbooks(batch_status: Path, batch_rows: list[dict[str, str]]) -> None:
    expected_module_path = batch_scope_module_path(batch_status)
    for row in batch_rows:
        if not is_passed_batch(row):
            continue
        batch_id = row.get("批次ID", "")
        archive_raw = row.get("归档路径", "")
        import_raw = row.get("导入文件路径", "")
        if not archive_raw:
            fail(f"batch {batch_id} cannot pass 覆盖质量自检 without 归档路径")
        if not import_raw:
            fail(f"batch {batch_id} cannot pass 覆盖质量自检 without 导入文件路径")
        archive_path = resolve_project_path(archive_raw, batch_status)
        import_path = resolve_project_path(import_raw, batch_status)
        if not archive_path.exists():
            fail(f"batch {batch_id} 归档路径 does not exist: {archive_raw}")
        if not import_path.exists():
            fail(f"batch {batch_id} 导入文件路径 does not exist: {import_raw}")
        project_root = project_root_from_batch_status(batch_status)
        modules_dir = project_root / "docs" / "test-assets" / "modules"
        imports_dir = project_root / "docs" / "test-assets" / "imports"
        if not is_relative_to_path(archive_path, modules_dir):
            fail(
                f"batch {batch_id} 归档路径 must point to internal module archive under docs/test-assets/modules/: {archive_raw}"
            )
        if not is_relative_to_path(import_path, imports_dir):
            fail(
                f"batch {batch_id} 导入文件路径 must point to internal import archive under docs/test-assets/imports/: {import_raw}"
            )
        archive_data = validate_workbook(archive_path)
        validate_import_workbook(import_path, archive_data, expected_module_path)


def validate_completed_batch_workbook_semantics(
    workbook_data: dict[str, object],
    batch_rows: list[dict[str, str]],
) -> None:
    if not any(is_passed_batch(row) for row in batch_rows):
        return
    function_rows = workbook_data.get("function_rows")
    coverage_rows = workbook_data.get("coverage_rows")
    if not isinstance(function_rows, list) or not isinstance(coverage_rows, list):
        fail("formal workbook data is incomplete for completed-batch semantic validation")
    derived_counts = derived_case_quality_counts(function_rows)
    for row in batch_rows:
        if not is_passed_batch(row):
            continue
        mismatches = {
            field: (positive_int(row.get(field, "0"), field, row.get("批次ID", "")), expected)
            for field, expected in derived_counts.items()
            if positive_int(row.get(field, "0"), field, row.get("批次ID", "")) != expected
        }
        if mismatches:
            fail(f"batch {row.get('批次ID', '')} quality-direction counts differ from formal function cases: {mismatches}")
    pending = [
        f"row {index} ({row.get('页面/入口', '')}/{row.get('元素名称/文案', '')})"
        for index, row in enumerate(coverage_rows, start=2)
        if row.get("覆盖状态", "").strip() != "已覆盖"
    ]
    if pending:
        fail(
            "completed batch cannot retain pending/not-covered rows in formal 页面元素覆盖清单: "
            f"{pending[:10]}"
        )


def validate_batch_review(batch_status: Path, batch_rows: list[dict[str, str]]) -> None:
    def markdown_cell(value: object) -> str:
        return " ".join(str(value or "").split()).replace("|", "｜")

    review_path = batch_status.resolve().parent / "batch-review.md"
    if not review_path.exists():
        fail(f"batch-review.md not found beside batch-status.csv: {review_path}")
    assert_no_sensitive_text_values(review_path, "batch-review.md")
    text = review_path.read_text(encoding="utf-8-sig")
    completed_rows = [row for row in batch_rows if is_passed_batch(row)]
    for row in completed_rows:
        batch_id = row.get("批次ID", "")
        if batch_id and batch_id not in text:
            fail(f"batch-review.md must include completed batch: {batch_id}")
        stale_pattern = rf"\|\s*{re.escape(batch_id)}\s*\|\s*待开始\s*\|\s*0\s*\|\s*0\s*\|"
        if re.search(stale_pattern, text):
            fail(f"batch-review.md still contains stale template row for completed batch: {batch_id}")
        for field in ["归档路径", "导入文件路径"]:
            value = row.get(field, "")
            if value and value not in text:
                fail(f"batch-review.md must reference {field} for completed batch {batch_id}: {value}")
        table_rows: list[list[str]] = []
        for line in text.splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and cells[0] == batch_id:
                table_rows.append(cells)
        if len(table_rows) != 1:
            fail(
                f"batch-review.md must contain exactly one populated completion row for {batch_id}; "
                f"found {len(table_rows)}"
            )
        cells = table_rows[0]
        if len(cells) != 11:
            fail(f"batch-review.md completion row for {batch_id} must contain exactly 11 columns")
        expected = [
            markdown_cell(value)
            for value in [
                batch_id,
                row.get("状态", ""),
                row.get("页面数", ""),
                row.get("元素总数", ""),
                row.get("已覆盖元素数", ""),
                row.get("功能用例数", ""),
                row.get("性能场景数", ""),
                row.get("归档路径", ""),
                row.get("导入文件路径", ""),
                row.get("覆盖质量自检", ""),
                row.get("待确认问题", "").strip() or "无",
            ]
        ]
        if cells != expected:
            fail(
                f"batch-review.md completion row for {batch_id} must match batch-status.csv counts, paths, status, "
                f"quality result, and remaining issue; expected={expected}, actual={cells}"
            )


def validate_batch_plan(batch_status: Path, batch_rows: list[dict[str, str]]) -> None:
    plan_path = batch_status.resolve().parent / "batch-plan.md"
    if not plan_path.exists():
        fail(f"batch-plan.md not found beside batch-status.csv: {plan_path}")
    assert_no_sensitive_text_values(plan_path, "batch-plan.md")
    text = plan_path.read_text(encoding="utf-8-sig")
    completed_rows = [row for row in batch_rows if is_passed_batch(row)]
    for row in completed_rows:
        batch_id = row.get("批次ID", "")
        leaf_path = row.get("最小标题路径", "")
        if batch_id and batch_id not in text:
            fail(f"batch-plan.md must include completed batch ID: {batch_id}")
        if leaf_path and leaf_path not in text:
            fail(f"batch-plan.md must include completed batch 最小标题路径: {leaf_path}")
        stale_status_pattern = rf"\|\s*{re.escape(batch_id)}\s*\|[^\n|]*\|[^\n|]*\|\s*(执行中|待开始)\s*\|"
        if batch_id and re.search(stale_status_pattern, text):
            fail(f"batch-plan.md still marks completed batch {batch_id} as 执行中/待开始")

    page_section = re.search(r"##\s*页面清单(?P<body>.*?)(?:\n##\s|\Z)", text, re.S)
    if page_section and len(completed_rows) == 1:
        page_lines = [
            line
            for line in page_section.group("body").splitlines()
            if re.match(r"^\s*\d+\.\s+\S+", line)
        ]
        declared_pages = positive_int(completed_rows[0].get("页面数", ""), "页面数", completed_rows[0].get("批次ID", ""))
        if page_lines and len(page_lines) != declared_pages:
            fail(
                f"batch-plan.md 页面清单 count must match batch-status.csv 页面数 for completed single batch: "
                f"{len(page_lines)} != {declared_pages}"
            )


def validate_sheet_split_artifacts(
    run_dir: Path,
    workbook_function_rows: list[dict[str, str]] | int | None = None,
    *,
    workbook_function_case_count: int | None = None,
) -> None:
    import json

    # Preserve the former count-only call shape for external callers while the
    # delivery pipeline now supplies full rows for exact field parity.
    legacy_count = workbook_function_case_count
    if isinstance(workbook_function_rows, int):
        if legacy_count is not None and legacy_count != workbook_function_rows:
            fail("Conflicting workbook function case counts were supplied")
        legacy_count = workbook_function_rows
        workbook_function_rows = None

    data_dir = run_dir / "artifacts" / "data"
    scripts_dir = run_dir / "artifacts" / "scripts"
    if not data_dir.exists():
        fail(f"artifacts/data directory is required for sheet-split generation: {data_dir}")
    misplaced_python_helpers = [
        path for path in (run_dir / "artifacts").rglob("*.py")
        if scripts_dir not in path.parents
    ]
    if misplaced_python_helpers:
        fail(
            "Generated Python helpers must stay under artifacts/scripts and must not be hidden beside data/evidence: "
            f"{misplaced_python_helpers[:10]}"
        )
    required_data_files = [
        "overview.json",
        "requirements.json",
        "scenarios.json",
        "performance.json",
        "risks.json",
        "automation.json",
        "page_elements.json",
    ]
    missing_data = [name for name in required_data_files if not (data_dir / name).exists()]
    if missing_data:
        fail(f"artifacts/data is missing sheet-split data files: {missing_data}")
    manifest = data_dir / "function_cases_manifest.json"
    if not manifest.exists():
        fail("artifacts/data must contain function_cases_manifest.json and Excel assembly must read manifest-listed shards")
    try:
        manifest_data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        fail(f"{manifest} must be valid JSON: {exc}")
    manifest_parts = manifest_data.get("parts") if isinstance(manifest_data, dict) else manifest_data
    if not isinstance(manifest_parts, list) or not manifest_parts:
        fail("function_cases_manifest.json must contain a non-empty parts list")
    bad_part_names = [str(name) for name in manifest_parts if not FUNCTION_CASE_PART_RE.match(str(name))]
    if bad_part_names:
        fail(f"function_cases_manifest.json contains invalid shard names: {bad_part_names[:10]}")
    expected_part_names = [f"function_cases_part_{index:03d}.json" for index in range(1, len(manifest_parts) + 1)]
    actual_part_names = [str(name) for name in manifest_parts]
    if actual_part_names != expected_part_names:
        fail(
            "function_cases_manifest.json parts must be unique, sequential, and ordered as 001..N; "
            f"expected={expected_part_names}, actual={actual_part_names}"
        )
    function_parts = [data_dir / str(name) for name in manifest_parts]
    stale_parts = sorted(path.name for path in data_dir.glob("function_cases_part_*.json") if path.name not in set(map(str, manifest_parts)))
    if stale_parts:
        fail(f"artifacts/data contains stale function case shards not listed in function_cases_manifest.json: {stale_parts[:10]}")
    actual_function_case_count = 0
    manifest_function_rows: list[dict[str, object]] = []
    shard_function_rows: list[list[dict[str, object]]] = []
    for part in function_parts:
        try:
            data = json.loads(part.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            fail(f"{part} must be valid JSON: {exc}")
        cases = data.get("cases") if isinstance(data, dict) else data
        if not isinstance(cases, list):
            fail(f"{part} must contain a list or an object with cases list")
        if not (1 <= len(cases) <= MAX_FUNCTION_CASES_PER_PART):
            fail(f"{part} must contain 1..{MAX_FUNCTION_CASES_PER_PART} function cases: {len(cases)}")
        actual_function_case_count += len(cases)
        current_shard_rows: list[dict[str, object]] = []
        for case_index, case in enumerate(cases, start=1):
            if not isinstance(case, dict):
                fail(f"{part.name} case {case_index} must be an object")
            keys = set(case)
            forbidden = sorted(keys & FUNCTION_CASE_FORBIDDEN_FIELDS)
            if forbidden:
                fail(f"{part.name} case {case_index} contains forbidden/deprecated fields: {forbidden}")
            missing = [field for field in FUNCTION_CASE_REQUIRED_FIELDS if field not in case]
            if missing:
                fail(f"{part.name} case {case_index} is missing required fields: {missing}")
            extra = sorted(keys - set(FUNCTION_CASE_REQUIRED_FIELDS))
            if extra:
                fail(f"{part.name} case {case_index} contains non-standard fields: {extra}")
            case_id = str(case.get("用例 ID", "") or "").strip()
            if not case_id or "XXX" in case_id:
                fail(f"{part.name} case {case_index} must use a concrete 用例 ID")
            function_point = str(case.get("功能点", "") or "").strip()
            title = str(case.get("用例标题", "") or "").strip()
            if not title.startswith(f"{function_point}-"):
                fail(f"{part.name} case {case_index} 用例标题 must use 功能点-当前标题 format")
            manifest_function_rows.append(case)
            current_shard_rows.append(case)
        shard_function_rows.append(current_shard_rows)
    if isinstance(manifest_data, dict):
        if manifest_data.get("part_size") not in {None, MAX_FUNCTION_CASES_PER_PART}:
            fail(f"function_cases_manifest.json part_size must be {MAX_FUNCTION_CASES_PER_PART}: {manifest_data.get('part_size')}")
        if manifest_data.get("total_cases") not in {None, actual_function_case_count}:
            fail(
                "function_cases_manifest.json total_cases must match actual shard rows: "
                f"{manifest_data.get('total_cases')} != {actual_function_case_count}"
            )
    try:
        validate_case_collection(manifest_function_rows, label="function case manifest")
        validate_contiguous_function_point_groups(manifest_function_rows, label="function case manifest")
        validate_function_point_aware_shards(
            shard_function_rows,
            label="function case manifest",
            max_per_shard=MAX_FUNCTION_CASES_PER_PART,
        )
        if workbook_function_rows is not None:
            validate_case_field_parity(
                manifest_function_rows,
                workbook_function_rows,
                fields=FUNCTION_CASE_REQUIRED_FIELDS,
                source_label="function case manifest",
                target_label="功能测试用例 Sheet",
            )
            validate_case_order_parity(
                manifest_function_rows,
                workbook_function_rows,
                source_field_map={field: field for field in FUNCTION_CASE_REQUIRED_FIELDS},
                target_field_map={field: field for field in FUNCTION_CASE_REQUIRED_FIELDS},
                fields=FUNCTION_CASE_REQUIRED_FIELDS,
                source_label="function case manifest",
                target_label="功能测试用例 Sheet",
            )
    except ValueError as exc:
        fail(str(exc))
    if legacy_count is not None and legacy_count != actual_function_case_count:
        fail(
            "功能测试用例 Sheet row count must match manifest-listed function case shards: "
            f"{legacy_count} != {actual_function_case_count}"
        )
    if scripts_dir.exists():
        for script in scripts_dir.glob("*.py"):
            text = script.read_text(encoding="utf-8-sig", errors="ignore")
            if "load_workbook" in text and re.search(r"\.save\s*\(", text):
                fail(
                    f"Batch script must not assemble/save formal Excel directly: {script}. "
                    "Use scripts/run-test-design.ps1 assemble-formal-workbook or complete-deliverables --run-dir."
                )


def validate_element_case_plan_and_lifecycle(
    run_dir: Path,
    discovery_rows: list[dict[str, str]],
    case_ids: set[str],
    case_rows: list[dict[str, str]],
) -> None:
    plan_path = run_dir / "element-case-plan.csv"
    lifecycle_path = run_dir / "test-data-lifecycle.csv"
    plan_rows_all = csv_rows_with_exact_header(plan_path, ELEMENT_CASE_PLAN_EXPECTED_HEADERS, "element-case-plan.csv")
    lifecycle_rows_all = csv_rows_with_exact_header(
        lifecycle_path,
        TEST_DATA_LIFECYCLE_EXPECTED_HEADERS,
        "test-data-lifecycle.csv",
    )
    assert_no_sensitive_csv_values(plan_rows_all, "element-case-plan.csv")
    assert_no_sensitive_csv_values(lifecycle_rows_all, "test-data-lifecycle.csv")
    plan_rows = [row for row in plan_rows_all if not is_template_or_empty_row(row)]
    lifecycle_rows = [row for row in lifecycle_rows_all if not is_template_or_empty_row(row)]
    if not plan_rows:
        fail("element-case-plan.csv must contain real element-to-case planning rows")
    has_mutation = validate_operation_plan_rows(plan_rows)
    validate_lifecycle_rows(lifecycle_rows, has_mutation, contains_any_marker, plan_rows)

    plan_keys = {
        normalized_key(row.get("页面/入口", ""), row.get("元素名称/文案", ""))
        for row in plan_rows
        if row.get("页面/入口") and row.get("元素名称/文案")
    }
    generated_discovery_keys = {
        normalized_key(row.get("页面/入口", ""), row.get("元素名称/文案", ""))
        for row in discovery_rows
        if row.get("是否已生成用例") == "是" and row.get("页面/入口") and row.get("元素名称/文案")
    }
    missing_plan = sorted(generated_discovery_keys - plan_keys)
    if missing_plan:
        fail(f"element-case-plan.csv is missing generated page elements: {missing_plan[:10]}")

    all_actual_ids: set[str] = set()
    configuration_case_ids: set[str] = set()
    lifecycle_required_ids: set[str] = set()
    for index, row in enumerate(plan_rows, start=2):
        batch_id = row.get("批次ID", "")
        if not row.get("最小标题路径"):
            fail(f"element-case-plan.csv row {index} must include 最小标题路径")
        if not row.get("交互实例ID") or not row.get("功能点") or not row.get("元素名称/文案"):
            fail(f"element-case-plan.csv row {index} must include 交互实例ID, 功能点 and 元素名称/文案")
        assert_dfx_mapping(row.get("适用DFX维度", ""), row.get("适用DFX场景", ""), f"element-case-plan.csv row {index}")
        expected_raw = row.get("应生成用例数", "")
        if not re.fullmatch(r"\d+", expected_raw or ""):
            fail(f"element-case-plan.csv row {index} 应生成用例数 must be a non-negative integer")
        expected_count = int(expected_raw)
        actual_sequence = parse_id_sequence(row.get("实际用例ID", ""))
        planned_sequence = parse_id_sequence(row.get("计划用例ID", ""))
        actual_ids = set(actual_sequence)
        planned_ids = set(planned_sequence)
        if expected_count > 0 and not (actual_ids or row.get("未生成原因")):
            fail(f"element-case-plan.csv row {index} expects cases but has neither 实际用例ID nor 未生成原因")
        if actual_ids:
            unknown = sorted(actual_ids - case_ids)
            if unknown:
                fail(f"element-case-plan.csv row {index} references unknown 实际用例ID: {unknown}")
            if planned_sequence and actual_sequence != planned_sequence:
                fail(
                    f"element-case-plan.csv row {index} 实际用例ID must exactly preserve 计划用例ID order; "
                    f"planned={planned_sequence}, actual={actual_sequence}"
                )
            all_actual_ids.update(actual_ids)
        if row.get("是否涉及配置生效") == "是":
            configuration_case_ids.update(actual_ids)
        if row.get("是否涉及CRUD闭环") == "是":
            lifecycle_required_ids.update(actual_ids)
        if expected_count > 0 and len(actual_ids) < expected_count and not row.get("未生成原因"):
            fail(
                f"element-case-plan.csv row {index} generated fewer cases than 应生成用例数 without 未生成原因: "
                f"{len(actual_ids)} < {expected_count}"
            )

    missing_from_plan = sorted(case_ids - all_actual_ids)
    if missing_from_plan:
        fail(f"功能测试用例 contains case IDs missing from element-case-plan.csv 实际用例ID: {missing_from_plan[:10]}")

    case_by_id = {row.get("用例 ID", ""): row for row in case_rows if row.get("用例 ID")}
    for case_id in sorted(configuration_case_ids):
        row = case_by_id.get(case_id)
        if row and not has_configuration_effective_evidence(row):
            fail(f"Configuration-related case {case_id} must verify saved echo/effective behavior")

    lifecycle_case_ids: set[str] = set()
    lifecycle_names: list[str] = []
    for index, row in enumerate(lifecycle_rows, start=2):
        name = row.get("测试数据ID/名称", "")
        if not name:
            fail(f"test-data-lifecycle.csv row {index} must include 测试数据ID/名称")
        lifecycle_names.append(name)
        if not contains_any_marker(" ".join(row.values()), MUTATING_TEST_DATA_MARKERS):
            fail(f"test-data-lifecycle.csv row {index} must use AI_TEST/CODEX_TEST or user-provided test data marker")
        lifecycle_case_ids.update(parse_ids(row.get("创建步骤关联用例", "")))
        lifecycle_case_ids.update(parse_ids(" ".join(row.values())) & case_ids)
        if row.get("配置生效验证点") and not any(marker in row.get("配置生效验证点", "") for marker in ["回显", "生效", "测试", "调用", "预览", "关联"]):
            fail(f"test-data-lifecycle.csv row {index} 配置生效验证点 must describe echo/effective verification")

    mutation_case_ids = {
        row.get("用例 ID", "")
        for row in case_rows
        if row.get("用例 ID")
        and contains_any_marker(
            "\n".join([row.get("测试数据", ""), row.get("操作步骤", ""), row.get("预期结果", ""), row.get("备注", "")]),
            MUTATION_COMMIT_MARKERS,
        )
        and contains_any_marker(
            "\n".join([row.get("测试数据", ""), row.get("操作步骤", ""), row.get("预期结果", ""), row.get("备注", "")]),
            MUTATING_TEST_DATA_MARKERS,
        )
    }
    if mutation_case_ids and not lifecycle_rows:
        fail("test-data-lifecycle.csv must contain rows when function cases create/edit/delete test data")
    missing_lifecycle = sorted((mutation_case_ids | lifecycle_required_ids) - lifecycle_case_ids)
    if missing_lifecycle:
        fail(f"test-data-lifecycle.csv is missing lifecycle linkage for mutation/config cases: {missing_lifecycle[:10]}")

    try:
        validate_plan_function_point_alignment(plan_rows, case_rows, split_ids=parse_id_sequence)
        validate_plan_case_order_alignment(plan_rows, case_rows, split_ids=parse_id_sequence)
        validate_discovery_plan_case_alignment(
            discovery_rows,
            plan_rows,
            case_rows,
            split_ids=parse_id_sequence,
        )
        option_rows_all = csv_rows_with_exact_header(
            run_dir / "selection-option-observations.csv",
            SELECTION_OPTION_OBSERVATIONS_EXPECTED_HEADERS,
            "selection-option-observations.csv",
        )
        option_rows = [row for row in option_rows_all if row.get("选项值") or row.get("元素名称/文案")]
        branch_rows_all = csv_rows_with_exact_header(
            run_dir / "interaction-branch-observations.csv",
            INTERACTION_BRANCH_OBSERVATIONS_EXPECTED_HEADERS,
            "interaction-branch-observations.csv",
        )
        branch_rows = [row for row in branch_rows_all if row.get("分支类别") or row.get("分支动作")]
        validate_selection_option_rows(
            discovery_rows,
            option_rows,
            lambda value: resolved_run_evidence_file(run_dir, value) is not None,
            lambda value: run_evidence_fingerprint(run_dir, value),
        )
        validate_selection_plan_links(option_rows, plan_rows, parse_id_sequence)
        validate_interaction_branch_rows(
            discovery_rows,
            option_rows,
            branch_rows,
            lambda value: resolved_run_evidence_file(run_dir, value) is not None,
            lambda value: run_evidence_fingerprint(run_dir, value),
        )
        validate_branch_plan_links(branch_rows, plan_rows, parse_id_sequence)
        validate_branch_case_grounding(branch_rows, case_rows, parse_id_sequence)
        validate_selection_case_grounding(option_rows, case_rows, parse_id_sequence)
    except ValueError as exc:
        fail(str(exc))


def validate_product_map_sync(
    workbook_data: dict[str, object],
    product_map: Path,
    page_discovery: Path,
    batch_rows: list[dict[str, str]] | None = None,
) -> None:
    discovery_rows = csv_rows_with_exact_header(page_discovery, PAGE_DISCOVERY_EXPECTED_HEADERS, "page-discovery.csv")
    missing_discovery_required = [header for header in PAGE_DISCOVERY_REQUIRED_HEADERS if header not in PAGE_DISCOVERY_EXPECTED_HEADERS]
    if missing_discovery_required:
        fail(f"Internal validator configuration error, missing page discovery required headers: {missing_discovery_required}")
    assert_no_sensitive_csv_values(discovery_rows, "page-discovery.csv")
    if not discovery_rows:
        fail("page-discovery.csv must contain at least one discovery row when product map sync validation is enabled")

    if not product_map.exists():
        fail(f"Product map not found: {product_map}")

    product_page_rows_raw = sheet_rows(product_map, "页面元素地图")
    product_case_rows_raw = sheet_rows(product_map, "用例资产索引")
    product_change_rows_raw = sheet_rows(product_map, "变更记录")
    require_headers(product_page_rows_raw, PRODUCT_MAP_PAGE_ELEMENT_HEADERS, "product-map 页面元素地图")
    require_headers(product_case_rows_raw, PRODUCT_MAP_CASE_INDEX_HEADERS, "product-map 用例资产索引")
    require_headers(product_change_rows_raw, PRODUCT_MAP_CHANGE_HEADERS, "product-map 变更记录")

    product_page_rows = row_dicts(product_page_rows_raw, "product-map 页面元素地图")
    product_case_rows = row_dicts(product_case_rows_raw, "product-map 用例资产索引")
    product_change_rows = row_dicts(product_change_rows_raw, "product-map 变更记录")
    if not product_page_rows:
        fail("product-map 页面元素地图 must contain synced page elements")
    if not product_case_rows:
        fail("product-map 用例资产索引 must contain synced case assets")
    if not product_change_rows:
        fail("product-map 变更记录 must record this product map sync")
    for label, rows in [
        ("product-map 页面元素地图", product_page_rows),
        ("product-map 用例资产索引", product_case_rows),
        ("product-map 变更记录", product_change_rows),
    ]:
        if not any("示例" not in "".join(row.values()) for row in rows):
            fail(f"{label} still only contains sample/template rows and has not been synced with real product facts")
        sample_rows = [index for index, row in enumerate(rows, start=2) if "示例" in "".join(row.values())]
        if sample_rows:
            fail(f"{label} contains sample/template rows after sync: rows {sample_rows[:10]}")
    assert_no_sensitive_values(product_map, PRODUCT_MAP_REQUIRED_REAL_SHEETS)
    validate_table_ranges(product_map, PRODUCT_MAP_REQUIRED_REAL_SHEETS)

    for sheet_name in PRODUCT_MAP_REQUIRED_REAL_SHEETS:
        rows_raw = sheet_rows(product_map, sheet_name)
        rows = row_dicts(rows_raw, f"product-map {sheet_name}")
        if not rows:
            fail(f"product-map {sheet_name} must contain real synced rows")
        sample_rows = [index for index, row in enumerate(rows, start=2) if "示例" in "".join(row.values())]
        if sample_rows:
            fail(f"product-map {sheet_name} contains sample/template rows after sync: rows {sample_rows[:10]}")

    coverage_rows = workbook_data["coverage_rows"]
    case_ids = workbook_data["case_ids"]
    case_titles = workbook_data["case_titles"]
    case_function_points = workbook_data["case_function_points"]
    function_case_count = workbook_data["function_case_count"]
    function_rows = workbook_data["function_rows"]
    assert isinstance(coverage_rows, list)
    assert isinstance(case_ids, set)
    assert isinstance(case_titles, dict)
    assert isinstance(case_function_points, dict)
    assert isinstance(function_case_count, int)
    assert isinstance(function_rows, list)
    run_dir = page_discovery.resolve().parent
    inventory_rows = csv_rows_with_exact_header(
        run_dir / "page-element-inventory.csv",
        PAGE_ELEMENT_INVENTORY_EXPECTED_HEADERS,
        "page-element-inventory.csv",
    )
    try:
        validate_page_element_inventory(
            [row for row in inventory_rows if row.get("元素指纹") or row.get("元素名称/文案")],
            discovery_rows,
            lambda value: resolved_run_evidence_file(run_dir, value) is not None,
        )
        validate_discovery_rows(
            discovery_rows,
            lambda value: resolved_run_evidence_file(run_dir, value) is not None,
            lambda value: run_evidence_fingerprint(run_dir, value),
        )
    except ValueError as exc:
        fail(str(exc))
    validate_sheet_split_artifacts(run_dir, function_rows)
    validate_element_case_plan_and_lifecycle(run_dir, discovery_rows, case_ids, function_rows)

    workbook_elements = {
        normalized_key(row.get("页面/入口", ""), row.get("元素名称/文案", ""))
        for row in coverage_rows
        if row.get("页面/入口") and row.get("元素名称/文案")
    }
    product_elements = {
        normalized_key(row.get("页面/入口", ""), row.get("元素名称/文案", ""))
        for row in product_page_rows
        if row.get("页面/入口") and row.get("元素名称/文案")
    }
    product_case_ids = {row.get("用例ID", "") for row in product_case_rows if row.get("用例ID")}
    if len(product_case_ids) < len(case_ids):
        fail(f"product-map 用例资产索引 has fewer unique case IDs than workbook 功能测试用例: {len(product_case_ids)} < {len(case_ids)}")
    if len(product_elements) < len(workbook_elements):
        fail(f"product-map 页面元素地图 has fewer unique page elements than workbook 页面元素覆盖清单: {len(product_elements)} < {len(workbook_elements)}")

    def coverage_key(row: dict[str, str]) -> tuple[str, str, str]:
        return normalized_key(
            row.get("页面/入口", ""),
            row.get("元素名称/文案", ""),
            row.get("元素类型", ""),
        )

    discovery_coverage: dict[tuple[str, str, str], set[str]] = {}
    for row in discovery_rows:
        if row.get("页面/入口") and row.get("元素名称/文案"):
            discovery_coverage.setdefault(coverage_key(row), set()).update(parse_ids(row.get("关联用例ID", "")))

    def unique_coverage_map(
        source_rows: list[dict[str, str]],
        id_field: str,
        label: str,
    ) -> dict[tuple[str, str, str], set[str]]:
        result: dict[tuple[str, str, str], set[str]] = {}
        for row in source_rows:
            if not row.get("页面/入口") or not row.get("元素名称/文案"):
                continue
            key = coverage_key(row)
            if key in result:
                fail(f"{label} contains duplicate page-element identity: {key}")
            result[key] = parse_ids(row.get(id_field, ""))
        return result

    workbook_coverage = unique_coverage_map(coverage_rows, "覆盖用例 ID", "Workbook 页面元素覆盖清单")
    discovery_leaf_paths = {
        normalize(row.get("最小标题路径", ""))
        for row in discovery_rows
        if row.get("最小标题路径")
    }
    scoped_product_rows = [
        row for row in product_page_rows
        if any(normalize(row.get("模块", "")).endswith(leaf) for leaf in discovery_leaf_paths)
    ]
    product_coverage = unique_coverage_map(scoped_product_rows, "关联用例ID", "product-map 页面元素地图")
    for label, actual, reject_unexpected in [
        ("Workbook 页面元素覆盖清单", workbook_coverage, True),
        ("product-map 页面元素地图", product_coverage, False),
    ]:
        missing_keys = sorted(set(discovery_coverage) - set(actual))
        unexpected_keys = sorted(set(actual) - set(discovery_coverage)) if reject_unexpected else []
        if missing_keys or unexpected_keys:
            fail(
                f"{label} element identities must exactly match page-discovery.csv; "
                f"missing={missing_keys[:10]}, unexpected={unexpected_keys[:10]}"
            )
        mismatched_links = [
            key for key, linked_ids in discovery_coverage.items()
            if actual.get(key, set()) != linked_ids
        ]
        if mismatched_links:
            fail(
                f"{label} linked case sets must exactly match page-discovery.csv for every element: "
                f"{mismatched_links[:10]}"
            )

    passed_batches = {
        row.get("批次ID", ""): row.get("最小标题路径", "").strip()
        for row in (batch_rows or [])
        if is_passed_batch(row)
    }
    passed_batch_numbers = {
        row.get("批次ID", ""): {
            field: positive_int(row.get(field, ""), field, row.get("批次ID", ""))
            for field in ["页面数", "元素总数", "已覆盖元素数"]
        }
        for row in (batch_rows or [])
        if is_passed_batch(row)
    }
    discovery_count_by_batch: dict[str, int] = {}
    generated_count_by_batch: dict[str, int] = {}
    discovery_pages_by_batch: dict[str, set[str]] = {}
    pagination_rows_by_page: dict[tuple[str, str], list[dict[str, str]]] = {}

    for index, row in enumerate(discovery_rows, start=2):
        batch_id = row.get("批次ID", "")
        leaf_path = row.get("最小标题路径", "").strip()
        if not leaf_path:
            fail(f"page-discovery.csv row {index} must include 最小标题路径")
        if passed_batches:
            expected_leaf = passed_batches.get(batch_id)
            if not expected_leaf:
                fail(f"page-discovery.csv row {index} references unknown or unfinished batch: {batch_id}")
            if leaf_path != expected_leaf:
                fail(
                    f"page-discovery.csv row {index} 最小标题路径 must match batch-status.csv for {batch_id}: {leaf_path} != {expected_leaf}"
                )
        page = row.get("页面/入口", "")
        element = row.get("元素名称/文案", "")
        if not page or not element:
            fail(f"page-discovery.csv row {index} must include 页面/入口 and 元素名称/文案")
        discovery_count_by_batch[batch_id] = discovery_count_by_batch.get(batch_id, 0) + 1
        if page:
            discovery_pages_by_batch.setdefault(batch_id, set()).add(page.strip())
        if row.get("是否已生成用例", "") == "是":
            generated_count_by_batch[batch_id] = generated_count_by_batch.get(batch_id, 0) + 1
            assert_dfx_mapping(
                row.get("适用DFX维度", ""),
                row.get("适用DFX场景", ""),
                f"page-discovery.csv row {index}",
            )
        if is_selection_element(row):
            if not row.get("选项取值/输入值"):
                fail(f"page-discovery.csv row {index} selection element must record selected option values: {page} / {element}")
            assert_selection_has_real_choice(row, index, page, element)
            if row.get("是否已生成用例", "") == "是" and not row.get("联动/依赖变化"):
                fail(f"page-discovery.csv row {index} generated selection case must record 联动/依赖变化: {page} / {element}")
            if row.get("是否已生成用例", "") == "是" and not row.get("结果分支/后续状态"):
                fail(f"page-discovery.csv row {index} generated selection case must record 结果分支/后续状态: {page} / {element}")
        if is_input_element(row):
            if not row.get("选项取值/输入值"):
                fail(f"page-discovery.csv row {index} input element must record actual input values: {page} / {element}")
            if row.get("是否已生成用例", "") == "是":
                if not row.get("预期/观察行为"):
                    fail(f"page-discovery.csv row {index} generated input case must record 预期/观察行为: {page} / {element}")
                if not row.get("结果分支/后续状态"):
                    fail(f"page-discovery.csv row {index} generated input case must record 结果分支/后续状态: {page} / {element}")
        if is_create_flow_element(row) and row.get("是否已生成用例", "") == "是":
            if not row.get("选项取值/输入值"):
                fail(f"page-discovery.csv row {index} generated create flow must record actual submitted data: {page} / {element}")
            if not row.get("预期/观察行为"):
                fail(f"page-discovery.csv row {index} generated create flow must record success/failure observation: {page} / {element}")
            result_branch = row.get("结果分支/后续状态", "")
            if not result_branch:
                fail(f"page-discovery.csv row {index} generated create flow must record next page or failure state: {page} / {element}")
            if not has_create_result_branch(result_branch):
                fail(f"page-discovery.csv row {index} generated create flow result must mention success/failure/next state: {page} / {element}")
        if is_mutating_discovery_element(row) and row.get("是否已生成用例", "") == "是":
            assert_mutating_discovery_evidence(row, index, page, element)
        if is_pagination_element(row):
            pagination_rows_by_page.setdefault((batch_id, page), []).append(row)
        if normalized_key(page, element) not in workbook_elements:
            fail(f"page-discovery.csv row {index} element is missing from workbook 页面元素覆盖清单: {page} / {element}")
        if normalized_key(page, element) not in product_elements:
            fail(f"page-discovery.csv row {index} element is missing from product-map 页面元素地图: {page} / {element}")

        generated = row.get("是否已生成用例", "")
        linked_ids = parse_ids(row.get("关联用例ID", ""))
        if generated == "是":
            if not linked_ids:
                fail(f"page-discovery.csv row {index} is generated but missing 关联用例ID")
            unknown_workbook = sorted(linked_ids - case_ids)
            if unknown_workbook:
                fail(f"page-discovery.csv row {index} references case IDs missing from workbook: {unknown_workbook}")
            unknown_product = sorted(linked_ids - product_case_ids)
            if unknown_product:
                fail(f"page-discovery.csv row {index} references case IDs missing from product-map 用例资产索引: {unknown_product}")

    for case_id in sorted(case_ids):
        if case_id not in product_case_ids:
            fail(f"Workbook case ID is missing from product-map 用例资产索引: {case_id}")
        product_rows = [row for row in product_case_rows if row.get("用例ID") == case_id]
        if not any(row.get("用例标题") == case_titles[case_id] for row in product_rows):
            fail(f"product-map 用例资产索引 title mismatch or missing for case ID: {case_id}")
        if not any(row.get("功能点") == case_function_points[case_id] for row in product_rows):
            fail(f"product-map 用例资产索引 功能点 mismatch or missing for case ID: {case_id}")

    synced_changes = [
        row for row in product_change_rows
        if row.get("是否已同步产品版图") == "是" and row.get("变更内容")
    ]
    if not synced_changes:
        fail("product-map 变更记录 must include at least one synced change row with 是否已同步产品版图=是")
    discovery_elements = {
        normalized_key(row.get("页面/入口", ""), row.get("元素名称/文案", ""))
        for row in discovery_rows
        if row.get("页面/入口") and row.get("元素名称/文案")
    }
    missing_discovery_elements = sorted(workbook_elements - discovery_elements)
    if missing_discovery_elements:
        fail(f"Workbook 页面元素覆盖清单 elements missing from page-discovery.csv: {missing_discovery_elements[:10]}")
    for batch_id, numbers in passed_batch_numbers.items():
        discovered = discovery_count_by_batch.get(batch_id, 0)
        generated = generated_count_by_batch.get(batch_id, 0)
        page_count = len(discovery_pages_by_batch.get(batch_id, set()))
        if page_count != numbers["页面数"]:
            fail(
                f"page-discovery.csv distinct pages for {batch_id} must exactly equal batch-status.csv 页面数: "
                f"{page_count} != {numbers['页面数']}"
            )
        if discovered != numbers["元素总数"]:
            fail(
                f"page-discovery.csv element-level rows for {batch_id} must exactly equal batch-status.csv 元素总数: "
                f"{discovered} != {numbers['元素总数']}"
            )
        if generated != numbers["已覆盖元素数"]:
            fail(
                f"page-discovery.csv generated coverage rows for {batch_id} must exactly equal batch-status.csv "
                f"已覆盖元素数: {generated} != {numbers['已覆盖元素数']}"
            )
        minimum_cases = max(1, int(numbers["已覆盖元素数"] * MIN_FUNCTION_CASES_PER_GENERATED_ELEMENT + 0.999))
        if function_case_count < minimum_cases:
            fail(
                f"Workbook 功能测试用例 count is too low for generated page elements in {batch_id}: "
                f"{function_case_count} < {minimum_cases}. DFX must expand element/interaction paths instead of compressing cases."
            )

    for (batch_id, page), rows in pagination_rows_by_page.items():
        generated_rows = [row for row in rows if row.get("是否已生成用例", "") == "是"]
        if not generated_rows:
            continue
        if len(rows) < 3:
            fail(f"page-discovery.csv pagination control must be split into at least 3 rows for {batch_id} / {page}")
        combined = " ".join(" ".join(row.values()) for row in rows)
        required_groups = {
            "page size dropdown": ["每页", "条数", "条/页", "条每页"],
            "page navigation": ["上一页", "下一页", "页码", "跳转"],
            "pagination boundary": ["首页", "末页", "第一页", "最后一页", "边界", "禁用", "空数据"],
        }
        for label, markers in required_groups.items():
            if not any(marker in combined for marker in markers):
                fail(f"page-discovery.csv pagination control for {batch_id} / {page} is missing {label} coverage")


def default_product_map_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "test-assets" / "product-map.xlsx"


def default_page_discovery_path(batch_status: Path | None) -> Path | None:
    if not batch_status:
        return None
    return batch_status.resolve().parent / "page-discovery.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated test design deliverable workbook.")
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--batch-status", type=Path)
    parser.add_argument("--product-map", type=Path)
    parser.add_argument("--page-discovery", type=Path)
    parser.add_argument("--import-workbook", type=Path)
    args = parser.parse_args()

    if not args.page_discovery:
        args.page_discovery = default_page_discovery_path(args.batch_status)
    if args.page_discovery and not args.product_map:
        args.product_map = default_product_map_path()
    if args.page_discovery:
        discovered_batch_status = validate_batch_run_directory_from_page_discovery(args.page_discovery)
        if not args.batch_status:
            args.batch_status = discovered_batch_status

    workbook_data = validate_workbook(args.workbook)
    batch_rows = None
    if args.batch_status:
        batch_rows = validate_batch_status(args.batch_status)
        validate_completed_batch_workbook_semantics(workbook_data, batch_rows)
        validate_batch_artifacts_location(args.batch_status)
        validate_batch_file_consistency(args.batch_status, batch_rows)
        validate_batch_plan(args.batch_status, batch_rows)
        validate_batch_review(args.batch_status, batch_rows)
        validate_batch_import_workbooks(args.batch_status, batch_rows)
    if args.import_workbook:
        if not args.batch_status:
            fail("--import-workbook requires --batch-status so module hierarchy can be checked against batch-scope.json")
        expected_module_path = batch_scope_module_path(args.batch_status) if args.batch_status else None
        validate_import_workbook(args.import_workbook, workbook_data, expected_module_path)
    if bool(args.product_map) != bool(args.page_discovery):
        fail("--product-map and --page-discovery must be provided together")
    if args.product_map and args.page_discovery:
        validate_catalog(args.product_map, require_existing=True)
        validate_product_map_sync(workbook_data, args.product_map, args.page_discovery, batch_rows)
    print("OK: test design deliverable quality checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
