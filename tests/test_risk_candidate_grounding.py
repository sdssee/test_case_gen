# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from test_design.orchestration.engine import (  # noqa: E402
    OrchestrationError,
    _validate_risk_candidates_file,
)


class RiskCandidateGroundingTests(unittest.TestCase):
    @staticmethod
    def _candidate(**overrides: object) -> dict[str, object]:
        candidate: dict[str, object] = {
            "risk_id": "RISK-EXT-001",
            "question": "异步任务完成规则由哪个外部状态定义",
            "page_verifiability": "external_semantics",
            "page_action": "在详情页执行查询并观察任务状态",
            "page_result": "页面仅显示处理中，无法展示异步任务完成原因",
            "external_reason": "需要接口日志和异步任务状态才能解释业务语义",
            "affected_interaction_ids": ["INT-001"],
            "evidence": ["artifacts/evidence/risk-proof.txt"],
            "dfx_dimensions": ["DFR可靠"],
        }
        candidate.update(overrides)
        return candidate

    @staticmethod
    def _write_candidate(path: Path, candidate: dict[str, object]) -> None:
        path.write_text(
            json.dumps({"candidates": [candidate]}, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _run(root: Path) -> tuple[Path, Path]:
        run_dir = root / "run"
        evidence_dir = run_dir / "artifacts" / "evidence"
        evidence_dir.mkdir(parents=True)
        with (run_dir / "page-discovery.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=["交互实例ID", "页面/入口", "证据路径"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "交互实例ID": "INT-001",
                    "页面/入口": "告警列表",
                    "证据路径": "artifacts/evidence/risk-proof.txt",
                }
            )
        (evidence_dir / "risk-proof.txt").write_text(
            "页面显示异步任务仍在处理中；完成规则需要外部日志确认。\n",
            encoding="utf-8",
        )
        candidate_path = run_dir / "risk-candidates.json"
        return run_dir, candidate_path

    def test_grounded_external_candidate_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir, path = self._run(Path(value))
            candidate = self._candidate()
            self._write_candidate(path, candidate)

            self.assertEqual(
                [candidate],
                _validate_risk_candidates_file(path, run_dir=run_dir),
            )

    def test_unknown_interaction_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir, path = self._run(Path(value))
            self._write_candidate(
                path,
                self._candidate(affected_interaction_ids=["INT-NOT-DISCOVERED"]),
            )

            with self.assertRaisesRegex(
                OrchestrationError,
                "absent from current page-discovery.csv",
            ):
                _validate_risk_candidates_file(path, run_dir=run_dir)

    def test_evidence_must_be_non_empty_and_inside_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_dir, path = self._run(root)
            empty = run_dir / "artifacts" / "evidence" / "empty.txt"
            empty.touch()
            outside = root / "outside.txt"
            outside.write_text("外部文件不能作为当前批次证据。\n", encoding="utf-8")
            invalid_paths = (
                "artifacts/evidence/missing.txt",
                "artifacts/evidence/empty.txt",
                str(outside.resolve()),
            )

            for invalid_path in invalid_paths:
                with self.subTest(evidence=invalid_path):
                    self._write_candidate(path, self._candidate(evidence=[invalid_path]))
                    with self.assertRaisesRegex(
                        OrchestrationError,
                        "real, non-empty file inside the current run artifacts",
                    ):
                        _validate_risk_candidates_file(path, run_dir=run_dir)

    def test_evidence_from_another_interaction_cannot_be_substituted(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir, path = self._run(Path(value))
            other_evidence = run_dir / "artifacts" / "evidence" / "other-control.txt"
            other_evidence.write_text(
                "另一个控件的实探证据不能证明当前风险候选。\n",
                encoding="utf-8",
            )
            self._write_candidate(
                path,
                self._candidate(evidence=["artifacts/evidence/other-control.txt"]),
            )

            with self.assertRaisesRegex(
                OrchestrationError,
                "does not cover each affected interaction's page-discovery evidence",
            ):
                _validate_risk_candidates_file(path, run_dir=run_dir)

    def test_evidence_must_cover_every_affected_interaction(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir, path = self._run(Path(value))
            second_evidence = run_dir / "artifacts" / "evidence" / "second-control.txt"
            second_evidence.write_text(
                "第二个控件的独立实探证据。\n",
                encoding="utf-8",
            )
            with (run_dir / "page-discovery.csv").open(
                "a", encoding="utf-8-sig", newline=""
            ) as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["交互实例ID", "页面/入口", "证据路径"],
                )
                writer.writerow(
                    {
                        "交互实例ID": "INT-002",
                        "页面/入口": "告警列表",
                        "证据路径": "artifacts/evidence/second-control.txt",
                    }
                )
            self._write_candidate(
                path,
                self._candidate(
                    affected_interaction_ids=["INT-001", "INT-002"],
                    evidence=["artifacts/evidence/risk-proof.txt"],
                ),
            )

            with self.assertRaisesRegex(OrchestrationError, "INT-002"):
                _validate_risk_candidates_file(path, run_dir=run_dir)

    def test_sensitive_text_evidence_is_rejected_by_shared_scanner(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir, path = self._run(Path(value))
            (run_dir / "artifacts" / "evidence" / "risk-proof.txt").write_text(
                "token=RiskEvidenceSecret_123\n",
                encoding="utf-8",
            )
            self._write_candidate(path, self._candidate())

            with self.assertRaisesRegex(OrchestrationError, "possible unmasked secret"):
                _validate_risk_candidates_file(path, run_dir=run_dir)

    def test_page_verifiable_candidate_still_returns_to_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            run_dir, path = self._run(Path(value))
            self._write_candidate(
                path,
                self._candidate(
                    question="点击告警级别下拉项后列表如何变化",
                    page_verifiability="page_verifiable",
                    page_result="尚未逐项点击",
                    external_reason="",
                ),
            )
            # Page-verifiable questions must be routed back before external-risk
            # fact grounding can obscure the required discovery rework outcome.
            (run_dir / "page-discovery.csv").unlink()

            with self.assertRaisesRegex(
                OrchestrationError,
                "must return to discovery via NEEDS_REWORK",
            ):
                _validate_risk_candidates_file(path, run_dir=run_dir)


if __name__ == "__main__":
    unittest.main()
