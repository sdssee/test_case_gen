# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
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

GLOBAL_INTERMEDIATE_SCAN_DIRS = [
    Path("docs/test-assets/batch-runs"),
    Path("docs/test-design/current"),
    Path("docs/test-design/deliverables"),
]

GLOBAL_INTERMEDIATE_EXTS = {".py", ".json", ".csv", ".md", ".txt", ".tmp"}

GLOBAL_INTERMEDIATE_ALLOWED_NAMES = {
    "README.md",
    "batch-plan.md",
    "batch-status.csv",
    "batch-review.md",
    "page-discovery.csv",
}

GLOBAL_INTERMEDIATE_NAME_PATTERNS = [
    "all_cases",
    "all-cases",
    "allcases",
    "cases_all",
    "test_cases_all",
    "full_product",
    "full-product",
    "fullproduct",
    "global_cases",
    "merged_cases",
    "case_pool",
    "casepool",
    "all_test_cases",
    "complete_cases",
    "全量用例",
    "全部用例",
    "完整用例",
    "统一用例",
    "用例池",
]

GLOBAL_INTERMEDIATE_CONTENT_MARKERS = [
    "all test cases",
    "all cases",
    "full product cases",
    "global cases",
    "merged cases",
    "case pool",
    "全量测试用例",
    "全部测试用例",
    "完整测试用例",
    "跨批次用例",
    "多个最小标题",
    "统一生成 Excel",
    "先集中写入",
]

GENERATED_BATCH_SCRIPT_PATTERNS = [
    "gen_batch*.py",
    "fix_batch*.py",
    "*batch*_cases.py",
]

CASE_BODY_MARKERS = [
    "用例ID",
    "用例 ID",
    "测试步骤",
    "预期结果",
    "测试用例名称",
    "case_id",
    "case id",
    "test steps",
    "expected result",
]

ENTRY_FILE_CHAR_LIMIT = 10000


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


def is_lightweight_entry(path: Path) -> bool:
    normalized = str(path).replace("\\", "/")
    return any(
        normalized.endswith(suffix)
        for suffix in [
            "AGENTS.md",
            "CODEBUDDY.md",
            ".codebuddy/skills/test-design/SKILL.md",
            ".codebuddy/.rules/test-design-rule.mdc",
            ".codebuddy/rules/test-design-rule.md",
        ]
    )


def lightweight_entry_reference_text(path: Path) -> str:
    for parent in [path.resolve(), *path.resolve().parents]:
        rules_dir = parent / "docs" / "test-design" / "rules"
        if rules_dir.is_dir():
            references = sorted(rules_dir.glob("*.md"))
            references.extend(
                [
                    parent / "docs" / "test-design" / "excel-template-spec.md",
                    parent / "docs" / "test-design" / "archive-and-index-guidelines.md",
                    parent / "docs" / "test-assets" / "batch-runs" / "README.md",
                ]
            )
            return "\n".join(read_text(item) for item in references if item.exists())
    return ""


def assert_contains(path: Path, markers: list[str]) -> None:
    text = read_text(path)
    reference_text = lightweight_entry_reference_text(path) if is_lightweight_entry(path) else ""
    for marker in markers:
        if marker not in text and marker not in reference_text:
            fail(f"{path.relative_to(path.parents[1])} is missing required marker: {marker}")


def assert_contains_across(paths: list[Path], markers: list[str], label: str) -> None:
    combined = "\n".join(read_text(path) for path in paths)
    for marker in markers:
        if marker not in combined:
            fail(f"{label} is missing required marker across rule sources: {marker}")


def assert_max_chars(path: Path, limit: int) -> None:
    length = len(read_text(path))
    if length > limit:
        fail(f"{path.relative_to(path.parents[1])} is too large for a lightweight AI entry: {length} > {limit} characters")


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


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def is_allowed_batch_run_file(path: Path, repo_root: Path) -> bool:
    batch_runs_dir = repo_root / "docs" / "test-assets" / "batch-runs"
    templates_dir = batch_runs_dir / "templates"
    if is_under(path, templates_dir):
        return True
    if path.name in GLOBAL_INTERMEDIATE_ALLOWED_NAMES:
        return True
    return False


