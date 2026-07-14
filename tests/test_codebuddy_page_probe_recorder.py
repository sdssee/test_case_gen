from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORDER_PATH = ROOT / ".codebuddy" / "hooks" / "record-page-probe.py"
SCHEMA_PATH = (
    ROOT
    / "docs"
    / "test-design"
    / "schemas"
    / "orchestration"
    / "page-probe-hook-record.schema.json"
)
SPEC = importlib.util.spec_from_file_location("codebuddy_page_probe_recorder", RECORDER_PATH)
assert SPEC is not None and SPEC.loader is not None
RECORDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RECORDER
SPEC.loader.exec_module(RECORDER)


class CodeBuddyPageProbeRecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.transcript = self.root / ".runtime" / "session.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text('{"message":{"content":"probe"}}\n', encoding="utf-8")

    def payload(
        self,
        *,
        tool_name: str = "mcp__page__control",
        tool_input: object | None = None,
        tool_response: object | None = None,
        session_id: str = "session-fixed",
    ) -> dict[str, object]:
        return {
            "session_id": session_id,
            "transcript_path": str(self.transcript),
            "cwd": str(self.root),
            "tool_name": tool_name,
            "tool_input": {"action": "snapshot"} if tool_input is None else tool_input,
            "tool_response": {"page": "before"} if tool_response is None else tool_response,
        }

    @staticmethod
    def read_records(path: Path) -> list[dict[str, object]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_direct_mcp_record_is_strict_hashed_and_does_not_persist_raw_content(self) -> None:
        secret = "raw-page-value-must-not-be-persisted"
        result = RECORDER.record_event(
            self.payload(
                tool_input={"action": "snapshot", "selector": secret},
                tool_response={"page": secret},
            ),
            self.root,
        )
        self.assertIsNotNone(result)
        path, record = result
        self.assertTrue(path.is_file())
        self.assertIn(".test-design-locks/page-probe-spool", path.as_posix())
        self.assertNotIn(secret, path.read_text(encoding="utf-8"))
        self.assertEqual(set(RECORDER._RECORD_FIELDS), set(record))
        self.assertEqual("read", record["operation_kind"])
        self.assertEqual("read", record["operation_name"])
        self.assertTrue(record["response_nonempty"])
        self.assertFalse(record["response_error"])
        expected = hashlib.sha256(
            RECORDER._canonical_bytes(
                {key: value for key, value in record.items() if key != "record_id"}
            )
        ).hexdigest()
        self.assertEqual(expected, record["record_id"])

    def test_records_form_an_ordered_hash_chain_and_capture_changed_response(self) -> None:
        first_path, first = RECORDER.record_event(self.payload(), self.root)
        second_path, second = RECORDER.record_event(
            self.payload(
                tool_input={"action": "click", "selector": "alarm-row"},
                tool_response={"page": "after click"},
            ),
            self.root,
        )
        self.assertEqual(first_path, second_path)
        self.assertEqual(1, first["sequence"])
        self.assertIsNone(first["previous_record_id"])
        self.assertEqual(2, second["sequence"])
        self.assertEqual(first["record_id"], second["previous_record_id"])
        self.assertEqual("mutation", second["operation_kind"])
        self.assertEqual("click", second["operation_name"])
        self.assertNotEqual(first["tool_response_sha256"], second["tool_response_sha256"])
        self.assertEqual([first, second], self.read_records(first_path))

    def test_only_stable_hook_fields_affect_call_content_identity(self) -> None:
        payload = self.payload()
        payload["tool_use_id"] = "unstable-identifier-one"
        _, first = RECORDER.record_event(payload, self.root)
        payload["tool_use_id"] = "unstable-identifier-two"
        payload["hook_event_name"] = "PostToolUse"
        _, second = RECORDER.record_event(payload, self.root)
        self.assertEqual(first["call_content_sha256"], second["call_content_sha256"])
        self.assertNotEqual(first["record_id"], second["record_id"])

    def test_post_tool_output_exposes_only_hashed_lookup_context(self) -> None:
        _, record = RECORDER.record_event(self.payload(), self.root)
        output = RECORDER._post_tool_output(record)
        hook_output = output["hookSpecificOutput"]
        self.assertEqual("PostToolUse", hook_output["hookEventName"])
        prefix, raw_context = hook_output["additionalContext"].split("=", 1)
        self.assertEqual("PAGE_PROBE_RECORD", prefix)
        context = json.loads(raw_context)
        self.assertEqual(
            {
                "record_id",
                "session_sha256",
                "transcript_sha256",
                "tool_name",
                "operation_kind",
            },
            set(context),
        )
        self.assertEqual(record["record_id"], context["record_id"])
        rendered = json.dumps(output, ensure_ascii=False)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("session-fixed", rendered)

    def test_deferred_tool_requires_one_exact_top_level_canonical_selector(self) -> None:
        approved = "mcp__page__control"
        valid = self.payload(
            tool_name="DeferExecuteTool",
            tool_input={"tool_name": approved, "arguments": {"action": "select"}},
        )
        _, record = RECORDER.record_event(valid, self.root)
        self.assertEqual(approved, record["tool_name"])
        self.assertEqual("select", record["operation_name"])

        invalid_inputs = [
            {"arguments": {"tool_name": approved, "action": "select"}},
            {"tool_name": approved, "note": "mcp__files__write"},
            {"tool_name": approved, "nested": {"tool_name": approved}},
            {"tool_name": "not-canonical", "arguments": {}},
        ]
        for tool_input in invalid_inputs:
            with self.subTest(tool_input=tool_input):
                with self.assertRaises(RECORDER.ProbeRecordError):
                    RECORDER.record_event(
                        self.payload(tool_name="DeferExecuteTool", tool_input=tool_input),
                        self.root,
                    )

    def test_empty_and_structured_error_responses_are_recorded_but_not_acceptable_evidence(self) -> None:
        path, empty = RECORDER.record_event(
            self.payload(tool_response={}),
            self.root,
        )
        _, failed = RECORDER.record_event(
            self.payload(
                tool_input={"action": "click"},
                tool_response={"success": False, "status": "failed"},
            ),
            self.root,
        )
        self.assertFalse(empty["response_nonempty"])
        self.assertFalse(empty["response_error"])
        self.assertTrue(failed["response_nonempty"])
        self.assertTrue(failed["response_error"])
        self.assertEqual(2, len(self.read_records(path)))

    def test_common_mcp_error_shapes_are_not_recorded_as_success(self) -> None:
        failures = (
            "Error: target is unavailable",
            "FAILED to click target",
            {"error": "target is unavailable"},
            {"exception": {"message": "timeout"}},
            {"content": [{"type": "text", "isError": True}]},
            {"content": {"ok": False}},
        )
        for response in failures:
            with self.subTest(response=response):
                _, record = RECORDER.record_event(
                    self.payload(tool_response=response), self.root
                )
                self.assertTrue(record["response_error"])
        for response in (
            "The error rate is displayed as 0%",
            {"error": "", "exception": None, "success": True},
            {"content": [{"type": "text", "text": "normal page content"}]},
        ):
            with self.subTest(success=response):
                _, record = RECORDER.record_event(
                    self.payload(tool_response=response), self.root
                )
                self.assertFalse(record["response_error"])

    def test_unrelated_tools_are_ignored_and_required_stable_fields_fail_closed(self) -> None:
        self.assertIsNone(
            RECORDER.record_event(self.payload(tool_name="Read"), self.root)
        )
        for field in (
            "session_id",
            "transcript_path",
            "cwd",
            "tool_name",
            "tool_input",
            "tool_response",
        ):
            payload = self.payload()
            payload.pop(field)
            with self.subTest(field=field):
                with self.assertRaises(RECORDER.ProbeRecordError):
                    RECORDER.record_event(payload, self.root)

    def test_cwd_outside_project_and_incomplete_spool_fail_closed(self) -> None:
        payload = self.payload()
        payload["cwd"] = str(self.root.parent)
        with self.assertRaises(RECORDER.ProbeRecordError):
            RECORDER.record_event(payload, self.root)

        path, _ = RECORDER.record_event(self.payload(), self.root)
        with path.open("ab") as stream:
            stream.write(b"incomplete")
        with self.assertRaises(RECORDER.ProbeRecordError):
            RECORDER.record_event(self.payload(), self.root)

    def test_concurrent_records_remain_contiguous_and_hash_chained(self) -> None:
        def write(index: int) -> Path:
            path, _ = RECORDER.record_event(
                self.payload(
                    tool_input={"action": "click", "index": index},
                    tool_response={"page": f"state-{index}"},
                ),
                self.root,
            )
            return path

        with ThreadPoolExecutor(max_workers=4) as executor:
            paths = list(executor.map(write, range(8)))
        self.assertEqual(1, len(set(paths)))
        records = self.read_records(paths[0])
        self.assertEqual(list(range(1, 9)), [row["sequence"] for row in records])
        for previous, current in zip(records, records[1:]):
            self.assertEqual(previous["record_id"], current["previous_record_id"])

    def test_schema_is_strict_and_matches_runtime_record_contract(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(RECORDER._RECORD_FIELDS), set(schema["required"]))
        self.assertEqual(set(RECORDER._RECORD_FIELDS), set(schema["properties"]))
        self.assertEqual(
            {"read", "mutation", "unknown"},
            set(schema["properties"]["operation_kind"]["enum"]),
        )


if __name__ == "__main__":
    unittest.main()
