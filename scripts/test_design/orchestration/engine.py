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
import shutil
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
from ..io_utils import atomic_copy, atomic_write_json, exclusive_process_lock, rollback_files_on_error
from ..pipeline import derive_pipeline_status
from ..validation_cache import fingerprint
from ..validators.function_cases import validate_sheet_data_file
from .case_merge import (
    aggregate_case_workers,
    plan_groups,
    sync_discovery_status,
    traceability_expectations,
)
from .contracts import (
    AgentResult,
    AgentRole,
    AgentTask,
    ReworkRequest,
    ReworkTarget,
    RunConfig,
    TaskStatus,
    canonical_fingerprint,
)
from .event_store import EventStore
from .review import REQUIRED_REVIEW_CHECKS, review_source_fingerprint, validate_review_artifacts
from .state_machine import OrchestrationStateMachine, PHASE_ORDER, Phase
from .workspace import WorkspaceManager


ARCHITECTURE = "multi-agent-final"
MANIFEST_VERSION = 1
MANIFEST_NAME = "orchestration/run-manifest.json"
CONFIG_NAME = "orchestration/config.json"
STATE_NAME = "orchestration/state.json"
EVENTS_NAME = "orchestration/events.jsonl"
TASK_STATUSES = {
    "PENDING", "SUCCEEDED", "FAILED", "NEEDS_REWORK", "EXTERNAL_BLOCKED", "INVALIDATED"
}

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


