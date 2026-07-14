# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from test_design.io_utils import (
    DurableFileTransaction,
    atomic_copy,
    atomic_save_workbook,
    atomic_write_text,
    exclusive_process_lock,
    rollback_files_on_error,
    temporary_sibling,
)

try:
    from openpyxl import load_workbook
except ImportError as exc:  # pragma: no cover - depends on local runtime packaging.
    raise SystemExit(
        "ERROR: openpyxl is required. Run this script in the CodeBuddy/Codex spreadsheet runtime "
        "or install openpyxl in the active Python environment."
    ) from exc

from test_design.fact_store import (
    ensure_catalog,
    project_catalog_to_workbook,
    rebuild_index,
    validate_catalog,
)
from test_design.excel_utils import (
    FORMAL_FUNCTION_SHEET,
    FORMAL_MULTILINE_FIELDS,
    IMPORT_MULTILINE_FIELDS,
    apply_template_workbook_format,
    clear_data_rows,
    copy_row_style,
    header_map,
    non_empty_rows,
    remove_workbook_tables_and_refresh_filters,
    row_dict,
    set_wrap,
    write_mapped_row,
)
from test_design.paths import (
    canonical_module_parts,
    deliverable_names,
    module_names,
    relative_project_path,
)
from test_design.formal_assembler import assemble_formal_workbook


IMPORT_AUTO_FIELDS = {
    "一级模块系统编号", "二级模块系统编号", "三级模块系统编号", "四级模块系统编号", "五级模块系统编号",
    "其他模块系统编号", "其他模块名称", "测试用例系统编号", "维护人", "作者",
}
IMPORT_ALLOWED_VALUES = {
    "测试类型": {"功能测试", "性能规格测试", "可靠性测试", "兼容性测试", "可维护性测试", "安全性测试", "易用性测试"},
    "测试用例级别": {"L1", "L2", "L3", "L4"},
    "执行方式": {"自动化", "手动"},
}
IMPORT_EXCLUDED_TEST_TYPES = {"性能规格测试"}
IMPORT_EXCLUDED_DFX_DIMENSIONS = {"DFP性能"}
IMPORT_EXCLUDED_DFX_EXTREME_SCENARIOS = {"压力极限", "资源耗尽", "并发极限"}
PROTECTED_PUBLICATION_DIRS = (
    Path("docs/test-design/current"),
    Path("docs/test-design/deliverables"),
    Path("docs/test-assets/modules"),
    Path("docs/test-assets/imports"),
    Path("docs/test-assets/catalog"),
)


def _containing_project_root(path: Path) -> Path | None:
    resolved = path.resolve()
    for candidate in [resolved, *resolved.parents, Path.cwd().resolve(), *Path.cwd().resolve().parents]:
        if (candidate / "docs" / "test-design").is_dir() and (candidate / "docs" / "test-assets").is_dir():
            return candidate
    return None


def reject_direct_protected_output(path: Path, command: str) -> None:
    root = _containing_project_root(path)
    if root is None:
        return
    resolved = path.resolve()
    protected_dirs = [(root / relative).resolve() for relative in PROTECTED_PUBLICATION_DIRS]
    protected_file = (root / "docs/test-assets/product-map.xlsx").resolve()
    for target in protected_dirs:
        if resolved == target or target in resolved.parents:
            raise ValueError(
                f"{command} cannot write a protected publication path directly: {resolved}. "
                "Use orchestrated complete-deliverables after the Review Gate."
            )
    if resolved == protected_file:
        raise ValueError(
            f"{command} cannot write a protected publication path directly: {resolved}. "
            "Use orchestrated complete-deliverables after the Review Gate."
        )


def validate_assembly_preview_output(run_dir: Path, output: Path) -> None:
    preview_root = (run_dir.resolve() / "artifacts" / "previews").resolve()
    resolved = output.resolve()
    if resolved == preview_root or preview_root not in resolved.parents:
        raise ValueError(
            "assemble-formal-workbook is preview-only and must write under "
            f"{preview_root}; formal publication is owned by complete-deliverables"
        )


def normalize_case_level(priority: str) -> str:
    value = (priority or "").upper()
    if value in {"L1", "L2", "L3", "L4"}:
        return value
    if value in {"P0", "P1", "高", "高优先级"}:
        return "L1"
    if value in {"P2", "中", "中优先级"}:
        return "L2"
    if value in {"P3", "低", "低优先级"}:
        return "L3"
    return "L2"


def normalize_test_type(value: str) -> str:
    if value in IMPORT_ALLOWED_VALUES["测试类型"]:
        return value
    if "性能" in value:
        return "性能规格测试"
    if "兼容" in value:
        return "兼容性测试"
    if "安全" in value or "权限" in value:
        return "安全性测试"
    if "可靠" in value or "稳定" in value:
        return "可靠性测试"
    if "易用" in value:
        return "易用性测试"
    if "维护" in value:
        return "可维护性测试"
    return "功能测试"


def split_dfx_values(text: str) -> set[str]:
    normalized = (text or "").replace("，", ",").replace("；", ",").replace("、", ",").replace("/", ",")
    return {item.strip() for item in normalized.split(",") if item.strip()}


def is_importable_function_case(case: dict[str, str]) -> bool:
    test_type = normalize_test_type(case.get("测试类型", ""))
    dfx_dimensions = split_dfx_values(case.get("DFX维度", ""))
    dfx_scenarios = split_dfx_values(case.get("DFX场景", ""))
    if test_type in IMPORT_EXCLUDED_TEST_TYPES:
        return False
    if dfx_dimensions & IMPORT_EXCLUDED_DFX_DIMENSIONS:
        return False
    if "DFX极端" in dfx_dimensions and dfx_scenarios & IMPORT_EXCLUDED_DFX_EXTREME_SCENARIOS:
        return False
    return True


def execution_mode(row: dict[str, str]) -> str:
    note = "".join([row.get("备注", ""), row.get("是否适合自动化", "")])
    automation_markers = ["自动化资产", "脚本", "流水线", "API自动化", "UI自动化", "已实现"]
    if "自动化" in note and any(marker in note for marker in automation_markers):
        return "自动化"
    return "手动"


def copy_workbook(source: Path, target: Path) -> None:
    atomic_copy(source, target)


def update_batch_status_paths(
    batch_status: Path,
    batch_id: str | None,
    archive_rel: str,
    import_rel: str,
    product_map_updated: bool = False,
) -> list[dict[str, str]]:
    if not batch_status:
        return []
    with batch_status.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        headers = reader.fieldnames or []
        rows = list(reader)
    if not headers:
        raise ValueError(f"batch-status.csv has no header row: {batch_status}")
    required = {"批次ID", "归档路径", "导入文件路径", "导入文件已生成"}
    missing = sorted(required - set(headers))
    if missing:
        raise ValueError(f"batch-status.csv is missing required finalize columns: {missing}")
    target_rows = [row for row in rows if not batch_id or row.get("批次ID") == batch_id]
    if not target_rows:
        raise ValueError(f"No matching batch row found for batch_id={batch_id!r}")
    changes: list[dict[str, str]] = []
    for row in target_rows:
        changes.append(
            {
                "批次ID": row.get("批次ID", ""),
                "旧归档路径": row.get("归档路径", ""),
                "旧导入文件路径": row.get("导入文件路径", ""),
                "归档路径": archive_rel,
                "导入文件路径": import_rel,
            }
        )
        row["归档路径"] = archive_rel
        row["导入文件路径"] = import_rel
        row["导入文件已生成"] = "是"
        terminal_values = {
            "状态": "已完成",
            "页面遍历完成": "是",
            "功能用例完成": "是",
            "性能设计完成": "是",
            "异常边界权限覆盖完成": "是",
            "页面元素覆盖完成": "是",
            "覆盖质量自检": "通过",
            "下一步动作": "批次完成",
        }
        if product_map_updated:
            terminal_values["产品版图已更新"] = "是"
        for field, value in terminal_values.items():
            if field in row:
                row[field] = value
    temporary = temporary_sibling(batch_status)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, batch_status)
    finally:
        temporary.unlink(missing_ok=True)
    return changes


