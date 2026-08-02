# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
import zipfile
from pathlib import Path
from typing import Callable
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

IMPORT_ALLOWED_VALUES = {
    "测试类型": {"功能测试", "性能规格测试", "可靠性测试", "兼容性测试", "可维护性测试", "安全性测试", "易用性测试"},
    "测试用例级别": {"L1", "L2", "L3", "L4"},
    "执行方式": {"自动化", "手动"},
}

DFX_SCENARIOS = {
    "DFT功能": {"正向流程", "边界值", "异常输入", "逆向操作"},
    "DFP性能": {"响应时间", "并发处理", "大数据量", "资源监控"},
    "DFI接口": {"参数校验", "协议兼容", "错误码", "超时重试"},
    "DFC兼容": {"浏览器", "操作系统", "屏幕适配", "数据格式"},
    "DFS安全": {"身份认证", "权限控制", "数据脱敏", "注入防护"},
    "DFR可靠": {"故障恢复", "数据一致", "幂等性", "断点续传"},
    "DFM维护": {"配置热更新", "灰度发布", "回滚机制", "日志追踪"},
    "DFU可用": {"操作便捷", "错误提示", "用户引导", "操作反馈"},
    "DFD部署": {"全新安装", "版本升级", "卸载回滚", "配置迁移"},
    "DFO运维": {"监控指标", "告警配置", "故障自愈", "容量规划"},
    "DFB业务": {"业务流程", "数据准确", "端到端", "报表统计"},
    "DFX极端": {"压力极限", "破坏性", "资源耗尽", "并发极限"},
}

DEPRECATED_SCENARIO_HEADERS = {"场景类型", "正向/反向"}
GENERATED_SCENARIO_REQUIRED_FIELDS = [
    "场景 ID",
    "Story ID/需求 ID",
    "功能点",
    "测试对象/页面元素",
    "DFX维度",
    "DFX场景",
    "输入数据/状态条件",
    "观察点",
]
SCENARIO_SIGNATURE_FIELDS = [
    "Story ID/需求 ID",
    "功能点",
    "测试对象/页面元素",
    "DFX维度",
    "DFX场景",
    "输入数据/状态条件",
    "观察点",
]
CASE_MERGE_SIGNATURE_FIELDS = ["功能点", "前置条件", "测试数据", "操作步骤"]
FINDING_DISPLAY_LIMIT = 20
CHINESE_TEXT_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]*")
ALLOWED_ENGLISH_TERMS = {
    "ai", "api", "boolean", "css", "csv", "db", "dfx", "dft", "dfp", "dfi", "dfc", "dfs", "dfr",
    "dfm", "dfu", "dfd", "dfo", "dfb", "dom", "e2e", "excel", "html", "http", "https",
    "id", "ip", "jmeter", "json", "k6", "l1", "l2", "l3", "l4", "locust", "mfa", "mock",
    "jest", "mocha", "n/a", "number", "p0", "p1", "p2", "p3", "playwright", "qps", "sql", "string",
    "supertest", "tcp", "token", "tps", "udp", "ui", "uri", "url", "uuid", "wiremock", "xml", "xpath",
}
CHINESE_DELIVERY_FIELDS = {
    "测试设计总览": ["测试范围", "不测范围", "主要风险", "准入条件", "准出条件", "待确认问题"],
    "需求用户故事拆解": [
        "用户故事/需求描述", "角色", "业务价值", "验收标准", "业务规则", "前置条件", "后置影响", "待确认问题",
    ],
    "测试场景矩阵": ["功能点", "测试对象/页面元素", "输入数据/状态条件", "观察点", "备注"],
    "功能测试用例": [
        "模块", "功能点", "用例标题", "前置条件", "测试数据", "操作步骤", "预期结果", "关联风险", "备注",
    ],
    "性能测试设计": ["业务链路", "监控指标", "通过标准", "造数策略", "风险说明"],
    "风险与待确认问题": ["描述", "影响范围", "建议处理方式"],
    "页面元素覆盖清单": [
        "元素类型", "交互方式", "前置状态/权限", "预期行为", "业务依据/规则来源", "发现方式", "待确认问题/备注",
    ],
}
UNRESOLVED_COVERAGE_NOTE_PATTERN = re.compile(
    r"^\s*(待实探|待确认|未验证|需验证|功能不明)\s*[：:]"
)
UI_WRAPPER_PATTERN = re.compile(
    r"(?:点击|进入|打开|选择|切换|勾选|展开|关闭)[^，。；;\r\n]{0,16}"
    r"[\[【「『“‘](?=[^\]】」』”’\r\n]{0,59}[\u4e00-\u9fffA-Za-z])"
    r"[^\]】」』”’\r\n]{1,60}[\]】」』”’]"
    r"|[\[【「『“‘](?=[^\]】」』”’\r\n]{0,59}[\u4e00-\u9fffA-Za-z])"
    r"[^\]】」』”’\r\n]{1,60}[\]】」』”’]"
    r"(?:按钮|菜单|字段|输入框|下拉框|页签|页面|选项|图标)"
)
NAVIGATION_ACTION_PATTERN = re.compile(
    r"(?:进入|依次进入|导航至|打开|点击[^，。；;\r\n]{0,12}菜单)[^。；;\r\n]{0,120}"
)
FORBIDDEN_NAVIGATION_SEPARATOR_PATTERN = re.compile(
    r"(?<=[\u4e00-\u9fffA-Za-z\]】」』”’])\s*(?:->|→|>)\s*"
    r"(?=[\u4e00-\u9fffA-Za-z\[【「『“‘])"
)

IMPORT_REQUIRED_FIELDS = ["一级模块名称", "二级模块名称", "三级模块名称", "测试用例名称", "测试类型", "测试用例级别", "执行方式"]
IMPORT_AUTO_FIELDS = ["测试用例系统编号", "作者"]
IMPORT_MULTILINE_FIELDS = ["测试步骤描述", "测试步骤预期结果", "前置条件", "测试用例说明", "备注"]

FORMAL_MULTILINE_FIELDS = {
    "功能测试用例": ["前置条件", "测试数据", "操作步骤", "预期结果", "备注"],
    "性能测试设计": ["前置条件/数据准备", "执行步骤", "监控指标", "通过标准", "风险备注"],
    "风险与待确认问题": ["描述", "影响范围", "建议处理方式"],
    "页面元素覆盖清单": ["业务依据/规则来源", "待确认问题/备注"],
}

RESIDUAL_MARKERS = ["{NAV}", "{NL}", "{Q}", "{E}", "${", "{{", "TODO", "TBD"]

DISCOVERY_FINAL_STATUSES = {"已验证", "客观受限", "不适用"}
DISCOVERY_INTERACTIVE_KINDS = {"交互", "状态变化"}
DISCOVERY_STATEFUL_CONTROL_MARKERS = ["弹窗", "抽屉", "下拉", "编辑态", "删除确认", "确认框", "浮层"]
DISCOVERY_SOURCE_ALLOWED_VALUES = {
    "需求文档",
    "截图",
    "原型",
    "浏览器实探",
    "computer use",
    "代码/DOM",
    "用户说明",
}
PAGE_DISCOVERY_SOURCES = {"浏览器实探", "computer use", "代码/DOM"}
PAGE_DISCOVERY_SOURCES_NORMALIZED = {item.casefold() for item in PAGE_DISCOVERY_SOURCES}
RISK_ALLOWED_STATUSES = {
    "待实探",
    "已实探",
    "待确认",
    "已确认",
    "需联调",
    "待环境",
    "缺权限",
    "不适用",
    "已关闭",
}
NO_PENDING_QUESTION_VALUES = {"无", "暂无", "不适用", "无待确认问题", "已确认", "已关闭"}
BRANCH_POLICIES = {"逐项验证", "用例逐项覆盖"}
DATA_CHANGE_COMMIT_PATTERN = re.compile(
    r"(?:点击|执行)?(?:确定|保存|提交|确认|应用|发布|导入)(?:按钮|操作)?|(?:自动保存|立即生效)"
)
PERSISTENT_BRANCH_TARGET_PATTERN = re.compile(
    r"(?:新建|创建|新增|添加|编辑|修改|配置|表单|字段|变量|策略|保存|提交|发布|导入)"
)
PAGINATION_EVIDENCE_PATTERN = re.compile(
    r"(?:分页(?:组件|控件|区域)?|总条数|共\s*\d+\s*条|每页(?:条数)?|页容量|条\s*/\s*页|上一页|下一页|页码|跳页|跳至(?:第|某|指定|[nN\d])?页)"
)
PAGINATION_GENERIC_ELEMENT_PATTERN = re.compile(r"^(?:分页|分页组件|分页控件|分页区域|页码导航)$")
PAGE_SIZE_PATTERN = re.compile(r"(?:每页(?:条数)?|页容量|条\s*/\s*页)")
PAGINATION_TARGET_FIELDS = ["element", "control_type", "action", "evidence", "observation", "result"]
PAGINATION_IDENTITY_FIELDS = ["element", "control_type"]
PAGINATION_CAPABILITY_RULES = [
    ("首页", re.compile(r"首页(?!页码)")),
    ("上一页", re.compile("上一页")),
    ("页码", re.compile("页码(?:按钮|选择)?|当前页")),
    ("下一页", re.compile("下一页")),
    ("末页", re.compile(r"末页(?!页码)")),
    ("省略号", re.compile("省略号")),
    ("每页条数", PAGE_SIZE_PATTERN),
    ("跳页", re.compile(r"跳页|跳至(?:$|第?\s*[Nn\d]+页|指定页)")),
]
PAGINATION_CAPABILITY_PATTERNS = [pattern for _, pattern in PAGINATION_CAPABILITY_RULES]
STORY_REQUIRED_FIELDS = ["Story ID/需求 ID", "用户故事/需求描述", "业务价值", "验收标准"]
STORY_DUPLICATE_FIELDS = [
    "用户故事/需求描述", "角色", "业务价值", "验收标准", "业务规则", "前置条件", "后置影响", "依赖系统", "待确认问题",
]

def fail(message: str) -> None:
    raise AssertionError(message)


def add_finding(findings: dict[str, list[str]], category: str, message: str) -> None:
    findings.setdefault(category, []).append(message)


def collect_validation_issue(
    findings: dict[str, list[str]],
    category: str,
    action: Callable[[], object],
) -> object | None:
    try:
        return action()
    except AssertionError as exc:
        add_finding(findings, category, str(exc))
        return None


def is_page_discovery_source(value: str) -> bool:
    return value.strip().casefold() in PAGE_DISCOVERY_SOURCES_NORMALIZED


def has_open_understanding_question(value: str) -> bool:
    normalized = re.sub(r"[\s。；;，,]+", "", value or "")
    return bool(normalized) and normalized not in NO_PENDING_QUESTION_VALUES


DROPDOWN_ACTION_PATTERNS = [
    re.compile(r"(?:点击|打开|展开|切换)[^。；;\r\n]{0,30}(?:下拉(?:框|浮层)?)"),
    re.compile(r"(?:open|show|expand|click)[^.；;\r\n]{0,30}(?:dropdown)", re.IGNORECASE),
]

NON_DROPDOWN_TRANSIENT_ACTION_PATTERNS = [
    re.compile(r"(?:点击|打开|展开|进入|切换)[^。；;\r\n]{0,30}(?:弹窗|对话框|抽屉|编辑态|删除确认框|确认弹窗)"),
    re.compile(r"(?:点击|选择)[^。；;\r\n]{0,20}(?:编辑|删除|添加变量|新增变量)[^。；;\r\n]{0,20}(?:按钮|图标|入口|操作)?"),
    re.compile(r"(?:open|show|expand|enter|click)[^.；;\r\n]{0,30}(?:modal|dialog|drawer|edit mode|delete confirmation)", re.IGNORECASE),
]

TRANSIENT_ACTION_PATTERNS = NON_DROPDOWN_TRANSIENT_ACTION_PATTERNS + DROPDOWN_ACTION_PATTERNS

DROPDOWN_SELECTION_PATTERN = re.compile(r"(?:选择|选中|切换为|设置为)[^。；;\r\n]{1,40}")
DROPDOWN_RESULT_PATTERN = re.compile(
    r"(?:收起|关闭|消失|更新|刷新|显示|展示|加载|生效|变为|切换|保持|筛选|分页)"
)

TERMINAL_ACTION_MARKERS = [
    "click OK",
    "click Cancel",
    "close",
    "return",
    "back to list",
    "save",
    "submit",
    "not save",
    "点击确定",
    "点击「确定」",
    "点击取消",
    "点击「取消」",
    "点击关闭",
    "点击「关闭」",
    "返回",
    "回到列表",
    "返回列表",
    "保存",
    "提交",
    "不保存",
    "关闭弹窗",
    "退出编辑",
]

