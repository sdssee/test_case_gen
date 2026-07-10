from __future__ import annotations

from copy import copy, deepcopy

from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter, range_boundaries


FORMAL_FUNCTION_SHEET = "功能测试用例"
IMPORT_MULTILINE_FIELDS = ["测试步骤描述", "测试步骤预期结果", "前置条件", "测试用例说明", "备注"]
FORMAL_MULTILINE_FIELDS = {
    "功能测试用例": ["前置条件", "测试数据", "操作步骤", "预期结果", "备注"],
    "性能测试设计": ["前置条件/数据准备", "执行步骤", "监控指标", "通过标准", "风险备注"],
    "风险与待确认问题": ["描述", "影响范围", "建议处理方式"],
    "自动化建议": ["建议说明", "前置条件", "维护要求"],
    "页面元素覆盖清单": ["业务依据/规则来源", "待确认问题/备注"],
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
            ranges.append(f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{old_max_row}")
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
        if sheet_name in template_wb.sheetnames:
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
    if ws.max_row > 1 and ws.max_column > 1:
        ws.auto_filter.ref = worksheet_used_range(ws)


def remove_workbook_tables_and_refresh_filters(wb) -> None:
    for ws in wb.worksheets:
        remove_worksheet_tables_and_refresh_filter(ws)


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
