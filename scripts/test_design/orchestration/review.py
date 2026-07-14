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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    AgentClaim,
    AgentResult,
    AgentRole,
    AgentTask,
    ExecutorKind,
    TaskStatus,
    TraceabilityRecord,
    canonical_fingerprint,
    role_contract_relative_paths,
)
from ..sensitive_data import (
    BINARY_EVIDENCE_AUDIT_SUFFIX,
    assert_no_sensitive_artifact,
    binary_evidence_audit_path,
)
from ..validation_cache import fingerprint
from .event_store import EventStore, EventStoreError
from .execution_binding import ExecutionBindingError, validate_execution_binding
from .page_probe import (
    PageProbeError,
    load_page_probe_receipt,
    page_probe_event_registry,
    receipt_path as page_probe_receipt_path,
    validate_project_record_consumption,
)


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
    "interaction-branch-observations.csv",
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
    "binary_evidence_privacy_verified",
    "page_probe_receipts_verified",
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
_FORMAL_EXECUTOR_KINDS = {
    ExecutorKind.CODEBUDDY_SUBAGENT,
    ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK,
}


class ReviewValidationError(ValueError):
    """Raised when an orchestrated run is not independently reviewable."""


def _validate_fallback_authorization(
    task_id: str,
    entry: Mapping[str, Any],
    task: AgentTask,
    claim: AgentClaim,
) -> None:
    value = entry.get("fallback_authorization")
    if not isinstance(value, Mapping):
        raise ReviewValidationError(
            f"fallback task {task_id!r} has no supervisor authorization"
        )
    expected_fields = {
        "schema_version", "task_id", "execution_id", "coordinator_id", "executor_id",
        "executor_kind", "source_fingerprint", "input_snapshot_fingerprint",
        "task_packet_fingerprint", "context_fingerprint", "failure_count",
        "failure_reason", "authorized_at", "quality_gates_unchanged",
        "workspace_isolation_required", "review_required", "delivery_single_writer",
        "authorization_fingerprint",
    }
    if set(value) != expected_fields:
        raise ReviewValidationError(f"fallback task {task_id!r} authorization fields are invalid")
    content = dict(value)
    fingerprint_value = content.pop("authorization_fingerprint", None)
    if fingerprint_value != canonical_fingerprint(content):
        raise ReviewValidationError(f"fallback task {task_id!r} authorization fingerprint is stale")
    expected = {
        "schema_version": "1.0.0",
        "task_id": task_id,
        "execution_id": claim.execution_id,
        "coordinator_id": claim.coordinator_id,
        "executor_id": claim.executor_id,
        "executor_kind": ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK.value,
        "source_fingerprint": task.source_fingerprint,
        "input_snapshot_fingerprint": claim.input_snapshot_fingerprint,
        "task_packet_fingerprint": claim.task_packet_fingerprint,
        "context_fingerprint": claim.context_fingerprint,
        "quality_gates_unchanged": True,
        "workspace_isolation_required": True,
        "review_required": True,
        "delivery_single_writer": True,
    }
    mismatched = [key for key, item in expected.items() if value.get(key) != item]
    if mismatched:
        raise ReviewValidationError(
            f"fallback task {task_id!r} authorization does not match frozen execution: {mismatched}"
        )
    if not isinstance(value.get("failure_count"), int) or value["failure_count"] < 2:
        raise ReviewValidationError(f"fallback task {task_id!r} did not exhaust the native Agent retry")
    if not isinstance(value.get("failure_reason"), str) or not value["failure_reason"].strip():
        raise ReviewValidationError(f"fallback task {task_id!r} has no concrete dispatch failure")


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


