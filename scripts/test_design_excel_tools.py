# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
from copy import copy
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
    "风险与待确认问题": ["问题描述", "影响范围", "建议处理方式"],
    "自动化建议": ["建议说明", "前置条件", "维护要求"],
    "页面元素覆盖清单": ["业务依据/规则来源", "待确认问题/备注"],
}

IMPORT_AUTO_FIELDS = {"测试用例系统编号", "作者"}
IMPORT_ALLOWED_VALUES = {
    "测试类型": {"功能测试", "性能规格测试", "可靠性测试", "兼容性测试", "可维护性测试", "安全性测试", "易用性测试"},
    "测试用例级别": {"L1", "L2", "L3", "L4"},
    "执行方式": {"自动化", "手动"},
}


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


def execution_mode(row: dict[str, str]) -> str:
    note = "".join([row.get("备注", ""), row.get("是否适合自动化", "")])
    automation_markers = ["自动化资产", "脚本", "流水线", "API自动化", "UI自动化", "已实现"]
    if "自动化" in note and any(marker in note for marker in automation_markers):
        return "自动化"
    return "手动"


def module_names(module_path: str) -> list[str]:
    parts = [part.strip() for part in module_path.replace("/", ">").split(">") if part.strip()]
    return (parts + [""] * 5)[:5]


def clear_data_rows(ws, start_row: int = 2) -> None:
    if ws.max_row > start_row:
        ws.delete_rows(start_row + 1, ws.max_row - start_row)
    if ws.max_row >= start_row:
        for cell in ws[start_row]:
            cell.value = None


def worksheet_used_range(ws) -> str:
    return f"A1:{get_column_letter(ws.max_column)}{max(ws.max_row, 1)}"


def resize_worksheet_tables(ws) -> None:
    if not ws.tables:
        return
    sheet_ref = worksheet_used_range(ws)
    for table in ws.tables.values():
        min_col, min_row, _, _ = range_boundaries(table.ref)
        new_ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(ws.max_column)}{max(ws.max_row, min_row)}"
        table.ref = new_ref
        if table.autoFilter is not None:
            table.autoFilter.ref = new_ref
    if ws.auto_filter and ws.auto_filter.ref:
        ws.auto_filter.ref = sheet_ref


def resize_workbook_tables(wb) -> None:
    for ws in wb.worksheets:
        resize_worksheet_tables(ws)


def generate_import_workbook(formal_workbook: Path, import_template: Path, output: Path, module_path: str) -> None:
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

    modules = module_names(module_path)
    write_row = 2
    for row_index in range(2, function_ws.max_row + 1):
        case = row_dict(function_ws, function_headers, row_index)
        if not case.get("用例 ID") and not case.get("用例标题"):
            continue
        copy_row_style(import_ws, 2 if import_ws.max_row >= 2 else 1, write_row)
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
            "标签": case.get("模块", ""),
            "备注": case.get("备注", ""),
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

    resize_workbook_tables(import_wb)
    import_wb.save(output)


def apply_formal_workbook_styles(workbook: Path, output: Path | None = None) -> None:
    target = output or workbook
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workbook, output)
    wb = load_workbook(target)
    for sheet_name, fields in FORMAL_MULTILINE_FIELDS.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        headers = header_map(ws)
        for row_index in range(2, ws.max_row + 1):
            set_wrap(ws, headers, row_index, fields)
            ws.row_dimensions[row_index].height = max(ws.row_dimensions[row_index].height or 18, 60)
    resize_workbook_tables(wb)
    wb.save(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or normalize test design Excel deliverables.")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-import", help="Generate test-system import workbook from formal test design workbook.")
    gen.add_argument("--formal-workbook", required=True, type=Path)
    gen.add_argument("--import-template", required=True, type=Path)
    gen.add_argument("--output", required=True, type=Path)
    gen.add_argument("--module-path", required=True)

    style = sub.add_parser("fix-formal-styles", help="Apply required multiline wrapping styles to a formal workbook.")
    style.add_argument("--workbook", required=True, type=Path)
    style.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "generate-import":
        generate_import_workbook(args.formal_workbook, args.import_template, args.output, args.module_path)
    elif args.command == "fix-formal-styles":
        apply_formal_workbook_styles(args.workbook, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
