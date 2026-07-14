# -*- coding: utf-8 -*-
"""Deterministic multi-agent orchestration engine for one leaf batch run.

The engine never calls a model.  It creates strict task packets, validates and
promotes externally produced AgentResult payloads, reuses the existing batch
gates, merges Case Worker output deterministically, and requires an independent
review before delivery.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

from ..batch import (
    SHEET_DATA_FILES,
    batch_scope_data,
    generation_session_data,
    generation_session_is_current,
    generation_catalog_paths,
    generation_source_fingerprint,
    prepare_function_case_generation,
    record_no_model_uncertainty,
    resolved_evidence_file,
    validate_batch_artifacts,
)
from ..contracts.function_cases import FUNCTION_CASE_REQUIRED_FIELDS, MAX_FUNCTION_CASES_PER_PART
from ..contracts.sheet_data import SHEET_DATA_HEADERS
from ..io_utils import atomic_copy, atomic_write_json, exclusive_process_lock, rollback_files_on_error
from ..pipeline import derive_pipeline_status
from ..sensitive_data import (
    SensitiveDataError,
    assert_no_sensitive_artifact,
    assert_no_sensitive_text_file,
    binary_evidence_audit_path,
)
from ..validation_cache import fingerprint
from ..validators.function_cases import validate_sheet_data_file
from .case_merge import (
    aggregate_case_workers,
    plan_groups,
    sync_discovery_status,
    traceability_expectations,
)
from .contracts import (
    AgentClaim,
    AgentResult,
    AgentRole,
    AgentTask,
    ExecutorKind,
    PageProbeReceipt,
    ReworkRequest,
    ReworkTarget,
    RunConfig,
    TaskStatus,
    canonical_fingerprint,
    role_contract_relative_paths,
)
from .event_store import EventStore
from .execution_binding import (
    ExecutionBindingError,
    execution_binding_path,
    validate_execution_binding,
)
from .page_probe import (
    PageProbeError,
    create_page_probe_receipt,
    load_page_probe_receipt,
    page_probe_event_registry,
    reserve_project_record_consumption,
    receipt_event_payload,
    receipt_path as page_probe_receipt_path,
    validate_project_record_consumption,
)
from .review import (
    REQUIRED_REVIEW_CHECKS,
    review_evidence_paths,
    review_source_fingerprint,
    validate_review_artifacts,
)
from .state_machine import OrchestrationStateMachine, PHASE_ORDER, Phase
from .workspace import PromotionReceipt, WorkspaceError, WorkspaceManager


ARCHITECTURE = "multi-agent-final"
MANIFEST_VERSION = 1
MANIFEST_NAME = "orchestration/run-manifest.json"
CONFIG_NAME = "orchestration/config.json"
STATE_NAME = "orchestration/state.json"
EVENTS_NAME = "orchestration/events.jsonl"
TASK_STATUSES = {
    "PENDING", "CLAIMED", "SUCCEEDED", "FAILED", "NEEDS_REWORK", "EXTERNAL_BLOCKED", "INVALIDATED"
}
_PROMOTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
AGENT_DISPATCH_RETRY_LIMIT = 1
FALLBACK_AUTHORIZATION_VERSION = "1.0.0"

DFX_MATRIX = {
    "DFT功能": ["正向流程", "边界值", "异常输入", "逆向操作"],
    "DFP性能": ["响应时间", "并发处理", "大数据量", "资源监控"],
    "DFI接口": ["参数校验", "协议兼容", "错误码", "超时重试"],
    "DFC兼容": ["浏览器", "操作系统", "屏幕适配", "数据格式"],
    "DFS安全": ["身份认证", "权限控制", "数据脱敏", "注入防护"],
    "DFR可靠": ["故障恢复", "数据一致", "幂等性", "断点续传"],
    "DFM维护": ["配置热更新", "灰度发布", "回滚机制", "日志追踪"],
    "DFU可用": ["操作便捷", "错误提示", "用户引导", "操作反馈"],
    "DFD部署": ["全新安装", "版本升级", "卸载回滚", "配置迁移"],
    "DFO运维": ["监控指标", "告警配置", "故障自愈", "容量规划"],
    "DFB业务": ["业务流程", "数据准确", "端到端", "报表统计"],
    "DFX极端": ["压力极限", "破坏性", "资源耗尽", "并发极限"],
}

ROLE_PHASE = {
    AgentRole.DISCOVERY: Phase.DISCOVERY,
    AgentRole.PLAN_DFX: Phase.PLAN,
    AgentRole.RISK_ARBITER: Phase.RISK,
    AgentRole.CASE_WORKER: Phase.CASES,
    AgentRole.REVIEWER: Phase.REVIEW,
}


class OrchestrationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _claim_event_payload(claim: AgentClaim) -> dict[str, object]:
    return {"claim": claim.to_dict()}


def _quiet(callable_, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return callable_(*args, **kwargs)


def _project_root(run_dir: Path) -> Path:
    root = run_dir.resolve()
    if len(root.parents) < 4:
        raise OrchestrationError(f"run-dir is not under docs/test-assets/batch-runs: {root}")
    return root.parents[3]


def _relative_to_project(run_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(_project_root(run_dir)).as_posix()


def _manifest_path(run_dir: Path) -> Path:
    return run_dir.resolve() / MANIFEST_NAME


def orchestration_exists(run_dir: Path | str) -> bool:
    return _manifest_path(Path(run_dir)).is_file()


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"invalid {label}: {path}: {exc}") from exc


def _load_manifest(run_dir: Path) -> dict[str, Any]:
    value = _load_json(_manifest_path(run_dir), "orchestration run manifest")
    if not isinstance(value, dict):
        raise OrchestrationError("orchestration/run-manifest.json must be an object")
    required = {
        "schema_version", "architecture", "agent_mode", "run_id", "batch_id", "created_at", "updated_at",
        "config_path", "state_machine", "tasks", "case_task_order",
    }
    if set(value) != required:
        raise OrchestrationError(
            f"run-manifest fields are invalid; missing={sorted(required-set(value))}, unknown={sorted(set(value)-required)}"
        )
    if value.get("schema_version") != MANIFEST_VERSION or value.get("architecture") != ARCHITECTURE:
        raise OrchestrationError("run-manifest architecture/schema is not supported")
    if value.get("agent_mode") != "required" or not isinstance(value.get("tasks"), dict):
        raise OrchestrationError("run-manifest must use required mode and an object task graph")
    for task_id, entry in value["tasks"].items():
        if not isinstance(entry, dict) or entry.get("status") not in TASK_STATUSES:
            raise OrchestrationError(f"run-manifest task {task_id!r} has an invalid status")
        raw_claim = entry.get("claim")
        if raw_claim is not None:
            try:
                claim = AgentClaim.from_dict(raw_claim)
            except (TypeError, ValueError) as exc:
                raise OrchestrationError(f"run-manifest task {task_id!r} has an invalid claim: {exc}") from exc
            if claim.task_id != task_id:
                raise OrchestrationError(f"run-manifest task {task_id!r} claim task_id differs")
    OrchestrationStateMachine.from_dict(value["state_machine"])
    return value


def _save_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = _now()
    atomic_write_json(_manifest_path(run_dir), manifest)
    atomic_write_json(run_dir / STATE_NAME, manifest["state_machine"])


def _machine(manifest: Mapping[str, Any]) -> OrchestrationStateMachine:
    return OrchestrationStateMachine.from_dict(manifest["state_machine"])


def _save_machine(run_dir: Path, manifest: dict[str, Any], machine: OrchestrationStateMachine) -> None:
    manifest["state_machine"] = machine.to_dict()
    _save_manifest(run_dir, manifest)


def _event_store(run_dir: Path) -> EventStore:
    return EventStore(run_dir / EVENTS_NAME)


def _delivery_completion_event_payloads(
    manifest: Mapping[str, Any],
) -> tuple[tuple[str, dict[str, object], str], tuple[str, dict[str, object], str]]:
    """Reconstruct the two terminal delivery events from the manifest authority.

    ``complete_delivery_orchestration`` persists the final state checkpoint
    before appending events.  A hard stop in that interval must therefore be
    recoverable without inventing a new transition or a second logical event.
    """

    state = manifest.get("state_machine")
    if not isinstance(state, Mapping):
        raise OrchestrationError("run-manifest has no state-machine authority")
    revision = state.get("revision")
    if (
        state.get("state") != "COMPLETE"
        or state.get("validated_phases") != [phase.value for phase in PHASE_ORDER]
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 2
    ):
        raise OrchestrationError(
            "cannot reconstruct delivery completion events from a non-terminal manifest"
        )
    phase_payload: dict[str, object] = {
        "previous_state": "DELIVERY_RUNNING",
        "state": "DELIVERY_VALIDATED",
        "revision": revision - 1,
        "phase": Phase.DELIVERY.value,
        "invalidated_phases": [],
        "reason": "",
        "closed_rework_request_ids": [],
    }
    completed_payload: dict[str, object] = {
        "previous_state": "DELIVERY_VALIDATED",
        "state": "COMPLETE",
        "revision": revision,
        "phase": Phase.DELIVERY.value,
        "invalidated_phases": [],
        "reason": "",
    }

    def stable_id(event_type: str, event_revision: int) -> str:
        return "delivery-" + canonical_fingerprint(
            {
                "run_id": manifest.get("run_id"),
                "batch_id": manifest.get("batch_id"),
                "event_type": event_type,
                "revision": event_revision,
            }
        )

    return (
        (
            "PHASE_VALIDATED",
            phase_payload,
            stable_id("PHASE_VALIDATED", revision - 1),
        ),
        (
            "RUN_COMPLETED",
            completed_payload,
            stable_id("RUN_COMPLETED", revision),
        ),
    )


def _ensure_delivery_completion_events(
    manifest: Mapping[str, Any], events: EventStore
) -> None:
    """Idempotently close semantic audit gaps after a terminal hard stop."""

    state = manifest.get("state_machine")
    if not isinstance(state, Mapping) or state.get("state") != "COMPLETE":
        return
    for event_type, payload, event_id in _delivery_completion_event_payloads(manifest):
        rows = events.read_events()
        semantic_matches = [
            row
            for row in rows
            if row.get("event_type") == event_type and row.get("payload") == payload
        ]
        if len(semantic_matches) > 1:
            raise OrchestrationError(
                f"delivery completion has duplicate semantic {event_type} events"
            )
        if semantic_matches:
            continue
        conflicting = next(
            (row for row in rows if row.get("event_id") == event_id), None
        )
        if conflicting is not None:
            raise OrchestrationError(
                f"stable delivery event_id is already bound to another event: {event_id}"
            )
        events.append(event_type, payload, event_id=event_id)


def _config(run_dir: Path) -> RunConfig:
    try:
        return RunConfig.from_dict(_load_json(run_dir / CONFIG_NAME, "orchestration config"))
    except (TypeError, ValueError) as exc:
        raise OrchestrationError(f"invalid orchestration config: {exc}") from exc


def _generation_task_fingerprint(run_dir: Path) -> str:
    session = generation_session_data(run_dir) or {}
    identity = {
        "generation_session_id": session.get("generation_session_id"),
        "source_fingerprint": session.get("source_fingerprint"),
        "catalog_source_fingerprint": session.get("catalog_source_fingerprint"),
    }
    if any(not isinstance(value, str) or not value for value in identity.values()):
        return ""
    return canonical_fingerprint(identity)


def _page_probe_link(receipt: PageProbeReceipt, status: str) -> dict[str, Any]:
    return {
        "receipt_id": receipt.receipt_id,
        "receipt_path": page_probe_receipt_path(
            Path("."), receipt.receipt_id
        ).as_posix().removeprefix("./"),
        "receipt_fingerprint": receipt.receipt_fingerprint,
        "execution_id": receipt.execution_id,
        "coordinator_id": receipt.coordinator_id,
        "source_fingerprint": receipt.source_fingerprint,
        "approved_page_mcp_tools": list(receipt.approved_mcp_tools),
        "status": status,
    }


_PAGE_PROBE_LINK_FIELDS = {
    "receipt_id", "receipt_path", "receipt_fingerprint", "execution_id",
    "coordinator_id", "source_fingerprint", "approved_page_mcp_tools", "status",
}
_PAGE_PROBE_HISTORY_FIELDS = _PAGE_PROBE_LINK_FIELDS | {
    "released_at", "release_reason",
}


def _page_probe_projection_status(task_status: object) -> str:
    # COMMITTED means preflight is prepared but no execution has claimed it.
    # Every post-claim status keeps the receipt bound/ACTIVE until an explicit
    # no-side-effects release tombstones it, including failure/rework states.
    return "COMMITTED" if task_status == "PENDING" else "ACTIVE"


def _recover_page_probe_receipts(
    run_dir: Path,
    manifest: dict[str, Any],
    events: EventStore,
) -> None:
    """Finish interrupted receipt commits and repair their manifest/event projection."""

    try:
        event_rows = events.read_events()
        registry = page_probe_event_registry(event_rows)
    except PageProbeError as exc:
        raise OrchestrationError(f"invalid page probe event registry: {exc}") from exc
    manifest_changed = False
    for entry in manifest.get("tasks", {}).values():
        if "page_probe_receipt" not in entry:
            entry["page_probe_receipt"] = None
            manifest_changed = True
        if "page_probe_history" not in entry:
            entry["page_probe_history"] = []
            manifest_changed = True

    # Manifest release history is the crash-safe intent.  Materialize missing
    # tombstone events before projecting active receipts, so one recovery pass
    # can never resurrect a released receipt as COMMITTED.
    recovered_tombstones: dict[str, PageProbeReceipt] = {}
    seen_history_receipts: dict[str, str] = {}
    task_entries = manifest.get("tasks", {})
    if not isinstance(task_entries, Mapping):
        raise OrchestrationError("manifest tasks must be an object")
    for entry_task_id, entry in task_entries.items():
        if not isinstance(entry, dict):
            raise OrchestrationError(f"manifest task entry {entry_task_id!r} must be an object")
        history = entry.get("page_probe_history")
        if not isinstance(history, list):
            raise OrchestrationError("page_probe_history must be an array")
        for item in history:
            if (
                not isinstance(item, Mapping)
                or set(item) not in (_PAGE_PROBE_LINK_FIELDS, _PAGE_PROBE_HISTORY_FIELDS)
                or item.get("status") != "TOMBSTONED"
                or (("released_at" in item) != ("release_reason" in item))
            ):
                raise OrchestrationError(
                    "page_probe_history may contain only immutable TOMBSTONED links"
                )
            if "released_at" in item and (
                not isinstance(item.get("released_at"), str)
                or re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                    str(item.get("released_at")),
                ) is None
                or not isinstance(item.get("release_reason"), str)
                or not str(item.get("release_reason")).strip()
                or item.get("release_reason") != str(item.get("release_reason")).strip()
            ):
                raise OrchestrationError("page probe history release metadata is invalid")
            receipt_id = item.get("receipt_id")
            if not isinstance(receipt_id, str):
                raise OrchestrationError("page probe history receipt_id is invalid")
            prior_owner = seen_history_receipts.get(receipt_id)
            if prior_owner is not None:
                raise OrchestrationError(
                    "page_probe_history duplicates receipt "
                    f"{receipt_id} across task entries {prior_owner!r} and {entry_task_id!r}"
                )
            seen_history_receipts[receipt_id] = str(entry_task_id)
            state = registry.get(str(receipt_id))
            if state is None:
                raise OrchestrationError("page probe history has no reservation event")
            receipt = state["receipt"]
            if receipt.task_id != entry_task_id:
                raise OrchestrationError(
                    "page probe history receipt belongs to another task entry"
                )
            expected_tombstone = _page_probe_link(receipt, "TOMBSTONED")
            if any(item.get(key) != value for key, value in expected_tombstone.items()):
                raise OrchestrationError("page probe history conflicts with immutable tombstone")
            if state["tombstoned_sequence"] is None:
                recovered_tombstones[receipt.receipt_id] = receipt

    # Validate every pre-existing current projection before writing any audit
    # event.  A stray/foreign/incomplete link is tampering, not recoverable
    # state, and must leave the append-only ledger untouched.
    for entry_task_id, entry in task_entries.items():
        current = entry.get("page_probe_receipt")
        if current is None:
            continue
        if not isinstance(current, Mapping) or set(current) != _PAGE_PROBE_LINK_FIELDS:
            raise OrchestrationError("manifest page probe projection has an invalid field set")
        receipt_id = current.get("receipt_id")
        state = registry.get(str(receipt_id))
        if state is None:
            raise OrchestrationError("manifest page probe projection has no reservation event")
        receipt = state["receipt"]
        if receipt.task_id != entry_task_id:
            raise OrchestrationError("manifest page probe projection belongs to another task")
        if state["tombstoned_sequence"] is not None:
            raise OrchestrationError("manifest page probe projection references a tombstoned receipt")
        expected_status = _page_probe_projection_status(entry.get("status"))
        if dict(current) != _page_probe_link(receipt, expected_status):
            raise OrchestrationError("manifest page probe projection conflicts with its receipt")

    for receipt in recovered_tombstones.values():
        events.append(
            "AUDIT_PAGE_PROBE_TOMBSTONED",
            {
                **receipt_event_payload(receipt),
                "reason": "released receipt lacked durable tombstone event",
            },
            task_id=receipt.task_id,
        )
    if recovered_tombstones:
        try:
            registry = page_probe_event_registry(events.read_events())
        except PageProbeError as exc:
            raise OrchestrationError(f"invalid recovered page probe registry: {exc}") from exc

    recovered_commits: list[PageProbeReceipt] = []
    for state in registry.values():
        receipt: PageProbeReceipt = state["receipt"]
        entry = manifest.get("tasks", {}).get(receipt.task_id)
        if not isinstance(entry, dict):
            raise OrchestrationError(
                f"page probe receipt references missing task {receipt.task_id}"
            )
        task = AgentTask.from_dict(entry.get("task"))
        if task.agent_role is not AgentRole.DISCOVERY:
            raise OrchestrationError("page probe receipt may bind only a Discovery task")
        if task.source_fingerprint != receipt.source_fingerprint:
            raise OrchestrationError("page probe receipt source no longer matches its task")
        try:
            validate_project_record_consumption(_project_root(run_dir), run_dir, receipt)
        except PageProbeError as exc:
            raise OrchestrationError(
                f"page probe project-level consumption binding is stale: {exc}"
            ) from exc
        tombstoned = state["tombstoned_sequence"] is not None
        path = page_probe_receipt_path(run_dir, receipt.receipt_id)
        if tombstoned:
            current = entry.get("page_probe_receipt")
            if isinstance(current, Mapping) and current.get("receipt_id") == receipt.receipt_id:
                entry["page_probe_receipt"] = None
                manifest_changed = True
            history = entry.get("page_probe_history")
            if not isinstance(history, list):
                raise OrchestrationError("page_probe_history must be an array")
            matching_history = [
                item
                for item in history
                if isinstance(item, Mapping) and item.get("receipt_id") == receipt.receipt_id
            ]
            expected_tombstone = _page_probe_link(receipt, "TOMBSTONED")
            if len(matching_history) > 1:
                raise OrchestrationError("page probe history duplicates a receipt tombstone")
            if matching_history and any(
                matching_history[0].get(key) != value
                for key, value in expected_tombstone.items()
            ):
                raise OrchestrationError("page probe history conflicts with immutable tombstone")
            if not matching_history:
                history.append(expected_tombstone)
                manifest_changed = True
            continue

        if path.exists():
            try:
                loaded = load_page_probe_receipt(
                    run_dir,
                    receipt.receipt_id,
                    expected_fingerprint=receipt.receipt_fingerprint,
                )
            except PageProbeError as exc:
                raise OrchestrationError(f"stale page probe receipt: {exc}") from exc
            if loaded != receipt:
                raise OrchestrationError("page probe receipt file conflicts with reservation event")
        else:
            atomic_write_json(path, receipt.to_dict())
        expected_link = _page_probe_link(
            receipt,
            _page_probe_projection_status(entry.get("status")),
        )
        current = entry.get("page_probe_receipt")
        if current is None:
            entry["page_probe_receipt"] = expected_link
            manifest_changed = True
        elif not isinstance(current, Mapping) or any(
            current.get(key) != value
            for key, value in expected_link.items()
            if key != "status"
        ):
            raise OrchestrationError("manifest page probe projection conflicts with receipt")
        elif current.get("status") != expected_link["status"]:
            entry["page_probe_receipt"] = expected_link
            manifest_changed = True
        if state["committed_sequence"] is None:
            recovered_commits.append(receipt)

    if manifest_changed:
        _save_manifest(run_dir, manifest)
    for receipt in recovered_commits:
        events.append(
            "AUDIT_PAGE_PROBE_COMMITTED",
            {
                **receipt_event_payload(receipt),
                "reason": "reserved receipt lacked durable commit projection",
            },
            task_id=receipt.task_id,
        )

    # Final reverse check: every surviving current link must project exactly
    # one non-tombstoned registry receipt owned by this task entry.
    try:
        final_registry = page_probe_event_registry(events.read_events())
    except PageProbeError as exc:
        raise OrchestrationError(
            f"invalid final page probe event registry: {exc}"
        ) from exc
    for entry_task_id, entry in task_entries.items():
        current = entry.get("page_probe_receipt")
        if current is None:
            continue
        state = final_registry.get(str(current.get("receipt_id")))
        if state is None or state["tombstoned_sequence"] is not None:
            raise OrchestrationError("manifest page probe projection is not active in the event registry")
        receipt = state["receipt"]
        expected_status = _page_probe_projection_status(entry.get("status"))
        if receipt.task_id != entry_task_id or dict(current) != _page_probe_link(
            receipt, expected_status
        ):
            raise OrchestrationError("manifest page probe projection failed final reverse validation")


def _initialize_orchestration_unlocked(
    run_dir: Path | str,
    *,
    max_case_workers: int = 3,
    max_rework_attempts: int = 5,
) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    if not root.is_dir():
        raise OrchestrationError(f"batch run directory does not exist: {root}")
    WorkspaceManager(root).initialize()
    for relative in [
        "orchestration/tasks", "orchestration/results", "orchestration/inputs",
        "orchestration/rework-requests", "orchestration/checkpoints",
        "orchestration/page-probe-receipts",
    ]:
        (root / relative).mkdir(parents=True, exist_ok=True)
    existing = _manifest_path(root)
    if existing.exists():
        manifest = _load_manifest(root)
        _config(root)
        events = _event_store(root)
        events.verify()
        _recover_page_probe_receipts(root, manifest, events)
        _reconcile_durable_promotions(root, manifest, events)
        _ensure_delivery_completion_events(manifest, events)
        event_rows = events.read_events()
        state_path = root / STATE_NAME
        state_value = _load_json(state_path, "orchestration state") if state_path.is_file() else None
        if state_value != manifest["state_machine"]:
            atomic_write_json(state_path, manifest["state_machine"])
            events.append(
                "STATE_CHECKPOINT_REPAIRED",
                {
                    "authority": MANIFEST_NAME,
                    "state": manifest["state_machine"].get("state"),
                    "revision": manifest["state_machine"].get("revision"),
                },
            )
            event_rows = events.read_events()
        event_revision = max(
            (
                int(event.get("payload", {}).get("revision", 0))
                for event in event_rows
                if isinstance(event.get("payload"), dict)
                and isinstance(event.get("payload", {}).get("revision", 0), int)
            ),
            default=0,
        )
        manifest_revision = int(manifest["state_machine"].get("revision", 0))
        if manifest_revision > event_revision:
            events.append(
                "AUDIT_STATE_RECOVERED",
                {
                    "revision": manifest_revision,
                    "state": manifest["state_machine"].get("state"),
                    "recovered_after_event_revision": event_revision,
                },
            )
            event_rows = events.read_events()
        created_task_ids = {
            str(event.get("task_id"))
            for event in event_rows
            if event.get("event_type") in {"TASK_CREATED", "AUDIT_TASK_RECOVERED"} and event.get("task_id")
        }
        for task_id, entry in manifest["tasks"].items():
            if task_id in created_task_ids:
                continue
            events.append(
                "AUDIT_TASK_RECOVERED",
                {
                    "status": entry.get("status"),
                    "result_path": entry.get("result_path"),
                    "reason": "manifest task had no durable TASK_CREATED event",
                },
                task_id=task_id,
            )
        event_rows = events.read_events()
        for task_id, entry in manifest["tasks"].items():
            raw_claim = entry.get("claim")
            if isinstance(raw_claim, dict):
                claim = AgentClaim.from_dict(raw_claim)
                complete_claim_events = []
                legacy_claim_events = []
                for event in event_rows:
                    if (
                        event.get("task_id") != task_id
                        or event.get("event_type") not in {
                            "TASK_CLAIMED", "TASK_EXECUTOR_DEGRADED", "AUDIT_CLAIM_RECOVERED"
                        }
                        or not isinstance(event.get("payload"), dict)
                    ):
                        continue
                    payload = event["payload"]
                    nested = payload.get("claim")
                    if isinstance(nested, dict):
                        try:
                            event_claim = AgentClaim.from_dict(nested)
                            if event_claim.execution_id == claim.execution_id:
                                complete_claim_events.append(event_claim)
                        except (TypeError, ValueError) as exc:
                            raise OrchestrationError(
                                f"task {task_id} has an invalid durable claim event: {exc}"
                            ) from exc
                    elif payload.get("execution_id") == claim.execution_id:
                        legacy_claim_events.append(payload)
                for payload in legacy_claim_events:
                    legacy_identity = {
                        key: payload.get(key)
                        for key in (
                            "execution_id", "coordinator_id", "executor_id", "executor_kind", "wave_id"
                        )
                        if payload.get(key) is not None
                    }
                    expected_identity = {
                        "execution_id": claim.execution_id,
                        "coordinator_id": claim.coordinator_id,
                        "executor_id": claim.executor_id,
                        "executor_kind": claim.executor_kind.value,
                        "wave_id": claim.wave_id,
                    }
                    if any(expected_identity[key] != value for key, value in legacy_identity.items()):
                        raise OrchestrationError(
                            f"task {task_id} manifest claim conflicts with its durable claim event"
                        )
                if complete_claim_events and any(item != claim for item in complete_claim_events):
                    if claim.executor_kind is not ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK:
                        raise OrchestrationError(
                            f"task {task_id} manifest claim conflicts with its durable full-claim event"
                        )
                    stable_fields = {
                        key: value
                        for key, value in claim.to_dict().items()
                        if key not in {"executor_id", "executor_kind"}
                    }
                    incompatible = [
                        item
                        for item in complete_claim_events
                        if item != claim
                        and (
                            item.executor_kind is not ExecutorKind.CODEBUDDY_SUBAGENT
                            or {
                                key: value
                                for key, value in item.to_dict().items()
                                if key not in {"executor_id", "executor_kind"}
                            }
                            != stable_fields
                        )
                    ]
                    if incompatible:
                        raise OrchestrationError(
                            f"task {task_id} fallback claim conflicts with its original durable claim"
                        )
                recovered_fallback_event = False
                if claim.executor_kind is ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK:
                    _validate_fallback_authorization(
                        AgentTask.from_dict(entry.get("task")), entry, claim
                    )
                    degradation_events = [
                        event
                        for event in event_rows
                        if event.get("task_id") == task_id
                        and event.get("event_type") == "TASK_EXECUTOR_DEGRADED"
                        and isinstance(event.get("payload"), dict)
                        and event["payload"].get("claim") == claim.to_dict()
                    ]
                    if len(degradation_events) > 1:
                        raise OrchestrationError(
                            f"task {task_id} has duplicate durable executor degradation events"
                        )
                    if not degradation_events:
                        original_claims = {
                            canonical_fingerprint(item.to_dict()): item
                            for item in complete_claim_events
                            if item.executor_kind is ExecutorKind.CODEBUDDY_SUBAGENT
                            and item != claim
                        }
                        if len(original_claims) != 1:
                            raise OrchestrationError(
                                f"task {task_id} fallback cannot recover its unique original Agent claim"
                            )
                        previous_claim = next(iter(original_claims.values()))
                        events.append(
                            "TASK_EXECUTOR_DEGRADED",
                            {
                                "previous_claim": previous_claim.to_dict(),
                                "claim": claim.to_dict(),
                                "fallback_authorization": dict(entry["fallback_authorization"]),
                                "recovered_after_manifest_commit": True,
                            },
                            task_id=task_id,
                        )
                        recovered_fallback_event = True
                if not any(item == claim for item in complete_claim_events) and not recovered_fallback_event:
                    events.append(
                        "AUDIT_CLAIM_RECOVERED",
                        {
                            **_claim_event_payload(claim),
                            "status": entry.get("status"),
                            "reason": "manifest claim had no complete durable claim event",
                        },
                        task_id=task_id,
                    )
            history = entry.get("claim_history")
            if not isinstance(history, list):
                continue
            for item in history:
                if not isinstance(item, dict) or not isinstance(item.get("claim"), dict):
                    continue
                released_claim = AgentClaim.from_dict(item["claim"])
                release_is_audited = any(
                    event.get("task_id") == task_id
                    and event.get("event_type") in {"TASK_CLAIM_RELEASED", "AUDIT_CLAIM_RELEASE_RECOVERED"}
                    and isinstance(event.get("payload"), dict)
                    and isinstance(event["payload"].get("claim"), dict)
                    and event["payload"]["claim"] == released_claim.to_dict()
                    for event in event_rows
                )
                if not release_is_audited:
                    events.append(
                        "AUDIT_CLAIM_RELEASE_RECOVERED",
                        {
                            **_claim_event_payload(released_claim),
                            "released_at": item.get("released_at"),
                            "reason": "manifest claim release had no durable TASK_CLAIM_RELEASED event",
                        },
                        task_id=task_id,
                    )
        event_rows = events.read_events()
        for task_id, entry in manifest["tasks"].items():
            if not entry.get("result_path"):
                continue
            result_fingerprint = str(entry.get("result_fingerprint") or "")
            succeeded = entry.get("status") == TaskStatus.SUCCEEDED.value
            result_is_audited = False
            if len(result_fingerprint) == 64:
                for event in event_rows:
                    payload = event.get("payload")
                    if (
                        event.get("task_id") != task_id
                        or not isinstance(payload, dict)
                        or payload.get("result_fingerprint") != result_fingerprint
                    ):
                        continue
                    if succeeded:
                        result_is_audited = event.get("event_type") == "TASK_SUCCEEDED" or (
                            event.get("event_type") == "AUDIT_RESULT_RECOVERED"
                            and payload.get("commit_proof") is True
                        )
                    else:
                        result_is_audited = event.get("event_type") in {
                            "TASK_RESULT_STORED", "EXTERNAL_BLOCKED", "RUN_FAILED",
                            "TASK_FAILED_RETRYABLE", "AUDIT_RESULT_RECOVERED",
                        }
                    if result_is_audited:
                        break
            if result_is_audited:
                continue
            commit_proof = False
            if succeeded:
                task = AgentTask.from_dict(entry.get("task"))
                validated = manifest["state_machine"].get("validated_phases", [])
                phase_validated = isinstance(validated, list) and task.phase.value in validated
                promotion_ids = entry.get("promotion_ids")
                promotion_committed = isinstance(promotion_ids, list) and all(
                    isinstance(
                        (receipt := _load_json(
                            _promotion_receipt_path(root, str(promotion_id)),
                            f"promotion {promotion_id} receipt",
                        )),
                        Mapping,
                    )
                    and receipt.get("status") == "FINALIZED"
                    for promotion_id in promotion_ids
                )
                try:
                    recovered_result = AgentResult.from_dict(
                        _load_json(root / str(entry["result_path"]), f"task {task_id} result")
                    )
                    gate_proven = (
                        recovered_result.status is TaskStatus.SUCCEEDED
                        and recovered_result.gate_summary.get(task.required_gate) is True
                    )
                except (TypeError, ValueError):
                    gate_proven = False
                commit_proof = phase_validated and promotion_committed and gate_proven
                if not commit_proof:
                    # A stored result alone is not proof that its phase/gate
                    # committed.  Leave it uncommitted so Formal Review fails closed.
                    continue
            events.append(
                "AUDIT_RESULT_RECOVERED",
                {
                    "status": entry.get("status"),
                    "result_path": entry.get("result_path"),
                    "result_fingerprint": result_fingerprint,
                    "commit_proof": commit_proof,
                    "reason": "manifest result had no durable result-state event",
                },
                task_id=task_id,
            )
        return manifest
    scope = batch_scope_data(root)
    if not isinstance(scope, dict):
        raise OrchestrationError("initialize orchestration only after init-batch-run created batch-scope.json")
    run_id = str(scope.get("run_id") or root.name).strip()
    batch_id = str(scope.get("batch_id") or "").strip()
    if not batch_id:
        raise OrchestrationError("batch-scope.json is missing batch_id")
    scope_fp = canonical_fingerprint(scope)
    config = RunConfig(
        schema_version="1.0.0",
        run_id=run_id,
        batch_id=batch_id,
        agent_mode="required",
        parallel_discovery=False,
        case_parallel_threshold=3,
        max_case_workers=max_case_workers,
        max_rework_attempts=max_rework_attempts,
        review_required=True,
        delivery_single_writer=True,
        source_fingerprint=scope_fp,
    )
    machine = OrchestrationStateMachine()
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_VERSION,
        "architecture": ARCHITECTURE,
        "agent_mode": "required",
        "run_id": run_id,
        "batch_id": batch_id,
        "created_at": _now(),
        "updated_at": _now(),
        "config_path": CONFIG_NAME,
        "state_machine": machine.to_dict(),
        "tasks": {},
        "case_task_order": [],
    }
    atomic_write_json(root / CONFIG_NAME, config.to_dict())
    _save_manifest(root, manifest)
    _event_store(root).append(
        "RUN_INITIALIZED",
        {"architecture": ARCHITECTURE, "agent_mode": "required", "config": config.to_dict()},
    )
    return manifest


def initialize_orchestration(
    run_dir: Path | str,
    *,
    max_case_workers: int = 3,
    max_rework_attempts: int = 5,
) -> dict[str, Any]:
    """Initialize or recover one run while owning its single-writer lock.

    Recovery is a mutating operation: it may repair checkpoints, append audit
    events, and roll back or finalize interrupted promotions.  Keeping it under
    the same lock as claim/submit/release prevents a read-side recovery pass
    from mistaking an in-flight promotion for a crashed transaction.
    """

    root = Path(run_dir).resolve()
    with exclusive_process_lock(root / "orchestration" / ".orchestrator.lock"):
        return _initialize_orchestration_unlocked(
            root,
            max_case_workers=max_case_workers,
            max_rework_attempts=max_rework_attempts,
        )


def _rule_sources(run_dir: Path, names: Sequence[str]) -> list[Path]:
    project = _project_root(run_dir)
    result = [project / "VERSION", project / ".codebuddy" / "rules" / "test-design-rule.md"]
    result.extend(project / "docs" / "test-design" / "rules" / name for name in names)
    return result


def _catalog_sources(run_dir: Path) -> list[Path]:
    return [path for path in generation_catalog_paths(run_dir) if path.is_file()]


def _role_contract_sources(run_dir: Path, role: AgentRole) -> list[Path]:
    """Return immutable output contracts that must travel with every task packet."""

    project = _project_root(run_dir)
    sources = [project / relative for relative in role_contract_relative_paths(role)]
    missing = [path for path in sources if not path.is_file()]
    if missing:
        raise OrchestrationError(
            f"missing frozen output contract(s) for {role.value}: "
            + ", ".join(str(path) for path in missing)
        )
    return [path.resolve() for path in sources]


def _ledger_evidence_sources(run_dir: Path, ledger_names: Sequence[str]) -> list[Path]:
    result: list[Path] = []
    for name in ledger_names:
        path = run_dir / name
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                for field, raw in row.items():
                    if "证据路径" not in str(field) or not str(raw or "").strip():
                        continue
                    resolved = resolved_evidence_file(run_dir, str(raw))
                    if resolved is not None:
                        result.append(resolved)
                        audit = binary_evidence_audit_path(resolved)
                        if audit.is_file():
                            result.append(audit)
    return list(dict.fromkeys(result))


def _phase_sources(run_dir: Path, role: AgentRole) -> list[Path]:
    root_files: dict[AgentRole, list[str]] = {
        AgentRole.DISCOVERY: ["batch-scope.json"],
        AgentRole.PLAN_DFX: [
            "batch-scope.json", "page-element-inventory.csv", "page-discovery.csv",
            "selection-option-observations.csv", "interaction-branch-observations.csv", "test-data-lifecycle.csv",
        ],
        AgentRole.RISK_ARBITER: [
            "batch-scope.json", "page-discovery.csv", "element-case-plan.csv",
        ],
        AgentRole.CASE_WORKER: [
            "batch-scope.json", "page-element-inventory.csv", "page-discovery.csv",
            "selection-option-observations.csv", "interaction-branch-observations.csv", "element-case-plan.csv", "test-data-lifecycle.csv",
            "risk-confirmation.csv", "artifacts/data/generation-session.json",
        ],
        AgentRole.REVIEWER: [
            "batch-scope.json", "page-element-inventory.csv", "page-discovery.csv",
            "selection-option-observations.csv", "interaction-branch-observations.csv", "element-case-plan.csv", "test-data-lifecycle.csv",
            "risk-confirmation.csv", "artifacts/data/generation-session.json",
            "artifacts/data/function_cases_manifest.json", "artifacts/data/case-traceability.json",
            "artifacts/data/dfx-assessment.json",
            *(f"artifacts/data/{name}" for name in SHEET_DATA_FILES),
        ],
    }
    rule_names = {
        AgentRole.DISCOVERY: ["page-discovery.md", "data-safety.md"],
        AgentRole.PLAN_DFX: ["case-design.md", "dfx-test-strategy.md", "product-map-sync.md"],
        AgentRole.RISK_ARBITER: ["page-discovery.md", "case-design.md"],
        AgentRole.CASE_WORKER: ["case-design.md", "dfx-test-strategy.md", "data-safety.md"],
        AgentRole.REVIEWER: ["page-discovery.md", "case-design.md", "batch-run.md", "excel-deliverable.md"],
    }
    sources = [run_dir / relative for relative in root_files[role]]
    if role is AgentRole.REVIEWER:
        data = generation_session_data(run_dir) or {}
        manifest_path = run_dir / "artifacts" / "data" / "function_cases_manifest.json"
        if manifest_path.is_file():
            payload = _load_json(manifest_path, "function case manifest")
            if isinstance(payload, dict):
                sources.extend(run_dir / "artifacts" / "data" / str(name) for name in payload.get("parts", []))
        sources.extend(review_evidence_paths(run_dir))
    sources.extend(_rule_sources(run_dir, rule_names[role]))
    sources.extend(_catalog_sources(run_dir))
    sources.extend(_role_contract_sources(run_dir, role))
    if role is not AgentRole.DISCOVERY:
        sources.extend(
            _ledger_evidence_sources(
                run_dir,
                [
                    "page-element-inventory.csv", "page-discovery.csv",
                    "selection-option-observations.csv", "interaction-branch-observations.csv", "risk-confirmation.csv",
                ],
            )
        )
    return list(dict.fromkeys(path.resolve() for path in sources if path.is_file()))


def _read_ledger_rows(run_dir: Path, name: str) -> list[dict[str, str]]:
    with (run_dir / name).open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _case_worker_context_facts(run_dir: Path, owner_key: str) -> dict[str, list[dict[str, str]]]:
    rows = plan_groups(run_dir)[owner_key]
    identity_fields = ["最小标题路径", "交互实例ID", "页面/入口", "元素名称/文案", "元素类型"]

    def identity(row: Mapping[str, object]) -> tuple[str, ...]:
        return tuple("".join(str(row.get(field, "") or "").split()).lower() for field in identity_fields)

    identities = {identity(row) for row in rows}
    interactions = {str(row.get("交互实例ID", "") or "").strip() for row in rows}
    planned_ids = {case_id for row in rows for case_id in _split_ids(row.get("计划用例ID", ""))}
    discovery = [row for row in _read_ledger_rows(run_dir, "page-discovery.csv") if identity(row) in identities]
    selections = [
        row for row in _read_ledger_rows(run_dir, "selection-option-observations.csv")
        if identity(row) in identities
    ]
    branches = [
        row for row in _read_ledger_rows(run_dir, "interaction-branch-observations.csv")
        if identity(row) in identities
    ]
    lifecycle = [
        row for row in _read_ledger_rows(run_dir, "test-data-lifecycle.csv")
        if str(row.get("交互实例ID", "") or "").strip() in interactions
    ]
    risks = [
        row for row in _read_ledger_rows(run_dir, "risk-confirmation.csv")
        if planned_ids & set(_split_ids(row.get("关联用例ID", "")))
    ]
    return {
        "discovery_facts": discovery,
        "selection_facts": selections,
        "branch_facts": branches,
        "lifecycle_facts": lifecycle,
        "risk_facts": risks,
    }


def _case_worker_sources(run_dir: Path, owner_key: str) -> list[Path]:
    sources = [run_dir / "batch-scope.json", run_dir / "artifacts/data/generation-session.json"]
    sources.extend(_rule_sources(run_dir, ["case-design.md", "dfx-test-strategy.md", "data-safety.md"]))
    sources.extend(_catalog_sources(run_dir))
    sources.extend(_role_contract_sources(run_dir, AgentRole.CASE_WORKER))
    facts = _case_worker_context_facts(run_dir, owner_key)
    for rows in facts.values():
        for row in rows:
            for field, raw in row.items():
                if "证据路径" not in str(field) or not str(raw or "").strip():
                    continue
                evidence = resolved_evidence_file(run_dir, str(raw))
                if evidence is not None:
                    sources.append(evidence)
    return list(dict.fromkeys(path.resolve() for path in sources if path.is_file()))


def _frozen_source_bytes(source: Path) -> bytes:
    """Serialize task inputs without introducing forbidden environment URLs.

    JSON Schema's optional ``$schema`` dialect URI is metadata, not part of an
    output contract.  Run directories prohibit raw URLs, so frozen schema
    copies omit only that key while the original file still participates in
    the separately recorded contract fingerprint.
    """

    normalized = source.as_posix()
    if "/docs/test-design/schemas/orchestration/" in normalized and source.suffix == ".json":
        payload = _load_json(source, f"orchestration schema {source.name}")
        if not isinstance(payload, dict):
            raise OrchestrationError(f"orchestration schema must be an object: {source}")
        sanitized = {key: value for key, value in payload.items() if key != "$schema"}
        return (json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return source.read_bytes()


def _snapshot_inputs(run_dir: Path, task_id: str, sources: Sequence[Path]) -> tuple[list[str], list[str]]:
    project = _project_root(run_dir)
    snapshot_root = run_dir / "orchestration" / "inputs" / task_id
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root)
    input_files: list[str] = []
    original_paths: list[str] = []
    for source in sources:
        resolved = source.resolve()
        try:
            relative = resolved.relative_to(project)
        except ValueError as exc:
            raise OrchestrationError(f"task input is outside project root: {source}") from exc
        target = snapshot_root / "project" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_frozen_source_bytes(resolved))
        input_files.append(target.relative_to(run_dir).as_posix())
        original_paths.append(relative.as_posix())
    return input_files, original_paths


def _task_base(role: AgentRole, owner_key: str | None) -> str:
    names = {
        AgentRole.DISCOVERY: "TASK-DISCOVERY",
        AgentRole.PLAN_DFX: "TASK-PLAN-DFX",
        AgentRole.RISK_ARBITER: "TASK-RISK",
        AgentRole.REVIEWER: "TASK-REVIEW",
    }
    if role in names:
        return names[role]
    digest = hashlib.sha256(str(owner_key).encode("utf-8")).hexdigest()[:10].upper()
    return f"TASK-CASE-{digest}"


def _task_outputs(role: AgentRole, task_id: str) -> tuple[list[str], list[str]]:
    base = f"artifacts/agent-work/{role.value}/{task_id}/output"
    names = {
        AgentRole.DISCOVERY: [
            "page-element-inventory.csv", "page-discovery.csv",
            "selection-option-observations.csv", "interaction-branch-observations.csv", "test-data-lifecycle.csv",
        ],
        AgentRole.PLAN_DFX: [
            "element-case-plan.csv", "selection-option-observations.csv", "interaction-branch-observations.csv", "test-data-lifecycle.csv",
            "dfx-assessment.json", "risk-candidates.json",
            "overview.json", "requirements.json", "scenarios.json", "performance.json",
            "risks.json", "automation.json", "page_elements.json",
        ],
        AgentRole.RISK_ARBITER: ["risk-confirmation.csv", "risks.json"],
        AgentRole.CASE_WORKER: ["function_cases.json", "case-traceability.json"],
        AgentRole.REVIEWER: ["review-report.json"],
    }[role]
    exact = [f"{base}/{name}" for name in names]
    prefixes = [f"{base}/evidence/", f"{base}/screenshots/"] if role is AgentRole.DISCOVERY else []
    return exact, prefixes


def _task_context(
    run_dir: Path,
    task: AgentTask,
    source_paths: Sequence[str],
    input_files: Sequence[str],
) -> dict[str, Any]:
    role = task.agent_role
    task_id = task.task_id
    owner_key = task.owner_key
    source_fingerprint = task.source_fingerprint
    frozen_inputs = dict(zip(source_paths, input_files, strict=True))
    project = _project_root(run_dir)
    contract_paths = {
        path.relative_to(project).as_posix()
        for path in _role_contract_sources(run_dir, role)
    }
    contract_inputs = {
        source: snapshot
        for source, snapshot in frozen_inputs.items()
        if source in contract_paths
    }
    context: dict[str, Any] = {
        "architecture": ARCHITECTURE,
        "agent_role": role.value,
        "task_id": task_id,
        "owner_key": owner_key,
        "source_fingerprint": source_fingerprint,
        "write_policy": "只写本任务 output 目录；不得直接写正式账本、manifest、Excel 或产品事实",
        "frozen_input_files": frozen_inputs,
        "contract_input_files": contract_inputs,
        "result_rules": {
            "schema": contract_inputs["docs/test-design/schemas/orchestration/agent-result.schema.json"],
            "rework_schema": contract_inputs["docs/test-design/schemas/orchestration/rework-request.schema.json"],
            "produced_files": "所有状态均必须精确等于本任务 output 目录实际存在的全部文件，不得漏报或虚报",
            "success_required_outputs": list(task.allowed_output_files),
            "success_required_gate": task.required_gate,
            "success": "gate_summary[success_required_gate]=true，rework_requests=[]，error_message=null",
            "needs_rework": "至少一个严格符合冻结 rework_schema 的请求，error_message=null",
            "failed_or_external_blocked": "rework_requests=[] 且 error_message 为非空具体原因",
        },
        "output_contract": {
            "allowed_output_files": list(task.allowed_output_files),
            "allowed_output_prefixes": list(task.allowed_output_prefixes),
        },
    }
    if role is AgentRole.DISCOVERY:
        context["output_contract"]["csv_templates"] = {
            name.removesuffix("-template.csv") + ".csv": contract_inputs[
                f"docs/test-assets/batch-runs/templates/{name}"
            ]
            for name in [
                "page-element-inventory-template.csv",
                "page-discovery-template.csv",
                "selection-option-observations-template.csv",
                "interaction-branch-observations-template.csv",
                "test-data-lifecycle-template.csv",
            ]
        }
        context["output_contract"]["binary_evidence_audit_schema"] = contract_inputs[
            "docs/test-design/schemas/orchestration/binary-evidence-audit.schema.json"
        ]
        context["instructions"] = [
            "独立盘点后全量执行全部交互", "有限选择项逐项操作", "输入/动态选择/分页/弹窗逐分支独立执行并记账", "页面可验证问题自行验证",
            "创建成功后完成修改回显和生效闭环", "证据写入 output/evidence 或 output/screenshots",
            "二进制截图/视频/PDF 必须先裁剪或遮蔽地址栏、环境标识、账号和凭据，并生成同名 .sensitive-audit.json（绑定 SHA256、转录可见文本）",
        ]
    elif role is AgentRole.PLAN_DFX:
        context["output_contract"].update(
            {
                "csv_templates": {
                    name.removesuffix("-template.csv") + ".csv": contract_inputs[
                        f"docs/test-assets/batch-runs/templates/{name}"
                    ]
                    for name in [
                        "element-case-plan-template.csv",
                        "selection-option-observations-template.csv",
                        "interaction-branch-observations-template.csv",
                        "test-data-lifecycle-template.csv",
                    ]
                },
                "sheet_json_headers": SHEET_DATA_HEADERS,
                "dfx_assessment_schema": contract_inputs[
                    "docs/test-design/schemas/orchestration/dfx-assessment.schema.json"
                ],
                "dfx_matrix": DFX_MATRIX,
                "risk_candidates_schema": contract_inputs[
                    "docs/test-design/schemas/orchestration/risk-candidates.schema.json"
                ],
            }
        )
        context["instructions"] = [
            "只基于已冻结 discovery 事实生成计划", "完成 DFX 12×4 评估和预算",
            "输出全部非功能用例 Sheet JSON",
            "risk-candidates.json 使用 {candidates: []} 结构；affected_interaction_ids 必须来自当前 page-discovery，evidence 必须引用当前 run 的非空实探证据，每项包含 dfx_dimensions",
            "页面可验证问题必须 NEEDS_REWORK 返回 discovery，不得作为外部语义候选提交",
        ]
    elif role is AgentRole.RISK_ARBITER:
        context["output_contract"].update(
            {
                "risk_confirmation_template": contract_inputs[
                    "docs/test-assets/batch-runs/templates/risk-confirmation-template.csv"
                ],
                "risks_json_headers": SHEET_DATA_HEADERS["risks.json"],
                "risk_candidates_schema": contract_inputs[
                    "docs/test-design/schemas/orchestration/risk-candidates.schema.json"
                ],
            }
        )
        context["instructions"] = [
            "页面可验证项必须 NEEDS_REWORK 返回 discovery", "只保留真实外部语义确认项",
        ]
        context["risk_candidates"] = _plan_risk_candidates(run_dir)
    elif role is AgentRole.CASE_WORKER:
        rows = plan_groups(run_dir)[str(owner_key)]
        generation_fp = str((generation_session_data(run_dir) or {}).get("source_fingerprint", ""))
        expected = traceability_expectations(run_dir, rows, task_id, generation_fp)
        context.update(
            {
                "output_contract": {
                    **context["output_contract"],
                    "function_case_required_fields": FUNCTION_CASE_REQUIRED_FIELDS,
                    "max_cases_per_formal_shard": MAX_FUNCTION_CASES_PER_PART,
                    "traceability_schema": contract_inputs[
                        "docs/test-design/schemas/orchestration/traceability-record.schema.json"
                    ],
                },
                "plan_rows": rows,
                "planned_case_ids": [case_id for row in rows for case_id in _split_ids(row.get("计划用例ID", ""))],
                "traceability_expectations": [record.to_dict() for record in expected.values()],
                **_case_worker_context_facts(run_dir, str(owner_key)),
                "instructions": [
                    "只生成当前功能点", "严格使用计划 ID 与顺序", "步骤和预期分别唯一",
                    "每条 case-traceability 必须与给定 expectation 完全一致",
                ],
            }
        )
    else:
        context.update(
            {
                "output_contract": {
                    **context["output_contract"],
                    "review_report_schema": contract_inputs[
                        "docs/test-design/schemas/orchestration/review-report.schema.json"
                    ],
                    "binary_evidence_audit_schema": contract_inputs[
                        "docs/test-design/schemas/orchestration/binary-evidence-audit.schema.json"
                    ],
                    "traceability_schema": contract_inputs[
                        "docs/test-design/schemas/orchestration/traceability-record.schema.json"
                    ],
                },
                "required_checks": list(REQUIRED_REVIEW_CHECKS),
                "generation_session": generation_session_data(run_dir),
                "review_source_fingerprint": review_source_fingerprint(run_dir),
                "generator_task_ids": _successful_case_task_ids(_load_manifest(run_dir)),
                "instructions": [
                    "只读审查，不修改正式产物",
                    "逐个查看二进制证据，并核对同哈希 .sensitive-audit.json 的可见文本与脱敏声明",
                    "有问题输出 NEEDS_REWORK；无问题输出 APPROVED 报告",
                ],
            }
        )
    return context


def _split_ids(value: str) -> list[str]:
    from ..batch import split_plan_values

    return split_plan_values(value)


def _create_task(
    run_dir: Path,
    manifest: dict[str, Any],
    role: AgentRole,
    *,
    owner_key: str | None = None,
    force_generation_fingerprint: bool = False,
) -> AgentTask:
    config = _config(run_dir)
    base = _task_base(role, owner_key)
    sources = (
        _case_worker_sources(run_dir, str(owner_key))
        if role is AgentRole.CASE_WORKER and owner_key is not None
        else _phase_sources(run_dir, role)
    )
    source_fp = (
        _generation_task_fingerprint(run_dir)
        if force_generation_fingerprint
        else fingerprint(sources)
    )
    if len(source_fp) != 64:
        raise OrchestrationError(f"cannot create {role.value} task without a valid source fingerprint")
    same = [
        entry for entry in manifest["tasks"].values()
        if entry.get("task", {}).get("agent_role") == role.value
        and entry.get("task", {}).get("owner_key") == owner_key
    ]
    sequence = len(same) + 1
    attempt = 1 + sum(
        entry.get("task", {}).get("source_fingerprint") == source_fp for entry in same
    )
    if attempt > config.max_rework_attempts + 1:
        machine = _machine(manifest)
        reason = (
            f"{role.value}/{owner_key or '-'} exceeded {config.max_rework_attempts + 1} "
            f"attempts for source {source_fp[:12]}"
        )
        change = machine.fail(reason)
        _save_machine(run_dir, manifest, machine)
        _event_store(run_dir).append("RUN_FAILED", change.to_dict())
        raise OrchestrationError(reason)
    task_id = f"{base}-A{sequence:02d}"
    input_files, source_paths = _snapshot_inputs(run_dir, task_id, sources)
    outputs, prefixes = _task_outputs(role, task_id)
    phase = ROLE_PHASE[role]
    task = AgentTask(
        schema_version="1.0.0",
        task_id=task_id,
        run_id=str(manifest["run_id"]),
        batch_id=str(manifest["batch_id"]),
        phase=ReworkTarget(phase.value),
        agent_role=role,
        owner_key=owner_key,
        input_files=tuple(input_files),
        allowed_output_files=tuple(outputs),
        allowed_output_prefixes=tuple(prefixes),
        required_gate="cases-worker" if role is AgentRole.CASE_WORKER else phase.value,
        source_fingerprint=source_fp,
        attempt=attempt,
    )
    workspace = WorkspaceManager(run_dir)
    root = workspace.create_task_workspace(role.value, task_id, clean=True)
    task_packet_path = root / "meta" / "agent-task.json"
    context_path = root / "meta" / "task-context.json"
    atomic_write_json(task_packet_path, task.to_dict())
    atomic_write_json(context_path, _task_context(run_dir, task, source_paths, input_files))
    task_path = run_dir / "orchestration" / "tasks" / f"{task_id}.json"
    atomic_write_json(task_path, task.to_dict())
    project = _project_root(run_dir)
    contract_sources = _role_contract_sources(run_dir, role)
    manifest["tasks"][task_id] = {
        "task": task.to_dict(),
        "status": "PENDING",
        "claim": None,
        "claim_history": [],
        "page_probe_receipt": None,
        "page_probe_history": [],
        "dispatch_wave": None,
        "required_outputs": outputs,
        "source_paths": source_paths,
        "contract_source_paths": [
            path.relative_to(project).as_posix() for path in contract_sources
        ],
        "contract_fingerprint": fingerprint(contract_sources),
        "input_snapshot_fingerprint": fingerprint([run_dir / path for path in input_files]),
        "task_packet_fingerprint": fingerprint([task_packet_path]),
        "context_fingerprint": fingerprint([context_path]),
        "review_input_fingerprint": review_source_fingerprint(run_dir) if role is AgentRole.REVIEWER else None,
        "result_path": None,
        "result_fingerprint": None,
        "output_fingerprint": None,
        "accepted_output_root": None,
        "accepted_output_fingerprint": None,
        "promotion_ids": [],
        "invalidated_reason": None,
    }
    _save_manifest(run_dir, manifest)
    _event_store(run_dir).append("TASK_CREATED", {"role": role.value, "owner_key": owner_key, "attempt": attempt}, task_id=task_id)
    return task


def _pending_task(
    manifest: Mapping[str, Any], role: AgentRole, owner_key: str | None = None
) -> AgentTask | None:
    for entry in manifest["tasks"].values():
        raw = entry.get("task", {})
        if (
            entry.get("status") in {"PENDING", "CLAIMED"}
            and raw.get("agent_role") == role.value
            and raw.get("owner_key") == owner_key
        ):
            return AgentTask.from_dict(raw)
    return None


def _latest_success_entry(
    run_dir: Path, manifest: Mapping[str, Any], role: AgentRole, owner_key: str | None = None
) -> tuple[AgentTask, Mapping[str, Any]] | None:
    matches: list[tuple[AgentTask, Mapping[str, Any]]] = []
    for entry in manifest["tasks"].values():
        raw = entry.get("task", {})
        if entry.get("status") == "SUCCEEDED" and raw.get("agent_role") == role.value and raw.get("owner_key") == owner_key:
            matches.append((AgentTask.from_dict(raw), entry))
    if not matches:
        return None
    selected = max(matches, key=lambda item: item[0].attempt)
    task, entry = selected
    expected = str(entry.get("accepted_output_fingerprint") or "")
    accepted_paths = [path for path in _accepted_root(run_dir, task).rglob("*") if path.is_file()]
    actual = fingerprint(accepted_paths)
    if len(expected) != 64 or actual != expected:
        raise OrchestrationError(f"successful task {task.task_id} workspace output fingerprint is stale")
    return selected


def _workspace_output(run_dir: Path, task: AgentTask, name: str) -> Path:
    return run_dir / f"artifacts/agent-work/{task.agent_role.value}/{task.task_id}/output/{name}"


def _accepted_root(run_dir: Path, task: AgentTask) -> Path:
    return run_dir / "orchestration" / "accepted" / task.task_id


def _accepted_output(run_dir: Path, task: AgentTask, name: str) -> Path:
    return _accepted_root(run_dir, task) / name


def _snapshot_accepted_outputs(run_dir: Path, task: AgentTask) -> str:
    accepted = _accepted_root(run_dir, task)
    trusted_root = (run_dir / "orchestration" / "accepted").resolve()
    if accepted.resolve() == trusted_root or trusted_root not in accepted.resolve().parents:
        raise OrchestrationError(f"unsafe accepted output path: {accepted}")
    temporary = accepted.with_name(f".{accepted.name}.{uuid4().hex}.tmp")
    temporary.mkdir(parents=True, exist_ok=False)
    workspace = WorkspaceManager(run_dir)
    before = workspace.output_manifest(task.agent_role.value, task.task_id)
    try:
        for record in before:
            relative = str(record["path"])
            source = workspace.resolve_task_output(task.agent_role.value, task.task_id, relative)
            target = (temporary / relative).resolve()
            try:
                target.relative_to(temporary.resolve())
            except ValueError as exc:
                raise OrchestrationError(f"accepted output path escapes task snapshot: {relative}") from exc
            atomic_copy(source, target)
        after = workspace.output_manifest(task.agent_role.value, task.task_id)
        if before != after:
            raise OrchestrationError("Agent workspace changed while creating the accepted output snapshot")
        copied = WorkspaceManager(run_dir).file_record
        copied_manifest = sorted(
            [copied(path, relative_to=temporary) for path in temporary.rglob("*") if path.is_file()],
            key=lambda item: str(item["path"]),
        )
        if copied_manifest != before:
            raise OrchestrationError("accepted output snapshot hashes differ from the validated Agent workspace")
        if accepted.exists():
            shutil.rmtree(accepted)
        os.replace(temporary, accepted)
        temporary = None
        paths = [path for path in accepted.rglob("*") if path.is_file()]
        return fingerprint(paths)
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)


def _plan_risk_candidates(run_dir: Path) -> list[dict[str, Any]]:
    manifest = _load_manifest(run_dir)
    latest = _latest_success_entry(run_dir, manifest, AgentRole.PLAN_DFX)
    if not latest:
        return []
    task, _ = latest
    return _validate_risk_candidates_file(
        _accepted_output(run_dir, task, "risk-candidates.json"),
        run_dir=run_dir,
    )


def _current_discovery_interaction_evidence(run_dir: Path) -> dict[str, set[Path]]:
    path = run_dir / "page-discovery.csv"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            required_headers = {"交互实例ID", "证据路径"}
            missing_headers = sorted(required_headers - set(reader.fieldnames or []))
            if missing_headers:
                raise OrchestrationError(
                    "page-discovery.csv is missing risk-grounding headers: "
                    f"{missing_headers}"
                )
            interactions: dict[str, set[Path]] = {}
            for row_number, row in enumerate(reader, start=2):
                interaction_id = str(row.get("交互实例ID", "") or "").strip()
                if not interaction_id:
                    continue
                raw_evidence = str(row.get("证据路径", "") or "").strip()
                evidence = resolved_evidence_file(run_dir, raw_evidence)
                if evidence is None:
                    raise OrchestrationError(
                        f"page-discovery.csv row {row_number} interaction {interaction_id} must have "
                        "real, non-empty evidence inside the current run artifacts before risk grounding"
                    )
                interactions.setdefault(interaction_id, set()).add(evidence)
            return interactions
    except OSError as exc:
        raise OrchestrationError(
            f"cannot read current page-discovery.csv for risk candidate grounding: {exc}"
        ) from exc


def _validate_risk_candidates_file(
    path: Path,
    *,
    run_dir: Path | None = None,
) -> list[dict[str, Any]]:
    try:
        assert_no_sensitive_text_file(path, "risk-candidates.json")
    except SensitiveDataError as exc:
        raise OrchestrationError(str(exc)) from exc
    value = _load_json(path, "risk-candidates.json")
    if not isinstance(value, dict) or set(value) != {"candidates"} or not isinstance(value["candidates"], list):
        raise OrchestrationError("risk-candidates.json must be exactly {\"candidates\": [...]} ")
    required = {
        "risk_id", "question", "page_verifiability", "page_action", "page_result",
        "external_reason", "affected_interaction_ids", "evidence", "dfx_dimensions",
    }
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    discovery_evidence_by_interaction: dict[str, set[Path]] | None = None
    for index, raw in enumerate(value["candidates"]):
        if not isinstance(raw, dict) or set(raw) != required:
            raise OrchestrationError(
                f"risk-candidates.json candidate {index} fields must be exactly {sorted(required)}"
            )
        risk_id = str(raw["risk_id"] or "").strip()
        question = str(raw["question"] or "").strip()
        if not risk_id or not question or risk_id in seen:
            raise OrchestrationError("risk candidates require unique risk_id and non-empty question")
        seen.add(risk_id)
        if raw["page_verifiability"] not in {"page_verifiable", "external_semantics"}:
            raise OrchestrationError("risk candidate page_verifiability must be page_verifiable or external_semantics")
        for field in ["affected_interaction_ids", "evidence", "dfx_dimensions"]:
            if not isinstance(raw[field], list):
                raise OrchestrationError(f"risk candidate {field} must be an array")
        if any(str(item) not in DFX_MATRIX for item in raw["dfx_dimensions"]):
            raise OrchestrationError(f"risk candidate {risk_id} contains an unknown DFX dimension")
        if len(raw["dfx_dimensions"]) != len(set(raw["dfx_dimensions"])):
            raise OrchestrationError(f"risk candidate {risk_id} repeats DFX dimensions")
        if raw["page_verifiability"] == "page_verifiable":
            raise OrchestrationError(
                f"risk candidate {risk_id} is page-verifiable and must return to discovery via NEEDS_REWORK"
            )
        for field in ["page_action", "page_result", "external_reason"]:
            if not str(raw[field] or "").strip():
                raise OrchestrationError(f"external risk candidate {risk_id} requires non-empty {field}")
        if not raw["affected_interaction_ids"] or not raw["evidence"]:
            raise OrchestrationError(
                f"external risk candidate {risk_id} requires affected interaction IDs and deep-discovery evidence"
            )
        if run_dir is None:
            raise OrchestrationError(
                f"external risk candidate {risk_id} requires the current run directory for fact grounding"
            )
        if discovery_evidence_by_interaction is None:
            discovery_evidence_by_interaction = _current_discovery_interaction_evidence(
                run_dir.resolve()
            )
        if any(not isinstance(item, str) for item in raw["affected_interaction_ids"]):
            raise OrchestrationError(
                f"external risk candidate {risk_id} affected_interaction_ids must contain strings"
            )
        affected_ids = [item.strip() for item in raw["affected_interaction_ids"]]
        if any(not item for item in affected_ids):
            raise OrchestrationError(
                f"external risk candidate {risk_id} affected_interaction_ids must contain non-empty IDs"
            )
        if len(affected_ids) != len(set(affected_ids)):
            raise OrchestrationError(
                f"external risk candidate {risk_id} repeats affected_interaction_ids"
            )
        unknown_interactions = sorted(
            set(affected_ids) - set(discovery_evidence_by_interaction)
        )
        if unknown_interactions:
            raise OrchestrationError(
                f"external risk candidate {risk_id} references interaction IDs absent from current "
                f"page-discovery.csv: {unknown_interactions}"
            )
        resolved_evidence: set[Path] = set()
        for evidence_index, raw_evidence in enumerate(raw["evidence"]):
            if not isinstance(raw_evidence, str) or not raw_evidence.strip():
                raise OrchestrationError(
                    f"external risk candidate {risk_id} evidence[{evidence_index}] must be a non-empty path"
                )
            evidence = resolved_evidence_file(run_dir, raw_evidence.strip())
            if evidence is None:
                raise OrchestrationError(
                    f"external risk candidate {risk_id} evidence[{evidence_index}] must resolve to a real, "
                    "non-empty file inside the current run artifacts"
                )
            if evidence in resolved_evidence:
                raise OrchestrationError(
                    f"external risk candidate {risk_id} repeats the same resolved evidence file: "
                    f"{raw_evidence.strip()}"
                )
            resolved_evidence.add(evidence)
            try:
                assert_no_sensitive_artifact(
                    evidence,
                    f"risk candidate {risk_id} evidence[{evidence_index}]",
                )
            except SensitiveDataError as exc:
                raise OrchestrationError(str(exc)) from exc
        evidence_mismatches = {
            interaction_id: sorted(
                evidence.relative_to(run_dir.resolve()).as_posix()
                for evidence in discovery_evidence_by_interaction[interaction_id] - resolved_evidence
            )
            for interaction_id in affected_ids
            if discovery_evidence_by_interaction[interaction_id] - resolved_evidence
        }
        if evidence_mismatches:
            raise OrchestrationError(
                f"external risk candidate {risk_id} evidence does not cover each affected interaction's "
                f"page-discovery evidence: {evidence_mismatches}"
            )
        candidates.append(raw)
    return candidates


def _split_case_ids(value: object) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[,，;；\s]+", str(value or ""))
        if item.strip()
    ]


def _validate_dfx_assessment(path: Path, plan_path: Path) -> dict[str, str]:
    value = _load_json(path, "dfx-assessment.json")
    if (
        not isinstance(value, dict)
        or set(value) != {"dimensions", "elements"}
        or not isinstance(value["dimensions"], list)
        or not isinstance(value["elements"], list)
    ):
        raise OrchestrationError(
            "dfx-assessment.json must contain exactly global dimensions and per-element assessments"
        )
    expected = set(DFX_MATRIX)
    actual: set[str] = set()
    statuses: dict[str, str] = {}
    for index, item in enumerate(value["dimensions"]):
        required = {"dimension", "status", "reason", "scenarios"}
        if not isinstance(item, dict) or set(item) != required:
            raise OrchestrationError(f"DFX dimension {index} must use fields {sorted(required)}")
        dimension = str(item["dimension"] or "").strip()
        if dimension not in expected or dimension in actual:
            raise OrchestrationError(f"DFX dimension {dimension!r} is unknown or duplicated")
        actual.add(dimension)
        if item["status"] not in {"适用", "不适用", "待确认", "需补充证据"}:
            raise OrchestrationError(f"DFX dimension {dimension} has invalid status")
        if not str(item["reason"] or "").strip():
            raise OrchestrationError(f"DFX dimension {dimension} requires a reason")
        if item["scenarios"] != DFX_MATRIX[dimension]:
            raise OrchestrationError(
                f"DFX dimension {dimension} scenarios must exactly equal the canonical four-scenario matrix"
            )
        statuses[dimension] = str(item["status"])
    if actual != expected:
        raise OrchestrationError(f"dfx-assessment.json must cover all 12 dimensions; missing={sorted(expected-actual)}")

    with plan_path.open("r", encoding="utf-8-sig", newline="") as stream:
        plan_rows = list(csv.DictReader(stream))
    if not plan_rows:
        raise OrchestrationError("element-case-plan.csv must contain at least one element before DFX assessment")
    plan_by_interaction: dict[str, Mapping[str, str]] = {}
    for row_number, row in enumerate(plan_rows, start=2):
        interaction_id = str(row.get("交互实例ID", "") or "").strip()
        if not interaction_id or interaction_id in plan_by_interaction:
            raise OrchestrationError(
                f"element-case-plan.csv row {row_number} requires a unique 交互实例ID"
            )
        plan_by_interaction[interaction_id] = row
    seen_elements: set[str] = set()
    for element_index, element in enumerate(value["elements"]):
        required_element = {"interaction_id", "function_point", "assessments"}
        if not isinstance(element, dict) or set(element) != required_element:
            raise OrchestrationError(
                f"DFX element {element_index} must use fields {sorted(required_element)}"
            )
        interaction_id = str(element["interaction_id"] or "").strip()
        plan = plan_by_interaction.get(interaction_id)
        if plan is None or interaction_id in seen_elements:
            raise OrchestrationError(
                f"DFX element interaction_id {interaction_id!r} is missing from plan or duplicated"
            )
        seen_elements.add(interaction_id)
        function_point = str(element["function_point"] or "").strip()
        if not function_point or function_point != str(plan.get("功能点", "") or "").strip():
            raise OrchestrationError(
                f"DFX element {interaction_id} function_point must exactly match element-case-plan.csv"
            )
        assessments = element["assessments"]
        if not isinstance(assessments, list) or len(assessments) != len(DFX_MATRIX):
            raise OrchestrationError(f"DFX element {interaction_id} must assess all 12 dimensions")
        assessed_dimensions: set[str] = set()
        applicable_dimensions: set[str] = set()
        applicable_scenarios: set[str] = set()
        referenced_case_ids: set[str] = set()
        for assessment_index, assessment in enumerate(assessments):
            if not isinstance(assessment, dict) or set(assessment) != {"dimension", "scenarios"}:
                raise OrchestrationError(
                    f"DFX element {interaction_id} assessment {assessment_index} has invalid fields"
                )
            dimension = str(assessment["dimension"] or "").strip()
            if dimension not in DFX_MATRIX or dimension in assessed_dimensions:
                raise OrchestrationError(
                    f"DFX element {interaction_id} dimension {dimension!r} is unknown or duplicated"
                )
            assessed_dimensions.add(dimension)
            scenario_rows = assessment["scenarios"]
            if not isinstance(scenario_rows, list) or len(scenario_rows) != 4:
                raise OrchestrationError(
                    f"DFX element {interaction_id}/{dimension} must assess exactly four scenarios"
                )
            scenario_names: list[str] = []
            for scenario_index, scenario_row in enumerate(scenario_rows):
                fields = {"scenario", "status", "reason", "planned_case_ids"}
                if not isinstance(scenario_row, dict) or set(scenario_row) != fields:
                    raise OrchestrationError(
                        f"DFX element {interaction_id}/{dimension} scenario {scenario_index} has invalid fields"
                    )
                scenario = str(scenario_row["scenario"] or "").strip()
                scenario_names.append(scenario)
                status = scenario_row["status"]
                if status not in {"适用", "不适用", "待确认", "需补充证据"}:
                    raise OrchestrationError(
                        f"DFX element {interaction_id}/{dimension}/{scenario} has invalid status"
                    )
                if not str(scenario_row["reason"] or "").strip():
                    raise OrchestrationError(
                        f"DFX element {interaction_id}/{dimension}/{scenario} requires a reason"
                    )
                case_ids = scenario_row["planned_case_ids"]
                if not isinstance(case_ids, list) or len(case_ids) != len(set(case_ids)) or any(
                    not isinstance(case_id, str) or not case_id.strip() for case_id in case_ids
                ):
                    raise OrchestrationError(
                        f"DFX element {interaction_id}/{dimension}/{scenario} planned_case_ids is invalid"
                    )
                if status == "适用":
                    applicable_dimensions.add(dimension)
                    applicable_scenarios.add(scenario)
                    repeated = referenced_case_ids & set(case_ids)
                    if repeated:
                        raise OrchestrationError(
                            f"DFX element {interaction_id} assigns planned cases to multiple scenarios: "
                            f"{sorted(repeated)}"
                        )
                    referenced_case_ids.update(case_ids)
                elif case_ids:
                    raise OrchestrationError(
                        f"DFX element {interaction_id}/{dimension}/{scenario} may reference cases only when status=适用"
                    )
            if scenario_names != DFX_MATRIX[dimension]:
                raise OrchestrationError(
                    f"DFX element {interaction_id}/{dimension} scenarios must equal the canonical four in order"
                )
        if assessed_dimensions != set(DFX_MATRIX):
            raise OrchestrationError(f"DFX element {interaction_id} does not cover all 12 dimensions")
        planned_ids = set(_split_case_ids(plan.get("计划用例ID", "")))
        if referenced_case_ids != planned_ids:
            raise OrchestrationError(
                f"DFX element {interaction_id} applicable scenario case IDs must exactly equal its plan budget; "
                f"missing={sorted(planned_ids-referenced_case_ids)}, unknown={sorted(referenced_case_ids-planned_ids)}"
            )
        plan_dimensions = set(_split_case_ids(plan.get("适用DFX维度", "")))
        plan_scenarios = set(_split_case_ids(plan.get("适用DFX场景", "")))
        if applicable_dimensions != plan_dimensions or applicable_scenarios != plan_scenarios:
            raise OrchestrationError(
                f"DFX element {interaction_id} applicable dimensions/scenarios must exactly match element-case-plan.csv"
            )
    if seen_elements != set(plan_by_interaction):
        raise OrchestrationError(
            "dfx-assessment.json elements must cover every and only plan interaction; "
            f"missing={sorted(set(plan_by_interaction)-seen_elements)}"
        )
    return statuses


def _validate_plan_retained_outputs(run_dir: Path, task: AgentTask) -> None:
    statuses = _validate_dfx_assessment(
        _accepted_output(run_dir, task, "dfx-assessment.json"),
        _accepted_output(run_dir, task, "element-case-plan.csv"),
    )
    candidates = _validate_risk_candidates_file(
        _accepted_output(run_dir, task, "risk-candidates.json"),
        run_dir=run_dir,
    )
    evidence_gaps = sorted(name for name, status in statuses.items() if status == "需补充证据")
    if evidence_gaps:
        raise OrchestrationError(
            f"DFX dimensions still require evidence and must return to discovery/plan: {evidence_gaps}"
        )
    pending = {name for name, status in statuses.items() if status == "待确认"}
    candidate_dimensions = {
        str(name) for item in candidates for name in item.get("dfx_dimensions", [])
    }
    if pending != candidate_dimensions:
        raise OrchestrationError(
            "DFX 待确认 dimensions must exactly match external risk candidate dfx_dimensions; "
            f"pending={sorted(pending)}, candidates={sorted(candidate_dimensions)}"
        )
    for name in SHEET_DATA_FILES:
        validate_sheet_data_file(_accepted_output(run_dir, task, name))
    landing_text = "\n".join(
        _accepted_output(run_dir, task, name).read_text(encoding="utf-8-sig")
        for name in ["scenarios.json", "performance.json", "risks.json", "page_elements.json"]
    )
    applicable = [name for name, status in statuses.items() if status == "适用"]
    missing_landing = [name for name in applicable if name not in landing_text]
    if missing_landing:
        raise OrchestrationError(
            f"applicable DFX dimensions must land in plan Sheet JSON: {missing_landing}"
        )


def _validate_plan_link_outputs(run_dir: Path, task: AgentTask) -> None:
    """Allow Plan & DFX to add case links without rewriting discovery facts."""

    policies = {
        "selection-option-observations.csv": {"关联用例ID"},
        "interaction-branch-observations.csv": {"关联用例ID"},
        "test-data-lifecycle.csv": {"创建步骤关联用例"},
    }
    for name, mutable_fields in policies.items():
        source_path = run_dir / name
        output_path = _accepted_output(run_dir, task, name)
        with source_path.open("r", encoding="utf-8-sig", newline="") as stream:
            source_reader = csv.DictReader(stream)
            source_headers = list(source_reader.fieldnames or [])
            source_rows = list(source_reader)
        with output_path.open("r", encoding="utf-8-sig", newline="") as stream:
            output_reader = csv.DictReader(stream)
            output_headers = list(output_reader.fieldnames or [])
            output_rows = list(output_reader)
        if output_headers != source_headers:
            raise OrchestrationError(f"{name} Plan output must preserve the exact discovery header")
        if len(output_rows) != len(source_rows):
            raise OrchestrationError(f"{name} Plan output must preserve discovery row count and order")
        immutable_fields = [field for field in source_headers if field not in mutable_fields]
        for index, (before, after) in enumerate(zip(source_rows, output_rows), start=2):
            changed = [field for field in immutable_fields if before.get(field, "") != after.get(field, "")]
            if changed:
                raise OrchestrationError(
                    f"{name} row {index} changes discovery-owned fields: {changed}"
                )


def _validate_risk_candidate_resolution(run_dir: Path, task: AgentTask) -> None:
    candidates = _plan_risk_candidates(run_dir)
    expected = [str(item["risk_id"]).strip() for item in candidates]
    risk_path = _accepted_output(run_dir, task, "risk-confirmation.csv")
    with risk_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    actual = [
        str(row.get("风险ID", "") or "").strip()
        for row in rows
        if str(row.get("风险ID", "") or "").strip() not in {"", "RISK-PENDING"}
    ]
    if actual != expected:
        raise OrchestrationError(
            "Risk Arbiter must resolve every and only risk-candidates.json item in order; "
            f"expected={expected}, actual={actual}"
        )


_RETAINED_SHEET_JSON_NAMES = (
    "overview.json",
    "requirements.json",
    "scenarios.json",
    "performance.json",
    "risks.json",
    "automation.json",
    "page_elements.json",
    "dfx-assessment.json",
)


def _retained_sheet_sources(
    run_dir: Path,
    plan_task: AgentTask,
    risk_task: AgentTask | None,
) -> dict[str, Path]:
    sources = {
        name: _accepted_output(run_dir, plan_task, name)
        for name in _RETAINED_SHEET_JSON_NAMES
    }
    if risk_task is not None:
        sources["risks.json"] = _accepted_output(run_dir, risk_task, "risks.json")
    missing = [name for name, path in sources.items() if not path.is_file()]
    if missing:
        raise OrchestrationError(
            f"retained Plan/Risk sheet JSON source is missing: {missing}"
        )
    return sources


def _retained_sheet_source_fingerprint(sources: Mapping[str, Path]) -> str:
    return canonical_fingerprint(
        [
            {
                "name": name,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
            for name, path in sorted(sources.items())
        ]
    )


def _retained_sheet_metadata(
    plan_task: AgentTask,
    risk_task: AgentTask | None,
    source_fingerprint: str,
) -> dict[str, object]:
    return {
        "promotion_kind": "retained-sheet-json",
        "plan_task_id": plan_task.task_id,
        "risk_task_id": risk_task.task_id if risk_task is not None else None,
        "source_fingerprint": source_fingerprint,
    }


def _retained_sheet_transaction_id(metadata: Mapping[str, object]) -> str:
    return f"retained-sheets-{canonical_fingerprint(dict(metadata))[:40]}"


def _assert_retained_sheet_targets_match(
    run_dir: Path,
    sources: Mapping[str, Path],
) -> None:
    """Fail closed when a finalized retained-sheet set no longer matches its sources."""

    mismatches: list[str] = []
    for name in _RETAINED_SHEET_JSON_NAMES:
        source = sources[name]
        target = run_dir / "artifacts" / "data" / name
        if target.is_symlink() or not target.is_file():
            mismatches.append(f"{name}: missing or not a regular file")
            continue
        if target.stat().st_size != source.stat().st_size:
            mismatches.append(f"{name}: size mismatch")
            continue
        if _sha256_file(target) != _sha256_file(source):
            mismatches.append(f"{name}: sha256 mismatch")
    if mismatches:
        raise OrchestrationError(
            "finalized retained Plan/Risk sheet promotion no longer matches its "
            "accepted source set: " + "; ".join(mismatches)
        )


def _promote_retained_sheet_json(run_dir: Path, manifest: dict[str, Any]) -> None:
    plan_entry = _latest_success_entry(run_dir, manifest, AgentRole.PLAN_DFX)
    if not plan_entry:
        raise OrchestrationError("cases phase requires a successful Plan & DFX task")
    plan_task, _ = plan_entry
    risk_entry = _latest_success_entry(run_dir, manifest, AgentRole.RISK_ARBITER)
    risk_task = risk_entry[0] if risk_entry else None
    sources = _retained_sheet_sources(run_dir, plan_task, risk_task)
    source_fingerprint = _retained_sheet_source_fingerprint(sources)
    metadata = _retained_sheet_metadata(plan_task, risk_task, source_fingerprint)
    transaction_id = _retained_sheet_transaction_id(metadata)
    mapping = {
        name: f"artifacts/data/{name}" for name in _RETAINED_SHEET_JSON_NAMES
    }

    entry = manifest["tasks"].get(plan_task.task_id)
    if not isinstance(entry, dict):
        raise OrchestrationError("retained sheet promotion lost its Plan task entry")
    promotion_ids = entry.setdefault("promotion_ids", [])
    if not isinstance(promotion_ids, list):
        raise OrchestrationError(
            f"task {plan_task.task_id} promotion_ids must be an array"
        )

    # A finalized receipt is the durable intent checkpoint.  Never silently
    # trust file existence: workers may start only when all eight formal files
    # still match the accepted Plan/Risk source set byte-for-byte.
    receipt_path = _promotion_receipt_path(run_dir, transaction_id)
    if receipt_path.is_file():
        value = _load_json(
            receipt_path, f"retained promotion {transaction_id} receipt"
        )
        if not isinstance(value, Mapping):
            raise OrchestrationError(
                f"retained promotion receipt must be an object: {receipt_path}"
            )
        if value.get("status") == "FINALIZED":
            if value.get("task_id") != plan_task.task_id:
                raise OrchestrationError(
                    f"retained promotion {transaction_id} Plan task does not match"
                )
            if value.get("agent_role") != plan_task.agent_role.value:
                raise OrchestrationError(
                    f"retained promotion {transaction_id} agent role does not match"
                )
            if transaction_id not in promotion_ids:
                raise OrchestrationError(
                    f"finalized retained promotion {transaction_id} is not linked "
                    "from its Plan task"
                )
            _validate_retained_promotion_receipt(
                run_dir, manifest, transaction_id, value, plan_task
            )
            _assert_retained_sheet_targets_match(run_dir, sources)
            _cleanup_promotion_source(run_dir, transaction_id)
            return

    # Freeze the source set before publishing the intent link.  If the process
    # stops after the manifest save but before receipt.json is created, the next
    # CASES advance derives the same transaction id and safely creates/resumes it.
    source_root = _prepare_named_promotion_sources(
        run_dir, sources, transaction_id
    )
    if transaction_id not in promotion_ids:
        promotion_ids.append(transaction_id)
        _save_manifest(run_dir, manifest)

    manager = WorkspaceManager(run_dir)
    try:
        receipt = manager.atomic_promote(
            plan_task.agent_role.value,
            plan_task.task_id,
            mapping,
            source_root=source_root,
            target_root=run_dir,
            transaction_id=transaction_id,
            metadata=metadata,
        )
        manager.finalize_promotion(receipt)
    except WorkspaceError as exc:
        raise OrchestrationError(
            f"durable retained Plan/Risk sheet promotion failed: {exc}"
        ) from exc
    _cleanup_promotion_source(run_dir, transaction_id)


def _begin_if_needed(run_dir: Path, manifest: dict[str, Any], machine: OrchestrationStateMachine, phase: Phase) -> None:
    if machine.active_phase is phase:
        return
    if machine.next_phase is not phase:
        raise OrchestrationError(f"cannot begin {phase.value} from {machine.state}")
    change = machine.start_phase(phase)
    _save_machine(run_dir, manifest, machine)
    _event_store(run_dir).append("PHASE_STARTED", change.to_dict())


def _validate_phase(run_dir: Path, manifest: dict[str, Any], machine: OrchestrationStateMachine, phase: Phase) -> None:
    change = machine.validate_phase(phase)
    closed: list[str] = []
    rework_dir = run_dir / "orchestration" / "rework-requests"
    for path in sorted(rework_dir.glob("*.json")) if rework_dir.is_dir() else []:
        payload = _load_json(path, f"rework request {path.name}")
        if not isinstance(payload, dict) or payload.get("status") != "OPEN":
            continue
        request = payload.get("request")
        if not isinstance(request, dict) or request.get("target_phase") != phase.value:
            continue
        payload["status"] = "RESOLVED"
        payload["closed_at"] = _now()
        atomic_write_json(path, payload)
        closed.append(str(request.get("request_id") or path.stem))
    _save_machine(run_dir, manifest, machine)
    _event_store(run_dir).append("PHASE_VALIDATED", {**change.to_dict(), "closed_rework_request_ids": closed})


def _try_batch_gate(run_dir: Path, phase: str) -> tuple[bool, str]:
    try:
        _quiet(validate_batch_artifacts, run_dir, phase, use_cache=False)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _ensure_role_task(
    run_dir: Path,
    manifest: dict[str, Any],
    role: AgentRole,
    owner_key: str | None = None,
    *,
    generation_fingerprint: bool = False,
) -> AgentTask:
    pending = _pending_task(manifest, role, owner_key)
    if pending:
        entry = manifest["tasks"][pending.task_id]
        inputs_current = _task_inputs_still_current(run_dir, pending, entry)
        current = (
            _generation_task_fingerprint(run_dir)
            if generation_fingerprint
            else fingerprint(_current_task_source_paths(run_dir, pending))
        )
        if (
            inputs_current
            and current == pending.source_fingerprint
            and (
                pending.agent_role is not AgentRole.REVIEWER
                or review_source_fingerprint(run_dir) == entry.get("review_input_fingerprint")
            )
        ):
            return pending
        if entry.get("status") == "CLAIMED":
            raise OrchestrationError(
                f"claimed task {pending.task_id} source/contract changed while execution may have side effects; "
                "resume and reconcile that execution, or explicitly agent-release only after confirming no side effects"
            )
        entry["status"] = "INVALIDATED"
        entry["invalidated_reason"] = "task source changed before submission"
        _save_manifest(run_dir, manifest)
        _event_store(run_dir).append(
            "TASK_INVALIDATED",
            {"reason": "source changed before submission", "replacement_attempt": pending.attempt + 1},
            task_id=pending.task_id,
        )
    return _create_task(
        run_dir, manifest, role, owner_key=owner_key, force_generation_fingerprint=generation_fingerprint
    )


def _ensure_case_tasks(run_dir: Path, manifest: dict[str, Any]) -> None:
    order: list[str] = []
    generation_fp = _generation_task_fingerprint(run_dir)
    for point in plan_groups(run_dir):
        success = _latest_success_entry(run_dir, manifest, AgentRole.CASE_WORKER, point)
        if success and (
            success[0].source_fingerprint != generation_fp
            or not _task_inputs_still_current(run_dir, success[0], success[1])
        ):
            manifest["tasks"][success[0].task_id]["status"] = "INVALIDATED"
            manifest["tasks"][success[0].task_id]["invalidated_reason"] = (
                "generation source or frozen output contract changed"
            )
            success = None
        task = success[0] if success else _ensure_role_task(
            run_dir, manifest, AgentRole.CASE_WORKER, point, generation_fingerprint=True
        )
        order.append(task.task_id)
    manifest["case_task_order"] = order
    _save_manifest(run_dir, manifest)


def _successful_case_task_ids(manifest: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for task_id in manifest.get("case_task_order", []):
        entry = manifest.get("tasks", {}).get(task_id, {})
        if entry.get("status") == "SUCCEEDED":
            result.append(task_id)
    return result


def _all_case_workers_ready(run_dir: Path, manifest: Mapping[str, Any]) -> bool:
    groups = plan_groups(run_dir)
    current = _generation_task_fingerprint(run_dir)
    return all(
        (latest := _latest_success_entry(run_dir, manifest, AgentRole.CASE_WORKER, point))
        and latest[0].source_fingerprint == current
        and _task_inputs_still_current(run_dir, latest[0], latest[1])
        for point in groups
    )


def _aggregate_ready_workers(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    payloads: list[tuple[AgentTask, Path, Path]] = []
    order: list[str] = []
    for point in plan_groups(run_dir):
        latest = _latest_success_entry(run_dir, manifest, AgentRole.CASE_WORKER, point)
        if not latest:
            raise OrchestrationError(f"missing successful Case Worker for {point}")
        task, _ = latest
        order.append(task.task_id)
        payloads.append(
            (
                task,
                _accepted_output(run_dir, task, "function_cases.json"),
                _accepted_output(run_dir, task, "case-traceability.json"),
            )
        )
    manifest["case_task_order"] = order
    summary = aggregate_case_workers(run_dir, payloads)
    _save_manifest(run_dir, manifest)
    _event_store(run_dir).append("CASE_WORKERS_MERGED", summary)
    return summary


def _active_dispatch_wave(
    manifest: Mapping[str, Any],
) -> tuple[tuple[str, str], list[tuple[str, Mapping[str, Any]]]] | None:
    active: dict[tuple[str, str], list[tuple[str, Mapping[str, Any]]]] = {}
    for task_id, entry in manifest["tasks"].items():
        if entry.get("status") not in {"PENDING", "CLAIMED"}:
            continue
        wave = entry.get("dispatch_wave")
        if not isinstance(wave, Mapping):
            continue
        wave_id = str(wave.get("wave_id") or "")
        coordinator_id = str(wave.get("coordinator_id") or "")
        if not wave_id or not coordinator_id:
            raise OrchestrationError(f"task {task_id} has an invalid dispatch_wave reservation")
        active.setdefault((wave_id, coordinator_id), []).append((task_id, entry))
    if len(active) > 1:
        raise OrchestrationError(
            "multiple active dispatch waves detected; reconcile claimed tasks before dispatching more work"
        )
    return next(iter(active.items())) if active else None


def _case_task_order_key(
    manifest: Mapping[str, Any],
    task_id: str,
) -> tuple[int, int | str, str]:
    """Order Case tasks by the frozen plan-owner order, with a safe fallback."""

    order = manifest.get("case_task_order")
    if isinstance(order, list):
        for index, candidate in enumerate(order):
            if candidate == task_id:
                return (0, index, task_id)
    return (1, task_id, task_id)


def _ordered_wave_task_ids(
    manifest: Mapping[str, Any],
    task_ids: Iterable[str],
) -> list[str]:
    return sorted(task_ids, key=lambda task_id: _case_task_order_key(manifest, task_id))


def _require_complete_case_wave_claims(
    manifest: Mapping[str, Any],
    task: AgentTask,
    entry: Mapping[str, Any],
    claim: AgentClaim,
) -> None:
    """Block Case submission until every reserved member of its wave is claimed.

    A wave is one scheduling unit in both parallel and serial-fallback modes.
    Allowing its first executor to submit while peers were never claimed lets
    orchestration advance on a partially dispatched wave.  A peer that was
    explicitly released after a full-wave decision is handled by the later
    status-aware barrier instead of being mistaken for an unclaimed member.
    """

    if task.agent_role is not AgentRole.CASE_WORKER:
        return
    reservation = entry.get("dispatch_wave")
    if not isinstance(reservation, Mapping):
        raise OrchestrationError(
            f"Case Worker {task.task_id} has no durable dispatch wave reservation"
        )
    wave_id = str(reservation.get("wave_id") or "")
    coordinator_id = str(reservation.get("coordinator_id") or "")
    if (wave_id, coordinator_id) != (claim.wave_id, claim.coordinator_id):
        raise OrchestrationError(
            f"Case Worker {task.task_id} claim identity differs from its dispatch wave"
        )
    pending: list[str] = []
    for candidate_id, candidate_entry in manifest["tasks"].items():
        candidate_wave = candidate_entry.get("dispatch_wave")
        if not isinstance(candidate_wave, Mapping):
            continue
        if (
            str(candidate_wave.get("wave_id") or "") == wave_id
            and str(candidate_wave.get("coordinator_id") or "") == coordinator_id
            and candidate_entry.get("status") == "PENDING"
            and not _case_wave_release_matches(
                candidate_entry,
                task_id=candidate_id,
                wave_id=wave_id,
                coordinator_id=coordinator_id,
            )
        ):
            pending.append(candidate_id)
    if pending:
        raise OrchestrationError(
            "Case Worker cannot submit before the entire dispatch wave is claimed; "
            f"wave_id={wave_id!r}, pending_task_ids={_ordered_wave_task_ids(manifest, pending)}"
        )


def _case_wave_release_matches(
    entry: Mapping[str, Any],
    *,
    task_id: str,
    wave_id: str,
    coordinator_id: str,
) -> bool:
    """Return whether a pending Case peer was explicitly and safely released.

    Released peers intentionally retain their reservation while another member
    of the wave is still claimed.  That prevents a second coordinator from
    dispatching replacement work before the wave's control result is accepted,
    but means the ordinary claim-all barrier must distinguish them from members
    that were never claimed.
    """

    if entry.get("status") != "PENDING" or entry.get("claim") is not None:
        return False
    history = entry.get("claim_history")
    if not isinstance(history, list) or not history:
        return False
    latest = history[-1]
    if (
        not isinstance(latest, Mapping)
        or latest.get("no_side_effects_confirmed") is not True
        or not isinstance(latest.get("released_at"), str)
        or not str(latest.get("released_at") or "").strip()
        or not isinstance(latest.get("reason"), str)
        or not str(latest.get("reason") or "").strip()
    ):
        return False
    try:
        released_claim = AgentClaim.from_dict(latest.get("claim"))
    except (TypeError, ValueError):
        return False
    return (
        released_claim.task_id == task_id
        and released_claim.wave_id == wave_id
        and released_claim.coordinator_id == coordinator_id
    )


def _case_wave_members(
    manifest: Mapping[str, Any],
    task: AgentTask,
    entry: Mapping[str, Any],
    claim: AgentClaim,
) -> tuple[str, str, list[tuple[str, Mapping[str, Any]]]]:
    if task.agent_role is not AgentRole.CASE_WORKER:
        return "", "", []
    reservation = entry.get("dispatch_wave")
    if not isinstance(reservation, Mapping):
        raise OrchestrationError(
            f"Case Worker {task.task_id} has no durable dispatch wave reservation"
        )
    wave_id = str(reservation.get("wave_id") or "")
    coordinator_id = str(reservation.get("coordinator_id") or "")
    if (wave_id, coordinator_id) != (claim.wave_id, claim.coordinator_id):
        raise OrchestrationError(
            f"Case Worker {task.task_id} claim identity differs from its dispatch wave"
        )
    members: list[tuple[str, Mapping[str, Any]]] = []
    for candidate_id, candidate_entry in manifest["tasks"].items():
        candidate_wave = candidate_entry.get("dispatch_wave")
        if not isinstance(candidate_wave, Mapping):
            continue
        if (
            str(candidate_wave.get("wave_id") or "") == wave_id
            and str(candidate_wave.get("coordinator_id") or "") == coordinator_id
        ):
            members.append((candidate_id, candidate_entry))
    members.sort(key=lambda item: _case_task_order_key(manifest, item[0]))
    if task.task_id not in {candidate_id for candidate_id, _ in members}:
        raise OrchestrationError(
            f"Case Worker {task.task_id} is missing from its durable dispatch wave"
        )
    return wave_id, coordinator_id, members


def _validate_case_wave_result_barrier(
    manifest: Mapping[str, Any],
    task: AgentTask,
    entry: Mapping[str, Any],
    claim: AgentClaim,
    status: TaskStatus,
) -> None:
    """Enforce one deterministic decision for the whole frozen Case wave."""

    if task.agent_role is not AgentRole.CASE_WORKER:
        return
    wave_id, coordinator_id, members = _case_wave_members(
        manifest, task, entry, claim
    )
    member_ids = [candidate_id for candidate_id, _ in members]
    current_index = member_ids.index(task.task_id)
    if status is TaskStatus.SUCCEEDED:
        invalid: list[str] = []
        for index, (candidate_id, candidate_entry) in enumerate(members):
            if candidate_id == task.task_id:
                continue
            expected_status = "SUCCEEDED" if index < current_index else "CLAIMED"
            if candidate_entry.get("status") != expected_status:
                invalid.append(
                    f"{candidate_id}:{candidate_entry.get('status')}"
                )
        if invalid:
            raise OrchestrationError(
                "Case Worker success results must be submitted in frozen wave order "
                "after the entire wave was claimed; "
                f"wave_id={wave_id!r}, conflicts={invalid}"
            )
        return

    if status not in {
        TaskStatus.NEEDS_REWORK,
        TaskStatus.FAILED,
        TaskStatus.EXTERNAL_BLOCKED,
    }:
        return
    unreleased: list[str] = []
    prior_terminal: list[str] = []
    for candidate_id, candidate_entry in members:
        if candidate_id == task.task_id:
            continue
        candidate_status = str(candidate_entry.get("status") or "")
        if _case_wave_release_matches(
            candidate_entry,
            task_id=candidate_id,
            wave_id=wave_id,
            coordinator_id=coordinator_id,
        ):
            continue
        if candidate_status in {
            "SUCCEEDED",
            "FAILED",
            "NEEDS_REWORK",
            "EXTERNAL_BLOCKED",
            "INVALIDATED",
        }:
            prior_terminal.append(f"{candidate_id}:{candidate_status}")
        else:
            unreleased.append(f"{candidate_id}:{candidate_status}")
    if prior_terminal:
        raise OrchestrationError(
            "Case wave control result cannot follow an already submitted peer result; "
            "the coordinator must collect the full wave before deciding; "
            f"wave_id={wave_id!r}, prior_results={prior_terminal}"
        )
    if unreleased:
        raise OrchestrationError(
            "Case wave control result requires every peer claim to be explicitly released "
            "with confirmed no side effects before submission; "
            f"wave_id={wave_id!r}, unreleased={unreleased}"
        )


def _clear_case_wave_reservations(
    manifest: dict[str, Any],
    task: AgentTask,
    entry: Mapping[str, Any],
    claim: AgentClaim,
) -> None:
    """Close a completed/control wave so retries or future waves cannot inherit it."""

    if task.agent_role is not AgentRole.CASE_WORKER:
        return
    _, _, members = _case_wave_members(manifest, task, entry, claim)
    for candidate_id, _ in members:
        manifest["tasks"][candidate_id]["dispatch_wave"] = None


def _runnable_tasks(run_dir: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    active_wave = _active_dispatch_wave(manifest)
    if active_wave:
        _, entries = active_wave
        selected = [
            AgentTask.from_dict(entry["task"])
            for _, entry in entries
            if entry.get("status") == "PENDING"
        ]
        selected.sort(key=lambda task: _case_task_order_key(manifest, task.task_id))
        return [task.to_dict() for task in selected]
    if any(entry.get("status") == "CLAIMED" for entry in manifest["tasks"].values()):
        raise OrchestrationError("claimed task has no active dispatch wave reservation")
    pending = [
        AgentTask.from_dict(entry["task"])
        for entry in manifest["tasks"].values()
        if entry.get("status") == "PENDING"
    ]
    pending.sort(key=lambda task: (list(AgentRole).index(task.agent_role), task.task_id))
    case_tasks = [task for task in pending if task.agent_role is AgentRole.CASE_WORKER]
    case_tasks.sort(key=lambda task: _case_task_order_key(manifest, task.task_id))
    non_case = [task for task in pending if task.agent_role is not AgentRole.CASE_WORKER]
    if non_case:
        selected = non_case[:1]
    elif case_tasks:
        config = _config(run_dir)
        limit = 1 if len(plan_groups(run_dir)) < config.case_parallel_threshold else config.max_case_workers
        selected = case_tasks[:limit]
    else:
        selected = []
    return [task.to_dict() for task in selected]


def _claim_identity(claim: AgentClaim) -> tuple[str, str]:
    return claim.executor_kind.value, claim.executor_id


def _validate_reviewer_execution_identity(
    manifest: Mapping[str, Any],
    reviewer_claim: AgentClaim,
) -> None:
    formal_kinds = {
        ExecutorKind.CODEBUDDY_SUBAGENT,
        ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK,
    }
    if reviewer_claim.executor_kind not in formal_kinds:
        raise OrchestrationError(
            "formal Reviewer execution must use an authenticated sub-agent or a supervisor-authorized "
            "isolated fallback claim; "
            f"diagnostic executor kind {reviewer_claim.executor_kind.value!r} cannot approve delivery"
        )
    reviewer_identity = _claim_identity(reviewer_claim)
    missing: list[str] = []
    conflicts: list[str] = []
    unauthenticated_generators: list[str] = []
    for task_id, entry in manifest["tasks"].items():
        raw_task = entry.get("task", {})
        if (
            entry.get("status") != "SUCCEEDED"
            or raw_task.get("agent_role") == AgentRole.REVIEWER.value
        ):
            continue
        raw_claim = entry.get("claim")
        if not isinstance(raw_claim, Mapping):
            missing.append(task_id)
            continue
        try:
            generator_claim = AgentClaim.from_dict(raw_claim)
        except (TypeError, ValueError):
            missing.append(task_id)
            continue
        if generator_claim.executor_kind not in formal_kinds:
            unauthenticated_generators.append(task_id)
        if _claim_identity(generator_claim) == reviewer_identity:
            conflicts.append(task_id)
    if missing:
        raise OrchestrationError(
            "Reviewer independence cannot be proven because successful generator tasks have no valid "
            f"execution claim: {missing}"
        )
    if unauthenticated_generators:
        raise OrchestrationError(
            "formal Review is blocked because successful generators used unauthenticated diagnostic "
            f"executor kinds: {unauthenticated_generators}"
        )
    if conflicts:
        raise OrchestrationError(
            "Reviewer executor identity must differ from every successful generator executor; "
            f"conflicts={conflicts}"
        )


def _validate_claim_matches_entry(
    task: AgentTask,
    entry: Mapping[str, Any],
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
        raise OrchestrationError(
            f"execution claim {claim.execution_id} no longer matches frozen task {task.task_id}"
        )
    link = entry.get("page_probe_receipt")
    if task.agent_role is AgentRole.DISCOVERY:
        if not isinstance(link, Mapping) or (
            claim.page_probe_receipt_id != link.get("receipt_id")
            or claim.page_probe_receipt_fingerprint != link.get("receipt_fingerprint")
            or list(claim.approved_page_mcp_tools)
            != link.get("approved_page_mcp_tools")
            or claim.execution_id != link.get("execution_id")
            or claim.coordinator_id != link.get("coordinator_id")
            or claim.source_fingerprint != link.get("source_fingerprint")
            or link.get("status") != "ACTIVE"
        ):
            raise OrchestrationError(
                f"Discovery claim {claim.execution_id} no longer matches its page probe receipt"
            )
    elif (
        claim.page_probe_receipt_id is not None
        or claim.page_probe_receipt_fingerprint is not None
        or claim.approved_page_mcp_tools
    ):
        raise OrchestrationError("non-Discovery claim cannot carry page probe authority")


def commit_page_probe_receipt(
    run_dir: Path | str,
    task_id: str,
    *,
    execution_id: str,
    coordinator_id: str,
    session_sha256: str,
    transcript_sha256: str,
    record_ids: Sequence[str],
    evidence_paths: Sequence[str],
) -> dict[str, Any]:
    """Bind coordinator preflight records to one future Discovery execution."""

    root = Path(run_dir).resolve()
    with exclusive_process_lock(root / "orchestration" / ".orchestrator.lock"):
        _initialize_orchestration_unlocked(root)
        manifest = _load_manifest(root)
        entry = manifest["tasks"].get(task_id)
        if not isinstance(entry, dict):
            raise OrchestrationError(f"unknown task ID: {task_id}")
        task = AgentTask.from_dict(entry.get("task"))
        if task.agent_role is not AgentRole.DISCOVERY:
            raise OrchestrationError("page-probe-commit applies only to Discovery tasks")
        machine = _machine(manifest)
        if machine.state != "DISCOVERY_RUNNING" or machine.active_phase is not Phase.DISCOVERY:
            raise OrchestrationError(
                "page-probe-commit requires the active DISCOVERY_RUNNING phase"
            )
        if entry.get("status") != "PENDING":
            raise OrchestrationError(
                f"page-probe-commit requires a pending task; current status={entry.get('status')}"
            )
        if not _task_inputs_still_current(root, task, entry):
            raise OrchestrationError("Discovery frozen inputs changed before page probe commit")
        if fingerprint(_current_task_source_paths(root, task)) != task.source_fingerprint:
            raise OrchestrationError("Discovery source changed before page probe commit")
        for other_entry in manifest["tasks"].values():
            raw_claim = other_entry.get("claim")
            if isinstance(raw_claim, Mapping) and raw_claim.get("execution_id") == execution_id:
                raise OrchestrationError(
                    f"execution_id {execution_id} is already bound to an active claim"
                )
            history = other_entry.get("claim_history")
            if isinstance(history, list) and any(
                isinstance(item, Mapping)
                and isinstance(item.get("claim"), Mapping)
                and item["claim"].get("execution_id") == execution_id
                for item in history
            ):
                raise OrchestrationError(
                    f"execution_id {execution_id} was released and cannot bind a page probe"
                )
        try:
            events = _event_store(root)
            event_rows = events.read_events()
            task_created_times = [
                str(event.get("occurred_at"))
                for event in event_rows
                if event.get("task_id") == task.task_id
                and event.get("event_type") in {"TASK_CREATED", "AUDIT_TASK_RECOVERED"}
            ]
            if len(task_created_times) != 1:
                raise PageProbeError(
                    "Discovery task must have exactly one durable creation timestamp"
                )
            receipt = create_page_probe_receipt(
                _project_root(root),
                root,
                task,
                run_id=str(manifest["run_id"]),
                batch_id=str(manifest["batch_id"]),
                execution_id=execution_id,
                coordinator_id=coordinator_id,
                session_sha256=session_sha256,
                transcript_sha256=transcript_sha256,
                record_ids=record_ids,
                evidence_paths=evidence_paths,
                not_before=task_created_times[0],
            )
            registry = page_probe_event_registry(event_rows)
        except (PageProbeError, TypeError, ValueError) as exc:
            raise OrchestrationError(f"page probe proof is invalid: {exc}") from exc

        existing_state = registry.get(receipt.receipt_id)
        if existing_state is not None:
            existing_receipt: PageProbeReceipt = existing_state["receipt"]
            if existing_receipt != receipt:
                raise OrchestrationError("page probe receipt ID conflicts with prior reservation")
            if existing_state["tombstoned_sequence"] is not None:
                raise OrchestrationError("page probe receipt was released and cannot be reused")
            if existing_state["committed_sequence"] is None:
                raise OrchestrationError("page probe reservation recovery did not complete")
            try:
                validate_project_record_consumption(_project_root(root), root, receipt)
            except PageProbeError as exc:
                raise OrchestrationError(
                    f"page probe project-level consumption binding is stale: {exc}"
                ) from exc
            current = entry.get("page_probe_receipt")
            if not isinstance(current, Mapping) or current.get("receipt_id") != receipt.receipt_id:
                raise OrchestrationError("page probe receipt projection is missing after recovery")
            return {
                **_orchestration_status_unlocked(root, manifest),
                "page_probe_receipt": receipt.to_dict(),
            }

        candidate_ids = {str(record["record_id"]) for record in receipt.records}
        for state in registry.values():
            prior: PageProbeReceipt = state["receipt"]
            if prior.execution_id == receipt.execution_id:
                raise OrchestrationError(
                    "one execution_id cannot bind multiple page probe receipts"
                )
            if candidate_ids & {str(record["record_id"]) for record in prior.records}:
                raise OrchestrationError("page probe record replay is forbidden")
        current = entry.get("page_probe_receipt")
        if current is not None:
            raise OrchestrationError("pending Discovery task already has a committed page probe receipt")

        try:
            # This project-level reservation precedes the run-local event.  It
            # is idempotent for the same immutable receipt and permanently
            # rejects replay by another run, even after a release/tombstone.
            reserve_project_record_consumption(_project_root(root), root, receipt)
        except PageProbeError as exc:
            raise OrchestrationError(f"page probe record replay is forbidden: {exc}") from exc
        events.append(
            "PAGE_PROBE_RECORDS_RESERVED",
            {"receipt": receipt.to_dict()},
            task_id=task.task_id,
            event_id=f"PAGE-PROBE-RESERVE-{receipt.receipt_id}",
        )
        target = page_probe_receipt_path(root, receipt.receipt_id)
        if target.exists():
            raise OrchestrationError("unregistered page probe receipt file already exists")
        atomic_write_json(target, receipt.to_dict())
        entry["page_probe_receipt"] = _page_probe_link(receipt, "COMMITTED")
        _save_manifest(root, manifest)
        events.append(
            "PAGE_PROBE_COMMITTED",
            receipt_event_payload(receipt),
            task_id=task.task_id,
            event_id=f"PAGE-PROBE-COMMIT-{receipt.receipt_id}",
        )
        return {
            **_orchestration_status_unlocked(root, manifest),
            "page_probe_receipt": receipt.to_dict(),
        }


def claim_agent_task(
    run_dir: Path | str,
    task_id: str,
    *,
    execution_id: str,
    coordinator_id: str,
    executor_id: str,
    executor_kind: str | ExecutorKind,
    wave_id: str,
    page_probe_receipt_id: str | None = None,
    page_probe_receipt_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Atomically reserve one runnable task for exactly one declared executor."""

    root = Path(run_dir).resolve()
    created: AgentClaim | None = None
    response: dict[str, Any] | None = None
    with exclusive_process_lock(root / "orchestration" / ".orchestrator.lock"):
        _initialize_orchestration_unlocked(root)
        manifest = _load_manifest(root)
        entry = manifest["tasks"].get(task_id)
        if not isinstance(entry, dict):
            raise OrchestrationError(f"unknown task ID: {task_id}")
        task = AgentTask.from_dict(entry["task"])
        machine = _machine(manifest)
        expected_phase = ROLE_PHASE[task.agent_role]
        expected_state = f"{expected_phase.value.upper()}_RUNNING"
        if machine.state != expected_state or machine.active_phase is not expected_phase:
            raise OrchestrationError(
                f"task {task_id} can only be claimed in {expected_state}; current workflow state is "
                f"{machine.state}"
            )
        try:
            requested_kind = (
                executor_kind if isinstance(executor_kind, ExecutorKind) else ExecutorKind(executor_kind)
            )
        except ValueError as exc:
            raise OrchestrationError(f"unsupported executor_kind: {executor_kind}") from exc
        if requested_kind is ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK:
            raise OrchestrationError(
                "codebuddy-isolated-fallback claims are created only by agent-dispatch-failed "
                "after the bounded native Agent retry is exhausted"
            )
        probe_receipt: PageProbeReceipt | None = None
        if task.agent_role is AgentRole.DISCOVERY:
            if not page_probe_receipt_id or not page_probe_receipt_fingerprint:
                raise OrchestrationError(
                    "Discovery claim requires a committed page probe receipt id and fingerprint"
                )
            link = entry.get("page_probe_receipt")
            if not isinstance(link, Mapping):
                raise OrchestrationError("Discovery task has no committed page probe receipt")
            if (
                link.get("receipt_id") != page_probe_receipt_id
                or link.get("receipt_fingerprint") != page_probe_receipt_fingerprint
                or link.get("execution_id") != execution_id
                or link.get("coordinator_id") != coordinator_id
                or link.get("source_fingerprint") != task.source_fingerprint
                or link.get("status") not in {"COMMITTED", "ACTIVE"}
            ):
                raise OrchestrationError(
                    "Discovery claim does not match the pre-bound page probe receipt"
                )
            try:
                probe_receipt = load_page_probe_receipt(
                    root,
                    page_probe_receipt_id,
                    expected_fingerprint=page_probe_receipt_fingerprint,
                )
                probe_registry = page_probe_event_registry(
                    _event_store(root).read_events()
                )
                validate_project_record_consumption(
                    _project_root(root), root, probe_receipt
                )
            except PageProbeError as exc:
                raise OrchestrationError(f"Discovery page probe receipt is invalid: {exc}") from exc
            probe_state = probe_registry.get(probe_receipt.receipt_id)
            if (
                probe_state is None
                or probe_state["committed_sequence"] is None
                or probe_state["tombstoned_sequence"] is not None
            ):
                raise OrchestrationError(
                    "Discovery page probe receipt is uncommitted or permanently consumed"
                )
            if (
                probe_receipt.task_id != task.task_id
                or probe_receipt.execution_id != execution_id
                or probe_receipt.coordinator_id != coordinator_id
                or probe_receipt.source_fingerprint != task.source_fingerprint
            ):
                raise OrchestrationError("Discovery page probe receipt binding is stale")
            for event in _event_store(root).read_events():
                if event.get("event_type") not in {"TASK_CLAIMED", "AUDIT_CLAIM_RECOVERED"}:
                    continue
                payload = event.get("payload")
                raw_claim = payload.get("claim") if isinstance(payload, Mapping) else None
                if not isinstance(raw_claim, Mapping):
                    continue
                if raw_claim.get("page_probe_receipt_id") != probe_receipt.receipt_id:
                    continue
                if (
                    raw_claim.get("task_id") != task.task_id
                    or raw_claim.get("execution_id") != execution_id
                ):
                    raise OrchestrationError("page probe receipt was already activated by another claim")
        elif page_probe_receipt_id is not None or page_probe_receipt_fingerprint is not None:
            raise OrchestrationError("only Discovery claims may bind a page probe receipt")
        for other_task_id, other_entry in manifest["tasks"].items():
            raw_other_claim = other_entry.get("claim")
            if (
                other_task_id != task_id
                and isinstance(raw_other_claim, Mapping)
                and raw_other_claim.get("execution_id") == execution_id
            ):
                raise OrchestrationError(
                    f"execution_id {execution_id} is already bound to task {other_task_id}"
                )
            history = other_entry.get("claim_history")
            if not isinstance(history, list):
                continue
            if any(
                isinstance(item, Mapping)
                and isinstance(item.get("claim"), Mapping)
                and item["claim"].get("execution_id") == execution_id
                for item in history
            ):
                raise OrchestrationError(
                    f"execution_id {execution_id} was already released and cannot be reused"
                )
        if entry.get("status") == "CLAIMED":
            existing = AgentClaim.from_dict(entry.get("claim"))
            if (
                existing.execution_id == execution_id
                and existing.coordinator_id == coordinator_id
                and existing.executor_id == executor_id
                and existing.executor_kind is requested_kind
                and existing.wave_id == wave_id
                and existing.page_probe_receipt_id == page_probe_receipt_id
                and existing.page_probe_receipt_fingerprint == page_probe_receipt_fingerprint
            ):
                _validate_claim_matches_entry(task, entry, existing)
                if task.agent_role is AgentRole.REVIEWER:
                    _validate_reviewer_execution_identity(manifest, existing)
                created = existing
            else:
                raise OrchestrationError(
                    f"task {task_id} is already claimed by execution {existing.execution_id} "
                    f"under coordinator {existing.coordinator_id}"
                )
        elif entry.get("status") != "PENDING":
            raise OrchestrationError(f"task {task_id} is not pending: {entry.get('status')}")
        else:
            active_wave = _active_dispatch_wave(manifest)
            reservation = entry.get("dispatch_wave")
            if active_wave:
                (active_wave_id, active_coordinator_id), _ = active_wave
                if (wave_id, coordinator_id) != (active_wave_id, active_coordinator_id):
                    raise OrchestrationError(
                        "another dispatch wave/coordinator already owns the active task wave"
                    )
                if not isinstance(reservation, Mapping):
                    raise OrchestrationError(f"task {task_id} is outside the active dispatch wave")
            else:
                runnable = _runnable_tasks(root, manifest)
                runnable_ids = [str(item["task_id"]) for item in runnable]
                if task_id not in runnable_ids:
                    raise OrchestrationError(
                        f"task {task_id} is not in the current runnable wave: {runnable_ids}"
                    )
                reserved_at = _now()
                for runnable_id in runnable_ids:
                    manifest["tasks"][runnable_id]["dispatch_wave"] = {
                        "wave_id": wave_id,
                        "coordinator_id": coordinator_id,
                        "reserved_at": reserved_at,
                    }
                reservation = manifest["tasks"][task_id]["dispatch_wave"]
            if (
                str(reservation.get("wave_id")) != wave_id
                or str(reservation.get("coordinator_id")) != coordinator_id
            ):
                raise OrchestrationError(f"task {task_id} reservation identity does not match claim request")
            if not _task_inputs_still_current(root, task, entry):
                raise OrchestrationError(f"task {task_id} frozen inputs/contracts changed before claim")
            if task.agent_role in {AgentRole.CASE_WORKER, AgentRole.REVIEWER}:
                current_source = _generation_task_fingerprint(root)
            else:
                current_source = fingerprint(_current_task_source_paths(root, task))
            if current_source != task.source_fingerprint:
                raise OrchestrationError(f"task {task_id} source changed before claim")
            created = AgentClaim(
                schema_version="1.0.0",
                execution_id=execution_id,
                task_id=task.task_id,
                coordinator_id=coordinator_id,
                executor_id=executor_id,
                executor_kind=requested_kind,
                wave_id=wave_id,
                claimed_at=_now(),
                source_fingerprint=task.source_fingerprint,
                input_snapshot_fingerprint=str(entry.get("input_snapshot_fingerprint") or ""),
                task_packet_fingerprint=str(entry.get("task_packet_fingerprint") or ""),
                context_fingerprint=str(entry.get("context_fingerprint") or ""),
                page_probe_receipt_id=(
                    probe_receipt.receipt_id if probe_receipt is not None else None
                ),
                page_probe_receipt_fingerprint=(
                    probe_receipt.receipt_fingerprint if probe_receipt is not None else None
                ),
                approved_page_mcp_tools=(
                    probe_receipt.approved_mcp_tools if probe_receipt is not None else ()
                ),
            )
            if task.agent_role is AgentRole.REVIEWER:
                _validate_reviewer_execution_identity(manifest, created)
            entry["claim"] = created.to_dict()
            if probe_receipt is not None:
                entry["page_probe_receipt"] = _page_probe_link(probe_receipt, "ACTIVE")
            entry.setdefault("claim_history", [])
            entry["status"] = "CLAIMED"
            _save_manifest(root, manifest)
            _event_store(root).append(
                "TASK_CLAIMED",
                _claim_event_payload(created),
                task_id=task_id,
            )
        if created is not None:
            response = {
                **_orchestration_status_unlocked(root, manifest),
                "claim": created.to_dict(),
            }
    if created is None:
        raise OrchestrationError(f"task {task_id} claim did not complete")
    assert response is not None
    return response


