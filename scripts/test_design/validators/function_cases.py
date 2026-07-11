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


PLACEHOLDER_CASE_IDS = {"", "TC-A2A-XXX", "TC-XXX", "TODO", "TBD"}


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
    if normalized["测试类型"] == "性能规格测试" or normalized["DFX维度"] == "DFP性能":
        raise ValueError(f"{label} must not put performance scenarios into function case shards")
    validate_case_steps_and_expected(normalized, label)


def validate_function_case_part(path: Path, planned_ids: set[str] | None = None) -> int:
    if not FUNCTION_CASE_PART_RE.match(path.name):
        raise ValueError(f"{path} must use function_cases_part_001.json naming")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or len(cases) > MAX_FUNCTION_CASES_PER_PART:
        raise ValueError(f"{path} must contain at most {MAX_FUNCTION_CASES_PER_PART} cases")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        validate_function_case_schema(case, f"{path.name} case {index}", planned_ids)
        case_id = str(case.get("用例 ID", "")).strip()
        if case_id in seen_ids:
            raise ValueError(f"{path.name} has duplicate 用例 ID: {case_id}")
        seen_ids.add(case_id)
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
