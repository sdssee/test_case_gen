# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from test_design.orchestration import workspace as workspace_module
from test_design.orchestration.contracts import (
    AgentResult,
    AgentRole,
    AgentTask,
    ReworkReason,
    ReworkRequest,
    ReworkTarget,
    RunConfig,
    SCHEMA_VERSION,
    TaskStatus,
    TraceabilityRecord,
    canonical_fingerprint,
)
from test_design.orchestration.event_store import (
    EventStore,
    EventStoreError,
    _exclusive_lock,
)
from test_design.orchestration.state_machine import (
    PHASE_ORDER,
    OrchestrationStateMachine,
    Phase,
    StateTransitionError,
)
from test_design.orchestration.workspace import (
    WorkspaceError,
    WorkspaceManager,
    sha256_file,
)


FINGERPRINT = "a" * 64
EVIDENCE_HASH = "b" * 64


def _task_value() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": "TASK-CASE-PAGING",
        "run_id": "RUN-001",
        "batch_id": "BATCH-001",
        "phase": "cases",
        "agent_role": "case_worker",
        "owner_key": "paging-page-size",
        "input_files": [
            "batch-scope.json",
            "artifacts/data/element-case-plan.csv",
        ],
        "allowed_output_files": [
            "artifacts/agent-work/case_worker/TASK-CASE-PAGING/function_cases.json",
            "artifacts/agent-work/case_worker/TASK-CASE-PAGING/case-traceability.json",
        ],
        "allowed_output_prefixes": [
            "artifacts/agent-work/case_worker/TASK-CASE-PAGING/evidence/"
        ],
        "required_gate": "cases-worker",
        "source_fingerprint": FINGERPRINT,
        "attempt": 1,
    }


def _rework_value() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": "REWORK-001",
        "run_id": "RUN-001",
        "batch_id": "BATCH-001",
        "target_phase": "cases",
        "target_task_id": "TASK-CASE-PAGING",
        "reason_code": "DUPLICATE_EXPECTED_RESULT",
        "affected_ids": ["TC-PAGING-003"],
        "evidence": ["artifacts/evidence/paging/page-size-20.png"],
        "required_action": "Rewrite only the affected expected result.",
        "source_fingerprint": FINGERPRINT,
        "attempt": 1,
    }


def _result_value() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": "TASK-CASE-PAGING",
        "agent_role": "case_worker",
        "status": "SUCCEEDED",
        "source_fingerprint": FINGERPRINT,
        "produced_files": [
            "artifacts/agent-work/case_worker/TASK-CASE-PAGING/function_cases.json"
        ],
        "affected_interaction_ids": ["INT-PAGING-SIZE-20"],
        "affected_case_ids": ["TC-PAGING-003"],
        "facts_used": ["PLAN-PAGING-SIZE"],
        "gate_summary": {"cases-worker": True},
        "rework_requests": [],
        "error_message": None,
    }


