# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path

from ..contracts.function_cases import (
    ENGLISH_TEMPLATE_MARKERS,
    FUNCTION_CASE_FORBIDDEN_FIELDS,
    FUNCTION_CASE_PART_RE,
    FUNCTION_CASE_REQUIRED_FIELDS,
    MAX_FUNCTION_CASES_PER_PART,
)
from ..contracts.sheet_data import SHEET_DATA_HEADERS
from .case_collection import validate_case_collection, validate_page_size_grounding


PLACEHOLDER_CASE_IDS = {"", "TC-A2A-XXX", "TC-XXX", "TODO", "TBD"}
MUTATING_TEST_DATA_MARKERS = ["AI_TEST", "CODEX_TEST", "本次创建", "本次新增", "用户提供测试数据", "测试标识"]
SAFE_EXISTING_DATA_MARKERS = ["不保存", "不提交", "不确认", "取消", "关闭", "数据不变", "状态不变"]
MUTATION_COMMIT_MARKERS = [
    "保存成功", "提交成功", "新增成功", "创建成功", "添加成功", "确认删除", "删除成功", "编辑成功", "修改成功",
    "配置成功", "启用成功", "停用成功", "发布成功", "下线成功", "审批成功", "重置成功", "撤销成功", "归档成功", "清空成功", "解绑成功",
    "列表刷新", "落库", "状态变更", "状态流转",
]
TRANSIENT_STEP_MARKERS = [
    "modal", "dialog", "drawer", "dropdown", "select", "confirm", "edit", "delete", "input",
    "弹窗", "对话框", "抽屉", "下拉", "选择", "确认框", "编辑", "删除", "输入", "尝试点击", "观察",
]
TERMINAL_STEP_MARKERS = [
    "click OK", "click Cancel", "close", "return", "back to list", "save", "submit", "not save", "no data changed",
    "点击确定", "点击「确定」", "点击取消", "点击「取消」", "点击关闭", "点击「关闭」", "返回", "回到列表", "返回列表", "保存", "提交", "确认", "不保存", "关闭弹窗", "弹窗关闭", "列表不变", "数据不变", "退出编辑",
]
INTERNAL_EXECUTION_MARKERS = [
    r"\buid\s*=", r"\b(?:element|node|backend|accessibility)[_-]?id\s*=",
    r"<element_uid>", r"\bartifacts[/\\]", r"\borchestration[/\\]",
    r"\bAgentResult\b", r"\bMCP\b", r"\bDevTools\b", r"开发者工具",
    r"可访问性树", r"DOM节点", r"interaction[-_ ]?id",
]
SCREENSHOT_STEP_RE = re.compile(
    r"(?:截图|截屏|屏幕截图|保存截图|拍照).{0,16}(?:证据|留档|记录|附件)?|"
    r"(?:证据|留档).{0,12}(?:截图|截屏)",
    re.IGNORECASE,
)
UNCERTAIN_RESULT_RE = re.compile(
    r"以下任一|任一行为|需(?:实际)?观察(?:后)?确认|待(?:页面)?确认|待验证|"
    r"根据实际(?:情况|结果)|以实际为准|预期观察|若.*则.*否则|N/?A",
    re.IGNORECASE,
)
CASE_REFERENCE_RE = re.compile(r"(?:同|参照|参考)\s*TC[-_A-Za-z0-9]+", re.IGNORECASE)


def contains_any(text: str, markers: list[str]) -> bool:
    return any(marker in (text or "") for marker in markers)


def numbered_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def validate_numbered_sequence(text: str, label: str, minimum: int) -> None:
    lines = numbered_lines(text)
    if len(lines) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} numbered lines")
    for expected, line in enumerate(lines, start=1):
        match = re.match(r"^(\d+)\.\s*\S+", line)
        if not match or int(match.group(1)) != expected:
            raise ValueError(f"{label} numbering must be continuous from 1: {line}")


