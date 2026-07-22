# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import py_compile
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


EXPECTED_FORMAL_SHEETS = [
    "测试设计总览",
    "需求用户故事拆解",
    "测试场景矩阵",
    "功能测试用例",
    "性能测试设计",
    "风险与待确认问题",
    "自动化建议",
    "页面元素覆盖清单",
]

EXPECTED_PAGE_DISCOVERY_HEADERS = [
    "批次ID", "一级模块", "二级菜单", "三级菜单/页面域", "最小标题路径", "页面/入口", "菜单路径/URL", "发现方式",
    "角色/权限", "数据状态", "元素名称/文案", "元素类型", "交互方式", "适用DFX维度", "适用DFX场景",
    "选项取值/输入值", "联动/依赖变化", "结果分支/后续状态", "完整点击路径", "预期/观察行为", "业务依据/规则来源",
    "测试数据来源", "事实状态", "是否已生成用例", "关联用例ID", "覆盖状态", "未覆盖/待确认原因", "证据路径", "备注",
]

EXPECTED_BATCH_STATUS_HEADERS = [
    "批次ID", "一级模块", "二级菜单", "三级菜单/页面域", "最小标题路径", "状态", "页面实探状态", "JSON分片状态",
    "功能用例数", "性能场景数", "归档路径", "导入文件路径", "最后更新时间", "下一步动作",
]

ENTRY_FILES = [
    "AGENTS.md",
    "CODEBUDDY.md",
    ".codebuddy/skills/test-design/SKILL.md",
    ".codebuddy/.rules/test-design-rule.mdc",
    ".codebuddy/rules/test-design-rule.md",
]

REQUIRED_FILES = ENTRY_FILES + [
    "docs/test-design/rules/README.md",
    "docs/test-design/rules/case-design.md",
    "docs/test-design/rules/page-discovery.md",
    "docs/test-design/rules/data-safety.md",
    "docs/test-design/rules/batch-run.md",
    "docs/test-design/rules/excel-deliverable.md",
    "docs/test-design/rules/dfx-test-strategy.md",
    "docs/test-design/rules/import-template.md",
    "docs/test-design/excel-template-spec.md",
    "docs/test-assets/batch-runs/templates/page-discovery-template.csv",
    "docs/test-assets/batch-runs/templates/batch-status-template.csv",
    "docs/test-design/codebuddy-test-design-template.xlsx",
    "docs/test-design/测试用例模板.xlsx",
    "scripts/test_design_excel_tools.py",
    "scripts/validate-test-design-deliverable.py",
]


def fail(message: str) -> None:
    raise AssertionError(message)


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def assert_contains(path: Path, markers: list[str]) -> None:
    value = text(path)
    missing = [marker for marker in markers if marker not in value]
    if missing:
        fail(f"{path} is missing required markers: {missing}")


def validate_csv_template(path: Path, expected: list[str]) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        rows = list(csv.reader(fp))
    if not rows or rows[0] != expected:
        fail(f"{path} header mismatch")
    for index, row in enumerate(rows[1:], start=2):
        if len(row) != len(expected):
            fail(f"{path} row {index} has {len(row)} columns; expected {len(expected)}")


def workbook_sheet_names(path: Path) -> list[str]:
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("xl/workbook.xml"))
    return [node.attrib["name"] for node in root.findall("x:sheets/x:sheet", ns)]


def validate_python_sources(repo_root: Path) -> None:
    for path in sorted((repo_root / "scripts").glob("*.py")):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            fail(f"Python syntax error in {path}: {exc.msg}")


def validate_architecture(repo_root: Path) -> None:
    for relative in REQUIRED_FILES:
        if not (repo_root / relative).exists():
            fail(f"Missing required project file: {relative}")

    for relative in ENTRY_FILES:
        path = repo_root / relative
        if len(text(path)) >= 10000:
            fail(f"Entry file must remain below 10000 characters: {relative}")

    rule_md = text(repo_root / ".codebuddy/rules/test-design-rule.md").strip()
    rule_mdc = text(repo_root / ".codebuddy/.rules/test-design-rule.mdc").strip()
    if rule_md != rule_mdc:
        fail("The two CodeBuddy rule entry files must remain identical")

    shared_markers = ["已实测", "页面观察", "DFX设计", "待确认", "有限选项", "有效输入等价类", "多个JSON", "baseline Case"]
    for relative in ENTRY_FILES:
        assert_contains(repo_root / relative, shared_markers)

    assert_contains(repo_root / "docs/test-design/rules/case-design.md", ["单因素", "创建成功", "编辑", "业务闭环", "确定性重复"])
    assert_contains(repo_root / "docs/test-design/rules/page-discovery.md", ["DOM", "可访问性树", "增量补探", "事实状态"])
    assert_contains(repo_root / "docs/test-design/rules/data-safety.md", ["不对内网IP", "不执行敏感内容", "操作边界"])
    assert_contains(repo_root / "docs/test-design/rules/batch-run.md", ["轻量自检", "final-review.md", "fix_*.py", "多页面"])
    assert_contains(repo_root / "docs/test-design/rules/excel-deliverable.md", ["1-N级", "原子交付", "同源", "模板写入"])

    deliverable_validator = text(repo_root / "scripts/validate-test-design-deliverable.py")
    for forbidden in ["SENSITIVE_VALUE_PATTERNS", "ENVIRONMENT_VALUE_PATTERNS", "UNMASKED_VALUE_PATTERNS", "assert_no_unmasked_value", "assert_transient_flow_closed"]:
        if forbidden in deliverable_validator:
            fail(f"Removed rejection mechanism returned to deliverable validator: {forbidden}")
    for required in ["FACT_STATUSES", "assert_business_transaction_closed", "事实状态", "validate_import_workbook"]:
        if required not in deliverable_validator:
            fail(f"Deliverable validator is missing required behavior: {required}")

    excel_tools = text(repo_root / "scripts/test_design_excel_tools.py")
    for required in ["prepare-formal", "import_module_names", "migrate_page_discovery_fact_status", "atomic_publish_copies", "legacy_repeated_leaf_names", "FORMAL_WORKBOOK=", "IMPORT_WORKBOOK="]:
        if required not in excel_tools:
            fail(f"Excel tool is missing required behavior: {required}")


def validate_full(repo_root: Path) -> None:
    validate_python_sources(repo_root)
    validate_csv_template(repo_root / "docs/test-assets/batch-runs/templates/page-discovery-template.csv", EXPECTED_PAGE_DISCOVERY_HEADERS)
    validate_csv_template(repo_root / "docs/test-assets/batch-runs/templates/batch-status-template.csv", EXPECTED_BATCH_STATUS_HEADERS)

    formal_sheets = workbook_sheet_names(repo_root / "docs/test-design/codebuddy-test-design-template.xlsx")
    if formal_sheets != EXPECTED_FORMAL_SHEETS:
        fail(f"Formal template sheets mismatch: {formal_sheets}")
    if not workbook_sheet_names(repo_root / "docs/test-design/测试用例模板.xlsx"):
        fail("Import template has no worksheet")

def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the lightweight test-design framework.")
    parser.add_argument("--mode", choices=["Fast", "Full"], default="Full")
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    validate_architecture(repo_root)
    if args.mode == "Full":
        validate_full(repo_root)
    print(f"OK: test-design framework {args.mode} validation passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
