# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from copy import copy, deepcopy
from pathlib import Path

try:
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment
    from openpyxl.utils import get_column_letter, range_boundaries
except ImportError as exc:  # pragma: no cover - depends on local runtime packaging.
    raise SystemExit(
        "ERROR: openpyxl is required. Run this script with the Codex bundled Python runtime; "
        "do not install or uninstall global dependencies."
    ) from exc


FORMAL_FUNCTION_SHEET = "功能测试用例"
IMPORT_MULTILINE_FIELDS = ["测试步骤描述", "测试步骤预期结果", "前置条件", "测试用例说明", "备注"]
FORMAL_MULTILINE_FIELDS = {
    "功能测试用例": ["前置条件", "测试数据", "操作步骤", "预期结果", "备注"],
    "性能测试设计": ["前置条件/数据准备", "执行步骤", "监控指标", "通过标准", "风险备注"],
    "风险与待确认问题": ["描述", "影响范围", "建议处理方式"],
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


def normalized_headers(ws) -> list[str]:
    headers = ["" if cell.value is None else str(cell.value).strip() for cell in ws[1]]
    while headers and not headers[-1]:
        headers.pop()
    return headers


def refresh_existing_filter(ws, max_row: int) -> None:
    if not ws.auto_filter.ref:
        return
    min_col, min_row, max_col, _ = range_boundaries(ws.auto_filter.ref)
    ws.auto_filter.ref = (
        f"{get_column_letter(min_col)}{min_row}:"
        f"{get_column_letter(max_col)}{max(max_row, 2)}"
    )


def rebuild_formal_workbook_from_template(source: Path, template: Path, output: Path) -> None:
    if not source.exists():
        raise ValueError(f"Formal workbook not found: {source}")
    if not template.exists():
        raise ValueError(f"Formal workbook template not found: {template}")

    source_wb = load_workbook(source)
    template_wb = load_workbook(template)
    if source_wb.sheetnames != template_wb.sheetnames:
        raise ValueError(
            "Formal workbook sheets must match the template exactly before delivery. "
            f"Expected {template_wb.sheetnames}, got {source_wb.sheetnames}"
        )

    for sheet_name in template_wb.sheetnames:
        source_ws = source_wb[sheet_name]
        target_ws = template_wb[sheet_name]
        expected_headers = normalized_headers(target_ws)
        actual_headers = normalized_headers(source_ws)
        if actual_headers != expected_headers:
            raise ValueError(
                f"{sheet_name} headers must match the formal template exactly. "
                f"Expected {expected_headers}, got {actual_headers}"
            )

        source_rows = []
        for row_index in range(2, source_ws.max_row + 1):
            unexpected_values = [
                source_ws.cell(row=row_index, column=column).value
                for column in range(len(expected_headers) + 1, source_ws.max_column + 1)
            ]
            if any(value is not None and str(value).strip() for value in unexpected_values):
                raise ValueError(
                    f"{sheet_name} row {row_index} contains data outside the formal template columns"
                )
            values = [
                source_ws.cell(row=row_index, column=column).value
                for column in range(1, len(expected_headers) + 1)
            ]
            if any(value is not None and str(value).strip() for value in values):
                source_rows.append(values)

        clear_data_rows(target_ws)
        write_row = 2
        for values in source_rows:
            copy_template_row_format(target_ws, target_ws, 2, write_row)
            for column, value in enumerate(values, start=1):
                target_ws.cell(row=write_row, column=column, value=value)
            write_row += 1

        extend_validation_ranges(target_ws, max(target_ws.max_row, 200))
        refresh_existing_filter(target_ws, target_ws.max_row)
        fields = FORMAL_MULTILINE_FIELDS.get(sheet_name, [])
        headers = header_map(target_ws)
        for row_index in range(2, target_ws.max_row + 1):
            set_wrap(target_ws, headers, row_index, fields)
            adjust_row_height(target_ws, row_index)

    output.parent.mkdir(parents=True, exist_ok=True)
    template_wb.save(output)


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


def text_display_width(value: str) -> int:
    width = 0
    for char in value:
        if char == "\t":
            width += 4
        elif unicodedata.east_asian_width(char) in {"W", "F"}:
            width += 2
        else:
            width += 1
    return width


def wrapped_line_count(value: str, column_width: float) -> int:
    usable_width = max(float(column_width) - 1, 1)
    total = 0
    for line in value.splitlines() or [""]:
        total += max(1, math.ceil(text_display_width(line) / usable_width))
    return total


def adjust_row_height(ws, row_index: int, min_height: float | None = None, max_height: float = 409) -> None:
    existing_height = ws.row_dimensions[row_index].height
    default_height = ws.sheet_format.defaultRowHeight or 18
    required_height = max(float(existing_height or 0), float(min_height or default_height))
    default_width = ws.sheet_format.defaultColWidth or 8.43

    for cell in ws[row_index]:
        if cell.value is None:
            continue
        value = str(cell.value)
        if not cell.alignment.wrap_text and "\n" not in value and "\r" not in value:
            continue
        column_dimension = ws.column_dimensions.get(get_column_letter(cell.column))
        column_width = (column_dimension.width if column_dimension else None) or default_width
        indent_width = float(cell.alignment.indent or 0) * 3
        line_count = wrapped_line_count(value, max(float(column_width) - indent_width, 1))
        font_size = float(cell.font.sz or 11)
        required_height = max(required_height, line_count * font_size * 1.35 + 4)

    ws.row_dimensions[row_index].height = min(required_height, max_height)


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
    parts = canonical_module_parts(module_path)
    return (parts + [""] * 5)[:5]


def canonical_module_parts(module_path: str, product_name: str | None = None) -> list[str]:
    normalized = module_path.replace("\\", ">").replace("/", ">").replace("→", ">")
    if ">" not in normalized and normalized.count("-") >= 2:
        normalized = normalized.replace("-", ">")
    parts = [part.strip() for part in normalized.split(">") if part.strip()]
    if product_name and parts and parts[0] == product_name.strip():
        parts = parts[1:]
    return parts


def deliverable_names(module_path: str, product_name: str | None = None) -> tuple[str, str, str]:
    parts = canonical_module_parts(module_path, product_name)
    stem = safe_filename(">".join(parts) if parts else module_path)
    return stem, f"{stem}_测试设计.xlsx", f"{stem}_导入用例.xlsx"


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


def safe_filename(value: str) -> str:
    cleaned = value.replace("\\", ">").replace("/", ">")
    for char in '<>:"|?*':
        cleaned = cleaned.replace(char, "_")
    cleaned = "_".join(part.strip() for part in cleaned.split("_") if part.strip())
    cleaned = cleaned.replace(" ", "")
    return cleaned or "测试设计"


def atomic_copy_workbook(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        return
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}.", suffix=target.suffix, dir=target.parent, delete=False
    )
    temporary = Path(handle.name)
    handle.close()
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def split_module_parts(module_path: str, product_name: str | None = None) -> tuple[str, list[str]]:
    if product_name:
        return product_name, canonical_module_parts(module_path, product_name)
    parts = canonical_module_parts(module_path)
    if len(parts) >= 4:
        return parts[0], parts[1:]
    return (parts[0] if parts else "产品"), parts


