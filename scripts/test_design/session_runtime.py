# -*- coding: utf-8 -*-
"""Compact single-session runtime for fact-driven test design.

The runtime persists phase artifacts but does not orchestrate browser actions, create
per-element obligations, or retry phases.  A model explores the page continuously;
this module records complete transactions, compiles facts, and performs one final
cross-artifact audit.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "2.0"
EVENT_KINDS = {"scope", "page", "function", "element", "transaction", "test_object", "open_item"}
FACT_COLLECTIONS = {
    "page": "pages",
    "function": "functions",
    "element": "elements",
    "transaction": "transactions",
    "test_object": "test_objects",
    "open_item": "open_items",
}
NON_ACTIONABLE_STATUSES = {"absent", "disabled", "not_applicable", "superseded"}
INTERNAL_PROSE = re.compile(
    r"(?:^|[^a-z])(?:uid|uuid|fact[_ -]?id|element[_ -]?id|interaction[_ -]?id|"
    r"dom|accessibility tree|aria|selector|xpath|css selector)(?:$|[^a-z])",
    re.IGNORECASE,
)
SCREENSHOT_MARKERS = ("截图", "截屏", "screenshot", "screen shot")
PLACEHOLDER_MARKERS = (
    "TODO", "TBD", "待补充", "请补充", "示例数据", "占位", "输入测试数据", "填写测试数据",
)
NATURAL_PLACEHOLDER_PATTERN = re.compile(
    r"(?:公网|有效|合法|可用|测试)[\w\u4e00-\u9fff]{0,16}(?:地址|域名|主机名|账号|账户|密码|密钥|令牌|手机号)"
)
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
IPV4_PATTERN = re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])")
FACT_ID_PREFIXES = {
    "page": "PAGE",
    "function": "FN",
    "element": "EL",
    "transaction": "TX",
    "test_object": "OBJ",
    "open_item": "OPEN",
}
OPEN_ITEM_CATEGORIES = {"external_question", "blocked_condition", "observed_risk"}
ELEMENT_TYPE_ALIASES = {
    "input": {"input", "textbox", "text", "textarea", "number", "password", "输入", "输入框", "文本框", "文本输入框", "数字框", "密码框"},
    "select": {"select", "combobox", "dropdown", "listbox", "下拉框", "选择器", "下拉选择"},
    "trigger": {"button", "submit", "action", "按钮", "操作按钮", "提交按钮"},
    "toggle": {"switch", "checkbox", "radio", "开关", "复选框", "单选框"},
    "container": {"tab", "drawer", "dialog", "accordion", "页签", "抽屉", "弹窗", "折叠面板"},
}
NEGATIVE_INPUT_CLASSES = {"empty", "invalid", "invalid_format", "duplicate", "boundary_min", "boundary_max"}
ANGLE_PLACEHOLDER = re.compile(r"<[^<>\r\n]{1,80}>")
PRIORITY_ALIASES = {"最高": "P0", "高": "P1", "中": "P2", "低": "P3", "high": "P1", "medium": "P2", "low": "P3"}
EVENT_ENVELOPE_FIELDS = ("fact_id", "status", "client_ref", "local_ref")
REVIEW_SECTIONS = ("cases", "performance", "risks", "automation", "elements", "cross_sheet")
DATA_REFERENCE_PATTERN = re.compile(r"\bTEST_[A-Z][A-Z0-9_]{2,}\b")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _content_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _semantic_value(value: Any) -> Any:
    """Remove volatile clock metadata while retaining all business content."""
    if isinstance(value, dict):
        return {
            key: _semantic_value(item)
            for key, item in value.items()
            if not (
                str(key).lower() in {"timestamp", "generated_at", "created_at", "updated_at", "checked_at", "reviewed_at"}
                or str(key).lower().endswith("_timestamp")
            )
        }
    if isinstance(value, list):
        return [_semantic_value(item) for item in value]
    return value


def _semantic_content_digest(value: Any) -> str:
    return _content_digest(_semantic_value(value))


def _planning_fact_digest(facts: dict[str, Any]) -> str:
    return _content_digest({key: facts.get(key) for key in ("scope", "pages", "functions", "elements", "transactions", "test_objects")})


def _review_fact_digest(facts: dict[str, Any]) -> str:
    return _content_digest({
        key: facts.get(key)
        for key in ("scope", "pages", "functions", "elements", "transactions", "test_objects", "open_items")
    })


def semantic_source_digests(run_dir: Path) -> dict[str, str]:
    """Stable business digests; timestamps and JSON formatting never invalidate review."""
    facts = load_facts(run_dir)
    plan = load_plan(run_dir)
    cases = load_cases(run_dir)
    return {
        "facts": _review_fact_digest(facts),
        "plan": _semantic_content_digest(plan),
        "cases": _semantic_content_digest(cases),
    }


def artifact_paths(run_dir: Path) -> dict[str, Path]:
    run_dir = run_dir.resolve()
    return {
        "events": run_dir / "events.jsonl",
        "facts": run_dir / "facts.json",
        "plan": run_dir / "case-plan.json",
        "cases": run_dir / "function-cases.json",
        "review": run_dir / "review.json",
        "delivery": run_dir / "deliverables",
        "formal_workbook": run_dir / "deliverables" / "正式测试设计.xlsx",
        "import_workbook": run_dir / "deliverables" / "测试系统导入.xlsx",
        "diagnostics": run_dir / "diagnostics",
    }


def _prepare_event(event: dict[str, Any]) -> dict[str, Any]:
    item = dict(event)
    kind = str(item.get("kind", "")).strip()
    fact_id = str(item.get("fact_id", "")).strip()
    if kind not in EVENT_KINDS:
        raise ValueError(f"unsupported event kind: {kind!r}")
    if not fact_id:
        if kind == "scope":
            fact_id = "SCOPE"
        else:
            fact_id = f"{FACT_ID_PREFIXES[kind]}-{uuid.uuid4().hex[:10].upper()}"
    data = item.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("event.data must be an object")
    data = dict(data)
    if kind == "element":
        data = _normalize_element(data)
        # Exploration branches are a deterministic DFX decision made when the
        # element is discovered. Callers cannot supply or weaken this list.
        data["exploration_requirements"] = _derive_exploration_requirements(data)
    serialized_data = json.dumps(data, ensure_ascii=False)
    if URL_PATTERN.search(serialized_data) or IPV4_PATTERN.search(serialized_data):
        raise ValueError("event data must mask URLs and IP addresses before persistence")
    if kind == "transaction":
        checks = data.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("transaction.data.checks must be a non-empty array")
        for index, check in enumerate(checks, 1):
            if not isinstance(check, dict):
                raise ValueError(f"transaction check {index} must be an object")
            check = dict(check)
            if str(check.get("input_class", "")).strip():
                check["input_class"] = _normalize_input_class(check["input_class"])
            checks[index - 1] = check
            if not str(check.get("action", "")).strip() or not str(check.get("result", "")).strip():
                raise ValueError(f"transaction check {index} requires action and result")
            anchor = check.get("result_anchor")
            if not isinstance(anchor, dict) or not str(anchor.get("assertion", "")).strip():
                raise ValueError(f"transaction check {index} result_anchor requires an assertion")
            if anchor.get("value") in (None, "", []) and anchor.get("tokens") in (None, "", []):
                raise ValueError(f"transaction check {index} result_anchor requires observable value or tokens")
            primary_ref = str(check.get("element_ref", "")).strip()
            used_refs = check.get("used_element_refs")
            if used_refs is None:
                used_refs = [primary_ref] if primary_ref else []
            if not isinstance(used_refs, list) or any(not str(ref).strip() for ref in used_refs):
                raise ValueError(f"transaction check {index} used_element_refs must be a non-empty reference array")
            check["used_element_refs"] = list(dict.fromkeys(str(ref).strip() for ref in used_refs))
            if primary_ref and primary_ref not in check["used_element_refs"]:
                raise ValueError(f"transaction check {index} used_element_refs must contain element_ref")
            trigger_ref = str(check.get("trigger_element_ref", "")).strip()
            if trigger_ref and trigger_ref not in check["used_element_refs"]:
                raise ValueError(f"transaction check {index} trigger_element_ref must be one of used_element_refs")
        transaction_type = str(data.get("transaction_type", ""))
        if transaction_type in {"create", "edit", "delete", "configuration"}:
            if data.get("outcome") != "success" or not str(data.get("test_object_ref", "")).strip():
                raise ValueError(f"{transaction_type} transaction requires success outcome and test_object_ref")
        if transaction_type in {"create", "edit", "delete"}:
            required = ["commit_result", "effect_result"]
            if transaction_type in {"create", "edit"}:
                required.append("persistence_result")
            if transaction_type == "edit":
                required.append("recovery_result")
            missing = [
                field for field in required
                if not str(data.get(field, "")).strip()
                and not any(str(check.get(field, "")).strip() for check in checks)
            ]
            if missing:
                raise ValueError(f"{transaction_type} transaction lacks closure fields: {missing}")
        if transaction_type == "configuration":
            if data.get("combination") is True:
                raise ValueError("configuration transaction must use single-factor checks")
            incomplete = [
                index for index, check in enumerate(checks, 1)
                if any(not str(check.get(field, "")).strip() for field in ("commit_result", "persistence_result", "effect_result", "recovery_result"))
            ]
            if incomplete:
                raise ValueError(f"configuration checks lack save/reopen/effect/recovery closure: {incomplete}")
    if kind == "open_item":
        category = str(data.get("category", "")).strip()
        if category not in OPEN_ITEM_CATEGORIES:
            raise ValueError(f"open_item.data.category must be one of {sorted(OPEN_ITEM_CATEGORIES)}")
        if data.get("page_verifiable") is True:
            raise ValueError("page-verifiable content must be explored, not recorded as an open item")
    item["kind"] = kind
    item["fact_id"] = fact_id
    item["data"] = data
    item.setdefault("event_id", f"EVT-{uuid.uuid4().hex[:12].upper()}")
    item.setdefault("observed_at", _now())
    item.setdefault("status", "active")
    return item


def _normalize_event_envelope(event: dict[str, Any]) -> dict[str, Any]:
    """Accept a misplaced envelope field without persisting a ghost fact.

    This compatibility normalization runs only on a new caller payload. Stored
    events remain strict and deterministic. Conflicting values are rejected
    before anything is appended to ``events.jsonl``.
    """
    item = dict(event)
    data = item.get("data")
    if not isinstance(data, dict):
        return item
    normalized_data = dict(data)
    for field in EVENT_ENVELOPE_FIELDS:
        if field not in normalized_data:
            continue
        nested = normalized_data.pop(field)
        top = item.get(field)
        if top not in (None, "") and str(top) != str(nested):
            raise ValueError(f"event {field} conflicts between envelope and data")
        if top in (None, ""):
            item[field] = nested
    item["data"] = normalized_data
    return item


def _canonical_element_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    for canonical, aliases in ELEMENT_TYPE_ALIASES.items():
        if raw in aliases:
            return canonical
    return raw


def _normalize_input_class(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not normalized or normalized in NEGATIVE_INPUT_CLASSES or normalized.startswith("valid_"):
        return normalized
    return "valid" if normalized == "valid" else f"valid_{normalized}"


def _value_from_descriptor(value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        raw = next((value.get(key) for key in ("class", "value", "name", "id", "label") if value.get(key) not in (None, "")), "")
        description = str(value.get("description") or value.get("label") or "").strip()
        return str(raw).strip(), description
    return str(value).strip(), ""


def _normalize_element(element: dict[str, Any]) -> dict[str, Any]:
    data = dict(element)
    source_type = str(data.get("type", "")).strip()
    canonical = _canonical_element_type(source_type)
    if not canonical and (
        data.get("configuration") is True or data.get("option_set") == "finite" or bool(data.get("options"))
    ):
        canonical = "select"
    if canonical:
        data["type"] = canonical
    if source_type and canonical != source_type.lower():
        data.setdefault("source_type", source_type)

    options = data.get("options", [])
    if isinstance(options, (str, dict)):
        options = [options]
    normalized_options = []
    for option in options if isinstance(options, list) else []:
        value, _ = _value_from_descriptor(option)
        if value and value not in normalized_options:
            normalized_options.append(value)
    if normalized_options:
        data["options"] = normalized_options
        if not str(data.get("option_set", "")).strip():
            data["option_set"] = "finite"

    constraints = dict(data.get("constraints", {})) if isinstance(data.get("constraints"), dict) else {}
    configured = data.get("valid_input_classes", constraints.get("valid_input_classes", []))
    if isinstance(configured, (str, dict)):
        configured = [configured]
    normalized_classes: list[str] = []
    descriptions: dict[str, str] = {}
    for descriptor in configured if isinstance(configured, list) else []:
        value, description = _value_from_descriptor(descriptor)
        normalized = _normalize_input_class(value)
        if normalized and normalized not in normalized_classes:
            normalized_classes.append(normalized)
        if normalized and description:
            descriptions[normalized] = description
    if normalized_classes:
        data["valid_input_classes"] = normalized_classes
    if descriptions:
        data["valid_input_class_descriptions"] = descriptions

    raw_formats = data.get("input_formats", data.get("input_format", constraints.get("formats", constraints.get("format", []))))
    if isinstance(raw_formats, str):
        raw_formats = [raw_formats]
    input_formats = list(dict.fromkeys(str(value).strip().lower() for value in raw_formats if str(value).strip())) if isinstance(raw_formats, list) else []
    if input_formats:
        data["input_formats"] = input_formats

    known = canonical in ELEMENT_TYPE_ALIASES
    if data.get("interactive", True) is False:
        data["classification_status"] = "not_interactive"
    elif not known:
        data["classification_status"] = "unknown"
    elif canonical == "select" and not normalized_options and data.get("dynamic_options") is not True:
        data["classification_status"] = "incomplete"
    else:
        data["classification_status"] = "classified"
    return data


def _is_input_element(element: dict[str, Any]) -> bool:
    return _canonical_element_type(element.get("type")) == "input"


def _is_trigger_element(element: dict[str, Any]) -> bool:
    return _canonical_element_type(element.get("type")) == "trigger"


def _action_has_trigger(action: str) -> bool:
    return bool(re.search(r"点击|单击|提交|执行|保存|查询|搜索|确认|触发|click|submit|execute|run", action, re.IGNORECASE))


def _normalized_action(value: Any) -> str:
    return re.sub(r"[\s，。；、,.;:：]+", "", str(value or "")).lower()


def _default_option_action_complete(element: dict[str, Any], check: dict[str, Any]) -> bool:
    target = str(check.get("option_value", "")).strip()
    default = str(element.get("default_value", "")).strip()
    options = [str(value).strip() for value in element.get("options", []) if str(value).strip()]
    if not target or target != default or len(options) < 2:
        return True
    action = str(check.get("action", ""))
    target_position = action.rfind(target)
    return target_position >= 0 and any(
        option != target and 0 <= action.find(option) < target_position
        for option in options
    )


def _derive_exploration_requirements(element: dict[str, Any]) -> list[dict[str, Any]]:
    """Compile the small, element-local exploration plan before interaction."""
    requirements: list[dict[str, Any]] = []
    constraints = element.get("constraints") if isinstance(element.get("constraints"), dict) else {}
    if _is_input_element(element):
        configured_classes = element.get("valid_input_classes", constraints.get("valid_input_classes", []))
        if isinstance(configured_classes, str):
            configured_classes = [configured_classes]
        valid_classes = list(dict.fromkeys(_normalize_input_class(value) for value in configured_classes if str(value).strip()))
        for valid_class in valid_classes or ["valid"]:
            normalized = valid_class
            requirements.append({
                "kind": "input_class", "value": normalized, "strategy": "baseline",
                "reason": "页面语义、需求参考或模型推断得到的独立有效输入等价类",
                "independent_case": True,
            })
        if element.get("required") is True or constraints.get("required") is True:
            requirements.append({
                "kind": "input_class", "value": "empty", "strategy": "DFX",
                "dfx_code": "empty", "dfx_dimension": "DFT功能", "dfx_scenario": "必填项为空",
                "reason": "页面声明该输入为必填",
                "independent_case": True,
            })
        if element.get("input_formats") or element.get("input_format") or constraints.get("format") or constraints.get("formats") or constraints.get("pattern"):
            requirements.append({
                "kind": "input_class", "value": "invalid_format", "strategy": "DFX",
                "dfx_code": "invalid_format", "dfx_dimension": "DFT功能", "dfx_scenario": "无效输入格式",
                "reason": "页面语义、需求参考或模型推断表明该元素接受结构化输入",
                "independent_case": True,
            })
        if element.get("unique") is True or constraints.get("unique") is True:
            requirements.append({
                "kind": "input_class", "value": "duplicate", "strategy": "DFX",
                "dfx_code": "duplicate", "dfx_dimension": "DFR可靠", "dfx_scenario": "重复值",
                "reason": "页面声明了唯一性约束",
                "independent_case": True,
            })
        if element.get("min_value") is not None or element.get("min_length") is not None or constraints.get("min") is not None or constraints.get("min_length") is not None:
            requirements.append({
                "kind": "input_class", "value": "boundary_min", "strategy": "DFX",
                "dfx_code": "boundary", "dfx_dimension": "DFT功能", "dfx_scenario": "输入下边界",
                "reason": "页面声明了最小值或最小长度",
                "independent_case": True,
            })
        if element.get("max_value") is not None or element.get("max_length") is not None or constraints.get("max") is not None or constraints.get("max_length") is not None:
            requirements.append({
                "kind": "input_class", "value": "boundary_max", "strategy": "DFX",
                "dfx_code": "boundary", "dfx_dimension": "DFT功能", "dfx_scenario": "输入上边界",
                "reason": "页面声明了最大值或最大长度",
                "independent_case": True,
            })
    if element.get("option_set") == "finite":
        for option in element.get("options", []):
            is_configuration_baseline = element.get("configuration") is True and str(option) == str(element.get("default_value"))
            requirement = {
                "kind": "option_value", "value": str(option),
                "strategy": "baseline",
                "reason": "单因素配置的默认或未配置基线" if is_configuration_baseline else "页面呈现的独立有效选项",
                "independent_case": True,
            }
            requirements.append(requirement)
    default = element.get("default_value")
    if element.get("configuration") is True and default is not None and not any(
        item["kind"] == "option_value" and item["value"] == str(default) for item in requirements
    ):
        requirements.append({
            "kind": "option_value", "value": str(default), "strategy": "baseline",
            "reason": "单因素配置的默认或未配置基线",
        })
    return requirements


def _input_class_matches(required: str, observed: set[str]) -> bool:
    if required == "valid":
        return any(value == "valid" or value.startswith("valid_") for value in observed)
    return required in observed


def _requirement_is_observed(requirement: dict[str, Any], checks: list[dict[str, Any]]) -> bool:
    kind = str(requirement.get("kind", ""))
    value = str(requirement.get("value", ""))
    if kind == "input_class":
        return _input_class_matches(value, {
            str(check.get("input_class", "")).strip()
            for check in checks if str(check.get("input_class", "")).strip()
        })
    if kind == "option_value":
        return value in {str(check.get("option_value")) for check in checks if check.get("option_value") is not None}
    return False


def _matching_requirements(element: dict[str, Any], check: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        requirement for requirement in element.get("exploration_requirements", [])
        if _requirement_is_observed(requirement, [check])
    ]


def _validate_new_transactions(run_dir: Path, items: list[dict[str, Any]]) -> None:
    """Validate transaction structure without inventing late exploration work."""
    latest = {str(event.get("fact_id", "")): _prepare_event(event) for event in load_events(run_dir)}
    latest.update({str(item.get("fact_id", "")): item for item in items})
    elements = {
        fact_id: event.get("data", {})
        for fact_id, event in latest.items()
        if event.get("kind") == "element" and event.get("status", "active") not in NON_ACTIONABLE_STATUSES
    }
    for item in items:
        if item.get("kind") != "transaction":
            continue
        data = item["data"]
        checks = data.get("checks", [])
        declared = {str(ref) for ref in data.get("element_refs", []) if str(ref).strip()}
        if not declared:
            raise ValueError("transaction element_refs must list every control used by the business transaction")
        unknown = declared - set(elements)
        if unknown:
            raise ValueError(f"transaction references unknown elements: {sorted(unknown)}")
        used = {
            str(ref)
            for check in checks
            for ref in check.get("used_element_refs", [])
            if str(ref).strip()
        }
        missing_usage = declared - used
        if missing_usage:
            raise ValueError(f"transaction elements were declared but not actually used by any check: {sorted(missing_usage)}")
        independent_actions: dict[str, tuple[int, str, str]] = {}
        for index, check in enumerate(checks, 1):
            primary_ref = str(check.get("element_ref", "")).strip()
            primary_element = elements.get(primary_ref, {})
            independent = any(row.get("independent_case") for row in _matching_requirements(primary_element, check))
            action_key = _normalized_action(check.get("action"))
            if independent and action_key:
                previous = independent_actions.get(action_key)
                branch = str(check.get("input_class") or check.get("option_value") or "")
                if previous and (previous[1] != primary_ref or previous[2] != branch):
                    raise ValueError(
                        f"transaction independent checks {previous[0]} and {index} reuse the same physical action; "
                        "execute each primary element branch independently"
                    )
                independent_actions[action_key] = (index, primary_ref, branch)
            if primary_element.get("option_set") == "finite" and not _default_option_action_complete(primary_element, check):
                raise ValueError(
                    f"transaction check {index} must switch away from and then back to the default option before observing its effect"
                )
            used_refs = {str(value) for value in check.get("used_element_refs", [])}
            trigger_refs = [ref for ref in used_refs if ref in elements and _is_trigger_element(elements[ref])]
            if not trigger_refs:
                continue
            trigger_ref = str(check.get("trigger_element_ref", "")).strip()
            if not trigger_ref and len(trigger_refs) == 1 and _action_has_trigger(str(check.get("action", ""))):
                trigger_ref = trigger_refs[0]
                check["trigger_element_ref"] = trigger_ref
            if trigger_ref not in trigger_refs:
                raise ValueError(f"transaction check {index} must identify the trigger control that produces its result")
            if not _action_has_trigger(str(check.get("action", ""))):
                raise ValueError(f"transaction check {index} action omits its submit/execute trigger")


def ensure_run(
    run_dir: Path,
    module_path: str,
    product_name: str = "",
    source: str = "",
    **scope_fields: Any,
) -> dict[str, Any]:
    """Transparently create or resume the run bound to ``module_path``.

    This is an internal Skill bootstrap, not a user-visible workflow phase.
    """
    module_path = module_path.strip()
    if not module_path:
        raise ValueError("module_path must not be empty")
    paths = artifact_paths(run_dir)
    if paths["facts"].exists():
        facts = load_facts(run_dir)
        existing = str(facts.get("scope", {}).get("module_path", "")).strip()
        if existing != module_path:
            raise ValueError(f"run is bound to {existing!r}, not {module_path!r}; choose a new run directory")
        return facts["scope"]
    paths["events"].parent.mkdir(parents=True, exist_ok=True)
    if paths["events"].exists() and paths["events"].stat().st_size:
        facts = compile_facts(run_dir)
        existing = str(facts.get("scope", {}).get("module_path", "")).strip()
        if existing != module_path:
            raise ValueError(f"run is bound to {existing!r}, not {module_path!r}; choose a new run directory")
        return facts["scope"]
    scope_fields.pop("menu_path", None)
    scope = {
        "run_id": run_dir.name,
        "module_path": module_path,
        "product_name": product_name.strip(),
        "source": source.strip(),
        "created_at": _now(),
        **scope_fields,
    }
    append_events(run_dir, [{"kind": "scope", "data": scope}])
    compile_facts(run_dir)
    return scope


def append_event(run_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    return append_events(run_dir, [event])[0]


def append_events(run_dir: Path, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate a complete batch, resolve local aliases, then append it once.

    New facts receive runtime IDs. ``local_ref`` is batch-local; ``client_ref`` is
    a persistent exact key that is safe across interrupted batches. Existing facts
    are merged by fact_id/client_ref and are never fuzzy-matched.
    """
    paths = artifact_paths(run_dir)
    raw_items = [_normalize_event_envelope(dict(event)) for event in events]
    existing_events = load_events(run_dir)
    client_refs: dict[str, tuple[str, str]] = {}
    for existing in existing_events:
        client_ref = str(existing.get("client_ref", "")).strip()
        if client_ref:
            client_refs[client_ref] = (str(existing.get("fact_id", "")), str(existing.get("kind", "")))
    aliases: dict[str, str] = {key: value[0] for key, value in client_refs.items()}
    items: list[dict[str, Any]] = []
    generated_ids: set[str] = set()
    for raw in raw_items:
        alias = str(raw.pop("local_ref", "")).strip()
        client_ref = str(raw.get("client_ref", "")).strip()
        if "client_ref" in raw and not client_ref:
            raise ValueError("client_ref must be a non-empty exact key")
        existing_ref = client_refs.get(client_ref) if client_ref else None
        if existing_ref:
            if str(raw.get("kind", "")).strip() != existing_ref[1]:
                raise ValueError(f"client_ref {client_ref!r} is already bound to kind {existing_ref[1]!r}")
            provided = str(raw.get("fact_id", "")).strip()
            if provided and provided != existing_ref[0]:
                raise ValueError(f"client_ref {client_ref!r} is already bound to fact_id {existing_ref[0]!r}")
            raw["fact_id"] = existing_ref[0]
        provided_id = bool(str(raw.get("fact_id", "")).strip())
        item = _prepare_event(raw)
        if not provided_id:
            generated_ids.add(str(item["fact_id"]))
        if alias:
            if alias in aliases:
                raise ValueError(f"duplicate local_ref: {alias}")
            aliases[alias] = item["fact_id"]
        if client_ref:
            if client_ref in aliases and aliases[client_ref] != item["fact_id"]:
                raise ValueError(f"duplicate client_ref: {client_ref}")
            aliases[client_ref] = item["fact_id"]
            client_refs[client_ref] = (str(item["fact_id"]), str(item["kind"]))
        items.append(item)

    def resolve(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("@"):
            alias = value[1:]
            if alias not in aliases:
                raise ValueError(f"unknown local_ref: {alias}")
            return aliases[alias]
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        return value

    items = [resolve(item) for item in items]
    if not items:
        return []

    # A final scan is an observation made at a point in the interaction stream.
    # Stamp it with the transaction sequence so an unchanged page can still prove
    # it was rescanned after new actions, while an exact repeated submission remains
    # an idempotent no-op.
    transaction_sequence = sum(
        1 for existing in existing_events
        if _prepare_event(existing).get("kind") == "transaction"
    )
    for item in items:
        if item.get("kind") == "transaction":
            transaction_sequence += 1
        elif item.get("kind") == "page" and item.get("data", {}).get("final_scan_status") == "stable":
            item.setdefault("data", {})["final_scan_transaction_sequence"] = transaction_sequence

    # Exact resubmissions are successful no-ops.  This is deliberately strict:
    # only the same kind/status/data is absorbed; similar business prose is not
    # fuzzy-matched.  Provisional IDs are rewritten in later batch references so
    # local_ref remains safe when an earlier item is absorbed.
    latest: dict[str, dict[str, Any]] = {}
    for existing in existing_events:
        prepared = _prepare_event(existing)
        latest[str(prepared["fact_id"])] = prepared
    signatures = {
        _content_digest({"kind": item.get("kind"), "status": item.get("status", "active"), "data": item.get("data", {})}): fact_id
        for fact_id, item in latest.items()
    }
    replacements: dict[str, str] = {}

    def replace_refs(value: Any) -> Any:
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [replace_refs(item) for item in value]
        if isinstance(value, dict):
            return {key: replace_refs(item) for key, item in value.items()}
        return value

    appended: list[dict[str, Any]] = []
    returned: list[dict[str, Any]] = []
    for original in items:
        item = replace_refs(original)
        same_id = latest.get(str(item["fact_id"]))
        if same_id:
            if same_id.get("kind") != item.get("kind"):
                raise ValueError(f"fact_id {item['fact_id']!r} cannot change kind")
            merged_data = {**same_id.get("data", {}), **item.get("data", {})}
            if item.get("kind") == "scope":
                for stable_field in ("run_id", "created_at"):
                    if same_id.get("data", {}).get(stable_field) not in (None, ""):
                        merged_data[stable_field] = same_id["data"][stable_field]
            item = _prepare_event({**item, "data": merged_data})
        signature = _content_digest({"kind": item.get("kind"), "status": item.get("status", "active"), "data": item.get("data", {})})
        duplicate_id = (
            signatures.get(signature)
            if str(item["fact_id"]) in generated_ids and item.get("kind") not in {"function", "element"}
            else None
        )
        if duplicate_id:
            replacements[str(item["fact_id"])] = duplicate_id
            returned.append(latest[duplicate_id])
            continue
        if same_id and _content_digest({"kind": same_id.get("kind"), "status": same_id.get("status", "active"), "data": same_id.get("data", {})}) == signature:
            returned.append(same_id)
            continue
        appended.append(item)
        returned.append(item)
        latest[str(item["fact_id"])] = item
        signatures[signature] = str(item["fact_id"])

    if not appended:
        return returned
    _validate_new_transactions(run_dir, appended)
    paths["events"].parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in appended)
    with paths["events"].open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    return returned