UNRESOLVED_EXPECTATION_PATTERNS = [
    re.compile(r"路径\s*[A-Za-zＡ-Ｚａ-ｚ]\s*[/／或]\s*路径\s*[A-Za-zＡ-Ｚａ-ｚ]", re.IGNORECASE),
    re.compile(r"或者?类似(?:提示|文案|结果)"),
    re.compile(r"前端或后端(?:任一)?(?:路径|处理|校验)"),
    re.compile(r"(?:可能|大概|预计会)[^。；;\r\n]{0,30}"),
    re.compile(r"(?:显示|提示|返回|处理)[^。；;\r\n]{0,20}(?:或者|或)[^。；;\r\n]{1,20}"),
]

ENVIRONMENT_SNAPSHOT_PATTERNS = [
    (
        "当前环境固定数量",
        re.compile(
            r"(?:当前(?:测试)?环境|当前|现有|已有|目前)[^。；;\r\n]{0,20}"
            r"(?:共|为|有|仅有)?\s*\d+\s*条(?!\s*/\s*页)(?:数据|记录)?"
        ),
    ),
    (
        "具体运行账号",
        re.compile(r"(?:测试账号|使用账号|登录账号)\s*[:：]?\s*(?:user|test|admin)[A-Za-z0-9_.-]*\d+[A-Za-z0-9_.-]*", re.IGNORECASE),
    ),
    (
        "带固定日期的测试对象",
        re.compile(r"(?:AI_TEST|CODEX_TEST)[^\s，。；;\r\n]{0,48}(?:20\d{6}|20\d{2}[-_/]\d{1,2}[-_/]\d{1,2})", re.IGNORECASE),
    ),
    (
        "现存数据的固定日期",
        re.compile(r"(?:当前|现有|已有)[^。；;\r\n]{0,30}20\d{2}-\d{1,2}-\d{1,2}"),
    ),
]

UNRESOLVED_CONFIRMATION_PATTERN = re.compile(
    r"(?:待|需|需要)(?:用户|产品|业务|需求|联调)?确认|尚未确认|原因未知|"
    r"是否(?:允许|支持|需要|属于|可以|可用)|发布计划(?:未知|未明确)"
)

ENVIRONMENT_WORKAROUND_NOTE_PATTERN = re.compile(
    r"(?:需|必须)(?:手动)?刷新(?:页面)?(?:才|后)?(?:能|可)?(?:看到|显示|生效|更新)"
)

EXPECTED_CONTRADICTION_PAIRS = [
    (
        re.compile(r"(?:弹窗|对话框|抽屉)(?![^。；;\r\n]{0,8}(?:保持打开|不关闭|仍显示))[^。；;\r\n]{0,8}(?:关闭|消失)"),
        re.compile(r"(?:弹窗|对话框|抽屉)[^。；;\r\n]{0,8}(?:保持打开|不关闭|仍显示)"),
    ),
    (re.compile(r"(?:列表|数据).{0,8}(?:新增|增加|更新|删除|移除)"), re.compile(r"(?:列表|数据).{0,8}(?:不变|无变化)")),
]

FORMAL_ALLOWED_VALUES = {
    ("测试场景矩阵", "是否生成用例"): {"是", "否"},
    ("功能测试用例", "是否适合自动化"): {"是", "否", "待评估"},
    ("性能测试设计", "是否纳入本轮测试"): {"是", "否", "待评估"},
}


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


def cell_style_id(cell: ET.Element) -> int:
    raw = cell.attrib.get("s", "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref)
    if not letters:
        return 0
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def column_number(cell_ref: str) -> int:
    return column_index(cell_ref) + 1


def parse_a1_cell(cell_ref: str) -> tuple[int, int]:
    cleaned = cell_ref.replace("$", "")
    match = re.match(r"([A-Z]+)(\d+)$", cleaned)
    if not match:
        return 0, 0
    return column_number(match.group(1)), int(match.group(2))


def parse_a1_range(range_text: str) -> tuple[int, int, int, int]:
    cleaned = range_text.replace("$", "")
    if ":" in cleaned:
        start, end = cleaned.split(":", 1)
    else:
        start = end = cleaned
    min_col, min_row = parse_a1_cell(start)
    max_col, max_row = parse_a1_cell(end)
    return min_col, min_row, max_col, max_row


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


def relationship_target(base_path: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_path), target))


def cell_reference_to_position(cell_ref: str) -> tuple[int, int]:
    match = re.match(r"^([A-Z]+)(\d+)$", cell_ref)
    if not match:
        return (0, 0)
    col = 0
    for char in match.group(1):
        col = col * 26 + ord(char) - ord("A") + 1
    return (int(match.group(2)), col)


def range_bounds(ref: str) -> tuple[int, int, int, int]:
    cells = ref.split(":")
    if len(cells) == 1:
        start = end = cells[0]
    else:
        start, end = cells[0], cells[-1]
    start_row, start_col = cell_reference_to_position(start)
    end_row, end_col = cell_reference_to_position(end)
    return (start_row, start_col, end_row, end_col)


def range_covers(actual_ref: str, expected_ref: str) -> bool:
    actual_start_row, actual_start_col, actual_end_row, actual_end_col = range_bounds(actual_ref)
    expected_start_row, expected_start_col, expected_end_row, expected_end_col = range_bounds(expected_ref)
    return (
        actual_start_row <= expected_start_row
        and actual_start_col <= expected_start_col
        and actual_end_row >= expected_end_row
        and actual_end_col >= expected_end_col
    )


def validate_table_ranges(path: Path, sheet_names: list[str] | None = None) -> None:
    with zipfile.ZipFile(path) as zf:
        table_files = [name for name in zf.namelist() if name.startswith("xl/tables/")]
        if table_files:
            fail(
                f"{path} must not contain Excel Table parts: {', '.join(table_files)}. "
                "Use normal cell ranges, styles, and auto filters instead; otherwise Excel may repair "
                "or delete /xl/tables/table*.xml when opening the workbook."
            )
        available_sheets = workbook_sheet_paths(zf)
        target_sheets = sheet_names or list(available_sheets)
        for sheet_name in target_sheets:
            sheet_path = available_sheets.get(sheet_name)
            if not sheet_path:
                continue
            root = ET.fromstring(zf.read(sheet_path))
            table_parts = root.findall("x:tableParts/x:tablePart", NS)
            if table_parts:
                fail(
                    f"{sheet_name} must not contain worksheet tableParts. "
                    "Remove Excel Table objects and keep only normal ranges/styles/auto filters."
                )


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


def sheet_cell_rows(path: Path, sheet_name: str) -> list[list[tuple[str, str, int]]]:
    with zipfile.ZipFile(path) as zf:
        paths = workbook_sheet_paths(zf)
        if sheet_name not in paths:
            fail(f"Workbook is missing sheet: {sheet_name}")
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read(paths[sheet_name]))
    rows: list[list[tuple[str, str, int]]] = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        values: list[tuple[str, str, int]] = []
        for cell in row.findall("x:c", NS):
            index = column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append(("", "", 0))
            values[index] = (
                cell.attrib.get("r", ""),
                cell_text(cell, shared),
                cell_style_id(cell),
            )
        rows.append(values)
    return rows


def xml_signature(element: ET.Element | None) -> tuple:
    if element is None:
        return ()
    attributes = dict(element.attrib)
    local_name = element.tag.rsplit("}", 1)[-1]
    if local_name in {"b", "i", "strike", "outline", "shadow", "condense", "extend"} and "val" not in attributes:
        attributes["val"] = "1"
    if local_name == "patternFill" and "patternType" not in attributes:
        attributes["patternType"] = "none"
    return (
        element.tag,
        tuple(sorted(attributes.items())),
        (element.text or "").strip(),
        tuple(sorted(xml_signature(child) for child in element)),
    )


def workbook_style_signatures(path: Path) -> dict[int, tuple]:
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("xl/styles.xml"))
    fonts = [xml_signature(item) for item in root.findall("x:fonts/x:font", NS)]
    fills = [xml_signature(item) for item in root.findall("x:fills/x:fill", NS)]
    borders = [xml_signature(item) for item in root.findall("x:borders/x:border", NS)]
    custom_formats = {
        item.attrib.get("numFmtId", ""): item.attrib.get("formatCode", "")
        for item in root.findall("x:numFmts/x:numFmt", NS)
    }
    signatures: dict[int, tuple] = {}
    for index, xf in enumerate(root.findall("x:cellXfs/x:xf", NS)):
        font_id = int(xf.attrib.get("fontId", "0"))
        fill_id = int(xf.attrib.get("fillId", "0"))
        border_id = int(xf.attrib.get("borderId", "0"))
        num_fmt_id = xf.attrib.get("numFmtId", "0")
        own_attributes = tuple(
            sorted(
                (key, value)
                for key, value in xf.attrib.items()
                if key not in {
                    "fontId",
                    "fillId",
                    "borderId",
                    "numFmtId",
                    "xfId",
                    "applyFont",
                    "applyFill",
                    "applyBorder",
                    "applyAlignment",
                    "applyNumberFormat",
                    "applyProtection",
                    "pivotButton",
                    "quotePrefix",
                }
            )
        )
        signatures[index] = (
            fonts[font_id] if font_id < len(fonts) else (),
            fills[fill_id] if fill_id < len(fills) else (),
            borders[border_id] if border_id < len(borders) else (),
            custom_formats.get(num_fmt_id, f"builtin:{num_fmt_id}"),
            own_attributes,
            tuple(sorted(xml_signature(child) for child in xf)),
        )
    return signatures


def trimmed_headers(path: Path, sheet_name: str) -> list[str]:
    rows = sheet_rows(path, sheet_name)
    headers = list(rows[0] if rows else [])
    while headers and not headers[-1]:
        headers.pop()
    return headers


def worksheet_structure(path: Path, sheet_name: str) -> dict[str, object]:
    with zipfile.ZipFile(path) as zf:
        sheet_paths = workbook_sheet_paths(zf)
        root = ET.fromstring(zf.read(sheet_paths[sheet_name]))
    columns: dict[int, tuple[str, str, str]] = {}
    for column in root.findall("x:cols/x:col", NS):
        start = int(column.attrib["min"])
        end = int(column.attrib["max"])
        signature = (
            column.attrib.get("width", ""),
            column.attrib.get("hidden", ""),
            column.attrib.get("bestFit", ""),
        )
        for index in range(start, end + 1):
            columns[index] = signature
    header_row = root.find(".//x:sheetData/x:row[@r='1']", NS)
    pane = root.find("x:sheetViews/x:sheetView/x:pane", NS)
    auto_filter = root.find("x:autoFilter", NS)
    validations = []
    for validation in root.findall("x:dataValidations/x:dataValidation", NS):
        ranges = []
        for item in validation.attrib.get("sqref", "").split():
            min_col, min_row, max_col, max_row = parse_a1_range(item)
            ranges.append((min_col, min_row, max_col, max_row))
        attributes = tuple(
            sorted(
                (key, value)
                for key, value in validation.attrib.items()
                if key == "type"
                or (
                    key not in {"sqref"}
                    and not key.endswith("}uid")
                    and value not in {"0", "false", "False"}
                )
            )
        )
        validations.append((attributes, tuple(xml_signature(child) for child in validation), tuple(ranges)))
    return {
        "columns": columns,
        "header_height": "" if header_row is None else header_row.attrib.get("ht", ""),
        "pane": None if pane is None else tuple(sorted(pane.attrib.items())),
        "auto_filter": None if auto_filter is None else auto_filter.attrib.get("ref", ""),
        "validations": validations,
    }


