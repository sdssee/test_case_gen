"""Read-only semantic review gate for orchestrated test-design runs.

The existing cases gate remains the authority for generated test artifacts.
This module adds the independent-review constraints required between cases and
delivery without importing the orchestration engine or pipeline.  Legacy runs
without ``orchestration/run-manifest.json`` are deliberately not applicable.
"""

from __future__ import annotations

import hashlib
import csv
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .contracts import AgentResult, AgentRole, AgentTask, TaskStatus, TraceabilityRecord, canonical_fingerprint
from ..validation_cache import fingerprint


REVIEW_REPORT = "orchestration/review-report.json"
RUN_MANIFEST = "orchestration/run-manifest.json"
TRACEABILITY_FILE = "artifacts/data/case-traceability.json"

_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PART_RE = re.compile(r"^function_cases_part_[0-9]{3}\.json$")

_KEY_LEDGERS = (
    "page-element-inventory.csv",
    "page-discovery.csv",
    "selection-option-observations.csv",
    "element-case-plan.csv",
    "test-data-lifecycle.csv",
    "risk-confirmation.csv",
)
_SHEET_DATA_FILES = (
    "overview.json",
    "requirements.json",
    "scenarios.json",
    "performance.json",
    "risks.json",
    "automation.json",
    "page_elements.json",
)

# These checks mirror the semantic responsibilities of the independent,
# read-only reviewer.  Requiring every named result prevents a generic
# ``checks: {"all_good": true}`` report from bypassing the review gate.
REQUIRED_REVIEW_CHECKS = (
    "cases_gate_passed",
    "inventory_discovery_complete",
    "selection_options_executed",
    "crud_lifecycle_verified",
    "page_verifiable_risks_resolved",
    "plan_case_alignment",
    "steps_unique",
    "expected_results_unique",
    "function_points_contiguous",
    "dfx_within_plan",
    "traceability_complete",
    "no_open_rework",
)

_REPORT_FIELDS = {
    "schema_version",
    "generation_session_id",
    "generation_source_fingerprint",
    "review_source_fingerprint",
    "review_task_id",
    "reviewer_role",
    "verdict",
    "generator_task_ids",
    "checks",
    "issues",
}
_ISSUE_FIELDS = {
    "issue_id",
    "severity",
    "status",
    "reason_code",
    "affected_ids",
    "message",
}
_CLOSED_REWORK_STATES = {"RESOLVED", "CLOSED", "WAIVED", "INVALIDATED"}


class ReviewValidationError(ValueError):
    """Raised when an orchestrated run is not independently reviewable."""


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewValidationError(f"JSON object contains duplicate field {key!r}")
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ReviewValidationError(f"{label} is missing or is not a regular file: {path}")
    try:
        return json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_strict_object_pairs,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewValidationError(f"{label} is not valid JSON: {path}: {exc}") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewValidationError(f"{label} must be a JSON object")
    return value


def _strict_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unknown:
            details.append(f"unknown={unknown}")
        raise ReviewValidationError(f"{label} has invalid fields: {', '.join(details)}")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ReviewValidationError(f"{label} must be a normalized identifier")
    return value


def _scope_identifier(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 128
        or value in {".", ".."}
        or any(char in value for char in "/\\\x00")
    ):
        raise ReviewValidationError(f"{label} must be a safe run/batch identifier")
    return value