def load_events(run_dir: Path) -> list[dict[str, Any]]:
    path = artifact_paths(run_dir)["events"]
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    result: list[dict[str, Any]] = []
    nonempty = [index for index, raw in enumerate(lines) if raw.strip()]
    last_nonempty = nonempty[-1] if nonempty else -1
    for index, raw in enumerate(lines):
        line_number = index + 1
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            if index == last_nonempty and not raw.endswith(("\n", "\r")):
                # A process can be interrupted during its final append.  Recover
                # only that truncated tail; corruption in any complete line fails.
                recovered = "".join(lines[:index])
                temporary = path.with_suffix(path.suffix + ".recover")
                temporary.write_text(recovered, encoding="utf-8", newline="\n")
                temporary.replace(path)
                break
            raise ValueError(f"invalid events.jsonl line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"events.jsonl line {line_number} must be an object")
        result.append(value)
    return result


def compile_facts(run_dir: Path) -> dict[str, Any]:
    events = load_events(run_dir)
    if not events:
        raise ValueError("events.jsonl has no scope event")
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        prepared = _prepare_event(event)
        latest[prepared["fact_id"]] = prepared
    scope_events = [item for item in latest.values() if item["kind"] == "scope"]
    if not scope_events:
        raise ValueError("events.jsonl has no scope event")
    facts: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scope": dict(scope_events[-1]["data"]),
        "source": "events.jsonl",
        "compiled_at": _now(),
        "event_count": len(events),
        "last_event_id": str(events[-1].get("event_id", "")),
        "fact_count": len(latest),
        "pages": [],
        "functions": [],
        "elements": [],
        "transactions": [],
        "test_objects": [],
        "open_items": [],
    }
    for fact_id, event in latest.items():
        kind = event["kind"]
        if kind == "scope" or event.get("status") == "superseded":
            continue
        record = {
            "fact_id": fact_id,
            "status": event.get("status", "active"),
            "observed_at": event.get("observed_at", ""),
            **event["data"],
        }
        facts[FACT_COLLECTIONS[kind]].append(record)
    # Dict replacement keeps the first discovery position of a fact while
    # allowing later events to update its content in place.
    _write_json(artifact_paths(run_dir)["facts"], facts)
    return facts


def load_facts(run_dir: Path, *, auto_rebuild: bool = True) -> dict[str, Any]:
    path = artifact_paths(run_dir)["facts"]
    if not path.exists():
        raise ValueError("facts.json does not exist")
    facts = _read_json(path)
    events = load_events(run_dir)
    last_event_id = str(events[-1].get("event_id", "")) if events else ""
    if facts.get("event_count") != len(events) or facts.get("last_event_id", "") != last_event_id:
        if auto_rebuild:
            return compile_facts(run_dir)
        raise ValueError("facts.json is stale")
    return facts


def load_plan(run_dir: Path) -> dict[str, Any]:
    path = artifact_paths(run_dir)["plan"]
    if not path.exists():
        raise ValueError("case-plan.json does not exist")
    value = _read_json(path)
    if not isinstance(value, dict):
        raise ValueError("case-plan.json must be an object")
    return value