def validate_case_steps_and_expected(case: dict[str, str], label: str) -> None:
    steps = str(case.get("操作步骤", "") or "")
    expected = str(case.get("预期结果", "") or "")
    precondition = str(case.get("前置条件", "") or "")
    combined = "\n".join([precondition, steps, expected, str(case.get("备注", "") or "")])
    if any(marker in combined for marker in ENGLISH_TEMPLATE_MARKERS):
        raise ValueError(f"{label} contains English placeholder/template text")
    human_fields = "\n".join(
        str(case.get(field, "") or "")
        for field in ["用例标题", "前置条件", "测试数据", "操作步骤", "预期结果"]
    )
    internal = next(
        (pattern for pattern in INTERNAL_EXECUTION_MARKERS if re.search(pattern, human_fields, re.IGNORECASE)),
        None,
    )
    if internal:
        raise ValueError(
            f"{label} contains internal probe/orchestration identifier {internal!r}; "
            "formal cases must use visible page text and human-executable actions"
        )
    if SCREENSHOT_STEP_RE.search(steps):
        raise ValueError(
            f"{label} 操作步骤 must not require screenshots as a test action; evidence collection belongs to discovery"
        )
    if UNCERTAIN_RESULT_RE.search(expected):
        raise ValueError(
            f"{label} 预期结果 is not deterministic; return to discovery/risk instead of leaving an observation placeholder"
        )
    if CASE_REFERENCE_RE.search(human_fields):
        raise ValueError(
            f"{label} must be independently executable and cannot replace steps/results with another TC reference"
        )
    validate_numbered_sequence(precondition, f"{label} 前置条件", 2)
    validate_numbered_sequence(steps, f"{label} 操作步骤", 4)
    validate_numbered_sequence(expected, f"{label} 预期结果", 3)
    strip_number = lambda line: re.sub(r"^\d+\.\s*", "", line).strip()
    if [strip_number(line) for line in numbered_lines(steps)[:3]] == [strip_number(line) for line in numbered_lines(expected)[:3]]:
        raise ValueError(f"{label} 预期结果 repeats navigation/actions from 操作步骤")
    first_steps = "\n".join(numbered_lines(steps)[:3])
    if not contains_any(first_steps, ["登录", "打开系统", "访问系统", "进入系统", "打开平台", "访问平台", "进入平台", "<product_login_url>"]):
        raise ValueError(f"{label} 操作步骤 must start from system/project entry")
    if not contains_any(first_steps, ["一级", "二级", "三级", "菜单", "模块", "导航", "路径", ">", "页面"]):
        raise ValueError(f"{label} 操作步骤 must include complete menu/module navigation")
    if re.search(r"\b点(搜索|保存|删除|确定|确认|取消)\b", steps) or contains_any(steps, ["操作元素", "扩展场景", "基本验证", "Extended"]):
        raise ValueError(f"{label} 操作步骤 contains generic or overly terse wording")
    mutation_markers = [
        "点击「确定」", "点击“确定”", "点击「确认」", "点击“确认”", "保存", "提交", "新增", "创建",
        "编辑", "修改", "删除", "移除", "清空", "解绑", "配置", "启用", "停用", "发布", "下线", "审批", "重置", "撤销", "归档", "批量确认", "屏蔽成功",
    ]
    mutation_success = [
        "成功", "已确认", "已屏蔽", "已删除", "已移除", "保存后", "提交后", "状态更新", "数据更新", "生效",
    ]
    if contains_any(steps, mutation_markers) and contains_any(expected, mutation_success):
        context = "\n".join([precondition, str(case.get("测试数据", "") or ""), steps])
        if not contains_any(context, ["AI_TEST", "CODEX_TEST", "用户提供测试数据"]):
            raise ValueError(f"{label} changes data/state without tagged test data")
    validate_mutation_case_evidence(case, label)
    validate_pagination_jump_has_data(case, label)
    validate_transient_flow_closed(steps, expected, label)


