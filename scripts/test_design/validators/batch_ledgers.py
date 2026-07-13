# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from collections.abc import Callable


OPERATION_CATEGORIES = {
    "查看", "创建", "编辑", "删除", "配置", "状态变更", "搜索", "筛选", "分页",
    "导入", "导出", "上传", "下载", "其他",
}
MUTATION_CATEGORIES = {"创建", "编辑", "删除", "配置", "状态变更"}
DATA_STRATEGIES = {"本次创建测试数据", "用户提供测试数据", "既有数据只读", "无数据变更"}
EXECUTION_STATES = {"待执行", "已完成", "阻塞", "不适用"}
VERIFICATION_REQUIREMENTS = {
    "回显", "持久化", "实际生效", "结果分支", "权限", "数据一致性", "确认取消", "错误恢复",
}
SELECTION_MARKERS = {
    "下拉", "单选", "复选", "多选", "级联", "树选择", "选择树", "选择器", "选择框", "枚举",
    "select", "combobox", "radio", "checkbox",
}
STRONG_SELECTION_MARKERS = SELECTION_MARKERS - {"选择器", "选择框"}
SELECTION_SET_TYPES = {"有限", "动态"}
CONCRETE_OPTION_BLOCKERS = {
    "禁用", "置灰", "不可选", "不可用", "无法选择", "无权限", "权限不足", "未开通", "前置条件不满足",
    "缺少依赖", "disabled", "readonly", "read-only",
}
UNCERTAIN_OPTION_BLOCKERS = {
    "未知", "待确认", "不确定", "可能", "未尝试", "未点击", "未操作", "unknown", "tbd",
}
PAGE_OBSERVABLE_MARKERS = {
    "点击后", "选择后", "页面显示", "页面变化", "列表变化", "下拉", "选项", "分页",
    "按钮", "弹窗", "提示", "字段", "禁用", "启用", "展开", "收起", "刷新",
}
EXTERNAL_DEPENDENCY_MARKERS = {
    "接口", "日志", "数据库", "异步", "消息", "任务", "权限", "账号", "验证码",
    "环境", "生产", "监控", "后端", "服务端", "第三方", "审计", "通知", "SLA",
    "需求", "文档", "规格", "业务规则", "产品定义", "验收标准", "原型标注", "接口契约",
}
REQUIRED_VERIFICATION = {
    "创建": {"回显", "持久化"},
    "编辑": {"回显", "持久化", "实际生效"},
    "配置": {"回显", "持久化", "实际生效"},
    "状态变更": {"回显", "持久化", "实际生效"},
    "删除": {"持久化", "确认取消"},
}
INCOMPLETE_DISCOVERY_PATTERNS = [
    re.compile(r"未(?:实际)?(?:执行|点击|操作|尝试)"),
    re.compile(r"(?:只|仅)(?:展开|查看)(?:了|到)?(?:选项|控件|页面)?[，,；;：:\s]*(?:未|没有)(?:逐项)?(?:选择|点击|操作|验证)"),
    re.compile(r"(?:数据不足|缺少测试数据).{0,16}(?:无法|未能|不能).{0,12}(?:验证|执行|翻页|跳转)"),
    re.compile(r"(?:仅|只有)\s*1\s*条.{0,24}(?:无法|未能|不能).{0,12}验证"),
    re.compile(r"待验证|待补充|尚未验证|未观察到|无法充分验证"),
    re.compile(r"权限未知|待确认权限"),
    re.compile(r"\b(?:tbd|todo|not executed)\b", re.IGNORECASE),
]
COMPLETED_DISCOVERY_STATUSES = {"已覆盖"}
IMAGE_EVIDENCE_RE = re.compile(r"\.(?:png|jpe?g|gif|bmp|webp)(?:$|[?#])", re.IGNORECASE)


def _is_static_observation_row(row: dict[str, str]) -> bool:
    element_type = row.get("元素类型", "")
    interaction = row.get("交互方式", "")
    combined = f"{element_type}\n{interaction}"
    interactive_markers = {"按钮", "链接", "输入", "选择", "下拉", "单选", "复选", "开关", "分页", "上传", "编辑", "点击"}
    static_markers = {"表格列", "文本", "标签", "只读字段", "展示字段", "提示", "图标说明"}
    return _contains(element_type, static_markers) and not _contains(combined, interactive_markers)


def validate_single_batch_scope(
    batch_rows: list[dict[str, str]],
    ledgers: dict[str, list[dict[str, str]]],
) -> tuple[str, str]:
    if len(batch_rows) != 1:
        raise ValueError(
            "Each batch run directory must contain exactly one batch-status.csv row; "
            "use one independent run directory per leaf-title batch"
        )
    batch_id = batch_rows[0].get("批次ID", "").strip()
    leaf_path = batch_rows[0].get("最小标题路径", "").strip()
    if not batch_id or not leaf_path:
        raise ValueError("batch-status.csv must identify one 批次ID and one 最小标题路径")
    for filename, rows in ledgers.items():
        for index, row in enumerate(rows, start=2):
            row_batch_id = row.get("批次ID", "").strip()
            if row_batch_id != batch_id:
                raise ValueError(
                    f"{filename} row {index} 批次ID={row_batch_id!r} does not match the run batch {batch_id!r}; "
                    "do not mix multiple leaf batches in one run directory"
                )
            if "最小标题路径" in row:
                row_leaf_path = row.get("最小标题路径", "").strip()
                if row_leaf_path != leaf_path:
                    raise ValueError(
                        f"{filename} row {index} 最小标题路径={row_leaf_path!r} does not match the run leaf {leaf_path!r}; "
                        "use one independent run directory per leaf-title batch"
                    )
    return batch_id, leaf_path


def _contains(text: str, markers: set[str]) -> bool:
    return any(marker in text for marker in markers)


def _is_yes(value: str) -> bool:
    return (value or "").strip() in {"是", "Y", "Yes", "YES", "yes", "true", "True", "1"}


def _normalized(value: str) -> str:
    return "".join((value or "").split()).lower()


def _selection_key(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        row.get("最小标题路径", "").strip(),
        row.get("交互实例ID", "").strip(),
        row.get("页面/入口", "").strip(),
        row.get("元素名称/文案", "").strip(),
        row.get("元素类型", "").strip(),
    )


def is_selection_control(row: dict[str, str]) -> bool:
    type_and_interaction = "\n".join([row.get("元素类型", ""), row.get("交互方式", "")]).lower()
    combined = "\n".join(
        [row.get("元素名称/文案", ""), row.get("元素类型", ""), row.get("交互方式", "")]
    ).lower()
    if _contains(type_and_interaction, {"日期", "时间"}) and not _contains(
        type_and_interaction, STRONG_SELECTION_MARKERS
    ):
        return False
    return _contains(combined, SELECTION_MARKERS)