def load_cases(run_dir: Path) -> dict[str, Any]:
    path = artifact_paths(run_dir)["cases"]
    if not path.exists():
        raise ValueError("function-cases.json does not exist")
    value = _read_json(path)
    if not isinstance(value, dict):
        raise ValueError("function-cases.json must be an object")
    return value


def _all_fact_ids(facts: dict[str, Any]) -> set[str]:
    return {
        str(row.get("fact_id", ""))
        for collection in FACT_COLLECTIONS.values()
        for row in facts.get(collection, [])
        if row.get("fact_id")
    }


def _transaction_check_refs(transaction: dict[str, Any]) -> set[str]:
    refs = {
        str(value)
        for check in transaction.get("checks", [])
        for value in check.get("used_element_refs", [])
        if str(value).strip()
    }
    refs.update(str(check.get("element_ref")) for check in transaction.get("checks", []) if check.get("element_ref"))
    return refs


def _pending_exploration(
    elements: list[dict[str, Any]], transactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    checks_by_element: dict[str, list[dict[str, Any]]] = {}
    for transaction in transactions:
        for check in transaction.get("checks", []):
            # An element's independent branch can only be completed when that
            # element is the primary verification target. Auxiliary use never
            # consumes another control's own exploration requirement.
            ref = str(check.get("element_ref", "")).strip()
            if ref:
                checks_by_element.setdefault(ref, []).append(check)
    pending: list[dict[str, Any]] = []
    for element in elements:
        if element.get("status") in NON_ACTIONABLE_STATUSES or element.get("interactive", True) is False:
            continue
        ref = str(element.get("fact_id", ""))
        missing = [
            requirement for requirement in element.get("exploration_requirements", [])
            if not _requirement_is_observed(requirement, checks_by_element.get(ref, []))
        ]
        if missing:
            pending.append({
                "element_ref": ref,
                "element_name": str(element.get("name", "")),
                "requirements": missing,
            })
    return pending


def pending_exploration_requirements(run_dir: Path) -> list[dict[str, Any]]:
    """Return only the remaining predeclared exploration branches."""
    latest: dict[str, dict[str, Any]] = {}
    for event in load_events(run_dir):
        prepared = _prepare_event(event)
        latest[str(prepared["fact_id"])] = prepared
    elements = [
        {"fact_id": item["fact_id"], "status": item.get("status", "active"), **item.get("data", {})}
        for item in latest.values() if item.get("kind") == "element"
    ]
    transactions = [
        {"fact_id": item["fact_id"], "status": item.get("status", "active"), **item.get("data", {})}
        for item in latest.values()
        if item.get("kind") == "transaction" and item.get("status", "active") not in NON_ACTIONABLE_STATUSES
    ]
    return _pending_exploration(elements, transactions)


def _final_scan_issues(run_dir: Path, facts: dict[str, Any]) -> list[dict[str, str]]:
    """Require one explicit stable page scan after that page's last transaction."""
    events = load_events(run_dir)
    element_pages = {
        str(element.get("fact_id", "")): str(element.get("page_ref", ""))
        for element in facts.get("elements", [])
    }
    function_pages: dict[str, set[str]] = {}
    for element in facts.get("elements", []):
        function_ref = str(element.get("function_ref", ""))
        page_ref = str(element.get("page_ref", ""))
        if function_ref and page_ref:
            function_pages.setdefault(function_ref, set()).add(page_ref)
    last_transaction_by_page: dict[str, int] = {}
    final_scan_by_page: dict[str, int] = {}
    explicit_unhandled: dict[str, Any] = {}
    for position, raw in enumerate(events):
        event = _prepare_event(raw)
        data = event.get("data", {})
        if event.get("kind") == "page" and data.get("final_scan_status") == "stable":
            page_ref = str(event.get("fact_id", ""))
            final_scan_by_page[page_ref] = position
            explicit_unhandled[page_ref] = data.get("unhandled_element_refs", None)
        elif event.get("kind") == "transaction" and event.get("status", "active") not in NON_ACTIONABLE_STATUSES:
            page_refs = set(function_pages.get(str(data.get("function_ref", "")), set()))
            for check in data.get("checks", []):
                for element_ref in check.get("used_element_refs", []):
                    if element_pages.get(str(element_ref)):
                        page_refs.add(element_pages[str(element_ref)])
            for page_ref in page_refs:
                last_transaction_by_page[page_ref] = position
    issues: list[dict[str, str]] = []
    for page in facts.get("pages", []):
        page_ref = str(page.get("fact_id", ""))
        final_position = final_scan_by_page.get(page_ref, -1)
        transaction_position = last_transaction_by_page.get(page_ref, -1)
        if final_position < 0:
            issues.append(_issue("missing_final_scan", "blocker", "facts.json", f"page {page_ref} has no explicit stable final scan", "record one final page update after all page transactions"))
        elif final_position <= transaction_position:
            issues.append(_issue("stale_final_scan", "blocker", "facts.json", f"page {page_ref} final scan predates its last transaction", "rescan the page once after the last transaction"))
        unhandled = explicit_unhandled.get(page_ref, None)
        if not isinstance(unhandled, list):
            issues.append(_issue("missing_final_scan_scope", "blocker", "facts.json", f"page {page_ref} final scan does not explicitly list unhandled elements", "record unhandled_element_refs as an explicit array"))
        elif unhandled:
            issues.append(_issue("unhandled_final_elements", "blocker", "facts.json", f"page {page_ref} still has unhandled elements: {unhandled}", "explore only those final-scan elements"))
    return issues


def inspect_discovery(run_dir: Path, facts: dict[str, Any] | None = None) -> list[dict[str, str]]:
    if facts is None:
        try:
            facts = load_facts(run_dir)
        except ValueError as exc:
            return [_issue("missing_fact", "blocker", "facts.json", str(exc), "resume the current discovery session")]
    issues: list[dict[str, str]] = []
    for key in ("pages", "functions", "elements", "transactions"):
        if not facts.get(key):
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"no {key} were recorded", "record the missing page fact once"))
    function_refs = {str(row.get("fact_id")) for row in facts.get("functions", [])}
    page_refs = {str(row.get("fact_id")) for row in facts.get("pages", [])}
    element_refs = {str(row.get("fact_id")) for row in facts.get("elements", [])}
    test_object_refs = {str(row.get("fact_id")) for row in facts.get("test_objects", [])}
    transaction_by_element: dict[str, list[dict[str, Any]]] = {ref: [] for ref in element_refs}
    for transaction in facts.get("transactions", []):
        for ref in _transaction_check_refs(transaction):
            transaction_by_element.setdefault(ref, []).append(transaction)
    for page in facts.get("pages", []):
        menu_path = page.get("menu_path")
        if not isinstance(menu_path, list) or not menu_path or any(not str(part).strip() for part in menu_path):
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"page {page['fact_id']} lacks its actual menu_path", "record the menu hierarchy observed during navigation"))
        if not str(page.get("name", "")).strip():
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"page {page['fact_id']} lacks its visible name", "record the visible page name"))
    issues.extend(_final_scan_issues(run_dir, facts))
    for element in facts.get("elements", []):
        ref = str(element.get("fact_id"))
        function_ref = str(element.get("function_ref", ""))
        if function_ref and function_ref not in function_refs:
            issues.append(_issue("broken_reference", "blocker", "facts.json", f"element {ref} references unknown function {function_ref}", "correct this element relation"))
        classification = str(element.get("classification_status", ""))
        if element.get("interactive", True) and classification in {"unknown", "incomplete", ""}:
            repair = "record the control's actual semantic type"
            if _canonical_element_type(element.get("type")) == "select":
                repair = "expand the control and record its finite options, or explicitly mark dynamic_options"
            issues.append(_issue(
                "unclassified_interactive_element", "blocker", "facts.json",
                f"interactive element {ref} is not completely classified",
                repair,
            ))
        if (
            element.get("interactive", True)
            and (_is_input_element(element) or element.get("option_set") == "finite")
            and not element.get("exploration_requirements")
        ):
            issues.append(_issue(
                "missing_exploration_plan", "blocker", "facts.json",
                f"interactive element {ref} has no predeclared exploration branches",
                "repair this element registration before continuing interaction",
            ))
        if (
            element.get("interactive", True)
            and element.get("status") not in NON_ACTIONABLE_STATUSES
            and not transaction_by_element.get(ref)
            and not element.get("exploration_requirements")
        ):
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"interactive element {ref} was not actually operated", "execute one related function transaction"))
        page_ref = str(element.get("page_ref", ""))
        if page_ref not in page_refs:
            issues.append(_issue("broken_reference", "blocker", "facts.json", f"element {ref} references unknown page {page_ref!r}", "bind the element to the observed page"))
        if element.get("dynamic") is True and not str(element.get("trigger_condition", "")).strip():
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"dynamic element {ref} lacks its trigger condition", "record the action or state that makes it appear"))
    for item in _pending_exploration(facts.get("elements", []), facts.get("transactions", [])):
        labels = [f"{row.get('kind')}={row.get('value')}" for row in item["requirements"]]
        issues.append(_issue(
            "pending_exploration", "blocker", "facts.json",
            f"element {item['element_ref']} still has predeclared exploration branches: {labels}",
            "continue the current page transaction with only these listed branches",
        ))
    for transaction in facts.get("transactions", []):
        kind = transaction.get("transaction_type")
        if kind in {"create", "edit", "configuration", "delete"}:
            if transaction.get("test_object_ref") not in test_object_refs:
                issues.append(_issue("broken_reference", "blocker", "facts.json", f"{kind} transaction {transaction['fact_id']} has no current-run test object", "bind the current-run test object"))
    independent_actions: dict[tuple[str, str], tuple[str, int, str, str]] = {}
    elements_by_ref = {str(row.get("fact_id", "")): row for row in facts.get("elements", [])}
    for transaction in facts.get("transactions", []):
        function_ref = str(transaction.get("function_ref", ""))
        for index, check in enumerate(transaction.get("checks", []), 1):
            element_ref = str(check.get("element_ref", ""))
            element = elements_by_ref.get(element_ref, {})
            if not any(row.get("independent_case") for row in _matching_requirements(element, check)):
                continue
            action_key = _normalized_action(check.get("action"))
            branch = str(check.get("input_class") or check.get("option_value") or "")
            key = (function_ref, action_key)
            previous = independent_actions.get(key)
            if action_key and previous and (previous[2] != element_ref or previous[3] != branch):
                issues.append(_issue(
                    "reused_independent_action", "blocker", "facts.json",
                    f"independent checks {(previous[0], previous[1])} and {(transaction.get('fact_id'), index)} reuse one physical action",
                    "execute and record the later primary branch independently", function_ref=function_ref,
                ))
            elif action_key:
                independent_actions[key] = (str(transaction.get("fact_id", "")), index, element_ref, branch)
            if element.get("option_set") == "finite" and not _default_option_action_complete(element, check):
                issues.append(_issue(
                    "default_option_not_reselected", "blocker", "facts.json",
                    f"check {(transaction.get('fact_id'), index)} does not switch away from and back to its default option",
                    "repeat only the default option branch with an observable switch-away-and-back action", function_ref=function_ref,
                ))
    for item in facts.get("open_items", []):
        if item.get("page_verifiable") is True:
            issues.append(_issue("invalid_open_item", "blocker", "facts.json", f"open item {item['fact_id']} can be verified on the page", "operate the page and update this item instead of asking the user"))
        affected = item.get("affected_function_refs")
        if not isinstance(affected, list) or not affected:
            issues.append(_issue("missing_scope", "repairable", "facts.json", f"open item {item['fact_id']} has no affected functions", "scope it to the functions whose expected results are affected"))
        elif any(str(ref) not in function_refs for ref in affected):
            issues.append(_issue("broken_reference", "repairable", "facts.json", f"open item {item['fact_id']} references an unknown function", "repair only its affected function list"))
    for test_object in facts.get("test_objects", []):
        if test_object.get("owner") == "current_run" and test_object.get("state") not in {"cleaned", "deleted", "retained_for_followup"}:
            issues.append(_issue("unsafe_lifecycle", "blocker", "facts.json", f"current-run test object {test_object['fact_id']} has no safe final state", "clean it up or explicitly retain it for follow-up"))
    return issues