def assert_formal_template_invariants(workbook: Path, template: Path) -> None:
    if not template.exists():
        fail(f"Formal workbook template not found: {template}")
    with zipfile.ZipFile(template) as zf:
        template_sheets = list(workbook_sheet_paths(zf))
    with zipfile.ZipFile(workbook) as zf:
        workbook_sheets = list(workbook_sheet_paths(zf))
    if workbook_sheets != template_sheets:
        fail(f"Formal workbook sheets must match template exactly. Expected {template_sheets}, got {workbook_sheets}")

    issues: list[str] = []
    workbook_styles = workbook_style_signatures(workbook)
    template_styles = workbook_style_signatures(template)
    for sheet_name in template_sheets:
        workbook_value_rows = sheet_rows(workbook, sheet_name)
        last_data_row = max(
            (index for index, row in enumerate(workbook_value_rows, start=1) if any(row)),
            default=1,
        )
        expected_headers = trimmed_headers(template, sheet_name)
        actual_headers = trimmed_headers(workbook, sheet_name)
        if actual_headers != expected_headers:
            issues.append(
                f"{sheet_name} headers must match formal template exactly. "
                f"Expected {expected_headers}, got {actual_headers}"
            )

        expected_structure = worksheet_structure(template, sheet_name)
        actual_structure = worksheet_structure(workbook, sheet_name)
        for field, label in [
            ("columns", "column widths"),
            ("header_height", "header row height"),
            ("pane", "freeze panes"),
        ]:
            if actual_structure[field] != expected_structure[field]:
                issues.append(f"{sheet_name} {label} must match the formal template")

        expected_filter = expected_structure["auto_filter"]
        actual_filter = actual_structure["auto_filter"]
        if bool(expected_filter) != bool(actual_filter):
            issues.append(f"{sheet_name} auto filter presence must match the formal template")
        if expected_filter and actual_filter:
            expected_bounds = parse_a1_range(str(expected_filter))
            actual_bounds = parse_a1_range(str(actual_filter))
            if actual_bounds[:3] != expected_bounds[:3]:
                issues.append(f"{sheet_name} auto filter columns must match the formal template")
            if actual_bounds[3] < last_data_row:
                issues.append(f"{sheet_name} auto filter must cover the last data row {last_data_row}")

        expected_validations = expected_structure["validations"]
        actual_validations = actual_structure["validations"]
        if len(actual_validations) != len(expected_validations):
            issues.append(f"{sheet_name} data validation count must match the formal template")
        for index, (expected, actual) in enumerate(zip(expected_validations, actual_validations), start=1):
            if actual[:2] != expected[:2]:
                issues.append(f"{sheet_name} data validation {index} rule must match the formal template")
            expected_bases = [(item[0], item[1], item[2]) for item in expected[2]]
            actual_bases = [(item[0], item[1], item[2]) for item in actual[2]]
            if actual_bases != expected_bases:
                issues.append(f"{sheet_name} data validation {index} columns must match the formal template")
            for expected_range, actual_range in zip(expected[2], actual[2]):
                if expected_range[1] <= 2 <= expected_range[3] and actual_range[3] < last_data_row:
                    issues.append(f"{sheet_name} data validation {index} must cover the last data row {last_data_row}")

        template_rows = sheet_cell_rows(template, sheet_name)
        workbook_rows = sheet_cell_rows(workbook, sheet_name)
        if not template_rows or not workbook_rows:
            issues.append(f"{sheet_name} must contain the template header and sample style row")
            continue
        header_columns = len(expected_headers)
        for column in range(header_columns):
            expected_style = template_rows[0][column][2] if column < len(template_rows[0]) else 0
            actual_style = workbook_rows[0][column][2] if column < len(workbook_rows[0]) else 0
            if workbook_styles.get(actual_style) != template_styles.get(expected_style):
                issues.append(f"{sheet_name} header column {column + 1} style must match the formal template")

        template_sample = template_rows[1] if len(template_rows) > 1 else []
        for row_number, row in enumerate(workbook_rows[1:], start=2):
            if not any(value for _, value, _ in row):
                continue
            for column in range(header_columns):
                expected_style = template_sample[column][2] if column < len(template_sample) else 0
                actual_style = row[column][2] if column < len(row) else 0
                if workbook_styles.get(actual_style) != template_styles.get(expected_style):
                    issues.append(
                        f"{sheet_name} row {row_number} column {column + 1} style must match the formal template sample row 2"
                    )
    if issues:
        fail("正式测试设计模板一致性检查未通过：\n- " + "\n- ".join(issues))


def wrapped_style_ids(path: Path) -> set[int]:
    with zipfile.ZipFile(path) as zf:
        try:
            root = ET.fromstring(zf.read("xl/styles.xml"))
        except KeyError:
            return set()
    wrapped: set[int] = set()
    cell_xfs = root.find("x:cellXfs", NS)
    if cell_xfs is None:
        return wrapped
    for index, xf in enumerate(cell_xfs.findall("x:xf", NS)):
        alignment = xf.find("x:alignment", NS)
        if alignment is not None and alignment.attrib.get("wrapText") in {"1", "true", "True"}:
            wrapped.add(index)
    return wrapped


def horizontal_alignment_style_ids(path: Path, expected_alignment: str) -> set[int]:
    with zipfile.ZipFile(path) as zf:
        try:
            root = ET.fromstring(zf.read("xl/styles.xml"))
        except KeyError:
            return set()
    matched: set[int] = set()
    cell_xfs = root.find("x:cellXfs", NS)
    if cell_xfs is None:
        return matched
    for index, xf in enumerate(cell_xfs.findall("x:xf", NS)):
        alignment = xf.find("x:alignment", NS)
        if alignment is not None and alignment.attrib.get("horizontal") == expected_alignment:
            matched.add(index)
    return matched


def assert_cells_horizontal_alignment(path: Path, sheet_name: str, expected_alignment: str) -> None:
    allowed_styles = horizontal_alignment_style_ids(path, expected_alignment)
    rows = sheet_cell_rows(path, sheet_name)
    issues: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        for column_number, (ref, _, style_id) in enumerate(row, start=1):
            if style_id not in allowed_styles:
                cell_ref = ref or f"row {row_number} column {column_number}"
                issues.append(f"{sheet_name} {cell_ref} must use horizontal alignment: {expected_alignment}")
    if issues:
        fail("导入文件水平对齐检查未通过：\n- " + "\n- ".join(issues))


def assert_multiline_cells_wrapped(path: Path, sheet_name: str, field_names: list[str]) -> None:
    rows = sheet_cell_rows(path, sheet_name)
    if not rows:
        return
    headers = [cell[1] for cell in rows[0]]
    header_index = {header: index for index, header in enumerate(headers) if header}
    target_indexes = [header_index[field] for field in field_names if field in header_index]
    if not target_indexes:
        return
    wrapped = wrapped_style_ids(path)
    issues: list[str] = []
    for row_number, row in enumerate(rows[1:], start=2):
        for index in target_indexes:
            if index >= len(row):
                continue
            ref, value, style_id = row[index]
            if "\n" in value and style_id not in wrapped:
                field = headers[index]
                cell_ref = ref or f"{field} row {row_number}"
                issues.append(f"{sheet_name} {cell_ref} contains multiline text but wrapText is not enabled for field {field}")
    if issues:
        fail("多行文本自动换行检查未通过：\n- " + "\n- ".join(issues))


def assert_data_rows_follow_sample_styles(path: Path, sheet_names: list[str] | None = None) -> None:
    with zipfile.ZipFile(path) as zf:
        available_sheets = workbook_sheet_paths(zf)
    target_sheets = sheet_names or list(available_sheets)
    issues: list[str] = []
    for sheet_name in target_sheets:
        rows = sheet_cell_rows(path, sheet_name)
        if len(rows) <= 2:
            continue
        sample_styles = {index: cell[2] for index, cell in enumerate(rows[1])}
        for row_number, row in enumerate(rows[2:], start=3):
            if not any(value for _, value, _ in row):
                continue
            for index, (_, _, style_id) in enumerate(row):
                expected = sample_styles.get(index)
                if expected is None:
                    continue
                if style_id != expected:
                    issues.append(
                        f"{sheet_name} row {row_number} column {index + 1} style must match template sample row 2. "
                        "Only cell content should change; borders, fills, fonts, number formats, and alignment must be preserved."
                    )
    if issues:
        fail("数据行模板样式检查未通过：\n- " + "\n- ".join(issues))


def range_covers_column_row(range_text: str, column: int, row: int) -> bool:
    for part in range_text.split():
        min_col, min_row, max_col, max_row = parse_a1_range(part)
        if min_col <= column <= max_col and min_row <= row <= max_row:
            return True
    return False


def assert_dropdown_validations_cover_rows(path: Path, sheet_name: str, field_names: list[str], last_row: int) -> None:
    if last_row < 2:
        return
    rows = sheet_cell_rows(path, sheet_name)
    if not rows:
        return
    headers = [cell[1] for cell in rows[0]]
    header_index = {header: index + 1 for index, header in enumerate(headers) if header}
    target_columns = {field: header_index[field] for field in field_names if field in header_index}
    if not target_columns:
        return
    with zipfile.ZipFile(path) as zf:
        sheet_paths = workbook_sheet_paths(zf)
        root = ET.fromstring(zf.read(sheet_paths[sheet_name]))
    validations = root.findall(".//x:dataValidations/x:dataValidation", NS)
    issues: list[str] = []
    for field, column in target_columns.items():
        if not any(
            validation.attrib.get("type") == "list"
            and range_covers_column_row(validation.attrib.get("sqref", ""), column, last_row)
            for validation in validations
        ):
            issues.append(f"{sheet_name} field {field} dropdown validation must cover row {last_row}")
    if issues:
        fail("下拉验证范围检查未通过：\n- " + "\n- ".join(issues))


def assert_no_residual_markers(path: Path, sheet_names: list[str] | None = None) -> None:
    with zipfile.ZipFile(path) as zf:
        available_sheets = workbook_sheet_paths(zf)
    target_sheets = sheet_names or list(available_sheets)
    issues: list[str] = []
    for sheet_name in target_sheets:
        rows = sheet_rows(path, sheet_name)
        for row_number, row in enumerate(rows, start=1):
            for column_number, value in enumerate(row, start=1):
                if not value:
                    continue
                for marker in RESIDUAL_MARKERS:
                    if marker in value:
                        issues.append(f"{sheet_name} row {row_number} column {column_number} contains unresolved template marker: {marker}")
    if issues:
        fail("模板占位符检查未通过：\n- " + "\n- ".join(issues))


def validate_formal_workbook_styles(workbook: Path) -> None:
    findings: dict[str, list[str]] = {}
    collect_validation_issue(
        findings,
        "数据行样式",
        lambda: assert_data_rows_follow_sample_styles(workbook, EXPECTED_SHEETS),
    )
    for sheet_name, fields in FORMAL_MULTILINE_FIELDS.items():
        collect_validation_issue(
            findings,
            "多行文本自动换行",
            lambda sheet_name=sheet_name, fields=fields: assert_multiline_cells_wrapped(
                workbook, sheet_name, fields
            ),
        )
    if findings:
        fail(format_findings("正式工作簿样式检查未通过：", findings))


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
    numbers = [int(re.match(r"^(\d+)\.", line).group(1)) for line in lines]
    if numbers != list(range(1, len(lines) + 1)):
        fail(f"{label} numbering must be continuous from 1: {numbers}")


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


def ui_symbol_style_issues(text: str) -> list[str]:
    issues: list[str] = []
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        if UI_WRAPPER_PATTERN.search(line):
            issues.append(f"UI名称不得使用括号或引号包裹: {line}")
        navigation_action = NAVIGATION_ACTION_PATTERN.search(line)
        if navigation_action and FORBIDDEN_NAVIGATION_SEPARATOR_PATTERN.search(navigation_action.group(0)):
            issues.append(f"导航路径必须使用一级菜单-二级菜单-目标页面格式: {line}")
    return issues


def assert_transient_flow_closed(steps: str, expected: str, label: str) -> None:
    normalized_steps = re.sub(r"\s+", "", steps or "").lower()
    if not normalized_steps:
        return
    has_transient_action = any(pattern.search(steps or "") for pattern in TRANSIENT_ACTION_PATTERNS)
    if not has_transient_action:
        return
    has_terminal_action = any(marker.lower() in normalized_steps for marker in TERMINAL_ACTION_MARKERS)
    has_non_dropdown_action = any(
        pattern.search(steps or "") for pattern in NON_DROPDOWN_TRANSIENT_ACTION_PATTERNS
    )
    dropdown_selection_closed = (
        not has_non_dropdown_action
        and any(pattern.search(steps or "") for pattern in DROPDOWN_ACTION_PATTERNS)
        and bool(DROPDOWN_SELECTION_PATTERN.search(steps or ""))
        and bool(DROPDOWN_RESULT_PATTERN.search(expected or ""))
    )
    if dropdown_selection_closed:
        return
    if not has_terminal_action:
        fail(
            f"{label} opens or changes a transient UI state but its operation steps do not execute a confirm/cancel/close/return/recovery action"
        )


def has_active_login_step(steps: str) -> bool:
    for raw_line in (steps or "").splitlines():
        line = re.sub(r"^\s*\d+\.\s*", "", raw_line).strip()
        if not line or any(marker in line for marker in ["未登录", "退出登录", "清除登录状态", "登录状态"]):
            continue
        if re.search(r"(?:使用|输入|填写|以)[^。；;\r\n]{0,30}(?:账号|账户|用户|身份)[^。；;\r\n]{0,20}登录", line):
            return True
        if re.search(r"(?:登录系统|登录平台|执行登录)", line):
            return True
    return False


def collect_assertion_issue(action: Callable[[], None]) -> str | None:
    try:
        action()
    except AssertionError as exc:
        return str(exc)
    return None