def review_evidence_paths(run_dir: Path | str) -> tuple[Path, ...]:
    """Return every formal evidence file that the reviewer must inspect.

    Ledger references are not sufficient: an unreferenced screenshot left in
    the promoted evidence directories is still a generated run artifact.  All
    such files therefore enter the frozen Reviewer input and final review
    fingerprint.  Binary evidence additionally requires its hash-bound visual
    privacy sidecar; orphan sidecars are rejected instead of being silently
    ignored.
    """

    root = Path(run_dir).resolve()
    candidates: set[Path] = set()
    for name in ("evidence", "screenshots"):
        directory = root / "artifacts" / name
        if directory.is_dir():
            candidates.update(path for path in directory.rglob("*") if path.is_file())

    sidecars = {path for path in candidates if path.name.endswith(BINARY_EVIDENCE_AUDIT_SUFFIX)}
    symbolic_sidecars = sorted(
        (path for path in sidecars if path.is_symlink()),
        key=lambda item: item.as_posix(),
    )
    if symbolic_sidecars:
        raise ReviewValidationError(
            "binary visual privacy audit must not be a symbolic link: "
            f"{[path.relative_to(root).as_posix() for path in symbolic_sidecars]}"
        )
    result: set[Path] = set()
    consumed_sidecars: set[Path] = set()
    for path in sorted(candidates - sidecars, key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ReviewValidationError(f"review evidence must not be a symbolic link: {path}")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
            is_text = assert_no_sensitive_artifact(resolved, f"review evidence {relative}")
        except ValueError as exc:
            raise ReviewValidationError(str(exc)) from exc
        result.add(resolved)
        if not is_text:
            audit = binary_evidence_audit_path(resolved)
            if audit.is_symlink() or not audit.is_file():
                raise ReviewValidationError(
                    f"binary review evidence is missing a regular visual privacy audit: {relative}"
                )
            result.add(audit)
            consumed_sidecars.add(audit)

    orphan_sidecars = sorted(sidecars - consumed_sidecars, key=lambda item: item.as_posix())
    if orphan_sidecars:
        raise ReviewValidationError(
            "orphan binary visual privacy audit(s) have no matching evidence: "
            f"{[path.relative_to(root).as_posix() for path in orphan_sidecars]}"
        )
    return tuple(sorted(result, key=lambda item: item.as_posix()))


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
    paths.extend(review_evidence_paths(run_dir))
    manifest = _mapping(
        _load_json(run_dir / RUN_MANIFEST, "run-manifest.json"),
        "run-manifest.json",
    )
    tasks = _mapping(manifest.get("tasks"), "run-manifest tasks")
    bound_page_probe_evidence: set[Path] = set()
    for task_id, entry_value in tasks.items():
        if not isinstance(entry_value, Mapping) or entry_value.get("status") != "SUCCEEDED":
            continue
        raw_task = entry_value.get("task")
        if not isinstance(raw_task, Mapping) or raw_task.get("agent_role") != "discovery":
            continue
        link = entry_value.get("page_probe_receipt")
        if not isinstance(link, Mapping) or not isinstance(link.get("receipt_id"), str):
            raise ReviewValidationError(
                f"successful Discovery task {task_id!r} has no page probe receipt projection"
            )
        try:
            receipt = load_page_probe_receipt(
                run_dir,
                str(link["receipt_id"]),
                expected_fingerprint=str(link.get("receipt_fingerprint") or ""),
            )
        except PageProbeError as exc:
            raise ReviewValidationError(
                f"successful Discovery task {task_id!r} page probe receipt is stale: {exc}"
            ) from exc
        paths.append(page_probe_receipt_path(run_dir, receipt.receipt_id))
        for item in receipt.evidence:
            evidence_path = _inside_run(
                run_dir, item["path"], "page probe evidence"
            )
            paths.append(evidence_path)
            bound_page_probe_evidence.add(evidence_path)
            if item["sidecar_path"] is not None:
                sidecar_path = _inside_run(
                    run_dir,
                    item["sidecar_path"],
                    "page probe evidence sidecar",
                )
                paths.append(sidecar_path)
                bound_page_probe_evidence.add(sidecar_path)
    registered_receipts: dict[str, str] = {}
    for task_id, entry_value in tasks.items():
        if not isinstance(entry_value, Mapping):
            raise ReviewValidationError(
                f"run-manifest task {task_id!r} must be an object"
            )
        raw_task = entry_value.get("task")
        history = entry_value.get("page_probe_history")
        if history is None:
            history = []
        if not isinstance(history, list):
            raise ReviewValidationError(
                f"task {task_id!r} page_probe_history must be an array"
            )
        links: list[tuple[str, Mapping[str, Any]]] = []
        current_link = entry_value.get("page_probe_receipt")
        if current_link is not None:
            if not isinstance(current_link, Mapping):
                raise ReviewValidationError(
                    f"task {task_id!r} page_probe_receipt must be an object or null"
                )
            links.append(("current", current_link))
        for index, history_link in enumerate(history):
            if not isinstance(history_link, Mapping):
                raise ReviewValidationError(
                    f"task {task_id!r} page_probe_history[{index}] must be an object"
                )
            links.append((f"history[{index}]", history_link))
        if links and (
            not isinstance(raw_task, Mapping)
            or raw_task.get("agent_role") != "discovery"
        ):
            raise ReviewValidationError(
                f"non-Discovery task {task_id!r} carries page probe receipt history"
            )
        for label, link in links:
            receipt_id = link.get("receipt_id")
            receipt_fingerprint = link.get("receipt_fingerprint")
            if not isinstance(receipt_id, str) or not isinstance(
                receipt_fingerprint, str
            ):
                raise ReviewValidationError(
                    f"task {task_id!r} {label} page probe link is incomplete"
                )
            prior_fingerprint = registered_receipts.get(receipt_id)
            if (
                prior_fingerprint is not None
                and prior_fingerprint != receipt_fingerprint
            ):
                raise ReviewValidationError(
                    f"page probe receipt {receipt_id!r} has conflicting manifest bindings"
                )
            registered_receipts[receipt_id] = receipt_fingerprint
            try:
                receipt = load_page_probe_receipt(
                    run_dir,
                    receipt_id,
                    expected_fingerprint=receipt_fingerprint,
                )
                validate_project_record_consumption(
                    run_dir.parents[3], run_dir, receipt
                )
            except PageProbeError as exc:
                raise ReviewValidationError(
                    f"task {task_id!r} {label} page probe receipt is stale: {exc}"
                ) from exc
            if (
                receipt.task_id != task_id
                or link.get("execution_id") != receipt.execution_id
                or link.get("coordinator_id") != receipt.coordinator_id
                or link.get("source_fingerprint") != receipt.source_fingerprint
            ):
                raise ReviewValidationError(
                    f"task {task_id!r} {label} page probe receipt binding is inconsistent"
                )
            paths.append(page_probe_receipt_path(run_dir, receipt.receipt_id))
            for item in receipt.evidence:
                evidence_path = _inside_run(
                    run_dir, item["path"], "registered page probe evidence"
                )
                paths.append(evidence_path)
                bound_page_probe_evidence.add(evidence_path)
                if item["sidecar_path"] is not None:
                    sidecar_path = _inside_run(
                        run_dir,
                        item["sidecar_path"],
                        "registered page probe evidence sidecar",
                    )
                    paths.append(sidecar_path)
                    bound_page_probe_evidence.add(sidecar_path)
    page_probe_root = run_dir / "artifacts" / "page-probe-evidence"
    actual_page_probe_evidence: set[Path] = set()
    if page_probe_root.exists():
        if page_probe_root.is_symlink() or not page_probe_root.is_dir():
            raise ReviewValidationError(
                "artifacts/page-probe-evidence must be a regular directory"
            )
        for candidate in page_probe_root.rglob("*"):
            if candidate.is_symlink():
                raise ReviewValidationError(
                    "page probe evidence must not contain symbolic links: "
                    f"{candidate.relative_to(run_dir).as_posix()}"
                )
            if candidate.is_file():
                actual_page_probe_evidence.add(candidate.resolve())
    if actual_page_probe_evidence != bound_page_probe_evidence:
        orphaned = sorted(
            path.relative_to(run_dir).as_posix()
            for path in actual_page_probe_evidence - bound_page_probe_evidence
        )
        missing = sorted(
            path.relative_to(run_dir).as_posix()
            for path in bound_page_probe_evidence - actual_page_probe_evidence
        )
        raise ReviewValidationError(
            "page probe evidence directory differs from registered receipt bindings: "
            f"orphan={orphaned}, missing={missing}"
        )
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
    generation_fingerprint: str | None = None,
) -> AgentResult:
    if entry.get("status") != TaskStatus.SUCCEEDED.value:
        raise ReviewValidationError(f"task {task_id!r} must have status SUCCEEDED")
    result_path = _inside_run(run_dir, entry.get("result_path"), f"task {task_id} result_path")
    try:
        result = AgentResult.from_dict(_load_json(result_path, f"task {task_id} result"))
    except (TypeError, ValueError) as exc:
        raise ReviewValidationError(f"task {task_id!r} result contract is invalid: {exc}") from exc
    result_fingerprint = entry.get("result_fingerprint")
    if not isinstance(result_fingerprint, str) or not _FINGERPRINT_RE.fullmatch(result_fingerprint):
        raise ReviewValidationError(f"task {task_id!r} has no valid result_fingerprint")
    if hashlib.sha256(result_path.read_bytes()).hexdigest() != result_fingerprint:
        raise ReviewValidationError(f"task {task_id!r} result_fingerprint is stale")
    if (
        result.task_id != task_id
        or result.agent_role is not task.agent_role
        or result.status is not TaskStatus.SUCCEEDED
    ):
        raise ReviewValidationError(f"task {task_id!r} result identity/status does not match run manifest")
    if result.source_fingerprint != task.source_fingerprint:
        raise ReviewValidationError(f"task {task_id!r} result source fingerprint differs from AgentTask")
    if generation_fingerprint is not None and task.source_fingerprint != generation_fingerprint:
        raise ReviewValidationError(f"task {task_id!r} uses a stale generation source fingerprint")
    required_outputs = entry.get("required_outputs")
    if not isinstance(required_outputs, list) or not set(required_outputs).issubset(result.produced_files):
        raise ReviewValidationError(f"task {task_id!r} successful result is missing required outputs")
    if result.gate_summary.get(task.required_gate) is not True:
        raise ReviewValidationError(
            f"task {task_id!r} successful result did not pass gate {task.required_gate!r}"
        )
    return result


