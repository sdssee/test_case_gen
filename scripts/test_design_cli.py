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


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal helpers for one-session test design")
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", help="Atomically record complete facts or transactions")
    record.add_argument("--run-dir", required=True, type=Path)
    record.add_argument("--file", type=Path)
    record.add_argument("--module-path", default="", help="Required only on the first record")
    record.add_argument("--product-name", default="")
    record.add_argument("--source", default="")
    record.add_argument("--menu-path", default="")

    compile_command = sub.add_parser("compile", help="Rebuild the compact facts view once")
    compile_command.add_argument("--run-dir", required=True, type=Path)

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
                menu_path=args.menu_path or args.module_path,
            )
        payload = _payload(args.file)
        events = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(item, dict) for item in events):
            raise ValueError("record payload must be an object or an array of objects")
        append_events(args.run_dir, events)
        facts = compile_facts(args.run_dir)
        _print({"recorded": len(events), "fact_count": facts["fact_count"]})
    elif args.command == "compile":
        _print(compile_facts(args.run_dir))
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
