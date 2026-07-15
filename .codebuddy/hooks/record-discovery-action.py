#!/usr/bin/env python3
"""Record page-tool PostToolUse calls for the active discovery obligation.

Raw page content and tool arguments are never written.  The local run keeps
only hashes, ordering, operation classification and success flags.  A later
``discovery-complete`` command binds three physical records to one obligation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MCP_TOOL = re.compile(r"^mcp__[A-Za-z0-9_.:-]+$")
BUILTIN_PAGE_TOOLS = {"Browser", "ComputerUse"}
MUTATIONS = (
    ("click", ("click", "press", "tap", "activate", "submit", "save")),
    ("select", ("select", "choose", "option", "dropdown")),
    ("input", ("input", "type", "fill", "enter_text", "set_value")),
    ("toggle", ("toggle", "check", "uncheck", "switch")),
    ("expand", ("expand", "collapse", "open_menu", "close_menu")),
    ("navigate", ("navigate", "goto", "go_to", "open_page")),
    ("upload", ("upload", "attach")),
)
READ_HINTS = (
    "read", "observe", "inspect", "snapshot", "screenshot", "capture",
    "page_state", "get_page", "query", "describe", "list", "wait_for",
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def effective_tool_name(hook_name: str, tool_input: Any) -> str | None:
    if MCP_TOOL.fullmatch(hook_name) or hook_name in BUILTIN_PAGE_TOOLS:
        return hook_name
    if hook_name != "DeferExecuteTool" or not isinstance(tool_input, dict):
        return None
    name = tool_input.get("tool_name")
    return name if isinstance(name, str) and (MCP_TOOL.fullmatch(name) or name in BUILTIN_PAGE_TOOLS) else None


def action_tokens(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 4 or not isinstance(value, dict):
        return []
    result: list[str] = []
    action_keys = {"action", "operation", "operation_name", "method", "command", "intent", "type"}
    for key, item in value.items():
        if key.casefold() in action_keys and isinstance(item, str):
            result.append(item.casefold())
        elif isinstance(item, dict):
            result.extend(action_tokens(item, depth=depth + 1))
    return result


def classify(tool_name: str, tool_input: Any) -> tuple[str, str]:
    tokens = [tool_name.rsplit("__", 1)[-1].casefold()]
    tokens.extend(action_tokens(tool_input))
    text = "_".join(re.sub(r"[^a-z0-9]+", "_", value) for value in tokens)
    for operation, hints in MUTATIONS:
        if any(hint in text for hint in hints):
            return "mutation", operation
    if any(hint in text for hint in READ_HINTS):
        return "read", "read"
    if any(hint in text for hint in ("drag", "drop", "hover")):
        return "mutation", "other_mutation"
    return "unknown", "unknown"


def nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple)):
        return bool(value)
    return True


def response_error(value: Any) -> bool:
    if isinstance(value, str):
        return re.match(r"^\s*(?:error|failed|failure|exception)\b", value, re.I) is not None
    if not isinstance(value, dict):
        return False
    return value.get("is_error") is True or value.get("isError") is True or value.get("success") is False or value.get("ok") is False


def active_file(project_root: Path) -> Path | None:
    candidates = list(
        project_root.glob("docs/test-assets/batch-runs/*/artifacts/discovery-control/active-obligation.json")
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        raise ValueError("multiple batches have active discovery obligations")
    return candidates[0]


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    # Single-agent execution means one writer.  O_APPEND prevents partial
    # seek/overwrite races with the status reader.
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    root_text = os.environ.get("CODEBUDDY_PROJECT_DIR", "").strip()
    if not root_text:
        return 0
    root = Path(root_text).resolve()
    active = active_file(root)
    if active is None:
        return 0
    raw = sys.stdin.buffer.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError("hook payload is too large")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be an object")
    hook_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input")
    tool_response = payload.get("tool_response", payload.get("tool_result", payload.get("result")))
    tool_name = effective_tool_name(hook_name, tool_input)
    if not tool_name:
        return 0
    operation_kind, operation_name = classify(tool_name, tool_input)
    if operation_kind == "unknown":
        return 0
    control_root = active.parent
    events_path = control_root / "action-events.jsonl"
    sequence = 1
    previous_record_id = ""
    if events_path.exists():
        lines = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        sequence = len(lines) + 1
        if lines:
            previous_record_id = str(json.loads(lines[-1]).get("record_id", ""))
    session_id = str(payload.get("session_id") or payload.get("conversation_id") or payload.get("thread_id") or "")
    transcript = str(payload.get("transcript_path") or os.environ.get("CODEBUDDY_TRANSCRIPT_PATH", ""))
    input_hash = digest(tool_input)
    response_hash = digest(tool_response)
    record_seed = f"{sequence}\0{previous_record_id}\0{session_id}\0{transcript}\0{tool_name}\0{input_hash}\0{response_hash}"
    event = {
        "version": 1,
        "record_id": hashlib.sha256(record_seed.encode("utf-8")).hexdigest(),
        "sequence": sequence,
        "previous_record_id": previous_record_id,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "session_sha256": hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
        "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
        "tool_name": tool_name,
        "tool_input_sha256": input_hash,
        "tool_response_sha256": response_hash,
        "response_nonempty": nonempty(tool_response),
        "response_error": response_error(tool_response),
        "operation_kind": operation_kind,
        "operation_name": operation_name,
    }
    append_event(events_path, event)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"CodeBuddy discovery recorder: {exc}", file=sys.stderr)
        raise SystemExit(2)
