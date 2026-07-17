# -*- coding: utf-8 -*-
"""Low-level helpers used internally by the test-design Skill."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from test_design.formal_assembler import complete_deliverables
from test_design.session_runtime import (
    append_events,
    artifact_paths,
    build_plan_skeleton,
    checkpoint_facts,
    compile_facts,
    ensure_run,
    pending_exploration_requirements,
    pipeline_status,
    review_run,
    save_cases,
    save_plan,
)


for stream_name in ("stdin", "stdout", "stderr"):
    stream = getattr(sys, stream_name, None)
    if stream is not None and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")


def _payload(path: Path | None) -> object:
    if path:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        finally:
            resolved = path.resolve()
            temp_root = Path(tempfile.gettempdir()).resolve()
            if resolved.parent == temp_root and resolved.name.startswith("test-design-"):
                resolved.unlink(missing_ok=True)
    if sys.stdin.isatty():
        raise ValueError("provide --file or pipe JSON through stdin")
    return json.load(sys.stdin)


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _project_scoped_run_dir(value: Path) -> Path:
    project_root = Path.cwd().resolve()
    canonical_root = (project_root / "docs" / "test-design" / "current").resolve()
    if not value.is_absolute() and len(value.parts) == 1:
        resolved = (canonical_root / value).resolve()
    else:
        resolved = value.resolve()
    if resolved != project_root and project_root not in resolved.parents:
        raise ValueError(f"run-dir must stay inside the current project root: {project_root}")
    is_canonical = canonical_root in resolved.parents
    is_existing_legacy = (resolved / "events.jsonl").is_file() or (resolved / "facts.json").is_file()
    if not is_canonical and not is_existing_legacy:
        raise ValueError(f"new run-dir must be under the canonical root: {canonical_root}")
    return resolved


def execute_request(
    command: str,
    run_dir: Path,
    payload: object | None = None,
    *,
    module_path: str = "",
    product_name: str = "",
    source: str = "",
    project_root: Path = Path("."),
) -> object:
    """Canonical in-process adapter used by both the CLI and its single fallback."""
    run_dir = _project_scoped_run_dir(run_dir)
    if command == "record":
        paths = artifact_paths(run_dir)
        if not paths["facts"].exists():
            if not module_path:
                raise ValueError("the first record requires module_path for transparent scope binding")
            ensure_run(run_dir, module_path, product_name, source)
        events = payload if isinstance(payload, list) else [payload]
        if not events or not all(isinstance(item, dict) for item in events):
            raise ValueError("record payload must be an object or an array of objects")
        recorded = append_events(run_dir, events)
        should_checkpoint = any(
            item.get("kind") == "page" and item.get("data", {}).get("final_scan_status") == "stable"
            for item in recorded
        )
        result: dict[str, object] = {
            "recorded": len(recorded), "facts": [item["fact_id"] for item in recorded],
            "checkpointed": should_checkpoint,
        }
        element_plans = [
            {"element_ref": item["fact_id"], "element_name": str(item.get("data", {}).get("name", "")),
             "requirements": item.get("data", {}).get("exploration_requirements", [])}
            for item in recorded
            if item.get("kind") == "element" and item.get("data", {}).get("exploration_requirements")
        ]
        if element_plans:
            result["exploration_plan"] = element_plans
        result["remaining_exploration"] = pending_exploration_requirements(run_dir)
        if should_checkpoint:
            result["checkpoint"] = checkpoint_facts(run_dir)
        return result
    if command == "compile":
        return compile_facts(run_dir)
    if command == "checkpoint":
        return checkpoint_facts(run_dir)
    if command == "plan-skeleton":
        return build_plan_skeleton(run_dir)
    if command == "write-plan":
        if not isinstance(payload, dict):
            raise ValueError("plan payload must be an object")
        return save_plan(run_dir, payload)
    if command == "write-cases":
        if not isinstance(payload, dict):
            raise ValueError("cases payload must be an object")
        return save_cases(run_dir, payload)
    if command == "status":
        return pipeline_status(run_dir)
    if command == "review":
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("semantic review payload must be an object")
        return review_run(run_dir, payload)
    if command == "deliver":
        return complete_deliverables(run_dir, project_root.resolve())
    raise ValueError(f"unsupported command: {command}")


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
    review.add_argument("--file", type=Path, help="One compact model semantic-review JSON payload")

    deliver = sub.add_parser("deliver", help="Generate both independent Excel deliverables")
    deliver.add_argument("--run-dir", required=True, type=Path)
    deliver.add_argument("--project-root", type=Path, default=Path("."))

    args = parser.parse_args()
    payload_commands = {"record", "write-plan", "write-cases"}
    payload = _payload(args.file) if args.command in payload_commands or (args.command == "review" and args.file) else None
    _print(execute_request(
        args.command,
        args.run_dir,
        payload,
        module_path=getattr(args, "module_path", ""),
        product_name=getattr(args, "product_name", ""),
        source=getattr(args, "source", ""),
        project_root=getattr(args, "project_root", Path(".")),
    ))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
