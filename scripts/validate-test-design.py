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


def cell_value(path: Path, sheet_index: int, cell_ref: str) -> str:
    with zipfile.ZipFile(path) as zf:
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read(f"xl/worksheets/sheet{sheet_index}.xml"))
    cell = root.find(f".//x:sheetData/x:row/x:c[@r='{cell_ref}']", NS)
    if cell is None:
        return ""
    return cell_text(cell, shared)


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


def parse_key_value_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in read_text(path).splitlines():
        line = raw_line.lstrip("\ufeff").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    design_template = repo_root / "docs" / "test-design" / "codebuddy-test-design-template.xlsx"
    system_template = repo_root / "docs" / "test-design" / "测试用例模板.xlsx"
    product_map = repo_root / "docs" / "test-assets" / "product-map.xlsx"
    version_file = repo_root / "VERSION"
    upgrade_manifest = repo_root / "UPGRADE_MANIFEST.md"
    upgrade_doc = repo_root / "docs" / "UPGRADE.md"
    package_script = repo_root / "scripts" / "new-framework-upgrade-package.ps1"
    upgrade_script = repo_root / "scripts" / "upgrade-framework.ps1"
    deliverable_validator = repo_root / "scripts" / "validate-test-design-deliverable.py"
    deliverable_validator_ps1 = repo_root / "scripts" / "validate-test-design-deliverable.ps1"

    if not design_template.exists():
        fail(f"Missing design template: {design_template}")
    if not system_template.exists():
        fail(f"Missing system import template: {system_template}")
    if not product_map.exists():
        fail(f"Missing product map: {product_map}")
    for path in [version_file, upgrade_manifest, upgrade_doc, package_script, upgrade_script, deliverable_validator, deliverable_validator_ps1]:
        if not path.exists():
            fail(f"Missing upgrade mechanism file: {path}")

    versions = parse_key_value_file(version_file)
    for key in ["framework_version", "asset_schema_version"]:
        if key not in versions or not versions[key]:
            fail(f"VERSION is missing {key}")
        if not re.fullmatch(r"\d+\.\d+\.\d+", versions[key]):
            fail(f"VERSION {key} should use semantic numeric format: {versions[key]}")

    for dirname in ["current", "deliverables"]:
        path = repo_root / "docs" / "test-design" / dirname
        if not path.is_dir():
            fail(f"Missing deliverable directory: {path}")
    for dirname in ["modules", "imports", "indexes"]:
        path = repo_root / "docs" / "test-assets" / dirname
        if not path.is_dir():
            fail(f"Missing internal test asset directory: {path}")

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

    for row in range(2, 5):
        function_point = cell_value(design_template, 4, f"D{row}")
        case_title = cell_value(design_template, 4, f"E{row}")
        if not function_point:
            fail(f"功能测试用例 sample function point must not be empty at row {row}")
        if not case_title:
            fail(f"功能测试用例 sample title must not be empty at row {row}")
        if not case_title.startswith(f"{function_point}-"):
            fail(f"功能测试用例 sample title must start with its 功能点 at row {row}: {case_title}")
        suffix = case_title.removeprefix(f"{function_point}-")
        if not suffix:
            fail(f"功能测试用例 sample title must include content after 功能点- at row {row}: {case_title}")
        if f"{function_point} -" in case_title or f"{function_point}- " in case_title:
            fail(f"功能测试用例 sample title must not include spaces around hyphen at row {row}: {case_title}")

    system_sheets = workbook_sheets(system_template)
    if not system_sheets:
        fail("System import template should contain at least one sheet")

    expected_product_map_sheets = [
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
    product_map_sheets = workbook_sheets(product_map)
    if product_map_sheets != expected_product_map_sheets:
        fail(
            "Product map sheets mismatch.\n"
            f"Expected: {expected_product_map_sheets}\n"
            f"Actual:   {product_map_sheets}"
        )

    expected_product_map_headers = {
        1: ["产品/系统", "一级模块", "二级模块", "三级模块", "页面/入口", "菜单路径/URL", "模块功能摘要", "归档测试设计路径", "覆盖状态", "最后更新时间", "待确认问题"],
        2: ["产品/系统", "业务对象", "来源模块", "消费模块", "关键字段", "关键状态", "状态生产者", "状态消费者", "创建用例ID", "状态变更用例ID", "归档测试设计路径", "待确认问题"],
        3: ["链路ID", "链路名称", "起始模块", "中间模块", "结束模块", "业务对象", "关键状态流转", "主流程用例ID", "跨模块用例ID", "依赖测试数据", "风险点", "归档测试设计路径"],
        4: ["产品/系统", "模块", "页面/入口", "菜单路径/URL", "元素名称/文案", "元素类型", "交互方式", "前置状态/权限", "关联用例ID", "覆盖状态", "发现来源", "最后更新时间", "备注"],
        5: ["产品/系统", "模块", "功能点", "用例ID", "用例标题", "测试类型", "执行方式", "是否可复用为前置条件", "是否跨模块", "关联业务对象", "关联业务链路", "归档测试设计路径", "最后更新时间"],
        6: ["产品/系统", "模块", "功能点", "能力/数据对象", "能力描述", "关键状态", "可复用前置条件", "关联用例ID", "归档测试设计路径", "限制/待确认问题", "最后更新时间"],
        7: ["产品/系统", "当前模块", "依赖模块", "依赖业务对象", "依赖功能点/能力", "依赖类型", "引用用例ID", "当前模块用例ID", "使用方式", "风险/待确认问题", "最后更新时间"],
        8: ["产品/系统", "模块", "数据对象", "测试数据标识", "数据用途", "可执行敏感操作", "创建/维护方式", "关联用例ID", "清理策略", "敏感信息处理", "最后更新时间"],
        9: ["变更ID", "需求/任务", "变更模块", "影响模块", "影响业务对象", "影响业务链路", "需复核历史用例ID", "需新增/修改用例", "风险等级", "处理状态", "分析日期", "备注"],
        10: ["版本", "日期", "变更人/来源", "变更类型", "影响模块", "变更内容", "是否已同步产品版图", "备注"],
    }
    for sheet_index, expected in expected_product_map_headers.items():
        actual = first_row_values(product_map, sheet_index)
        if actual != expected:
            fail(
                f"Product map headers mismatch on sheet {sheet_index}.\n"
                f"Expected: {expected}\n"
                f"Actual:   {actual}"
            )

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
    for path in [design_template, system_template, product_map]:
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
        repo_root / "docs" / "RULE_OWNERSHIP.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
    ]
    for path in architecture_files:
        if not path.exists():
            fail(f"Missing architecture file: {path}")

    batch_runs_dir = repo_root / "docs" / "test-assets" / "batch-runs"
    batch_templates_dir = batch_runs_dir / "templates"
    batch_plan_template = batch_templates_dir / "batch-plan-template.md"
    batch_status_template = batch_templates_dir / "batch-status-template.csv"
    batch_review_template = batch_templates_dir / "batch-review-template.md"
    page_discovery_template = batch_templates_dir / "page-discovery-template.csv"
    for path in [
        batch_runs_dir / "README.md",
        batch_plan_template,
        batch_status_template,
        batch_review_template,
        page_discovery_template,
    ]:
        if not path.exists():
            fail(f"Missing batch run asset: {path}")

    expected_batch_status_header = (
        "批次ID,一级模块,二级菜单,三级菜单/页面域,批次范围,状态,页面数,元素总数,已覆盖元素数,"
        "待确认元素数,功能用例数,性能场景数,异常用例数,边界用例数,权限/状态用例数,数据一致性用例数,"
        "页面遍历完成,功能用例完成,性能设计完成,异常边界权限覆盖完成,页面元素覆盖完成,产品版图已更新,"
        "覆盖质量自检,未覆盖元素清单路径,归档路径,待确认问题,下一步动作"
    )
    actual_batch_status_header = read_text(batch_status_template).splitlines()[0]
    if actual_batch_status_header != expected_batch_status_header:
        fail("batch-status-template.csv header changed unexpectedly")

    required_markers = [
        "正式测试设计",
        "测试系统导入用例",
        "独立导入文件",
        "测试用例模板.xlsx",
    ]
    for path in architecture_files:
        if path.name == "archive-and-index-guidelines.md":
            continue
        if path.name == "RULE_OWNERSHIP.md":
            continue
        assert_contains(path, required_markers[:2] if path.name == "AGENTS.md" else required_markers[:3])

    stale_markers = [
        "必须输出 `测试系统导入用例` Sheet",
        "正式交付时必须包含 `测试系统导入用例` Sheet",
        "请生成测试系统导入用例 Sheet",
        "模板包含 `测试系统导入用例` Sheet",
    ]
    for path in architecture_files:
        assert_not_contains(path, stale_markers)

    rule_mdc = repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc"
    rule_md = repo_root / ".codebuddy" / "rules" / "test-design-rule.md"
    if read_text(rule_mdc) != read_text(rule_md):
        fail("CodeBuddy Rule mirrors must stay identical: .codebuddy/.rules/test-design-rule.mdc and .codebuddy/rules/test-design-rule.md")

    ownership_file = repo_root / "docs" / "RULE_OWNERSHIP.md"
    ownership_markers = [
        "规则归属矩阵",
        "权威源",
        "可摘要引用",
        "不应承载完整规则",
        ".codebuddy/.rules/test-design-rule.mdc",
        ".codebuddy/rules/test-design-rule.md",
        ".codebuddy/skills/test-design/SKILL.md",
        "docs/test-design/excel-template-spec.md",
        "docs/test-design/archive-and-index-guidelines.md",
        "docs/UPGRADE.md",
        "docs/test-assets/batch-runs/README.md",
        "docs/test-assets/batch-runs/templates/",
        "README.md",
    ]
    assert_contains(ownership_file, ownership_markers)
    for path in [repo_root / "README.md", repo_root / "README_IMPORT.md", repo_root / "docs" / "ARCHITECTURE.md"]:
        assert_contains(path, ["docs/RULE_OWNERSHIP.md"])
    summary_only_files = [
        repo_root / "README.md",
        repo_root / "docs" / "test-design" / "README.md",
    ]
    full_rule_markers = [
        "测试用例必须尽可能详细",
        "批次队列",
        "不得重新生成各批完整用例",
        "分批默认按一级模块下的二级菜单",
    ]
    for path in summary_only_files:
        assert_not_contains(path, full_rule_markers)

    assert_contains(repo_root / "AGENTS.md", ["GitHub 提交信息必须使用中文"])

    execution_mode_markers = [
        "默认填写 `手动`",
        "自动化建议",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
    ]:
        assert_contains(path, execution_mode_markers)

    title_format_markers = [
        "功能点-当前用例标题",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / "docs" / "ARCHITECTURE.md",
    ]:
        assert_contains(path, title_format_markers)

    archive_markers = [
        "product-map.xlsx",
        "docs/test-assets/modules/",
        "docs/test-assets/imports/",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
    ]:
        assert_contains(path, archive_markers)

    deliverable_markers = [
        "docs/test-design/current/",
        "docs/test-design/deliverables/",
        "客户交付件",
        "不作为默认客户交付件",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-assets" / "README.md",
    ]:
        assert_contains(path, deliverable_markers)

    understanding_markers = [
        "产品理解摘要",
        "当前模块",
        "依赖模块",
        "业务链路",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
    ]:
        assert_contains(path, understanding_markers)

    upgrade_protection_markers = [
        "PROTECTED_ASSET_DIRS",
        "docs/test-assets/",
        "docs/test-design/current/",
        "docs/test-design/deliverables/",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / "README.md",
        repo_root / "README_IMPORT.md",
        repo_root / "UPGRADE_MANIFEST.md",
        repo_root / "docs" / "UPGRADE.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-assets" / "README.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        package_script,
        upgrade_script,
    ]:
        assert_contains(path, upgrade_protection_markers)

    upgrade_version_markers = [
        "framework_version",
        "asset_schema_version",
    ]
    for path in [
        version_file,
        upgrade_manifest,
        upgrade_doc,
        repo_root / "README.md",
        repo_root / "CODEBUDDY.md",
        repo_root / "AGENTS.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        package_script,
        upgrade_script,
    ]:
        assert_contains(path, upgrade_version_markers)

    for path in [upgrade_manifest, upgrade_doc, repo_root / "README.md", repo_root / "README_IMPORT.md"]:
        assert_contains(path, ["new-framework-upgrade-package.ps1", "upgrade-framework.ps1"])

    batch_design_markers = [
        "全产品",
        "大模块",
        "一级菜单",
        "二级菜单",
        "三级菜单",
        "菜单轮廓",
        "分批设计计划",
        "分批默认按一级模块下的二级菜单",
        "二级菜单过大",
        "二级菜单过小",
        "跨二级菜单强依赖",
        "超过一个二级菜单",
        "禁止直接生成完整测试用例",
        "批次队列",
        "覆盖质量自检",
        "才能进入下一批",
        "不得重新生成各批完整用例",
        "测试用例必须尽可能详细",
        "每个测试点",
        "每个页面元素",
        "不同测试方向",
        "组合条件",
        "禁用态/空状态/错误态",
        "可恢复路径",
        "笼统用例",
        "不得一次性生成完整测试用例",
        "跨模块汇总",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
    ]:
        assert_contains(path, batch_design_markers)

    batch_run_state_markers = [
        "docs/test-assets/batch-runs/",
        "batch-plan.md",
        "batch-status.csv",
        "batch-review.md",
        "page-discovery.csv",
        "artifacts/",
        "页面数",
        "元素总数",
        "已覆盖元素数",
        "功能用例数",
        "覆盖质量自检",
        "才能进入下一批",
        "最终汇总",
        "不得重新生成各批完整用例",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-assets" / "batch-runs" / "README.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
    ]:
        assert_contains(path, batch_run_state_markers)
    for path in [
        repo_root / "README.md",
        repo_root / "README_IMPORT.md",
        repo_root / "docs" / "RULE_OWNERSHIP.md",
        repo_root / "docs" / "test-assets" / "README.md",
    ]:
        assert_contains(path, ["docs/test-assets/batch-runs/"])
    assert_contains(batch_plan_template, ["批次执行计划", "batch-status.csv", "page-discovery.csv", "才能进入下一批", "不得重新生成各批完整用例"])
    assert_contains(batch_review_template, ["批次执行复盘", "页面数", "元素总数", "最终交付约束", "不得重新生成各批完整用例"])
    expected_page_discovery_header = (
        "批次ID,一级模块,二级菜单,三级菜单/页面域,页面/入口,菜单路径/URL,发现方式,角色/权限,数据状态,"
        "元素名称/文案,元素类型,交互方式,完整点击路径,预期/观察行为,业务依据/规则来源,测试数据来源,"
        "是否已生成用例,关联用例ID,覆盖状态,未覆盖/待确认原因,证据路径,备注"
    )
    actual_page_discovery_header = read_text(page_discovery_template).splitlines()[0]
    if actual_page_discovery_header != expected_page_discovery_header:
        fail("page-discovery-template.csv header changed unexpectedly")
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / "README.md",
        repo_root / "docs" / "ARCHITECTURE.md",
    ]:
        assert_contains(path, ["validate-test-design-deliverable.ps1"])

    batch_exploration_markers = [
        "当前批次",
        "浏览器",
        "computer use",
        "遍历",
        "所有可点击/可交互功能点",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
    ]:
        assert_contains(path, batch_exploration_markers)

    module_two_pass_markers = [
        "模块级粗遍历",
        "深遍历",
        "可点击",
        "可输入",
        "可测试元素",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
    ]:
        assert_contains(path, module_two_pass_markers)

    product_map_persistence_markers = [
        "不是临时分析结果",
        "必须沉淀",
        "product-map.xlsx",
        "产品模块地图",
        "页面元素地图",
        "业务对象地图",
        "业务链路地图",
        "模块能力索引",
        "跨模块依赖关系",
        "变更记录",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
    ]:
        assert_contains(path, product_map_persistence_markers)

    batch_complete_markers = [
        "每一批测试设计",
        "完整 test-design Skill 和 Rule",
        "不得因为分批而降级",
        "功能测试",
        "性能测试",
        "异常",
        "边界",
        "权限",
        "状态",
        "数据一致性",
        "风险",
        "自动化建议",
        "页面元素覆盖清单",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
    ]:
        assert_contains(path, batch_complete_markers)

    print("OK: test design templates are aligned and import template validations are preserved.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
