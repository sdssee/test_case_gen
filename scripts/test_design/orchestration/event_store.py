"""Append-only JSONL event ledger for orchestration runs.

Each record carries a monotonically increasing sequence and a SHA-256 hash link
to the preceding record.  Readers fail closed on truncated, reordered, edited,
or duplicated events.  Appends are serialized with an OS-level lock and flushed
to disk before returning.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping
from uuid import uuid4


class EventStoreError(ValueError):
    """Raised when an event cannot be appended or the ledger is corrupt."""


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
        raise EventStoreError(f"event is not JSON serializable: {exc}") from exc


def _record_hash(record_without_hash: Mapping[str, object]) -> str:
    return hashlib.sha256(
        _canonical_json(record_without_hash).encode("utf-8")
    ).hexdigest()


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Acquire a small cross-platform process lock for ledger appends."""

    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    acquired = False
    try:
        # Locking one byte from offset zero is valid even when the file is
        # empty.  Do not write a marker before acquiring the lock: concurrent
        # Windows writers can otherwise both observe an empty file and one of
        # their buffered marker flushes races a peer's byte-range lock.
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise EventStoreError(f"another orchestration writer holds lock {path}") from exc
        acquired = True
        yield
    finally:
        if acquired:
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


class EventStore:
    """Read and append a versioned orchestration event stream."""

    SCHEMA_VERSION = 1
    GENESIS_HASH = "0" * 64

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")

    def append(
        self,
        event_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        actor: str = "orchestrator",
        task_id: str | None = None,
        event_id: str | None = None,
        occurred_at: str | None = None,
    ) -> dict[str, object]:
        """Append one event and return its complete immutable envelope."""

        event_type = str(event_type).strip()
        actor = str(actor).strip()
        if not event_type:
            raise EventStoreError("event_type must be non-empty")
        if not actor:
            raise EventStoreError("actor must be non-empty")
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise EventStoreError("payload must be a JSON object")
        payload_value = dict(payload)
        # Validate before acquiring the process lock.
        _canonical_json(payload_value)

        event_id = str(event_id or uuid4()).strip()
        if not event_id:
            raise EventStoreError("event_id must be non-empty")
        occurred_at = str(
            occurred_at
            or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            )
        ).strip()
        if not occurred_at:
            raise EventStoreError("occurred_at must be non-empty")

        with _exclusive_lock(self.lock_path):
            events = self._read_unlocked(verify=True)
            if any(str(event.get("event_id")) == event_id for event in events):
                raise EventStoreError(f"duplicate event_id: {event_id}")
            previous_hash = (
                str(events[-1]["event_hash"]) if events else self.GENESIS_HASH
            )
            record: dict[str, object] = {
                "schema_version": self.SCHEMA_VERSION,
                "sequence": len(events) + 1,
                "event_id": event_id,
                "occurred_at": occurred_at,
                "event_type": event_type,
                "actor": actor,
                "task_id": str(task_id).strip() if task_id else None,
                "payload": payload_value,
                "previous_hash": previous_hash,
            }
            record["event_hash"] = _record_hash(record)
            line = _canonical_json(record) + "\n"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            return dict(record)

    append_event = append

    def _read_unlocked(self, *, verify: bool) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        if not self.path.is_file():
            raise EventStoreError(f"event path is not a file: {self.path}")
        events: list[dict[str, object]] = []
        previous_hash = self.GENESIS_HASH
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                if not raw_line.strip():
                    raise EventStoreError(
                        f"blank JSONL record at {self.path}:{line_number}"
                    )
                try:
                    value = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise EventStoreError(
                        f"invalid JSONL record at {self.path}:{line_number}: {exc}"
                    ) from exc
                if not isinstance(value, dict):
                    raise EventStoreError(
                        f"event at {self.path}:{line_number} must be an object"
                    )
                if verify:
                    self._verify_record(value, line_number, previous_hash)
                previous_hash = str(value.get("event_hash", ""))
                events.append(value)
        return events

    def _verify_record(
        self,
        record: Mapping[str, object],
        line_number: int,
        expected_previous_hash: str,
    ) -> None:
        required = {
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
        missing = required - set(record)
        if missing:
            raise EventStoreError(
                f"event at line {line_number} is missing fields: {sorted(missing)}"
            )
        if record.get("schema_version") != self.SCHEMA_VERSION:
            raise EventStoreError(
                f"event at line {line_number} uses unsupported schema_version"
            )
        sequence = record.get("sequence")
        if sequence != line_number or isinstance(sequence, bool):
            raise EventStoreError(
                f"event sequence mismatch at line {line_number}: {sequence!r}"
            )
        for field in ("event_id", "occurred_at", "event_type", "actor"):
            if not isinstance(record.get(field), str) or not str(record[field]).strip():
                raise EventStoreError(
                    f"event at line {line_number} has invalid {field}"
                )
        if not isinstance(record.get("payload"), dict):
            raise EventStoreError(f"event payload at line {line_number} must be an object")
        if record.get("previous_hash") != expected_previous_hash:
            raise EventStoreError(f"event hash chain is broken at line {line_number}")
        recorded_hash = record.get("event_hash")
        if not isinstance(recorded_hash, str) or len(recorded_hash) != 64:
            raise EventStoreError(f"event_hash at line {line_number} is invalid")
        hash_input = dict(record)
        hash_input.pop("event_hash", None)
        if _record_hash(hash_input) != recorded_hash:
            raise EventStoreError(f"event content hash mismatch at line {line_number}")

    def read_events(
        self,
        *,
        after_sequence: int = 0,
        event_type: str | None = None,
        task_id: str | None = None,
        verify: bool = True,
    ) -> list[dict[str, object]]:
        """Read events in ledger order with optional deterministic filters."""

        if not isinstance(after_sequence, int) or isinstance(after_sequence, bool):
            raise EventStoreError("after_sequence must be an integer")
        values = self._read_unlocked(verify=verify)
        return [
            value
            for value in values
            if int(value.get("sequence", 0)) > after_sequence
            and (event_type is None or value.get("event_type") == event_type)
            and (task_id is None or value.get("task_id") == task_id)
        ]

    read = read_events

    def iter_events(self, **filters: object) -> Iterator[dict[str, object]]:
        yield from self.read_events(**filters)

    def last_event(self) -> dict[str, object] | None:
        events = self._read_unlocked(verify=True)
        return dict(events[-1]) if events else None

    def verify(self) -> dict[str, object]:
        events = self._read_unlocked(verify=True)
        return {
            "valid": True,
            "event_count": len(events),
            "last_sequence": len(events),
            "last_hash": (
                events[-1]["event_hash"] if events else self.GENESIS_HASH
            ),
        }


__all__ = ["EventStore", "EventStoreError"]
