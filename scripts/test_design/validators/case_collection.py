# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence


TRANSFER_FIELDS = ("用例标题", "操作步骤", "预期结果", "前置条件")
TITLE_STATUS_VALUES = {
    "启用", "停用", "开启", "关闭", "已确认", "未确认", "已屏蔽", "未屏蔽", "待处理", "未处理", "处理中", "已处理",
    "在线", "离线", "已发布", "未发布", "已归档", "未归档", "已删除", "未删除",
}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def normalize_semantic_text(value: object) -> str:
    """Normalize formatting noise while preserving business words and numeric values."""
    text = unicodedata.normalize("NFKC", _text(value))
    text = re.sub(
        r"(?<![0-9A-Za-z])TC[-_0-9A-Za-z]*\d+(?![0-9A-Za-z])",
        "<case-id>",
        text,
        flags=re.IGNORECASE,
    )
    normalized_lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"^\s*\d+\s*[.、．)]\s*", "", line.strip())
        line = re.sub(r"[.．]+$", "", line)
        if line:
            normalized_lines.append(line)
    normalized = "".join(normalized_lines).lower()
    # Ignore prose formatting noise but preserve punctuation embedded in business
    # values. Removing every Unicode punctuation character would collapse 1.2
    # into 12, A-B into AB, and similar genuinely different inputs.
    return re.sub(r"[\s，,。；;：:、！!？?]+", "", normalized)


def execution_signature(
    row: Mapping[str, object],
    *,
    steps_field: str = "操作步骤",
    expected_field: str = "预期结果",
) -> tuple[str, str]:
    return (
        normalize_semantic_text(row.get(steps_field, "")),
        normalize_semantic_text(row.get(expected_field, "")),
    )


def page_size_parameters(title: object) -> set[str]:
    """Extract explicit page-size values from a page-size-oriented case title."""
    text = unicodedata.normalize("NFKC", _text(title))
    compact = re.sub(r"\s+", "", text)
    page_size_context = any(
        marker in compact
        for marker in ["每页", "单页", "页容量", "分页条数", "条/页", "条每页"]
    )
    if not page_size_context:
        return set()

    # Page-number targets are a different parameter class. Do not mistake 第2页
    # for a page-size value when the same title also mentions pagination.
    without_page_numbers = re.sub(r"第\d+页", "", compact)
    values: set[str] = set()
    for segment in re.findall(
        r"(?:每页|单页|页容量|分页条数|每页条数)([^条]{0,40})条",
        without_page_numbers,
    ):
        values.update(re.findall(r"(?<!\d)\d{1,6}(?!\d)", segment))
    patterns = [
        r"(?:每页|单页|页容量|分页条数|每页条数)[^0-9]{0,10}(\d{1,6})",
        r"(?<!\d)(\d{1,6})条/页",
        r"(?<!\d)(\d{1,6})条每页",
        r"(?:选择|切换|设置|调整)[^0-9]{0,8}(\d{1,6})条",
    ]
    for pattern in patterns:
        values.update(re.findall(pattern, without_page_numbers))
    return values


