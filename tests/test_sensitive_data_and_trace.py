# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

DELIVERABLE_SPEC = importlib.util.spec_from_file_location(
    "sensitive_test_deliverable_validator",
    REPO_ROOT / "scripts" / "validate-test-design-deliverable.py",
)
assert DELIVERABLE_SPEC and DELIVERABLE_SPEC.loader
DELIVERABLE_VALIDATOR = importlib.util.module_from_spec(DELIVERABLE_SPEC)
DELIVERABLE_SPEC.loader.exec_module(DELIVERABLE_VALIDATOR)

try:
    from tests import test_architecture_safety as architecture_safety
except ModuleNotFoundError:  # unittest discovery may place tests/ directly on sys.path.
    import test_architecture_safety as architecture_safety

from test_design.orchestration.contracts import TraceabilityRecord
from test_design.orchestration.review import ReviewValidationError, _validate_traceability
from test_design.fact_store import validate_catalog
from test_design.sensitive_data import (
    assert_no_unmasked_value,
    binary_evidence_audit_path,
)


class SensitiveBatchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = architecture_safety.ArchitectureSafetyTests(methodName="runTest")

    def _valid_run(self, root: Path, run_id: str) -> Path:
        self.helper.create_project_root(root)
        return self.helper.make_valid_plan_run(root, run_id)

    @staticmethod
    def _write_binary_audit(
        evidence: Path,
        *,
        visible_text: str = "<no_visible_text>",
    ) -> Path:
        audit = binary_evidence_audit_path(evidence)
        audit.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    "inspection_method": "model_visual_review",
                    "visible_text": visible_text,
                    "address_bar_cropped_or_masked": True,
                    "environment_identifiers_masked": True,
                    "credentials_masked": True,
                    "status": "PASSED",
                    "notes": "已检查可见区域并完成必要裁剪和遮蔽",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return audit

    @staticmethod
    def _csv_rows(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_inventory_secret_is_rejected_by_every_batch_phase(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._valid_run(Path(value), "sensitive-inventory")
            inventory_path = run_dir / "page-element-inventory.csv"
            rows = self._csv_rows(inventory_path)
            rows[0]["备注"] = "password=RealSecret_123!"
            self.helper.write_csv_rows(inventory_path, rows)

            for phase in ("discovery", "plan", "risk", "cases"):
                with self.subTest(phase=phase):
                    with self.assertRaisesRegex(ValueError, "possible unmasked secret"):
                        architecture_safety.TOOLS.validate_batch_artifacts(run_dir, phase, use_cache=False)
            with self.assertRaisesRegex(AssertionError, "possible unmasked secret"):
                DELIVERABLE_VALIDATOR.validate_batch_status(run_dir / "batch-status.csv")

    def test_risk_ledger_secret_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._valid_run(Path(value), "sensitive-risk")
            risk_path = run_dir / "risk-confirmation.csv"
            rows = self._csv_rows(risk_path)
            rows[0]["备注"] = "token=RealRiskToken_123"
            self.helper.write_csv_rows(risk_path, rows)

            with self.assertRaisesRegex(ValueError, "possible unmasked secret"):
                architecture_safety.TOOLS.validate_batch_artifacts(run_dir, "risk", use_cache=False)

    def test_decodable_text_evidence_secret_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._valid_run(Path(value), "sensitive-evidence")
            evidence = run_dir / "artifacts" / "screenshots" / "danger-action.txt"
            evidence.write_text(
                "页面操作证据\nsecret=EvidenceSecret_123\n页面已恢复",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "possible unmasked secret"):
                architecture_safety.TOOLS.validate_batch_artifacts(run_dir, "discovery", use_cache=False)

    def test_promoted_json_secret_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._valid_run(Path(value), "sensitive-promoted-json")
            promoted = run_dir / "artifacts" / "data" / "retained-plan-output.json"
            promoted.parent.mkdir(parents=True, exist_ok=True)
            promoted.write_text(
                json.dumps({"note": "token=PromotedJsonSecret_123"}, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "possible unmasked secret"):
                architecture_safety.TOOLS.validate_batch_artifacts(
                    run_dir,
                    "discovery",
                    use_cache=False,
                )

    def test_svg_text_secret_is_rejected_while_public_namespace_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._valid_run(Path(value), "sensitive-svg")
            svg = run_dir / "artifacts" / "screenshots" / "risk-proof.svg"
            svg.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><text>页面已恢复</text></svg>\n',
                encoding="utf-8",
            )
            architecture_safety.TOOLS.validate_batch_artifacts(
                run_dir,
                "discovery",
                use_cache=False,
            )
            svg.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg">'
                '<text>password=SvgEvidenceSecret_123</text>'
                "</svg>\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "possible unmasked secret"):
                architecture_safety.TOOLS.validate_batch_artifacts(
                    run_dir,
                    "discovery",
                    use_cache=False,
                )

    def test_binary_evidence_requires_hash_bound_visual_privacy_audit(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._valid_run(Path(value), "sensitive-binary-audit")
            evidence = run_dir / "artifacts" / "screenshots" / "safe-state.png"
            evidence.write_bytes(b"\x89PNG\r\n\x1a\n" + b"sanitized-image-pixels")

            with self.assertRaisesRegex(ValueError, "requires adjacent visual privacy audit"):
                architecture_safety.TOOLS.validate_batch_artifacts(
                    run_dir,
                    "discovery",
                    use_cache=False,
                )

            self._write_binary_audit(evidence)
            architecture_safety.TOOLS.validate_batch_artifacts(
                run_dir,
                "discovery",
                use_cache=False,
            )

            evidence.write_bytes(evidence.read_bytes() + b"changed-after-review")
            with self.assertRaisesRegex(ValueError, "audit hash does not match"):
                architecture_safety.TOOLS.validate_batch_artifacts(
                    run_dir,
                    "discovery",
                    use_cache=False,
                )

    def test_binary_evidence_embedded_metadata_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._valid_run(Path(value), "sensitive-binary-metadata")
            evidence = run_dir / "artifacts" / "screenshots" / "metadata-state.png"
            evidence.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"source=https://prod-node.internal.local/private"
            )
            self._write_binary_audit(evidence, visible_text="页面状态已脱敏")

            with self.assertRaisesRegex(ValueError, "possible unmasked environment address/account"):
                architecture_safety.TOOLS.validate_batch_artifacts(
                    run_dir,
                    "discovery",
                    use_cache=False,
                )

    def test_product_catalog_json_secret_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            self.helper.create_project_root(root)
            catalog = root / "docs" / "test-assets" / "catalog"
            modules = catalog / "modules"
            modules.mkdir(parents=True, exist_ok=True)
            (catalog / "migration.json").write_text(
                json.dumps({"schema_version": "2.0.0"}),
                encoding="utf-8",
            )
            (modules / "leaked.json").write_text(
                json.dumps({"note": "password=CatalogSecret_123"}, ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "possible unmasked secret"):
                validate_catalog(
                    root / "docs" / "test-assets" / "product-map.xlsx",
                    require_existing=True,
                )

    def test_public_ip_ipv6_short_host_account_and_bare_tokens_are_rejected(self) -> None:
        unsafe_values = (
            "resolver=8.8.8.8",
            "主机=fe80::1",
            "hostname=prod-node-01",
            "username=real-admin",
            '"token": "RealJsonToken_12345"',
            "Authorization: Bearer AbCdEf0123456789.Token",
            "AKIA1234567890ABCDEF",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyZWFsLXVzZXIifQ.signature123",
            "https://real-user:real-password@example.com/private",
        )
        for value in unsafe_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "possible unmasked"):
                    assert_no_unmasked_value(value, "probe")

    def test_reserved_example_and_local_urls_are_allowed(self) -> None:
        safe_values = (
            "https://api.example.com/test/path",
            "http://localhost:8080/login",
            "http://127.0.0.1:3000/health",
            "192.0.2.25",
            "2001:db8::25",
            "username=<test_user_account>",
            '"username": "<test_user_account>"',
            '"host": "<test_host>"',
            '"token": "<test_token>"',
            "Authorization: Bearer <test_token>",
        )
        for value in safe_values:
            with self.subTest(value=value):
                assert_no_unmasked_value(value, "safe probe")

    def test_orchestration_and_agent_workspace_text_are_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = self._valid_run(Path(value), "sensitive-orchestration")
            result_path = run_dir / "orchestration" / "results" / "leaked-result.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps({"error_message": "Bearer OrchestrationToken_12345"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "possible unmasked secret"):
                architecture_safety.TOOLS.validate_batch_artifacts(
                    run_dir,
                    "discovery",
                    use_cache=False,
                )
            result_path.unlink()
            agent_output = (
                run_dir
                / "artifacts"
                / "agent-work"
                / "discovery"
                / "TASK-PROBE"
                / "output"
                / "probe.json"
            )
            agent_output.parent.mkdir(parents=True, exist_ok=True)
            agent_output.write_text(
                json.dumps({"endpoint": "https://real-service.invalid-tld.internal-path"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "possible unmasked environment address/account"):
                architecture_safety.TOOLS.validate_batch_artifacts(
                    run_dir,
                    "discovery",
                    use_cache=False,
                )


class ReviewTraceOrderTests(unittest.TestCase):
    def test_reversed_trace_order_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir = Path(value)
            data_dir = run_dir / "artifacts" / "data"
            data_dir.mkdir(parents=True)
            (data_dir / "function_cases_manifest.json").write_text(
                json.dumps({"parts": ["function_cases_part_001.json"]}),
                encoding="utf-8",
            )
            (data_dir / "function_cases_part_001.json").write_text(
                json.dumps(
                    [
                        {"用例 ID": "TC-001", "功能点": "告警列表"},
                        {"用例 ID": "TC-002", "功能点": "告警列表"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            source_fingerprint = "a" * 64
            records = [
                TraceabilityRecord(
                    schema_version="1.0.0",
                    case_id=case_id,
                    function_point="告警列表",
                    plan_owner_id="PLAN-001",
                    interaction_ids=(f"INT-{index:03d}",),
                    selection_observation_ids=(),
                    branch_observation_ids=(),
                    lifecycle_ids=(),
                    evidence_hashes=("b" * 64,),
                    worker_task_id="TASK-CASE-001",
                    source_fingerprint=source_fingerprint,
                )
                for index, case_id in enumerate(("TC-001", "TC-002"), start=1)
            ]
            (data_dir / "case-traceability.json").write_text(
                json.dumps([record.to_dict() for record in reversed(records)], ensure_ascii=False),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ReviewValidationError,
                "record order must exactly match formal case order",
            ):
                _validate_traceability(
                    run_dir,
                    ("TASK-CASE-001",),
                    source_fingerprint,
                    {record.case_id: record for record in records},
                )


if __name__ == "__main__":
    unittest.main()