def _split_option_tokens(value: str) -> set[str]:
    # Keep slashes inside UI labels such as “10条/页”; only split explicit list separators.
    return {
        _normalized(token)
        for token in re.split(r"[;；,，、\r\n]+", value or "")
        if _normalized(token)
    }


def validate_selection_option_rows(
    discovery_rows: list[dict[str, str]],
    option_rows: list[dict[str, str]],
    evidence_exists: Callable[[str], bool],
    evidence_fingerprint: Callable[[str], str | None] | None = None,
) -> dict[tuple[str, str, str, str, str], int]:
    selection_discovery: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
    for discovery in discovery_rows:
        if not is_selection_control(discovery):
            continue
        key = _selection_key(discovery)
        if key in selection_discovery:
            raise ValueError(
                f"page-discovery.csv has duplicate selection-control identity {key[2]}/{key[3]}/{key[4]} ({key[1]}); "
                "use distinct element identities for different roles or data states"
            )
        selection_discovery[key] = discovery
    grouped: dict[tuple[str, str, str, str, str], list[tuple[int, dict[str, str]]]] = {}
    global_evidence_locations: dict[tuple[str, str], str] = {}
    global_image_digests: dict[str, str] = {}
    for index, row in enumerate(option_rows, start=2):
        key = _selection_key(row)
        label = f"selection-option-observations.csv row {index} ({key[2]}/{key[3]}/{row.get('选项值', '')})"
        required = [
            "最小标题路径", "交互实例ID", "页面/入口", "元素名称/文案", "元素类型", "选项值", "选项序号",
            "可用选项总数", "选项集合类型", "是否实际选择", "选择前状态", "选择后页面变化",
            "联动/依赖变化", "结果分支/后续状态", "预期结果锚点", "恢复/清空结果", "覆盖策略", "证据路径", "证据定位",
        ]
        missing = [field for field in required if not row.get(field, "").strip()]
        if missing:
            raise ValueError(f"{label} is missing per-option discovery fields: {missing}")
        if key not in selection_discovery:
            raise ValueError(f"{label} has no exactly matching selection control in page-discovery.csv")
        set_type = row.get("选项集合类型", "").strip()
        if set_type not in SELECTION_SET_TYPES:
            raise ValueError(f"{label} 选项集合类型 must be one of {sorted(SELECTION_SET_TYPES)}")
        anchor = _normalized(row.get("预期结果锚点", ""))
        option_value = _normalized(row.get("选项值", ""))
        observed_effect = _normalized("\n".join([
            row.get("选择后页面变化", ""), row.get("联动/依赖变化", ""), row.get("结果分支/后续状态", ""),
        ]))
        generic_anchors = {"成功", "正常", "正确", "功能正常", "页面正常", "数据正确", "无异常", "无变化"}
        if (
            len(anchor) < 4
            or anchor == option_value
            or anchor in generic_anchors
            or anchor not in observed_effect
        ):
            raise ValueError(
                f"{label} 预期结果锚点 must be a non-trivial phrase copied from the actually observed page "
                "change/linkage/result; it cannot equal only the option value or a generic success word"
            )
        try:
            sequence = int(row.get("选项序号", ""))
        except ValueError as exc:
            raise ValueError(f"{label} 选项序号 must be a positive integer") from exc
        if sequence <= 0:
            raise ValueError(f"{label} 选项序号 must be a positive integer")
        actually_selected = _is_yes(row.get("是否实际选择", ""))
        if not actually_selected:
            blocker = row.get("阻塞原因", "").strip()
            blocker_context = "\n".join(
                [blocker, row.get("选择后页面变化", ""), row.get("结果分支/后续状态", "")]
            ).lower()
            if (
                not blocker
                or not _contains(blocker_context, CONCRETE_OPTION_BLOCKERS)
                or _contains(blocker_context, UNCERTAIN_OPTION_BLOCKERS)
            ):
                raise ValueError(
                    f"{label} must be actually selected; only a concretely observed disabled/unselectable option "
                    "may use 是否实际选择=否, with a specific 阻塞原因 and evidence"
                )
        evidence = row.get("证据路径", "").strip()
        if not evidence_exists(evidence):
            raise ValueError(f"{label} must reference existing per-option evidence")
        locator = row.get("证据定位", "").strip()
        evidence_key = (_normalized(evidence), _normalized(locator))
        previous_owner = global_evidence_locations.get(evidence_key)
        if previous_owner:
            raise ValueError(
                f"{label} reuses the same evidence path+locator as {previous_owner}; evidence uniqueness is global"
            )
        global_evidence_locations[evidence_key] = label
        if evidence_fingerprint:
            digest = evidence_fingerprint(evidence)
            if digest and (digest.startswith("image:") or IMAGE_EVIDENCE_RE.search(evidence)):
                previous_digest_owner = global_image_digests.get(digest)
                if previous_digest_owner:
                    raise ValueError(
                        f"{label} reuses image content already used by {previous_digest_owner}; renaming or copying "
                        "the same screenshot does not create independent option evidence"
                    )
                global_image_digests[digest] = label
        coverage = row.get("覆盖策略", "").strip()
        if set_type == "有限":
            try:
                total = int(row.get("可用选项总数", ""))
            except ValueError as exc:
                raise ValueError(f"{label} finite selection 可用选项总数 must be a positive integer") from exc
            if total <= 0:
                raise ValueError(f"{label} finite selection 可用选项总数 must be a positive integer")
            if not _contains(coverage, {"逐项", "全量"}):
                raise ValueError(f"{label} finite selection 覆盖策略 must state 逐项/全量 selection")
        else:
            if row.get("可用选项总数", "").strip() != "动态":
                raise ValueError(f"{label} dynamic selection must use 可用选项总数=动态")
            discovery_context = "\n".join(value or "" for value in selection_discovery[key].values())
            if _contains(f"{key[3]}\n{discovery_context}", {"每页条数", "条/页", "页容量"}) or not _contains(
                discovery_context,
                {"远程", "滚动加载", "懒加载", "分页加载", "异步加载", "服务端搜索", "按需加载"},
            ):
                raise ValueError(
                    f"{label} may use 选项集合类型=动态 only when page-discovery.csv records an actual "
                    "remote/search/lazy-loading option source; finite page-size or visibly enumerated lists must use 有限"
                )
            dynamic_markers = {"搜索", "滚动", "分页", "首项", "中间项", "末项", "边界", "无结果", "清空", "筛选", "抽样"}
            if sum(marker in coverage for marker in dynamic_markers) < 2:
                raise ValueError(
                    f"{label} dynamic selection 覆盖策略 must explicitly cover at least two search/scroll/boundary/clear branches"
                )
        grouped.setdefault(key, []).append((index, row))

    missing_groups = [key for key in selection_discovery if key not in grouped]
    if missing_groups:
        preview = ", ".join(f"{key[2]}/{key[3]}({key[1]})" for key in missing_groups[:10])
        raise ValueError(
            "selection-option-observations.csv must record every selection control and every finite option; missing: "
            + preview
        )

    counts: dict[tuple[str, str, str, str, str], int] = {}
    for key, entries in grouped.items():
        values = [row.get("选项值", "").strip() for _, row in entries]
        normalized_values = [_normalized(value) for value in values]
        sequences = [int(row.get("选项序号", "")) for _, row in entries]
        evidence_locations = [
            (_normalized(row.get("证据路径", "")), _normalized(row.get("证据定位", "")))
            for _, row in entries
        ]
        if len(normalized_values) != len(set(normalized_values)):
            raise ValueError(f"selection-option-observations.csv has duplicate option values for {key[2]}/{key[3]}")
        if len(sequences) != len(set(sequences)):
            raise ValueError(f"selection-option-observations.csv has duplicate option sequences for {key[2]}/{key[3]}")
        if len(evidence_locations) != len(set(evidence_locations)):
            raise ValueError(
                f"selection-option-observations.csv must use a unique (证据路径, 证据定位) pair for each option of {key[2]}/{key[3]}"
            )
        set_types = {row.get("选项集合类型", "").strip() for _, row in entries}
        if len(set_types) != 1:
            raise ValueError(f"selection-option-observations.csv mixes option set types for {key[2]}/{key[3]}")
        summary_tokens = _split_option_tokens(selection_discovery[key].get("选项取值/输入值", ""))
        observed_tokens = set(normalized_values)
        absent = [value for value in values if _normalized(value) not in summary_tokens]
        if absent:
            raise ValueError(
                f"page-discovery.csv selection summary for {key[2]}/{key[3]} does not list observed option value(s): {absent}"
            )
        if set_types == {"有限"}:
            totals = {int(row.get("可用选项总数", "")) for _, row in entries}
            if len(totals) != 1:
                raise ValueError(f"selection-option-observations.csv finite option totals disagree for {key[2]}/{key[3]}")
            total = next(iter(totals))
            if len(entries) != total:
                raise ValueError(
                    f"selection-option-observations.csv finite selection {key[2]}/{key[3]} records {len(entries)} option(s), expected {total}"
                )
            if sorted(sequences) != list(range(1, total + 1)):
                raise ValueError(
                    f"selection-option-observations.csv finite selection {key[2]}/{key[3]} must use contiguous sequences 1..{total}"
                )
            unobserved = sorted(summary_tokens - observed_tokens)
            if unobserved or len(summary_tokens) != total:
                raise ValueError(
                    f"selection-option-observations.csv finite selection {key[2]}/{key[3]} must exactly cover every "
                    f"option listed by page-discovery.csv; total={total}, summary_count={len(summary_tokens)}, "
                    f"unobserved={unobserved}"
                )
        else:
            if sorted(sequences) != list(range(1, len(entries) + 1)):
                raise ValueError(
                    f"selection-option-observations.csv dynamic selection {key[2]}/{key[3]} must use contiguous observed sequences"
                )
        counts[key] = len(entries)
    return counts