def case_generalization_issues(row: dict[str, str]) -> list[str]:
    """识别高置信度环境快照；只做写入前兜底，不替代生成阶段的证据抽象。"""
    content = "\n".join(
        row.get(field, "")
        for field in ["前置条件", "测试数据", "操作步骤", "预期结果", "备注"]
    )
    issues = [label for label, pattern in ENVIRONMENT_SNAPSHOT_PATTERNS if pattern.search(content)]
    if ENVIRONMENT_WORKAROUND_NOTE_PATTERN.search(row.get("备注", "")):
        issues.append("将环境异常或手工绕行写成正常用例路径")
    return issues


def validate_function_case_preflight(function_rows: list[dict[str, str]]) -> None:
    """一次汇总生成阶段最常见的用例结构问题，避免逐次生成、逐错修正。"""
    findings: dict[str, list[str]] = {}
    generalization_rows: dict[str, list[str]] = {}

    def add(category: str, message: str) -> None:
        findings.setdefault(category, []).append(message)

    seen_case_ids: set[str] = set()
    for index, row in enumerate(function_rows, start=2):
        case_id = row.get("用例 ID", "").strip()
        function_point = row.get("功能点", "").strip()
        title = row.get("用例标题", "").strip()
        anchor = f"第 {index} 行 / 用例 ID={case_id or '缺失'} / 标题={title or '缺失'}"

        if not case_id:
            add("标识与标题", f"{anchor}：缺少用例 ID")
        elif case_id in seen_case_ids:
            add("标识与标题", f"{anchor}：用例 ID 重复")
        seen_case_ids.add(case_id)
        if not function_point:
            add("标识与标题", f"{anchor}：缺少功能点")
        elif not title.startswith(f"{function_point}-"):
            add("标识与标题", f"{anchor}：用例标题必须以本行功能点-开头")

        checks = [
            (
                "步骤与预期编号",
                lambda row=row, anchor=anchor: assert_numbered(
                    row.get("操作步骤", ""), f"{anchor} 操作步骤"
                ),
            ),
            (
                "入口导航",
                lambda row=row, anchor=anchor: assert_complete_operation_steps(
                    row.get("操作步骤", ""), f"{anchor} 操作步骤"
                ),
            ),
            (
                "步骤与预期编号",
                lambda row=row, anchor=anchor: assert_numbered(
                    row.get("预期结果", ""), f"{anchor} 预期结果"
                ),
            ),
            (
                "预期一致性",
                lambda row=row, anchor=anchor: assert_expected_result_consistency(
                    row.get("预期结果", ""), f"{anchor} 预期结果"
                ),
            ),
            (
                "DFX 映射",
                lambda row=row, anchor=anchor: assert_dfx_mapping(
                    row.get("DFX维度", ""), row.get("DFX场景", ""), anchor
                ),
            ),
            (
                "交互闭环",
                lambda row=row, anchor=anchor: assert_transient_flow_closed(
                    row.get("操作步骤", ""), row.get("预期结果", ""), anchor
                ),
            ),
        ]
        if row.get("前置条件"):
            checks.append(
                (
                    "步骤与预期编号",
                    lambda row=row, anchor=anchor: assert_numbered(
                        row.get("前置条件", ""), f"{anchor} 前置条件"
                    ),
                )
            )
        for category, check in checks:
            issue = collect_assertion_issue(check)
            if issue:
                add(category, issue)

        allowed_automation = FORMAL_ALLOWED_VALUES[("功能测试用例", "是否适合自动化")]
        automation_value = row.get("是否适合自动化", "").strip()
        if automation_value not in allowed_automation:
            add("枚举", f"{anchor}：是否适合自动化只能使用 {sorted(allowed_automation)}")
        for issue in ui_symbol_style_issues(row.get("操作步骤", "")):
            add("符号格式", f"{anchor}：{issue}")
        for issue in case_generalization_issues(row):
            generalization_rows.setdefault(issue, []).append(f"第 {index} 行/{case_id or '缺失 ID'}")

        unauthenticated_context = "\n".join(
            [title, row.get("前置条件", ""), row.get("操作步骤", "")]
        )
        if re.search(r"(?:未登录|无痕|清除登录状态|退出登录)", unauthenticated_context) and has_active_login_step(
            row.get("操作步骤", "")
        ):
            add("特殊角色与状态", f"{anchor}：未登录场景不得机械追加登录步骤")

    for issue, rows in generalization_rows.items():
        add(
            "通用性与数据独立性",
            f"{issue}出现在 {'、'.join(rows)}，应统一改为业务角色、当前用例造数、运行时唯一标识或相对断言",
        )

    if findings:
        fail(format_findings("功能测试用例写入前集中预检未通过：", findings))


def validate_story_rows(story_rows: list[dict[str, str]]) -> set[str]:
    findings: dict[str, list[str]] = {
        "必要字段缺失": [],
        "Story ID 重复": [],
        "故事内容精确重复": [],
    }
    seen_ids: set[str] = set()
    signatures: dict[tuple[str, ...], tuple[int, str]] = {}
    for index, row in enumerate(story_rows, start=2):
        story_id = row.get("Story ID/需求 ID", "").strip()
        for field in STORY_REQUIRED_FIELDS:
            if not row.get(field, "").strip():
                findings["必要字段缺失"].append(f"第 {index} 行 {field} 不能为空")
        if story_id and story_id in seen_ids:
            findings["Story ID 重复"].append(f"第 {index} 行 Story ID/需求 ID 重复：{story_id}")
        if story_id:
            seen_ids.add(story_id)
        signature = tuple(normalize_signature_value(row.get(field, "")) for field in STORY_DUPLICATE_FIELDS)
        previous = signatures.get(signature)
        if previous and any(signature):
            findings["故事内容精确重复"].append(
                f"第 {previous[0]} 行/{previous[1] or '缺失 ID'} 与第 {index} 行/{story_id or '缺失 ID'} 内容精确重复"
            )
        elif any(signature):
            signatures[signature] = (index, story_id)
    if any(findings.values()):
        fail(format_findings("需求用户故事拆解检查未通过：", findings))
    return seen_ids


def validate_story_traceability(
    story_ids: set[str],
    scenario_rows: list[dict[str, str]],
    function_rows: list[dict[str, str]],
) -> None:
    findings: dict[str, list[str]] = {"引用缺失": [], "引用不存在": [], "Story 未形成场景": []}
    scenario_story_ids: set[str] = set()
    for sheet_name, rows in [("测试场景矩阵", scenario_rows), ("功能测试用例", function_rows)]:
        for index, row in enumerate(rows, start=2):
            references = parse_ids(row.get("Story ID/需求 ID", ""))
            if not references:
                findings["引用缺失"].append(f"{sheet_name} 第 {index} 行缺少 Story ID/需求 ID")
                continue
            unknown = sorted(references - story_ids)
            if unknown:
                findings["引用不存在"].append(f"{sheet_name} 第 {index} 行引用了不存在的 Story：{unknown}")
            if sheet_name == "测试场景矩阵":
                scenario_story_ids.update(references & story_ids)
    uncovered = sorted(story_ids - scenario_story_ids)
    if uncovered:
        findings["Story 未形成场景"].append(f"以下 Story 没有关联测试场景：{uncovered}")
    if any(findings.values()):
        fail(format_findings("Story 到场景和用例追踪检查未通过：", findings))


def assert_expected_result_consistency(expected: str, label: str) -> None:
    for pattern in UNRESOLVED_EXPECTATION_PATTERNS:
        if pattern.search(expected or ""):
            fail(f"{label} contains an unresolved alternative instead of one verifiable result")
    lines = [re.sub(r"\s+", "", line) for line in (expected or "").splitlines() if line.strip()]
    for line in lines:
        for positive, negative in EXPECTED_CONTRADICTION_PAIRS:
            if positive.search(line) and negative.search(line):
                fail(f"{label} contains mutually contradictory results in one numbered item: {line}")


def assert_allowed_values(
    rows: list[dict[str, str]],
    sheet_name: str,
    field: str,
    allowed: set[str],
) -> None:
    invalid = [
        f"第 {index} 行={row.get(field, '') or '<空>'}"
        for index, row in enumerate(rows, start=2)
        if row.get(field, "") not in allowed
    ]
    if invalid:
        fail(f"{sheet_name}.{field} 只能使用 {sorted(allowed)}：{'; '.join(invalid)}")


def parse_ids(text: str) -> set[str]:
    return {item.strip() for item in re.split(r"[,，;；、\s]+", text) if item.strip()}


def split_dfx_values(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，;；、/\\\s]+", text or "") if item.strip()]


def assert_dfx_mapping(dimensions_text: str, scenarios_text: str, label: str) -> tuple[set[str], set[str]]:
    dimensions = set(split_dfx_values(dimensions_text))
    scenarios = set(split_dfx_values(scenarios_text))
    if not dimensions:
        fail(f"{label} must fill DFX维度")
    if not scenarios:
        fail(f"{label} must fill DFX场景")
    invalid_dimensions = sorted(dimensions - set(DFX_SCENARIOS))
    if invalid_dimensions:
        fail(f"{label} has invalid DFX维度: {invalid_dimensions}")
    allowed_scenarios: set[str] = set()
    for dimension in dimensions:
        allowed_scenarios.update(DFX_SCENARIOS[dimension])
    invalid_scenarios = sorted(scenarios - allowed_scenarios)
    if invalid_scenarios:
        fail(f"{label} has DFX场景 not allowed by selected DFX维度: {invalid_scenarios}")
    return dimensions, scenarios


def dfx_pairs(dimensions_text: str, scenarios_text: str) -> set[tuple[str, str]]:
    dimensions = split_dfx_values(dimensions_text)
    scenarios = split_dfx_values(scenarios_text)
    if len(dimensions) == len(scenarios):
        return {
            (dimension, scenario)
            for dimension, scenario in zip(dimensions, scenarios)
            if dimension in DFX_SCENARIOS and scenario in DFX_SCENARIOS[dimension]
        }
    return {
        (dimension, scenario)
        for dimension in dimensions
        for scenario in scenarios
        if dimension in DFX_SCENARIOS and scenario in DFX_SCENARIOS[dimension]
    }


def assert_no_deprecated_scenario_headers(rows: list[list[str]], sheet_name: str) -> None:
    if not rows:
        return
    headers = set(rows[0])
    deprecated = sorted(headers & DEPRECATED_SCENARIO_HEADERS)
    if deprecated:
        fail(f"{sheet_name} must use DFX维度/DFX场景 instead of deprecated headers: {deprecated}")


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "").strip().lower()


def normalize_signature_value(value: str) -> str:
    normalized = re.sub(r"[,，;；、/\\|]+", "|", value or "")
    return re.sub(r"\s+", " ", normalized).strip().lower()


def signature(row: dict[str, str], fields: list[str]) -> tuple[str, ...]:
    return tuple(normalize_signature_value(row.get(field, "")) for field in fields)


def format_findings(title: str, findings: dict[str, list[str]]) -> str:
    lines = [title]
    for category, messages in findings.items():
        if not messages:
            continue
        lines.append(f"- {category}（共 {len(messages)} 项）")
        lines.extend(f"  - {message}" for message in messages)
    return "\n".join(lines)


def is_obvious_english_narrative(value: str) -> bool:
    text = (value or "").strip()
    if not text or CHINESE_TEXT_PATTERN.search(text):
        return False
    words = [word.lower().rstrip(".") for word in ENGLISH_WORD_PATTERN.findall(text)]
    narrative_words = [
        word
        for word in words
        if len(word) > 1
        and word not in ALLOWED_ENGLISH_TERMS
        and not any(character.isdigit() for character in word)
    ]
    return bool(narrative_words)


def validate_chinese_delivery_language(workbook: Path) -> None:
    findings: dict[str, list[str]] = {}
    for sheet_name, fields in CHINESE_DELIVERY_FIELDS.items():
        rows = row_dicts(sheet_rows(workbook, sheet_name), sheet_name)
        for row_number, row in enumerate(rows, start=2):
            for field in fields:
                value = row.get(field, "").strip()
                if not is_obvious_english_narrative(value):
                    continue
                findings.setdefault(sheet_name, []).append(
                    f"第 {row_number} 行字段 {field} 存在明显全英文描述：{value[:80]}"
                )
    if findings:
        fail(
            format_findings(
                "正式测试设计描述性内容必须使用中文；实际 UI 文案、标识符和必要技术术语可保留原文：",
                findings,
            )
        )