def sync_batch_markdown_paths(batch_status: Path, changes: list[dict[str, str]]) -> None:
    def markdown_cell(value: object) -> str:
        return " ".join(str(value or "").split()).replace("|", "｜")

    with batch_status.open("r", encoding="utf-8-sig", newline="") as stream:
        status_by_id = {
            row.get("批次ID", "").strip(): row
            for row in csv.DictReader(stream)
            if row.get("批次ID", "").strip()
        }
    for markdown_name in ["batch-plan.md", "batch-review.md"]:
        markdown_path = batch_status.resolve().parent / markdown_name
        if not markdown_path.exists():
            continue
        text = markdown_path.read_text(encoding="utf-8-sig")
        for change in changes:
            for old_key, new_key in [("旧归档路径", "归档路径"), ("旧导入文件路径", "导入文件路径")]:
                old_value = change.get(old_key, "")
                new_value = change.get(new_key, "")
                if old_value and new_value:
                    text = text.replace(old_value, new_value)
            if markdown_name == "batch-review.md":
                batch_id = change["批次ID"]
                status = status_by_id.get(batch_id)
                if status:
                    issue = status.get("待确认问题", "").strip() or "无"
                    completion_line = "| " + " | ".join(
                        markdown_cell(value)
                        for value in [
                            batch_id,
                            status.get("状态", ""),
                            status.get("页面数", ""),
                            status.get("元素总数", ""),
                            status.get("已覆盖元素数", ""),
                            status.get("功能用例数", ""),
                            status.get("性能场景数", ""),
                            status.get("归档路径", ""),
                            status.get("导入文件路径", ""),
                            status.get("覆盖质量自检", ""),
                            issue,
                        ]
                    ) + " |"
                    pattern = re.compile(rf"^\|\s*{re.escape(batch_id)}\s*\|.*$", re.MULTILINE)
                    if pattern.search(text):
                        text = pattern.sub(lambda _match: completion_line, text, count=1)
                    else:
                        lines = text.splitlines()
                        section_index = next(
                            (index for index, line in enumerate(lines) if line.strip() == "## 批次完成情况"),
                            -1,
                        )
                        separator_index = next(
                            (
                                index
                                for index in range(section_index + 1, len(lines))
                                if lines[index].lstrip().startswith("| ---")
                            ),
                            -1,
                        )
                        if separator_index < 0:
                            raise ValueError("batch-review.md is missing the standard completion table")
                        lines.insert(separator_index + 1, completion_line)
                        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
            if change["归档路径"] not in text or change["导入文件路径"] not in text:
                text += (
                    "\n\n## 交付收口路径\n"
                    f"- {change['批次ID']} 归档路径：{change['归档路径']}\n"
                    f"- {change['批次ID']} 导入文件路径：{change['导入文件路径']}\n"
                )
        atomic_write_text(markdown_path, text, encoding="utf-8")


def cleanup_batch_artifacts(batch_status: Path | None) -> None:
    if not batch_status:
        return
    pycache = batch_status.resolve().parent / "artifacts" / "scripts" / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)


from test_design.batch import (
    BATCH_SCOPE,
    DELIVERY_RECEIPT,
    batch_scope_data,
    generation_session_data,
    generation_session_core_is_current,
    generation_session_is_current,
    split_module_parts,
    prepare_function_case_generation,
    record_no_model_uncertainty,
    validate_batch_artifacts,
    init_batch_run,
)
from test_design.pipeline import derive_pipeline_status
from test_design.orchestration.engine import (
    advance_orchestration,
    claim_agent_task,
    commit_page_probe_receipt,
    complete_delivery_orchestration,
    orchestration_status,
    orchestration_status_under_lock,
    release_agent_claim,
    resume_external_block,
    submit_agent_result,
    validate_delivery_running_state,
)
from test_design.orchestration.review import validate_review_artifacts


from test_design.product_map_sync import product_map_mutable_paths, sync_product_map


def validate_delivery_scope(
    batch_status: Path,
    batch_id: str | None,
    module_path: str,
    product_name: str | None,
) -> str | None:
    with batch_status.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError("Delivery requires exactly one batch-status.csv row in the independent run directory")
    row = rows[0]
    ledger_batch_id = row.get("批次ID", "").strip()
    if batch_id and batch_id != ledger_batch_id:
        raise ValueError(f"--batch-id {batch_id!r} does not match batch-status.csv {ledger_batch_id!r}")
    scope = batch_scope_data(batch_status.resolve().parent)
    if scope is None:
        raise ValueError(f"Delivery requires a valid {BATCH_SCOPE}; resume the batch with its original --product-name")
    if str(scope.get("batch_id", "")).strip() != ledger_batch_id:
        raise ValueError(f"{BATCH_SCOPE} batch_id does not match batch-status.csv")
    scoped_product = str(scope.get("product_name", "")).strip()
    if not scoped_product:
        raise ValueError(f"{BATCH_SCOPE} must preserve a non-empty product_name")
    if product_name and product_name.strip() != scoped_product:
        raise ValueError(
            f"--product-name {product_name!r} does not match the batch scope product {scoped_product!r}"
        )
    resolved_product = scoped_product or product_name
    _, modules = split_module_parts(module_path, resolved_product)
    requested_leaf = ">".join(modules) or module_path.strip()
    ledger_leaf = row.get("最小标题路径", "").strip()
    scoped_leaf = str(scope.get("module_path", "")).strip()
    if requested_leaf != ledger_leaf or scoped_leaf != ledger_leaf:
        raise ValueError(
            f"--module-path resolves to leaf {requested_leaf!r}, {BATCH_SCOPE} preserves {scoped_leaf!r}, "
            f"but batch-status.csv is scoped to {ledger_leaf!r}; "
            "do not deliver one leaf batch under another module path"
        )
    return resolved_product