def validate_selection_plan_links(
    option_rows: list[dict[str, str]],
    plan_rows: list[dict[str, str]],
    split_ids: Callable[[str], list[str]],
) -> None:
    """Bind every observed option to a distinct case owned by the exact control plan."""
    plans_by_key: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = {}
    for plan in plan_rows:
        plans_by_key.setdefault(_selection_key(plan), []).append(plan)

    options_by_key: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = {}
    for option in option_rows:
        options_by_key.setdefault(_selection_key(option), []).append(option)

    for key, options in options_by_key.items():
        matching_plans = plans_by_key.get(key, [])
        label = f"{key[2]}/{key[3]}({key[1]})"
        if not matching_plans:
            raise ValueError(
                f"selection-option-observations.csv {label} has no exactly matching element-case-plan.csv row"
            )
        planned_ids: set[str] = set()
        declared_total = 0
        for plan in matching_plans:
            planned_ids.update(split_ids(plan.get("计划用例ID", "")))
            try:
                declared_total += int(plan.get("应生成用例数", ""))
            except ValueError as exc:
                raise ValueError(f"element-case-plan.csv {label} 应生成用例数 must be an integer") from exc
        if declared_total < len(options):
            raise ValueError(
                f"element-case-plan.csv {label} declares {declared_total} case(s), but "
                f"{len(options)} observed option(s) each require a grounded case"
            )

        used_ids: dict[str, str] = {}
        for option in options:
            option_value = option.get("选项值", "").strip()
            linked_ids = split_ids(option.get("关联用例ID", ""))
            if not linked_ids:
                raise ValueError(
                    f"selection-option-observations.csv {label}/{option_value} must link at least one planned case ID"
                )
            unknown = sorted(set(linked_ids) - planned_ids)
            if unknown:
                raise ValueError(
                    f"selection-option-observations.csv {label}/{option_value} links case IDs outside the exact control plan: {unknown}"
                )
            for case_id in linked_ids:
                previous = used_ids.get(case_id)
                if previous is not None and previous != option_value:
                    raise ValueError(
                        f"selection-option-observations.csv {label} reuses {case_id} for options "
                        f"{previous!r} and {option_value!r}; each observed option needs a distinct grounded case"
                    )
                used_ids[case_id] = option_value


