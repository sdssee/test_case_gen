# -*- coding: utf-8 -*-
"""Strict, JSON-serializable contracts for the multi-agent orchestration layer.

This module deliberately contains no scheduling or state-machine behavior.  It
defines the only payloads that agents and the deterministic orchestrator may
exchange.  Paths are run-directory-relative POSIX paths so a contract remains
portable and cannot escape its run workspace.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, ClassVar, Mapping


SCHEMA_VERSION = "1.0.0"
SOURCE_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GATE_NAMES = frozenset({"discovery", "plan", "risk", "cases-worker", "cases", "review", "delivery"})
INPUT_FILE_PREFIXES = (
    "artifacts/data/",
    "artifacts/evidence/",
    "artifacts/screenshots/",
    "orchestration/inputs/",
    "orchestration/rework-requests/",
)
INPUT_FILE_EXACT = frozenset({"batch-scope.json"})


class AgentRole(str, Enum):
    DISCOVERY = "discovery"
    PLAN_DFX = "plan_dfx"
    RISK_ARBITER = "risk_arbiter"
    CASE_WORKER = "case_worker"
    REVIEWER = "reviewer"


class TaskStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_REWORK = "NEEDS_REWORK"
    EXTERNAL_BLOCKED = "EXTERNAL_BLOCKED"


class ReworkTarget(str, Enum):
    DISCOVERY = "discovery"
    PLAN = "plan"
    RISK = "risk"
    CASES = "cases"
    REVIEW = "review"
    DELIVERY = "delivery"


class ReworkReason(str, Enum):
    ELEMENT_NOT_EXECUTED = "ELEMENT_NOT_EXECUTED"
    SELECTION_OPTION_MISSING = "SELECTION_OPTION_MISSING"
    CRUD_EFFECT_UNVERIFIED = "CRUD_EFFECT_UNVERIFIED"
    PAGE_VERIFIABLE_RISK = "PAGE_VERIFIABLE_RISK"
    EXTERNAL_SEMANTICS_REQUIRED = "EXTERNAL_SEMANTICS_REQUIRED"
    DFX_GAP = "DFX_GAP"
    CASE_BUDGET_INSUFFICIENT = "CASE_BUDGET_INSUFFICIENT"
    DUPLICATE_STEPS = "DUPLICATE_STEPS"
    DUPLICATE_EXPECTED_RESULT = "DUPLICATE_EXPECTED_RESULT"
    FUNCTION_POINT_DRIFT = "FUNCTION_POINT_DRIFT"
    TRACEABILITY_GAP = "TRACEABILITY_GAP"
    DELIVERY_MISMATCH = "DELIVERY_MISMATCH"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    AGENT_OUTPUT_INVALID = "AGENT_OUTPUT_INVALID"


def canonical_fingerprint(value: Any) -> str:
    """Return a stable SHA-256 fingerprint for JSON-compatible input."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    return value


def _strict_keys(data: Mapping[str, Any], required: set[str], name: str) -> None:
    if any(not isinstance(key, str) for key in data):
        raise TypeError(f"{name} field names must be strings")
    actual = set(data)
    missing = sorted(required - actual)
    extra = sorted(actual - required)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"unknown={extra}")
        raise ValueError(f"{name} has invalid fields: {', '.join(details)}")


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name)


def _identifier(value: object, name: str) -> str:
    text = _required_string(value, name)
    if not IDENTIFIER_RE.fullmatch(text):
        raise ValueError(f"{name} must match {IDENTIFIER_RE.pattern}")
    return text


def _scope_identifier(value: object, name: str) -> str:
    text = _required_string(value, name)
    if len(text) > 128 or text in {".", ".."} or any(char in text for char in "/\\\x00"):
        raise ValueError(f"{name} must be a single safe run/batch identifier")
    return text


def _fingerprint(value: object, name: str = "source_fingerprint") -> str:
    text = _required_string(value, name)
    if not SOURCE_FINGERPRINT_RE.fullmatch(text):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return text


def _enum(value: object, enum_type: type[Enum], name: str):
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = [item.value for item in enum_type]
        raise ValueError(f"{name} must be one of {allowed}") from exc