def _validate_task_contract_fingerprint(
    run_dir: Path,
    task_id: str,
    entry: Mapping[str, Any],
    task: AgentTask,
) -> None:
    project = run_dir.parents[3]
    expected_paths = list(role_contract_relative_paths(task.agent_role))
    if entry.get("contract_source_paths") != expected_paths:
        raise ReviewValidationError(
            f"task {task_id!r} does not record the complete frozen output-contract path set"
        )
    sources: list[Path] = []
    for relative in expected_paths:
        path = (project / relative).resolve()
        try:
            path.relative_to(project.resolve())
        except ValueError as exc:
            raise ReviewValidationError(f"task {task_id!r} contract path escapes project") from exc
        if not path.is_file():
            raise ReviewValidationError(f"task {task_id!r} contract is missing: {relative}")
        sources.append(path)
    if fingerprint(sources) != entry.get("contract_fingerprint"):
        raise ReviewValidationError(f"task {task_id!r} frozen output contract changed after execution")


def _task_claim(task_id: str, entry: Mapping[str, Any]) -> AgentClaim:
    try:
        claim = AgentClaim.from_dict(entry.get("claim"))
    except (TypeError, ValueError) as exc:
        raise ReviewValidationError(
            f"successful task {task_id!r} has no valid durable execution claim: {exc}"
        ) from exc
    if claim.task_id != task_id:
        raise ReviewValidationError(f"successful task {task_id!r} claim identity is inconsistent")
    return claim


