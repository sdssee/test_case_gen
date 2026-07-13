# -*- coding: utf-8 -*-
from __future__ import annotations

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
from test_design.orchestration.review import REQUIRED_REVIEW_CHECKS


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
            run_dir = self.helper.make_valid_plan_run(root, "full-agent-chain")

            discovery = advance_orchestration(run_dir)["runnable_tasks"][0]
            discovery_output = self._output_dir(run_dir, discovery)
            for name in (
                "page-element-inventory.csv",
                "page-discovery.csv",
                "selection-option-observations.csv",
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
            scenarios_path.write_text(
                json.dumps(scenarios_rows, ensure_ascii=False), encoding="utf-8"
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
            with self.assertRaisesRegex(ValueError, "external --formal-workbook input is not allowed"):
                architecture_safety.TOOLS.complete_deliverables(
                    root,
                    architecture_safety.REPO_ROOT / "docs/test-design/codebuddy-test-design-template.xlsx",
                    architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                    "产品>模块>页面",
                    batch_status=run_dir / "batch-status.csv",
                    batch_id="BATCH-001",
                    product_name="产品",
                )


if __name__ == "__main__":
    unittest.main()
