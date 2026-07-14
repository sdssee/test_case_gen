# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterable

try:
    from tests import test_architecture_safety as architecture_safety
except ModuleNotFoundError:  # unittest discovery may place tests/ directly on sys.path.
    import test_architecture_safety as architecture_safety

from test_design.orchestration import engine as engine_module
from test_design.orchestration.contracts import PageProbeReceipt
from test_design.orchestration.engine import (
    OrchestrationError,
    advance_orchestration,
    claim_agent_task,
    commit_page_probe_receipt,
    initialize_orchestration,
)
from test_design.orchestration.page_probe import (
    PageProbeError,
    page_probe_event_registry,
)


_RECORDER_PATH = (
    Path(__file__).resolve().parents[1] / ".codebuddy" / "hooks" / "record-page-probe.py"
)
_RECORDER_SPEC = importlib.util.spec_from_file_location(
    "durable_page_probe_receipt_recorder", _RECORDER_PATH
)
assert _RECORDER_SPEC is not None and _RECORDER_SPEC.loader is not None
_RECORDER = importlib.util.module_from_spec(_RECORDER_SPEC)
sys.modules[_RECORDER_SPEC.name] = _RECORDER
_RECORDER_SPEC.loader.exec_module(_RECORDER)


class DurablePageProbeReceiptTests(unittest.TestCase):
    DEFAULT_CALLS = (
        (
            "mcp__page__snapshot",
            {"action": "snapshot", "phase": "before"},
            {"state": "before"},
        ),
        (
            "mcp__page__click",
            {"action": "click", "target": "safe-test-control"},
            {"state": "clicked"},
        ),
        (
            "mcp__page__snapshot",
            {"action": "snapshot", "phase": "after"},
            {"state": "after"},
        ),
    )

    def setUp(self) -> None:
        self.helper = architecture_safety.ArchitectureSafetyTests(methodName="runTest")

    def _prepare_project(self, root: Path) -> None:
        if not (root / "docs" / "test-assets" / "batch-runs" / "templates").is_dir():
            self.helper.create_project_root(root)

    def _new_run(self, root: Path, run_id: str) -> Path:
        self._prepare_project(root)
        return architecture_safety.TOOLS.init_batch_run(
            root,
            run_id,
            "Product>Module>Page",
            f"BATCH-{run_id.upper()}",
            "Product",
        )

    @staticmethod
    def _discovery_task(run_dir: Path) -> dict[str, Any]:
        tasks = advance_orchestration(run_dir)["runnable_tasks"]
        if len(tasks) != 1 or tasks[0]["agent_role"] != "discovery":
            raise AssertionError(f"expected one Discovery task, got {tasks!r}")
        return tasks[0]

    @staticmethod
    def _record_probe(
        root: Path,
        token: str,
        calls: Iterable[tuple[str, dict[str, Any], Any]],
    ) -> dict[str, Any]:
        transcript = root / ".runtime" / f"{token}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            '{"message":{"content":"coordinator page-probe preflight"}}\n',
            encoding="utf-8",
        )
        records: list[dict[str, Any]] = []
        spool_path: Path | None = None
        for tool_name, tool_input, tool_response in calls:
            result = _RECORDER.record_event(
                {
                    "session_id": f"SESSION-{token}",
                    "transcript_path": str(transcript),
                    "cwd": str(root),
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_response": tool_response,
                },
                root,
            )
            if result is None:
                raise AssertionError(f"recorder ignored test MCP tool {tool_name}")
            spool_path, record = result
            records.append(record)
        if not records or spool_path is None:
            raise AssertionError("page-probe recorder produced no records")
        return {
            "records": records,
            "record_ids": [str(record["record_id"]) for record in records],
            "session_sha256": str(records[0]["session_sha256"]),
            "transcript_sha256": str(records[0]["transcript_path_sha256"]),
            "spool_path": spool_path,
        }

    @staticmethod
    def _write_evidence(run_dir: Path, execution_id: str) -> str:
        relative = f"artifacts/page-probe-evidence/{execution_id}/probe.json"
        evidence = run_dir / relative
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            '{"observed":"before-mutation-after","sensitive_data":false}\n',
            encoding="utf-8",
        )
        return relative

    def _commit(
        self,
        run_dir: Path,
        task: dict[str, Any],
        probe: dict[str, Any],
        *,
        execution_id: str,
        coordinator_id: str = "COORD-PROBE",
    ) -> dict[str, Any]:
        evidence = self._write_evidence(run_dir, execution_id)
        return commit_page_probe_receipt(
            run_dir,
            str(task["task_id"]),
            execution_id=execution_id,
            coordinator_id=coordinator_id,
            session_sha256=str(probe["session_sha256"]),
            transcript_sha256=str(probe["transcript_sha256"]),
            record_ids=list(probe["record_ids"]),
            evidence_paths=[evidence],
        )["page_probe_receipt"]

    @staticmethod
    def _claim(
        run_dir: Path,
        task: dict[str, Any],
        receipt: dict[str, Any] | None,
        *,
        execution_id: str,
        coordinator_id: str = "COORD-PROBE",
    ) -> dict[str, Any]:
        receipt_arguments: dict[str, Any] = {}
        if receipt is not None:
            receipt_arguments = {
                "page_probe_receipt_id": receipt["receipt_id"],
                "page_probe_receipt_fingerprint": receipt["receipt_fingerprint"],
            }
        return claim_agent_task(
            run_dir,
            str(task["task_id"]),
            execution_id=execution_id,
            coordinator_id=coordinator_id,
            executor_id=f"EXECUTOR-{execution_id}",
            executor_kind="codebuddy-subagent",
            wave_id=f"WAVE-{execution_id}",
            **receipt_arguments,
        )["claim"]

    @staticmethod
    def _manifest(run_dir: Path) -> tuple[Path, dict[str, Any]]:
        path = run_dir / "orchestration" / "run-manifest.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_commit_and_claim_bind_exact_durable_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-positive")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "positive", self.DEFAULT_CALLS)
            receipt = self._commit(
                run_dir, task, probe, execution_id="EXEC-PROBE-POSITIVE"
            )

            claim = self._claim(
                run_dir,
                task,
                receipt,
                execution_id="EXEC-PROBE-POSITIVE",
            )

            self.assertEqual(receipt["receipt_id"], claim["page_probe_receipt_id"])
            self.assertEqual(
                receipt["receipt_fingerprint"],
                claim["page_probe_receipt_fingerprint"],
            )
            self.assertEqual(
                sorted({call[0] for call in self.DEFAULT_CALLS}),
                claim["approved_page_mcp_tools"],
            )
            _, manifest = self._manifest(run_dir)
            link = manifest["tasks"][task["task_id"]]["page_probe_receipt"]
            self.assertEqual("ACTIVE", link["status"])
            self.assertEqual(receipt["receipt_id"], link["receipt_id"])

    def test_one_receipt_can_bind_every_successfully_probed_exact_tool_on_one_server(self) -> None:
        calls = (
            (
                "mcp__page__snapshot",
                {"action": "snapshot", "phase": "before"},
                {"state": "before"},
            ),
            (
                "mcp__page__click",
                {"action": "click", "target": "safe-reversible-control"},
                {"state": "clicked"},
            ),
            (
                "mcp__page__fill",
                {"action": "fill", "target": "AI_TEST_probe", "value": "AI_TEST"},
                {"state": "filled"},
            ),
            (
                "mcp__page__select",
                {"action": "select", "target": "page-size", "value": "20"},
                {"state": "selected"},
            ),
            (
                "mcp__page__navigate",
                {"action": "navigate", "target": "current-test-page"},
                {"state": "navigated"},
            ),
            (
                "mcp__page__snapshot",
                {"action": "snapshot", "phase": "after"},
                {"state": "after-all-probes"},
            ),
        )
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-multi-tool")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "multi-tool", calls)
            receipt = self._commit(
                run_dir,
                task,
                probe,
                execution_id="EXEC-PROBE-MULTI-TOOL",
            )
            claim = self._claim(
                run_dir,
                task,
                receipt,
                execution_id="EXEC-PROBE-MULTI-TOOL",
            )
            self.assertEqual(
                sorted({tool_name for tool_name, _, _ in calls}),
                claim["approved_page_mcp_tools"],
            )

    def test_discovery_claim_rejects_missing_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-missing")
            task = self._discovery_task(run_dir)
            with self.assertRaisesRegex(
                OrchestrationError,
                "requires a committed page probe receipt",
            ):
                self._claim(
                    run_dir,
                    task,
                    None,
                    execution_id="EXEC-PROBE-MISSING",
                )

    def test_commit_rejects_no_change_read_mutation_read_sequence(self) -> None:
        calls = (
            (
                "mcp__page__snapshot",
                {"action": "snapshot", "phase": "before"},
                {"state": "unchanged"},
            ),
            (
                "mcp__page__click",
                {"action": "click", "target": "safe-test-control"},
                {"state": "clicked"},
            ),
            (
                "mcp__page__snapshot",
                {"action": "snapshot", "phase": "after"},
                {"state": "unchanged"},
            ),
        )
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-no-change")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "no-change", calls)
            with self.assertRaisesRegex(
                OrchestrationError,
                "changed post-read",
            ):
                self._commit(
                    run_dir,
                    task,
                    probe,
                    execution_id="EXEC-PROBE-NO-CHANGE",
                )

    def test_commit_rejects_mixed_mcp_server_namespaces(self) -> None:
        calls = (
            self.DEFAULT_CALLS[0],
            (
                "mcp__foreign__click",
                {"action": "click", "target": "safe-test-control"},
                {"state": "clicked"},
            ),
            self.DEFAULT_CALLS[2],
        )
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-mixed-server")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "mixed-server", calls)
            with self.assertRaisesRegex(
                OrchestrationError,
                "one exact MCP server namespace",
            ):
                self._commit(
                    run_dir,
                    task,
                    probe,
                    execution_id="EXEC-PROBE-MIXED-SERVER",
                )

    def test_commit_rejects_records_older_than_durable_task_creation(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            self._prepare_project(root)
            probe = self._record_probe(root, "old-records", self.DEFAULT_CALLS)
            run_dir = self._new_run(root, "probe-old-records")
            task = self._discovery_task(run_dir)

            with self.assertRaisesRegex(
                OrchestrationError,
                "predates this Discovery task",
            ):
                self._commit(
                    run_dir,
                    task,
                    probe,
                    execution_id="EXEC-PROBE-OLD-RECORDS",
                )

    def test_project_consumption_registry_rejects_cross_run_record_replay(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            first_run = self._new_run(root, "probe-replay-a")
            second_run = self._new_run(root, "probe-replay-b")
            first_task = self._discovery_task(first_run)
            second_task = self._discovery_task(second_run)
            probe = self._record_probe(root, "cross-run-replay", self.DEFAULT_CALLS)

            self._commit(
                first_run,
                first_task,
                probe,
                execution_id="EXEC-PROBE-REPLAY-A",
            )
            with self.assertRaisesRegex(
                OrchestrationError,
                "replay is forbidden|consumed by another run/receipt",
            ):
                self._commit(
                    second_run,
                    second_task,
                    probe,
                    execution_id="EXEC-PROBE-REPLAY-B",
                )

    def test_consumption_marker_tamper_blocks_claim_without_event_write(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-consumption-tamper")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "consumption-tamper", self.DEFAULT_CALLS)
            receipt = self._commit(
                run_dir,
                task,
                probe,
                execution_id="EXEC-PROBE-CONSUMPTION-TAMPER",
            )
            first_record_id = receipt["records"][0]["record_id"]
            marker = (
                root
                / ".test-design-locks"
                / "page-probe-consumption"
                / f"{first_record_id}.json"
            )
            marker_value = json.loads(marker.read_text(encoding="utf-8"))
            marker_value["coordinator_id"] = "COORD-TAMPERED"
            marker.write_text(
                json.dumps(marker_value, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            events_path = run_dir / "orchestration" / "events.jsonl"
            events_before = events_path.read_bytes()

            with self.assertRaisesRegex(OrchestrationError, "consumption"):
                self._claim(
                    run_dir,
                    task,
                    receipt,
                    execution_id="EXEC-PROBE-CONSUMPTION-TAMPER",
                )
            self.assertEqual(events_before, events_path.read_bytes())

    def test_release_manifest_history_recovers_missing_tombstone_in_one_pass(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-release-recovery")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "release-recovery", self.DEFAULT_CALLS)
            receipt = self._commit(
                run_dir,
                task,
                probe,
                execution_id="EXEC-PROBE-RELEASE-RECOVERY",
            )
            self._claim(
                run_dir,
                task,
                receipt,
                execution_id="EXEC-PROBE-RELEASE-RECOVERY",
            )

            manifest_path, manifest = self._manifest(run_dir)
            entry = manifest["tasks"][task["task_id"]]
            released_claim = copy.deepcopy(entry["claim"])
            active_link = copy.deepcopy(entry["page_probe_receipt"])
            released_at = "2026-07-13T02:03:04Z"
            reason = "simulated crash after durable manifest release checkpoint"
            entry["claim_history"].append(
                {
                    "claim": released_claim,
                    "released_at": released_at,
                    "reason": reason,
                    "no_side_effects_confirmed": True,
                }
            )
            entry["claim"] = None
            entry["status"] = "PENDING"
            entry["dispatch_wave"] = None
            entry["page_probe_history"].append(
                {
                    **active_link,
                    "status": "TOMBSTONED",
                    "released_at": released_at,
                    "release_reason": reason,
                }
            )
            entry["page_probe_receipt"] = None
            self._write_manifest(manifest_path, manifest)

            recovered = initialize_orchestration(run_dir)
            recovered_entry = recovered["tasks"][task["task_id"]]
            self.assertEqual("PENDING", recovered_entry["status"])
            self.assertIsNone(recovered_entry["page_probe_receipt"])
            self.assertEqual(1, len(recovered_entry["page_probe_history"]))
            events_path = run_dir / "orchestration" / "events.jsonl"
            events = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
            ]
            recovered_tombstones = [
                event
                for event in events
                if event["event_type"] == "AUDIT_PAGE_PROBE_TOMBSTONED"
                and event["task_id"] == task["task_id"]
            ]
            self.assertEqual(1, len(recovered_tombstones))
            registry = page_probe_event_registry(events)
            self.assertIsNotNone(registry[receipt["receipt_id"]]["tombstoned_sequence"])

            stable_events = events_path.read_bytes()
            initialize_orchestration(run_dir)
            self.assertEqual(stable_events, events_path.read_bytes())

    def test_duplicate_history_fails_before_any_event_append(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-duplicate-history")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "duplicate-history", self.DEFAULT_CALLS)
            self._commit(
                run_dir,
                task,
                probe,
                execution_id="EXEC-PROBE-DUPLICATE-HISTORY",
            )
            manifest_path, manifest = self._manifest(run_dir)
            entry = manifest["tasks"][task["task_id"]]
            tombstone = {
                **copy.deepcopy(entry["page_probe_receipt"]),
                "status": "TOMBSTONED",
            }
            entry["page_probe_receipt"] = None
            entry["page_probe_history"] = [copy.deepcopy(tombstone), copy.deepcopy(tombstone)]
            self._write_manifest(manifest_path, manifest)
            events_path = run_dir / "orchestration" / "events.jsonl"
            events_before = events_path.read_bytes()

            with self.assertRaisesRegex(OrchestrationError, "duplicates receipt"):
                initialize_orchestration(run_dir)
            self.assertEqual(events_before, events_path.read_bytes())

    def test_stray_current_link_fails_before_any_event_append(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-stray-link")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "stray-link", self.DEFAULT_CALLS)
            self._commit(
                run_dir,
                task,
                probe,
                execution_id="EXEC-PROBE-STRAY-LINK",
            )
            manifest_path, manifest = self._manifest(run_dir)
            link = manifest["tasks"][task["task_id"]]["page_probe_receipt"]
            link["receipt_id"] = "PPR-ffffffffffffffffffffffff"
            self._write_manifest(manifest_path, manifest)
            events_path = run_dir / "orchestration" / "events.jsonl"
            events_before = events_path.read_bytes()

            with self.assertRaisesRegex(OrchestrationError, "no reservation event"):
                initialize_orchestration(run_dir)
            self.assertEqual(events_before, events_path.read_bytes())

    def test_page_probe_event_envelope_task_id_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-event-envelope")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "event-envelope", self.DEFAULT_CALLS)
            self._commit(
                run_dir,
                task,
                probe,
                execution_id="EXEC-PROBE-EVENT-ENVELOPE",
            )
            events = engine_module._event_store(run_dir).read_events()
            tampered = copy.deepcopy(events)
            reservation = next(
                event
                for event in tampered
                if event["event_type"] == "PAGE_PROBE_RECORDS_RESERVED"
            )
            reservation["task_id"] = "TASK-DISCOVERY-FOREIGN"

            with self.assertRaisesRegex(PageProbeError, "event task_id mismatch"):
                page_probe_event_registry(tampered)

    def test_receipt_rejects_operation_kind_name_contradiction(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir = self._new_run(root, "probe-operation-contract")
            task = self._discovery_task(run_dir)
            probe = self._record_probe(root, "operation-contract", self.DEFAULT_CALLS)
            receipt = self._commit(
                run_dir,
                task,
                probe,
                execution_id="EXEC-PROBE-OPERATION-CONTRACT",
            )
            contradictory = copy.deepcopy(receipt)
            contradictory["records"][0]["operation_name"] = "click"

            with self.assertRaisesRegex(ValueError, "operation_kind/name"):
                PageProbeReceipt.from_dict(contradictory)


if __name__ == "__main__":
    unittest.main()