def _group_issues(issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for issue in issues:
        key = (str(issue.get("severity", "")), str(issue.get("local_repair", "")))
        group = groups.setdefault(key, {
            "severity": key[0], "repair": key[1], "count": 0, "messages": [],
        })
        group["count"] += 1
        group["messages"].append(str(issue.get("message", "")))
    return list(groups.values())


def checkpoint_facts(run_dir: Path) -> dict[str, Any]:
    """Compile once at a page/session checkpoint and persist one grouped readiness summary."""
    facts = compile_facts(run_dir)
    issues = inspect_discovery(run_dir, facts)
    checkpoint = {
        "checked_at": _now(),
        "fact_digest": _planning_fact_digest(facts),
        "ready": not issues,
        "issue_groups": _group_issues(issues),
    }
    facts["checkpoint"] = checkpoint
    _write_json(artifact_paths(run_dir)["facts"], facts)
    return checkpoint


def _current_checkpoint(facts: dict[str, Any]) -> dict[str, Any]:
    checkpoint = facts.get("checkpoint") if isinstance(facts.get("checkpoint"), dict) else {}
    if checkpoint.get("fact_digest") != _planning_fact_digest(facts):
        return {"ready": False, "issue_groups": [], "reason": "checkpoint is missing or stale"}
    return checkpoint


def _dfx_hints(element: dict[str, Any]) -> list[dict[str, Any]]:
    hints = [
        {
            "code": str(requirement.get("dfx_code", "")),
            "scope": "element",
            "scope_ref": str(element.get("fact_id", "")),
            "reason": str(requirement.get("reason", "")),
            "dfx_dimension": str(requirement.get("dfx_dimension", "")),
            "dfx_scenario": str(requirement.get("dfx_scenario", "")),
            "requirement_kind": str(requirement.get("kind", "")),
            "requirement_value": str(requirement.get("value", "")),
        }
        for requirement in element.get("exploration_requirements", [])
        if requirement.get("strategy") == "DFX" and requirement.get("dfx_code")
    ]
    return hints


def _function_dfx_hints(elements: list[dict[str, Any]], transactions: list[dict[str, Any]], function_ref: str) -> list[dict[str, Any]]:
    hints = [hint for element in elements for hint in _dfx_hints(element)]
    if any(element.get("role_constraint") or (element.get("constraints", {}).get("role") if isinstance(element.get("constraints"), dict) else None) for element in elements):
        hints.append({"code": "permission", "scope": "function", "scope_ref": function_ref, "reason": "角色权限差异"})
    if any(element.get("state_constraint") or (element.get("constraints", {}).get("state") if isinstance(element.get("constraints"), dict) else None) for element in elements):
        hints.append({"code": "state", "scope": "function", "scope_ref": function_ref, "reason": "业务状态差异"})
    for transaction in transactions:
        if str(transaction.get("transaction_type", "")) in {"create", "edit", "configuration", "delete"}:
            hints.append({
                "code": "lifecycle", "scope": "transaction", "scope_ref": str(transaction.get("fact_id", "")),
                "reason": "持久化、实际生效与恢复/清理",
            })
    return list({json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in hints}.values())


def _verification_focus_hint(element: dict[str, Any], check: dict[str, Any]) -> str:
    """Build one factual focus hint without inventing business semantics."""
    element_name = str(element.get("name") or check.get("element_ref") or "当前交互").strip()
    branch = str(check.get("input_class") or check.get("option_value") or "已实探分支").strip()
    anchor = check.get("result_anchor") if isinstance(check.get("result_anchor"), dict) else {}
    raw_result = anchor.get("stable_tokens", anchor.get("tokens"))
    if raw_result in (None, "", []):
        raw_result = anchor.get("value") or check.get("result") or "页面实际观察结果"
    if isinstance(raw_result, list):
        result = "、".join(str(value).strip() for value in raw_result if str(value).strip())
    else:
        result = str(raw_result).strip()
    return f"验证{element_name}在{branch}下产生{result}"


def _branch_action_intent(element: dict[str, Any], requirement: dict[str, Any]) -> str:
    kind = str(requirement.get("kind", ""))
    value = str(requirement.get("value", ""))
    name = str(element.get("name") or "当前控件")
    if kind == "option_value":
        options = [str(option) for option in element.get("options", [])]
        default = str(element.get("default_value", ""))
        if value == default and any(option != value for option in options):
            return f"先切换到其他安全选项，再切回{value}，观察选中状态及实际功能效果"
        return f"从当前状态切换到{value}，观察选中状态及实际功能效果"
    labels = {
        "empty": "清空输入并触发功能，观察拦截提示及数据未提交",
        "invalid_format": "输入明确的无效格式并触发功能，观察格式校验及数据未提交",
        "boundary_min": "输入已声明的下边界并触发功能，观察边界处理结果",
        "boundary_max": "输入已声明的上边界并触发功能，观察边界处理结果",
    }
    return labels.get(value, f"在{name}中使用{value}并触发功能，观察该输入类别的实际效果")


def _observable_wait_refs(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only facts that actually observed waiting, timing or timeout state.

    A trigger reference merely proves that an action was submitted; it is not
    performance evidence by itself.
    """
    refs: list[dict[str, Any]] = []
    for transaction in facts.get("transactions", []):
        transaction_type = str(transaction.get("transaction_type", "")).strip().lower()
        for index, check in enumerate(transaction.get("checks", []), 1):
            has_state = bool(check.get("intermediate_states")) or bool(str(check.get("completion_state", "")).strip())
            has_timing = any(
                check.get(field) not in (None, "", [])
                for field in ("observed_duration", "elapsed_time", "started_at", "completed_at", "timeout_state")
            )
            is_long_running = transaction_type in {"async", "asynchronous", "long_task", "long-running", "batch"}
            if has_state or has_timing or is_long_running:
                refs.append({
                    "function_ref": str(transaction.get("function_ref", "")),
                    "transaction_ref": str(transaction.get("fact_id", "")),
                    "check_index": index,
                })
    return refs


def _has_observable_wait(facts: dict[str, Any]) -> bool:
    return bool(_observable_wait_refs(facts))


def _risk_theme(value: Any) -> str:
    """Group common risk causes without product- or page-specific wording."""
    text = re.sub(r"\s+", "", str(value or "")).lower()
    themes = (
        ("response_dependency", ("响应", "波动", "超时", "延迟", "不可达", "不可用", "response", "timeout", "latency", "unavailable")),
        ("permission", ("权限", "越权", "permission", "authorization")),
        ("consistency", ("一致性", "持久化", "幂等", "consistency", "persistence", "idempot")),
        ("data", ("数据", "造数", "样本", "data")),
        ("security", ("安全", "泄露", "注入", "security", "leak", "injection")),
        ("compatibility", ("兼容", "浏览器", "分辨率", "compatib", "browser")),
    )
    for theme, tokens in themes:
        if any(token in text for token in tokens):
            return theme
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _deduplicate_risks(rows: list[dict[str, Any]], function_names: dict[str, str]) -> list[dict[str, Any]]:
    """Merge the same semantic risk and retain every affected function."""
    merged: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = json.loads(json.dumps(raw, ensure_ascii=False))
        dimension = str(row.get("dfx_dimension") or row.get("type") or "风险").strip()
        key = (dimension, _risk_theme(row.get("description")))
        current = by_key.get(key)
        function_ref = str(row.get("function_ref", "")).strip()
        affected = [str(value) for value in row.get("affected_function_refs", []) if str(value).strip()]
        if function_ref:
            affected.append(function_ref)
        affected = list(dict.fromkeys(affected))
        if current is None:
            row["affected_function_refs"] = affected
            by_key[key] = row
            merged.append(row)
            continue
        current_refs = [str(value) for value in current.get("affected_function_refs", []) if str(value).strip()]
        current["affected_function_refs"] = list(dict.fromkeys(current_refs + affected))
        impacts = [str(current.get("impact", "")).strip(), str(row.get("impact", "")).strip()]
        impacts.extend(function_names.get(ref, ref) for ref in current["affected_function_refs"])
        current["impact"] = "；".join(dict.fromkeys(value for value in impacts if value))
        if not str(current.get("function_ref", "")).strip() or not function_ref or current.get("function_ref") != function_ref:
            current.pop("function_ref", None)
            current.pop("requirement_id", None)
    return merged


def _has_quantified_time_target(value: Any) -> bool:
    text = str(value or "")
    return bool(re.search(r"\d+(?:\.\d+)?\s*(?:ms|毫秒|s|sec(?:ond)?s?|秒|min(?:ute)?s?|分钟|h(?:our)?s?|小时)", text, re.IGNORECASE))


def _substantive_stability_risk(value: Any) -> bool:
    text = re.sub(r"\s+", "", str(value or "")).lower()
    if not text:
        return False
    benign = {"无", "无风险", "无已知风险", "无已知稳定性风险", "不适用", "none", "n/a", "na"}
    return text not in benign


def build_plan_skeleton(run_dir: Path) -> dict[str, Any]:
    """Build the factual planning input without creating another persisted artifact."""
    facts = load_facts(run_dir)
    pages = {str(row["fact_id"]): row for row in facts.get("pages", [])}
    elements = facts.get("elements", [])
    transactions = facts.get("transactions", [])
    functions: list[dict[str, Any]] = []
    for function in facts.get("functions", []):
        function_ref = str(function["fact_id"])
        owned_elements = [row for row in elements if str(row.get("function_ref", "")) == function_ref]
        owned_transactions = [row for row in transactions if str(row.get("function_ref", "")) == function_ref]
        page_refs = sorted({str(row.get("page_ref")) for row in owned_elements if str(row.get("page_ref", "")) in pages})
        checks = [
            {
                "transaction_ref": str(transaction["fact_id"]),
                "check_index": index,
                "action": str(check.get("action", "")),
                "observed_result": str(check.get("result", "")),
                "element_ref": str(check.get("element_ref", "")),
                "used_element_refs": list(check.get("used_element_refs", [])),
                "input_class": str(check.get("input_class", "")),
                "option_value": str(check.get("option_value", "")),
                "result_anchor": check.get("result_anchor", {}),
                "intermediate_states": check.get("intermediate_states", []),
                "completion_state": check.get("completion_state", ""),
            }
            for transaction in owned_transactions
            for index, check in enumerate(transaction.get("checks", []), 1)
        ]
        element_map = {str(row.get("fact_id", "")): row for row in owned_elements}
        required_case_branches: list[dict[str, Any]] = []
        for transaction in owned_transactions:
            transaction_ref = str(transaction["fact_id"])
            for index, check in enumerate(transaction.get("checks", []), 1):
                element_ref = str(check.get("element_ref", ""))
                element = element_map.get(element_ref, {})
                for requirement in _matching_requirements(element, check):
                    required_case_branches.append({
                        "kind": str(requirement.get("kind", "")),
                        "element_ref": element_ref,
                        "value": str(requirement.get("value", "")),
                        "strategy": str(requirement.get("strategy", "baseline")).lower(),
                        "dfx_dimension": str(requirement.get("dfx_dimension", "DFT功能")),
                        "dfx_scenario": str(requirement.get("dfx_scenario", "正向流程")),
                        "independent_case": bool(requirement.get("independent_case")),
                        "verification_focus_hint": _verification_focus_hint(element, check),
                        "action_intent_hint": _branch_action_intent(element, requirement),
                        "related_check": {"transaction_ref": transaction_ref, "check_index": index},
                    })
        unique_hints = _function_dfx_hints(owned_elements, owned_transactions, function_ref)
        for hint in unique_hints:
            related_checks: list[dict[str, Any]] = []
            for transaction in owned_transactions:
                transaction_ref = str(transaction.get("fact_id", ""))
                if hint.get("scope") == "transaction" and hint.get("scope_ref") != transaction_ref:
                    continue
                for index, check in enumerate(transaction.get("checks", []), 1):
                    primary_ref = str(check.get("element_ref", ""))
                    if hint.get("scope") == "element" and str(hint.get("scope_ref", "")) != primary_ref:
                        continue
                    tags = {str(tag) for tag in check.get("dfx_tags", [])}
                    hint_code = str(hint.get("code", ""))
                    requirement_kind = str(hint.get("requirement_kind", ""))
                    requirement_value = str(hint.get("requirement_value", ""))
                    if requirement_kind == "input_class" and not _input_class_matches(
                        requirement_value, {str(check.get("input_class", ""))},
                    ):
                        continue
                    if requirement_kind == "option_value" and str(check.get("option_value", "")) != requirement_value:
                        continue
                    if not requirement_kind and tags and hint_code not in tags:
                        continue
                    related_checks.append({"transaction_ref": transaction_ref, "check_index": index})
            hint["related_checks"] = related_checks
        functions.append({
            "function_ref": function_ref,
            "name": str(function.get("name", "")),
            "page_refs": page_refs,
            "elements": [{key: row.get(key) for key in ("fact_id", "name", "type", "constraints", "trigger_condition", "options") if row.get(key) is not None} for row in owned_elements],
            "transactions": [{"transaction_ref": str(row["fact_id"]), "transaction_type": row.get("transaction_type"), "checks": len(row.get("checks", []))} for row in owned_transactions],
            "checks": checks,
            "required_case_branches": required_case_branches,
            "dfx_hints": unique_hints,
            "design_context_fields": [
                "user_goal", "role", "business_value", "acceptance_criteria", "business_rules",
                "dependencies", "postcondition", "basis",
            ],
            "automation_profile_fields": [
                "level", "dependency", "stability_risk", "recommendation",
            ],
            "automation_rule": "页面交互用例使用UI层；只有实际测试入口是接口、命令行或组件时才使用对应层级",
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "facts.json",
        "checkpoint": _current_checkpoint(facts),
        "specialist_decisions": {
            "performance": {
                "observable_wait": _has_observable_wait(facts),
                "observed_wait_refs": _observable_wait_refs(facts),
                "rule": "按钮或提交动作本身不是性能依据；只有实测加载、异步、长任务、超时、耗时或需求性能目标时写场景，未提供量化目标时不得发明秒数",
            },
            "risk": {
                "observed_open_items": [
                    {key: row.get(key) for key in ("fact_id", "category", "description", "affected_function_refs") if row.get(key) not in (None, "", [])}
                    for row in facts.get("open_items", [])
                    if row.get("status") not in {"resolved", "accepted", "closed"}
                ],
                "rule": "从外部依赖、稳定性、权限、数据、状态一致性、超时和实探异常聚合去重；存在具体风险时不得写无风险",
            },
        },
        "case_intent_rule": "每个Case填写唯一verification_focus；它描述主验证对象、独立分支和可观察效果，辅助控件不能替代主验证",
        "dfx_evaluation": {
            "candidate_dimensions": ["DFT功能", "DFB业务", "DFS安全", "DFR可靠", "DFU可用", "DFI接口", "DFC兼容", "DFP性能", "DFM维护", "DFD部署", "DFO运维", "DFX极端"],
            "rule": "结合事实和功能语义只选择适用维度；页面可执行项进入用例，专项进入性能或风险，不生成固定矩阵",
        },
        "functions": functions,
    }


def _nonempty_text_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(str(item).strip() for item in value)


def _has_requirement_reference(facts: dict[str, Any]) -> bool:
    scope = facts.get("scope", {})
    source = str(scope.get("source", ""))
    return bool(
        scope.get("requirements") or scope.get("requirement_name") or scope.get("requirement_document")
        or "需求" in source or "文档" in source
    )


def inspect_plan(run_dir: Path) -> list[dict[str, str]]:
    try:
        facts, plan = load_facts(run_dir), load_plan(run_dir)
    except ValueError as exc:
        return [_issue("missing_artifact", "blocker", "case-plan.json", str(exc), "create the missing artifact from facts")]
    issues: list[dict[str, str]] = []
    if plan.get("source") != "facts.json":
        issues.append(_issue("invalid_source", "repairable", "case-plan.json", "plan source must be facts.json", "repair the source declaration"))
    if plan.get("source_digest") != _planning_fact_digest(facts):
        issues.append(_issue("stale_source", "repairable", "case-plan.json", "plan no longer matches planning facts", "repair only functions affected by changed facts"))
    fact_ids = _all_fact_ids(facts)
    functions = {str(row["fact_id"]): row for row in facts.get("functions", [])}
    transactions = {str(row["fact_id"]): row for row in facts.get("transactions", [])}
    elements = {str(row["fact_id"]): row for row in facts.get("elements", [])}
    pages = {str(row["fact_id"]): row for row in facts.get("pages", [])}
    planned_functions: set[str] = set()
    case_ids: set[str] = set()
    titles: set[tuple[str, str]] = set()
    focuses: set[tuple[str, str]] = set()
    profile_stability_risks: list[tuple[str, str]] = []
    planned_cases: dict[str, dict[str, Any]] = {}
    for function in plan.get("functions", []):
        function_ref = str(function.get("function_ref", ""))
        if function_ref not in functions:
            issues.append(_issue("broken_reference", "repairable", "case-plan.json", f"unknown function {function_ref!r}", "repair this function mapping", function_ref=function_ref))
        if function_ref in planned_functions:
            issues.append(_issue("duplicate_mapping", "repairable", "case-plan.json", f"function {function_ref} appears more than once", "merge this function block", function_ref=function_ref))
        planned_functions.add(function_ref)
        context = function.get("design_context") if isinstance(function.get("design_context"), dict) else {}
        for field in ("user_goal", "role", "business_value", "postcondition"):
            if not str(context.get(field, "")).strip():
                issues.append(_issue("missing_design_context", "repairable", "case-plan.json", f"function {function_ref} lacks design_context.{field}", "complete this function's compact design context", function_ref=function_ref))
        for field in ("acceptance_criteria", "business_rules", "dependencies", "basis"):
            if not _nonempty_text_list(context.get(field)):
                issues.append(_issue("missing_design_context", "repairable", "case-plan.json", f"function {function_ref} lacks design_context.{field}", "complete this function's compact design context", function_ref=function_ref))
        if not _has_requirement_reference(facts) and any(
            "需求" in str(item) or "文档" in str(item) for item in context.get("basis", [])
        ):
            issues.append(_issue("unsupported_basis", "repairable", "case-plan.json", f"function {function_ref} claims a requirement document that was not supplied", "retain only actual page exploration or supplied sources", function_ref=function_ref))
        profile = function.get("automation_profile") if isinstance(function.get("automation_profile"), dict) else {}
        if any(not str(profile.get(field, "")).strip() for field in ("level", "dependency", "stability_risk", "recommendation")):
            issues.append(_issue("missing_automation_profile", "repairable", "case-plan.json", f"function {function_ref} lacks a compact automation profile", "complete the four function-level automation fields", function_ref=function_ref))
        level = str(profile.get("level", "")).strip().lower()
        if level and level not in {"ui", "页面", "手工", "manual", "不适用", "none", "n/a", "na"}:
            issues.append(_issue(
                "automation_entry_mismatch", "repairable", "case-plan.json",
                f"function {function_ref} is a page interaction but automation level is {profile.get('level')!r}",
                "use UI for the executable page case; assess lower-layer automation separately",
                function_ref=function_ref,
            ))
        if _substantive_stability_risk(profile.get("stability_risk")):
            profile_stability_risks.append((function_ref, str(profile.get("stability_risk"))))
        cases = function.get("cases", [])
        if not cases or not any(str(case.get("strategy", "")).lower() == "baseline" for case in cases):
            issues.append(_issue("missing_baseline", "repairable", "case-plan.json", f"function {function_ref} lacks a baseline intent", "add its observed baseline intent", function_ref=function_ref))
        for case in cases:
            case_id = str(case.get("case_id", "")).strip()
            if not case_id or case_id in case_ids:
                issues.append(_issue("invalid_case_id", "repairable", "case-plan.json", f"empty or duplicate case ID {case_id!r}", "assign one stable case ID", function_ref=function_ref, case_id=case_id))
            case_ids.add(case_id)
            planned_cases[case_id] = case
            page_ref = str(case.get("page_ref", ""))
            if page_ref not in pages:
                issues.append(_issue("broken_reference", "repairable", "case-plan.json", f"case {case_id} has no valid page_ref", "select the observed page for this intent", function_ref=function_ref, case_id=case_id))
            refs = {str(value) for value in case.get("fact_refs", [])}
            if not refs or not refs <= fact_ids:
                issues.append(_issue("broken_reference", "repairable", "case-plan.json", f"case {case_id} has missing or unknown facts", "repair only this case mapping", function_ref=function_ref, case_id=case_id))
            if not str(case.get("title", "")).strip():
                issues.append(_issue("empty_title", "repairable", "case-plan.json", f"case {case_id} has no intent title", "write the concrete intent", function_ref=function_ref, case_id=case_id))
            title_key = (function_ref, str(case.get("title", "")).strip())
            if title_key in titles:
                issues.append(_issue("duplicate_title", "repairable", "case-plan.json", f"function {function_ref} repeats planned title {title_key[1]!r}", "merge or differentiate the actual intent", function_ref=function_ref, case_id=case_id))
            titles.add(title_key)
            focus = str(case.get("verification_focus", "")).strip()
            if not focus:
                issues.append(_issue(
                    "missing_verification_focus", "repairable", "case-plan.json",
                    f"case {case_id} lacks its primary verification focus",
                    "state the primary element, independent branch and observable effect once",
                    function_ref=function_ref, case_id=case_id,
                ))
            focus_key = (function_ref, re.sub(r"\s+", "", focus).lower())
            if focus and focus_key in focuses:
                issues.append(_issue(
                    "duplicate_verification_focus", "repairable", "case-plan.json",
                    f"case {case_id} repeats another case's primary verification focus",
                    "differentiate the primary behavior being verified instead of changing only the title",
                    function_ref=function_ref, case_id=case_id,
                ))
            if focus:
                focuses.add(focus_key)
            function_name = str(functions.get(function_ref, {}).get("name", "")).strip()
            if function_name and function_name in str(case.get("title", "")):
                issues.append(_issue("redundant_title", "repairable", "case-plan.json", f"case {case_id} repeats its function name inside the scenario title", "retain only the concrete scenario in the plan title", function_ref=function_ref, case_id=case_id))
            if not str(case.get("dfx_dimension", "")).strip() or not str(case.get("dfx_scenario", "")).strip():
                issues.append(_issue("missing_dfx", "repairable", "case-plan.json", f"case {case_id} lacks dimension or scenario", "complete DFX while planning", function_ref=function_ref, case_id=case_id))
    missing_functions = set(functions) - planned_functions
    if missing_functions:
        issues.append(_issue("missing_function", "repairable", "case-plan.json", f"functions missing from plan: {sorted(missing_functions)}", "plan only the missing function blocks"))
    if not plan.get("performance_scenarios") and not str(plan.get("performance_not_applicable_reason", "")).strip():
        issues.append(_issue("missing_performance_decision", "repairable", "case-plan.json", "performance design is empty without an explicit applicability reason", "record the specialist decision during planning", function_ref="__global__"))
    if not plan.get("performance_scenarios") and _has_observable_wait(facts):
        issues.append(_issue(
            "unsupported_performance_na", "repairable", "case-plan.json",
            "observed waiting, loading, timing or timeout behavior requires one light response or timeout scenario",
            "add one compact response/timeout scenario; do not create per-case performance rows",
            function_ref="__global__",
        ))
    if not plan.get("performance_scenarios"):
        refs = plan.get("performance_basis_refs", [])
        if not isinstance(refs, list) or not refs or any(str(ref) not in fact_ids for ref in refs):
            issues.append(_issue("unsupported_performance_decision", "repairable", "case-plan.json", "performance not-applicable decision lacks valid fact basis", "reference the observed facts used for this decision", function_ref="__global__"))
    if not plan.get("risks") and not str(plan.get("risk_not_applicable_reason", "")).strip():
        issues.append(_issue("missing_risk_decision", "repairable", "case-plan.json", "risk design is empty without an explicit applicability reason", "record the specialist decision during planning", function_ref="__global__"))
    if not plan.get("risks") and profile_stability_risks:
        issues.append(_issue(
            "unsupported_risk_na", "repairable", "case-plan.json",
            f"automation profiles already identify stability risks: {profile_stability_risks}",
            "deduplicate these concrete risks into the global risk list once",
            function_ref="__global__",
        ))
    if not plan.get("risks"):
        refs = plan.get("risk_basis_refs", [])
        if not isinstance(refs, list) or not refs or any(str(ref) not in fact_ids for ref in refs):
            issues.append(_issue("unsupported_risk_decision", "repairable", "case-plan.json", "risk not-applicable decision lacks valid fact basis", "reference the observed facts used for this decision", function_ref="__global__"))
    for scenario in plan.get("performance_scenarios", []):
        scenario_id = str(scenario.get("scenario_id", ""))
        required = ("flow", "test_type", "concurrency", "throughput", "response_time", "data_scale", "duration", "metrics", "pass_criteria", "data_strategy", "risk", "included")
        missing = [field for field in required if not str(scenario.get(field, "")).strip()]
        if missing:
            issues.append(_issue("incomplete_performance_scenario", "repairable", "case-plan.json", f"performance scenario {scenario_id} lacks {missing}", "complete this specialist scenario during planning", function_ref=str(scenario.get("function_ref", "__global__"))))
        quantified_target = " ".join(
            str(scenario.get(field, "")) for field in ("response_time", "pass_criteria")
        )
        if _has_quantified_time_target(quantified_target):
            refs = scenario.get("target_basis_refs", [])
            source = str(scenario.get("target_basis", "")).strip().lower()
            if source not in {"requirement", "observed", "需求", "实测"} or not isinstance(refs, list) or not refs or any(str(ref) not in fact_ids for ref in refs):
                issues.append(_issue(
                    "unsupported_performance_target", "repairable", "case-plan.json",
                    f"performance scenario {scenario_id} contains a quantified time target without traceable requirement or observed basis",
                    "remove the invented threshold or provide target_basis and valid target_basis_refs during planning",
                    function_ref=str(scenario.get("function_ref", "__global__")),
                ))
    for risk in plan.get("risks", []):
        risk_id = str(risk.get("risk_id", ""))
        required = ("description", "impact", "level", "recommendation", "status")
        missing = [field for field in required if not str(risk.get(field, "")).strip()]
        if missing:
            issues.append(_issue("incomplete_risk", "repairable", "case-plan.json", f"risk {risk_id} lacks {missing}", "complete this risk during planning", function_ref=str(risk.get("function_ref", "__global__"))))
    case_owners = {
        str(case.get("case_id", "")): str(function.get("function_ref", ""))
        for function in plan.get("functions", [])
        for case in function.get("cases", [])
    }
    seen_assignments: set[tuple[str, int]] = set()
    assigned_case_ids: set[str] = set()
    assignment_by_check: dict[tuple[str, int], dict[str, Any]] = {}
    for assignment in plan.get("check_assignments", []):
        try:
            key = (str(assignment.get("transaction_ref", "")), int(assignment.get("check_index", 0)))
        except (TypeError, ValueError):
            issues.append(_issue("invalid_assignment", "repairable", "case-plan.json", "a check assignment has a non-numeric index", "repair this assignment once"))
            continue
        transaction = transactions.get(key[0])
        if key in seen_assignments:
            issues.append(_issue("duplicate_assignment", "repairable", "case-plan.json", f"check {key} is assigned more than once", "retain one canonical assignment"))
        seen_assignments.add(key)
        assignment_by_check[key] = assignment
        if not transaction or key[1] < 1 or key[1] > len(transaction.get("checks", [])):
            issues.append(_issue("broken_reference", "repairable", "case-plan.json", f"assignment references unknown check {key}", "repair this assignment once"))
            continue
        disposition = str(assignment.get("disposition", ""))
        if disposition == "case":
            case_id = str(assignment.get("case_id", ""))
            assigned_case_ids.add(case_id)
            if case_id not in case_owners:
                issues.append(_issue("broken_reference", "repairable", "case-plan.json", f"check {key} references unknown case {case_id!r}", "bind it to an existing case"))
            elif case_owners[case_id] != str(transaction.get("function_ref", "")):
                issues.append(_issue("wrong_function", "repairable", "case-plan.json", f"check {key} is assigned to another function's case", "bind it to its owning function"))
        elif disposition in {"performance", "risk", "not_applicable"}:
            if not str(assignment.get("reason", "")).strip():
                issues.append(_issue("invalid_assignment", "repairable", "case-plan.json", f"non-case check {key} has no reason", "add one concrete reason"))
        else:
            issues.append(_issue("invalid_assignment", "repairable", "case-plan.json", f"check {key} has unsupported disposition {disposition!r}", "use case/performance/risk/not_applicable"))
        if transaction and key[1] >= 1 and key[1] <= len(transaction.get("checks", [])):
            check = transaction.get("checks", [])[key[1] - 1]
            observed_outcome = str(check.get("outcome") or transaction.get("outcome") or "").strip().lower()
            if observed_outcome in {"unexpected", "anomaly", "failed_unexpectedly"} and disposition not in {"case", "risk", "performance"}:
                issues.append(_issue(
                    "unhandled_observed_anomaly", "repairable", "case-plan.json",
                    f"unexpected observed check {key} has no executable or specialist disposition",
                    "assign this observed anomaly to a case, risk or performance scenario", function_ref=str(transaction.get("function_ref", "")),
                ))
    empty_cases = set(case_owners) - assigned_case_ids
    for case_id in sorted(empty_cases):
        issues.append(_issue(
            "unassigned_case", "repairable", "case-plan.json", f"case {case_id} has no assigned checks",
            "assign observed checks or remove this empty intent",
            function_ref=case_owners.get(case_id, ""), case_id=case_id,
        ))
    all_checks = {
        (str(transaction["fact_id"]), index)
        for transaction in facts.get("transactions", [])
        for index, _ in enumerate(transaction.get("checks", []), 1)
    }
    unassigned = all_checks - seen_assignments
    for transaction_ref, check_index in sorted(unassigned):
        issues.append(_issue(
            "unassigned_check", "repairable", "case-plan.json",
            f"transaction check {(transaction_ref, check_index)} has no planned disposition",
            "assign only this check to a case or explicit non-case disposition",
            function_ref=str(transactions.get(transaction_ref, {}).get("function_ref", "")),
        ))

    independent_by_case: dict[str, list[str]] = {}
    for transaction_ref, transaction in transactions.items():
        function_ref = str(transaction.get("function_ref", ""))
        for index, check in enumerate(transaction.get("checks", []), 1):
            element_ref = str(check.get("element_ref", ""))
            element = elements.get(element_ref, {})
            requirements = [row for row in _matching_requirements(element, check) if row.get("independent_case")]
            if not requirements:
                continue
            if len(requirements) > 1:
                issues.append(_issue(
                    "ambiguous_exploration_branch", "repairable", "case-plan.json",
                    f"check {(transaction_ref, index)} matches multiple independent requirements",
                    "split the observed branches before planning", function_ref=function_ref,
                ))
                continue
            requirement = requirements[0]
            label = f"{requirement.get('kind')} {element.get('name') or element_ref}={requirement.get('value')}"
            assignment = assignment_by_check.get((transaction_ref, index), {})
            case_id = str(assignment.get("case_id", "")) if assignment.get("disposition") == "case" else ""
            if not case_id:
                issues.append(_issue(
                    "independent_branch_not_case", "repairable", "case-plan.json",
                    f"{label} must produce its own executable case", "assign this observed branch to one case",
                    function_ref=function_ref,
                ))
                continue
            independent_by_case.setdefault(case_id, []).append(label)
            expected_strategy = str(requirement.get("strategy", "baseline")).lower()
            if str(planned_cases.get(case_id, {}).get("strategy", "")).lower() != expected_strategy:
                issues.append(_issue(
                    "branch_strategy_mismatch", "repairable", "case-plan.json",
                    f"{label} must use {expected_strategy} strategy", "align this intent with its predeclared exploration strategy",
                    function_ref=function_ref, case_id=case_id,
                ))
            planned_case = planned_cases.get(case_id, {})
            if expected_strategy == "dfx" and (
                str(planned_case.get("dfx_dimension", "")) != str(requirement.get("dfx_dimension", ""))
                or str(planned_case.get("dfx_scenario", "")) != str(requirement.get("dfx_scenario", ""))
            ):
                issues.append(_issue(
                    "branch_dfx_mismatch", "repairable", "case-plan.json",
                    f"{label} DFX metadata differs from the predeclared requirement",
                    "copy the requirement's DFX dimension and scenario into this intent",
                    function_ref=function_ref, case_id=case_id,
                ))
    for case_id, labels in independent_by_case.items():
        if len(labels) > 1:
            function_ref = case_owners.get(case_id, "")
            issues.append(_issue(
                "combined_independent_branches", "repairable", "case-plan.json",
                f"case {case_id} combines independent branches: {labels}",
                "create one case for each independent exploration branch",
                function_ref=function_ref, case_id=case_id,
            ))
    return issues


def _normalize_menu_path(value: str) -> str:
    return re.sub(r"\s*(?:>|/|\\|→)\s*", "-", value.strip())


def _normalize_core_prose(steps: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    normalized: list[tuple[str, str]] = []
    core_steps = steps[1:] if steps and "进入" in str(steps[0].get("action", "")) else steps
    for step in core_steps:  # common menu navigation is intentionally ignored
        pair: list[str] = []
        for key in ("action", "expected"):
            text = re.sub(r"(?i)(?:AI_TEST|CODEX_TEST)(?:[-_][A-Za-z0-9]+)+", "<TEST_OBJECT>", str(step.get(key, "")))
            pair.append(re.sub(r"\s+", " ", text).strip())
        normalized.append((pair[0], pair[1]))
    return tuple(normalized)


def _normalize_core_column(steps: list[dict[str, Any]], key: str) -> tuple[str, ...]:
    core_steps = steps[1:] if steps and "进入" in str(steps[0].get("action", "")) else steps
    return tuple(
        re.sub(r"\s+", " ", re.sub(
            r"(?i)(?:AI_TEST|CODEX_TEST)(?:[-_][A-Za-z0-9]+)+", "<TEST_OBJECT>", str(step.get(key, "")),
        )).strip()
        for step in core_steps
    )


def _expected_satisfies_anchor(expected: str, check: dict[str, Any]) -> bool:
    anchor = check.get("result_anchor") if isinstance(check.get("result_anchor"), dict) else {}
    raw_tokens = anchor.get("stable_tokens", anchor.get("tokens"))
    if raw_tokens not in (None, "", []):
        values = raw_tokens if isinstance(raw_tokens, list) else [raw_tokens]
    else:
        value = anchor.get("value") or check.get("result", "")
        values = value if isinstance(value, list) else [value]
    tokens = [str(item).strip() for item in values if item not in (None, "") and str(item).strip()]
    return all(token in expected for token in tokens)


def _uses_volatile_anchor_sample(expected: str, check: dict[str, Any]) -> bool:
    anchor = check.get("result_anchor") if isinstance(check.get("result_anchor"), dict) else {}
    stable = anchor.get("stable_tokens", anchor.get("tokens"))
    value = anchor.get("value")
    if stable in (None, "", []) or value in (None, "", []):
        return False
    samples = value if isinstance(value, list) else [value]
    return any(
        str(sample).strip() in expected and bool(re.search(r"\d|%|百分比|耗时|进度", str(sample)))
        for sample in samples if str(sample).strip()
    )


def _action_satisfies_check(action: str, check: dict[str, Any]) -> bool:
    tokens = check.get("action_tokens", [])
    if isinstance(tokens, str):
        tokens = [tokens]
    tokens = [str(item).strip() for item in tokens if str(item).strip()]
    for field in ("option_value", "input_value", "test_value"):
        value = str(check.get(field, "")).strip()
        if value:
            tokens.append(value)
    return all(token in action for token in dict.fromkeys(tokens))


def inspect_cases(run_dir: Path) -> list[dict[str, str]]:
    try:
        facts, plan, document = load_facts(run_dir), load_plan(run_dir), load_cases(run_dir)
    except ValueError as exc:
        return [_issue("missing_artifact", "blocker", "function-cases.json", str(exc), "create the missing artifact from the plan")]
    issues: list[dict[str, str]] = []
    if document.get("source_plan") != "case-plan.json":
        issues.append(_issue("invalid_source", "repairable", "function-cases.json", "case source_plan must be case-plan.json", "repair the source declaration"))
    if document.get("source_plan_digest") != _semantic_content_digest(plan):
        issues.append(_issue("stale_source", "repairable", "function-cases.json", "cases no longer match the plan", "regenerate only changed planned cases"))
    fact_ids = _all_fact_ids(facts)
    pages = {str(row["fact_id"]): row for row in facts.get("pages", [])}
    transactions = {str(row["fact_id"]): row for row in facts.get("transactions", [])}
    elements = {str(row["fact_id"]): row for row in facts.get("elements", [])}
    planned: dict[str, tuple[str, dict[str, Any], str]] = {}
    assignments_by_case: dict[str, list[tuple[str, int]]] = {}
    for assignment in plan.get("check_assignments", []):
        if assignment.get("disposition") == "case":
            try:
                assignments_by_case.setdefault(str(assignment.get("case_id", "")), []).append(
                    (str(assignment.get("transaction_ref", "")), int(assignment.get("check_index", 0)))
                )
            except (TypeError, ValueError):
                pass
    for function in plan.get("functions", []):
        function_ref = str(function.get("function_ref", ""))
        function_name = str(function.get("name", ""))
        for case in function.get("cases", []):
            planned[str(case.get("case_id", ""))] = (function_ref, case, function_name)
    actual_ids: set[str] = set()
    closed_functions: set[str] = set()
    previous_function = ""
    signatures: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}
    action_signatures: dict[tuple[str, tuple[str, ...]], str] = {}
    expected_signatures: dict[tuple[str, tuple[str, ...]], str] = {}
    for case in document.get("cases", []):
        case_id = str(case.get("case_id", "")).strip()
        function_ref = str(case.get("function_ref", "")).strip()
        title = str(case.get("title", "")).strip()
        context = {"function_ref": function_ref, "case_id": case_id}
        if case_id not in planned:
            issues.append(_issue("plan_mismatch", "repairable", "function-cases.json", f"case {case_id!r} is not planned", "remove or plan this case before writing", **context))
            planned_case, function_name = {}, ""
        else:
            planned_function, planned_case, function_name = planned[case_id]
            if planned_function != function_ref:
                issues.append(_issue("plan_mismatch", "repairable", "function-cases.json", f"case {case_id} belongs to the wrong function", "regenerate only this function block", **context))
        if case_id in actual_ids:
            issues.append(_issue("duplicate_case", "repairable", "function-cases.json", f"duplicate case {case_id}", "retain the planned case once", **context))
        actual_ids.add(case_id)
        if previous_function and function_ref != previous_function:
            closed_functions.add(previous_function)
        if function_ref in closed_functions:
            issues.append(_issue("function_order", "repairable", "function-cases.json", f"function {function_ref} is split into multiple blocks", "move its cases into one contiguous block", **context))
        previous_function = function_ref
        if not title or (function_name and not title.startswith(function_name + "-")):
            issues.append(_issue("invalid_title", "repairable", "function-cases.json", f"case {case_id} title must be '功能点-具体场景'", "repair this title", **context))
        for field in ("priority", "test_type", "test_data"):
            if not str(case.get(field, "")).strip():
                issues.append(_issue("empty_field", "repairable", "function-cases.json", f"case {case_id} has empty {field}", "complete this field with concrete content", **context))
        if str(case.get("priority", "")) not in {"P0", "P1", "P2", "P3"}:
            issues.append(_issue("invalid_priority", "repairable", "function-cases.json", f"case {case_id} has unsupported priority", "use P0/P1/P2/P3", **context))
        for field in ("automation_value", "automation_priority"):
            if not str(case.get(field, "")).strip():
                issues.append(_issue("missing_automation_decision", "repairable", "function-cases.json", f"case {case_id} has empty {field}", "write the lightweight case-level automation decision", **context))
        if str(case.get("automation_priority", "")) not in {"P0", "P1", "P2", "P3"}:
            issues.append(_issue("invalid_priority", "repairable", "function-cases.json", f"case {case_id} has unsupported automation priority", "use P0/P1/P2/P3", **context))
        preconditions = case.get("preconditions")
        if not isinstance(preconditions, list) or not preconditions or any(not str(value).strip() for value in preconditions):
            issues.append(_issue("empty_field", "repairable", "function-cases.json", f"case {case_id} has no explicit preconditions", "write concrete conditions or '无特殊前置条件'", **context))
        planned_title = str(planned_case.get("title", "")).strip()
        if function_name and planned_title and title != f"{function_name}-{planned_title}":
            issues.append(_issue("plan_mismatch", "repairable", "function-cases.json", f"case {case_id} title differs from its planned intent", "restore this planned title", **context))
        planned_focus = str(planned_case.get("verification_focus", "")).strip()
        actual_focus = str(case.get("verification_focus", "")).strip()
        if not actual_focus or actual_focus != planned_focus:
            issues.append(_issue(
                "verification_focus_mismatch", "repairable", "function-cases.json",
                f"case {case_id} does not preserve its planned primary verification focus",
                "restore the planned focus and make the paired step express that behavior",
                **context,
            ))
        steps = case.get("steps")
        if not isinstance(steps, list) or not steps:
            issues.append(_issue("invalid_steps", "repairable", "function-cases.json", f"case {case_id} has no paired steps", "write paired action and expected entries", **context))
            steps = []
        for index, step in enumerate(steps, 1):
            if not isinstance(step, dict) or not str(step.get("action", "")).strip() or not str(step.get("expected", "")).strip():
                issues.append(_issue("invalid_steps", "repairable", "function-cases.json", f"case {case_id} step {index} lacks action or expected", "repair only this paired step", **context))
        if steps:
            page_ref = str(planned_case.get("page_ref", ""))
            page = pages.get(page_ref, {})
            menu_path = "-".join(str(part).strip() for part in page.get("menu_path", []) if str(part).strip())
            first_action = str(steps[0].get("action", ""))
            first_expected = str(steps[0].get("expected", ""))
            if not menu_path or _normalize_menu_path(first_action) != f"进入{menu_path}":
                issues.append(_issue("missing_navigation", "repairable", "function-cases.json", f"case {case_id} must start from complete menu path {menu_path!r}", "repair its first navigation step", **context))
            page_anchor = str(page.get("result_anchor") or page.get("name", "")).strip()
            if page_anchor and page_anchor not in first_expected:
                issues.append(_issue("ungrounded_expected", "repairable", "function-cases.json", f"case {case_id} navigation result lacks page anchor {page_anchor!r}", "use the observed page name or result anchor", **context))
            if steps[0].get("source_check"):
                issues.append(_issue("invalid_source_check", "repairable", "function-cases.json", f"case {case_id} navigation must not claim a transaction check", "remove source_check from the navigation step", **context))
        planned_check_list = assignments_by_case.get(case_id, [])
        planned_checks = set(planned_check_list)
        actual_checks: set[tuple[str, int]] = set()
        actual_check_list: list[tuple[str, int]] = []
        for index, step in enumerate(steps[1:], 2):
            source = step.get("source_check")
            if not isinstance(source, dict):
                issues.append(_issue("missing_source_check", "repairable", "function-cases.json", f"case {case_id} step {index} has no source check", "bind this step to one planned transaction result", **context))
                continue
            try:
                source_key = (str(source.get("transaction_ref", "")), int(source.get("check_index", 0)))
            except (TypeError, ValueError):
                source_key = ("", 0)
            actual_checks.add(source_key)
            actual_check_list.append(source_key)
            transaction = transactions.get(source_key[0])
            if source_key not in planned_checks or not transaction or source_key[1] < 1 or source_key[1] > len(transaction.get("checks", [])):
                issues.append(_issue("invalid_source_check", "repairable", "function-cases.json", f"case {case_id} step {index} references an unplanned check", "repair only this source mapping", **context))
                continue
            check = transaction["checks"][source_key[1] - 1]
            trigger_ref = str(check.get("trigger_element_ref", ""))
            if trigger_ref and trigger_ref in elements and not _action_has_trigger(str(step.get("action", ""))):
                issues.append(_issue("missing_trigger", "blocker", "function-cases.json", f"case {case_id} step {index} omits the observed submit/execute trigger", "restore the complete observed action", **context))
            if not _action_satisfies_check(str(step.get("action", "")), check):
                issues.append(_issue("action_fact_mismatch", "blocker", "function-cases.json", f"case {case_id} step {index} omits the observed option or input value", "restore the concrete action from the transaction fact", **context))
            if not _expected_satisfies_anchor(str(step.get("expected", "")), check):
                issues.append(_issue("ungrounded_expected", "blocker", "function-cases.json", f"case {case_id} step {index} does not preserve its observed result", "rewrite this expected result from the transaction fact", **context))
            if _uses_volatile_anchor_sample(str(step.get("expected", "")), check):
                issues.append(_issue(
                    "volatile_sample_expected", "repairable", "function-cases.json",
                    f"case {case_id} step {index} fixes an observed numeric sample even though stable tokens exist",
                    "retain the stable result fields and remove the incidental count, percentage, duration or progress value",
                    **context,
                ))
        if planned_checks != actual_checks or actual_check_list != planned_check_list or len(steps[1:]) != len(planned_check_list):
            issues.append(_issue("check_mapping_mismatch", "repairable", "function-cases.json", f"case {case_id} steps do not map one-to-one to all planned checks", "add, remove or remap only the affected paired steps", **context))
        prose = "\n".join(
            [title, str(case.get("test_data", ""))]
            + [str(value) for value in case.get("preconditions", [])]
            + [str(step.get(key, "")) for step in steps for key in ("action", "expected")]
        )
        if INTERNAL_PROSE.search(prose):
            issues.append(_issue("internal_prose", "repairable", "function-cases.json", f"case {case_id} exposes an internal identifier or page-tool term", "rewrite only this executable prose", **context))
        if any(marker.lower() in prose.lower() for marker in SCREENSHOT_MARKERS):
            issues.append(_issue("screenshot_step", "repairable", "function-cases.json", f"case {case_id} asks the tester to take a screenshot", "replace it with an observable assertion", **context))
        if any(marker.lower() in prose.lower() for marker in PLACEHOLDER_MARKERS):
            issues.append(_issue("placeholder", "repairable", "function-cases.json", f"case {case_id} contains placeholder prose", "supply concrete masked data", **context))
        executable_input_text = "\n".join(
            [str(case.get("test_data", ""))] + [str(step.get("action", "")) for step in steps]
        )
        if NATURAL_PLACEHOLDER_PATTERN.search(executable_input_text):
            issues.append(_issue("placeholder", "repairable", "function-cases.json", f"case {case_id} contains a natural-language data placeholder", "use a named TEST_* controlled-data reference and explain its source", **context))
        if ANGLE_PLACEHOLDER.search(prose):
            issues.append(_issue("placeholder", "repairable", "function-cases.json", f"case {case_id} contains unresolved angle-bracket data", "replace it with concrete controlled test data", **context))
        if URL_PATTERN.search(prose) or IPV4_PATTERN.search(prose):
            issues.append(_issue("sensitive_network", "blocker", "function-cases.json", f"case {case_id} exposes a URL or IP address", "replace it with a controlled masked test-data reference", **context))
        test_data_refs = set(DATA_REFERENCE_PATTERN.findall(str(case.get("test_data", ""))))
        precondition_text = "\n".join(str(value) for value in case.get("preconditions", []))
        missing_data_refs = sorted(ref for ref in test_data_refs if ref not in precondition_text)
        if missing_data_refs:
            issues.append(_issue(
                "test_data_source_missing", "repairable", "function-cases.json",
                f"case {case_id} does not explain the controlled source of {', '.join(missing_data_refs)}",
                "add the same named test-data reference and its controlled source to the preconditions",
                **context,
            ))
        refs = {str(value) for value in case.get("fact_refs", [])}
        planned_refs = {str(value) for value in planned_case.get("fact_refs", [])}
        if not refs or not refs <= fact_ids or not planned_refs <= refs:
            issues.append(_issue("ungrounded_case", "blocker", "function-cases.json", f"case {case_id} is not grounded in all planned facts", "restore only the missing fact mapping; do not invent an expected result", **context))
        signature = _normalize_core_prose(steps)
        signature_key = (function_ref, signature)
        duplicate_pair = bool(signature and signature_key in signatures)
        if duplicate_pair:
            issues.append(_issue("duplicate_core", "repairable", "function-cases.json", f"case {case_id} duplicates the core actions and results of {signatures[signature_key]}", "rewrite or merge this planned scenario", **context))
        if signature:
            signatures[signature_key] = case_id
        action_signature = _normalize_core_column(steps, "action")
        expected_signature = _normalize_core_column(steps, "expected")
        action_key = (function_ref, action_signature)
        expected_key = (function_ref, expected_signature)
        if not duplicate_pair and action_signature and action_key in action_signatures:
            issues.append(_issue(
                "duplicate_core_action", "repairable", "function-cases.json",
                f"case {case_id} repeats the core action of {action_signatures[action_key]} despite a different primary focus",
                "use the independently observed action intent for this primary branch", **context,
            ))
        if not duplicate_pair and expected_signature and expected_key in expected_signatures:
            issues.append(_issue(
                "duplicate_core_expected", "repairable", "function-cases.json",
                f"case {case_id} repeats the core expected result of {expected_signatures[expected_key]} despite a different scenario",
                "state the branch-specific observable effect while retaining stable result tokens", **context,
            ))
        if action_signature:
            action_signatures[action_key] = case_id
        if expected_signature:
            expected_signatures[expected_key] = case_id
        for field in ("dfx_dimension", "dfx_scenario"):
            if str(case.get(field, "")).strip() != str(planned_case.get(field, "")).strip():
                issues.append(_issue("plan_mismatch", "repairable", "function-cases.json", f"case {case_id} {field} differs from plan", "restore this planned value", **context))
    missing = set(planned) - actual_ids
    for case_id in sorted(missing):
        function_ref = planned.get(case_id, ("", {}, ""))[0]
        issues.append(_issue(
            "missing_case", "repairable", "function-cases.json", f"planned case {case_id} was not written",
            "generate only this missing case", function_ref=function_ref, case_id=case_id,
        ))
    return issues


def _issue(code: str, severity: str, artifact: str, message: str, repair: str, **context: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "artifact": artifact, "message": message, "local_repair": repair, **context}


def validate_discovery(run_dir: Path) -> list[str]:
    return [item["message"] for item in inspect_discovery(run_dir)]


def validate_plan(run_dir: Path) -> list[str]:
    return [item["message"] for item in inspect_plan(run_dir)]


def validate_cases(run_dir: Path) -> list[str]:
    return [item["message"] for item in inspect_cases(run_dir)]


def _save_with_construction_check(path: Path, value: dict[str, Any], inspector: Any, run_dir: Path) -> dict[str, Any]:
    """Persist one artifact only when its generation-time constraints hold."""
    old = path.read_bytes() if path.exists() else None
    _write_json(path, value)
    issues = inspector(run_dir)
    if issues:
        if old is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(old)
        raise ValueError("construction needs one grouped local correction: " + json.dumps(_group_issues(issues), ensure_ascii=False))
    return value


def _derive_case_fact_refs(
    function_ref: str,
    page_ref: str,
    assignments: list[dict[str, Any]],
    transactions: dict[str, dict[str, Any]],
) -> list[str]:
    refs = [function_ref, page_ref]
    for assignment in assignments:
        transaction_ref = str(assignment.get("transaction_ref", ""))
        transaction = transactions.get(transaction_ref, {})
        refs.append(transaction_ref)
        try:
            check = transaction.get("checks", [])[int(assignment.get("check_index", 0)) - 1]
        except (IndexError, TypeError, ValueError):
            check = {}
        if check.get("element_ref"):
            refs.append(str(check["element_ref"]))
        refs.extend(str(value) for value in check.get("used_element_refs", []) if str(value).strip())
    return list(dict.fromkeys(ref for ref in refs if ref))


def _normalize_plan(run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    facts = load_facts(run_dir)
    value = json.loads(json.dumps(plan, ensure_ascii=False))
    if "non_case_checks" in value or any("dfx_decisions" in function for function in value.get("functions", [])) or any(
        "covered_checks" in case for function in value.get("functions", []) for case in function.get("cases", [])
    ):
        raise ValueError("deprecated parallel coverage ledgers are not accepted; use check_assignments only")
    value["source"] = "facts.json"
    value["source_digest"] = _planning_fact_digest(facts)
    for scenario in value.get("performance_scenarios", []):
        if not isinstance(scenario, dict):
            continue
        if not str(scenario.get("flow", "")).strip() and str(scenario.get("business_link", "")).strip():
            scenario["flow"] = scenario["business_link"]
        legacy_included = scenario.pop("included_in_current_test", None)
        if legacy_included not in (None, ""):
            current = scenario.get("included")
            if current not in (None, "") and str(current).strip() != str(legacy_included).strip():
                raise ValueError("performance included conflicts with included_in_current_test")
            scenario["included"] = legacy_included
    function_basis = [str(row.get("fact_id", "")) for row in facts.get("functions", []) if str(row.get("fact_id", ""))]
    if not value.get("performance_scenarios") and str(value.get("performance_not_applicable_reason", "")).strip() and "performance_basis_refs" not in value:
        value["performance_basis_refs"] = function_basis
    if not value.get("risks") and str(value.get("risk_not_applicable_reason", "")).strip() and "risk_basis_refs" not in value:
        value["risk_basis_refs"] = function_basis
    transactions = {str(row["fact_id"]): row for row in facts.get("transactions", [])}
    elements = {str(row["fact_id"]): row for row in facts.get("elements", [])}
    function_names = {str(row.get("fact_id", "")): str(row.get("name", "")) for row in facts.get("functions", [])}
    by_case: dict[str, list[dict[str, Any]]] = {}
    for assignment in value.get("check_assignments", []):
        if assignment.get("disposition") == "case":
            by_case.setdefault(str(assignment.get("case_id", "")), []).append(assignment)
    supplied_requirements = facts.get("scope", {}).get("requirements", [])
    requirement_ids = {
        str(function.get("fact_id", "")): str(
            function.get("requirement_id")
            or (supplied_requirements[index - 1].get("requirement_id") if index <= len(supplied_requirements) else "")
            or f"REQ-{index:03d}"
        )
        for index, function in enumerate(facts.get("functions", []), 1)
    }
    page_functions = {
        str(element.get("function_ref", ""))
        for element in facts.get("elements", [])
        if str(element.get("page_ref", "")).strip()
    }
    incoming_function_refs = {str(function.get("function_ref", "")) for function in value.get("functions", [])}
    value["risks"] = [
        row for row in value.get("risks", [])
        if not (
            isinstance(row, dict)
            and row.get("source") == "automation_profile"
            and str(row.get("function_ref", "")) in incoming_function_refs
        )
    ]
    value["performance_scenarios"] = [
        row for row in value.get("performance_scenarios", [])
        if not (isinstance(row, dict) and row.get("source") == "observed_wait")
    ]
    profile_risk_candidates: list[dict[str, Any]] = []
    for function in value.get("functions", []):
        function_ref = str(function.get("function_ref", ""))
        profile = function.get("automation_profile") if isinstance(function.get("automation_profile"), dict) else {}
        level = str(profile.get("level", "")).strip().lower()
        if function_ref in page_functions and level not in {"", "ui", "页面", "手工", "manual", "不适用", "none", "n/a", "na"}:
            profile["level"] = "UI"
        if _substantive_stability_risk(profile.get("stability_risk")):
            profile_risk_candidates.append({
                "function_ref": function_ref,
                "source": "automation_profile",
                "type": "稳定性风险",
                "dfx_dimension": "DFR可靠",
                "dfx_scenario": "自动化与环境稳定性",
                "description": str(profile.get("stability_risk", "")).strip(),
                "impact": function_names.get(function_ref) or function_ref,
                "level": "中",
                "recommendation": "使用受控数据和稳定断言，并隔离可变环境依赖",
                "status": "已识别",
            })
        function["automation_profile"] = profile
    wait_refs = _observable_wait_refs(facts)
    if wait_refs and not value.get("performance_scenarios"):
        scope = facts.get("scope", {})
        affected_function_refs = list(dict.fromkeys(str(row.get("function_ref", "")) for row in wait_refs if str(row.get("function_ref", ""))))
        affected_names = [function_names.get(ref, ref) for ref in affected_function_refs]
        value["performance_scenarios"] = [{
            "source": "observed_wait",
            "function_ref": affected_function_refs[0] if len(affected_function_refs) == 1 else "",
            "basis_refs": wait_refs,
            "flow": "；".join(affected_names) or str(scope.get("module_path") or "页面功能链路"),
            "test_type": "单次响应与超时体验",
            "concurrency": "单用户",
            "throughput": "不适用",
            "response_time": "未提供量化目标；记录实际响应时间",
            "data_scale": "复用功能用例的受控数据",
            "duration": "从触发操作至完成或超时",
            "metrics": "开始时间、完成时间、加载状态、完成或超时反馈",
            "pass_criteria": "操作完成或超时时页面给出明确、可恢复的反馈",
            "data_strategy": "复用功能用例的受控数据",
            "risk": "实际时延可能受环境和外部依赖影响",
            "included": "是",
        }]
        value.pop("performance_not_applicable_reason", None)
        value.pop("performance_basis_refs", None)
    if value.get("performance_scenarios"):
        value.pop("performance_not_applicable_reason", None)
        value.pop("performance_basis_refs", None)
    value["risks"] = _deduplicate_risks(
        [row for row in value.get("risks", []) if isinstance(row, dict)] + profile_risk_candidates,
        function_names,
    )
    if value.get("risks"):
        value.pop("risk_not_applicable_reason", None)
        value.pop("risk_basis_refs", None)
    for index, scenario in enumerate(value.get("performance_scenarios", []), 1):
        function_ref = str(scenario.get("function_ref", ""))
        scenario.setdefault("scenario_id", f"PERF-{index:03d}")
        scenario.setdefault("requirement_id", requirement_ids.get(function_ref, "不适用"))
        scenario.setdefault("dfx_dimension", "DFP性能")
        scenario.setdefault("dfx_scenario", str(scenario.get("test_type") or "性能专项"))
        scenario.setdefault("included", "是")
    for index, risk in enumerate(value.get("risks", []), 1):
        function_ref = str(risk.get("function_ref", ""))
        risk.setdefault("risk_id", f"RISK-{index:03d}")
        risk.setdefault("requirement_id", requirement_ids.get(function_ref, ""))
    for function in value.get("functions", []):
        function_ref = str(function.get("function_ref", ""))
        if not str(function.get("name", "")).strip() and function_names.get(function_ref):
            function["name"] = function_names[function_ref]
        function.setdefault("requirement_id", requirement_ids.get(function_ref, ""))
        for case in function.get("cases", []):
            case_id = str(case.get("case_id", ""))
            case.setdefault("requirement_id", function.get("requirement_id", ""))
            if str(case.get("strategy", "")).lower() == "baseline":
                case.setdefault("dfx_dimension", "DFT功能")
                case.setdefault("dfx_scenario", "正向流程")
            case["fact_refs"] = _derive_case_fact_refs(
                function_ref, str(case.get("page_ref", "")), by_case.get(case_id, []), transactions,
            )
            if not str(case.get("verification_focus", "")).strip():
                focus_hints: list[str] = []
                for assignment in by_case.get(case_id, []):
                    transaction = transactions.get(str(assignment.get("transaction_ref", "")), {})
                    try:
                        check = transaction.get("checks", [])[int(assignment.get("check_index", 0)) - 1]
                    except (IndexError, TypeError, ValueError):
                        continue
                    element = elements.get(str(check.get("element_ref", "")), {})
                    focus_hints.append(_verification_focus_hint(element, check))
                if focus_hints:
                    case["verification_focus"] = "；".join(dict.fromkeys(focus_hints))
    return value


def _merge_plan_functions(run_dir: Path, incoming: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    paths = artifact_paths(run_dir)
    existing = load_plan(run_dir) if paths["plan"].exists() else {"schema_version": SCHEMA_VERSION, "functions": [], "check_assignments": []}
    incoming_function_refs = [str(row.get("function_ref", "")) for row in incoming.get("functions", []) if str(row.get("function_ref", "")).strip()]
    affected = set(incoming_function_refs)
    if not affected:
        raise ValueError("plan upsert requires at least one function block")
    if len(incoming_function_refs) != len(affected):
        raise ValueError("plan upsert repeats a function block")
    facts = load_facts(run_dir)
    fact_function_refs = {str(row.get("fact_id", "")) for row in facts.get("functions", [])}
    unknown_functions = affected - fact_function_refs
    if unknown_functions:
        raise ValueError(f"plan upsert references unknown functions: {sorted(unknown_functions)}")
    if any(key in incoming for key in (
        "risks", "risk_not_applicable_reason", "risk_basis_refs",
        "performance_scenarios", "performance_not_applicable_reason", "performance_basis_refs",
    )):
        affected.add("__global__")
    transaction_owner = {str(row.get("fact_id", "")): str(row.get("function_ref", "")) for row in facts.get("transactions", [])}
    incoming_assignments: list[dict[str, Any]] = []
    for row in incoming.get("check_assignments", []):
        owner = transaction_owner.get(str(row.get("transaction_ref", "")))
        if owner and owner not in affected:
            raise ValueError("an upsert function block cannot replace another function's check assignment")
        incoming_assignments.append(row)
    retained_assignments = [
        row for row in existing.get("check_assignments", [])
        if transaction_owner.get(str(row.get("transaction_ref", ""))) not in affected
    ]
    by_function = {str(row.get("function_ref", "")): row for row in existing.get("functions", [])}
    by_function.update({str(row.get("function_ref", "")): row for row in incoming.get("functions", [])})
    fact_order = [str(row.get("fact_id", "")) for row in facts.get("functions", [])]
    merged = {
        key: json.loads(json.dumps(value, ensure_ascii=False))
        for key, value in existing.items()
        if key not in {"functions", "check_assignments", "source", "source_digest"}
    }
    for key, incoming_value in incoming.items():
        if key not in {"functions", "check_assignments", "source", "source_digest", "risks", "performance_scenarios"}:
            merged[key] = json.loads(json.dumps(incoming_value, ensure_ascii=False))
    for key in ("risks", "performance_scenarios"):
        if key not in incoming:
            continue
        incoming_rows = json.loads(json.dumps(incoming.get(key, []), ensure_ascii=False))
        existing_rows = existing.get(key, []) if isinstance(existing.get(key), list) else []
        incoming_has_global = any(not str(row.get("function_ref", "")).strip() for row in incoming_rows if isinstance(row, dict))
        retained_rows = [
            row for row in existing_rows
            if str(row.get("function_ref", "")).strip() not in affected
            and (str(row.get("function_ref", "")).strip() or not incoming_has_global)
        ]
        merged[key] = retained_rows + incoming_rows
    merged["schema_version"] = SCHEMA_VERSION
    merged["functions"] = [by_function[ref] for ref in fact_order if ref in by_function]
    merged["check_assignments"] = retained_assignments + incoming_assignments
    return merged, affected


def _save_incremental(path: Path, value: dict[str, Any], inspector: Any, run_dir: Path, affected: set[str], complete: bool) -> dict[str, Any]:
    old = path.read_bytes() if path.exists() else None
    unchanged = old is not None and _semantic_content_digest(_read_json(path)) == _semantic_content_digest(value)
    if not unchanged:
        _write_json(path, value)
    issues = inspector(run_dir)
    if not complete:
        issues = [
            issue for issue in issues
            if issue.get("code") != "missing_function"
            and (not issue.get("function_ref") or issue.get("function_ref") in affected)
        ]
    if issues:
        if not unchanged:
            if old is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(old)
        raise ValueError("construction needs one grouped local correction: " + json.dumps(_group_issues(issues), ensure_ascii=False))
    return _read_json(path) if unchanged else value


def save_plan(run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    facts = load_facts(run_dir)
    checkpoint = _current_checkpoint(facts)
    if checkpoint.get("ready") is not True:
        raise ValueError("discovery checkpoint is not ready: " + json.dumps(checkpoint, ensure_ascii=False))
    merged, affected = _merge_plan_functions(run_dir, plan)
    value = _normalize_plan(run_dir, merged)
    fact_functions = {str(row.get("fact_id", "")) for row in facts.get("functions", [])}
    planned_functions = {str(row.get("function_ref", "")) for row in value.get("functions", [])}
    return _save_incremental(
        artifact_paths(run_dir)["plan"], value, inspect_plan, run_dir, affected,
        complete=fact_functions == planned_functions,
    )


def _assign_case_sources(run_dir: Path, cases: dict[str, Any]) -> dict[str, Any]:
    plan = load_plan(run_dir)
    facts = load_facts(run_dir)
    pages = {str(page.get("fact_id", "")): page for page in facts.get("pages", [])}
    value = json.loads(json.dumps(cases, ensure_ascii=False))
    assignments: dict[str, list[dict[str, Any]]] = {}
    for assignment in plan.get("check_assignments", []):
        if assignment.get("disposition") == "case":
            assignments.setdefault(str(assignment.get("case_id", "")), []).append(assignment)
    planned_cases = {
        str(case.get("case_id", "")): case
        for function in plan.get("functions", [])
        for case in function.get("cases", [])
    }
    planned_function_names = {
        str(function.get("function_ref", "")): str(function.get("name", ""))
        for function in plan.get("functions", [])
    }
    for case in value.get("cases", []):
        case_id = str(case.get("case_id", ""))
        planned = planned_cases.get(case_id, {})
        case["test_type"] = str(case.get("test_type") or planned.get("test_type") or "功能测试")
        case["priority"] = str(case.get("priority") or planned.get("priority") or "P1")
        case["automation_priority"] = str(case.get("automation_priority") or planned.get("automation_priority") or case["priority"])
        for field in ("priority", "automation_priority"):
            raw = str(case.get(field, "")).strip()
            case[field] = PRIORITY_ALIASES.get(raw.lower(), PRIORITY_ALIASES.get(raw, raw.upper()))
        function_name = planned_function_names.get(str(case.get("function_ref", "")), "")
        planned_title = str(planned.get("title", "")).strip()
        if function_name and planned_title:
            case["title"] = f"{function_name}-{planned_title}"
        steps = case.get("steps", []) if isinstance(case.get("steps"), list) else []
        core_steps = steps[1:] if steps and str(steps[0].get("action", "")).strip().startswith("进入") else steps
        page = pages.get(str(planned.get("page_ref", "")), {})
        menu_path = "-".join(str(part).strip() for part in page.get("menu_path", []) if str(part).strip())
        page_anchor = str(page.get("result_anchor") or page.get("name") or "目标页面").strip()
        navigation = {
            "action": f"进入{menu_path}",
            "expected": f"显示{page_anchor}" if page_anchor.endswith("页面") else f"显示{page_anchor}页面",
        }
        steps = [navigation] + core_steps
        case["steps"] = steps
        sources = assignments.get(case_id, [])
        for step in steps:
            if isinstance(step, dict):
                step.pop("source_check", None)
        if len(core_steps) == len(sources):
            for step, source in zip(core_steps, sources):
                step["source_check"] = {
                    "transaction_ref": str(source.get("transaction_ref", "")),
                    "check_index": int(source.get("check_index", 0)),
                }
        case["fact_refs"] = list(planned.get("fact_refs", []))
        for field in ("requirement_id", "strategy", "dfx_dimension", "dfx_scenario", "verification_focus"):
            case[field] = planned.get(field, case.get(field, ""))
    value["source_plan"] = "case-plan.json"
    value["source_plan_digest"] = _semantic_content_digest(plan)
    return value


def save_cases(run_dir: Path, cases: dict[str, Any]) -> dict[str, Any]:
    plan = load_plan(run_dir)
    incoming_rows = cases.get("cases", [])
    affected = {str(row.get("function_ref", "")) for row in incoming_rows if str(row.get("function_ref", "")).strip()}
    if not affected:
        raise ValueError("case upsert requires at least one function block")
    plan_owners = {
        str(case.get("case_id", "")): str(function.get("function_ref", ""))
        for function in plan.get("functions", []) for case in function.get("cases", [])
    }
    incoming_ids = [str(row.get("case_id", "")) for row in incoming_rows]
    if len(incoming_ids) != len(set(incoming_ids)):
        raise ValueError("case upsert repeats a case ID")
    for row in incoming_rows:
        case_id = str(row.get("case_id", ""))
        function_ref = str(row.get("function_ref", ""))
        if case_id not in plan_owners or plan_owners[case_id] != function_ref:
            raise ValueError(f"case upsert contains an unplanned or wrongly-owned case: {case_id!r}")
    path = artifact_paths(run_dir)["cases"]
    existing = load_cases(run_dir) if path.exists() else {"schema_version": SCHEMA_VERSION, "cases": []}
    retained = [row for row in existing.get("cases", []) if str(row.get("function_ref", "")) not in affected]
    incoming = [row for row in incoming_rows if str(row.get("function_ref", "")) in affected]
    by_id = {str(row.get("case_id", "")): row for row in retained + incoming}
    plan_order = [
        str(case.get("case_id", ""))
        for function in plan.get("functions", [])
        for case in function.get("cases", [])
    ]
    merged = {key: value for key, value in existing.items() if key not in {"cases", "source_plan", "source_plan_digest"}}
    merged["schema_version"] = SCHEMA_VERSION
    merged["cases"] = [by_id[case_id] for case_id in plan_order if case_id in by_id]
    value = _assign_case_sources(run_dir, merged)
    planned_ids = set(plan_order)
    written_ids = {str(row.get("case_id", "")) for row in value.get("cases", [])}
    return _save_incremental(path, value, inspect_cases, run_dir, affected, complete=planned_ids == written_ids)


def inspect_cross_artifacts(run_dir: Path) -> list[dict[str, str]]:
    """Audit only facts→plan→cases semantics; generation checks already ran on write."""
    facts, plan, document = load_facts(run_dir), load_plan(run_dir), load_cases(run_dir)
    issues: list[dict[str, str]] = []
    if plan.get("source_digest") != _planning_fact_digest(facts):
        issues.append(_issue("stale_source", "repairable", "case-plan.json", "planning facts changed after the plan was written", "repair only affected function plans"))
    if document.get("source_plan_digest") != _semantic_content_digest(plan):
        issues.append(_issue("stale_source", "repairable", "function-cases.json", "plan changed after cases were written", "repair only affected function cases"))
    assignments_by_case: dict[str, set[tuple[str, int]]] = {}
    for assignment in plan.get("check_assignments", []):
        if assignment.get("disposition") != "case":
            continue
        try:
            assignments_by_case.setdefault(str(assignment.get("case_id", "")), set()).add(
                (str(assignment.get("transaction_ref", "")), int(assignment.get("check_index", 0)))
            )
        except (TypeError, ValueError):
            issues.append(_issue("cross_mapping", "repairable", "case-plan.json", "plan contains a malformed check assignment", "repair only this assignment"))
    planned: dict[str, tuple[str, set[tuple[str, int]]]] = {}
    for function in plan.get("functions", []):
        function_ref = str(function.get("function_ref", ""))
        for case in function.get("cases", []):
            case_id = str(case.get("case_id", ""))
            planned[str(case.get("case_id", ""))] = (
                function_ref,
                assignments_by_case.get(case_id, set()),
            )
    written: set[str] = set()
    for case in document.get("cases", []):
        case_id = str(case.get("case_id", ""))
        written.add(case_id)
        expected_function, expected_checks = planned.get(case_id, ("", set()))
        actual_checks: set[tuple[str, int]] = set()
        for step in case.get("steps", [])[1:]:
            source = step.get("source_check")
            if not isinstance(source, dict):
                continue
            try:
                actual_checks.add((str(source.get("transaction_ref", "")), int(source.get("check_index", 0))))
            except (TypeError, ValueError):
                issues.append(_issue("cross_mapping", "repairable", "function-cases.json", f"case {case_id} has a malformed source check", "repair only this step mapping", case_id=case_id))
        if str(case.get("function_ref", "")) != expected_function or actual_checks != expected_checks:
            issues.append(_issue("cross_mapping", "repairable", "function-cases.json", f"case {case_id} differs from its function/check plan", "repair only this case", case_id=case_id))
    if set(planned) != written:
        issues.append(_issue("cross_case_set", "repairable", "function-cases.json", "planned and written case sets differ", "add or remove only the differing cases"))
    primary_covered_elements: set[str] = set()
    auxiliary_covered_elements: set[str] = set()
    transactions = {str(row.get("fact_id", "")): row for row in facts.get("transactions", [])}
    for assignment in plan.get("check_assignments", []):
        transaction = transactions.get(str(assignment.get("transaction_ref", "")), {})
        try:
            check = transaction.get("checks", [])[int(assignment.get("check_index", 0)) - 1]
        except (IndexError, TypeError, ValueError):
            continue
        primary_ref = str(check.get("element_ref", "")).strip()
        if assignment.get("disposition") == "case":
            if primary_ref:
                primary_covered_elements.add(primary_ref)
            auxiliary_covered_elements.update(
                str(ref) for ref in check.get("used_element_refs", []) if str(ref).strip() and str(ref) != primary_ref
            )
    handled_non_case: set[str] = set()
    for assignment in plan.get("check_assignments", []):
        if assignment.get("disposition") == "case":
            continue
        transaction = transactions.get(str(assignment.get("transaction_ref", "")), {})
        try:
            check = transaction.get("checks", [])[int(assignment.get("check_index", 0)) - 1]
        except (IndexError, TypeError, ValueError):
            continue
        if check.get("element_ref"):
            handled_non_case.add(str(check.get("element_ref")))
    blocked_functions = {
        str(ref)
        for item in facts.get("open_items", [])
        if item.get("category") == "blocked_condition" and item.get("status") not in {"resolved", "accepted", "closed"}
        for ref in item.get("affected_function_refs", [])
    }
    for element in facts.get("elements", []):
        element_ref = str(element.get("fact_id", ""))
        if element.get("status") in NON_ACTIONABLE_STATUSES or element.get("interactive", True) is False:
            continue
        covered = element_ref in primary_covered_elements or (
            not element.get("exploration_requirements") and element_ref in auxiliary_covered_elements
        )
        if not covered and element_ref not in handled_non_case and str(element.get("function_ref", "")) not in blocked_functions:
            issues.append(_issue(
                "uncovered_element", "repairable", "function-cases.json",
                f"interactive element {element.get('name') or element_ref} has no case or explicit specialist disposition",
                "add only its missing independent intent or explicit non-case disposition",
                function_ref=str(element.get("function_ref", "")),
            ))
    return issues


def review_run(run_dir: Path, semantic_review: dict[str, Any] | None = None) -> dict[str, Any]:
    """Combine deterministic checks with one explicit model semantic audit."""
    facts = load_facts(run_dir)
    plan = load_plan(run_dir)
    cases = load_cases(run_dir)
    deterministic_issues = (
        inspect_discovery(run_dir, facts)
        + inspect_plan(run_dir)
        + inspect_cases(run_dir)
        + inspect_cross_artifacts(run_dir)
    )
    issues = list(deterministic_issues)
    unresolved = [
        item for item in facts.get("open_items", [])
        if item.get("status") not in {"resolved", "accepted", "closed"}
    ]
    for item in unresolved:
        category = item.get("category")
        material = item.get("material", True)
        affected = [str(ref) for ref in item.get("affected_function_refs", [])]
        if category in {"external_question", "blocked_condition"} and material:
            issues.append(_issue("open_material_fact", "blocker", "facts.json", str(item.get("description") or item.get("fact_id")), "resolve this external fact once", function_ref=",".join(affected)))
        else:
            issues.append(_issue("open_note", "warning", "facts.json", str(item.get("description") or item.get("fact_id")), "retain as a scoped delivery note", function_ref=",".join(affected)))
    semantic = semantic_review if isinstance(semantic_review, dict) else {}
    semantic_issues: list[dict[str, str]] = []
    expected_case_ids = [str(case.get("case_id", "")) for case in cases.get("cases", [])]
    reviewed_case_ids = [str(value) for value in semantic.get("reviewed_case_ids", [])]
    if not semantic:
        semantic_issues.append(_issue("semantic_review_missing", "repairable", "review.json", "the one model semantic review has not been supplied", "review the compact facts/plan/case projection once"))
    else:
        reviewed_sections = [str(value).strip() for value in semantic.get("reviewed_sections", []) if str(value).strip()]
        missing_sections = [section for section in REVIEW_SECTIONS if section not in reviewed_sections]
        if missing_sections:
            semantic_issues.append(_issue(
                "semantic_review_scope", "repairable", "review.json",
                f"semantic review did not cover sections: {missing_sections}",
                "review cases, performance, risks, automation, elements and cross-sheet conclusions in the same one-time audit",
            ))
        if reviewed_case_ids != expected_case_ids:
            semantic_issues.append(_issue("semantic_review_scope", "repairable", "review.json", "semantic review case order/set differs from function-cases.json", "review the current case set once in its existing order"))
        if not str(semantic.get("summary", "")).strip():
            semantic_issues.append(_issue("semantic_review_incomplete", "repairable", "review.json", "semantic review lacks a concise conclusion", "state the actual semantic finding once"))
        if not isinstance(semantic.get("issues", []), list):
            semantic_issues.append(_issue("semantic_review_incomplete", "repairable", "review.json", "semantic review issues must be an array", "supply concrete findings or an empty array"))
        for index, item in enumerate(semantic.get("issues", []), 1):
            if not isinstance(item, dict) or not str(item.get("message", "")).strip():
                semantic_issues.append(_issue("invalid_semantic_issue", "repairable", "review.json", f"semantic issue {index} is malformed", "record a concrete message and local repair"))
                continue
            severity = str(item.get("severity") or "repairable")
            if severity not in {"blocker", "repairable", "warning"}:
                severity = "repairable"
            semantic_issues.append(_issue(
                str(item.get("code") or "semantic_issue"),
                severity,
                str(item.get("artifact") or "function-cases.json"),
                str(item.get("message")),
                str(item.get("local_repair") or "apply one explicit local correction"),
                function_ref=str(item.get("function_ref", "")),
                case_id=str(item.get("case_id", "")),
            ))
    issues.extend(semantic_issues)
    issues = list({json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in issues}.values())
    if any(item["severity"] == "blocker" for item in issues):
        status = "blocked_by_fact"
    elif any(item["severity"] == "repairable" for item in issues):
        status = "needs_local_fix"
    elif issues:
        status = "ready_with_notes"
    else:
        status = "ready"
    review = {
        "schema_version": SCHEMA_VERSION,
        "reviewed_at": _now(),
        "status": status,
        "counts": {
            "functions": len(facts.get("functions", [])),
            "transactions": len(facts.get("transactions", [])),
            "planned_cases": sum(len(function.get("cases", [])) for function in plan.get("functions", [])),
            "written_cases": len(cases.get("cases", [])),
            "open_items": len(unresolved),
        },
        "sources": {
            **semantic_source_digests(run_dir)
        },
        "deterministic": {"status": "pass" if not deterministic_issues else "fail", "issues": deterministic_issues},
        "semantic": {
            "status": (
                "missing" if not semantic
                else "needs_local_fix" if any(item.get("severity") in {"blocker", "repairable"} for item in semantic_issues)
                else "pass_with_notes" if semantic_issues
                else "pass"
            ),
            "reviewed_case_ids": reviewed_case_ids,
            "reviewed_sections": [str(value) for value in semantic.get("reviewed_sections", [])] if semantic else [],
            "summary": str(semantic.get("summary", "")),
            "issues": semantic_issues,
            "local_fixes": semantic.get("local_fixes", []),
        },
        "issues": issues,
    }
    _write_json(artifact_paths(run_dir)["review"], review)
    return review


def pipeline_status(run_dir: Path) -> dict[str, Any]:
    """Describe resumable progress without acting as a phase gate."""
    paths = artifact_paths(run_dir)
    if not paths["facts"].exists():
        return {"stage": "scope_binding", "state": "transparent", "next_action": "invoke the Skill with a target menu path"}
    facts = load_facts(run_dir)
    if not paths["plan"].exists():
        pending = pending_exploration_requirements(run_dir)
        return {
            "stage": "discovery", "state": "continue",
            "counts": {key: len(facts.get(key, [])) for key in FACT_COLLECTIONS.values()},
            "remaining_exploration": pending,
            "next_action": "execute only the listed predeclared branches" if pending else "continue scanning and function transactions, then compile the plan",
        }
    plan = load_plan(run_dir)
    if plan.get("source_digest") != _planning_fact_digest(facts):
        return {"stage": "planning", "state": "needs_local_fix", "next_action": "repair only function plans affected by changed facts"}
    plan_issues = inspect_plan(run_dir)
    planned = {str(row.get("function_ref", "")) for row in plan.get("functions", [])}
    fact_function_refs = {str(row.get("fact_id", "")) for row in facts.get("functions", [])}
    missing_function_refs = fact_function_refs - planned
    plan_progress_codes = {"missing_function", "unassigned_check", "independent_branch_not_case"}
    blocking_plan_issues = [
        item for item in plan_issues
        if item.get("code") not in plan_progress_codes
        or (item.get("code") != "missing_function" and item.get("function_ref") not in missing_function_refs)
    ]
    if blocking_plan_issues:
        return {"stage": "planning", "state": "needs_local_fix", "issues": blocking_plan_issues, "next_action": "repair only the listed function plan"}
    if plan_issues:
        remaining = [str(row.get("fact_id", "")) for row in facts.get("functions", []) if str(row.get("fact_id", "")) not in planned]
        return {"stage": "planning", "state": "continue", "remaining_functions": remaining, "next_action": "upsert only the next unplanned function"}
    if not paths["cases"].exists():
        return {"stage": "planning", "state": "continue", "next_action": "write paired executable cases in plan order"}
    cases = load_cases(run_dir)
    if cases.get("source_plan_digest") != _semantic_content_digest(plan):
        return {"stage": "case_writing", "state": "needs_local_fix", "next_action": "repair only cases affected by the changed plan"}
    case_issues = inspect_cases(run_dir)
    missing_case_issues = [item for item in case_issues if item.get("code") == "missing_case"]
    other_case_issues = [item for item in case_issues if item.get("code") != "missing_case"]
    if other_case_issues:
        return {"stage": "case_writing", "state": "needs_local_fix", "issues": other_case_issues, "next_action": "repair only the listed function cases"}
    if missing_case_issues:
        written = {str(row.get("case_id", "")) for row in cases.get("cases", [])}
        remaining = [
            str(case.get("case_id", ""))
            for function in plan.get("functions", []) for case in function.get("cases", [])
            if str(case.get("case_id", "")) not in written
        ]
        return {"stage": "case_writing", "state": "continue", "remaining_cases": remaining, "next_action": "upsert only the next function case block"}
    if not paths["review"].exists():
        return {"stage": "case_writing", "state": "continue", "next_action": "run the single cross-artifact review"}
    review = _read_json(paths["review"])
    current_sources = semantic_source_digests(run_dir)
    if review.get("sources") != current_sources:
        return {
            "stage": "review", "state": "needs_local_fix",
            "issues": [_issue("stale_review", "repairable", "review.json", "upstream business content changed", "review the changed chain once")],
            "next_action": "run the single semantic review once",
        }
    if review.get("status") in {"ready", "ready_with_notes"} and paths["formal_workbook"].is_file() and paths["import_workbook"].is_file():
        return {
            "stage": "completed", "state": "completed", "issues": review.get("issues", []),
            "deliverables": {
                "formal_workbook": str(paths["formal_workbook"].resolve()),
                "import_workbook": str(paths["import_workbook"].resolve()),
            },
            "next_action": "none",
        }
    return {"stage": "review", "state": review.get("status"), "issues": review.get("issues", []), "next_action": "deliver" if review.get("status") in {"ready", "ready_with_notes"} else "apply only the listed local repair"}


# Kept as a Python compatibility alias for integrations; it is intentionally absent
# from the user-facing CLI and documentation.
init_run = ensure_run