def _string_tuple(value: object, name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be an array")
    result = tuple(_required_string(item, f"{name}[{index}]") for index, item in enumerate(value))
    if not allow_empty and not result:
        raise ValueError(f"{name} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _relative_posix_path(value: object, name: str) -> str:
    text = _required_string(value, name)
    if "\\" in text or "\x00" in text:
        raise ValueError(f"{name} must be a POSIX path without NUL or backslash")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise ValueError(f"{name} must be relative to the run directory")
    raw_parts = text.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"{name} must be normalized and must not traverse directories")
    path = PurePosixPath(text)
    if str(path) != text:
        raise ValueError(f"{name} must be a normalized POSIX path")
    return text


def _input_path(value: object, name: str) -> str:
    path = _relative_posix_path(value, name)
    if path not in INPUT_FILE_EXACT and not any(path.startswith(prefix) for prefix in INPUT_FILE_PREFIXES):
        raise ValueError(
            f"{name} is outside the input whitelist; allowed exact paths={sorted(INPUT_FILE_EXACT)}, "
            f"prefixes={list(INPUT_FILE_PREFIXES)}"
        )
    return path


def _output_root(role: AgentRole, task_id: str) -> str:
    return f"artifacts/agent-work/{role.value}/{task_id}/"


def _output_path(value: object, name: str, role: AgentRole, task_id: str) -> str:
    path = _relative_posix_path(value, name)
    root = _output_root(role, task_id)
    if not path.startswith(root) or path == root.rstrip("/"):
        raise ValueError(f"{name} must be a file below {root}")
    return path


def _output_prefix(value: object, name: str, role: AgentRole, task_id: str) -> str:
    text = _required_string(value, name)
    if not text.endswith("/"):
        raise ValueError(f"{name} must end with /")
    path = _relative_posix_path(text[:-1], name) + "/"
    root = _output_root(role, task_id)
    if not path.startswith(root) or path == root:
        raise ValueError(f"{name} must be a directory below {root}")
    return path


def _evidence_path(value: object, name: str) -> str:
    path = _relative_posix_path(value, name)
    prefixes = ("artifacts/evidence/", "artifacts/screenshots/", "artifacts/data/", "artifacts/agent-work/")
    if not any(path.startswith(prefix) for prefix in prefixes):
        raise ValueError(f"{name} must be below one of {list(prefixes)}")
    return path


def _schema_version(value: object) -> str:
    if value != SCHEMA_VERSION:
        raise ValueError(f"schema_version must equal {SCHEMA_VERSION}")
    return SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AgentTask:
    schema_version: str
    task_id: str
    run_id: str
    batch_id: str
    phase: ReworkTarget
    agent_role: AgentRole
    owner_key: str | None
    input_files: tuple[str, ...]
    allowed_output_files: tuple[str, ...]
    allowed_output_prefixes: tuple[str, ...]
    required_gate: str
    source_fingerprint: str
    attempt: int

    FIELDS: ClassVar[set[str]] = {
        "schema_version", "task_id", "run_id", "batch_id", "phase", "agent_role", "owner_key",
        "input_files", "allowed_output_files", "allowed_output_prefixes", "required_gate", "source_fingerprint", "attempt",
    }

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        object.__setattr__(self, "run_id", _scope_identifier(self.run_id, "run_id"))
        object.__setattr__(self, "batch_id", _scope_identifier(self.batch_id, "batch_id"))
        object.__setattr__(self, "phase", _enum(self.phase, ReworkTarget, "phase"))
        object.__setattr__(self, "agent_role", _enum(self.agent_role, AgentRole, "agent_role"))
        object.__setattr__(self, "owner_key", _optional_string(self.owner_key, "owner_key"))
        input_files = _string_tuple(self.input_files, "input_files")
        object.__setattr__(self, "input_files", tuple(_input_path(path, f"input_files[{i}]") for i, path in enumerate(input_files)))
        output_files = _string_tuple(self.allowed_output_files, "allowed_output_files", allow_empty=False)
        object.__setattr__(
            self,
            "allowed_output_files",
            tuple(_output_path(path, f"allowed_output_files[{i}]", self.agent_role, self.task_id) for i, path in enumerate(output_files)),
        )
        prefixes = _string_tuple(self.allowed_output_prefixes, "allowed_output_prefixes")
        object.__setattr__(
            self,
            "allowed_output_prefixes",
            tuple(_output_prefix(path, f"allowed_output_prefixes[{i}]", self.agent_role, self.task_id) for i, path in enumerate(prefixes)),
        )
        gate = _required_string(self.required_gate, "required_gate")
        if gate not in GATE_NAMES:
            raise ValueError(f"required_gate must be one of {sorted(GATE_NAMES)}")
        object.__setattr__(self, "required_gate", gate)
        object.__setattr__(self, "source_fingerprint", _fingerprint(self.source_fingerprint))
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be an integer greater than or equal to 1")
        expected_phase = {
            AgentRole.DISCOVERY: ReworkTarget.DISCOVERY,
            AgentRole.PLAN_DFX: ReworkTarget.PLAN,
            AgentRole.RISK_ARBITER: ReworkTarget.RISK,
            AgentRole.CASE_WORKER: ReworkTarget.CASES,
            AgentRole.REVIEWER: ReworkTarget.REVIEW,
        }[self.agent_role]
        if self.phase is not expected_phase:
            raise ValueError(f"{self.agent_role.value} tasks must use phase {expected_phase.value!r}")
        if self.agent_role is AgentRole.CASE_WORKER and self.owner_key is None:
            raise ValueError("case_worker tasks require owner_key")

    @classmethod
    def from_dict(cls, value: object) -> "AgentTask":
        data = _require_mapping(value, "AgentTask")
        _strict_keys(data, cls.FIELDS, "AgentTask")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "phase": self.phase.value,
            "agent_role": self.agent_role.value,
            "owner_key": self.owner_key,
            "input_files": list(self.input_files),
            "allowed_output_files": list(self.allowed_output_files),
            "allowed_output_prefixes": list(self.allowed_output_prefixes),
            "required_gate": self.required_gate,
            "source_fingerprint": self.source_fingerprint,
            "attempt": self.attempt,
        }


