# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import posixpath
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

BATCH_REQUIRED_HEADERS = [
    "批次ID",
    "状态",
    "页面数",
    "元素总数",
    "已覆盖元素数",
    "待确认元素数",
    "功能用例数",
    "性能场景数",
    "异常用例数",
    "边界用例数",
    "权限/状态用例数",
    "数据一致性用例数",
    "页面遍历完成",
    "功能用例完成",
    "性能设计完成",
    "异常边界权限覆盖完成",
    "页面元素覆盖完成",
    "产品版图已更新",
    "覆盖质量自检",
    "导入文件路径",
    "导入文件已生成",
]

BATCH_NUMBER_FIELDS = [
    "页面数",
    "元素总数",
    "已覆盖元素数",
    "待确认元素数",
    "功能用例数",
    "性能场景数",
    "异常用例数",
    "边界用例数",
    "权限/状态用例数",
    "数据一致性用例数",
]

BATCH_PASS_BOOLEAN_FIELDS = [
    "页面遍历完成",
    "功能用例完成",
    "性能设计完成",
    "异常边界权限覆盖完成",
    "页面元素覆盖完成",
    "产品版图已更新",
    "导入文件已生成",
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

IMPORT_ALLOWED_VALUES = {
    "测试类型": {"功能测试", "性能规格测试", "可靠性测试", "兼容性测试", "可维护性测试", "安全性测试", "易用性测试"},
    "测试用例级别": {"L1", "L2", "L3", "L4"},
    "执行方式": {"自动化", "手动"},
}

IMPORT_REQUIRED_FIELDS = ["一级模块名称", "二级模块名称", "三级模块名称", "测试用例名称", "测试类型", "测试用例级别", "执行方式"]
IMPORT_AUTO_FIELDS = ["测试用例系统编号", "作者"]

PAGE_DISCOVERY_REQUIRED_HEADERS = [
    "批次ID",
    "页面/入口",
    "菜单路径/URL",
    "元素名称/文案",
    "元素类型",
    "交互方式",
    "完整点击路径",
    "是否已生成用例",
    "关联用例ID",
    "覆盖状态",
]

PRODUCT_MAP_PAGE_ELEMENT_HEADERS = [
    "产品/系统",
    "模块",
    "页面/入口",
    "菜单路径/URL",
    "元素名称/文案",
    "元素类型",
    "交互方式",
    "关联用例ID",
    "覆盖状态",
    "发现来源",
]

PRODUCT_MAP_CASE_INDEX_HEADERS = [
    "产品/系统",
    "模块",
    "功能点",
    "用例ID",
    "用例标题",
    "归档测试设计路径",
]

PRODUCT_MAP_CHANGE_HEADERS = [
    "版本",
    "日期",
    "变更人/来源",
    "变更类型",
    "影响模块",
    "变更内容",
    "是否已同步产品版图",
]


def fail(message: str) -> None:
    raise AssertionError(message)


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(t.text or "" for t in si.findall(".//x:t", NS)) for si in root.findall("x:si", NS)]


def cell_text(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//x:t", NS)).strip()
    value = cell.find("x:v", NS)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        return shared[int(value.text)].strip()
    return value.text.strip()


def column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref)
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def workbook_sheet_paths(zf: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("rel:Relationship", NS)
        if rel.attrib.get("Type", "").endswith("/worksheet")
    }
    paths: dict[str, str] = {}
    for sheet in workbook.findall("x:sheets/x:sheet", NS):
        name = sheet.attrib["name"]
        rel_id = sheet.attrib[f"{{{NS['r']}}}id"]
        target = rel_targets[rel_id]
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        paths[name] = path
    return paths


def sheet_rows(path: Path, sheet_name: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as zf:
        paths = workbook_sheet_paths(zf)
        if sheet_name not in paths:
            fail(f"Workbook is missing sheet: {sheet_name}")
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read(paths[sheet_name]))
    rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        values: list[str] = []
        for cell in row.findall("x:c", NS):
            index = column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append("")
            values[index] = cell_text(cell, shared)
        rows.append(values)
    return rows