def emit_warnings(category: str, messages: list[str]) -> None:
    if not messages:
        return
    print(f"WARNING: {category}（共 {len(messages)} 项）", file=sys.stderr)
    for message in messages[:FINDING_DISPLAY_LIMIT]:
        print(f"WARNING: {message}", file=sys.stderr)
    if len(messages) > FINDING_DISPLAY_LIMIT:
        print(f"WARNING: 其余 {len(messages) - FINDING_DISPLAY_LIMIT} 项已省略", file=sys.stderr)


def format_row_numbers(rows: list[int]) -> str:
    return "、".join(str(row) for row in rows)


def validate_atomic_scenario_rows(scenario_rows: list[dict[str, str]]) -> None:
    findings = {
        "生成场景必填字段缺失": [],
        "场景 ID 重复": [],
        "DFX 原子映射无效": [],
        "场景精确重复": [],
    }
    scene_ids: dict[str, list[int]] = {}
    scenario_signatures: dict[tuple[str, ...], list[int]] = {}
    dfx_groups: dict[tuple[str, ...], list[tuple[int, tuple[str, ...]]]] = {}

    for index, row in enumerate(scenario_rows, start=2):
        if row.get("是否生成用例", "") == "是":
            missing = [field for field in GENERATED_SCENARIO_REQUIRED_FIELDS if not row.get(field, "").strip()]
            if missing:
                findings["生成场景必填字段缺失"].append(f"第 {index} 行缺少 {missing}")

        scene_id_key = normalize_signature_value(row.get("场景 ID", ""))
        if scene_id_key:
            scene_ids.setdefault(scene_id_key, []).append(index)

        dimensions = split_dfx_values(row.get("DFX维度", ""))
        scenarios = split_dfx_values(row.get("DFX场景", ""))
        if len(dimensions) != 1 or len(scenarios) != 1:
            findings["DFX 原子映射无效"].append(
                f"第 {index} 行必须且只能填写一个 DFX维度和一个 DFX场景，当前为 {dimensions} / {scenarios}"
            )
        elif dimensions[0] not in DFX_SCENARIOS:
            findings["DFX 原子映射无效"].append(f"第 {index} 行 DFX维度无效：{dimensions[0]}")
        elif scenarios[0] not in DFX_SCENARIOS[dimensions[0]]:
            findings["DFX 原子映射无效"].append(
                f"第 {index} 行 {dimensions[0]} 不允许使用 DFX场景：{scenarios[0]}"
            )

        row_signature = signature(row, SCENARIO_SIGNATURE_FIELDS)
        scenario_signatures.setdefault(row_signature, []).append(index)
        dfx_key = signature(row, ["功能点", "测试对象/页面元素", "DFX维度", "DFX场景"])
        dfx_groups.setdefault(dfx_key, []).append((index, row_signature))

    for rows in scene_ids.values():
        if len(rows) > 1:
            findings["场景 ID 重复"].append(f"第 {format_row_numbers(rows)} 行使用相同场景 ID")
    for rows in scenario_signatures.values():
        if len(rows) > 1:
            findings["场景精确重复"].append(f"第 {format_row_numbers(rows)} 行的场景签名完全相同，应合并")

    repeated_dfx_warnings = []
    for key, grouped_rows in dfx_groups.items():
        if not all(key) or len(grouped_rows) < 2:
            continue
        distinct_signatures = {row_signature for _, row_signature in grouped_rows}
        if len(distinct_signatures) > 1:
            rows = [row_number for row_number, _ in grouped_rows]
            repeated_dfx_warnings.append(
                f"测试场景矩阵第 {format_row_numbers(rows)} 行重复使用同一功能点、对象和 DFX 组合，"
                "请确认输入/状态/观察点差异确有必要"
            )
    emit_warnings("DFX 组合重复提示，不影响退出码", repeated_dfx_warnings)

    if any(findings.values()):
        fail(format_findings("测试场景矩阵轻量 DFX 硬校验未通过：", findings))


def warn_case_merge_candidates(function_rows: list[dict[str, str]]) -> None:
    groups: dict[tuple[str, ...], list[tuple[int, str]]] = {}
    for index, row in enumerate(function_rows, start=2):
        merge_signature = signature(row, CASE_MERGE_SIGNATURE_FIELDS)
        if not merge_signature[0] or not merge_signature[-1]:
            continue
        groups.setdefault(merge_signature, []).append((index, row.get("用例 ID", "")))
    warnings = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        row_numbers = [row_number for row_number, _ in rows]
        case_ids = [case_id for _, case_id in rows if case_id]
        case_id_text = "、".join(case_ids) if case_ids else "未填写"
        warnings.append(
            f"功能测试用例第 {format_row_numbers(row_numbers)} 行具有相同功能点、前置条件、测试数据和操作步骤"
            f"（用例 ID：{case_id_text}），可评估是否合并；不得为消除警告破坏 DFX 对应或业务闭环"
        )
    emit_warnings("用例合并候选提示；交付前必须逐项分类为需合并、合理差异或校验误报", warnings)


def validate_evidence_status_consistency(
    risk_rows: list[dict[str, str]],
    coverage_rows: list[dict[str, str]],
    overview_rows: list[dict[str, str]] | None = None,
    story_rows: list[dict[str, str]] | None = None,
) -> None:
    findings = {
        "待确认字段未关闭": [],
        "风险状态缺失或非法": [],
        "待实探风险未关闭": [],
        "待确认理解问题未关闭": [],
        "已覆盖与未解决备注并存": [],
    }
    for sheet_name, rows in [
        ("测试设计总览", overview_rows or []),
        ("需求用户故事拆解", story_rows or []),
    ]:
        for index, row in enumerate(rows, start=2):
            question = row.get("待确认问题", "").strip()
            if has_open_understanding_question(question):
                findings["待确认字段未关闭"].append(
                    f"{sheet_name} 第 {index} 行仍有待确认问题：{question}；必须先等待用户回复并清空或更新为已确认"
                )
    for index, row in enumerate(risk_rows, start=2):
        risk_id = row.get("编号", "") or f"第 {index} 行"
        risk_type = row.get("类型", "").strip()
        raw_status = row.get("状态", "").strip()
        status = normalize_signature_value(raw_status)
        if not raw_status:
            findings["风险状态缺失或非法"].append(f"风险与待确认问题 {risk_id} 的状态不能为空")
            continue
        if raw_status not in RISK_ALLOWED_STATUSES:
            findings["风险状态缺失或非法"].append(
                f"风险与待确认问题 {risk_id} 的状态只能使用 {sorted(RISK_ALLOWED_STATUSES)}，当前为：{raw_status}"
            )
            continue
        if risk_type == "待确认" and raw_status not in {"待确认", "已确认", "已关闭"}:
            findings["风险状态缺失或非法"].append(
                f"风险与待确认问题 {risk_id} 的类型为待确认，状态只能为待确认、已确认或已关闭"
            )
        if raw_status == "已确认":
            confirmation_text = "\n".join(
                row.get(field, "") for field in ["描述", "建议处理方式"]
            )
            if UNRESOLVED_CONFIRMATION_PATTERN.search(confirmation_text):
                findings["待确认理解问题未关闭"].append(
                    f"风险与待确认问题 {risk_id} 标记为已确认，但描述或建议仍包含未确认语义；"
                    "必须保留待确认并等待用户回复，或改写为已确认结论"
                )
        if status == "待实探":
            findings["待实探风险未关闭"].append(
                f"风险与待确认问题 {risk_id} 仍为待实探；请先完成定向补探，"
                "客观受限时改为需联调、待环境或缺权限并写明原因"
            )
        elif status == "待确认":
            findings["待确认理解问题未关闭"].append(
                f"风险与待确认问题 {risk_id} 仍为待确认；请先结束当前轮次等待用户明确回复，"
                "收到回复后更新状态再继续交付"
            )

    for index, row in enumerate(coverage_rows, start=2):
        note = row.get("待确认问题/备注", "")
        if row.get("覆盖状态", "") == "已覆盖" and UNRESOLVED_COVERAGE_NOTE_PATTERN.match(note):
            element = row.get("元素名称/文案", "") or row.get("元素 ID", "") or "未命名元素"
            findings["已覆盖与未解决备注并存"].append(
                f"第 {index} 行元素“{element}”已标记已覆盖，但备注仍是未解决状态：{note}"
            )
    if any(findings.values()):
        fail(
            format_findings(
                "风险与页面元素证据状态门禁未通过：",
                findings,
            )
        )


def load_discovery_state(path: Path) -> dict[str, object]:
    if not path.exists():
        fail(f"深探状态文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"深探状态文件无法读取或不是合法 JSON：{path}；{exc}")
    if not isinstance(data, dict):
        fail("深探状态文件根节点必须是 JSON 对象")
    return data


def string_list(value: object, label: str, findings: dict[str, list[str]], required: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or (required and not value)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        findings.setdefault("状态结构错误", []).append(f"{label} 必须是非空字符串数组")
        return []
    return [item.strip() for item in value]


def branch_value_present(value: str, text: str) -> bool:
    normalized_value = re.sub(r"\s+", " ", value or "").strip().lower()
    normalized_text = re.sub(r"\s+", " ", text or "").strip().lower()
    if not normalized_value:
        return False
    if re.fullmatch(r"[a-z0-9_.+-]+", normalized_value):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_value)}(?![a-z0-9])",
                normalized_text,
            )
        )
    return normalized_value in normalized_text


def row_contains_branch_value(row: dict[str, str], fields: list[str], value: str) -> bool:
    return any(branch_value_present(value, row.get(field, "")) for field in fields)


def target_requires_persistent_commit(target: dict[str, object]) -> bool:
    text = "\n".join(
        str(target.get(field, ""))
        for field in ["page", "element", "kind", "action", "result"]
    )
    return bool(PERSISTENT_BRANCH_TARGET_PATTERN.search(text))


def target_matches(
    target: dict[str, object],
    pattern: re.Pattern[str],
    fields: list[str] = PAGINATION_TARGET_FIELDS,
) -> bool:
    return any(pattern.search(str(target.get(field, ""))) for field in fields)


