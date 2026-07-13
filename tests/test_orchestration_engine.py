# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from tests import test_architecture_safety as architecture_safety
except ModuleNotFoundError:  # unittest discovery may place tests/ directly on sys.path.
    import test_architecture_safety as architecture_safety

from test_design.orchestration.engine import (
    DFX_MATRIX,
    OrchestrationError,
    advance_orchestration,
    initialize_orchestration,
    orchestration_status,
    resume_external_block,
    submit_agent_result,
    _validate_risk_candidates_file,
)
from test_design.orchestration.review import (
    REQUIRED_REVIEW_CHECKS,
    ReviewValidationError,
    _validate_report,
    validate_review_artifacts,
)


class OrchestrationEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = architecture_safety.ArchitectureSafetyTests(methodName="runTest")

    def _new_run(self, root: Path, run_id: str = "agent-engine") -> Path:
        self.helper.create_project_root(root)
        return architecture_safety.TOOLS.init_batch_run(
            root,
            run_id,
            "产品>模块>页面",
            "BATCH-001",
            "产品",
        )

    @staticmethod
    def _result(task: dict[str, object], status: str, error: str | None = None) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "task_id": task["task_id"],
            "agent_role": task["agent_role"],
            "status": status,
            "source_fingerprint": task["source_fingerprint"],
            "produced_files": [],
            "affected_interaction_ids": [],
            "affected_case_ids": [],
            "facts_used": [],
            "gate_summary": {},
            "rework_requests": [],
            "error_message": error,
        }

    @staticmethod
    def _write_result(run_dir: Path, name: str, value: dict[str, object]) -> Path:
        path = run_dir / "orchestration" / f"{name}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _success_result(task: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "task_id": task["task_id"],
            "agent_role": task["agent_role"],
            "status": "SUCCEEDED",
            "source_fingerprint": task["source_fingerprint"],
            "produced_files": list(task["allowed_output_files"]),
            "affected_interaction_ids": [],
            "affected_case_ids": [],
            "facts_used": list(task["input_files"]),
            "gate_summary": {str(task["required_gate"]): True},
            "rework_requests": [],
            "error_message": None,
        }

    @staticmethod
    def _output_dir(run_dir: Path, task: dict[str, object]) -> Path:
        return (
            run_dir
            / "artifacts"
            / "agent-work"
            / str(task["agent_role"])
            / str(task["task_id"])
            / "output"
        )

    @staticmethod
    def _write_binary_audit(evidence: Path, notes: str = "已逐图核对并确认可见信息完成脱敏") -> Path:
        audit = evidence.with_name(evidence.name + ".sensitive-audit.json")
        audit.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    "inspection_method": "model_visual_review",
                    "visible_text": "<no_visible_text>",
                    "address_bar_cropped_or_masked": True,
                    "environment_identifiers_masked": True,
                    "credentials_masked": True,
                    "status": "PASSED",
                    "notes": notes,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return audit

    def test_init_creates_final_required_architecture_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value))
            first = initialize_orchestration(run_dir)
            second = initialize_orchestration(run_dir)
            self.assertEqual(first["created_at"], second["created_at"])
            self.assertEqual("multi-agent-final", second["architecture"])
            self.assertEqual("required", second["agent_mode"])
            self.assertEqual("INIT", second["state_machine"]["state"])
            for relative in (
                "orchestration/config.json",
                "orchestration/run-manifest.json",
                "orchestration/state.json",
                "orchestration/events.jsonl",
                "orchestration/tasks",
                "orchestration/results",
                "orchestration/rework-requests",
                "artifacts/agent-work",
            ):
                self.assertTrue((run_dir / relative).exists(), relative)

    def test_valid_manual_ledgers_cannot_bypass_required_discovery_agent(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            self.helper.create_project_root(root)
            run_dir = self.helper.make_valid_plan_run(root, "required-discovery")
            status = advance_orchestration(run_dir)
            self.assertEqual("DISCOVERY_RUNNING", status["state"])
            self.assertEqual([], status["validated_phases"])
            self.assertEqual(1, len(status["runnable_tasks"]))
            task = status["runnable_tasks"][0]
            self.assertEqual("discovery", task["agent_role"])
            self.assertFalse(task["allowed_output_prefixes"] == [])
            self.assertTrue((run_dir / "orchestration/inputs" / task["task_id"]).is_dir())

    def test_retryable_failure_creates_next_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "retry-agent")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            result_path = self._write_result(
                run_dir,
                "failed-result",
                self._result(task, "FAILED", "temporary model failure"),
            )
            status = submit_agent_result(run_dir, str(task["task_id"]), result_path)
            self.assertEqual("DISCOVERY_RUNNING", status["state"])
            self.assertEqual(1, status["task_counts"]["FAILED"])
            self.assertEqual("TASK-DISCOVERY-A02", status["runnable_tasks"][0]["task_id"])

    def test_sensitive_agent_result_is_rejected_before_audit_storage(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "sensitive-agent-result")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            result_path = self._write_result(
                run_dir,
                "sensitive-result",
                self._result(task, "FAILED", "Bearer RealAgentResultToken_12345"),
            )
            with self.assertRaisesRegex(OrchestrationError, "possible unmasked secret"):
                submit_agent_result(run_dir, str(task["task_id"]), result_path)
            self.assertFalse(
                (run_dir / "orchestration" / "results" / f"{task['task_id']}.json").exists()
            )

    def test_binary_agent_output_requires_visual_privacy_audit_before_result_storage(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "binary-agent-result")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            output_dir = self._output_dir(run_dir, task)
            screenshot = output_dir / "screenshots" / "safe-state.png"
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"sanitized-image-pixels")
            relative = screenshot.relative_to(run_dir).as_posix()
            result = self._result(task, "FAILED", "binary evidence gate probe")
            result["produced_files"] = [relative]
            result_path = self._write_result(run_dir, "binary-result", result)

            with self.assertRaisesRegex(
                OrchestrationError,
                "requires adjacent visual privacy audit",
            ):
                submit_agent_result(run_dir, str(task["task_id"]), result_path)
            self.assertFalse(
                (run_dir / "orchestration" / "results" / f"{task['task_id']}.json").exists()
            )

    def test_review_report_requires_explicit_binary_privacy_check(self) -> None:
        checks = {name: True for name in REQUIRED_REVIEW_CHECKS}
        checks.pop("binary_evidence_privacy_verified")
        report = {
            "schema_version": "1.0.0",
            "generation_session_id": "SESSION-001",
            "generation_source_fingerprint": "0" * 64,
            "review_source_fingerprint": "1" * 64,
            "review_task_id": "TASK-REVIEW-A01",
            "reviewer_role": "reviewer",
            "verdict": "APPROVED",
            "generator_task_ids": ["TASK-CASE-A01"],
            "checks": checks,
            "issues": [],
        }
        with self.assertRaisesRegex(ReviewValidationError, "binary_evidence_privacy_verified"):
            _validate_report(report)

    def test_stale_task_source_is_rejected_before_result_is_stored(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "stale-agent")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            scope_path = run_dir / "batch-scope.json"
            scope = json.loads(scope_path.read_text(encoding="utf-8"))
            scope_path.write_text(json.dumps(scope, ensure_ascii=False, indent=4), encoding="utf-8")
            result_path = self._write_result(
                run_dir,
                "stale-result",
                self._result(task, "FAILED", "should not be accepted"),
            )
            with self.assertRaisesRegex(OrchestrationError, "source changed"):
                submit_agent_result(run_dir, str(task["task_id"]), result_path)
            self.assertEqual(1, orchestration_status(run_dir)["task_counts"].get("PENDING"))
            self.assertFalse((run_dir / "orchestration/results" / f"{task['task_id']}.json").exists())
            refreshed = advance_orchestration(run_dir)
            self.assertEqual(1, refreshed["task_counts"].get("INVALIDATED"))
            self.assertEqual("TASK-DISCOVERY-A02", refreshed["runnable_tasks"][0]["task_id"])

    def test_new_catalog_source_invalidates_dispatched_discovery_task(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "late-catalog-source")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            catalog_module = root / "docs/test-assets/catalog/modules/late-module.json"
            catalog_module.parent.mkdir(parents=True, exist_ok=True)
            catalog_module.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "product": "产品",
                        "module_key": "产品>模块>页面",
                        "module_path": "模块>页面",
                        "facts": {"新增事实": "任务派发后新增，旧任务未读取"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result_path = self._write_result(
                run_dir,
                "late-catalog-result",
                self._result(task, "FAILED", "stale discovery must not be accepted"),
            )
            with self.assertRaisesRegex(OrchestrationError, "source changed"):
                submit_agent_result(run_dir, str(task["task_id"]), result_path)
            self.assertFalse((run_dir / "orchestration/results" / f"{task['task_id']}.json").exists())

            refreshed = advance_orchestration(run_dir)
            self.assertEqual(1, refreshed["task_counts"].get("INVALIDATED"))
            self.assertEqual("TASK-DISCOVERY-A02", refreshed["runnable_tasks"][0]["task_id"])
            replacement_inputs = refreshed["runnable_tasks"][0]["input_files"]
            self.assertTrue(any(path.endswith("late-module.json") for path in replacement_inputs))

    def test_new_catalog_source_rewinds_validated_discovery_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            self.helper.create_project_root(root)
            run_dir = self.helper.make_valid_plan_run(root, "late-catalog-prefix")
            discovery = advance_orchestration(run_dir)["runnable_tasks"][0]
            discovery_output = self._output_dir(run_dir, discovery)
            for name in (
                "page-element-inventory.csv",
                "page-discovery.csv",
                "selection-option-observations.csv",
                "interaction-branch-observations.csv",
                "test-data-lifecycle.csv",
            ):
                shutil.copy2(run_dir / name, discovery_output / name)
            status = submit_agent_result(
                run_dir,
                str(discovery["task_id"]),
                self._write_result(run_dir, "discovery-before-late-catalog", self._success_result(discovery)),
            )
            self.assertEqual("PLAN_RUNNING", status["state"])

            catalog_module = root / "docs/test-assets/catalog/modules/late-module.json"
            catalog_module.parent.mkdir(parents=True, exist_ok=True)
            catalog_module.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "product": "产品",
                        "module_key": "产品>模块>页面",
                        "module_path": "模块>页面",
                        "facts": {"新增事实": "Discovery 通过后新增，必须回退重探"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rewound = advance_orchestration(run_dir)
            self.assertEqual("DISCOVERY_RUNNING", rewound["state"])
            self.assertEqual([], rewound["validated_phases"])
            self.assertEqual("discovery", rewound["runnable_tasks"][0]["agent_role"])
            self.assertTrue(
                any(path.endswith("late-module.json") for path in rewound["runnable_tasks"][0]["input_files"])
            )

    def test_external_block_resume_creates_fresh_task(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "external-agent")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            result_path = self._write_result(
                run_dir,
                "blocked-result",
                self._result(task, "EXTERNAL_BLOCKED", "login service unavailable"),
            )
            blocked = submit_agent_result(run_dir, str(task["task_id"]), result_path)
            self.assertEqual("EXTERNAL_BLOCKED", blocked["state"])
            resumed = resume_external_block(run_dir)
            self.assertEqual("DISCOVERY_RUNNING", resumed["state"])
            self.assertEqual("TASK-DISCOVERY-A02", resumed["runnable_tasks"][0]["task_id"])

    def test_invalid_rework_is_rejected_without_partial_audit_state(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "invalid-rework")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            request_id = "RW-FORWARD-001"
            result = self._result(task, "NEEDS_REWORK")
            result["rework_requests"] = [
                {
                    "schema_version": "1.0.0",
                    "request_id": request_id,
                    "run_id": "invalid-rework",
                    "batch_id": "BATCH-001",
                    "target_phase": "delivery",
                    "target_task_id": None,
                    "reason_code": "DELIVERY_MISMATCH",
                    "affected_ids": ["DELIVERY-001"],
                    "evidence": [],
                    "required_action": "不得从 discovery 向尚未到达的 delivery 返工",
                    "source_fingerprint": task["source_fingerprint"],
                    "attempt": 1,
                }
            ]
            result_path = self._write_result(run_dir, "invalid-rework-result", result)
            with self.assertRaisesRegex(ValueError, "forward rework"):
                submit_agent_result(run_dir, str(task["task_id"]), result_path)
            self.assertFalse((run_dir / "orchestration/results" / f"{task['task_id']}.json").exists())
            self.assertFalse((run_dir / "orchestration/rework-requests" / f"{request_id}.json").exists())
            self.assertEqual("PENDING", json.loads((run_dir / "orchestration/run-manifest.json").read_text(encoding="utf-8"))["tasks"][task["task_id"]]["status"])

    def test_page_verifiable_risk_candidate_cannot_advance_to_risk_agent(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            path = Path(value) / "risk-candidates.json"
            path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {
                                "risk_id": "RISK-PAGE-001",
                                "question": "点击后状态如何变化",
                                "page_verifiability": "page_verifiable",
                                "page_action": "点击控件",
                                "page_result": "尚未点击",
                                "external_reason": "",
                                "affected_interaction_ids": ["INT-001"],
                                "evidence": ["artifacts/screenshots/probe.png"],
                                "dfx_dimensions": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(OrchestrationError, "must return to discovery"):
                _validate_risk_candidates_file(path)

    def test_pipeline_status_json_is_one_machine_readable_document(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "clean-json")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(architecture_safety.REPO_ROOT / "scripts/test_design_excel_tools.py"),
                    "pipeline-status",
                    "--run-dir",
                    str(run_dir),
                    "--json",
                ],
                cwd=architecture_safety.REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual("DISCOVERY_REQUIRED", payload["state"])
            self.assertNotIn("OK:", completed.stdout)

    def test_complete_agent_chain_reaches_delivery_only_after_independent_review(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            self.helper.create_project_root(root)
            product_map = root / "docs" / "test-assets" / "product-map.xlsx"
            product_map.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                architecture_safety.REPO_ROOT / "docs" / "test-assets" / "product-map.xlsx",
                product_map,
            )
            product_map_before = hashlib.sha256(product_map.read_bytes()).hexdigest()
            run_dir = self.helper.make_valid_plan_run(root, "full-agent-chain")
            unreferenced_binary = run_dir / "artifacts" / "screenshots" / "unreferenced-state.png"
            unreferenced_binary.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"sanitized-unreferenced-image-pixels"
            )
            unreferenced_audit = self._write_binary_audit(unreferenced_binary)
            original_audit = unreferenced_audit.read_bytes()
            module_path = "产品>模块>子模块>页面"
            leaf_path = "模块>子模块>页面"
            scope_path = run_dir / "batch-scope.json"
            scope = json.loads(scope_path.read_text(encoding="utf-8"))
            scope.update({"module_path": leaf_path, "requested_module_path": module_path})
            scope_path.write_text(
                json.dumps(scope, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for name in ("batch-plan.md", "batch-review.md"):
                path = run_dir / name
                path.write_text(
                    path.read_text(encoding="utf-8").replace("模块>页面", leaf_path),
                    encoding="utf-8",
                )
            scoped_ledgers = [
                "batch-status.csv",
                "page-element-inventory.csv",
                "page-discovery.csv",
                "selection-option-observations.csv",
                "interaction-branch-observations.csv",
                "element-case-plan.csv",
                "test-data-lifecycle.csv",
                "risk-confirmation.csv",
            ]
            for name in scoped_ledgers:
                path = run_dir / name
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    rows = list(csv.DictReader(stream))
                for row in rows:
                    if "最小标题路径" in row:
                        row["最小标题路径"] = leaf_path
                if name == "batch-status.csv":
                    rows[0].update(
                        {
                            "一级模块": "模块",
                            "二级菜单": "子模块",
                            "三级菜单/页面域": "页面",
                            "批次范围": leaf_path,
                        }
                    )
                elif name == "element-case-plan.csv":
                    rows[0].update(
                        {
                            "应生成用例数": "5",
                            "计划用例ID": ",".join(
                                f"TC-RISK-{index:03d}" for index in range(1, 6)
                            ),
                        }
                    )
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    headers = list(next(csv.reader(stream)))
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=headers)
                    writer.writeheader()
                    writer.writerows(rows)

            discovery = advance_orchestration(run_dir)["runnable_tasks"][0]
            discovery_output = self._output_dir(run_dir, discovery)
            for name in (
                "page-element-inventory.csv",
                "page-discovery.csv",
                "selection-option-observations.csv",
                "interaction-branch-observations.csv",
                "test-data-lifecycle.csv",
            ):
                shutil.copy2(run_dir / name, discovery_output / name)
            status = submit_agent_result(
                run_dir,
                str(discovery["task_id"]),
                self._write_result(run_dir, "discovery-success", self._success_result(discovery)),
            )
            self.assertEqual("PLAN_RUNNING", status["state"])

            plan = status["runnable_tasks"][0]
            plan_output = self._output_dir(run_dir, plan)
            for name in (
                "element-case-plan.csv",
                "selection-option-observations.csv",
                "interaction-branch-observations.csv",
                "test-data-lifecycle.csv",
            ):
                shutil.copy2(run_dir / name, plan_output / name)
            self.helper.write_valid_sheet_files(plan_output)
            scenarios_path = plan_output / "scenarios.json"
            scenarios_rows = json.loads(scenarios_path.read_text(encoding="utf-8"))
            scenarios_rows[0]["DFX维度"] = ",".join(DFX_MATRIX)
            scenarios_rows[0]["DFX场景"] = ",".join(
                scenario for values in DFX_MATRIX.values() for scenario in values
            )
            scenarios_rows[0]["是否生成用例"] = "否"
            scenarios_path.write_text(
                json.dumps(scenarios_rows, ensure_ascii=False), encoding="utf-8"
            )
            performance_path = plan_output / "performance.json"
            performance_rows = json.loads(performance_path.read_text(encoding="utf-8"))
            performance_rows[0].update(
                {
                    "性能场景 ID": "PERF-NA-001",
                    "Story ID/需求 ID": "REQ-001",
                    "业务链路": "模块>页面>危险操作",
                    "性能测试类型": "本轮不适用",
                    "DFX维度": "DFP性能",
                    "DFX场景": "响应时间",
                    "是否纳入本轮测试": "否",
                }
            )
            performance_path.write_text(
                json.dumps(performance_rows, ensure_ascii=False), encoding="utf-8"
            )
            risks_path = plan_output / "risks.json"
            risks_rows = json.loads(risks_path.read_text(encoding="utf-8"))
            risks_rows[0].update(
                {
                    "编号": "RISK-001",
                    "类型": "测试风险",
                    "关联DFX维度": "DFT功能",
                    "关联DFX场景": "正向流程",
                    "描述": "危险操作弹窗需保持关闭路径可用",
                    "影响范围": "模块>页面",
                    "风险等级": "低",
                    "建议处理方式": "按已实探结果执行五条功能用例",
                    "负责人": "测试负责人",
                    "状态": "已关闭",
                }
            )
            risks_path.write_text(
                json.dumps(risks_rows, ensure_ascii=False), encoding="utf-8"
            )
            page_elements_path = plan_output / "page_elements.json"
            page_element_rows = json.loads(page_elements_path.read_text(encoding="utf-8"))
            page_element_rows[0].update(
                {
                    "元素 ID": "EL-RISK-001",
                    "Story ID/需求 ID": "REQ-001",
                    "页面/入口": "风险页面",
                    "页面 URL/菜单路径": "系统>模块>页面",
                    "元素名称/文案": "危险操作按钮",
                    "元素类型": "按钮",
                    "交互方式": "点击",
                    "适用DFX维度": "DFT功能",
                    "适用DFX场景": "正向流程",
                    "前置状态/权限": "测试账号已登录",
                    "预期行为": "打开确认弹窗并可安全关闭",
                    "业务依据/规则来源": "本轮页面实探证据",
                    "覆盖用例 ID": ",".join(
                        f"TC-RISK-{index:03d}" for index in range(1, 6)
                    ),
                    "覆盖状态": "已覆盖",
                    "发现方式": "页面实探",
                    "素材来源": "artifacts/screenshots/danger-action.txt",
                    "待确认问题/备注": "无",
                }
            )
            page_elements_path.write_text(
                json.dumps(page_element_rows, ensure_ascii=False), encoding="utf-8"
            )
            (plan_output / "risk-candidates.json").write_text(
                json.dumps({"candidates": []}, ensure_ascii=False), encoding="utf-8"
            )
            (plan_output / "dfx-assessment.json").write_text(
                json.dumps(
                    {
                        "dimensions": [
                            {
                                "dimension": dimension,
                                "status": "适用",
                                "reason": "已按页面事实完成四场景评估",
                                "scenarios": scenarios,
                            }
                            for dimension, scenarios in DFX_MATRIX.items()
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            status = submit_agent_result(
                run_dir,
                str(plan["task_id"]),
                self._write_result(run_dir, "plan-success", self._success_result(plan)),
            )
            self.assertEqual("CASES_RUNNING", status["state"])
            self.assertEqual(["discovery", "plan", "risk"], status["validated_phases"])

            worker = status["runnable_tasks"][0]
            worker_output = self._output_dir(run_dir, worker)
            context = json.loads(
                (
                    worker_output.parent
                    / "meta"
                    / "task-context.json"
                ).read_text(encoding="utf-8")
            )
            case_ids = list(context["planned_case_ids"])
            (worker_output / "function_cases.json").write_text(
                json.dumps([self.helper.function_case(case_id) for case_id in case_ids], ensure_ascii=False),
                encoding="utf-8",
            )
            (worker_output / "case-traceability.json").write_text(
                json.dumps(context["traceability_expectations"], ensure_ascii=False),
                encoding="utf-8",
            )
            worker_result = self._success_result(worker)
            worker_result["affected_case_ids"] = case_ids
            status = submit_agent_result(
                run_dir,
                str(worker["task_id"]),
                self._write_result(run_dir, "worker-success", worker_result),
            )
            self.assertEqual("REVIEW_RUNNING", status["state"])
            self.assertFalse((run_dir / "orchestration/review-report.json").exists())

            reviewer = status["runnable_tasks"][0]
            rework = self._result(reviewer, "NEEDS_REWORK")
            rework["rework_requests"] = [
                {
                    "schema_version": "1.0.0",
                    "request_id": "RW-CASES-001",
                    "run_id": "full-agent-chain",
                    "batch_id": "BATCH-001",
                    "target_phase": "cases",
                    "target_task_id": worker["task_id"],
                    "reason_code": "DUPLICATE_STEPS",
                    "affected_ids": [case_ids[0]],
                    "evidence": ["artifacts/screenshots/danger-action.txt"],
                    "required_action": "重写第一条用例的具体操作与预期结果并重新合并",
                    "source_fingerprint": reviewer["source_fingerprint"],
                    "attempt": reviewer["attempt"],
                }
            ]
            status = submit_agent_result(
                run_dir,
                str(reviewer["task_id"]),
                self._write_result(run_dir, "review-rework", rework),
            )
            self.assertEqual("CASES_RUNNING", status["state"])
            status = advance_orchestration(run_dir)
            replacement = status["runnable_tasks"][0]
            self.assertEqual("case_worker", replacement["agent_role"])
            self.assertNotEqual(worker["task_id"], replacement["task_id"])
            replacement_output = self._output_dir(run_dir, replacement)
            replacement_context = json.loads(
                (replacement_output.parent / "meta" / "task-context.json").read_text(encoding="utf-8")
            )
            replacement_cases = [self.helper.function_case(case_id) for case_id in case_ids]
            replacement_cases[0]["操作步骤"] += "\n5. 执行返工后的唯一确认动作"
            replacement_cases[0]["预期结果"] += "\n4. 页面显示返工后的唯一确认结果"
            (replacement_output / "function_cases.json").write_text(
                json.dumps(replacement_cases, ensure_ascii=False), encoding="utf-8"
            )
            (replacement_output / "case-traceability.json").write_text(
                json.dumps(replacement_context["traceability_expectations"], ensure_ascii=False),
                encoding="utf-8",
            )
            replacement_result = self._success_result(replacement)
            replacement_result["affected_case_ids"] = case_ids
            status = submit_agent_result(
                run_dir,
                str(replacement["task_id"]),
                self._write_result(run_dir, "worker-rework-success", replacement_result),
            )
            self.assertEqual("REVIEW_RUNNING", status["state"])
            formal_cases = json.loads(
                (run_dir / "artifacts/data/function_cases_part_001.json").read_text(encoding="utf-8")
            )
            self.assertIn("返工后的唯一确认动作", formal_cases[0]["操作步骤"])

            reviewer = status["runnable_tasks"][0]
            self.assertTrue(
                any(path.endswith("/artifacts/screenshots/unreferenced-state.png") for path in reviewer["input_files"])
            )
            self.assertTrue(
                any(
                    path.endswith(
                        "/artifacts/screenshots/unreferenced-state.png.sensitive-audit.json"
                    )
                    for path in reviewer["input_files"]
                )
            )
            reviewer_output = self._output_dir(run_dir, reviewer)
            reviewer_context = json.loads(
                (reviewer_output.parent / "meta" / "task-context.json").read_text(encoding="utf-8")
            )
            session = reviewer_context["generation_session"]
            report = {
                "schema_version": "1.0.0",
                "generation_session_id": session["generation_session_id"],
                "generation_source_fingerprint": session["source_fingerprint"],
                "review_source_fingerprint": reviewer_context["review_source_fingerprint"],
                "review_task_id": reviewer["task_id"],
                "reviewer_role": "reviewer",
                "verdict": "APPROVED",
                "generator_task_ids": reviewer_context["generator_task_ids"],
                "checks": {name: True for name in REQUIRED_REVIEW_CHECKS},
                "issues": [],
            }
            (reviewer_output / "review-report.json").write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )
            status = submit_agent_result(
                run_dir,
                str(reviewer["task_id"]),
                self._write_result(run_dir, "review-success", self._success_result(reviewer)),
            )
            self.assertEqual("DELIVERY_RUNNING", status["state"])
            self.assertEqual(
                ["discovery", "plan", "risk", "cases", "review"],
                status["validated_phases"],
            )
            self.assertTrue((run_dir / "orchestration/review-report.json").is_file())
            self.assertTrue(status["delivery_command"])
            changed_audit = json.loads(unreferenced_audit.read_text(encoding="utf-8"))
            changed_audit["notes"] = "已再次逐图核对并形成另一份合法审计记录"
            unreferenced_audit.write_text(
                json.dumps(changed_audit, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(ReviewValidationError, "review_source_fingerprint is stale"):
                validate_review_artifacts(run_dir)
            unreferenced_audit.write_bytes(original_audit)
            self.assertTrue(validate_review_artifacts(run_dir))
            with self.assertRaisesRegex(ValueError, "external --formal-workbook input is not allowed"):
                architecture_safety.TOOLS.complete_deliverables(
                    root,
                    architecture_safety.REPO_ROOT / "docs/test-design/codebuddy-test-design-template.xlsx",
                    architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                    module_path,
                    batch_status=run_dir / "batch-status.csv",
                    batch_id="BATCH-001",
                    product_name="产品",
                )

            working = root / "working"
            formal = working / "产品_模块_子模块_页面_测试设计.xlsx"
            imported = working / "产品_模块_子模块_页面_导入用例.xlsx"
            counts = architecture_safety.TOOLS.complete_deliverables(
                root,
                formal,
                architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                module_path,
                import_workbook=imported,
                batch_status=run_dir / "batch-status.csv",
                batch_id="BATCH-001",
                product_map=product_map,
                page_discovery=run_dir / "page-discovery.csv",
                product_name="产品",
                assembly_run_dir=run_dir,
                formal_template=architecture_safety.REPO_ROOT / "docs/test-design/codebuddy-test-design-template.xlsx",
            )
            self.assertEqual(5, counts["功能测试用例"])
            formal_book = architecture_safety.load_workbook(formal, data_only=True)
            self.assertEqual(
                [
                    "测试设计总览",
                    "需求用户故事拆解",
                    "测试场景矩阵",
                    "功能测试用例",
                    "性能测试设计",
                    "风险与待确认问题",
                    "自动化建议",
                    "页面元素覆盖清单",
                ],
                formal_book.sheetnames,
            )
            import_book = architecture_safety.load_workbook(imported, data_only=True)
            self.assertNotEqual(formal.resolve(), imported.resolve())
            self.assertNotIn("测试系统导入用例", formal_book.sheetnames)
            self.assertGreater(len(import_book.sheetnames), 0)

            _, formal_name, import_name = architecture_safety.TOOLS.deliverable_names(
                module_path, "产品"
            )
            published = [
                root / "docs/test-design/current" / formal_name,
                root / "docs/test-design/deliverables" / formal_name,
                root / "docs/test-design/deliverables" / import_name,
                root / "docs/test-assets/modules" / formal_name,
                root / "docs/test-assets/imports" / import_name,
            ]
            self.assertEqual(5, len(published))
            self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in published))
            formal_hash = hashlib.sha256(formal.read_bytes()).hexdigest()
            import_hash = hashlib.sha256(imported.read_bytes()).hexdigest()
            for path in published:
                expected = import_hash if path.name == import_name else formal_hash
                self.assertEqual(expected, hashlib.sha256(path.read_bytes()).hexdigest(), path)

            receipt_path = run_dir / "artifacts/data/delivery-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt_entries = {root / Path(entry["path"]): entry for entry in receipt["files"]}
            self.assertTrue(all(path in receipt_entries for path in published))
            for path, entry in receipt_entries.items():
                self.assertEqual(path.stat().st_size, entry["size"])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])
            self.assertEqual("docs/test-assets/product-map.xlsx", receipt["product_map_path"])
            self.assertNotEqual(product_map_before, hashlib.sha256(product_map.read_bytes()).hexdigest())
            catalog = product_map.parent / "catalog"
            self.assertTrue((catalog / "index.json").is_file())
            module_documents = [
                path for path in (catalog / "modules").glob("*.json") if path.name != "_legacy.json"
            ]
            self.assertEqual(1, len(module_documents))
            module_facts = module_documents[0].read_text(encoding="utf-8")
            self.assertTrue(all(case_id in module_facts for case_id in case_ids))

            final_status = orchestration_status(run_dir)
            self.assertEqual("COMPLETE", final_status["state"])
            self.assertEqual(
                ["discovery", "plan", "risk", "cases", "review", "delivery"],
                final_status["validated_phases"],
            )
            self.assertFalse(final_status["delivery_command"])

    def test_orchestrated_delivery_rejects_non_delivery_running_state(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "delivery-state-guard")
            with self.assertRaisesRegex(ValueError, "requires state=DELIVERY_RUNNING"):
                architecture_safety.TOOLS.complete_deliverables(
                    root,
                    root / "working" / "formal.xlsx",
                    architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                    "产品>模块>页面",
                    import_workbook=root / "working" / "import.xlsx",
                    batch_status=run_dir / "batch-status.csv",
                    batch_id="BATCH-001",
                    product_name="产品",
                    assembly_run_dir=run_dir,
                    formal_template=architecture_safety.REPO_ROOT / "docs/test-design/codebuddy-test-design-template.xlsx",
                )
            self.assertEqual("INIT", orchestration_status(run_dir)["state"])
            self.assertFalse((root / "working" / "formal.xlsx").exists())


if __name__ == "__main__":
    unittest.main()