def row_dicts(rows: list[list[str]], sheet_name: str) -> list[dict[str, str]]:
    if not rows:
        fail(f"{sheet_name} has no header row")
    headers = rows[0]
    result: list[dict[str, str]] = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        result.append({header: row[i].strip() if i < len(row) else "" for i, header in enumerate(headers) if header})
    return result


def assert_numbered(text: str, label: str) -> None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        fail(f"{label} must not be empty")
    for line in lines:
        if not re.match(r"^\d+\.\s*\S+", line):
            fail(f"{label} must use numbered lines like '1. ...': {line}")


def assert_complete_operation_steps(text: str, label: str) -> None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        fail(f"{label} must include full navigation and operation steps, not a single short sentence")
    first_steps = "\n".join(lines[:3])
    entry_markers = ["登录", "打开系统", "访问系统", "进入系统", "打开平台", "访问平台", "进入平台", "URL"]
    navigation_markers = ["一级", "二级", "三级", "菜单", "模块", "导航", "路径", ">", "页面"]
    if not any(marker in first_steps for marker in entry_markers):
        fail(f"{label} must start from system/project entry and include navigation path to target function")
    if not any(marker in first_steps for marker in navigation_markers):
        fail(f"{label} must include complete menu/module navigation before operating target controls")
    if re.match(r"^1\.\s*在[^，,。]*页面", lines[0]):
        fail(f"{label} must not assume the tester is already on the target module page")


def parse_ids(text: str) -> set[str]:
    return {item.strip() for item in re.split(r"[,，;；\s]+", text) if item.strip()}


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip().lower()


def normalized_key(*values: str) -> tuple[str, ...]:
    return tuple(normalize(value) for value in values)


def csv_row_dicts(path: Path, required: list[str], label: str) -> list[dict[str, str]]:
    if not path.exists():
        fail(f"{label} not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        headers = reader.fieldnames or []
        missing = [header for header in required if header not in headers]
        if missing:
            fail(f"{label} is missing headers: {missing}")
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]


def require_headers(rows: list[list[str]], required: list[str], sheet_name: str) -> None:
    headers = set(rows[0] if rows else [])
    missing = [header for header in required if header not in headers]
    if missing:
        fail(f"{sheet_name} is missing headers: {missing}")


def first_sheet_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as zf:
        paths = workbook_sheet_paths(zf)
        if not paths:
            fail(f"Workbook has no sheets: {path}")
        first_sheet = next(iter(paths))
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read(paths[first_sheet]))
    rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        values: list[str] = []
        for cell in row.findall("x:c", NS):
            index = column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append("")
            values[index] = cell_text(cell, shared)
        rows.append(values)
    return rows


def first_worksheet_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        paths = workbook_sheet_paths(zf)
        if not paths:
            fail(f"Workbook has no sheets: {path}")
        first_sheet = next(iter(paths))
        return zf.read(paths[first_sheet]).decode("utf-8", errors="ignore")