def likely_global_intermediate_name(path: Path) -> bool:
    normalized = path.name.lower().replace(" ", "_")
    return any(pattern.lower() in normalized for pattern in GLOBAL_INTERMEDIATE_NAME_PATTERNS)


def likely_global_intermediate_content(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return False
    normalized = text.lower()
    has_global_marker = any(marker.lower() in normalized for marker in GLOBAL_INTERMEDIATE_CONTENT_MARKERS)
    case_marker_count = sum(1 for marker in CASE_BODY_MARKERS if marker.lower() in normalized)
    return has_global_marker and case_marker_count >= 2


def validate_no_global_intermediate_files(repo_root: Path) -> None:
    for relative_dir in GLOBAL_INTERMEDIATE_SCAN_DIRS:
        scan_dir = repo_root / relative_dir
        if not scan_dir.exists():
            continue
        for path in scan_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in GLOBAL_INTERMEDIATE_EXTS:
                continue
            if is_allowed_batch_run_file(path, repo_root):
                continue
            if likely_global_intermediate_name(path) or likely_global_intermediate_content(path):
                fail(
                    "Forbidden global test-case intermediate file found: "
                    f"{path.relative_to(repo_root)}. "
                    "Large-scope work must keep case bodies inside the current batch workbook, "
                    "page-discovery.csv, and batch-status.csv instead of aggregating all cases first."
                )


def validate_no_generated_batch_scripts_in_framework_scripts(repo_root: Path) -> None:
    scripts_dir = repo_root / "scripts"
    offenders: list[Path] = []
    for pattern in GENERATED_BATCH_SCRIPT_PATTERNS:
        offenders.extend(path for path in scripts_dir.glob(pattern) if path.is_file())
    if offenders:
        relative = ", ".join(str(path.relative_to(repo_root)) for path in sorted(offenders))
        fail(
            "Generated batch helper scripts must not stay in framework scripts/: "
            f"{relative}. Put current-batch scripts under "
            "docs/test-assets/batch-runs/<task>/artifacts/scripts/ and remove them after use."
        )


def validate_no_root_batch_artifacts_dir(repo_root: Path) -> None:
    root_artifacts = repo_root / "docs" / "test-assets" / "batch-runs" / "artifacts"
    if root_artifacts.exists():
        fail(
            "Batch artifacts must live under docs/test-assets/batch-runs/<task>/artifacts/, "
            "not the shared batch-runs/artifacts directory."
        )


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
    excel_tools = repo_root / "scripts" / "test_design_excel_tools.py"
    generated_python_validator = repo_root / "scripts" / "validate-generated-python-scripts.py"
    generated_python_validator_ps1 = repo_root / "scripts" / "validate-generated-python-scripts.ps1"
    rules_dir = repo_root / "docs" / "test-design" / "rules"
    rule_docs = [
        rules_dir / "README.md",
        rules_dir / "case-design.md",
        rules_dir / "page-discovery.md",
        rules_dir / "batch-run.md",
        rules_dir / "excel-deliverable.md",
        rules_dir / "import-template.md",
        rules_dir / "product-map-sync.md",
        rules_dir / "data-safety.md",
        rules_dir / "dfx-test-strategy.md",
    ]
    lightweight_entries = [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
    ]

    if not design_template.exists():
        fail(f"Missing design template: {design_template}")
    if not system_template.exists():
        fail(f"Missing system import template: {system_template}")
    if not product_map.exists():
        fail(f"Missing product map: {product_map}")
    for path in [
        version_file,
        upgrade_manifest,
        upgrade_doc,
        package_script,
        upgrade_script,
        deliverable_validator,
        deliverable_validator_ps1,
        excel_tools,
        generated_python_validator,
        generated_python_validator_ps1,
        *rule_docs,
    ]:
        if not path.exists():
            fail(f"Missing upgrade mechanism file: {path}")
    for path in lightweight_entries:
        assert_max_chars(path, ENTRY_FILE_CHAR_LIMIT)

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

    validate_no_global_intermediate_files(repo_root)
    validate_no_generated_batch_scripts_in_framework_scripts(repo_root)
    validate_no_root_batch_artifacts_dir(repo_root)

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
        "覆盖质量自检,未覆盖元素清单路径,归档路径,导入文件路径,导入文件已生成,最小标题路径,待确认问题,下一步动作"
    )
    actual_batch_status_header = read_text(batch_status_template).splitlines()[0]
    if actual_batch_status_header != expected_batch_status_header:
        fail("batch-status-template.csv header changed unexpectedly")
    with batch_status_template.open("r", encoding="utf-8-sig", newline="") as fp:
        batch_status_rows = list(csv.reader(fp))
    if len(batch_status_rows) < 2 or len(batch_status_rows[1]) != len(batch_status_rows[0]):
        fail("batch-status-template.csv sample row must have the same column count as its header")

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
        "分批默认按一级模块下的最小标题路径",
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
        "风险项",
        "待确认问题",
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

    risk_confirmation_markers = [
        "正式写测试用例前",
        "风险项与待确认问题",
        "用户确认",
        "动态调整",
        "测试范围",
        "测试数据",
        "预期结果",
        "风险等级",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "rules" / "product-map-sync.md",
        repo_root / "docs" / "test-design" / "rules" / "case-design.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "ARCHITECTURE.md",
    ]:
        assert_contains(path, risk_confirmation_markers)

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
    assert_contains(package_script, ["Test-GeneratedPath", "__pycache__", ".pyc"])

    batch_design_markers = [
        "全产品",
        "大模块",
        "一级菜单",
        "二级菜单",
        "三级菜单",
        "菜单轮廓",
        "分批设计计划",
        "最小标题路径",
        "最深标题级别",
        "禁止合并",
        "禁止再拆分",
        "逐个匹配校验",
        "超过一个最小标题",
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

    selection_control_markers = [
        "选择类控件",
        "不得只展开查看选项",
        "代表性选项",
        "选项取值/输入值",
        "联动/依赖变化",
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
        assert_contains(path, selection_control_markers)

    input_control_markers = [
        "输入类控件",
        "不得只观察字段存在",
        "实际输入",
        "结果分支/后续状态",
        "真实提示",
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
        assert_contains(path, input_control_markers)

    create_flow_markers = [
        "新增类流程",
        "实填实走",
        "详情页",
        "下一级页面",
        "停留页面",
        "可恢复路径",
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
        assert_contains(path, create_flow_markers)

    existing_data_probe_markers = [
        "既有数据",
        "只读深探",
        "确认弹窗",
        "二次确认",
        "取消路径",
        "复制既有数据",
        "改名或改编码",
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
        assert_contains(path, existing_data_probe_markers)

    incremental_supplement_markers = [
        "增量补充",
        "二次补充",
        "覆盖缺口",
        "补充批次",
        "不得只追加用例",
        "重新页面实探",
        "复用已有用例",
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
        assert_contains(path, incremental_supplement_markers)
    assert_contains(batch_plan_template, incremental_supplement_markers)

    batch_run_state_markers = [
        "docs/test-assets/batch-runs/",
        "batch-plan.md",
        "batch-status.csv",
        "batch-review.md",
        "page-discovery.csv",
        "artifacts/",
        "init-batch-run",
        "导入文件路径",
        "导入文件已生成",
        "最小标题路径",
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
    assert_contains(batch_plan_template, ["批次执行计划", "最小标题路径", "最深标题级别", "禁止合并", "禁止再拆分", "batch-status.csv", "page-discovery.csv", "导入文件", "才能进入下一批", "不得重新生成各批完整用例"])
    assert_contains(batch_plan_template, ["标准模板", "CSV writer", "示例产品", "<valid_api_key>", "执行中或待开始"])
    assert_contains(batch_review_template, ["批次执行复盘", "页面数", "元素总数", "导入文件路径", "最终交付约束", "不得重新生成各批完整用例"])
    expected_page_discovery_header = (
        "批次ID,一级模块,二级菜单,三级菜单/页面域,最小标题路径,页面/入口,菜单路径/URL,发现方式,角色/权限,数据状态,"
        "元素名称/文案,元素类型,交互方式,选项取值/输入值,联动/依赖变化,结果分支/后续状态,完整点击路径,预期/观察行为,业务依据/规则来源,测试数据来源,"
        "是否已生成用例,关联用例ID,覆盖状态,未覆盖/待确认原因,证据路径,备注"
    )
    actual_page_discovery_header = read_text(page_discovery_template).splitlines()[0]
    if actual_page_discovery_header != expected_page_discovery_header:
        fail("page-discovery-template.csv header changed unexpectedly")
    with page_discovery_template.open("r", encoding="utf-8-sig", newline="") as fp:
        page_discovery_rows = list(csv.reader(fp))
    if len(page_discovery_rows) < 2 or len(page_discovery_rows[1]) != len(page_discovery_rows[0]):
        fail("page-discovery-template.csv sample row must have the same column count as its header")

    no_global_intermediate_markers = [
        "承载全量测试用例正文",
        "单一中间文件",
        "Python",
        "JSON",
        "Markdown",
        "200KB",
        "256KB",
        "大 Python",
        "大 JSON",
        "当前批次的模板填充、格式转换或校验",
        "artifacts/scripts",
        "统一生成 Excel",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / "docs" / "test-assets" / "batch-runs" / "README.md",
        batch_plan_template,
    ]:
        assert_contains(path, no_global_intermediate_markers)
    generated_python_script_markers = [
        "repr()",
        "json.dumps(..., ensure_ascii=False)",
        "validate-generated-python-scripts.ps1",
        "单文件大小",
        "JSON 语法",
        "中文弯引号",
        "未转义双引号",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / "docs" / "test-assets" / "batch-runs" / "README.md",
        batch_plan_template,
    ]:
        assert_contains(path, generated_python_script_markers)
    strict_batch_quality_markers = [
        "自定义精简表头",
        "CSV writer",
        "字段错位",
        "执行中或待开始",
        "示例产品",
        "用例资产索引",
        "页面元素地图",
        "<valid_api_key>",
        "<test_token>",
        "<test_service_url>",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-assets" / "batch-runs" / "README.md",
        repo_root / "docs" / "test-assets" / "batch-runs" / "templates" / "batch-plan-template.md",
        repo_root / "docs" / "test-design" / "archive-and-index-guidelines.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
    ]:
        assert_contains(path, strict_batch_quality_markers)
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / "README.md",
        repo_root / "docs" / "ARCHITECTURE.md",
    ]:
        assert_contains(path, ["validate-test-design-deliverable.ps1"])

    operation_navigation_markers = [
        "操作步骤",
        "不得默认",
        "当前模块页面",
        "完整导航路径",
        "系统或项目入口",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
    ]:
        assert_contains(path, operation_navigation_markers)

    assert_contains(repo_root / "docs" / "test-design" / "rules" / "data-safety.md", ["<product_login_url>", "环境地址"])
    assert_contains(repo_root / "docs" / "test-design" / "rules" / "case-design.md", ["闭环", "取消或关闭", "DFX 测试策略落地"])
    assert_contains(
        repo_root / "docs" / "test-design" / "rules" / "dfx-test-strategy.md",
        ["DFX 12", "DFT", "DFP", "DFI", "DFC", "DFS", "DFR", "DFM", "DFU", "DFD", "DFO", "DFB", "压力极限", "DFX 覆盖评估", "适用", "不适用", "需补充证据"],
    )
    assert_contains(repo_root / "README.md", ["dfx-test-strategy.md", "DFX 不替代原测试维度"])
    assert_contains(repo_root / "docs" / "RULE_OWNERSHIP.md", ["DFX 测试策略矩阵", "dfx-test-strategy.md"])
    assert_contains(repo_root / ".codebuddy" / "rules" / "test-design-rule.md", ["docs/test-design/rules/dfx-test-strategy.md"])
    assert_contains(repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc", ["docs/test-design/rules/dfx-test-strategy.md"])
    assert_contains(repo_root / "docs" / "test-design" / "excel-template-spec.md", ["DFX 12 维度", "dfx-test-strategy.md"])
    dfx_pre_eval_markers = [
        "正式写测试用例前",
        "DFX 覆盖评估",
        "适用",
        "不适用",
        "待确认",
        "需补充证据",
    ]
    for path in [
        repo_root / "AGENTS.md",
        repo_root / "CODEBUDDY.md",
        repo_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        repo_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        repo_root / ".codebuddy" / "rules" / "test-design-rule.md",
        repo_root / "docs" / "test-design" / "rules" / "dfx-test-strategy.md",
        repo_root / "docs" / "test-design" / "rules" / "case-design.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
        repo_root / "docs" / "ARCHITECTURE.md",
    ]:
        assert_contains(path, dfx_pre_eval_markers)
    assert_contains(repo_root / "docs" / "test-design" / "rules" / "excel-deliverable.md", ["表格对象", "修复提示"])
    assert_contains(repo_root / "docs" / "test-design" / "rules" / "batch-run.md", ["batch-runs/<task>/artifacts", "根目录 artifacts"])
    assert_contains(
        deliverable_validator,
        [
            "assert_complete_operation_steps",
            "entry_markers",
            "navigation_markers",
            "full navigation",
            "must not assume",
            "validate_product_map_sync",
            "validate_import_workbook",
            "validate_batch_granularity",
            "validate_batch_import_workbooks",
            "assert_multiline_cells_wrapped",
            "assert_no_residual_markers",
            "assert_no_unmasked_value",
            "assert_transient_flow_closed",
            "validate_table_ranges",
            "validate_batch_artifacts_location",
            "validate_batch_run_directory_from_page_discovery",
            "validate_batch_file_consistency",
            "validate_batch_plan",
            "assert_data_rows_follow_sample_styles",
            "assert_dropdown_validations_cover_rows",
            "MULTI_LEAF_SEPARATORS",
            "BATCH_EXPECTED_HEADERS",
            "PAGE_DISCOVERY_EXPECTED_HEADERS",
            "csv_rows_with_exact_header",
            "assert_no_sensitive_values",
            "PRODUCT_MAP_REQUIRED_REAL_SHEETS",
            "SENSITIVE_VALUE_PATTERNS",
            "最小标题路径",
            "--import-workbook",
            "default_page_discovery_path",
            "default_product_map_path",
            "--product-map",
            "--page-discovery",
            "--batch-status",
            "page-discovery.csv",
            "product-map",
            "generated workbook copies",
        ],
    )
    assert_contains(
        deliverable_validator_ps1,
        ["ProductMapPath", "PageDiscoveryPath", "ImportWorkbookPath", "--product-map", "--page-discovery", "--import-workbook", "page-discovery.csv"],
    )
    assert_contains(
        excel_tools,
        [
            "generate-import",
            "fix-formal-styles",
            "init-batch-run",
            "finalize-deliverables",
            "sync-product-map",
            "header_map",
            "IMPORT_AUTO_FIELDS",
            "wrap_text=True",
            "resize_worksheet_tables",
            "table.ref",
            "性能测试设计",
            "update_batch_status_paths",
            "sync_batch_markdown_paths",
            "cleanup_batch_artifacts",
            "--batch-status is required when --page-discovery is provided",
            "apply_template_workbook_format",
            "extend_validation_ranges",
            "sync_product_map",
        ],
    )
    assert_contains(
        generated_python_validator,
        ["FORBIDDEN_QUOTE_CHARS", "py_compile", "MAX_PYTHON_BYTES", "MAX_JSON_BYTES", "json.load"],
    )
    assert_contains(
        generated_python_validator_ps1,
        ["validate-generated-python-scripts.py", "Python was not found in PATH"],
    )
    for path in [
        repo_root / "README.md",
        repo_root / "README_IMPORT.md",
        repo_root / "docs" / "UPGRADE.md",
        repo_root / "docs" / "ARCHITECTURE.md",
        repo_root / "docs" / "test-design" / "excel-template-spec.md",
    ]:
        assert_contains(path, ["scripts/test_design_excel_tools.py", "generate-import"])
    assert_contains(
        repo_root / "docs" / "RULE_OWNERSHIP.md",
        ["scripts/test_design_excel_tools.py", "scripts/validate-generated-python-scripts.py", "交付件质量校验"],
    )

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