def validate_transient_flow_closed(steps: str, expected: str, label: str) -> None:
    normalized_steps = re.sub(r"\s+", "", steps or "").lower()
    combined = re.sub(r"\s+", "", f"{steps}\n{expected}").lower()
    if normalized_steps and any(marker.lower() in normalized_steps for marker in TRANSIENT_STEP_MARKERS):
        if not any(marker.lower() in combined for marker in TERMINAL_STEP_MARKERS):
            raise ValueError(
                f"{label} opens or changes a transient UI state but does not describe a "
                "confirm/cancel/close/return/recovery path"
            )


def validate_pagination_jump_has_data(row: dict[str, str], label: str) -> None:
    combined = "\n".join([str(row.get("测试数据", "") or ""), str(row.get("操作步骤", "") or ""), str(row.get("预期结果", "") or "")])
    if not contains_any(combined, ["第2页", "第 2 页", "输入2", "输入 2", "跳至页码", "页码输入"]):
        return
    if not contains_any(combined, ["超过一页", "多页", "大于1页", "大于 1 页", "超过10条", "超过 10 条", "造数", "准备超过"]):
        raise ValueError(f"{label} jumps to page 2 but does not declare multi-page test data preparation")


def validate_mutation_case_evidence(row: dict[str, str], label: str) -> None:
    combined = "\n".join(
        str(row.get(field, "") or "")
        for field in ["功能点", "用例标题", "测试数据", "操作步骤", "预期结果", "备注"]
    )
    mutation_markers = ["新增", "创建", "添加", "新建", "保存", "提交", "编辑", "修改", "删除", "移除", "清空", "解绑", "配置", "启用", "停用", "发布", "下线", "审批", "重置", "撤销", "归档", "状态变更"]
    if not contains_any(combined, mutation_markers):
        return
    commits_change = contains_any(combined, MUTATION_COMMIT_MARKERS)
    if not commits_change and contains_any(combined, SAFE_EXISTING_DATA_MARKERS):
        return
    if contains_any(combined, ["已有数据", "既有数据"]):
        if not contains_any(combined, SAFE_EXISTING_DATA_MARKERS):
            raise ValueError(f"{label} touches existing data but does not close with cancel/close/no-save/no-change")
        if contains_any(combined, ["确认删除", "保存修改", "提交修改", "最终确认"]):
            raise ValueError(f"{label} must not finally modify or delete existing data")
        return
    if commits_change and not contains_any(combined, MUTATING_TEST_DATA_MARKERS):
        raise ValueError(f"{label} mutating case must bind to AI_TEST/CODEX_TEST or user-provided test data")
    if contains_any(combined, ["新增", "创建", "添加", "新建", "保存", "提交"]):
        if not contains_any(combined, ["列表", "详情", "下一级", "刷新", "成功", "失败", "校验"]):
            raise ValueError(f"{label} create/save flow must verify list/detail/next-page/success/failure state")
    if contains_any(combined, ["编辑", "修改"]):
        if not contains_any(combined, ["编辑前", "编辑后", "变更", "回显", "列表", "详情", "数据不变"]):
            raise ValueError(f"{label} edit flow must verify before/after value, echo, list/detail, or unchanged state")
    if "删除" in combined and not contains_any(combined, ["删除取消", "取消删除", "确认删除", "列表不再展示", "搜索不到", "数据不变"]):
        raise ValueError(f"{label} delete flow must include cancel/confirm and post-delete or unchanged verification")