def _validate_claim_binding(
    run_dir: Path,
    task_id: str,
    entry: Mapping[str, Any],
    task: AgentTask,
    claim: AgentClaim,
) -> None:
    expected = {
        "source_fingerprint": task.source_fingerprint,
        "input_snapshot_fingerprint": entry.get("input_snapshot_fingerprint"),
        "task_packet_fingerprint": entry.get("task_packet_fingerprint"),
        "context_fingerprint": entry.get("context_fingerprint"),
    }
    actual = {
        "source_fingerprint": claim.source_fingerprint,
        "input_snapshot_fingerprint": claim.input_snapshot_fingerprint,
        "task_packet_fingerprint": claim.task_packet_fingerprint,
        "context_fingerprint": claim.context_fingerprint,
    }
    if actual != expected:
        raise ReviewValidationError(f"task {task_id!r} claim no longer matches frozen task metadata")

    workspace = run_dir / "artifacts" / "agent-work" / task.agent_role.value / task_id
    input_paths = [_inside_run(run_dir, value, f"task {task_id} input") for value in task.input_files]
    if fingerprint(input_paths) != entry.get("input_snapshot_fingerprint"):
        raise ReviewValidationError(f"task {task_id!r} frozen input snapshot changed after execution")
    if fingerprint([workspace / "meta" / "agent-task.json"]) != entry.get("task_packet_fingerprint"):
        raise ReviewValidationError(f"task {task_id!r} frozen AgentTask packet changed after execution")
    if fingerprint([workspace / "meta" / "task-context.json"]) != entry.get("context_fingerprint"):
        raise ReviewValidationError(f"task {task_id!r} frozen task context changed after execution")


