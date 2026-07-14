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
MCP_TOOL_RE = re.compile(r"^mcp__([A-Za-z0-9_.:-]+)__([A-Za-z0-9_.:-]+)$")
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


COMMON_AGENT_CONTRACT_PATHS = (
    "docs/test-design/schemas/orchestration/agent-task.schema.json",
    "docs/test-design/schemas/orchestration/agent-result.schema.json",
    "docs/test-design/schemas/orchestration/rework-request.schema.json",
)

ROLE_AGENT_CONTRACT_PATHS = {
    AgentRole.DISCOVERY: (
        "docs/test-assets/batch-runs/templates/page-element-inventory-template.csv",
        "docs/test-assets/batch-runs/templates/page-discovery-template.csv",
        "docs/test-assets/batch-runs/templates/selection-option-observations-template.csv",
        "docs/test-assets/batch-runs/templates/interaction-branch-observations-template.csv",
        "docs/test-assets/batch-runs/templates/test-data-lifecycle-template.csv",
        "docs/test-design/schemas/orchestration/binary-evidence-audit.schema.json",
    ),
    AgentRole.PLAN_DFX: (
        "docs/test-assets/batch-runs/templates/element-case-plan-template.csv",
        "docs/test-assets/batch-runs/templates/selection-option-observations-template.csv",
        "docs/test-assets/batch-runs/templates/interaction-branch-observations-template.csv",
        "docs/test-assets/batch-runs/templates/test-data-lifecycle-template.csv",
        "scripts/test_design/contracts/sheet_data.py",
        "docs/test-design/schemas/orchestration/dfx-assessment.schema.json",
        "docs/test-design/schemas/orchestration/risk-candidates.schema.json",
    ),
    AgentRole.RISK_ARBITER: (
        "docs/test-assets/batch-runs/templates/risk-confirmation-template.csv",
        "scripts/test_design/contracts/sheet_data.py",
        "docs/test-design/schemas/orchestration/risk-candidates.schema.json",
    ),
    AgentRole.CASE_WORKER: (
        "scripts/test_design/contracts/function_cases.py",
        "docs/test-design/schemas/orchestration/traceability-record.schema.json",
    ),
    AgentRole.REVIEWER: (
        "docs/test-design/schemas/orchestration/review-report.schema.json",
        "docs/test-design/schemas/orchestration/binary-evidence-audit.schema.json",
        "docs/test-design/schemas/orchestration/traceability-record.schema.json",
    ),
}


def role_contract_relative_paths(role: AgentRole | str) -> tuple[str, ...]:
    normalized = role if isinstance(role, AgentRole) else AgentRole(role)
    return (*COMMON_AGENT_CONTRACT_PATHS, *ROLE_AGENT_CONTRACT_PATHS[normalized])


class TaskStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_REWORK = "NEEDS_REWORK"
    EXTERNAL_BLOCKED = "EXTERNAL_BLOCKED"


class ExecutorKind(str, Enum):
    CODEBUDDY_SUBAGENT = "codebuddy-subagent"
    CODEBUDDY_ISOLATED_FALLBACK = "codebuddy-isolated-fallback"
    CODEBUDDY_MAIN_SESSION = "codebuddy-main-session"
    CODEBUDDY_AGENT_TEAM = "codebuddy-agent-team"
    EXTERNAL_SESSION = "external-session"


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