def validate_selection_case_grounding(
    option_rows: list[dict[str, str]],
    case_rows: list[dict[str, object]],
    split_ids: Callable[[str], list[str]],
) -> None:
    cases_by_id = {
        str(case.get("用例 ID", "")).strip(): case
        for case in case_rows
        if str(case.get("用例 ID", "")).strip()
    }
    values_by_key: dict[tuple[str, str, str, str, str], set[str]] = {}
    for option in option_rows:
        values_by_key.setdefault(_selection_key(option), set()).add(_normalized(option.get("选项值", "")))

    def contains_exact_option(text: str, target: str, siblings: set[str]) -> bool:
        remaining = text
        for sibling in sorted(siblings, key=len, reverse=True):
            if sibling != target and target in sibling:
                remaining = remaining.replace(sibling, "")
        return target in remaining

    for option in option_rows:
        page = option.get("页面/入口", "").strip()
        element = option.get("元素名称/文案", "").strip()
        option_value = option.get("选项值", "").strip()
        normalized_option = _normalized(option_value)
        expected_anchor = _normalized(option.get("预期结果锚点", ""))
        sibling_values = values_by_key[_selection_key(option)]
        for case_id in split_ids(option.get("关联用例ID", "")):
            case = cases_by_id.get(case_id)
            if case is None:
                raise ValueError(
                    f"selection-option-observations.csv {page}/{element}/{option_value} references missing generated case {case_id}"
                )
            steps = _normalized(str(case.get("操作步骤", "")))
            expected = _normalized(str(case.get("预期结果", "")))
            if not contains_exact_option(steps, normalized_option, sibling_values) or not contains_exact_option(
                expected, normalized_option, sibling_values
            ):
                raise ValueError(
                    f"generated case {case_id} for {page}/{element}/{option_value} must state the exact option value "
                    "in both 操作步骤 and 预期结果"
                )
            if not expected_anchor or expected_anchor not in expected:
                raise ValueError(
                    f"selection-option-observations.csv {page}/{element}/{option_value} 预期结果锚点 must "
                    f"appear in the exact linked case {case_id} 预期结果 so observed page effects are not replaced "
                    "by generic or unrelated outcomes"
                )


def validate_page_element_inventory(
    inventory_rows: list[dict[str, str]],
    discovery_rows: list[dict[str, str]],
    evidence_exists: Callable[[str], bool],
) -> None:
    """Use an independently captured page inventory to detect discovery omissions."""
    if not inventory_rows:
        raise ValueError(
            "page-element-inventory.csv must contain independently captured DOM/accessibility/trace elements before discovery can pass"
        )

    def identity(row: dict[str, str]) -> tuple[str, ...]:
        return tuple(
            _normalized(row.get(field, ""))
            for field in [
                "最小标题路径", "页面/入口", "角色/权限", "数据状态", "交互实例ID",
                "元素名称/文案", "元素类型", "交互方式",
            ]
        )

    inventory_identities: dict[tuple[str, ...], str] = {}
    fingerprints: dict[str, str] = {}
    for index, row in enumerate(inventory_rows, start=2):
        label = f"page-element-inventory.csv row {index} ({row.get('页面/入口', '')}/{row.get('元素名称/文案', '')})"
        required = [
            "最小标题路径", "页面/入口", "角色/权限", "数据状态", "交互实例ID", "采集快照ID", "元素指纹", "元素名称/文案", "元素类型",
            "交互方式", "可交互状态", "DOM/可访问性定位", "发现来源", "证据路径", "证据定位",
        ]
        missing = [field for field in required if not row.get(field, "").strip()]
        if missing:
            raise ValueError(f"{label} is missing inventory fields: {missing}")
        source = row.get("发现来源", "")
        if not _contains(source.lower(), {"dom", "可访问性", "accessibility", "trace", "控件树", "浏览器", "computer use"}):
            raise ValueError(
                f"{label} 发现来源 must be an independently captured DOM/accessibility/trace/control-tree source"
            )
        if not evidence_exists(row.get("证据路径", "")):
            raise ValueError(f"{label} must reference an existing non-empty inventory evidence file")
        fingerprint = _normalized(row.get("元素指纹", ""))
        if fingerprint in fingerprints:
            raise ValueError(f"{label} duplicates 元素指纹 already used by {fingerprints[fingerprint]}")
        fingerprints[fingerprint] = label
        key = identity(row)
        if key in inventory_identities:
            raise ValueError(f"{label} duplicates inventory identity already used by {inventory_identities[key]}")
        inventory_identities[key] = label

    discovery_identities = {identity(row) for row in discovery_rows}
    inventory_identity_set = set(inventory_identities)
    missing_discovery = sorted(inventory_identity_set - discovery_identities)
    ungrounded_discovery = sorted(discovery_identities - inventory_identity_set)
    if missing_discovery:
        raise ValueError(
            "page-discovery.csv omits element(s) captured by page-element-inventory.csv: "
            f"{missing_discovery[:10]}"
        )
    if ungrounded_discovery:
        raise ValueError(
            "page-discovery.csv contains element(s) absent from the independent page inventory: "
            f"{ungrounded_discovery[:10]}"
        )


