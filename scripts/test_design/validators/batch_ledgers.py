# -*- coding: utf-8 -*-
from __future__ import annotations

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
REQUIRED_VERIFICATION = {
    "创建": {"回显", "持久化"},
    "编辑": {"回显", "持久化", "实际生效"},
    "配置": {"回显", "持久化", "实际生效"},
    "状态变更": {"回显", "持久化", "实际生效"},
    "删除": {"持久化", "确认取消"},
}


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


def validate_discovery_rows(rows: list[dict[str, str]], evidence_exists: Callable[[str], bool]) -> None:
    pagination_pages: set[str] = set()
    for index, row in enumerate(rows, start=2):
        combined = "\n".join([row.get("元素名称/文案", ""), row.get("元素类型", ""), row.get("交互方式", "")])
        label = f"page-discovery.csv row {index} ({row.get('页面/入口', '')}/{row.get('元素名称/文案', '')})"
        required = ["页面/入口", "元素名称/文案", "元素类型", "交互方式", "完整点击路径", "预期/观察行为", "适用DFX维度", "适用DFX场景"]
        missing = [field for field in required if not row.get(field, "").strip()]
        if missing:
            raise ValueError(f"{label} is missing full-discovery fields: {missing}")
        if _contains(combined, {"下拉", "选择", "单选", "复选", "级联", "树选择"}):
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
            evidence = row.get("证据路径", "").strip()
            if not evidence or not evidence_exists(evidence):
                raise ValueError(f"{label} mutation must reference existing evidence")
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
            field for field in ["适用DFX维度", "适用DFX场景", "测试设计方向"]
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
        matches = [
            row for row in discovery_rows
            if row.get("页面/入口", "").strip() == page and row.get("元素名称/文案", "").strip() == element
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
        return
    if not lifecycle_rows:
        raise ValueError("test-data-lifecycle.csv must record AI_TEST/CODEX_TEST CRUD/config lifecycle before writing cases")
    for index, row in enumerate(lifecycle_rows, start=2):
        combined = "\n".join(row.values())
        if not contains_any(combined, ["AI_TEST", "CODEX_TEST", "用户提供测试数据"]):
            raise ValueError(f"test-data-lifecycle.csv row {index} must bind to AI_TEST/CODEX_TEST or user-provided test data")
        if contains_any(combined, ["配置", "开关", "权限", "变量", "模型", "路由", "认证"]):
            if not row.get("配置生效验证点", "").strip():
                raise ValueError(f"test-data-lifecycle.csv row {index} must record actual configuration effect verification")
    mutation_plan_rows = [row for row in (plan_rows or []) if row.get("操作类别", "").strip() in MUTATION_CATEGORIES]
    fields_by_category = {
        "创建": ["创建结果", "查看结果", "实际生效结果"],
        "编辑": ["创建结果", "查看结果", "编辑前值", "编辑后值", "编辑结果", "保存后回显", "实际生效结果"],
        "配置": ["创建结果", "查看结果", "编辑前值", "编辑后值", "编辑结果", "保存后回显", "实际生效结果", "配置生效验证点"],
        "状态变更": ["创建结果", "查看结果", "编辑前值", "编辑后值", "编辑结果", "保存后回显", "实际生效结果"],
        "删除": ["创建结果", "查看结果", "删除取消结果", "删除确认结果", "清理状态"],
    }
    for plan in mutation_plan_rows:
        page = plan.get("页面/入口", "").strip()
        element = plan.get("元素名称/文案", "").strip()
        matching = [
            row for row in lifecycle_rows
            if row.get("关联页面/入口", "").strip() == page and row.get("修改项/元素", "").strip() == element
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
            if row.get("关联用例ID", "").strip():
                reasons.append(f"row {index}: RISK-NONE must not reference case IDs")
            continue
        if risk_id == "RISK-PENDING" or status != "已确认" or conclusion in {"", "待用户确认", "待确认"}:
            reasons.append(f"row {index}: model uncertainty still requires user confirmation")
        if row.get("是否阻塞用例设计", "").strip() not in {"否", "N", "No", "NO", "no"}:
            reasons.append(f"row {index}: confirmed uncertainty still blocks case design")
    return ("ready", []) if not reasons else ("pending", reasons)


def validate_risk_confirmation(rows: list[dict[str, str]], split_ids: Callable[[str], list[str]]) -> tuple[list[dict[str, str]], set[str]]:
    state, reasons = risk_confirmation_state(rows)
    if state != "ready":
        raise ValueError("risk-confirmation.csv is not ready: " + "; ".join(reasons))
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
