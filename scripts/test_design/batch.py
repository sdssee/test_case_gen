# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from .io_utils import atomic_copy, atomic_write_text, temporary_sibling
from .discovery_control import (
    assert_discovery_execution_complete,
    discovery_control_enabled,
    initialize_discovery_control,
)
from .contracts.function_cases import (
    FUNCTION_CASE_PART_RE,
    MAX_FUNCTION_CASES_PER_PART,
)
from .validators.function_cases import validate_function_case_part, validate_sheet_data_file
from .validators.case_collection import (
    derived_case_quality_counts,
    validate_case_collection,
    validate_contiguous_function_point_groups,
    validate_discovery_plan_case_alignment,
    validate_function_point_aware_shards,
    validate_plan_case_order_alignment,
    validate_plan_function_point_alignment,
)
from .validation_cache import cache_hit, fingerprint, record_success
from .validators.batch_ledgers import (
    is_selection_control,
    risk_confirmation_state,
    validate_single_batch_scope,
    validate_lifecycle_rows,
    validate_interaction_branch_rows,
    validate_branch_plan_links,
    validate_branch_case_grounding,
    validate_page_element_inventory,
    validate_discovery_rows,
    validate_mutation_discovery_evidence,
    validate_operation_plan_rows,
    validate_risk_confirmation,
    validate_selection_case_grounding,
    validate_selection_option_rows,
    validate_selection_plan_links,
)


def canonical_module_parts(module_path: str, product_name: str | None = None) -> list[str]:
    parts = [part.strip() for part in module_path.replace("\\", ">").replace("/", ">").split(">") if part.strip()]
    if product_name and parts and parts[0] == product_name.strip():
        parts = parts[1:]
    return parts


def split_module_parts(module_path: str, product_name: str | None = None) -> tuple[str, list[str]]:
    if product_name:
        return product_name, canonical_module_parts(module_path, product_name)
    parts = canonical_module_parts(module_path)
    if len(parts) >= 4:
        return parts[0], parts[1:]
    return (parts[0] if parts else "产品"), parts


def batch_scope_data(run_dir: Path) -> dict[str, object] | None:
    path = run_dir.resolve() / BATCH_SCOPE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    return data


def write_batch_scope(
    run_dir: Path,
    *,
    run_id: str,
    batch_id: str,
    module_path: str,
    product_name: str | None,
) -> dict[str, object]:
    product, modules = split_module_parts(module_path, product_name)
    leaf_path = ">".join(modules) or module_path.strip()
    scope: dict[str, object] = {
        "version": 1,
        "run_id": run_id,
        "batch_id": batch_id,
        "product_name": product,
        "module_path": leaf_path,
        "requested_module_path": module_path.strip(),
        "product_name_source": "explicit" if product_name else "derived",
    }
    atomic_write_text(
        run_dir.resolve() / BATCH_SCOPE,
        json.dumps(scope, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return scope


def validate_batch_scope(run_dir: Path, batch_id: str, leaf_path: str) -> dict[str, object]:
    scope = batch_scope_data(run_dir)
    if scope is None:
        raise ValueError(f"{BATCH_SCOPE} is missing or invalid; resume the batch with its original --product-name")
    if str(scope.get("batch_id", "")).strip() != batch_id:
        raise ValueError(f"{BATCH_SCOPE} batch_id does not match batch-status.csv")
    if str(scope.get("module_path", "")).strip() != leaf_path:
        raise ValueError(f"{BATCH_SCOPE} module_path does not match the batch leaf scope")
    if not str(scope.get("product_name", "")).strip():
        raise ValueError(f"{BATCH_SCOPE} must preserve a non-empty product_name")
    return scope


def copy_template_if_missing(source: Path, target: Path) -> bool:
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_copy(source, target)
    return True


def write_single_csv_row(path: Path, values: dict[str, str]) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        headers = next(reader, [])
    if not headers:
        raise ValueError(f"CSV template has no header row: {path}")
    row = {header: "" for header in headers}
    for key, value in values.items():
        if key in row:
            row[key] = value
    temporary = temporary_sibling(path)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=headers)
            writer.writeheader()
            writer.writerow(row)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


CSV_REQUIRED_FILES = {
    "batch-status.csv": "batch-status-template.csv",
    "page-element-inventory.csv": "page-element-inventory-template.csv",
    "page-discovery.csv": "page-discovery-template.csv",
    "selection-option-observations.csv": "selection-option-observations-template.csv",
    "interaction-branch-observations.csv": "interaction-branch-observations-template.csv",
    "element-case-plan.csv": "element-case-plan-template.csv",
    "test-data-lifecycle.csv": "test-data-lifecycle-template.csv",
    "risk-confirmation.csv": "risk-confirmation-template.csv",
}

FUNCTION_CASE_MANIFEST = "function_cases_manifest.json"
GENERATION_SESSION = "generation-session.json"
DELIVERY_RECEIPT = "delivery-receipt.json"
BATCH_SCOPE = "batch-scope.json"
SHEET_DATA_FILES = [
    "overview.json",
    "requirements.json",
    "scenarios.json",
    "performance.json",
    "risks.json",
    "automation.json",
    "page_elements.json",
]

GENERATION_LEDGER_INCLUDE_FIELDS = {
    "batch-status.csv": {
        "批次ID", "一级模块", "二级菜单", "三级菜单/页面域", "批次范围", "页面数", "元素总数", "最小标题路径", "待确认问题",
    },
}
GENERATION_LEDGER_RESULT_FIELDS = {
    "page-discovery.csv": {"是否已生成用例", "关联用例ID", "覆盖状态", "未覆盖/待确认原因"},
    "element-case-plan.csv": {"实际用例ID", "未生成原因"},
    "test-data-lifecycle.csv": {"创建步骤关联用例"},
}

INTERACTIVE_ELEMENT_MARKERS = [
    "按钮",
    "输入",
    "下拉",
    "选择",
    "单选",
    "复选",
    "开关",
    "分页",
    "页码",
    "弹窗",
    "表格",
    "链接",
    "上传",
    "编辑",
    "删除",
    "保存",
    "创建",
    "新增",
    "添加",
    "提交",
    "测试",
    "搜索",
    "筛选",
]


def read_csv_exact(path: Path, expected_headers: list[str], label: str) -> list[dict[str, str]]:
    if not path.exists():
        raise ValueError(f"{label} not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{label} has no header row: {path}") from exc
        if headers != expected_headers:
            raise ValueError(f"{label} header must match the standard template exactly. Expected {expected_headers}, got {headers}")
        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != len(headers):
                raise ValueError(f"{label} row {index} column count mismatch: expected {len(headers)}, got {len(row)}")
            rows.append({header: row[col].strip() for col, header in enumerate(headers)})
    return rows


def template_headers(templates_dir: Path, template_name: str) -> list[str]:
    template_path = templates_dir / template_name
    if not template_path.exists():
        raise ValueError(f"Batch template not found: {template_path}")
    with template_path.open("r", encoding="utf-8-sig", newline="") as fp:
        return next(csv.reader(fp))


def infer_operation_category(row: dict[str, str]) -> str:
    combined = "\n".join(row.values())
    mappings = [
        ("配置", ["配置", "开关", "认证", "变量", "路由"]),
        ("状态变更", ["启用", "停用", "发布", "下线", "审批", "状态"]),
        ("删除", ["删除", "移除"]),
        ("编辑", ["编辑", "修改", "保存"]),
        ("创建", ["新增", "创建", "添加", "新建"]),
        ("分页", ["分页", "翻页", "页码", "每页"]),
        ("筛选", ["筛选"]),
        ("搜索", ["搜索", "查询"]),
        ("导入", ["导入"]),
        ("导出", ["导出"]),
        ("上传", ["上传"]),
        ("下载", ["下载"]),
    ]
    for category, markers in mappings:
        if contains_any(combined, markers):
            return category
    return "查看"


def migrate_structured_batch_ledgers(run_dir: Path, templates_dir: Path) -> None:
    migrations = {
        "page-discovery.csv": "page-discovery-template.csv",
        "selection-option-observations.csv": "selection-option-observations-template.csv",
        "element-case-plan.csv": "element-case-plan-template.csv",
        "test-data-lifecycle.csv": "test-data-lifecycle-template.csv",
    }
    compatible_missing_field_sets = {
        "page-discovery.csv": [
            {"证据定位"},
            {"交互实例ID"},
            {"证据定位", "交互实例ID"},
            {"操作步骤锚点", "预期结果锚点"},
            {"证据定位", "操作步骤锚点", "预期结果锚点"},
            {"交互实例ID", "操作步骤锚点", "预期结果锚点"},
            {"证据定位", "交互实例ID", "操作步骤锚点", "预期结果锚点"},
        ],
        "selection-option-observations.csv": [
            {"交互实例ID"},
            {"预期结果锚点"},
            {"交互实例ID", "预期结果锚点"},
        ],
        "element-case-plan.csv": [
            {"交互实例ID"},
            {"操作类别", "验证要求", "数据策略", "执行状态"},
            {"交互实例ID", "操作类别", "验证要求", "数据策略", "执行状态"},
        ],
        "test-data-lifecycle.csv": [
            {"关联页面/入口", "修改项/元素", "保存后回显", "实际生效结果"},
            {"交互实例ID"},
            {"交互实例ID", "关联页面/入口", "修改项/元素", "保存后回显", "实际生效结果"},
        ],
    }
    pending: list[tuple[Path, list[str], list[dict[str, str]], str]] = []
    for filename, template_name in migrations.items():
        path = run_dir / filename
        expected_headers = template_headers(templates_dir, template_name)
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            current_headers = reader.fieldnames or []
            rows = list(reader)
        if current_headers == expected_headers:
            continue
        known_legacy_headers = [
            [header for header in expected_headers if header not in missing_fields]
            for missing_fields in compatible_missing_field_sets[filename]
        ]
        if current_headers not in known_legacy_headers:
            raise ValueError(f"Cannot automatically migrate unsupported {filename} header: {current_headers}")
        pending.append((path, expected_headers, rows, filename))

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    for path, expected_headers, rows, filename in pending:
        backup_path = path.with_name(f"{path.stem}.pre-structured-ledger-{timestamp}.csv")
        shutil.copy2(path, backup_path)
        migrated_rows: list[dict[str, str]] = []
        for old in rows:
            row = {header: old.get(header, "") for header in expected_headers}
            if filename == "page-discovery.csv":
                row["证据定位"] = ""
                row["交互实例ID"] = old.get("交互实例ID", "")
                row["操作步骤锚点"] = old.get("操作步骤锚点", "")
                row["预期结果锚点"] = old.get("预期结果锚点", "")
                row["备注"] = (
                    ((old.get("备注", "") + "；") if old.get("备注") else "")
                    + "旧账本自动迁移：必须补录交互实例ID、步骤/结果锚点和独立证据定位，不继承或伪造已执行事实"
                )
            elif filename == "selection-option-observations.csv":
                row["交互实例ID"] = old.get("交互实例ID", "")
                row["预期结果锚点"] = old.get("预期结果锚点", "")
                row["备注"] = (
                    ((old.get("备注", "") + "；") if old.get("备注") else "")
                    + "旧账本自动迁移：需按 page-discovery.csv 补录交互实例ID，并从真实页面结果补录预期结果锚点"
                )
            elif filename == "element-case-plan.csv":
                category = infer_operation_category(old)
                required = {
                    "创建": "回显,持久化",
                    "编辑": "回显,持久化,实际生效",
                    "配置": "回显,持久化,实际生效",
                    "状态变更": "回显,持久化,实际生效",
                    "删除": "持久化,确认取消",
                }.get(category, "结果分支")
                row.update(
                    {
                        "交互实例ID": old.get("交互实例ID", ""),
                        "操作类别": old.get("操作类别", "") or category,
                        "验证要求": old.get("验证要求", "") or required,
                        "数据策略": old.get("数据策略", "") or ("本次创建测试数据" if category in {"创建", "编辑", "删除", "配置", "状态变更"} else "无数据变更"),
                        "执行状态": old.get("执行状态", "") or ("待执行" if any(old.values()) else "不适用"),
                        "备注": ((old.get("备注", "") + "；") if old.get("备注") else "") + "旧账本自动迁移，需按结构化字段复核",
                    }
                )
            else:
                row["交互实例ID"] = old.get("交互实例ID", "")
                row["备注"] = ((old.get("备注", "") + "；") if old.get("备注") else "") + "旧账本自动迁移，需逐修改项补充交互实例ID、页面、元素、回显和生效证据"
            migrated_rows.append(row)
        temporary = temporary_sibling(path)
        try:
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=expected_headers)
                writer.writeheader()
                writer.writerows(migrated_rows)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Migrated structured ledger and preserved backup: {backup_path}")