def validate_discovery_rows(
    rows: list[dict[str, str]],
    evidence_exists: Callable[[str], bool],
    evidence_fingerprint: Callable[[str], str | None] | None = None,
) -> None:
    pagination_pages: set[str] = set()
    evidence_locations: dict[tuple[str, str], str] = {}
    image_evidence_owners: dict[str, str] = {}
    interaction_owners: dict[str, str] = {}
    for index, row in enumerate(rows, start=2):
        combined = "\n".join([row.get("元素名称/文案", ""), row.get("元素类型", ""), row.get("交互方式", "")])
        label = f"page-discovery.csv row {index} ({row.get('页面/入口', '')}/{row.get('元素名称/文案', '')})"
        required = [
            "页面/入口", "角色/权限", "数据状态", "交互实例ID", "元素名称/文案", "元素类型",
            "交互方式", "完整点击路径", "预期/观察行为", "操作步骤锚点", "预期结果锚点",
            "适用DFX维度", "适用DFX场景",
        ]
        missing = [field for field in required if not row.get(field, "").strip()]
        if missing:
            raise ValueError(f"{label} is missing full-discovery fields: {missing}")
        step_anchor = _normalized(row.get("操作步骤锚点", ""))
        result_anchor = _normalized(row.get("预期结果锚点", ""))
        step_source = _normalized("\n".join([
            row.get("元素名称/文案", ""), row.get("交互方式", ""), row.get("完整点击路径", ""),
        ]))
        result_source = _normalized("\n".join([
            row.get("预期/观察行为", ""), row.get("联动/依赖变化", ""), row.get("结果分支/后续状态", ""),
        ]))
        generic_anchors = {"点击", "操作", "正常", "成功", "页面", "功能正常", "页面正常", "操作成功"}
        if len(step_anchor) < 2 or step_anchor in generic_anchors or step_anchor not in step_source:
            raise ValueError(f"{label} 操作步骤锚点 must be a meaningful phrase copied from the executed element/action/path")
        if len(result_anchor) < 2 or result_anchor in generic_anchors or result_anchor not in result_source:
            raise ValueError(f"{label} 预期结果锚点 must be a meaningful phrase copied from the actually observed result")
        interaction_id = _normalized(row.get("交互实例ID", ""))
        previous_interaction_owner = interaction_owners.get(interaction_id)
        if previous_interaction_owner:
            raise ValueError(
                f"{label} reuses 交互实例ID already owned by {previous_interaction_owner}; "
                "every role/data-state/action branch must have a unique stable interaction instance ID"
            )
        interaction_owners[interaction_id] = label
        evidence = row.get("证据路径", "").strip()
        evidence_locator = row.get("证据定位", "").strip()
        if not evidence or not evidence_exists(evidence):
            raise ValueError(f"{label} must reference existing evidence for the actually executed interaction")
        if not evidence_locator:
            raise ValueError(
                f"{label} must record 证据定位 such as screenshot state/region, DOM or trace step, or video timestamp"
            )
        evidence_key = (_normalized(evidence), _normalized(evidence_locator))
        previous_evidence_owner = evidence_locations.get(evidence_key)
        if previous_evidence_owner:
            raise ValueError(
                f"{label} reuses the same evidence path+locator as {previous_evidence_owner}; "
                "every interaction must have independently reviewable execution evidence"
            )
        evidence_locations[evidence_key] = label
        evidence_digest = evidence_fingerprint(evidence) if evidence_fingerprint else None
        is_image = bool(
            IMAGE_EVIDENCE_RE.search(evidence)
            or (evidence_digest and evidence_digest.startswith("image:"))
        )
        if is_image and not _is_static_observation_row(row):
            image_key = evidence_digest or _normalized(evidence)
            previous_image_owner = image_evidence_owners.get(image_key)
            if previous_image_owner:
                raise ValueError(
                    f"{label} reuses static image evidence from {previous_image_owner}; a screenshot can prove only "
                    "the interaction state it captured, so capture a distinct post-action image or use trace/video "
                    "with unique locators"
                )
            image_evidence_owners[image_key] = label
        execution_text = "\n".join(
            [
                row.get("完整点击路径", ""),
                row.get("预期/观察行为", ""),
                row.get("联动/依赖变化", ""),
                row.get("结果分支/后续状态", ""),
                row.get("未覆盖/待确认原因", ""),
                row.get("备注", ""),
            ]
        ).lower()
        incomplete = sorted(
            {pattern.pattern for pattern in INCOMPLETE_DISCOVERY_PATTERNS if pattern.search(execution_text)}
        )
        if incomplete:
            raise ValueError(
                f"{label} still records an unexecuted/incomplete interaction {incomplete}; keep the batch in "
                "DISCOVERY_REQUIRED instead of claiming coverage or generating cases"
            )
        coverage_status = row.get("覆盖状态", "").strip()
        if coverage_status not in COMPLETED_DISCOVERY_STATUSES:
            raise ValueError(
                f"{label} must be actually executed and mark 覆盖状态=已覆盖 before the discovery gate can pass"
            )
        if row.get("未覆盖/待确认原因", "").strip():
            raise ValueError(f"{label} is marked 已覆盖 and must leave 未覆盖/待确认原因 empty")
        if is_selection_control(row):
            if not row.get("选项取值/输入值", "").strip() or not row.get("联动/依赖变化", "").strip():
                raise ValueError(f"{label} selection control must record a real selected value and dependency change")
        if _contains(combined, {"输入", "文本框", "数字框", "日期框", "搜索框"}):
            if not row.get("选项取值/输入值", "").strip() or not row.get("结果分支/后续状态", "").strip():
                raise ValueError(f"{label} input control must record actual input and result branch")
        if _contains(combined, {"分页", "翻页", "页码", "每页", "跳页"}):
            pagination_pages.add(row.get("页面/入口", "").strip())
        if _contains(
            combined,
            {"新增", "创建", "添加", "保存", "提交", "编辑", "修改", "删除", "移除", "清空", "解绑", "配置", "启用", "停用", "发布", "下线", "审批", "重置", "撤销", "归档"},
        ):
            source = row.get("测试数据来源", "")
            observed = "\n".join([row.get("预期/观察行为", ""), row.get("结果分支/后续状态", "")])
            if not _contains(source, {"AI_TEST", "CODEX_TEST", "用户提供测试数据"}):
                raise ValueError(f"{label} mutation must use tagged test data")
            if not _contains(observed, {"成功", "回显", "生效", "持久化"}):
                raise ValueError(f"{label} mutation must record success, persisted echo, or effective behavior")
    for page in pagination_pages:
        page_text = "\n".join(
            "\n".join([row.get("元素名称/文案", ""), row.get("元素类型", ""), row.get("交互方式", "")])
            for row in rows if row.get("页面/入口", "").strip() == page
        )
        required_groups = [{"每页", "条数"}, {"上一页", "下一页", "翻页"}, {"页码", "跳转", "边界", "禁用"}]
        if any(not _contains(page_text, group) for group in required_groups):
            raise ValueError(f"page-discovery.csv pagination on {page} must split page size, navigation, and boundary/jump controls")


def split_values(value: str) -> set[str]:
    normalized = (value or "").replace("，", ",").replace("；", ",").replace("、", ",").replace("/", ",")
    return {item.strip() for item in normalized.split(",") if item.strip()}