def write_delivery_receipt(
    project_root: Path,
    batch_status: Path,
    published: list[Path],
    product_map: Path | None,
    page_discovery: Path | None,
    module_path: str,
    product_name: str | None,
) -> Path:
    run_dir = batch_status.resolve().parent
    if not generation_session_core_is_current(run_dir):
        raise ValueError("Cannot write delivery receipt for a missing or stale generation session")
    tracked = list(published)
    if product_map and page_discovery:
        tracked.extend(path for path in product_map_mutable_paths(product_map, module_path, product_name) if path.is_file())
    receipt = delivery_receipt_value(
        project_root,
        batch_status,
        [(path, path) for path in tracked],
        product_map if product_map and page_discovery else None,
    )
    target = run_dir / "artifacts" / "data" / DELIVERY_RECEIPT
    atomic_write_text(target, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def delivery_receipt_value(
    project_root: Path,
    batch_status: Path,
    tracked_files: list[tuple[Path, Path]],
    product_map: Path | None,
) -> dict[str, object]:
    """Build a receipt from logical publication paths and immutable content sources."""

    run_dir = batch_status.resolve().parent
    if not generation_session_core_is_current(run_dir):
        raise ValueError("Cannot write delivery receipt for a missing or stale generation session")
    session = generation_session_data(run_dir) or {}
    files = []
    seen: set[Path] = set()
    for logical_path, content_source in tracked_files:
        path = logical_path.resolve()
        if path in seen:
            continue
        seen.add(path)
        if content_source.is_symlink():
            raise ValueError(f"Cannot write delivery receipt from symbolic link: {content_source}")
        source = content_source.resolve()
        if not source.is_file() or source.stat().st_size == 0:
            raise ValueError(f"Cannot write delivery receipt for missing or empty file: {source}")
        files.append(
            {
                "path": relative_project_path(project_root, path),
                "size": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        )
    return {
        "version": 1,
        "generation_session_id": session.get("generation_session_id"),
        "source_fingerprint": session.get("source_fingerprint"),
        "catalog_source_fingerprint": session.get("catalog_source_fingerprint"),
        "product_map_path": (
            relative_project_path(project_root, product_map)
            if product_map
            else ""
        ),
        "files": files,
    }


def finalize_deliverables(
    project_root: Path,
    formal_workbook: Path,
    import_workbook: Path,
    module_path: str,
    batch_status: Path | None = None,
    batch_id: str | None = None,
    product_map: Path | None = None,
    page_discovery: Path | None = None,
    product_name: str | None = None,
) -> None:
    project_root = project_root.resolve()
    _, formal_name, import_name = deliverable_names(module_path, product_name)

    module_archive = project_root / "docs" / "test-assets" / "modules" / formal_name
    import_archive = project_root / "docs" / "test-assets" / "imports" / import_name
    current_copy = project_root / "docs" / "test-design" / "current" / formal_name
    deliverable_formal = project_root / "docs" / "test-design" / "deliverables" / formal_name
    deliverable_import = project_root / "docs" / "test-design" / "deliverables" / import_name

    for target in [module_archive, current_copy, deliverable_formal]:
        copy_workbook(formal_workbook, target)
    for target in [import_archive, deliverable_import]:
        copy_workbook(import_workbook, target)

    if product_map and page_discovery:
        sync_product_map(
            product_map,
            module_archive,
            page_discovery,
            module_path,
            relative_project_path(project_root, module_archive),
            product_name,
        )
    if batch_status:
        changes = update_batch_status_paths(
            batch_status,
            batch_id,
            relative_project_path(project_root, module_archive),
            relative_project_path(project_root, import_archive),
            product_map_updated=bool(product_map and page_discovery),
        )
        sync_batch_markdown_paths(batch_status, changes)
        cleanup_batch_artifacts(batch_status)


def run_python_script(script: Path, args: list[str]) -> None:
    completed = subprocess.run([sys.executable, str(script), *args], check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def delivery_publication_paths(
    project_root: Path,
    module_path: str,
    product_name: str | None,
) -> list[Path]:
    _, formal_name, import_name = deliverable_names(module_path, product_name)
    return [
        project_root / "docs" / "test-design" / "current" / formal_name,
        project_root / "docs" / "test-design" / "deliverables" / formal_name,
        project_root / "docs" / "test-design" / "deliverables" / import_name,
        project_root / "docs" / "test-assets" / "modules" / formal_name,
        project_root / "docs" / "test-assets" / "imports" / import_name,
    ]


def _project_relative(project_root: Path, path: Path) -> Path | None:
    try:
        return path.resolve(strict=False).relative_to(project_root.resolve())
    except ValueError:
        return None


def _require_canonical_delivery_source(
    actual: Path | None, expected: Path, label: str
) -> Path:
    """Reject aliases and override files that were not part of the reviewed run."""

    if actual is None:
        raise ValueError(
            f"orchestrated delivery requires canonical {label}: {expected}"
        )
    if actual.is_symlink() or actual.resolve(strict=False) != expected.resolve(strict=False):
        raise ValueError(
            f"orchestrated delivery requires canonical {label}: {expected}; got {actual}"
        )
    return expected


def _seed_staged_file(project_root: Path, staged_project: Path, source: Path) -> Path:
    relative = _project_relative(project_root, source)
    if relative is None:
        raise ValueError(f"Delivery transaction target is outside project root: {source}")
    target = staged_project / relative
    if source.is_symlink():
        raise ValueError(f"Delivery transaction refuses symbolic-link source: {source}")
    if source.exists():
        if not source.is_file():
            raise ValueError(f"Delivery transaction source is not a regular file: {source}")
        atomic_copy(source, target)
    return target


def _delivery_transaction_identity(
    project_root: Path,
    run_dir: Path,
    module_path: str,
    batch_id: str | None,
    product_name: str | None,
) -> dict[str, object]:
    session = generation_session_data(run_dir) or {}
    return {
        "operation": "complete-deliverables",
        "run_dir": relative_project_path(project_root, run_dir),
        "batch_id": batch_id or "",
        "module_path": module_path,
        "product_name": product_name or "",
        "generation_session_id": session.get("generation_session_id"),
        "source_fingerprint": session.get("source_fingerprint"),
        "catalog_source_fingerprint": session.get("catalog_source_fingerprint"),
    }


def _prepare_delivery_transaction(
    transaction: DurableFileTransaction,
    project_root: Path,
    run_dir: Path,
    import_template: Path,
    formal_template: Path,
    module_path: str,
    batch_status: Path,
    batch_id: str | None,
    product_map: Path | None,
    page_discovery: Path | None,
    product_name: str | None,
) -> dict[str, int]:
    """Build every desired delivery file without touching a formal target."""

    build_root = transaction.transaction_dir / "build"
    if build_root.exists():
        shutil.rmtree(build_root)
    staged_project = build_root / "project"
    (staged_project / "docs" / "test-design").mkdir(parents=True)
    (staged_project / "docs" / "test-assets").mkdir(parents=True)
    staged_formal = build_root / "formal.xlsx"
    staged_import = build_root / "import.xlsx"

    assembly_counts = assemble_formal_workbook(run_dir, formal_template, staged_formal)
    apply_formal_workbook_styles(staged_formal)
    generate_import_workbook(
        staged_formal,
        import_template,
        staged_import,
        module_path,
        product_name,
    )
    run_python_script(
        Path(__file__).resolve().parent / "validate-test-design-deliverable.py",
        ["--workbook", str(staged_formal)],
    )

    seed_paths = [
        batch_status,
        batch_status.parent / "batch-plan.md",
        batch_status.parent / "batch-review.md",
    ]
    if product_map:
        seed_paths.extend(product_map_mutable_paths(product_map, module_path, product_name))
    seeded: set[str] = set()
    for seed in seed_paths:
        key = str(seed.absolute()).replace("\\", "/").casefold()
        if key in seeded:
            continue
        seeded.add(key)
        _seed_staged_file(project_root, staged_project, seed)

    staged_batch_status = _seed_staged_file(project_root, staged_project, batch_status)
    staged_product_map = (
        _seed_staged_file(project_root, staged_project, product_map)
        if product_map
        else None
    )
    finalize_deliverables(
        staged_project,
        staged_formal,
        staged_import,
        module_path,
        staged_batch_status,
        batch_id,
        staged_product_map,
        page_discovery,
        product_name,
    )

    desired: dict[Path, Path | None] = {}
    published = delivery_publication_paths(project_root, module_path, product_name)
    for target in published:
        relative = _project_relative(project_root, target)
        assert relative is not None
        desired[target] = staged_project / relative

    run_mutable = [
        batch_status,
        batch_status.parent / "batch-plan.md",
        batch_status.parent / "batch-review.md",
    ]
    for target in run_mutable:
        relative = _project_relative(project_root, target)
        assert relative is not None
        staged = staged_project / relative
        if staged.is_file():
            desired[target] = staged

    catalog_targets: list[Path] = []
    if product_map and page_discovery:
        catalog_targets = product_map_mutable_paths(product_map, module_path, product_name)
        for target in catalog_targets:
            relative = _project_relative(project_root, target)
            if relative is None:
                raise ValueError(f"Product fact target is outside project root: {target}")
            staged = staged_project / relative
            if staged.is_file():
                desired[target] = staged

    receipt_target = run_dir / "artifacts" / "data" / DELIVERY_RECEIPT
    tracked_pairs = [
        (target, desired[target])
        for target in published
    ]
    if product_map and page_discovery:
        tracked_pairs.extend(
            (target, desired[target])
            for target in catalog_targets
            if target in desired
        )
    receipt = delivery_receipt_value(
        project_root,
        batch_status,
        [(logical, source) for logical, source in tracked_pairs if source is not None],
        product_map if product_map and page_discovery else None,
    )
    staged_receipt = build_root / "delivery-receipt.json"
    atomic_write_text(
        staged_receipt,
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    desired[receipt_target] = staged_receipt

    transaction.prepare(
        desired,
        metadata={
            "assembly_counts": assembly_counts,
            "formal_name": published[0].name,
            "import_name": published[2].name,
        },
    )
    return assembly_counts


def _complete_orchestrated_delivery(
    project_root: Path,
    run_dir: Path,
    import_template: Path,
    formal_template: Path,
    module_path: str,
    batch_status: Path,
    batch_id: str | None,
    product_map: Path | None,
    page_discovery: Path | None,
    product_name: str | None,
) -> dict[str, int]:
    """Recover or execute the crash-safe formal delivery transaction."""

    locked_product = validate_delivery_scope(
        batch_status, batch_id, module_path, product_name
    )
    if locked_product != product_name:
        raise RuntimeError("Delivery scope changed before the locked transaction began")
    identity = _delivery_transaction_identity(
        project_root, run_dir, module_path, batch_id, product_name
    )
    transaction = DurableFileTransaction(
        project_root,
        run_dir / "orchestration" / "delivery-transaction",
        identity,
    )
    journal = transaction.load()
    if journal is not None and journal["status"] == "ROLLED_BACK":
        transaction.reset_terminal()
        journal = None

    state = orchestration_status_under_lock(run_dir)
    if (
        state.get("state") == "COMPLETE"
        and journal is not None
        and journal["status"] not in {"FINALIZING", "FINALIZED"}
    ):
        raise RuntimeError(
            "Orchestration is COMPLETE but the durable delivery journal never entered "
            f"FINALIZING; illegal recovery combination: {journal['status']} + COMPLETE"
        )
    if journal is not None and journal["status"] == "FINALIZED":
        transaction.verify_committed(journal)
        if state.get("state") != "COMPLETE":
            raise RuntimeError(
                "Delivery journal is finalized but orchestration is not COMPLETE; fail closed"
            )
        transaction.cleanup_payloads()
        counts = dict(journal["metadata"].get("assembly_counts") or {})
        return {str(key): int(value) for key, value in counts.items()}

    if state.get("state") not in {"DELIVERY_RUNNING", "COMPLETE"}:
        # Preserve the public orchestration guard and its actionable error
        # contract while staying inside the caller-owned run lock.
        validate_delivery_running_state(run_dir)
        raise AssertionError("unreachable delivery state guard")
    if state.get("state") == "DELIVERY_RUNNING":
        try:
            validate_delivery_running_state(run_dir)
            validate_batch_artifacts(run_dir, "cases")
            validate_review_artifacts(run_dir)
        except BaseException as exc:
            if (
                isinstance(exc, (Exception, SystemExit))
                and journal is not None
                and journal["status"] in {"PREPARED", "APPLYING", "FILES_COMMITTED"}
            ):
                transaction.rollback()
            raise

    if journal is None:
        if state.get("state") == "COMPLETE":
            raise RuntimeError(
                "Orchestration is COMPLETE but no durable delivery journal exists; "
                "validate the existing receipt instead of silently republishing"
            )
        if transaction.transaction_dir.exists():
            shutil.rmtree(transaction.transaction_dir)
        try:
            assembly_counts = _prepare_delivery_transaction(
                transaction,
                project_root,
                run_dir,
                import_template,
                formal_template,
                module_path,
                batch_status,
                batch_id,
                product_map,
                page_discovery,
                product_name,
            )
        except BaseException:
            if not transaction.journal_path.exists():
                shutil.rmtree(transaction.transaction_dir, ignore_errors=True)
            raise
        journal = transaction.load()
        assert journal is not None
    else:
        assembly_counts = {
            str(key): int(value)
            for key, value in dict(journal["metadata"].get("assembly_counts") or {}).items()
        }

    try:
        journal = transaction.apply_all()
        cleanup_batch_artifacts(batch_status)
        published = delivery_publication_paths(project_root, module_path, product_name)
        validator_args = [
            "--workbook",
            str(published[3]),
            "--import-workbook",
            str(published[4]),
            "--batch-status",
            str(batch_status),
        ]
        if product_map:
            validator_args.extend(["--product-map", str(product_map)])
        if page_discovery:
            validator_args.extend(["--page-discovery", str(page_discovery)])
        run_python_script(
            Path(__file__).resolve().parent / "validate-test-design-deliverable.py",
            validator_args,
        )
    except BaseException as exc:
        current = transaction.load()
        recoverable_failure = isinstance(exc, (Exception, SystemExit))
        if (
            recoverable_failure
            and current is not None
            and current["status"] != "FINALIZING"
        ):
            transaction.rollback()
        raise

    if journal["status"] != "FINALIZING":
        journal = transaction.set_status("FINALIZING")
    state = orchestration_status_under_lock(run_dir)
    if state.get("state") == "DELIVERY_RUNNING":
        complete_delivery_orchestration(run_dir)
    elif state.get("state") != "COMPLETE":
        raise RuntimeError(
            "Delivery files committed but orchestration cannot be deterministically finalized: "
            f"{state.get('state')}"
        )

    transaction.set_status("FINALIZED")
    transaction.cleanup_payloads()
    published = delivery_publication_paths(project_root, module_path, product_name)
    receipt = run_dir / "artifacts" / "data" / DELIVERY_RECEIPT
    print("OK: delivery outputs published and validated:")
    for path in published:
        print(f"- {relative_project_path(project_root, path)}")
    print(f"- {relative_project_path(project_root, receipt)}")
    return assembly_counts


def complete_deliverables(
    project_root: Path,
    formal_workbook: Path,
    import_template: Path,
    module_path: str,
    import_workbook: Path | None = None,
    batch_status: Path | None = None,
    batch_id: str | None = None,
    product_map: Path | None = None,
    page_discovery: Path | None = None,
    product_name: str | None = None,
    scripts_path: Path | None = None,
    assembly_run_dir: Path | None = None,
    formal_template: Path | None = None,
) -> dict[str, int]:
    project_root = project_root.resolve()
    run_dir = batch_status.resolve().parent if batch_status else None
    if batch_status:
        product_name = validate_delivery_scope(batch_status.resolve(), batch_id, module_path, product_name)
    script_dir = Path(__file__).resolve().parent
    _, _, import_name = deliverable_names(module_path, product_name)
    target_import = import_workbook or (project_root / "docs" / "test-assets" / "imports" / import_name)
    _, formal_name, _ = deliverable_names(module_path, product_name)
    mutable_paths = [
        formal_workbook,
        target_import,
        project_root / "docs" / "test-assets" / "modules" / formal_name,
        project_root / "docs" / "test-assets" / "imports" / import_name,
        project_root / "docs" / "test-design" / "current" / formal_name,
        project_root / "docs" / "test-design" / "deliverables" / formal_name,
        project_root / "docs" / "test-design" / "deliverables" / import_name,
    ]
    if batch_status:
        mutable_paths.extend(
            [
                batch_status,
                batch_status.resolve().parent / "batch-plan.md",
                batch_status.resolve().parent / "batch-review.md",
                batch_status.resolve().parent / "artifacts" / "data" / DELIVERY_RECEIPT,
            ]
        )
    if product_map:
        mutable_paths.append(product_map)
        if page_discovery:
            mutable_paths.extend(product_map_mutable_paths(product_map, module_path, product_name))

    if scripts_path and scripts_path.exists():
        run_python_script(script_dir / "validate-generated-python-scripts.py", ["--path", str(scripts_path)])
    orchestrated = bool(run_dir and (run_dir / "orchestration" / "run-manifest.json").exists())
    if orchestrated:
        assert run_dir is not None
        _require_canonical_delivery_source(
            batch_status, run_dir / "batch-status.csv", "batch-status.csv"
        )
        _require_canonical_delivery_source(
            page_discovery,
            run_dir / "page-discovery.csv",
            "page-discovery.csv",
        )
        _require_canonical_delivery_source(
            product_map,
            project_root / "docs" / "test-assets" / "product-map.xlsx",
            "product-map.xlsx",
        )
        canonical_import = delivery_publication_paths(
            project_root, module_path, product_name
        )[4]
        if import_workbook is not None and (
            import_workbook.is_symlink()
            or import_workbook.resolve(strict=False) != canonical_import.resolve(strict=False)
        ):
            raise ValueError(
                "orchestrated delivery publishes only the canonical import archive; "
                "external --import-workbook output is not allowed"
            )
        if assembly_run_dir is None or assembly_run_dir.resolve() != run_dir:
            raise ValueError(
                "orchestrated delivery must assemble from the reviewed run-dir inside the locked transaction; "
                "external --formal-workbook input is not allowed"
            )
        if formal_template is None:
            raise ValueError("orchestrated delivery requires the standard formal template")
        delivery_lock = project_root / ".test-design-locks" / "delivery.lock"
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                exclusive_process_lock(run_dir / "orchestration" / ".orchestrator.lock")
            )
            stack.enter_context(exclusive_process_lock(delivery_lock))
            if product_map:
                stack.enter_context(
                    exclusive_process_lock(project_root / ".test-design-locks" / "catalog.lock")
                )
            return _complete_orchestrated_delivery(
                project_root,
                run_dir,
                import_template,
                formal_template,
                module_path,
                batch_status,
                batch_id,
                product_map,
                page_discovery,
                product_name,
            )
    assembly_counts: dict[str, int] = {}
    delivery_lock = project_root / ".test-design-locks" / "delivery.lock"
    with contextlib.ExitStack() as stack:
        # Hold the run lock from final cases/review validation through receipt
        # publication, so a concurrent rework/result submission cannot race the
        # delivery transaction after Review has passed.
        if orchestrated and run_dir:
            stack.enter_context(exclusive_process_lock(run_dir / "orchestration" / ".orchestrator.lock"))
        stack.enter_context(exclusive_process_lock(delivery_lock))
        if product_map:
            stack.enter_context(
                exclusive_process_lock(project_root / ".test-design-locks" / "catalog.lock")
            )
        if orchestrated and run_dir:
            validate_delivery_running_state(run_dir)
        if run_dir:
            validate_batch_artifacts(run_dir, "cases")
            if orchestrated:
                validate_review_artifacts(run_dir)
        stack.enter_context(rollback_files_on_error(mutable_paths))
        if orchestrated and run_dir and formal_template:
            assembly_counts = assemble_formal_workbook(run_dir, formal_template, formal_workbook)
        apply_formal_workbook_styles(formal_workbook)
        generate_import_workbook(formal_workbook, import_template, target_import, module_path, product_name)

        finalize_deliverables(
            project_root,
            formal_workbook,
            target_import,
            module_path,
            batch_status,
            batch_id,
            product_map,
            page_discovery,
            product_name,
        )

        validator_args = ["--workbook", str(formal_workbook), "--import-workbook", str(target_import)]
        if batch_status:
            validator_args.extend(["--batch-status", str(batch_status)])
        if product_map:
            validator_args.extend(["--product-map", str(product_map)])
        if page_discovery:
            validator_args.extend(["--page-discovery", str(page_discovery)])
        run_python_script(script_dir / "validate-test-design-deliverable.py", validator_args)
        published = [
            project_root / "docs" / "test-design" / "current" / formal_name,
            project_root / "docs" / "test-design" / "deliverables" / formal_name,
            project_root / "docs" / "test-design" / "deliverables" / import_name,
            project_root / "docs" / "test-assets" / "modules" / formal_name,
            project_root / "docs" / "test-assets" / "imports" / import_name,
        ]
        missing_published = [str(path) for path in published if not path.exists() or path.stat().st_size == 0]
        if missing_published:
            raise ValueError(f"Delivery reported success but published files are missing or empty: {missing_published}")
        formal_hash = hashlib.sha256(formal_workbook.read_bytes()).hexdigest()
        import_hash = hashlib.sha256(target_import.read_bytes()).hexdigest()
        for path in published:
            expected_hash = import_hash if path.name == import_name else formal_hash
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"Published file hash differs from its validated source: {path}")
        receipt: Path | None = None
        if batch_status:
            receipt = write_delivery_receipt(
                project_root,
                batch_status,
                published,
                product_map,
                page_discovery,
                module_path,
                product_name,
            )
        if orchestrated and run_dir:
            complete_delivery_orchestration(run_dir)
        print("OK: delivery outputs published and validated:")
        for path in published:
            print(f"- {relative_project_path(project_root, path)}")
        if receipt:
            print(f"- {relative_project_path(project_root, receipt)}")
    return assembly_counts


def generate_import_workbook(
    formal_workbook: Path,
    import_template: Path,
    output: Path,
    module_path: str,
    product_name: str | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = temporary_sibling(output)
    try:
        populate_import_workbook(formal_workbook, import_template, temporary_output, module_path, product_name)
        os.replace(temporary_output, output)
    finally:
        temporary_output.unlink(missing_ok=True)


def populate_import_workbook(
    formal_workbook: Path,
    import_template: Path,
    output: Path,
    module_path: str,
    product_name: str | None = None,
) -> None:
    shutil.copy2(import_template, output)

    formal_wb = load_workbook(formal_workbook)
    if FORMAL_FUNCTION_SHEET not in formal_wb.sheetnames:
        raise ValueError(f"Formal workbook is missing sheet: {FORMAL_FUNCTION_SHEET}")
    function_ws = formal_wb[FORMAL_FUNCTION_SHEET]
    function_headers = header_map(function_ws)

    import_wb = load_workbook(output)
    import_ws = import_wb[import_wb.sheetnames[0]]
    import_headers = header_map(import_ws)
    clear_data_rows(import_ws)

    canonical_path = ">".join(canonical_module_parts(module_path, product_name)) or module_path
    modules = module_names(canonical_path)
    write_row = 2
    skipped_cases: list[str] = []
    for row_index in range(2, function_ws.max_row + 1):
        case = row_dict(function_ws, function_headers, row_index)
        if not case.get("用例 ID") and not case.get("用例标题"):
            continue
        if not is_importable_function_case(case):
            skipped_cases.append(case.get("用例 ID") or case.get("用例标题") or f"row {row_index}")
            continue
        copy_row_style(import_ws, 2 if import_ws.max_row >= 2 else 1, write_row)
        dfx_dimension = case.get("DFX维度", "")
        dfx_scenario = case.get("DFX场景", "")
        tags = ";".join(part for part in [case.get("模块", ""), case.get("功能点", ""), dfx_dimension, dfx_scenario] if part)
        dfx_note = f"DFX覆盖：{dfx_dimension}-{dfx_scenario}" if dfx_dimension and dfx_scenario else ""
        remarks = "\n".join(part for part in [dfx_note, case.get("备注", "")] if part)
        mapped = {
            "一级模块名称": modules[0],
            "二级模块名称": modules[1],
            "三级模块名称": modules[2],
            "四级模块名称": modules[3],
            "五级模块名称": modules[4],
            "测试用例序号": str(write_row - 1),
            "测试用例名称": case.get("用例标题", ""),
            "测试步骤描述": case.get("操作步骤", ""),
            "测试步骤预期结果": case.get("预期结果", ""),
            "测试类型": normalize_test_type(case.get("测试类型", "")),
            "测试用例级别": normalize_case_level(case.get("优先级", "")),
            "执行方式": execution_mode(case),
            "测试用例说明": case.get("功能点", ""),
            "前置条件": case.get("前置条件", ""),
            "标签": tags,
            "备注": remarks,
        }
        for field in IMPORT_AUTO_FIELDS:
            mapped[field] = ""
        for field, value in mapped.items():
            column = import_headers.get(field)
            if column:
                import_ws.cell(row=write_row, column=column, value=value)
        set_wrap(import_ws, import_headers, write_row, IMPORT_MULTILINE_FIELDS)
        import_ws.row_dimensions[write_row].height = max(import_ws.row_dimensions[write_row].height or 18, 60)
        write_row += 1
    if skipped_cases:
        print(
            "WARN: skipped non-functional/performance-style cases when generating import workbook: "
            + ", ".join(skipped_cases[:20]),
            file=sys.stderr,
        )

    template_wb = load_workbook(import_template)
    apply_template_workbook_format(import_wb, template_wb)
    for row_index in range(2, import_ws.max_row + 1):
        set_wrap(import_ws, import_headers, row_index, IMPORT_MULTILINE_FIELDS)
        import_ws.row_dimensions[row_index].height = max(import_ws.row_dimensions[row_index].height or 18, 60)
    remove_workbook_tables_and_refresh_filters(import_wb)
    import_wb.save(output)


def apply_formal_workbook_styles(workbook: Path, output: Path | None = None, template: Path | None = None) -> None:
    target = output or workbook
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy(workbook, output)
    wb = load_workbook(target)
    template_path = template or (Path(__file__).resolve().parents[1] / "docs" / "test-design" / "codebuddy-test-design-template.xlsx")
    if template_path.exists():
        template_wb = load_workbook(template_path)
        apply_template_workbook_format(wb, template_wb)
    for sheet_name, fields in FORMAL_MULTILINE_FIELDS.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        headers = header_map(ws)
        for row_index in range(2, ws.max_row + 1):
            set_wrap(ws, headers, row_index, fields)
            ws.row_dimensions[row_index].height = max(ws.row_dimensions[row_index].height or 18, 60)
    remove_workbook_tables_and_refresh_filters(wb)
    atomic_save_workbook(wb, target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or normalize test design Excel deliverables.")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-import", help="Generate test-system import workbook from formal test design workbook.")
    gen.add_argument("--formal-workbook", required=True, type=Path)
    gen.add_argument("--import-template", required=True, type=Path)
    gen.add_argument("--output", required=True, type=Path)
    gen.add_argument("--module-path", required=True)
    gen.add_argument("--product-name")

    style = sub.add_parser("fix-formal-styles", help="Apply required multiline wrapping styles to a formal workbook.")
    style.add_argument("--workbook", required=True, type=Path)
    style.add_argument("--output", type=Path)
    style.add_argument("--template", type=Path)

    init = sub.add_parser("init-batch-run", help="Create a standard batch-run ledger from templates before page discovery.")
    init.add_argument("--project-root", required=True, type=Path)
    init.add_argument("--run-id", required=True)
    init.add_argument("--module-path", required=True)
    init.add_argument("--batch-id", default="BATCH-001")
    init.add_argument(
        "--product-name",
        required=True,
        help="Explicit product/system name persisted in batch-scope.json and reused by delivery.",
    )
    init_mode = init.add_mutually_exclusive_group()
    init_mode.add_argument("--resume", action="store_true", help="Reuse an existing batch run without changing its ledgers.")
    init_mode.add_argument(
        "--force-reinitialize",
        action="store_true",
        help="Back up an existing batch run and initialize a clean replacement.",
    )

    batch_gate = sub.add_parser("validate-batch-artifacts", help="Validate batch-run CSV ledgers, element DFX minimums, CRUD lifecycle, and case shards before continuing.")
    batch_gate.add_argument("--run-dir", required=True, type=Path)
    batch_gate.add_argument("--phase", choices=["discovery", "plan", "risk", "cases"], default="cases")
    batch_gate.add_argument("--no-cache", action="store_true", help="Ignore a successful hash cache and run the phase again.")

    prepare_cases = sub.add_parser("prepare-function-case-generation", help="Remove stale function case shards and manifest before generating new JSON shards.")
    prepare_cases.add_argument("--run-dir", required=True, type=Path)

    no_risk = sub.add_parser("record-risk-none", help="Record that full exploration found no model uncertainty; does not impersonate user confirmation.")
    no_risk.add_argument("--run-dir", required=True, type=Path)

    pipeline_status = sub.add_parser("pipeline-status", help="Derive the next batch action from validated artifacts instead of trusting manual status text.")
    pipeline_status.add_argument("--run-dir", required=True, type=Path)
    pipeline_status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    agent_run = sub.add_parser(
        "agent-run",
        help="Advance the required final multi-agent architecture and emit the next isolated AgentTask packet(s).",
    )
    agent_run.add_argument("--run-dir", required=True, type=Path)
    agent_run.add_argument("--json", action="store_true", help="Emit one clean machine-readable JSON document.")

    agent_status = sub.add_parser(
        "agent-status",
        help="Read state without advancing phases; repair audit projections after an interrupted write if required.",
    )
    agent_status.add_argument("--run-dir", required=True, type=Path)
    agent_status.add_argument("--json", action="store_true")

    page_probe_commit = sub.add_parser(
        "page-probe-commit",
        help="Validate coordinator preflight PostToolUse records and bind one receipt to a future Discovery execution.",
    )
    page_probe_commit.add_argument("--run-dir", required=True, type=Path)
    page_probe_commit.add_argument("--task-id", required=True)
    page_probe_commit.add_argument("--execution-id", required=True)
    page_probe_commit.add_argument("--coordinator-id", required=True)
    page_probe_commit.add_argument("--session-sha256", required=True)
    page_probe_commit.add_argument("--transcript-sha256", required=True)
    page_probe_commit.add_argument("--record-id", required=True, action="append")
    page_probe_commit.add_argument("--evidence", required=True, action="append")
    page_probe_commit.add_argument("--json", action="store_true")

    agent_claim = sub.add_parser(
        "agent-claim",
        help="Atomically bind one runnable AgentTask to one durable execution identity before dispatch.",
    )
    agent_claim.add_argument("--run-dir", required=True, type=Path)
    agent_claim.add_argument("--task-id", required=True)
    agent_claim.add_argument("--execution-id", required=True)
    agent_claim.add_argument("--coordinator-id", required=True)
    agent_claim.add_argument("--executor-id", required=True)
    agent_claim.add_argument(
        "--executor-kind",
        required=True,
        choices=[
            "codebuddy-subagent", "codebuddy-main-session",
            "codebuddy-agent-team", "external-session",
        ],
    )
    agent_claim.add_argument("--wave-id", required=True)
    agent_claim.add_argument("--page-probe-receipt-id")
    agent_claim.add_argument("--page-probe-receipt-fingerprint")
    agent_claim.add_argument("--json", action="store_true")

    agent_submit = sub.add_parser("agent-submit", help="Validate and submit one isolated AgentResult, then advance safely.")
    agent_submit.add_argument("--run-dir", required=True, type=Path)
    agent_submit.add_argument("--task-id", required=True)
    agent_submit.add_argument("--execution-id", required=True)
    agent_submit.add_argument("--result", required=True, type=Path)
    agent_submit.add_argument("--json", action="store_true")

    agent_release = sub.add_parser(
        "agent-release",
        help="Release a durable task claim only after explicitly confirming that execution caused no side effects.",
    )
    agent_release.add_argument("--run-dir", required=True, type=Path)
    agent_release.add_argument("--task-id", required=True)
    agent_release.add_argument("--execution-id", required=True)
    agent_release.add_argument("--coordinator-id", required=True)
    agent_release.add_argument("--reason", required=True)
    agent_release.add_argument("--confirm-no-side-effects", action="store_true")
    agent_release.add_argument("--json", action="store_true")

    agent_resume = sub.add_parser("agent-resume", help="Resume a genuinely external-blocked Agent phase.")
    agent_resume.add_argument("--run-dir", required=True, type=Path)
    agent_resume.add_argument("--json", action="store_true")

    review_gate = sub.add_parser(
        "validate-review-artifacts",
        help="Run the mandatory independent read-only Review Gate before delivery.",
    )
    review_gate.add_argument("--run-dir", required=True, type=Path)

    assemble = sub.add_parser("assemble-formal-workbook", help="Assemble all 8 formal-design Sheets from one batch run and the standard template.")
    assemble.add_argument("--run-dir", required=True, type=Path)
    assemble.add_argument("--template", type=Path)
    assemble.add_argument("--output", required=True, type=Path)
    assemble.add_argument("--project-root", type=Path, default=Path("."))

    complete = sub.add_parser("complete-deliverables", help="One-shot precheck, style, import generation, finalize, and delivery validation.")
    complete.add_argument("--project-root", type=Path, default=Path("."))
    complete.add_argument("--run-dir", type=Path, help="Batch run to validate and assemble when --formal-workbook is omitted.")
    complete.add_argument("--formal-workbook", type=Path)
    complete.add_argument("--formal-template", type=Path)
    complete.add_argument("--import-template", type=Path)
    complete.add_argument("--module-path", required=True)
    complete.add_argument("--import-workbook", type=Path)
    complete.add_argument("--batch-status", type=Path)
    complete.add_argument("--batch-id")
    complete.add_argument("--product-map", type=Path)
    complete.add_argument("--page-discovery", type=Path)
    complete.add_argument("--product-name")
    complete.add_argument("--scripts-path", type=Path)

    sync = sub.add_parser("sync-product-map", help="Sync product-map.xlsx from a formal workbook and page-discovery.csv.")
    sync.add_argument("--product-map", required=True, type=Path)
    sync.add_argument("--formal-workbook", required=True, type=Path)
    sync.add_argument("--page-discovery", required=True, type=Path)
    sync.add_argument("--module-path", required=True)
    sync.add_argument("--archive-path", required=True)
    sync.add_argument("--product-name")

    migrate_facts = sub.add_parser(
        "migrate-product-facts",
        help="Migrate existing real product-map.xlsx rows into the versioned JSON fact catalog.",
    )
    migrate_facts.add_argument("--product-map", required=True, type=Path)

    validate_facts = sub.add_parser("validate-product-facts", help="Validate all versioned product fact documents.")
    validate_facts.add_argument("--product-map", required=True, type=Path)

    rebuild_map = sub.add_parser("rebuild-product-map", help="Rebuild product-map.xlsx from the JSON fact catalog.")
    rebuild_map.add_argument("--product-map", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "generate-import":
        reject_direct_protected_output(args.output, "generate-import")
        generate_import_workbook(args.formal_workbook, args.import_template, args.output, args.module_path, args.product_name)
    elif args.command == "fix-formal-styles":
        reject_direct_protected_output(args.output or args.workbook, "fix-formal-styles")
        apply_formal_workbook_styles(args.workbook, args.output, args.template)
    elif args.command == "init-batch-run":
        init_batch_run(
            args.project_root,
            args.run_id,
            args.module_path,
            args.batch_id,
            args.product_name,
            args.resume,
            args.force_reinitialize,
        )
    elif args.command == "validate-batch-artifacts":
        validate_batch_artifacts(args.run_dir, args.phase, use_cache=not args.no_cache)
    elif args.command == "prepare-function-case-generation":
        prepare_function_case_generation(args.run_dir)
    elif args.command == "record-risk-none":
        record_no_model_uncertainty(args.run_dir)
    elif args.command == "pipeline-status":
        if args.json:
            with contextlib.redirect_stdout(io.StringIO()):
                status = derive_pipeline_status(args.run_dir)
        else:
            status = derive_pipeline_status(args.run_dir)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print(f"state={status['state']}")
            print(f"next_action={status['next_action']}")
            if status.get("command"):
                print(f"command={status['command']}")
            for reason in status.get("reasons", []):
                print(f"reason={reason}")
    elif args.command in {
        "agent-run", "agent-status", "page-probe-commit", "agent-claim", "agent-submit", "agent-release", "agent-resume"
    }:
        with contextlib.redirect_stdout(io.StringIO()):
            if args.command == "agent-run":
                status = advance_orchestration(args.run_dir)
            elif args.command == "agent-status":
                status = orchestration_status(args.run_dir)
            elif args.command == "page-probe-commit":
                status = commit_page_probe_receipt(
                    args.run_dir,
                    args.task_id,
                    execution_id=args.execution_id,
                    coordinator_id=args.coordinator_id,
                    session_sha256=args.session_sha256,
                    transcript_sha256=args.transcript_sha256,
                    record_ids=args.record_id,
                    evidence_paths=args.evidence,
                )
            elif args.command == "agent-claim":
                status = claim_agent_task(
                    args.run_dir,
                    args.task_id,
                    execution_id=args.execution_id,
                    coordinator_id=args.coordinator_id,
                    executor_id=args.executor_id,
                    executor_kind=args.executor_kind,
                    wave_id=args.wave_id,
                    page_probe_receipt_id=args.page_probe_receipt_id,
                    page_probe_receipt_fingerprint=args.page_probe_receipt_fingerprint,
                )
            elif args.command == "agent-submit":
                status = submit_agent_result(
                    args.run_dir,
                    args.task_id,
                    args.result,
                    execution_id=args.execution_id,
                )
            elif args.command == "agent-release":
                status = release_agent_claim(
                    args.run_dir,
                    args.task_id,
                    execution_id=args.execution_id,
                    coordinator_id=args.coordinator_id,
                    reason=args.reason,
                    confirm_no_side_effects=args.confirm_no_side_effects,
                )
            else:
                status = resume_external_block(args.run_dir)
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print(f"state={status['state']}")
            if status.get("claim"):
                print(f"execution_id={status['claim']['execution_id']}")
            if status.get("page_probe_receipt"):
                receipt = status["page_probe_receipt"]
                print(f"page_probe_receipt_id={receipt['receipt_id']}")
                print(f"page_probe_receipt_fingerprint={receipt['receipt_fingerprint']}")
                for tool_name in receipt["approved_mcp_tools"]:
                    print(f"approved_page_mcp_tool={tool_name}")
            for task in status.get("runnable_tasks", []):
                task_id = task.get("task_id", "")
                role = task.get("agent_role", "")
                print(f"task={task_id} role={role}")
                print(
                    "task_packet="
                    + str(args.run_dir.resolve() / "artifacts" / "agent-work" / role / task_id / "meta" / "agent-task.json")
                )
            if status.get("delivery_command"):
                print(f"command={status['delivery_command']}")
    elif args.command == "validate-review-artifacts":
        if not validate_review_artifacts(args.run_dir):
            raise ValueError("Review Gate is not applicable because this run has no final orchestration manifest")
        print(f"OK: independent Review Gate passed: {args.run_dir.resolve()}")
    elif args.command == "assemble-formal-workbook":
        project_root = args.project_root.resolve()
        template = args.template or (project_root / "docs" / "test-design" / "codebuddy-test-design-template.xlsx")
        validate_assembly_preview_output(args.run_dir, args.output)
        counts = assemble_formal_workbook(args.run_dir, template, args.output)
        print(f"OK: assembled formal workbook: {args.output} ({sum(counts.values())} total data rows)")
    elif args.command == "complete-deliverables":
        project_root = args.project_root.resolve()
        run_dir = args.run_dir.resolve() if args.run_dir else None
        batch_status = args.batch_status or (run_dir / "batch-status.csv" if run_dir else None)
        page_discovery = args.page_discovery or (run_dir / "page-discovery.csv" if run_dir else None)
        scripts_path = args.scripts_path or (run_dir / "artifacts" / "scripts" if run_dir else None)
        product_map = args.product_map or (project_root / "docs" / "test-assets" / "product-map.xlsx" if run_dir else None)
        import_template = args.import_template or (project_root / "docs" / "test-design" / "测试用例模板.xlsx")
        formal_template = args.formal_template or (project_root / "docs" / "test-design" / "codebuddy-test-design-template.xlsx")
        if page_discovery and not batch_status:
            raise SystemExit(
                "ERROR: --batch-status is required when --page-discovery is provided. "
                "Run init-batch-run first and keep batch-plan.md, batch-status.csv, batch-review.md, and page-discovery.csv together."
            )
        if args.formal_workbook:
            raise SystemExit(
                "ERROR: final architecture does not allow external --formal-workbook publication. "
                "Use --run-dir so reviewed JSON is assembled inside the locked complete-deliverables transaction."
            )
        if run_dir:
            with tempfile.TemporaryDirectory(prefix="test-design-formal-") as value:
                assembled = Path(value) / "formal.xlsx"
                counts = complete_deliverables(
                    project_root, assembled, import_template, args.module_path,
                    args.import_workbook, batch_status, args.batch_id, product_map,
                    page_discovery, args.product_name, scripts_path,
                    assembly_run_dir=run_dir, formal_template=formal_template,
                )
                print(f"OK: assembled and delivered {counts.get(FORMAL_FUNCTION_SHEET, 0)} function case(s) from {run_dir}")
        else:
            raise SystemExit(
                "ERROR: final architecture requires complete-deliverables --run-dir; "
                "external --formal-workbook publication is disabled"
            )
    elif args.command == "sync-product-map":
        discovery_run = args.page_discovery.resolve().parent
        if (discovery_run / "orchestration" / "run-manifest.json").is_file():
            raise ValueError(
                "sync-product-map cannot publish facts for an orchestrated run; "
                "complete-deliverables owns Review validation, locking, publication, and receipt creation"
            )
        root = _containing_project_root(args.product_map) or Path.cwd().resolve()
        with exclusive_process_lock(root / ".test-design-locks" / "catalog.lock"):
            sync_product_map(
                args.product_map,
                args.formal_workbook,
                args.page_discovery,
                args.module_path,
                args.archive_path,
                args.product_name,
            )
    elif args.command == "migrate-product-facts":
        ensure_catalog(args.product_map)
        rebuild_index(args.product_map)
        counts = validate_catalog(args.product_map)
        print(f"OK: migrated product facts: {sum(counts.values())} record(s)")
    elif args.command == "validate-product-facts":
        counts = validate_catalog(args.product_map)
        print(f"OK: product fact catalog is valid: {sum(counts.values())} record(s)")
    elif args.command == "rebuild-product-map":
        validate_catalog(args.product_map)
        project_catalog_to_workbook(args.product_map)
        print(f"OK: rebuilt product map from catalog: {args.product_map}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