def split_plan_values(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、;；/\s]+", text or "") if item.strip()]


def parse_positive_int(value: str, label: str) -> int:
    try:
        number = int(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a positive integer, got: {value}") from exc
    if number <= 0:
        raise ValueError(f"{label} must be greater than 0, got: {value}")
    return number


def is_yes(value: str) -> bool:
    return (value or "").strip() in {"是", "Y", "Yes", "yes", "true", "True", "1"}


def is_no(value: str) -> bool:
    return (value or "").strip() in {"否", "N", "No", "no", "false", "False", "0", "不适用"}


def evidence_path_candidates(run_dir: Path, value: str) -> list[Path]:
    raw = (value or "").strip()
    if not raw or raw in {"待填写", "待补充", "无"}:
        return []
    path = Path(raw)
    if path.is_absolute():
        return [path]
    return [run_dir / path, run_dir.parents[3] / path]


def resolved_evidence_file(run_dir: Path, value: str) -> Path | None:
    allowed_root = (run_dir.resolve() / "artifacts").resolve()
    for candidate in evidence_path_candidates(run_dir, value):
        try:
            resolved = candidate.resolve()
            resolved.relative_to(allowed_root)
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved.stat().st_size > 0:
            return resolved
    return None


def evidence_path_exists(run_dir: Path, value: str) -> bool:
    return resolved_evidence_file(run_dir, value) is not None


def evidence_content_fingerprint(run_dir: Path, value: str) -> str | None:
    path = resolved_evidence_file(run_dir, value)
    if path is None:
        return None
    digest = hashlib.sha256()
    prefix = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if not prefix:
                prefix = chunk[:32]
            digest.update(chunk)
    lowered = prefix.lstrip().lower()
    is_image = bool(
        re.search(r"\.(?:png|jpe?g|gif|bmp|webp|svg|tiff?)$", path.name, re.IGNORECASE)
        or prefix.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*"))
        or (prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP")
        or lowered.startswith((b"<svg", b"<?xml")) and b"<svg" in lowered
    )
    return f"{'image' if is_image else 'file'}:{digest.hexdigest()}"


def contains_any(text: str, markers: list[str]) -> bool:
    return any(marker in (text or "") for marker in markers)


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def is_template_or_empty_row(row: dict[str, str], meaningful_fields: list[str]) -> bool:
    return not any((row.get(field) or "").strip() for field in meaningful_fields)


def is_interactive_discovery_row(row: dict[str, str]) -> bool:
    return bool(
        row.get("元素名称/文案", "").strip()
        and (row.get("元素类型", "").strip() or row.get("交互方式", "").strip())
    )


def dfx_pair_count(dimensions_text: str, scenarios_text: str) -> int:
    dimensions = split_plan_values(dimensions_text)
    scenarios = split_plan_values(scenarios_text)
    if not dimensions or not scenarios:
        return 0
    if len(dimensions) == len(scenarios):
        return len(list(zip(dimensions, scenarios)))
    return max(len(dimensions), len(scenarios))


def element_is_planned(discovery: dict[str, str], plan_rows: list[dict[str, str]]) -> bool:
    name = normalized_text(discovery.get("元素名称/文案", ""))
    if not name:
        return True
    for row in plan_rows:
        same_leaf = normalized_text(discovery.get("最小标题路径", "")) == normalized_text(row.get("最小标题路径", ""))
        same_interaction = normalized_text(discovery.get("交互实例ID", "")) == normalized_text(row.get("交互实例ID", ""))
        same_page = normalized_text(discovery.get("页面/入口", "")) == normalized_text(row.get("页面/入口", ""))
        same_name = name == normalized_text(row.get("元素名称/文案", ""))
        discovery_type = normalized_text(discovery.get("元素类型", ""))
        plan_type = normalized_text(row.get("元素类型", ""))
        same_type = bool(discovery_type and plan_type and discovery_type == plan_type)
        if same_leaf and same_interaction and same_page and same_name and same_type:
            return True
    return False


def minimum_cases_for_plan_row(row: dict[str, str]) -> int:
    element_type = row.get("元素类型", "")
    interaction = row.get("交互方式", "")
    function_point = row.get("功能点", "")
    direction = row.get("测试设计方向", "")
    element_name = row.get("元素名称/文案", "")
    dimensions = set(split_plan_values(row.get("适用DFX维度", "")))
    scenarios = set(split_plan_values(row.get("适用DFX场景", "")))
    combined = "\n".join([element_type, interaction, function_point, direction, element_name])

    minimum = 1
    if contains_any(combined, ["搜索", "筛选"]):
        minimum = max(minimum, 4)
    if contains_any(combined, ["输入", "文本框", "数字", "邮箱", "手机号", "URL", "地址"]):
        minimum = max(minimum, 3)
    if is_selection_control(row):
        minimum = max(minimum, 4)
    if contains_any(combined, ["分页", "翻页", "页码", "每页", "跳页"]):
        minimum = max(minimum, 5)
    if contains_any(combined, ["弹窗", "抽屉", "对话框"]):
        minimum = max(minimum, 3)
    if contains_any(combined, ["新增", "创建", "添加", "接入", "保存", "提交"]):
        minimum = max(minimum, 5)
    if contains_any(combined, ["编辑", "修改"]):
        minimum = max(minimum, 4)
    if "删除" in combined:
        minimum = max(minimum, 3)
    if contains_any(combined, ["表格", "行操作", "批量"]):
        minimum = max(minimum, 3)
    if contains_any(combined, ["密钥", "Token", "密码", "鉴权", "权限"]):
        minimum = max(minimum, 4)
    if is_yes(row.get("是否涉及CRUD闭环", "")):
        minimum = max(minimum, 5)
    if is_yes(row.get("是否涉及配置生效", "")):
        minimum = max(minimum, 4)

    if {"边界值", "异常输入", "逆向操作"} & scenarios:
        minimum = max(minimum, 3)
    if dimensions & {"DFS安全", "DFR可靠", "DFU可用", "DFB业务"}:
        minimum += 1
    if scenarios & {"数据一致", "幂等性", "权限控制", "数据脱敏", "错误提示", "业务流程", "数据准确"}:
        minimum += 1
    if dimensions & {"DFP性能", "DFX极端"}:
        # 性能和极端场景不进入功能用例，但必须进入性能设计/风险/自动化建议。
        minimum = max(minimum, 1)

    return max(minimum, dfx_pair_count(row.get("适用DFX维度", ""), row.get("适用DFX场景", "")) or 1)


def planned_case_id_count(value: str) -> int:
    ids = split_plan_values(value)
    return len(ids)


def planned_case_ids(plan_rows: list[dict[str, str]]) -> set[str]:
    ids: set[str] = set()
    for row in plan_rows:
        ids.update(split_plan_values(row.get("计划用例ID", "")))
    return ids


def manifest_parts(data_dir: Path) -> list[Path]:
    manifest = data_dir / FUNCTION_CASE_MANIFEST
    if not manifest.exists():
        raise ValueError(f"artifacts/data must contain {FUNCTION_CASE_MANIFEST}; Excel assembly must read the manifest, not glob stale shards")
    with manifest.open("r", encoding="utf-8-sig") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(
            f"{manifest} must be a JSON object containing parts, generation_session_id, and source_fingerprint"
        )
    raw_parts = data.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError(f"{manifest} must contain a non-empty parts list")
    result: list[Path] = []
    expected_names = [f"function_cases_part_{index:03d}.json" for index in range(1, len(raw_parts) + 1)]
    actual_names = [str(item).strip() for item in raw_parts]
    if actual_names != expected_names:
        raise ValueError(
            f"{manifest} parts must be unique, sequential, and ordered as 001..N; "
            f"expected={expected_names}, actual={actual_names}"
        )
    for item in raw_parts:
        name = str(item).strip()
        if not FUNCTION_CASE_PART_RE.match(name):
            raise ValueError(f"{manifest} contains invalid part name: {name}; use function_cases_part_001.json")
        path = data_dir / name
        if not path.exists():
            raise ValueError(f"{manifest} references missing function case shard: {name}")
        result.append(path)
    declared = {path.name for path in result}
    stale = sorted(path.name for path in data_dir.glob("function_cases_part_*.json") if path.name not in declared)
    if stale:
        raise ValueError(f"artifacts/data contains stale function case shards not listed in {FUNCTION_CASE_MANIFEST}: {stale[:10]}")
    return result


def prepare_function_case_generation(run_dir: Path) -> None:
    validate_batch_artifacts(run_dir, "risk", use_cache=True)
    data_dir = run_dir.resolve() / "artifacts" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for pattern in ["function_cases_part_*.json", FUNCTION_CASE_MANIFEST, GENERATION_SESSION, DELIVERY_RECEIPT, *SHEET_DATA_FILES]:
        for path in data_dir.glob(pattern):
            if path.is_file():
                removed.append(path.name)
                path.unlink()
    session = {
        "generation_session_id": str(uuid.uuid4()),
        "source_fingerprint": generation_source_fingerprint(run_dir.resolve()),
        "catalog_source_fingerprint": generation_catalog_fingerprint(run_dir.resolve()),
        "created_at": datetime.now().astimezone().isoformat(),
    }
    atomic_write_text(data_dir / GENERATION_SESSION, json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: prepared function case generation under {data_dir}; removed {len(removed)} stale file(s).")


def record_no_model_uncertainty(run_dir: Path) -> None:
    run_dir = run_dir.resolve()
    validate_batch_artifacts(run_dir, "plan", use_cache=True)
    with (run_dir / "risk-confirmation.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        existing_rows = list(csv.DictReader(stream))
    real_ids = {
        row.get("风险ID", "").strip()
        for row in existing_rows
        if row.get("风险ID", "").strip() not in {"", "RISK-PENDING", "RISK-NONE"}
    }
    if real_ids:
        raise ValueError(f"record-risk-none refuses to overwrite real model uncertainty rows: {sorted(real_ids)}")
    existing = existing_rows[0] if existing_rows else {}
    if existing.get("风险ID", "").strip() == "RISK-NONE":
        validate_batch_artifacts(run_dir, "risk", use_cache=True)
        print(f"OK: RISK-NONE already recorded: {run_dir}")
        return
    write_single_csv_row(
        run_dir / "risk-confirmation.csv",
        {
            "批次ID": existing.get("批次ID", "BATCH-001"),
            "风险ID": "RISK-NONE",
            "模型不理解内容/待确认问题": "全量深探后未发现需要用户解释的业务语义或规则歧义",
            "已完成深探依据": "discovery 与 plan 门禁已通过，全部可交互元素和 CRUD 生效证据已记录",
            "页面可验证性": "不适用",
            "页面验证动作": "不适用：不存在需要用户确认的问题",
            "页面验证结果": "不适用：页面可验证项均已由模型完成验证",
            "不可验证/外部依赖原因": "不适用：未遗留页面不可观察项",
            "用户确认结论": "无需用户确认",
            "处置策略": "按已验证的页面行为和业务资料继续设计用例",
            "是否阻塞用例设计": "否",
            "确认状态": "无需用户确认",
            "备注": "由 record-risk-none 在 plan 门禁通过后写入，不代表用户作出过确认",
        },
    )
    print(f"OK: recorded RISK-NONE without unnecessary user confirmation: {run_dir}")


def validation_input_paths(run_dir: Path, phase: str) -> list[Path]:
    templates_dir = run_dir.parent / "templates"
    paths = [run_dir / name for name in CSV_REQUIRED_FILES]
    paths.append(run_dir / BATCH_SCOPE)
    paths.extend(templates_dir / template for template in CSV_REQUIRED_FILES.values())
    paths.extend(
        [
            Path(__file__),
            Path(__file__).parent / "validation_cache.py",
            Path(__file__).parent / "validators" / "batch_ledgers.py",
            Path(__file__).parent / "validators" / "case_collection.py",
            Path(__file__).parent / "validators" / "function_cases.py",
            Path(__file__).parent / "contracts" / "function_cases.py",
            Path(__file__).parent / "contracts" / "sheet_data.py",
            run_dir / "artifacts",
            run_dir / "artifacts" / "scripts",
            run_dir / "artifacts" / "data",
            run_dir / "artifacts" / "screenshots",
        ]
    )
    for ledger_name in ["page-element-inventory.csv", "page-discovery.csv", "selection-option-observations.csv", "interaction-branch-observations.csv", "risk-confirmation.csv"]:
        ledger = run_dir / ledger_name
        if not ledger.exists():
            continue
        with ledger.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                raw = (row.get("证据路径", "") or "").strip()
                if raw:
                    paths.extend(evidence_path_candidates(run_dir, raw))
    case_count = 0
    if phase == "cases":
        data_dir = run_dir / "artifacts" / "data"
        paths.extend(data_dir.glob("*.json"))
        paths.extend(generation_catalog_paths(run_dir))
    return paths


def generation_source_paths(run_dir: Path) -> list[Path]:
    run_dir = run_dir.resolve()
    project_root = run_dir.parents[3]
    templates_dir = run_dir.parent / "templates"
    paths = [templates_dir / template for template in CSV_REQUIRED_FILES.values()]
    paths.append(run_dir / BATCH_SCOPE)
    paths.extend(
        [
            Path(__file__),
            Path(__file__).parent / "validation_cache.py",
            Path(__file__).parent / "validators" / "batch_ledgers.py",
            Path(__file__).parent / "validators" / "case_collection.py",
            Path(__file__).parent / "validators" / "function_cases.py",
            Path(__file__).parent / "contracts" / "function_cases.py",
            Path(__file__).parent / "contracts" / "sheet_data.py",
        ]
    )
    paths.extend(
        [
            project_root / "VERSION",
            project_root / "AGENTS.md",
            project_root / "CODEBUDDY.md",
            project_root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
            project_root / ".codebuddy" / "rules" / "test-design-rule.md",
            project_root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        ]
    )
    rule_dir = project_root / "docs" / "test-design" / "rules"
    if rule_dir.exists():
        paths.extend(rule_dir.glob("*.md"))
        paths.extend(rule_dir.glob("*.json"))
    for ledger_name in ["page-element-inventory.csv", "page-discovery.csv", "selection-option-observations.csv", "interaction-branch-observations.csv", "risk-confirmation.csv"]:
        ledger = run_dir / ledger_name
        if not ledger.exists():
            continue
        with ledger.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                raw = (row.get("证据路径", "") or "").strip()
                if raw:
                    paths.extend(evidence_path_candidates(run_dir, raw))
    return paths


def generation_ledger_semantics(run_dir: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in CSV_REQUIRED_FILES:
        path = run_dir / name
        if not path.exists():
            result[name] = None
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames or []
            included = GENERATION_LEDGER_INCLUDE_FIELDS.get(name)
            result_fields = GENERATION_LEDGER_RESULT_FIELDS.get(name, set())
            selected = [field for field in headers if (included is None or field in included) and field not in result_fields]
            result[name] = [
                {field: (row.get(field, "") or "").strip() for field in selected}
                for row in reader
            ]
    return result


def generation_source_fingerprint(run_dir: Path) -> str:
    run_dir = run_dir.resolve()
    digest = hashlib.sha256()
    digest.update(fingerprint(generation_source_paths(run_dir)).encode("ascii"))
    semantics = json.dumps(generation_ledger_semantics(run_dir), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest.update(semantics.encode("utf-8"))
    return digest.hexdigest()


def generation_catalog_paths(run_dir: Path) -> list[Path]:
    project_root = run_dir.resolve().parents[3]
    catalog = project_root / "docs" / "test-assets" / "catalog"
    paths = list(catalog.rglob("*.json")) if catalog.exists() else []
    paths.append(project_root / "docs" / "test-assets" / "product-map.xlsx")
    return paths


def generation_catalog_fingerprint(run_dir: Path) -> str:
    return fingerprint(generation_catalog_paths(run_dir.resolve()))


def generation_session_data(run_dir: Path) -> dict[str, object] | None:
    path = run_dir.resolve() / "artifacts" / "data" / GENERATION_SESSION
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def generation_session_core_is_current(run_dir: Path) -> bool:
    data = generation_session_data(run_dir)
    return bool(
        data
        and data.get("generation_session_id")
        and data.get("source_fingerprint") == generation_source_fingerprint(run_dir.resolve())
    )


def delivery_receipt_matches_generation_session(run_dir: Path, session: dict[str, object]) -> bool:
    run_dir = run_dir.resolve()
    status_path = run_dir / "batch-status.csv"
    receipt_path = run_dir / "artifacts" / "data" / DELIVERY_RECEIPT
    if not status_path.exists() or not receipt_path.exists():
        return False
    try:
        with status_path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    metadata_matches = bool(
        len(rows) == 1
        and rows[0].get("状态", "").strip() == "已完成"
        and isinstance(receipt, dict)
        and receipt.get("generation_session_id") == session.get("generation_session_id")
        and receipt.get("source_fingerprint") == session.get("source_fingerprint")
        and receipt.get("catalog_source_fingerprint") == session.get("catalog_source_fingerprint")
    )
    if not metadata_matches:
        return False
    product_map_path = str(receipt.get("product_map_path", "") or "").replace("\\", "/").strip()
    file_entries = receipt.get("files")
    if not product_map_path or not isinstance(file_entries, list):
        return False
    project_root = run_dir.parents[3]
    catalog_entries = []
    for entry in file_entries:
        if not isinstance(entry, dict):
            return False
        relative = str(entry.get("path", "") or "").replace("\\", "/").strip()
        if relative == product_map_path or relative.startswith("docs/test-assets/catalog/"):
            catalog_entries.append(entry)
    if not any(str(entry.get("path", "") or "").replace("\\", "/").strip() == product_map_path for entry in catalog_entries):
        return False
    for entry in catalog_entries:
        try:
            relative = Path(str(entry["path"]))
            path = (project_root / relative).resolve()
            path.relative_to(project_root.resolve())
            expected_size = int(entry["size"])
            expected_hash = str(entry["sha256"])
        except (KeyError, TypeError, ValueError):
            return False
        if not path.is_file() or path.stat().st_size != expected_size:
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            return False
    try:
        product_map = (project_root / Path(product_map_path)).resolve()
        product_map.relative_to(project_root.resolve())
        from .fact_store import validate_catalog

        validate_catalog(product_map, require_existing=True)
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def generation_session_is_current(run_dir: Path) -> bool:
    data = generation_session_data(run_dir)
    if not data or not generation_session_core_is_current(run_dir):
        return False
    if data.get("catalog_source_fingerprint") == generation_catalog_fingerprint(run_dir.resolve()):
        return True
    return delivery_receipt_matches_generation_session(run_dir, data)


def validate_batch_artifacts(run_dir: Path, phase: str = "cases", use_cache: bool = False) -> None:
    run_dir = run_dir.resolve()
    if not run_dir.exists():
        raise ValueError(f"Batch run directory not found: {run_dir}")
    if phase not in {"discovery", "plan", "risk", "cases"}:
        raise ValueError(f"Unsupported batch validation phase: {phase}")
    input_paths = validation_input_paths(run_dir, phase)
    current_fingerprint = fingerprint(input_paths)
    if use_cache and cache_hit(run_dir, phase, current_fingerprint):
        print(f"OK: batch artifacts reused cached {phase} validation: {run_dir}")
        return
    templates_dir = run_dir.parent / "templates"
    expected_headers = {
        csv_name: template_headers(templates_dir, template_name)
        for csv_name, template_name in CSV_REQUIRED_FILES.items()
    }

    batch_rows = read_csv_exact(run_dir / "batch-status.csv", expected_headers["batch-status.csv"], "batch-status.csv")
    inventory_rows = read_csv_exact(
        run_dir / "page-element-inventory.csv",
        expected_headers["page-element-inventory.csv"],
        "page-element-inventory.csv",
    )
    discovery_rows = read_csv_exact(run_dir / "page-discovery.csv", expected_headers["page-discovery.csv"], "page-discovery.csv")
    selection_option_rows = read_csv_exact(
        run_dir / "selection-option-observations.csv",
        expected_headers["selection-option-observations.csv"],
        "selection-option-observations.csv",
    )
    interaction_branch_rows = read_csv_exact(
        run_dir / "interaction-branch-observations.csv",
        expected_headers["interaction-branch-observations.csv"],
        "interaction-branch-observations.csv",
    )
    plan_rows = read_csv_exact(run_dir / "element-case-plan.csv", expected_headers["element-case-plan.csv"], "element-case-plan.csv")
    lifecycle_rows = read_csv_exact(run_dir / "test-data-lifecycle.csv", expected_headers["test-data-lifecycle.csv"], "test-data-lifecycle.csv")
    risk_confirmation_rows = read_csv_exact(
        run_dir / "risk-confirmation.csv",
        expected_headers["risk-confirmation.csv"],
        "risk-confirmation.csv",
    )
    run_batch_id, run_leaf_path = validate_single_batch_scope(
        batch_rows,
        {
            "page-element-inventory.csv": inventory_rows,
            "page-discovery.csv": discovery_rows,
            "selection-option-observations.csv": selection_option_rows,
            "interaction-branch-observations.csv": interaction_branch_rows,
            "element-case-plan.csv": plan_rows,
            "test-data-lifecycle.csv": lifecycle_rows,
            "risk-confirmation.csv": risk_confirmation_rows,
        },
    )
    validate_batch_scope(run_dir, run_batch_id, run_leaf_path)

    real_discovery_rows = [
        row for row in discovery_rows
        if not is_template_or_empty_row(row, ["页面/入口", "元素名称/文案", "元素类型", "交互方式"])
    ]
    if phase in {"discovery", "plan", "risk", "cases"} and not real_discovery_rows:
        raise ValueError("page-discovery.csv must contain real page elements before continuing")

    interactive_rows = [row for row in real_discovery_rows if is_interactive_discovery_row(row)]
    if phase in {"discovery", "plan", "risk", "cases"} and not interactive_rows:
        raise ValueError("page-discovery.csv must contain clickable/input/selectable/testable elements, not only static text")
    if phase in {"discovery", "plan", "risk", "cases"}:
        real_inventory_rows = [
            row for row in inventory_rows
            if not is_template_or_empty_row(row, ["页面/入口", "元素指纹", "元素名称/文案", "元素类型"])
        ]
        validate_discovery_rows(
            interactive_rows,
            lambda value: evidence_path_exists(run_dir, value),
            lambda value: evidence_content_fingerprint(run_dir, value),
        )
        validate_page_element_inventory(
            real_inventory_rows,
            interactive_rows,
            lambda value: evidence_path_exists(run_dir, value),
        )
        real_selection_option_rows = [
            row for row in selection_option_rows
            if not is_template_or_empty_row(row, ["页面/入口", "元素名称/文案", "选项值"])
        ]
        validate_selection_option_rows(
            interactive_rows,
            real_selection_option_rows,
            lambda value: evidence_path_exists(run_dir, value),
            lambda value: evidence_content_fingerprint(run_dir, value),
        )
        real_interaction_branch_rows = [
            row for row in interaction_branch_rows
            if not is_template_or_empty_row(row, ["页面/入口", "交互实例ID", "分支类别", "分支动作"])
        ]
        if discovery_control_enabled(run_dir) or real_interaction_branch_rows:
            validate_interaction_branch_rows(
                interactive_rows,
                real_selection_option_rows,
                real_interaction_branch_rows,
                lambda value: evidence_path_exists(run_dir, value),
            )
        # Legacy runs without a control config remain readable. Newly
        # initialized/resumed runs must close deterministic obligations first.
        assert_discovery_execution_complete(run_dir)

    confirmed_risk_rows: list[dict[str, str]] = []
    risk_case_ids: set[str] = set()
    if phase in {"plan", "risk", "cases"}:
        real_plan_rows = [
            row for row in plan_rows
            if not is_template_or_empty_row(row, ["页面/入口", "功能点", "元素名称/文案", "元素类型", "测试设计方向", "计划用例ID"])
        ]
        if not real_plan_rows:
            raise ValueError("element-case-plan.csv must contain real element-driven case plans before writing cases")

        missing_plan = [
            row.get("元素名称/文案", "")
            for row in interactive_rows
            if row.get("元素名称/文案", "").strip() and not element_is_planned(row, real_plan_rows)
        ]
        if missing_plan:
            preview = ", ".join(missing_plan[:10])
            raise ValueError(f"element-case-plan.csv is missing interactive page elements from page-discovery.csv: {preview}")

        unobserved_plan = [
            f"{row.get('页面/入口', '')}/{row.get('元素名称/文案', '')}/{row.get('元素类型', '')}"
            for row in real_plan_rows
            if not element_is_planned(row, interactive_rows)
        ]
        if unobserved_plan:
            raise ValueError(
                "element-case-plan.csv contains elements with no exact page-discovery.csv fact; execute and "
                f"record them before planning cases: {unobserved_plan[:10]}"
            )

        computed_min_total = 0
        declared_total = 0
        planned_id_owners: dict[str, str] = {}
        for index, row in enumerate(real_plan_rows, start=2):
            declared = parse_positive_int(row.get("应生成用例数", ""), f"element-case-plan.csv row {index} 应生成用例数")
            minimum = minimum_cases_for_plan_row(row)
            if declared < minimum:
                raise ValueError(
                    f"element-case-plan.csv row {index} declares {declared} case(s), "
                    f"but element type + DFX minimum requires at least {minimum}: "
                    f"{row.get('元素名称/文案', '')} / {row.get('测试设计方向', '')}"
                )
            row_planned_ids = split_plan_values(row.get("计划用例ID", ""))
            if len(row_planned_ids) != len(set(row_planned_ids)):
                raise ValueError(f"element-case-plan.csv row {index} contains duplicate 计划用例ID values")
            if len(row_planned_ids) < declared:
                raise ValueError(f"element-case-plan.csv row {index} must provide at least {declared} planned case ID(s)")
            owner = f"row {index} ({row.get('页面/入口', '')}/{row.get('功能点', '')})"
            for case_id in row_planned_ids:
                previous_owner = planned_id_owners.get(case_id)
                if previous_owner is not None:
                    raise ValueError(
                        f"element-case-plan.csv case ID {case_id} is owned by both {previous_owner} and {owner}"
                    )
                planned_id_owners[case_id] = owner
            computed_min_total += minimum
            declared_total += declared

        validate_selection_plan_links(real_selection_option_rows, real_plan_rows, split_plan_values)
        if real_interaction_branch_rows:
            validate_branch_plan_links(real_interaction_branch_rows, real_plan_rows, split_plan_values)

        if declared_total < len(interactive_rows):
            raise ValueError(
                f"element-case-plan.csv declares only {declared_total} cases for {len(interactive_rows)} interactive elements; "
                "DFX扩展后用例数不得低于可交互元素数"
            )

        has_crud_or_config = validate_operation_plan_rows(real_plan_rows)
        validate_mutation_discovery_evidence(
            real_plan_rows,
            interactive_rows,
            lambda value: evidence_path_exists(run_dir, value),
        )
        real_lifecycle_rows = [
            row for row in lifecycle_rows
            if not is_template_or_empty_row(row, ["测试数据ID/名称", "创建结果", "查看结果", "编辑结果", "删除确认结果", "清理状态"])
        ]
        validate_lifecycle_rows(real_lifecycle_rows, has_crud_or_config, contains_any, real_plan_rows)

    if phase in {"risk", "cases"}:
        confirmed_risk_rows, risk_case_ids = validate_risk_confirmation(
            risk_confirmation_rows,
            split_plan_values,
            interactive_rows,
            lambda value: evidence_path_exists(run_dir, value),
        )

    artifacts_dir = run_dir / "artifacts"
    scripts_dir = artifacts_dir / "scripts"
    data_dir = artifacts_dir / "data"
    screenshots_dir = artifacts_dir / "screenshots"
    for required_dir in [artifacts_dir, scripts_dir, data_dir, screenshots_dir]:
        if phase in {"cases"} and not required_dir.exists():
            raise ValueError(f"Required batch artifact directory is missing: {required_dir}")

    misplaced_parts = sorted(artifacts_dir.glob("function_cases_part_*.json")) if artifacts_dir.exists() else []
    if misplaced_parts:
        raise ValueError("function_cases_part_*.json must be written under artifacts/data, not artifacts root")

    if phase == "cases":
        session = generation_session_data(run_dir)
        if not session:
            raise ValueError("generation-session.json is missing or invalid; run prepare-function-case-generation")
        current_source_fingerprint = generation_source_fingerprint(run_dir)
        if session.get("source_fingerprint") != current_source_fingerprint:
            raise ValueError("generation session source fingerprint is stale; rerun prepare-function-case-generation")
        current_catalog_fingerprint = generation_catalog_fingerprint(run_dir)
        if (
            session.get("catalog_source_fingerprint") != current_catalog_fingerprint
            and not delivery_receipt_matches_generation_session(run_dir, session)
        ):
            raise ValueError("generation session catalog source fingerprint is stale; rerun prepare-function-case-generation")
        parts = manifest_parts(data_dir)
        planned_ids = planned_case_ids(plan_rows)
        part_counts = [validate_function_case_part(path, planned_ids) for path in parts]
        case_count = sum(part_counts)
        actual_case_ids: set[str] = set()
        duplicate_case_ids: set[str] = set()
        actual_case_rows: list[dict[str, object]] = []
        shard_case_rows: list[list[dict[str, object]]] = []
        for path in parts:
            with path.open("r", encoding="utf-8-sig") as fp:
                payload = json.load(fp)
            case_rows = payload.get("cases") if isinstance(payload, dict) else payload
            current_shard_rows: list[dict[str, object]] = []
            for item in case_rows:
                if not isinstance(item, dict):
                    continue
                case_id = str(item.get("用例 ID", "")).strip()
                if case_id in actual_case_ids:
                    duplicate_case_ids.add(case_id)
                actual_case_ids.add(case_id)
                actual_case_rows.append(item)
                current_shard_rows.append(item)
            shard_case_rows.append(current_shard_rows)
        if duplicate_case_ids:
            raise ValueError(f"function case IDs must be unique across all manifest shards: {sorted(duplicate_case_ids)[:10]}")
        validate_case_collection(actual_case_rows, label="function case manifest")
        validate_contiguous_function_point_groups(actual_case_rows, label="function case manifest")
        validate_function_point_aware_shards(
            shard_case_rows,
            label="function case manifest",
            max_per_shard=MAX_FUNCTION_CASES_PER_PART,
        )
        missing_planned_case_ids = sorted(planned_case_ids(plan_rows) - actual_case_ids)
        if missing_planned_case_ids:
            raise ValueError(f"planned case IDs are missing from current function shards: {missing_planned_case_ids[:10]}")
        for index, row in enumerate(real_plan_rows, start=2):
            planned_for_row = split_plan_values(row.get("计划用例ID", ""))
            actual_for_row = split_plan_values(row.get("实际用例ID", ""))
            if actual_for_row != planned_for_row:
                raise ValueError(
                    f"element-case-plan.csv row {index} 实际用例ID must exactly match generated 计划用例ID; "
                    f"planned={planned_for_row}, actual={actual_for_row}"
                )
        validate_plan_function_point_alignment(
            real_plan_rows,
            actual_case_rows,
            split_ids=split_plan_values,
        )
        validate_plan_case_order_alignment(
            real_plan_rows,
            actual_case_rows,
            split_ids=split_plan_values,
        )
        validate_discovery_plan_case_alignment(
            interactive_rows,
            real_plan_rows,
            actual_case_rows,
            split_ids=split_plan_values,
        )
        validate_selection_case_grounding(real_selection_option_rows, actual_case_rows, split_plan_values)
        if real_interaction_branch_rows:
            validate_branch_case_grounding(real_interaction_branch_rows, actual_case_rows, split_plan_values)
        unknown_risk_case_ids = sorted(risk_case_ids - actual_case_ids)
        if unknown_risk_case_ids:
            raise ValueError(
                f"risk-confirmation.csv references case IDs not present in current function shards: {unknown_risk_case_ids[:10]}"
            )
        known_risk_ids = {
            row.get("风险ID", "").strip()
            for row in confirmed_risk_rows
            if row.get("风险ID", "").strip() and row.get("风险ID", "").strip() != "RISK-NONE"
        }
        case_risk_ids = {
            risk_id
            for case in actual_case_rows
            for risk_id in split_plan_values(str(case.get("关联风险", "")))
            if risk_id and risk_id != "RISK-NONE"
        }
        unknown_case_risks = sorted(case_risk_ids - known_risk_ids)
        if unknown_case_risks:
            raise ValueError(f"function cases reference risks missing from risk-confirmation.csv: {unknown_case_risks[:10]}")
        for index, row in enumerate(confirmed_risk_rows, start=2):
            if row.get("风险ID", "").strip() == "RISK-NONE":
                continue
            linked = split_plan_values(row.get("关联用例ID", ""))
            strategy = row.get("处置策略", "")
            non_case_landing = contains_any(strategy, ["仅记录风险", "不生成用例", "性能测试设计", "自动化建议"])
            if not linked and not non_case_landing:
                raise ValueError(
                    f"risk-confirmation.csv row {index} must link generated case IDs or explicitly land in "
                    "性能测试设计/自动化建议/仅记录风险"
                )
            linked_set = set(linked)
            case_side = {
                str(case.get("用例 ID", "")).strip()
                for case in actual_case_rows
                if row.get("风险ID", "").strip() in split_plan_values(str(case.get("关联风险", "")))
            }
            if linked_set != case_side:
                raise ValueError(
                    f"risk-confirmation.csv row {index} 关联用例ID must exactly match cases declaring "
                    f"关联风险={row.get('风险ID', '')}; ledger={sorted(linked_set)}, cases={sorted(case_side)}"
                )
        with (data_dir / FUNCTION_CASE_MANIFEST).open("r", encoding="utf-8-sig") as fp:
            manifest_data = json.load(fp)
        if isinstance(manifest_data, dict):
            if manifest_data.get("generation_session_id") != session.get("generation_session_id"):
                raise ValueError("function_cases_manifest.json generation_session_id must match generation-session.json")
            if manifest_data.get("source_fingerprint") != session.get("source_fingerprint"):
                raise ValueError("function_cases_manifest.json source_fingerprint must match current generation session")
            declared_part_size = manifest_data.get("part_size")
            declared_total = manifest_data.get("total_cases")
            if declared_part_size not in {None, MAX_FUNCTION_CASES_PER_PART}:
                raise ValueError(
                    f"{FUNCTION_CASE_MANIFEST} part_size must be {MAX_FUNCTION_CASES_PER_PART}, got {declared_part_size}"
                )
            if declared_total is not None and declared_total != case_count:
                raise ValueError(
                    f"{FUNCTION_CASE_MANIFEST} total_cases={declared_total} does not match actual shard total {case_count}"
                )
        missing_sheet_files = [name for name in SHEET_DATA_FILES if not (data_dir / name).exists()]
        if missing_sheet_files:
            raise ValueError(f"artifacts/data is missing sheet-split files: {missing_sheet_files}")
        for name in SHEET_DATA_FILES:
            validate_sheet_data_file(data_dir / name)
        if plan_rows:
            declared_total = sum(
                int(row.get("应生成用例数", "0"))
                for row in plan_rows
                if row.get("应生成用例数", "").isdigit()
            )
            if declared_total and case_count < declared_total:
                raise ValueError(f"function case shards contain {case_count} cases, fewer than element-case-plan declared {declared_total}")

    if batch_rows:
        for index, status_row in enumerate(batch_rows, start=2):
            if status_row.get("状态") == "待开始" or any(
                (status_row.get(field, "") or "0") == "0" for field in ["页面数", "元素总数"]
            ):
                raise ValueError(
                    f"batch-status.csv row {index} is still in the initial state; update page and element counts before continuing"
                )
            batch_id = status_row.get("批次ID", "").strip()
            scoped = [row for row in interactive_rows if not batch_id or row.get("批次ID", "").strip() == batch_id]
            expected_pages = len({row.get("页面/入口", "").strip() for row in scoped if row.get("页面/入口", "").strip()})
            expected_elements = len(scoped)
            if int(status_row.get("页面数", "0")) != expected_pages or int(status_row.get("元素总数", "0")) != expected_elements:
                raise ValueError(
                    f"batch-status.csv row {index} counts must match discovery facts: "
                    f"页面数={expected_pages}, 元素总数={expected_elements}"
                )
            expected_covered = sum(row.get("覆盖状态", "").strip() == "已覆盖" for row in scoped)
            expected_pending = expected_elements - expected_covered
            if int(status_row.get("已覆盖元素数", "0")) != expected_covered:
                raise ValueError(
                    f"batch-status.csv row {index} 已覆盖元素数 must be derived from page-discovery.csv: "
                    f"{expected_covered}"
                )
            if int(status_row.get("待确认元素数", "0")) != expected_pending:
                raise ValueError(
                    f"batch-status.csv row {index} 待确认元素数 must be derived from page-discovery.csv: "
                    f"{expected_pending}"
                )
            if phase == "cases" and expected_covered != expected_elements:
                raise ValueError("cases cannot be generated while any interactive discovery element is not actually covered")
            if phase == "cases" and int(status_row.get("功能用例数", "0")) != case_count:
                raise ValueError(f"batch-status.csv row {index} 功能用例数 must match manifest cases: {case_count}")
            if phase == "cases":
                derived_counts = derived_case_quality_counts(actual_case_rows)
                mismatches = {
                    field: (int(status_row.get(field, "0") or "0"), expected)
                    for field, expected in derived_counts.items()
                    if int(status_row.get(field, "0") or "0") != expected
                }
                if mismatches:
                    raise ValueError(
                        f"batch-status.csv row {index} quality-direction counts must be derived from manifest cases: "
                        f"{mismatches}"
                    )

    if use_cache:
        record_success(run_dir, phase, current_fingerprint, input_paths)
    print(f"OK: batch artifacts passed {phase} gate: {run_dir}")


def init_batch_run(
    project_root: Path,
    run_id: str,
    module_path: str,
    batch_id: str,
    product_name: str | None = None,
    resume: bool = False,
    force_reinitialize: bool = False,
) -> Path:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run-id must be a single safe directory name without path separators")
    batch_runs_dir = (project_root / "docs" / "test-assets" / "batch-runs").resolve()
    run_dir = batch_runs_dir / run_id
    templates_dir = batch_runs_dir / "templates"
    required_templates = {
        "batch-plan.md": templates_dir / "batch-plan-template.md",
        "batch-status.csv": templates_dir / "batch-status-template.csv",
        "batch-review.md": templates_dir / "batch-review-template.md",
        "page-element-inventory.csv": templates_dir / "page-element-inventory-template.csv",
        "page-discovery.csv": templates_dir / "page-discovery-template.csv",
        "selection-option-observations.csv": templates_dir / "selection-option-observations-template.csv",
        "interaction-branch-observations.csv": templates_dir / "interaction-branch-observations-template.csv",
        "element-case-plan.csv": templates_dir / "element-case-plan-template.csv",
        "test-data-lifecycle.csv": templates_dir / "test-data-lifecycle-template.csv",
        "risk-confirmation.csv": templates_dir / "risk-confirmation-template.csv",
    }
    missing = [str(path) for path in required_templates.values() if not path.exists()]
    if missing:
        raise ValueError(f"Batch template files are missing: {missing}")

    if run_dir.exists():
        if resume:
            missing_run_files = [name for name in required_templates if not (run_dir / name).exists()]
            _, resume_modules = split_module_parts(module_path, product_name)
            resume_leaf_path = ">".join(resume_modules) or module_path
            scope = batch_scope_data(run_dir)
            if scope is None:
                scope_path = run_dir / BATCH_SCOPE
                if scope_path.exists():
                    raise ValueError(
                        f"Existing {BATCH_SCOPE} is invalid and cannot be overwritten during --resume; "
                        "repair it from known batch scope facts or force-reinitialize the run"
                    )
                scope = write_batch_scope(
                    run_dir,
                    run_id=run_id,
                    batch_id=batch_id,
                    module_path=module_path,
                    product_name=product_name,
                )
                print(f"Added missing {BATCH_SCOPE} to legacy batch run: {run_dir}")
            requested_product, _ = split_module_parts(module_path, product_name)
            if str(scope.get("batch_id", "")).strip() != batch_id:
                raise ValueError(f"Existing {BATCH_SCOPE} batch_id does not match --batch-id")
            if str(scope.get("module_path", "")).strip() != resume_leaf_path:
                raise ValueError(f"Existing {BATCH_SCOPE} module_path does not match --module-path")
            if product_name and str(scope.get("product_name", "")).strip() != requested_product:
                raise ValueError(f"Existing {BATCH_SCOPE} product_name does not match --product-name")
            if "selection-option-observations.csv" in missing_run_files:
                copy_template_if_missing(
                    required_templates["selection-option-observations.csv"],
                    run_dir / "selection-option-observations.csv",
                )
                write_single_csv_row(
                    run_dir / "selection-option-observations.csv",
                    {
                        "批次ID": batch_id,
                        "最小标题路径": resume_leaf_path,
                        "备注": "由 --resume 补齐；存在选择控件时必须逐项补录实际选择与页面变化证据",
                    },
                )
                missing_run_files.remove("selection-option-observations.csv")
                print(f"Added missing selection-option-observations.csv to legacy batch run: {run_dir}")
            if "interaction-branch-observations.csv" in missing_run_files:
                copy_template_if_missing(
                    required_templates["interaction-branch-observations.csv"],
                    run_dir / "interaction-branch-observations.csv",
                )
                write_single_csv_row(
                    run_dir / "interaction-branch-observations.csv",
                    {
                        "批次ID": batch_id,
                        "最小标题路径": resume_leaf_path,
                        "备注": "由 --resume 补齐；必须按 discovery-next 返回的义务逐分支重新实探",
                    },
                )
                missing_run_files.remove("interaction-branch-observations.csv")
                print(f"Added missing interaction-branch-observations.csv to legacy batch run: {run_dir}")
            if "page-element-inventory.csv" in missing_run_files:
                copy_template_if_missing(
                    required_templates["page-element-inventory.csv"],
                    run_dir / "page-element-inventory.csv",
                )
                write_single_csv_row(
                    run_dir / "page-element-inventory.csv",
                    {
                        "批次ID": batch_id,
                        "最小标题路径": resume_leaf_path,
                        "备注": "由 --resume 补齐；必须从 DOM/可访问性树/trace/控件树重新采集，不伪造历史页面元素",
                    },
                )
                missing_run_files.remove("page-element-inventory.csv")
                print(f"Added missing page-element-inventory.csv to legacy batch run: {run_dir}")
            if "risk-confirmation.csv" in missing_run_files:
                copy_template_if_missing(required_templates["risk-confirmation.csv"], run_dir / "risk-confirmation.csv")
                write_single_csv_row(
                    run_dir / "risk-confirmation.csv",
                    {
                        "批次ID": batch_id,
                        "风险ID": "RISK-PENDING",
                        "模型不理解内容/待确认问题": "旧批次升级后需补录模型不理解项的用户确认结论",
                        "已完成深探依据": "先完成默认全量深探，再记录仍无法判定的业务语义",
                        "页面可验证性": "待复核",
                        "页面验证动作": "待完成页面验证性复核",
                        "页面验证结果": "待补充",
                        "不可验证/外部依赖原因": "待补充",
                        "用户确认结论": "待用户确认",
                        "处置策略": "待确认",
                        "是否阻塞用例设计": "是",
                        "确认状态": "待确认",
                        "备注": "由 --resume 自动补齐的新版本风险确认账本",
                    },
                )
                missing_run_files.remove("risk-confirmation.csv")
                print(f"Added missing risk-confirmation.csv to legacy batch run: {run_dir}")
            if missing_run_files:
                raise ValueError(f"Existing batch run is incomplete and cannot be resumed: {missing_run_files}")
            risk_path = run_dir / "risk-confirmation.csv"
            expected_risk_headers = template_headers(templates_dir, "risk-confirmation-template.csv")
            with risk_path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                current_headers = reader.fieldnames or []
                current_rows = list(reader)
            legacy_risk_headers = [
                "批次ID", "风险ID", "风险/待确认问题", "用户确认结论", "处置策略", "是否需要补充深探", "补充深探目标",
                "关联页面/入口", "关联元素名称/文案", "补充证据路径", "补充深探状态", "关联用例ID", "备注",
            ]
            pre_page_verification_headers = [
                "批次ID", "风险ID", "模型不理解内容/待确认问题", "已完成深探依据", "用户确认结论", "处置策略",
                "是否阻塞用例设计", "关联页面/入口", "关联元素名称/文案", "证据路径", "确认状态", "关联用例ID", "备注",
            ]
            if current_headers not in [expected_risk_headers, legacy_risk_headers, pre_page_verification_headers]:
                raise ValueError(f"Cannot automatically migrate unsupported risk-confirmation.csv header: {current_headers}")
            migrate_structured_batch_ledgers(run_dir, templates_dir)
            initialize_discovery_control(run_dir)
            if current_headers != expected_risk_headers:
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
                backup_marker = "pre-default-deep-dive" if current_headers == legacy_risk_headers else "pre-page-verification"
                backup_path = risk_path.with_name(f"risk-confirmation.{backup_marker}-{timestamp}.csv")
                shutil.copy2(risk_path, backup_path)
                migrated_rows: list[dict[str, str]] = []
                for row in current_rows:
                    migrated = {header: "" for header in expected_risk_headers}
                    is_legacy = current_headers == legacy_risk_headers
                    risk_id = row.get("风险ID", "RISK-PENDING")
                    migrated.update(
                        {
                            "批次ID": row.get("批次ID", batch_id),
                            "风险ID": risk_id,
                            "模型不理解内容/待确认问题": (
                                row.get("风险/待确认问题", "") if is_legacy else row.get("模型不理解内容/待确认问题", "")
                            ),
                            "已完成深探依据": (
                                row.get("补充深探目标", "") if is_legacy else row.get("已完成深探依据", "")
                            ) or "旧账本迁移：需核对是否已完成默认全量深探",
                            "用户确认结论": row.get("用户确认结论", "待用户确认"),
                            "处置策略": row.get("处置策略", "待确认"),
                            "是否阻塞用例设计": "否" if risk_id == "RISK-NONE" else "是",
                            "关联页面/入口": row.get("关联页面/入口", ""),
                            "关联元素名称/文案": row.get("关联元素名称/文案", ""),
                            "证据路径": row.get("补充证据路径", "") if is_legacy else row.get("证据路径", ""),
                            "确认状态": "无需用户确认" if risk_id == "RISK-NONE" else "待确认",
                            "关联用例ID": row.get("关联用例ID", ""),
                            "备注": "由 --resume 迁移；非 RISK-NONE 项必须复核页面可验证性和逐项实探证据",
                        }
                    )
                    if risk_id == "RISK-NONE":
                        migrated.update(
                            {
                                "页面可验证性": "不适用",
                                "页面验证动作": "不适用：不存在需要用户确认的问题",
                                "页面验证结果": "不适用：页面可验证项均已由模型完成验证",
                                "不可验证/外部依赖原因": "不适用：未遗留页面不可观察项",
                                "用户确认结论": "无需用户确认",
                            }
                        )
                    else:
                        migrated.update(
                            {
                                "页面可验证性": "待复核",
                                "页面验证动作": migrated["已完成深探依据"],
                                "页面验证结果": "待补充页面验证结果",
                                "不可验证/外部依赖原因": "待补充不可页面验证或外部阻塞原因",
                            }
                        )
                    migrated_rows.append(migrated)
                temporary = temporary_sibling(risk_path)
                try:
                    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                        writer = csv.DictWriter(stream, fieldnames=expected_risk_headers)
                        writer.writeheader()
                        writer.writerows(migrated_rows)
                    os.replace(temporary, risk_path)
                finally:
                    temporary.unlink(missing_ok=True)
                print(f"Migrated risk-confirmation.csv page-verification schema and preserved backup: {backup_path}")
            print(f"Resumed existing batch run; preserved facts and applied compatible schema additions: {run_dir}")
            return run_dir
        if not force_reinitialize:
            raise ValueError(
                f"Batch run already exists: {run_dir}. Use --resume to keep existing data or "
                "--force-reinitialize to back it up and create a clean run."
            )
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_dir = batch_runs_dir / f"{run_id}_backup_{timestamp}"
        shutil.copytree(run_dir, backup_dir)
        shutil.rmtree(run_dir)
        print(f"Backed up existing batch run before reinitializing: {backup_dir}")

    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = run_dir / "artifacts"
    scripts_dir = artifacts_dir / "scripts"
    data_dir = artifacts_dir / "data"
    screenshots_dir = artifacts_dir / "screenshots"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    for target_name, template_path in required_templates.items():
        copy_template_if_missing(template_path, run_dir / target_name)

    product, modules = split_module_parts(module_path, product_name)
    level1 = modules[0] if len(modules) > 0 else ""
    level2 = modules[1] if len(modules) > 1 else ""
    level3 = modules[2] if len(modules) > 2 else ""
    leaf_path = ">".join(modules) or module_path
    write_batch_scope(
        run_dir,
        run_id=run_id,
        batch_id=batch_id,
        module_path=module_path,
        product_name=product_name,
    )

    write_single_csv_row(
        run_dir / "batch-status.csv",
        {
            "批次ID": batch_id,
            "一级模块": level1,
            "二级菜单": level2,
            "三级菜单/页面域": level3,
            "批次范围": leaf_path,
            "状态": "待开始",
            "页面数": "0",
            "元素总数": "0",
            "已覆盖元素数": "0",
            "待确认元素数": "0",
            "功能用例数": "0",
            "性能场景数": "0",
            "异常用例数": "0",
            "边界用例数": "0",
            "权限/状态用例数": "0",
            "数据一致性用例数": "0",
            "页面遍历完成": "否",
            "功能用例完成": "否",
            "性能设计完成": "否",
            "异常边界权限覆盖完成": "否",
            "页面元素覆盖完成": "否",
            "产品版图已更新": "否",
            "覆盖质量自检": "未通过",
            "导入文件已生成": "否",
            "最小标题路径": leaf_path,
            "下一步动作": "先独立采集 page-element-inventory.csv，再按交互实例ID执行页面实探并补充 page-discovery.csv",
        },
    )
    write_single_csv_row(
        run_dir / "page-element-inventory.csv",
        {
            "批次ID": batch_id,
            "最小标题路径": leaf_path,
            "备注": "先独立采集 DOM/可访问性树/浏览器 trace/桌面控件树，再与 page-discovery.csv 双向对账",
        },
    )
    write_single_csv_row(
        run_dir / "page-discovery.csv",
        {
            "批次ID": batch_id,
            "一级模块": level1,
            "二级菜单": level2,
            "三级菜单/页面域": level3,
            "最小标题路径": leaf_path,
            "菜单路径/URL": leaf_path,
            "发现方式": "浏览器实探/页面资料",
            "是否已生成用例": "否",
            "覆盖状态": "待确认",
            "备注": "按当前批次页面实探结果补充页面、元素、取值、联动和关联用例",
        },
    )
    write_single_csv_row(
        run_dir / "selection-option-observations.csv",
        {
            "批次ID": batch_id,
            "最小标题路径": leaf_path,
            "备注": "选择控件按选项逐行记录；有限集合必须全量选择，动态集合必须写明搜索/滚动/边界/清空覆盖策略",
        },
    )
    write_single_csv_row(
        run_dir / "interaction-branch-observations.csv",
        {
            "批次ID": batch_id,
            "最小标题路径": leaf_path,
            "备注": "输入、动态选择、分页和弹窗按 discovery-next 返回的义务逐项执行并绑定真实页面工具记录",
        },
    )
    write_single_csv_row(
        run_dir / "element-case-plan.csv",
        {
            "批次ID": batch_id,
            "最小标题路径": leaf_path,
            "操作类别": "其他",
            "验证要求": "结果分支",
            "数据策略": "无数据变更",
            "执行状态": "不适用",
            "是否必须真实执行": "是",
            "是否涉及配置生效": "否",
            "是否涉及CRUD闭环": "否",
            "备注": "按当前独立叶子批次补充页面元素、结构化操作和计划用例ID",
        },
    )
    write_single_csv_row(
        run_dir / "test-data-lifecycle.csv",
        {
            "批次ID": batch_id,
            "最小标题路径": leaf_path,
            "备注": "仅记录当前独立叶子批次中本次创建或用户提供测试数据的逐修改项生命周期",
        },
    )
    write_single_csv_row(
        run_dir / "risk-confirmation.csv",
        {
            "批次ID": batch_id,
            "风险ID": "RISK-PENDING",
            "模型不理解内容/待确认问题": "默认全量深探后，列出模型仍无法理解的内容并提交用户确认",
            "已完成深探依据": "填写已执行的页面操作、观察结果和仍无法判定的原因",
            "页面可验证性": "待复核",
            "页面验证动作": "先逐项执行所有可由页面验证的操作",
            "页面验证结果": "待补充",
            "不可验证/外部依赖原因": "待补充",
            "用户确认结论": "待用户确认",
            "处置策略": "待确认",
            "是否阻塞用例设计": "是",
            "确认状态": "待确认",
            "备注": "plan 通过后由模型归纳；没有不理解项时运行 record-risk-none，不伪造用户确认",
        },
    )

    init_note = (
        "\n\n## 批次初始化\n"
        f"- 产品/系统：{product}\n"
        f"- 模块路径：{leaf_path}\n"
        f"- 批次ID：{batch_id}\n"
        "- 执行要求：先独立采集 page-element-inventory.csv，再按交互实例ID补全 page-discovery.csv，之后生成测试设计、导入文件和 batch-status.csv 覆盖数据。\n"
    )
    for markdown_name in ["batch-plan.md", "batch-review.md"]:
        markdown_path = run_dir / markdown_name
        text = markdown_path.read_text(encoding="utf-8-sig")
        if "## 批次初始化" not in text:
            atomic_write_text(markdown_path, text.rstrip() + init_note, encoding="utf-8")

    initialize_discovery_control(run_dir)

    print(f"Initialized batch run: {run_dir}")
    return run_dir