class OrchestrationContractTests(unittest.TestCase):
    def test_all_contracts_have_strict_lossless_round_trips(self) -> None:
        task = AgentTask.from_dict(_task_value())
        self.assertEqual(_task_value(), task.to_dict())
        self.assertEqual(task, AgentTask.from_dict(task.to_dict()))

        rework = ReworkRequest.from_dict(_rework_value())
        self.assertEqual(_rework_value(), rework.to_dict())
        self.assertEqual(rework, ReworkRequest.from_dict(rework.to_dict()))

        result = AgentResult.from_dict(_result_value())
        self.assertEqual(_result_value(), result.to_dict())
        self.assertEqual(result, AgentResult.from_dict(result.to_dict()))

        trace_value = {
            "schema_version": SCHEMA_VERSION,
            "case_id": "TC-PAGING-003",
            "function_point": "Paging - page size",
            "plan_owner_id": "PLAN-PAGING-SIZE",
            "interaction_ids": ["INT-PAGING-SIZE-20"],
            "selection_observation_ids": ["OPT-PAGING-SIZE-20"],
            "lifecycle_ids": [],
            "evidence_hashes": [EVIDENCE_HASH],
            "worker_task_id": "TASK-CASE-PAGING",
            "source_fingerprint": FINGERPRINT,
        }
        trace = TraceabilityRecord.from_dict(trace_value)
        self.assertEqual(trace_value, trace.to_dict())
        self.assertEqual(trace, TraceabilityRecord.from_dict(trace.to_dict()))

        config_value = {
            "schema_version": SCHEMA_VERSION,
            "run_id": "RUN-001",
            "batch_id": "BATCH-001",
            "agent_mode": "required",
            "parallel_discovery": False,
            "case_parallel_threshold": 3,
            "max_case_workers": 3,
            "max_rework_attempts": 2,
            "review_required": True,
            "delivery_single_writer": True,
            "source_fingerprint": FINGERPRINT,
        }
        config = RunConfig.from_dict(config_value)
        self.assertEqual(config_value, config.to_dict())
        self.assertEqual(config, RunConfig.from_dict(config.to_dict()))

    def test_contracts_reject_missing_unknown_and_empty_required_fields(self) -> None:
        missing = _task_value()
        missing.pop("task_id")
        with self.assertRaisesRegex(ValueError, "missing"):
            AgentTask.from_dict(missing)

        unknown = _task_value()
        unknown["model_notes"] = "must not cross the contract boundary"
        with self.assertRaisesRegex(ValueError, "unknown"):
            AgentTask.from_dict(unknown)

        for field, value in (
            ("task_id", ""),
            ("run_id", " "),
            ("allowed_output_files", []),
            ("source_fingerprint", ""),
            ("attempt", 0),
        ):
            with self.subTest(field=field):
                invalid = _task_value()
                invalid[field] = value
                with self.assertRaises((TypeError, ValueError)):
                    AgentTask.from_dict(invalid)

    def test_task_contract_rejects_path_escape_and_non_whitelisted_inputs(self) -> None:
        invalid_inputs = (
            "../batch-scope.json",
            "artifacts/data/../../secret.txt",
            "artifacts\\data\\plan.csv",
            "/etc/passwd",
            "C:/Windows/System32/config",
            "docs/test-assets/catalog/index.json",
        )
        for path in invalid_inputs:
            with self.subTest(path=path):
                invalid = _task_value()
                invalid["input_files"] = [path]
                with self.assertRaises(ValueError):
                    AgentTask.from_dict(invalid)

        invalid_outputs = (
            "../function_cases.json",
            "artifacts/agent-work/case_worker/OTHER-TASK/function_cases.json",
            "artifacts/agent-work/reviewer/TASK-CASE-PAGING/report.json",
            "artifacts/data/function_cases.json",
        )
        for path in invalid_outputs:
            with self.subTest(path=path):
                invalid = _task_value()
                invalid["allowed_output_files"] = [path]
                with self.assertRaises(ValueError):
                    AgentTask.from_dict(invalid)

        invalid_prefixes = (
            "artifacts/agent-work/case_worker/TASK-CASE-PAGING/evidence",
            "artifacts/agent-work/case_worker/OTHER-TASK/evidence/",
            "artifacts/agent-work/case_worker/TASK-CASE-PAGING/../evidence/",
            "artifacts/data/evidence/",
        )
        for path in invalid_prefixes:
            with self.subTest(prefix=path):
                invalid = _task_value()
                invalid["allowed_output_prefixes"] = [path]
                with self.assertRaises(ValueError):
                    AgentTask.from_dict(invalid)

    def test_task_contract_rejects_role_phase_and_case_owner_mismatch(self) -> None:
        wrong_phase = _task_value()
        wrong_phase["phase"] = "plan"
        with self.assertRaisesRegex(ValueError, "phase"):
            AgentTask.from_dict(wrong_phase)

        missing_owner = _task_value()
        missing_owner["owner_key"] = None
        with self.assertRaisesRegex(ValueError, "owner_key"):
            AgentTask.from_dict(missing_owner)

        invalid_gate = _task_value()
        invalid_gate["required_gate"] = "model-approved"
        with self.assertRaisesRegex(ValueError, "required_gate"):
            AgentTask.from_dict(invalid_gate)

    def test_result_contract_rejects_empty_or_contradictory_statuses(self) -> None:
        invalid_status = _result_value()
        invalid_status["status"] = ""
        with self.assertRaises(ValueError):
            AgentResult.from_dict(invalid_status)

        succeeded_with_error = _result_value()
        succeeded_with_error["error_message"] = "unexpected partial failure"
        with self.assertRaisesRegex(ValueError, "SUCCEEDED"):
            AgentResult.from_dict(succeeded_with_error)

        needs_rework_without_request = _result_value()
        needs_rework_without_request["status"] = "NEEDS_REWORK"
        with self.assertRaisesRegex(ValueError, "at least one rework"):
            AgentResult.from_dict(needs_rework_without_request)

        failed_without_error = _result_value()
        failed_without_error["status"] = "FAILED"
        with self.assertRaisesRegex(ValueError, "error_message"):
            AgentResult.from_dict(failed_without_error)

        non_boolean_gate = _result_value()
        non_boolean_gate["gate_summary"] = {"cases-worker": 1}
        with self.assertRaises(TypeError):
            AgentResult.from_dict(non_boolean_gate)

    def test_result_rework_must_be_unique_and_match_source(self) -> None:
        needs_rework = _result_value()
        needs_rework["status"] = "NEEDS_REWORK"
        needs_rework["rework_requests"] = [_rework_value()]
        self.assertEqual(
            TaskStatus.NEEDS_REWORK,
            AgentResult.from_dict(needs_rework).status,
        )

        duplicate = dict(needs_rework)
        duplicate["rework_requests"] = [_rework_value(), _rework_value()]
        with self.assertRaisesRegex(ValueError, "duplicate request_id"):
            AgentResult.from_dict(duplicate)

        mismatched_request = _rework_value()
        mismatched_request["source_fingerprint"] = "c" * 64
        mismatch = dict(needs_rework)
        mismatch["rework_requests"] = [mismatched_request]
        with self.assertRaisesRegex(ValueError, "source_fingerprint"):
            AgentResult.from_dict(mismatch)

    def test_final_run_configuration_rejects_unsafe_modes(self) -> None:
        base = {
            "schema_version": SCHEMA_VERSION,
            "run_id": "RUN-001",
            "batch_id": "BATCH-001",
            "agent_mode": "required",
            "parallel_discovery": False,
            "case_parallel_threshold": 3,
            "max_case_workers": 3,
            "max_rework_attempts": 2,
            "review_required": True,
            "delivery_single_writer": True,
            "source_fingerprint": FINGERPRINT,
        }
        for field, value in (
            ("agent_mode", "preview"),
            ("parallel_discovery", True),
            ("review_required", False),
            ("delivery_single_writer", False),
            ("max_case_workers", 0),
            ("max_rework_attempts", 11),
        ):
            with self.subTest(field=field):
                invalid = {**base, field: value}
                with self.assertRaises((TypeError, ValueError)):
                    RunConfig.from_dict(invalid)

    def test_canonical_fingerprint_is_deterministic_and_strict_json(self) -> None:
        first = canonical_fingerprint({"b": [2, 1], "a": "告警"})
        second = canonical_fingerprint({"a": "告警", "b": [2, 1]})
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        with self.assertRaises((TypeError, ValueError)):
            canonical_fingerprint({"bad": float("nan")})