def validate_workbook(workbook: Path) -> dict[str, object]:
    if not workbook.exists():
        fail(f"Workbook not found: {workbook}")
    with zipfile.ZipFile(workbook) as zf:
        sheet_names = list(workbook_sheet_paths(zf))
    if sheet_names != EXPECTED_SHEETS:
        fail(f"Workbook sheets mismatch. Expected {EXPECTED_SHEETS}, got {sheet_names}")

    function_rows_raw = sheet_rows(workbook, "功能测试用例")
    require_headers(function_rows_raw, ["用例 ID", "功能点", "用例标题", "操作步骤", "预期结果"], "功能测试用例")
    function_rows = row_dicts(function_rows_raw, "功能测试用例")
    if not function_rows:
        fail("功能测试用例 must contain at least one case")

    case_ids: set[str] = set()
    case_titles: dict[str, str] = {}
    case_function_points: dict[str, str] = {}
    for index, row in enumerate(function_rows, start=2):
        case_id = row.get("用例 ID", "")
        function_point = row.get("功能点", "")
        title = row.get("用例标题", "")
        if not case_id:
            fail(f"功能测试用例 row {index} is missing 用例 ID")
        if case_id in case_ids:
            fail(f"Duplicate 用例 ID: {case_id}")
        case_ids.add(case_id)
        case_titles[case_id] = title
        case_function_points[case_id] = function_point
        if not function_point:
            fail(f"功能测试用例 row {index} is missing 功能点")
        if not title.startswith(f"{function_point}-"):
            fail(f"功能测试用例 row {index} title must start with 功能点-: {title}")
        assert_numbered(row.get("操作步骤", ""), f"功能测试用例 row {index} 操作步骤")
        assert_complete_operation_steps(row.get("操作步骤", ""), f"功能测试用例 row {index} 操作步骤")
        assert_numbered(row.get("预期结果", ""), f"功能测试用例 row {index} 预期结果")
        if row.get("前置条件"):
            assert_numbered(row["前置条件"], f"功能测试用例 row {index} 前置条件")

    performance_rows_raw = sheet_rows(workbook, "性能测试设计")
    require_headers(performance_rows_raw, ["性能场景 ID", "业务链路", "性能测试类型", "是否纳入本轮测试"], "性能测试设计")
    performance_rows = row_dicts(performance_rows_raw, "性能测试设计")
    if not performance_rows:
        fail("性能测试设计 must contain at least one scenario or explicit not-applicable row")

    coverage_rows_raw = sheet_rows(workbook, "页面元素覆盖清单")
    require_headers(
        coverage_rows_raw,
        ["元素 ID", "元素名称/文案", "元素类型", "覆盖用例 ID", "覆盖状态", "待确认问题/备注"],
        "页面元素覆盖清单",
    )
    coverage_rows = row_dicts(coverage_rows_raw, "页面元素覆盖清单")
    valid_status = {"已覆盖", "不适用", "不测范围", "待确认"}
    for index, row in enumerate(coverage_rows, start=2):
        element = row.get("元素名称/文案", "")
        if not element:
            fail(f"页面元素覆盖清单 row {index} is missing 元素名称/文案")
        status = row.get("覆盖状态", "")
        if status not in valid_status:
            fail(f"页面元素覆盖清单 row {index} has invalid 覆盖状态: {status}")
        linked_ids = parse_ids(row.get("覆盖用例 ID", ""))
        if status == "已覆盖":
            if not linked_ids:
                fail(f"页面元素覆盖清单 row {index} is 已覆盖 but missing 覆盖用例 ID")
            unknown = sorted(linked_ids - case_ids)
            if unknown:
                fail(f"页面元素覆盖清单 row {index} references unknown case IDs: {unknown}")
        elif not row.get("待确认问题/备注", ""):
            fail(f"页面元素覆盖清单 row {index} status {status} must explain reason in 待确认问题/备注")

    return {
        "case_ids": case_ids,
        "case_titles": case_titles,
        "case_function_points": case_function_points,
        "coverage_rows": coverage_rows,
    }


