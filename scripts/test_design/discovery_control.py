# -*- coding: utf-8 -*-
"""Left-shifted, single-agent page-discovery execution control.

The phase validator remains a final backstop.  This module expands every
interactive inventory row into small executable obligations and binds a
completion to an automatically recorded page-tool sequence:

    successful read -> successful mutation -> successful changed read

The control files remain mutable during discovery.  There is deliberately no
source fingerprint or task claim, so repairing one element never invalidates
already completed elements or a whole phase.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_text


CONTROL_VERSION = 1
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
CONTROL_DIR = Path("artifacts/discovery-control")
CONFIG_FILE = "config.json"
ACTIVE_FILE = "active-obligation.json"
EVENTS_FILE = "action-events.jsonl"
COMPLETIONS_FILE = "completions.jsonl"
OBLIGATIONS_FILE = "obligations.json"

INPUT_BRANCHES = ("正常输入", "空值", "边界输入", "非法输入")
DYNAMIC_SELECTION_BRANCHES = (
    "有结果搜索", "无结果搜索", "滚动/分页加载", "首项选择",
    "中间项选择", "末项/边界选择", "清空恢复",
)
PAGINATION_BRANCHES = (
    "每页条数", "上一页", "下一页", "页码跳转",
    "边界页", "末页/无数据", "筛选后重置",
)
MODAL_BRANCHES = ("打开", "确认", "取消", "关闭/Esc", "恢复")

_TYPE_ALIASES = {
    "textbox": {"textbox", "文本框", "输入框", "text input", "input"},
    "textarea": {"textarea", "文本域", "多行文本框"},
    "select": {"select", "combobox", "下拉", "下拉框", "选择框", "枚举"},
    "radio": {"radio", "单选", "单选框"},
    "checkbox": {"checkbox", "复选", "复选框", "多选"},
    "pagination": {"pagination", "pager", "分页", "翻页", "页码"},
    "dialog": {"dialog", "modal", "drawer", "弹窗", "对话框", "抽屉", "浮层"},
    "button": {"button", "按钮", "图标按钮"},
    "link": {"link", "链接"},
    "switch": {"switch", "开关", "切换"},
    "upload": {"upload", "上传"},
}
_INTERACTION_ALIASES = {
    "input": {"input", "fill", "type", "填写", "输入", "录入"},
    "select": {"select", "choose", "选择", "下拉选择", "勾选"},
    "click": {"click", "点击", "单击", "触发", "打开"},
    "toggle": {"toggle", "切换", "勾选", "取消勾选"},
    "paginate": {"paginate", "pagination", "分页", "翻页", "跳页"},
    "upload": {"upload", "上传"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: str, aliases: dict[str, set[str]], default: str = "") -> str:
    text = " ".join((value or "").strip().casefold().split())
    if not text:
        return default
    for canonical, values in aliases.items():
        if text in values or any(token in text for token in values if len(token) >= 2):
            return canonical
    return text


def canonical_element_type(value: str) -> str:
    return _canonical(value, _TYPE_ALIASES, "unknown")


def canonical_interaction(value: str) -> str:
    return _canonical(value, _INTERACTION_ALIASES, "unknown")


def normalized_control_identity(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    """Stable identity tolerant of Chinese/English control synonyms."""
    return (
        row.get("最小标题路径", "").strip(),
        row.get("交互实例ID", "").strip(),
        row.get("页面/入口", "").strip(),
        row.get("元素名称/文案", "").strip(),
        canonical_element_type(row.get("元素类型", "")),
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [
            {key: (value or "").strip() for key, value in row.items() if key is not None}
            for row in csv.DictReader(stream)
            if any((value or "").strip() for value in row.values())
        ]


def _real_inventory_rows(run_dir: Path) -> list[dict[str, str]]:
    return [
        row for row in _read_csv(run_dir / "page-element-inventory.csv")
        if row.get("交互实例ID") and row.get("元素名称/文案") and row.get("元素类型")
    ]


def _option_rows(run_dir: Path, interaction_id: str) -> list[dict[str, str]]:
    return [
        row for row in _read_csv(run_dir / "selection-option-observations.csv")
        if row.get("交互实例ID") == interaction_id and row.get("选项值")
    ]


def _configuration_rows(run_dir: Path, interaction_id: str = "") -> list[dict[str, str]]:
    rows = [
        row for row in _read_csv(run_dir / "configuration-variant-observations.csv")
        if row.get("交互实例ID") and row.get("变体ID") and row.get("变体类别")
    ]
    return [row for row in rows if row.get("交互实例ID") == interaction_id] if interaction_id else rows


def _slug(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value).strip("-")
    return text[:40] or "item"


def _obligation_id(interaction_id: str, kind: str, branch: str, semantic_identity: tuple[str, ...]) -> str:
    raw = "\0".join((interaction_id, kind, branch, *semantic_identity)).encode("utf-8")
    return f"{_slug(interaction_id)}--{_slug(kind)}--{_slug(branch)}--{hashlib.sha256(raw).hexdigest()[:10]}"


def _looks_interactive(row: dict[str, str]) -> bool:
    state = row.get("可交互状态", "").casefold()
    if state in {"否", "不可交互", "disabled", "false", "0"}:
        return False
    combined = "\n".join(
        [row.get("元素名称/文案", ""), row.get("元素类型", ""), row.get("交互方式", "")]
    ).casefold()
    return any(
        marker in combined
        for marker in {
            "按钮", "点击", "输入", "填写", "选择", "下拉", "单选", "复选", "开关",
            "分页", "翻页", "页码", "弹窗", "对话框", "抽屉", "上传", "编辑", "删除",
            "创建", "新增", "保存", "提交", "button", "link", "input", "textbox",
            "textarea", "select", "combobox", "radio", "checkbox", "pagination", "dialog",
        }
    )


def _control_traits(row: dict[str, str], observed: dict[str, str] | None = None) -> set[str]:
    element_type = canonical_element_type(row.get("元素类型", ""))
    interaction = canonical_interaction(row.get("交互方式", ""))
    direct = "\n".join(
        [row.get("元素名称/文案", ""), row.get("元素类型", ""), row.get("交互方式", "")]
    ).casefold()
    context = f"{row.get('页面/入口', '')}\n{direct}".casefold()
    observed_behavior = "\n".join([
        (observed or {}).get("预期/观察行为", ""),
        (observed or {}).get("结果分支/后续状态", ""),
        (observed or {}).get("联动/依赖变化", ""),
    ]).casefold()
    traits: set[str] = set()
    if element_type in {"textbox", "textarea"} or interaction == "input":
        traits.add("input")
    if element_type in {"select", "radio", "checkbox"} or interaction == "select":
        traits.add("selection")
    if element_type == "pagination" or interaction == "paginate" or any(
        marker in f"{direct}\n{observed_behavior}" for marker in {"分页", "翻页", "页码", "每页", "上一页", "下一页", "pagination", "pager"}
    ):
        traits.add("pagination")
    if element_type == "dialog" or any(marker in f"{direct}\n{observed_behavior}" for marker in {"弹窗", "对话框", "抽屉", "浮层", "modal", "dialog", "drawer"}):
        traits.add("modal")
    if any(marker in direct for marker in {"创建", "新增", "添加", "create", "add"}):
        traits.add("create")
    persistent_context = any(
        marker in context for marker in {"编辑", "修改", "配置", "状态", "edit", "update", "config"}
    )
    editable_control = element_type in {
        "textbox", "textarea", "select", "radio", "checkbox", "switch", "upload"
    } or interaction in {"input", "select", "toggle", "upload"}
    direct_mutation = any(
        marker in direct for marker in {"编辑", "修改", "配置", "启用", "停用", "发布", "下线", "edit", "update", "config"}
    )
    if direct_mutation or (persistent_context and editable_control):
        traits.add("persistent_mutation")
    if any(marker in direct for marker in {"删除", "移除", "delete", "remove"}):
        traits.add("delete")
    return traits


def _is_configurable(row: dict[str, str], traits: set[str]) -> bool:
    return "persistent_mutation" in traits and bool(
        traits.intersection({"input", "selection"})
        or canonical_element_type(row.get("元素类型", "")) in {"switch", "upload", "radio", "checkbox"}
    )


def configuration_plan_issues(run_dir: Path) -> list[str]:
    issues: list[str] = []
    all_rows = _configuration_rows(run_dir)
    variant_ids: set[str] = set()
    inventory_by_id = {row.get("交互实例ID", ""): row for row in _real_inventory_rows(run_dir)}
    configurable_ids: set[str] = set()
    for interaction_id, inventory in inventory_by_id.items():
        traits = _control_traits(inventory)
        if not _is_configurable(inventory, traits) and not any(
            row.get("交互实例ID") == interaction_id for row in all_rows
        ):
            continue
        configurable_ids.add(interaction_id)
        variants = [row for row in all_rows if row.get("交互实例ID") == interaction_id]
        if not variants:
            issues.append(f"{interaction_id}/{inventory.get('元素名称/文案')}: configuration variants are not planned")
            continue
        categories = {row.get("变体类别", "") for row in variants}
        if not categories.intersection({"默认不配置", "默认值", "不配置"}):
            issues.append(f"{interaction_id}: missing default/unconfigured variant")
        option_values = {row.get("选项值", "") for row in _option_rows(run_dir, interaction_id)}
        planned_values = {row.get("配置值/组合", "") for row in variants}
        missing_values = sorted(option_values - planned_values)
        if missing_values:
            issues.append(f"{interaction_id}: finite option variants missing {missing_values}")
        if canonical_element_type(inventory.get("元素类型", "")) == "switch":
            normalized = "\n".join(categories | planned_values)
            if not all(any(marker in normalized for marker in markers) for markers in (("开启", "开", "on"), ("关闭", "关", "off"))):
                issues.append(f"{interaction_id}: switch must plan both on and off variants")
        for row in variants:
            variant_id = row.get("变体ID", "")
            if variant_id in variant_ids:
                issues.append(f"duplicate configuration variant id: {variant_id}")
            variant_ids.add(variant_id)
            required = ["页面/入口", "配置项", "配置值/组合", "生效时机", "执行策略"]
            missing = [field for field in required if not row.get(field, "")]
            if missing:
                issues.append(f"{interaction_id}/{variant_id}: missing {missing}")
            if row.get("生效时机") == "创建时":
                data_id = row.get("测试数据ID/名称", "")
                if not re.search(r"(?:AI_TEST|CODEX_TEST)", data_id):
                    issues.append(f"{interaction_id}/{variant_id}: create-time variant requires tagged independent test data")
                if row.get("执行策略") != "独立创建":
                    issues.append(f"{interaction_id}/{variant_id}: create-time variant must use 独立创建")
        combination_declared = any(
            row.get("依赖/互斥条件", "") or "组合" in row.get("变体类别", "") for row in variants
        )
        totals = {row.get("可达组合总数", "") for row in variants if row.get("可达组合总数", "")}
        if combination_declared and not totals:
            issues.append(f"{interaction_id}: combination variants must declare 可达组合总数")
        if len(totals) > 1:
            issues.append(f"{interaction_id}: 可达组合总数 must be consistent")
        if totals:
            raw_total = next(iter(totals))
            try:
                total = int(raw_total)
            except ValueError:
                issues.append(f"{interaction_id}: 可达组合总数 must be an integer")
            else:
                strategies = {row.get("组合覆盖策略", "") for row in variants}
                if total <= 16:
                    if strategies != {"全组合"} or len(planned_values) < total:
                        issues.append(f"{interaction_id}: reachable combinations <=16 require 全组合 and {total} unique variants")
                else:
                    required_strategy = "单项全量+依赖互斥边界+Pairwise"
                    required_categories = {"边界组合", "Pairwise组合"}
                    dependency_text = "\n".join(row.get("依赖/互斥条件", "") for row in variants)
                    if "依赖" in dependency_text:
                        required_categories.add("依赖组合")
                    if "互斥" in dependency_text:
                        required_categories.add("互斥组合")
                    if strategies != {required_strategy} or not required_categories.issubset(categories):
                        issues.append(
                            f"{interaction_id}: large combination space requires {required_strategy} and {sorted(required_categories)}"
                        )
    extras = sorted({row.get("交互实例ID", "") for row in all_rows} - configurable_ids)
    if extras:
        issues.append(f"configuration variants reference non-configurable or missing interactions: {extras}")
    create_data_ids = [
        row.get("测试数据ID/名称", "") for row in all_rows if row.get("生效时机") == "创建时"
    ]
    duplicates = sorted({item for item in create_data_ids if item and create_data_ids.count(item) > 1})
    if duplicates:
        issues.append(f"create-time variants must use independent test objects; duplicated: {duplicates}")
    return issues


def _make_obligation(
    row: dict[str, str],
    kind: str,
    branch: str,
    operation: str,
    instruction: str,
    *,
    requires_commit: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    interaction_id = row.get("交互实例ID", "").strip()
    semantic_identity = normalized_control_identity(row)
    result = {
        "obligation_id": _obligation_id(interaction_id, kind, branch, semantic_identity),
        "interaction_id": interaction_id,
        "leaf_path": row.get("最小标题路径", "").strip(),
        "page": row.get("页面/入口", "").strip(),
        "element_name": row.get("元素名称/文案", "").strip(),
        "element_type": canonical_element_type(row.get("元素类型", "")),
        "interaction": canonical_interaction(row.get("交互方式", "")),
        "kind": kind,
        "branch": branch,
        "required_operation": operation,
        "requires_commit": requires_commit,
        "instruction": instruction,
    }
    result.update(extra or {})
    return result


def build_obligations(run_dir: Path) -> list[dict[str, Any]]:
    """Derive deterministic obligations from the mutable element inventory."""
    run_dir = run_dir.resolve()
    obligations: list[dict[str, Any]] = []
    discovery_by_identity = {
        normalized_control_identity(row): row
        for row in _read_csv(run_dir / "page-discovery.csv")
        if row.get("交互实例ID") and row.get("元素名称/文案")
    }
    for row in _real_inventory_rows(run_dir):
        if not _looks_interactive(row):
            continue
        interaction_id = row["交互实例ID"]
        element = row["元素名称/文案"]
        traits = _control_traits(row, discovery_by_identity.get(normalized_control_identity(row)))
        configuration_rows = _configuration_rows(run_dir, interaction_id)
        configurable = _is_configurable(row, traits) or bool(configuration_rows)
        specialized = False
        if "input" in traits:
            specialized = True
            for branch in INPUT_BRANCHES:
                obligations.append(_make_obligation(
                    row, "input", branch, "input",
                    f"对“{element}”实际执行{branch}，提交/触发校验，观察结果并恢复可继续测试的状态。",
                ))
        if "selection" in traits:
            specialized = True
            options = _option_rows(run_dir, interaction_id)
            obligations.append(_make_obligation(
                row, "selection", "枚举选项", "expand",
                f"展开“{element}”，读取完整选项集合并逐行写入 selection-option-observations.csv。",
            ))
            set_types = {option.get("选项集合类型", "") for option in options}
            if "动态" in set_types:
                for branch in DYNAMIC_SELECTION_BRANCHES:
                    obligations.append(_make_obligation(
                        row, "dynamic-selection", branch, "select",
                        f"对动态选择控件“{element}”实际执行{branch}并观察页面变化和恢复结果。",
                    ))
            else:
                for option in sorted(options, key=lambda item: (item.get("选项序号", ""), item.get("选项值", ""))):
                    value = option.get("选项值", "")
                    obligations.append(_make_obligation(
                        row, "selection-option", value, "select",
                        f"在“{element}”中实际选择“{value}”，观察选择后的页面、联动和数据变化，再恢复。",
                    ))
        if "pagination" in traits:
            specialized = True
            for branch in PAGINATION_BRANCHES:
                obligations.append(_make_obligation(
                    row, "pagination", branch, "click" if branch != "每页条数" else "select",
                    f"在“{element}”所在列表实际执行分页分支“{branch}”，观察页码、条数、列表和边界状态。",
                ))
        if "modal" in traits:
            specialized = True
            for branch in MODAL_BRANCHES:
                obligations.append(_make_obligation(
                    row, "modal", branch, "click",
                    f"对“{element}”实际执行弹窗分支“{branch}”，观察弹窗、数据和恢复状态。",
                ))
        if "create" in traits:
            specialized = True
            obligations.append(_make_obligation(
                row, "crud", "创建成功", "click",
                f"使用本次 AI_TEST/CODEX_TEST 数据通过“{element}”完成真实创建，并验证列表或详情出现该数据。",
            ))
        if configurable and configuration_rows:
            specialized = True
            field_operation = canonical_interaction(row.get("交互方式", ""))
            if field_operation not in {"input", "select", "toggle", "upload", "click"}:
                field_operation = "click"
            for variant in sorted(configuration_rows, key=lambda item: item.get("变体ID", "")):
                variant_id = variant.get("变体ID", "")
                variant_value = variant.get("配置值/组合", "")
                obligations.append(_make_obligation(
                    row,
                    "configuration-variant",
                    f"{variant_id}:{variant_value}",
                    field_operation,
                    f"对“{element}”执行配置变体“{variant_value}”，在同一次事务中完成创建/保存、重新进入回显、持久化、实际生效及恢复/清理。",
                    requires_commit=True,
                    extra={
                        "configuration_variant_id": variant_id,
                        "configuration_value": variant_value,
                        "effective_timing": variant.get("生效时机", ""),
                        "execution_strategy": variant.get("执行策略", ""),
                        "test_data_id": variant.get("测试数据ID/名称", ""),
                    },
                ))
        elif "persistent_mutation" in traits and not configurable:
            specialized = True
            field_operation = canonical_interaction(row.get("交互方式", ""))
            if field_operation not in {"input", "select", "toggle", "upload", "click"}:
                field_operation = "click"
            obligations.append(_make_obligation(
                row,
                "mutation-effect",
                "修改成功并验证生效",
                field_operation,
                f"仅对本次创建测试数据真实修改“{element}”并保存，重新进入后验证回显、持久化和依赖功能实际生效。",
                requires_commit=True,
            ))
        if "delete" in traits:
            specialized = True
            for branch in ("取消删除数据不变", "确认删除成功"):
                obligations.append(_make_obligation(
                    row, "delete", branch, "click",
                    f"仅对本次创建测试数据通过“{element}”验证{branch}。",
                ))
        if not specialized:
            operation = canonical_interaction(row.get("交互方式", ""))
            if operation not in {"click", "toggle", "upload", "select", "input"}:
                operation = "click"
            obligations.append(_make_obligation(
                row, "interaction", "实际执行", operation,
                f"实际操作“{element}”，记录操作前状态、具体动作、操作后页面变化和恢复结果。",
            ))
    unique = {item["obligation_id"]: item for item in obligations}
    return sorted(unique.values(), key=lambda item: (item["page"], item["interaction_id"], item["kind"], item["branch"]))


def _control_root(run_dir: Path) -> Path:
    return run_dir.resolve() / CONTROL_DIR


def initialize_discovery_control(run_dir: Path) -> None:
    root = _control_root(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    config = root / CONFIG_FILE
    if not config.exists():
        atomic_write_text(config, json.dumps({
            "version": CONTROL_VERSION,
            "mode": "single-agent-left-shift",
            "created_at": _now(),
            "phase_freeze": "after-discovery-validation",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def discovery_control_enabled(run_dir: Path) -> bool:
    path = _control_root(run_dir) / CONFIG_FILE
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("version") == CONTROL_VERSION and data.get("mode") == "single-agent-left-shift"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} line {line_number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} line {line_number} must be an object")
        result.append(value)
    return result


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    atomic_write_text(
        path,
        existing + json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _upsert_interaction_branch_row(
    run_dir: Path,
    active: dict[str, Any],
    evidence_path: str,
    evidence_location: str,
    before_state: str,
    executed_action: str,
    observed_result: str,
    recovery_result: str,
) -> None:
    category_by_kind = {
        "input": "输入",
        "dynamic-selection": "动态选择",
        "pagination": "分页",
        "modal": "弹窗",
    }
    category = category_by_kind.get(str(active.get("kind", "")))
    if not category:
        return
    path = run_dir / "interaction-branch-observations.csv"
    if not path.is_file():
        raise ValueError("interaction-branch-observations.csv is missing")
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    required_headers = {
        "批次ID", "最小标题路径", "交互实例ID", "页面/入口", "元素名称/文案", "元素类型",
        "分支类别", "分支动作", "执行前状态", "执行动作", "执行后结果", "恢复结果",
        "操作步骤锚点", "预期结果锚点", "是否实际执行", "证据路径", "证据定位",
    }
    missing_headers = sorted(required_headers - set(headers))
    if missing_headers:
        raise ValueError(f"interaction-branch-observations.csv is missing columns: {missing_headers}")
    scope_rows = _read_csv(run_dir / "batch-status.csv")
    batch_id = scope_rows[0].get("批次ID", "") if scope_rows else ""
    branch = str(active.get("branch", "")).strip()
    interaction_id = str(active.get("interaction_id", "")).strip()
    row = {header: "" for header in headers}
    row.update({
        "批次ID": batch_id,
        "最小标题路径": str(active.get("leaf_path", "")).strip(),
        "交互实例ID": interaction_id,
        "页面/入口": str(active.get("page", "")).strip(),
        "元素名称/文案": str(active.get("element_name", "")).strip(),
        "元素类型": str(active.get("element_type", "")).strip(),
        "分支类别": category,
        "分支动作": branch,
        "执行前状态": before_state.strip(),
        "执行动作": executed_action.strip(),
        "执行后结果": observed_result.strip(),
        "恢复结果": recovery_result.strip(),
        "操作步骤锚点": executed_action.strip(),
        "预期结果锚点": observed_result.strip(),
        "是否实际执行": "是",
        "证据路径": evidence_path,
        "证据定位": evidence_location.strip(),
        "备注": "由 discovery-complete 根据已绑定的页面工具动作自动记录",
    })
    identity = (interaction_id, category, branch)
    normalize = lambda value: re.sub(r"\s+", "", str(value or "")).casefold()
    evidence_identity = (normalize(evidence_path), normalize(evidence_location))
    for existing in rows:
        existing_identity = (
            existing.get("交互实例ID", "").strip(),
            existing.get("分支类别", "").strip(),
            existing.get("分支动作", "").strip(),
        )
        if not existing_identity[0] or existing_identity == identity:
            continue
        if (normalize(existing.get("证据路径")), normalize(existing.get("证据定位"))) == evidence_identity:
            raise ValueError("current branch reuses an evidence path+location owned by another branch")
        if existing_identity[:2] == identity[:2]:
            if normalize(existing.get("执行动作")) == normalize(executed_action):
                raise ValueError("different branches of one control must describe different executed actions")
            if normalize(existing.get("执行后结果")) == normalize(observed_result):
                raise ValueError("different branches of one control must have different observed results")
    retained = [
        existing for existing in rows
        if existing.get("交互实例ID", "").strip()
        if (
            existing.get("交互实例ID", "").strip(),
            existing.get("分支类别", "").strip(),
            existing.get("分支动作", "").strip(),
        ) != identity
    ]
    retained.append(row)
    from io import StringIO
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(retained)
    atomic_write_text(path, "\ufeff" + output.getvalue(), encoding="utf-8")


def _record_configuration_variant_result(
    run_dir: Path,
    active: dict[str, Any],
    completion: dict[str, Any],
) -> None:
    if active.get("kind") != "configuration-variant":
        return
    path = run_dir / "configuration-variant-observations.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    variant_id = str(active.get("configuration_variant_id", ""))
    matched = 0
    for row in rows:
        if row.get("交互实例ID", "").strip() != active.get("interaction_id") or row.get("变体ID", "").strip() != variant_id:
            continue
        matched += 1
        row.update({
            "执行前状态": completion["before_state"],
            "实际配置动作": completion["executed_action"],
            "创建/保存结果": completion["commit_result"],
            "回显/持久化结果": completion["persistence_result"],
            "实际生效结果": completion["effect_result"],
            "恢复/清理结果": completion["recovery_result"],
            "是否实际执行": "是",
            "证据路径": completion["evidence_path"],
            "证据定位": completion["evidence_location"],
        })
    if matched != 1:
        raise ValueError(f"configuration variant {variant_id!r} must match exactly one ledger row")
    from io import StringIO
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(path, "\ufeff" + output.getvalue(), encoding="utf-8")


def _completion_map(run_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("obligation_id")): item
        for item in _read_jsonl(_control_root(run_dir) / COMPLETIONS_FILE)
        if item.get("obligation_id")
    }


def hook_runtime_status(run_dir: Path) -> dict[str, Any]:
    project_root = run_dir.resolve().parents[3]
    wrapper = project_root / ".codebuddy" / "hooks" / "run-discovery-recorder.cmd"
    settings = project_root / ".codebuddy" / "settings.json"
    configured = False
    if settings.is_file():
        try:
            configured = "run-discovery-recorder.cmd" in settings.read_text(encoding="utf-8")
        except OSError:
            configured = False
    return {
        "optional": True,
        "configured": configured,
        "wrapper_exists": wrapper.is_file(),
        "events_recorded": len(_read_jsonl(_control_root(run_dir) / EVENTS_FILE)),
        "fallback_modes": ["TRACE_VERIFIED", "ARTIFACT_VERIFIED"],
    }


def discovery_status(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    if not discovery_control_enabled(run_dir):
        return {
            "version": CONTROL_VERSION,
            "state": "DISCOVERY_CONTROL_DISABLED",
            "inventory_count": 0,
            "obligation_count": 0,
            "completed_count": 0,
            "pending_count": 0,
            "active": None,
            "active_events": [],
            "next_obligation": None,
            "pending_by_element": {},
        }
    inventory = _real_inventory_rows(run_dir)
    obligations = build_obligations(run_dir)
    completions = _completion_map(run_dir)
    pending = [item for item in obligations if item["obligation_id"] not in completions]
    configuration_issues = configuration_plan_issues(run_dir)
    root = _control_root(run_dir)
    active = None
    if (root / ACTIVE_FILE).is_file():
        active = json.loads((root / ACTIVE_FILE).read_text(encoding="utf-8"))
    active_events: list[dict[str, Any]] = []
    if active:
        first_sequence = int(active.get("first_event_sequence", 1))
        active_events = [
            item for item in _read_jsonl(root / EVENTS_FILE)
            if int(item.get("sequence", 0)) >= first_sequence
        ]
    state = (
        "INVENTORY_REQUIRED" if not inventory
        else "CONFIGURATION_VARIANT_PLAN_REQUIRED" if configuration_issues
        else "DISCOVERY_EXECUTION_REQUIRED" if pending
        else "DISCOVERY_EXECUTION_COMPLETE"
    )
    snapshot = {
        "version": CONTROL_VERSION,
        "state": state,
        "inventory_count": len(inventory),
        "obligation_count": len(obligations),
        "completed_count": len(obligations) - len(pending),
        "pending_count": len(pending),
        "active": active,
        "active_events": active_events,
        "next_obligation": pending[0] if pending else None,
        "pending_by_element": {},
        "completion_by_evidence_mode": {},
        "hook": hook_runtime_status(run_dir),
        "configuration_plan_issues": configuration_issues,
    }
    for item in completions.values():
        mode = str(item.get("evidence_mode") or "LEGACY_HOOK_VERIFIED")
        snapshot["completion_by_evidence_mode"][mode] = snapshot["completion_by_evidence_mode"].get(mode, 0) + 1
    for item in pending:
        snapshot["pending_by_element"].setdefault(item["interaction_id"], 0)
        snapshot["pending_by_element"][item["interaction_id"]] += 1
    atomic_write_text(
        root / OBLIGATIONS_FILE,
        json.dumps({**snapshot, "obligations": obligations}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshot


def begin_obligation(run_dir: Path, obligation_id: str) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    status = discovery_status(run_dir)
    if status["state"] == "CONFIGURATION_VARIANT_PLAN_REQUIRED":
        raise ValueError(
            "complete configuration-variant-observations.csv planning before executing page mutations: "
            + "; ".join(status.get("configuration_plan_issues", [])[:10])
        )
    obligations = {item["obligation_id"]: item for item in build_obligations(run_dir)}
    if obligation_id not in obligations:
        raise ValueError(f"unknown or stale discovery obligation: {obligation_id}")
    if obligation_id in _completion_map(run_dir):
        raise ValueError(f"discovery obligation is already complete: {obligation_id}")
    root = _control_root(run_dir)
    active_path = root / ACTIVE_FILE
    if active_path.exists():
        active = json.loads(active_path.read_text(encoding="utf-8"))
        if active.get("obligation_id") != obligation_id:
            raise ValueError(
                f"another discovery obligation is active: {active.get('obligation_id')}; complete or abort it first"
            )
        return active
    globally_active = [
        path for path in run_dir.parent.glob("*/artifacts/discovery-control/active-obligation.json")
        if path.resolve() != active_path.resolve()
    ]
    if globally_active:
        raise ValueError(f"another batch has an active discovery obligation: {globally_active[0]}")
    events = _read_jsonl(root / EVENTS_FILE)
    active = {
        **obligations[obligation_id],
        "started_at": _now(),
        "first_event_sequence": len(events) + 1,
    }
    atomic_write_text(active_path, json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return active


def abort_obligation(run_dir: Path, obligation_id: str, reason: str) -> None:
    if len(reason.strip()) < 8:
        raise ValueError("abort reason must be concrete and at least 8 characters")
    active_path = _control_root(run_dir) / ACTIVE_FILE
    if not active_path.is_file():
        raise ValueError("no active discovery obligation")
    active = json.loads(active_path.read_text(encoding="utf-8"))
    if active.get("obligation_id") != obligation_id:
        raise ValueError("active discovery obligation does not match")
    _append_jsonl(_control_root(run_dir) / "aborts.jsonl", {
        "obligation_id": obligation_id,
        "aborted_at": _now(),
        "reason": reason.strip(),
    })
    active_path.unlink()


def _validate_evidence(run_dir: Path, raw_path: str) -> Path:
    if not raw_path.strip():
        raise ValueError("evidence path is required")
    candidate = Path(raw_path)
    path = candidate.resolve() if candidate.is_absolute() else (run_dir / candidate).resolve()
    artifacts = (run_dir / "artifacts").resolve()
    try:
        path.relative_to(artifacts)
    except ValueError as exc:
        raise ValueError("evidence must be inside the current run-dir artifacts directory") from exc
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("evidence must be an existing non-empty file")
    return path


def _evidence_segment(run_dir: Path, stage: str, raw_path: str, location: str) -> dict[str, str]:
    path = _validate_evidence(run_dir, raw_path)
    if len(location.strip()) < 3:
        raise ValueError(f"{stage} evidence location must identify the concrete step/state")
    return {
        "stage": stage,
        "path": path.relative_to(run_dir).as_posix(),
        "location": location.strip(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _validate_hook_sequence(
    root: Path,
    active: dict[str, Any],
    before_record_id: str,
    mutation_record_id: str | list[str],
    after_record_id: str,
) -> tuple[list[str], str, str]:
    events = {str(item.get("record_id")): item for item in _read_jsonl(root / EVENTS_FILE)}
    mutation_record_ids = (
        [item.strip() for item in mutation_record_id if item.strip()]
        if isinstance(mutation_record_id, list)
        else [item.strip() for item in mutation_record_id.split(",") if item.strip()]
    )
    if not mutation_record_ids:
        raise ValueError("at least one mutation record id is required for HOOK_VERIFIED")
    try:
        before = events[before_record_id]
        mutations = [events[record_id] for record_id in mutation_record_ids]
        after = events[after_record_id]
    except KeyError as exc:
        raise ValueError(f"unknown action-event record id: {exc.args[0]}") from exc
    records = [before, *mutations, after]
    sequences = [int(record["sequence"]) for record in records]
    if sequences != sorted(set(sequences)):
        raise ValueError("completion must use records in read -> mutation(s) -> changed read order")
    if int(before["sequence"]) < int(active.get("first_event_sequence", 1)):
        raise ValueError("completion cannot reuse a page-tool record captured before discovery-begin")
    if before.get("operation_kind") != "read" or after.get("operation_kind") != "read":
        raise ValueError("before and after records must be successful page reads")
    if any(mutation.get("operation_kind") != "mutation" for mutation in mutations):
        raise ValueError("all middle records must be actual page mutations")
    required_operation = str(active.get("required_operation"))
    if not any(_operation_satisfies(required_operation, str(mutation.get("operation_name"))) for mutation in mutations):
        raise ValueError(
            f"mutation operations {[item.get('operation_name') for item in mutations]!r} do not satisfy required "
            f"operation {required_operation!r}"
        )
    if active.get("requires_commit"):
        field_indexes = [
            index for index, mutation in enumerate(mutations)
            if _operation_satisfies(required_operation, str(mutation.get("operation_name")))
        ]
        commit_indexes = [
            index for index, mutation in enumerate(mutations)
            if mutation.get("operation_name") in {"click", "other_mutation"}
        ]
        if not any(commit_index > field_index for field_index in field_indexes for commit_index in commit_indexes):
            raise ValueError("persistent mutation obligation must bind the field change and a later save/submit click")
    if any(item.get("response_error") or not item.get("response_nonempty") for item in records):
        raise ValueError("all bound page-tool records must be successful and non-empty")
    sessions = {(item.get("session_sha256"), item.get("transcript_sha256")) for item in records}
    if len(sessions) != 1:
        raise ValueError("bound page-tool records must come from one physical session and transcript")
    session_hash, transcript_hash = next(iter(sessions))
    if not session_hash or not transcript_hash or session_hash == EMPTY_SHA256 or transcript_hash == EMPTY_SHA256:
        raise ValueError("page-tool records must identify a non-empty physical session and transcript")
    if before.get("tool_response_sha256") == after.get("tool_response_sha256"):
        raise ValueError("before and after page reads are identical; a determinable page response change is required")
    return mutation_record_ids, str(session_hash), str(transcript_hash)


def _operation_satisfies(required: str, actual: str) -> bool:
    allowed = {
        "input": {"input"},
        "select": {"select", "click"},
        "expand": {"expand", "click", "select"},
        "click": {"click", "toggle", "other_mutation"},
        "toggle": {"toggle", "click"},
        "upload": {"upload", "input", "other_mutation"},
    }
    return actual in allowed.get(required, {required})


def complete_obligation(
    run_dir: Path,
    obligation_id: str,
    before_record_id: str = "",
    mutation_record_id: str | list[str] = "",
    after_record_id: str = "",
    evidence_path: str = "",
    evidence_location: str = "",
    before_state: str = "",
    executed_action: str = "",
    observed_result: str = "",
    recovery_result: str = "",
    *,
    evidence_mode: str = "auto",
    trace_evidence_path: str = "",
    trace_before_location: str = "",
    trace_action_location: str = "",
    trace_after_location: str = "",
    trace_recovery_location: str = "",
    before_evidence_path: str = "",
    before_evidence_location: str = "",
    after_evidence_path: str = "",
    after_evidence_location: str = "",
    recovery_evidence_path: str = "",
    recovery_evidence_location: str = "",
    effect_evidence_path: str = "",
    effect_evidence_location: str = "",
    commit_result: str = "",
    persistence_result: str = "",
    effect_result: str = "",
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    root = _control_root(run_dir)
    active_path = root / ACTIVE_FILE
    if not active_path.is_file():
        raise ValueError("no active discovery obligation; run discovery-begin first")
    active = json.loads(active_path.read_text(encoding="utf-8"))
    if active.get("obligation_id") != obligation_id:
        raise ValueError("active discovery obligation does not match")
    current_obligations = {item["obligation_id"] for item in build_obligations(run_dir)}
    if obligation_id not in current_obligations:
        raise ValueError("active discovery obligation became stale after the element inventory changed; abort and re-begin it")
    if obligation_id in _completion_map(run_dir):
        raise ValueError("discovery obligation is already complete")
    requested_mode = evidence_mode.strip().casefold() or "auto"
    if requested_mode not in {"auto", "hook", "trace", "artifact"}:
        raise ValueError("evidence mode must be auto, hook, trace, or artifact")
    auto_requested = requested_mode == "auto"
    if requested_mode == "auto":
        if before_record_id.strip() and after_record_id.strip() and mutation_record_id:
            requested_mode = "hook"
        elif trace_evidence_path.strip():
            requested_mode = "trace"
        else:
            requested_mode = "artifact"
    mutation_record_ids: list[str] = []
    session_hash = ""
    transcript_hash = ""
    hook_fallback_reason = ""
    segments: list[dict[str, str]] = []
    if requested_mode == "hook":
        try:
            mutation_record_ids, session_hash, transcript_hash = _validate_hook_sequence(
                root, active, before_record_id, mutation_record_id, after_record_id
            )
        except ValueError as exc:
            if not auto_requested:
                raise
            hook_fallback_reason = str(exc)
            if trace_evidence_path.strip():
                requested_mode = "trace"
            elif before_evidence_path.strip() and recovery_evidence_path.strip() and (
                after_evidence_path.strip() or evidence_path.strip()
            ):
                requested_mode = "artifact"
            else:
                raise ValueError(
                    f"Hook evidence failed and automatic fallback evidence is incomplete: {exc}"
                ) from exc
    verified_mode = f"{requested_mode.upper()}_VERIFIED"
    if requested_mode == "hook":
        segments.append(_evidence_segment(run_dir, "after", evidence_path, evidence_location))
    elif requested_mode == "trace":
        locations = [trace_before_location, trace_action_location, trace_after_location, trace_recovery_location]
        if any(len(item.strip()) < 3 for item in locations) or len({item.strip() for item in locations}) != 4:
            raise ValueError("TRACE_VERIFIED requires four distinct before/action/after/recovery locations")
        for stage, location in zip(("before", "action", "after", "recovery"), locations):
            segments.append(_evidence_segment(run_dir, stage, trace_evidence_path, location))
    else:
        artifact_inputs = [
            ("before", before_evidence_path, before_evidence_location),
            ("after", after_evidence_path or evidence_path, after_evidence_location or evidence_location),
            ("recovery", recovery_evidence_path, recovery_evidence_location),
        ]
        segments = [_evidence_segment(run_dir, *item) for item in artifact_inputs]
        identities = {(item["path"], item["location"]) for item in segments}
        if len(identities) != len(segments):
            raise ValueError("ARTIFACT_VERIFIED requires distinct before/after/recovery evidence states")
        image_segments = [item for item in segments if Path(item["path"]).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}]
        if len({item["sha256"] for item in image_segments}) != len(image_segments):
            raise ValueError("renamed/copied screenshots cannot represent different evidence states")
    effect_kinds = {"crud", "mutation-effect", "configuration-variant"}
    effect_required = active.get("kind") in effect_kinds or (
        active.get("kind") == "delete" and active.get("branch") == "确认删除成功"
    )
    if effect_required:
        effect_segment = _evidence_segment(run_dir, "effect", effect_evidence_path, effect_evidence_location)
        if (effect_segment["path"], effect_segment["location"]) in {
            (item["path"], item["location"]) for item in segments
        }:
            raise ValueError("actual effect evidence must identify a state distinct from save/after evidence")
        if Path(effect_segment["path"]).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} and any(
            item["sha256"] == effect_segment["sha256"] for item in segments
        ):
            raise ValueError("actual effect screenshot cannot reuse copied save/after evidence")
        segments.append(effect_segment)
        for label, value in {
            "commit result": commit_result,
            "persistence result": persistence_result,
            "effect result": effect_result,
        }.items():
            if len(value.strip()) < 4:
                raise ValueError(f"{label} must be concrete for CRUD/configuration effect closure")
    if len(before_state.strip()) < 4:
        raise ValueError("before state must be concrete and at least 4 characters")
    if len(executed_action.strip()) < 4:
        raise ValueError("executed action must be concrete and at least 4 characters")
    if len(observed_result.strip()) < 6:
        raise ValueError("observed result must be concrete and at least 6 characters")
    if len(recovery_result.strip()) < 4:
        raise ValueError("recovery result must be concrete and at least 4 characters")
    if active.get("kind") == "selection" and active.get("branch") == "枚举选项":
        if not _option_rows(run_dir, str(active.get("interaction_id"))):
            raise ValueError(
                "option enumeration cannot close until selection-option-observations.csv contains the observed options"
            )
    completion = {
        "version": CONTROL_VERSION,
        "obligation_id": obligation_id,
        "interaction_id": active.get("interaction_id"),
        "kind": active.get("kind"),
        "branch": active.get("branch"),
        "completed_at": _now(),
        "evidence_mode": verified_mode,
        "hook_fallback_reason": hook_fallback_reason,
        "evidence_segments": segments,
        "before_record_id": before_record_id if requested_mode == "hook" else "",
        "mutation_record_id": mutation_record_ids[0] if mutation_record_ids else "",
        "mutation_record_ids": mutation_record_ids,
        "after_record_id": after_record_id if requested_mode == "hook" else "",
        "session_sha256": session_hash,
        "transcript_sha256": transcript_hash,
        "evidence_path": next(item["path"] for item in segments if item["stage"] == "after"),
        "evidence_location": next(item["location"] for item in segments if item["stage"] == "after"),
        "before_state": before_state.strip(),
        "executed_action": executed_action.strip(),
        "observed_result": observed_result.strip(),
        "recovery_result": recovery_result.strip(),
        "commit_result": commit_result.strip(),
        "persistence_result": persistence_result.strip(),
        "effect_result": effect_result.strip(),
    }
    _upsert_interaction_branch_row(
        run_dir,
        active,
        completion["evidence_path"],
        completion["evidence_location"],
        before_state,
        executed_action,
        observed_result,
        recovery_result,
    )
    _record_configuration_variant_result(run_dir, active, completion)
    _append_jsonl(root / COMPLETIONS_FILE, completion)
    active_path.unlink()
    discovery_status(run_dir)
    return completion


def assert_discovery_execution_complete(run_dir: Path) -> None:
    """Final backstop used by the existing discovery phase validator."""
    if not discovery_control_enabled(run_dir):
        return  # Legacy runs remain resumable; every newly initialized run enables control.
    status = discovery_status(run_dir)
    if status["state"] == "INVENTORY_REQUIRED":
        raise ValueError("left-shift discovery requires page-element-inventory.csv before any interaction")
    if status["state"] == "CONFIGURATION_VARIANT_PLAN_REQUIRED":
        raise ValueError(
            "configuration variant plan is incomplete: " + "; ".join(status.get("configuration_plan_issues", [])[:10])
        )
    if status.get("active"):
        raise ValueError(
            f"left-shift discovery has an unfinished active obligation: {status['active'].get('obligation_id')}"
        )
    if status["pending_count"]:
        next_item = status["next_obligation"] or {}
        raise ValueError(
            "left-shift discovery execution is incomplete; run discovery-next and execute the returned obligation: "
            f"{next_item.get('interaction_id')}/{next_item.get('branch')} ({status['pending_count']} pending)"
        )
    for row in _configuration_rows(run_dir):
        required = [
            "执行前状态", "实际配置动作", "创建/保存结果", "回显/持久化结果",
            "实际生效结果", "恢复/清理结果", "证据路径", "证据定位",
        ]
        missing = [field for field in required if not row.get(field, "").strip()]
        if row.get("是否实际执行", "").strip() != "是" or missing:
            raise ValueError(
                f"configuration variant {row.get('变体ID')} is not a complete executed transaction; missing {missing}"
            )