def validate_operation_plan_rows(plan_rows: list[dict[str, str]]) -> bool:
    has_mutation = False
    for index, row in enumerate(plan_rows, start=2):
        category = row.get("操作类别", "").strip()
        requirements = split_values(row.get("验证要求", ""))
        data_strategy = row.get("数据策略", "").strip()
        execution_state = row.get("执行状态", "").strip()
        label = f"element-case-plan.csv row {index} ({row.get('页面/入口', '')}/{row.get('元素名称/文案', '')})"
        missing_design = [
            field for field in ["交互实例ID", "适用DFX维度", "适用DFX场景", "测试设计方向"]
            if not row.get(field, "").strip()
        ]
        if missing_design:
            raise ValueError(f"{label} is missing DFX/design fields: {missing_design}")
        if category not in OPERATION_CATEGORIES:
            raise ValueError(f"{label} 操作类别 must be one of {sorted(OPERATION_CATEGORIES)}")
        if data_strategy not in DATA_STRATEGIES:
            raise ValueError(f"{label} 数据策略 must be one of {sorted(DATA_STRATEGIES)}")
        if execution_state not in EXECUTION_STATES:
            raise ValueError(f"{label} 执行状态 must be one of {sorted(EXECUTION_STATES)}")
        unknown_requirements = requirements - VERIFICATION_REQUIREMENTS
        if unknown_requirements:
            raise ValueError(f"{label} 验证要求 contains unsupported values: {sorted(unknown_requirements)}")
        required = REQUIRED_VERIFICATION.get(category, set())
        missing = sorted(required - requirements)
        if missing:
            raise ValueError(f"{label} 验证要求 is missing {missing} for 操作类别={category}")
        semantic_text = "\n".join(
            [row.get("功能点", ""), row.get("元素名称/文案", ""), row.get("元素类型", ""), row.get("交互方式", ""), row.get("测试设计方向", "")]
        )
        element_name = row.get("元素名称/文案", "")
        function_point = row.get("功能点", "")
        close_context = "\n".join(
            [element_name, row.get("元素类型", ""), row.get("测试设计方向", "")]
        )
        temporary_close = (
            _contains(element_name, {"取消", "返回", "关闭(X)", "关闭（X）", "Esc", "ESC"})
            or ("关闭" in element_name and _contains(close_context, {"弹窗", "抽屉", "对话框", "面板", "浮层"}))
        )
        if category in MUTATION_CATEGORIES and temporary_close and not _contains(
            f"{function_point}\n{element_name}",
            {
                "取消订单", "取消屏蔽", "取消确认", "取消发布", "取消归档", "取消订阅", "撤销审批",
                "终止任务", "关闭告警", "关闭工单", "关闭任务", "状态变更",
            },
        ):
            raise ValueError(f"{label} cancel/close/back interaction is not a persisted {category} operation")
        if category in MUTATION_CATEGORIES and "重置" in element_name and _contains(
            f"{row.get('页面/入口', '')}\n{function_point}",
            {"筛选", "查询条件", "搜索条件", "检索条件"},
        ):
            raise ValueError(f"{label} filter reset must use 操作类别=筛选 instead of persisted mutation")
        if category in MUTATION_CATEGORIES and is_selection_control(row) and not _contains(
            semantic_text,
            {"确认", "屏蔽", "启用", "停用", "发布", "下线", "审批", "撤销", "归档", "删除", "保存", "提交"},
        ):
            raise ValueError(f"{label} selection state is temporary UI state, not a persisted {category} operation")
        semantic_categories = {
            "创建": {"新增", "创建", "添加", "新建"},
            "编辑": {"编辑", "修改"},
            "删除": {"删除", "移除", "清空", "解绑"},
            "配置": {"配置", "开关", "变量", "路由", "认证"},
            "状态变更": {"启用", "停用", "发布", "下线", "审批", "重置", "撤销", "归档"},
        }
        detected = {name for name, markers in semantic_categories.items() if _contains(semantic_text, markers)}
        if detected and category not in detected:
            raise ValueError(f"{label} 操作类别={category} conflicts with element semantics {sorted(detected)}")
        if category in MUTATION_CATEGORIES:
            persistent_markers = {
                "创建": {"新增", "创建", "添加", "新建", "接入", "保存", "提交", "完成"},
                "编辑": {"编辑", "修改", "维护", "保存", "提交", "确定"},
                "删除": {"删除", "移除", "清空", "解绑"},
                "配置": {"配置", "开关", "变量", "路由", "认证", "保存", "提交"},
                "状态变更": {
                    "状态", "确认", "屏蔽", "启用", "停用", "发布", "下线", "审批", "重置", "撤销", "归档",
                },
            }
            if not _contains(semantic_text, persistent_markers[category]):
                raise ValueError(
                    f"{label} 操作类别={category} lacks a concrete persisted mutation action; "
                    "do not classify selection, reset, cancel, or close UI state as data mutation"
                )
            has_mutation = True
            if data_strategy not in {"本次创建测试数据", "用户提供测试数据"}:
                raise ValueError(f"{label} mutation must use 本次创建测试数据 or 用户提供测试数据")
            if execution_state != "已完成":
                raise ValueError(f"{label} mutation must be actually executed and marked 执行状态=已完成")
        if row.get("是否必须真实执行", "").strip() in {"是", "Y", "Yes", "YES", "yes"} and execution_state != "已完成":
            raise ValueError(f"{label} is marked 是否必须真实执行=是 and must have 执行状态=已完成")
    return has_mutation


def validate_mutation_discovery_evidence(
    plan_rows: list[dict[str, str]],
    discovery_rows: list[dict[str, str]],
    evidence_exists: Callable[[str], bool],
) -> None:
    for index, plan in enumerate(plan_rows, start=2):
        if plan.get("操作类别", "").strip() not in MUTATION_CATEGORIES:
            continue
        page = plan.get("页面/入口", "").strip()
        element = plan.get("元素名称/文案", "").strip()
        interaction_id = plan.get("交互实例ID", "").strip()
        element_type = plan.get("元素类型", "").strip()
        matches = [
            row for row in discovery_rows
            if row.get("交互实例ID", "").strip() == interaction_id
            and row.get("页面/入口", "").strip() == page
            and row.get("元素名称/文案", "").strip() == element
            and row.get("元素类型", "").strip() == element_type
        ]
        label = f"element-case-plan.csv row {index} ({page}/{element})"
        if not matches:
            raise ValueError(f"{label} mutation has no matching page-discovery.csv row")
        for discovery in matches:
            source = discovery.get("测试数据来源", "")
            if not _contains(source, {"AI_TEST", "CODEX_TEST", "用户提供测试数据"}):
                raise ValueError(f"{label} mutation discovery must use tagged test data")
            evidence = discovery.get("证据路径", "").strip()
            if not evidence or not evidence_exists(evidence):
                raise ValueError(f"{label} mutation discovery must reference existing evidence")
            observed = "\n".join([discovery.get("预期/观察行为", ""), discovery.get("结果分支/后续状态", "")])
            if not _contains(observed, {"成功", "回显", "生效", "持久化", "不再显示", "搜索不到", "状态更新", "数据更新"}):
                raise ValueError(f"{label} mutation discovery must record an observable persisted/effective result")