def validate_import_workbook(import_workbook: Path, workbook_data: dict[str, object]) -> None:
    if not import_workbook.exists():
        fail(f"Import workbook not found: {import_workbook}")
    with zipfile.ZipFile(import_workbook) as zf:
        sheet_names = list(workbook_sheet_paths(zf))
    if sheet_names == EXPECTED_SHEETS or "测试系统导入用例" in sheet_names:
        fail("Import workbook must be a copy of 测试用例模板.xlsx, not the formal test design workbook")

    rows_raw = first_sheet_rows(import_workbook)
    if not rows_raw:
        fail("Import workbook has no header row")
    headers = rows_raw[0]
    if headers[: len(IMPORT_HEADERS)] != IMPORT_HEADERS:
        fail(f"Import workbook headers mismatch. Expected {IMPORT_HEADERS}, got {headers}")

    rows = row_dicts(rows_raw, "测试系统导入文件")
    if not rows:
        fail("Import workbook must contain mapped test cases")

    case_titles = workbook_data["case_titles"]
    assert isinstance(case_titles, dict)
    formal_titles = set(case_titles.values())
    imported_titles: set[str] = set()
    for index, row in enumerate(rows, start=2):
        for field in IMPORT_REQUIRED_FIELDS:
            if not row.get(field):
                fail(f"Import workbook row {index} is missing required field: {field}")
        for field in IMPORT_AUTO_FIELDS:
            if row.get(field):
                fail(f"Import workbook row {index} must leave auto-generated field blank: {field}")
        for field, allowed in IMPORT_ALLOWED_VALUES.items():
            value = row.get(field, "")
            if value not in allowed:
                fail(f"Import workbook row {index} has invalid {field}: {value}")
        if row.get("执行方式") == "自动化":
            note = row.get("备注", "") + row.get("标签", "") + row.get("测试用例说明", "")
            if not any(marker in note for marker in ["自动化资产", "脚本", "流水线", "API自动化", "UI自动化"]):
                fail(f"Import workbook row {index} uses 自动化 but does not reference an implemented automation asset")
        title = row.get("测试用例名称", "")
        if "-" not in title or " -" in title or "- " in title:
            fail(f"Import workbook row {index} 测试用例名称 must use 功能点-当前用例标题 without spaces: {title}")
        assert_numbered(row.get("测试步骤描述", ""), f"Import workbook row {index} 测试步骤描述")
        assert_complete_operation_steps(row.get("测试步骤描述", ""), f"Import workbook row {index} 测试步骤描述")
        assert_numbered(row.get("测试步骤预期结果", ""), f"Import workbook row {index} 测试步骤预期结果")
        if row.get("前置条件"):
            assert_numbered(row["前置条件"], f"Import workbook row {index} 前置条件")
        imported_titles.add(title)

    missing_titles = sorted(formal_titles - imported_titles)
    if missing_titles:
        fail(f"Import workbook is missing formal function cases: {missing_titles[:10]}")

    xml = first_worksheet_xml(import_workbook)
    for marker, label in {
        'sqref="R2:R2001"': "测试类型",
        'sqref="S2:S2001"': "测试用例级别",
        'sqref="T2:T2001"': "执行方式",
    }.items():
        if marker not in xml:
            fail(f"Import workbook is missing preserved {label} dropdown validation: {marker}")


def positive_int(value: str, field: str, batch_id: str) -> int:
    if not re.fullmatch(r"\d+", value or ""):
        fail(f"batch {batch_id} field {field} must be a non-negative integer: {value}")
    return int(value)


