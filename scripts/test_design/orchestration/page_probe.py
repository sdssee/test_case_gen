# -*- coding: utf-8 -*-
"""Durable page-probe spool validation and receipt contracts.

The CodeBuddy PostToolUse hook writes an ignored local spool.  This module is
the deterministic trust boundary that validates the complete hash chain,
selects an ordered read/mutation/read proof, binds formal Discovery evidence,
and creates an immutable run-local receipt.  Scheduling and event writes stay
in :mod:`engine` under the run orchestrator lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..io_utils import exclusive_process_lock
from ..sensitive_data import (
    assert_no_sensitive_artifact,
    binary_evidence_audit_path,
)
from .contracts import AgentTask, MCP_TOOL_RE, PageProbeReceipt, canonical_fingerprint


PAGE_PROBE_RECEIPT_DIR = "orchestration/page-probe-receipts"
PAGE_PROBE_EVIDENCE_PREFIX = "artifacts/page-probe-evidence/"
PAGE_PROBE_HOOK_SCHEMA_VERSION = "1.0.0"
PAGE_PROBE_RECORDER_VERSION = "1.0.0"
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_MAX_RECORD_LINE_BYTES = 1024 * 1024
_HOOK_RECORD_FIELDS = {
    "schema_version", "recorder_version", "record_id", "sequence",
    "previous_record_id", "recorded_at", "session_sha256",
    "transcript_path_sha256", "cwd_sha256", "project_root_sha256",
    "hook_tool_name", "tool_name", "tool_input_sha256",
    "tool_response_sha256", "call_content_sha256", "tool_input_bytes",
    "tool_response_bytes", "response_nonempty", "response_error",
    "operation_kind", "operation_name",
}
_CONSUMPTION_FIELDS = {
    "schema_version", "record_id", "receipt_id", "receipt_fingerprint",
    "run_dir_sha256", "run_id", "batch_id", "task_id", "execution_id",
    "coordinator_id", "source_fingerprint", "binding_fingerprint",
}


class PageProbeError(ValueError):
    """A page-probe proof is missing, stale, replayed, or malformed."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PageProbeError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PageProbeError("page probe value is not canonical JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PageProbeError(f"cannot hash page probe file: {path}") from exc
    return digest.hexdigest()


def _normalized_path_text(path: Path) -> str:
    value = str(path.resolve(strict=False)).replace("\\", "/")
    return value.casefold() if os.name == "nt" else value


def _fingerprint(value: object, name: str) -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise PageProbeError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _spool_path(
    project_root: Path,
    session_sha256: str,
    transcript_sha256: str,
) -> Path:
    session = _fingerprint(session_sha256, "session_sha256")
    transcript = _fingerprint(transcript_sha256, "transcript_sha256")
    return (
        project_root
        / ".test-design-locks"
        / "page-probe-spool"
        / f"{session}-{transcript}.jsonl"
    )


