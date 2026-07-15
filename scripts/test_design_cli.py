# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from test_design.formal_assembler import assemble_formal_workbook, complete_deliverables, generate_import_workbook
from test_design.session_runtime import (
    append_events,
    artifact_paths,
    compile_facts,
    init_run,
    pipeline_status,
    review_run,
    validate_cases,
    validate_discovery,
    validate_plan,
)


def _payload(path: Path | None) -> object:
    if path:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if sys.stdin.isatty():
        raise ValueError("provide --file or pipe JSON through stdin")
    return json.load(sys.stdin)


def _print(value: object, as_json: bool = True) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Single-session test-design workflow")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-run", help="Initialize the compact single-session run directory")
    init.add_argument("--run-dir", required=True, type=Path)
    init.add_argument("--module-path", required=True)
    init.add_argument("--product-name", default="")
    init.add_argument("--source", default="")

    record = sub.add_parser("record-observation", help="Append one transaction observation or a JSON array, then rebuild facts")
    record.add_argument("--run-dir", required=True, type=Path)
    record.add_argument("--file", type=Path)

    compile_command = sub.add_parser("compile-facts", help="Rebuild facts.json from append-only events.jsonl")
    compile_command.add_argument("--run-dir", required=True, type=Path)

    validate = sub.add_parser("validate-stage", help="Run one lightweight stage-boundary validation")
    validate.add_argument("--run-dir", required=True, type=Path)
    validate.add_argument("--stage", required=True, choices=["discovery", "plan", "cases", "review"])

    status = sub.add_parser("pipeline-status", help="Derive the next action from stage artifacts")
    status.add_argument("--run-dir", required=True, type=Path)

    review = sub.add_parser("review-run", help="Run bidirectional fact-plan-case review once")
    review.add_argument("--run-dir", required=True, type=Path)

    assemble = sub.add_parser("assemble-formal-workbook", help="Assemble the exact 8-sheet formal workbook")
    assemble.add_argument("--run-dir", required=True, type=Path)
    assemble.add_argument("--template", required=True, type=Path)
    assemble.add_argument("--output", required=True, type=Path)

    generate = sub.add_parser("generate-import", help="Generate the independent test-system import workbook")
    generate.add_argument("--formal-workbook", required=True, type=Path)
    generate.add_argument("--template", required=True, type=Path)
    generate.add_argument("--output", required=True, type=Path)
    generate.add_argument("--module-path", required=True)

    complete = sub.add_parser("complete-deliverables", help="Review and generate formal plus import workbooks")
    complete.add_argument("--run-dir", required=True, type=Path)
    complete.add_argument("--project-root", type=Path, default=Path("."))

    args = parser.parse_args()
    if args.command == "init-run":
        _print(init_run(args.run_dir, args.module_path, args.product_name, args.source))
    elif args.command == "record-observation":
        payload = _payload(args.file)
        events = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(item, dict) for item in events):
            raise ValueError("observation payload must be an object or an array of objects")
        append_events(args.run_dir, events)
        facts = compile_facts(args.run_dir)
        _print({"appended": len(events), "fact_count": facts["fact_count"]})
    elif args.command == "compile-facts":
        _print(compile_facts(args.run_dir))
    elif args.command == "validate-stage":
        validators = {
            "discovery": validate_discovery,
            "plan": validate_plan,
            "cases": validate_cases,
            "review": lambda run_dir: review_run(run_dir)["errors"],
        }
        errors = validators[args.stage](args.run_dir)
        _print({"stage": args.stage, "status": "passed" if not errors else "failed", "errors": errors})
        return 1 if errors else 0
    elif args.command == "pipeline-status":
        _print(pipeline_status(args.run_dir))
    elif args.command == "review-run":
        result = review_run(args.run_dir)
        _print(result)
        return 1 if result["status"] != "passed" else 0
    elif args.command == "assemble-formal-workbook":
        _print(assemble_formal_workbook(args.run_dir, args.template, args.output))
    elif args.command == "generate-import":
        _print({"import_cases": generate_import_workbook(args.formal_workbook, args.template, args.output, args.module_path)})
    elif args.command == "complete-deliverables":
        _print(complete_deliverables(args.run_dir, args.project_root.resolve()))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
