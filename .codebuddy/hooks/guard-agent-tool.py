#!/usr/bin/env python3
"""Fail-closed CodeBuddy PreToolUse guard for test-design execution.

Canonical sub-agents remain bound to one durable claim.  The coordinator is
also prevented from writing formal ledgers, accepted data, orchestration state,
or publication paths directly.  When native Agent dispatch is unavailable,
only a supervisor-authorized isolated fallback claim may write that task's
exact output allowlist; all promotion, review, and delivery still belongs to
the deterministic runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


TEST_DESIGN_AGENT_GUARD = "4.1.0"
_GUARD_CACHE_VERSION = 4

_EXECUTION_BINDING_SCHEMA_VERSION = "1.0.0"
_EXECUTION_BINDING_DIR = ".test-design-locks/agent-execution-bindings"
_EXECUTION_BINDING_FIELDS = {
    "schema_version",
    "guard_version",
    "run_dir_sha256",
    "run_id",
    "batch_id",
    "task_id",
    "execution_id",
    "coordinator_id",
    "executor_id",
    "executor_kind",
    "source_fingerprint",
    "input_snapshot_fingerprint",
    "task_packet_fingerprint",
    "context_fingerprint",
    "claim_fingerprint",
    "transcript_path",
    "transcript_path_sha256",
    "transcript_parent_name",
    "transcript_file_name",
    "transcript_bound_size",
    "transcript_prefix_sha256",
    "transcript_device",
    "transcript_inode",
    "binding_fingerprint",
}

_ROLES = {"discovery", "plan_dfx", "risk_arbiter", "case_worker", "reviewer"}
_ROLE_PHASES = {
    "discovery": "discovery",
    "plan_dfx": "plan",
    "risk_arbiter": "risk",
    "case_worker": "cases",
    "reviewer": "review",
}
_ROLE_GATES = {
    "discovery": "discovery",
    "plan_dfx": "plan",
    "risk_arbiter": "risk",
    "case_worker": "cases-worker",
    "reviewer": "review",
}
_TASK_FIELDS = {
    "schema_version",
    "task_id",
    "run_id",
    "batch_id",
    "phase",
    "agent_role",
    "owner_key",
    "input_files",
    "allowed_output_files",
    "allowed_output_prefixes",
    "required_gate",
    "source_fingerprint",
    "attempt",
}
_CLAIM_FIELDS = {
    "schema_version",
    "execution_id",
    "task_id",
    "coordinator_id",
    "executor_id",
    "executor_kind",
    "wave_id",
    "claimed_at",
    "source_fingerprint",
    "input_snapshot_fingerprint",
    "task_packet_fingerprint",
    "context_fingerprint",
    "page_probe_receipt_id",
    "page_probe_receipt_fingerprint",
    "approved_page_mcp_tools",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "architecture",
    "agent_mode",
    "run_id",
    "batch_id",
    "created_at",
    "updated_at",
    "config_path",
    "state_machine",
    "tasks",
    "case_task_order",
}
_EVENT_FIELDS = {
    "schema_version",
    "sequence",
    "event_id",
    "occurred_at",
    "event_type",
    "actor",
    "task_id",
    "payload",
    "previous_hash",
    "event_hash",
}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_SUBAGENT_TRANSCRIPT = re.compile(
    r"^agent-[A-Za-z0-9._-]+\.jsonl$", re.IGNORECASE
)
_MCP_TOOL_NAME = re.compile(r"^mcp__([A-Za-z0-9_.:-]+)__([A-Za-z0-9_.:-]+)$")
_PAGE_PROBE_RECEIPT_FIELDS = {
    "schema_version", "receipt_id", "receipt_fingerprint", "run_id", "batch_id",
    "task_id", "execution_id", "coordinator_id", "source_fingerprint",
    "committed_at", "probe_session_sha256", "probe_transcript_sha256",
    "mcp_server", "approved_mcp_tools", "records", "evidence",
}
_PAGE_PROBE_LINK_FIELDS = {
    "receipt_id", "receipt_path", "receipt_fingerprint", "execution_id",
    "coordinator_id", "source_fingerprint", "approved_page_mcp_tools", "status",
}
_PAGE_PROBE_RECORD_FIELDS = {
    "record_id", "sequence", "recorded_at", "tool_name", "operation_kind", "operation_name",
    "tool_input_sha256", "tool_response_sha256", "call_content_sha256",
    "response_nonempty", "response_error",
}
_PAGE_PROBE_EVIDENCE_FIELDS = {
    "path", "sha256", "bytes", "sidecar_path", "sidecar_sha256",
}
_PAGE_PROBE_CONSUMPTION_FIELDS = {
    "schema_version", "record_id", "receipt_id", "receipt_fingerprint",
    "run_dir_sha256", "run_id", "batch_id", "task_id", "execution_id",
    "coordinator_id", "source_fingerprint", "binding_fingerprint",
}
_READ_TOOLS = {"Read", "read_file"}
_WRITE_TOOLS = {"Write", "write_to_file", "replace_in_file"}
_DISCOVERY_META_TOOLS = {"ToolSearch", "DeferExecuteTool", "WaitForMcpServers"}
_ALWAYS_DENIED_TOOLS = {
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Grep",
    "Glob",
    "Bash",
    "PowerShell",
    "execute_command",
    "run_in_terminal",
}
_MATCHED_TOOLS = (
    _READ_TOOLS | _WRITE_TOOLS | _ALWAYS_DENIED_TOOLS | _DISCOVERY_META_TOOLS
)
_MAX_JSONL_LINE_BYTES = 64 * 1024 * 1024
_MAX_TASK_BYTES = 1024 * 1024
_MAX_CONTEXT_BYTES = 32 * 1024 * 1024
_MAX_MANIFEST_BYTES = 32 * 1024 * 1024


class GuardedTaskError(ValueError):
    """A sub-agent authorization could not be proven and must be denied."""


@dataclass
class TranscriptEvidence:
    task_paths: tuple[Path, ...]
    transcript_path: Path
    project_root: Path
    cache_path: Path
    cache_state: dict[str, Any]
    binding_checkpoint: dict[str, Any] | None = None


@dataclass(frozen=True)
class GuardedTask:
    task_path: Path
    context_path: Path
    run_dir: Path
    execution_id: str
    executor_id: str
    agent_role: str
    approved_page_mcp_tools: tuple[str, ...]
    readable_files: frozenset[str]
    exact_output_files: frozenset[str]
    output_prefixes: tuple[str, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _path_key(path: Path) -> str:
    value = str(path.resolve(strict=False)).replace("\\", "/").rstrip("/")
    return value.casefold() if os.name == "nt" else value


def _is_key_within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent + "/")


def _stat_signature(path: Path) -> dict[str, int]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise GuardedTaskError(f"无法读取文件状态：{path}") from exc
    return {
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "ctime_ns": int(stat.st_ctime_ns),
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
    }


def _file_identity(signature: dict[str, int]) -> tuple[int, int]:
    return signature["device"], signature["inode"]


def _verify_append_only_prefix(
    path: Path,
    cached: dict[str, Any],
    current_signature: dict[str, int],
    *,
    label: str,
) -> tuple[int, Any]:
    required = {
        "processed_size",
        "signature",
        "prefix_sha256",
    }
    if not isinstance(cached, dict) or not required.issubset(cached):
        raise GuardedTaskError(f"{label} 增量校验缓存结构无效")
    old_signature = cached.get("signature")
    if not isinstance(old_signature, dict) or set(old_signature) != {
        "size", "mtime_ns", "ctime_ns", "device", "inode"
    }:
        raise GuardedTaskError(f"{label} 增量校验缓存签名无效")
    try:
        processed_size = int(cached["processed_size"])
    except (TypeError, ValueError) as exc:
        raise GuardedTaskError(f"{label} 增量校验缓存偏移无效") from exc
    if processed_size < 0 or processed_size != int(old_signature["size"]):
        raise GuardedTaskError(f"{label} 增量校验缓存大小无效")
    if current_signature["size"] < processed_size:
        raise GuardedTaskError(f"{label} 已被截断；拒绝重新索引以防绕过历史约束")
    if _file_identity(current_signature) != _file_identity(old_signature):
        raise GuardedTaskError(f"{label} 文件身份已替换")
    prefix_sha256 = cached.get("prefix_sha256")
    if not isinstance(prefix_sha256, str) or _FINGERPRINT.fullmatch(prefix_sha256) is None:
        raise GuardedTaskError(f"{label} 增量校验缓存 prefix_sha256 无效")
    hasher = hashlib.sha256()
    remaining = processed_size
    try:
        with path.open("rb") as stream:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise GuardedTaskError(f"{label} 在完整前缀哈希期间发生截断")
                hasher.update(chunk)
                remaining -= len(chunk)
    except GuardedTaskError:
        raise
    except OSError as exc:
        raise GuardedTaskError(f"无法重算 {label} 完整前缀哈希") from exc
    if hasher.hexdigest() != prefix_sha256:
        raise GuardedTaskError(f"{label} 已改写既有前缀")
    if current_signature["size"] == processed_size and current_signature != old_signature:
        raise GuardedTaskError(f"{label} 在未追加内容时发生元数据变化")
    if processed_size:
        try:
            with path.open("rb") as stream:
                stream.seek(processed_size - 1)
                boundary = stream.read(1)
        except OSError as exc:
            raise GuardedTaskError(f"无法读取 {label} JSONL 记录边界") from exc
        if boundary != b"\n":
            raise GuardedTaskError(f"{label} 已缓存前缀不是完整 JSONL 记录边界")
    return processed_size, hasher


def _guard_cache_path(project_root: Path, transcript_path: Path) -> Path:
    configured = os.environ.get("CODEBUDDY_TEST_DESIGN_GUARD_CACHE_DIR")
    if configured:
        base = Path(configured)
    else:
        local = os.environ.get("LOCALAPPDATA")
        base = (
            Path(local) / "CodeBuddy" / "test-design-guard-cache"
            if local
            else Path(tempfile.gettempdir()) / "codebuddy-test-design-guard-cache"
        )
    project_digest = hashlib.sha256(_path_key(project_root).encode("utf-8")).hexdigest()
    transcript_digest = hashlib.sha256(
        _path_key(transcript_path).encode("utf-8")
    ).hexdigest()
    return (
        base
        / f"guard-{TEST_DESIGN_AGENT_GUARD}-cache-{_GUARD_CACHE_VERSION}"
        / project_digest
        / f"{transcript_digest}.json"
    )


@contextmanager
def _exclusive_guard_cache(cache_path: Path):
    """Serialize read/verify/checkpoint for one physical sub-agent transcript."""

    lock_path = cache_path.with_suffix(cache_path.suffix + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
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
    except OSError as exc:
        raise GuardedTaskError("无法取得 guard 增量缓存排他锁") from exc
    try:
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _new_cache_state(project_root: Path, transcript_path: Path) -> dict[str, Any]:
    return {
        "schema_version": _GUARD_CACHE_VERSION,
        "guard_version": TEST_DESIGN_AGENT_GUARD,
        "project_root": _path_key(project_root),
        "transcript_path": _path_key(transcript_path),
        "poisoned": False,
        "poison_reason": None,
        "transcript": None,
        "claim_digest": None,
        "execution_binding_fingerprint": None,
        "fingerprints": {},
        "event_chain": None,
    }


def _read_guard_cache(
    cache_path: Path, project_root: Path, transcript_path: Path
) -> dict[str, Any] | None:
    if not cache_path.exists():
        return None
    try:
        if not cache_path.is_file() or cache_path.is_symlink():
            raise GuardedTaskError("guard 增量缓存不是普通文件")
        value = json.loads(
            cache_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except GuardedTaskError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise GuardedTaskError("guard 增量缓存无法可信解析") from exc
    expected_fields = {
        "schema_version",
        "guard_version",
        "project_root",
        "transcript_path",
        "poisoned",
        "poison_reason",
        "transcript",
        "claim_digest",
        "execution_binding_fingerprint",
        "fingerprints",
        "event_chain",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise GuardedTaskError("guard 增量缓存字段集合无效")
    if (
        value.get("schema_version") != _GUARD_CACHE_VERSION
        or value.get("guard_version") != TEST_DESIGN_AGENT_GUARD
        or value.get("project_root") != _path_key(project_root)
        or value.get("transcript_path") != _path_key(transcript_path)
        or type(value.get("poisoned")) is not bool
        or (
            value.get("execution_binding_fingerprint") is not None
            and (
                not isinstance(value.get("execution_binding_fingerprint"), str)
                or _FINGERPRINT.fullmatch(value["execution_binding_fingerprint"]) is None
            )
        )
        or not isinstance(value.get("fingerprints"), dict)
    ):
        raise GuardedTaskError("guard 增量缓存身份无效")
    if value["poisoned"]:
        raise GuardedTaskError(
            "guard 增量缓存已失败关闭：" + str(value.get("poison_reason") or "未知原因")
        )
    return value


def _write_guard_cache(cache_path: Path, value: dict[str, Any]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, cache_path)
    except OSError as exc:
        raise GuardedTaskError("无法持久化 guard 增量校验缓存") from exc


def _poison_guard_cache(
    cache_path: Path,
    project_root: Path,
    transcript_path: Path,
    previous: dict[str, Any] | None,
    reason: str,
) -> None:
    value = dict(previous) if isinstance(previous, dict) else _new_cache_state(
        project_root, transcript_path
    )
    value["poisoned"] = True
    value["poison_reason"] = reason[:1000]
    _write_guard_cache(cache_path, value)


def _cached_fingerprint(
    paths: list[Path], evidence: TranscriptEvidence, *, label: str
) -> str:
    fingerprints = evidence.cache_state.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise GuardedTaskError("guard immutable fingerprint 缓存无效")
    resolved = [path.resolve(strict=True) for path in paths]
    path_keys = [_path_key(path) for path in resolved]
    signatures = [_stat_signature(path) for path in resolved]
    cached = fingerprints.get(label)
    if cached is not None:
        if (
            not isinstance(cached, dict)
            or cached.get("paths") != path_keys
            or cached.get("signatures") != signatures
            or not isinstance(cached.get("fingerprint"), str)
        ):
            raise GuardedTaskError(f"{label} 冻结文件在 claim 期间发生变化")
        return str(cached["fingerprint"])
    value = _fingerprint(resolved)
    fingerprints[label] = {
        "paths": path_keys,
        "signatures": signatures,
        "fingerprint": value,
    }
    return value


def _fingerprint(paths: list[Path]) -> str:
    """Match scripts.test_design.validation_cache.fingerprint for regular files."""

    digest = hashlib.sha256()
    for path in sorted({item.resolve() for item in paths}, key=str):
        digest.update(str(path).encode("utf-8"))
        if not path.exists():
            digest.update(b"<missing>")
            continue
        if path.is_dir():
            digest.update(b"<directory>")
            for child in sorted(
                item.name for item in path.iterdir() if item.name != ".validation-cache.json"
            ):
                digest.update(child.encode("utf-8"))
            continue
        digest.update(str(path.stat().st_size).encode("ascii"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise GuardedTaskError("事件账本包含不可规范化 JSON") from exc


def _execution_binding_path(project_root: Path, execution_id: str) -> Path:
    digest = hashlib.sha256(execution_id.encode("utf-8")).hexdigest()
    return project_root / _EXECUTION_BINDING_DIR / f"{digest}.json"


def _read_existing_execution_binding(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
            raise GuardedTaskError("物理 sub-agent execution binding 不是可信普通文件")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except GuardedTaskError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise GuardedTaskError("物理 sub-agent execution binding 无法可信解析") from exc
    if not isinstance(value, dict) or set(value) != _EXECUTION_BINDING_FIELDS:
        raise GuardedTaskError("物理 sub-agent execution binding 字段集无效")
    return value


def _verify_execution_binding_checkpoint(
    binding: dict[str, Any], evidence: TranscriptEvidence
) -> None:
    content = {
        key: value for key, value in binding.items() if key != "binding_fingerprint"
    }
    if binding.get("binding_fingerprint") != hashlib.sha256(
        _canonical_json(content).encode("utf-8")
    ).hexdigest():
        raise GuardedTaskError("物理 sub-agent execution binding 指纹无效")
    transcript = evidence.transcript_path
    normalized = _path_key(transcript)
    parent_is_canonical = (
        transcript.parent.name.casefold() == "subagents"
        if os.name == "nt"
        else transcript.parent.name == "subagents"
    )
    bound_size = binding.get("transcript_bound_size")
    prefix_sha256 = binding.get("transcript_prefix_sha256")
    device = binding.get("transcript_device")
    inode = binding.get("transcript_inode")
    if (
        binding.get("transcript_path") != normalized
        or binding.get("transcript_path_sha256")
        != hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        or binding.get("transcript_parent_name") != "subagents"
        or not parent_is_canonical
        or binding.get("transcript_file_name")
        != (transcript.name.casefold() if os.name == "nt" else transcript.name)
        or _SUBAGENT_TRANSCRIPT.fullmatch(transcript.name) is None
        or not isinstance(bound_size, int)
        or isinstance(bound_size, bool)
        or bound_size <= 0
        or not isinstance(prefix_sha256, str)
        or _FINGERPRINT.fullmatch(prefix_sha256) is None
        or (device is not None and (not isinstance(device, int) or isinstance(device, bool)))
        or (inode is not None and (not isinstance(inode, int) or isinstance(inode, bool)))
    ):
        raise GuardedTaskError("物理 sub-agent transcript checkpoint 字段无效")
    try:
        if transcript.is_symlink() or not transcript.is_file():
            raise GuardedTaskError("物理 sub-agent transcript 已删除或被替换")
        signature = _stat_signature(transcript)
        if signature["size"] < bound_size:
            raise GuardedTaskError("物理 sub-agent transcript 已被截断")
        if device is not None and signature["device"] != device:
            raise GuardedTaskError("物理 sub-agent transcript device 发生变化")
        if inode is not None and signature["inode"] != inode:
            raise GuardedTaskError("物理 sub-agent transcript inode 发生变化")
        digest = hashlib.sha256()
        remaining = bound_size
        with transcript.open("rb") as stream:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise GuardedTaskError("物理 sub-agent transcript checkpoint 不完整")
                digest.update(chunk)
                remaining -= len(chunk)
        if digest.hexdigest() != prefix_sha256:
            raise GuardedTaskError("物理 sub-agent transcript 首次绑定前缀已被篡改")
    except GuardedTaskError:
        raise
    except OSError as exc:
        raise GuardedTaskError("无法重验物理 sub-agent transcript checkpoint") from exc


def _bind_physical_subagent_execution(
    project_root: Path,
    run_dir: Path,
    task: dict[str, Any],
    claim: dict[str, Any],
    evidence: TranscriptEvidence,
) -> None:
    """Atomically bind one claim to one canonical physical transcript.

    The execution-id keyed marker is intentionally project-global and is never
    removed by claim release.  A hard-link promotion gives no-replace semantics
    even when two different transcripts race to use the same claim.
    """

    root = project_root.resolve(strict=True)
    binding_root = root / _EXECUTION_BINDING_DIR
    try:
        binding_root.mkdir(parents=True, exist_ok=True)
        resolved_binding_root = binding_root.resolve(strict=True)
    except OSError as exc:
        raise GuardedTaskError("无法创建物理 sub-agent execution binding 注册表") from exc
    if (
        binding_root.is_symlink()
        or not binding_root.is_dir()
        or not _is_key_within(_path_key(resolved_binding_root), _path_key(root))
    ):
        raise GuardedTaskError("物理 sub-agent execution binding 注册表路径不安全")

    normalized_transcript = _path_key(evidence.transcript_path)
    base_content: dict[str, Any] = {
        "schema_version": _EXECUTION_BINDING_SCHEMA_VERSION,
        "guard_version": TEST_DESIGN_AGENT_GUARD,
        "run_dir_sha256": hashlib.sha256(_path_key(run_dir).encode("utf-8")).hexdigest(),
        "run_id": task["run_id"],
        "batch_id": task["batch_id"],
        "task_id": task["task_id"],
        "execution_id": claim["execution_id"],
        "coordinator_id": claim["coordinator_id"],
        "executor_id": claim["executor_id"],
        "executor_kind": claim["executor_kind"],
        "source_fingerprint": task["source_fingerprint"],
        "input_snapshot_fingerprint": claim["input_snapshot_fingerprint"],
        "task_packet_fingerprint": claim["task_packet_fingerprint"],
        "context_fingerprint": claim["context_fingerprint"],
        "claim_fingerprint": hashlib.sha256(
            _canonical_json(claim).encode("utf-8")
        ).hexdigest(),
        "transcript_path": normalized_transcript,
        "transcript_path_sha256": hashlib.sha256(
            normalized_transcript.encode("utf-8")
        ).hexdigest(),
        "transcript_parent_name": "subagents",
        "transcript_file_name": (
            evidence.transcript_path.name.casefold()
            if os.name == "nt"
            else evidence.transcript_path.name
        ),
    }
    marker_path = _execution_binding_path(root, str(claim["execution_id"]))
    cached_binding = evidence.cache_state.get("execution_binding_fingerprint")

    if marker_path.exists() or marker_path.is_symlink():
        existing = _read_existing_execution_binding(marker_path)
        if any(existing.get(key) != value for key, value in base_content.items()):
            raise GuardedTaskError(
                "execution_id 已绑定其他物理 sub-agent transcript"
            )
        _verify_execution_binding_checkpoint(existing, evidence)
        if (
            cached_binding is not None
            and cached_binding != existing["binding_fingerprint"]
        ):
            raise GuardedTaskError(
                "同一物理 sub-agent transcript 的 execution binding 发生切换"
            )
        evidence.cache_state["execution_binding_fingerprint"] = existing[
            "binding_fingerprint"
        ]
        return
    if cached_binding is not None:
        raise GuardedTaskError(
            "已持久化的物理 sub-agent execution binding 被删除"
        )

    checkpoint = evidence.binding_checkpoint
    if not isinstance(checkpoint, dict):
        raise GuardedTaskError(
            "首次物理 sub-agent execution binding 缺少 transcript checkpoint"
        )
    signature = checkpoint.get("signature")
    bound_size = checkpoint.get("processed_size")
    prefix_sha256 = checkpoint.get("prefix_sha256")
    if (
        not isinstance(signature, dict)
        or not isinstance(bound_size, int)
        or isinstance(bound_size, bool)
        or bound_size <= 0
        or not isinstance(prefix_sha256, str)
        or _FINGERPRINT.fullmatch(prefix_sha256) is None
    ):
        raise GuardedTaskError("首次 transcript checkpoint 结构无效")
    device = signature.get("device")
    inode = signature.get("inode")
    content = {
        **base_content,
        "transcript_bound_size": bound_size,
        "transcript_prefix_sha256": prefix_sha256,
        "transcript_device": (
            device if isinstance(device, int) and not isinstance(device, bool) and device > 0 else None
        ),
        "transcript_inode": (
            inode if isinstance(inode, int) and not isinstance(inode, bool) and inode > 0 else None
        ),
    }
    expected = {
        **content,
        "binding_fingerprint": hashlib.sha256(
            _canonical_json(content).encode("utf-8")
        ).hexdigest(),
    }
    _verify_execution_binding_checkpoint(expected, evidence)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{marker_path.name}.",
            suffix=".tmp",
            dir=binding_root,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(expected, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, marker_path)
        except FileExistsError:
            pass
        except OSError as exc:
            raise GuardedTaskError(
                "无法以原子 no-replace 方式绑定物理 sub-agent execution"
            ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    if _read_existing_execution_binding(marker_path) != expected:
        raise GuardedTaskError(
            "execution_id 已绑定其他物理 sub-agent transcript"
        )
    evidence.cache_state["execution_binding_fingerprint"] = expected[
        "binding_fingerprint"
    ]


def _event_hash(record_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(record_without_hash).encode("utf-8")).hexdigest()


def _verify_unique_claim_event(
    events_path: Path,
    task_id: str,
    claim: dict[str, Any],
    evidence: TranscriptEvidence,
    *,
    receipt: dict[str, Any] | None,
) -> None:
    """Incrementally verify the append-only event ledger and current full claim."""

    claim_digest = hashlib.sha256(_canonical_json(claim).encode("utf-8")).hexdigest()
    receipt_digest = (
        hashlib.sha256(_canonical_json(receipt).encode("utf-8")).hexdigest()
        if receipt is not None
        else None
    )
    try:
        if not events_path.is_file() or events_path.is_symlink():
            raise GuardedTaskError("orchestration/events.jsonl 不存在或不是普通文件")
        current_signature = _stat_signature(events_path)
        cached = evidence.cache_state.get("event_chain")
        if cached is None:
            offset = 0
            prefix_hasher = hashlib.sha256()
            sequence = 0
            previous_hash = "0" * 64
            current_execution_events = 0
            receipt_reservations = 0
            receipt_commits = 0
            receipt_tombstones = 0
            receipt_reserved_sequence = 0
            receipt_committed_sequence = 0
            claim_sequence = 0
        else:
            if (
                not isinstance(cached, dict)
                or cached.get("path") != _path_key(events_path)
                or cached.get("task_id") != task_id
                or cached.get("claim_digest") != claim_digest
                or cached.get("receipt_digest") != receipt_digest
                or not isinstance(cached.get("sequence"), int)
                or not isinstance(cached.get("previous_hash"), str)
                or not isinstance(cached.get("current_execution_events"), int)
                or not isinstance(cached.get("receipt_reservations"), int)
                or not isinstance(cached.get("receipt_commits"), int)
                or not isinstance(cached.get("receipt_tombstones"), int)
                or not isinstance(cached.get("receipt_reserved_sequence"), int)
                or not isinstance(cached.get("receipt_committed_sequence"), int)
                or not isinstance(cached.get("claim_sequence"), int)
            ):
                raise GuardedTaskError("orchestration 事件增量缓存与当前 claim 不一致")
            offset, prefix_hasher = _verify_append_only_prefix(
                events_path,
                cached,
                current_signature,
                label="orchestration/events.jsonl",
            )
            sequence = int(cached["sequence"])
            previous_hash = str(cached["previous_hash"])
            current_execution_events = int(cached["current_execution_events"])
            receipt_reservations = int(cached["receipt_reservations"])
            receipt_commits = int(cached["receipt_commits"])
            receipt_tombstones = int(cached["receipt_tombstones"])
            receipt_reserved_sequence = int(cached["receipt_reserved_sequence"])
            receipt_committed_sequence = int(cached["receipt_committed_sequence"])
            claim_sequence = int(cached["claim_sequence"])

        try:
            stream = events_path.open("rb")
        except OSError as exc:
            raise GuardedTaskError("无法读取 orchestration/events.jsonl") from exc
        with stream:
            stream.seek(offset)
            while True:
                raw_line = stream.readline(_MAX_JSONL_LINE_BYTES + 1)
                if not raw_line:
                    break
                sequence += 1
                if len(raw_line) > _MAX_JSONL_LINE_BYTES or not raw_line.endswith(b"\n"):
                    raise GuardedTaskError(
                        f"orchestration/events.jsonl 第 {sequence} 行过大或不是完整 JSONL 记录"
                    )
                prefix_hasher.update(raw_line)
                if not raw_line.strip():
                    raise GuardedTaskError("orchestration/events.jsonl 含空记录")
                try:
                    record = json.loads(
                        raw_line.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
                    )
                except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    raise GuardedTaskError(
                        f"orchestration/events.jsonl 第 {sequence} 行无法可信解析"
                    ) from exc
                if not isinstance(record, dict) or set(record) != _EVENT_FIELDS:
                    raise GuardedTaskError("orchestration 事件字段集合无效")
                if (
                    record.get("schema_version") != 1
                    or record.get("sequence") != sequence
                    or isinstance(record.get("sequence"), bool)
                    or record.get("previous_hash") != previous_hash
                    or not isinstance(record.get("payload"), dict)
                ):
                    raise GuardedTaskError("orchestration 事件序列或哈希链无效")
                for field in ("event_id", "occurred_at", "event_type", "actor"):
                    if not isinstance(record.get(field), str) or not record[field].strip():
                        raise GuardedTaskError(f"orchestration 事件 {field} 无效")
                recorded_hash = record.get("event_hash")
                hash_input = dict(record)
                hash_input.pop("event_hash", None)
                if (
                    not isinstance(recorded_hash, str)
                    or not _FINGERPRINT.fullmatch(recorded_hash)
                    or _event_hash(hash_input) != recorded_hash
                ):
                    raise GuardedTaskError("orchestration 事件内容哈希无效")
                previous_hash = recorded_hash

                if (
                    record.get("task_id") == task_id
                    and record.get("event_type")
                    in {"TASK_CLAIMED", "AUDIT_CLAIM_RECOVERED"}
                ):
                    nested = record["payload"].get("claim")
                    if (
                        isinstance(nested, dict)
                        and nested.get("execution_id") == claim["execution_id"]
                    ):
                        if nested != claim:
                            raise GuardedTaskError(
                                "当前 execution_id 的 durable claim 与 manifest 冲突"
                            )
                        current_execution_events += 1
                        claim_sequence = sequence
                    elif record["payload"].get("execution_id") == claim["execution_id"]:
                        raise GuardedTaskError(
                            "当前 execution_id 仅有不完整 legacy claim 事件"
                        )
                if receipt is not None and record.get("task_id") == task_id:
                    event_type = record.get("event_type")
                    payload = record["payload"]
                    receipt_id = receipt["receipt_id"]
                    if event_type == "PAGE_PROBE_RECORDS_RESERVED":
                        reserved = payload.get("receipt")
                        if (
                            isinstance(reserved, dict)
                            and reserved.get("receipt_id") == receipt_id
                        ):
                            if reserved != receipt:
                                raise GuardedTaskError("page probe reservation 与 receipt 冲突")
                            receipt_reservations += 1
                            receipt_reserved_sequence = sequence
                    elif event_type in {"PAGE_PROBE_COMMITTED", "AUDIT_PAGE_PROBE_COMMITTED"}:
                        if payload.get("receipt_id") == receipt_id:
                            expected = {
                                "receipt_id": receipt_id,
                                "receipt_fingerprint": receipt["receipt_fingerprint"],
                                "execution_id": receipt["execution_id"],
                                "coordinator_id": receipt["coordinator_id"],
                                "source_fingerprint": receipt["source_fingerprint"],
                                "record_ids": [item["record_id"] for item in receipt["records"]],
                                "approved_page_mcp_tools": receipt["approved_mcp_tools"],
                                "mcp_server": receipt["mcp_server"],
                            }
                            if any(payload.get(key) != value for key, value in expected.items()):
                                raise GuardedTaskError("page probe commit event 与 receipt 冲突")
                            receipt_commits += 1
                            receipt_committed_sequence = sequence
                    elif event_type in {"PAGE_PROBE_TOMBSTONED", "AUDIT_PAGE_PROBE_TOMBSTONED"}:
                        if payload.get("receipt_id") == receipt_id:
                            receipt_tombstones += 1
            processed_size = stream.tell()

        final_signature = _stat_signature(events_path)
        if processed_size != final_signature["size"]:
            raise GuardedTaskError("orchestration/events.jsonl 在增量校验期间发生并发变化")
        if current_execution_events != 1:
            raise GuardedTaskError(
                "当前 manifest claim 必须且只能绑定一个完整 durable claim 事件"
            )
        if receipt is not None and (
            receipt_reservations != 1
            or receipt_commits != 1
            or receipt_tombstones != 0
            or not (
                0 < receipt_reserved_sequence < receipt_committed_sequence < claim_sequence
            )
        ):
            raise GuardedTaskError(
                "Discovery claim 必须绑定唯一、先提交且未 tombstone 的 page probe receipt"
            )
        evidence.cache_state["event_chain"] = {
            "path": _path_key(events_path),
            "task_id": task_id,
            "claim_digest": claim_digest,
            "receipt_digest": receipt_digest,
            "processed_size": processed_size,
            "signature": final_signature,
            "prefix_sha256": prefix_hasher.hexdigest(),
            "sequence": sequence,
            "previous_hash": previous_hash,
            "current_execution_events": current_execution_events,
            "receipt_reservations": receipt_reservations,
            "receipt_commits": receipt_commits,
            "receipt_tombstones": receipt_tombstones,
            "receipt_reserved_sequence": receipt_reserved_sequence,
            "receipt_committed_sequence": receipt_committed_sequence,
            "claim_sequence": claim_sequence,
        }
    except GuardedTaskError as exc:
        _poison_guard_cache(
            evidence.cache_path,
            evidence.project_root,
            evidence.transcript_path,
            evidence.cache_state,
            str(exc),
        )
        raise


def _canonical_subagent_transcript(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise GuardedTaskError("hook 缺少可信 transcript_path")
    path = Path(value)
    if not path.is_absolute():
        raise GuardedTaskError("transcript_path 不是绝对路径")
    parent_is_canonical = (
        path.parent.name.casefold() == "subagents"
        if os.name == "nt"
        else path.parent.name == "subagents"
    )
    if parent_is_canonical:
        if _SUBAGENT_TRANSCRIPT.fullmatch(path.name) is None:
            raise GuardedTaskError(
                "subagents 目录中的 transcript 文件名不符合 agent-*.jsonl；按失败关闭处理"
            )
        return path
    return None


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(key)
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def _normalise_embedded_paths(text: str) -> str:
    return text.replace("\\\\", "\\").replace("\\", "/")


def _task_path_pattern(project_root: Path) -> re.Pattern[str]:
    root = _normalise_embedded_paths(str(project_root.resolve(strict=True))).rstrip("/")
    component = r"[^/\r\n\"'<>|?*:\x00]+"
    role = "(?:" + "|".join(sorted(_ROLES)) + ")"
    return re.compile(
        re.escape(root)
        + rf"/(?:{component}/)*artifacts/agent-work/{role}/"
        + r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}/meta/agent-task\.json",
        re.IGNORECASE,
    )


def _visit_jsonl_strings(
    path: Path,
    *,
    offset: int,
    starting_line: int,
    label: str,
    visitor,
    byte_hasher=None,
) -> tuple[int, int, dict[str, int]]:
    try:
        stream = path.open("rb")
    except OSError as exc:
        raise GuardedTaskError(f"无法读取 {label}") from exc
    line_number = starting_line
    with stream:
        stream.seek(offset)
        while True:
            raw_line = stream.readline(_MAX_JSONL_LINE_BYTES + 1)
            if not raw_line:
                break
            line_number += 1
            if len(raw_line) > _MAX_JSONL_LINE_BYTES or not raw_line.endswith(b"\n"):
                raise GuardedTaskError(
                    f"{label} 第 {line_number} 行过大或不是完整 JSONL 记录"
                )
            if byte_hasher is not None:
                byte_hasher.update(raw_line)
            try:
                parsed = json.loads(
                    raw_line.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
                )
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise GuardedTaskError(
                    f"{label} 第 {line_number} 行不是可信 JSONL"
                ) from exc
            for candidate in _iter_strings(parsed):
                visitor(candidate)
        processed_size = stream.tell()
    signature = _stat_signature(path)
    if processed_size != signature["size"]:
        raise GuardedTaskError(f"{label} 在增量解析期间发生并发变化")
    return processed_size, line_number, signature


def _inspect_transcript(transcript_path: Path, project_root: Path) -> TranscriptEvidence:
    pattern = _task_path_pattern(project_root)
    cache_path = _guard_cache_path(project_root, transcript_path)
    cache_state: dict[str, Any] | None = None
    try:
        resolved = transcript_path.resolve(strict=True)
        if not resolved.is_file() or resolved.is_symlink():
            raise GuardedTaskError("sub-agent transcript 不是普通文件")
        cache_state = _read_guard_cache(cache_path, project_root, resolved)
        if cache_state is None:
            cache_state = _new_cache_state(project_root, resolved)
        transcript_cache = cache_state.get("transcript")
        signature = _stat_signature(resolved)
        if transcript_cache is None:
            offset = 0
            prefix_hasher = hashlib.sha256()
            line_count = 0
            found: dict[str, Path] = {}
        else:
            if (
                not isinstance(transcript_cache, dict)
                or transcript_cache.get("path") != _path_key(resolved)
                or not isinstance(transcript_cache.get("task_paths"), list)
                or not isinstance(transcript_cache.get("line_count"), int)
            ):
                raise GuardedTaskError("sub-agent transcript 增量缓存结构无效")
            offset, prefix_hasher = _verify_append_only_prefix(
                resolved,
                transcript_cache,
                signature,
                label="sub-agent transcript",
            )
            line_count = int(transcript_cache["line_count"])
            found = {
                _path_key(Path(value)): Path(value)
                for value in transcript_cache["task_paths"]
                if isinstance(value, str)
            }
            if len(found) != len(transcript_cache["task_paths"]):
                raise GuardedTaskError("sub-agent transcript 缓存任务路径无效")

        def inspect(candidate: str) -> None:
            normalised = _normalise_embedded_paths(candidate)
            for match in pattern.finditer(normalised):
                task_path = Path(match.group(0))
                found[_path_key(task_path)] = task_path
            if len(found) > 1:
                raise GuardedTaskError("canonical sub-agent transcript 含多个任务包")

        processed_size, line_count, final_signature = _visit_jsonl_strings(
            resolved,
            offset=offset,
            starting_line=line_count,
            label="sub-agent transcript",
            visitor=inspect,
            byte_hasher=prefix_hasher,
        )
        if not found:
            raise GuardedTaskError("canonical sub-agent 未绑定 agent-task.json")
        if len(found) != 1:
            raise GuardedTaskError("canonical sub-agent transcript 含多个任务包")
        cache_state["transcript"] = {
            "path": _path_key(resolved),
            "processed_size": processed_size,
            "signature": final_signature,
            "prefix_sha256": prefix_hasher.hexdigest(),
            "line_count": line_count,
            "task_paths": [str(path) for path in found.values()],
        }
        return TranscriptEvidence(
            task_paths=tuple(found.values()),
            transcript_path=resolved,
            project_root=project_root.resolve(strict=True),
            cache_path=cache_path,
            cache_state=cache_state,
        )
    except GuardedTaskError as exc:
        if cache_state is not None:
            _poison_guard_cache(
                cache_path, project_root, transcript_path, cache_state, str(exc)
            )
        raise
    except OSError as exc:
        raise GuardedTaskError("无法读取 sub-agent transcript") from exc


def _transcript_contains_claim_identity(
    evidence: TranscriptEvidence, execution_id: str, executor_id: str
) -> bool:
    found = {execution_id: False, executor_id: False}
    prefix_hasher = hashlib.sha256()

    def inspect(candidate: str) -> None:
        for value in found:
            if value in candidate:
                found[value] = True

    processed_size, _, signature = _visit_jsonl_strings(
        evidence.transcript_path,
        offset=0,
        starting_line=0,
        label="sub-agent transcript identity scan",
        visitor=inspect,
        byte_hasher=prefix_hasher,
    )
    evidence.binding_checkpoint = {
        "processed_size": processed_size,
        "prefix_sha256": prefix_hasher.hexdigest(),
        "signature": signature,
    }
    return all(found.values())


def _read_json_file(path: Path, *, label: str, max_bytes: int) -> tuple[Path, dict[str, Any]]:
    try:
        if not path.is_absolute():
            raise GuardedTaskError(f"{label} 路径不是绝对路径")
        if not path.is_file() or path.is_symlink() or path.stat().st_size > max_bytes:
            raise GuardedTaskError(f"{label} 不存在、不是普通文件或大小异常")
        resolved = path.resolve(strict=True)
        payload = json.loads(
            resolved.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except GuardedTaskError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise GuardedTaskError(f"{label} 无法可信解析") from exc
    if not isinstance(payload, dict):
        raise GuardedTaskError(f"{label} 必须是 JSON object")
    return resolved, payload


def _safe_relative_path(value: Any, *, prefix: bool = False) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise GuardedTaskError("任务包包含无效相对路径")
    if prefix != value.endswith("/"):
        raise GuardedTaskError("任务包文件/目录前缀格式不一致")
    pure = PurePosixPath(value.rstrip("/"))
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise GuardedTaskError("任务包路径不是规范 run-dir 相对路径")
    if any(part.endswith((".", " ")) or ":" in part for part in pure.parts):
        raise GuardedTaskError("任务包路径包含不安全文件名")
    return pure


def _string_array(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise GuardedTaskError(f"{label} 必须是数组")
    if not all(isinstance(item, str) for item in value) or len(value) != len(set(value)):
        raise GuardedTaskError(f"{label} 必须是无重复字符串数组")
    return list(value)


def _validate_task(task: dict[str, Any], role_from_path: str, task_id_from_path: str) -> None:
    if set(task) != _TASK_FIELDS:
        raise GuardedTaskError("AgentTask 字段集合不符合冻结契约")
    role = task.get("agent_role")
    task_id = task.get("task_id")
    if role != role_from_path or task_id != task_id_from_path or role not in _ROLES:
        raise GuardedTaskError("AgentTask 角色/task_id 与目录不一致")
    if task.get("schema_version") != "1.0.0" or not _IDENTIFIER.fullmatch(str(task_id)):
        raise GuardedTaskError("AgentTask schema_version/task_id 无效")
    if task.get("phase") != _ROLE_PHASES[role] or task.get("required_gate") != _ROLE_GATES[role]:
        raise GuardedTaskError("AgentTask phase/required_gate 与角色不一致")
    if role == "case_worker":
        if not isinstance(task.get("owner_key"), str) or not task["owner_key"].strip():
            raise GuardedTaskError("Case Worker 缺少 owner_key")
    elif task.get("owner_key") is not None:
        raise GuardedTaskError("非 Case 任务不得声明 owner_key")
    if not isinstance(task.get("run_id"), str) or not task["run_id"].strip():
        raise GuardedTaskError("AgentTask run_id 无效")
    if not isinstance(task.get("batch_id"), str) or not task["batch_id"].strip():
        raise GuardedTaskError("AgentTask batch_id 无效")
    if not isinstance(task.get("attempt"), int) or isinstance(task.get("attempt"), bool) or task["attempt"] < 1:
        raise GuardedTaskError("AgentTask attempt 无效")
    if not isinstance(task.get("source_fingerprint"), str) or not _FINGERPRINT.fullmatch(task["source_fingerprint"]):
        raise GuardedTaskError("AgentTask source_fingerprint 无效")

    inputs = _string_array(task.get("input_files"), "input_files", allow_empty=False)
    files = _string_array(task.get("allowed_output_files"), "allowed_output_files", allow_empty=False)
    prefixes = _string_array(task.get("allowed_output_prefixes"), "allowed_output_prefixes")
    input_root = f"orchestration/inputs/{task_id}/"
    workspace = f"artifacts/agent-work/{role}/{task_id}/output/"
    for value in inputs:
        _safe_relative_path(value)
        if not value.startswith(input_root):
            raise GuardedTaskError("input_files 未指向当前任务冻结快照")
    for value in files:
        _safe_relative_path(value)
        if not value.startswith(workspace):
            raise GuardedTaskError("allowed_output_files 越出当前任务 output")
    for value in prefixes:
        _safe_relative_path(value, prefix=True)
        if not value.startswith(workspace):
            raise GuardedTaskError("allowed_output_prefixes 越出当前任务 output")


def _resolve_run_path(run_dir: Path, value: str, *, label: str) -> Path:
    pure = _safe_relative_path(value)
    candidate = run_dir.joinpath(*pure.parts).resolve(strict=False)
    if not _is_key_within(_path_key(candidate), _path_key(run_dir)):
        raise GuardedTaskError(f"{label} 越出 run-dir")
    return candidate


def _cached_file_sha256(
    path: Path,
    evidence: TranscriptEvidence,
    *,
    label: str,
) -> str:
    fingerprints = evidence.cache_state.get("fingerprints")
    if not isinstance(fingerprints, dict):
        raise GuardedTaskError("guard immutable fingerprint 缓存无效")
    resolved = path.resolve(strict=True)
    signature = _stat_signature(resolved)
    key = "sha256:" + label
    cached = fingerprints.get(key)
    if cached is not None:
        if (
            not isinstance(cached, dict)
            or cached.get("path") != _path_key(resolved)
            or cached.get("signature") != signature
            or not isinstance(cached.get("sha256"), str)
        ):
            raise GuardedTaskError(f"{label} 在 claim 期间发生变化")
        return str(cached["sha256"])
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise GuardedTaskError(f"无法读取 {label}") from exc
    value = digest.hexdigest()
    fingerprints[key] = {
        "path": _path_key(resolved),
        "signature": signature,
        "sha256": value,
    }
    return value


def _validate_page_probe_receipt(
    project_root: Path,
    run_dir: Path,
    task: dict[str, Any],
    entry: dict[str, Any],
    claim: dict[str, Any],
    evidence: TranscriptEvidence,
) -> tuple[tuple[str, ...], dict[str, Any] | None]:
    if task["agent_role"] != "discovery":
        if entry.get("page_probe_receipt") is not None:
            raise GuardedTaskError("非 Discovery manifest entry 不得携带 active page probe receipt")
        return (), None
    receipt_id = claim["page_probe_receipt_id"]
    receipt_fingerprint = claim["page_probe_receipt_fingerprint"]
    approved_tools = tuple(claim["approved_page_mcp_tools"])
    link = entry.get("page_probe_receipt")
    expected_relative = f"orchestration/page-probe-receipts/{receipt_id}.json"
    if (
        not isinstance(link, dict)
        or set(link) != _PAGE_PROBE_LINK_FIELDS
        or link.get("receipt_id") != receipt_id
        or link.get("receipt_path") != expected_relative
        or link.get("receipt_fingerprint") != receipt_fingerprint
        or link.get("execution_id") != claim["execution_id"]
        or link.get("coordinator_id") != claim["coordinator_id"]
        or link.get("source_fingerprint") != task["source_fingerprint"]
        or link.get("approved_page_mcp_tools") != list(approved_tools)
        or link.get("status") != "ACTIVE"
    ):
        raise GuardedTaskError("Discovery manifest page probe projection 与 claim 不一致")
    receipt_path = _resolve_run_path(run_dir, expected_relative, label="page probe receipt")
    receipt_path, receipt = _read_json_file(
        receipt_path, label="page probe receipt", max_bytes=8 * 1024 * 1024
    )
    if not isinstance(receipt, dict) or set(receipt) != _PAGE_PROBE_RECEIPT_FIELDS:
        raise GuardedTaskError("page probe receipt 字段集合无效")
    content = {
        key: value
        for key, value in receipt.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    actual_fingerprint = hashlib.sha256(
        _canonical_json(content).encode("utf-8")
    ).hexdigest()
    if (
        receipt.get("schema_version") != "1.0.0"
        or receipt.get("receipt_id") != receipt_id
        or receipt.get("receipt_fingerprint") != receipt_fingerprint
        or actual_fingerprint != receipt_fingerprint
        or receipt_id != f"PPR-{receipt_fingerprint[:24]}"
        or receipt.get("run_id") != task["run_id"]
        or receipt.get("batch_id") != task["batch_id"]
        or receipt.get("task_id") != task["task_id"]
        or receipt.get("execution_id") != claim["execution_id"]
        or receipt.get("coordinator_id") != claim["coordinator_id"]
        or receipt.get("source_fingerprint") != task["source_fingerprint"]
        or receipt.get("approved_mcp_tools") != list(approved_tools)
    ):
        raise GuardedTaskError("page probe receipt 身份/内容指纹与 claim 不一致")
    mcp_server = receipt.get("mcp_server")
    if not isinstance(mcp_server, str) or any(
        _MCP_TOOL_NAME.fullmatch(tool).group(1) != mcp_server
        for tool in approved_tools
    ):
        raise GuardedTaskError("page probe receipt MCP server/tool allowlist 无效")

    records = receipt.get("records")
    if not isinstance(records, list) or len(records) < 3:
        raise GuardedTaskError("page probe receipt 缺少三段有序记录")
    sequences: list[int] = []
    seen_records: set[str] = set()
    seen_content: set[str] = set()
    seen_tools: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != _PAGE_PROBE_RECORD_FIELDS:
            raise GuardedTaskError("page probe receipt record 字段集合无效")
        for name in (
            "record_id", "tool_input_sha256", "tool_response_sha256",
            "call_content_sha256",
        ):
            if not isinstance(record.get(name), str) or _FINGERPRINT.fullmatch(record[name]) is None:
                raise GuardedTaskError(f"page probe receipt record {name} 无效")
        if record["record_id"] in seen_records or record["call_content_sha256"] in seen_content:
            raise GuardedTaskError("page probe receipt 含 record/call replay")
        seen_records.add(record["record_id"])
        seen_content.add(record["call_content_sha256"])
        if not isinstance(record.get("sequence"), int):
            raise GuardedTaskError("page probe receipt record sequence 无效")
        sequences.append(record["sequence"])
        if not isinstance(record.get("recorded_at"), str) or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
            record["recorded_at"],
        ) is None:
            raise GuardedTaskError("page probe receipt record recorded_at is invalid")
        if record.get("tool_name") not in approved_tools:
            raise GuardedTaskError("page probe receipt record tool 越出 allowlist")
        seen_tools.add(record["tool_name"])
        if (
            record.get("operation_kind") not in {"read", "mutation"}
            or record.get("operation_name") not in {
                "read", "click", "select", "input", "toggle", "expand",
                "navigate", "other_mutation",
            }
            or record.get("response_nonempty") is not True
            or record.get("response_error") is not False
        ):
            raise GuardedTaskError("page probe receipt record 不是成功且可判定的页面操作")
        if (record.get("operation_kind") == "read") != (
            record.get("operation_name") == "read"
        ):
            raise GuardedTaskError(
                "page probe receipt operation kind/name are inconsistent"
            )
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        raise GuardedTaskError("page probe receipt record 顺序无效")
    if seen_tools != set(approved_tools):
        raise GuardedTaskError("page probe receipt 未逐工具提供成功记录")
    if not any(
        records[before]["operation_kind"] == "read"
        and records[mutation]["operation_kind"] == "mutation"
        and records[after]["operation_kind"] == "read"
        and records[before]["tool_response_sha256"]
        != records[after]["tool_response_sha256"]
        for before in range(len(records))
        for mutation in range(before + 1, len(records))
        for after in range(mutation + 1, len(records))
    ):
        raise GuardedTaskError("page probe receipt 缺少 read→mutation→changed read 闭环")

    raw_evidence = receipt.get("evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise GuardedTaskError("page probe receipt 缺少非空证据")
    expected_prefix = f"artifacts/page-probe-evidence/{claim['execution_id']}/"
    for index, item in enumerate(raw_evidence):
        if not isinstance(item, dict) or set(item) != _PAGE_PROBE_EVIDENCE_FIELDS:
            raise GuardedTaskError("page probe receipt evidence 字段集合无效")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative.startswith(expected_prefix):
            raise GuardedTaskError("page probe evidence 不属于专用 execution 目录")
        path = _resolve_run_path(run_dir, relative, label="page probe evidence")
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item.get("bytes"):
            raise GuardedTaskError("page probe evidence 缺失、为空或大小变化")
        if _cached_file_sha256(path, evidence, label=f"page_probe_evidence_{index}") != item.get("sha256"):
            raise GuardedTaskError("page probe evidence SHA256 变化")
        sidecar_relative = item.get("sidecar_path")
        sidecar_sha = item.get("sidecar_sha256")
        if (sidecar_relative is None) != (sidecar_sha is None):
            raise GuardedTaskError("page probe evidence sidecar path/hash 不完整")
        if sidecar_relative is not None:
            if sidecar_relative != relative + ".sensitive-audit.json":
                raise GuardedTaskError("page probe binary evidence sidecar 不是相邻同名文件")
            sidecar = _resolve_run_path(
                run_dir, sidecar_relative, label="page probe evidence sidecar"
            )
            if sidecar.is_symlink() or not sidecar.is_file():
                raise GuardedTaskError("page probe binary evidence sidecar 缺失")
            if _cached_file_sha256(
                sidecar, evidence, label=f"page_probe_evidence_sidecar_{index}"
            ) != sidecar_sha:
                raise GuardedTaskError("page probe binary evidence sidecar SHA256 变化")
    consumption_root = project_root / ".test-design-locks" / "page-probe-consumption"
    if consumption_root.is_symlink() or not consumption_root.is_dir():
        raise GuardedTaskError("page probe project consumption registry is missing or unsafe")
    run_dir_sha256 = hashlib.sha256(_path_key(run_dir).encode("utf-8")).hexdigest()
    for index, record in enumerate(records):
        record_id = record["record_id"]
        marker_path, marker = _read_json_file(
            consumption_root / f"{record_id}.json",
            label="page probe project consumption marker",
            max_bytes=64 * 1024,
        )
        content = {
            "schema_version": "1.0.0",
            "record_id": record_id,
            "receipt_id": receipt_id,
            "receipt_fingerprint": receipt_fingerprint,
            "run_dir_sha256": run_dir_sha256,
            "run_id": receipt["run_id"],
            "batch_id": receipt["batch_id"],
            "task_id": receipt["task_id"],
            "execution_id": receipt["execution_id"],
            "coordinator_id": receipt["coordinator_id"],
            "source_fingerprint": receipt["source_fingerprint"],
        }
        expected_marker = {
            **content,
            "binding_fingerprint": hashlib.sha256(
                _canonical_json(content).encode("utf-8")
            ).hexdigest(),
        }
        if set(marker) != _PAGE_PROBE_CONSUMPTION_FIELDS or marker != expected_marker:
            raise GuardedTaskError(
                "page probe project consumption marker does not match the receipt"
            )
        _cached_file_sha256(
            marker_path,
            evidence,
            label=f"page_probe_consumption_{index}_{record_id}",
        )
    _cached_fingerprint([receipt_path], evidence, label="page_probe_receipt")
    return approved_tools, receipt


def _validate_claim(
    claim: Any,
    *,
    task: dict[str, Any],
    entry: dict[str, Any],
    evidence: TranscriptEvidence,
) -> tuple[str, str, tuple[str, ...]]:
    if not isinstance(claim, dict) or set(claim) != _CLAIM_FIELDS:
        raise GuardedTaskError("manifest CLAIMED 任务缺少严格 AgentClaim")
    if claim.get("schema_version") != "1.0.0" or claim.get("task_id") != task["task_id"]:
        raise GuardedTaskError("AgentClaim schema/task_id 无效")
    for name in (
        "execution_id",
        "coordinator_id",
        "executor_id",
        "wave_id",
        "claimed_at",
    ):
        if not isinstance(claim.get(name), str) or not claim[name].strip():
            raise GuardedTaskError(f"AgentClaim {name} 无效")
    if claim.get("executor_kind") != "codebuddy-subagent":
        raise GuardedTaskError("项目 sub-agent 必须使用 codebuddy-subagent claim")
    if claim.get("source_fingerprint") != task["source_fingerprint"]:
        raise GuardedTaskError("AgentClaim source_fingerprint 与任务不一致")
    for name in (
        "source_fingerprint",
        "input_snapshot_fingerprint",
        "task_packet_fingerprint",
        "context_fingerprint",
    ):
        if not isinstance(claim.get(name), str) or not _FINGERPRINT.fullmatch(claim[name]):
            raise GuardedTaskError(f"AgentClaim {name} 无效")
        if name != "source_fingerprint" and claim[name] != entry.get(name):
            raise GuardedTaskError(f"AgentClaim {name} 与 manifest 不一致")
    receipt_id = claim.get("page_probe_receipt_id")
    receipt_fingerprint = claim.get("page_probe_receipt_fingerprint")
    raw_tools = claim.get("approved_page_mcp_tools")
    if not isinstance(raw_tools, list) or len(raw_tools) != len(set(raw_tools)):
        raise GuardedTaskError("AgentClaim approved_page_mcp_tools 无效")
    approved_tools = tuple(raw_tools)
    if task["agent_role"] == "discovery":
        if (
            not isinstance(receipt_id, str)
            or re.fullmatch(r"PPR-[0-9a-f]{24}", receipt_id) is None
            or not isinstance(receipt_fingerprint, str)
            or _FINGERPRINT.fullmatch(receipt_fingerprint) is None
            or not approved_tools
            or approved_tools != tuple(sorted(approved_tools))
            or any(
                not isinstance(tool, str) or _MCP_TOOL_NAME.fullmatch(tool) is None
                for tool in approved_tools
            )
        ):
            raise GuardedTaskError("Discovery AgentClaim 缺少严格 page probe receipt/tool allowlist")
        servers = {_MCP_TOOL_NAME.fullmatch(tool).group(1) for tool in approved_tools}
        if len(servers) != 1:
            raise GuardedTaskError("Discovery page MCP allowlist 必须属于同一 server namespace")
    elif receipt_id is not None or receipt_fingerprint is not None or approved_tools:
        raise GuardedTaskError("非 Discovery AgentClaim 不得携带 page probe 权限")
    claim_digest = hashlib.sha256(_canonical_json(claim).encode("utf-8")).hexdigest()
    cached_claim_digest = evidence.cache_state.get("claim_digest")
    if cached_claim_digest is not None and cached_claim_digest != claim_digest:
        raise GuardedTaskError("同一物理 sub-agent transcript 不得切换到另一 claim")
    if cached_claim_digest is None:
        if not _transcript_contains_claim_identity(
            evidence, claim["execution_id"], claim["executor_id"]
        ):
            raise GuardedTaskError("sub-agent transcript 未绑定本次 claim 执行身份")
        evidence.cache_state["claim_digest"] = claim_digest
    return claim["execution_id"], claim["executor_id"], approved_tools


def _load_guarded_task(
    task_path: Path,
    project_root: Path,
    evidence: TranscriptEvidence,
) -> GuardedTask:
    project = project_root.resolve(strict=True)
    resolved, task = _read_json_file(
        task_path, label="agent-task.json", max_bytes=_MAX_TASK_BYTES
    )
    if not _is_key_within(_path_key(resolved), _path_key(project)):
        raise GuardedTaskError("任务包不属于当前项目")
    try:
        relative_parts = resolved.relative_to(project).parts
    except ValueError as exc:
        raise GuardedTaskError("任务包不属于当前项目") from exc
    if len(relative_parts) < 7:
        raise GuardedTaskError("任务包目录结构无效")
    tail = relative_parts[-6:]
    if (
        tail[0].casefold() != "artifacts"
        or tail[1].casefold() != "agent-work"
        or tail[2].casefold() not in _ROLES
        or tail[4].casefold() != "meta"
        or tail[5].casefold() != "agent-task.json"
    ):
        raise GuardedTaskError("任务包目录结构无效")
    role_from_path, task_id_from_path = tail[2], tail[3]
    run_dir = project.joinpath(*relative_parts[:-6]).resolve(strict=True)
    allowed_batch_root = (project / "docs" / "test-assets" / "batch-runs").resolve(strict=True)
    if run_dir.parent != allowed_batch_root or run_dir.name.casefold() == "templates":
        raise GuardedTaskError("run-dir 不是 docs/test-assets/batch-runs 下的独立批次目录")
    _validate_task(task, role_from_path, task_id_from_path)

    context_path = resolved.with_name("task-context.json")
    context_path, context = _read_json_file(
        context_path, label="task-context.json", max_bytes=_MAX_CONTEXT_BYTES
    )
    manifest_path = run_dir / "orchestration" / "run-manifest.json"
    manifest_path, manifest = _read_json_file(
        manifest_path, label="run-manifest.json", max_bytes=_MAX_MANIFEST_BYTES
    )
    if (
        set(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema_version") != 1
        or manifest.get("architecture") != "multi-agent-final"
        or manifest.get("agent_mode") != "required"
        or manifest.get("run_id") != task["run_id"]
        or manifest.get("batch_id") != task["batch_id"]
        or not isinstance(manifest.get("tasks"), dict)
    ):
        raise GuardedTaskError("run-manifest 不是当前最终架构批次的权威 manifest")
    entry = manifest["tasks"].get(task["task_id"])
    if not isinstance(entry, dict) or entry.get("status") != "CLAIMED" or entry.get("task") != task:
        raise GuardedTaskError("任务不在 manifest 中或尚未 CLAIMED")
    if entry.get("task_packet_fingerprint") != _cached_fingerprint(
        [resolved], evidence, label="task_packet"
    ):
        raise GuardedTaskError("agent-task.json 与 manifest 指纹不一致")
    if entry.get("context_fingerprint") != _cached_fingerprint(
        [context_path], evidence, label="task_context"
    ):
        raise GuardedTaskError("task-context.json 与 manifest 指纹不一致")

    frozen = context.get("frozen_input_files")
    contracts = context.get("contract_input_files")
    if (
        context.get("architecture") != "multi-agent-final"
        or context.get("agent_role") != task["agent_role"]
        or context.get("task_id") != task["task_id"]
        or context.get("source_fingerprint") != task["source_fingerprint"]
        or not isinstance(frozen, dict)
        or not isinstance(contracts, dict)
        or set(frozen.values()) != set(task["input_files"])
        or not set(contracts.values()).issubset(set(task["input_files"]))
    ):
        raise GuardedTaskError("task-context 与 AgentTask 冻结输入不一致")
    output_contract = context.get("output_contract")
    result_rules = context.get("result_rules")
    if (
        not isinstance(output_contract, dict)
        or output_contract.get("allowed_output_files") != task["allowed_output_files"]
        or output_contract.get("allowed_output_prefixes") != task["allowed_output_prefixes"]
        or not isinstance(result_rules, dict)
        or result_rules.get("success_required_gate") != task["required_gate"]
    ):
        raise GuardedTaskError("task-context 输出/AgentResult 契约与任务不一致")

    readable: set[str] = {_path_key(resolved), _path_key(context_path)}
    input_paths: list[Path] = []
    for value in task["input_files"]:
        path = _resolve_run_path(run_dir, value, label="input_files")
        try:
            if not path.is_file() or path.is_symlink():
                raise GuardedTaskError("冻结输入不存在或不是普通文件")
        except OSError as exc:
            raise GuardedTaskError("无法验证冻结输入") from exc
        input_paths.append(path)
        readable.add(_path_key(path))
    actual_input_fingerprint = _cached_fingerprint(
        input_paths, evidence, label="frozen_inputs"
    )
    if entry.get("input_snapshot_fingerprint") != actual_input_fingerprint:
        raise GuardedTaskError("冻结输入内容与 manifest input_snapshot_fingerprint 不一致")

    raw_claim = entry.get("claim")
    execution_id, executor_id, claim_tools = _validate_claim(
        raw_claim, task=task, entry=entry, evidence=evidence
    )
    assert isinstance(raw_claim, dict)
    if raw_claim.get("input_snapshot_fingerprint") != actual_input_fingerprint:
        raise GuardedTaskError("AgentClaim input_snapshot_fingerprint 与冻结输入不一致")
    approved_page_mcp_tools, page_probe_receipt = _validate_page_probe_receipt(
        project, run_dir, task, entry, raw_claim, evidence
    )
    if approved_page_mcp_tools != claim_tools:
        raise GuardedTaskError("AgentClaim 与 page probe receipt tool allowlist 不一致")
    _verify_unique_claim_event(
        run_dir / "orchestration" / "events.jsonl",
        task["task_id"],
        raw_claim,
        evidence,
        receipt=page_probe_receipt,
    )
    _bind_physical_subagent_execution(
        project,
        run_dir,
        task,
        raw_claim,
        evidence,
    )

    exact_outputs: set[str] = set()
    prefixes: list[str] = []
    for value in task["allowed_output_files"]:
        exact_outputs.add(_path_key(_resolve_run_path(run_dir, value, label="output file")))
    for value in task["allowed_output_prefixes"]:
        pure = _safe_relative_path(value, prefix=True)
        path = run_dir.joinpath(*pure.parts).resolve(strict=False)
        if not _is_key_within(_path_key(path), _path_key(run_dir)):
            raise GuardedTaskError("output prefix 越出 run-dir")
        prefixes.append(_path_key(path))

    guarded = GuardedTask(
        task_path=resolved,
        context_path=context_path,
        run_dir=run_dir,
        execution_id=execution_id,
        executor_id=executor_id,
        agent_role=task["agent_role"],
        approved_page_mcp_tools=approved_page_mcp_tools,
        readable_files=frozenset(readable),
        exact_output_files=frozenset(exact_outputs),
        output_prefixes=tuple(sorted(prefixes)),
    )
    _write_guard_cache(evidence.cache_path, evidence.cache_state)
    return guarded


def _guard_for_payload(payload: dict[str, Any], project_root: Path) -> GuardedTask | None:
    transcript = _canonical_subagent_transcript(payload.get("transcript_path"))
    if transcript is None:
        return None
    cache_path = _guard_cache_path(project_root, transcript)
    with _exclusive_guard_cache(cache_path):
        evidence = _inspect_transcript(transcript, project_root)
        return _load_guarded_task(evidence.task_paths[0], project_root, evidence)


def _target_path(payload: dict[str, Any]) -> Path:
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise GuardedTaskError("文件工具缺少 tool_input")
    value = next(
        (
            tool_input.get(key)
            for key in ("file_path", "filePath", "path")
            if isinstance(tool_input.get(key), str) and tool_input.get(key).strip()
        ),
        None,
    )
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise GuardedTaskError(f"{tool_name} 缺少可信 file_path")
    raw_path = Path(value)
    if any(part == ".." or part.endswith((".", " ")) for part in raw_path.parts):
        raise GuardedTaskError("文件路径包含目录穿越或不安全别名")
    if not raw_path.is_absolute():
        cwd_value = payload.get("cwd")
        if not isinstance(cwd_value, str) or not Path(cwd_value).is_absolute():
            raise GuardedTaskError("相对文件路径缺少可信绝对 cwd")
        raw_path = Path(cwd_value) / raw_path
    # Reject NTFS alternate data streams while preserving the drive component.
    if any(":" in part for part in raw_path.parts[1:]):
        raise GuardedTaskError("文件路径包含 NTFS alternate data stream")
    return raw_path.resolve(strict=False)


def _under_output_prefix(target_key: str, prefixes: tuple[str, ...]) -> bool:
    return any(target_key.startswith(prefix + "/") for prefix in prefixes)


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _is_matched_tool(tool_name: Any) -> bool:
    return isinstance(tool_name, str) and (
        tool_name in _MATCHED_TOOLS or tool_name.startswith("mcp__")
    )


_PUBLICATION_PATHS = (
    "docs/test-design/current",
    "docs/test-design/deliverables",
    "docs/test-assets/modules",
    "docs/test-assets/imports",
    "docs/test-assets/catalog",
)
_RUN_LEDGER_NAMES = {
    "batch-scope.json", "batch-status.csv", "batch-plan.md", "batch-review.md",
    "page-element-inventory.csv", "page-discovery.csv",
    "selection-option-observations.csv", "interaction-branch-observations.csv",
    "element-case-plan.csv", "test-data-lifecycle.csv", "risk-confirmation.csv",
}
_SHELL_TOOL_NAMES = {"Bash", "PowerShell", "execute_command", "run_in_terminal"}
_DANGEROUS_SHELL_PATHS = (
    "artifacts/agent-work", "artifacts/data", "orchestration/",
    "docs/test-design/current", "docs/test-design/deliverables",
    "docs/test-assets/modules", "docs/test-assets/imports", "docs/test-assets/catalog",
    "docs/test-assets/product-map.xlsx",
)


def _relative_project_path(target: Path, project_root: Path) -> str | None:
    try:
        return target.resolve(strict=False).relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return None


def _run_dir_for_target(target: Path, project_root: Path) -> Path | None:
    root = project_root.resolve()
    current = target.resolve(strict=False)
    for candidate in (current, *current.parents):
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if (candidate / "orchestration" / "run-manifest.json").is_file():
            return candidate
        if candidate == root:
            break
    return None


def _safe_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _main_fallback_allows(target: Path, run_dir: Path) -> bool:
    manifest = _safe_json(run_dir / "orchestration" / "run-manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("tasks"), dict):
        return False
    target_key = _path_key(target)
    matching = 0
    for entry in manifest["tasks"].values():
        if not isinstance(entry, dict) or entry.get("status") != "CLAIMED":
            continue
        claim = entry.get("claim")
        task = entry.get("task")
        authorization = entry.get("fallback_authorization")
        if (
            not isinstance(claim, dict)
            or claim.get("executor_kind") != "codebuddy-isolated-fallback"
            or not isinstance(task, dict)
            or not isinstance(authorization, dict)
            or authorization.get("authorization_fingerprint")
            != hashlib.sha256(
                json.dumps(
                    {key: value for key, value in authorization.items() if key != "authorization_fingerprint"},
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            or authorization.get("task_id") != task.get("task_id")
            or authorization.get("execution_id") != claim.get("execution_id")
            or authorization.get("coordinator_id") != claim.get("coordinator_id")
            or authorization.get("executor_id") != claim.get("executor_id")
            or authorization.get("executor_kind") != "codebuddy-isolated-fallback"
            or authorization.get("source_fingerprint") != task.get("source_fingerprint")
            or authorization.get("input_snapshot_fingerprint") != claim.get("input_snapshot_fingerprint")
            or authorization.get("task_packet_fingerprint") != claim.get("task_packet_fingerprint")
            or authorization.get("context_fingerprint") != claim.get("context_fingerprint")
            or not isinstance(authorization.get("failure_count"), int)
            or isinstance(authorization.get("failure_count"), bool)
            or authorization.get("failure_count", 0) < 2
            or authorization.get("quality_gates_unchanged") is not True
            or authorization.get("workspace_isolation_required") is not True
            or authorization.get("review_required") is not True
            or authorization.get("delivery_single_writer") is not True
        ):
            continue
        allowed = {
            _path_key((run_dir / str(relative)).resolve(strict=False))
            for relative in task.get("allowed_output_files", [])
            if isinstance(relative, str)
        }
        prefixes = tuple(
            _path_key((run_dir / str(relative)).resolve(strict=False)).rstrip("/")
            for relative in task.get("allowed_output_prefixes", [])
            if isinstance(relative, str)
        )
        if target_key in allowed or any(target_key.startswith(prefix + "/") for prefix in prefixes):
            matching += 1
    return matching == 1


def _guard_main_write(payload: dict[str, Any], project_root: Path) -> dict[str, Any] | None:
    try:
        target = _target_path(payload)
    except GuardedTaskError as exc:
        return _deny(f"拒绝无法证明安全的主会话写入：{exc}")
    relative = _relative_project_path(target, project_root)
    if relative is None:
        return None
    lowered = relative.casefold()
    if any(lowered == prefix or lowered.startswith(prefix + "/") for prefix in _PUBLICATION_PATHS):
        return _deny("正式交付、归档、导入和产品事实只能由 complete-deliverables 单写者事务更新")
    if lowered == "docs/test-assets/product-map.xlsx":
        return _deny("product-map.xlsx 只能由标准事实投影/交付事务更新")
    run_dir = _run_dir_for_target(target, project_root)
    if run_dir is None:
        return None
    run_relative = target.resolve(strict=False).relative_to(run_dir.resolve()).as_posix()
    run_lower = run_relative.casefold()
    if run_relative in _RUN_LEDGER_NAMES or run_lower.startswith("artifacts/data/"):
        return _deny("主会话不得直接写正式批次账本或 artifacts/data；必须由 Agent 提升/确定性合并器写入")
    if run_lower.startswith("orchestration/submissions/") and target.suffix.casefold() == ".json":
        return None
    if run_lower.startswith("orchestration/"):
        return _deny("主会话不得直接修改编排状态、结果、accepted、事件或收据")
    if run_lower.startswith("artifacts/agent-work/"):
        if _main_fallback_allows(target, run_dir):
            return None
        return _deny("Agent workspace 只能由物理 sub-agent 或 supervisor 授权的单个 fallback claim 写入")
    if run_lower.startswith("artifacts/scripts/") and re.search(
        r"(?:gen|build|create|fix|patch).*(?:excel|deliver|workbook)|(?:excel|deliver|workbook).*(?:gen|build|create|fix|patch)",
        target.name,
        re.IGNORECASE,
    ):
        return _deny("禁止批次级自制 Excel/Delivery 脚本；必须使用标准组装器和 complete-deliverables")
    return None


def _contains_unquoted_shell_operator(command: str) -> bool:
    quote: str | None = None
    for char in command:
        if char == "`":
            return True
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in ";|&<>\r\n":
            return True
    return quote is not None


def _guard_main_shell(payload: dict[str, Any]) -> dict[str, Any] | None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return _deny("主会话命令工具缺少可信输入")
    command = next(
        (
            tool_input.get(key)
            for key in ("command", "cmd", "script")
            if isinstance(tool_input.get(key), str)
        ),
        "",
    )
    normalized = command.replace("\\", "/").casefold()
    if not normalized.strip():
        return _deny("主会话命令为空")
    if "scripts/run-test-design.ps1" in normalized or "scripts/test_design_excel_tools.py" in normalized:
        # A standard entry point is safe only as a single command.  Otherwise an
        # attacker could append an arbitrary writer after a trusted substring.
        if _contains_unquoted_shell_operator(command) or "$(" in command or re.search(
            r"\b(?:invoke-expression|iex|start-process|powershell|pwsh|cmd(?:\.exe)?)\b",
            normalized,
        ):
            return _deny("鏍囧噯鍏ュ彛鍛戒护涓嶅緱閾惧紡鎴栧啀娲惧彂鍏朵粬 shell 鍛戒护")
        return None
    if any(path in normalized for path in _DANGEROUS_SHELL_PATHS):
        return _deny(
            "禁止通过任意 shell 旁路写 Agent workspace、正式账本、编排状态或交付目录；使用 run-test-design.ps1"
        )
    if "artifacts/scripts" in normalized and re.search(r"gen[_-]?(?:excel|deliver)|openpyxl", normalized):
        return _deny("禁止执行批次级 Excel/Delivery 生成脚本")
    return None


def _deferred_input_selects(
    tool_input: Any, approved_tools: tuple[str, ...]
) -> bool:
    if not isinstance(tool_input, dict):
        return False
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
    if len(selectors) != 1 or selectors[0][0] != ("tool_name",):
        return False
    selected = selectors[0][1]
    return (
        isinstance(selected, str)
        and selected in approved_tools
        and canonical_values == [selected]
    )


def evaluate_event(payload: Any, project_root: Path) -> dict[str, Any] | None:
    """Return a deny decision, or None to continue normal CodeBuddy permissions."""

    if not isinstance(payload, dict):
        return _deny("CodeBuddy hook 输入不是 JSON object；按失败关闭处理")
    tool_name = payload.get("tool_name")
    if not _is_matched_tool(tool_name):
        return None
    try:
        guarded = _guard_for_payload(payload, project_root)
    except GuardedTaskError as exc:
        return _deny(f"测试设计 Agent 权限上下文无效：{exc}")
    except Exception:
        return _deny("测试设计 Agent 权限上下文发生未预期异常；按失败关闭处理")
    if guarded is None:
        if tool_name in _WRITE_TOOLS:
            return _guard_main_write(payload, project_root)
        if tool_name in _SHELL_TOOL_NAMES:
            return _guard_main_shell(payload)
        return None
    if tool_name in _DISCOVERY_META_TOOLS or tool_name.startswith("mcp__"):
        if guarded.agent_role != "discovery" or not guarded.approved_page_mcp_tools:
            return _deny(f"{tool_name} 只允许已绑定 page probe receipt 的 Discovery 使用")
        if tool_name == "DeferExecuteTool" and not _deferred_input_selects(
            payload.get("tool_input"), guarded.approved_page_mcp_tools
        ):
            return _deny("DeferExecuteTool 未精确选择 receipt allowlist 内唯一工具")
        if tool_name.startswith("mcp__") and tool_name not in guarded.approved_page_mcp_tools:
            return _deny("直接 MCP 调用不在 page probe receipt exact allowlist")
        return None
    if tool_name in _ALWAYS_DENIED_TOOLS:
        return _deny(f"测试设计认知 Agent 禁止使用 {tool_name}")
    try:
        target = _target_path(payload)
    except GuardedTaskError as exc:
        return _deny(f"拒绝无法证明属于任务授权范围的文件访问：{exc}")
    target_key = _path_key(target)
    if tool_name in _READ_TOOLS:
        if (
            target_key in guarded.readable_files
            or target_key in guarded.exact_output_files
            or _under_output_prefix(target_key, guarded.output_prefixes)
        ):
            return None
        return _deny("Read 路径不在冻结输入、任务 meta 或本任务 output 中")
    if target_key in guarded.exact_output_files or _under_output_prefix(
        target_key, guarded.output_prefixes
    ):
        return None
    return _deny("Write 路径不在 agent-task.json 的 output 白名单中")


def process_input(raw_input: str, project_root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw_input, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, ValueError):
        return _deny("CodeBuddy hook 输入无法可信解析；按失败关闭处理")
    return evaluate_event(payload, project_root)


def main() -> int:
    configured_root = os.environ.get("CODEBUDDY_PROJECT_DIR")
    project_root = (
        Path(configured_root) if configured_root else Path(__file__).resolve().parents[2]
    )
    try:
        decision = process_input(sys.stdin.read(), project_root)
    except Exception:
        decision = _deny("CodeBuddy guard 启动失败；按失败关闭处理")
    if decision is not None:
        sys.stdout.write(json.dumps(decision, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
