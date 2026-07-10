# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from copy import copy, deepcopy
from datetime import date
from pathlib import Path

try:
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment
    from openpyxl.utils import get_column_letter, range_boundaries
except ImportError as exc:  # pragma: no cover - depends on local runtime packaging.
    raise SystemExit(
        "ERROR: openpyxl is required. Run this script in the CodeBuddy/Codex spreadsheet runtime "
        "or install openpyxl in the active Python environment."
    ) from exc


FORMAL_FUNCTION_SHEET = "功能测试用例"
IMPORT_MULTILINE_FIELDS = ["测试步骤描述", "测试步骤预期结果", "前置条件", "测试用例说明", "备注"]
FORMAL_MULTILINE_FIELDS = {
    "功能测试用例": ["前置条件", "测试数据", "操作步骤", "预期结果", "备注"],
    "性能测试设计": ["前置条件/数据准备", "执行步骤", "监控指标", "通过标准", "风险备注"],
    "风险与待确认问题": ["描述", "影响范围", "建议处理方式"],
    "自动化建议": ["建议说明", "前置条件", "维护要求"],
    "页面元素覆盖清单": ["业务依据/规则来源", "待确认问题/备注"],
}

IMPORT_AUTO_FIELDS = {"测试用例系统编号", "作者"}
IMPORT_ALLOWED_VALUES = {
    "测试类型": {"功能测试", "性能规格测试", "可靠性测试", "兼容性测试", "可维护性测试", "安全性测试", "易用性测试"},
    "测试用例级别": {"L1", "L2", "L3", "L4"},
    "执行方式": {"自动化", "手动"},
}
IMPORT_EXCLUDED_TEST_TYPES = {"性能规格测试"}
IMPORT_EXCLUDED_DFX_DIMENSIONS = {"DFP性能"}
IMPORT_EXCLUDED_DFX_EXTREME_SCENARIOS = {"压力极限", "资源耗尽", "并发极限"}


def header_map(ws, header_row: int = 1) -> dict[str, int]:
    headers: dict[str, int] = {}
    for cell in ws[header_row]:
        if cell.value:
            headers[str(cell.value).strip()] = cell.column
    return headers