def validate_discovery_state(
    path: Path,
    workbook_data: dict[str, object] | None = None,
) -> dict[str, object]:
    data = load_discovery_state(path)
    findings: dict[str, list[str]] = {
        "状态结构错误": [],
        "深探未收口": [],
        "状态证据不足": [],
        "页面覆盖不同步": [],
        "事实去向缺失": [],
        "场景用例映射缺失": [],
        "逐项用例覆盖缺失": [],
        "分页专项未落地": [],
    }
    if data.get("version") != 1:
        findings["状态结构错误"].append("version 必须为 1")
    if not isinstance(data.get("scope"), str) or not str(data.get("scope", "")).strip():
        findings["状态结构错误"].append("scope 必须填写当前最小模块或页面范围")
    if data.get("baseline_complete") is not True:
        findings["深探未收口"].append("baseline_complete 必须为 true")
    if data.get("closure_rescan_complete") is not True:
        findings["深探未收口"].append("closure_rescan_complete 必须为 true")
    if data.get("closure_rescan_new_targets") != 0:
        findings["深探未收口"].append("closure_rescan_new_targets 必须为 0；发现新目标后应继续深探并再次复扫")
    questions = data.get("understanding_questions")
    if not isinstance(questions, list):
        findings["状态结构错误"].append("understanding_questions 必须是数组")
    elif questions:
        findings["深探未收口"].append("仍存在待用户理解确认问题，必须结束当前轮次等待回复")

    targets = data.get("targets")
    if not isinstance(targets, list) or not targets:
        findings["状态结构错误"].append("targets 必须包含至少一个深探目标")
        targets = []
    target_ids: set[str] = set()
    target_signatures: dict[tuple[str, ...], str] = {}
    target_rows: list[dict[str, object]] = []
    for index, raw_target in enumerate(targets, start=1):
        label = f"targets[{index}]"
        if not isinstance(raw_target, dict):
            findings["状态结构错误"].append(f"{label} 必须是对象")
            continue
        target = raw_target
        target_rows.append(target)
        target_id = str(target.get("id", "")).strip()
        if not target_id:
            findings["状态结构错误"].append(f"{label} 缺少 id")
        elif target_id in target_ids:
            findings["状态结构错误"].append(f"目标 ID 重复：{target_id}")
        else:
            target_ids.add(target_id)
        for field in ["page", "element", "kind", "control_type"]:
            if not isinstance(target.get(field), str) or not str(target.get(field, "")).strip():
                findings["状态结构错误"].append(f"{label} 缺少 {field}")
        status = str(target.get("status", "")).strip()
        if status not in DISCOVERY_FINAL_STATUSES:
            findings["深探未收口"].append(
                f"{target_id or label} 状态必须为已验证、客观受限或不适用，当前为：{status or '空'}"
            )
            continue
        if status == "客观受限" and not str(target.get("blocking_reason", "")).strip():
            findings["状态证据不足"].append(f"{target_id or label} 客观受限但未填写 blocking_reason")
        if status == "不适用" and not str(target.get("reason", "")).strip():
            findings["状态证据不足"].append(f"{target_id or label} 不适用但未填写 reason")
        if status == "已验证":
            for field in ["evidence_source", "evidence", "observation", "result"]:
                if not isinstance(target.get(field), str) or not str(target.get(field, "")).strip():
                    findings["状态证据不足"].append(f"{target_id or label} 已验证但缺少 {field}")
            if str(target.get("kind", "")).strip() in DISCOVERY_INTERACTIVE_KINDS:
                if not str(target.get("action", "")).strip():
                    findings["状态证据不足"].append(f"{target_id or label} 交互目标缺少 action")
                control_type = str(target.get("control_type", "")).lower()
                if str(target.get("kind", "")).strip() == "状态变化" or any(
                    marker.lower() in control_type for marker in DISCOVERY_STATEFUL_CONTROL_MARKERS
                ):
                    for field in ["state_before", "state_after", "terminal_action", "recovery"]:
                        if not str(target.get(field, "")).strip():
                            findings["状态证据不足"].append(f"{target_id or label} 状态变化目标缺少 {field}")

        target_signature = tuple(
            normalize(str(target.get(field, "")))
            for field in ["page", "state_before", "element", "action", "branch_value"]
        )
        previous_target = target_signatures.get(target_signature)
        if previous_target and any(target_signature):
            findings["状态结构错误"].append(
                f"语义目标重复：{previous_target} 与 {target_id or label}；请按页面、状态、元素、动作和数据分支去重"
            )
        else:
            target_signatures[target_signature] = target_id or label

    branch_values_by_target: dict[str, list[str]] = {}
    for index, target in enumerate(target_rows, start=1):
        parent_id = str(target.get("parent_id", "")).strip()
        branch_value = str(target.get("branch_value", "")).strip()
        is_branch_child = bool(parent_id and branch_value)
        if parent_id and parent_id not in target_ids:
            findings["状态结构错误"].append(f"targets[{index}] parent_id 不存在：{parent_id}")
        branch_policy = str(target.get("branch_policy", "")).strip()
        if branch_policy and not is_branch_child and branch_policy not in BRANCH_POLICIES:
            findings["状态结构错误"].append(
                f"{str(target.get('id', '')).strip() or f'targets[{index}]'} branch_policy 只能为逐项验证或用例逐项覆盖"
            )
        if not is_branch_child and branch_policy in BRANCH_POLICIES:
            discovered_values = string_list(
                target.get("discovered_values"),
                f"{str(target.get('id', '')).strip() or f'targets[{index}]'}.discovered_values",
                findings,
            )
            branch_values_by_target[str(target.get("id", "")).strip() or f"targets[{index}]"] = discovered_values
        if branch_policy == "逐项验证":
            child_values = {
                str(child.get("branch_value", "")).strip()
                for child in target_rows
                if str(child.get("parent_id", "")).strip() == str(target.get("id", "")).strip()
                and str(child.get("branch_value", "")).strip()
            }
            missing_values = sorted(set(discovered_values) - child_values)
            if missing_values:
                findings["深探未收口"].append(
                    f"{str(target.get('id', '')).strip() or f'targets[{index}]'} 要求逐项验证，仍缺少分支目标：{missing_values}"
                )

    has_pagination_evidence = any(target_matches(target, PAGINATION_EVIDENCE_PATTERN) for target in target_rows)
    pagination_targets = [
        target
        for target in target_rows
        if target_matches(target, PAGINATION_EVIDENCE_PATTERN, PAGINATION_IDENTITY_FIELDS)
    ]
    if has_pagination_evidence and not pagination_targets:
        findings["分页专项未落地"].append("深探证据中已出现分页语义，但没有建立具体分页目标")
    for target in pagination_targets:
        target_id = str(target.get("id", "")).strip() or "未命名分页目标"
        parent_id = str(target.get("parent_id", "")).strip()
        branch_value = str(target.get("branch_value", "")).strip()
        element = str(target.get("element", "")).strip()
        if PAGINATION_GENERIC_ELEMENT_PATTERN.fullmatch(element):
            page = normalize(str(target.get("page", "")))
            has_specific_target = any(
                other is not target
                and normalize(str(other.get("page", ""))) == page
                and target_matches(other, PAGINATION_EVIDENCE_PATTERN, PAGINATION_IDENTITY_FIELDS)
                and not PAGINATION_GENERIC_ELEMENT_PATTERN.fullmatch(str(other.get("element", "")).strip())
                for other in pagination_targets
            )
            if not has_specific_target:
                findings["分页专项未落地"].append(
                    f"{target_id} 只有笼统的“{element}”，必须按页面实际能力拆分分页目标"
                )
        if sum(bool(pattern.search(element)) for pattern in PAGINATION_CAPABILITY_PATTERNS) > 1:
            findings["分页专项未落地"].append(
                f"{target_id} 在一个目标中合并了多项分页能力，必须按实际控件分别建目标"
            )
        if (
            str(target.get("status", "")).strip() == "已验证"
            and not (parent_id and branch_value)
            and target_matches(target, PAGE_SIZE_PATTERN)
            and str(target.get("branch_policy", "")).strip() != "逐项验证"
        ):
            findings["分页专项未落地"].append(
                f"{target_id} 已发现页容量能力，必须记录 branch_policy=逐项验证 和全部 discovered_values"
            )

    if workbook_data is not None:
        scenario_ids = workbook_data["scenario_ids"]
        generated_scenario_ids = workbook_data["generated_scenario_ids"]
        case_ids = workbook_data["case_ids"]
        risk_ids = workbook_data["risk_ids"]
        performance_ids = workbook_data["performance_ids"]
        coverage_rows = workbook_data["coverage_rows"]
        scenario_rows_by_id = workbook_data["scenario_rows_by_id"]
        case_rows_by_id = workbook_data["case_rows_by_id"]
        assert isinstance(scenario_ids, set)
        assert isinstance(generated_scenario_ids, set)
        assert isinstance(case_ids, set)
        assert isinstance(risk_ids, set)
        assert isinstance(performance_ids, set)
        assert isinstance(coverage_rows, list)
        assert isinstance(scenario_rows_by_id, dict)
        assert isinstance(case_rows_by_id, dict)
        target_elements = {
            (normalize(str(target.get("page", ""))), normalize(str(target.get("element", ""))))
            for target in target_rows
            if str(target.get("page", "")).strip() and str(target.get("element", "")).strip()
        }
        page_evidence_elements = {
            (normalize(row.get("页面/入口", "")), normalize(row.get("元素名称/文案", "")))
            for row in coverage_rows
            if row.get("页面/入口")
            and row.get("元素名称/文案")
            and is_page_discovery_source(row.get("发现方式", ""))
        }
        missing_targets = sorted(page_evidence_elements - target_elements)
        if missing_targets:
            findings["页面覆盖不同步"].append(f"页面实探覆盖清单中的元素没有进入动态队列：{missing_targets[:10]}")
        unknown_targets = sorted(target_elements - {
            (normalize(row.get("页面/入口", "")), normalize(row.get("元素名称/文案", "")))
            for row in coverage_rows
            if row.get("页面/入口") and row.get("元素名称/文案")
        })
        if unknown_targets:
            findings["页面覆盖不同步"].append(f"动态队列中的元素没有写入页面元素覆盖清单：{unknown_targets[:10]}")
        referenced_scenarios: set[str] = set()
        target_scenario_references: dict[str, list[str]] = {}
        for index, target in enumerate(target_rows, start=1):
            target_id = str(target.get("id", "")).strip() or f"targets[{index}]"
            disposition = str(target.get("disposition", "")).strip()
            reference_ids = string_list(target.get("reference_ids"), f"{target_id}.reference_ids", findings)
            valid_references: set[str] = set()
            if disposition == "场景":
                valid_references = scenario_ids
                referenced_scenarios.update(reference_ids)
                target_scenario_references[target_id] = reference_ids
            elif disposition == "风险":
                valid_references = risk_ids
            elif disposition == "性能":
                valid_references = performance_ids
            elif disposition == "不适用":
                if not str(target.get("reason", "")).strip():
                    findings["事实去向缺失"].append(f"{target_id} 标记不适用但未填写 reason")
                continue
            else:
                findings["事实去向缺失"].append(f"{target_id} 必须填写 disposition：场景、风险、性能或不适用")
                continue
            unknown = sorted(set(reference_ids) - valid_references)
            if unknown:
                findings["事实去向缺失"].append(f"{target_id} 引用了不存在的 {disposition} ID：{unknown}")
            if (
                target in pagination_targets
                and str(target.get("kind", "")).strip() in DISCOVERY_INTERACTIVE_KINDS
                and not PAGINATION_GENERIC_ELEMENT_PATTERN.fullmatch(str(target.get("element", "")).strip())
                and disposition != "场景"
            ):
                findings["分页专项未落地"].append(
                    f"{target_id} 是实际分页交互能力，必须进入测试场景和功能用例；数据不足只能标记实探受限"
                )

        mappings = data.get("scenario_case_mapping")
        if not isinstance(mappings, list):
            findings["状态结构错误"].append("scenario_case_mapping 必须是数组")
            mappings = []
        mapped_scenarios: set[str] = set()
        scenario_to_cases: dict[str, set[str]] = {}
        for index, raw_mapping in enumerate(mappings, start=1):
            if not isinstance(raw_mapping, dict):
                findings["状态结构错误"].append(f"scenario_case_mapping[{index}] 必须是对象")
                continue
            scene_id = str(raw_mapping.get("scenario_id", "")).strip()
            mapped_cases = string_list(raw_mapping.get("case_ids"), f"场景 {scene_id or index} 的 case_ids", findings)
            if scene_id not in generated_scenario_ids:
                findings["场景用例映射缺失"].append(f"映射引用了不存在或未标记生成用例的场景：{scene_id or '空'}")
            else:
                mapped_scenarios.add(scene_id)
            scenario_to_cases.setdefault(scene_id, set()).update(mapped_cases)
            unknown_cases = sorted(set(mapped_cases) - case_ids)
            if unknown_cases:
                findings["场景用例映射缺失"].append(f"场景 {scene_id} 引用了不存在的用例：{unknown_cases}")
            scenario_row = scenario_rows_by_id.get(scene_id, {})
            scenario_function_point = normalize(str(scenario_row.get("功能点", "")))
            for case_id in mapped_cases:
                case_row = case_rows_by_id.get(case_id, {})
                case_function_point = normalize(str(case_row.get("功能点", "")))
                if (
                    scenario_function_point
                    and case_function_point
                    and scenario_function_point != case_function_point
                ):
                    findings["场景用例映射缺失"].append(
                        f"场景 {scene_id} 的功能点“{scenario_row.get('功能点', '')}”"
                        f"与用例 {case_id} 的功能点“{case_row.get('功能点', '')}”不一致"
                    )
        missing_mappings = sorted(generated_scenario_ids - mapped_scenarios)
        if missing_mappings:
            findings["场景用例映射缺失"].append(f"以下生成场景没有对应功能用例映射：{missing_mappings}")
        missing_target_scenarios = sorted(referenced_scenarios - generated_scenario_ids)
        if missing_target_scenarios:
            findings["事实去向缺失"].append(f"深探事实关联的场景未标记生成用例：{missing_target_scenarios}")

        pagination_capability_scenarios: dict[str, set[str]] = {}
        for target in pagination_targets:
            if (
                str(target.get("kind", "")).strip() not in DISCOVERY_INTERACTIVE_KINDS
                or str(target.get("disposition", "")).strip() != "场景"
            ):
                continue
            element = str(target.get("element", "")).strip()
            if PAGINATION_GENERIC_ELEMENT_PATTERN.fullmatch(element):
                continue
            references = set(target_scenario_references.get(str(target.get("id", "")).strip(), []))
            for capability, pattern in PAGINATION_CAPABILITY_RULES:
                if pattern.search(element):
                    pagination_capability_scenarios.setdefault(capability, set()).update(references)
        capability_names = sorted(pagination_capability_scenarios)
        for left_index, left_name in enumerate(capability_names):
            for right_name in capability_names[left_index + 1:]:
                shared_scenarios = sorted(
                    pagination_capability_scenarios[left_name]
                    & pagination_capability_scenarios[right_name]
                )
                if shared_scenarios:
                    findings["分页专项未落地"].append(
                        f"分页能力“{left_name}”与“{right_name}”复用了场景 {shared_scenarios}，"
                        "不同分页动作必须独立形成场景"
                    )
                left_cases = set().union(
                    *(scenario_to_cases.get(scene_id, set()) for scene_id in pagination_capability_scenarios[left_name])
                )
                right_cases = set().union(
                    *(scenario_to_cases.get(scene_id, set()) for scene_id in pagination_capability_scenarios[right_name])
                )
                shared_cases = sorted(left_cases & right_cases)
                if shared_cases:
                    findings["分页专项未落地"].append(
                        f"分页能力“{left_name}”与“{right_name}”复用了功能用例 {shared_cases}，"
                        "不同分页动作必须独立形成用例"
                    )

        scenario_branch_fields = ["测试对象/页面元素", "输入数据/状态条件", "观察点"]
        case_branch_fields = ["用例标题", "测试数据", "操作步骤", "预期结果"]
        for target in target_rows:
            target_id = str(target.get("id", "")).strip()
            if str(target.get("parent_id", "")).strip() and str(target.get("branch_value", "")).strip():
                continue
            branch_policy = str(target.get("branch_policy", "")).strip()
            if branch_policy not in BRANCH_POLICIES:
                continue
            reference_ids = target_scenario_references.get(target_id, [])
            page_size_value_scenarios: dict[str, set[str]] = {}
            page_size_value_cases: dict[str, set[str]] = {}
            for value in branch_values_by_target.get(target_id, []):
                matching_scenarios = {
                    scene_id
                    for scene_id in reference_ids
                    if scene_id in scenario_rows_by_id
                    and row_contains_branch_value(
                        scenario_rows_by_id[scene_id], scenario_branch_fields, value
                    )
                }
                if not matching_scenarios:
                    findings["逐项用例覆盖缺失"].append(
                        f"{target_id} 的分支值“{value}”未写入该目标关联的测试场景"
                    )
                    continue
                mapped_case_ids = set().union(
                    *(scenario_to_cases.get(scene_id, set()) for scene_id in matching_scenarios)
                )
                matching_cases = {
                    case_id
                    for case_id in mapped_case_ids
                    if case_id in case_rows_by_id
                    and row_contains_branch_value(case_rows_by_id[case_id], case_branch_fields, value)
                }
                if not matching_cases:
                    findings["逐项用例覆盖缺失"].append(
                        f"{target_id} 的分支值“{value}”未写入关联场景映射的功能用例"
                    )
                    continue
                if target_matches(target, PAGE_SIZE_PATTERN):
                    page_size_value_scenarios[value] = matching_scenarios
                    page_size_value_cases[value] = matching_cases
                if branch_policy == "用例逐项覆盖":
                    requires_commit = target_requires_persistent_commit(target)
                    has_terminal_result = any(
                        branch_value_present(value, case_rows_by_id[case_id].get("预期结果", ""))
                        and (
                            DATA_CHANGE_COMMIT_PATTERN.search(case_rows_by_id[case_id].get("操作步骤", ""))
                            if requires_commit
                            else DROPDOWN_RESULT_PATTERN.search(case_rows_by_id[case_id].get("预期结果", ""))
                        )
                        for case_id in matching_cases
                    )
                    if not has_terminal_result:
                        expected_terminal = "保存、提交或等价持久化结果" if requires_commit else "筛选、刷新或等价状态结果"
                        findings["逐项用例覆盖缺失"].append(
                            f"{target_id} 的有效分支值“{value}”缺少逐值的{expected_terminal}验证"
                        )
            if target_matches(target, PAGE_SIZE_PATTERN):
                page_size_values = sorted(page_size_value_scenarios)
                for left_index, left_value in enumerate(page_size_values):
                    for right_value in page_size_values[left_index + 1:]:
                        shared_scenarios = sorted(
                            page_size_value_scenarios[left_value]
                            & page_size_value_scenarios[right_value]
                        )
                        if shared_scenarios:
                            findings["分页专项未落地"].append(
                                f"{target_id} 的页容量“{left_value}”与“{right_value}”"
                                f"复用了场景 {shared_scenarios}，每个页容量必须独立形成场景"
                            )
                        shared_cases = sorted(
                            page_size_value_cases.get(left_value, set())
                            & page_size_value_cases.get(right_value, set())
                        )
                        if shared_cases:
                            findings["分页专项未落地"].append(
                                f"{target_id} 的页容量“{left_value}”与“{right_value}”"
                                f"复用了功能用例 {shared_cases}，每个页容量必须独立形成用例"
                            )

    if any(findings.values()):
        fail(format_findings("动态深探队列与状态证据门禁未通过：", findings))
    return data


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


