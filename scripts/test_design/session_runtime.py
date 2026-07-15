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


def artifact_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        raise ValueError("event.fact_id must not be empty")
    data = item.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("event.data must be an object")
    if kind == "transaction":
        checks = data.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("transaction.data.checks must be a non-empty array")
        for index, check in enumerate(checks, 1):
            if not isinstance(check, dict):
                raise ValueError(f"transaction check {index} must be an object")
            if not str(check.get("action", "")).strip() or not str(check.get("result", "")).strip():
                raise ValueError(f"transaction check {index} requires action and result")
    item["kind"] = kind
    item["fact_id"] = fact_id
    item["data"] = data
    item.setdefault("event_id", f"EVT-{uuid.uuid4().hex[:12].upper()}")
    item.setdefault("observed_at", _now())
    item.setdefault("status", "active")
    return item


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
    scope = {
        "run_id": run_dir.name,
        "module_path": module_path,
        "menu_path": str(scope_fields.pop("menu_path", "") or module_path),
        "product_name": product_name.strip(),
        "source": source.strip(),
        "created_at": _now(),
        **scope_fields,
    }
    append_events(run_dir, [{"kind": "scope", "fact_id": "SCOPE", "data": scope}])
    compile_facts(run_dir)
    return scope


def append_event(run_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    return append_events(run_dir, [event])[0]


def append_events(run_dir: Path, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate the complete batch before appending any line."""
    paths = artifact_paths(run_dir)
    items = [_prepare_event(event) for event in events]
    if not items:
        return []
    paths["events"].parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in items)
    with paths["events"].open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    return items


def load_events(run_dir: Path) -> list[dict[str, Any]]:
    path = artifact_paths(run_dir)["events"]
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
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


def load_facts(run_dir: Path) -> dict[str, Any]:
    path = artifact_paths(run_dir)["facts"]
    if not path.exists():
        raise ValueError("facts.json does not exist")
    facts = _read_json(path)
    events = load_events(run_dir)
    last_event_id = str(events[-1].get("event_id", "")) if events else ""
    if facts.get("event_count") != len(events) or facts.get("last_event_id", "") != last_event_id:
        raise ValueError("facts.json is stale; compile the newly recorded events once")
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
    refs = {str(value) for value in transaction.get("element_refs", [])}
    refs.update(str(check.get("element_ref")) for check in transaction.get("checks", []) if check.get("element_ref"))
    return refs


def inspect_discovery(run_dir: Path) -> list[dict[str, str]]:
    try:
        facts = load_facts(run_dir)
    except ValueError as exc:
        return [_issue("missing_fact", "blocker", "facts.json", str(exc), "resume the current discovery session")]
    issues: list[dict[str, str]] = []
    for key in ("pages", "functions", "elements", "transactions"):
        if not facts.get(key):
            issues.append(_issue("missing_fact", "blocker", "facts.json", f"no {key} were recorded", "record the missing page fact once"))
    function_refs = {str(row.get("fact_id")) for row in facts.get("functions", [])}
    element_refs = {str(row.get("fact_id")) for row in facts.get("elements", [])}
    test_object_refs = {str(row.get("fact_id")) for row in facts.get("test_objects", [])}
    transaction_by_element: dict[str, list[dict[str, Any]]] = {ref: [] for ref in element_refs}
    for transaction in facts.get("transactions", []):
        for ref in _transaction_check_refs(transaction):
            transaction_by_element.setdefault(ref, []).append(transaction)
    for page in facts.get("pages", []):
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
        options = [str(value) for value in element.get("options", [])]
        if element.get("option_set") == "finite" and options:
            selected = {
                str(check.get("option_value"))
                for transaction in transaction_by_element.get(ref, [])
                for check in transaction.get("checks", [])
                if str(check.get("element_ref", ref)) == ref and check.get("option_value") is not None
            }
            missing = set(options) - selected
            if missing:
                issues.append(_issue("missing_fact", "blocker", "facts.json", f"finite options not selected for {ref}: {sorted(missing)}", "select only the missing options in the current session"))
        if element.get("configuration") is True:
            checks = [
                check
                for transaction in transaction_by_element.get(ref, [])
                for check in transaction.get("checks", [])
                if str(check.get("element_ref", ref)) == ref
            ]
            default = element.get("default_value")
            values = {str(check.get("option_value")): check for check in checks if check.get("option_value") is not None}
            for option in options:
                check = values.get(option)
                required = ("commit_result", "persistence_result", "effect_result", "recovery_result")
                if not check or any(not str(check.get(field, "")).strip() for field in required):
                    issues.append(_issue("missing_fact", "blocker", "facts.json", f"configuration value {option!r} for {ref} lacks a save/reopen/effect/recovery closure", "execute this single configuration value once"))
            if default is not None and str(default) not in values:
                issues.append(_issue("missing_fact", "blocker", "facts.json", f"configuration element {ref} lacks its default baseline", "verify the default/unconfigured value"))
    for transaction in facts.get("transactions", []):
        kind = transaction.get("transaction_type")
        if kind in {"create", "edit", "configuration", "delete"}:
            if transaction.get("outcome") != "success":
                issues.append(_issue("missing_fact", "blocker", "facts.json", f"{kind} transaction {transaction['fact_id']} did not complete successfully", "resume only this business transaction"))
            if transaction.get("test_object_ref") not in test_object_refs:
                issues.append(_issue("broken_reference", "blocker", "facts.json", f"{kind} transaction {transaction['fact_id']} has no current-run test object", "bind the current-run test object"))
        if kind in {"create", "edit", "delete"}:
            required = ["commit_result", "effect_result"]
            if kind in {"create", "edit"}:
                required.append("persistence_result")
            if kind == "edit":
                required.append("recovery_result")
            for field in required:
                if not str(transaction.get(field, "")).strip() and not any(str(check.get(field, "")).strip() for check in transaction.get("checks", [])):
                    issues.append(_issue("missing_fact", "blocker", "facts.json", f"{kind} transaction {transaction['fact_id']} lacks {field}", "complete only this business closure"))
        if kind == "configuration" and transaction.get("combination"):
            issues.append(_issue("invalid_scope", "repairable", "facts.json", f"configuration transaction {transaction['fact_id']} uses a combination", "retain single-factor checks only"))
    for test_object in facts.get("test_objects", []):
        if test_object.get("owner") == "current_run" and test_object.get("state") not in {"cleaned", "deleted", "retained_for_followup"}:
            issues.append(_issue("unsafe_lifecycle", "blocker", "facts.json", f"current-run test object {test_object['fact_id']} has no safe final state", "clean it up or explicitly retain it for follow-up"))
    return issues


def inspect_plan(run_dir: Path) -> list[dict[str, str]]:
    try:
        facts, plan = load_facts(run_dir), load_plan(run_dir)
    except ValueError as exc:
        return [_issue("missing_artifact", "blocker", "case-plan.json", str(exc), "create the missing artifact from facts")]
    issues: list[dict[str, str]] = []
    if plan.get("source") != "facts.json":
        issues.append(_issue("invalid_source", "repairable", "case-plan.json", "plan source must be facts.json", "repair the source declaration"))
    fact_ids = _all_fact_ids(facts)
    functions = {str(row["fact_id"]): row for row in facts.get("functions", [])}
    transactions = {str(row["fact_id"]): row for row in facts.get("transactions", [])}
    elements = {str(row["fact_id"]): row for row in facts.get("elements", [])}
    planned_functions: set[str] = set()
    covered_elements_by_function: dict[str, set[str]] = {}
    case_ids: set[str] = set()
    titles: set[tuple[str, str]] = set()
    assigned_checks: set[tuple[str, int]] = set()
    for function in plan.get("functions", []):
        function_ref = str(function.get("function_ref", ""))
        if function_ref not in functions:
            issues.append(_issue("broken_reference", "repairable", "case-plan.json", f"unknown function {function_ref!r}", "repair this function mapping", function_ref=function_ref))
        if function_ref in planned_functions:
            issues.append(_issue("duplicate_mapping", "repairable", "case-plan.json", f"function {function_ref} appears more than once", "merge this function block", function_ref=function_ref))
        planned_functions.add(function_ref)
        cases = function.get("cases", [])
        function_elements = covered_elements_by_function.setdefault(function_ref, set())
        if not cases or not any(str(case.get("strategy", "")).lower() == "baseline" for case in cases):
            issues.append(_issue("missing_baseline", "repairable", "case-plan.json", f"function {function_ref} lacks a baseline intent", "add its observed baseline intent", function_ref=function_ref))
        for case in cases:
            case_id = str(case.get("case_id", "")).strip()
            if not case_id or case_id in case_ids:
                issues.append(_issue("invalid_case_id", "repairable", "case-plan.json", f"empty or duplicate case ID {case_id!r}", "assign one stable case ID", function_ref=function_ref, case_id=case_id))
            case_ids.add(case_id)
            refs = {str(value) for value in case.get("fact_refs", [])}
            function_elements.update(refs & set(elements))
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
            for transaction_ref, indexes in case.get("covered_checks", {}).items():
                transaction = transactions.get(str(transaction_ref))
                if not transaction:
                    issues.append(_issue("broken_reference", "repairable", "case-plan.json", f"case {case_id} covers unknown transaction {transaction_ref}", "repair only this check mapping", function_ref=function_ref, case_id=case_id))
                    continue
                if str(transaction.get("function_ref", "")) != function_ref:
                    issues.append(_issue("wrong_function", "repairable", "case-plan.json", f"case {case_id} covers a transaction owned by another function", "assign it to its owning function", function_ref=function_ref, case_id=case_id))
                for index in indexes:
                    numeric = int(index)
                    if numeric < 1 or numeric > len(transaction.get("checks", [])):
                        issues.append(_issue("broken_reference", "repairable", "case-plan.json", f"case {case_id} covers invalid check {transaction_ref}#{numeric}", "repair only this check index", function_ref=function_ref, case_id=case_id))
                        continue
                    assigned_checks.add((str(transaction_ref), numeric))
                    check = transaction["checks"][numeric - 1]
                    if check.get("element_ref"):
                        function_elements.add(str(check["element_ref"]))
    missing_functions = set(functions) - planned_functions
    if missing_functions:
        issues.append(_issue("missing_function", "repairable", "case-plan.json", f"functions missing from plan: {sorted(missing_functions)}", "plan only the missing function blocks"))
    for element_ref, element in elements.items():
        function_ref = str(element.get("function_ref", ""))
        if element.get("status") not in NON_ACTIONABLE_STATUSES and element_ref not in covered_elements_by_function.get(function_ref, set()):
            issues.append(_issue("missing_element_coverage", "repairable", "case-plan.json", f"element {element_ref} is not covered by a case owned by function {function_ref}", "assign it within its owning function", function_ref=function_ref))
    non_case = {
        (str(item.get("transaction_ref")), int(index))
        for item in plan.get("non_case_checks", [])
        for index in item.get("check_indexes", [])
        if item.get("disposition") in {"performance", "risk", "not_applicable"}
    }
    all_checks = {
        (str(transaction["fact_id"]), index)
        for transaction in facts.get("transactions", [])
        for index, _ in enumerate(transaction.get("checks", []), 1)
    }
    unassigned = all_checks - assigned_checks - non_case
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


def inspect_cases(run_dir: Path) -> list[dict[str, str]]:
    try:
        facts, plan, document = load_facts(run_dir), load_plan(run_dir), load_cases(run_dir)
    except ValueError as exc:
        return [_issue("missing_artifact", "blocker", "function-cases.json", str(exc), "create the missing artifact from the plan")]
    issues: list[dict[str, str]] = []
    if document.get("source_plan") != "case-plan.json":
        issues.append(_issue("invalid_source", "repairable", "function-cases.json", "case source_plan must be case-plan.json", "repair the source declaration"))
    fact_ids = _all_fact_ids(facts)
    planned: dict[str, tuple[str, dict[str, Any], str]] = {}
    for function in plan.get("functions", []):
        function_ref = str(function.get("function_ref", ""))
        function_name = str(function.get("name", ""))
        for case in function.get("cases", []):
            planned[str(case.get("case_id", ""))] = (function_ref, case, function_name)
    actual_ids: set[str] = set()
    closed_functions: set[str] = set()
    previous_function = ""
    signatures: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}
    menu_path = _normalize_menu_path(str(facts.get("scope", {}).get("menu_path") or facts.get("scope", {}).get("module_path", "")))
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
            first_action = str(steps[0].get("action", ""))
            if "进入" not in first_action or (menu_path and menu_path not in _normalize_menu_path(first_action)):
                issues.append(_issue("missing_navigation", "repairable", "function-cases.json", f"case {case_id} must start from complete menu path {menu_path!r}", "repair its first navigation step", **context))
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
        messages = " | ".join(item["message"] for item in issues[:10])
        raise ValueError(f"construction needs a local correction: {messages}")
    return value


def save_plan(run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    return _save_with_construction_check(artifact_paths(run_dir)["plan"], plan, inspect_plan, run_dir)


def save_cases(run_dir: Path, cases: dict[str, Any]) -> dict[str, Any]:
    return _save_with_construction_check(artifact_paths(run_dir)["cases"], cases, inspect_cases, run_dir)


def review_run(run_dir: Path) -> dict[str, Any]:
    """Run one read-only semantic audit and write its compact result."""
    facts = load_facts(run_dir)
    plan = load_plan(run_dir)
    cases = load_cases(run_dir)
    issues = inspect_discovery(run_dir) + inspect_plan(run_dir) + inspect_cases(run_dir)
    unresolved = [
        item for item in facts.get("open_items", [])
        if item.get("status") not in {"resolved", "accepted", "closed"}
    ]
    for item in unresolved:
        category = item.get("category")
        material = item.get("material", True)
        if category in {"external_question", "blocked_condition"} and material:
            issues.append(_issue("open_material_fact", "blocker", "facts.json", str(item.get("description") or item.get("fact_id")), "resolve externally once or record the real blocked condition"))
        else:
            issues.append(_issue("open_note", "warning", "facts.json", str(item.get("description") or item.get("fact_id")), "retain as a delivery note"))
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
            name: artifact_digest(artifact_paths(run_dir)[name])
            for name in ("facts", "plan", "cases")
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
    if not paths["cases"].exists():
        return {"stage": "planning", "state": "continue", "next_action": "write paired executable cases in plan order"}
    if not paths["review"].exists():
        return {"stage": "case_writing", "state": "continue", "next_action": "run the single cross-artifact review"}
    review = _read_json(paths["review"])
    return {"stage": "review", "state": review.get("status"), "issues": review.get("issues", []), "next_action": "deliver" if review.get("status") in {"ready", "ready_with_notes"} else "apply only the listed local repair"}


# Kept as a Python compatibility alias for integrations; it is intentionally absent
# from the user-facing CLI and documentation.
init_run = ensure_run