@dataclass(frozen=True, slots=True)
class ReworkRequest:
    schema_version: str
    request_id: str
    run_id: str
    batch_id: str
    target_phase: ReworkTarget
    target_task_id: str | None
    reason_code: ReworkReason
    affected_ids: tuple[str, ...]
    evidence: tuple[str, ...]
    required_action: str
    source_fingerprint: str
    attempt: int

    FIELDS: ClassVar[set[str]] = {
        "schema_version", "request_id", "run_id", "batch_id", "target_phase", "target_task_id",
        "reason_code", "affected_ids", "evidence", "required_action", "source_fingerprint", "attempt",
    }

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request_id"))
        object.__setattr__(self, "run_id", _scope_identifier(self.run_id, "run_id"))
        object.__setattr__(self, "batch_id", _scope_identifier(self.batch_id, "batch_id"))
        object.__setattr__(self, "target_phase", _enum(self.target_phase, ReworkTarget, "target_phase"))
        target_task = _optional_string(self.target_task_id, "target_task_id")
        if target_task is not None:
            target_task = _identifier(target_task, "target_task_id")
        object.__setattr__(self, "target_task_id", target_task)
        object.__setattr__(self, "reason_code", _enum(self.reason_code, ReworkReason, "reason_code"))
        affected = _string_tuple(self.affected_ids, "affected_ids", allow_empty=False)
        object.__setattr__(self, "affected_ids", affected)
        evidence = _string_tuple(self.evidence, "evidence")
        object.__setattr__(self, "evidence", tuple(_evidence_path(path, f"evidence[{i}]") for i, path in enumerate(evidence)))
        object.__setattr__(self, "required_action", _required_string(self.required_action, "required_action"))
        object.__setattr__(self, "source_fingerprint", _fingerprint(self.source_fingerprint))
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be an integer greater than or equal to 1")

    @classmethod
    def from_dict(cls, value: object) -> "ReworkRequest":
        data = _require_mapping(value, "ReworkRequest")
        _strict_keys(data, cls.FIELDS, "ReworkRequest")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "target_phase": self.target_phase.value,
            "target_task_id": self.target_task_id,
            "reason_code": self.reason_code.value,
            "affected_ids": list(self.affected_ids),
            "evidence": list(self.evidence),
            "required_action": self.required_action,
            "source_fingerprint": self.source_fingerprint,
            "attempt": self.attempt,
        }


