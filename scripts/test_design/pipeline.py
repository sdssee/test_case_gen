# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path

from .batch import (
    DELIVERY_RECEIPT,
    FUNCTION_CASE_MANIFEST,
    evidence_path_exists,
    generation_session_data,
    generation_session_is_current,
    read_csv_exact,
    template_headers,
    validate_batch_artifacts,
)
from .validators.batch_ledgers import risk_confirmation_state, risk_page_verification_state
from .fact_store import validate_catalog


def _failure(stage: str, action: str, reason: str, command: str = "") -> dict[str, object]:
    return {
        "state": stage,
        "next_action": action,
        "command": command,
        "ready": False,
        "reasons": [reason],
    }


def validate_delivery_receipt(
    run_dir: Path,
    project_root: Path,
    expected_files: list[Path],
    require_product_map: bool,
) -> None:
    receipt_path = run_dir / "artifacts" / "data" / DELIVERY_RECEIPT
    if not receipt_path.is_file():
        raise ValueError(f"missing {DELIVERY_RECEIPT}; rerun complete-deliverables")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {DELIVERY_RECEIPT}: {exc}") from exc
    session = generation_session_data(run_dir) or {}
    if not isinstance(receipt, dict) or receipt.get("version") != 1:
        raise ValueError(f"{DELIVERY_RECEIPT} must use version 1 object schema")
    if receipt.get("generation_session_id") != session.get("generation_session_id"):
        raise ValueError(f"{DELIVERY_RECEIPT} generation_session_id does not match current session")
    if receipt.get("source_fingerprint") != session.get("source_fingerprint"):
        raise ValueError(f"{DELIVERY_RECEIPT} source_fingerprint does not match current session")
    if receipt.get("catalog_source_fingerprint") != session.get("catalog_source_fingerprint"):
        raise ValueError(f"{DELIVERY_RECEIPT} catalog_source_fingerprint does not match current session")
    raw_files = receipt.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError(f"{DELIVERY_RECEIPT} must contain a non-empty files list")
    recorded: dict[Path, dict[str, object]] = {}
    root = project_root.resolve()
    for entry in raw_files:
        if not isinstance(entry, dict) or not all(entry.get(field) not in {None, ""} for field in ["path", "size", "sha256"]):
            raise ValueError(f"{DELIVERY_RECEIPT} contains an invalid file record")
        raw = Path(str(entry["path"]))
        if raw.is_absolute():
            raise ValueError(f"{DELIVERY_RECEIPT} paths must be project-relative: {raw}")
        path = (root / raw).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{DELIVERY_RECEIPT} path escapes project root: {raw}") from exc
        if path in recorded:
            raise ValueError(f"{DELIVERY_RECEIPT} contains duplicate path: {raw}")
        if not path.is_file() or path.stat().st_size != int(entry["size"]):
            raise ValueError(f"delivered file is missing or size changed since validation: {raw}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != str(entry["sha256"]):
            raise ValueError(f"delivered file hash changed since validation: {raw}")
        if path.suffix.lower() == ".xlsx" and not zipfile.is_zipfile(path):
            raise ValueError(f"delivered workbook is not a valid XLSX/ZIP package: {raw}")
        recorded[path] = entry
    missing = [str(path) for path in expected_files if path.resolve() not in recorded]
    if missing:
        raise ValueError(f"{DELIVERY_RECEIPT} does not cover required delivery files: {missing}")
    if require_product_map:
        raw_product_map = str(receipt.get("product_map_path", "") or "").strip()
        if not raw_product_map:
            raise ValueError(f"{DELIVERY_RECEIPT} does not identify the validated product map")
        product_map_path = (root / Path(raw_product_map)).resolve()
        try:
            product_map_path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{DELIVERY_RECEIPT} product_map_path escapes project root") from exc
        if product_map_path not in recorded or product_map_path.suffix.lower() != ".xlsx":
            raise ValueError(f"{DELIVERY_RECEIPT} product_map_path is not a validated XLSX file")
        validate_catalog(product_map_path, require_existing=True)


def derive_pipeline_status(run_dir: Path) -> dict[str, object]:
    run_dir = run_dir.resolve()
    for phase, state, action in [
        ("discovery", "DISCOVERY_REQUIRED", "继续默认全量深探，先补全 page-element-inventory.csv，再按交互实例ID补全 page-discovery.csv"),
        ("plan", "PLAN_REQUIRED", "补全结构化元素计划与逐修改项生命周期证据"),
    ]:
        try:
            validate_batch_artifacts(run_dir, phase, use_cache=True)
        except Exception as exc:
            return _failure(
                state,
                action,
                str(exc),
                f"scripts/run-test-design.ps1 validate-batch-artifacts --run-dir \"{run_dir}\" --phase {phase}",
            )

    templates_dir = run_dir.parent / "templates"
    try:
        rows = read_csv_exact(
            run_dir / "risk-confirmation.csv",
            template_headers(templates_dir, "risk-confirmation-template.csv"),
            "risk-confirmation.csv",
        )
        discovery_rows = read_csv_exact(
            run_dir / "page-discovery.csv",
            template_headers(templates_dir, "page-discovery-template.csv"),
            "page-discovery.csv",
        )
        page_verification_state, page_verification_reasons = risk_page_verification_state(
            rows,
            discovery_rows,
            lambda value: evidence_path_exists(run_dir, value),
        )
        if page_verification_state != "ready":
            return _failure(
                "DISCOVERY_REQUIRED",
                "继续页面实探，先自行验证所有可由页面观察的问题",
                "; ".join(page_verification_reasons),
                f"scripts/run-test-design.ps1 validate-batch-artifacts --run-dir \"{run_dir}\" --phase discovery",
            )
        risk_state, reasons = risk_confirmation_state(rows)
    except Exception as exc:
        return _failure("RISK_ASSESSMENT_REQUIRED", "修复风险确认账本", str(exc))
    if risk_state != "ready":
        has_real_uncertainty = any(
            row.get("风险ID", "").strip() not in {"", "RISK-PENDING", "RISK-NONE"}
            for row in rows
        )
        if has_real_uncertainty:
            return _failure("USER_CONFIRMATION_REQUIRED", "仅请用户确认模型仍不理解的内容", "; ".join(reasons))
        return _failure(
            "RISK_ASSESSMENT_REQUIRED",
            "模型归纳不理解项；若没有则记录 RISK-NONE",
            "; ".join(reasons),
            f"scripts/run-test-design.ps1 record-risk-none --run-dir \"{run_dir}\"",
        )
    try:
        validate_batch_artifacts(run_dir, "risk", use_cache=True)
    except Exception as exc:
        return _failure("RISK_LEDGER_REPAIR_REQUIRED", "补全已确认风险的依据和处置策略", str(exc))

    manifest = run_dir / "artifacts" / "data" / FUNCTION_CASE_MANIFEST
    if not generation_session_is_current(run_dir):
        return _failure(
            "CASE_PREPARATION_REQUIRED",
            "清理旧产物并创建绑定当前 plan/risk 的生成会话",
            "generation session is missing or stale",
            f"scripts/run-test-design.ps1 prepare-function-case-generation --run-dir \"{run_dir}\"",
        )
    if not manifest.exists():
        return _failure(
            "CASE_GENERATION_REQUIRED",
            "按 generation-session.json 生成本轮用例分片、manifest 和 Sheet JSON",
            f"missing {manifest.name}",
        )
    try:
        validate_batch_artifacts(run_dir, "cases", use_cache=True)
    except Exception as exc:
        return _failure("CASE_GENERATION_REQUIRED", "补齐或修复当前用例分片和 Sheet JSON", str(exc))

    status_path = run_dir / "batch-status.csv"
    with status_path.open("r", encoding="utf-8-sig", newline="") as stream:
        status_rows = list(csv.DictReader(stream))
    project_root = run_dir.parents[3]
    delivered = bool(status_rows)
    delivery_expected: list[Path] = []
    product_map_required = False
    for row in status_rows:
        delivered = delivered and row.get("状态", "").strip() == "已完成"
        delivered = delivered and all(
            row.get(field, "").strip() in {"是", "通过", "已通过", "已完成"}
            for field in ["导入文件已生成", "产品版图已更新", "覆盖质量自检"]
        )
        archive_raw = row.get("归档路径", "").strip()
        import_raw = row.get("导入文件路径", "").strip()
        archive = project_root / archive_raw if archive_raw else None
        import_file = project_root / import_raw if import_raw else None
        expected_files = [archive, import_file]
        if archive:
            expected_files.extend(
                [
                    project_root / "docs" / "test-design" / "current" / archive.name,
                    project_root / "docs" / "test-design" / "deliverables" / archive.name,
                ]
            )
        if import_file:
            expected_files.append(project_root / "docs" / "test-design" / "deliverables" / import_file.name)
        if row.get("产品版图已更新", "").strip() in {"是", "通过", "已通过", "已完成"}:
            product_map_required = True
        delivery_expected.extend(path for path in expected_files if path is not None)
        delivered = delivered and all(path is not None and path.exists() and path.stat().st_size > 0 for path in expected_files)
    if not delivered:
        return {
            "state": "DELIVERY_REQUIRED",
            "next_action": "运行 complete-deliverables 完成交付、归档与产品版图同步",
            "command": f"scripts/run-test-design.ps1 complete-deliverables --run-dir \"{run_dir}\" --module-path \"<模块路径>\" --batch-id <批次ID>",
            "ready": True,
            "reasons": [],
        }
    try:
        validate_delivery_receipt(run_dir, project_root, delivery_expected, product_map_required)
    except Exception as exc:
        return _failure("DELIVERY_REQUIRED", "重新运行 complete-deliverables 生成可验证交付收据", str(exc))
    return {"state": "COMPLETE", "next_action": "批次已完成", "command": "", "ready": True, "reasons": []}
