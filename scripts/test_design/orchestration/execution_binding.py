# -*- coding: utf-8 -*-
"""Read-only validation for guard-created physical sub-agent bindings."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .contracts import AgentClaim, AgentTask, canonical_fingerprint


EXECUTION_BINDING_SCHEMA_VERSION = "1.0.0"
EXECUTION_BINDING_GUARD_VERSION = "4.1.0"
EXECUTION_BINDING_DIR = ".test-design-locks/agent-execution-bindings"
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_TRANSCRIPT_NAME = re.compile(r"^agent-[A-Za-z0-9._-]+\.jsonl$", re.IGNORECASE)
_FIELDS = {
    "schema_version", "guard_version", "run_dir_sha256", "run_id", "batch_id",
    "task_id", "execution_id", "coordinator_id", "executor_id", "executor_kind",
    "source_fingerprint", "input_snapshot_fingerprint", "task_packet_fingerprint",
    "context_fingerprint", "claim_fingerprint", "transcript_path",
    "transcript_path_sha256", "transcript_parent_name", "transcript_file_name",
    "transcript_bound_size", "transcript_prefix_sha256", "transcript_device",
    "transcript_inode", "binding_fingerprint",
}


class ExecutionBindingError(ValueError):
    """The claimed execution lacks one immutable physical sub-agent binding."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExecutionBindingError(f"duplicate execution binding key: {key}")
        result[key] = value
    return result


def _normalized_path(path: Path) -> str:
    value = str(path.resolve(strict=False)).replace("\\", "/").rstrip("/")
    return value.casefold() if os.name == "nt" else value


def execution_binding_path(project_root: Path, execution_id: str) -> Path:
    digest = hashlib.sha256(execution_id.encode("utf-8")).hexdigest()
    return project_root / EXECUTION_BINDING_DIR / f"{digest}.json"


def _validate_transcript_checkpoint(value: dict[str, Any]) -> None:
    raw_path = value.get("transcript_path")
    bound_size = value.get("transcript_bound_size")
    prefix_sha256 = value.get("transcript_prefix_sha256")
    device = value.get("transcript_device")
    inode = value.get("transcript_inode")
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or "\x00" in raw_path
        or not Path(raw_path).is_absolute()
        or not isinstance(bound_size, int)
        or isinstance(bound_size, bool)
        or bound_size <= 0
        or not isinstance(prefix_sha256, str)
        or _FINGERPRINT.fullmatch(prefix_sha256) is None
        or (device is not None and (not isinstance(device, int) or isinstance(device, bool)))
        or (inode is not None and (not isinstance(inode, int) or isinstance(inode, bool)))
    ):
        raise ExecutionBindingError("agent transcript checkpoint fields are invalid")
    transcript_path = Path(raw_path)
    normalized = _normalized_path(transcript_path)
    parent_is_canonical = (
        transcript_path.parent.name.casefold() == "subagents"
        if os.name == "nt"
        else transcript_path.parent.name == "subagents"
    )
    if (
        raw_path != normalized
        or value.get("transcript_path_sha256")
        != hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        or value.get("transcript_parent_name") != "subagents"
        or not parent_is_canonical
        or value.get("transcript_file_name")
        != (transcript_path.name.casefold() if os.name == "nt" else transcript_path.name)
        or _TRANSCRIPT_NAME.fullmatch(transcript_path.name) is None
    ):
        raise ExecutionBindingError("agent transcript path identity is invalid")
    try:
        if transcript_path.is_symlink() or not transcript_path.is_file():
            raise ExecutionBindingError("bound agent transcript is missing or not a regular file")
        stat = transcript_path.stat()
        if stat.st_size < bound_size:
            raise ExecutionBindingError("bound agent transcript was truncated")
        if device is not None and int(stat.st_dev) != device:
            raise ExecutionBindingError("bound agent transcript device identity changed")
        if inode is not None and int(stat.st_ino) != inode:
            raise ExecutionBindingError("bound agent transcript inode identity changed")
        digest = hashlib.sha256()
        remaining = bound_size
        with transcript_path.open("rb") as stream:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ExecutionBindingError("bound agent transcript checkpoint is incomplete")
                digest.update(chunk)
                remaining -= len(chunk)
        if digest.hexdigest() != prefix_sha256:
            raise ExecutionBindingError("bound agent transcript prefix changed after first use")
    except ExecutionBindingError:
        raise
    except OSError as exc:
        raise ExecutionBindingError("bound agent transcript cannot be revalidated") from exc


def validate_execution_binding(
    project_root: Path,
    run_dir: Path,
    task: AgentTask,
    claim: AgentClaim,
) -> dict[str, Any]:
    """Validate the immutable binding emitted by the trusted PreToolUse guard."""

    if claim.executor_kind.value != "codebuddy-subagent":
        raise ExecutionBindingError(
            "formal physical execution proof requires executor_kind=codebuddy-subagent"
        )
    try:
        root = project_root.resolve(strict=True)
    except OSError as exc:
        raise ExecutionBindingError("project root is missing while validating execution binding") from exc
    binding_root = root / EXECUTION_BINDING_DIR
    try:
        resolved_binding_root = binding_root.resolve(strict=True)
        resolved_binding_root.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ExecutionBindingError("agent execution binding registry is missing or unsafe") from exc
    if binding_root.is_symlink() or not binding_root.is_dir():
        raise ExecutionBindingError("agent execution binding registry is missing or unsafe")
    path = execution_binding_path(root, claim.execution_id)
    try:
        unsafe_marker = (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 64 * 1024
        )
    except OSError as exc:
        raise ExecutionBindingError(
            "agent execution binding cannot be inspected"
        ) from exc
    if unsafe_marker:
        raise ExecutionBindingError("agent execution binding is missing or not a regular file")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExecutionBindingError("agent execution binding is invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ExecutionBindingError("agent execution binding field set is invalid")
    content = {key: item for key, item in value.items() if key != "binding_fingerprint"}
    if (
        value.get("schema_version") != EXECUTION_BINDING_SCHEMA_VERSION
        or value.get("guard_version") != EXECUTION_BINDING_GUARD_VERSION
        or value.get("binding_fingerprint") != canonical_fingerprint(content)
        or value.get("run_dir_sha256")
        != hashlib.sha256(_normalized_path(run_dir).encode("utf-8")).hexdigest()
        or value.get("run_id") != task.run_id
        or value.get("batch_id") != task.batch_id
        or value.get("task_id") != task.task_id
        or value.get("execution_id") != claim.execution_id
        or value.get("coordinator_id") != claim.coordinator_id
        or value.get("executor_id") != claim.executor_id
        or value.get("executor_kind") != claim.executor_kind.value
        or value.get("source_fingerprint") != task.source_fingerprint
        or value.get("input_snapshot_fingerprint") != claim.input_snapshot_fingerprint
        or value.get("task_packet_fingerprint") != claim.task_packet_fingerprint
        or value.get("context_fingerprint") != claim.context_fingerprint
        or value.get("claim_fingerprint") != canonical_fingerprint(claim.to_dict())
    ):
        raise ExecutionBindingError("agent execution binding does not match task/claim/transcript")
    _validate_transcript_checkpoint(value)
    return value


__all__ = [
    "EXECUTION_BINDING_DIR",
    "EXECUTION_BINDING_GUARD_VERSION",
    "EXECUTION_BINDING_SCHEMA_VERSION",
    "ExecutionBindingError",
    "execution_binding_path",
    "validate_execution_binding",
]