def _fallback_authorization(
    task: AgentTask,
    claim: AgentClaim,
    *,
    failure_count: int,
    failure_reason: str,
) -> dict[str, Any]:
    content = {
        "schema_version": FALLBACK_AUTHORIZATION_VERSION,
        "task_id": task.task_id,
        "execution_id": claim.execution_id,
        "coordinator_id": claim.coordinator_id,
        "executor_id": claim.executor_id,
        "executor_kind": claim.executor_kind.value,
        "source_fingerprint": task.source_fingerprint,
        "input_snapshot_fingerprint": claim.input_snapshot_fingerprint,
        "task_packet_fingerprint": claim.task_packet_fingerprint,
        "context_fingerprint": claim.context_fingerprint,
        "failure_count": failure_count,
        "failure_reason": failure_reason,
        "authorized_at": _now(),
        "quality_gates_unchanged": True,
        "workspace_isolation_required": True,
        "review_required": True,
        "delivery_single_writer": True,
    }
    return {**content, "authorization_fingerprint": canonical_fingerprint(content)}


def _validate_fallback_authorization(
    task: AgentTask,
    entry: Mapping[str, Any],
    claim: AgentClaim,
) -> None:
    authorization = entry.get("fallback_authorization")
    if not isinstance(authorization, Mapping):
        raise OrchestrationError(
            f"fallback task {task.task_id} has no deterministic fallback authorization"
        )
    expected_fields = {
        "schema_version", "task_id", "execution_id", "coordinator_id", "executor_id",
        "executor_kind", "source_fingerprint", "input_snapshot_fingerprint",
        "task_packet_fingerprint", "context_fingerprint", "failure_count",
        "failure_reason", "authorized_at", "quality_gates_unchanged",
        "workspace_isolation_required", "review_required", "delivery_single_writer",
        "authorization_fingerprint",
    }
    if set(authorization) != expected_fields:
        raise OrchestrationError("fallback authorization field set is invalid")
    content = dict(authorization)
    actual_fingerprint = content.pop("authorization_fingerprint", None)
    if actual_fingerprint != canonical_fingerprint(content):
        raise OrchestrationError("fallback authorization fingerprint is stale")
    expected = {
        "schema_version": FALLBACK_AUTHORIZATION_VERSION,
        "task_id": task.task_id,
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
    mismatched = [key for key, value in expected.items() if authorization.get(key) != value]
    if mismatched:
        raise OrchestrationError(
            f"fallback authorization does not match its frozen task/claim: {mismatched}"
        )
    if not isinstance(authorization.get("failure_count"), int) or authorization["failure_count"] <= AGENT_DISPATCH_RETRY_LIMIT:
        raise OrchestrationError("fallback authorization was issued before bounded Agent retries were exhausted")
    if not isinstance(authorization.get("failure_reason"), str) or not authorization["failure_reason"].strip():
        raise OrchestrationError("fallback authorization has no concrete Agent dispatch failure")


def record_agent_dispatch_failure(
    run_dir: Path | str,
    task_id: str,
    *,
    execution_id: str,
    coordinator_id: str,
    reason: str,
    fallback_executor_id: str,
) -> dict[str, Any]:
    """Record an unavailable native Agent and automatically authorize safe continuation.

    The first failure keeps the exact claim for one bounded retry.  A subsequent
    failure atomically converts that same execution to an isolated fallback
    claim, preserving Discovery's page-probe receipt and every frozen input.
    Conversion is allowed only before a physical sub-agent binding, output, or
    result exists, so it cannot steal work from a running Agent.
    """

    if not isinstance(reason, str) or not reason.strip() or reason != reason.strip():
        raise OrchestrationError("agent-dispatch-failed requires a concrete trimmed reason")
    root = Path(run_dir).resolve()
    with exclusive_process_lock(root / "orchestration" / ".orchestrator.lock"):
        _initialize_orchestration_unlocked(root)
        manifest = _load_manifest(root)
        entry = manifest.get("tasks", {}).get(task_id)
        if not isinstance(entry, dict) or entry.get("status") != "CLAIMED":
            raise OrchestrationError(f"task {task_id} is not claimed")
        task = AgentTask.from_dict(entry.get("task"))
        claim = AgentClaim.from_dict(entry.get("claim"))
        if claim.execution_id != execution_id or claim.coordinator_id != coordinator_id:
            raise OrchestrationError("dispatch failure identity does not match the active claim")
        if claim.executor_kind is ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK:
            _validate_fallback_authorization(task, entry, claim)
            return {
                **_orchestration_status_unlocked(root, manifest),
                "dispatch_action": "RUN_ISOLATED_FALLBACK",
                "claim": claim.to_dict(),
                "fallback_authorization": dict(entry["fallback_authorization"]),
            }
        if claim.executor_kind is not ExecutorKind.CODEBUDDY_SUBAGENT:
            raise OrchestrationError(
                "only a failed codebuddy-subagent dispatch can enter formal fallback"
            )
        if entry.get("result_path") or entry.get("promotion_ids"):
            raise OrchestrationError("cannot degrade a claim after result storage or promotion")
        output_root = root / "artifacts" / "agent-work" / task.agent_role.value / task.task_id / "output"
        output_files = [path for path in output_root.rglob("*") if path.is_file()] if output_root.is_dir() else []
        if output_files:
            raise OrchestrationError(
                "cannot degrade a claim after Agent output exists; reconcile the original execution"
            )
        binding = execution_binding_path(_project_root(root), claim.execution_id)
        if binding.exists():
            raise OrchestrationError(
                "cannot degrade a claim after a physical sub-agent transcript was bound"
            )
        failures = entry.setdefault("dispatch_failures", [])
        if not isinstance(failures, list):
            raise OrchestrationError("dispatch_failures must be an array")
        durable_failures = [
            dict(event["payload"])
            for event in _event_store(root).read_events()
            if event.get("task_id") == task_id
            and event.get("event_type") == "AGENT_DISPATCH_FAILED"
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("execution_id") == claim.execution_id
        ]
        if any(
            failure.get("sequence") != index
            for index, failure in enumerate(durable_failures, start=1)
        ):
            raise OrchestrationError("durable Agent dispatch failure sequence is invalid")
        if len(durable_failures) < len(failures):
            raise OrchestrationError(
                "manifest contains Agent dispatch failures without durable audit events"
            )
        if failures and failures != durable_failures[: len(failures)]:
            raise OrchestrationError("manifest Agent dispatch failure history conflicts with events")
        if len(durable_failures) > len(failures):
            # Event append precedes manifest persistence.  Recover a hard stop at
            # that exact boundary without counting the same dispatch twice.
            failures[:] = durable_failures

        def authorize_fallback(last_reason: str) -> dict[str, Any]:
            fallback_claim = AgentClaim.from_dict(
                {
                    **claim.to_dict(),
                    "executor_id": fallback_executor_id,
                    "executor_kind": ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK.value,
                }
            )
            authorization = _fallback_authorization(
                task,
                fallback_claim,
                failure_count=len(failures),
                failure_reason=last_reason,
            )
            entry["claim"] = fallback_claim.to_dict()
            entry["fallback_authorization"] = authorization
            _save_manifest(root, manifest)
            _event_store(root).append(
                "TASK_EXECUTOR_DEGRADED",
                {
                    "previous_claim": claim.to_dict(),
                    "claim": fallback_claim.to_dict(),
                    "fallback_authorization": authorization,
                },
                task_id=task_id,
            )
            return {
                **_orchestration_status_unlocked(root, manifest),
                "dispatch_action": "RUN_ISOLATED_FALLBACK",
                "claim": fallback_claim.to_dict(),
                "fallback_authorization": authorization,
            }

        if len(failures) > AGENT_DISPATCH_RETRY_LIMIT:
            return authorize_fallback(str(failures[-1].get("reason") or reason))
        failure = {
            "sequence": len(failures) + 1,
            "recorded_at": _now(),
            "execution_id": claim.execution_id,
            "executor_id": claim.executor_id,
            "reason": reason,
        }
        failures.append(failure)
        _event_store(root).append("AGENT_DISPATCH_FAILED", failure, task_id=task_id)
        if len(failures) <= AGENT_DISPATCH_RETRY_LIMIT:
            _save_manifest(root, manifest)
            return {
                **_orchestration_status_unlocked(root, manifest),
                "dispatch_action": "RETRY_NATIVE_AGENT",
                "remaining_native_retries": AGENT_DISPATCH_RETRY_LIMIT - len(failures) + 1,
                "claim": claim.to_dict(),
            }
        return authorize_fallback(reason)


def release_agent_claim(
    run_dir: Path | str,
    task_id: str,
    *,
    execution_id: str,
    coordinator_id: str,
    reason: str,
    confirm_no_side_effects: bool,
) -> dict[str, Any]:
    """Explicitly recover a claim only after an operator confirms no side effects."""

    if not confirm_no_side_effects:
        raise OrchestrationError("agent-release requires --confirm-no-side-effects")
    if not isinstance(reason, str) or not reason.strip() or reason != reason.strip():
        raise OrchestrationError("agent-release requires a concrete reason without surrounding whitespace")
    root = Path(run_dir).resolve()
    response: dict[str, Any]
    with exclusive_process_lock(root / "orchestration" / ".orchestrator.lock"):
        _initialize_orchestration_unlocked(root)
        manifest = _load_manifest(root)
        entry = manifest["tasks"].get(task_id)
        if not isinstance(entry, dict) or entry.get("status") != "CLAIMED":
            raise OrchestrationError(f"task {task_id} is not claimed")
        claim = AgentClaim.from_dict(entry.get("claim"))
        if claim.execution_id != execution_id or claim.coordinator_id != coordinator_id:
            raise OrchestrationError(f"task {task_id} claim execution/coordinator does not match")
        task = AgentTask.from_dict(entry["task"])
        probe_receipt: PageProbeReceipt | None = None
        if task.agent_role is AgentRole.DISCOVERY:
            if not claim.page_probe_receipt_id or not claim.page_probe_receipt_fingerprint:
                raise OrchestrationError("Discovery claim has no page probe receipt to tombstone")
            try:
                probe_receipt = load_page_probe_receipt(
                    root,
                    claim.page_probe_receipt_id,
                    expected_fingerprint=claim.page_probe_receipt_fingerprint,
                )
                validate_project_record_consumption(
                    _project_root(root), root, probe_receipt
                )
            except PageProbeError as exc:
                raise OrchestrationError(f"cannot release stale page probe receipt: {exc}") from exc
        _rollback_task_promotions(root, task.task_id, claim.execution_id)
        output_root = (
            root / "artifacts" / "agent-work" / task.agent_role.value / task.task_id / "output"
        )
        if output_root.exists():
            shutil.rmtree(output_root)
        output_root.mkdir(parents=True, exist_ok=True)
        released_at = _now()
        history = entry.setdefault("claim_history", [])
        if not isinstance(history, list):
            raise OrchestrationError(f"task {task_id} claim_history must be an array")
        history.append(
            {
                "claim": claim.to_dict(),
                "released_at": released_at,
                "reason": reason,
                "no_side_effects_confirmed": True,
            }
        )
        if probe_receipt is not None:
            probe_history = entry.setdefault("page_probe_history", [])
            if not isinstance(probe_history, list):
                raise OrchestrationError("page_probe_history must be an array")
            if any(
                isinstance(item, Mapping)
                and item.get("receipt_id") == probe_receipt.receipt_id
                for item in probe_history
            ):
                raise OrchestrationError("page probe receipt was already tombstoned")
            probe_history.append(
                {
                    **_page_probe_link(probe_receipt, "TOMBSTONED"),
                    "released_at": released_at,
                    "release_reason": reason,
                }
            )
            entry["page_probe_receipt"] = None
        entry["claim"] = None
        entry["status"] = "PENDING"
        reservation = entry.get("dispatch_wave")
        released_reservations: list[str] = []
        wave_id = ""
        wave_coordinator = ""
        if isinstance(reservation, Mapping):
            wave_id = str(reservation.get("wave_id") or "")
            wave_coordinator = str(reservation.get("coordinator_id") or "")
            other_claimed = any(
                other_id != task_id
                and other_entry.get("status") == "CLAIMED"
                and isinstance(other_entry.get("dispatch_wave"), Mapping)
                and str(other_entry["dispatch_wave"].get("wave_id") or "") == wave_id
                and str(other_entry["dispatch_wave"].get("coordinator_id") or "") == wave_coordinator
                for other_id, other_entry in manifest["tasks"].items()
            )
            if not other_claimed:
                for other_id, other_entry in manifest["tasks"].items():
                    other_reservation = other_entry.get("dispatch_wave")
                    if (
                        other_entry.get("status") == "PENDING"
                        and isinstance(other_reservation, Mapping)
                        and str(other_reservation.get("wave_id") or "") == wave_id
                        and str(other_reservation.get("coordinator_id") or "") == wave_coordinator
                    ):
                        other_entry["dispatch_wave"] = None
                        released_reservations.append(other_id)
        _save_manifest(root, manifest)
        if probe_receipt is not None:
            _event_store(root).append(
                "PAGE_PROBE_TOMBSTONED",
                {
                    **receipt_event_payload(probe_receipt),
                    "released_at": released_at,
                    "reason": reason,
                },
                task_id=task_id,
                event_id=f"PAGE-PROBE-TOMBSTONE-{probe_receipt.receipt_id}",
            )
        _event_store(root).append(
            "TASK_CLAIM_RELEASED",
            {
                **_claim_event_payload(claim),
                "reason": reason,
                "no_side_effects_confirmed": True,
                "released_at": released_at,
                "released_wave_reservation_task_ids": _ordered_wave_task_ids(
                    manifest, released_reservations
                ),
            },
            task_id=task_id,
        )
        if released_reservations:
            _event_store(root).append(
                "DISPATCH_WAVE_RELEASED",
                {
                    "wave_id": wave_id,
                    "coordinator_id": wave_coordinator,
                    "released_task_ids": _ordered_wave_task_ids(
                        manifest, released_reservations
                    ),
                    "reason": reason,
                },
                task_id=task_id,
            )
        response = _orchestration_status_unlocked(root, manifest)
    return response


def _orchestration_status_unlocked(
    run_dir: Path | str,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    manifest = _load_manifest(root) if manifest is None else manifest
    state = manifest["state_machine"]
    counts: dict[str, int] = {}
    for entry in manifest["tasks"].values():
        status = str(entry.get("status", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
    runnable = (
        []
        if state["state"] in {"FAILED", "EXTERNAL_BLOCKED", "COMPLETE"}
        else _runnable_tasks(root, manifest)
    )
    claimed = []
    for task_id, entry in manifest["tasks"].items():
        if entry.get("status") != "CLAIMED":
            continue
        task = AgentTask.from_dict(entry["task"])
        claim = AgentClaim.from_dict(entry.get("claim"))
        claimed.append(
            {
                "task_id": task_id,
                "agent_role": task.agent_role.value,
                "owner_key": task.owner_key,
                "claim": claim.to_dict(),
            }
        )
    claimed.sort(key=lambda item: str(item["task_id"]))
    active_wave = _active_dispatch_wave(manifest)
    dispatch_wave: dict[str, Any] | None = None
    if active_wave:
        (wave_id, coordinator_id), wave_entries = active_wave
        wave_task_ids = _ordered_wave_task_ids(
            manifest, (task_id for task_id, _ in wave_entries)
        )
        dispatch_wave = {
            "wave_id": wave_id,
            "coordinator_id": coordinator_id,
            "task_ids": wave_task_ids,
            "pending_task_ids": _ordered_wave_task_ids(
                manifest,
                (
                    task_id
                    for task_id, entry in wave_entries
                    if entry.get("status") == "PENDING"
                ),
            ),
            "claimed_task_ids": _ordered_wave_task_ids(
                manifest,
                (
                    task_id
                    for task_id, entry in wave_entries
                    if entry.get("status") == "CLAIMED"
                ),
            ),
        }
    return {
        "architecture": ARCHITECTURE,
        "agent_mode": "required",
        "run_id": manifest["run_id"],
        "batch_id": manifest["batch_id"],
        "state": state["state"],
        "validated_phases": state["validated_phases"],
        "active_phase": state["active_phase"],
        "task_counts": counts,
        "runnable_tasks": runnable,
        "claimed_tasks": claimed,
        "active_dispatch_wave": dispatch_wave,
        "delivery_command": (
            f'scripts/run-test-design.ps1 complete-deliverables --run-dir "{root}" '
            '--module-path "<模块路径>" --batch-id <批次ID>'
            if state["state"] == "DELIVERY_RUNNING" else ""
        ),
    }


def orchestration_status(run_dir: Path | str) -> dict[str, Any]:
    """Return one consistent status snapshot after completing locked recovery."""

    root = Path(run_dir).resolve()
    with exclusive_process_lock(root / "orchestration" / ".orchestrator.lock"):
        manifest = _initialize_orchestration_unlocked(root)
        return _orchestration_status_unlocked(root, manifest)


def orchestration_status_under_lock(run_dir: Path | str) -> dict[str, Any]:
    """Recover and read status when the caller already owns ``.orchestrator.lock``.

    This narrow entry point exists for the delivery single-writer transaction,
    which must hold the run lock continuously through final publication.  Other
    callers must use :func:`orchestration_status`.
    """

    root = Path(run_dir).resolve()
    manifest = _initialize_orchestration_unlocked(root)
    return _orchestration_status_unlocked(root, manifest)


def validate_delivery_running_state(run_dir: Path | str) -> None:
    """Require the locked run to be ready for the single-writer delivery transaction.

    ``complete-deliverables`` calls this only while holding the run's
    ``.orchestrator.lock``.  Keeping the state check in the orchestration module
    avoids duplicating state-machine invariants in the Excel delivery CLI.
    """

    root = Path(run_dir).resolve()
    manifest = _load_manifest(root)
    machine = _machine(manifest)
    if machine.state != "DELIVERY_RUNNING" or machine.active_phase is not Phase.DELIVERY:
        raise OrchestrationError(
            "orchestrated complete-deliverables requires state=DELIVERY_RUNNING; "
            f"current state is {machine.state}"
        )


def complete_delivery_orchestration(run_dir: Path | str) -> dict[str, object]:
    """Validate the published receipt and atomically close delivery to COMPLETE.

    The caller must hold ``.orchestrator.lock`` and first persist the durable
    delivery journal as ``FINALIZING``.  This function deliberately does not
    acquire the lock again because the complete-deliverables single writer owns
    it from precheck through publication.  A hard interruption is recovered
    forward by ``initialize_orchestration`` plus that journal; manifest/state/
    events are no longer assumed to live inside an in-process rollback context.
    """

    root = Path(run_dir).resolve()
    manifest = _load_manifest(root)
    machine = _machine(manifest)
    if machine.state != "DELIVERY_RUNNING" or machine.active_phase is not Phase.DELIVERY:
        raise OrchestrationError(
            "cannot complete orchestrated delivery unless state=DELIVERY_RUNNING; "
            f"current state is {machine.state}"
        )
    pipeline = _quiet(derive_pipeline_status, root)
    if pipeline.get("state") != "COMPLETE":
        reasons = "; ".join(str(item) for item in pipeline.get("reasons", []))
        raise OrchestrationError(
            "published delivery did not pass the deterministic receipt gate: "
            + (reasons or str(pipeline.get("state") or "unknown delivery state"))
        )
    # Advance both deterministic transitions in memory and persist only the
    # final checkpoint.  A process must never be left at DELIVERY_VALIDATED
    # merely because it died between two state writes; locked initialization
    # repairs the projections/events and the FINALIZING delivery journal then
    # completes forward.
    machine.validate_phase(Phase.DELIVERY)
    machine.complete()
    _save_machine(root, manifest, machine)
    # Both events use deterministic identities.  If the process dies after the
    # manifest checkpoint or between these appends, initialization reconstructs
    # the same payloads and appends only the missing semantic event.
    _ensure_delivery_completion_events(manifest, _event_store(root))
    return machine.to_dict()


def _validated_prefix_drift(
    run_dir: Path,
    manifest: Mapping[str, Any],
    machine: OrchestrationStateMachine,
) -> tuple[Phase, str] | None:
    for phase in machine.validated_phases:
        try:
            if phase is Phase.DISCOVERY:
                passed, reason = _try_batch_gate(run_dir, "discovery")
                latest = _latest_success_entry(run_dir, manifest, AgentRole.DISCOVERY)
                if not passed or not latest or not _task_inputs_still_current(run_dir, latest[0], latest[1]):
                    return phase, reason or "Discovery task/source fingerprint changed"
            elif phase is Phase.PLAN:
                passed, reason = _try_batch_gate(run_dir, "plan")
                latest = _latest_success_entry(run_dir, manifest, AgentRole.PLAN_DFX)
                if not passed or not latest or not _task_inputs_still_current(run_dir, latest[0], latest[1]):
                    return phase, reason or "Plan & DFX task/source fingerprint changed"
            elif phase is Phase.RISK:
                passed, reason = _try_batch_gate(run_dir, "risk")
                candidates = _plan_risk_candidates(run_dir)
                if not passed:
                    return phase, reason
                if candidates:
                    latest = _latest_success_entry(run_dir, manifest, AgentRole.RISK_ARBITER)
                    if not latest or not _task_inputs_still_current(run_dir, latest[0], latest[1]):
                        return phase, "Risk task/source fingerprint changed"
            elif phase is Phase.CASES:
                passed, reason = _try_batch_gate(run_dir, "cases")
                if not passed or not generation_session_is_current(run_dir) or not _all_case_workers_ready(run_dir, manifest):
                    return phase, reason or "generation session or Case Worker source changed"
            elif phase is Phase.REVIEW:
                _quiet(validate_review_artifacts, run_dir)
            elif phase is Phase.DELIVERY:
                pipeline = _quiet(derive_pipeline_status, run_dir)
                if pipeline.get("state") != "COMPLETE":
                    return phase, "; ".join(str(item) for item in pipeline.get("reasons", [])) or "delivery receipt changed"
        except Exception as exc:
            return phase, str(exc)
    return None


def _request_source_drift_rework(
    run_dir: Path,
    manifest: dict[str, Any],
    machine: OrchestrationStateMachine,
    phase: Phase,
    reason: str,
) -> None:
    request_id = f"RW-SOURCE-{machine.revision + 1:04d}"
    suffix = 1
    while (run_dir / "orchestration" / "rework-requests" / f"{request_id}.json").exists():
        suffix += 1
        request_id = f"RW-SOURCE-{machine.revision + 1:04d}-{suffix:02d}"
    source_fp = (
        _generation_task_fingerprint(run_dir)
        if phase in {Phase.CASES, Phase.REVIEW, Phase.DELIVERY}
        else fingerprint(_phase_sources(run_dir, {
            Phase.DISCOVERY: AgentRole.DISCOVERY,
            Phase.PLAN: AgentRole.PLAN_DFX,
            Phase.RISK: AgentRole.RISK_ARBITER,
        }[phase]))
    )
    if len(source_fp) != 64:
        source_fp = canonical_fingerprint({"phase": phase.value, "reason": reason})
    request = ReworkRequest(
        schema_version="1.0.0",
        request_id=request_id,
        run_id=str(manifest["run_id"]),
        batch_id=str(manifest["batch_id"]),
        target_phase=ReworkTarget(phase.value),
        target_task_id=None,
        reason_code="SOURCE_CHANGED",
        affected_ids=(phase.value,),
        evidence=(),
        required_action=f"validated {phase.value} source drifted; rerun the phase: {reason}",
        source_fingerprint=source_fp,
        attempt=1,
    )
    _invalidate_for_rework(run_dir, manifest, machine, [request])


def advance_orchestration(run_dir: Path | str) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    lock = root / "orchestration" / ".orchestrator.lock"
    with exclusive_process_lock(lock):
        _initialize_orchestration_unlocked(root)
        manifest = _load_manifest(root)
        machine = _machine(manifest)
        fast_cases_resume = (
            machine.active_phase is Phase.CASES and generation_session_is_current(root)
        )
        if machine.state not in {"FAILED", "EXTERNAL_BLOCKED"} and not fast_cases_resume:
            drift = _validated_prefix_drift(root, manifest, machine)
            if drift:
                _request_source_drift_rework(root, manifest, machine, *drift)
                manifest = _load_manifest(root)
                machine = _machine(manifest)
        if machine.state in {"FAILED", "EXTERNAL_BLOCKED", "COMPLETE"}:
            return _orchestration_status_unlocked(root, manifest)
        for _ in range(12):
            phase = machine.active_phase or machine.next_phase
            if phase is None:
                break
            _begin_if_needed(root, manifest, machine, phase)
            if phase is Phase.DISCOVERY:
                passed, _ = _try_batch_gate(root, "discovery")
                if passed and _latest_success_entry(root, manifest, AgentRole.DISCOVERY):
                    _validate_phase(root, manifest, machine, phase)
                    continue
                _ensure_role_task(root, manifest, AgentRole.DISCOVERY)
                break
            if phase is Phase.PLAN:
                passed, _ = _try_batch_gate(root, "plan")
                if passed and _latest_success_entry(root, manifest, AgentRole.PLAN_DFX):
                    _validate_phase(root, manifest, machine, phase)
                    continue
                _ensure_role_task(root, manifest, AgentRole.PLAN_DFX)
                break
            if phase is Phase.RISK:
                candidates = _plan_risk_candidates(root)
                if not candidates:
                    _quiet(record_no_model_uncertainty, root)
                    passed, reason = _try_batch_gate(root, "risk")
                    if not passed:
                        raise OrchestrationError(f"deterministic RISK-NONE did not pass risk gate: {reason}")
                    _validate_phase(root, manifest, machine, phase)
                    continue
                passed, _ = _try_batch_gate(root, "risk")
                if passed and _latest_success_entry(root, manifest, AgentRole.RISK_ARBITER):
                    _validate_phase(root, manifest, machine, phase)
                    continue
                _ensure_role_task(root, manifest, AgentRole.RISK_ARBITER)
                break
            if phase is Phase.CASES:
                prepared_generation = False
                if not generation_session_is_current(root):
                    _quiet(prepare_function_case_generation, root)
                    prepared_generation = True
                # This is an unconditional CASES precondition, not a
                # missing-file repair.  It also closes the hard-stop window
                # between the manifest intent link and receipt creation.
                _promote_retained_sheet_json(root, manifest)
                if prepared_generation:
                    _event_store(root).append("CASE_GENERATION_PREPARED", generation_session_data(root) or {})
                _ensure_case_tasks(root, manifest)
                if _all_case_workers_ready(root, manifest):
                    _aggregate_ready_workers(root, manifest)
                    passed, reason = _try_batch_gate(root, "cases")
                    if not passed:
                        raise OrchestrationError(f"merged Case Worker output failed cases gate: {reason}")
                    _validate_phase(root, manifest, machine, phase)
                    continue
                break
            if phase is Phase.REVIEW:
                try:
                    _quiet(validate_review_artifacts, root)
                except Exception:
                    _ensure_role_task(root, manifest, AgentRole.REVIEWER, generation_fingerprint=True)
                    break
                _validate_phase(root, manifest, machine, phase)
                continue
            if phase is Phase.DELIVERY:
                pipeline = _quiet(derive_pipeline_status, root)
                if pipeline.get("state") == "COMPLETE":
                    _validate_phase(root, manifest, machine, phase)
                    change = machine.complete()
                    _save_machine(root, manifest, machine)
                    _event_store(root).append("RUN_COMPLETED", change.to_dict())
                break
        return _orchestration_status_unlocked(root, manifest)


def _resolve_source_paths(run_dir: Path, entry: Mapping[str, Any]) -> list[Path]:
    project = _project_root(run_dir)
    raw = entry.get("source_paths")
    if not isinstance(raw, list):
        raise OrchestrationError("task source_paths must be an array")
    result: list[Path] = []
    for value in raw:
        path = (project / str(value)).resolve()
        try:
            path.relative_to(project)
        except ValueError as exc:
            raise OrchestrationError(f"task source path escapes project: {value}") from exc
        result.append(path)
    return result


def _current_task_source_paths(run_dir: Path, task: AgentTask) -> list[Path]:
    """Re-enumerate the complete current source set for one dispatched task."""

    if task.agent_role is AgentRole.CASE_WORKER:
        if task.owner_key is None:
            raise OrchestrationError("case_worker task is missing owner_key")
        return _case_worker_sources(run_dir, task.owner_key)
    return _phase_sources(run_dir, task.agent_role)


def _task_contracts_still_current(
    run_dir: Path,
    task: AgentTask,
    entry: Mapping[str, Any],
) -> bool:
    raw_paths = entry.get("contract_source_paths")
    if not isinstance(raw_paths, list) or any(not isinstance(item, str) for item in raw_paths):
        return False
    project = _project_root(run_dir)
    recorded = [(project / item).resolve() for item in raw_paths]
    expected = _role_contract_sources(run_dir, task.agent_role)
    if [path.relative_to(project).as_posix() for path in expected] != raw_paths:
        return False
    return (
        all(path.is_file() for path in recorded)
        and fingerprint(recorded) == entry.get("contract_fingerprint")
    )


def _task_inputs_still_current(
    run_dir: Path,
    task: AgentTask,
    entry: Mapping[str, Any],
) -> bool:
    if not _task_contracts_still_current(run_dir, task, entry):
        return False
    originals = _resolve_source_paths(run_dir, entry)
    current_sources = _current_task_source_paths(run_dir, task)
    original_set = {path.resolve() for path in originals}
    current_set = {path.resolve() for path in current_sources}
    if (
        len(originals) != len(original_set)
        or len(current_sources) != len(current_set)
        or original_set != current_set
    ):
        return False
    snapshots = [run_dir / path for path in task.input_files]
    if len(originals) != len(snapshots):
        return False
    case_result_fields = {
        "page-discovery.csv": {"是否已生成用例", "关联用例ID", "覆盖状态", "未覆盖/待确认原因"},
        "element-case-plan.csv": {"实际用例ID", "未生成原因"},
        "test-data-lifecycle.csv": {"创建步骤关联用例"},
    }
    ignored = dict(case_result_fields)
    if task.agent_role is AgentRole.PLAN_DFX:
        ignored["selection-option-observations.csv"] = {"关联用例ID"}
        ignored["interaction-branch-observations.csv"] = {"关联用例ID"}
    for original, snapshot in zip(originals, snapshots):
        if not original.is_file() or not snapshot.is_file():
            return False
        if original.name in ignored:
            def semantic_rows(path: Path) -> list[dict[str, str]]:
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    return [
                        {key: value for key, value in row.items() if key not in ignored[original.name]}
                        for row in csv.DictReader(stream)
                    ]

            if semantic_rows(original) != semantic_rows(snapshot):
                return False
        elif _frozen_source_bytes(original) != snapshot.read_bytes():
            return False
    return True


def _validate_result_files(run_dir: Path, task: AgentTask, entry: Mapping[str, Any], result: AgentResult) -> None:
    if result.task_id != task.task_id or result.agent_role is not task.agent_role:
        raise OrchestrationError("AgentResult task_id/agent_role does not match AgentTask")
    if result.source_fingerprint != task.source_fingerprint:
        raise OrchestrationError("AgentResult source_fingerprint does not match AgentTask")
    snapshot_paths = [run_dir / path for path in task.input_files]
    if fingerprint(snapshot_paths) != entry.get("input_snapshot_fingerprint"):
        raise OrchestrationError("AgentTask frozen input snapshot changed after dispatch")
    workspace_root = run_dir / "artifacts" / "agent-work" / task.agent_role.value / task.task_id
    if fingerprint([workspace_root / "meta" / "agent-task.json"]) != entry.get("task_packet_fingerprint"):
        raise OrchestrationError("AgentTask packet changed after dispatch")
    if fingerprint([workspace_root / "meta" / "task-context.json"]) != entry.get("context_fingerprint"):
        raise OrchestrationError("AgentTask context changed after dispatch")
    if not _task_inputs_still_current(run_dir, task, entry):
        raise OrchestrationError(
            "AgentTask source changed (path set or content); discard stale output and create a new task"
        )
    if task.agent_role is AgentRole.REVIEWER:
        current_review = review_source_fingerprint(run_dir)
        if current_review != entry.get("review_input_fingerprint"):
            raise OrchestrationError("Reviewer input changed after dispatch; create a fresh Review task")
    if task.agent_role in {AgentRole.CASE_WORKER, AgentRole.REVIEWER}:
        current = _generation_task_fingerprint(run_dir)
    else:
        current = fingerprint(_current_task_source_paths(run_dir, task))
    if current != task.source_fingerprint:
        raise OrchestrationError("AgentTask source changed; discard stale output and create a new task")
    required = set(entry.get("required_outputs") or [])
    produced = set(result.produced_files)
    if result.status is TaskStatus.SUCCEEDED and not required.issubset(produced):
        raise OrchestrationError(f"successful AgentResult is missing required outputs: {sorted(required-produced)}")
    allowed_exact = set(task.allowed_output_files)
    for path in produced:
        if path not in allowed_exact and not any(path.startswith(prefix) for prefix in task.allowed_output_prefixes):
            raise OrchestrationError(f"AgentResult produced an output outside its task allowlist: {path}")
    workspace = WorkspaceManager(run_dir)
    actual = {
        f"artifacts/agent-work/{task.agent_role.value}/{task.task_id}/output/{record['path']}"
        for record in workspace.output_manifest(task.agent_role.value, task.task_id)
    }
    if produced != actual:
        raise OrchestrationError(
            f"AgentResult produced_files must exactly match workspace files; missing={sorted(actual-produced)}, hidden={sorted(produced-actual)}"
        )
    for relative in sorted(produced):
        try:
            assert_no_sensitive_artifact(run_dir / relative, f"Agent output {relative}")
        except SensitiveDataError as exc:
            raise OrchestrationError(str(exc)) from exc
    if result.status is TaskStatus.SUCCEEDED and result.gate_summary.get(task.required_gate) is not True:
        raise OrchestrationError(f"successful AgentResult must declare gate_summary.{task.required_gate}=true")


def _promotion_mapping(task: AgentTask, result: AgentResult) -> dict[str, str]:
    prefix = f"artifacts/agent-work/{task.agent_role.value}/{task.task_id}/output/"
    mapping: dict[str, str] = {}
    for path in result.produced_files:
        relative = path.removeprefix(prefix)
        if task.agent_role is AgentRole.DISCOVERY:
            if relative.startswith("evidence/"):
                target = f"artifacts/{relative}"
            elif relative.startswith("screenshots/"):
                target = f"artifacts/{relative}"
            else:
                target = relative
            mapping[relative] = target
        elif task.agent_role is AgentRole.PLAN_DFX and relative in {
            "element-case-plan.csv", "selection-option-observations.csv", "interaction-branch-observations.csv", "test-data-lifecycle.csv"
        }:
            mapping[relative] = relative
        elif task.agent_role is AgentRole.RISK_ARBITER and relative == "risk-confirmation.csv":
            mapping[relative] = relative
        elif task.agent_role is AgentRole.REVIEWER and relative == "review-report.json":
            mapping[relative] = "orchestration/review-report.json"
    if task.agent_role is AgentRole.DISCOVERY:
        mapping["__derived_batch_status__"] = "batch-status.csv"
    return mapping


def _promotion_transaction_id(task_id: str, execution_id: str) -> str:
    identity = hashlib.sha256(f"{task_id}\0{execution_id}".encode("utf-8")).hexdigest()
    return f"promotion-{identity[:40]}"


def _promotion_source_root(run_dir: Path, transaction_id: str) -> Path:
    return run_dir / "orchestration" / "promotion-sources" / transaction_id


def _promotion_receipt_path(run_dir: Path, transaction_id: str) -> Path:
    if not _PROMOTION_ID_RE.fullmatch(transaction_id):
        raise OrchestrationError(f"unsafe promotion transaction ID: {transaction_id!r}")
    return run_dir / "orchestration" / "promotions" / transaction_id / "receipt.json"


def _tree_manifest(run_dir: Path, root: Path) -> list[dict[str, object]]:
    workspace = WorkspaceManager(run_dir)
    return sorted(
        [workspace.file_record(path, relative_to=root) for path in root.rglob("*") if path.is_file()],
        key=lambda item: str(item["path"]),
    )


def _prepare_named_promotion_sources(
    run_dir: Path,
    sources: Mapping[str, Path],
    transaction_id: str,
) -> Path:
    """Freeze a named multi-file source set for durable phase-level promotion."""

    source_root = _promotion_source_root(run_dir, transaction_id)
    parent = source_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{transaction_id}.", dir=parent))
    try:
        for name, source in sorted(sources.items()):
            raw_name = Path(name)
            if raw_name.is_absolute() or any(part in {"", ".", ".."} for part in raw_name.parts):
                raise OrchestrationError(
                    f"unsafe retained promotion source name: {name!r}"
                )
            if source.is_symlink() or not source.is_file() or source.stat().st_size == 0:
                raise OrchestrationError(
                    f"retained promotion source must be a non-empty regular file: {source}"
                )
            atomic_copy(source, temporary / raw_name)

        desired = _tree_manifest(run_dir, temporary)
        if source_root.exists():
            current = _tree_manifest(run_dir, source_root) if source_root.is_dir() else []
            if current == desired:
                return source_root
            receipt_path = _promotion_receipt_path(run_dir, transaction_id)
            receipt_status = None
            if receipt_path.is_file():
                receipt = _load_json(
                    receipt_path, f"promotion {transaction_id} receipt"
                )
                receipt_status = receipt.get("status") if isinstance(receipt, Mapping) else None
            if receipt_status not in {None, "ROLLED_BACK"}:
                raise OrchestrationError(
                    f"durable retained promotion {transaction_id} source changed after preparation"
                )
            if not source_root.is_dir() or source_root.is_symlink():
                raise OrchestrationError(
                    f"durable retained promotion source root is unsafe: {source_root}"
                )
            shutil.rmtree(source_root)
        os.replace(temporary, source_root)
        temporary = None
        return source_root
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)


def _prepare_promotion_sources(
    run_dir: Path,
    task: AgentTask,
    mapping: Mapping[str, str],
    transaction_id: str,
) -> Path:
    """Build an immutable, deterministic source tree for one durable promotion."""

    source_root = _promotion_source_root(run_dir, transaction_id)
    parent = source_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{transaction_id}.", dir=parent))
    derived_root: Path | None = None
    try:
        for source_name in mapping:
            target = temporary / source_name
            if source_name == "__derived_batch_status__":
                derived_root = Path(
                    tempfile.mkdtemp(prefix=".promotion-discovery-", dir=run_dir.parent)
                )
                atomic_copy(_accepted_output(run_dir, task, "page-discovery.csv"), derived_root / "page-discovery.csv")
                atomic_copy(run_dir / "batch-status.csv", derived_root / "batch-status.csv")
                sync_discovery_status(derived_root)
                atomic_copy(derived_root / "batch-status.csv", target)
            else:
                atomic_copy(_accepted_output(run_dir, task, source_name), target)

        desired = _tree_manifest(run_dir, temporary)
        if source_root.exists():
            current = _tree_manifest(run_dir, source_root) if source_root.is_dir() else []
            if current == desired:
                return source_root
            receipt_path = _promotion_receipt_path(run_dir, transaction_id)
            receipt_status = None
            if receipt_path.is_file():
                receipt = _load_json(receipt_path, f"promotion {transaction_id} receipt")
                receipt_status = receipt.get("status") if isinstance(receipt, Mapping) else None
            if receipt_status not in {None, "ROLLED_BACK"}:
                raise OrchestrationError(
                    f"durable promotion {transaction_id} source changed after preparation"
                )
            shutil.rmtree(source_root)
        os.replace(temporary, source_root)
        temporary = None
        return source_root
    finally:
        if derived_root is not None:
            shutil.rmtree(derived_root, ignore_errors=True)
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)


def _cleanup_promotion_source(run_dir: Path, transaction_id: str) -> None:
    shutil.rmtree(_promotion_source_root(run_dir, transaction_id), ignore_errors=True)


def _validate_retained_promotion_receipt(
    run_dir: Path,
    manifest: Mapping[str, Any],
    transaction_id: str,
    value: Mapping[str, Any],
    plan_task: AgentTask,
) -> None:
    metadata = value.get("metadata")
    if not isinstance(metadata, Mapping):
        raise OrchestrationError(
            f"retained promotion {transaction_id} has no strict source metadata"
        )
    raw_risk_id = metadata.get("risk_task_id")
    risk_task: AgentTask | None = None
    if raw_risk_id is not None:
        risk_entry = manifest.get("tasks", {}).get(raw_risk_id)
        if not isinstance(risk_entry, Mapping):
            raise OrchestrationError(
                f"retained promotion {transaction_id} references unknown Risk task"
            )
        risk_task = AgentTask.from_dict(risk_entry.get("task"))
        if risk_task.agent_role is not AgentRole.RISK_ARBITER:
            raise OrchestrationError(
                f"retained promotion {transaction_id} risk_task_id is not a Risk task"
            )
    sources = _retained_sheet_sources(run_dir, plan_task, risk_task)
    source_fingerprint = _retained_sheet_source_fingerprint(sources)
    expected_metadata = _retained_sheet_metadata(
        plan_task, risk_task, source_fingerprint
    )
    if dict(metadata) != expected_metadata:
        raise OrchestrationError(
            f"retained promotion {transaction_id} source metadata is stale or inconsistent"
        )
    if transaction_id != _retained_sheet_transaction_id(expected_metadata):
        raise OrchestrationError(
            f"retained promotion {transaction_id} is not bound to its source fingerprint"
        )
    expected_items = [
        {"source": name, "destination": f"artifacts/data/{name}"}
        for name in _RETAINED_SHEET_JSON_NAMES
    ]
    if value.get("items") != expected_items:
        raise OrchestrationError(
            f"retained promotion {transaction_id} has an unexpected formal output mapping"
        )


def _reconcile_durable_promotions(
    run_dir: Path,
    manifest: dict[str, Any],
    events: EventStore,
) -> None:
    """Fail closed or complete every durable promotion after process interruption."""

    promotion_root = run_dir / "orchestration" / "promotions"
    if not promotion_root.is_dir():
        return
    manager = WorkspaceManager(run_dir)
    for receipt_path in sorted(promotion_root.glob("*/receipt.json")):
        value = _load_json(receipt_path, f"promotion receipt {receipt_path.parent.name}")
        if not isinstance(value, Mapping):
            raise OrchestrationError(f"promotion receipt must be an object: {receipt_path}")
        transaction_id = str(value.get("transaction_id") or "")
        if transaction_id != receipt_path.parent.name:
            raise OrchestrationError(f"promotion receipt identity mismatch: {receipt_path}")
        task_id = str(value.get("task_id") or "")
        entry = manifest.get("tasks", {}).get(task_id)
        if not isinstance(entry, Mapping):
            raise OrchestrationError(
                f"promotion {transaction_id} references unknown task {task_id!r}"
            )
        task = AgentTask.from_dict(entry.get("task"))
        if value.get("agent_role") != task.agent_role.value:
            raise OrchestrationError(f"promotion {transaction_id} agent role does not match task")
        status = str(value.get("status") or "")
        task_status = str(entry.get("status") or "")
        promotion_ids = entry.get("promotion_ids")
        linked = isinstance(promotion_ids, list) and transaction_id in promotion_ids
        metadata = value.get("metadata")
        promotion_kind = (
            metadata.get("promotion_kind") if isinstance(metadata, Mapping) else None
        )
        if promotion_kind is not None and promotion_kind != "retained-sheet-json":
            raise OrchestrationError(
                f"promotion {transaction_id} has unsupported promotion_kind {promotion_kind!r}"
            )
        if promotion_kind == "retained-sheet-json":
            if task.agent_role is not AgentRole.PLAN_DFX:
                raise OrchestrationError(
                    f"retained promotion {transaction_id} is not owned by a Plan task"
                )
            _validate_retained_promotion_receipt(
                run_dir, manifest, transaction_id, value, task
            )
            if status == "ROLLED_BACK":
                if task_status not in {
                    TaskStatus.SUCCEEDED.value,
                    "INVALIDATED",
                }:
                    raise OrchestrationError(
                        f"rolled-back retained promotion {transaction_id} has invalid Plan task status "
                        f"{task_status!r}"
                    )
                manager.rollback_promotion(transaction_id)
                _cleanup_promotion_source(run_dir, transaction_id)
                if isinstance(promotion_ids, list) and transaction_id in promotion_ids:
                    entry["promotion_ids"] = [
                        item for item in promotion_ids if item != transaction_id
                    ]
                    _save_manifest(run_dir, manifest)
                audited = any(
                    event.get("event_type") == "AUDIT_RETAINED_PROMOTION_ROLLED_BACK"
                    and isinstance(event.get("payload"), Mapping)
                    and event["payload"].get("transaction_id") == transaction_id
                    for event in events.read_events()
                )
                if not audited:
                    events.append(
                        "AUDIT_RETAINED_PROMOTION_ROLLED_BACK",
                        {
                            "transaction_id": transaction_id,
                            "task_status": task_status,
                        },
                        task_id=task_id,
                    )
                continue

        try:
            if status in {"PREPARED", "APPLYING", "PROMOTED"}:
                if task_status in {"CLAIMED", "PENDING"}:
                    if task_status == "CLAIMED":
                        claim = AgentClaim.from_dict(entry.get("claim"))
                        metadata = value.get("metadata")
                        if not isinstance(metadata, Mapping) or metadata.get(
                            "execution_id"
                        ) != claim.execution_id:
                            raise OrchestrationError(
                                f"promotion {transaction_id} execution does not match active claim"
                            )
                    manager.rollback_promotion(transaction_id)
                    _cleanup_promotion_source(run_dir, transaction_id)
                    events.append(
                        "AUDIT_PROMOTION_ROLLED_BACK",
                        {
                            "transaction_id": transaction_id,
                            "recovered_status": status,
                            "task_status": task_status,
                        },
                        task_id=task_id,
                    )
                elif task_status == TaskStatus.SUCCEEDED.value and linked:
                    receipt = manager.resume_promotion(transaction_id)
                    manager.finalize_promotion(receipt)
                    _cleanup_promotion_source(run_dir, transaction_id)
                    events.append(
                        "AUDIT_PROMOTION_FINALIZED",
                        {
                            "transaction_id": transaction_id,
                            "recovered_status": status,
                        },
                        task_id=task_id,
                    )
                else:
                    raise OrchestrationError(
                        f"active promotion {transaction_id} is inconsistent with task status "
                        f"{task_status!r} or is not linked from the task manifest"
                    )
            elif status == "ROLLED_BACK":
                manager.rollback_promotion(transaction_id)
                _cleanup_promotion_source(run_dir, transaction_id)
                if task_status == TaskStatus.SUCCEEDED.value and linked:
                    raise OrchestrationError(
                        f"successful task {task_id} links rolled-back promotion {transaction_id}"
                    )
            elif status == "FINALIZED":
                manager.finalize_promotion(transaction_id)
                _cleanup_promotion_source(run_dir, transaction_id)
                if not linked or task_status not in {
                    TaskStatus.SUCCEEDED.value,
                    "INVALIDATED",
                }:
                    raise OrchestrationError(
                        f"finalized promotion {transaction_id} is not a valid task history entry"
                    )
            else:
                raise OrchestrationError(
                    f"promotion {transaction_id} has unsupported durable status {status!r}"
                )
        except WorkspaceError as exc:
            raise OrchestrationError(
                f"promotion {transaction_id} reconciliation failed closed: {exc}"
            ) from exc


def _rollback_task_promotions(run_dir: Path, task_id: str, execution_id: str) -> None:
    transaction_id = _promotion_transaction_id(task_id, execution_id)
    receipt_path = _promotion_receipt_path(run_dir, transaction_id)
    if receipt_path.is_file():
        try:
            WorkspaceManager(run_dir).rollback_promotion(transaction_id)
        except WorkspaceError as exc:
            raise OrchestrationError(
                f"cannot safely release task {task_id}; promotion rollback failed: {exc}"
            ) from exc
    _cleanup_promotion_source(run_dir, transaction_id)


def _store_result(
    run_dir: Path,
    manifest: dict[str, Any],
    task: AgentTask,
    result: AgentResult,
    *,
    promotion_id: str | None = None,
) -> Path:
    path = run_dir / "orchestration" / "results" / f"{task.task_id}.json"
    atomic_write_json(path, result.to_dict())
    result_fingerprint = _sha256_file(path)
    entry = manifest["tasks"][task.task_id]
    entry["result_path"] = path.relative_to(run_dir).as_posix()
    entry["result_fingerprint"] = result_fingerprint
    entry["status"] = result.status.value
    entry["output_fingerprint"] = WorkspaceManager(run_dir).fingerprint_outputs(
        task.agent_role.value, task.task_id
    )
    if promotion_id is not None:
        promotion_ids = entry.setdefault("promotion_ids", [])
        if not isinstance(promotion_ids, list):
            raise OrchestrationError(f"task {task.task_id} promotion_ids must be an array")
        if promotion_id not in promotion_ids:
            promotion_ids.append(promotion_id)
    if result.status is TaskStatus.SUCCEEDED:
        accepted_paths = [path for path in _accepted_root(run_dir, task).rglob("*") if path.is_file()]
        entry["accepted_output_root"] = _accepted_root(run_dir, task).relative_to(run_dir).as_posix()
        entry["accepted_output_fingerprint"] = fingerprint(accepted_paths)
    _save_manifest(run_dir, manifest)
    _event_store(run_dir).append(
        "TASK_RESULT_STORED",
        {
            "status": result.status.value,
            "result_path": path.relative_to(run_dir).as_posix(),
            "result_fingerprint": result_fingerprint,
        },
        task_id=task.task_id,
    )
    return path


def _invalidate_for_rework(
    run_dir: Path,
    manifest: dict[str, Any],
    machine: OrchestrationStateMachine,
    requests: Sequence[ReworkRequest],
) -> None:
    phase_index = {phase.value: index for index, phase in enumerate(PHASE_ORDER)}
    target = min(requests, key=lambda item: phase_index[item.target_phase.value]).target_phase
    for request in requests:
        path = run_dir / "orchestration" / "rework-requests" / f"{request.request_id}.json"
        atomic_write_json(path, {"request": request.to_dict(), "status": "OPEN", "closed_at": None})
    target_index = phase_index[target.value]
    earliest_requests = [request for request in requests if request.target_phase is target]
    target_ids = (
        {request.target_task_id for request in earliest_requests if request.target_task_id}
        if target is ReworkTarget.CASES and all(request.target_task_id for request in earliest_requests)
        else set()
    )
    claimed_conflicts: list[str] = []
    for task_id, entry in manifest["tasks"].items():
        if entry.get("status") != "CLAIMED":
            continue
        task_phase = str(entry.get("task", {}).get("phase", ""))
        if task_phase not in phase_index:
            continue
        affected = phase_index[task_phase] > target_index
        if phase_index[task_phase] == target_index:
            affected = not target_ids or task_id in target_ids
        if affected:
            claimed_conflicts.append(task_id)
    if claimed_conflicts:
        raise OrchestrationError(
            "cannot invalidate claimed executions; resume/submit them or explicitly release only after "
            f"confirming no side effects: {claimed_conflicts}"
        )
    for task_id, entry in manifest["tasks"].items():
        task_phase = str(entry.get("task", {}).get("phase", ""))
        if task_phase not in phase_index:
            continue
        should_invalidate = phase_index[task_phase] > target_index
        if phase_index[task_phase] == target_index:
            should_invalidate = not target_ids or task_id in target_ids
        if should_invalidate and entry.get("status") in {"PENDING", "SUCCEEDED"}:
            entry["status"] = "INVALIDATED"
            entry["invalidated_reason"] = "; ".join(request.required_action for request in requests)
    (run_dir / "orchestration" / "review-report.json").unlink(missing_ok=True)
    change = machine.request_rework(target.value, "; ".join(request.required_action for request in requests))
    _save_machine(run_dir, manifest, machine)
    _event_store(run_dir).append("REWORK_REQUESTED", {**change.to_dict(), "request_ids": [r.request_id for r in requests]})


def _validate_rework_requests(
    run_dir: Path,
    manifest: Mapping[str, Any],
    machine: OrchestrationStateMachine,
    submitting_task: AgentTask,
    requests: Sequence[ReworkRequest],
) -> None:
    phase_index = {phase.value: index for index, phase in enumerate(PHASE_ORDER)}
    target = min(requests, key=lambda item: phase_index[item.target_phase.value]).target_phase
    # Validate the backward transition on an isolated copy before writing the
    # AgentResult, request files, manifest, or state checkpoint.
    for request in requests:
        probe = OrchestrationStateMachine.from_dict(machine.to_dict())
        probe.request_rework(request.target_phase.value, "preflight")
    for request in requests:
        if request.run_id != manifest.get("run_id") or request.batch_id != manifest.get("batch_id"):
            raise OrchestrationError(f"rework request {request.request_id} run/batch scope does not match")
        if request.attempt != submitting_task.attempt:
            raise OrchestrationError(
                f"rework request {request.request_id} attempt must match submitting task attempt"
            )
        request_path = run_dir / "orchestration" / "rework-requests" / f"{request.request_id}.json"
        if request_path.exists():
            raise OrchestrationError(f"duplicate rework request ID: {request.request_id}")
        if not request.target_task_id:
            continue
        target_entry = manifest["tasks"].get(request.target_task_id)
        if not isinstance(target_entry, dict):
            raise OrchestrationError(
                f"rework request {request.request_id} references unknown target task {request.target_task_id}"
            )
        task_phase = str(target_entry.get("task", {}).get("phase", ""))
        if task_phase != request.target_phase.value:
            raise OrchestrationError(
                f"rework request {request.request_id} target task phase {task_phase!r} "
                f"does not match {request.target_phase.value!r}"
            )
        target_status = target_entry.get("status")
        if target_status not in {"PENDING", "SUCCEEDED"} and not (
            target_status == "CLAIMED" and request.target_task_id == submitting_task.task_id
        ):
            raise OrchestrationError(
                f"rework request {request.request_id} target task is not currently active/successful"
            )
        if request.target_phase is ReworkTarget.CASES:
            if request.target_task_id not in manifest.get("case_task_order", []):
                raise OrchestrationError(
                    f"rework request {request.request_id} target is not a current case_task_order worker"
                )
    target_index = phase_index[target.value]
    earliest_requests = [request for request in requests if request.target_phase is target]
    target_ids = (
        {request.target_task_id for request in earliest_requests if request.target_task_id}
        if target is ReworkTarget.CASES and all(request.target_task_id for request in earliest_requests)
        else set()
    )
    claimed_conflicts: list[str] = []
    for candidate_id, candidate_entry in manifest["tasks"].items():
        if candidate_id == submitting_task.task_id or candidate_entry.get("status") != "CLAIMED":
            continue
        candidate_phase = str(candidate_entry.get("task", {}).get("phase", ""))
        if candidate_phase not in phase_index:
            continue
        affected = phase_index[candidate_phase] > target_index
        if phase_index[candidate_phase] == target_index:
            affected = not target_ids or candidate_id in target_ids
        if affected:
            claimed_conflicts.append(candidate_id)
    if claimed_conflicts:
        raise OrchestrationError(
            "rework would invalidate other claimed executions; resume/submit them or explicitly release "
            f"after confirming no side effects: {claimed_conflicts}"
        )


def submit_agent_result(
    run_dir: Path | str,
    task_id: str,
    result_path: Path | str,
    *,
    execution_id: str,
) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    lock = root / "orchestration" / ".orchestrator.lock"
    with exclusive_process_lock(lock):
        _initialize_orchestration_unlocked(root)
        manifest = _load_manifest(root)
        entry = manifest["tasks"].get(task_id)
        if not isinstance(entry, dict):
            raise OrchestrationError(f"unknown task ID: {task_id}")
        if entry.get("status") != "CLAIMED":
            raise OrchestrationError(f"task {task_id} is not claimed: {entry.get('status')}")
        task = AgentTask.from_dict(entry["task"])
        claim = AgentClaim.from_dict(entry.get("claim"))
        if claim.execution_id != execution_id:
            raise OrchestrationError(
                f"task {task_id} is claimed by execution {claim.execution_id}, not {execution_id}"
            )
        _validate_claim_matches_entry(task, entry, claim)
        if claim.executor_kind is ExecutorKind.CODEBUDDY_SUBAGENT:
            try:
                validate_execution_binding(_project_root(root), root, task, claim)
            except ExecutionBindingError as exc:
                raise OrchestrationError(
                    f"task {task_id} has no valid physical sub-agent execution binding: {exc}"
                ) from exc
        if task.agent_role is AgentRole.DISCOVERY:
            try:
                assert claim.page_probe_receipt_id is not None
                assert claim.page_probe_receipt_fingerprint is not None
                probe_receipt = load_page_probe_receipt(
                    root,
                    claim.page_probe_receipt_id,
                    expected_fingerprint=claim.page_probe_receipt_fingerprint,
                )
                validate_project_record_consumption(
                    _project_root(root), root, probe_receipt
                )
                probe_state = page_probe_event_registry(
                    _event_store(root).read_events()
                ).get(probe_receipt.receipt_id)
            except (AssertionError, PageProbeError) as exc:
                raise OrchestrationError(
                    f"Discovery page probe receipt is stale before submit: {exc}"
                ) from exc
            if (
                probe_state is None
                or probe_state["committed_sequence"] is None
                or probe_state["tombstoned_sequence"] is not None
                or probe_receipt.execution_id != claim.execution_id
                or probe_receipt.coordinator_id != claim.coordinator_id
                or probe_receipt.source_fingerprint != claim.source_fingerprint
                or probe_receipt.approved_mcp_tools != claim.approved_page_mcp_tools
            ):
                raise OrchestrationError(
                    "Discovery page probe receipt is not the active committed authority"
                )
        _require_complete_case_wave_claims(manifest, task, entry, claim)
        if task.agent_role is AgentRole.REVIEWER:
            _validate_reviewer_execution_identity(manifest, claim)
        result_source = Path(result_path).resolve()
        try:
            assert_no_sensitive_text_file(result_source, "AgentResult")
        except SensitiveDataError as exc:
            raise OrchestrationError(str(exc)) from exc
        try:
            result = AgentResult.from_dict(_load_json(result_source, "AgentResult"))
        except (TypeError, ValueError) as exc:
            raise OrchestrationError(f"invalid AgentResult: {exc}") from exc
        _validate_result_files(root, task, entry, result)
        _validate_case_wave_result_barrier(
            manifest, task, entry, claim, result.status
        )
        if claim.executor_kind is ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK:
            _validate_fallback_authorization(task, entry, claim)
        if (
            claim.executor_kind not in {
                ExecutorKind.CODEBUDDY_SUBAGENT,
                ExecutorKind.CODEBUDDY_ISOLATED_FALLBACK,
            }
            and result.status in {TaskStatus.SUCCEEDED, TaskStatus.NEEDS_REWORK}
        ):
            raise OrchestrationError(
                f"diagnostic executor kind {claim.executor_kind.value!r} cannot submit "
                f"{result.status.value} or promote formal artifacts; use codebuddy-subagent or "
                "the supervisor-authorized isolated fallback"
            )
        machine = _machine(manifest)
        expected_state = f"{ROLE_PHASE[task.agent_role].value.upper()}_RUNNING"
        if machine.state != expected_state or machine.active_phase is not ROLE_PHASE[task.agent_role]:
            raise OrchestrationError(
                f"task {task_id} requires {expected_state}; current workflow state is {machine.state}"
            )
        if result.status in {
            TaskStatus.NEEDS_REWORK,
            TaskStatus.FAILED,
            TaskStatus.EXTERNAL_BLOCKED,
        }:
            _clear_case_wave_reservations(manifest, task, entry, claim)
        elif result.status is TaskStatus.SUCCEEDED and task.agent_role is AgentRole.CASE_WORKER:
            _, _, wave_members = _case_wave_members(manifest, task, entry, claim)
            if wave_members[-1][0] == task.task_id:
                _clear_case_wave_reservations(manifest, task, entry, claim)
        if result.status is TaskStatus.NEEDS_REWORK:
            _validate_rework_requests(root, manifest, machine, task, result.rework_requests)
            guarded = [
                root / MANIFEST_NAME,
                root / STATE_NAME,
                root / "orchestration" / "review-report.json",
                root / "orchestration" / "results" / f"{task.task_id}.json",
                *(
                    root / "orchestration" / "rework-requests" / f"{request.request_id}.json"
                    for request in result.rework_requests
                ),
            ]
            with rollback_files_on_error(guarded):
                _store_result(root, manifest, task, result)
                _invalidate_for_rework(root, manifest, machine, result.rework_requests)
            return _orchestration_status_unlocked(root, manifest)
        if result.status is TaskStatus.EXTERNAL_BLOCKED:
            _store_result(root, manifest, task, result)
            change = machine.block_external(result.error_message or "external dependency")
            _save_machine(root, manifest, machine)
            _event_store(root).append("EXTERNAL_BLOCKED", change.to_dict(), task_id=task_id)
            return _orchestration_status_unlocked(root, manifest)
        if result.status is TaskStatus.FAILED:
            _store_result(root, manifest, task, result)
            if task.attempt >= _config(root).max_rework_attempts + 1:
                change = machine.fail(result.error_message or "agent task failed")
                _save_machine(root, manifest, machine)
                _event_store(root).append("RUN_FAILED", change.to_dict(), task_id=task_id)
            else:
                _event_store(root).append("TASK_FAILED_RETRYABLE", {"error": result.error_message, "attempt": task.attempt}, task_id=task_id)
                if task.agent_role is AgentRole.CASE_WORKER:
                    _ensure_case_tasks(root, manifest)
                else:
                    _ensure_role_task(
                        root,
                        manifest,
                        task.agent_role,
                        task.owner_key,
                        generation_fingerprint=task.agent_role is AgentRole.REVIEWER,
                    )
            return _orchestration_status_unlocked(root, manifest)

        accepted_fingerprint = _snapshot_accepted_outputs(root, task)
        mapping = _promotion_mapping(task, result)
        if task.agent_role is AgentRole.PLAN_DFX:
            _validate_plan_link_outputs(root, task)
        elif task.agent_role is AgentRole.RISK_ARBITER:
            _validate_risk_candidate_resolution(root, task)
        promotion_id = (
            _promotion_transaction_id(task.task_id, claim.execution_id) if mapping else None
        )
        promotion_receipt: PromotionReceipt | None = None
        promotion_manager = WorkspaceManager(root)
        if promotion_id:
            source_root = _prepare_promotion_sources(root, task, mapping, promotion_id)
            try:
                promotion_receipt = promotion_manager.atomic_promote(
                    task.agent_role.value,
                    task.task_id,
                    mapping,
                    source_root=source_root,
                    target_root=root,
                    transaction_id=promotion_id,
                    allowed_reserved_targets=("orchestration/review-report.json",),
                    metadata={
                        "execution_id": claim.execution_id,
                        "accepted_output_fingerprint": accepted_fingerprint,
                    },
                )
            except WorkspaceError as exc:
                raise OrchestrationError(
                    f"durable promotion failed for task {task.task_id}: {exc}"
                ) from exc
        try:
            if task.agent_role is AgentRole.DISCOVERY:
                _quiet(validate_batch_artifacts, root, "discovery", use_cache=False)
            elif task.agent_role is AgentRole.PLAN_DFX:
                _validate_plan_retained_outputs(root, task)
                _quiet(validate_batch_artifacts, root, "plan", use_cache=False)
            elif task.agent_role is AgentRole.RISK_ARBITER:
                validate_sheet_data_file(_accepted_output(root, task, "risks.json"))
                _quiet(validate_batch_artifacts, root, "risk", use_cache=False)
            elif task.agent_role is AgentRole.CASE_WORKER:
                from .case_merge import validate_worker_outputs

                validate_worker_outputs(
                    root,
                    task,
                    _accepted_output(root, task, "function_cases.json"),
                    _accepted_output(root, task, "case-traceability.json"),
                )
            result_record = _store_result(
                root, manifest, task, result, promotion_id=promotion_id
            )
        except BaseException:
            if promotion_receipt is not None:
                try:
                    promotion_manager.rollback_promotion(promotion_receipt)
                except WorkspaceError as rollback_exc:
                    raise OrchestrationError(
                        f"task {task.task_id} validation failed and its promotion could not be rolled back: "
                        f"{rollback_exc}"
                    ) from rollback_exc
                _cleanup_promotion_source(root, promotion_receipt.transaction_id)
            latest_manifest = _load_manifest(root)
            latest_entry = latest_manifest["tasks"][task_id]
            if latest_entry.get("status") == TaskStatus.SUCCEEDED.value:
                latest_entry["status"] = TaskStatus.FAILED.value
                latest_entry["invalidated_reason"] = (
                    "result persistence or deterministic validation did not complete"
                )
                _save_manifest(root, latest_manifest)
            raise
        manifest = _load_manifest(root)
        machine = _machine(manifest)
        try:
            if task.agent_role in {AgentRole.DISCOVERY, AgentRole.PLAN_DFX, AgentRole.RISK_ARBITER}:
                _validate_phase(root, manifest, machine, ROLE_PHASE[task.agent_role])
            elif task.agent_role is AgentRole.REVIEWER:
                _quiet(
                    validate_review_artifacts,
                    root,
                    allow_uncommitted_reviewer_task_id=task.task_id,
                )
                _validate_phase(root, manifest, machine, Phase.REVIEW)
        except BaseException:
            if promotion_receipt is not None:
                try:
                    promotion_manager.rollback_promotion(promotion_receipt)
                except WorkspaceError as rollback_exc:
                    raise OrchestrationError(
                        f"formal gate rejected task {task.task_id} and promotion rollback failed: "
                        f"{rollback_exc}"
                    ) from rollback_exc
                _cleanup_promotion_source(root, promotion_receipt.transaction_id)
            manifest = _load_manifest(root)
            manifest["tasks"][task_id]["status"] = "FAILED"
            manifest["tasks"][task_id]["invalidated_reason"] = (
                "deterministic formal gate rejected submitted result"
            )
            _save_manifest(root, manifest)
            raise
        result_fingerprint = str(
            _load_manifest(root)["tasks"][task_id].get("result_fingerprint") or ""
        )
        _event_store(root).append(
            "TASK_SUCCEEDED",
            {
                "result_path": result_record.relative_to(root).as_posix(),
                "result_fingerprint": result_fingerprint,
                "promotion_id": promotion_id,
                "accepted_output_fingerprint": accepted_fingerprint,
            },
            task_id=task_id,
        )
        if task.agent_role is AgentRole.REVIEWER:
            _quiet(validate_review_artifacts, root)
        if promotion_receipt is not None:
            try:
                promotion_manager.finalize_promotion(promotion_receipt)
            except WorkspaceError as exc:
                raise OrchestrationError(
                    f"task {task.task_id} succeeded but promotion finalization failed: {exc}"
                ) from exc
            _cleanup_promotion_source(root, promotion_receipt.transaction_id)
    return advance_orchestration(root)


def resume_external_block(run_dir: Path | str) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    with exclusive_process_lock(root / "orchestration" / ".orchestrator.lock"):
        _initialize_orchestration_unlocked(root)
        manifest = _load_manifest(root)
        machine = _machine(manifest)
        change = machine.resume_external()
        _save_machine(root, manifest, machine)
        _event_store(root).append("EXTERNAL_RESUMED", change.to_dict())
    return advance_orchestration(root)


__all__ = [
    "ARCHITECTURE",
    "OrchestrationError",
    "advance_orchestration",
    "claim_agent_task",
    "commit_page_probe_receipt",
    "complete_delivery_orchestration",
    "initialize_orchestration",
    "orchestration_exists",
    "orchestration_status",
    "orchestration_status_under_lock",
    "record_agent_dispatch_failure",
    "release_agent_claim",
    "resume_external_block",
    "submit_agent_result",
    "validate_delivery_running_state",
]
