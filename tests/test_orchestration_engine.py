# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

try:
    from tests import test_architecture_safety as architecture_safety
except ModuleNotFoundError:  # unittest discovery may place tests/ directly on sys.path.
    import test_architecture_safety as architecture_safety

from test_design.orchestration.engine import (
    DFX_MATRIX,
    OrchestrationError,
    advance_orchestration,
    claim_agent_task,
    commit_page_probe_receipt,
    initialize_orchestration,
    orchestration_status,
    resume_external_block,
    release_agent_claim,
    submit_agent_result,
    _validate_reviewer_execution_identity,
    _validate_risk_candidates_file,
)
from test_design.orchestration.contracts import AgentClaim
from test_design.orchestration import engine as engine_module
from test_design.orchestration import case_merge as case_merge_module
from test_design.orchestration import execution_binding as execution_binding_module
from test_design.orchestration import workspace as workspace_module
from test_design import batch as batch_module
from test_design.orchestration.state_machine import OrchestrationStateMachine, Phase
from test_design.sensitive_data import (
    SensitiveDataError,
    assert_no_sensitive_batch_files,
)


_RECORDER_PATH = Path(__file__).resolve().parents[1] / ".codebuddy/hooks/record-page-probe.py"
_RECORDER_SPEC = importlib.util.spec_from_file_location(
    "orchestration_engine_page_probe_recorder", _RECORDER_PATH
)
assert _RECORDER_SPEC is not None and _RECORDER_SPEC.loader is not None
_RECORDER = importlib.util.module_from_spec(_RECORDER_SPEC)
sys.modules[_RECORDER_SPEC.name] = _RECORDER
_RECORDER_SPEC.loader.exec_module(_RECORDER)
_GUARD_PATH = Path(__file__).resolve().parents[1] / ".codebuddy/hooks/guard-agent-tool.py"
_GUARD_SPEC = importlib.util.spec_from_file_location(
    "orchestration_engine_agent_guard", _GUARD_PATH
)
assert _GUARD_SPEC is not None and _GUARD_SPEC.loader is not None
_GUARD = importlib.util.module_from_spec(_GUARD_SPEC)
sys.modules[_GUARD_SPEC.name] = _GUARD
_GUARD_SPEC.loader.exec_module(_GUARD)
from test_design.orchestration.review import (
    REQUIRED_REVIEW_CHECKS,
    ReviewValidationError,
    _review_source_paths,
    _validate_task_events,
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

    def _claim_task(
        self,
        run_dir: Path,
        task: dict[str, object],
        *,
        coordinator_id: str = "COORD-TEST",
        executor_id: str | None = None,
        executor_kind: str = "codebuddy-subagent",
        wave_id: str | None = None,
        execution_id: str | None = None,
        physical_binding: bool = True,
    ) -> str:
        task_id = str(task["task_id"])
        execution = execution_id or f"EXEC-{task_id}"
        probe_claim: dict[str, object] = {}
        if task.get("agent_role") == "discovery":
            project_root = run_dir.parents[3]
            transcript = project_root / ".runtime" / f"{execution}.jsonl"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text('{"message":{"content":"coordinator preflight"}}\n', encoding="utf-8")
            session_id = f"SESSION-{execution}"
            records = []
            for tool_name, tool_input, tool_response in (
                ("mcp__page__snapshot", {"action": "snapshot"}, {"state": "before"}),
                ("mcp__page__click", {"action": "click", "target": "safe"}, {"state": "clicked"}),
                ("mcp__page__snapshot", {"action": "snapshot"}, {"state": "after"}),
            ):
                _, record = _RECORDER.record_event(
                    {
                        "session_id": session_id,
                        "transcript_path": str(transcript),
                        "cwd": str(project_root),
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "tool_response": tool_response,
                    },
                    project_root,
                )
                records.append(record)
            evidence_relative = f"artifacts/page-probe-evidence/{execution}/probe.json"
            evidence = run_dir / evidence_relative
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text('{"observed":"before-click-after"}\n', encoding="utf-8")
            committed = commit_page_probe_receipt(
                run_dir,
                task_id,
                execution_id=execution,
                coordinator_id=coordinator_id,
                session_sha256=str(records[0]["session_sha256"]),
                transcript_sha256=str(records[0]["transcript_path_sha256"]),
                record_ids=[str(record["record_id"]) for record in records],
                evidence_paths=[evidence_relative],
            )["page_probe_receipt"]
            probe_claim = {
                "page_probe_receipt_id": committed["receipt_id"],
                "page_probe_receipt_fingerprint": committed["receipt_fingerprint"],
            }
        executor = executor_id or f"EXECUTOR-{task_id}"
        claim_agent_task(
            run_dir,
            task_id,
            execution_id=execution,
            coordinator_id=coordinator_id,
            executor_id=executor,
            executor_kind=executor_kind,
            wave_id=wave_id or f"WAVE-{task_id}",
            **probe_claim,
        )
        if executor_kind == "codebuddy-subagent" and physical_binding:
            project_root = run_dir.parents[3]
            task_path = (
                run_dir
                / "artifacts"
                / "agent-work"
                / str(task["agent_role"])
                / task_id
                / "meta"
                / "agent-task.json"
            )
            safe_execution = "".join(
                char if char.isalnum() or char in "._-" else "-" for char in execution
            )
            transcript = (
                project_root
                / ".runtime"
                / "parent-session"
                / "subagents"
                / f"agent-{safe_execution}.jsonl"
            )
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(
                json.dumps(
                    {
                        "message": {
                            "content": (
                                f"task={task_path}\nexecution_id={execution}\n"
                                f"executor_id={executor}"
                            )
                        }
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            if task_path.is_file():
                decision = _GUARD.evaluate_event(
                    {
                        "session_id": f"SESSION-{safe_execution}",
                        "transcript_path": str(transcript),
                        "cwd": str(project_root),
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Read",
                        "tool_input": {"file_path": str(task_path)},
                    },
                    project_root,
                )
                if decision is not None:
                    raise AssertionError(
                        f"test guard could not bind claimed execution: {decision}"
                    )
            else:
                # Scheduler-only fixtures deliberately omit physical task packets.
                # Install the exact trusted-marker shape directly so these tests
                # stay focused on frozen-wave ordering rather than hook contracts.
                manifest = json.loads(
                    (run_dir / "orchestration" / "run-manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                claim = AgentClaim.from_dict(manifest["tasks"][task_id]["claim"])
                normalized_run = str(run_dir.resolve()).replace("\\", "/").rstrip("/")
                normalized_transcript = (
                    str(transcript.resolve()).replace("\\", "/").rstrip("/")
                )
                if os.name == "nt":
                    normalized_run = normalized_run.casefold()
                    normalized_transcript = normalized_transcript.casefold()
                transcript_bytes = transcript.read_bytes()
                transcript_stat = transcript.stat()
                content = {
                    "schema_version": execution_binding_module.EXECUTION_BINDING_SCHEMA_VERSION,
                    "guard_version": execution_binding_module.EXECUTION_BINDING_GUARD_VERSION,
                    "run_dir_sha256": hashlib.sha256(
                        normalized_run.encode("utf-8")
                    ).hexdigest(),
                    "run_id": task["run_id"],
                    "batch_id": task["batch_id"],
                    "task_id": task_id,
                    "execution_id": claim.execution_id,
                    "coordinator_id": claim.coordinator_id,
                    "executor_id": claim.executor_id,
                    "executor_kind": claim.executor_kind.value,
                    "source_fingerprint": task["source_fingerprint"],
                    "input_snapshot_fingerprint": claim.input_snapshot_fingerprint,
                    "task_packet_fingerprint": claim.task_packet_fingerprint,
                    "context_fingerprint": claim.context_fingerprint,
                    "claim_fingerprint": engine_module.canonical_fingerprint(
                        claim.to_dict()
                    ),
                    "transcript_path": normalized_transcript,
                    "transcript_path_sha256": hashlib.sha256(
                        normalized_transcript.encode("utf-8")
                    ).hexdigest(),
                    "transcript_parent_name": "subagents",
                    "transcript_file_name": (
                        transcript.name.casefold() if os.name == "nt" else transcript.name
                    ),
                    "transcript_bound_size": len(transcript_bytes),
                    "transcript_prefix_sha256": hashlib.sha256(
                        transcript_bytes
                    ).hexdigest(),
                    "transcript_device": (
                        int(transcript_stat.st_dev) if int(transcript_stat.st_dev) > 0 else None
                    ),
                    "transcript_inode": (
                        int(transcript_stat.st_ino) if int(transcript_stat.st_ino) > 0 else None
                    ),
                }
                marker = {
                    **content,
                    "binding_fingerprint": engine_module.canonical_fingerprint(content),
                }
                marker_path = execution_binding_module.execution_binding_path(
                    project_root, execution
                )
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                marker_path.write_text(
                    json.dumps(
                        marker,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )
        return execution

    def _submit(
        self,
        run_dir: Path,
        task: dict[str, object],
        result_path: Path,
        **claim_kwargs: object,
    ) -> dict[str, object]:
        execution_id = self._claim_task(run_dir, task, **claim_kwargs)
        return submit_agent_result(
            run_dir,
            str(task["task_id"]),
            result_path,
            execution_id=execution_id,
        )

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
    def _case_control_result(
        task: dict[str, object],
        status: str,
    ) -> dict[str, object]:
        result = OrchestrationEngineTests._result(
            task,
            status,
            None if status == "NEEDS_REWORK" else f"synthetic {status.lower()} control",
        )
        if status == "NEEDS_REWORK":
            result["rework_requests"] = [
                {
                    "schema_version": "1.0.0",
                    "request_id": f"RW-WAVE-{task['task_id']}",
                    "run_id": task["run_id"],
                    "batch_id": task["batch_id"],
                    "target_phase": "cases",
                    "target_task_id": task["task_id"],
                    "reason_code": "DUPLICATE_STEPS",
                    "affected_ids": [str(task["owner_key"])],
                    "evidence": [],
                    "required_action": "regenerate the affected function point from distinct observed anchors",
                    "source_fingerprint": task["source_fingerprint"],
                    "attempt": task["attempt"],
                }
            ]
        return result

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

    def _seed_case_wave(
        self,
        run_dir: Path,
        owner_task_pairs: list[tuple[str, str]],
    ) -> tuple[list[dict[str, object]], str]:
        """Install a minimal, contract-valid CASES_RUNNING manifest for scheduler tests."""

        manifest = initialize_orchestration(run_dir)
        machine = OrchestrationStateMachine()
        for phase in (Phase.DISCOVERY, Phase.PLAN, Phase.RISK):
            machine.start_phase(phase)
            machine.validate_phase(phase)
        machine.start_phase(Phase.CASES)
        source_fingerprint = "a" * 64
        tasks: list[dict[str, object]] = []
        entries: dict[str, object] = {}
        for owner_key, task_id in owner_task_pairs:
            output_root = f"artifacts/agent-work/case_worker/{task_id}/output"
            task: dict[str, object] = {
                "schema_version": "1.0.0",
                "task_id": task_id,
                "run_id": manifest["run_id"],
                "batch_id": manifest["batch_id"],
                "phase": "cases",
                "agent_role": "case_worker",
                "owner_key": owner_key,
                "input_files": [],
                "allowed_output_files": [
                    f"{output_root}/function_cases.json",
                    f"{output_root}/case-traceability.json",
                ],
                "allowed_output_prefixes": [],
                "required_gate": "cases-worker",
                "source_fingerprint": source_fingerprint,
                "attempt": 1,
            }
            tasks.append(task)
            entries[task_id] = {
                "task": task,
                "status": "PENDING",
                "claim": None,
                "claim_history": [],
                "dispatch_wave": None,
                "input_snapshot_fingerprint": "b" * 64,
                "task_packet_fingerprint": "c" * 64,
                "context_fingerprint": "d" * 64,
                "result_path": None,
                "result_fingerprint": None,
                "promotion_ids": [],
            }
        manifest["state_machine"] = machine.to_dict()
        manifest["tasks"] = entries
        manifest["case_task_order"] = [task_id for _, task_id in owner_task_pairs]
        serialized = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        (run_dir / "orchestration" / "run-manifest.json").write_text(
            serialized, encoding="utf-8"
        )
        (run_dir / "orchestration" / "state.json").write_text(
            json.dumps(machine.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return tasks, source_fingerprint

    def _seed_successful_retained_plan(
        self,
        run_dir: Path,
    ) -> tuple[dict[str, object], str, dict[str, bytes], dict[str, bytes]]:
        manifest = initialize_orchestration(run_dir)
        task_id = "TASK-PLAN-DFX-A01"
        output_root = f"artifacts/agent-work/plan_dfx/{task_id}/output"
        task: dict[str, object] = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "run_id": manifest["run_id"],
            "batch_id": manifest["batch_id"],
            "phase": "plan",
            "agent_role": "plan_dfx",
            "owner_key": None,
            "input_files": [],
            "allowed_output_files": [
                f"{output_root}/{name}"
                for name in engine_module._RETAINED_SHEET_JSON_NAMES
            ],
            "allowed_output_prefixes": [],
            "required_gate": "plan",
            "source_fingerprint": "a" * 64,
            "attempt": 1,
        }
        accepted = run_dir / "orchestration" / "accepted" / task_id
        accepted.mkdir(parents=True)
        data_dir = run_dir / "artifacts" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        expected: dict[str, bytes] = {}
        original: dict[str, bytes] = {}
        for index, name in enumerate(engine_module._RETAINED_SHEET_JSON_NAMES):
            expected[name] = json.dumps(
                {"version": "new", "name": name, "index": index},
                ensure_ascii=False,
            ).encode("utf-8")
            original[name] = json.dumps(
                {"version": "old", "name": name, "index": index},
                ensure_ascii=False,
            ).encode("utf-8")
            (accepted / name).write_bytes(expected[name])
            (data_dir / name).write_bytes(original[name])
        accepted_paths = [path for path in accepted.rglob("*") if path.is_file()]
        manifest["tasks"][task_id] = {
            "task": task,
            "status": "SUCCEEDED",
            "claim": None,
            "claim_history": [],
            "dispatch_wave": None,
            "accepted_output_root": f"orchestration/accepted/{task_id}",
            "accepted_output_fingerprint": engine_module.fingerprint(accepted_paths),
            "promotion_ids": [],
            "result_path": None,
            "result_fingerprint": None,
        }
        return manifest, task_id, expected, original

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

    def test_submit_requires_untampered_physical_subagent_binding_and_transcript(self) -> None:
        scenarios = (
            "missing-marker",
            "tampered-marker",
            "deleted-marker",
            "deleted-transcript",
            "truncated-transcript",
            "rewritten-prefix",
            "replaced-transcript",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as value:
                run_dir = self._new_run(Path(value), f"binding-{scenario}")
                task = advance_orchestration(run_dir)["runnable_tasks"][0]
                execution = self._claim_task(
                    run_dir,
                    task,
                    execution_id=f"EXEC-{scenario}",
                    physical_binding=scenario != "missing-marker",
                )
                marker_path = execution_binding_module.execution_binding_path(
                    run_dir.parents[3], execution
                )
                if scenario == "missing-marker":
                    # Merely mentioning a task/claim in a main-session transcript
                    # cannot manufacture the hook-owned physical proof.
                    spoof = run_dir.parents[3] / ".runtime" / "main-session.jsonl"
                    spoof.parent.mkdir(parents=True, exist_ok=True)
                    spoof.write_text(
                        json.dumps(
                            {
                                "content": (
                                    f"{task['task_id']} {execution} "
                                    f"EXECUTOR-{task['task_id']}"
                                )
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    self.assertFalse(marker_path.exists())
                else:
                    marker = json.loads(marker_path.read_text(encoding="utf-8"))
                    transcript = Path(marker["transcript_path"])
                    original = transcript.read_bytes()
                    if scenario == "tampered-marker":
                        marker["executor_id"] = "TAMPERED-EXECUTOR"
                        marker_path.write_text(
                            json.dumps(marker, ensure_ascii=False) + "\n",
                            encoding="utf-8",
                        )
                    elif scenario == "deleted-marker":
                        marker_path.unlink()
                    elif scenario == "deleted-transcript":
                        transcript.unlink()
                    elif scenario == "truncated-transcript":
                        transcript.write_bytes(original[: max(1, len(original) // 2)])
                    elif scenario == "rewritten-prefix":
                        changed = bytearray(original)
                        changed[min(10, len(changed) - 1)] ^= 1
                        transcript.write_bytes(changed)
                    elif scenario == "replaced-transcript":
                        backup = transcript.with_suffix(".bound-backup")
                        transcript.rename(backup)
                        transcript.write_bytes(original)
                result_path = self._write_result(
                    run_dir,
                    f"binding-result-{scenario}",
                    self._result(task, "FAILED", "synthetic failure"),
                )
                with self.assertRaisesRegex(
                    OrchestrationError,
                    "physical sub-agent execution binding",
                ):
                    submit_agent_result(
                        run_dir,
                        str(task["task_id"]),
                        result_path,
                        execution_id=execution,
                    )

    def test_orphan_page_probe_binary_is_always_a_sensitive_audit_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "orphan-page-probe-binary")
            orphan = (
                run_dir
                / "artifacts"
                / "page-probe-evidence"
                / "FAILED-EXECUTION"
                / "orphan.png"
            )
            orphan.parent.mkdir(parents=True, exist_ok=True)
            orphan.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-test-image")
            with self.assertRaisesRegex(
                SensitiveDataError,
                "requires adjacent visual privacy audit",
            ):
                assert_no_sensitive_batch_files(run_dir)
            self._write_binary_audit(orphan)
            assert_no_sensitive_batch_files(run_dir)

    def test_claim_is_durable_idempotent_and_requires_explicit_safe_release(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "durable-claim")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            unclaimed_result = self._write_result(
                run_dir, "unclaimed-execution", self._result(task, "FAILED", "probe")
            )
            with self.assertRaisesRegex(OrchestrationError, "not claimed"):
                submit_agent_result(
                    run_dir,
                    str(task["task_id"]),
                    unclaimed_result,
                    execution_id="EXEC-UNCLAIMED",
                )
            execution_id = self._claim_task(run_dir, task)
            status = orchestration_status(run_dir)
            self.assertEqual([], status["runnable_tasks"])
            self.assertEqual(1, status["task_counts"]["CLAIMED"])
            self.assertEqual(execution_id, status["claimed_tasks"][0]["claim"]["execution_id"])
            self.assertEqual(
                [str(task["task_id"])], status["active_dispatch_wave"]["claimed_task_ids"]
            )
            durable_claim = status["claimed_tasks"][0]["claim"]

            replay = claim_agent_task(
                run_dir,
                str(task["task_id"]),
                execution_id=execution_id,
                coordinator_id="COORD-TEST",
                executor_id=f"EXECUTOR-{task['task_id']}",
                executor_kind="codebuddy-subagent",
                wave_id=f"WAVE-{task['task_id']}",
                page_probe_receipt_id=durable_claim["page_probe_receipt_id"],
                page_probe_receipt_fingerprint=durable_claim[
                    "page_probe_receipt_fingerprint"
                ],
            )
            self.assertEqual(execution_id, replay["claim"]["execution_id"])
            with self.assertRaisesRegex(OrchestrationError, "pre-bound page probe receipt"):
                claim_agent_task(
                    run_dir,
                    str(task["task_id"]),
                    execution_id="EXEC-OTHER",
                    coordinator_id="COORD-OTHER",
                    executor_id="EXECUTOR-OTHER",
                    executor_kind="external-session",
                    wave_id="WAVE-OTHER",
                    page_probe_receipt_id=durable_claim["page_probe_receipt_id"],
                    page_probe_receipt_fingerprint=durable_claim[
                        "page_probe_receipt_fingerprint"
                    ],
                )
            self.assertEqual(
                "CLAIMED",
                initialize_orchestration(run_dir)["tasks"][task["task_id"]]["status"],
            )

            result_path = self._write_result(
                run_dir, "wrong-execution", self._result(task, "FAILED", "probe")
            )
            with self.assertRaisesRegex(OrchestrationError, "not EXEC-WRONG"):
                submit_agent_result(
                    run_dir,
                    str(task["task_id"]),
                    result_path,
                    execution_id="EXEC-WRONG",
                )
            with self.assertRaisesRegex(OrchestrationError, "confirm-no-side-effects"):
                release_agent_claim(
                    run_dir,
                    str(task["task_id"]),
                    execution_id=execution_id,
                    coordinator_id="COORD-TEST",
                    reason="operator has not confirmed safety",
                    confirm_no_side_effects=False,
                )
            partial = self._output_dir(run_dir, task) / "partial.txt"
            partial.write_text("partial isolated output", encoding="utf-8")
            released = release_agent_claim(
                run_dir,
                str(task["task_id"]),
                execution_id=execution_id,
                coordinator_id="COORD-TEST",
                reason="executor never opened the product and created no external side effects",
                confirm_no_side_effects=True,
            )
            self.assertFalse(partial.exists())
            self.assertTrue(
                execution_binding_module.execution_binding_path(
                    run_dir.parents[3], execution_id
                ).is_file()
            )
            self.assertEqual(str(task["task_id"]), released["runnable_tasks"][0]["task_id"])
            self.assertEqual(1, released["task_counts"]["PENDING"])
            self._claim_task(
                run_dir,
                task,
                execution_id="EXEC-NEW-COORDINATOR",
                coordinator_id="COORD-NEW",
                executor_id="EXECUTOR-NEW",
                executor_kind="codebuddy-subagent",
                wave_id="WAVE-NEW",
            )
            reclaimed = orchestration_status(run_dir)
            self.assertEqual(
                "COORD-NEW", reclaimed["claimed_tasks"][0]["claim"]["coordinator_id"]
            )

    def test_case_wave_freezes_plan_order_and_rejects_foreign_claims(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "ordered-case-wave")
            owner_tasks = [
                ("功能点-第一", "TASK-CASE-ZZZ-A01"),
                ("功能点-第二", "TASK-CASE-AAA-A01"),
                ("功能点-第三", "TASK-CASE-MMM-A01"),
            ]
            tasks, source_fingerprint = self._seed_case_wave(run_dir, owner_tasks)
            groups = {owner: [] for owner, _ in owner_tasks}
            expected_order = [task_id for _, task_id in owner_tasks]

            with (
                mock.patch.object(engine_module, "plan_groups", return_value=groups),
                mock.patch.object(engine_module, "_task_inputs_still_current", return_value=True),
                mock.patch.object(
                    engine_module,
                    "_generation_task_fingerprint",
                    return_value=source_fingerprint,
                ),
            ):
                status = orchestration_status(run_dir)
                self.assertEqual(expected_order, [task["task_id"] for task in status["runnable_tasks"]])

                first = claim_agent_task(
                    run_dir,
                    expected_order[0],
                    execution_id="EXEC-WAVE-001",
                    coordinator_id="COORD-WAVE",
                    executor_id="EXECUTOR-WAVE-001",
                    executor_kind="codebuddy-subagent",
                    wave_id="WAVE-PLAN-ORDER",
                )
                wave = first["active_dispatch_wave"]
                self.assertEqual(expected_order, wave["task_ids"])
                self.assertEqual(expected_order[1:], wave["pending_task_ids"])
                self.assertEqual([expected_order[0]], wave["claimed_task_ids"])

                with self.assertRaisesRegex(
                    OrchestrationError, "another dispatch wave/coordinator"
                ):
                    claim_agent_task(
                        run_dir,
                        expected_order[1],
                        execution_id="EXEC-FOREIGN-WAVE",
                        coordinator_id="COORD-FOREIGN",
                        executor_id="EXECUTOR-FOREIGN",
                        executor_kind="codebuddy-subagent",
                        wave_id="WAVE-FOREIGN",
                    )

                released = release_agent_claim(
                    run_dir,
                    expected_order[0],
                    execution_id="EXEC-WAVE-001",
                    coordinator_id="COORD-WAVE",
                    reason="test confirms the partially claimed parallel wave caused no side effects",
                    confirm_no_side_effects=True,
                )
                self.assertIsNone(released["active_dispatch_wave"])
                self.assertEqual(
                    expected_order,
                    [task["task_id"] for task in released["runnable_tasks"]],
                )
                events = [
                    json.loads(line)
                    for line in (run_dir / "orchestration" / "events.jsonl").read_text(
                        encoding="utf-8"
                    ).splitlines()
                ]
                wave_release = [
                    event
                    for event in events
                    if event["event_type"] == "DISPATCH_WAVE_RELEASED"
                ][-1]
                self.assertEqual(expected_order, wave_release["payload"]["released_task_ids"])

                claim_agent_task(
                    run_dir,
                    expected_order[0],
                    execution_id="EXEC-WAVE-RETRY-001",
                    coordinator_id="COORD-WAVE",
                    executor_id="EXECUTOR-WAVE-RETRY-001",
                    executor_kind="codebuddy-subagent",
                    wave_id="WAVE-PLAN-ORDER-RETRY",
                )
                for index, task_id in enumerate(expected_order[1:], start=2):
                    status = claim_agent_task(
                        run_dir,
                        task_id,
                        execution_id=f"EXEC-WAVE-RETRY-{index:03d}",
                        coordinator_id="COORD-WAVE",
                        executor_id=f"EXECUTOR-WAVE-RETRY-{index:03d}",
                        executor_kind="codebuddy-subagent",
                        wave_id="WAVE-PLAN-ORDER-RETRY",
                    )
                self.assertEqual(expected_order, status["active_dispatch_wave"]["claimed_task_ids"])
                self.assertEqual([], status["active_dispatch_wave"]["pending_task_ids"])

    def test_case_wave_rejects_early_and_out_of_order_submit_then_accepts_frozen_order(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "case-wave-submit-barrier")
            owner_tasks = [
                ("鍔熻兘鐐?绗竴", "TASK-CASE-ZZZ-A01"),
                ("鍔熻兘鐐?绗簩", "TASK-CASE-AAA-A01"),
                ("鍔熻兘鐐?绗笁", "TASK-CASE-MMM-A01"),
            ]
            tasks, source_fingerprint = self._seed_case_wave(run_dir, owner_tasks)
            groups = {owner: [] for owner, _ in owner_tasks}
            expected_order = [task_id for _, task_id in owner_tasks]
            by_id = {str(task["task_id"]): task for task in tasks}
            for task in tasks:
                self._output_dir(run_dir, task).mkdir(parents=True, exist_ok=True)
            wave_id = "WAVE-CLAIM-ALL-FIRST"
            coordinator_id = "COORD-WAVE-BARRIER"

            source_patches = (
                mock.patch.object(engine_module, "plan_groups", return_value=groups),
                mock.patch.object(engine_module, "_task_inputs_still_current", return_value=True),
                mock.patch.object(
                    engine_module,
                    "_generation_task_fingerprint",
                    return_value=source_fingerprint,
                ),
            )
            with source_patches[0], source_patches[1], source_patches[2]:
                first_execution = self._claim_task(
                    run_dir,
                    by_id[expected_order[0]],
                    coordinator_id=coordinator_id,
                    wave_id=wave_id,
                    execution_id="EXEC-WAVE-BARRIER-001",
                )
                manifest_before = (
                    run_dir / "orchestration" / "run-manifest.json"
                ).read_bytes()
                with self.assertRaisesRegex(
                    OrchestrationError,
                    "entire dispatch wave is claimed",
                ):
                    submit_agent_result(
                        run_dir,
                        expected_order[0],
                        run_dir / "result-must-not-be-read-before-wave-barrier.json",
                        execution_id=first_execution,
                    )
                self.assertEqual(
                    manifest_before,
                    (run_dir / "orchestration" / "run-manifest.json").read_bytes(),
                )

                executions = {expected_order[0]: first_execution}
                for index, task_id in enumerate(expected_order[1:], start=2):
                    executions[task_id] = self._claim_task(
                        run_dir,
                        by_id[task_id],
                        coordinator_id=coordinator_id,
                        wave_id=wave_id,
                        execution_id=f"EXEC-WAVE-BARRIER-{index:03d}",
                    )

                with (
                    mock.patch.object(engine_module, "_validate_result_files"),
                    mock.patch.object(
                        engine_module,
                        "_snapshot_accepted_outputs",
                        return_value="f" * 64,
                    ),
                    mock.patch.object(case_merge_module, "validate_worker_outputs"),
                    mock.patch.object(
                        engine_module,
                        "advance_orchestration",
                        return_value={"state": "CASES_RUNNING"},
                    ),
                ):
                    with self.assertRaisesRegex(
                        OrchestrationError,
                        "frozen wave order",
                    ):
                        submit_agent_result(
                            run_dir,
                            expected_order[2],
                            self._write_result(
                                run_dir,
                                "wave-out-of-order-rejected",
                                self._success_result(by_id[expected_order[2]]),
                            ),
                            execution_id=executions[expected_order[2]],
                        )
                    rejected_manifest = initialize_orchestration(run_dir)
                    self.assertTrue(
                        all(
                            rejected_manifest["tasks"][task_id]["status"] == "CLAIMED"
                            for task_id in expected_order
                        )
                    )

                    first_task_id = expected_order[0]
                    submit_agent_result(
                        run_dir,
                        first_task_id,
                        self._write_result(
                            run_dir,
                            "wave-frozen-order-001",
                            self._success_result(by_id[first_task_id]),
                        ),
                        execution_id=executions[first_task_id],
                    )
                    with self.assertRaisesRegex(
                        OrchestrationError,
                        "already submitted peer result",
                    ):
                        submit_agent_result(
                            run_dir,
                            expected_order[1],
                            self._write_result(
                                run_dir,
                                "wave-control-after-success-rejected",
                                self._result(
                                    by_id[expected_order[1]],
                                    "FAILED",
                                    "later worker returned a control result",
                                ),
                            ),
                            execution_id=executions[expected_order[1]],
                        )

                    for index, task_id in enumerate(expected_order[1:], start=2):
                        task = by_id[task_id]
                        submit_agent_result(
                            run_dir,
                            task_id,
                            self._write_result(
                                run_dir,
                                f"wave-out-of-order-{index:03d}",
                                self._success_result(task),
                            ),
                            execution_id=executions[task_id],
                        )

                events = [
                    json.loads(line)
                    for line in (run_dir / "orchestration" / "events.jsonl").read_text(
                        encoding="utf-8"
                    ).splitlines()
                ]
                self.assertEqual(
                    expected_order,
                    [
                        event["task_id"]
                        for event in events
                        if event["event_type"] == "TASK_SUCCEEDED"
                        and event.get("task_id") in expected_order
                    ],
                )

                merged_order: list[str] = []

                def capture_merge(
                    _run_dir: Path,
                    payloads: list[tuple[object, Path, Path]],
                ) -> dict[str, object]:
                    merged_order.extend(str(task.task_id) for task, _, _ in payloads)
                    return {
                        "total_cases": 3,
                        "parts": ["function_cases_part_001.json"],
                        "traceability_records": 3,
                        "worker_task_ids": list(merged_order),
                    }

                current_manifest = initialize_orchestration(run_dir)
                self.assertTrue(
                    all(
                        current_manifest["tasks"][task_id].get("dispatch_wave")
                        is None
                        for task_id in expected_order
                    )
                )
                with mock.patch.object(
                    engine_module, "aggregate_case_workers", side_effect=capture_merge
                ):
                    engine_module._aggregate_ready_workers(run_dir, current_manifest)
                self.assertEqual(expected_order, merged_order)

    def _exercise_case_wave_control_result(self, control_status: str) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(
                Path(value), f"case-wave-control-{control_status.lower()}"
            )
            owner_tasks = [
                ("feature-first", "TASK-CASE-WAVE-FIRST-A01"),
                ("feature-control", "TASK-CASE-WAVE-CONTROL-A01"),
                ("feature-last", "TASK-CASE-WAVE-LAST-A01"),
            ]
            tasks, source_fingerprint = self._seed_case_wave(run_dir, owner_tasks)
            groups = {owner: [] for owner, _ in owner_tasks}
            expected_order = [task_id for _, task_id in owner_tasks]
            by_id = {str(task["task_id"]): task for task in tasks}
            for task in tasks:
                self._output_dir(run_dir, task).mkdir(parents=True, exist_ok=True)
            wave_id = f"WAVE-CONTROL-{control_status}"
            coordinator_id = "COORD-WAVE-CONTROL"
            control_task_id = expected_order[1]
            control_task = by_id[control_task_id]

            with (
                mock.patch.object(engine_module, "plan_groups", return_value=groups),
                mock.patch.object(
                    engine_module, "_task_inputs_still_current", return_value=True
                ),
                mock.patch.object(
                    engine_module,
                    "_generation_task_fingerprint",
                    return_value=source_fingerprint,
                ),
                mock.patch.object(engine_module, "_validate_result_files"),
            ):
                executions: dict[str, str] = {}
                for index, task_id in enumerate(expected_order, start=1):
                    executions[task_id] = self._claim_task(
                        run_dir,
                        by_id[task_id],
                        coordinator_id=coordinator_id,
                        wave_id=wave_id,
                        execution_id=f"EXEC-CONTROL-{control_status}-{index:03d}",
                    )

                control_result_path = self._write_result(
                    run_dir,
                    f"case-wave-{control_status.lower()}",
                    self._case_control_result(control_task, control_status),
                )
                manifest_path = run_dir / "orchestration" / "run-manifest.json"
                manifest_before = manifest_path.read_bytes()
                with self.assertRaisesRegex(
                    OrchestrationError,
                    "explicitly released",
                ):
                    submit_agent_result(
                        run_dir,
                        control_task_id,
                        control_result_path,
                        execution_id=executions[control_task_id],
                    )
                self.assertEqual(manifest_before, manifest_path.read_bytes())

                for peer_task_id in (expected_order[0], expected_order[2]):
                    release_agent_claim(
                        run_dir,
                        peer_task_id,
                        execution_id=executions[peer_task_id],
                        coordinator_id=coordinator_id,
                        reason=(
                            f"full wave returned {control_status}; peer has no product or external side effects"
                        ),
                        confirm_no_side_effects=True,
                    )
                waiting = orchestration_status(run_dir)
                self.assertEqual(
                    [control_task_id],
                    [item["task_id"] for item in waiting["claimed_tasks"]],
                )
                self.assertEqual(
                    expected_order,
                    waiting["active_dispatch_wave"]["task_ids"],
                )

                control_response = submit_agent_result(
                    run_dir,
                    control_task_id,
                    control_result_path,
                    execution_id=executions[control_task_id],
                )

                self.assertEqual([], control_response["claimed_tasks"])
                self.assertIsNone(control_response["active_dispatch_wave"])
                manifest = initialize_orchestration(run_dir)
                self.assertTrue(
                    all(
                        entry.get("dispatch_wave") is None
                        for entry in manifest["tasks"].values()
                    )
                )
                self.assertFalse(
                    any(
                        entry.get("status") == "CLAIMED"
                        for entry in manifest["tasks"].values()
                    )
                )

                if control_status == "EXTERNAL_BLOCKED":
                    self.assertEqual("EXTERNAL_BLOCKED", control_response["state"])
                    self.assertEqual([], control_response["runnable_tasks"])
                    with (
                        mock.patch.object(
                            engine_module,
                            "generation_session_is_current",
                            return_value=True,
                        ),
                        mock.patch.object(
                            engine_module, "_promote_retained_sheet_json"
                        ),
                        mock.patch.object(
                            engine_module, "_all_case_workers_ready", return_value=False
                        ),
                    ):
                        recovered = resume_external_block(run_dir)
                elif control_status == "NEEDS_REWORK":
                    with (
                        mock.patch.object(
                            engine_module,
                            "generation_session_is_current",
                            return_value=True,
                        ),
                        mock.patch.object(
                            engine_module, "_promote_retained_sheet_json"
                        ),
                        mock.patch.object(
                            engine_module, "_all_case_workers_ready", return_value=False
                        ),
                    ):
                        recovered = advance_orchestration(run_dir)
                else:
                    recovered = control_response

                self.assertEqual("CASES_RUNNING", recovered["state"])
                recovered_manifest = initialize_orchestration(run_dir)
                pending_by_owner = {
                    str(entry["task"]["owner_key"]): str(entry["task"]["task_id"])
                    for entry in recovered_manifest["tasks"].values()
                    if entry.get("status") == "PENDING"
                    and entry.get("task", {}).get("agent_role") == "case_worker"
                }
                self.assertEqual(set(groups), set(pending_by_owner))
                replacement = pending_by_owner["feature-control"]
                self.assertNotEqual(control_task_id, replacement)
                self.assertTrue(replacement.endswith("-A02"))
                expected_recovered_order = [
                    pending_by_owner[owner] for owner, _ in owner_tasks
                ]
                self.assertEqual(
                    expected_recovered_order,
                    recovered_manifest["case_task_order"],
                )
                self.assertEqual(
                    expected_recovered_order,
                    [item["task_id"] for item in recovered["runnable_tasks"]],
                )
                self.assertTrue(
                    all(
                        recovered_manifest["tasks"][task_id].get("dispatch_wave")
                        is None
                        for task_id in expected_recovered_order
                    )
                )

    def test_case_wave_failed_control_releases_peers_and_rebuilds_ordered_retry(self) -> None:
        self._exercise_case_wave_control_result("FAILED")

    def test_case_wave_external_block_releases_peers_and_resumes_without_stale_wave(self) -> None:
        self._exercise_case_wave_control_result("EXTERNAL_BLOCKED")

    def test_case_wave_rework_control_releases_peers_and_rebuilds_ordered_retry(self) -> None:
        self._exercise_case_wave_control_result("NEEDS_REWORK")

    def test_case_wave_serial_threshold_and_partial_wave_release_are_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "serial-case-wave")
            owner_tasks = [
                ("功能点-前", "TASK-CASE-ZZZ-A01"),
                ("功能点-后", "TASK-CASE-AAA-A01"),
            ]
            _, source_fingerprint = self._seed_case_wave(run_dir, owner_tasks)
            groups = {owner: [] for owner, _ in owner_tasks}
            expected_order = [task_id for _, task_id in owner_tasks]

            with (
                mock.patch.object(engine_module, "plan_groups", return_value=groups),
                mock.patch.object(engine_module, "_task_inputs_still_current", return_value=True),
                mock.patch.object(
                    engine_module,
                    "_generation_task_fingerprint",
                    return_value=source_fingerprint,
                ),
            ):
                initial = orchestration_status(run_dir)
                self.assertEqual([expected_order[0]], [task["task_id"] for task in initial["runnable_tasks"]])
                claimed = claim_agent_task(
                    run_dir,
                    expected_order[0],
                    execution_id="EXEC-SERIAL-001",
                    coordinator_id="COORD-SERIAL",
                    executor_id="EXECUTOR-SERIAL-001",
                    executor_kind="codebuddy-subagent",
                    wave_id="WAVE-SERIAL",
                )
                self.assertEqual([expected_order[0]], claimed["active_dispatch_wave"]["task_ids"])
                released = release_agent_claim(
                    run_dir,
                    expected_order[0],
                    execution_id="EXEC-SERIAL-001",
                    coordinator_id="COORD-SERIAL",
                    reason="test confirms the partially dispatched wave caused no side effects",
                    confirm_no_side_effects=True,
                )
                self.assertIsNone(released["active_dispatch_wave"])
                self.assertEqual([expected_order[0]], [task["task_id"] for task in released["runnable_tasks"]])

            events = [
                json.loads(line)
                for line in (run_dir / "orchestration" / "events.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            wave_releases = [
                event for event in events if event["event_type"] == "DISPATCH_WAVE_RELEASED"
            ]
            self.assertEqual(1, len(wave_releases))
            self.assertEqual(
                [expected_order[0]], wave_releases[0]["payload"]["released_task_ids"]
            )

    def test_status_cannot_recover_an_inflight_submit_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            self.helper.create_project_root(root)
            run_dir = self.helper.make_valid_plan_run(root, "locked-promotion-submit")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            output = self._output_dir(run_dir, task)
            for name in (
                "page-element-inventory.csv",
                "page-discovery.csv",
                "selection-option-observations.csv",
                "interaction-branch-observations.csv",
                "test-data-lifecycle.csv",
            ):
                shutil.copy2(run_dir / name, output / name)
            execution_id = self._claim_task(run_dir, task)
            result_path = self._write_result(
                run_dir, "locked-promotion-result", self._success_result(task)
            )
            promoted = threading.Event()
            continue_submit = threading.Event()
            original_promote = workspace_module.WorkspaceManager.atomic_promote
            outcomes: list[dict[str, object]] = []
            failures: list[BaseException] = []

            def paused_promote(manager: object, *args: object, **kwargs: object):
                receipt = original_promote(manager, *args, **kwargs)
                promoted.set()
                if not continue_submit.wait(timeout=10):
                    raise AssertionError("test did not release the paused submit")
                return receipt

            def submit_in_background() -> None:
                try:
                    outcomes.append(
                        submit_agent_result(
                            run_dir,
                            str(task["task_id"]),
                            result_path,
                            execution_id=execution_id,
                        )
                    )
                except BaseException as exc:  # captured for assertion in the test thread
                    failures.append(exc)

            with mock.patch.object(
                workspace_module.WorkspaceManager,
                "atomic_promote",
                new=paused_promote,
            ):
                worker = threading.Thread(target=submit_in_background, daemon=True)
                worker.start()
                self.assertTrue(promoted.wait(timeout=10), "submit did not reach PROMOTED pause")
                receipt_path = next(
                    (run_dir / "orchestration" / "promotions").glob("*/receipt.json")
                )
                self.assertEqual(
                    "PROMOTED",
                    json.loads(receipt_path.read_text(encoding="utf-8"))["status"],
                )
                try:
                    with self.assertRaisesRegex(RuntimeError, "holds lock"):
                        orchestration_status(run_dir)
                    self.assertEqual(
                        "PROMOTED",
                        json.loads(receipt_path.read_text(encoding="utf-8"))["status"],
                    )
                finally:
                    continue_submit.set()
                worker.join(timeout=20)

            self.assertFalse(worker.is_alive(), "paused submit did not finish")
            self.assertEqual([], failures)
            self.assertEqual("PLAN_RUNNING", outcomes[0]["state"])
            self.assertEqual(
                "FINALIZED",
                json.loads(receipt_path.read_text(encoding="utf-8"))["status"],
            )

    def test_interrupted_retained_sheet_set_recovers_without_mixed_versions(self) -> None:
        class HardStop(BaseException):
            pass

        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "retained-sheet-recovery")
            manifest = initialize_orchestration(run_dir)
            task_id = "TASK-PLAN-DFX-A01"
            output_root = f"artifacts/agent-work/plan_dfx/{task_id}/output"
            task: dict[str, object] = {
                "schema_version": "1.0.0",
                "task_id": task_id,
                "run_id": manifest["run_id"],
                "batch_id": manifest["batch_id"],
                "phase": "plan",
                "agent_role": "plan_dfx",
                "owner_key": None,
                "input_files": [],
                "allowed_output_files": [
                    f"{output_root}/{name}"
                    for name in engine_module._RETAINED_SHEET_JSON_NAMES
                ],
                "allowed_output_prefixes": [],
                "required_gate": "plan",
                "source_fingerprint": "a" * 64,
                "attempt": 1,
            }
            accepted = run_dir / "orchestration" / "accepted" / task_id
            accepted.mkdir(parents=True)
            data_dir = run_dir / "artifacts" / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            expected: dict[str, bytes] = {}
            original: dict[str, bytes] = {}
            for index, name in enumerate(engine_module._RETAINED_SHEET_JSON_NAMES):
                expected[name] = json.dumps(
                    {"version": "new", "name": name, "index": index},
                    ensure_ascii=False,
                ).encode("utf-8")
                original[name] = json.dumps(
                    {"version": "old", "name": name, "index": index},
                    ensure_ascii=False,
                ).encode("utf-8")
                (accepted / name).write_bytes(expected[name])
                (data_dir / name).write_bytes(original[name])
            accepted_paths = [path for path in accepted.rglob("*") if path.is_file()]
            manifest["tasks"][task_id] = {
                "task": task,
                "status": "SUCCEEDED",
                "claim": None,
                "claim_history": [],
                "dispatch_wave": None,
                "accepted_output_root": f"orchestration/accepted/{task_id}",
                "accepted_output_fingerprint": engine_module.fingerprint(accepted_paths),
                "promotion_ids": [],
                "result_path": None,
                "result_fingerprint": None,
            }
            (run_dir / "orchestration" / "run-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            original_copy = workspace_module._atomic_copy
            formal_targets = {
                (data_dir / name).resolve()
                for name in engine_module._RETAINED_SHEET_JSON_NAMES
            }
            copied = 0

            def hard_stop_copy(source: Path, target: Path) -> None:
                nonlocal copied
                if target.resolve() in formal_targets:
                    copied += 1
                    if copied == 4:
                        raise HardStop("simulated retained sheet process termination")
                original_copy(source, target)

            with mock.patch.object(
                workspace_module, "_atomic_copy", side_effect=hard_stop_copy
            ):
                with self.assertRaises(HardStop):
                    engine_module._promote_retained_sheet_json(run_dir, manifest)

            promoted_states = [
                (data_dir / name).read_bytes() == expected[name] for name in expected
            ]
            self.assertIn(True, promoted_states, "fault injection replaced no formal file")
            self.assertIn(False, promoted_states, "fault injection replaced the full set")
            receipt_path = next(
                (run_dir / "orchestration" / "promotions").glob("*/receipt.json")
            )
            self.assertEqual(
                "APPLYING",
                json.loads(receipt_path.read_text(encoding="utf-8"))["status"],
            )

            recovered = initialize_orchestration(run_dir)
            self.assertIn(
                receipt_path.parent.name,
                recovered["tasks"][task_id]["promotion_ids"],
            )
            self.assertEqual(
                "FINALIZED",
                json.loads(receipt_path.read_text(encoding="utf-8"))["status"],
            )
            for name, content in expected.items():
                self.assertEqual(content, (data_dir / name).read_bytes(), name)
            self.assertFalse(
                (run_dir / "orchestration" / "promotion-sources" / receipt_path.parent.name).exists()
            )

    def test_cases_advance_recovers_retained_intent_link_before_receipt(self) -> None:
        class HardStop(BaseException):
            pass

        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "retained-intent-before-receipt")
            manifest, task_id, expected, original = self._seed_successful_retained_plan(
                run_dir
            )
            machine = OrchestrationStateMachine()
            for phase in (Phase.DISCOVERY, Phase.PLAN, Phase.RISK):
                machine.start_phase(phase)
                machine.validate_phase(phase)
            machine.start_phase(Phase.CASES)
            manifest["state_machine"] = machine.to_dict()
            (run_dir / "orchestration" / "run-manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (run_dir / "orchestration" / "state.json").write_text(
                json.dumps(machine.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            # Keep the generation session current while all eight formal files
            # still contain an old, internally consistent set.
            session = {
                "generation_session_id": "SESSION-RETAINED-INTENT",
                "source_fingerprint": batch_module.generation_source_fingerprint(
                    run_dir
                ),
                "catalog_source_fingerprint": batch_module.generation_catalog_fingerprint(
                    run_dir
                ),
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            (run_dir / "artifacts" / "data" / batch_module.GENERATION_SESSION).write_text(
                json.dumps(session, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertTrue(batch_module.generation_session_is_current(run_dir))

            with mock.patch.object(
                engine_module.WorkspaceManager,
                "atomic_promote",
                side_effect=HardStop("stop after manifest intent, before receipt"),
            ), mock.patch.object(engine_module, "_ensure_case_tasks"), mock.patch.object(
                engine_module, "_all_case_workers_ready", return_value=False
            ):
                with self.assertRaises(HardStop):
                    advance_orchestration(run_dir)

            interrupted_manifest = json.loads(
                (run_dir / "orchestration" / "run-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            promotion_ids = interrupted_manifest["tasks"][task_id]["promotion_ids"]
            self.assertEqual(1, len(promotion_ids))
            transaction_id = promotion_ids[0]
            receipt_path = (
                run_dir
                / "orchestration"
                / "promotions"
                / transaction_id
                / "receipt.json"
            )
            self.assertFalse(receipt_path.exists())
            for name, content in original.items():
                self.assertEqual(content, (run_dir / "artifacts" / "data" / name).read_bytes())

            with mock.patch.object(engine_module, "_ensure_case_tasks"), mock.patch.object(
                engine_module, "_all_case_workers_ready", return_value=False
            ):
                advance_orchestration(run_dir)

            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual("FINALIZED", receipt["status"])
            for name, content in expected.items():
                self.assertEqual(content, (run_dir / "artifacts" / "data" / name).read_bytes())

            # A finalized receipt cannot mask later formal-file drift.
            (run_dir / "artifacts" / "data" / "overview.json").write_text(
                '{"drifted": true}', encoding="utf-8"
            )
            with mock.patch.object(engine_module, "_ensure_case_tasks"), mock.patch.object(
                engine_module, "_all_case_workers_ready", return_value=False
            ):
                with self.assertRaisesRegex(
                    OrchestrationError,
                    "no longer matches its accepted source set",
                ):
                    advance_orchestration(run_dir)

    def test_task_context_freezes_exact_result_and_role_output_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "frozen-contract")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            context_path = (
                self._output_dir(run_dir, task).parent / "meta" / "task-context.json"
            )
            context = json.loads(context_path.read_text(encoding="utf-8"))
            contracts = context["contract_input_files"]
            self.assertIn(
                "docs/test-design/schemas/orchestration/agent-result.schema.json", contracts
            )
            self.assertIn(
                "docs/test-assets/batch-runs/templates/page-discovery-template.csv", contracts
            )
            self.assertEqual(task["required_gate"], context["result_rules"]["success_required_gate"])
            self.assertEqual(
                task["allowed_output_files"],
                context["result_rules"]["success_required_outputs"],
            )

            execution_id = self._claim_task(run_dir, task)
            contract = root / "docs/test-design/schemas/orchestration/agent-result.schema.json"
            contract.write_text(contract.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            result_path = self._write_result(
                run_dir, "contract-drift", self._result(task, "FAILED", "contract drift probe")
            )
            with self.assertRaisesRegex(OrchestrationError, "source changed"):
                submit_agent_result(
                    run_dir,
                    str(task["task_id"]),
                    result_path,
                    execution_id=execution_id,
                )

    def test_initialize_recovers_missing_release_audit_without_name_error(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "release-recovery")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            self._claim_task(run_dir, task)
            manifest_path = run_dir / "orchestration" / "run-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest["tasks"][task["task_id"]]
            released_claim = entry["claim"]
            probe_link = dict(entry["page_probe_receipt"])
            probe_receipt = engine_module.load_page_probe_receipt(
                run_dir,
                probe_link["receipt_id"],
                expected_fingerprint=probe_link["receipt_fingerprint"],
            )
            entry["claim_history"].append(
                {
                    "claim": released_claim,
                    "released_at": "2026-07-13T02:03:04Z",
                    "reason": "simulated crash after manifest release checkpoint",
                    "no_side_effects_confirmed": True,
                }
            )
            entry["claim"] = None
            entry["status"] = "PENDING"
            entry["dispatch_wave"] = None
            entry["page_probe_history"].append(
                {
                    **probe_link,
                    "status": "TOMBSTONED",
                    "released_at": "2026-07-13T02:03:04Z",
                    "release_reason": "simulated crash after manifest release checkpoint",
                }
            )
            entry["page_probe_receipt"] = None
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            engine_module._event_store(run_dir).append(
                "PAGE_PROBE_TOMBSTONED",
                {
                    **engine_module.receipt_event_payload(probe_receipt),
                    "released_at": "2026-07-13T02:03:04Z",
                    "reason": "simulated crash after manifest release checkpoint",
                },
                task_id=str(task["task_id"]),
            )

            recovered = initialize_orchestration(run_dir)
            self.assertEqual("PENDING", recovered["tasks"][task["task_id"]]["status"])
            events = [
                json.loads(line)
                for line in (run_dir / "orchestration" / "events.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            recovered_events = [
                event
                for event in events
                if event["event_type"] == "AUDIT_CLAIM_RELEASE_RECOVERED"
            ]
            self.assertEqual(1, len(recovered_events))
            self.assertEqual(released_claim, recovered_events[0]["payload"]["claim"])

    def test_reviewer_execution_identity_is_independent_and_guarded(self) -> None:
        def claim(
            task_id: str,
            executor_id: str,
            kind: str = "codebuddy-subagent",
        ) -> AgentClaim:
            return AgentClaim(
                schema_version="1.0.0",
                execution_id=f"EXEC-{task_id}",
                task_id=task_id,
                coordinator_id="COORD-TEST",
                executor_id=executor_id,
                executor_kind=kind,
                wave_id="WAVE-TEST",
                claimed_at="2026-07-13T01:02:03Z",
                source_fingerprint="a" * 64,
                input_snapshot_fingerprint="b" * 64,
                task_packet_fingerprint="c" * 64,
                context_fingerprint="d" * 64,
                page_probe_receipt_id=None,
                page_probe_receipt_fingerprint=None,
                approved_page_mcp_tools=(),
            )

        reviewer = claim("TASK-REVIEW-A01", "REVIEWER-01")
        independent = {
            "tasks": {
                "TASK-DISCOVERY-A01": {
                    "status": "SUCCEEDED",
                    "task": {"agent_role": "discovery"},
                    "claim": claim("TASK-DISCOVERY-A01", "DISCOVERY-01").to_dict(),
                }
            }
        }
        _validate_reviewer_execution_identity(independent, reviewer)

        same_executor = json.loads(json.dumps(independent))
        same_executor["tasks"]["TASK-DISCOVERY-A01"]["claim"] = claim(
            "TASK-DISCOVERY-A01", "REVIEWER-01"
        ).to_dict()
        with self.assertRaisesRegex(OrchestrationError, "must differ"):
            _validate_reviewer_execution_identity(same_executor, reviewer)

        main_generator = json.loads(json.dumps(independent))
        main_generator["tasks"]["TASK-DISCOVERY-A01"]["claim"] = claim(
            "TASK-DISCOVERY-A01", "MAIN-01", "codebuddy-main-session"
        ).to_dict()
        with self.assertRaisesRegex(OrchestrationError, "unauthenticated diagnostic"):
            _validate_reviewer_execution_identity(main_generator, reviewer)

        with self.assertRaisesRegex(OrchestrationError, "authenticated codebuddy-subagent"):
            _validate_reviewer_execution_identity(
                independent,
                claim("TASK-REVIEW-A01", "MAIN-REVIEW", "codebuddy-main-session"),
            )

    def test_review_requires_one_full_claim_and_a_success_commit_event(self) -> None:
        claim = AgentClaim(
            schema_version="1.0.0",
            execution_id="EXEC-TASK-001",
            task_id="TASK-001",
            coordinator_id="COORD-001",
            executor_id="SUBAGENT-001",
            executor_kind="codebuddy-subagent",
            wave_id="WAVE-001",
            claimed_at="2026-07-13T01:02:03Z",
            source_fingerprint="a" * 64,
            input_snapshot_fingerprint="b" * 64,
            task_packet_fingerprint="c" * 64,
            context_fingerprint="d" * 64,
            page_probe_receipt_id=None,
            page_probe_receipt_fingerprint=None,
            approved_page_mcp_tools=(),
        )
        entry = {"result_fingerprint": "e" * 64}
        claim_event = {
            "task_id": "TASK-001",
            "event_type": "TASK_CLAIMED",
            "payload": {"claim": claim.to_dict()},
        }
        stored_event = {
            "task_id": "TASK-001",
            "event_type": "TASK_RESULT_STORED",
            "payload": {"status": "SUCCEEDED", "result_fingerprint": "e" * 64},
        }
        with self.assertRaisesRegex(ReviewValidationError, "no durable success commit"):
            _validate_task_events("TASK-001", entry, claim, [claim_event, stored_event])

        success_event = {
            "task_id": "TASK-001",
            "event_type": "TASK_SUCCEEDED",
            "payload": {"result_fingerprint": "e" * 64},
        }
        _validate_task_events(
            "TASK-001", entry, claim, [claim_event, stored_event, success_event]
        )
        with self.assertRaisesRegex(ReviewValidationError, "full-claim event"):
            _validate_task_events(
                "TASK-001",
                entry,
                claim,
                [claim_event, dict(claim_event), stored_event, success_event],
            )

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
            status = self._submit(run_dir, task, result_path)
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
                self._submit(run_dir, task, result_path)
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
                self._submit(run_dir, task, result_path)
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

    def test_diagnostic_executor_cannot_submit_success_or_promote_formal_files(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            self.helper.create_project_root(root)
            run_dir = self.helper.make_valid_plan_run(root, "diagnostic-no-promote")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            output = self._output_dir(run_dir, task)
            for name in (
                "page-element-inventory.csv",
                "page-discovery.csv",
                "selection-option-observations.csv",
                "interaction-branch-observations.csv",
                "test-data-lifecycle.csv",
            ):
                shutil.copy2(run_dir / name, output / name)
            original = (run_dir / "page-discovery.csv").read_bytes()
            execution_id = self._claim_task(
                run_dir, task, executor_kind="external-session"
            )
            with self.assertRaisesRegex(OrchestrationError, "cannot submit SUCCEEDED"):
                submit_agent_result(
                    run_dir,
                    str(task["task_id"]),
                    self._write_result(
                        run_dir, "diagnostic-success", self._success_result(task)
                    ),
                    execution_id=execution_id,
                )
            self.assertEqual(original, (run_dir / "page-discovery.csv").read_bytes())
            self.assertEqual(
                [], list((run_dir / "orchestration" / "promotions").glob("*/receipt.json"))
            )
            self.assertFalse(
                (run_dir / "orchestration" / "results" / f"{task['task_id']}.json").exists()
            )

    def test_interrupted_claimed_promotion_is_rolled_back_then_same_execution_retries(self) -> None:
        class HardStop(BaseException):
            pass

        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            self.helper.create_project_root(root)
            run_dir = self.helper.make_valid_plan_run(root, "promotion-recovery")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            output = self._output_dir(run_dir, task)
            ledger_names = (
                "page-element-inventory.csv",
                "page-discovery.csv",
                "selection-option-observations.csv",
                "interaction-branch-observations.csv",
                "test-data-lifecycle.csv",
            )
            for name in ledger_names:
                shutil.copy2(run_dir / name, output / name)
            for name in ledger_names[:2]:
                path = output / name
                path.write_bytes(path.read_bytes() + b"\r\n")
            formal_names = (*ledger_names, "batch-status.csv")
            original = {name: (run_dir / name).read_bytes() for name in formal_names}
            execution_id = self._claim_task(run_dir, task)
            result_path = self._write_result(
                run_dir, "interrupted-promotion", self._success_result(task)
            )
            original_copy = workspace_module._atomic_copy
            formal_targets = {(run_dir / name).resolve() for name in formal_names}
            copied = 0

            def hard_stop_copy(source: Path, target: Path) -> None:
                nonlocal copied
                if target.resolve() in formal_targets:
                    copied += 1
                    if copied == 2:
                        raise HardStop("simulated process termination")
                original_copy(source, target)

            with mock.patch.object(workspace_module, "_atomic_copy", side_effect=hard_stop_copy):
                with self.assertRaises(HardStop):
                    submit_agent_result(
                        run_dir,
                        str(task["task_id"]),
                        result_path,
                        execution_id=execution_id,
                    )
            self.assertEqual(
                "APPLYING",
                json.loads(
                    next((run_dir / "orchestration" / "promotions").glob("*/receipt.json")).read_text(
                        encoding="utf-8"
                    )
                )["status"],
            )

            recovered = initialize_orchestration(run_dir)
            self.assertEqual("CLAIMED", recovered["tasks"][task["task_id"]]["status"])
            for name, content in original.items():
                self.assertEqual(content, (run_dir / name).read_bytes())
            receipt_path = next(
                (run_dir / "orchestration" / "promotions").glob("*/receipt.json")
            )
            self.assertEqual(
                "ROLLED_BACK",
                json.loads(receipt_path.read_text(encoding="utf-8"))["status"],
            )
            status = submit_agent_result(
                run_dir,
                str(task["task_id"]),
                result_path,
                execution_id=execution_id,
            )
            self.assertEqual("PLAN_RUNNING", status["state"])
            self.assertEqual(
                "FINALIZED",
                json.loads(receipt_path.read_text(encoding="utf-8"))["status"],
            )

    def test_stale_task_source_is_rejected_before_result_is_stored(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._new_run(Path(value), "stale-agent")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            execution_id = self._claim_task(run_dir, task)
            scope_path = run_dir / "batch-scope.json"
            scope = json.loads(scope_path.read_text(encoding="utf-8"))
            scope_path.write_text(json.dumps(scope, ensure_ascii=False, indent=4), encoding="utf-8")
            result_path = self._write_result(
                run_dir,
                "stale-result",
                self._result(task, "FAILED", "should not be accepted"),
            )
            with self.assertRaisesRegex(OrchestrationError, "source changed"):
                submit_agent_result(
                    run_dir, str(task["task_id"]), result_path, execution_id=execution_id
                )
            self.assertEqual(1, orchestration_status(run_dir)["task_counts"].get("CLAIMED"))
            self.assertFalse((run_dir / "orchestration/results" / f"{task['task_id']}.json").exists())
            with self.assertRaisesRegex(OrchestrationError, "claimed task"):
                advance_orchestration(run_dir)
            release_agent_claim(
                run_dir,
                str(task["task_id"]),
                execution_id=execution_id,
                coordinator_id="COORD-TEST",
                reason="test confirms the task never reached an external executor",
                confirm_no_side_effects=True,
            )
            refreshed = advance_orchestration(run_dir)
            self.assertEqual(1, refreshed["task_counts"].get("INVALIDATED"))
            self.assertEqual("TASK-DISCOVERY-A02", refreshed["runnable_tasks"][0]["task_id"])

    def test_new_catalog_source_invalidates_dispatched_discovery_task(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "late-catalog-source")
            task = advance_orchestration(run_dir)["runnable_tasks"][0]
            execution_id = self._claim_task(run_dir, task)
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
                submit_agent_result(
                    run_dir, str(task["task_id"]), result_path, execution_id=execution_id
                )
            self.assertFalse((run_dir / "orchestration/results" / f"{task['task_id']}.json").exists())

            release_agent_claim(
                run_dir,
                str(task["task_id"]),
                execution_id=execution_id,
                coordinator_id="COORD-TEST",
                reason="test confirms the task never reached an external executor",
                confirm_no_side_effects=True,
            )
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
            status = self._submit(
                run_dir,
                discovery,
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
            blocked = self._submit(run_dir, task, result_path)
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
                self._submit(run_dir, task, result_path)
            self.assertFalse((run_dir / "orchestration/results" / f"{task['task_id']}.json").exists())
            self.assertFalse((run_dir / "orchestration/rework-requests" / f"{request_id}.json").exists())
            self.assertEqual("CLAIMED", json.loads((run_dir / "orchestration/run-manifest.json").read_text(encoding="utf-8"))["tasks"][task["task_id"]]["status"])

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
            historical_execution = self._claim_task(
                run_dir,
                discovery,
                execution_id="EXEC-DISCOVERY-HISTORICAL",
            )
            historical_manifest = json.loads(
                (run_dir / "orchestration" / "run-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            historical_link = historical_manifest["tasks"][discovery["task_id"]][
                "page_probe_receipt"
            ]
            historical_receipt = json.loads(
                (
                    run_dir
                    / "orchestration"
                    / "page-probe-receipts"
                    / f"{historical_link['receipt_id']}.json"
                ).read_text(encoding="utf-8")
            )
            historical_evidence = run_dir / historical_receipt["evidence"][0]["path"]
            release_agent_claim(
                run_dir,
                str(discovery["task_id"]),
                execution_id=historical_execution,
                coordinator_id="COORD-TEST",
                reason="historical executor stopped before product mutation",
                confirm_no_side_effects=True,
            )
            discovery_output = self._output_dir(run_dir, discovery)
            for name in (
                "page-element-inventory.csv",
                "page-discovery.csv",
                "selection-option-observations.csv",
                "interaction-branch-observations.csv",
                "test-data-lifecycle.csv",
            ):
                shutil.copy2(run_dir / name, discovery_output / name)
            status = self._submit(
                run_dir,
                discovery,
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
            status = self._submit(
                run_dir,
                plan,
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
            status = self._submit(
                run_dir,
                worker,
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
            status = self._submit(
                run_dir,
                reviewer,
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
            status = self._submit(
                run_dir,
                replacement,
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
            status = self._submit(
                run_dir,
                reviewer,
                self._write_result(run_dir, "review-success", self._success_result(reviewer)),
            )
            self.assertEqual("DELIVERY_RUNNING", status["state"])
            self.assertEqual(
                ["discovery", "plan", "risk", "cases", "review"],
                status["validated_phases"],
            )
            self.assertTrue((run_dir / "orchestration/review-report.json").is_file())
            self.assertTrue(status["delivery_command"])
            self.assertIn(
                historical_evidence.resolve(),
                set(_review_source_paths(run_dir)),
            )
            changed_audit = json.loads(unreferenced_audit.read_text(encoding="utf-8"))
            changed_audit["notes"] = "已再次逐图核对并形成另一份合法审计记录"
            unreferenced_audit.write_text(
                json.dumps(changed_audit, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(ReviewValidationError, "review_source_fingerprint is stale"):
                validate_review_artifacts(run_dir)
            unreferenced_audit.write_bytes(original_audit)
            self.assertTrue(validate_review_artifacts(run_dir))

            orphan_probe = (
                run_dir
                / "artifacts"
                / "page-probe-evidence"
                / "FAILED-COMMIT"
                / "orphan.png"
            )
            orphan_probe.parent.mkdir(parents=True, exist_ok=True)
            orphan_probe.write_bytes(b"\x89PNG\r\n\x1a\nfailed-probe-commit")
            orphan_probe_audit = self._write_binary_audit(orphan_probe)
            with self.assertRaisesRegex(
                ReviewValidationError,
                "page probe evidence directory differs from registered receipt bindings",
            ):
                validate_review_artifacts(run_dir)
            orphan_probe.unlink()
            orphan_probe_audit.unlink()
            orphan_probe.parent.rmdir()
            self.assertTrue(validate_review_artifacts(run_dir))

            manifest_path = run_dir / "orchestration" / "run-manifest.json"
            manifest_bytes = manifest_path.read_bytes()
            manifest_value = json.loads(manifest_bytes)
            discovery_id = next(
                task_id
                for task_id, entry in manifest_value["tasks"].items()
                if entry["task"]["agent_role"] == "discovery"
                and entry["status"] == "SUCCEEDED"
            )
            discovery_result = run_dir / manifest_value["tasks"][discovery_id]["result_path"]
            discovery_result_bytes = discovery_result.read_bytes()
            discovery_result.write_bytes(discovery_result_bytes + b"\n")
            with self.assertRaisesRegex(ReviewValidationError, "result_fingerprint is stale"):
                validate_review_artifacts(run_dir)
            discovery_result.write_bytes(discovery_result_bytes)

            tampered_manifest = json.loads(manifest_bytes)
            tampered_manifest["tasks"][discovery_id]["claim"]["executor_id"] = (
                "TAMPERED-SUBAGENT"
            )
            manifest_path.write_text(
                json.dumps(tampered_manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReviewValidationError,
                "physical sub-agent execution binding|claim ordering proof",
            ):
                validate_review_artifacts(run_dir)
            manifest_path.write_bytes(manifest_bytes)
            self.assertTrue(validate_review_artifacts(run_dir))
            discovery_execution = manifest_value["tasks"][discovery_id]["claim"][
                "execution_id"
            ]
            discovery_binding = execution_binding_module.execution_binding_path(
                root, discovery_execution
            )
            discovery_binding_bytes = discovery_binding.read_bytes()
            discovery_binding.unlink()
            with self.assertRaisesRegex(
                ReviewValidationError,
                "physical sub-agent execution binding",
            ):
                validate_review_artifacts(run_dir)
            discovery_binding.write_bytes(discovery_binding_bytes)
            self.assertTrue(validate_review_artifacts(run_dir))
            with self.assertRaisesRegex(ValueError, "external --formal-workbook input is not allowed"):
                architecture_safety.TOOLS.complete_deliverables(
                    root,
                    architecture_safety.REPO_ROOT / "docs/test-design/codebuddy-test-design-template.xlsx",
                    architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                    module_path,
                    batch_status=run_dir / "batch-status.csv",
                    batch_id="BATCH-001",
                    product_map=product_map,
                    page_discovery=run_dir / "page-discovery.csv",
                    product_name="产品",
                )

            working = root / "working"
            formal = working / "产品_模块_子模块_页面_测试设计.xlsx"
            imported = working / "产品_模块_子模块_页面_导入用例.xlsx"
            class DeliveryHardStop(BaseException):
                pass

            original_complete_delivery = architecture_safety.TOOLS.complete_delivery_orchestration

            def complete_state_then_hard_stop(path: Path) -> None:
                original_complete_delivery(path)
                raise DeliveryHardStop("simulated process death after orchestration state commit")

            delivery_arguments = {
                "batch_status": run_dir / "batch-status.csv",
                "batch_id": "BATCH-001",
                "product_map": product_map,
                "page_discovery": run_dir / "page-discovery.csv",
                "product_name": "产品",
                "assembly_run_dir": run_dir,
                "formal_template": architecture_safety.REPO_ROOT / "docs/test-design/codebuddy-test-design-template.xlsx",
            }
            with mock.patch.object(
                architecture_safety.TOOLS,
                "complete_delivery_orchestration",
                side_effect=complete_state_then_hard_stop,
            ):
                with self.assertRaises(DeliveryHardStop):
                    architecture_safety.TOOLS.complete_deliverables(
                        root,
                        formal,
                        architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                        module_path,
                        **delivery_arguments,
                    )
            interrupted_journal = json.loads(
                (
                    run_dir
                    / "orchestration/delivery-transaction/journal.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("FINALIZING", interrupted_journal["status"])
            delivery_identity = architecture_safety.TOOLS._delivery_transaction_identity(
                root, run_dir, module_path, "BATCH-001", "产品"
            )
            delivery_transaction_probe = architecture_safety.TOOLS.DurableFileTransaction(
                root,
                run_dir / "orchestration/delivery-transaction",
                delivery_identity,
            )
            illegal_journal = dict(interrupted_journal)
            illegal_journal["status"] = "FILES_COMMITTED"
            delivery_transaction_probe._write(illegal_journal)
            with self.assertRaisesRegex(RuntimeError, "illegal recovery combination"):
                architecture_safety.TOOLS.complete_deliverables(
                    root,
                    formal,
                    architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                    module_path,
                    **delivery_arguments,
                )
            delivery_transaction_probe._write(interrupted_journal)
            counts = architecture_safety.TOOLS.complete_deliverables(
                root,
                formal,
                architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                module_path,
                **delivery_arguments,
            )
            self.assertEqual(5, counts["功能测试用例"])
            replay_formal = working / "replay-formal.xlsx"
            replay_import = working / "replay-import.xlsx"
            with self.assertRaisesRegex(ValueError, "external --import-workbook output"):
                architecture_safety.TOOLS.complete_deliverables(
                    root,
                    replay_formal,
                    architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                    module_path,
                    import_workbook=replay_import,
                    batch_status=run_dir / "batch-status.csv",
                    batch_id="BATCH-001",
                    product_map=product_map,
                    page_discovery=run_dir / "page-discovery.csv",
                    product_name="产品",
                    assembly_run_dir=run_dir,
                    formal_template=architecture_safety.REPO_ROOT / "docs/test-design/codebuddy-test-design-template.xlsx",
                )
            replay_counts = architecture_safety.TOOLS.complete_deliverables(
                root,
                replay_formal,
                architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                module_path,
                **delivery_arguments,
            )
            self.assertEqual(counts, replay_counts)
            self.assertFalse(formal.exists())
            self.assertFalse(imported.exists())
            self.assertFalse(replay_formal.exists())
            self.assertFalse(replay_import.exists())
            delivery_transaction = run_dir / "orchestration/delivery-transaction"
            delivery_journal = json.loads(
                (delivery_transaction / "journal.json").read_text(encoding="utf-8")
            )
            self.assertEqual("FINALIZED", delivery_journal["status"])
            self.assertFalse((delivery_transaction / "backups").exists())
            self.assertFalse((delivery_transaction / "payloads").exists())

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
            formal_book = architecture_safety.load_workbook(published[3], data_only=True)
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
            import_book = architecture_safety.load_workbook(published[4], data_only=True)
            self.assertNotEqual(published[3].resolve(), published[4].resolve())
            self.assertNotIn("测试系统导入用例", formal_book.sheetnames)
            self.assertGreater(len(import_book.sheetnames), 0)

            self.assertEqual(5, len(published))
            self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in published))
            formal_hash = hashlib.sha256(published[3].read_bytes()).hexdigest()
            import_hash = hashlib.sha256(published[4].read_bytes()).hexdigest()
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
            product_map = root / "docs/test-assets/product-map.xlsx"
            product_map.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                architecture_safety.REPO_ROOT / "docs/test-assets/product-map.xlsx",
                product_map,
            )
            with self.assertRaisesRegex(ValueError, "requires state=DELIVERY_RUNNING"):
                architecture_safety.TOOLS.complete_deliverables(
                    root,
                    root / "working" / "formal.xlsx",
                    architecture_safety.REPO_ROOT / "docs/test-design/测试用例模板.xlsx",
                    "产品>模块>页面",
                    batch_status=run_dir / "batch-status.csv",
                    batch_id="BATCH-001",
                    product_map=product_map,
                    page_discovery=run_dir / "page-discovery.csv",
                    product_name="产品",
                    assembly_run_dir=run_dir,
                    formal_template=architecture_safety.REPO_ROOT / "docs/test-design/codebuddy-test-design-template.xlsx",
                )
            self.assertEqual("INIT", orchestration_status(run_dir)["state"])
            self.assertFalse((root / "working" / "formal.xlsx").exists())

    def test_orchestrated_delivery_rejects_unreviewed_fact_source_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "delivery-canonical-sources")
            product_map = root / "docs/test-assets/product-map.xlsx"
            product_map.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                architecture_safety.REPO_ROOT / "docs/test-assets/product-map.xlsx",
                product_map,
            )
            alternate_page = root / "alternate-page-discovery.csv"
            alternate_product_map = root / "docs/test-assets/alternate-product-map.xlsx"
            alternate_status = run_dir / "alternate-batch-status.csv"
            shutil.copy2(run_dir / "page-discovery.csv", alternate_page)
            shutil.copy2(product_map, alternate_product_map)
            shutil.copy2(run_dir / "batch-status.csv", alternate_status)
            base = {
                "batch_status": run_dir / "batch-status.csv",
                "batch_id": "BATCH-001",
                "product_map": product_map,
                "page_discovery": run_dir / "page-discovery.csv",
                "product_name": "产品",
                "assembly_run_dir": run_dir,
                "formal_template": architecture_safety.REPO_ROOT
                / "docs/test-design/codebuddy-test-design-template.xlsx",
            }
            overrides = {
                "page-discovery.csv": {"page_discovery": alternate_page},
                "product-map.xlsx": {"product_map": alternate_product_map},
                "batch-status.csv": {"batch_status": alternate_status},
            }
            for label, override in overrides.items():
                with self.subTest(label=label), self.assertRaisesRegex(
                    ValueError, f"canonical {label}"
                ):
                    architecture_safety.TOOLS.complete_deliverables(
                        root,
                        root / "working/formal.xlsx",
                        architecture_safety.REPO_ROOT
                        / "docs/test-design/测试用例模板.xlsx",
                        "产品>模块>页面",
                        **{**base, **override},
                    )
            self.assertEqual("INIT", orchestration_status(run_dir)["state"])
            self.assertFalse((root / "working/formal.xlsx").exists())

    def test_terminal_delivery_events_recover_after_both_hard_stop_boundaries(self) -> None:
        for cutpoint in ("after_manifest", "after_phase_event"):
            with self.subTest(cutpoint=cutpoint), tempfile.TemporaryDirectory() as value:
                root = Path(value)
                run_dir = self._new_run(root, f"delivery-event-{cutpoint}")
                manifest = initialize_orchestration(run_dir)
                machine = OrchestrationStateMachine()
                delivery_change = None
                for phase in Phase:
                    machine.start_phase(phase)
                    delivery_change = machine.validate_phase(phase)
                assert delivery_change is not None
                completion_change = machine.complete()
                engine_module._save_machine(run_dir, manifest, machine)

                store = engine_module._event_store(run_dir)
                expected = engine_module._delivery_completion_event_payloads(manifest)
                if cutpoint == "after_phase_event":
                    store.append(
                        "PHASE_VALIDATED",
                        {**delivery_change.to_dict(), "closed_rework_request_ids": []},
                        event_id=expected[0][2],
                    )

                initialize_orchestration(run_dir)
                recovered = store.read_events()
                phase_events = [
                    row
                    for row in recovered
                    if row["event_type"] == "PHASE_VALIDATED"
                    and row["payload"].get("phase") == "delivery"
                ]
                completion_events = [
                    row for row in recovered if row["event_type"] == "RUN_COMPLETED"
                ]
                self.assertEqual(1, len(phase_events))
                self.assertEqual(1, len(completion_events))
                self.assertEqual(
                    {**delivery_change.to_dict(), "closed_rework_request_ids": []},
                    phase_events[0]["payload"],
                )
                self.assertEqual(completion_change.to_dict(), completion_events[0]["payload"])
                self.assertEqual(expected[0][2], phase_events[0]["event_id"])
                self.assertEqual(expected[1][2], completion_events[0]["event_id"])
                self.assertLess(phase_events[0]["sequence"], completion_events[0]["sequence"])

                event_count = len(recovered)
                initialize_orchestration(run_dir)
                self.assertEqual(event_count, len(store.read_events()))


if __name__ == "__main__":
    unittest.main()