def run_python_script(script: Path, args: list[str]) -> None:
    completed = subprocess.run([sys.executable, str(script), *args], check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def delivery_state_path(formal_workbook: Path) -> Path:
    return formal_workbook.resolve().parent / f".{formal_workbook.stem}.delivery-state.json"


def atomic_write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read_delivery_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": 1, "delivery_attempts": 0, "complete_attempted": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Delivery state is unreadable: {path}; {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"Delivery state is invalid: {path}")
    return data


def initialize_discovery_state(scope: str, output: Path) -> None:
    if output.exists():
        raise ValueError(f"Discovery state already exists; reuse the same file: {output.resolve()}")
    atomic_write_json(
        output,
        {
            "version": 1,
            "phase": "discovery",
            "scope": scope.strip(),
            "baseline_complete": False,
            "closure_rescan_complete": False,
            "closure_rescan_new_targets": None,
            "understanding_questions": [],
            "targets": [],
            "scenario_case_mapping": [],
        },
    )
    print(f"OK: 深探状态已初始化：{output.resolve()}")


def complete_deliverables(
    project_root: Path,
    formal_workbook: Path,
    import_template: Path,
    module_path: str,
    import_workbook: Path | None = None,
    product_name: str | None = None,
    discovery_state: Path | None = None,
) -> None:
    project_root = project_root.resolve()
    script_dir = Path(__file__).resolve().parent
    _, formal_name, import_name = deliverable_names(module_path, product_name)
    formal_template = project_root / "docs" / "test-design" / "codebuddy-test-design-template.xlsx"
    deliverable_dir = project_root / "deliverables"
    deliverable_formal = deliverable_dir / formal_name
    deliverable_import = deliverable_dir / import_name
    target_import = import_workbook or (deliverable_dir / import_name)
    state_path = delivery_state_path(formal_workbook)
    state = read_delivery_state(state_path)
    if state.get("complete_attempted") is True:
        raise ValueError("当前批次已经执行过正式交付；禁止自动重试或重复生成")
    attempts = int(state.get("delivery_attempts", 0))
    if attempts >= 2:
        raise ValueError("当前批次已经完成两次集中校验且仍未交付；必须停止并返回完整问题")
    state.update(delivery_attempts=attempts + 1, delivery_result="validating")
    atomic_write_json(state_path, state)
    try:
        with tempfile.TemporaryDirectory(prefix="test-design-deliverables-") as temporary_dir:
            temporary_root = Path(temporary_dir)
            temporary_formal = temporary_root / "formal.xlsx"
            temporary_import = temporary_root / "import.xlsx"
            rebuild_formal_workbook_from_template(formal_workbook, formal_template, temporary_formal)
            generate_import_workbook(
                temporary_formal,
                import_template,
                temporary_import,
                module_path,
                product_name,
            )
            validator_args = [
                "--workbook", str(temporary_formal),
                "--import-workbook", str(temporary_import),
                "--formal-template", str(formal_template),
            ]
            if discovery_state:
                validator_args.extend(["--discovery-state", str(discovery_state)])
            run_python_script(script_dir / "validate-test-design-deliverable.py", validator_args)
            state.update(complete_attempted=True, delivery_result="publishing")
            atomic_write_json(state_path, state)
            formal_targets = {formal_workbook.resolve(), deliverable_formal.resolve()}
            import_targets = {target_import.resolve(), deliverable_import.resolve()}
            for target in formal_targets:
                atomic_copy_workbook(temporary_formal, target)
            for target in import_targets:
                atomic_copy_workbook(temporary_import, target)
    except BaseException:
        state["delivery_result"] = "failed"
        atomic_write_json(state_path, state)
        raise
    state["delivery_result"] = "passed"
    atomic_write_json(state_path, state)
    print(f"OK: 正式测试设计已写入 {deliverable_formal}")
    print(f"OK: 测试系统导入文件已写入 {deliverable_import}")


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
    for row_index in range(2, function_ws.max_row + 1):
        case = row_dict(function_ws, function_headers, row_index)
        if not case.get("用例 ID") and not case.get("用例标题"):
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
        adjust_row_height(import_ws, write_row)
        write_row += 1

    template_wb = load_workbook(import_template)
    apply_template_workbook_format(import_wb, template_wb)
    for row_index in range(2, import_ws.max_row + 1):
        set_wrap(import_ws, import_headers, row_index, IMPORT_MULTILINE_FIELDS)
        adjust_row_height(import_ws, row_index)
    remove_workbook_tables_and_refresh_filters(import_wb)
    import_wb.save(output)


def apply_formal_workbook_styles(workbook: Path, output: Path | None = None, template: Path | None = None) -> None:
    template_path = template or (Path(__file__).resolve().parents[1] / "docs" / "test-design" / "codebuddy-test-design-template.xlsx")
    target = output or workbook
    with tempfile.TemporaryDirectory(prefix="test-design-formal-") as temporary_dir:
        temporary = Path(temporary_dir) / "formal.xlsx"
        rebuild_formal_workbook_from_template(workbook, template_path, temporary)
        atomic_copy_workbook(temporary, target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or normalize test design Excel deliverables.")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-import", help="Maintenance only: regenerate an import workbook; not a formal delivery flow.")
    gen.add_argument("--formal-workbook", required=True, type=Path)
    gen.add_argument("--import-template", required=True, type=Path)
    gen.add_argument("--output", required=True, type=Path)
    gen.add_argument("--module-path", required=True)
    gen.add_argument("--product-name")

    style = sub.add_parser("fix-formal-styles", help="Maintenance only: rebuild workbook styles; not a formal delivery flow.")
    style.add_argument("--workbook", required=True, type=Path)
    style.add_argument("--output", type=Path)
    style.add_argument("--template", type=Path)

    discovery = sub.add_parser("validate-discovery", help="Validate the dynamic discovery queue before scenario design.")
    discovery.add_argument("--discovery-state", required=True, type=Path)

    init_discovery = sub.add_parser("init-discovery", help="Create the single discovery state before browser exploration.")
    init_discovery.add_argument("--scope", required=True)
    init_discovery.add_argument("--output", required=True, type=Path)

    complete = sub.add_parser(
        "complete-deliverables",
        help="Validate and publish one draft, with at most one corrected retry.",
    )
    complete.add_argument("--project-root", required=True, type=Path)
    complete.add_argument("--formal-workbook", required=True, type=Path)
    complete.add_argument("--import-template", required=True, type=Path)
    complete.add_argument("--module-path", required=True)
    complete.add_argument("--import-workbook", type=Path)
    complete.add_argument("--product-name")
    complete.add_argument("--discovery-state", type=Path)

    args = parser.parse_args()
    if args.command == "generate-import":
        generate_import_workbook(args.formal_workbook, args.import_template, args.output, args.module_path, args.product_name)
    elif args.command == "fix-formal-styles":
        apply_formal_workbook_styles(args.workbook, args.output, args.template)
    elif args.command == "validate-discovery":
        run_python_script(
            Path(__file__).resolve().parent / "validate-test-design-deliverable.py",
            ["--discovery-state", str(args.discovery_state), "--discovery-only"],
        )
    elif args.command == "init-discovery":
        initialize_discovery_state(args.scope, args.output)
    elif args.command == "complete-deliverables":
        complete_deliverables(
            args.project_root,
            args.formal_workbook,
            args.import_template,
            args.module_path,
            args.import_workbook,
            args.product_name,
            args.discovery_state,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