def validate_workbook(
    workbook: Path,
    formal_template: Path | None = None,
    findings: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    own_findings = findings is None
    if findings is None:
        findings = {}
    if not workbook.exists():
        fail(f"Workbook not found: {workbook}")
    with zipfile.ZipFile(workbook) as zf:
        sheet_names = list(workbook_sheet_paths(zf))
    if sheet_names != EXPECTED_SHEETS:
        fail(f"Workbook sheets mismatch. Expected {EXPECTED_SHEETS}, got {sheet_names}")
    if formal_template:
        collect_validation_issue(
            findings,
            "正式工作簿模板与样式",
            lambda: assert_formal_template_invariants(workbook, formal_template),
        )
    collect_validation_issue(
        findings,
        "正式工作簿模板与样式",
        lambda: assert_no_residual_markers(workbook, EXPECTED_SHEETS),
    )
    collect_validation_issue(
        findings,
        "正式工作簿模板与样式",
        lambda: validate_table_ranges(workbook, EXPECTED_SHEETS),
    )
    collect_validation_issue(
        findings,
        "正式工作簿模板与样式",
        lambda: validate_formal_workbook_styles(workbook),
    )
    collect_validation_issue(
        findings,
        "正式工作簿语言",
        lambda: validate_chinese_delivery_language(workbook),
    )

    overview_rows_raw = sheet_rows(workbook, "测试设计总览")
    require_headers(overview_rows_raw, ["待确认问题"], "测试设计总览")
    overview_rows = row_dicts(overview_rows_raw, "测试设计总览")

    story_rows_raw = sheet_rows(workbook, "需求用户故事拆解")
    require_headers(
        story_rows_raw,
        ["Story ID/需求 ID", "用户故事/需求描述", "角色", "待确认问题"],
        "需求用户故事拆解",
    )
    story_rows = row_dicts(story_rows_raw, "需求用户故事拆解")
    if not story_rows:
        add_finding(findings, "需求用户故事拆解", "需求用户故事拆解 must contain at least one story")
    collect_validation_issue(
        findings,
        "需求用户故事拆解",
        lambda: validate_story_rows(story_rows),
    )
    story_ids = {row.get("Story ID/需求 ID", "") for row in story_rows if row.get("Story ID/需求 ID", "")}

    scenario_rows_raw = sheet_rows(workbook, "测试场景矩阵")
    collect_validation_issue(
        findings,
        "测试场景矩阵",
        lambda: assert_no_deprecated_scenario_headers(scenario_rows_raw, "测试场景矩阵"),
    )
    require_headers(
        scenario_rows_raw,
        [
            "场景 ID",
            "Story ID/需求 ID",
            "功能点",
            "测试维度",
            "DFX维度",
            "DFX场景",
            "测试对象/页面元素",
            "输入数据/状态条件",
            "观察点",
            "是否生成用例",
        ],
        "测试场景矩阵",
    )
    scenario_rows = row_dicts(scenario_rows_raw, "测试场景矩阵")
    if not scenario_rows:
        add_finding(findings, "测试场景矩阵", "测试场景矩阵 must contain at least one DFX-driven scenario")
    collect_validation_issue(
        findings,
        "测试场景矩阵",
        lambda: assert_allowed_values(
            scenario_rows,
            "测试场景矩阵",
            "是否生成用例",
            FORMAL_ALLOWED_VALUES[("测试场景矩阵", "是否生成用例")],
        ),
    )
    collect_validation_issue(
        findings,
        "测试场景矩阵",
        lambda: validate_atomic_scenario_rows(scenario_rows),
    )
    scenario_ids = {row.get("场景 ID", "") for row in scenario_rows if row.get("场景 ID", "")}
    generated_scenario_ids = {
        row.get("场景 ID", "")
        for row in scenario_rows
        if row.get("是否生成用例", "") == "是" and row.get("场景 ID", "")
    }
    generated_scenario_dfx: set[tuple[str, str]] = set()
    for row in scenario_rows:
        if row.get("是否生成用例", "") == "是":
            generated_scenario_dfx.update(dfx_pairs(row.get("DFX维度", ""), row.get("DFX场景", "")))

    function_rows_raw = sheet_rows(workbook, "功能测试用例")
    require_headers(
        function_rows_raw,
        [
            "用例 ID",
            "Story ID/需求 ID",
            "功能点",
            "用例标题",
            "测试类型",
            "DFX维度",
            "DFX场景",
            "操作步骤",
            "预期结果",
            "是否适合自动化",
        ],
        "功能测试用例",
    )
    function_rows = row_dicts(function_rows_raw, "功能测试用例")
    if not function_rows:
        add_finding(findings, "功能测试用例", "功能测试用例 must contain at least one case")
    collect_validation_issue(
        findings,
        "Story 到场景和用例追踪",
        lambda: validate_story_traceability(story_ids, scenario_rows, function_rows),
    )
    collect_validation_issue(
        findings,
        "功能测试用例",
        lambda: validate_function_case_preflight(function_rows),
    )
    case_ids: set[str] = set()
    case_titles: dict[str, str] = {}
    case_function_points: dict[str, str] = {}
    function_dfx: set[tuple[str, str]] = set()
    for index, row in enumerate(function_rows, start=2):
        case_id = row.get("用例 ID", "")
        function_point = row.get("功能点", "")
        title = row.get("用例标题", "")
        case_ids.add(case_id)
        case_titles[case_id] = title
        case_function_points[case_id] = function_point
        function_dfx.update(dfx_pairs(row.get("DFX维度", ""), row.get("DFX场景", "")))
    warn_case_merge_candidates(function_rows)

    performance_rows_raw = sheet_rows(workbook, "性能测试设计")
    require_headers(performance_rows_raw, ["性能场景 ID", "业务链路", "性能测试类型", "DFX维度", "DFX场景", "是否纳入本轮测试"], "性能测试设计")
    performance_rows = row_dicts(performance_rows_raw, "性能测试设计")
    if not performance_rows:
        add_finding(findings, "性能测试设计", "性能测试设计 must contain at least one scenario or explicit not-applicable row")
    collect_validation_issue(
        findings,
        "性能测试设计",
        lambda: assert_allowed_values(
            performance_rows,
            "性能测试设计",
            "是否纳入本轮测试",
            FORMAL_ALLOWED_VALUES[("性能测试设计", "是否纳入本轮测试")],
        ),
    )
    performance_dfx: set[tuple[str, str]] = set()
    performance_ids = {
        row.get("性能场景 ID", "") for row in performance_rows if row.get("性能场景 ID", "")
    }
    for index, row in enumerate(performance_rows, start=2):
        if row.get("是否纳入本轮测试", "") != "否":
            collect_validation_issue(
                findings,
                "性能测试设计",
                lambda row=row, index=index: assert_dfx_mapping(
                    row.get("DFX维度", ""),
                    row.get("DFX场景", ""),
                    f"性能测试设计 row {index}",
                ),
            )
            performance_dfx.update(dfx_pairs(row.get("DFX维度", ""), row.get("DFX场景", "")))
            dimensions = set(split_dfx_values(row.get("DFX维度", "")))
            if dimensions and not dimensions & {"DFP性能", "DFX极端", "DFO运维", "DFR可靠"}:
                add_finding(
                    findings,
                    "性能测试设计",
                    f"性能测试设计 row {index} should map to DFP性能/DFX极端/DFO运维/DFR可靠, got {sorted(dimensions)}",
                )

    risk_rows_raw = sheet_rows(workbook, "风险与待确认问题")
    require_headers(
        risk_rows_raw,
        ["编号", "类型", "关联DFX维度", "关联DFX场景", "描述", "影响范围", "建议处理方式", "状态"],
        "风险与待确认问题",
    )
    risk_dfx: set[tuple[str, str]] = set()
    risk_rows = row_dicts(risk_rows_raw, "风险与待确认问题")
    risk_ids = {row.get("编号", "") for row in risk_rows if row.get("编号", "")}
    for index, row in enumerate(risk_rows, start=2):
        collect_validation_issue(
            findings,
            "风险与待确认问题",
            lambda row=row, index=index: assert_dfx_mapping(
                row.get("关联DFX维度", ""),
                row.get("关联DFX场景", ""),
                f"风险与待确认问题 row {index}",
            ),
        )
        risk_dfx.update(dfx_pairs(row.get("关联DFX维度", ""), row.get("关联DFX场景", "")))

    coverage_rows_raw = sheet_rows(workbook, "页面元素覆盖清单")
    require_headers(
        coverage_rows_raw,
        [
            "元素 ID",
            "元素名称/文案",
            "元素类型",
            "适用DFX维度",
            "适用DFX场景",
            "覆盖用例 ID",
            "覆盖状态",
            "发现方式",
            "待确认问题/备注",
        ],
        "页面元素覆盖清单",
    )
    coverage_rows = row_dicts(coverage_rows_raw, "页面元素覆盖清单")
    if not coverage_rows:
        add_finding(
            findings,
            "页面元素覆盖清单",
            "页面元素覆盖清单必须至少包含一个实际元素或明确的不适用/不测范围记录",
        )
    valid_status = {"已覆盖", "不适用", "不测范围", "待确认"}
    for index, row in enumerate(coverage_rows, start=2):
        element = row.get("元素名称/文案", "")
        if not element:
            add_finding(findings, "页面元素覆盖清单", f"页面元素覆盖清单 row {index} is missing 元素名称/文案")
        collect_validation_issue(
            findings,
            "页面元素覆盖清单",
            lambda row=row, index=index: assert_dfx_mapping(
                row.get("适用DFX维度", ""),
                row.get("适用DFX场景", ""),
                f"页面元素覆盖清单 row {index}",
            ),
        )
        status = row.get("覆盖状态", "")
        if status not in valid_status:
            add_finding(findings, "页面元素覆盖清单", f"页面元素覆盖清单 row {index} has invalid 覆盖状态: {status}")
        discovery_source = row.get("发现方式", "").strip()
        if not discovery_source:
            add_finding(findings, "页面元素覆盖清单", f"页面元素覆盖清单 row {index} 的发现方式不能为空")
        elif discovery_source not in DISCOVERY_SOURCE_ALLOWED_VALUES:
            add_finding(
                findings,
                "页面元素覆盖清单",
                f"页面元素覆盖清单 row {index} 的发现方式只能使用 {sorted(DISCOVERY_SOURCE_ALLOWED_VALUES)}，当前为：{discovery_source}",
            )
        linked_ids = parse_ids(row.get("覆盖用例 ID", ""))
        if status == "已覆盖":
            if not linked_ids:
                add_finding(findings, "页面元素覆盖清单", f"页面元素覆盖清单 row {index} is 已覆盖 but missing 覆盖用例 ID")
            unknown = sorted(linked_ids - case_ids)
            if unknown:
                add_finding(findings, "页面元素覆盖清单", f"页面元素覆盖清单 row {index} references unknown case IDs: {unknown}")
        elif not row.get("待确认问题/备注", ""):
            add_finding(findings, "页面元素覆盖清单", f"页面元素覆盖清单 row {index} status {status} must explain reason in 待确认问题/备注")

    collect_validation_issue(
        findings,
        "风险与页面元素状态",
        lambda: validate_evidence_status_consistency(risk_rows, coverage_rows, overview_rows, story_rows),
    )

    landed_dfx = function_dfx | performance_dfx | risk_dfx
    missing_landed_dfx = sorted(generated_scenario_dfx - landed_dfx)
    if missing_landed_dfx:
        add_finding(
            findings,
            "DFX 落地追踪",
            "测试场景矩阵 generated DFX scenarios are not reflected in 功能测试用例/性能测试设计/风险与待确认问题: "
            f"{missing_landed_dfx}",
        )

    workbook_data = {
        "scenario_ids": scenario_ids,
        "generated_scenario_ids": generated_scenario_ids,
        "case_ids": case_ids,
        "risk_ids": risk_ids,
        "performance_ids": performance_ids,
        "case_titles": case_titles,
        "case_function_points": case_function_points,
        "scenario_rows_by_id": {
            row.get("场景 ID", ""): row for row in scenario_rows if row.get("场景 ID", "")
        },
        "case_rows_by_id": {
            row.get("用例 ID", ""): row for row in function_rows if row.get("用例 ID", "")
        },
        "coverage_rows": coverage_rows,
        "scenario_count": len(scenario_rows),
        "case_count": len(function_rows),
        "performance_count": len(performance_rows),
        "risk_count": len(risk_rows),
        "coverage_count": len(coverage_rows),
        "requires_discovery_state": any(is_page_discovery_source(row.get("发现方式", "")) for row in coverage_rows),
    }
    if own_findings and findings:
        fail(format_findings("正式测试设计质检未通过：", findings))
    return workbook_data


def validate_import_workbook(
    import_workbook: Path,
    workbook_data: dict[str, object],
    findings: dict[str, list[str]] | None = None,
) -> int:
    own_findings = findings is None
    if findings is None:
        findings = {}
    if not import_workbook.exists():
        message = f"Import workbook not found: {import_workbook}"
        if own_findings:
            fail(message)
        add_finding(findings, "测试系统导入文件结构", message)
        return 0
    with zipfile.ZipFile(import_workbook) as zf:
        sheet_names = list(workbook_sheet_paths(zf))
    if sheet_names == EXPECTED_SHEETS or "测试系统导入用例" in sheet_names:
        message = "Import workbook must be a copy of 测试用例模板.xlsx, not the formal test design workbook"
        if own_findings:
            fail(message)
        add_finding(findings, "测试系统导入文件结构", message)
        return 0

    rows_raw = first_sheet_rows(import_workbook)
    if not rows_raw:
        message = "Import workbook has no header row"
        if own_findings:
            fail(message)
        add_finding(findings, "测试系统导入文件结构", message)
        return 0
    headers = rows_raw[0]
    if headers[: len(IMPORT_HEADERS)] != IMPORT_HEADERS:
        message = f"Import workbook headers mismatch. Expected {IMPORT_HEADERS}, got {headers}"
        if own_findings:
            fail(message)
        add_finding(findings, "测试系统导入文件结构", message)
        return 0
    collect_validation_issue(
        findings,
        "测试系统导入文件样式",
        lambda: assert_no_residual_markers(import_workbook),
    )
    collect_validation_issue(
        findings,
        "测试系统导入文件样式",
        lambda: validate_table_ranges(import_workbook),
    )
    collect_validation_issue(
        findings,
        "测试系统导入文件样式",
        lambda: assert_data_rows_follow_sample_styles(import_workbook),
    )
    first_sheet_name = ""
    with zipfile.ZipFile(import_workbook) as zf:
        sheet_paths = workbook_sheet_paths(zf)
        first_sheet_name = next(iter(sheet_paths))
    collect_validation_issue(
        findings,
        "测试系统导入文件样式",
        lambda: assert_cells_horizontal_alignment(import_workbook, first_sheet_name, "left"),
    )
    collect_validation_issue(
        findings,
        "测试系统导入文件样式",
        lambda: assert_multiline_cells_wrapped(import_workbook, first_sheet_name, IMPORT_MULTILINE_FIELDS),
    )

    rows = row_dicts(rows_raw, "测试系统导入文件")
    if not rows:
        add_finding(findings, "测试系统导入文件内容", "Import workbook must contain mapped test cases")
    collect_validation_issue(
        findings,
        "测试系统导入文件样式",
        lambda: assert_dropdown_validations_cover_rows(
            import_workbook,
            first_sheet_name,
            list(IMPORT_ALLOWED_VALUES),
            len(rows) + 1,
        ),
    )

    case_titles = workbook_data["case_titles"]
    assert isinstance(case_titles, dict)
    formal_titles = set(case_titles.values())
    imported_titles: set[str] = set()
    for index, row in enumerate(rows, start=2):
        for field in IMPORT_REQUIRED_FIELDS:
            if not row.get(field):
                add_finding(findings, "测试系统导入文件内容", f"Import workbook row {index} is missing required field: {field}")
        for field in IMPORT_AUTO_FIELDS:
            if row.get(field):
                add_finding(findings, "测试系统导入文件内容", f"Import workbook row {index} must leave auto-generated field blank: {field}")
        for field, allowed in IMPORT_ALLOWED_VALUES.items():
            value = row.get(field, "")
            if value not in allowed:
                add_finding(findings, "测试系统导入文件内容", f"Import workbook row {index} has invalid {field}: {value}")
        if row.get("执行方式") == "自动化":
            note = row.get("备注", "") + row.get("标签", "") + row.get("测试用例说明", "")
            if not any(marker in note for marker in ["自动化资产", "脚本", "流水线", "API自动化", "UI自动化"]):
                add_finding(
                    findings,
                    "测试系统导入文件内容",
                    f"Import workbook row {index} uses 自动化 but does not reference an implemented automation asset",
                )
        title = row.get("测试用例名称", "")
        if "-" not in title or " -" in title or "- " in title:
            add_finding(
                findings,
                "测试系统导入文件内容",
                f"Import workbook row {index} 测试用例名称 must use 功能点-当前用例标题 without spaces: {title}",
            )
        row_checks = [
            lambda row=row, index=index: assert_numbered(
                row.get("测试步骤描述", ""), f"Import workbook row {index} 测试步骤描述"
            ),
            lambda row=row, index=index: assert_complete_operation_steps(
                row.get("测试步骤描述", ""), f"Import workbook row {index} 测试步骤描述"
            ),
            lambda row=row, index=index: assert_numbered(
                row.get("测试步骤预期结果", ""), f"Import workbook row {index} 测试步骤预期结果"
            ),
            lambda row=row, index=index: assert_expected_result_consistency(
                row.get("测试步骤预期结果", ""), f"Import workbook row {index} 测试步骤预期结果"
            ),
            lambda row=row, index=index: assert_transient_flow_closed(
                row.get("测试步骤描述", ""),
                row.get("测试步骤预期结果", ""),
                f"Import workbook row {index}",
            ),
        ]
        if row.get("前置条件"):
            row_checks.append(
                lambda row=row, index=index: assert_numbered(
                    row["前置条件"], f"Import workbook row {index} 前置条件"
                )
            )
        for check in row_checks:
            collect_validation_issue(findings, "测试系统导入文件内容", check)
        imported_titles.add(title)

    missing_titles = sorted(formal_titles - imported_titles)
    if missing_titles:
        add_finding(findings, "测试系统导入文件映射", f"Import workbook is missing formal function cases: {missing_titles}")

    xml = first_worksheet_xml(import_workbook)
    for marker, label in {
        'sqref="R2:R2001"': "测试类型",
        'sqref="S2:S2001"': "测试用例级别",
        'sqref="T2:T2001"': "执行方式",
    }.items():
        if marker not in xml:
            add_finding(
                findings,
                "测试系统导入文件样式",
                f"Import workbook is missing preserved {label} dropdown validation: {marker}",
            )
    if own_findings and findings:
        fail(format_findings("测试系统导入文件质检未通过：", findings))
    return len(rows)


def validate_delivery_bundle(
    workbook: Path,
    import_workbook: Path,
    formal_template: Path,
    discovery_state: Path | None = None,
) -> tuple[dict[str, object], int]:
    findings: dict[str, list[str]] = {}
    workbook_data = validate_workbook(workbook, formal_template, findings)
    requires_discovery_state = bool(workbook_data.get("requires_discovery_state"))
    if requires_discovery_state and not discovery_state:
        add_finding(
            findings,
            "深探状态与交付映射",
            "页面元素覆盖清单声明了页面/浏览器/DOM 实探证据，必须提供 --discovery-state 通过深探准出门禁",
        )
    if discovery_state:
        collect_validation_issue(
            findings,
            "深探状态与交付映射",
            lambda: validate_discovery_state(discovery_state, workbook_data),
        )
    import_count = validate_import_workbook(import_workbook, workbook_data, findings)
    if findings:
        fail(format_findings("测试设计、深探映射和导入文件联合质检未通过：", findings))
    return workbook_data, import_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated test design deliverable workbook.")
    parser.add_argument("--workbook", type=Path)
    parser.add_argument("--import-workbook", type=Path)
    parser.add_argument("--discovery-state", type=Path)
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument(
        "--formal-template",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "docs" / "test-design" / "codebuddy-test-design-template.xlsx",
    )
    args = parser.parse_args()

    if args.discovery_only:
        if not args.discovery_state:
            parser.error("--discovery-only requires --discovery-state")
        validate_discovery_state(args.discovery_state)
        print("OK: 动态深探队列已收口，状态证据准出检查通过。")
        return 0
    if not args.workbook or not args.import_workbook:
        parser.error("正式交付校验必须同时提供 --workbook 和 --import-workbook")
    workbook_data, import_count = validate_delivery_bundle(
        args.workbook,
        args.import_workbook,
        args.formal_template,
        args.discovery_state,
    )
    print(
        "OK: test design deliverable quality checks passed. "
        f"场景={workbook_data['scenario_count']}，功能用例={workbook_data['case_count']}，"
        f"性能设计={workbook_data['performance_count']}，风险={workbook_data['risk_count']}，"
        f"元素覆盖={workbook_data['coverage_count']}，导入用例={import_count}。"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