def _execution_label(value: object, name: str) -> str:
    text = _required_string(value, name)
    if len(text) > 256 or any(char in text for char in "\r\n\x00"):
        raise ValueError(f"{name} must be a single line of at most 256 characters")
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
class AgentClaim:
    schema_version: str
    execution_id: str
    task_id: str
    coordinator_id: str
    executor_id: str
    executor_kind: ExecutorKind
    wave_id: str
    claimed_at: str
    source_fingerprint: str
    input_snapshot_fingerprint: str
    task_packet_fingerprint: str
    context_fingerprint: str
    page_probe_receipt_id: str | None
    page_probe_receipt_fingerprint: str | None
    approved_page_mcp_tools: tuple[str, ...]

    FIELDS: ClassVar[set[str]] = {
        "schema_version", "execution_id", "task_id", "coordinator_id", "executor_id",
        "executor_kind", "wave_id", "claimed_at", "source_fingerprint",
        "input_snapshot_fingerprint", "task_packet_fingerprint", "context_fingerprint",
        "page_probe_receipt_id", "page_probe_receipt_fingerprint",
        "approved_page_mcp_tools",
    }

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        object.__setattr__(self, "execution_id", _identifier(self.execution_id, "execution_id"))
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        object.__setattr__(self, "coordinator_id", _execution_label(self.coordinator_id, "coordinator_id"))
        object.__setattr__(self, "executor_id", _execution_label(self.executor_id, "executor_id"))
        object.__setattr__(self, "executor_kind", _enum(self.executor_kind, ExecutorKind, "executor_kind"))
        object.__setattr__(self, "wave_id", _execution_label(self.wave_id, "wave_id"))
        claimed_at = _required_string(self.claimed_at, "claimed_at")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", claimed_at):
            raise ValueError("claimed_at must be an RFC 3339 UTC timestamp with second precision")
        object.__setattr__(self, "claimed_at", claimed_at)
        for field_name in (
            "source_fingerprint", "input_snapshot_fingerprint",
            "task_packet_fingerprint", "context_fingerprint",
        ):
            object.__setattr__(self, field_name, _fingerprint(getattr(self, field_name), field_name))
        receipt_id = _optional_string(self.page_probe_receipt_id, "page_probe_receipt_id")
        if receipt_id is not None:
            receipt_id = _identifier(receipt_id, "page_probe_receipt_id")
            if re.fullmatch(r"PPR-[0-9a-f]{24}", receipt_id) is None:
                raise ValueError("page_probe_receipt_id must be a canonical PPR identifier")
        object.__setattr__(self, "page_probe_receipt_id", receipt_id)
        receipt_fingerprint = self.page_probe_receipt_fingerprint
        if receipt_fingerprint is not None:
            receipt_fingerprint = _fingerprint(
                receipt_fingerprint, "page_probe_receipt_fingerprint"
            )
        object.__setattr__(self, "page_probe_receipt_fingerprint", receipt_fingerprint)
        tools = _string_tuple(
            self.approved_page_mcp_tools,
            "approved_page_mcp_tools",
        )
        if any(MCP_TOOL_RE.fullmatch(tool) is None for tool in tools):
            raise ValueError("approved_page_mcp_tools must contain exact canonical MCP tools")
        if tools != tuple(sorted(tools)):
            raise ValueError("approved_page_mcp_tools must be deterministically sorted")
        if (receipt_id is None) != (receipt_fingerprint is None) or bool(receipt_id) != bool(tools):
            raise ValueError(
                "page probe receipt id, fingerprint and approved MCP tools must be all present or all absent"
            )
        object.__setattr__(self, "approved_page_mcp_tools", tools)

    @classmethod
    def from_dict(cls, value: object) -> "AgentClaim":
        data = _require_mapping(value, "AgentClaim")
        _strict_keys(data, cls.FIELDS, "AgentClaim")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "coordinator_id": self.coordinator_id,
            "executor_id": self.executor_id,
            "executor_kind": self.executor_kind.value,
            "wave_id": self.wave_id,
            "claimed_at": self.claimed_at,
            "source_fingerprint": self.source_fingerprint,
            "input_snapshot_fingerprint": self.input_snapshot_fingerprint,
            "task_packet_fingerprint": self.task_packet_fingerprint,
            "context_fingerprint": self.context_fingerprint,
            "page_probe_receipt_id": self.page_probe_receipt_id,
            "page_probe_receipt_fingerprint": self.page_probe_receipt_fingerprint,
            "approved_page_mcp_tools": list(self.approved_page_mcp_tools),
        }