@dataclass(frozen=True, slots=True)
class AgentResult:
    schema_version: str
    task_id: str
    agent_role: AgentRole
    status: TaskStatus
    source_fingerprint: str
    produced_files: tuple[str, ...]
    affected_interaction_ids: tuple[str, ...]
    affected_case_ids: tuple[str, ...]
    facts_used: tuple[str, ...]
    gate_summary: Mapping[str, bool]
    rework_requests: tuple[ReworkRequest, ...]
    error_message: str | None

    FIELDS: ClassVar[set[str]] = {
        "schema_version", "task_id", "agent_role", "status", "source_fingerprint", "produced_files",
        "affected_interaction_ids", "affected_case_ids", "facts_used", "gate_summary", "rework_requests",
        "error_message",
    }

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        object.__setattr__(self, "agent_role", _enum(self.agent_role, AgentRole, "agent_role"))
        object.__setattr__(self, "status", _enum(self.status, TaskStatus, "status"))
        object.__setattr__(self, "source_fingerprint", _fingerprint(self.source_fingerprint))
        produced = _string_tuple(self.produced_files, "produced_files")
        object.__setattr__(
            self,
            "produced_files",
            tuple(_output_path(path, f"produced_files[{i}]", self.agent_role, self.task_id) for i, path in enumerate(produced)),
        )
        for field_name in ("affected_interaction_ids", "affected_case_ids", "facts_used"):
            object.__setattr__(self, field_name, _string_tuple(getattr(self, field_name), field_name))
        summary = _require_mapping(self.gate_summary, "gate_summary")
        normalized_summary: dict[str, bool] = {}
        for key, passed in summary.items():
            normalized_key = _required_string(key, "gate_summary key")
            if type(passed) is not bool:
                raise TypeError(f"gate_summary[{normalized_key!r}] must be a boolean")
            normalized_summary[normalized_key] = passed
        object.__setattr__(self, "gate_summary", MappingProxyType(normalized_summary))
        if not isinstance(self.rework_requests, (list, tuple)):
            raise TypeError("rework_requests must be an array")
        requests = tuple(
            item if isinstance(item, ReworkRequest) else ReworkRequest.from_dict(item)
            for item in self.rework_requests
        )
        if len({item.request_id for item in requests}) != len(requests):
            raise ValueError("rework_requests must not contain duplicate request_id values")
        if any(item.source_fingerprint != self.source_fingerprint for item in requests):
            raise ValueError("rework_requests must use the result source_fingerprint")
        object.__setattr__(self, "rework_requests", requests)
        object.__setattr__(self, "error_message", _optional_string(self.error_message, "error_message"))
        if self.status is TaskStatus.SUCCEEDED and (requests or self.error_message):
            raise ValueError("SUCCEEDED results must not contain rework_requests or error_message")
        if self.status is TaskStatus.NEEDS_REWORK and not requests:
            raise ValueError("NEEDS_REWORK results require at least one rework request")
        if self.status is not TaskStatus.NEEDS_REWORK and requests:
            raise ValueError("only NEEDS_REWORK results may contain rework_requests")
        if self.status in {TaskStatus.FAILED, TaskStatus.EXTERNAL_BLOCKED} and self.error_message is None:
            raise ValueError(f"{self.status.value} results require error_message")

    @classmethod
    def from_dict(cls, value: object) -> "AgentResult":
        data = _require_mapping(value, "AgentResult")
        _strict_keys(data, cls.FIELDS, "AgentResult")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "agent_role": self.agent_role.value,
            "status": self.status.value,
            "source_fingerprint": self.source_fingerprint,
            "produced_files": list(self.produced_files),
            "affected_interaction_ids": list(self.affected_interaction_ids),
            "affected_case_ids": list(self.affected_case_ids),
            "facts_used": list(self.facts_used),
            "gate_summary": dict(self.gate_summary),
            "rework_requests": [item.to_dict() for item in self.rework_requests],
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class TraceabilityRecord:
    schema_version: str
    case_id: str
    function_point: str
    plan_owner_id: str
    interaction_ids: tuple[str, ...]
    selection_observation_ids: tuple[str, ...]
    branch_observation_ids: tuple[str, ...]
    lifecycle_ids: tuple[str, ...]
    evidence_hashes: tuple[str, ...]
    worker_task_id: str
    source_fingerprint: str

    FIELDS: ClassVar[set[str]] = {
        "schema_version", "case_id", "function_point", "plan_owner_id", "interaction_ids",
        "selection_observation_ids", "branch_observation_ids", "lifecycle_ids", "evidence_hashes",
        "worker_task_id", "source_fingerprint",
    }

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        object.__setattr__(self, "case_id", _identifier(self.case_id, "case_id"))
        object.__setattr__(self, "function_point", _required_string(self.function_point, "function_point"))
        object.__setattr__(self, "plan_owner_id", _identifier(self.plan_owner_id, "plan_owner_id"))
        for field_name in (
            "interaction_ids", "selection_observation_ids", "branch_observation_ids", "lifecycle_ids"
        ):
            object.__setattr__(self, field_name, _string_tuple(getattr(self, field_name), field_name))
        hashes = _string_tuple(self.evidence_hashes, "evidence_hashes")
        object.__setattr__(
            self,
            "evidence_hashes",
            tuple(_fingerprint(item, f"evidence_hashes[{i}]") for i, item in enumerate(hashes)),
        )
        object.__setattr__(self, "worker_task_id", _identifier(self.worker_task_id, "worker_task_id"))
        object.__setattr__(self, "source_fingerprint", _fingerprint(self.source_fingerprint))

    @classmethod
    def from_dict(cls, value: object) -> "TraceabilityRecord":
        data = _require_mapping(value, "TraceabilityRecord")
        _strict_keys(data, cls.FIELDS, "TraceabilityRecord")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "function_point": self.function_point,
            "plan_owner_id": self.plan_owner_id,
            "interaction_ids": list(self.interaction_ids),
            "selection_observation_ids": list(self.selection_observation_ids),
            "branch_observation_ids": list(self.branch_observation_ids),
            "lifecycle_ids": list(self.lifecycle_ids),
            "evidence_hashes": list(self.evidence_hashes),
            "worker_task_id": self.worker_task_id,
            "source_fingerprint": self.source_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class RunConfig:
    schema_version: str
    run_id: str
    batch_id: str
    agent_mode: str
    parallel_discovery: bool
    case_parallel_threshold: int
    max_case_workers: int
    max_rework_attempts: int
    review_required: bool
    delivery_single_writer: bool
    source_fingerprint: str

    FIELDS: ClassVar[set[str]] = {
        "schema_version", "run_id", "batch_id", "agent_mode", "parallel_discovery",
        "case_parallel_threshold", "max_case_workers", "max_rework_attempts", "review_required",
        "delivery_single_writer", "source_fingerprint",
    }

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        object.__setattr__(self, "run_id", _scope_identifier(self.run_id, "run_id"))
        object.__setattr__(self, "batch_id", _scope_identifier(self.batch_id, "batch_id"))
        if self.agent_mode != "required":
            raise ValueError("agent_mode must equal 'required' in the final architecture")
        for field_name in ("parallel_discovery", "review_required", "delivery_single_writer"):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be a boolean")
        if self.parallel_discovery:
            raise ValueError("parallel_discovery must be false to preserve the single discovery owner")
        if not self.review_required:
            raise ValueError("review_required must be true")
        if not self.delivery_single_writer:
            raise ValueError("delivery_single_writer must be true")
        if type(self.case_parallel_threshold) is not int or self.case_parallel_threshold < 1:
            raise ValueError("case_parallel_threshold must be an integer greater than or equal to 1")
        if type(self.max_case_workers) is not int or not 1 <= self.max_case_workers <= 32:
            raise ValueError("max_case_workers must be an integer between 1 and 32")
        if type(self.max_rework_attempts) is not int or not 0 <= self.max_rework_attempts <= 10:
            raise ValueError("max_rework_attempts must be an integer between 0 and 10")
        object.__setattr__(self, "source_fingerprint", _fingerprint(self.source_fingerprint))

    @classmethod
    def from_dict(cls, value: object) -> "RunConfig":
        data = _require_mapping(value, "RunConfig")
        _strict_keys(data, cls.FIELDS, "RunConfig")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "agent_mode": self.agent_mode,
            "parallel_discovery": self.parallel_discovery,
            "case_parallel_threshold": self.case_parallel_threshold,
            "max_case_workers": self.max_case_workers,
            "max_rework_attempts": self.max_rework_attempts,
            "review_required": self.review_required,
            "delivery_single_writer": self.delivery_single_writer,
            "source_fingerprint": self.source_fingerprint,
        }


__all__ = [
    "AgentResult",
    "AgentRole",
    "AgentTask",
    "ReworkReason",
    "ReworkRequest",
    "ReworkTarget",
    "RunConfig",
    "SCHEMA_VERSION",
    "TaskStatus",
    "TraceabilityRecord",
    "canonical_fingerprint",
]