def _validate_hook_record(
    value: Any,
    *,
    expected_sequence: int,
    previous_record_id: str | None,
    session_sha256: str,
    transcript_sha256: str,
    project_root_sha256: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _HOOK_RECORD_FIELDS:
        raise PageProbeError("page probe spool record has an invalid field set")
    if value.get("schema_version") != PAGE_PROBE_HOOK_SCHEMA_VERSION:
        raise PageProbeError("page probe spool record schema version mismatch")
    if value.get("recorder_version") != PAGE_PROBE_RECORDER_VERSION:
        raise PageProbeError("page probe spool recorder version mismatch")
    if value.get("sequence") != expected_sequence:
        raise PageProbeError("page probe spool sequence is not contiguous")
    if value.get("previous_record_id") != previous_record_id:
        raise PageProbeError("page probe spool hash chain predecessor mismatch")
    if value.get("session_sha256") != session_sha256:
        raise PageProbeError("page probe spool mixes sessions")
    if value.get("transcript_path_sha256") != transcript_sha256:
        raise PageProbeError("page probe spool mixes transcripts")
    if value.get("project_root_sha256") != project_root_sha256:
        raise PageProbeError("page probe spool belongs to another project root")
    for field in (
        "record_id", "session_sha256", "transcript_path_sha256", "cwd_sha256",
        "project_root_sha256", "tool_input_sha256", "tool_response_sha256",
        "call_content_sha256",
    ):
        _fingerprint(value.get(field), field)
    tool = value.get("tool_name")
    if not isinstance(tool, str) or MCP_TOOL_RE.fullmatch(tool) is None:
        raise PageProbeError("page probe spool record has a non-canonical MCP tool")
    if value.get("operation_kind") not in {"read", "mutation", "unknown"}:
        raise PageProbeError("page probe spool operation_kind is invalid")
    if value.get("operation_name") not in {
        "read", "click", "select", "input", "toggle", "expand", "navigate",
        "other_mutation", "unknown",
    }:
        raise PageProbeError("page probe spool operation_name is invalid")
    for field in ("response_nonempty", "response_error"):
        if type(value.get(field)) is not bool:
            raise PageProbeError(f"page probe spool {field} must be boolean")
    for field in ("tool_input_bytes", "tool_response_bytes"):
        if type(value.get(field)) is not int or value[field] < 0:
            raise PageProbeError(f"page probe spool {field} is invalid")
    expected_id = _sha256_bytes(
        _canonical_bytes({key: item for key, item in value.items() if key != "record_id"})
    )
    if value["record_id"] != expected_id:
        raise PageProbeError("page probe spool record_id content hash mismatch")
    return value


def load_selected_spool_records(
    project_root: Path,
    *,
    session_sha256: str,
    transcript_sha256: str,
    record_ids: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    """Validate the full physical spool and return selected records in call order."""

    root = project_root.resolve(strict=True)
    requested = tuple(_fingerprint(value, "record_id") for value in record_ids)
    if len(requested) < 3 or len(requested) != len(set(requested)):
        raise PageProbeError("page probe commit requires at least three unique record IDs")
    path = _spool_path(root, session_sha256, transcript_sha256)
    if not path.is_file() or path.is_symlink():
        raise PageProbeError("page probe spool is missing or not a regular file")
    project_hash = _sha256_bytes(_normalized_path_text(root).encode("utf-8"))
    records: dict[str, dict[str, Any]] = {}
    previous: str | None = None
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        with exclusive_process_lock(lock_path):
            with path.open("rb") as stream:
                for sequence, raw in enumerate(stream, start=1):
                    if len(raw) > _MAX_RECORD_LINE_BYTES:
                        raise PageProbeError("page probe spool record exceeds size limit")
                    if not raw.endswith(b"\n"):
                        raise PageProbeError("page probe spool has an incomplete trailing record")
                    try:
                        value = json.loads(
                            raw.decode("utf-8"),
                            object_pairs_hook=_reject_duplicate_keys,
                        )
                    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                        raise PageProbeError("page probe spool contains invalid JSONL") from exc
                    record = _validate_hook_record(
                        value,
                        expected_sequence=sequence,
                        previous_record_id=previous,
                        session_sha256=session_sha256,
                        transcript_sha256=transcript_sha256,
                        project_root_sha256=project_hash,
                    )
                    record_id = str(record["record_id"])
                    if record_id in records:
                        raise PageProbeError("page probe spool repeats a record_id")
                    records[record_id] = record
                    previous = record_id
    except RuntimeError as exc:
        raise PageProbeError("page probe spool is being written; retry commit") from exc
    missing = [record_id for record_id in requested if record_id not in records]
    if missing:
        raise PageProbeError(f"page probe spool is missing selected records: {missing}")
    selected = tuple(records[record_id] for record_id in requested)
    if [int(item["sequence"]) for item in selected] != sorted(
        int(item["sequence"]) for item in selected
    ):
        raise PageProbeError("page probe record IDs must follow physical call order")
    return selected


def _inside_run_artifact(run_dir: Path, raw: str, label: str) -> tuple[Path, str]:
    if not isinstance(raw, str) or not raw or "\\" in raw or raw.startswith("/"):
        raise PageProbeError(f"{label} must be a run-relative POSIX path")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise PageProbeError(f"{label} must be normalized without traversal")
    resolved = (run_dir / Path(*parts)).resolve(strict=True)
    try:
        relative = resolved.relative_to(run_dir.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise PageProbeError(f"{label} escapes the batch run") from exc
    if relative != raw or not relative.startswith("artifacts/"):
        raise PageProbeError(f"{label} must stay under run artifacts/")
    if resolved.is_symlink() or not resolved.is_file() or resolved.stat().st_size < 1:
        raise PageProbeError(f"{label} must be a non-empty regular file")
    return resolved, relative


def validate_probe_evidence(
    run_dir: Path,
    task: AgentTask,
    execution_id: str,
    evidence_paths: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    if not evidence_paths or len(evidence_paths) != len(set(evidence_paths)):
        raise PageProbeError("page probe commit requires unique non-empty evidence paths")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(evidence_paths):
        path, relative = _inside_run_artifact(run_dir, raw, f"evidence[{index}]")
        execution_prefix = f"{PAGE_PROBE_EVIDENCE_PREFIX}{execution_id}/"
        if not relative.startswith(execution_prefix):
            raise PageProbeError(
                "page probe evidence must use the dedicated immutable prefix "
                f"{execution_prefix}: {relative}"
            )
        if relative.endswith(".sensitive-audit.json"):
            raise PageProbeError("page probe evidence must name the evidence, not its audit sidecar")
        try:
            is_text = assert_no_sensitive_artifact(path, f"page probe evidence {relative}")
        except (OSError, TypeError, ValueError) as exc:
            raise PageProbeError(f"page probe evidence privacy validation failed: {exc}") from exc
        sidecar_path: str | None = None
        sidecar_sha: str | None = None
        if not is_text:
            sidecar = binary_evidence_audit_path(path)
            sidecar_path = sidecar.relative_to(run_dir).as_posix()
            sidecar_sha = _sha256_file(sidecar)
        result.append(
            {
                "path": relative,
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
                "sidecar_path": sidecar_path,
                "sidecar_sha256": sidecar_sha,
            }
        )
    return tuple(result)


def create_page_probe_receipt(
    project_root: Path,
    run_dir: Path,
    task: AgentTask,
    *,
    run_id: str,
    batch_id: str,
    execution_id: str,
    coordinator_id: str,
    session_sha256: str,
    transcript_sha256: str,
    record_ids: Sequence[str],
    evidence_paths: Sequence[str],
    not_before: str,
    committed_at: str | None = None,
) -> PageProbeReceipt:
    selected = load_selected_spool_records(
        project_root,
        session_sha256=session_sha256,
        transcript_sha256=transcript_sha256,
        record_ids=record_ids,
    )
    try:
        lower_bound = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PageProbeError("Discovery TASK_CREATED timestamp is invalid") from exc
    if lower_bound.tzinfo is None:
        raise PageProbeError("Discovery TASK_CREATED timestamp must be UTC")
    upper_bound = datetime.now(timezone.utc) + timedelta(minutes=5)
    for record in selected:
        try:
            observed_at = datetime.fromisoformat(
                str(record["recorded_at"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise PageProbeError("selected page probe record timestamp is invalid") from exc
        if observed_at < lower_bound or observed_at > upper_bound:
            raise PageProbeError(
                "selected page probe record predates this Discovery task or is future-dated"
            )
    tools = tuple(sorted({str(record["tool_name"]) for record in selected}))
    namespaces = {
        MCP_TOOL_RE.fullmatch(tool).group(1)  # type: ignore[union-attr]
        for tool in tools
    }
    if len(namespaces) != 1:
        raise PageProbeError("page probe approved tools must use one exact MCP server namespace")
    records = tuple(
        {
            key: record[key]
            for key in PageProbeReceipt.RECORD_FIELDS
        }
        for record in selected
    )
    evidence = validate_probe_evidence(run_dir, task, execution_id, evidence_paths)
    if committed_at is None:
        recorded_at = str(selected[-1].get("recorded_at") or "")
        matched_time = re.fullmatch(
            r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?Z",
            recorded_at,
        )
        if matched_time is None:
            raise PageProbeError("selected page probe record has an invalid recorded_at")
        timestamp = matched_time.group(1) + "Z"
    else:
        timestamp = committed_at
    content = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "batch_id": batch_id,
        "task_id": task.task_id,
        "execution_id": execution_id,
        "coordinator_id": coordinator_id,
        "source_fingerprint": task.source_fingerprint,
        "committed_at": timestamp,
        "probe_session_sha256": _fingerprint(session_sha256, "session_sha256"),
        "probe_transcript_sha256": _fingerprint(transcript_sha256, "transcript_sha256"),
        "mcp_server": next(iter(namespaces)),
        "approved_mcp_tools": list(tools),
        "records": list(records),
        "evidence": list(evidence),
    }
    receipt_fingerprint = canonical_fingerprint(content)
    return PageProbeReceipt.from_dict(
        {
            **content,
            "receipt_id": f"PPR-{receipt_fingerprint[:24]}",
            "receipt_fingerprint": receipt_fingerprint,
        }
    )


def receipt_path(run_dir: Path, receipt_id: str) -> Path:
    if not isinstance(receipt_id, str) or not re.fullmatch(r"PPR-[0-9a-f]{24}", receipt_id):
        raise PageProbeError("page probe receipt_id is invalid")
    return run_dir / PAGE_PROBE_RECEIPT_DIR / f"{receipt_id}.json"


def load_page_probe_receipt(
    run_dir: Path,
    receipt_id: str,
    *,
    expected_fingerprint: str | None = None,
    validate_evidence: bool = True,
) -> PageProbeReceipt:
    path = receipt_path(run_dir, receipt_id)
    if not path.is_file() or path.is_symlink():
        raise PageProbeError(f"page probe receipt is missing: {receipt_id}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        receipt = PageProbeReceipt.from_dict(value)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PageProbeError(f"page probe receipt is invalid: {receipt_id}: {exc}") from exc
    if expected_fingerprint is not None and receipt.receipt_fingerprint != expected_fingerprint:
        raise PageProbeError("page probe receipt fingerprint does not match claim")
    if validate_evidence:
        for item in receipt.evidence:
            evidence, relative = _inside_run_artifact(
                run_dir, str(item["path"]), "receipt evidence"
            )
            if evidence.stat().st_size != item["bytes"] or _sha256_file(evidence) != item["sha256"]:
                raise PageProbeError(f"page probe receipt evidence changed: {relative}")
            sidecar_path = item["sidecar_path"]
            if sidecar_path is not None:
                sidecar, sidecar_relative = _inside_run_artifact(
                    run_dir, str(sidecar_path), "receipt evidence sidecar"
                )
                if _sha256_file(sidecar) != item["sidecar_sha256"]:
                    raise PageProbeError(
                        f"page probe receipt evidence sidecar changed: {sidecar_relative}"
                    )
            try:
                assert_no_sensitive_artifact(evidence, f"page probe receipt evidence {relative}")
            except (OSError, TypeError, ValueError) as exc:
                raise PageProbeError(
                    f"page probe receipt evidence no longer passes privacy checks: {exc}"
                ) from exc
    return receipt


def receipt_record_ids(receipt: PageProbeReceipt) -> tuple[str, ...]:
    return tuple(str(record["record_id"]) for record in receipt.records)


def _consumption_value(
    run_dir: Path,
    receipt: PageProbeReceipt,
    record_id: str,
) -> dict[str, Any]:
    content = {
        "schema_version": "1.0.0",
        "record_id": record_id,
        "receipt_id": receipt.receipt_id,
        "receipt_fingerprint": receipt.receipt_fingerprint,
        "run_dir_sha256": _sha256_bytes(
            _normalized_path_text(run_dir.resolve()).encode("utf-8")
        ),
        "run_id": receipt.run_id,
        "batch_id": receipt.batch_id,
        "task_id": receipt.task_id,
        "execution_id": receipt.execution_id,
        "coordinator_id": receipt.coordinator_id,
        "source_fingerprint": receipt.source_fingerprint,
    }
    return {**content, "binding_fingerprint": canonical_fingerprint(content)}


def _load_consumption(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise PageProbeError(f"page probe consumption record is not regular: {path.name}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise PageProbeError(f"page probe consumption record is invalid: {path.name}") from exc
    if not isinstance(value, dict) or set(value) != _CONSUMPTION_FIELDS:
        raise PageProbeError("page probe consumption record field set is invalid")
    content = {
        key: item for key, item in value.items() if key != "binding_fingerprint"
    }
    if (
        value.get("schema_version") != "1.0.0"
        or not isinstance(value.get("record_id"), str)
        or _FINGERPRINT.fullmatch(value["record_id"]) is None
        or value.get("binding_fingerprint") != canonical_fingerprint(content)
    ):
        raise PageProbeError("page probe consumption record fingerprint is invalid")
    return value


def _prepare_consumption_root(project_root: Path) -> tuple[Path, Path]:
    root = project_root.resolve(strict=True)
    lock_root = root / ".test-design-locks"
    if lock_root.exists() and (lock_root.is_symlink() or not lock_root.is_dir()):
        raise PageProbeError(".test-design-locks must be a non-symlink directory")
    lock_root.mkdir(exist_ok=True)
    consumption_root = lock_root / "page-probe-consumption"
    if consumption_root.exists() and (
        consumption_root.is_symlink() or not consumption_root.is_dir()
    ):
        raise PageProbeError("page probe consumption root must be a non-symlink directory")
    consumption_root.mkdir(exist_ok=True)
    return consumption_root, lock_root / "page-probe-consumption.lock"


def reserve_project_record_consumption(
    project_root: Path,
    run_dir: Path,
    receipt: PageProbeReceipt,
) -> None:
    """Atomically reserve every selected record across all runs in a project."""

    root, lock_path = _prepare_consumption_root(project_root)
    candidates = {
        record_id: _consumption_value(run_dir, receipt, record_id)
        for record_id in receipt_record_ids(receipt)
    }
    try:
        with exclusive_process_lock(lock_path):
            missing: list[tuple[Path, dict[str, Any]]] = []
            for record_id, candidate in candidates.items():
                path = root / f"{record_id}.json"
                if path.exists():
                    if _load_consumption(path) != candidate:
                        raise PageProbeError(
                            f"page probe record {record_id} was consumed by another run/receipt"
                        )
                else:
                    missing.append((path, candidate))
            for path, candidate in missing:
                raw = _canonical_bytes(candidate) + b"\n"
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                if hasattr(os, "O_BINARY"):
                    flags |= os.O_BINARY
                temporary = root / (
                    f".{candidate['record_id']}.{os.getpid()}."
                    f"{os.urandom(8).hex()}.tmp"
                )
                descriptor: int | None = None
                try:
                    # Write and fsync a private file first, then publish it with
                    # a no-replace hard link.  A process crash can leave only a
                    # harmless temp file or a complete marker, never a partial
                    # authoritative marker.
                    descriptor = os.open(temporary, flags, 0o600)
                    view = memoryview(raw)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("short page probe consumption write")
                        view = view[written:]
                    os.fsync(descriptor)
                    os.close(descriptor)
                    descriptor = None
                    os.link(temporary, path)
                except FileExistsError:
                    if _load_consumption(path) != candidate:
                        raise PageProbeError(
                            f"page probe record {candidate['record_id']} was consumed concurrently"
                        )
                except OSError as exc:
                    raise PageProbeError(
                        f"cannot reserve page probe record {candidate['record_id']}"
                    ) from exc
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        # The authoritative marker, if linked, is already
                        # complete.  Stale dot-temp files are never read as
                        # consumption records.
                        pass
    except RuntimeError as exc:
        raise PageProbeError("page probe consumption registry is busy") from exc


def validate_project_record_consumption(
    project_root: Path,
    run_dir: Path,
    receipt: PageProbeReceipt,
) -> None:
    project = project_root.resolve(strict=True)
    lock_root = project / ".test-design-locks"
    root = lock_root / "page-probe-consumption"
    lock_path = lock_root / "page-probe-consumption.lock"
    if (
        lock_root.is_symlink()
        or not lock_root.is_dir()
        or root.is_symlink()
        or not root.is_dir()
    ):
        raise PageProbeError("page probe consumption registry is missing or unsafe")
    try:
        with exclusive_process_lock(lock_path):
            for record_id in receipt_record_ids(receipt):
                expected = _consumption_value(run_dir, receipt, record_id)
                if _load_consumption(root / f"{record_id}.json") != expected:
                    raise PageProbeError(
                        f"page probe consumption binding changed for record {record_id}"
                    )
    except RuntimeError as exc:
        raise PageProbeError("page probe consumption registry is busy") from exc


def receipt_event_payload(receipt: PageProbeReceipt) -> dict[str, Any]:
    return {
        "receipt_id": receipt.receipt_id,
        "receipt_fingerprint": receipt.receipt_fingerprint,
        "execution_id": receipt.execution_id,
        "coordinator_id": receipt.coordinator_id,
        "source_fingerprint": receipt.source_fingerprint,
        "record_ids": list(receipt_record_ids(receipt)),
        "approved_page_mcp_tools": list(receipt.approved_mcp_tools),
        "mcp_server": receipt.mcp_server,
    }


def page_probe_event_registry(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Rebuild the authoritative one-time receipt/record registry from events."""

    registry: dict[str, dict[str, Any]] = {}
    record_owners: dict[str, str] = {}
    execution_owners: dict[tuple[str, str], str] = {}
    for event in events:
        event_type = event.get("event_type")
        if event_type not in {
            "PAGE_PROBE_RECORDS_RESERVED",
            "PAGE_PROBE_COMMITTED",
            "AUDIT_PAGE_PROBE_COMMITTED",
            "PAGE_PROBE_TOMBSTONED",
            "AUDIT_PAGE_PROBE_TOMBSTONED",
        }:
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise PageProbeError(f"{event_type} payload must be an object")
        if event_type == "PAGE_PROBE_RECORDS_RESERVED":
            raw_receipt = payload.get("receipt")
            try:
                receipt = PageProbeReceipt.from_dict(raw_receipt)
            except (TypeError, ValueError) as exc:
                raise PageProbeError(f"reserved page probe receipt is invalid: {exc}") from exc
            if event.get("task_id") != receipt.task_id:
                raise PageProbeError("reserved page probe event task_id mismatch")
            receipt_id = receipt.receipt_id
            if receipt_id in registry:
                raise PageProbeError(f"duplicate page probe reservation: {receipt_id}")
            execution_key = (receipt.task_id, receipt.execution_id)
            existing_execution = execution_owners.get(execution_key)
            if existing_execution is not None and existing_execution != receipt_id:
                raise PageProbeError(
                    "one task/execution cannot reserve multiple page probe receipts"
                )
            execution_owners[execution_key] = receipt_id
            for record_id in receipt_record_ids(receipt):
                owner = record_owners.get(record_id)
                if owner is not None and owner != receipt_id:
                    raise PageProbeError(
                        f"page probe record {record_id} was replayed by receipt {receipt_id}"
                    )
                record_owners[record_id] = receipt_id
            registry[receipt_id] = {
                "receipt": receipt,
                "reserved_sequence": event.get("sequence"),
                "committed_sequence": None,
                "tombstoned_sequence": None,
            }
            continue

        receipt_id = payload.get("receipt_id")
        if not isinstance(receipt_id, str) or receipt_id not in registry:
            raise PageProbeError(f"{event_type} has no prior page probe reservation")
        state = registry[receipt_id]
        receipt = state["receipt"]
        if event.get("task_id") != receipt.task_id:
            raise PageProbeError(f"{event_type} event task_id mismatch")
        expected = receipt_event_payload(receipt)
        for key, value in expected.items():
            if payload.get(key) != value:
                raise PageProbeError(f"{event_type} conflicts with reserved receipt {receipt_id}")
        sequence = event.get("sequence")
        if not isinstance(sequence, int) or sequence <= int(state["reserved_sequence"]):
            raise PageProbeError(f"{event_type} event ordering is invalid")
        if event_type in {"PAGE_PROBE_COMMITTED", "AUDIT_PAGE_PROBE_COMMITTED"}:
            if state["committed_sequence"] is not None:
                raise PageProbeError(f"duplicate page probe commit event: {receipt_id}")
            state["committed_sequence"] = sequence
        else:
            if state["committed_sequence"] is None:
                raise PageProbeError("page probe receipt cannot be tombstoned before commit")
            if state["tombstoned_sequence"] is not None:
                raise PageProbeError(f"duplicate page probe tombstone event: {receipt_id}")
            state["tombstoned_sequence"] = sequence
    return registry


__all__ = [
    "PAGE_PROBE_EVIDENCE_PREFIX",
    "PAGE_PROBE_RECEIPT_DIR",
    "PageProbeError",
    "create_page_probe_receipt",
    "load_page_probe_receipt",
    "page_probe_event_registry",
    "receipt_event_payload",
    "receipt_path",
    "receipt_record_ids",
    "reserve_project_record_consumption",
    "validate_project_record_consumption",
    "validate_probe_evidence",
]
