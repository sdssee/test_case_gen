#!/usr/bin/env python3
"""Durably record successful CodeBuddy page-MCP PostToolUse observations.

Only CodeBuddy's stable hook fields are consumed: ``session_id``,
``transcript_path``, ``cwd``, ``tool_name``, ``tool_input`` and
``tool_response``.  Raw page input/response content is never persisted; the
ignored local spool stores a hash-chained, strictly shaped record instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


PAGE_PROBE_RECORDER_VERSION = "1.0.0"
PAGE_PROBE_RECORD_SCHEMA_VERSION = "1.0.0"

_MAX_HOOK_INPUT_BYTES = 64 * 1024 * 1024
_MAX_STABLE_TEXT_LENGTH = 32 * 1024
_MCP_TOOL_NAME = re.compile(r"^mcp__[A-Za-z0-9_.:-]+$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_KEYS = {
    "action",
    "command",
    "event",
    "intent",
    "kind",
    "method",
    "op",
    "operation",
    "operation_name",
}
_MUTATION_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("click", ("click", "press", "tap", "activate")),
    ("select", ("select", "choose", "option", "dropdown")),
    ("input", ("input", "type", "fill", "enter_text", "set_value")),
    ("toggle", ("toggle", "check", "uncheck", "switch")),
    ("expand", ("expand", "collapse", "open_menu", "close_menu")),
    ("navigate", ("navigate", "goto", "go_to", "open_page")),
)
_READ_HINTS = (
    "read",
    "observe",
    "inspect",
    "snapshot",
    "screenshot",
    "capture",
    "page_state",
    "get_page",
    "query",
    "describe",
)
_RECORD_FIELDS = {
    "schema_version",
    "recorder_version",
    "record_id",
    "sequence",
    "previous_record_id",
    "recorded_at",
    "session_sha256",
    "transcript_path_sha256",
    "cwd_sha256",
    "project_root_sha256",
    "hook_tool_name",
    "tool_name",
    "tool_input_sha256",
    "tool_response_sha256",
    "call_content_sha256",
    "tool_input_bytes",
    "tool_response_bytes",
    "response_nonempty",
    "response_error",
    "operation_kind",
    "operation_name",
}


class ProbeRecordError(ValueError):
    """A PostToolUse call cannot be recorded without weakening provenance."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProbeRecordError(f"hook JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProbeRecordError("hook field is not canonical JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _normalized_path_text(path: Path) -> str:
    value = str(path.resolve(strict=False)).replace("\\", "/")
    return value.casefold() if os.name == "nt" else value


def _stable_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProbeRecordError(f"missing non-empty stable hook field: {field}")
    if "\x00" in value or len(value) > _MAX_STABLE_TEXT_LENGTH:
        raise ProbeRecordError(f"stable hook field is unsafe: {field}")
    return value


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validated_project_context(
    payload: dict[str, Any], project_root: Path
) -> tuple[Path, Path, Path]:
    try:
        root = project_root.resolve(strict=True)
    except OSError as exc:
        raise ProbeRecordError("CODEBUDDY_PROJECT_DIR is unavailable") from exc
    if not root.is_dir() or root.is_symlink():
        raise ProbeRecordError("CODEBUDDY_PROJECT_DIR must be a regular directory")

    cwd_text = _stable_text(payload, "cwd")
    transcript_text = _stable_text(payload, "transcript_path")
    try:
        cwd = Path(cwd_text).resolve(strict=True)
        transcript = Path(transcript_text).resolve(strict=True)
    except OSError as exc:
        raise ProbeRecordError("cwd or transcript_path cannot be resolved") from exc
    if not cwd.is_dir() or not _path_is_within(cwd, root):
        raise ProbeRecordError("hook cwd is outside CODEBUDDY_PROJECT_DIR")
    if not transcript.is_file() or transcript.is_symlink():
        raise ProbeRecordError("transcript_path must be a non-symlink regular file")
    return root, cwd, transcript


def _effective_tool_name(hook_tool_name: str, tool_input: Any) -> str | None:
    if _MCP_TOOL_NAME.fullmatch(hook_tool_name):
        return hook_tool_name
    if hook_tool_name != "DeferExecuteTool":
        return None
    if not isinstance(tool_input, dict):
        raise ProbeRecordError("DeferExecuteTool tool_input must be an object")

    selectors: list[tuple[tuple[object, ...], Any]] = []
    canonical_values: list[str] = []

    def visit(value: Any, path: tuple[object, ...]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child = (*path, key)
                if key == "tool_name":
                    selectors.append((child, item))
                visit(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, index))
        elif isinstance(value, str) and _MCP_TOOL_NAME.fullmatch(value):
            canonical_values.append(value)

    visit(tool_input, ())
    if (
        len(selectors) != 1
        or selectors[0][0] != ("tool_name",)
        or not isinstance(selectors[0][1], str)
        or _MCP_TOOL_NAME.fullmatch(selectors[0][1]) is None
        or canonical_values != [selectors[0][1]]
    ):
        raise ProbeRecordError(
            "DeferExecuteTool requires exactly one canonical top-level tool_name"
        )
    return selectors[0][1]


def _operation_tokens(tool_name: str, tool_input: Any) -> list[str]:
    values = [tool_name.rsplit("__", 1)[-1]]

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in _OPERATION_KEYS and isinstance(item, str):
                    values.append(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(tool_input)
    return [
        re.sub(r"[^a-z0-9]+", "_", item.strip().casefold()).strip("_")
        for item in values
        if item.strip()
    ]


def _classify_operation(tool_name: str, tool_input: Any) -> tuple[str, str]:
    tokens = _operation_tokens(tool_name, tool_input)
    joined = "_".join(tokens)
    for operation, hints in _MUTATION_HINTS:
        if any(hint in joined for hint in hints):
            return "mutation", operation
    if any(hint in joined for hint in _READ_HINTS):
        return "read", "read"
    if any(token in joined for token in ("drag", "drop", "hover", "submit", "save")):
        return "mutation", "other_mutation"
    return "unknown", "unknown"


def _response_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _response_error(value: Any) -> bool:
    if isinstance(value, str):
        return re.match(
            r"^\s*(?:error|failed|failure|exception)\b",
            value,
            flags=re.IGNORECASE,
        ) is not None
    if not isinstance(value, dict):
        return False
    if value.get("is_error") is True or value.get("isError") is True:
        return True
    if value.get("success") is False or value.get("ok") is False:
        return True
    status = value.get("status")
    if isinstance(status, str) and status.casefold() in {
        "error",
        "failed",
        "failure",
        "cancelled",
        "canceled",
    }:
        return True
    for field in ("error", "exception"):
        nested = value.get(field)
        if nested not in (None, False, "", [], {}, ()):
            return True
    content = value.get("content")
    if isinstance(content, dict):
        return _response_error(content)
    if isinstance(content, (list, tuple)):
        return any(_response_error(item) for item in content)
    return False


def _validate_record_shape(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict) or set(record) != _RECORD_FIELDS:
        raise ProbeRecordError("page probe spool record has an invalid field set")
    if record.get("schema_version") != PAGE_PROBE_RECORD_SCHEMA_VERSION:
        raise ProbeRecordError("page probe spool record schema version mismatch")
    if record.get("recorder_version") != PAGE_PROBE_RECORDER_VERSION:
        raise ProbeRecordError("page probe spool recorder version mismatch")
    if not isinstance(record.get("sequence"), int) or record["sequence"] < 1:
        raise ProbeRecordError("page probe spool sequence is invalid")
    for field in (
        "record_id",
        "session_sha256",
        "transcript_path_sha256",
        "cwd_sha256",
        "project_root_sha256",
        "tool_input_sha256",
        "tool_response_sha256",
        "call_content_sha256",
    ):
        if not isinstance(record.get(field), str) or _FINGERPRINT.fullmatch(record[field]) is None:
            raise ProbeRecordError(f"page probe spool fingerprint is invalid: {field}")
    previous = record.get("previous_record_id")
    if previous is not None and (
        not isinstance(previous, str) or _FINGERPRINT.fullmatch(previous) is None
    ):
        raise ProbeRecordError("page probe spool previous_record_id is invalid")
    if record["sequence"] == 1 and previous is not None:
        raise ProbeRecordError("first page probe spool record cannot have a predecessor")
    if record["sequence"] > 1 and previous is None:
        raise ProbeRecordError("non-first page probe spool record requires a predecessor")
    if not isinstance(record.get("recorded_at"), str) or not record["recorded_at"].endswith("Z"):
        raise ProbeRecordError("page probe spool timestamp is invalid")
    if not isinstance(record.get("hook_tool_name"), str):
        raise ProbeRecordError("page probe spool hook_tool_name is invalid")
    if not isinstance(record.get("tool_name"), str) or _MCP_TOOL_NAME.fullmatch(record["tool_name"]) is None:
        raise ProbeRecordError("page probe spool canonical tool_name is invalid")
    for field in ("tool_input_bytes", "tool_response_bytes"):
        if not isinstance(record.get(field), int) or record[field] < 0:
            raise ProbeRecordError(f"page probe spool byte count is invalid: {field}")
    for field in ("response_nonempty", "response_error"):
        if type(record.get(field)) is not bool:
            raise ProbeRecordError(f"page probe spool boolean is invalid: {field}")
    if record.get("operation_kind") not in {"read", "mutation", "unknown"}:
        raise ProbeRecordError("page probe spool operation_kind is invalid")
    if record.get("operation_name") not in {
        "read",
        "click",
        "select",
        "input",
        "toggle",
        "expand",
        "navigate",
        "other_mutation",
        "unknown",
    }:
        raise ProbeRecordError("page probe spool operation_name is invalid")
    expected_id = _sha256_bytes(
        _canonical_bytes({key: value for key, value in record.items() if key != "record_id"})
    )
    if record["record_id"] != expected_id:
        raise ProbeRecordError("page probe spool record_id content hash mismatch")
    return record


def _spool_path(root: Path, session_sha256: str, transcript_sha256: str) -> Path:
    return (
        root
        / ".test-design-locks"
        / "page-probe-spool"
        / f"{session_sha256}-{transcript_sha256}.jsonl"
    )


def _prepare_spool_path(path: Path, root: Path) -> None:
    lock_root = root / ".test-design-locks"
    if lock_root.exists() and (lock_root.is_symlink() or not lock_root.is_dir()):
        raise ProbeRecordError(".test-design-locks must be a non-symlink directory")
    lock_root.mkdir(exist_ok=True)
    spool_root = lock_root / "page-probe-spool"
    if spool_root.exists() and (spool_root.is_symlink() or not spool_root.is_dir()):
        raise ProbeRecordError("page probe spool root must be a non-symlink directory")
    spool_root.mkdir(exist_ok=True)
    if not _path_is_within(spool_root.resolve(strict=True), root):
        raise ProbeRecordError("page probe spool root escapes CODEBUDDY_PROJECT_DIR")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise ProbeRecordError("page probe spool must be a non-symlink regular file")


@contextmanager
def _exclusive_lock(lock_path: Path) -> Iterator[None]:
    stream = None
    try:
        stream = lock_path.open("a+b")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield
    except ProbeRecordError:
        raise
    except OSError as exc:
        raise ProbeRecordError("cannot acquire page probe spool lock") from exc
    finally:
        if stream is not None:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            stream.close()


def _last_spool_record(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        size = path.stat().st_size
        if size == 0:
            return None
        with path.open("rb") as stream:
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) != b"\n":
                raise ProbeRecordError("page probe spool has an incomplete trailing record")
            position = size - 2
            while position >= 0:
                stream.seek(position)
                if stream.read(1) == b"\n":
                    position += 1
                    break
                position -= 1
            if position < 0:
                position = 0
            stream.seek(position)
            raw = stream.readline()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except ProbeRecordError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProbeRecordError("page probe spool trailing record is invalid") from exc
    return _validate_record_shape(value)


def _append_record(path: Path, record: dict[str, Any]) -> None:
    raw = _canonical_bytes(record) + b"\n"
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = None
    try:
        descriptor = os.open(path, flags, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short append")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise ProbeRecordError("cannot append page probe spool record") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def record_event(payload: Any, project_root: Path) -> tuple[Path, dict[str, Any]] | None:
    """Record one page-MCP PostToolUse event, or ignore an unrelated tool."""

    if not isinstance(payload, dict):
        raise ProbeRecordError("hook input must be a JSON object")
    session_id = _stable_text(payload, "session_id")
    hook_tool_name = _stable_text(payload, "tool_name")
    if "tool_input" not in payload or "tool_response" not in payload:
        raise ProbeRecordError("hook input is missing tool_input or tool_response")
    tool_input = payload["tool_input"]
    tool_response = payload["tool_response"]
    tool_name = _effective_tool_name(hook_tool_name, tool_input)
    if tool_name is None:
        return None

    root, cwd, transcript = _validated_project_context(payload, project_root)
    input_bytes = _canonical_bytes(tool_input)
    response_bytes = _canonical_bytes(tool_response)
    session_sha256 = _sha256_text(session_id)
    transcript_sha256 = _sha256_text(_normalized_path_text(transcript))
    cwd_sha256 = _sha256_text(_normalized_path_text(cwd))
    root_sha256 = _sha256_text(_normalized_path_text(root))
    operation_kind, operation_name = _classify_operation(tool_name, tool_input)
    call_content_sha256 = _sha256_bytes(
        _canonical_bytes(
            {
                "session_id": session_id,
                "transcript_path": _normalized_path_text(transcript),
                "cwd": _normalized_path_text(cwd),
                "hook_tool_name": hook_tool_name,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_response": tool_response,
            }
        )
    )
    path = _spool_path(root, session_sha256, transcript_sha256)
    _prepare_spool_path(path, root)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with _exclusive_lock(lock_path):
        previous = _last_spool_record(path)
        sequence = 1 if previous is None else int(previous["sequence"]) + 1
        record: dict[str, Any] = {
            "schema_version": PAGE_PROBE_RECORD_SCHEMA_VERSION,
            "recorder_version": PAGE_PROBE_RECORDER_VERSION,
            "sequence": sequence,
            "previous_record_id": None if previous is None else previous["record_id"],
            "recorded_at": datetime.now(timezone.utc).isoformat(
                timespec="microseconds"
            ).replace("+00:00", "Z"),
            "session_sha256": session_sha256,
            "transcript_path_sha256": transcript_sha256,
            "cwd_sha256": cwd_sha256,
            "project_root_sha256": root_sha256,
            "hook_tool_name": hook_tool_name,
            "tool_name": tool_name,
            "tool_input_sha256": _sha256_bytes(input_bytes),
            "tool_response_sha256": _sha256_bytes(response_bytes),
            "call_content_sha256": call_content_sha256,
            "tool_input_bytes": len(input_bytes),
            "tool_response_bytes": len(response_bytes),
            "response_nonempty": _response_nonempty(tool_response),
            "response_error": _response_error(tool_response),
            "operation_kind": operation_kind,
            "operation_name": operation_name,
        }
        record["record_id"] = _sha256_bytes(_canonical_bytes(record))
        _validate_record_shape(record)
        _append_record(path, record)
    return path, record


def _post_tool_output(record: dict[str, Any]) -> dict[str, Any]:
    context = {
        "record_id": record["record_id"],
        "session_sha256": record["session_sha256"],
        "transcript_sha256": record["transcript_path_sha256"],
        "tool_name": record["tool_name"],
        "operation_kind": record["operation_kind"],
    }
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "PAGE_PROBE_RECORD="
            + json.dumps(
                context,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
    }


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(_MAX_HOOK_INPUT_BYTES + 1)
        if len(raw) > _MAX_HOOK_INPUT_BYTES:
            raise ProbeRecordError("hook input exceeds the recorder size limit")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        configured_root = os.environ.get("CODEBUDDY_PROJECT_DIR")
        if not configured_root:
            raise ProbeRecordError("CODEBUDDY_PROJECT_DIR is not set")
        result = record_event(payload, Path(configured_root))
        if result is not None:
            _, record = result
            print(
                json.dumps(
                    _post_tool_output(record),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        return 0
    except (ProbeRecordError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"CodeBuddy page probe recorder denied event: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print(
            "CodeBuddy page probe recorder failed unexpectedly; event was not recorded",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