@dataclass(frozen=True, slots=True)
class PageProbeReceipt:
    schema_version: str
    receipt_id: str
    receipt_fingerprint: str
    run_id: str
    batch_id: str
    task_id: str
    execution_id: str
    coordinator_id: str
    source_fingerprint: str
    committed_at: str
    probe_session_sha256: str
    probe_transcript_sha256: str
    mcp_server: str
    approved_mcp_tools: tuple[str, ...]
    records: tuple[Mapping[str, Any], ...]
    evidence: tuple[Mapping[str, Any], ...]

    FIELDS: ClassVar[set[str]] = {
        "schema_version", "receipt_id", "receipt_fingerprint", "run_id", "batch_id",
        "task_id", "execution_id", "coordinator_id", "source_fingerprint",
        "committed_at", "probe_session_sha256", "probe_transcript_sha256",
        "mcp_server", "approved_mcp_tools", "records", "evidence",
    }
    RECORD_FIELDS: ClassVar[set[str]] = {
        "record_id", "sequence", "recorded_at", "tool_name", "operation_kind", "operation_name",
        "tool_input_sha256", "tool_response_sha256", "call_content_sha256",
        "response_nonempty", "response_error",
    }
    EVIDENCE_FIELDS: ClassVar[set[str]] = {
        "path", "sha256", "bytes", "sidecar_path", "sidecar_sha256",
    }

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _schema_version(self.schema_version))
        receipt_id = _identifier(self.receipt_id, "receipt_id")
        if re.fullmatch(r"PPR-[0-9a-f]{24}", receipt_id) is None:
            raise ValueError("receipt_id must be a canonical PPR identifier")
        object.__setattr__(self, "receipt_id", receipt_id)
        object.__setattr__(
            self, "receipt_fingerprint", _fingerprint(self.receipt_fingerprint, "receipt_fingerprint")
        )
        object.__setattr__(self, "run_id", _scope_identifier(self.run_id, "run_id"))
        object.__setattr__(self, "batch_id", _scope_identifier(self.batch_id, "batch_id"))
        object.__setattr__(self, "task_id", _identifier(self.task_id, "task_id"))
        object.__setattr__(self, "execution_id", _identifier(self.execution_id, "execution_id"))
        object.__setattr__(
            self, "coordinator_id", _execution_label(self.coordinator_id, "coordinator_id")
        )
        object.__setattr__(self, "source_fingerprint", _fingerprint(self.source_fingerprint))
        committed_at = _required_string(self.committed_at, "committed_at")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", committed_at):
            raise ValueError("committed_at must be an RFC 3339 UTC timestamp with second precision")
        object.__setattr__(self, "committed_at", committed_at)
        for name in ("probe_session_sha256", "probe_transcript_sha256"):
            object.__setattr__(self, name, _fingerprint(getattr(self, name), name))
        server = _required_string(self.mcp_server, "mcp_server")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", server):
            raise ValueError("mcp_server must be one exact canonical MCP server namespace")
        object.__setattr__(self, "mcp_server", server)
        tools = _string_tuple(
            self.approved_mcp_tools, "approved_mcp_tools", allow_empty=False
        )
        if tools != tuple(sorted(tools)):
            raise ValueError("approved_mcp_tools must be deterministically sorted")
        for tool in tools:
            matched = MCP_TOOL_RE.fullmatch(tool)
            if matched is None or matched.group(1) != server:
                raise ValueError("approved_mcp_tools must be exact tools from mcp_server")
        object.__setattr__(self, "approved_mcp_tools", tools)

        if not isinstance(self.records, (list, tuple)) or len(self.records) < 3:
            raise ValueError("page probe receipt requires at least three ordered records")
        normalized_records: list[Mapping[str, Any]] = []
        record_ids: set[str] = set()
        content_ids: set[str] = set()
        previous_sequence = 0
        seen_tools: set[str] = set()
        for index, raw in enumerate(self.records):
            record = _require_mapping(raw, f"records[{index}]")
            _strict_keys(record, self.RECORD_FIELDS, f"records[{index}]")
            normalized = dict(record)
            for field_name in (
                "record_id", "tool_input_sha256", "tool_response_sha256",
                "call_content_sha256",
            ):
                normalized[field_name] = _fingerprint(
                    normalized[field_name], f"records[{index}].{field_name}"
                )
            if normalized["record_id"] in record_ids:
                raise ValueError("page probe receipt cannot reuse a record_id")
            if normalized["call_content_sha256"] in content_ids:
                raise ValueError("page probe receipt cannot replay identical call content")
            record_ids.add(normalized["record_id"])
            content_ids.add(normalized["call_content_sha256"])
            sequence = normalized.get("sequence")
            if type(sequence) is not int or sequence <= previous_sequence:
                raise ValueError("page probe receipt record sequences must strictly increase")
            previous_sequence = sequence
            recorded_at = normalized.get("recorded_at")
            if not isinstance(recorded_at, str) or re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
                recorded_at,
            ) is None:
                raise ValueError("page probe receipt record recorded_at is invalid")
            tool = _required_string(normalized.get("tool_name"), f"records[{index}].tool_name")
            if tool not in tools:
                raise ValueError("page probe receipt record tool is outside approved_mcp_tools")
            seen_tools.add(tool)
            operation_kind = normalized.get("operation_kind")
            operation_name = normalized.get("operation_name")
            if operation_kind not in {"read", "mutation"}:
                raise ValueError("page probe receipt records must have a known read/mutation operation")
            if operation_name not in {
                "read", "click", "select", "input", "toggle", "expand", "navigate",
                "other_mutation",
            }:
                raise ValueError("page probe receipt record operation_name is invalid")
            if (operation_kind == "read") != (operation_name == "read"):
                raise ValueError(
                    "page probe receipt operation_kind/name must consistently describe read or mutation"
                )
            if normalized.get("response_nonempty") is not True or normalized.get("response_error") is not False:
                raise ValueError("page probe receipt records require non-empty successful responses")
            normalized_records.append(MappingProxyType(normalized))
        if seen_tools != set(tools):
            raise ValueError("every approved MCP tool requires a selected successful record")
        valid_transition = any(
            normalized_records[before]["operation_kind"] == "read"
            and normalized_records[mutation]["operation_kind"] == "mutation"
            and normalized_records[after]["operation_kind"] == "read"
            and normalized_records[before]["tool_response_sha256"]
            != normalized_records[after]["tool_response_sha256"]
            for before in range(len(normalized_records))
            for mutation in range(before + 1, len(normalized_records))
            for after in range(mutation + 1, len(normalized_records))
        )
        if not valid_transition:
            raise ValueError(
                "page probe receipt requires ordered pre-read -> mutation -> changed post-read"
            )
        object.__setattr__(self, "records", tuple(normalized_records))

        if not isinstance(self.evidence, (list, tuple)) or not self.evidence:
            raise ValueError("page probe receipt requires non-empty hash-bound evidence")
        normalized_evidence: list[Mapping[str, Any]] = []
        evidence_paths: set[str] = set()
        for index, raw in enumerate(self.evidence):
            evidence = _require_mapping(raw, f"evidence[{index}]")
            _strict_keys(evidence, self.EVIDENCE_FIELDS, f"evidence[{index}]")
            normalized = dict(evidence)
            normalized["path"] = _relative_posix_path(
                normalized.get("path"), f"evidence[{index}].path"
            )
            evidence_prefix = f"artifacts/page-probe-evidence/{self.execution_id}/"
            if not normalized["path"].startswith(evidence_prefix):
                raise ValueError(
                    "page probe evidence must stay under its dedicated execution prefix"
                )
            if normalized["path"] in evidence_paths:
                raise ValueError("page probe evidence paths must be unique")
            evidence_paths.add(normalized["path"])
            normalized["sha256"] = _fingerprint(
                normalized.get("sha256"), f"evidence[{index}].sha256"
            )
            if type(normalized.get("bytes")) is not int or normalized["bytes"] < 1:
                raise ValueError("page probe evidence must be non-empty")
            sidecar_path = normalized.get("sidecar_path")
            sidecar_sha = normalized.get("sidecar_sha256")
            if sidecar_path is None and sidecar_sha is not None:
                raise ValueError("page probe evidence sidecar path/hash must be both present or absent")
            if sidecar_path is not None:
                normalized["sidecar_path"] = _relative_posix_path(
                    sidecar_path, f"evidence[{index}].sidecar_path"
                )
                if normalized["sidecar_path"] != normalized["path"] + ".sensitive-audit.json":
                    raise ValueError(
                        "page probe evidence sidecar must be the adjacent hash-audit file"
                    )
                normalized["sidecar_sha256"] = _fingerprint(
                    sidecar_sha, f"evidence[{index}].sidecar_sha256"
                )
            normalized_evidence.append(MappingProxyType(normalized))
        object.__setattr__(self, "evidence", tuple(normalized_evidence))

        content = self.content_dict()
        expected = canonical_fingerprint(content)
        if self.receipt_fingerprint != expected:
            raise ValueError("page probe receipt_fingerprint does not match canonical content")
        if self.receipt_id != f"PPR-{expected[:24]}":
            raise ValueError("page probe receipt_id does not match receipt_fingerprint")

    def content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "coordinator_id": self.coordinator_id,
            "source_fingerprint": self.source_fingerprint,
            "committed_at": self.committed_at,
            "probe_session_sha256": self.probe_session_sha256,
            "probe_transcript_sha256": self.probe_transcript_sha256,
            "mcp_server": self.mcp_server,
            "approved_mcp_tools": list(self.approved_mcp_tools),
            "records": [dict(item) for item in self.records],
            "evidence": [dict(item) for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, value: object) -> "PageProbeReceipt":
        data = _require_mapping(value, "PageProbeReceipt")
        _strict_keys(data, cls.FIELDS, "PageProbeReceipt")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.content_dict(),
            "receipt_id": self.receipt_id,
            "receipt_fingerprint": self.receipt_fingerprint,
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
        if self.status is TaskStatus.NEEDS_REWORK and self.error_message is not None:
            raise ValueError("NEEDS_REWORK results must use rework_requests and must not contain error_message")
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
    "AgentClaim",
    "AgentResult",
    "AgentRole",
    "AgentTask",
    "ExecutorKind",
    "MCP_TOOL_RE",
    "PageProbeReceipt",
    "ReworkReason",
    "ReworkRequest",
    "ReworkTarget",
    "RunConfig",
    "SCHEMA_VERSION",
    "TaskStatus",
    "TraceabilityRecord",
    "canonical_fingerprint",
    "role_contract_relative_paths",
]
