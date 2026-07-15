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
    if any(marker in context for marker in {"编辑", "修改", "配置", "状态", "启用", "停用", "edit", "update", "config"}):
        traits.add("persistent_mutation")
    if any(marker in direct for marker in {"删除", "移除", "delete", "remove"}):
        traits.add("delete")
    return traits


def _make_obligation(
    row: dict[str, str],
    kind: str,
    branch: str,
    operation: str,
    instruction: str,
    *,
    requires_commit: bool = False,
) -> dict[str, Any]:
    interaction_id = row.get("交互实例ID", "").strip()
    semantic_identity = normalized_control_identity(row)
    return {
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
        if "persistent_mutation" in traits:
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


def _completion_map(run_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("obligation_id")): item
        for item in _read_jsonl(_control_root(run_dir) / COMPLETIONS_FILE)
        if item.get("obligation_id")
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
    state = "INVENTORY_REQUIRED" if not inventory else ("DISCOVERY_EXECUTION_REQUIRED" if pending else "DISCOVERY_EXECUTION_COMPLETE")
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
    }
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
    before_record_id: str,
    mutation_record_id: str | list[str],
    after_record_id: str,
    evidence_path: str,
    evidence_location: str,
    before_state: str,
    executed_action: str,
    observed_result: str,
    recovery_result: str,
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
    events = {str(item.get("record_id")): item for item in _read_jsonl(root / EVENTS_FILE)}
    mutation_record_ids = (
        [item.strip() for item in mutation_record_id if item.strip()]
        if isinstance(mutation_record_id, list)
        else [item.strip() for item in mutation_record_id.split(",") if item.strip()]
    )
    if not mutation_record_ids:
        raise ValueError("at least one mutation record id is required")
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
    evidence = _validate_evidence(run_dir, evidence_path)
    if len(evidence_location.strip()) < 3:
        raise ValueError("evidence location must identify the concrete step/state")
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
        "before_record_id": before_record_id,
        "mutation_record_id": mutation_record_ids[0],
        "mutation_record_ids": mutation_record_ids,
        "after_record_id": after_record_id,
        "evidence_path": evidence.relative_to(run_dir).as_posix(),
        "evidence_location": evidence_location.strip(),
        "before_state": before_state.strip(),
        "executed_action": executed_action.strip(),
        "observed_result": observed_result.strip(),
        "recovery_result": recovery_result.strip(),
    }
    _upsert_interaction_branch_row(
        run_dir,
        active,
        completion["evidence_path"],
        evidence_location,
        before_state,
        executed_action,
        observed_result,
        recovery_result,
    )
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