def validate_lifecycle_rows(
    lifecycle_rows: list[dict[str, str]],
    has_mutation: bool,
    contains_any: Callable[[str, list[str]], bool],
    plan_rows: list[dict[str, str]] | None = None,
) -> None:
    if not has_mutation:
        if lifecycle_rows:
            raise ValueError(
                "test-data-lifecycle.csv contains rows but element-case-plan.csv has no persisted mutation owner; "
                "do not wrap filters, selection, cancel, close, or view actions as lifecycle data"
            )
        return
    if not lifecycle_rows:
        raise ValueError("test-data-lifecycle.csv must record AI_TEST/CODEX_TEST CRUD/config lifecycle before writing cases")
    for index, row in enumerate(lifecycle_rows, start=2):
        if not row.get("交互实例ID", "").strip():
            raise ValueError(f"test-data-lifecycle.csv row {index} must record the exact 交互实例ID mutation owner")
        combined = "\n".join(row.values())
        if not contains_any(combined, ["AI_TEST", "CODEX_TEST", "用户提供测试数据"]):
            raise ValueError(f"test-data-lifecycle.csv row {index} must bind to AI_TEST/CODEX_TEST or user-provided test data")
        if contains_any(combined, ["配置", "开关", "权限", "变量", "模型", "路由", "认证"]):
            if not row.get("配置生效验证点", "").strip():
                raise ValueError(f"test-data-lifecycle.csv row {index} must record actual configuration effect verification")
    mutation_plan_rows = [row for row in (plan_rows or []) if row.get("操作类别", "").strip() in MUTATION_CATEGORIES]
    mutation_owner_keys = {
        (
            row.get("交互实例ID", "").strip(),
            row.get("页面/入口", "").strip(),
            row.get("元素名称/文案", "").strip(),
        )
        for row in mutation_plan_rows
    }
    extra_lifecycle_rows = [
        f"row {index} ({row.get('关联页面/入口', '')}/{row.get('修改项/元素', '')})"
        for index, row in enumerate(lifecycle_rows, start=2)
        if (
            row.get("交互实例ID", "").strip(),
            row.get("关联页面/入口", "").strip(),
            row.get("修改项/元素", "").strip(),
        )
        not in mutation_owner_keys
    ]
    if extra_lifecycle_rows:
        raise ValueError(
            "test-data-lifecycle.csv contains rows with no persisted mutation owner in element-case-plan.csv: "
            f"{extra_lifecycle_rows[:10]}; do not wrap filters, selection, cancel, close, or view actions as lifecycle data"
        )
    fields_by_category = {
        "创建": ["创建结果", "查看结果", "实际生效结果"],
        "编辑": ["创建结果", "查看结果", "编辑前值", "编辑后值", "编辑结果", "保存后回显", "实际生效结果"],
        "配置": ["创建结果", "查看结果", "编辑前值", "编辑后值", "编辑结果", "保存后回显", "实际生效结果", "配置生效验证点"],
        "状态变更": ["创建结果", "查看结果", "编辑前值", "编辑后值", "编辑结果", "保存后回显", "实际生效结果"],
        "删除": ["创建结果", "查看结果", "删除取消结果", "删除确认结果", "清理状态"],
    }
    def ids(value: str) -> set[str]:
        return {part.strip() for part in re.split(r"[,，;；\s]+", value or "") if part.strip()}

    create_objects: dict[str, set[str]] = {}
    for create_plan in [row for row in mutation_plan_rows if row.get("操作类别", "").strip() == "创建"]:
        interaction_id = create_plan.get("交互实例ID", "").strip()
        planned_create_ids = ids(create_plan.get("计划用例ID", ""))
        matching_create_rows = [
            row for row in lifecycle_rows
            if row.get("交互实例ID", "").strip() == interaction_id
            and row.get("关联页面/入口", "").strip() == create_plan.get("页面/入口", "").strip()
            and row.get("修改项/元素", "").strip() == create_plan.get("元素名称/文案", "").strip()
        ]
        for lifecycle in matching_create_rows:
            data_id = lifecycle.get("测试数据ID/名称", "").strip()
            owner_ids = ids(lifecycle.get("创建步骤关联用例", ""))
            if not data_id or not owner_ids or not owner_ids.issubset(planned_create_ids):
                raise ValueError(
                    f"test-data-lifecycle.csv create owner {interaction_id} must bind 测试数据ID/名称 and "
                    "创建步骤关联用例 to the exact create plan IDs"
                )
            create_objects.setdefault(data_id, set()).update(owner_ids)

    for plan in mutation_plan_rows:
        interaction_id = plan.get("交互实例ID", "").strip()
        page = plan.get("页面/入口", "").strip()
        element = plan.get("元素名称/文案", "").strip()
        matching = [
            row for row in lifecycle_rows
            if row.get("交互实例ID", "").strip() == interaction_id
            and row.get("关联页面/入口", "").strip() == page
            and row.get("修改项/元素", "").strip() == element
        ]
        if not matching:
            raise ValueError(
                "test-data-lifecycle.csv must record every mutating item separately; missing "
                f"{page}/{element}"
            )
        category = plan.get("操作类别", "").strip()
        for row in matching:
            missing = [field for field in fields_by_category[category] if not row.get(field, "").strip()]
            if missing:
                raise ValueError(f"test-data-lifecycle.csv {page}/{element} is missing {missing} for 操作类别={category}")
            if category in {"编辑", "配置", "状态变更"} and row.get("编辑前值", "").strip() == row.get("编辑后值", "").strip():
                raise ValueError(f"test-data-lifecycle.csv {page}/{element} 编辑前值 and 编辑后值 must differ")
            if category != "创建" and plan.get("数据策略", "").strip() == "本次创建测试数据":
                data_id = row.get("测试数据ID/名称", "").strip()
                owner_ids = ids(row.get("创建步骤关联用例", ""))
                if data_id not in create_objects or not owner_ids or owner_ids != create_objects[data_id]:
                    raise ValueError(
                        f"test-data-lifecycle.csv {page}/{element} uses 本次创建测试数据 but does not reference "
                        "the same 测试数据ID/名称 and exact 创建步骤关联用例 as its create owner"
                    )


