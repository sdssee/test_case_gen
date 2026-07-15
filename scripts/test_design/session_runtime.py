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
PLACEHOLDER_MARKERS = ("TODO", "TBD", "待补充", "请补充", "示例数据", "占位", "输入测试数据", "填写测试数据")
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


def _is_input_element(element: dict[str, Any]) -> bool:
    kind = str(element.get("type", "")).strip().lower()
    return any(marker in kind for marker in ("输入", "input", "textarea", "文本框", "数字框", "密码框"))


def _is_trigger_element(element: dict[str, Any]) -> bool:
    kind = str(element.get("type", "")).strip().lower()
    return any(marker in kind for marker in ("按钮", "button", "submit"))


def _action_has_trigger(action: str) -> bool:
    return bool(re.search(r"点击|单击|提交|执行|保存|查询|搜索|确认|触发|click|submit|execute|run", action, re.IGNORECASE))


def _required_input_classes(element: dict[str, Any]) -> set[str]:
    if not _is_input_element(element):
        return set()
    constraints = element.get("constraints") if isinstance(element.get("constraints"), dict) else {}
    required = {"valid"}
    if element.get("required") is True or constraints.get("required") is True:
        required.add("empty")
    if element.get("input_format") or constraints.get("format") or constraints.get("pattern"):
        required.add("invalid_format")
    if element.get("min_value") is not None or element.get("min_length") is not None or constraints.get("min") is not None or constraints.get("min_length") is not None:
        required.add("boundary_min")
    if element.get("max_value") is not None or element.get("max_length") is not None or constraints.get("max") is not None or constraints.get("max_length") is not None:
        required.add("boundary_max")
    return required


def _input_class_matches(required: str, observed: set[str]) -> bool:
    if required == "valid":
        return any(value == "valid" or value.startswith("valid_") for value in observed)
    return required in observed


