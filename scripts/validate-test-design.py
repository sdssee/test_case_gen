# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

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

REQUIRED_FILES = [
    "AGENTS.md",
    "CODEBUDDY.md",
    "README.md",
    "README_IMPORT.md",
    ".codebuddy/skills/test-design/SKILL.md",
    ".codebuddy/.rules/test-design-rule.mdc",
    ".codebuddy/rules/test-design-rule.md",
    "docs/ARCHITECTURE.md",
    "docs/RULE_OWNERSHIP.md",
    "docs/test-design/excel-template-spec.md",
    "docs/test-design/rules/README.md",
    "docs/test-design/rules/case-design.md",
    "docs/test-design/rules/page-discovery.md",
    "docs/test-design/rules/batch-run.md",
    "docs/test-design/rules/excel-deliverable.md",
    "docs/test-design/rules/import-template.md",
    "docs/test-design/rules/data-safety.md",
    "docs/test-design/rules/dfx-test-strategy.md",
    "scripts/test_design_excel_tools.py",
    "scripts/validate-test-design-deliverable.py",
]


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
    return ["".join(node.text or "" for node in item.findall(".//x:t", NS)) for item in root.findall("x:si", NS)]


def cell_text(cell: ET.Element, shared: list[str]) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", NS))
    value = cell.find("x:v", NS)
    if value is None or value.text is None:
        return ""
    if cell.attrib.get("t") == "s":
        return shared[int(value.text)]
    return value.text


def first_row_values(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    row = root.find(".//x:sheetData/x:row[@r='1']", NS)
    if row is None:
        fail(f"{path} 第一张 Sheet 缺少表头行")
    return [cell_text(cell, shared) for cell in row.findall("x:c", NS)]


def validate_no_excel_tables(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        stale = [name for name in zf.namelist() if name.startswith("xl/tables/")]
        if stale:
            fail(f"{path} 不应包含 Excel Table 部件: {stale}")


def validate_import_left_alignment(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        styles = ET.fromstring(zf.read("xl/styles.xml"))
        cell_xfs = styles.find("x:cellXfs", NS)
        if cell_xfs is None:
            fail("导入模板缺少单元格样式")
        left_style_ids = {
            index
            for index, xf in enumerate(cell_xfs.findall("x:xf", NS))
            if (alignment := xf.find("x:alignment", NS)) is not None
            and alignment.attrib.get("horizontal") == "left"
        }
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    cells = sheet.findall(".//x:sheetData/x:row[@r='2']/x:c", NS)
    wrong = [cell.attrib.get("r", "") for cell in cells if int(cell.attrib.get("s", "0")) not in left_style_ids]
    if wrong:
        fail(f"测试用例模板.xlsx 第 2 行必须全部左对齐，异常单元格: {wrong[:10]}")


def assert_contains(path: Path, markers: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for marker in markers:
        if marker not in text:
            fail(f"{path} 缺少必要规则: {marker}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    for relative in REQUIRED_FILES:
        path = root / relative
        if not path.exists():
            fail(f"缺少项目文件: {relative}")

    formal_template = root / "docs" / "test-design" / "codebuddy-test-design-template.xlsx"
    import_template = root / "docs" / "test-design" / "测试用例模板.xlsx"
    for template in [formal_template, import_template]:
        if not template.exists():
            fail(f"缺少 Excel 模板: {template}")
        validate_no_excel_tables(template)

    if workbook_sheets(formal_template) != EXPECTED_SHEETS:
        fail("正式测试设计模板必须且只能包含 8 个标准 Sheet")
    if first_row_values(import_template) != IMPORT_HEADERS:
        fail("测试用例模板.xlsx 表头发生变化")
    validate_import_left_alignment(import_template)

    rule_a = (root / ".codebuddy" / ".rules" / "test-design-rule.mdc").read_text(encoding="utf-8")
    rule_b = (root / ".codebuddy" / "rules" / "test-design-rule.md").read_text(encoding="utf-8")
    if rule_a != rule_b:
        fail("两份 CodeBuddy Rule 镜像内容不一致")

    tool = root / "scripts" / "test_design_excel_tools.py"
    assert_contains(tool, ["adjust_row_height", "apply_template_workbook_format", "complete-deliverables", "generate-import"])
    assert_contains(
        root / "docs" / "test-design" / "excel-template-spec.md",
        ["自动调整行高", "水平左对齐", "docs/test-design/deliverables/"],
    )

    print("OK: test design templates and lightweight project structure are aligned.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
