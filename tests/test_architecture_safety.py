# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "test_design_excel_tools.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("test_design_excel_tools", MODULE_PATH)
assert SPEC and SPEC.loader
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)


class ArchitectureSafetyTests(unittest.TestCase):
    def test_formal_assembler_populates_all_sheets_and_removes_template_examples(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = root / "run"
            data_dir = run_dir / "artifacts" / "data"
            data_dir.mkdir(parents=True)
            template = REPO_ROOT / "docs" / "test-design" / "codebuddy-test-design-template.xlsx"
            workbook = load_workbook(template, data_only=False)
            sources = {
                "测试设计总览": "overview.json",
                "需求用户故事拆解": "requirements.json",
                "测试场景矩阵": "scenarios.json",
                "性能测试设计": "performance.json",
                "风险与待确认问题": "risks.json",
                "自动化建议": "automation.json",
                "页面元素覆盖清单": "page_elements.json",
            }
            for sheet_name, filename in sources.items():
                headers = [cell.value for cell in workbook[sheet_name][1] if cell.value]
                row = {str(header): f"ASSEMBLED-{index + 1}" for index, header in enumerate(headers)}
                (data_dir / filename).write_text(json.dumps([row], ensure_ascii=False), encoding="utf-8")

            function_headers = [cell.value for cell in workbook["功能测试用例"][1] if cell.value]
            function_row = {str(header): f"ASSEMBLED-{index + 1}" for index, header in enumerate(function_headers)}
            function_row["用例 ID"] = "TC-ASSEMBLED-001"
            function_row["Story ID/需求 ID"] = "REQ-ASSEMBLED-001"
            function_row["用例标题"] = "组装验证-正式用例"
            part_name = "function_cases_part_001.json"
            (data_dir / part_name).write_text(json.dumps([function_row], ensure_ascii=False), encoding="utf-8")
            (data_dir / "function_cases_manifest.json").write_text(
                json.dumps({"part_size": 10, "total_cases": 1, "parts": [part_name]}, ensure_ascii=False),
                encoding="utf-8",
            )

            output = root / "assembled.xlsx"
            counts = TOOLS.assemble_formal_workbook(run_dir, template, output)
            self.assertEqual(1, counts["功能测试用例"])
            assembled = load_workbook(output, data_only=True)
            self.assertEqual("TC-ASSEMBLED-001", assembled["功能测试用例"]["A2"].value)
            for sheet in assembled.worksheets:
                combined = "".join(str(cell.value or "") for row in sheet.iter_rows(min_row=2) for cell in row)
                self.assertNotIn("TC-LOGIN-001", combined)
                self.assertNotIn("示例项目", combined)

    def test_deliverable_validator_rejects_unmodified_formal_template(self) -> None:
        template = REPO_ROOT / "docs" / "test-design" / "codebuddy-test-design-template.xlsx"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "validate-test-design-deliverable.py"),
                "--workbook",
                str(template),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("formal-template example marker", result.stderr + result.stdout)

    def create_project_root(self, root: Path) -> None:
        source = REPO_ROOT / "docs" / "test-assets" / "batch-runs" / "templates"
        target = root / "docs" / "test-assets" / "batch-runs" / "templates"
        shutil.copytree(source, target)

    def test_batch_init_requires_explicit_resume_and_preserves_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            project_root = Path(value)
            self.create_project_root(project_root)
            run_dir = TOOLS.init_batch_run(project_root, "probe", "产品>模块>页面", "BATCH-001")
            status_path = run_dir / "batch-status.csv"
            with status_path.open("r", encoding="utf-8-sig", newline="") as fp:
                reader = csv.DictReader(fp)
                headers = reader.fieldnames or []
                rows = list(reader)
            rows[0]["状态"] = "执行中"
            rows[0]["页面数"] = "3"
            with status_path.open("w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.DictWriter(fp, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)

            with self.assertRaisesRegex(ValueError, "already exists"):
                TOOLS.init_batch_run(project_root, "probe", "产品>模块>页面", "BATCH-001")
            (run_dir / "risk-confirmation.csv").unlink()
            TOOLS.init_batch_run(project_root, "probe", "产品>模块>页面", "BATCH-001", resume=True)

            with status_path.open("r", encoding="utf-8-sig", newline="") as fp:
                resumed = next(csv.DictReader(fp))
            self.assertEqual("执行中", resumed["状态"])
            self.assertEqual("3", resumed["页面数"])
            self.assertTrue((run_dir / "risk-confirmation.csv").exists())

            TOOLS.init_batch_run(
                project_root,
                "probe",
                "产品>模块>页面",
                "BATCH-001",
                force_reinitialize=True,
            )
            with status_path.open("r", encoding="utf-8-sig", newline="") as fp:
                reset = next(csv.DictReader(fp))
            self.assertEqual("待开始", reset["状态"])
            self.assertEqual("0", reset["页面数"])
            self.assertEqual(1, len(list(run_dir.parent.glob("probe_backup_*"))))

    def test_resume_migrates_legacy_risk_driven_deep_dive_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            project_root = Path(value)
            self.create_project_root(project_root)
            run_dir = TOOLS.init_batch_run(project_root, "legacy-risk", "产品>模块>页面", "BATCH-001")
            risk_path = run_dir / "risk-confirmation.csv"
            old_headers = [
                "批次ID", "风险ID", "风险/待确认问题", "用户确认结论", "处置策略", "是否需要补充深探",
                "补充深探目标", "关联页面/入口", "关联元素名称/文案", "补充证据路径", "补充深探状态",
                "关联用例ID", "备注",
            ]
            with risk_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=old_headers)
                writer.writeheader()
                writer.writerow({"批次ID": "BATCH-001", "风险ID": "RISK-001", "风险/待确认问题": "旧问题"})

            TOOLS.init_batch_run(project_root, "legacy-risk", "产品>模块>页面", "BATCH-001", resume=True)

            with risk_path.open("r", encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual("旧问题", row["模型不理解内容/待确认问题"])
            self.assertEqual("待确认", row["确认状态"])
            self.assertTrue(risk_path.with_suffix(".pre-default-deep-dive.csv").exists())

    def test_plan_gate_requires_confirmed_model_uncertainty(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            project_root = Path(value)
            self.create_project_root(project_root)
            run_dir = TOOLS.init_batch_run(project_root, "risk-probe", "产品>模块>页面", "BATCH-001")

            discovery_path = run_dir / "page-discovery.csv"
            with discovery_path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                headers = reader.fieldnames or []
            discovery = {header: "" for header in headers}
            discovery.update(
                {
                    "批次ID": "BATCH-001",
                    "最小标题路径": "模块>页面",
                    "页面/入口": "风险页面",
                    "元素名称/文案": "危险操作按钮",
                    "元素类型": "按钮",
                    "交互方式": "点击",
                    "完整点击路径": "系统>模块>页面>危险操作按钮",
                    "预期/观察行为": "打开确认弹窗",
                }
            )
            with discovery_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=headers)
                writer.writeheader()
                writer.writerow(discovery)

            with self.assertRaisesRegex(ValueError, "pending user confirmation"):
                TOOLS.validate_batch_artifacts(run_dir, "plan")

            risk_path = run_dir / "risk-confirmation.csv"
            with risk_path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                risk_headers = reader.fieldnames or []
            risk = {header: "" for header in risk_headers}
            risk.update(
                {
                    "批次ID": "BATCH-001",
                    "风险ID": "RISK-001",
                    "模型不理解内容/待确认问题": "确认后是否触发异步审批",
                    "已完成深探依据": "已完成确认、取消和关闭路径，页面未展示审批规则",
                    "用户确认结论": "确认后触发异步审批",
                    "处置策略": "按异步审批设计用例",
                    "是否阻塞用例设计": "否",
                    "关联页面/入口": "风险页面",
                    "关联元素名称/文案": "危险操作按钮",
                    "确认状态": "待确认",
                }
            )
            with risk_path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=risk_headers)
                writer.writeheader()
                writer.writerow(risk)
            with self.assertRaisesRegex(ValueError, "确认状态 must be 已确认"):
                TOOLS.validate_batch_artifacts(run_dir, "plan")

    def test_delivery_rollback_restores_existing_and_removes_new_files(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            existing = root / "existing.txt"
            created = root / "created.txt"
            existing.write_text("before", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "probe failure"):
                with TOOLS.rollback_files_on_error([existing, created]):
                    existing.write_text("after", encoding="utf-8")
                    created.write_text("new", encoding="utf-8")
                    raise RuntimeError("probe failure")

            self.assertEqual("before", existing.read_text(encoding="utf-8"))
            self.assertFalse(created.exists())

    def test_delivery_lock_rejects_concurrent_writer(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            lock_path = Path(value) / "delivery.lock"
            with TOOLS.exclusive_process_lock(lock_path):
                with self.assertRaisesRegex(RuntimeError, "Another delivery process"):
                    with TOOLS.exclusive_process_lock(lock_path):
                        self.fail("Concurrent delivery should never acquire the same lock")
            with TOOLS.exclusive_process_lock(lock_path):
                self.assertTrue(lock_path.exists())

    def test_complete_delivery_rolls_back_when_prevalidation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            project_root = Path(value)
            formal = project_root / "working" / "formal.xlsx"
            import_template = project_root / "working" / "import-template.xlsx"
            formal.parent.mkdir(parents=True)
            shutil.copy2(REPO_ROOT / "docs" / "test-design" / "codebuddy-test-design-template.xlsx", formal)
            shutil.copy2(REPO_ROOT / "docs" / "test-design" / "测试用例模板.xlsx", import_template)
            before = formal.read_bytes()

            with mock.patch.object(TOOLS, "run_python_script", side_effect=RuntimeError("prevalidation failed")):
                with self.assertRaisesRegex(RuntimeError, "prevalidation failed"):
                    TOOLS.complete_deliverables(
                        project_root,
                        formal,
                        import_template,
                        "模块>页面",
                    )

            self.assertEqual(before, formal.read_bytes())
            self.assertFalse((project_root / "docs" / "test-assets" / "imports" / "模块_页面_导入用例.xlsx").exists())
            self.assertFalse((project_root / "docs" / "test-assets" / "modules" / "模块_页面_测试设计.xlsx").exists())
            lock_path = project_root / ".test-design-locks" / "delivery.lock"
            with TOOLS.exclusive_process_lock(lock_path):
                self.assertTrue(lock_path.exists())

    def test_product_map_sync_uses_current_schema_and_real_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            formal = root / "formal.xlsx"
            product_map = root / "product-map.xlsx"
            discovery = root / "page-discovery.csv"
            shutil.copy2(REPO_ROOT / "docs" / "test-design" / "codebuddy-test-design-template.xlsx", formal)
            shutil.copy2(REPO_ROOT / "docs" / "test-assets" / "product-map.xlsx", product_map)

            workbook = load_workbook(formal)
            function_sheet = workbook["功能测试用例"]
            function_headers = TOOLS.header_map(function_sheet)
            TOOLS.write_mapped_row(
                function_sheet,
                function_headers,
                2,
                {
                    "用例 ID": "TC-SYNC-001",
                    "模块": "模块>页面",
                    "功能点": "查询",
                    "用例标题": "查询-正常查询",
                    "测试类型": "功能测试",
                    "DFX维度": "DFX功能",
                    "DFX场景": "正常处理",
                    "测试数据": "CODEX_TEST_001",
                },
            )
            requirement_sheet = workbook["需求用户故事拆解"]
            TOOLS.write_mapped_row(
                requirement_sheet,
                TOOLS.header_map(requirement_sheet),
                2,
                {"Story ID/需求 ID": "REQ-001", "依赖系统": "依赖系统A"},
            )
            performance_sheet = workbook["性能测试设计"]
            TOOLS.write_mapped_row(
                performance_sheet,
                TOOLS.header_map(performance_sheet),
                2,
                {"性能场景 ID": "PERF-001", "业务链路": "查询链路"},
            )
            workbook.save(formal)

            template = REPO_ROOT / "docs" / "test-assets" / "batch-runs" / "templates" / "page-discovery-template.csv"
            with template.open("r", encoding="utf-8-sig", newline="") as fp:
                headers = next(csv.reader(fp))
            row = {header: "" for header in headers}
            row.update(
                {
                    "批次ID": "BATCH-001",
                    "最小标题路径": "模块>页面",
                    "页面/入口": "查询页面",
                    "元素名称/文案": "查询",
                    "元素类型": "按钮",
                    "覆盖状态": "已覆盖",
                    "关联用例ID": "TC-SYNC-001",
                }
            )
            with discovery.open("w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.DictWriter(fp, fieldnames=headers)
                writer.writeheader()
                writer.writerow(row)

            TOOLS.sync_product_map(
                product_map,
                formal,
                discovery,
                "产品>模块>页面",
                "docs/test-assets/modules/模块_页面_测试设计.xlsx",
            )
            synced = load_workbook(product_map, data_only=True)
            dependencies = TOOLS.non_empty_rows(
                synced["跨模块依赖关系"], TOOLS.header_map(synced["跨模块依赖关系"])
            )
            reusable_data = TOOLS.non_empty_rows(
                synced["可复用测试数据"], TOOLS.header_map(synced["可复用测试数据"])
            )
            impacts = TOOLS.non_empty_rows(
                synced["变更影响分析"], TOOLS.header_map(synced["变更影响分析"])
            )
            changes = TOOLS.non_empty_rows(synced["变更记录"], TOOLS.header_map(synced["变更记录"]))
            self.assertEqual("依赖系统A", dependencies[-1]["依赖模块"])
            self.assertEqual("CODEX_TEST_001", reusable_data[-1]["数据用途"])
            self.assertTrue(impacts[-1]["变更ID"].startswith("CHANGE-"))
            self.assertTrue(changes[-1]["日期"])
            catalog = root / "catalog"
            self.assertTrue((catalog / "index.json").exists())
            module_documents = list((catalog / "modules").glob("*.json"))
            documents = [json.loads(path.read_text(encoding="utf-8")) for path in module_documents]
            matching_documents = [
                item for item in documents if item["module_key"] == "产品>模块>页面"
            ]
            self.assertEqual(1, len(matching_documents))
            document = matching_documents[0]
            self.assertEqual("2.0.0", document["schema_version"])
            self.assertEqual("产品>模块>页面", document["module_key"])
            self.assertEqual("依赖系统A", document["facts"]["跨模块依赖关系"][0]["data"]["依赖模块"])
            for sheet in synced.worksheets:
                for row_index in range(2, sheet.max_row + 1):
                    combined = "".join("" if cell.value is None else str(cell.value) for cell in sheet[row_index])
                    self.assertNotIn("示例", combined, f"{sheet.title} still contains a sample row")
                    self.assertNotIn("FLOW-DEMO-001", combined, f"{sheet.title} still contains a demo row")

            TOOLS.sync_product_map(
                product_map,
                formal,
                discovery,
                "产品>模块>页面",
                "docs/test-assets/modules/模块_页面_测试设计.xlsx",
            )
            repeated_documents = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (catalog / "modules").glob("*.json")
            ]
            self.assertEqual(
                1,
                len([item for item in repeated_documents if item["module_key"] == "产品>模块>页面"]),
            )
            rebuilt = load_workbook(product_map, data_only=True)
            rebuilt_dependencies = TOOLS.non_empty_rows(
                rebuilt["跨模块依赖关系"], TOOLS.header_map(rebuilt["跨模块依赖关系"])
            )
            self.assertEqual(
                1,
                len([item for item in rebuilt_dependencies if item["当前模块"] == "产品>模块>页面"]),
            )

    def test_product_fact_migration_preserves_existing_real_excel_rows(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            product_map = root / "product-map.xlsx"
            shutil.copy2(REPO_ROOT / "docs" / "test-assets" / "product-map.xlsx", product_map)
            workbook = load_workbook(product_map)
            sheet = workbook["产品模块地图"]
            headers = TOOLS.header_map(sheet)
            for cell in sheet[2]:
                cell.value = None
            TOOLS.write_mapped_row(
                sheet,
                headers,
                2,
                {
                    "产品/系统": "真实产品",
                    "一级模块": "资产模块",
                    "页面/入口": "资产页面",
                    "菜单路径/URL": "资产模块>资产页面",
                    "模块功能摘要": "迁移前已存在的真实资产",
                    "归档测试设计路径": "docs/test-assets/modules/资产模块_测试设计.xlsx",
                    "覆盖状态": "已覆盖",
                    "最后更新时间": "2026-07-10",
                },
            )
            workbook.save(product_map)

            TOOLS.ensure_catalog(product_map)
            TOOLS.rebuild_index(product_map)
            TOOLS.project_catalog_to_workbook(product_map)

            legacy_path = root / "catalog" / "modules" / "_legacy.json"
            self.assertTrue(legacy_path.exists())
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            migrated_rows = legacy["facts"]["产品模块地图"]
            self.assertEqual("真实产品", migrated_rows[0]["data"]["产品/系统"])
            projected = load_workbook(product_map, data_only=True)
            projected_rows = TOOLS.non_empty_rows(
                projected["产品模块地图"], TOOLS.header_map(projected["产品模块地图"])
            )
            self.assertEqual("真实产品", projected_rows[0]["产品/系统"])

    @unittest.skipIf(os.name != "nt", "PowerShell upgrade rollback integration runs on Windows")
    @unittest.skipIf(os.environ.get("TEST_DESIGN_SKIP_UPGRADE_INTEGRATION") == "1", "Avoid recursive upgrade test")
    def test_upgrade_failure_restores_framework_and_protected_assets(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            sandbox = root / "repo"
            ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "dist", "dist-test", ".upgrade-backups", ".test-design-locks")
            shutil.copytree(REPO_ROOT, sandbox, ignore=ignore)
            environment = os.environ.copy()
            environment["TEST_DESIGN_SKIP_UPGRADE_INTEGRATION"] = "1"

            package_result = subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(sandbox / "scripts" / "new-framework-upgrade-package.ps1"),
                    "-OutputDir",
                    "dist-test",
                ],
                cwd=sandbox,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, package_result.returncode, package_result.stderr)
            valid_package = sandbox / "dist-test" / "framework-upgrade-2.0.0.zip"
            extracted = root / "broken-package"
            with zipfile.ZipFile(valid_package) as archive:
                archive.extractall(extracted)
            (extracted / "README.md").write_text("BROKEN UPGRADE PACKAGE\n", encoding="utf-8")
            probe_file = extracted / "tests" / "new-file-probe.txt"
            probe_file.parent.mkdir(parents=True, exist_ok=True)
            probe_file.write_text("must be removed by rollback", encoding="utf-8")
            broken_package = root / "broken-upgrade.zip"
            with zipfile.ZipFile(broken_package, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in extracted.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(extracted))

            readme_before = (sandbox / "README.md").read_bytes()
            product_map_before = (sandbox / "docs" / "test-assets" / "product-map.xlsx").read_bytes()
            upgrade_result = subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(sandbox / "scripts" / "upgrade-framework.ps1"),
                    "-PackagePath",
                    str(broken_package),
                ],
                cwd=sandbox,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, upgrade_result.returncode)
            self.assertIn("restoring framework and protected assets", upgrade_result.stderr + upgrade_result.stdout)
            self.assertEqual(readme_before, (sandbox / "README.md").read_bytes())
            self.assertEqual(product_map_before, (sandbox / "docs" / "test-assets" / "product-map.xlsx").read_bytes())
            self.assertFalse((sandbox / "tests" / "new-file-probe.txt").exists())

    @unittest.skipIf(os.name != "nt", "PowerShell asset migration integration runs on Windows")
    @unittest.skipIf(os.environ.get("TEST_DESIGN_SKIP_UPGRADE_INTEGRATION") == "1", "Avoid recursive upgrade test")
    def test_upgrade_migrates_asset_schema_1_to_2_without_losing_excel_facts(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            sandbox = root / "repo"
            ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "dist", "dist-test", ".upgrade-backups", ".test-design-locks")
            shutil.copytree(REPO_ROOT, sandbox, ignore=ignore)
            environment = os.environ.copy()
            environment["TEST_DESIGN_SKIP_UPGRADE_INTEGRATION"] = "1"

            package_result = subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(sandbox / "scripts" / "new-framework-upgrade-package.ps1"),
                    "-OutputDir",
                    "dist-test",
                ],
                cwd=sandbox,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, package_result.returncode, package_result.stderr)
            package = sandbox / "dist-test" / "framework-upgrade-2.0.0.zip"

            (sandbox / "VERSION").write_text(
                "framework_version=1.2.0\nasset_schema_version=1.0.0\n",
                encoding="utf-8",
            )
            product_map = sandbox / "docs" / "test-assets" / "product-map.xlsx"
            workbook = load_workbook(product_map)
            sheet = workbook["产品模块地图"]
            for cell in sheet[2]:
                cell.value = None
            TOOLS.write_mapped_row(
                sheet,
                TOOLS.header_map(sheet),
                2,
                {
                    "产品/系统": "迁移产品",
                    "一级模块": "迁移模块",
                    "页面/入口": "迁移页面",
                    "菜单路径/URL": "迁移模块>迁移页面",
                    "模块功能摘要": "升级前真实事实",
                    "归档测试设计路径": "docs/test-assets/modules/迁移模块_测试设计.xlsx",
                    "覆盖状态": "已覆盖",
                    "最后更新时间": "2026-07-10",
                },
            )
            workbook.save(product_map)

            upgrade_result = subprocess.run(
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(sandbox / "scripts" / "upgrade-framework.ps1"),
                    "-PackagePath",
                    str(package),
                    "-RunMigrations",
                ],
                cwd=sandbox,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, upgrade_result.returncode, upgrade_result.stderr + upgrade_result.stdout)
            self.assertIn("asset_schema_version=2.0.0", (sandbox / "VERSION").read_text(encoding="utf-8-sig"))
            legacy_path = sandbox / "docs" / "test-assets" / "catalog" / "modules" / "_legacy.json"
            self.assertTrue(legacy_path.exists())
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            migrated = legacy["facts"]["产品模块地图"]
            self.assertEqual("迁移产品", migrated[0]["data"]["产品/系统"])


if __name__ == "__main__":
    unittest.main()