def _contains_numeric_token(text: object, value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", _text(text))
    normalized = "\n".join(
        re.sub(r"^\s*\d+\s*[.、．)]\s*", "", line)
        for line in normalized.splitlines()
    )
    return bool(re.search(rf"(?<!\d){re.escape(value)}(?!\d)", normalized))


def page_number_parameters(title: object) -> set[str]:
    """Extract explicit target-page values without treating every page reference as a target."""
    text = unicodedata.normalize("NFKC", _text(title))
    compact = re.sub(r"\s+", "", text)
    if not any(marker in compact for marker in ["页码", "跳页", "跳转", "目标页", "翻页", "第"]):
        return set()
    targets = set(
        re.findall(r"(?:跳转|跳页|目标页(?:码)?|页码)[^0-9]{0,8}第?(\d{1,6})页?", compact)
    )
    targets.update(re.findall(r"(?:到|至)第?(\d{1,6})页", compact))
    explicit_pages = re.findall(r"第(\d{1,6})页", compact)
    if len(explicit_pages) == 1:
        targets.add(explicit_pages[0])
    return targets


def status_parameters(title: object) -> set[str]:
    """Extract status values only when they form an explicit title segment."""
    text = unicodedata.normalize("NFKC", _text(title))
    segments = {
        segment.strip()
        for segment in re.split(r"[-–—_>：:/|｜]+", text)
        if segment.strip()
    }
    return TITLE_STATUS_VALUES & segments


def validate_page_size_grounding(
    row: Mapping[str, object],
    *,
    label: str,
    title_field: str = "用例标题",
    steps_field: str = "操作步骤",
    expected_field: str = "预期结果",
) -> None:
    parameters = sorted(page_size_parameters(row.get(title_field, "")), key=lambda value: int(value))
    if not parameters:
        return
    steps = row.get(steps_field, "")
    expected = row.get(expected_field, "")
    missing_from_steps = [value for value in parameters if not _contains_numeric_token(steps, value)]
    missing_from_expected = [value for value in parameters if not _contains_numeric_token(expected, value)]
    if missing_from_steps or missing_from_expected:
        raise ValueError(
            f"{label} page-size parameters from title must appear in both concrete 操作步骤 and 预期结果; "
            f"missing_from_steps={missing_from_steps}, missing_from_expected={missing_from_expected}"
        )


def validate_page_number_and_status_grounding(
    row: Mapping[str, object],
    *,
    label: str,
    title_field: str = "用例标题",
    steps_field: str = "操作步骤",
    expected_field: str = "预期结果",
) -> None:
    title = row.get(title_field, "")
    steps = row.get(steps_field, "")
    expected = row.get(expected_field, "")
    page_numbers = sorted(page_number_parameters(title), key=lambda value: int(value))
    missing_page_steps = [value for value in page_numbers if not _contains_numeric_token(steps, value)]
    missing_page_expected = [value for value in page_numbers if not _contains_numeric_token(expected, value)]
    if missing_page_steps or missing_page_expected:
        raise ValueError(
            f"{label} target-page parameters from title must appear in both concrete 操作步骤 and 预期结果; "
            f"missing_from_steps={missing_page_steps}, missing_from_expected={missing_page_expected}"
        )
    statuses = sorted(status_parameters(title))
    normalized_steps = unicodedata.normalize("NFKC", _text(steps))
    normalized_expected = unicodedata.normalize("NFKC", _text(expected))
    missing_status_steps = [value for value in statuses if value not in normalized_steps]
    missing_status_expected = [value for value in statuses if value not in normalized_expected]
    if missing_status_steps or missing_status_expected:
        raise ValueError(
            f"{label} status parameters from title must appear in both concrete 操作步骤 and 预期结果; "
            f"missing_from_steps={missing_status_steps}, missing_from_expected={missing_status_expected}"
        )


def validate_case_collection(
    rows: Iterable[Mapping[str, object]],
    *,
    label: str,
    id_field: str = "用例 ID",
    title_field: str = "用例标题",
    steps_field: str = "操作步骤",
    expected_field: str = "预期结果",
) -> None:
    """Reject duplicate executable bodies and ungrounded page-size parameters."""
    signatures: dict[tuple[str, str], tuple[str, str, int]] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} row {index} must be an object")
        row_label = f"{label} row {index}"
        validate_page_size_grounding(
            row,
            label=row_label,
            title_field=title_field,
            steps_field=steps_field,
            expected_field=expected_field,
        )
        validate_page_number_and_status_grounding(
            row,
            label=row_label,
            title_field=title_field,
            steps_field=steps_field,
            expected_field=expected_field,
        )
        signature = execution_signature(row, steps_field=steps_field, expected_field=expected_field)
        if not all(signature):
            continue
        current_id = _text(row.get(id_field, "")) or f"row {index}"
        current_title = _text(row.get(title_field, ""))
        previous = signatures.get(signature)
        if previous:
            previous_id, previous_title, previous_index = previous
            raise ValueError(
                f"{label} contains duplicate 操作步骤+预期结果 after normalization: "
                f"row {previous_index} ({previous_id}, {previous_title}) and "
                f"row {index} ({current_id}, {current_title}); merge the cases or make the concrete action/outcome distinct"
            )
        signatures[signature] = (current_id, current_title, index)