def _validate_discovery_page_probe(
    run_dir: Path,
    task_id: str,
    entry: Mapping[str, Any],
    task: AgentTask,
    claim: AgentClaim,
    events: list[dict[str, object]],
    registry: Mapping[str, Mapping[str, Any]],
) -> None:
    """Independently verify Discovery's durable preflight authority."""

    if task.agent_role is not AgentRole.DISCOVERY:
        if (
            claim.page_probe_receipt_id is not None
            or claim.page_probe_receipt_fingerprint is not None
            or claim.approved_page_mcp_tools
        ):
            raise ReviewValidationError(
                f"non-Discovery task {task_id!r} carries page probe authority"
            )
        return
    if not claim.page_probe_receipt_id or not claim.page_probe_receipt_fingerprint:
        raise ReviewValidationError(
            f"successful Discovery task {task_id!r} has no durable page probe receipt"
        )
    expected_link = {
        "receipt_id": claim.page_probe_receipt_id,
        "receipt_path": (
            f"orchestration/page-probe-receipts/{claim.page_probe_receipt_id}.json"
        ),
        "receipt_fingerprint": claim.page_probe_receipt_fingerprint,
        "execution_id": claim.execution_id,
        "coordinator_id": claim.coordinator_id,
        "source_fingerprint": claim.source_fingerprint,
        "approved_page_mcp_tools": list(claim.approved_page_mcp_tools),
        "status": "ACTIVE",
    }
    link = entry.get("page_probe_receipt")
    if not isinstance(link, Mapping) or dict(link) != expected_link:
        raise ReviewValidationError(
            f"successful Discovery task {task_id!r} page probe projection is stale"
        )
    try:
        receipt = load_page_probe_receipt(
            run_dir,
            claim.page_probe_receipt_id,
            expected_fingerprint=claim.page_probe_receipt_fingerprint,
        )
        validate_project_record_consumption(run_dir.parents[3], run_dir, receipt)
    except (OSError, PageProbeError) as exc:
        raise ReviewValidationError(
            f"successful Discovery task {task_id!r} page probe proof is stale: {exc}"
        ) from exc
    if (
        receipt.run_id != task.run_id
        or receipt.batch_id != task.batch_id
        or receipt.task_id != task_id
        or receipt.execution_id != claim.execution_id
        or receipt.coordinator_id != claim.coordinator_id
        or receipt.source_fingerprint != task.source_fingerprint
        or receipt.approved_mcp_tools != claim.approved_page_mcp_tools
    ):
        raise ReviewValidationError(
            f"successful Discovery task {task_id!r} page probe identity is stale"
        )
    state = registry.get(receipt.receipt_id)
    if (
        not isinstance(state, Mapping)
        or state.get("receipt") != receipt
        or not isinstance(state.get("reserved_sequence"), int)
        or not isinstance(state.get("committed_sequence"), int)
        or state.get("tombstoned_sequence") is not None
    ):
        raise ReviewValidationError(
            f"successful Discovery task {task_id!r} page probe is not uniquely committed"
        )
    matching_claim_events = []
    for event in events:
        if event.get("task_id") != task_id or event.get("event_type") not in {
            "TASK_CLAIMED", "AUDIT_CLAIM_RECOVERED"
        }:
            continue
        payload = event.get("payload")
        if (
            isinstance(payload, Mapping)
            and isinstance(payload.get("claim"), Mapping)
            and payload["claim"].get("execution_id") == claim.execution_id
        ):
            matching_claim_events.append(event)
    if len(matching_claim_events) != 1 or not isinstance(
        matching_claim_events[0].get("sequence"), int
    ):
        raise ReviewValidationError(
            f"successful Discovery task {task_id!r} has no unique claim ordering proof"
        )
    if not (
        int(state["reserved_sequence"])
        < int(state["committed_sequence"])
        < int(matching_claim_events[0]["sequence"])
    ):
        raise ReviewValidationError(
            f"successful Discovery task {task_id!r} claimed before its page probe commit"
        )

    creation_events = [
        event
        for event in events
        if event.get("task_id") == task_id
        and event.get("event_type") in {"TASK_CREATED", "AUDIT_TASK_RECOVERED"}
    ]
    if len(creation_events) != 1:
        raise ReviewValidationError(
            f"successful Discovery task {task_id!r} has no unique creation timestamp"
        )
    try:
        created_at = datetime.fromisoformat(
            str(creation_events[0].get("occurred_at")).replace("Z", "+00:00")
        )
        if created_at.tzinfo is None:
            raise ValueError("creation timestamp lacks timezone")
        future_limit = datetime.now(timezone.utc) + timedelta(minutes=5)
        for record in receipt.records:
            recorded_at = datetime.fromisoformat(
                str(record["recorded_at"]).replace("Z", "+00:00")
            )
            if recorded_at < created_at or recorded_at > future_limit:
                raise ValueError("record is outside the Discovery task lifetime")
    except (TypeError, ValueError) as exc:
        raise ReviewValidationError(
            f"successful Discovery task {task_id!r} uses old or future page probe records"
        ) from exc


