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
    assert_contains(
        tool,
        [
            "adjust_row_height",
            "rebuild_formal_workbook_from_template",
            "atomic_copy_workbook",
            "complete-deliverables",
            "generate-import",
        ],
    )
    assert_contains(
        root / "AGENTS.md",
        [
            "每次正式交付",
            "不询问是否需要生成",
            "complete-deliverables",
            "唯一结构与样式基线",
            "手工降级生成",
            "正式测试设计默认使用中文",
            "集中执行一次中文自查",
            "不得把自动翻译写入 Excel 工具",
            "测试设计执行与交付流程不涉及 Git",
            "触发元素 → 状态出现 → 状态内操作 → 终态动作 → 页面/数据结果",
            "相似用例告警必须在交付前分类",
        ],
    )
    assert_contains(
        root / "scripts" / "validate-test-design-deliverable.py",
        [
            '"--import-workbook", required=True',
            "validate_atomic_scenario_rows",
            "warn_case_merge_candidates",
            "validate_evidence_status_consistency",
            "ui_symbol_style_issues",
            "NAVIGATION_ACTION_PATTERN",
            "UNRESOLVED_COVERAGE_NOTE_PATTERN",
            "FINDING_DISPLAY_LIMIT",
            "assert_formal_template_invariants",
            "validate_chinese_delivery_language",
            "CHINESE_DELIVERY_FIELDS",
            "TRANSIENT_ACTION_PATTERNS",
            "assert_expected_result_consistency",
            "FORMAL_ALLOWED_VALUES",
            "scenario_count",
            '"--formal-template"',
        ],
    )
    assert_contains(
        root / "docs" / "test-design" / "excel-template-spec.md",
        [
            "自动调整行高",
            "水平左对齐",
            "docs/test-design/deliverables/",
            "功能点` 作为父场景",
            "每个场景行只允许一个主 `DFX维度`",
            "待实探：",
            "已覆盖` 不得与上述未解决前缀并存",
            "一级菜单-二级菜单-目标页面",
            "说明性字段默认使用中文",
            "是否生成用例` 只能填写 `是` 或 `否",
            "是否适合自动化` 只能填写 `是`、`否` 或 `待评估",
        ],
    )
    assert_contains(
        root / "scripts" / "validate-generated-python-scripts.py",
        ["SMART_QUOTE_HINT_CHARS", "Generated intermediate validation found", "validate_compile", "validate_utf8"],
    )

    print("OK: test design templates and lightweight project structure are aligned.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