def initialize_orchestration(
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
    ]:
        (root / relative).mkdir(parents=True, exist_ok=True)
    existing = _manifest_path(root)
    if existing.exists():
        manifest = _load_manifest(root)
        _config(root)
        events = _event_store(root)
        events.verify()
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
        audited_result_ids = {
            str(event.get("task_id"))
            for event in event_rows
            if event.get("event_type") in {
                "TASK_SUCCEEDED", "EXTERNAL_BLOCKED", "RUN_FAILED",
                "TASK_FAILED_RETRYABLE", "AUDIT_RESULT_RECOVERED",
            }
            and event.get("task_id")
        }
        for task_id, entry in manifest["tasks"].items():
            if not entry.get("result_path") or task_id in audited_result_ids:
                continue
            events.append(
                "AUDIT_RESULT_RECOVERED",
                {
                    "status": entry.get("status"),
                    "result_path": entry.get("result_path"),
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


def _rule_sources(run_dir: Path, names: Sequence[str]) -> list[Path]:
    project = _project_root(run_dir)
    result = [project / "VERSION", project / ".codebuddy" / "rules" / "test-design-rule.md"]
    result.extend(project / "docs" / "test-design" / "rules" / name for name in names)
    return result


def _catalog_sources(run_dir: Path) -> list[Path]:
    return [path for path in generation_catalog_paths(run_dir) if path.is_file()]


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
    return list(dict.fromkeys(result))


def _phase_sources(run_dir: Path, role: AgentRole) -> list[Path]:
    root_files: dict[AgentRole, list[str]] = {
        AgentRole.DISCOVERY: ["batch-scope.json"],
        AgentRole.PLAN_DFX: [
            "batch-scope.json", "page-element-inventory.csv", "page-discovery.csv",
            "selection-option-observations.csv", "test-data-lifecycle.csv",
        ],
        AgentRole.RISK_ARBITER: [
            "batch-scope.json", "page-discovery.csv", "element-case-plan.csv",
        ],
        AgentRole.CASE_WORKER: [
            "batch-scope.json", "page-element-inventory.csv", "page-discovery.csv",
            "selection-option-observations.csv", "element-case-plan.csv", "test-data-lifecycle.csv",
            "risk-confirmation.csv", "artifacts/data/generation-session.json",
        ],
        AgentRole.REVIEWER: [
            "batch-scope.json", "page-element-inventory.csv", "page-discovery.csv",
            "selection-option-observations.csv", "element-case-plan.csv", "test-data-lifecycle.csv",
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
    sources.extend(_rule_sources(run_dir, rule_names[role]))
    sources.extend(_catalog_sources(run_dir))
    if role is not AgentRole.DISCOVERY:
        sources.extend(
            _ledger_evidence_sources(
                run_dir,
                [
                    "page-element-inventory.csv", "page-discovery.csv",
                    "selection-option-observations.csv", "risk-confirmation.csv",
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
        "lifecycle_facts": lifecycle,
        "risk_facts": risks,
    }


def _case_worker_sources(run_dir: Path, owner_key: str) -> list[Path]:
    sources = [run_dir / "batch-scope.json", run_dir / "artifacts/data/generation-session.json"]
    sources.extend(_rule_sources(run_dir, ["case-design.md", "dfx-test-strategy.md", "data-safety.md"]))
    sources.extend(_catalog_sources(run_dir))
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
        shutil.copy2(resolved, target)
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
            "selection-option-observations.csv", "test-data-lifecycle.csv",
        ],
        AgentRole.PLAN_DFX: [
            "element-case-plan.csv", "selection-option-observations.csv", "test-data-lifecycle.csv",
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
    role: AgentRole,
    task_id: str,
    owner_key: str | None,
    source_fingerprint: str,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "architecture": ARCHITECTURE,
        "agent_role": role.value,
        "task_id": task_id,
        "owner_key": owner_key,
        "source_fingerprint": source_fingerprint,
        "write_policy": "只写本任务 output 目录；不得直接写正式账本、manifest、Excel 或产品事实",
    }
    if role is AgentRole.DISCOVERY:
        context["instructions"] = [
            "独立盘点后全量执行全部交互", "有限选择项逐项操作", "页面可验证问题自行验证",
            "创建成功后完成修改回显和生效闭环", "证据写入 output/evidence 或 output/screenshots",
        ]
    elif role is AgentRole.PLAN_DFX:
        context["instructions"] = [
            "只基于已冻结 discovery 事实生成计划", "完成 DFX 12×4 评估和预算",
            "输出全部非功能用例 Sheet JSON", "risk-candidates.json 使用 {candidates: []} 结构且每项包含 dfx_dimensions",
        ]
    elif role is AgentRole.RISK_ARBITER:
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
                "required_checks": list(REQUIRED_REVIEW_CHECKS),
                "generation_session": generation_session_data(run_dir),
                "review_source_fingerprint": review_source_fingerprint(run_dir),
                "generator_task_ids": _successful_case_task_ids(_load_manifest(run_dir)),
                "instructions": ["只读审查，不修改正式产物", "有问题输出 NEEDS_REWORK；无问题输出 APPROVED 报告"],
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
    atomic_write_json(context_path, _task_context(run_dir, role, task_id, owner_key, source_fp))
    task_path = run_dir / "orchestration" / "tasks" / f"{task_id}.json"
    atomic_write_json(task_path, task.to_dict())
    manifest["tasks"][task_id] = {
        "task": task.to_dict(),
        "status": "PENDING",
        "required_outputs": outputs,
        "source_paths": source_paths,
        "input_snapshot_fingerprint": fingerprint([run_dir / path for path in input_files]),
        "task_packet_fingerprint": fingerprint([task_packet_path]),
        "context_fingerprint": fingerprint([context_path]),
        "review_input_fingerprint": review_source_fingerprint(run_dir) if role is AgentRole.REVIEWER else None,
        "result_path": None,
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
            entry.get("status") == "PENDING"
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
    path = _accepted_output(run_dir, task, "risk-candidates.json")
    value = _load_json(path, "risk-candidates.json")
    if not isinstance(value, dict) or set(value) != {"candidates"} or not isinstance(value["candidates"], list):
        raise OrchestrationError("risk-candidates.json must be exactly {\"candidates\": [...]} ")
    if any(not isinstance(item, dict) for item in value["candidates"]):
        raise OrchestrationError("risk-candidates.json candidates must be objects")
    return list(value["candidates"])


def _validate_risk_candidates_file(path: Path) -> list[dict[str, Any]]:
    value = _load_json(path, "risk-candidates.json")
    if not isinstance(value, dict) or set(value) != {"candidates"} or not isinstance(value["candidates"], list):
        raise OrchestrationError("risk-candidates.json must be exactly {\"candidates\": [...]} ")
    required = {
        "risk_id", "question", "page_verifiability", "page_action", "page_result",
        "external_reason", "affected_interaction_ids", "evidence", "dfx_dimensions",
    }
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
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
        candidates.append(raw)
    return candidates


def _validate_dfx_assessment(path: Path) -> dict[str, str]:
    value = _load_json(path, "dfx-assessment.json")
    if not isinstance(value, dict) or set(value) != {"dimensions"} or not isinstance(value["dimensions"], list):
        raise OrchestrationError("dfx-assessment.json must be exactly {\"dimensions\": [...]} ")
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
    return statuses


def _validate_plan_retained_outputs(run_dir: Path, task: AgentTask) -> None:
    statuses = _validate_dfx_assessment(_accepted_output(run_dir, task, "dfx-assessment.json"))
    candidates = _validate_risk_candidates_file(_accepted_output(run_dir, task, "risk-candidates.json"))
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


def _promote_retained_sheet_json(run_dir: Path, manifest: Mapping[str, Any]) -> None:
    plan_entry = _latest_success_entry(run_dir, manifest, AgentRole.PLAN_DFX)
    if not plan_entry:
        raise OrchestrationError("cases phase requires a successful Plan & DFX task")
    plan_task, _ = plan_entry
    risk_entry = _latest_success_entry(run_dir, manifest, AgentRole.RISK_ARBITER)
    data_dir = run_dir / "artifacts" / "data"
    for name in ["overview.json", "requirements.json", "scenarios.json", "performance.json", "automation.json", "page_elements.json"]:
        atomic_copy(_accepted_output(run_dir, plan_task, name), data_dir / name)
    atomic_copy(_accepted_output(run_dir, plan_task, "dfx-assessment.json"), data_dir / "dfx-assessment.json")
    risk_source = _accepted_output(run_dir, risk_entry[0], "risks.json") if risk_entry else _accepted_output(run_dir, plan_task, "risks.json")
    atomic_copy(risk_source, data_dir / "risks.json")


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
        current = (
            _generation_task_fingerprint(run_dir)
            if generation_fingerprint
            else fingerprint(_resolve_source_paths(run_dir, entry))
        )
        if current == pending.source_fingerprint:
            return pending
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
        if success and success[0].source_fingerprint != generation_fp:
            manifest["tasks"][success[0].task_id]["status"] = "INVALIDATED"
            manifest["tasks"][success[0].task_id]["invalidated_reason"] = "generation source changed"
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


def _runnable_tasks(run_dir: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    pending = [
        AgentTask.from_dict(entry["task"])
        for entry in manifest["tasks"].values()
        if entry.get("status") == "PENDING"
    ]
    pending.sort(key=lambda task: (list(AgentRole).index(task.agent_role), task.task_id))
    case_tasks = [task for task in pending if task.agent_role is AgentRole.CASE_WORKER]
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


def orchestration_status(run_dir: Path | str) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    manifest = initialize_orchestration(root)
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
        "delivery_command": (
            f'scripts/run-test-design.ps1 complete-deliverables --run-dir "{root}" '
            '--module-path "<模块路径>" --batch-id <批次ID>'
            if state["state"] == "DELIVERY_RUNNING" else ""
        ),
    }


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
    initialize_orchestration(root)
    lock = root / "orchestration" / ".orchestrator.lock"
    with exclusive_process_lock(lock):
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
            return orchestration_status(root)
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
                if not generation_session_is_current(root):
                    _quiet(prepare_function_case_generation, root)
                    _promote_retained_sheet_json(root, manifest)
                    _event_store(root).append("CASE_GENERATION_PREPARED", generation_session_data(root) or {})
                elif any(not (root / "artifacts" / "data" / name).is_file() for name in SHEET_DATA_FILES):
                    _promote_retained_sheet_json(root, manifest)
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
        return orchestration_status(root)


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


def _task_inputs_still_current(
    run_dir: Path,
    task: AgentTask,
    entry: Mapping[str, Any],
) -> bool:
    originals = _resolve_source_paths(run_dir, entry)
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
        elif original.read_bytes() != snapshot.read_bytes():
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
    if task.agent_role is AgentRole.REVIEWER:
        current_review = review_source_fingerprint(run_dir)
        if current_review != entry.get("review_input_fingerprint"):
            raise OrchestrationError("Reviewer input changed after dispatch; create a fresh Review task")
    if task.agent_role in {AgentRole.CASE_WORKER, AgentRole.REVIEWER}:
        current = _generation_task_fingerprint(run_dir)
    else:
        current = fingerprint(_resolve_source_paths(run_dir, entry))
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
            "element-case-plan.csv", "selection-option-observations.csv", "test-data-lifecycle.csv"
        }:
            mapping[relative] = relative
        elif task.agent_role is AgentRole.RISK_ARBITER and relative == "risk-confirmation.csv":
            mapping[relative] = relative
    return mapping


def _store_result(run_dir: Path, manifest: dict[str, Any], task: AgentTask, result: AgentResult) -> Path:
    path = run_dir / "orchestration" / "results" / f"{task.task_id}.json"
    atomic_write_json(path, result.to_dict())
    entry = manifest["tasks"][task.task_id]
    entry["result_path"] = path.relative_to(run_dir).as_posix()
    entry["status"] = result.status.value
    entry["output_fingerprint"] = WorkspaceManager(run_dir).fingerprint_outputs(
        task.agent_role.value, task.task_id
    )
    if result.status is TaskStatus.SUCCEEDED:
        accepted_paths = [path for path in _accepted_root(run_dir, task).rglob("*") if path.is_file()]
        entry["accepted_output_root"] = _accepted_root(run_dir, task).relative_to(run_dir).as_posix()
        entry["accepted_output_fingerprint"] = fingerprint(accepted_paths)
    _save_manifest(run_dir, manifest)
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
        if target_entry.get("status") not in {"PENDING", "SUCCEEDED"}:
            raise OrchestrationError(
                f"rework request {request.request_id} target task is not currently active/successful"
            )
        if request.target_phase is ReworkTarget.CASES:
            if request.target_task_id not in manifest.get("case_task_order", []):
                raise OrchestrationError(
                    f"rework request {request.request_id} target is not a current case_task_order worker"
                )


def submit_agent_result(
    run_dir: Path | str,
    task_id: str,
    result_path: Path | str,
) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    initialize_orchestration(root)
    lock = root / "orchestration" / ".orchestrator.lock"
    with exclusive_process_lock(lock):
        manifest = _load_manifest(root)
        entry = manifest["tasks"].get(task_id)
        if not isinstance(entry, dict):
            raise OrchestrationError(f"unknown task ID: {task_id}")
        if entry.get("status") != "PENDING":
            raise OrchestrationError(f"task {task_id} is not pending: {entry.get('status')}")
        task = AgentTask.from_dict(entry["task"])
        try:
            result = AgentResult.from_dict(_load_json(Path(result_path), "AgentResult"))
        except (TypeError, ValueError) as exc:
            raise OrchestrationError(f"invalid AgentResult: {exc}") from exc
        _validate_result_files(root, task, entry, result)
        machine = _machine(manifest)
        expected_state = f"{ROLE_PHASE[task.agent_role].value.upper()}_RUNNING"
        if machine.state != expected_state or machine.active_phase is not ROLE_PHASE[task.agent_role]:
            raise OrchestrationError(
                f"task {task_id} requires {expected_state}; current workflow state is {machine.state}"
            )
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
            return orchestration_status(root)
        if result.status is TaskStatus.EXTERNAL_BLOCKED:
            _store_result(root, manifest, task, result)
            change = machine.block_external(result.error_message or "external dependency")
            _save_machine(root, manifest, machine)
            _event_store(root).append("EXTERNAL_BLOCKED", change.to_dict(), task_id=task_id)
            return orchestration_status(root)
        if result.status is TaskStatus.FAILED:
            _store_result(root, manifest, task, result)
            if task.attempt >= _config(root).max_rework_attempts + 1:
                change = machine.fail(result.error_message or "agent task failed")
                _save_machine(root, manifest, machine)
                _event_store(root).append("RUN_FAILED", change.to_dict(), task_id=task_id)
            else:
                _event_store(root).append("TASK_FAILED_RETRYABLE", {"error": result.error_message, "attempt": task.attempt}, task_id=task_id)
                _ensure_role_task(
                    root,
                    manifest,
                    task.agent_role,
                    task.owner_key,
                    generation_fingerprint=task.agent_role in {AgentRole.CASE_WORKER, AgentRole.REVIEWER},
                )
            return orchestration_status(root)

        accepted_fingerprint = _snapshot_accepted_outputs(root, task)
        promotion_id: str | None = None
        mapping = _promotion_mapping(task, result)
        if task.agent_role is AgentRole.PLAN_DFX:
            _validate_plan_link_outputs(root, task)
        elif task.agent_role is AgentRole.RISK_ARBITER:
            _validate_risk_candidate_resolution(root, task)
        promotion_targets = [root / destination for destination in mapping.values()]
        if mapping:
            promotion_id = uuid4().hex
        promotion_receipt_path = (
            root / "orchestration" / "promotions" / f"{promotion_id}.json"
            if promotion_id else None
        )
        status_guard = (
            [root / "batch-status.csv"]
            if task.agent_role is AgentRole.DISCOVERY
            else [root / "orchestration" / "review-report.json"]
            if task.agent_role is AgentRole.REVIEWER
            else []
        )
        status_guard.extend(promotion_targets)
        status_guard.extend(
            [
                root / MANIFEST_NAME,
                root / "orchestration" / "results" / f"{task.task_id}.json",
            ]
        )
        if promotion_receipt_path:
            status_guard.append(promotion_receipt_path)
        with rollback_files_on_error(status_guard):
            for source_name, destination in mapping.items():
                atomic_copy(_accepted_output(root, task, source_name), root / destination)
            if promotion_receipt_path:
                atomic_write_json(
                    promotion_receipt_path,
                    {
                        "schema_version": 1,
                        "transaction_id": promotion_id,
                        "task_id": task.task_id,
                        "agent_role": task.agent_role.value,
                        "accepted_output_fingerprint": accepted_fingerprint,
                        "mapping": mapping,
                        "created_at": _now(),
                    },
                )
            if task.agent_role is AgentRole.DISCOVERY:
                sync_discovery_status(root)
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
            elif task.agent_role is AgentRole.REVIEWER:
                # Agent workspaces are intentionally forbidden from targeting
                # orchestration metadata.  The trusted orchestrator performs
                # this one explicit atomic copy under rollback protection.
                atomic_copy(
                    _accepted_output(root, task, "review-report.json"),
                    root / "orchestration" / "review-report.json",
                )
            result_record = _store_result(root, manifest, task, result)
            if promotion_id:
                manifest = _load_manifest(root)
                manifest["tasks"][task_id]["promotion_ids"].append(promotion_id)
                _save_manifest(root, manifest)
        manifest = _load_manifest(root)
        machine = _machine(manifest)
        if task.agent_role in {AgentRole.DISCOVERY, AgentRole.PLAN_DFX, AgentRole.RISK_ARBITER}:
            _validate_phase(root, manifest, machine, ROLE_PHASE[task.agent_role])
        elif task.agent_role is AgentRole.REVIEWER:
            try:
                _quiet(validate_review_artifacts, root)
            except BaseException:
                # Keep the immutable result for diagnosis but remove an unapproved report.
                (root / "orchestration" / "review-report.json").unlink(missing_ok=True)
                manifest = _load_manifest(root)
                manifest["tasks"][task_id]["status"] = "FAILED"
                manifest["tasks"][task_id]["invalidated_reason"] = "deterministic Review Gate rejected report"
                _save_manifest(root, manifest)
                raise
            _validate_phase(root, manifest, machine, Phase.REVIEW)
        _event_store(root).append(
            "TASK_SUCCEEDED",
            {
                "result_path": result_record.relative_to(root).as_posix(),
                "promotion_id": promotion_id,
                "accepted_output_fingerprint": accepted_fingerprint,
            },
            task_id=task_id,
        )
    return advance_orchestration(root)


def resume_external_block(run_dir: Path | str) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    initialize_orchestration(root)
    with exclusive_process_lock(root / "orchestration" / ".orchestrator.lock"):
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
    "initialize_orchestration",
    "orchestration_exists",
    "orchestration_status",
    "resume_external_block",
    "submit_agent_result",
]