def _validate_new_transactions(run_dir: Path, items: list[dict[str, Any]]) -> None:
    """Validate discovery completeness once, immediately before appending the batch."""
    latest = {str(event.get("fact_id", "")): event for event in load_events(run_dir)}
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
        for ref in declared:
            element = elements[ref]
            related = [check for check in checks if ref in {str(value) for value in check.get("used_element_refs", [])}]
            required_classes = _required_input_classes(element)
            if required_classes:
                observed_classes = {str(check.get("input_class", "")).strip() for check in related if str(check.get("input_class", "")).strip()}
                missing_classes = sorted(value for value in required_classes if not _input_class_matches(value, observed_classes))
                if missing_classes:
                    raise ValueError(f"input element {ref} lacks onsite branches: {missing_classes}")
            options = [str(value) for value in element.get("options", [])]
            if element.get("option_set") == "finite" and options:
                selected = {str(check.get("option_value")) for check in related if check.get("option_value") is not None}
                missing_options = sorted(set(options) - selected)
                if missing_options:
                    raise ValueError(f"finite options were not actually selected for {ref}: {missing_options}")
            if element.get("configuration") is True and element.get("default_value") is not None:
                selected = {str(check.get("option_value")) for check in related if check.get("option_value") is not None}
                if str(element.get("default_value")) not in selected:
                    raise ValueError(f"configuration element {ref} lacks its default/unconfigured baseline")
        for index, check in enumerate(checks, 1):
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

    New facts receive runtime IDs.  A batch may give an event ``local_ref`` and use
    ``@local_ref`` in later event data; aliases are removed before persistence.
    Existing facts are updated by passing their returned ``fact_id``.
    """
    paths = artifact_paths(run_dir)
    raw_items = [dict(event) for event in events]
    aliases: dict[str, str] = {}
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        alias = str(raw.pop("local_ref", "")).strip()
        item = _prepare_event(raw)
        if alias:
            if alias in aliases:
                raise ValueError(f"duplicate local_ref: {alias}")
            aliases[alias] = item["fact_id"]
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
    _validate_new_transactions(run_dir, items)
    paths["events"].parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in items)
    with paths["events"].open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    return items


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
    for key in FACT_COLLECTIONS.values():
        facts[key].sort(key=lambda row: str(row.get("fact_id", "")))
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
        if page.get("final_scan_status") != "stable":
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"page {page['fact_id']} has no stable final scan", "perform one final full scan"))
        if page.get("unhandled_element_refs"):
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"page {page['fact_id']} still has unhandled elements", "explore only the listed elements"))
    for element in facts.get("elements", []):
        ref = str(element.get("fact_id"))
        function_ref = str(element.get("function_ref", ""))
        if function_ref and function_ref not in function_refs:
            issues.append(_issue("broken_reference", "blocker", "facts.json", f"element {ref} references unknown function {function_ref}", "correct this element relation"))
        if element.get("interactive", True) and element.get("status") not in NON_ACTIONABLE_STATUSES and not transaction_by_element.get(ref):
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"interactive element {ref} was not actually operated", "execute one related function transaction"))
        page_ref = str(element.get("page_ref", ""))
        if page_ref not in page_refs:
            issues.append(_issue("broken_reference", "blocker", "facts.json", f"element {ref} references unknown page {page_ref!r}", "bind the element to the observed page"))
        if element.get("dynamic") is True and not str(element.get("trigger_condition", "")).strip():
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"dynamic element {ref} lacks its trigger condition", "record the action or state that makes it appear"))
    for transaction in facts.get("transactions", []):
        kind = transaction.get("transaction_type")
        if kind in {"create", "edit", "configuration", "delete"}:
            if transaction.get("test_object_ref") not in test_object_refs:
                issues.append(_issue("broken_reference", "blocker", "facts.json", f"{kind} transaction {transaction['fact_id']} has no current-run test object", "bind the current-run test object"))
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
    hints: list[dict[str, Any]] = []
    constraints = element.get("constraints") if isinstance(element.get("constraints"), dict) else {}
    candidates = (
        (element.get("required") is True or constraints.get("required") is True, "empty", "必填项为空"),
        (any(element.get(key) is not None for key in ("min_value", "max_value", "min_length", "max_length")) or any(constraints.get(key) is not None for key in ("min", "max", "min_value", "max_value", "min_length", "max_length")), "boundary", "已观察到的输入边界"),
        (element.get("unique") is True or constraints.get("unique") is True, "duplicate", "唯一性冲突"),
        (element.get("option_set") == "finite", "finite_options", "有限选项逐项结果"),
    )
    for applies, code, reason in candidates:
        if applies:
            hints.append({"code": code, "scope": "element", "scope_ref": str(element.get("fact_id", "")), "reason": reason})
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
            }
            for transaction in owned_transactions
            for index, check in enumerate(transaction.get("checks", []), 1)
        ]
        unique_hints = _function_dfx_hints(owned_elements, owned_transactions, function_ref)
        for hint in unique_hints:
            related_checks: list[dict[str, Any]] = []
            for transaction in owned_transactions:
                transaction_ref = str(transaction.get("fact_id", ""))
                if hint.get("scope") == "transaction" and hint.get("scope_ref") != transaction_ref:
                    continue
                for index, check in enumerate(transaction.get("checks", []), 1):
                    used_refs = {str(value) for value in check.get("used_element_refs", [])}
                    if check.get("element_ref"):
                        used_refs.add(str(check.get("element_ref")))
                    if hint.get("scope") == "element" and str(hint.get("scope_ref", "")) not in used_refs:
                        continue
                    tags = {str(tag) for tag in check.get("dfx_tags", [])}
                    hint_code = str(hint.get("code", ""))
                    input_class = str(check.get("input_class", ""))
                    if hint_code == "empty" and input_class != "empty":
                        continue
                    if hint_code == "boundary" and not (input_class.startswith("boundary_") or "boundary" in tags):
                        continue
                    if hint_code == "duplicate" and not (input_class == "duplicate" or "duplicate" in tags):
                        continue
                    if hint_code == "finite_options" and check.get("option_value") is None:
                        continue
                    if tags and hint_code not in tags and hint_code not in {"empty", "boundary", "duplicate", "finite_options"}:
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
            "dfx_hints": unique_hints,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "facts.json",
        "checkpoint": _current_checkpoint(facts),
        "functions": functions,
    }


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
    pages = {str(row["fact_id"]): row for row in facts.get("pages", [])}
    planned_functions: set[str] = set()
    case_ids: set[str] = set()
    titles: set[tuple[str, str]] = set()
    for function in plan.get("functions", []):
        function_ref = str(function.get("function_ref", ""))
        if function_ref not in functions:
            issues.append(_issue("broken_reference", "repairable", "case-plan.json", f"unknown function {function_ref!r}", "repair this function mapping", function_ref=function_ref))
        if function_ref in planned_functions:
            issues.append(_issue("duplicate_mapping", "repairable", "case-plan.json", f"function {function_ref} appears more than once", "merge this function block", function_ref=function_ref))
        planned_functions.add(function_ref)
        cases = function.get("cases", [])
        if not cases or not any(str(case.get("strategy", "")).lower() == "baseline" for case in cases):
            issues.append(_issue("missing_baseline", "repairable", "case-plan.json", f"function {function_ref} lacks a baseline intent", "add its observed baseline intent", function_ref=function_ref))
        for case in cases:
            case_id = str(case.get("case_id", "")).strip()
            if not case_id or case_id in case_ids:
                issues.append(_issue("invalid_case_id", "repairable", "case-plan.json", f"empty or duplicate case ID {case_id!r}", "assign one stable case ID", function_ref=function_ref, case_id=case_id))
            case_ids.add(case_id)
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
            if str(case.get("strategy", "")).lower() != "baseline" and (
                not str(case.get("dfx_dimension", "")).strip() or not str(case.get("dfx_scenario", "")).strip()
            ):
                issues.append(_issue("missing_dfx", "repairable", "case-plan.json", f"DFX case {case_id} lacks dimension or scenario", "complete DFX while planning", function_ref=function_ref, case_id=case_id))
    missing_functions = set(functions) - planned_functions
    if missing_functions:
        issues.append(_issue("missing_function", "repairable", "case-plan.json", f"functions missing from plan: {sorted(missing_functions)}", "plan only the missing function blocks"))
    case_owners = {
        str(case.get("case_id", "")): str(function.get("function_ref", ""))
        for function in plan.get("functions", [])
        for case in function.get("cases", [])
    }
    seen_assignments: set[tuple[str, int]] = set()
    assigned_case_ids: set[str] = set()
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
    empty_cases = set(case_owners) - assigned_case_ids
    if empty_cases:
        issues.append(_issue("unassigned_case", "repairable", "case-plan.json", f"cases have no assigned checks: {sorted(empty_cases)}", "assign observed checks or remove these empty intents"))
    all_checks = {
        (str(transaction["fact_id"]), index)
        for transaction in facts.get("transactions", [])
        for index, _ in enumerate(transaction.get("checks", []), 1)
    }
    unassigned = all_checks - seen_assignments
    if unassigned:
        issues.append(_issue("unassigned_check", "repairable", "case-plan.json", f"transaction checks have no planned disposition: {sorted(unassigned)}", "assign only these checks to a case or explicit non-case disposition"))
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


def _expected_satisfies_anchor(expected: str, check: dict[str, Any]) -> bool:
    anchor = check.get("result_anchor") if isinstance(check.get("result_anchor"), dict) else {}
    assertion = str(anchor.get("assertion", ""))
    if assertion == "observed_text" or not assertion:
        value = str(anchor.get("value") or check.get("result", "")).strip()
        return not value or value in expected
    raw_tokens = anchor.get("tokens")
    if raw_tokens not in (None, "", []):
        values = raw_tokens if isinstance(raw_tokens, list) else [raw_tokens]
    else:
        value = anchor.get("value")
        values = value if isinstance(value, list) else [value]
    tokens = [str(item).strip() for item in values if item not in (None, "") and str(item).strip()]
    return all(token in expected for token in tokens)


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
        preconditions = case.get("preconditions")
        if not isinstance(preconditions, list) or not preconditions or any(not str(value).strip() for value in preconditions):
            issues.append(_issue("empty_field", "repairable", "function-cases.json", f"case {case_id} has no explicit preconditions", "write concrete conditions or '无特殊前置条件'", **context))
        planned_title = str(planned_case.get("title", "")).strip()
        if function_name and planned_title and title != f"{function_name}-{planned_title}":
            issues.append(_issue("plan_mismatch", "repairable", "function-cases.json", f"case {case_id} title differs from its planned intent", "restore this planned title", **context))
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
            if not _expected_satisfies_anchor(str(step.get("expected", "")), check):
                issues.append(_issue("ungrounded_expected", "blocker", "function-cases.json", f"case {case_id} step {index} does not preserve its observed result", "rewrite this expected result from the transaction fact", **context))
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
        if URL_PATTERN.search(prose) or IPV4_PATTERN.search(prose):
            issues.append(_issue("sensitive_network", "blocker", "function-cases.json", f"case {case_id} exposes a URL or IP address", "replace it with a controlled masked test-data reference", **context))
        refs = {str(value) for value in case.get("fact_refs", [])}
        planned_refs = {str(value) for value in planned_case.get("fact_refs", [])}
        if not refs or not refs <= fact_ids or not planned_refs <= refs:
            issues.append(_issue("ungrounded_case", "blocker", "function-cases.json", f"case {case_id} is not grounded in all planned facts", "restore only the missing fact mapping; do not invent an expected result", **context))
        signature = _normalize_core_prose(steps)
        signature_key = (function_ref, signature)
        if signature and signature_key in signatures:
            issues.append(_issue("duplicate_core", "repairable", "function-cases.json", f"case {case_id} duplicates the core actions and results of {signatures[signature_key]}", "rewrite or merge this planned scenario", **context))
        if signature:
            signatures[signature_key] = case_id
        for field in ("dfx_dimension", "dfx_scenario"):
            if str(case.get(field, "")).strip() != str(planned_case.get(field, "")).strip():
                issues.append(_issue("plan_mismatch", "repairable", "function-cases.json", f"case {case_id} {field} differs from plan", "restore this planned value", **context))
    missing = set(planned) - actual_ids
    if missing:
        issues.append(_issue("missing_case", "repairable", "function-cases.json", f"planned cases were not written: {sorted(missing)}", "generate only the missing cases"))
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
    transactions = {str(row["fact_id"]): row for row in facts.get("transactions", [])}
    by_case: dict[str, list[dict[str, Any]]] = {}
    for assignment in value.get("check_assignments", []):
        if assignment.get("disposition") == "case":
            by_case.setdefault(str(assignment.get("case_id", "")), []).append(assignment)
    for function in value.get("functions", []):
        function_ref = str(function.get("function_ref", ""))
        for case in function.get("cases", []):
            case_id = str(case.get("case_id", ""))
            case["fact_refs"] = _derive_case_fact_refs(
                function_ref, str(case.get("page_ref", "")), by_case.get(case_id, []), transactions,
            )
    return value


def save_plan(run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    facts = load_facts(run_dir)
    checkpoint = _current_checkpoint(facts)
    if checkpoint.get("ready") is not True:
        raise ValueError("discovery checkpoint is not ready: " + json.dumps(checkpoint, ensure_ascii=False))
    value = _normalize_plan(run_dir, plan)
    return _save_with_construction_check(artifact_paths(run_dir)["plan"], value, inspect_plan, run_dir)


def _assign_case_sources(run_dir: Path, cases: dict[str, Any]) -> dict[str, Any]:
    plan = load_plan(run_dir)
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
    for case in value.get("cases", []):
        case_id = str(case.get("case_id", ""))
        planned = planned_cases.get(case_id, {})
        steps = case.get("steps", []) if isinstance(case.get("steps"), list) else []
        sources = assignments.get(case_id, [])
        for step in steps:
            if isinstance(step, dict):
                step.pop("source_check", None)
        if len(steps[1:]) == len(sources):
            for step, source in zip(steps[1:], sources):
                step["source_check"] = {
                    "transaction_ref": str(source.get("transaction_ref", "")),
                    "check_index": int(source.get("check_index", 0)),
                }
        case["fact_refs"] = list(planned.get("fact_refs", []))
    value["source_plan"] = "case-plan.json"
    value["source_plan_digest"] = _semantic_content_digest(plan)
    return value


def save_cases(run_dir: Path, cases: dict[str, Any]) -> dict[str, Any]:
    value = _assign_case_sources(run_dir, cases)
    return _save_with_construction_check(artifact_paths(run_dir)["cases"], value, inspect_cases, run_dir)


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
    return issues


def review_run(run_dir: Path) -> dict[str, Any]:
    """Run one read-only semantic audit and write its compact result."""
    facts = load_facts(run_dir)
    plan = load_plan(run_dir)
    cases = load_cases(run_dir)
    issues = inspect_cross_artifacts(run_dir)
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
        return {"stage": "discovery", "state": "continue", "counts": {key: len(facts.get(key, [])) for key in FACT_COLLECTIONS.values()}, "next_action": "continue scanning and function transactions, then compile the plan"}
    plan = load_plan(run_dir)
    if plan.get("source_digest") != _planning_fact_digest(facts):
        return {"stage": "planning", "state": "needs_local_fix", "next_action": "repair only function plans affected by changed facts"}
    if not paths["cases"].exists():
        return {"stage": "planning", "state": "continue", "next_action": "write paired executable cases in plan order"}
    cases = load_cases(run_dir)
    if cases.get("source_plan_digest") != _semantic_content_digest(plan):
        return {"stage": "case_writing", "state": "needs_local_fix", "next_action": "repair only cases affected by the changed plan"}
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
    return {"stage": "review", "state": review.get("status"), "issues": review.get("issues", []), "next_action": "deliver" if review.get("status") in {"ready", "ready_with_notes"} else "apply only the listed local repair"}


# Kept as a Python compatibility alias for integrations; it is intentionally absent
# from the user-facing CLI and documentation.
init_run = ensure_run