def _validate_task_events(
    task_id: str,
    entry: Mapping[str, Any],
    claim: AgentClaim,
    events: list[dict[str, object]],
    *,
    allow_uncommitted_success: bool = False,
) -> None:
    claim_events: list[Mapping[str, Any]] = []
    for event in events:
        if event.get("task_id") != task_id or event.get("event_type") not in {
            "TASK_CLAIMED", "AUDIT_CLAIM_RECOVERED"
        }:
            continue
        payload = event.get("payload")
        if isinstance(payload, Mapping) and isinstance(payload.get("claim"), Mapping):
            claim_events.append(payload["claim"])
    expected_claim = claim.to_dict()
    current_claim_events = [
        event for event in claim_events if event.get("execution_id") == claim.execution_id
    ]
    if len(current_claim_events) != 1:
        raise ReviewValidationError(f"task {task_id!r} has no matching durable full-claim event")
    if claim.executor_kind is ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK:
        degraded = [
            event for event in events
            if event.get("task_id") == task_id
            and event.get("event_type") == "TASK_EXECUTOR_DEGRADED"
            and isinstance(event.get("payload"), Mapping)
            and event["payload"].get("claim") == expected_claim
        ]
        if len(degraded) != 1:
            raise ReviewValidationError(
                f"fallback task {task_id!r} has no unique executor degradation event"
            )
        if current_claim_events[0].get("executor_kind") != ExecutorKind.CODEBUDDY_SUBAGENT.value:
            raise ReviewValidationError(
                f"fallback task {task_id!r} was not originally claimed by a native Agent"
            )
    elif current_claim_events[0] != expected_claim:
        raise ReviewValidationError(f"task {task_id!r} durable claim differs from current claim")

    expected_fingerprint = entry.get("result_fingerprint")
    stored = False
    success_committed = False
    for event in events:
        if event.get("task_id") != task_id or event.get("event_type") not in {
            "TASK_RESULT_STORED", "TASK_SUCCEEDED", "AUDIT_RESULT_RECOVERED"
        }:
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        event_fingerprint = payload.get("result_fingerprint")
        if event_fingerprint != expected_fingerprint:
            raise ReviewValidationError(f"task {task_id!r} has a conflicting durable result event")
        if event.get("event_type") in {"TASK_RESULT_STORED", "AUDIT_RESULT_RECOVERED"}:
            if payload.get("status") != TaskStatus.SUCCEEDED.value:
                raise ReviewValidationError(f"task {task_id!r} durable result event has wrong status")
            stored = True
        if event.get("event_type") == "TASK_SUCCEEDED":
            success_committed = True
        elif event.get("event_type") == "AUDIT_RESULT_RECOVERED" and payload.get(
            "commit_proof"
        ) is True:
            success_committed = True
    if not stored:
        raise ReviewValidationError(f"task {task_id!r} has no durable successful result event")
    if not success_committed and not allow_uncommitted_success:
        raise ReviewValidationError(f"task {task_id!r} has no durable success commit event")


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
    formal_case_order = tuple(case_by_id)
    trace_case_order = tuple(record_by_id)
    if trace_case_order != formal_case_order:
        mismatch_index = next(
            index
            for index, (formal_case_id, trace_case_id) in enumerate(
                zip(formal_case_order, trace_case_order),
                start=1,
            )
            if formal_case_id != trace_case_id
        )
        raise ReviewValidationError(
            "case-traceability.json record order must exactly match formal case order: "
            f"first mismatch at position {mismatch_index}, "
            f"formal={formal_case_order[mismatch_index - 1]!r}, "
            f"trace={trace_case_order[mismatch_index - 1]!r}"
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


def validate_review_artifacts(
    run_dir: Path | str,
    *,
    allow_uncommitted_reviewer_task_id: str | None = None,
) -> bool:
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
    try:
        # Recompute the complete element-by-element DFX 12x4 contract from the
        # promoted formal files.  Reviewer must not trust the Plan Agent's
        # self-reported gate_summary or an earlier cached validation.
        from .engine import _validate_dfx_assessment

        _validate_dfx_assessment(
            root / "artifacts" / "data" / "dfx-assessment.json",
            root / "element-case-plan.csv",
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ReviewValidationError(f"current per-element DFX 12x4 gate has not passed: {exc}") from exc

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
    try:
        event_rows = EventStore(root / "orchestration" / "events.jsonl").read_events()
        page_probe_registry = page_probe_event_registry(event_rows)
    except (EventStoreError, PageProbeError) as exc:
        raise ReviewValidationError(f"orchestration event ledger is invalid: {exc}") from exc
    successful_results: dict[str, AgentResult] = {}
    for task_id, (entry, task) in entries.items():
        if entry.get("status") == TaskStatus.SUCCEEDED.value:
            result = _successful_result(
                root,
                task_id,
                entry,
                task,
                current_task_fingerprint
                if task.agent_role in {AgentRole.CASE_WORKER, AgentRole.REVIEWER}
                else None,
            )
            successful_results[task_id] = result
            claim = _task_claim(task_id, entry)
            if claim.executor_kind not in _FORMAL_EXECUTOR_KINDS:
                raise ReviewValidationError(
                    f"task {task_id!r} used diagnostic executor kind "
                    f"{claim.executor_kind.value!r}; formal review requires a guarded executor"
                )
            _validate_claim_binding(root, task_id, entry, task, claim)
            if claim.executor_kind is ExecutorKind.CODEBUDDY_SUBAGENT:
                try:
                    validate_execution_binding(root.parents[3], root, task, claim)
                except ExecutionBindingError as exc:
                    raise ReviewValidationError(
                        f"task {task_id!r} physical sub-agent execution binding is invalid: {exc}"
                    ) from exc
            else:
                _validate_fallback_authorization(task_id, entry, task, claim)
            _validate_discovery_page_probe(
                root,
                task_id,
                entry,
                task,
                claim,
                event_rows,
                page_probe_registry,
            )
            _validate_task_events(
                task_id,
                entry,
                claim,
                event_rows,
                allow_uncommitted_success=(
                    task.agent_role is AgentRole.REVIEWER
                    and task_id == allow_uncommitted_reviewer_task_id
                ),
            )
            _validate_accepted_snapshot(root, task_id, entry, task)
            _validate_task_contract_fingerprint(root, task_id, entry, task)
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
        if task_id not in successful_results:
            raise ReviewValidationError(f"case worker {task_id!r} has no validated successful result")

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
    if review_task_id not in successful_results:
        raise ReviewValidationError("reviewer task has no validated successful result")
    reviewer_claim = _task_claim(review_task_id, entry)
    if reviewer_claim.executor_kind not in _FORMAL_EXECUTOR_KINDS:
        raise ReviewValidationError(
            "Reviewer must use an authenticated sub-agent or supervisor-authorized fallback claim"
        )
    reviewer_identity = (reviewer_claim.executor_kind.value, reviewer_claim.executor_id)
    conflicting_generators: list[str] = []
    unguarded_generators: list[str] = []
    for generator_task_id, (generator_entry, generator_task) in entries.items():
        if (
            generator_task.agent_role is AgentRole.REVIEWER
            or generator_entry.get("status") != TaskStatus.SUCCEEDED.value
        ):
            continue
        generator_claim = _task_claim(generator_task_id, generator_entry)
        if generator_claim.executor_kind not in _FORMAL_EXECUTOR_KINDS:
            unguarded_generators.append(generator_task_id)
        if (generator_claim.executor_kind.value, generator_claim.executor_id) == reviewer_identity:
            conflicting_generators.append(generator_task_id)
    if unguarded_generators:
        raise ReviewValidationError(
            "formal review is blocked because successful generators used diagnostic "
            f"executor kinds: {unguarded_generators}"
        )
    if conflicting_generators:
        raise ReviewValidationError(
            "reviewer executor identity is not independent from successful generator tasks: "
            f"{conflicting_generators}"
        )

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
    "review_evidence_paths",
    "review_source_fingerprint",
    "validate_review_artifacts",
]