def row_dict(ws, headers: dict[str, int], row_index: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, column in headers.items():
        value = ws.cell(row=row_index, column=column).value
        result[name] = "" if value is None else str(value).strip()
    return result


def non_empty_rows(ws, headers: dict[str, int]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row_index in range(2, ws.max_row + 1):
        row = row_dict(ws, headers, row_index)
        if any(value for value in row.values()):
            rows.append(row)
    return rows


def copy_row_style(ws, source_row: int, target_row: int) -> None:
    for source_cell in ws[source_row]:
        target_cell = ws.cell(row=target_row, column=source_cell.column)
        if source_cell.has_style:
            target_cell._style = copy(source_cell._style)
        if source_cell.number_format:
            target_cell.number_format = source_cell.number_format
        if source_cell.alignment:
            target_cell.alignment = copy(source_cell.alignment)
    ws.row_dimensions[target_row].height = ws.row_dimensions[source_row].height


def copy_cell_format(source_cell, target_cell) -> None:
    if source_cell.has_style:
        target_cell._style = copy(source_cell._style)
    if source_cell.number_format:
        target_cell.number_format = source_cell.number_format
    if source_cell.alignment:
        target_cell.alignment = copy(source_cell.alignment)
    if source_cell.protection:
        target_cell.protection = copy(source_cell.protection)


def copy_template_row_format(template_ws, target_ws, template_row: int, target_row: int) -> None:
    max_col = min(target_ws.max_column, template_ws.max_column)
    for column in range(1, max_col + 1):
        copy_cell_format(template_ws.cell(row=template_row, column=column), target_ws.cell(row=target_row, column=column))
    target_ws.row_dimensions[target_row].height = template_ws.row_dimensions[template_row].height


def copy_column_dimensions(template_ws, target_ws) -> None:
    for key, dimension in template_ws.column_dimensions.items():
        target_dimension = target_ws.column_dimensions[key]
        target_dimension.width = dimension.width
        target_dimension.hidden = dimension.hidden
        target_dimension.bestFit = dimension.bestFit


def extend_validation_ranges(ws, max_row: int) -> None:
    if max_row < 2:
        return
    for validation in ws.data_validations.dataValidation:
        ranges: list[str] = []
        for cell_range in validation.sqref.ranges:
            min_col, min_row, max_col, old_max_row = range_boundaries(str(cell_range))
            if min_row <= 2 <= old_max_row:
                old_max_row = max(old_max_row, max_row)
            ranges.append(
                f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{old_max_row}"
            )
        validation.sqref = " ".join(ranges)


def apply_template_sheet_format(template_ws, target_ws) -> None:
    copy_column_dimensions(template_ws, target_ws)
    if target_ws.max_row >= 2 and template_ws.max_row >= 2:
        for row_index in range(2, target_ws.max_row + 1):
            copy_template_row_format(template_ws, target_ws, 2, row_index)
    target_ws.data_validations = deepcopy(template_ws.data_validations)
    extend_validation_ranges(target_ws, max(target_ws.max_row, 200))


def apply_template_workbook_format(target_wb, template_wb) -> None:
    for sheet_name in target_wb.sheetnames:
        if sheet_name not in template_wb.sheetnames:
            continue
        apply_template_sheet_format(template_wb[sheet_name], target_wb[sheet_name])


def set_wrap(ws, headers: dict[str, int], row_index: int, field_names: list[str]) -> None:
    for field in field_names:
        column = headers.get(field)
        if not column:
            continue
        cell = ws.cell(row=row_index, column=column)
        cell.alignment = Alignment(
            horizontal=cell.alignment.horizontal,
            vertical="top",
            text_rotation=cell.alignment.text_rotation,
            wrap_text=True,
            shrink_to_fit=cell.alignment.shrink_to_fit,
            indent=cell.alignment.indent,
        )


def normalize_case_level(priority: str) -> str:
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


def normalize_test_type(value: str) -> str:
    if value in IMPORT_ALLOWED_VALUES["测试类型"]:
        return value
    if "性能" in value:
        return "性能规格测试"
    if "兼容" in value:
        return "兼容性测试"
    if "安全" in value or "权限" in value:
        return "安全性测试"
    if "可靠" in value or "稳定" in value:
        return "可靠性测试"
    if "易用" in value:
        return "易用性测试"
    if "维护" in value:
        return "可维护性测试"
    return "功能测试"


def split_dfx_values(text: str) -> set[str]:
    normalized = (text or "").replace("，", ",").replace("；", ",").replace("、", ",").replace("/", ",")
    return {item.strip() for item in normalized.split(",") if item.strip()}


def is_importable_function_case(case: dict[str, str]) -> bool:
    test_type = normalize_test_type(case.get("测试类型", ""))
    dfx_dimensions = split_dfx_values(case.get("DFX维度", ""))
    dfx_scenarios = split_dfx_values(case.get("DFX场景", ""))
    if test_type in IMPORT_EXCLUDED_TEST_TYPES:
        return False
    if dfx_dimensions & IMPORT_EXCLUDED_DFX_DIMENSIONS:
        return False
    if "DFX极端" in dfx_dimensions and dfx_scenarios & IMPORT_EXCLUDED_DFX_EXTREME_SCENARIOS:
        return False
    return True


def execution_mode(row: dict[str, str]) -> str:
    note = "".join([row.get("备注", ""), row.get("是否适合自动化", "")])
    automation_markers = ["自动化资产", "脚本", "流水线", "API自动化", "UI自动化", "已实现"]
    if "自动化" in note and any(marker in note for marker in automation_markers):
        return "自动化"
    return "手动"


def module_names(module_path: str) -> list[str]:
    parts = [part.strip() for part in module_path.replace("/", ">").split(">") if part.strip()]
    return (parts + [""] * 5)[:5]


def canonical_module_parts(module_path: str, product_name: str | None = None) -> list[str]:
    parts = [part.strip() for part in module_path.replace("\\", ">").replace("/", ">").split(">") if part.strip()]
    if product_name and parts and parts[0] == product_name.strip():
        parts = parts[1:]
    return parts


def deliverable_names(module_path: str, product_name: str | None = None) -> tuple[str, str, str]:
    parts = canonical_module_parts(module_path, product_name)
    stem = safe_filename(">".join(parts) if parts else module_path)
    return stem, f"{stem}_测试设计.xlsx", f"{stem}_导入用例.xlsx"


def module_leaf_name(module_path: str) -> str:
    parts = [part.strip() for part in module_path.replace("/", ">").split(">") if part.strip()]
    return parts[-1] if parts else module_path


def clear_data_rows(ws, start_row: int = 2) -> None:
    if ws.max_row > start_row:
        ws.delete_rows(start_row + 1, ws.max_row - start_row)
    if ws.max_row >= start_row:
        for cell in ws[start_row]:
            cell.value = None


def worksheet_used_range(ws) -> str:
    return f"A1:{get_column_letter(ws.max_column)}{max(ws.max_row, 1)}"


def remove_worksheet_tables_and_refresh_filter(ws) -> None:
    for table_name in list(ws.tables.keys()):
        del ws.tables[table_name]
    sheet_ref = worksheet_used_range(ws)
    if ws.max_row > 1 and ws.max_column > 1:
        ws.auto_filter.ref = sheet_ref


def remove_workbook_tables_and_refresh_filters(wb) -> None:
    for ws in wb.worksheets:
        remove_worksheet_tables_and_refresh_filter(ws)


def relative_project_path(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def safe_filename(value: str) -> str:
    cleaned = value.replace("\\", ">").replace("/", ">")
    for char in '<>:"|?*':
        cleaned = cleaned.replace(char, "_")
    cleaned = "_".join(part.strip() for part in cleaned.split("_") if part.strip())
    cleaned = cleaned.replace(" ", "")
    return cleaned or "测试设计"


def copy_workbook(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        return
    shutil.copy2(source, target)


def write_mapped_row(ws, headers: dict[str, int], row_index: int, values: dict[str, str]) -> None:
    copy_row_style(ws, 2 if ws.max_row >= 2 else 1, row_index)
    for field, value in values.items():
        column = headers.get(field)
        if column:
            ws.cell(row=row_index, column=column, value=value)


def append_mapped_row(ws, values: dict[str, str]) -> None:
    headers = header_map(ws)
    row_index = ws.max_row + 1 if ws.max_row >= 1 else 2
    write_mapped_row(ws, headers, row_index, values)


def remove_rows_containing(ws, needles: list[str]) -> None:
    if not needles:
        return
    for row_index in range(ws.max_row, 1, -1):
        values = ["" if cell.value is None else str(cell.value) for cell in ws[row_index]]
        joined = "\n".join(values)
        if any(needle and needle in joined for needle in needles) or "示例" in joined:
            ws.delete_rows(row_index, 1)


def update_batch_status_paths(batch_status: Path, batch_id: str | None, archive_rel: str, import_rel: str) -> list[dict[str, str]]:
    if not batch_status:
        return []
    with batch_status.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        headers = reader.fieldnames or []
        rows = list(reader)
    if not headers:
        raise ValueError(f"batch-status.csv has no header row: {batch_status}")
    required = {"批次ID", "归档路径", "导入文件路径", "导入文件已生成"}
    missing = sorted(required - set(headers))
    if missing:
        raise ValueError(f"batch-status.csv is missing required finalize columns: {missing}")
    target_rows = [row for row in rows if not batch_id or row.get("批次ID") == batch_id]
    if not target_rows:
        raise ValueError(f"No matching batch row found for batch_id={batch_id!r}")
    changes: list[dict[str, str]] = []
    for row in target_rows:
        changes.append(
            {
                "批次ID": row.get("批次ID", ""),
                "旧归档路径": row.get("归档路径", ""),
                "旧导入文件路径": row.get("导入文件路径", ""),
                "归档路径": archive_rel,
                "导入文件路径": import_rel,
            }
        )
        row["归档路径"] = archive_rel
        row["导入文件路径"] = import_rel
        row["导入文件已生成"] = "是"
    with batch_status.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return changes


def sync_batch_markdown_paths(batch_status: Path, changes: list[dict[str, str]]) -> None:
    for markdown_name in ["batch-plan.md", "batch-review.md"]:
        markdown_path = batch_status.resolve().parent / markdown_name
        if not markdown_path.exists():
            continue
        text = markdown_path.read_text(encoding="utf-8-sig")
        for change in changes:
            for old_key, new_key in [("旧归档路径", "归档路径"), ("旧导入文件路径", "导入文件路径")]:
                old_value = change.get(old_key, "")
                new_value = change.get(new_key, "")
                if old_value and new_value:
                    text = text.replace(old_value, new_value)
            if change["归档路径"] not in text or change["导入文件路径"] not in text:
                text += (
                    "\n\n## 交付收口路径\n"
                    f"- {change['批次ID']} 归档路径：{change['归档路径']}\n"
                    f"- {change['批次ID']} 导入文件路径：{change['导入文件路径']}\n"
                )
        markdown_path.write_text(text, encoding="utf-8")


def cleanup_batch_artifacts(batch_status: Path | None) -> None:
    if not batch_status:
        return
    pycache = batch_status.resolve().parent / "artifacts" / "scripts" / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)


def copy_template_if_missing(source: Path, target: Path) -> bool:
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def write_single_csv_row(path: Path, values: dict[str, str]) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        headers = next(reader, [])
    if not headers:
        raise ValueError(f"CSV template has no header row: {path}")
    row = {header: "" for header in headers}
    for key, value in values.items():
        if key in row:
            row[key] = value
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=headers)
        writer.writeheader()
        writer.writerow(row)


CSV_REQUIRED_FILES = {
    "batch-status.csv": "batch-status-template.csv",
    "page-discovery.csv": "page-discovery-template.csv",
    "element-case-plan.csv": "element-case-plan-template.csv",
    "test-data-lifecycle.csv": "test-data-lifecycle-template.csv",
}

FUNCTION_CASE_PART_RE = re.compile(r"^function_cases_part_\d{3}\.json$")
MAX_FUNCTION_CASES_PER_PART = 10
FUNCTION_CASE_MANIFEST = "function_cases_manifest.json"
SHEET_DATA_FILES = [
    "overview.json",
    "requirements.json",
    "scenarios.json",
    "performance.json",
    "risks.json",
    "automation.json",
    "page_elements.json",
]
FUNCTION_CASE_REQUIRED_FIELDS = [
    "用例 ID",
    "Story ID/需求 ID",
    "模块",
    "功能点",
    "用例标题",
    "优先级",
    "测试类型",
    "DFX维度",
    "DFX场景",
    "前置条件",
    "测试数据",
    "操作步骤",
    "预期结果",
    "实际结果",
    "执行状态",
    "是否适合自动化",
    "关联风险",
    "备注",
]
FUNCTION_CASE_FORBIDDEN_FIELDS = {
    "用例编号",
    "用侊 ID",
    "用侊标题",
    "场景类型",
    "正向/反向",
    "steps",
    "expected",
    "title",
    "case_id",
    "id",
}
PLACEHOLDER_CASE_IDS = {"", "TC-A2A-XXX", "TC-XXX", "TODO", "TBD"}
ENGLISH_TEMPLATE_MARKERS = [
    "Open browser",
    "navigate to",
    "Verify page",
    "Operate element",
    "Execute extended scenario",
    "Extended scenario",
    "passes",
    "behaves as expected",
]

INTERACTIVE_ELEMENT_MARKERS = [
    "按钮",
    "输入",
    "下拉",
    "选择",
    "单选",
    "复选",
    "开关",
    "分页",
    "页码",
    "弹窗",
    "表格",
    "链接",
    "上传",
    "编辑",
    "删除",
    "保存",
    "创建",
    "新增",
    "添加",
    "提交",
    "测试",
    "搜索",
    "筛选",
]


def read_csv_exact(path: Path, expected_headers: list[str], label: str) -> list[dict[str, str]]:
    if not path.exists():
        raise ValueError(f"{label} not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{label} has no header row: {path}") from exc
        if headers != expected_headers:
            raise ValueError(f"{label} header must match the standard template exactly. Expected {expected_headers}, got {headers}")
        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != len(headers):
                raise ValueError(f"{label} row {index} column count mismatch: expected {len(headers)}, got {len(row)}")
            rows.append({header: row[col].strip() for col, header in enumerate(headers)})
    return rows


def template_headers(templates_dir: Path, template_name: str) -> list[str]:
    template_path = templates_dir / template_name
    if not template_path.exists():
        raise ValueError(f"Batch template not found: {template_path}")
    with template_path.open("r", encoding="utf-8-sig", newline="") as fp:
        return next(csv.reader(fp))


def split_plan_values(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、;；/\s]+", text or "") if item.strip()]


def parse_positive_int(value: str, label: str) -> int:
    try:
        number = int(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a positive integer, got: {value}") from exc
    if number <= 0:
        raise ValueError(f"{label} must be greater than 0, got: {value}")
    return number


def is_yes(value: str) -> bool:
    return (value or "").strip() in {"是", "Y", "Yes", "yes", "true", "True", "1"}


def contains_any(text: str, markers: list[str]) -> bool:
    return any(marker in (text or "") for marker in markers)


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def is_template_or_empty_row(row: dict[str, str], meaningful_fields: list[str]) -> bool:
    return not any((row.get(field) or "").strip() for field in meaningful_fields)


def is_interactive_discovery_row(row: dict[str, str]) -> bool:
    combined = "\n".join(
        [
            row.get("元素名称/文案", ""),
            row.get("元素类型", ""),
            row.get("交互方式", ""),
            row.get("完整点击路径", ""),
        ]
    )
    return contains_any(combined, INTERACTIVE_ELEMENT_MARKERS)


def dfx_pair_count(dimensions_text: str, scenarios_text: str) -> int:
    dimensions = split_plan_values(dimensions_text)
    scenarios = split_plan_values(scenarios_text)
    if not dimensions or not scenarios:
        return 0
    if len(dimensions) == len(scenarios):
        return len(list(zip(dimensions, scenarios)))
    return max(len(dimensions), len(scenarios))


def element_is_planned(discovery_name: str, plan_rows: list[dict[str, str]]) -> bool:
    name = normalized_text(discovery_name)
    if not name:
        return True
    for row in plan_rows:
        planned = normalized_text(
            "\n".join(
                [
                    row.get("元素名称/文案", ""),
                    row.get("功能点", ""),
                    row.get("交互方式", ""),
                    row.get("业务路径", ""),
                ]
            )
        )
        if name in planned or planned in name:
            return True
    return False


def minimum_cases_for_plan_row(row: dict[str, str]) -> int:
    element_type = row.get("元素类型", "")
    interaction = row.get("交互方式", "")
    function_point = row.get("功能点", "")
    direction = row.get("测试设计方向", "")
    element_name = row.get("元素名称/文案", "")
    dimensions = set(split_plan_values(row.get("适用DFX维度", "")))
    scenarios = set(split_plan_values(row.get("适用DFX场景", "")))
    combined = "\n".join([element_type, interaction, function_point, direction, element_name])

    minimum = 1
    if contains_any(combined, ["搜索", "筛选"]):
        minimum = max(minimum, 4)
    if contains_any(combined, ["输入", "文本框", "数字", "邮箱", "手机号", "URL", "地址"]):
        minimum = max(minimum, 3)
    if contains_any(combined, ["下拉", "选择", "单选", "复选", "级联"]):
        minimum = max(minimum, 4)
    if contains_any(combined, ["分页", "翻页", "页码", "每页", "跳页"]):
        minimum = max(minimum, 5)
    if contains_any(combined, ["弹窗", "抽屉", "对话框"]):
        minimum = max(minimum, 3)
    if contains_any(combined, ["新增", "创建", "添加", "接入", "保存", "提交"]):
        minimum = max(minimum, 5)
    if contains_any(combined, ["编辑", "修改"]):
        minimum = max(minimum, 4)
    if "删除" in combined:
        minimum = max(minimum, 3)
    if contains_any(combined, ["表格", "行操作", "批量"]):
        minimum = max(minimum, 3)
    if contains_any(combined, ["密钥", "Token", "密码", "鉴权", "权限"]):
        minimum = max(minimum, 4)
    if is_yes(row.get("是否涉及CRUD闭环", "")):
        minimum = max(minimum, 5)
    if is_yes(row.get("是否涉及配置生效", "")):
        minimum = max(minimum, 4)

    if {"边界值", "异常输入", "逆向操作"} & scenarios:
        minimum = max(minimum, 3)
    if dimensions & {"DFS安全", "DFR可靠", "DFU可用", "DFB业务"}:
        minimum += 1
    if scenarios & {"数据一致", "幂等性", "权限控制", "数据脱敏", "错误提示", "业务流程", "数据准确"}:
        minimum += 1
    if dimensions & {"DFP性能", "DFX极端"}:
        # 性能和极端场景不进入功能用例，但必须进入性能设计/风险/自动化建议。
        minimum = max(minimum, 1)

    return max(minimum, dfx_pair_count(row.get("适用DFX维度", ""), row.get("适用DFX场景", "")) or 1)


def planned_case_id_count(value: str) -> int:
    ids = split_plan_values(value)
    return len(ids)


def numbered_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def validate_numbered_sequence(text: str, label: str, minimum: int) -> None:
    lines = numbered_lines(text)
    if len(lines) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} numbered lines")
    expected = 1
    for line in lines:
        match = re.match(r"^(\d+)\.\s*\S+", line)
        if not match:
            raise ValueError(f"{label} must use numbered lines like '1. ...': {line}")
        number = int(match.group(1))
        if number != expected:
            raise ValueError(f"{label} numbering must be continuous; expected {expected}, got {number}: {line}")
        expected += 1


def validate_case_steps_and_expected(case: dict[str, str], label: str) -> None:
    steps = str(case.get("操作步骤", "") or "")
    expected = str(case.get("预期结果", "") or "")
    precondition = str(case.get("前置条件", "") or "")
    combined = "\n".join([precondition, steps, expected, str(case.get("备注", "") or "")])

    if any(marker in combined for marker in ENGLISH_TEMPLATE_MARKERS):
        raise ValueError(f"{label} contains English placeholder/template text; generate concrete Chinese executable steps and expectations")

    validate_numbered_sequence(precondition, f"{label} 前置条件", 2)
    validate_numbered_sequence(steps, f"{label} 操作步骤", 4)
    validate_numbered_sequence(expected, f"{label} 预期结果", 3)

    first_steps = "\n".join(numbered_lines(steps)[:3])
    entry_markers = ["登录", "打开系统", "访问系统", "进入系统", "打开平台", "访问平台", "进入平台", "<product_login_url>"]
    navigation_markers = ["一级", "二级", "三级", "菜单", "模块", "导航", "路径", ">", "页面"]
    if not any(marker in first_steps for marker in entry_markers):
        raise ValueError(f"{label} 操作步骤 must start from system/project entry")
    if not any(marker in first_steps for marker in navigation_markers):
        raise ValueError(f"{label} 操作步骤 must include complete menu/module navigation")

    if re.search(r"\b点(搜索|保存|删除|确定|确认|取消)\b", steps):
        raise ValueError(f"{label} 操作步骤 contains overly terse wording like '点搜索'; write the full control name and action")
    if any(marker in steps for marker in ["操作元素", "扩展场景", "基本验证", "Extended"]):
        raise ValueError(f"{label} 操作步骤 contains generic generated wording; write concrete page operations")
    if any(marker in expected for marker in ["behaves as expected", "passes", "符合预期", "正常显示"]) and len(numbered_lines(expected)) <= 3:
        raise ValueError(f"{label} 预期结果 is too generic; write observable page/data/state outcomes")


def validate_function_case_schema(case: dict[str, object], label: str, planned_ids: set[str] | None = None) -> None:
    if not isinstance(case, dict):
        raise ValueError(f"{label} must be a JSON object")
    keys = set(case)
    forbidden = sorted(keys & FUNCTION_CASE_FORBIDDEN_FIELDS)
    if forbidden:
        raise ValueError(f"{label} contains forbidden/deprecated fields: {forbidden}; use the standard function case schema")
    missing = [field for field in FUNCTION_CASE_REQUIRED_FIELDS if field not in case]
    if missing:
        raise ValueError(f"{label} is missing required fields: {missing}")
    extra = sorted(keys - set(FUNCTION_CASE_REQUIRED_FIELDS))
    if extra:
        raise ValueError(f"{label} contains extra fields not allowed by the standard schema: {extra}")

    normalized = {field: "" if case.get(field) is None else str(case.get(field)).strip() for field in FUNCTION_CASE_REQUIRED_FIELDS}
    case_id = normalized["用例 ID"]
    if case_id in PLACEHOLDER_CASE_IDS or "XXX" in case_id:
        raise ValueError(f"{label} must use a concrete 用例 ID, got: {case_id}")
    if planned_ids is not None and planned_ids and case_id not in planned_ids:
        raise ValueError(f"{label} 用例 ID is not declared in element-case-plan.csv 计划用例ID: {case_id}")

    function_point = normalized["功能点"]
    title = normalized["用例标题"]
    if not function_point:
        raise ValueError(f"{label} must fill 功能点")
    if not title.startswith(f"{function_point}-"):
        raise ValueError(f"{label} 用例标题 must use 功能点-当前标题 format: {title}")
    for field in ["模块", "优先级", "测试类型", "DFX维度", "DFX场景", "测试数据"]:
        if not normalized[field]:
            raise ValueError(f"{label} must fill {field}")
    if normalized["测试类型"] == "性能规格测试" or normalized["DFX维度"] == "DFP性能":
        raise ValueError(f"{label} must not put performance scenarios into function case shards")

    validate_case_steps_and_expected(normalized, label)


def validate_function_case_part(path: Path, planned_ids: set[str] | None = None) -> int:
    if not FUNCTION_CASE_PART_RE.match(path.name):
        raise ValueError(f"{path} must use function_cases_part_001.json naming")
    with path.open("r", encoding="utf-8-sig") as fp:
        data = json.load(fp)
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError(f"{path} must contain a list or an object with a cases list")
    if len(cases) > MAX_FUNCTION_CASES_PER_PART:
        raise ValueError(f"{path} contains {len(cases)} cases; each function_cases_part_*.json must contain at most {MAX_FUNCTION_CASES_PER_PART}")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        validate_function_case_schema(case, f"{path.name} case {index}", planned_ids)
        case_id = str(case.get("用例 ID", "")).strip()
        if case_id in seen_ids:
            raise ValueError(f"{path.name} has duplicate 用例 ID: {case_id}")
        seen_ids.add(case_id)
    return len(cases)


def planned_case_ids(plan_rows: list[dict[str, str]]) -> set[str]:
    ids: set[str] = set()
    for row in plan_rows:
        ids.update(split_plan_values(row.get("计划用例ID", "")))
    return ids


def manifest_parts(data_dir: Path) -> list[Path]:
    manifest = data_dir / FUNCTION_CASE_MANIFEST
    if not manifest.exists():
        raise ValueError(f"artifacts/data must contain {FUNCTION_CASE_MANIFEST}; Excel assembly must read the manifest, not glob stale shards")
    with manifest.open("r", encoding="utf-8-sig") as fp:
        data = json.load(fp)
    raw_parts = data.get("parts") if isinstance(data, dict) else data
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError(f"{manifest} must contain a non-empty parts list")
    result: list[Path] = []
    for item in raw_parts:
        name = str(item).strip()
        if not FUNCTION_CASE_PART_RE.match(name):
            raise ValueError(f"{manifest} contains invalid part name: {name}; use function_cases_part_001.json")
        path = data_dir / name
        if not path.exists():
            raise ValueError(f"{manifest} references missing function case shard: {name}")
        result.append(path)
    declared = {path.name for path in result}
    stale = sorted(path.name for path in data_dir.glob("function_cases_part_*.json") if path.name not in declared)
    if stale:
        raise ValueError(f"artifacts/data contains stale function case shards not listed in {FUNCTION_CASE_MANIFEST}: {stale[:10]}")
    return result


def prepare_function_case_generation(run_dir: Path) -> None:
    data_dir = run_dir.resolve() / "artifacts" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for pattern in ["function_cases_part_*.json", FUNCTION_CASE_MANIFEST]:
        for path in data_dir.glob(pattern):
            if path.is_file():
                removed.append(path.name)
                path.unlink()
    print(f"OK: prepared function case generation under {data_dir}; removed {len(removed)} stale file(s).")


def validate_batch_artifacts(run_dir: Path, phase: str = "cases") -> None:
    run_dir = run_dir.resolve()
    if not run_dir.exists():
        raise ValueError(f"Batch run directory not found: {run_dir}")
    templates_dir = run_dir.parent / "templates"
    expected_headers = {
        csv_name: template_headers(templates_dir, template_name)
        for csv_name, template_name in CSV_REQUIRED_FILES.items()
    }

    batch_rows = read_csv_exact(run_dir / "batch-status.csv", expected_headers["batch-status.csv"], "batch-status.csv")
    discovery_rows = read_csv_exact(run_dir / "page-discovery.csv", expected_headers["page-discovery.csv"], "page-discovery.csv")
    plan_rows = read_csv_exact(run_dir / "element-case-plan.csv", expected_headers["element-case-plan.csv"], "element-case-plan.csv")
    lifecycle_rows = read_csv_exact(run_dir / "test-data-lifecycle.csv", expected_headers["test-data-lifecycle.csv"], "test-data-lifecycle.csv")

    real_discovery_rows = [
        row for row in discovery_rows
        if not is_template_or_empty_row(row, ["页面/入口", "元素名称/文案", "元素类型", "交互方式"])
    ]
    if phase in {"discovery", "plan", "cases"} and not real_discovery_rows:
        raise ValueError("page-discovery.csv must contain real page elements before continuing")

    interactive_rows = [row for row in real_discovery_rows if is_interactive_discovery_row(row)]
    if phase in {"discovery", "plan", "cases"} and not interactive_rows:
        raise ValueError("page-discovery.csv must contain clickable/input/selectable/testable elements, not only static text")

    if phase in {"plan", "cases"}:
        real_plan_rows = [
            row for row in plan_rows
            if not is_template_or_empty_row(row, ["页面/入口", "功能点", "元素名称/文案", "元素类型", "测试设计方向", "计划用例ID"])
        ]
        if not real_plan_rows:
            raise ValueError("element-case-plan.csv must contain real element-driven case plans before writing cases")

        missing_plan = [
            row.get("元素名称/文案", "")
            for row in interactive_rows
            if row.get("元素名称/文案", "").strip() and not element_is_planned(row.get("元素名称/文案", ""), real_plan_rows)
        ]
        if missing_plan:
            preview = ", ".join(missing_plan[:10])
            raise ValueError(f"element-case-plan.csv is missing interactive page elements from page-discovery.csv: {preview}")

        computed_min_total = 0
        declared_total = 0
        for index, row in enumerate(real_plan_rows, start=2):
            declared = parse_positive_int(row.get("应生成用例数", ""), f"element-case-plan.csv row {index} 应生成用例数")
            minimum = minimum_cases_for_plan_row(row)
            if declared < minimum:
                raise ValueError(
                    f"element-case-plan.csv row {index} declares {declared} case(s), "
                    f"but element type + DFX minimum requires at least {minimum}: "
                    f"{row.get('元素名称/文案', '')} / {row.get('测试设计方向', '')}"
                )
            if planned_case_id_count(row.get("计划用例ID", "")) < declared:
                raise ValueError(f"element-case-plan.csv row {index} must provide at least {declared} planned case ID(s)")
            computed_min_total += minimum
            declared_total += declared

        if declared_total < len(interactive_rows):
            raise ValueError(
                f"element-case-plan.csv declares only {declared_total} cases for {len(interactive_rows)} interactive elements; "
                "DFX扩展后用例数不得低于可交互元素数"
            )

        has_crud_or_config = any(
            is_yes(row.get("是否涉及CRUD闭环", "")) or is_yes(row.get("是否涉及配置生效", ""))
            or contains_any(
                "\n".join([row.get("功能点", ""), row.get("元素名称/文案", ""), row.get("测试设计方向", "")]),
                ["新增", "创建", "添加", "保存", "提交", "编辑", "修改", "删除", "配置", "生效"],
            )
            for row in real_plan_rows
        )
        real_lifecycle_rows = [
            row for row in lifecycle_rows
            if not is_template_or_empty_row(row, ["测试数据ID/名称", "创建结果", "查看结果", "编辑结果", "删除确认结果", "清理状态"])
        ]
        if has_crud_or_config:
            if not real_lifecycle_rows:
                raise ValueError("test-data-lifecycle.csv must record AI_TEST/CODEX_TEST CRUD/config lifecycle before writing cases")
            for index, row in enumerate(real_lifecycle_rows, start=2):
                combined = "\n".join(row.values())
                if not contains_any(combined, ["AI_TEST", "CODEX_TEST", "用户提供测试数据"]):
                    raise ValueError(f"test-data-lifecycle.csv row {index} must bind to AI_TEST/CODEX_TEST or user-provided test data")

    artifacts_dir = run_dir / "artifacts"
    scripts_dir = artifacts_dir / "scripts"
    data_dir = artifacts_dir / "data"
    screenshots_dir = artifacts_dir / "screenshots"
    for required_dir in [artifacts_dir, scripts_dir, data_dir, screenshots_dir]:
        if phase in {"cases"} and not required_dir.exists():
            raise ValueError(f"Required batch artifact directory is missing: {required_dir}")

    misplaced_parts = sorted(artifacts_dir.glob("function_cases_part_*.json")) if artifacts_dir.exists() else []
    if misplaced_parts:
        raise ValueError("function_cases_part_*.json must be written under artifacts/data, not artifacts root")

    if phase == "cases":
        parts = manifest_parts(data_dir)
        planned_ids = planned_case_ids(plan_rows)
        case_count = sum(validate_function_case_part(path, planned_ids) for path in parts)
        missing_sheet_files = [name for name in SHEET_DATA_FILES if not (data_dir / name).exists()]
        if missing_sheet_files:
            raise ValueError(f"artifacts/data is missing sheet-split files: {missing_sheet_files}")
        if plan_rows:
            declared_total = sum(
                int(row.get("应生成用例数", "0"))
                for row in plan_rows
                if row.get("应生成用例数", "").isdigit()
            )
            if declared_total and case_count < declared_total:
                raise ValueError(f"function case shards contain {case_count} cases, fewer than element-case-plan declared {declared_total}")

    if batch_rows:
        first_status = batch_rows[0]
        if phase in {"plan", "cases"}:
            all_zero = all((first_status.get(field, "") or "0") == "0" for field in ["页面数", "元素总数", "功能用例数"])
            if first_status.get("状态") == "待开始" or all_zero:
                raise ValueError("batch-status.csv is still in the initial state; update page/element/case counts before continuing")

    print(f"OK: batch artifacts passed {phase} gate: {run_dir}")


def init_batch_run(project_root: Path, run_id: str, module_path: str, batch_id: str, product_name: str | None = None) -> Path:
    run_dir = project_root / "docs" / "test-assets" / "batch-runs" / run_id
    templates_dir = project_root / "docs" / "test-assets" / "batch-runs" / "templates"
    required_templates = {
        "batch-plan.md": templates_dir / "batch-plan-template.md",
        "batch-status.csv": templates_dir / "batch-status-template.csv",
        "batch-review.md": templates_dir / "batch-review-template.md",
        "page-discovery.csv": templates_dir / "page-discovery-template.csv",
        "element-case-plan.csv": templates_dir / "element-case-plan-template.csv",
        "test-data-lifecycle.csv": templates_dir / "test-data-lifecycle-template.csv",
    }
    missing = [str(path) for path in required_templates.values() if not path.exists()]
    if missing:
        raise ValueError(f"Batch template files are missing: {missing}")

    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = run_dir / "artifacts"
    scripts_dir = artifacts_dir / "scripts"
    data_dir = artifacts_dir / "data"
    screenshots_dir = artifacts_dir / "screenshots"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    for target_name, template_path in required_templates.items():
        copy_template_if_missing(template_path, run_dir / target_name)

    product, modules = split_module_parts(module_path, product_name)
    level1 = modules[0] if len(modules) > 0 else ""
    level2 = modules[1] if len(modules) > 1 else ""
    level3 = modules[2] if len(modules) > 2 else ""
    leaf_path = ">".join(modules) or module_path

    write_single_csv_row(
        run_dir / "batch-status.csv",
        {
            "批次ID": batch_id,
            "一级模块": level1,
            "二级菜单": level2,
            "三级菜单/页面域": level3,
            "批次范围": leaf_path,
            "状态": "待开始",
            "页面数": "0",
            "元素总数": "0",
            "已覆盖元素数": "0",
            "待确认元素数": "0",
            "功能用例数": "0",
            "性能场景数": "0",
            "异常用例数": "0",
            "边界用例数": "0",
            "权限/状态用例数": "0",
            "数据一致性用例数": "0",
            "页面遍历完成": "否",
            "功能用例完成": "否",
            "性能设计完成": "否",
            "异常边界权限覆盖完成": "否",
            "页面元素覆盖完成": "否",
            "产品版图已更新": "否",
            "覆盖质量自检": "未通过",
            "导入文件已生成": "否",
            "最小标题路径": leaf_path,
            "下一步动作": "开始页面实探并补充 page-discovery.csv",
        },
    )
    write_single_csv_row(
        run_dir / "page-discovery.csv",
        {
            "批次ID": batch_id,
            "一级模块": level1,
            "二级菜单": level2,
            "三级菜单/页面域": level3,
            "最小标题路径": leaf_path,
            "菜单路径/URL": leaf_path,
            "发现方式": "浏览器实探/页面资料",
            "是否已生成用例": "否",
            "覆盖状态": "待确认",
            "备注": "按当前批次页面实探结果补充页面、元素、取值、联动和关联用例",
        },
    )

    init_note = (
        "\n\n## 批次初始化\n"
        f"- 产品/系统：{product}\n"
        f"- 模块路径：{leaf_path}\n"
        f"- 批次ID：{batch_id}\n"
        "- 执行要求：先补全 page-discovery.csv，再生成测试设计、导入文件和 batch-status.csv 覆盖数据。\n"
    )
    for markdown_name in ["batch-plan.md", "batch-review.md"]:
        markdown_path = run_dir / markdown_name
        text = markdown_path.read_text(encoding="utf-8-sig")
        if "## 批次初始化" not in text:
            markdown_path.write_text(text.rstrip() + init_note, encoding="utf-8")

    print(f"Initialized batch run: {run_dir}")
    return run_dir


def split_module_parts(module_path: str, product_name: str | None = None) -> tuple[str, list[str]]:
    if product_name:
        return product_name, canonical_module_parts(module_path, product_name)
    parts = canonical_module_parts(module_path)
    if len(parts) >= 4:
        return parts[0], parts[1:]
    return (parts[0] if parts else "产品"), parts


def sync_product_map(
    product_map: Path,
    formal_workbook: Path,
    page_discovery: Path,
    module_path: str,
    archive_path: str,
    product_name: str | None = None,
) -> None:
    today = date.today().isoformat()
    product, modules = split_module_parts(module_path, product_name)
    module_label = ">".join(modules) or module_path
    level1 = modules[0] if len(modules) > 0 else ""
    level2 = modules[1] if len(modules) > 1 else ""
    level3 = modules[2] if len(modules) > 2 else ""

    formal_wb = load_workbook(formal_workbook, data_only=True)
    function_ws = formal_wb[FORMAL_FUNCTION_SHEET]
    function_rows = non_empty_rows(function_ws, header_map(function_ws))

    with page_discovery.open("r", encoding="utf-8-sig", newline="") as fp:
        discovery_rows = [row for row in csv.DictReader(fp) if any((value or "").strip() for value in row.values())]

    wb = load_workbook(product_map)
    for ws in wb.worksheets:
        remove_rows_containing(ws, [module_label, archive_path])

    pages = sorted({row.get("页面/入口", "") for row in discovery_rows if row.get("页面/入口")})
    for page in pages or [level3 or level2 or level1 or module_label]:
        append_mapped_row(
            wb["产品模块地图"],
            {
                "产品/系统": product,
                "一级模块": level1,
                "二级模块": level2,
                "三级模块": level3,
                "页面/入口": page,
                "菜单路径/URL": module_label,
                "模块功能摘要": f"{module_label} 页面功能、页面元素和测试资产已同步",
                "归档测试设计路径": archive_path,
                "覆盖状态": "已覆盖",
                "最后更新时间": today,
                "待确认问题": "无",
            },
        )

    linked_create_ids = ";".join(row.get("用例 ID", "") for row in function_rows[:3] if row.get("用例 ID"))
    append_mapped_row(
        wb["业务对象地图"],
        {
            "产品/系统": product,
            "业务对象": level3 or level2 or module_label,
            "来源模块": module_label,
            "消费模块": module_label,
            "关键字段": "名称、状态、配置项",
            "关键状态": "新增、编辑、删除、查询",
            "状态生产者": module_label,
            "状态消费者": module_label,
            "创建用例ID": linked_create_ids,
            "归档测试设计路径": archive_path,
            "待确认问题": "无",
        },
    )
    append_mapped_row(
        wb["业务链路地图"],
        {
            "链路ID": f"FLOW-{safe_filename(module_label)}",
            "链路名称": f"{module_label}主流程",
            "起始模块": module_label,
            "结束模块": module_label,
            "业务对象": level3 or level2 or module_label,
            "关键状态流转": "进入页面>新增/编辑/查询/删除>校验结果",
            "主流程用例ID": linked_create_ids,
            "依赖测试数据": "AI_TEST 测试数据",
            "风险点": "页面联动、数据一致性、权限状态",
            "归档测试设计路径": archive_path,
        },
    )

    for row in discovery_rows:
        append_mapped_row(
            wb["页面元素地图"],
            {
                "产品/系统": product,
                "模块": module_label,
                "页面/入口": row.get("页面/入口", ""),
                "菜单路径/URL": row.get("菜单路径/URL", "") or module_label,
                "元素名称/文案": row.get("元素名称/文案", ""),
                "元素类型": row.get("元素类型", ""),
                "交互方式": row.get("交互方式", ""),
                "前置状态/权限": row.get("角色/权限", ""),
                "关联用例ID": row.get("关联用例ID", ""),
                "覆盖状态": row.get("覆盖状态", ""),
                "发现来源": row.get("发现方式", ""),
                "最后更新时间": today,
                "备注": row.get("备注", ""),
            },
        )

    for row in function_rows:
        case_id = row.get("用例 ID", "")
        if not case_id:
            continue
        append_mapped_row(
            wb["用例资产索引"],
            {
                "产品/系统": product,
                "模块": module_label,
                "功能点": row.get("功能点", ""),
                "用例ID": case_id,
                "用例标题": row.get("用例标题", ""),
                "测试类型": row.get("测试类型", "功能测试") or "功能测试",
                "执行方式": "手动",
                "是否可复用为前置条件": "否",
                "是否跨模块": "否",
                "关联业务对象": level3 or level2 or module_label,
                "关联业务链路": f"{module_label}主流程",
                "归档测试设计路径": archive_path,
                "最后更新时间": today,
            },
        )

    for function_point in sorted({row.get("功能点", "") for row in function_rows if row.get("功能点")}):
        ids = ";".join(row.get("用例 ID", "") for row in function_rows if row.get("功能点") == function_point and row.get("用例 ID"))
        append_mapped_row(
            wb["模块能力索引"],
            {
                "产品/系统": product,
                "模块": module_label,
                "功能点": function_point,
                "能力/数据对象": level3 or level2 or module_label,
                "能力描述": f"{function_point} 已形成测试资产",
                "关键状态": "正常、异常、边界、权限/状态",
                "可复用前置条件": "按归档测试设计准备测试数据",
                "关联用例ID": ids,
                "归档测试设计路径": archive_path,
                "限制/待确认问题": "无",
                "最后更新时间": today,
            },
        )

    append_mapped_row(
        wb["跨模块依赖关系"],
        {
            "产品/系统": product,
            "当前模块": module_label,
            "依赖模块": "待识别",
            "依赖业务对象": level3 or level2 or module_label,
            "依赖功能点/能力": "页面入口、权限、数据状态",
            "依赖类型": "待确认",
            "引用用例ID": linked_create_ids,
            "当前模块用例ID": linked_create_ids,
            "使用方式": "作为页面实探和测试数据准备依据",
            "风险/待确认问题": "无明确跨模块依赖时保持待确认",
            "最后更新时间": today,
        },
    )
    append_mapped_row(
        wb["可复用测试数据"],
        {
            "产品/系统": product,
            "模块": module_label,
            "数据名称": "AI_TEST 测试数据",
            "数据类型": "页面实探数据",
            "数据内容/构造方式": "使用带 AI_TEST 或 CODEX_TEST 标识的数据",
            "适用用例ID": linked_create_ids,
            "是否可复用": "是",
            "限制/清理方式": "仅操作本次创建的数据，交付后按环境规则清理",
            "最后更新时间": today,
        },
    )
    append_mapped_row(
        wb["变更影响分析"],
        {
            "产品/系统": product,
            "变更模块": module_label,
            "变更点": "新增或更新模块测试设计",
            "影响模块": module_label,
            "影响业务对象": level3 or level2 or module_label,
            "影响用例ID": linked_create_ids,
            "回归建议": "回归页面入口、核心流程、异常边界、权限状态和数据一致性",
            "风险等级": "中",
            "最后更新时间": today,
        },
    )
    append_mapped_row(
        wb["变更记录"],
        {
            "变更日期": today,
            "变更类型": "测试资产同步",
            "变更内容": f"同步 {module_label} 测试设计、页面元素和用例资产",
            "影响范围": module_label,
            "关联归档路径": archive_path,
            "是否已同步产品版图": "是",
            "备注": "由 sync-product-map/finalize-deliverables 统一维护",
        },
    )
    remove_workbook_tables_and_refresh_filters(wb)
    wb.save(product_map)


def finalize_deliverables(
    project_root: Path,
    formal_workbook: Path,
    import_workbook: Path,
    module_path: str,
    batch_status: Path | None = None,
    batch_id: str | None = None,
    product_map: Path | None = None,
    page_discovery: Path | None = None,
    product_name: str | None = None,
) -> None:
    project_root = project_root.resolve()
    _, formal_name, import_name = deliverable_names(module_path, product_name)

    apply_formal_workbook_styles(formal_workbook)
    import_wb = load_workbook(import_workbook)
    remove_workbook_tables_and_refresh_filters(import_wb)
    import_wb.save(import_workbook)

    module_archive = project_root / "docs" / "test-assets" / "modules" / formal_name
    import_archive = project_root / "docs" / "test-assets" / "imports" / import_name
    current_copy = project_root / "docs" / "test-design" / "current" / formal_name
    deliverable_formal = project_root / "docs" / "test-design" / "deliverables" / formal_name
    deliverable_import = project_root / "docs" / "test-design" / "deliverables" / import_name

    for target in [module_archive, current_copy, deliverable_formal]:
        copy_workbook(formal_workbook, target)
    for target in [import_archive, deliverable_import]:
        copy_workbook(import_workbook, target)

    if batch_status:
        changes = update_batch_status_paths(
            batch_status,
            batch_id,
            relative_project_path(project_root, module_archive),
            relative_project_path(project_root, import_archive),
        )
        sync_batch_markdown_paths(batch_status, changes)
        cleanup_batch_artifacts(batch_status)
    if product_map and page_discovery:
        sync_product_map(
            product_map,
            module_archive,
            page_discovery,
            module_path,
            relative_project_path(project_root, module_archive),
            product_name,
        )


def run_python_script(script: Path, args: list[str]) -> None:
    completed = subprocess.run([sys.executable, str(script), *args], check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def complete_deliverables(
    project_root: Path,
    formal_workbook: Path,
    import_template: Path,
    module_path: str,
    import_workbook: Path | None = None,
    batch_status: Path | None = None,
    batch_id: str | None = None,
    product_map: Path | None = None,
    page_discovery: Path | None = None,
    product_name: str | None = None,
    scripts_path: Path | None = None,
) -> None:
    project_root = project_root.resolve()
    script_dir = Path(__file__).resolve().parent
    _, _, import_name = deliverable_names(module_path, product_name)
    target_import = import_workbook or (project_root / "docs" / "test-assets" / "imports" / import_name)

    if scripts_path and scripts_path.exists():
        run_python_script(script_dir / "validate-generated-python-scripts.py", ["--path", str(scripts_path)])
    if batch_status:
        validate_batch_artifacts(batch_status.resolve().parent, "cases")

    apply_formal_workbook_styles(formal_workbook)
    generate_import_workbook(formal_workbook, import_template, target_import, module_path, product_name)
    finalize_deliverables(
        project_root,
        formal_workbook,
        target_import,
        module_path,
        batch_status,
        batch_id,
        product_map,
        page_discovery,
        product_name,
    )

    validator_args = ["--workbook", str(formal_workbook), "--import-workbook", str(target_import)]
    if batch_status:
        validator_args.extend(["--batch-status", str(batch_status)])
    if product_map:
        validator_args.extend(["--product-map", str(product_map)])
    if page_discovery:
        validator_args.extend(["--page-discovery", str(page_discovery)])
    run_python_script(script_dir / "validate-test-design-deliverable.py", validator_args)


def generate_import_workbook(
    formal_workbook: Path,
    import_template: Path,
    output: Path,
    module_path: str,
    product_name: str | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(import_template, output)

    formal_wb = load_workbook(formal_workbook)
    if FORMAL_FUNCTION_SHEET not in formal_wb.sheetnames:
        raise ValueError(f"Formal workbook is missing sheet: {FORMAL_FUNCTION_SHEET}")
    function_ws = formal_wb[FORMAL_FUNCTION_SHEET]
    function_headers = header_map(function_ws)

    import_wb = load_workbook(output)
    import_ws = import_wb[import_wb.sheetnames[0]]
    import_headers = header_map(import_ws)
    clear_data_rows(import_ws)

    canonical_path = ">".join(canonical_module_parts(module_path, product_name)) or module_path
    modules = module_names(canonical_path)
    write_row = 2
    skipped_cases: list[str] = []
    for row_index in range(2, function_ws.max_row + 1):
        case = row_dict(function_ws, function_headers, row_index)
        if not case.get("用例 ID") and not case.get("用例标题"):
            continue
        if not is_importable_function_case(case):
            skipped_cases.append(case.get("用例 ID") or case.get("用例标题") or f"row {row_index}")
            continue
        copy_row_style(import_ws, 2 if import_ws.max_row >= 2 else 1, write_row)
        dfx_dimension = case.get("DFX维度", "")
        dfx_scenario = case.get("DFX场景", "")
        tags = ";".join(part for part in [case.get("模块", ""), case.get("功能点", ""), dfx_dimension, dfx_scenario] if part)
        dfx_note = f"DFX覆盖：{dfx_dimension}-{dfx_scenario}" if dfx_dimension and dfx_scenario else ""
        remarks = "\n".join(part for part in [dfx_note, case.get("备注", "")] if part)
        mapped = {
            "一级模块名称": modules[0],
            "二级模块名称": modules[1],
            "三级模块名称": modules[2],
            "四级模块名称": modules[3],
            "五级模块名称": modules[4],
            "测试用例序号": str(write_row - 1),
            "测试用例名称": case.get("用例标题", ""),
            "测试步骤描述": case.get("操作步骤", ""),
            "测试步骤预期结果": case.get("预期结果", ""),
            "测试类型": normalize_test_type(case.get("测试类型", "")),
            "测试用例级别": normalize_case_level(case.get("优先级", "")),
            "执行方式": execution_mode(case),
            "测试用例说明": case.get("功能点", ""),
            "前置条件": case.get("前置条件", ""),
            "标签": tags,
            "备注": remarks,
        }
        for field in IMPORT_AUTO_FIELDS:
            mapped[field] = ""
        for field, value in mapped.items():
            column = import_headers.get(field)
            if column:
                import_ws.cell(row=write_row, column=column, value=value)
        set_wrap(import_ws, import_headers, write_row, IMPORT_MULTILINE_FIELDS)
        import_ws.row_dimensions[write_row].height = max(import_ws.row_dimensions[write_row].height or 18, 60)
        write_row += 1
    if skipped_cases:
        print(
            "WARN: skipped non-functional/performance-style cases when generating import workbook: "
            + ", ".join(skipped_cases[:20]),
            file=sys.stderr,
        )

    template_wb = load_workbook(import_template)
    apply_template_workbook_format(import_wb, template_wb)
    for row_index in range(2, import_ws.max_row + 1):
        set_wrap(import_ws, import_headers, row_index, IMPORT_MULTILINE_FIELDS)
        import_ws.row_dimensions[row_index].height = max(import_ws.row_dimensions[row_index].height or 18, 60)
    remove_workbook_tables_and_refresh_filters(import_wb)
    import_wb.save(output)


def apply_formal_workbook_styles(workbook: Path, output: Path | None = None, template: Path | None = None) -> None:
    target = output or workbook
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workbook, output)
    wb = load_workbook(target)
    template_path = template or (Path(__file__).resolve().parents[1] / "docs" / "test-design" / "codebuddy-test-design-template.xlsx")
    if template_path.exists():
        template_wb = load_workbook(template_path)
        apply_template_workbook_format(wb, template_wb)
    for sheet_name, fields in FORMAL_MULTILINE_FIELDS.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        headers = header_map(ws)
        for row_index in range(2, ws.max_row + 1):
            set_wrap(ws, headers, row_index, fields)
            ws.row_dimensions[row_index].height = max(ws.row_dimensions[row_index].height or 18, 60)
    remove_workbook_tables_and_refresh_filters(wb)
    wb.save(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or normalize test design Excel deliverables.")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-import", help="Generate test-system import workbook from formal test design workbook.")
    gen.add_argument("--formal-workbook", required=True, type=Path)
    gen.add_argument("--import-template", required=True, type=Path)
    gen.add_argument("--output", required=True, type=Path)
    gen.add_argument("--module-path", required=True)
    gen.add_argument("--product-name")

    style = sub.add_parser("fix-formal-styles", help="Apply required multiline wrapping styles to a formal workbook.")
    style.add_argument("--workbook", required=True, type=Path)
    style.add_argument("--output", type=Path)
    style.add_argument("--template", type=Path)

    init = sub.add_parser("init-batch-run", help="Create a standard batch-run ledger from templates before page discovery.")
    init.add_argument("--project-root", required=True, type=Path)
    init.add_argument("--run-id", required=True)
    init.add_argument("--module-path", required=True)
    init.add_argument("--batch-id", default="BATCH-001")
    init.add_argument("--product-name")

    batch_gate = sub.add_parser("validate-batch-artifacts", help="Validate batch-run CSV ledgers, element DFX minimums, CRUD lifecycle, and case shards before continuing.")
    batch_gate.add_argument("--run-dir", required=True, type=Path)
    batch_gate.add_argument("--phase", choices=["discovery", "plan", "cases"], default="cases")

    prepare_cases = sub.add_parser("prepare-function-case-generation", help="Remove stale function case shards and manifest before generating new JSON shards.")
    prepare_cases.add_argument("--run-dir", required=True, type=Path)

    finalize = sub.add_parser("finalize-deliverables", help="Copy validated workbooks to current/deliverables/internal archives and update batch-status paths.")
    finalize.add_argument("--project-root", required=True, type=Path)
    finalize.add_argument("--formal-workbook", required=True, type=Path)
    finalize.add_argument("--import-workbook", required=True, type=Path)
    finalize.add_argument("--module-path", required=True)
    finalize.add_argument("--batch-status", type=Path)
    finalize.add_argument("--batch-id")
    finalize.add_argument("--product-map", type=Path)
    finalize.add_argument("--page-discovery", type=Path)
    finalize.add_argument("--product-name")

    complete = sub.add_parser("complete-deliverables", help="One-shot precheck, style, import generation, finalize, and delivery validation.")
    complete.add_argument("--project-root", required=True, type=Path)
    complete.add_argument("--formal-workbook", required=True, type=Path)
    complete.add_argument("--import-template", required=True, type=Path)
    complete.add_argument("--module-path", required=True)
    complete.add_argument("--import-workbook", type=Path)
    complete.add_argument("--batch-status", type=Path)
    complete.add_argument("--batch-id")
    complete.add_argument("--product-map", type=Path)
    complete.add_argument("--page-discovery", type=Path)
    complete.add_argument("--product-name")
    complete.add_argument("--scripts-path", type=Path)

    sync = sub.add_parser("sync-product-map", help="Sync product-map.xlsx from a formal workbook and page-discovery.csv.")
    sync.add_argument("--product-map", required=True, type=Path)
    sync.add_argument("--formal-workbook", required=True, type=Path)
    sync.add_argument("--page-discovery", required=True, type=Path)
    sync.add_argument("--module-path", required=True)
    sync.add_argument("--archive-path", required=True)
    sync.add_argument("--product-name")

    args = parser.parse_args()
    if args.command == "generate-import":
        generate_import_workbook(args.formal_workbook, args.import_template, args.output, args.module_path, args.product_name)
    elif args.command == "fix-formal-styles":
        apply_formal_workbook_styles(args.workbook, args.output, args.template)
    elif args.command == "init-batch-run":
        init_batch_run(args.project_root, args.run_id, args.module_path, args.batch_id, args.product_name)
    elif args.command == "validate-batch-artifacts":
        validate_batch_artifacts(args.run_dir, args.phase)
    elif args.command == "prepare-function-case-generation":
        prepare_function_case_generation(args.run_dir)
    elif args.command == "finalize-deliverables":
        if args.page_discovery and not args.batch_status:
            raise SystemExit(
                "ERROR: --batch-status is required when --page-discovery is provided. "
                "Run init-batch-run first and keep batch-plan.md, batch-status.csv, batch-review.md, and page-discovery.csv together."
            )
        finalize_deliverables(
            args.project_root,
            args.formal_workbook,
            args.import_workbook,
            args.module_path,
            args.batch_status,
            args.batch_id,
            args.product_map,
            args.page_discovery,
            args.product_name,
        )
    elif args.command == "complete-deliverables":
        if args.page_discovery and not args.batch_status:
            raise SystemExit(
                "ERROR: --batch-status is required when --page-discovery is provided. "
                "Run init-batch-run first and keep batch-plan.md, batch-status.csv, batch-review.md, and page-discovery.csv together."
            )
        complete_deliverables(
            args.project_root,
            args.formal_workbook,
            args.import_template,
            args.module_path,
            args.import_workbook,
            args.batch_status,
            args.batch_id,
            args.product_map,
            args.page_discovery,
            args.product_name,
            args.scripts_path,
        )
    elif args.command == "sync-product-map":
        sync_product_map(
            args.product_map,
            args.formal_workbook,
            args.page_discovery,
            args.module_path,
            args.archive_path,
            args.product_name,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