def transfer_counter(
    rows: Iterable[Mapping[str, object]],
    field_map: Mapping[str, str],
) -> Counter[tuple[str, str, str, str]]:
    missing = [field for field in TRANSFER_FIELDS if field not in field_map]
    if missing:
        raise ValueError(f"transfer field map is incomplete: {missing}")
    return Counter(
        tuple(_text(row.get(field_map[field], "")) for field in TRANSFER_FIELDS)
        for row in rows
    )


def validate_case_field_parity(
    source_rows: Sequence[Mapping[str, object]],
    target_rows: Sequence[Mapping[str, object]],
    *,
    fields: Sequence[str],
    source_label: str,
    target_label: str,
    id_field: str = "用例 ID",
) -> None:
    def index_rows(rows: Sequence[Mapping[str, object]], label: str) -> dict[str, Mapping[str, object]]:
        indexed: dict[str, Mapping[str, object]] = {}
        for index, row in enumerate(rows, start=1):
            case_id = _text(row.get(id_field, ""))
            if not case_id:
                raise ValueError(f"{label} row {index} is missing {id_field}")
            if case_id in indexed:
                raise ValueError(f"{label} contains duplicate {id_field}: {case_id}")
            indexed[case_id] = row
        return indexed

    source = index_rows(source_rows, source_label)
    target = index_rows(target_rows, target_label)
    missing = sorted(set(source) - set(target))
    unexpected = sorted(set(target) - set(source))
    if missing or unexpected:
        raise ValueError(
            f"{target_label} case IDs differ from {source_label}; missing={missing[:10]}, unexpected={unexpected[:10]}"
        )

    mismatches: list[str] = []
    for case_id, source_row in source.items():
        target_row = target[case_id]
        changed = [field for field in fields if _text(source_row.get(field, "")) != _text(target_row.get(field, ""))]
        if changed:
            mismatches.append(f"{case_id}: {changed}")
    if mismatches:
        raise ValueError(
            f"{target_label} standard fields differ from {source_label}: {mismatches[:10]}"
        )


def validate_plan_function_point_alignment(
    plan_rows: Sequence[Mapping[str, object]],
    case_rows: Sequence[Mapping[str, object]],
    *,
    split_ids: Callable[[str], list[str]],
) -> None:
    """Prevent plan ID ranges from drifting into the next feature block."""
    cases_by_id = {
        _text(case.get("用例 ID", "")): case
        for case in case_rows
        if _text(case.get("用例 ID", ""))
    }
    for index, plan in enumerate(plan_rows, start=2):
        expected = normalize_semantic_text(plan.get("功能点", ""))
        planned_ids = split_ids(_text(plan.get("计划用例ID", "")))
        missing = [case_id for case_id in planned_ids if case_id not in cases_by_id]
        if missing:
            raise ValueError(
                f"element-case-plan.csv row {index} references missing generated cases: {missing[:10]}"
            )
        mismatched = [
            case_id
            for case_id in planned_ids
            if normalize_semantic_text(cases_by_id[case_id].get("功能点", "")) != expected
        ]
        if mismatched:
            raise ValueError(
                f"element-case-plan.csv row {index} planned cases must preserve 功能点={_text(plan.get('功能点', ''))!r}; "
                f"mismatched case IDs: {mismatched[:10]}"
            )