class OrchestrationStateMachineTests(unittest.TestCase):
    def test_phases_advance_in_exact_order_and_review_precedes_delivery(self) -> None:
        machine = OrchestrationStateMachine()
        for phase in PHASE_ORDER:
            self.assertEqual(phase, machine.next_phase)
            if phase is Phase.REVIEW:
                with self.assertRaisesRegex(StateTransitionError, "expected next phase is review"):
                    machine.start_phase(Phase.DELIVERY)
            start = machine.start_phase(phase)
            self.assertEqual(f"{phase.value.upper()}_RUNNING", start.state)
            with self.assertRaises(StateTransitionError):
                machine.start_phase(phase)
            validated = machine.validate_phase(phase)
            self.assertEqual(f"{phase.value.upper()}_VALIDATED", validated.state)
        completed = machine.complete()
        self.assertEqual("COMPLETE", completed.state)
        self.assertTrue(machine.is_terminal)
        self.assertEqual(PHASE_ORDER, machine.validated_phases)

    def test_delivery_cannot_start_or_complete_before_review_gate(self) -> None:
        machine = OrchestrationStateMachine()
        for phase in PHASE_ORDER[:4]:
            machine.start_phase(phase)
            machine.validate_phase(phase)
        self.assertEqual("CASES_VALIDATED", machine.state)
        with self.assertRaises(StateTransitionError):
            machine.start_phase(Phase.DELIVERY)
        with self.assertRaisesRegex(StateTransitionError, "DELIVERY_VALIDATED"):
            machine.complete()

    def test_rework_invalidates_target_and_all_downstream_phases(self) -> None:
        machine = OrchestrationStateMachine()
        for phase in PHASE_ORDER[:5]:
            machine.start_phase(phase)
            machine.validate_phase(phase)
        change = machine.request_rework(Phase.CASES, "Review found function-point drift")
        self.assertEqual("CASES_RUNNING", machine.state)
        self.assertEqual(Phase.CASES, machine.active_phase)
        self.assertEqual(PHASE_ORDER[:3], machine.validated_phases)
        self.assertEqual(("cases", "review", "delivery"), change.invalidated_phases)
        machine.validate_phase(Phase.CASES)
        self.assertEqual(Phase.REVIEW, machine.next_phase)
        with self.assertRaisesRegex(StateTransitionError, "forward rework"):
            machine.request_rework(Phase.DELIVERY, "Cannot rework an unreached phase")

    def test_external_block_resume_and_checkpoint_round_trip(self) -> None:
        machine = OrchestrationStateMachine()
        machine.start_phase(Phase.DISCOVERY)
        machine.validate_phase(Phase.DISCOVERY)
        machine.start_phase(Phase.PLAN)
        machine.block_external("External product semantics are required")
        restored = OrchestrationStateMachine.from_dict(machine.to_dict())
        self.assertEqual(machine.to_dict(), restored.to_dict())
        self.assertEqual("EXTERNAL_BLOCKED", restored.state)
        self.assertIsNone(restored.next_phase)
        restored.resume_external()
        self.assertEqual("PLAN_RUNNING", restored.state)
        restored.validate_phase(Phase.PLAN)

    def test_restore_rejects_empty_unknown_or_inconsistent_state(self) -> None:
        base = OrchestrationStateMachine().to_dict()
        for mutation in (
            {"state": ""},
            {"state": "MODEL_APPROVED"},
            {"revision": True},
            {
                "state": "DELIVERY_VALIDATED",
                "validated_phases": ["discovery", "plan", "risk", "cases"],
            },
            {"state": "DISCOVERY_RUNNING", "active_phase": None},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(StateTransitionError):
                    OrchestrationStateMachine.from_dict({**base, **mutation})

    def test_invalid_transitions_and_blank_reasons_fail_closed(self) -> None:
        machine = OrchestrationStateMachine()
        with self.assertRaises(StateTransitionError):
            machine.start_phase(Phase.PLAN)
        with self.assertRaises(StateTransitionError):
            machine.validate_phase(Phase.DISCOVERY)
        with self.assertRaises(StateTransitionError):
            machine.request_rework(Phase.DISCOVERY, "because")
        machine.start_phase(Phase.DISCOVERY)
        with self.assertRaisesRegex(StateTransitionError, "non-empty"):
            machine.request_rework(Phase.DISCOVERY, " ")
        with self.assertRaisesRegex(StateTransitionError, "non-empty"):
            machine.fail("")


class OrchestrationEventStoreTests(unittest.TestCase):
    def test_append_read_filter_and_hash_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "orchestration" / "events.jsonl")
            first = store.append(
                "PHASE_STARTED",
                {"phase": "discovery"},
                event_id="EVT-001",
                task_id="TASK-DISCOVERY",
                occurred_at="2026-07-13T00:00:00.000Z",
            )
            second = store.append(
                "PHASE_VALIDATED",
                {"phase": "discovery"},
                event_id="EVT-002",
                task_id="TASK-DISCOVERY",
                occurred_at="2026-07-13T00:00:01.000Z",
            )
            self.assertEqual(EventStore.GENESIS_HASH, first["previous_hash"])
            self.assertEqual(first["event_hash"], second["previous_hash"])
            self.assertEqual([1, 2], [item["sequence"] for item in store.read_events()])
            self.assertEqual(
                ["EVT-002"],
                [item["event_id"] for item in store.read_events(after_sequence=1)],
            )
            self.assertEqual(2, store.verify()["event_count"])

    def test_duplicate_event_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "events.jsonl")
            store.append("RUN_STARTED", event_id="EVT-SAME")
            with self.assertRaisesRegex(EventStoreError, "duplicate event_id"):
                store.append("RUN_RESUMED", event_id="EVT-SAME")
            self.assertEqual(1, store.verify()["event_count"])

    def test_conflicting_writer_lock_fails_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "events.jsonl")
            with _exclusive_lock(store.lock_path):
                with self.assertRaisesRegex(EventStoreError, "holds lock"):
                    store.append("CONFLICTING_WRITE", event_id="EVT-CONFLICT")
            self.assertEqual(0, store.verify()["event_count"])

    def test_content_tampering_and_truncated_jsonl_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            store = EventStore(path)
            store.append("RUN_STARTED", {"mode": "required"}, event_id="EVT-001")
            value = json.loads(path.read_text(encoding="utf-8"))
            value["payload"]["mode"] = "off"
            path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(EventStoreError, "content hash mismatch"):
                store.verify()

            path.write_text('{"schema_version":1', encoding="utf-8")
            with self.assertRaisesRegex(EventStoreError, "invalid JSONL"):
                store.read_events()

    def test_concurrent_appends_are_serialized_without_lost_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = EventStore(Path(temporary) / "events.jsonl")

            def append(index: int) -> dict[str, object]:
                for _ in range(200):
                    try:
                        return store.append(
                            "WORKER_REPORTED",
                            {"index": index},
                            actor=f"worker-{index % 3}",
                            event_id=f"EVT-{index:03d}",
                        )
                    except EventStoreError as exc:
                        if "holds lock" not in str(exc):
                            raise
                        time.sleep(0.002)
                self.fail("event append did not acquire the writer lock after retries")

            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(append, range(12)))
            self.assertEqual(12, len(results))
            events = store.read_events()
            self.assertEqual(list(range(1, 13)), [event["sequence"] for event in events])
            self.assertEqual(12, len({event["event_id"] for event in events}))
            self.assertEqual(12, store.verify()["event_count"])


