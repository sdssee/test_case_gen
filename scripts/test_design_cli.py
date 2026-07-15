# -*- coding: utf-8 -*-
"""Low-level helpers used internally by the test-design Skill."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from test_design.formal_assembler import complete_deliverables
from test_design.session_runtime import (
    append_events,
    artifact_paths,
    build_plan_skeleton,
    checkpoint_facts,
    compile_facts,
    ensure_run,
    pipeline_status,
    review_run,
    save_cases,
    save_plan,
)


def _payload(path: Path | None) -> object:
    if path:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if sys.stdin.isatty():
        raise ValueError("provide --file or pipe JSON through stdin")
    return json.load(sys.stdin)


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _project_scoped_run_dir(value: Path) -> Path:
    project_root = Path.cwd().resolve()
    resolved = value.resolve()
    if resolved != project_root and project_root not in resolved.parents:
        raise ValueError(f"run-dir must stay inside the current project root: {project_root}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal helpers for one-session test design")
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="Record validated facts or one complete transaction")
    record.add_argument("--run-dir", required=True, type=Path)
    record.add_argument("--file", type=Path)
    record.add_argument("--module-path", default="", help="Required only on the first record")
    record.add_argument("--product-name", default="")
    record.add_argument("--source", default="")

    compile_command = sub.add_parser("compile", help="Rebuild the compact facts view once")
    compile_command.add_argument("--run-dir", required=True, type=Path)

    checkpoint = sub.add_parser("checkpoint", help="Compile facts and summarize discovery readiness once")
    checkpoint.add_argument("--run-dir", required=True, type=Path)

    skeleton = sub.add_parser("plan-skeleton", help="Build the factual plan skeleton without another artifact")
    skeleton.add_argument("--run-dir", required=True, type=Path)

    write_plan = sub.add_parser("write-plan", help="Write a plan with generation-time constraints")
    write_plan.add_argument("--run-dir", required=True, type=Path)
    write_plan.add_argument("--file", type=Path)

    write_cases = sub.add_parser("write-cases", help="Write paired cases with generation-time constraints")
    write_cases.add_argument("--run-dir", required=True, type=Path)
    write_cases.add_argument("--file", type=Path)

    status = sub.add_parser("status", help="Describe resumable progress without gating execution")
    status.add_argument("--run-dir", required=True, type=Path)

    review = sub.add_parser("review", help="Run the single cross-artifact audit")
    review.add_argument("--run-dir", required=True, type=Path)

    deliver = sub.add_parser("deliver", help="Generate both independent Excel deliverables")
    deliver.add_argument("--run-dir", required=True, type=Path)
    deliver.add_argument("--project-root", type=Path, default=Path("."))

    args = parser.parse_args()
    if hasattr(args, "run_dir"):
        args.run_dir = _project_scoped_run_dir(args.run_dir)
    if args.command == "record":
        paths = artifact_paths(args.run_dir)
        if not paths["facts"].exists():
            if not args.module_path:
                raise ValueError("the first record requires --module-path for transparent scope binding")
            ensure_run(
                args.run_dir,
                args.module_path,
                args.product_name,
                args.source,
            )
        payload = _payload(args.file)
        events = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(item, dict) for item in events):
            raise ValueError("record payload must be an object or an array of objects")
        recorded = append_events(args.run_dir, events)
        should_checkpoint = any(
            item.get("kind") == "page" and item.get("data", {}).get("final_scan_status") == "stable"
            for item in recorded
        )
        result = {"recorded": len(recorded), "facts": [item["fact_id"] for item in recorded], "checkpointed": should_checkpoint}
        if should_checkpoint:
            result["checkpoint"] = checkpoint_facts(args.run_dir)
        _print(result)
    elif args.command == "compile":
        _print(compile_facts(args.run_dir))
    elif args.command == "checkpoint":
        _print(checkpoint_facts(args.run_dir))
    elif args.command == "plan-skeleton":
        _print(build_plan_skeleton(args.run_dir))
    elif args.command == "write-plan":
        payload = _payload(args.file)
        if not isinstance(payload, dict):
            raise ValueError("plan payload must be an object")
        _print(save_plan(args.run_dir, payload))
    elif args.command == "write-cases":
        payload = _payload(args.file)
        if not isinstance(payload, dict):
            raise ValueError("cases payload must be an object")
        _print(save_cases(args.run_dir, payload))
    elif args.command == "status":
        _print(pipeline_status(args.run_dir))
    elif args.command == "review":
        _print(review_run(args.run_dir))
    elif args.command == "deliver":
        _print(complete_deliverables(args.run_dir, args.project_root.resolve()))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
