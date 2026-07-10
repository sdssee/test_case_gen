# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "test_design_excel_tools.py"
SPEC = importlib.util.spec_from_file_location("test_design_excel_tools", MODULE_PATH)
assert SPEC and SPEC.loader
TOOLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOLS)


class ArchitectureSafetyTests(unittest.TestCase):
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
            TOOLS.init_batch_run(project_root, "probe", "产品>模块>页面", "BATCH-001", resume=True)

            with status_path.open("r", encoding="utf-8-sig", newline="") as fp:
                resumed = next(csv.DictReader(fp))
            self.assertEqual("执行中", resumed["状态"])
            self.assertEqual("3", resumed["页面数"])

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
            for sheet in synced.worksheets:
                for row_index in range(2, sheet.max_row + 1):
                    combined = "".join("" if cell.value is None else str(cell.value) for cell in sheet[row_index])
                    self.assertNotIn("示例", combined, f"{sheet.title} still contains a sample row")
                    self.assertNotIn("FLOW-DEMO-001", combined, f"{sheet.title} still contains a demo row")


if __name__ == "__main__":
    unittest.main()
