# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def workbook_sheets(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("xl/workbook.xml"))
    return [node.attrib["name"] for node in root.findall("x:sheets/x:sheet", NS)]


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for si in root.findall("x:si", NS):
        values.append("".join(t.text or "" for t in si.findall(".//x:t", NS)))
    return values


def cell_text(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//x:t", NS))

    value = cell.find("x:v", NS)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        return shared[int(value.text)]
    return value.text


def first_row_values(path: Path, sheet_index: int = 1) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read(f"xl/worksheets/sheet{sheet_index}.xml"))
    row = root.find(".//x:sheetData/x:row[@r='1']", NS)
    if row is None:
        fail(f"Sheet {sheet_index} has no row 1")
    return [cell_text(cell, shared) for cell in row.findall("x:c", NS)]


def worksheet_xml(path: Path, sheet_index: int = 1) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read(f"xl/worksheets/sheet{sheet_index}.xml").decode("utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_contains(path: Path, markers: list[str]) -> None:
    text = read_text(path)
    for marker in markers:
        if marker not in text:
            fail(f"{path.relative_to(path.parents[1])} is missing required marker: {marker}")


def assert_not_contains(path: Path, markers: list[str]) -> None:
    text = read_text(path)
    for marker in markers:
        if marker in text:
            fail(f"{path.relative_to(path.parents[1])} contains stale marker: {marker}")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    design_template = repo_root / "docs" / "test-design" / "codebuddy-test-design-template.xlsx"
    system_template = repo_root / "docs" / "test-design" / "测试用例模板.xlsx"

    if not design_template.exists():
        fail(f"Missing design template: {design_template}")
    if not system_template.exists():
        fail(f"Missing system import template: {system_template}")

    expected_design_sheets = [
        "测试设计总览",
        "需求用户故事拆解",
        "测试场景矩阵",
        "功能测试用例",
        "性能测试设计",
        "风险与待确认问题",
        "自动化建议",
        "页面元素覆盖清单",
    ]

    design_sheets = workbook_sheets(design_template)
    if design_sheets != expected_design_sheets:
        fail(
            "Design template sheets mismatch.\n"
            f"Expected: {expected_design_sheets}\n"
            f"Actual:   {design_sheets}"
        )
    if "测试系统导入用例" in design_sheets:
        fail("Design template must not contain 测试系统导入用例 sheet")

    system_sheets = workbook_sheets(system_template)
    if not system_sheets:
        fail("System import template should contain at least one sheet")

    expected_headers = [
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

    headers = first_row_values(system_template, 1)
    if headers != expected_headers:
        fail(
            "System import template headers mismatch.\n"
            f"Expected: {expected_headers}\n"
            f"Actual:   {headers}"
        )

    xml = worksheet_xml(system_template, 1)
    expected_validations = {
        'sqref="R2:R2001"': "测试类型",
        'sqref="S2:S2001"': "测试用例级别",
        'sqref="T2:T2001"': "执行方式",
    }
    for marker, label in expected_validations.items():
        if marker not in xml:
            fail(f"System import template is missing {label} dropdown validation: {marker}")

    expected_value_markers = [
        "功能测试,性能规格测试,可靠性测试,兼容性测试,可维护性测试,安全性测试,易用性测试",
        "L1,L2,L3,L4",
        "自动化,手动",
    ]
    for marker in expected_value_markers:
        if marker not in xml:
            fail(f"System import template is missing dropdown values: {marker}")

    formula_errors = re.compile(r"#REF!|#DIV/0!|#VALUE!|#NAME\?|#N/A")
    for path in [design_template, system_template]:
        with zipfile.ZipFile(path) as zf:
            for item in zf.namelist():
                if item.startswith("xl/worksheets/") and item.endswith(".xml"):
                    text = zf.read(item).decode("utf-8", errors="ignore")
                    if formula_errors.search(text):
                        fail(f"Formula error marker found in {path.name}:{item}")

    architecture_files = [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / "docs" / "ARCHITECTURE.md",
    ]
    for path in architecture_files:
        if not path.exists():
            fail(f"Missing architecture file: {path}")

    required_markers = [
        "正式测试设计",
        "测试系统导入用例",
        "独立导入文件",
        "测试用例模板.xlsx",
    ]
    for path in architecture_files:
        assert_contains(path, required_markers[:2] if path.name == "AGENTS.md" else required_markers[:3])

    stale_markers = [
        "必须输出 `测试系统导入用例` Sheet",
        "正式交付时必须包含 `测试系统导入用例` Sheet",
        "请生成测试系统导入用例 Sheet",
        "模板包含 `测试系统导入用例` Sheet",
    ]
    for path in architecture_files:
        assert_not_contains(path, stale_markers)

    assert_contains(repo_root / "AGENTS.md", ["GitHub 提交信息必须使用中文"])

    print("OK: test design templates are aligned and import template validations are preserved.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