class OrchestrationWorkspaceTests(unittest.TestCase):
    def test_workspace_rejects_components_and_output_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = WorkspaceManager(Path(temporary) / "run")
            manager.create_task_workspace("case_worker", "TASK-001")
            for role, task_id in (
                ("../reviewer", "TASK-001"),
                ("case_worker", "../TASK-001"),
                ("case/worker", "TASK-001"),
            ):
                with self.subTest(role=role, task_id=task_id):
                    with self.assertRaises(WorkspaceError):
                        manager.task_workspace(role, task_id)
            for path in ("../escape.json", "nested/../../escape.json"):
                with self.subTest(path=path):
                    with self.assertRaisesRegex(WorkspaceError, "escapes allowed root"):
                        manager.resolve_task_output("case_worker", "TASK-001", path)

    def test_workspace_rejects_symlink_escape_and_symlink_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = WorkspaceManager(root / "run")
            manager.create_task_workspace("case_worker", "TASK-001")
            output = manager.task_output_root("case_worker", "TASK-001")
            outside = root / "outside"
            outside.mkdir()
            directory_link = output / "outside-link"
            file_link = output / "file-link.json"
            outside_file = outside / "outside.json"
            outside_file.write_text("outside", encoding="utf-8")
            try:
                directory_link.symlink_to(outside, target_is_directory=True)
                file_link.symlink_to(outside_file)
            except OSError as exc:
                self.skipTest(f"symbolic links are unavailable in this environment: {exc}")
            with self.assertRaisesRegex(WorkspaceError, "escapes allowed root"):
                manager.resolve_task_output(
                    "case_worker", "TASK-001", "outside-link/new.json"
                )
            with self.assertRaisesRegex(WorkspaceError, "non-symlink"):
                manager.output_manifest("case_worker", "TASK-001")

    def test_manifest_and_fingerprints_are_content_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = WorkspaceManager(Path(temporary) / "run")
            manager.create_task_workspace("case_worker", "TASK-001")
            output = manager.task_output_root("case_worker", "TASK-001")
            first = output / "a.json"
            second = output / "nested" / "b.json"
            second.parent.mkdir()
            first.write_bytes(b"abc")
            second.write_bytes(b"def")
            self.assertEqual(hashlib.sha256(b"abc").hexdigest(), sha256_file(first))
            manifest = manager.output_manifest("case_worker", "TASK-001")
            self.assertEqual(["a.json", "nested/b.json"], [item["path"] for item in manifest])
            before = manager.fingerprint_outputs("case_worker", "TASK-001")
            second.write_bytes(b"changed")
            after = manager.fingerprint_outputs("case_worker", "TASK-001")
            self.assertNotEqual(before, after)

    def test_atomic_promotion_records_hashes_and_rollback_restores_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = WorkspaceManager(Path(temporary) / "run")
            manager.create_task_workspace("case_worker", "TASK-001")
            output = manager.task_output_root("case_worker", "TASK-001")
            (output / "first.json").write_text("new-first", encoding="utf-8")
            (output / "nested").mkdir()
            (output / "nested" / "second.json").write_text("new-second", encoding="utf-8")
            formal = manager.formal_data_root
            formal.mkdir(parents=True, exist_ok=True)
            (formal / "first.json").write_text("old-first", encoding="utf-8")

            receipt = manager.atomic_promote(
                "case_worker",
                "TASK-001",
                {
                    "first.json": "first.json",
                    "nested/second.json": "nested/second.json",
                },
            )
            self.assertEqual("PROMOTED", receipt.status)
            self.assertEqual("new-first", (formal / "first.json").read_text(encoding="utf-8"))
            self.assertEqual("new-second", (formal / "nested" / "second.json").read_text(encoding="utf-8"))
            self.assertTrue((manager.run_dir / receipt.receipt_path).is_file())
            for record in receipt.files:
                target = manager.run_dir / str(record["target"])
                self.assertEqual(record["promoted_sha256"], sha256_file(target))

            rolled_back = manager.rollback_promotion(receipt)
            self.assertEqual("ROLLED_BACK", rolled_back["status"])
            self.assertEqual("old-first", (formal / "first.json").read_text(encoding="utf-8"))
            self.assertFalse((formal / "nested" / "second.json").exists())

    def test_promotion_failure_rolls_back_every_already_replaced_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = WorkspaceManager(Path(temporary) / "run")
            manager.create_task_workspace("case_worker", "TASK-001")
            output = manager.task_output_root("case_worker", "TASK-001")
            (output / "first.json").write_text("new-first", encoding="utf-8")
            (output / "second.json").write_text("new-second", encoding="utf-8")
            formal = manager.formal_data_root
            formal.mkdir(parents=True, exist_ok=True)
            first_target = formal / "first.json"
            second_target = formal / "second.json"
            first_target.write_text("old-first", encoding="utf-8")
            second_target.write_text("old-second", encoding="utf-8")

            original_replace = workspace_module.os.replace
            failure_injected = False

            def flaky_replace(source: Path | str, destination: Path | str) -> None:
                nonlocal failure_injected
                if Path(destination) == second_target and not failure_injected:
                    failure_injected = True
                    raise OSError("injected second-file replacement failure")
                original_replace(source, destination)

            with mock.patch.object(
                workspace_module.os, "replace", side_effect=flaky_replace
            ):
                with self.assertRaisesRegex(OSError, "injected"):
                    manager.atomic_promote(
                        "case_worker",
                        "TASK-001",
                        {"first.json": "first.json", "second.json": "second.json"},
                    )
            self.assertTrue(failure_injected)
            self.assertEqual("old-first", first_target.read_text(encoding="utf-8"))
            self.assertEqual("old-second", second_target.read_text(encoding="utf-8"))

    def test_rollback_refuses_to_overwrite_newer_formal_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = WorkspaceManager(Path(temporary) / "run")
            manager.create_task_workspace("case_worker", "TASK-001")
            output = manager.task_output_root("case_worker", "TASK-001")
            (output / "case.json").write_text("promoted", encoding="utf-8")
            receipt = manager.atomic_promote(
                "case_worker", "TASK-001", {"case.json": "case.json"}
            )
            target = manager.formal_data_root / "case.json"
            target.write_text("newer-transaction", encoding="utf-8")
            with self.assertRaisesRegex(WorkspaceError, "has drifted"):
                manager.rollback_promotion(receipt)
            self.assertEqual("newer-transaction", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
