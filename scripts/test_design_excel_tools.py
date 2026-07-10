# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

from test_design.io_utils import (
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


IMPORT_AUTO_FIELDS = {"测试用例系统编号", "作者"}
IMPORT_ALLOWED_VALUES = {
    "测试类型": {"功能测试", "性能规格测试", "可靠性测试", "兼容性测试", "可维护性测试", "安全性测试", "易用性测试"},
    "测试用例级别": {"L1", "L2", "L3", "L4"},
    "执行方式": {"自动化", "手动"},
}
IMPORT_EXCLUDED_TEST_TYPES = {"性能规格测试"}
IMPORT_EXCLUDED_DFX_DIMENSIONS = {"DFP性能"}
IMPORT_EXCLUDED_DFX_EXTREME_SCENARIOS = {"压力极限", "资源耗尽", "并发极限"}


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


def update_batch_status_paths(batch_status: Path, batch_id: str | None, archive_rel: str, import_rel: str) -> list[dict[str, str]]:
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
    prepare_function_case_generation,
    validate_batch_artifacts,
    init_batch_run,
)


from test_design.product_map_sync import sync_product_map


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

    apply_formal_workbook_styles(formal_workbook)
    import_wb = load_workbook(import_workbook)
    remove_workbook_tables_and_refresh_filters(import_wb)
    atomic_save_workbook(import_wb, import_workbook)

    module_archive = project_root / "docs" / "test-assets" / "modules" / formal_name
    import_archive = project_root / "docs" / "test-assets" / "imports" / import_name
    current_copy = project_root / "docs" / "test-design" / "current" / formal_name
    deliverable_formal = project_root / "docs" / "test-design" / "deliverables" / formal_name
    deliverable_import = project_root / "docs" / "test-design" / "deliverables" / import_name

    for target in [module_archive, current_copy, deliverable_formal]:
        copy_workbook(formal_workbook, target)
    for target in [import_archive, deliverable_import]:
        copy_workbook(import_workbook, target)

    if batch_status:
        changes = update_batch_status_paths(
            batch_status,
            batch_id,
            relative_project_path(project_root, module_archive),
            relative_project_path(project_root, import_archive),
        )
        sync_batch_markdown_paths(batch_status, changes)
        cleanup_batch_artifacts(batch_status)
    if product_map and page_discovery:
        sync_product_map(
            product_map,
            module_archive,
            page_discovery,
            module_path,
            relative_project_path(project_root, module_archive),
            product_name,
        )


def run_python_script(script: Path, args: list[str]) -> None:
    completed = subprocess.run([sys.executable, str(script), *args], check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


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
) -> None:
    project_root = project_root.resolve()
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
            ]
        )
    if product_map:
        mutable_paths.append(product_map)

    if scripts_path and scripts_path.exists():
        run_python_script(script_dir / "validate-generated-python-scripts.py", ["--path", str(scripts_path)])
    if batch_status:
        validate_batch_artifacts(batch_status.resolve().parent, "cases")

    delivery_lock = project_root / ".test-design-locks" / "delivery.lock"
    with exclusive_process_lock(delivery_lock), rollback_files_on_error(mutable_paths):
        apply_formal_workbook_styles(formal_workbook)
        generate_import_workbook(formal_workbook, import_template, target_import, module_path, product_name)

        # Validate workbook content before publishing copies or updating shared ledgers.
        run_python_script(
            script_dir / "validate-test-design-deliverable.py",
            ["--workbook", str(formal_workbook), "--import-workbook", str(target_import)],
        )
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
    init.add_argument("--product-name")
    init_mode = init.add_mutually_exclusive_group()
    init_mode.add_argument("--resume", action="store_true", help="Reuse an existing batch run without changing its ledgers.")
    init_mode.add_argument(
        "--force-reinitialize",
        action="store_true",
        help="Back up an existing batch run and initialize a clean replacement.",
    )

    batch_gate = sub.add_parser("validate-batch-artifacts", help="Validate batch-run CSV ledgers, element DFX minimums, CRUD lifecycle, and case shards before continuing.")
    batch_gate.add_argument("--run-dir", required=True, type=Path)
    batch_gate.add_argument("--phase", choices=["discovery", "plan", "cases"], default="cases")

    prepare_cases = sub.add_parser("prepare-function-case-generation", help="Remove stale function case shards and manifest before generating new JSON shards.")
    prepare_cases.add_argument("--run-dir", required=True, type=Path)

    finalize = sub.add_parser("finalize-deliverables", help="Copy validated workbooks to current/deliverables/internal archives and update batch-status paths.")
    finalize.add_argument("--project-root", required=True, type=Path)
    finalize.add_argument("--formal-workbook", required=True, type=Path)
    finalize.add_argument("--import-workbook", required=True, type=Path)
    finalize.add_argument("--module-path", required=True)
    finalize.add_argument("--batch-status", type=Path)
    finalize.add_argument("--batch-id")
    finalize.add_argument("--product-map", type=Path)
    finalize.add_argument("--page-discovery", type=Path)
    finalize.add_argument("--product-name")

    complete = sub.add_parser("complete-deliverables", help="One-shot precheck, style, import generation, finalize, and delivery validation.")
    complete.add_argument("--project-root", required=True, type=Path)
    complete.add_argument("--formal-workbook", required=True, type=Path)
    complete.add_argument("--import-template", required=True, type=Path)
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
        generate_import_workbook(args.formal_workbook, args.import_template, args.output, args.module_path, args.product_name)
    elif args.command == "fix-formal-styles":
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
        validate_batch_artifacts(args.run_dir, args.phase)
    elif args.command == "prepare-function-case-generation":
        prepare_function_case_generation(args.run_dir)
    elif args.command == "finalize-deliverables":
        if args.page_discovery and not args.batch_status:
            raise SystemExit(
                "ERROR: --batch-status is required when --page-discovery is provided. "
                "Run init-batch-run first and keep batch-plan.md, batch-status.csv, batch-review.md, and page-discovery.csv together."
            )
        finalize_deliverables(
            args.project_root,
            args.formal_workbook,
            args.import_workbook,
            args.module_path,
            args.batch_status,
            args.batch_id,
            args.product_map,
            args.page_discovery,
            args.product_name,
        )
    elif args.command == "complete-deliverables":
        if args.page_discovery and not args.batch_status:
            raise SystemExit(
                "ERROR: --batch-status is required when --page-discovery is provided. "
                "Run init-batch-run first and keep batch-plan.md, batch-status.csv, batch-review.md, and page-discovery.csv together."
            )
        complete_deliverables(
            args.project_root,
            args.formal_workbook,
            args.import_template,
            args.module_path,
            args.import_workbook,
            args.batch_status,
            args.batch_id,
            args.product_map,
            args.page_discovery,
            args.product_name,
            args.scripts_path,
        )
    elif args.command == "sync-product-map":
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