def risk_page_verification_state(
    rows: list[dict[str, str]],
    discovery_rows: list[dict[str, str]] | None = None,
    evidence_exists: Callable[[str], bool] | None = None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    discovery_rows = discovery_rows or []
    actual_rows = [
        row for row in rows
        if row.get("风险ID", "").strip() not in {"", "RISK-PENDING", "RISK-NONE"}
    ]
    for index, row in enumerate(actual_rows, start=2):
        risk_id = row.get("风险ID", "").strip()
        verifiability = row.get("页面可验证性", "").strip()
        label = f"row {index} ({risk_id})"
        if verifiability == "可直接验证":
            reasons.append(f"{label}: question is directly page-verifiable and must return to discovery instead of asking the user")
            continue
        if verifiability not in {"不可直接验证", "受外部阻塞"}:
            reasons.append(
                f"{label}: 页面可验证性 must be 不可直接验证 or 受外部阻塞 before requesting user confirmation"
            )
            continue
        required = ["页面验证动作", "页面验证结果", "不可验证/外部依赖原因", "证据路径"]
        missing = [field for field in required if not row.get(field, "").strip()]
        if missing:
            reasons.append(f"{label}: page verification evidence is incomplete: {missing}")
            continue
        combined_evidence = "\n".join(
            [row.get("页面验证动作", ""), row.get("页面验证结果", ""), row.get("已完成深探依据", "")]
        )
        if _contains(
            combined_evidence,
            {
                "待补充", "待验证", "未点击", "未操作", "未尝试", "未逐项", "没有逐项", "未实际",
                "仅查看", "只查看", "仅展开", "只展开", "看到选项", "仅观察", "只观察", "不知道", "不清楚",
            },
        ):
            reasons.append(f"{label}: page-verifiable exploration is still incomplete")
        reason = row.get("不可验证/外部依赖原因", "")
        question = row.get("模型不理解内容/待确认问题", "")
        if _contains(reason, {"未知", "待确认", "不确定", "可能", "不清楚", "不知道"}):
            reasons.append(f"{label}: external dependency reason is uncertain rather than an observed blocker")
        if verifiability == "受外部阻塞" and not _contains(
            combined_evidence,
            {
                "无权限", "权限不足", "未开通", "不可访问", "无法访问", "置灰", "禁用", "403", "未提供",
                "缺少", "不展示", "仅显示", "无法从页面", "环境不可用", "服务不可用", "接口失败", "超时",
                "无测试数据", "禁止造数", "第三方不可用",
            },
        ):
            reasons.append(f"{label}: 受外部阻塞 must record a concrete observed blocker, not a hypothetical dependency")
        if _contains(question, PAGE_OBSERVABLE_MARKERS) and not _contains(
            f"{question}\n{reason}", EXTERNAL_DEPENDENCY_MARKERS
        ):
            reasons.append(
                f"{label}: question describes an observable page interaction without an external dependency; verify it on the page"
            )
        evidence = row.get("证据路径", "").strip()
        if evidence_exists is not None and not evidence_exists(evidence):
            reasons.append(f"{label}: risk page-verification evidence path does not exist")
        page = row.get("关联页面/入口", "").strip()
        element = row.get("关联元素名称/文案", "").strip()
        if discovery_rows and page:
            page_rows = [
                discovery for discovery in discovery_rows
                if discovery.get("页面/入口", "").strip() == page
            ]
            if not page_rows:
                reasons.append(f"{label}: referenced page has no matching page-discovery.csv evidence row")
            elif element:
                elements = [item.strip() for item in re.split(r"[;；,，、\r\n]+", element) if item.strip()]
                known = {discovery.get("元素名称/文案", "").strip() for discovery in page_rows}
                missing_elements = [item for item in elements if item not in known]
                if missing_elements:
                    reasons.append(
                        f"{label}: referenced element(s) have no matching page-discovery.csv row: {missing_elements}"
                    )
        elif discovery_rows and not page and not _contains(reason, EXTERNAL_DEPENDENCY_MARKERS):
            reasons.append(f"{label}: must identify the explored page or a concrete external dependency")
    return ("ready", []) if not reasons else ("discovery_required", reasons)


def risk_confirmation_state(rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    real_rows = [row for row in rows if (row.get("风险ID", "") or "").strip()]
    if not real_rows:
        return "pending", ["risk-confirmation.csv has no decision row"]
    reasons: list[str] = []
    none_rows = [row for row in real_rows if row.get("风险ID", "").strip() == "RISK-NONE"]
    if none_rows and len(real_rows) != 1:
        reasons.append("RISK-NONE must be the only decision row and cannot be mixed with model uncertainty rows")
    ids = [row.get("风险ID", "").strip() for row in real_rows]
    if len(ids) != len(set(ids)):
        reasons.append("risk IDs must be unique")
    for index, row in enumerate(real_rows, start=2):
        risk_id = row.get("风险ID", "").strip()
        status = row.get("确认状态", "").strip()
        conclusion = row.get("用户确认结论", "").strip()
        if risk_id == "RISK-NONE":
            if status != "无需用户确认" or row.get("是否阻塞用例设计", "").strip() not in {"否", "N", "No", "NO", "no"}:
                reasons.append(f"row {index}: RISK-NONE must be non-blocking and marked 确认状态=无需用户确认")
            if conclusion != "无需用户确认":
                reasons.append(f"row {index}: RISK-NONE must use 用户确认结论=无需用户确认")
            for field in ["模型不理解内容/待确认问题", "已完成深探依据", "处置策略"]:
                if not row.get(field, "").strip():
                    reasons.append(f"row {index}: RISK-NONE is missing {field}")
            if row.get("页面可验证性", "").strip() != "不适用":
                reasons.append(f"row {index}: RISK-NONE must use 页面可验证性=不适用")
            for field in ["页面验证动作", "页面验证结果", "不可验证/外部依赖原因"]:
                if not row.get(field, "").strip():
                    reasons.append(f"row {index}: RISK-NONE is missing {field}")
            if row.get("关联用例ID", "").strip():
                reasons.append(f"row {index}: RISK-NONE must not reference case IDs")
            continue
        if risk_id == "RISK-PENDING" or status != "已确认" or conclusion in {"", "待用户确认", "待确认"}:
            reasons.append(f"row {index}: model uncertainty still requires user confirmation")
        if row.get("是否阻塞用例设计", "").strip() not in {"否", "N", "No", "NO", "no"}:
            reasons.append(f"row {index}: confirmed uncertainty still blocks case design")
    return ("ready", []) if not reasons else ("pending", reasons)


def validate_risk_confirmation(
    rows: list[dict[str, str]],
    split_ids: Callable[[str], list[str]],
    discovery_rows: list[dict[str, str]] | None = None,
    evidence_exists: Callable[[str], bool] | None = None,
) -> tuple[list[dict[str, str]], set[str]]:
    state, reasons = risk_confirmation_state(rows)
    if state != "ready":
        raise ValueError("risk-confirmation.csv is not ready: " + "; ".join(reasons))
    page_state, page_reasons = risk_page_verification_state(rows, discovery_rows, evidence_exists)
    if page_state != "ready":
        raise ValueError("risk-confirmation.csv contains page-verifiable or incompletely explored questions: " + "; ".join(page_reasons))
    real_rows = [row for row in rows if (row.get("风险ID", "") or "").strip()]
    case_ids: set[str] = set()
    for index, row in enumerate(real_rows, start=2):
        if row.get("风险ID", "").strip() == "RISK-NONE":
            continue
        required = ["模型不理解内容/待确认问题", "已完成深探依据", "处置策略"]
        missing = [field for field in required if not row.get(field, "").strip()]
        if missing:
            raise ValueError(f"risk-confirmation.csv row {index} is missing {missing}")
        linked = split_ids(row.get("关联用例ID", ""))
        non_case = _contains(row.get("处置策略", ""), {"仅记录风险", "不生成用例", "性能测试设计", "自动化建议"})
        if not linked and not non_case:
            raise ValueError(f"risk-confirmation.csv row {index} must link planned case IDs or explicitly use a non-case landing strategy")
        case_ids.update(linked)
    return real_rows, case_ids