def _fingerprint(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _FINGERPRINT_RE.fullmatch(value):
        raise ReviewValidationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _inside_run(run_dir: Path, raw_path: Any, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise ReviewValidationError(f"{label} must be a non-empty run-relative POSIX path")
    path_value = Path(raw_path)
    if path_value.is_absolute() or any(part in {"", ".", ".."} for part in raw_path.split("/")):
        raise ReviewValidationError(f"{label} must not be absolute or traverse directories")
    resolved = (run_dir / path_value).resolve(strict=False)
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise ReviewValidationError(f"{label} escapes the current run directory") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise ReviewValidationError(f"{label} is missing or is not a regular file: {raw_path}")
    return resolved


def _manifest_part_names(run_dir: Path) -> tuple[str, ...]:
    manifest_path = run_dir / "artifacts" / "data" / "function_cases_manifest.json"
    manifest = _mapping(_load_json(manifest_path, "function case manifest"), "function case manifest")
    raw_parts = manifest.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ReviewValidationError("function case manifest parts must be a non-empty array")
    names: list[str] = []
    for index, item in enumerate(raw_parts, start=1):
        expected = f"function_cases_part_{index:03d}.json"
        if not isinstance(item, str) or item != expected or not _PART_RE.fullmatch(item):
            raise ReviewValidationError(
                "function case manifest parts must be ordered, gap-free names starting at 001"
            )
        names.append(item)
    return tuple(names)


def _review_source_paths(run_dir: Path) -> tuple[Path, ...]:
    data_dir = run_dir / "artifacts" / "data"
    relative_paths = [
        "batch-scope.json",
        *_KEY_LEDGERS,
        "artifacts/data/generation-session.json",
        "artifacts/data/function_cases_manifest.json",
        "artifacts/data/dfx-assessment.json",
        TRACEABILITY_FILE,
        *(f"artifacts/data/{name}" for name in _SHEET_DATA_FILES),
        *(f"artifacts/data/{name}" for name in _manifest_part_names(run_dir)),
    ]
    paths: list[Path] = []
    for relative in relative_paths:
        path = _inside_run(run_dir, relative, f"review source {relative}")
        if path.stat().st_size == 0:
            raise ReviewValidationError(f"review source must not be empty: {relative}")
        paths.append(path)
    from ..batch import resolved_evidence_file

    for ledger_name in _KEY_LEDGERS:
        with (run_dir / ledger_name).open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                for field, raw in row.items():
                    if "证据路径" not in str(field) or not str(raw or "").strip():
                        continue
                    evidence = resolved_evidence_file(run_dir, str(raw))
                    if evidence is None:
                        raise ReviewValidationError(
                            f"review source evidence is missing for {ledger_name}: {raw}"
                        )
                    paths.append(evidence)
    # Guard against a manifest that hides stale formal shards from the review
    # fingerprint.  The cases gate checks this too, but the fingerprint helper
    # must be safe when called independently.
    declared = set(_manifest_part_names(run_dir))
    actual = {path.name for path in data_dir.glob("function_cases_part_*.json") if path.is_file()}
    if actual != declared:
        raise ReviewValidationError(
            f"formal function case shards differ from manifest: declared={sorted(declared)}, actual={sorted(actual)}"
        )
    return tuple(paths)


def review_source_fingerprint(run_dir: Path | str) -> str:
    """Fingerprint every formal fact consumed by the independent reviewer.

    Paths are hashed relative to the run directory, so a complete archived run
    remains verifiable after being moved.  The report, orchestration state and
    derived/delivery-owned ``batch-status.csv`` are intentionally excluded to
    avoid self-reference and to keep an approved review valid after delivery.
    """

    root = Path(run_dir).resolve()
    digest = hashlib.sha256()
    for path in sorted(set(_review_source_paths(root)), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_report(value: Any) -> Mapping[str, Any]:
    report = _mapping(value, "review-report.json")
    _strict_fields(report, _REPORT_FIELDS, "review-report.json")
    if report["schema_version"] != "1.0.0":
        raise ReviewValidationError("review-report.json schema_version must equal '1.0.0'")
    session_id = report["generation_session_id"]
    if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
        raise ReviewValidationError("generation_session_id must be a non-empty trimmed string")
    _fingerprint(report["generation_source_fingerprint"], "generation_source_fingerprint")
    _fingerprint(report["review_source_fingerprint"], "review_source_fingerprint")
    _identifier(report["review_task_id"], "review_task_id")
    if report["reviewer_role"] != "reviewer":
        raise ReviewValidationError("reviewer_role must equal 'reviewer'")
    if report["verdict"] != "APPROVED":
        raise ReviewValidationError("review verdict must equal 'APPROVED'")

    generator_ids = report["generator_task_ids"]
    if not isinstance(generator_ids, list) or not generator_ids:
        raise ReviewValidationError("generator_task_ids must be a non-empty array")
    normalized_generators = [_identifier(item, f"generator_task_ids[{index}]") for index, item in enumerate(generator_ids)]
    if len(normalized_generators) != len(set(normalized_generators)):
        raise ReviewValidationError("generator_task_ids must not contain duplicates")

    checks = _mapping(report["checks"], "review-report checks")
    expected_checks = set(REQUIRED_REVIEW_CHECKS)
    _strict_fields(checks, expected_checks, "review-report checks")
    failed = sorted(name for name, passed in checks.items() if passed is not True)
    if failed:
        raise ReviewValidationError(f"all mandatory review checks must be true: {failed}")

    issues = report["issues"]
    if not isinstance(issues, list):
        raise ReviewValidationError("review-report issues must be an array")
    seen_issue_ids: set[str] = set()
    for index, raw_issue in enumerate(issues):
        issue = _mapping(raw_issue, f"review issue {index}")
        _strict_fields(issue, _ISSUE_FIELDS, f"review issue {index}")
        issue_id = _identifier(issue["issue_id"], f"issues[{index}].issue_id")
        if issue_id in seen_issue_ids:
            raise ReviewValidationError(f"review issues contain duplicate issue_id {issue_id!r}")
        seen_issue_ids.add(issue_id)
        if issue["severity"] not in {"BLOCKING", "MAJOR", "MINOR", "INFO"}:
            raise ReviewValidationError(f"issues[{index}].severity is invalid")
        if issue["status"] not in {"OPEN", "RESOLVED", "WAIVED"}:
            raise ReviewValidationError(f"issues[{index}].status is invalid")
        _identifier(issue["reason_code"], f"issues[{index}].reason_code")
        affected = issue["affected_ids"]
        if not isinstance(affected, list):
            raise ReviewValidationError(f"issues[{index}].affected_ids must be an array")
        normalized = [_identifier(item, f"issues[{index}].affected_ids[{position}]") for position, item in enumerate(affected)]
        if len(normalized) != len(set(normalized)):
            raise ReviewValidationError(f"issues[{index}].affected_ids must be unique")
        message = issue["message"]
        if not isinstance(message, str) or not message or message != message.strip():
            raise ReviewValidationError(f"issues[{index}].message must be non-empty and trimmed")
        if issue["severity"] == "BLOCKING" or issue["status"] == "OPEN":
            raise ReviewValidationError(
                f"approved review cannot contain blocking or open issue {issue_id!r}"
            )
    return report


def _task_entries(manifest: Mapping[str, Any]) -> dict[str, tuple[Mapping[str, Any], AgentTask]]:
    raw_tasks = manifest.get("tasks")
    if not isinstance(raw_tasks, Mapping):
        raise ReviewValidationError("run-manifest.json tasks must be an object")
    result: dict[str, tuple[Mapping[str, Any], AgentTask]] = {}
    for task_id, raw_entry in raw_tasks.items():
        normalized_id = _identifier(task_id, "run-manifest task key")
        entry = _mapping(raw_entry, f"run-manifest task {normalized_id}")
        try:
            task = AgentTask.from_dict(entry.get("task"))
        except (TypeError, ValueError) as exc:
            raise ReviewValidationError(f"run-manifest task {normalized_id} is invalid: {exc}") from exc
        if task.task_id != normalized_id:
            raise ReviewValidationError(
                f"run-manifest task key {normalized_id!r} does not match task.task_id {task.task_id!r}"
            )
        result[normalized_id] = (entry, task)
    return result


def _successful_result(
    run_dir: Path,
    task_id: str,
    entry: Mapping[str, Any],
    task: AgentTask,
    generation_fingerprint: str,
) -> AgentResult:
    if entry.get("status") != TaskStatus.SUCCEEDED.value:
        raise ReviewValidationError(f"task {task_id!r} must have status SUCCEEDED")
    result_path = _inside_run(run_dir, entry.get("result_path"), f"task {task_id} result_path")
    try:
        result = AgentResult.from_dict(_load_json(result_path, f"task {task_id} result"))
    except (TypeError, ValueError) as exc:
        raise ReviewValidationError(f"task {task_id!r} result contract is invalid: {exc}") from exc
    if (
        result.task_id != task_id
        or result.agent_role is not task.agent_role
        or result.status is not TaskStatus.SUCCEEDED
    ):
        raise ReviewValidationError(f"task {task_id!r} result identity/status does not match run manifest")
    if task.source_fingerprint != generation_fingerprint or result.source_fingerprint != generation_fingerprint:
        raise ReviewValidationError(f"task {task_id!r} uses a stale generation source fingerprint")
    return result


def _validate_accepted_snapshot(
    run_dir: Path,
    task_id: str,
    entry: Mapping[str, Any],
    task: AgentTask,
) -> None:
    raw_root = entry.get("accepted_output_root")
    expected_root = f"orchestration/accepted/{task_id}"
    if raw_root != expected_root:
        raise ReviewValidationError(f"task {task_id!r} accepted_output_root is invalid")
    root = (run_dir / expected_root).resolve(strict=False)
    try:
        root.relative_to((run_dir / "orchestration" / "accepted").resolve())
    except ValueError as exc:
        raise ReviewValidationError(f"task {task_id!r} accepted output escapes its trusted root") from exc
    if root.is_symlink() or not root.is_dir():
        raise ReviewValidationError(f"task {task_id!r} accepted output directory is missing")
    result_path = _inside_run(run_dir, entry.get("result_path"), f"task {task_id} result_path")
    try:
        result = AgentResult.from_dict(_load_json(result_path, f"task {task_id} result"))
    except (TypeError, ValueError) as exc:
        raise ReviewValidationError(f"task {task_id!r} result contract is invalid: {exc}") from exc
    prefix = f"artifacts/agent-work/{task.agent_role.value}/{task_id}/output/"
    expected_files = {path.removeprefix(prefix) for path in result.produced_files}
    actual_paths = [path for path in root.rglob("*") if path.is_file()]
    if any(path.is_symlink() for path in actual_paths):
        raise ReviewValidationError(f"task {task_id!r} accepted output contains a symlink")
    actual_files = {path.relative_to(root).as_posix() for path in actual_paths}
    if actual_files != expected_files:
        raise ReviewValidationError(
            f"task {task_id!r} accepted files differ from AgentResult: "
            f"missing={sorted(expected_files-actual_files)}, unknown={sorted(actual_files-expected_files)}"
        )
    if fingerprint(actual_paths) != entry.get("accepted_output_fingerprint"):
        raise ReviewValidationError(f"task {task_id!r} accepted output fingerprint is stale")


def _load_case_rows(run_dir: Path) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for name in _manifest_part_names(run_dir):
        payload = _load_json(run_dir / "artifacts" / "data" / name, name)
        raw_rows = payload.get("cases") if isinstance(payload, Mapping) else payload
        if not isinstance(raw_rows, list):
            raise ReviewValidationError(f"{name} must contain a cases array")
        for index, row in enumerate(raw_rows):
            rows.append(_mapping(row, f"{name} case {index}"))
    if not rows:
        raise ReviewValidationError("function case manifest must contain at least one case")
    return rows


def _load_traceability(run_dir: Path) -> list[TraceabilityRecord]:
    payload = _load_json(run_dir / TRACEABILITY_FILE, TRACEABILITY_FILE)
    if isinstance(payload, Mapping):
        if set(payload) != {"schema_version", "records"} or payload.get("schema_version") != "1.0.0":
            raise ReviewValidationError(
                "case-traceability.json envelope must contain only schema_version='1.0.0' and records"
            )
        payload = payload.get("records")
    if not isinstance(payload, list):
        raise ReviewValidationError("case-traceability.json must be an array or strict records envelope")
    records: list[TraceabilityRecord] = []
    for index, item in enumerate(payload):
        try:
            records.append(TraceabilityRecord.from_dict(item))
        except (TypeError, ValueError) as exc:
            raise ReviewValidationError(f"case-traceability.json record {index} is invalid: {exc}") from exc
    return records


def _validate_traceability(
    run_dir: Path,
    generator_task_ids: tuple[str, ...],
    generation_fingerprint: str,
    expected_records: Mapping[str, TraceabilityRecord],
) -> None:
    cases = _load_case_rows(run_dir)
    case_by_id: dict[str, Mapping[str, Any]] = {}
    for index, case in enumerate(cases):
        case_id = str(case.get("用例 ID", "")).strip()
        if not case_id or case_id in case_by_id:
            raise ReviewValidationError(f"formal cases contain missing/duplicate case ID at row {index}")
        case_by_id[case_id] = case

    records = _load_traceability(run_dir)
    record_by_id: dict[str, TraceabilityRecord] = {}
    for record in records:
        if record.case_id in record_by_id:
            raise ReviewValidationError(
                f"case-traceability.json contains duplicate case_id {record.case_id!r}"
            )
        record_by_id[record.case_id] = record
        if record.source_fingerprint != generation_fingerprint:
            raise ReviewValidationError(
                f"traceability record {record.case_id!r} uses a stale generation source fingerprint"
            )
        if record.worker_task_id not in generator_task_ids:
            raise ReviewValidationError(
                f"traceability record {record.case_id!r} references non-generator task {record.worker_task_id!r}"
            )
        actual_function_point = str(case_by_id.get(record.case_id, {}).get("功能点", "")).strip()
        if actual_function_point and record.function_point != actual_function_point:
            raise ReviewValidationError(
                f"traceability record {record.case_id!r} function_point does not match the formal case"
            )
        expected = expected_records.get(record.case_id)
        if expected is None or record.to_dict() != expected.to_dict():
            raise ReviewValidationError(
                f"traceability record {record.case_id!r} differs from current deterministic ledger expectations"
            )

    expected_ids = set(case_by_id)
    traced_ids = set(record_by_id)
    if traced_ids != expected_ids:
        raise ReviewValidationError(
            "case-traceability.json must cover every and only manifest case exactly once: "
            f"missing={sorted(expected_ids - traced_ids)[:20]}, unknown={sorted(traced_ids - expected_ids)[:20]}"
        )


def _is_closed_rework_payload(value: Any) -> bool:
    if isinstance(value, list):
        return all(_is_closed_rework_payload(item) for item in value)
    if not isinstance(value, Mapping):
        return False
    status = value.get("status") or value.get("resolution_status")
    if isinstance(status, str) and status.upper() in _CLOSED_REWORK_STATES:
        return True
    if "request" in value:
        return _is_closed_rework_payload(value.get("request")) and bool(value.get("closed_at"))
    # A strict ReworkRequest has no status and therefore represents an open
    # request while it remains in the active rework directory.
    return False


def _validate_no_open_rework(run_dir: Path) -> None:
    directory = run_dir / "orchestration" / "rework-requests"
    if not directory.exists():
        return
    if directory.is_symlink() or not directory.is_dir():
        raise ReviewValidationError("orchestration/rework-requests must be a regular directory")
    for path in sorted(directory.rglob("*.json")):
        payload = _load_json(path, f"rework request {path.name}")
        if not _is_closed_rework_payload(payload):
            raise ReviewValidationError(f"unclosed rework request blocks review: {path}")


def validate_review_artifacts(run_dir: Path | str) -> bool:
    """Validate the current independent review without modifying the run.

    Returns ``False`` only for a legacy run that has no orchestration manifest;
    returns ``True`` for a fully approved orchestrated run.  Every invalid or
    stale orchestrated review raises :class:`ReviewValidationError`.
    """

    root = Path(run_dir).resolve()
    if not root.is_dir():
        raise ReviewValidationError(f"batch run directory does not exist: {root}")
    manifest_path = root / RUN_MANIFEST
    if not manifest_path.exists():
        return False

    # Lazy import keeps this module independent of engine/pipeline and lets the
    # established validator remain the single cases-gate authority.
    from ..batch import (
        generation_session_data,
        generation_source_fingerprint,
        validate_batch_artifacts,
    )

    try:
        validate_batch_artifacts(root, "cases", use_cache=False)
    except (OSError, TypeError, ValueError) as exc:
        raise ReviewValidationError(f"current cases gate has not passed: {exc}") from exc

    session = generation_session_data(root)
    if not isinstance(session, dict):
        raise ReviewValidationError("current generation-session.json is missing or invalid")
    session_id = session.get("generation_session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ReviewValidationError("current generation session has no generation_session_id")
    current_generation_fingerprint = generation_source_fingerprint(root)
    if session.get("source_fingerprint") != current_generation_fingerprint:
        raise ReviewValidationError("current generation session source fingerprint is stale")
    current_task_fingerprint = canonical_fingerprint(
        {
            "generation_session_id": session.get("generation_session_id"),
            "source_fingerprint": current_generation_fingerprint,
            "catalog_source_fingerprint": session.get("catalog_source_fingerprint"),
        }
    )

    manifest = _mapping(_load_json(manifest_path, "run-manifest.json"), "run-manifest.json")
    if manifest.get("schema_version") != 1:
        raise ReviewValidationError("run-manifest.json schema_version must equal 1")
    if manifest.get("architecture") != "multi-agent-final" or manifest.get("agent_mode") != "required":
        raise ReviewValidationError(
            "review gate applies only to architecture='multi-agent-final' with agent_mode='required'"
        )
    manifest_run_id = _scope_identifier(manifest.get("run_id"), "run-manifest run_id")
    manifest_batch_id = _scope_identifier(manifest.get("batch_id"), "run-manifest batch_id")
    state = _mapping(manifest.get("state_machine"), "run-manifest state_machine")
    validated_phases = state.get("validated_phases")
    if not isinstance(validated_phases, list) or validated_phases[:4] != [
        "discovery", "plan", "risk", "cases"
    ]:
        raise ReviewValidationError("run-manifest state must contain a validated discovery→plan→risk→cases prefix")

    entries = _task_entries(manifest)
    inconsistent_scope = sorted(
        task_id
        for task_id, (_, task) in entries.items()
        if task.run_id != manifest_run_id or task.batch_id != manifest_batch_id
    )
    if inconsistent_scope:
        raise ReviewValidationError(
            f"run-manifest tasks do not use the manifest run_id/batch_id: {inconsistent_scope[:20]}"
        )
    for task_id, (entry, task) in entries.items():
        if entry.get("status") == TaskStatus.SUCCEEDED.value:
            _validate_accepted_snapshot(root, task_id, entry, task)
    case_order = manifest.get("case_task_order")
    if not isinstance(case_order, list) or len(case_order) != len(set(case_order)):
        raise ReviewValidationError("run-manifest case_task_order must be a unique array")
    expected_generators: list[str] = []
    for index, task_id_value in enumerate(case_order):
        task_id = _identifier(task_id_value, f"case_task_order[{index}]")
        if task_id not in entries:
            raise ReviewValidationError(f"case_task_order references missing task {task_id!r}")
        entry, task = entries[task_id]
        if task.agent_role is not AgentRole.CASE_WORKER:
            raise ReviewValidationError(f"case_task_order task {task_id!r} is not a case_worker")
        if entry.get("status") == TaskStatus.SUCCEEDED.value:
            expected_generators.append(task_id)
    succeeded_case_workers = {
        task_id
        for task_id, (entry, task) in entries.items()
        if task.agent_role is AgentRole.CASE_WORKER and entry.get("status") == TaskStatus.SUCCEEDED.value
    }
    if set(expected_generators) != succeeded_case_workers or not expected_generators:
        raise ReviewValidationError(
            "all successful case_worker tasks must appear exactly once in case_task_order"
        )
    for task_id in expected_generators:
        entry, task = entries[task_id]
        _successful_result(root, task_id, entry, task, current_task_fingerprint)

    from .case_merge import plan_groups, traceability_expectations

    groups = plan_groups(root)
    expected_trace: dict[str, TraceabilityRecord] = {}
    for task_id in expected_generators:
        _, task = entries[task_id]
        if task.owner_key not in groups:
            raise ReviewValidationError(
                f"case worker {task_id!r} owner is absent from the current element plan"
            )
        for case_id, record in traceability_expectations(
            root,
            groups[str(task.owner_key)],
            task_id,
            current_generation_fingerprint,
        ).items():
            if case_id in expected_trace:
                raise ReviewValidationError(f"deterministic trace expectations duplicate case {case_id!r}")
            expected_trace[case_id] = record

    report = _validate_report(_load_json(root / REVIEW_REPORT, "review-report.json"))
    if report["generation_session_id"] != session_id:
        raise ReviewValidationError("review-report generation_session_id is stale")
    if report["generation_source_fingerprint"] != current_generation_fingerprint:
        raise ReviewValidationError("review-report generation_source_fingerprint is stale")
    current_review_fingerprint = review_source_fingerprint(root)
    if report["review_source_fingerprint"] != current_review_fingerprint:
        raise ReviewValidationError("review-report review_source_fingerprint is stale")
    if report["generator_task_ids"] != expected_generators:
        raise ReviewValidationError(
            "review-report generator_task_ids must exactly follow successful case workers in case_task_order"
        )

    review_task_id = report["review_task_id"]
    if review_task_id in succeeded_case_workers:
        raise ReviewValidationError("reviewer task must be independent from every generator task")
    review_entry = entries.get(review_task_id)
    if review_entry is None:
        raise ReviewValidationError(f"review_task_id {review_task_id!r} is missing from run-manifest tasks")
    entry, task = review_entry
    if task.agent_role is not AgentRole.REVIEWER:
        raise ReviewValidationError("review_task_id must reference a reviewer task")
    _successful_result(root, review_task_id, entry, task, current_task_fingerprint)

    _validate_traceability(
        root,
        tuple(expected_generators),
        current_generation_fingerprint,
        expected_trace,
    )
    _validate_no_open_rework(root)
    return True


__all__ = [
    "REQUIRED_REVIEW_CHECKS",
    "ReviewValidationError",
    "review_source_fingerprint",
    "validate_review_artifacts",
]
