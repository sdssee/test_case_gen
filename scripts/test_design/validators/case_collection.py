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
STATUS_CASE_COUNT_SCENARIOS = {
    "异常用例数": {"异常输入", "错误码", "超时重试", "故障恢复", "错误提示", "逆向操作", "破坏性", "资源耗尽"},
    "边界用例数": {"边界值"},
    "权限/状态用例数": {"身份认证", "权限控制"},
    "数据一致性用例数": {"数据一致", "幂等性", "数据准确"},
}
STATUS_CASE_COUNT_EXPLICIT_MARKERS = {
    "异常用例数": {"异常输入", "非法值", "无效值", "错误提示", "失败路径"},
    "边界用例数": {"边界值", "上限值", "下限值", "最大值", "最小值"},
    "权限/状态用例数": {"权限控制", "无权限", "权限不足", "状态变更", "状态流转", "启用", "停用", "发布", "下线", "屏蔽"},
    "数据一致性用例数": {"数据一致", "数据准确", "幂等性"},
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


def normalize_execution_text(value: object) -> str:
    """Normalize case-instance noise in addition to presentation noise for clone detection."""
    text = unicodedata.normalize("NFKC", _text(value))
    text = re.sub(
        r"(?<![0-9A-Za-z])(?:AI_TEST|CODEX_TEST)(?:"
        r"[-_][0-9A-Za-z_\-\u3400-\u9fff]{1,32}?(?=$|[\s,，。;；:：)）\]}】]|保存|删除|创建|编辑|更新|点击|提交|成功|失败|之后|以后|时)"
        r"|[0-9A-Za-z]+"
        r"|[\u3400-\u9fff]{1,24}[0-9A-Za-z]{1,12}"
        r"|[\u3400-\u9fff]{0,24}[零〇一二三四五六七八九十百千万甲乙丙丁]"
        r")?",
        "<test-data-id>",
        text,
        flags=re.IGNORECASE,
    )
    return normalize_semantic_text(text)


def derived_case_quality_counts(rows: Iterable[Mapping[str, object]]) -> dict[str, int]:
    """Derive overlapping quality-direction counts from an explicit DFX taxonomy."""
    counts = {field: 0 for field in STATUS_CASE_COUNT_SCENARIOS}
    for row in rows:
        scenarios = {
            part.strip()
            for part in re.split(r"[,，;；、/\r\n]+", _text(row.get("DFX场景", "")))
            if part.strip()
        }
        explicit_text = "\n".join(
            _text(row.get(field, ""))
            for field in ["功能点", "用例标题", "测试数据"]
        )
        for field, scenario_values in STATUS_CASE_COUNT_SCENARIOS.items():
            if scenarios & scenario_values or any(
                marker in explicit_text for marker in STATUS_CASE_COUNT_EXPLICIT_MARKERS[field]
            ):
                counts[field] += 1
    return counts


def execution_signature(
    row: Mapping[str, object],
    *,
    steps_field: str = "操作步骤",
    expected_field: str = "预期结果",
) -> tuple[str, str]:
    return (
        normalize_execution_text(row.get(steps_field, "")),
        normalize_execution_text(row.get(expected_field, "")),
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
    """Reject cloned/ambiguous cases and ungrounded title or DFX parameters."""
    signatures: dict[tuple[str, str], tuple[str, str, int]] = {}
    step_signatures: dict[str, tuple[str, str, int]] = {}
    expected_signatures: dict[str, tuple[str, str, int]] = {}
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

        for field_label, value, seen in [
            ("操作步骤", signature[0], step_signatures),
            ("预期结果", signature[1], expected_signatures),
        ]:
            previous_field = seen.get(value)
            if previous_field:
                previous_id, previous_title, previous_index = previous_field
                raise ValueError(
                    f"{label} contains duplicate {field_label} after normalization: "
                    f"row {previous_index} ({previous_id}, {previous_title}) and "
                    f"row {index} ({current_id}, {current_title}); every case must state its own concrete "
                    "action and observable outcome"
                )
            seen[value] = (current_id, current_title, index)

        _validate_deterministic_oracle(row.get(expected_field, ""), row_label)
        _validate_dfx_case_grounding(row, row_label, steps_field=steps_field, expected_field=expected_field)


GENERIC_ORACLE_RE = re.compile(
    r"^(?:(?:页面|列表|数据|字段|功能|操作|结果|系统|格式)(?:展示|运行|处理)?)?"
    r"(?:正常|正确|稳定|无异常|不报错|符合预期)$"
)
AMBIGUOUS_ORACLE_RE = re.compile(
    r"(?:成功|失败|为空|显示原值|报错|提示[^，。；;\n]{0,20})\s*(?:或|或者)"
    r"|(?:或|或者)\s*(?:成功|失败|为空|显示原值|报错|提示)"
    r"|可能|视情况|根据实际(?:情况)?|以实际为准"
)


def _validate_deterministic_oracle(value: object, label: str) -> None:
    text = unicodedata.normalize("NFKC", _text(value))
    if AMBIGUOUS_ORACLE_RE.search(text):
        raise ValueError(
            f"{label} 预期结果 contains an ambiguous pass/fail oracle; state one observed and objectively "
            "verifiable result instead of alternatives"
        )
    for line in text.splitlines():
        body = re.sub(r"^\s*\d+\s*[.、．)]\s*", "", line).strip(" 。；;，,")
        if body and GENERIC_ORACLE_RE.fullmatch(body):
            raise ValueError(
                f"{label} 预期结果 contains generic oracle {body!r}; record concrete page text, data, count, "
                "state, validation position, persistence, or side effect"
            )


def _validate_dfx_case_grounding(
    row: Mapping[str, object],
    label: str,
    *,
    steps_field: str,
    expected_field: str,
) -> None:
    scenario = _text(row.get("DFX场景", ""))
    combined = "\n".join(
        _text(row.get(field, ""))
        for field in ["前置条件", "测试数据", steps_field, expected_field]
    )
    combined_without_numbering = "\n".join(
        re.sub(r"^\s*\d+\s*[.、．)]\s*", "", line)
        for line in combined.splitlines()
    )
    if "边界值" in scenario and not re.search(
        r"\d|最大|最小|上限|下限|临界|超长|空值|首条|末条|首页|末页|字符|字节|长度",
        combined_without_numbering,
    ):
        raise ValueError(f"{label} DFX场景=边界值 must contain a concrete boundary value or boundary action")
    if "权限控制" in scenario:
        if not re.search(r"角色|权限|管理员|普通用户|只读|无权限|授权|受限账号", combined):
            raise ValueError(f"{label} DFX场景=权限控制 must name the role/account permission condition")
        if not re.search(r"不可见|隐藏|禁用|置灰|拒绝|拦截|无权限|403|允许|可操作", combined):
            raise ValueError(f"{label} DFX场景=权限控制 must state the observable permission difference")


def validate_contiguous_function_point_groups(
    rows: Sequence[Mapping[str, object]],
    *,
    label: str,
    field: str = "功能点",
    id_field: str = "用例 ID",
) -> None:
    """Require every exact function point to occupy one contiguous block."""
    closed: dict[str, tuple[str, int]] = {}
    current = ""
    current_display = ""
    for index, row in enumerate(rows, start=1):
        display = _text(row.get(field, ""))
        normalized = normalize_semantic_text(display)
        if not normalized:
            raise ValueError(f"{label} row {index} is missing {field}")
        if normalized == current:
            continue
        if current:
            closed[current] = (current_display, index - 1)
        previous = closed.get(normalized)
        if previous:
            previous_display, previous_end = previous
            case_id = _text(row.get(id_field, "")) or f"row {index}"
            raise ValueError(
                f"{label} function point {previous_display!r} is fragmented: its prior block ended at row "
                f"{previous_end}, but it reappears at row {index} ({case_id}); group every function point contiguously"
            )
        current = normalized
        current_display = display


def validate_case_order_parity(
    source_rows: Sequence[Mapping[str, object]],
    target_rows: Sequence[Mapping[str, object]],
    *,
    source_field_map: Mapping[str, str],
    target_field_map: Mapping[str, str],
    fields: Sequence[str],
    source_label: str,
    target_label: str,
) -> None:
    """Require exact ordered sequence parity, including duplicate multiplicity."""
    source = [tuple(_text(row.get(source_field_map[field], "")) for field in fields) for row in source_rows]
    target = [tuple(_text(row.get(target_field_map[field], "")) for field in fields) for row in target_rows]
    if source == target:
        return
    first_mismatch = next(
        (index for index, pair in enumerate(zip(source, target), start=1) if pair[0] != pair[1]),
        min(len(source), len(target)) + 1,
    )
    raise ValueError(
        f"{target_label} row order/content must exactly match {source_label}; first mismatch at data row "
        f"{first_mismatch}, source_count={len(source)}, target_count={len(target)}"
    )


def validate_plan_case_order_alignment(
    plan_rows: Sequence[Mapping[str, object]],
    case_rows: Sequence[Mapping[str, object]],
    *,
    split_ids: Callable[[str], list[str]],
) -> None:
    """Keep generated case order monotonic by plan owner and preserve per-plan ID order."""
    owner_by_id: dict[str, int] = {}
    grouped_plan_ids: dict[str, list[str]] = {}
    function_point_order: list[str] = []
    for plan_index, plan in enumerate(plan_rows, start=1):
        ids = split_ids(_text(plan.get("实际用例ID", ""))) or split_ids(_text(plan.get("计划用例ID", "")))
        function_point = normalize_semantic_text(plan.get("功能点", ""))
        if function_point not in grouped_plan_ids:
            grouped_plan_ids[function_point] = []
            function_point_order.append(function_point)
        for case_id in ids:
            if case_id in owner_by_id:
                raise ValueError(f"element-case-plan.csv assigns case {case_id} to multiple plan owners")
            owner_by_id[case_id] = plan_index
            grouped_plan_ids[function_point].append(case_id)
    expected_ids = [case_id for point in function_point_order for case_id in grouped_plan_ids[point]]
    actual_ids = [_text(row.get("用例 ID", "")) for row in case_rows]
    if actual_ids != expected_ids:
        first_mismatch = next(
            (index for index, pair in enumerate(zip(actual_ids, expected_ids), start=1) if pair[0] != pair[1]),
            min(len(actual_ids), len(expected_ids)) + 1,
        )
        raise ValueError(
            "function case manifest must preserve element-case-plan owner and ID order; "
            f"first mismatch at case position {first_mismatch}"
        )


def validate_function_point_aware_shards(
    shards: Sequence[Sequence[Mapping[str, object]]],
    *,
    label: str,
    max_per_shard: int,
) -> None:
    """Do not split a function point across files when its whole block fits one shard."""
    flattened = [row for shard in shards for row in shard]
    totals = Counter(normalize_semantic_text(row.get("功能点", "")) for row in flattened)
    for index in range(len(shards) - 1):
        left = shards[index]
        right = shards[index + 1]
        if not left or not right:
            continue
        left_point = normalize_semantic_text(left[-1].get("功能点", ""))
        right_point = normalize_semantic_text(right[0].get("功能点", ""))
        if left_point and left_point == right_point and totals[left_point] <= max_per_shard:
            display = _text(left[-1].get("功能点", ""))
            raise ValueError(
                f"{label} splits function point {display!r} between shard {index + 1} and {index + 2} even "
                f"though all {totals[left_point]} cases fit within one {max_per_shard}-case shard"
            )


def validate_discovery_plan_case_alignment(
    discovery_rows: Sequence[Mapping[str, object]],
    plan_rows: Sequence[Mapping[str, object]],
    case_rows: Sequence[Mapping[str, object]],
    *,
    split_ids: Callable[[str], list[str]],
) -> None:
    """Enforce exact discovery -> plan -> case ownership without inferred pseudo-elements."""
    def key(row: Mapping[str, object]) -> tuple[str, str, str, str, str]:
        return tuple(
            normalize_semantic_text(row.get(field, ""))
            for field in ["最小标题路径", "交互实例ID", "页面/入口", "元素名称/文案", "元素类型"]
        )

    discovery_by_key: dict[tuple[str, str, str, str, str], Mapping[str, object]] = {}
    for index, row in enumerate(discovery_rows, start=2):
        identity = key(row)
        if identity in discovery_by_key:
            raise ValueError(f"page-discovery.csv row {index} duplicates an exact discovery element identity")
        discovery_by_key[identity] = row

    plan_by_key: dict[tuple[str, str, str, str, str], list[Mapping[str, object]]] = {}
    for index, row in enumerate(plan_rows, start=2):
        identity = key(row)
        if identity not in discovery_by_key:
            raise ValueError(
                f"element-case-plan.csv row {index} has no exact page-discovery.csv fact for "
                f"{_text(row.get('页面/入口', ''))}/{_text(row.get('元素名称/文案', ''))}/"
                f"{_text(row.get('元素类型', ''))}"
            )
        if identity in plan_by_key:
            raise ValueError(
                f"element-case-plan.csv row {index} duplicates 交互实例ID={_text(row.get('交互实例ID', ''))!r}; "
                "one interaction instance must have exactly one plan owner"
            )
        plan_by_key.setdefault(identity, []).append(row)

    cases_by_id = {_text(row.get("用例 ID", "")): row for row in case_rows}
    plan_owner_by_case: dict[str, Mapping[str, object]] = {}
    for plan in plan_rows:
        for case_id in split_ids(_text(plan.get("实际用例ID", ""))) or split_ids(_text(plan.get("计划用例ID", ""))):
            previous = plan_owner_by_case.get(case_id)
            if previous is not None and previous is not plan:
                raise ValueError(f"case {case_id} is owned by more than one element-case-plan.csv row")
            plan_owner_by_case[case_id] = plan

    def taxonomy_values(value: object) -> set[str]:
        return {
            normalize_semantic_text(part)
            for part in re.split(r"[,，;；、/\r\n]+", _text(value))
            if normalize_semantic_text(part)
        }

    for identity, discovery in discovery_by_key.items():
        matching_plans = plan_by_key.get(identity, [])
        if not matching_plans:
            raise ValueError(
                "page-discovery.csv contains an interactive element with no exact element-case-plan.csv row: "
                f"{_text(discovery.get('页面/入口', ''))}/{_text(discovery.get('元素名称/文案', ''))}"
            )
        expected: list[str] = []
        allowed_points: set[str] = set()
        for plan in matching_plans:
            expected.extend(split_ids(_text(plan.get("实际用例ID", ""))) or split_ids(_text(plan.get("计划用例ID", ""))))
            allowed_points.add(normalize_semantic_text(plan.get("功能点", "")))
        linked = split_ids(_text(discovery.get("关联用例ID", "")))
        if linked != expected:
            raise ValueError(
                "page-discovery.csv linked case IDs must exactly equal its matching plan owner IDs for "
                f"{_text(discovery.get('页面/入口', ''))}/{_text(discovery.get('元素名称/文案', ''))}; "
                f"expected={expected}, linked={linked}"
            )
        if expected and _text(discovery.get("是否已生成用例", "")) != "是":
            raise ValueError("page-discovery.csv must mark 是否已生成用例=是 after matching planned cases are generated")
        wrong = [
            case_id for case_id in linked
            if case_id not in cases_by_id
            or normalize_semantic_text(cases_by_id[case_id].get("功能点", "")) not in allowed_points
        ]
        if wrong:
            raise ValueError(
                "page-discovery.csv links cases owned by another function point or missing from the manifest: "
                f"{wrong[:10]}"
            )
        step_anchor = normalize_semantic_text(discovery.get("操作步骤锚点", ""))
        result_anchor = normalize_semantic_text(discovery.get("预期结果锚点", ""))
        result_anchor_landed = False
        for case_id in linked:
            case = cases_by_id[case_id]
            if step_anchor and step_anchor not in normalize_semantic_text(case.get("操作步骤", "")):
                raise ValueError(
                    f"generated case {case_id} 操作步骤 does not contain discovery 操作步骤锚点 "
                    f"{_text(discovery.get('操作步骤锚点', ''))!r}"
                )
            if result_anchor and result_anchor in normalize_semantic_text(case.get("预期结果", "")):
                result_anchor_landed = True
            plan = plan_owner_by_case.get(case_id)
            if plan is None:
                continue
            plan_dimensions = taxonomy_values(plan.get("适用DFX维度", ""))
            plan_scenarios = taxonomy_values(plan.get("适用DFX场景", ""))
            case_dimensions = taxonomy_values(case.get("DFX维度", ""))
            case_scenarios = taxonomy_values(case.get("DFX场景", ""))
            if plan_dimensions and (not case_dimensions or not case_dimensions.issubset(plan_dimensions)):
                raise ValueError(f"generated case {case_id} DFX维度 is outside its exact element-case-plan owner")
            if plan_scenarios and (not case_scenarios or not case_scenarios.issubset(plan_scenarios)):
                raise ValueError(f"generated case {case_id} DFX场景 is outside its exact element-case-plan owner")
        if result_anchor and not result_anchor_landed:
            raise ValueError(
                "page-discovery.csv 预期结果锚点 must appear in at least one exact linked case 预期结果 for "
                f"{_text(discovery.get('页面/入口', ''))}/{_text(discovery.get('元素名称/文案', ''))}; "
                "variant abnormal/boundary cases may keep their own observed oracle"
            )


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
