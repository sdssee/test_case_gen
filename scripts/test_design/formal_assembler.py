from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .batch import manifest_parts
from .contracts.sheet_data import SHEET_DATA_HEADERS
from .excel_utils import (
    clear_data_rows,
    header_map,
    remove_workbook_tables_and_refresh_filters,
    write_mapped_row,
)
from .io_utils import atomic_copy, atomic_save_workbook, temporary_sibling


SHEET_DATA_SOURCES = {
    "测试设计总览": "overview.json",
    "需求用户故事拆解": "requirements.json",
    "测试场景矩阵": "scenarios.json",
    "性能测试设计": "performance.json",
    "风险与待确认问题": "risks.json",
    "自动化建议": "automation.json",
    "页面元素覆盖清单": "page_elements.json",
}
FUNCTION_SHEET = "功能测试用例"
TEMPLATE_MARKERS = {"TC-LOGIN-001", "STORY-001", "SCN-LOGIN-001", "PT-LOGIN-001", "EL-LOGIN-001"}


def _load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        payload = payload["rows"]
    elif isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{path} must contain a non-empty row list or one row object")
    if not all(isinstance(row, dict) for row in payload):
        raise ValueError(f"{path} must contain JSON objects only")
    return payload


def _validate_rows(path: Path, rows: list[dict[str, Any]], headers: dict[str, int]) -> None:
    expected = set(headers)
    for index, row in enumerate(rows, start=1):
        keys = set(row)
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        if missing or extra:
            raise ValueError(
                f"{path.name} row {index} must use the exact target Sheet headers; "
                f"missing={missing}, extra={extra}"
            )
        if not any(str(value or "").strip() for value in row.values()):
            raise ValueError(f"{path.name} row {index} is empty")


def _function_rows(data_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for part in manifest_parts(data_dir):
        with part.open("r", encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        part_rows = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(part_rows, list):
            raise ValueError(f"{part} must contain a list or a cases list")
        rows.extend(part_rows)
    if not rows:
        raise ValueError("Function case manifest contains no cases")
    return rows


def assemble_formal_workbook(run_dir: Path, template: Path, output: Path) -> dict[str, int]:
    run_dir = run_dir.resolve()
    data_dir = run_dir / "artifacts" / "data"
    if not template.exists():
        raise ValueError(f"Formal workbook template not found: {template}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_sibling(output)
    atomic_copy(template, temporary)
    workbook = load_workbook(temporary)
    counts: dict[str, int] = {}

    sources = {**SHEET_DATA_SOURCES, FUNCTION_SHEET: None}
    for sheet_name, filename in sources.items():
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"Formal workbook template is missing Sheet: {sheet_name}")
        worksheet = workbook[sheet_name]
        headers = header_map(worksheet)
        if not headers:
            raise ValueError(f"Formal workbook Sheet has no row-1 headers: {sheet_name}")
        if filename is None:
            rows = _function_rows(data_dir)
            source_path = data_dir / "function_cases_manifest.json"
        else:
            source_path = data_dir / filename
            if not source_path.exists():
                raise ValueError(f"Batch Sheet data file not found: {source_path}")
            if list(headers) != SHEET_DATA_HEADERS.get(filename):
                raise ValueError(f"Formal template headers drifted from the shared contract for {sheet_name}/{filename}")
            rows = _load_rows(source_path)
        _validate_rows(source_path, rows, headers)
        clear_data_rows(worksheet)
        for row_index, row in enumerate(rows, start=2):
            write_mapped_row(worksheet, headers, row_index, row)
        counts[sheet_name] = len(rows)

    remove_workbook_tables_and_refresh_filters(workbook)
    # A generated formal design must never retain template examples.
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows(min_row=2, values_only=True):
            text = "\n".join(str(value) for value in row if value is not None)
            if "示例" in text or any(marker in text for marker in TEMPLATE_MARKERS):
                raise ValueError(f"Generated formal workbook still contains template data in {worksheet.title}")
    atomic_save_workbook(workbook, temporary)
    os.replace(temporary, output)
    return counts