def validate_batch_status(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        fail(f"Batch status file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        headers = reader.fieldnames or []
        missing = [header for header in BATCH_REQUIRED_HEADERS if header not in headers]
        if missing:
            fail(f"batch-status.csv is missing headers: {missing}")
        rows = [row for row in reader if row.get("批次ID")]
    if not rows:
        fail("batch-status.csv must contain at least one batch row")

    for row in rows:
        batch_id = row["批次ID"]
        numbers = {field: positive_int(row.get(field, ""), field, batch_id) for field in BATCH_NUMBER_FIELDS}
        if numbers["已覆盖元素数"] > numbers["元素总数"]:
            fail(f"batch {batch_id} 已覆盖元素数 cannot exceed 元素总数")
        if numbers["待确认元素数"] > numbers["元素总数"]:
            fail(f"batch {batch_id} 待确认元素数 cannot exceed 元素总数")
        if row.get("覆盖质量自检") == "通过":
            for field in ["页面数", "元素总数", "已覆盖元素数", "功能用例数", "性能场景数"]:
                if numbers[field] <= 0:
                    fail(f"batch {batch_id} cannot pass 覆盖质量自检 with {field}=0")
            for field in BATCH_PASS_BOOLEAN_FIELDS:
                if row.get(field) != "是":
                    fail(f"batch {batch_id} cannot pass 覆盖质量自检 when {field} is not 是")
            if not row.get("导入文件路径"):
                fail(f"batch {batch_id} cannot pass 覆盖质量自检 without 导入文件路径")
    return rows


def validate_batch_review(batch_status: Path, batch_rows: list[dict[str, str]]) -> None:
    review_path = batch_status.resolve().parent / "batch-review.md"
    if not review_path.exists():
        fail(f"batch-review.md not found beside batch-status.csv: {review_path}")
    text = review_path.read_text(encoding="utf-8-sig")
    completed_rows = [
        row for row in batch_rows
        if row.get("覆盖质量自检") == "通过" or row.get("状态") in {"已完成", "完成"}
    ]
    for row in completed_rows:
        batch_id = row.get("批次ID", "")
        if batch_id and batch_id not in text:
            fail(f"batch-review.md must include completed batch: {batch_id}")
        stale_pattern = rf"\|\s*{re.escape(batch_id)}\s*\|\s*待开始\s*\|\s*0\s*\|\s*0\s*\|"
        if re.search(stale_pattern, text):
            fail(f"batch-review.md still contains stale template row for completed batch: {batch_id}")
        for field in ["归档路径", "导入文件路径"]:
            value = row.get(field, "")
            if value and value not in text:
                fail(f"batch-review.md must reference {field} for completed batch {batch_id}: {value}")


def validate_product_map_sync(
    workbook_data: dict[str, object],
    product_map: Path,
    page_discovery: Path,
) -> None:
    discovery_rows = csv_row_dicts(page_discovery, PAGE_DISCOVERY_REQUIRED_HEADERS, "page-discovery.csv")
    if not discovery_rows:
        fail("page-discovery.csv must contain at least one discovery row when product map sync validation is enabled")

    if not product_map.exists():
        fail(f"Product map not found: {product_map}")

    product_page_rows_raw = sheet_rows(product_map, "页面元素地图")
    product_case_rows_raw = sheet_rows(product_map, "用例资产索引")
    product_change_rows_raw = sheet_rows(product_map, "变更记录")
    require_headers(product_page_rows_raw, PRODUCT_MAP_PAGE_ELEMENT_HEADERS, "product-map 页面元素地图")
    require_headers(product_case_rows_raw, PRODUCT_MAP_CASE_INDEX_HEADERS, "product-map 用例资产索引")
    require_headers(product_change_rows_raw, PRODUCT_MAP_CHANGE_HEADERS, "product-map 变更记录")

    product_page_rows = row_dicts(product_page_rows_raw, "product-map 页面元素地图")
    product_case_rows = row_dicts(product_case_rows_raw, "product-map 用例资产索引")
    product_change_rows = row_dicts(product_change_rows_raw, "product-map 变更记录")
    if not product_page_rows:
        fail("product-map 页面元素地图 must contain synced page elements")
    if not product_case_rows:
        fail("product-map 用例资产索引 must contain synced case assets")
    if not product_change_rows:
        fail("product-map 变更记录 must record this product map sync")

    coverage_rows = workbook_data["coverage_rows"]
    case_ids = workbook_data["case_ids"]
    case_titles = workbook_data["case_titles"]
    case_function_points = workbook_data["case_function_points"]
    assert isinstance(coverage_rows, list)
    assert isinstance(case_ids, set)
    assert isinstance(case_titles, dict)
    assert isinstance(case_function_points, dict)

    workbook_elements = {
        normalized_key(row.get("页面/入口", ""), row.get("元素名称/文案", ""))
        for row in coverage_rows
        if row.get("页面/入口") and row.get("元素名称/文案")
    }
    product_elements = {
        normalized_key(row.get("页面/入口", ""), row.get("元素名称/文案", ""))
        for row in product_page_rows
        if row.get("页面/入口") and row.get("元素名称/文案")
    }
    product_case_ids = {row.get("用例ID", "") for row in product_case_rows if row.get("用例ID")}

    for index, row in enumerate(discovery_rows, start=2):
        page = row.get("页面/入口", "")
        element = row.get("元素名称/文案", "")
        if not page or not element:
            fail(f"page-discovery.csv row {index} must include 页面/入口 and 元素名称/文案")
        if normalized_key(page, element) not in workbook_elements:
            fail(f"page-discovery.csv row {index} element is missing from workbook 页面元素覆盖清单: {page} / {element}")
        if normalized_key(page, element) not in product_elements:
            fail(f"page-discovery.csv row {index} element is missing from product-map 页面元素地图: {page} / {element}")

        generated = row.get("是否已生成用例", "")
        linked_ids = parse_ids(row.get("关联用例ID", ""))
        if generated == "是":
            if not linked_ids:
                fail(f"page-discovery.csv row {index} is generated but missing 关联用例ID")
            unknown_workbook = sorted(linked_ids - case_ids)
            if unknown_workbook:
                fail(f"page-discovery.csv row {index} references case IDs missing from workbook: {unknown_workbook}")
            unknown_product = sorted(linked_ids - product_case_ids)
            if unknown_product:
                fail(f"page-discovery.csv row {index} references case IDs missing from product-map 用例资产索引: {unknown_product}")

    for case_id in sorted(case_ids):
        if case_id not in product_case_ids:
            fail(f"Workbook case ID is missing from product-map 用例资产索引: {case_id}")
        product_rows = [row for row in product_case_rows if row.get("用例ID") == case_id]
        if not any(row.get("用例标题") == case_titles[case_id] for row in product_rows):
            fail(f"product-map 用例资产索引 title mismatch or missing for case ID: {case_id}")
        if not any(row.get("功能点") == case_function_points[case_id] for row in product_rows):
            fail(f"product-map 用例资产索引 功能点 mismatch or missing for case ID: {case_id}")

    synced_changes = [
        row for row in product_change_rows
        if row.get("是否已同步产品版图") == "是" and row.get("变更内容")
    ]
    if not synced_changes:
        fail("product-map 变更记录 must include at least one synced change row with 是否已同步产品版图=是")


def default_product_map_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "test-assets" / "product-map.xlsx"


def default_page_discovery_path(batch_status: Path | None) -> Path | None:
    if not batch_status:
        return None
    candidate = batch_status.resolve().parent / "page-discovery.csv"
    if candidate.exists():
        return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated test design deliverable workbook.")
    parser.add_argument("--workbook", required=True, type=Path)
    parser.add_argument("--batch-status", type=Path)
    parser.add_argument("--product-map", type=Path)
    parser.add_argument("--page-discovery", type=Path)
    parser.add_argument("--import-workbook", type=Path)
    args = parser.parse_args()

    if not args.page_discovery:
        args.page_discovery = default_page_discovery_path(args.batch_status)
    if args.page_discovery and not args.product_map:
        args.product_map = default_product_map_path()

    workbook_data = validate_workbook(args.workbook)
    if args.batch_status:
        batch_rows = validate_batch_status(args.batch_status)
        validate_batch_review(args.batch_status, batch_rows)
    if args.import_workbook:
        validate_import_workbook(args.import_workbook, workbook_data)
    if bool(args.product_map) != bool(args.page_discovery):
        fail("--product-map and --page-discovery must be provided together")
    if args.product_map and args.page_discovery:
        validate_product_map_sync(workbook_data, args.product_map, args.page_discovery)
    print("OK: test design deliverable quality checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