def validate_function_case_schema(case: dict[str, object], label: str, planned_ids: set[str] | None = None) -> None:
    if not isinstance(case, dict):
        raise ValueError(f"{label} must be a JSON object")
    keys = set(case)
    forbidden = sorted(keys & FUNCTION_CASE_FORBIDDEN_FIELDS)
    if forbidden:
        raise ValueError(f"{label} contains forbidden/deprecated fields: {forbidden}")
    missing = [field for field in FUNCTION_CASE_REQUIRED_FIELDS if field not in case]
    extra = sorted(keys - set(FUNCTION_CASE_REQUIRED_FIELDS))
    if missing or extra:
        raise ValueError(f"{label} schema mismatch; missing={missing}, extra={extra}")
    normalized = {field: "" if case.get(field) is None else str(case.get(field)).strip() for field in FUNCTION_CASE_REQUIRED_FIELDS}
    case_id = normalized["用例 ID"]
    if case_id in PLACEHOLDER_CASE_IDS or "XXX" in case_id:
        raise ValueError(f"{label} must use a concrete 用例 ID")
    if planned_ids and case_id not in planned_ids:
        raise ValueError(f"{label} 用例 ID is not declared in element-case-plan.csv: {case_id}")
    function_point, title = normalized["功能点"], normalized["用例标题"]
    if not function_point or not title.startswith(f"{function_point}-"):
        raise ValueError(f"{label} 用例标题 must use 功能点-当前用例标题 format")
    for field in ["模块", "优先级", "测试类型", "DFX维度", "DFX场景", "测试数据"]:
        if not normalized[field]:
            raise ValueError(f"{label} must fill {field}")
    if normalized["执行状态"] != "未执行":
        raise ValueError(f"{label} is a design case and must use 执行状态=未执行")
    if normalized["实际结果"] not in {"", "未执行"}:
        raise ValueError(
            f"{label} must not fabricate or carry an execution result; 实际结果 must be blank or 未执行"
        )
    if normalized["测试类型"] == "性能规格测试" or normalized["DFX维度"] == "DFP性能":
        raise ValueError(f"{label} must not put performance scenarios into function case shards")
    validate_case_steps_and_expected(normalized, label)
    validate_page_size_grounding(normalized, label=label)


def _case_rows(payload: object, label: str) -> list[dict[str, object]]:
    rows = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"{label} must contain a list or an object with a cases list")
    return rows


def validate_function_case_part(path: Path, planned_ids: set[str] | None = None) -> int:
    if not FUNCTION_CASE_PART_RE.match(path.name):
        raise ValueError(f"{path} must use function_cases_part_001.json naming")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = _case_rows(data, str(path))
    if not isinstance(cases, list) or not (1 <= len(cases) <= MAX_FUNCTION_CASES_PER_PART):
        raise ValueError(f"{path} must contain 1..{MAX_FUNCTION_CASES_PER_PART} cases")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        validate_function_case_schema(case, f"{path.name} case {index}", planned_ids)
        case_id = str(case.get("用例 ID", "")).strip()
        if case_id in seen_ids:
            raise ValueError(f"{path.name} has duplicate 用例 ID: {case_id}")
        seen_ids.add(case_id)
    validate_case_collection(cases, label=path.name)
    return len(cases)


def validate_sheet_data_file(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} must contain valid JSON: {exc}") from exc
    rows = payload.get("rows") if isinstance(payload, dict) and "rows" in payload else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path} must contain a non-empty row list")
    if any(not isinstance(row, dict) or not row for row in rows):
        raise ValueError(f"{path} rows must be non-empty objects keyed by target Sheet headers")
    expected_headers = SHEET_DATA_HEADERS.get(path.name)
    if expected_headers is None:
        raise ValueError(f"No target Sheet header contract is registered for {path.name}")
    expected = set(expected_headers)
    for index, row in enumerate(rows, start=1):
        missing = sorted(expected - set(row))
        extra = sorted(set(row) - expected)
        if missing or extra:
            raise ValueError(
                f"{path.name} row {index} must use the exact target Sheet headers; missing={missing}, extra={extra}"
            )
        if not any(str(value or "").strip() for value in row.values()):
            raise ValueError(f"{path.name} row {index} must contain at least one non-empty target Sheet value")
    return len(rows)
