# -*- coding: utf-8 -*-
"""Single-session test-design runtime.

The runtime deliberately owns only stage artifacts. Browser/computer-use remains in
the same model session; one meaningful observation is appended after an interaction
transaction instead of creating a task/gate for every click.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.0"
EVENT_KINDS = {
    "scope", "requirement", "page", "function", "element", "observation",
    "test_object", "risk", "pending", "absence",
}
FACT_COLLECTIONS = {
    "requirement": "requirements",
    "page": "pages",
    "function": "functions",
    "element": "elements",
    "observation": "observations",
    "test_object": "test_objects",
    "risk": "risks",
    "pending": "pending",
    "absence": "absences",
}
INTERNAL_STEP_MARKERS = re.compile(
    r"(?:^|[^a-z])(?:uid|uuid|fact[_ -]?id|element[_ -]?id|interaction[_ -]?id|"
    r"dom|accessibility tree|aria|selector|xpath|css selector)(?:$|[^a-z])",
    re.IGNORECASE,
)
SCREENSHOT_MARKERS = ("截图", "截屏", "screenshot", "screen shot")
PLACEHOLDER_MARKERS = ("TODO", "TBD", "待补充", "示例", "占位")


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


def artifact_paths(run_dir: Path) -> dict[str, Path]:
    run_dir = run_dir.resolve()
    return {
        "scope": run_dir / "scope.json",
        "events": run_dir / "artifacts" / "discovery" / "events.jsonl",
        "facts": run_dir / "artifacts" / "discovery" / "facts.json",
        "evidence": run_dir / "artifacts" / "discovery" / "evidence",
        "plan": run_dir / "case-plan.json",
        "cases": run_dir / "function-cases.json",
        "review": run_dir / "review.json",
        "delivery": run_dir / "deliverables",
    }


def init_run(run_dir: Path, module_path: str, product_name: str = "", source: str = "") -> dict[str, Any]:
    paths = artifact_paths(run_dir)
    if paths["scope"].exists() or paths["events"].exists():
        raise ValueError(f"run directory is already initialized: {run_dir}")
    scope = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_dir.name,
        "product_name": product_name.strip(),
        "module_path": module_path.strip(),
        "source": source.strip(),
        "created_at": _now(),
    }
    if not scope["module_path"]:
        raise ValueError("module_path must not be empty")
    paths["evidence"].mkdir(parents=True, exist_ok=True)
    paths["events"].touch()
    _write_json(paths["scope"], scope)
    compile_facts(run_dir)
    return scope


def append_event(run_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    paths = artifact_paths(run_dir)
    if not paths["scope"].exists():
        raise ValueError("run is not initialized")
    item = dict(event)
    kind = str(item.get("kind", "")).strip()
    fact_id = str(item.get("fact_id", "")).strip()
    if kind not in EVENT_KINDS:
        raise ValueError(f"unsupported event kind: {kind!r}")
    if not fact_id:
        raise ValueError("event.fact_id must not be empty")
    item.setdefault("event_id", f"EVT-{uuid.uuid4().hex[:12].upper()}")
    item.setdefault("observed_at", _now())
    item.setdefault("status", "observed")
    item.setdefault("data", {})
    if not isinstance(item["data"], dict):
        raise ValueError("event.data must be an object")
    with paths["events"].open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return item


def append_events(run_dir: Path, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [append_event(run_dir, event) for event in events]


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


def _resolved_evidence(run_dir: Path, value: str) -> Path | None:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (run_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    evidence_root = artifact_paths(run_dir)["evidence"].resolve()
    try:
        candidate.relative_to(evidence_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() and candidate.stat().st_size > 0 else None


def compile_facts(run_dir: Path) -> dict[str, Any]:
    paths = artifact_paths(run_dir)
    scope = _read_json(paths["scope"])
    events = load_events(run_dir)
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        fact_id = str(event.get("fact_id", "")).strip()
        kind = str(event.get("kind", "")).strip()
        if not fact_id or kind not in EVENT_KINDS:
            raise ValueError("every event requires a supported kind and non-empty fact_id")
        latest[fact_id] = event

    facts: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scope": scope,
        "source": "artifacts/discovery/events.jsonl",
        "compiled_at": _now(),
        "event_count": len(events),
        "last_event_id": str(events[-1].get("event_id", "")) if events else "",
        "fact_count": len(latest),
        "requirements": [], "pages": [], "functions": [], "elements": [],
        "observations": [], "test_objects": [], "risks": [], "pending": [], "absences": [],
    }
    scope_events = [event for event in latest.values() if event.get("kind") == "scope"]
    if scope_events:
        facts["scope"] = {**scope, **scope_events[-1].get("data", {})}
    for fact_id, event in latest.items():
        kind = str(event["kind"])
        if kind == "scope" or event.get("status") == "superseded":
            continue
        record = {
            "fact_id": fact_id,
            "status": event.get("status", "observed"),
            "observed_at": event.get("observed_at", ""),
            **event.get("data", {}),
        }
        for relation in ("page_id", "function_id", "element_id"):
            if event.get(relation) and relation not in record:
                record[relation] = event[relation]
        if event.get("evidence"):
            record["evidence"] = event["evidence"]
        facts[FACT_COLLECTIONS[kind]].append(record)
    for key in FACT_COLLECTIONS.values():
        facts[key].sort(key=lambda row: str(row.get("fact_id", "")))
    _write_json(paths["facts"], facts)
    return facts


def load_facts(run_dir: Path) -> dict[str, Any]:
    path = artifact_paths(run_dir)["facts"]
    if not path.exists():
        raise ValueError("facts.json does not exist; run compile-facts")
    facts = _read_json(path)
    if not isinstance(facts, dict):
        raise ValueError("facts.json must be an object")
    events = load_events(run_dir)
    last_event_id = str(events[-1].get("event_id", "")) if events else ""
    if facts.get("event_count") != len(events) or facts.get("last_event_id", "") != last_event_id:
        raise ValueError("facts.json is stale; run compile-facts before leaving discovery")
    return facts


def _all_fact_ids(facts: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for collection in FACT_COLLECTIONS.values():
        ids.update(str(row.get("fact_id", "")) for row in facts.get(collection, []))
    return ids


def validate_discovery(run_dir: Path) -> list[str]:
    try:
        facts = load_facts(run_dir)
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []
    for key in ("pages", "functions", "elements", "observations"):
        if not facts.get(key):
            errors.append(f"discovery has no {key}")
    observations_by_element: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed_option_values: dict[str, set[str]] = defaultdict(set)
    for observation in facts.get("observations", []):
        element_id = str(observation.get("element_id", ""))
        if element_id:
            observations_by_element[element_id].append(observation)
        if observation.get("option_value") is not None:
            observed_option_values[element_id].add(str(observation["option_value"]))
        for option_value in observation.get("option_values", []):
            observed_option_values[element_id].add(str(option_value))
        evidence_items = observation.get("evidence", [])
        if not evidence_items:
            errors.append(f"observation {observation.get('fact_id')} has no evidence")
        for evidence in evidence_items:
            value = evidence.get("path", "") if isinstance(evidence, dict) else str(evidence)
            if not value or not _resolved_evidence(run_dir, value):
                errors.append(f"observation {observation.get('fact_id')} has missing/empty evidence: {value!r}")

    function_ids = {str(row.get("function_id") or row.get("fact_id")) for row in facts.get("functions", [])}
    test_object_ids = {
        str(row.get("test_object_id") or row.get("fact_id")) for row in facts.get("test_objects", [])
    }
    for element in facts.get("elements", []):
        element_id = str(element.get("element_id") or element.get("fact_id"))
        function_id = str(element.get("function_id", ""))
        if function_id and function_id not in function_ids:
            errors.append(f"element {element_id} references unknown function {function_id}")
        if element.get("interactive", True) and element.get("status") not in {"absent", "disabled", "not_applicable"}:
            if not observations_by_element.get(element_id):
                errors.append(f"interactive element {element_id} has no executed observation")
        options = element.get("options") or []
        if options and element.get("option_set") == "finite":
            missing = {str(value) for value in options} - observed_option_values[element_id]
            if missing:
                errors.append(f"finite options not actually selected for {element_id}: {sorted(missing)}")
        if element.get("configuration") is True:
            element_observations = observations_by_element.get(element_id, [])
            configuration_observations = [row for row in element_observations if row.get("closure") == "configuration"]
            default_value = element.get("default_value")
            has_default = any(
                (row.get("variant") == "default" and row.get("closure") in {"create", "edit", "configuration"})
                or (default_value is not None and str(row.get("option_value")) == str(default_value))
                for row in element_observations
            )
            if not has_default:
                errors.append(f"configuration element {element_id} lacks a default/unconfigured baseline closure")
            for option in options:
                if default_value is not None and str(option) == str(default_value):
                    continue
                if not any(str(row.get("option_value")) == str(option) for row in configuration_observations):
                    errors.append(f"configuration option {option!r} for {element_id} lacks its own single-factor closure")

    for observation in facts.get("observations", []):
        closure = observation.get("closure")
        if closure in {"create", "edit", "configuration"}:
            required = ["action", "commit_result", "persistence_result", "effect_result", "recovery_result"]
            missing = [key for key in required if not str(observation.get(key, "")).strip()]
            if missing:
                errors.append(f"{closure} observation {observation.get('fact_id')} lacks closure fields: {missing}")
            if closure == "configuration" and observation.get("combination"):
                errors.append(f"configuration observation {observation.get('fact_id')} must be single-factor")
            if observation.get("outcome") != "success":
                errors.append(f"{closure} observation {observation.get('fact_id')} must record outcome=success")
            test_object_id = str(observation.get("test_object_id", ""))
            if not test_object_id or test_object_id not in test_object_ids:
                errors.append(f"{closure} observation {observation.get('fact_id')} must bind a known test_object_id")
        if closure == "delete":
            required = ["action", "commit_result", "effect_result", "test_object_id"]
            missing = [key for key in required if not str(observation.get(key, "")).strip()]
            if missing:
                errors.append(f"delete observation {observation.get('fact_id')} lacks closure fields: {missing}")
            if str(observation.get("test_object_id", "")) not in test_object_ids:
                errors.append(f"delete observation {observation.get('fact_id')} must bind a known test_object_id")
    return errors


def load_plan(run_dir: Path) -> dict[str, Any]:
    path = artifact_paths(run_dir)["plan"]
    if not path.exists():
        raise ValueError("case-plan.json does not exist")
    value = _read_json(path)
    if not isinstance(value, dict):
        raise ValueError("case-plan.json must be an object")
    return value


def validate_plan(run_dir: Path) -> list[str]:
    try:
        facts = load_facts(run_dir)
    except ValueError as exc:
        return [str(exc)]
    try:
        plan = load_plan(run_dir)
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []
    if plan.get("source") != "artifacts/discovery/facts.json":
        errors.append("case-plan.json source must be artifacts/discovery/facts.json")
    fact_ids = _all_fact_ids(facts)
    discovered_functions = {
        str(row.get("function_id") or row.get("fact_id")): row for row in facts.get("functions", [])
    }
    planned_functions: set[str] = set()
    case_ids: set[str] = set()
    titles: set[tuple[str, str]] = set()
    for function in plan.get("functions", []):
        function_id = str(function.get("function_id", "")).strip()
        if not function_id or function_id not in discovered_functions:
            errors.append(f"plan references unknown/empty function {function_id!r}")
        if function_id in planned_functions:
            errors.append(f"function {function_id} appears more than once in plan")
        planned_functions.add(function_id)
        cases = function.get("cases") or []
        if not cases:
            errors.append(f"function {function_id} has no planned cases")
            continue
        if not any(str(case.get("strategy", "")).lower() == "baseline" for case in cases):
            errors.append(f"function {function_id} lacks an independent baseline case")
        for case in cases:
            case_id = str(case.get("case_id", "")).strip()
            if not case_id or case_id in case_ids:
                errors.append(f"case_id is empty or duplicated: {case_id!r}")
            case_ids.add(case_id)
            refs = {str(value) for value in case.get("fact_ids", [])}
            if not refs or not refs <= fact_ids:
                errors.append(f"planned case {case_id} has missing/unknown fact_ids")
            if not str(case.get("title", "")).strip():
                errors.append(f"planned case {case_id} has an empty title")
            title_key = (function_id, str(case.get("title", "")).strip())
            if title_key in titles:
                errors.append(f"function {function_id} has duplicate planned case title: {title_key[1]!r}")
            titles.add(title_key)
            if str(case.get("strategy", "")).lower() != "baseline":
                if not str(case.get("dfx_dimension", "")).strip() or not str(case.get("dfx_scenario", "")).strip():
                    errors.append(f"DFX case {case_id} lacks dimension or scenario")
    missing_functions = set(discovered_functions) - planned_functions
    if missing_functions:
        errors.append(f"discovered functions missing from plan: {sorted(missing_functions)}")
    return errors


def load_cases(run_dir: Path) -> dict[str, Any]:
    path = artifact_paths(run_dir)["cases"]
    if not path.exists():
        raise ValueError("function-cases.json does not exist")
    value = _read_json(path)
    if not isinstance(value, dict):
        raise ValueError("function-cases.json must be an object")
    return value


def _numbered_lines(values: Any) -> list[str]:
    if isinstance(values, list):
        return [str(value).strip() for value in values]
    return [line.strip() for line in str(values or "").splitlines() if line.strip()]


def _normalize_case_prose(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        normalized = re.sub(
            r"(?i)(?:AI_TEST|CODEX_TEST)(?:[-_][A-Za-z0-9]+)+",
            "<TEST_OBJECT>",
            value,
        )
        result.append(re.sub(r"\s+", " ", normalized).strip())
    return tuple(result)


def validate_cases(run_dir: Path) -> list[str]:
    try:
        facts = load_facts(run_dir)
    except ValueError as exc:
        return [str(exc)]
    try:
        plan = load_plan(run_dir)
        document = load_cases(run_dir)
    except ValueError as exc:
        return [str(exc)]
    errors = validate_plan(run_dir)
    if document.get("source_plan") != "case-plan.json":
        errors.append("function-cases.json source_plan must be case-plan.json")
    fact_ids = _all_fact_ids(facts)
    planned: dict[str, tuple[str, dict[str, Any]]] = {}
    function_names: dict[str, str] = {}
    for function in plan.get("functions", []):
        fid = str(function.get("function_id", ""))
        function_names[fid] = str(function.get("name", "")).strip()
        for case in function.get("cases", []):
            planned[str(case.get("case_id", ""))] = (fid, case)
    actual_ids: set[str] = set()
    function_closed: set[str] = set()
    previous_function = ""
    normalized_signatures: dict[tuple[tuple[str, ...], tuple[str, ...]], str] = {}
    for case in document.get("cases", []):
        case_id = str(case.get("case_id", "")).strip()
        function_id = str(case.get("function_id", "")).strip()
        title = str(case.get("title", "")).strip()
        if case_id not in planned:
            errors.append(f"case {case_id!r} is not present in case-plan.json")
        elif planned[case_id][0] != function_id:
            errors.append(f"case {case_id} belongs to a different function than planned")
        if case_id in actual_ids:
            errors.append(f"duplicate case_id: {case_id}")
        actual_ids.add(case_id)
        if previous_function and function_id != previous_function:
            function_closed.add(previous_function)
        if function_id in function_closed:
            errors.append(f"function cases are not contiguous: {function_id}")
        previous_function = function_id
        prefix = function_names.get(function_id, "")
        if not title or (prefix and not title.startswith(prefix + "-")):
            errors.append(f"case {case_id} title must be '功能点-当前用例标题'")
        steps = _numbered_lines(case.get("steps"))
        expected = _numbered_lines(case.get("expected_results"))
        if not isinstance(case.get("steps"), list) or not isinstance(case.get("expected_results"), list):
            errors.append(f"case {case_id} steps and expected_results must be JSON arrays")
        if not steps or not expected or len(steps) != len(expected):
            errors.append(f"case {case_id} steps and expected_results must be non-empty and one-to-one")
        prose = "\n".join(steps + expected + [title])
        if INTERNAL_STEP_MARKERS.search(prose):
            errors.append(f"case {case_id} exposes implementation identifiers in executable prose")
        if any(marker.lower() in prose.lower() for marker in SCREENSHOT_MARKERS):
            errors.append(f"case {case_id} must assert page behavior instead of asking for screenshots")
        if any(marker.lower() in prose.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append(f"case {case_id} contains placeholder text")
        refs = {str(value) for value in case.get("fact_ids", [])}
        if not refs or not refs <= fact_ids:
            errors.append(f"case {case_id} has missing/unknown fact_ids")
        signature = (_normalize_case_prose(steps), _normalize_case_prose(expected))
        if signature in normalized_signatures:
            errors.append(f"case {case_id} duplicates steps and expected results of {normalized_signatures[signature]}")
        normalized_signatures[signature] = case_id
    missing = set(planned) - actual_ids
    if missing:
        errors.append(f"planned cases were not written: {sorted(missing)}")
    return errors


def review_run(run_dir: Path) -> dict[str, Any]:
    facts = load_facts(run_dir)
    discovery_errors = validate_discovery(run_dir)
    plan_errors = validate_plan(run_dir)
    case_errors = validate_cases(run_dir)
    plan = load_plan(run_dir) if artifact_paths(run_dir)["plan"].exists() else {"functions": []}
    cases = load_cases(run_dir) if artifact_paths(run_dir)["cases"].exists() else {"cases": []}
    planned_fact_ids = {
        str(value)
        for function in plan.get("functions", [])
        for case in function.get("cases", [])
        for value in case.get("fact_ids", [])
    }
    case_fact_ids = {str(value) for case in cases.get("cases", []) for value in case.get("fact_ids", [])}
    executable_fact_ids = {
        str(row.get("fact_id")) for key in ("functions", "elements", "observations") for row in facts.get(key, [])
        if row.get("status") not in {"absent", "disabled", "not_applicable"}
    }
    trace_errors: list[str] = []
    missing_in_plan = executable_fact_ids - planned_fact_ids
    if missing_in_plan:
        trace_errors.append(f"facts not covered by plan: {sorted(missing_in_plan)}")
    missing_in_cases = planned_fact_ids - case_fact_ids
    if missing_in_cases:
        trace_errors.append(f"planned facts not covered by cases: {sorted(missing_in_cases)}")
    errors = list(dict.fromkeys(discovery_errors + plan_errors + case_errors + trace_errors))
    review = {
        "schema_version": SCHEMA_VERSION,
        "reviewed_at": _now(),
        "status": "passed" if not errors else "failed",
        "counts": {
            "facts": facts.get("fact_count", 0),
            "planned_cases": sum(len(f.get("cases", [])) for f in plan.get("functions", [])),
            "written_cases": len(cases.get("cases", [])),
            "pending_gaps": len(facts.get("pending", [])),
        },
        "errors": errors,
        "gap_list": facts.get("pending", []) + facts.get("risks", []),
    }
    _write_json(artifact_paths(run_dir)["review"], review)
    return review


def pipeline_status(run_dir: Path) -> dict[str, Any]:
    paths = artifact_paths(run_dir)
    if not paths["scope"].exists():
        return {"stage": "init", "state": "required", "next_action": "init-run"}
    discovery_errors = validate_discovery(run_dir)
    if discovery_errors:
        return {"stage": "discovery", "state": "in_progress", "errors": discovery_errors, "next_action": "continue page exploration"}
    if not paths["plan"].exists():
        return {"stage": "plan", "state": "required", "next_action": "write case-plan.json from facts.json"}
    plan_errors = validate_plan(run_dir)
    if plan_errors:
        return {"stage": "plan", "state": "needs_local_repair", "errors": plan_errors, "next_action": "repair case-plan.json"}
    if not paths["cases"].exists():
        return {"stage": "cases", "state": "required", "next_action": "write function-cases.json from facts.json and case-plan.json"}
    case_errors = validate_cases(run_dir)
    if case_errors:
        return {"stage": "cases", "state": "needs_local_repair", "errors": case_errors, "next_action": "repair affected cases only"}
    if not paths["review"].exists():
        return {"stage": "review", "state": "required", "next_action": "review-run"}
    review = _read_json(paths["review"])
    if review.get("status") != "passed":
        return {"stage": "review", "state": "needs_local_repair", "errors": review.get("errors", []), "next_action": "repair affected upstream artifact and review once"}
    return {"stage": "delivery", "state": "ready", "next_action": "complete-deliverables"}
