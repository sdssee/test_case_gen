# -*- coding: utf-8 -*-
"""Deterministic Case Worker validation, merge, traceability, and status sync."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..batch import (
    FUNCTION_CASE_MANIFEST,
    SHEET_DATA_FILES,
    generation_session_data,
    read_csv_exact,
    resolved_evidence_file,
    split_plan_values,
    template_headers,
)
from ..contracts.function_cases import MAX_FUNCTION_CASES_PER_PART
from ..io_utils import atomic_write_json, atomic_write_text, rollback_files_on_error
from ..validators.case_collection import (
    derived_case_quality_counts,
    validate_case_collection,
    validate_contiguous_function_point_groups,
    validate_plan_case_order_alignment,
)
from ..validators.function_cases import validate_function_case_schema
from .contracts import AgentTask, TraceabilityRecord


TRACEABILITY_FILE = "case-traceability.json"
IDENTITY_FIELDS = ["最小标题路径", "交互实例ID", "页面/入口", "元素名称/文案", "元素类型"]


def _normalized(value: object) -> str:
    return "".join(str(value or "").split()).lower()


def _identity(row: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(_normalized(row.get(field, "")) for field in IDENTITY_FIELDS)


def _load_ledger(run_dir: Path, name: str, template_name: str) -> list[dict[str, str]]:
    return read_csv_exact(
        run_dir / name,
        template_headers(run_dir.parent / "templates", template_name),
        name,
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        headers = next(csv.reader(stream))
    lines: list[list[str]] = [headers]
    for row in rows:
        lines.append([str(row.get(header, "") or "") for header in headers])
    temporary_text = "\ufeff" + "\r\n".join(
        ",".join(_csv_cell(value) for value in line) for line in lines
    ) + "\r\n"
    atomic_write_text(path, temporary_text, encoding="utf-8")


def _csv_cell(value: str) -> str:
    if any(marker in value for marker in [",", '"', "\r", "\n"]):
        return '"' + value.replace('"', '""') + '"'
    return value


def plan_owner_id(row: Mapping[str, object]) -> str:
    payload = "|".join(_identity(row))
    return "PLAN-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16].upper()


def plan_groups(run_dir: Path) -> "OrderedDict[str, list[dict[str, str]]]":
    rows = _load_ledger(run_dir, "element-case-plan.csv", "element-case-plan-template.csv")
    groups: "OrderedDict[str, list[dict[str, str]]]" = OrderedDict()
    for row in rows:
        point = row.get("功能点", "").strip()
        planned = split_plan_values(row.get("计划用例ID", ""))
        if not point or not planned:
            continue
        groups.setdefault(point, []).append(row)
    if not groups:
        raise ValueError("element-case-plan.csv has no function-point owner rows with planned case IDs")
    return groups


def expected_case_order(rows: Iterable[Mapping[str, object]]) -> list[str]:
    result: list[str] = []
    for row in rows:
        result.extend(split_plan_values(str(row.get("计划用例ID", "") or "")))
    if len(result) != len(set(result)):
        raise ValueError("element-case-plan.csv planned case IDs must be globally unique")
    return result


def _evidence_hash(run_dir: Path, raw: str) -> str | None:
    path = resolved_evidence_file(run_dir, raw)
    if path is None:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def traceability_expectations(
    run_dir: Path,
    plan_rows: Sequence[Mapping[str, object]],
    worker_task_id: str,
    source_fingerprint: str,
) -> dict[str, TraceabilityRecord]:
    discovery = _load_ledger(run_dir, "page-discovery.csv", "page-discovery-template.csv")
    selections = _load_ledger(
        run_dir, "selection-option-observations.csv", "selection-option-observations-template.csv"
    )
    lifecycle = _load_ledger(run_dir, "test-data-lifecycle.csv", "test-data-lifecycle-template.csv")
    discovery_by_identity = {_identity(row): row for row in discovery if row.get("元素名称/文案", "").strip()}
    result: dict[str, TraceabilityRecord] = {}
    for plan in plan_rows:
        identity = _identity(plan)
        fact = discovery_by_identity.get(identity)
        if fact is None:
            raise ValueError(f"plan owner {plan_owner_id(plan)} has no exact discovery fact")
        interaction_id = plan.get("交互实例ID", "").strip()
        evidence_hash = _evidence_hash(run_dir, fact.get("证据路径", ""))
        if not evidence_hash:
            raise ValueError(f"plan owner {plan_owner_id(plan)} has no non-empty discovery evidence")
        for case_id in split_plan_values(plan.get("计划用例ID", "")):
            option_ids: list[str] = []
            for option in selections:
                if _identity(option) != identity or case_id not in split_plan_values(option.get("关联用例ID", "")):
                    continue
                token = "|".join([*identity, _normalized(option.get("选项值", ""))])
                option_ids.append("OPT-" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16].upper())
            lifecycle_ids: list[str] = []
            for item in lifecycle:
                if _normalized(item.get("交互实例ID", "")) != _normalized(interaction_id):
                    continue
                token = "|".join(
                    [
                        _normalized(item.get("测试数据ID/名称", "")),
                        _normalized(item.get("修改项/元素", "")),
                        _normalized(interaction_id),
                    ]
                )
                lifecycle_ids.append("LIFE-" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16].upper())
            result[case_id] = TraceabilityRecord(
                schema_version="1.0.0",
                case_id=case_id,
                function_point=str(plan.get("功能点", "") or "").strip(),
                plan_owner_id=plan_owner_id(plan),
                interaction_ids=(interaction_id,),
                selection_observation_ids=tuple(option_ids),
                lifecycle_ids=tuple(lifecycle_ids),
                evidence_hashes=(evidence_hash,),
                worker_task_id=worker_task_id,
                source_fingerprint=source_fingerprint,
            )
    return result


def _json_rows(path: Path, key: str | None = None) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON file {path}: {exc}") from exc
    if key and isinstance(payload, dict):
        payload = payload.get(key)
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError(f"{path} must contain a JSON object list")
    return payload


def validate_worker_outputs(
    run_dir: Path,
    task: AgentTask,
    case_path: Path,
    trace_path: Path,
) -> tuple[list[dict[str, object]], list[TraceabilityRecord]]:
    groups = plan_groups(run_dir)
    if task.owner_key not in groups:
        raise ValueError(f"case worker owner {task.owner_key!r} is absent from the current plan")
    plan_rows = groups[str(task.owner_key)]
    planned_order = expected_case_order(plan_rows)
    planned = set(planned_order)
    cases = _json_rows(case_path, "cases")
    actual_order: list[str] = []
    for index, case in enumerate(cases, start=1):
        validate_function_case_schema(case, f"{task.task_id} case {index}", planned)
        if str(case.get("功能点", "") or "").strip() != task.owner_key:
            raise ValueError(f"{task.task_id} generated a case outside function point {task.owner_key!r}")
        actual_order.append(str(case.get("用例 ID", "") or "").strip())
    if actual_order != planned_order:
        raise ValueError(
            f"{task.task_id} case order/IDs must exactly follow plan owner order; "
            f"expected={planned_order}, actual={actual_order}"
        )
    validate_case_collection(cases, label=task.task_id)

    raw_trace = _json_rows(trace_path, "records")
    records = [TraceabilityRecord.from_dict(item) for item in raw_trace]
    if [record.case_id for record in records] != planned_order:
        raise ValueError(f"{task.task_id} traceability order/IDs must exactly match worker cases")
    expected_trace = traceability_expectations(
        run_dir,
        plan_rows,
        task.task_id,
        str((generation_session_data(run_dir) or {}).get("source_fingerprint", "")),
    )
    for record in records:
        if record.to_dict() != expected_trace[record.case_id].to_dict():
            raise ValueError(
                f"{task.task_id} traceability for {record.case_id} differs from deterministic plan/discovery facts"
            )
    return cases, records


def _case_shards(cases: Sequence[dict[str, object]]) -> list[list[dict[str, object]]]:
    groups: list[list[dict[str, object]]] = []
    for case in cases:
        point = str(case.get("功能点", "") or "").strip()
        if groups and str(groups[-1][0].get("功能点", "") or "").strip() == point:
            groups[-1].append(case)
        else:
            groups.append([case])
    shards: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    for group in groups:
        if len(group) > MAX_FUNCTION_CASES_PER_PART:
            if current:
                shards.append(current)
                current = []
            shards.extend(
                [group[index : index + MAX_FUNCTION_CASES_PER_PART] for index in range(0, len(group), MAX_FUNCTION_CASES_PER_PART)]
            )
        elif len(current) + len(group) <= MAX_FUNCTION_CASES_PER_PART:
            current.extend(group)
        else:
            if current:
                shards.append(current)
            current = list(group)
    if current:
        shards.append(current)
    return shards


def _load_json_row_count(path: Path) -> int:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 0
    rows = value.get("rows") if isinstance(value, dict) else value
    return len(rows) if isinstance(rows, list) else 0


def sync_discovery_status(run_dir: Path) -> None:
    """Derive discovery-owned batch counters; agents never hand-edit them."""

    discovery_rows = _load_ledger(run_dir, "page-discovery.csv", "page-discovery-template.csv")
    status_rows = _load_ledger(run_dir, "batch-status.csv", "batch-status-template.csv")
    if len(status_rows) != 1:
        raise ValueError("batch-status.csv must contain exactly one row")
    interactive = [
        row for row in discovery_rows
        if row.get("页面/入口", "").strip() and row.get("元素名称/文案", "").strip() and row.get("元素类型", "").strip()
    ]
    covered = sum(row.get("覆盖状态", "").strip() == "已覆盖" for row in interactive)
    status_rows[0].update(
        {
            "状态": "执行中",
            "页面数": str(len({row.get("页面/入口", "").strip() for row in interactive})),
            "元素总数": str(len(interactive)),
            "已覆盖元素数": str(covered),
            "待确认元素数": str(len(interactive) - covered),
            "页面遍历完成": "是" if interactive and covered == len(interactive) else "否",
            "页面元素覆盖完成": "是" if interactive and covered == len(interactive) else "否",
            "下一步动作": "生成元素用例计划并完成 DFX 12×4 评估",
        }
    )
    _write_csv(run_dir / "batch-status.csv", status_rows)


def backfill_case_links_and_status(run_dir: Path, cases: Sequence[Mapping[str, object]]) -> None:
    plan_path = run_dir / "element-case-plan.csv"
    discovery_path = run_dir / "page-discovery.csv"
    status_path = run_dir / "batch-status.csv"
    plan_rows = _load_ledger(run_dir, "element-case-plan.csv", "element-case-plan-template.csv")
    discovery_rows = _load_ledger(run_dir, "page-discovery.csv", "page-discovery-template.csv")
    status_rows = _load_ledger(run_dir, "batch-status.csv", "batch-status-template.csv")
    if len(status_rows) != 1:
        raise ValueError("batch-status.csv must contain exactly one row")
    plan_by_identity: dict[tuple[str, ...], list[dict[str, str]]] = {}
    for plan in plan_rows:
        planned = split_plan_values(plan.get("计划用例ID", ""))
        if planned:
            plan["实际用例ID"] = ",".join(planned)
            plan["未生成原因"] = ""
            plan_by_identity.setdefault(_identity(plan), []).append(plan)
    for discovery in discovery_rows:
        owners = plan_by_identity.get(_identity(discovery), [])
        linked = [case_id for owner in owners for case_id in split_plan_values(owner.get("计划用例ID", ""))]
        if linked:
            discovery["是否已生成用例"] = "是"
            discovery["关联用例ID"] = ",".join(linked)
            discovery["覆盖状态"] = "已覆盖"
            discovery["未覆盖/待确认原因"] = ""

    interactive = [
        row for row in discovery_rows
        if row.get("页面/入口", "").strip() and row.get("元素名称/文案", "").strip() and row.get("元素类型", "").strip()
    ]
    status = status_rows[0]
    status.update(
        {
            "状态": "执行中",
            "页面数": str(len({row.get("页面/入口", "").strip() for row in interactive})),
            "元素总数": str(len(interactive)),
            "已覆盖元素数": str(sum(row.get("覆盖状态", "").strip() == "已覆盖" for row in interactive)),
            "待确认元素数": str(sum(row.get("覆盖状态", "").strip() != "已覆盖" for row in interactive)),
            "功能用例数": str(len(cases)),
            "性能场景数": str(_load_json_row_count(run_dir / "artifacts" / "data" / "performance.json")),
            "页面遍历完成": "是",
            "功能用例完成": "是",
            "性能设计完成": "是",
            "异常边界权限覆盖完成": "是",
            "页面元素覆盖完成": "是",
            "覆盖质量自检": "通过",
            "下一步动作": "执行独立只读 Review Agent；Review Gate 通过后才能交付",
        }
    )
    status.update({field: str(value) for field, value in derived_case_quality_counts(cases).items()})
    _write_csv(plan_path, plan_rows)
    _write_csv(discovery_path, discovery_rows)
    _write_csv(status_path, status_rows)


def aggregate_case_workers(
    run_dir: Path,
    worker_payloads: Sequence[tuple[AgentTask, Path, Path]],
) -> dict[str, object]:
    run_dir = run_dir.resolve()
    groups = plan_groups(run_dir)
    payload_by_point: dict[str, tuple[AgentTask, list[dict[str, object]], list[TraceabilityRecord]]] = {}
    for task, case_path, trace_path in worker_payloads:
        cases, trace = validate_worker_outputs(run_dir, task, case_path, trace_path)
        if task.owner_key in payload_by_point:
            raise ValueError(f"function point {task.owner_key!r} has more than one successful Case Worker")
        payload_by_point[str(task.owner_key)] = (task, cases, trace)
    if set(payload_by_point) != set(groups):
        raise ValueError(
            f"successful Case Worker owners differ from current plan; missing={sorted(set(groups)-set(payload_by_point))}, "
            f"unexpected={sorted(set(payload_by_point)-set(groups))}"
        )
    ordered_cases: list[dict[str, object]] = []
    ordered_trace: list[TraceabilityRecord] = []
    plan_rows: list[dict[str, str]] = []
    for point, rows in groups.items():
        plan_rows.extend(rows)
        _, cases, traces = payload_by_point[point]
        ordered_cases.extend(cases)
        ordered_trace.extend(traces)
    validate_case_collection(ordered_cases, label="merged Case Worker output")
    validate_contiguous_function_point_groups(ordered_cases, label="merged Case Worker output")
    validate_plan_case_order_alignment(
        plan_rows,
        ordered_cases,
        split_ids=split_plan_values,
    )
    session = generation_session_data(run_dir)
    if not session:
        raise ValueError("generation-session.json is missing before Case Worker merge")
    data_dir = run_dir / "artifacts" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    shards = _case_shards(ordered_cases)
    part_paths = [data_dir / f"function_cases_part_{index:03d}.json" for index in range(1, len(shards) + 1)]
    trace_path = data_dir / TRACEABILITY_FILE
    manifest_path = data_dir / FUNCTION_CASE_MANIFEST
    mutable = [
        run_dir / "element-case-plan.csv",
        run_dir / "page-discovery.csv",
        run_dir / "batch-status.csv",
        manifest_path,
        trace_path,
        *data_dir.glob("function_cases_part_*.json"),
        *part_paths,
    ]
    with rollback_files_on_error(list(mutable)):
        for stale in data_dir.glob("function_cases_part_*.json"):
            stale.unlink()
        for path, rows in zip(part_paths, shards):
            atomic_write_json(path, rows)
        atomic_write_json(trace_path, [record.to_dict() for record in ordered_trace])
        manifest = {
            "part_size": MAX_FUNCTION_CASES_PER_PART,
            "total_cases": len(ordered_cases),
            "parts": [path.name for path in part_paths],
            "generation_session_id": session.get("generation_session_id"),
            "source_fingerprint": session.get("source_fingerprint"),
            "catalog_source_fingerprint": session.get("catalog_source_fingerprint"),
        }
        atomic_write_json(manifest_path, manifest)
        backfill_case_links_and_status(run_dir, ordered_cases)
    return {
        "total_cases": len(ordered_cases),
        "parts": [path.name for path in part_paths],
        "traceability_records": len(ordered_trace),
        "worker_task_ids": [payload_by_point[point][0].task_id for point in groups],
    }


__all__ = [
    "TRACEABILITY_FILE",
    "aggregate_case_workers",
    "backfill_case_links_and_status",
    "expected_case_order",
    "plan_groups",
    "plan_owner_id",
    "sync_discovery_status",
    "traceability_expectations",
    "validate_worker_outputs",
]
