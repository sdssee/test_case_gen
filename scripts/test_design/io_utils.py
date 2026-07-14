from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


def temporary_sibling(path: Path) -> Path:
    return path.with_name(f".{path.stem}.{uuid4().hex}.tmp{path.suffix}")


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        return
    temporary = temporary_sibling(target)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_sibling(path)
    try:
        temporary.write_text(text, encoding=encoding)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fsync_parent(path: Path) -> None:
    """Best-effort directory sync after an atomic replace.

    Windows does not allow opening directories through the regular Python file
    API.  File contents are still fsynced there; POSIX additionally persists
    the directory entry.
    """

    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_atomic_write_json(path: Path, value: object) -> None:
    """Atomically persist JSON, including the file data before replacement."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_sibling(path)
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_parent(path)
    finally:
        temporary.unlink(missing_ok=True)


def _durable_atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_sibling(target)
    try:
        shutil.copy2(source, temporary)
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        _fsync_parent(target)
    finally:
        temporary.unlink(missing_ok=True)


def _file_state(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise RuntimeError(f"Durable transaction refuses symbolic-link target: {path}")
    if not path.exists():
        return {"exists": False, "size": 0, "sha256": None}
    if not path.is_file():
        raise RuntimeError(f"Durable transaction target is not a regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return {"exists": True, "size": size, "sha256": digest.hexdigest()}


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class DurableFileTransaction:
    """Recoverable, hash-bound promotion of a finite set of project files.

    The journal records both the pre-transaction state and immutable desired
    payload.  Recovery only writes a target when its current bytes equal one of
    those two recorded states.  A third state is treated as an external edit
    and is never overwritten.  Each mutation rechecks the target immediately
    before replacement.  The project delivery/catalog locks exclude cooperating
    writers; an unrelated process that ignores those locks cannot be made part
    of one portable cross-platform filesystem transaction.
    """

    SCHEMA_VERSION = 1
    ACTIVE_STATUSES = frozenset(
        {"PREPARED", "APPLYING", "FILES_COMMITTED", "FINALIZING"}
    )
    TERMINAL_STATUSES = frozenset({"FINALIZED", "ROLLED_BACK"})
    STATUS_TRANSITIONS = {
        "PREPARED": frozenset({"APPLYING", "ROLLED_BACK"}),
        "APPLYING": frozenset({"APPLYING", "FILES_COMMITTED", "ROLLED_BACK"}),
        "FILES_COMMITTED": frozenset({"FINALIZING", "ROLLED_BACK"}),
        "FINALIZING": frozenset({"FINALIZED"}),
        "FINALIZED": frozenset({"FINALIZED"}),
        "ROLLED_BACK": frozenset({"ROLLED_BACK"}),
    }

    def __init__(
        self,
        project_root: Path,
        transaction_dir: Path,
        identity: Mapping[str, Any],
    ) -> None:
        self.project_root = project_root.resolve(strict=True)
        candidate = transaction_dir.resolve(strict=False)
        try:
            candidate.relative_to(self.project_root)
        except ValueError as exc:
            raise RuntimeError("Durable transaction directory must stay inside project root") from exc
        self.transaction_dir = candidate
        self.journal_path = candidate / "journal.json"
        self.identity = json.loads(
            json.dumps(identity, ensure_ascii=False, allow_nan=False, sort_keys=True)
        )
        self.transaction_id = _canonical_hash(self.identity)

    def _target(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise RuntimeError("Durable transaction journal contains an invalid target path")
        pure = Path(*relative.split("/"))
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise RuntimeError("Durable transaction target is not a canonical relative path")
        candidate = self.project_root.joinpath(*pure.parts)
        for component in (candidate, *candidate.parents):
            if component == self.project_root.parent:
                break
            if component.is_symlink():
                raise RuntimeError(
                    f"Durable transaction target contains a symbolic-link component: {relative}"
                )
            if component == self.project_root:
                break
        target = candidate.resolve(strict=False)
        try:
            target.relative_to(self.project_root)
        except ValueError as exc:
            raise RuntimeError(f"Durable transaction target escapes project root: {relative}") from exc
        return target

    def _relative(self, target: Path) -> str:
        absolute = target.absolute()
        for component in (absolute, *absolute.parents):
            if component.is_symlink():
                raise RuntimeError(
                    f"Durable transaction target contains a symbolic-link component: {target}"
                )
            if component == self.project_root:
                break
        resolved = absolute.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.project_root)
        except ValueError as exc:
            raise RuntimeError(
                f"Durable transaction only supports project-owned targets: {target}"
            ) from exc
        return relative.as_posix()

    @staticmethod
    def _with_hash(document: dict[str, Any]) -> dict[str, Any]:
        value = dict(document)
        value.pop("journal_hash", None)
        value["journal_hash"] = _canonical_hash(value)
        return value

    def _write(self, document: dict[str, Any]) -> dict[str, Any]:
        value = self._with_hash(document)
        durable_atomic_write_json(self.journal_path, value)
        return value

    def load(self) -> dict[str, Any] | None:
        if not self.journal_path.exists():
            return None
        if self.journal_path.is_symlink() or not self.journal_path.is_file():
            raise RuntimeError("Durable transaction journal is not a regular file")
        try:
            value = json.loads(
                self.journal_path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Durable transaction journal cannot be parsed") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Durable transaction journal must be a JSON object")
        recorded_hash = value.get("journal_hash")
        unsigned = dict(value)
        unsigned.pop("journal_hash", None)
        if recorded_hash != _canonical_hash(unsigned):
            raise RuntimeError("Durable transaction journal hash is invalid")
        required = {
            "schema_version",
            "transaction_id",
            "identity",
            "status",
            "created_at",
            "updated_at",
            "metadata",
            "files",
            "journal_hash",
        }
        if set(value) != required:
            raise RuntimeError("Durable transaction journal fields are invalid")
        if value.get("schema_version") != self.SCHEMA_VERSION:
            raise RuntimeError("Durable transaction journal schema is unsupported")
        if not isinstance(value.get("identity"), dict):
            raise RuntimeError("Durable transaction identity is invalid")
        if value.get("transaction_id") != _canonical_hash(value["identity"]):
            raise RuntimeError("Durable transaction identity hash is invalid")
        if value.get("transaction_id") != self.transaction_id or value.get("identity") != self.identity:
            raise RuntimeError("Durable transaction journal belongs to a different frozen invocation")
        if value.get("status") not in self.ACTIVE_STATUSES | self.TERMINAL_STATUSES:
            raise RuntimeError("Durable transaction status is invalid")
        if not isinstance(value.get("files"), list) or not isinstance(
            value.get("metadata"), dict
        ):
            raise RuntimeError("Durable transaction journal payload is invalid")
        seen_targets: set[str] = set()
        for index, record in enumerate(value["files"]):
            if not isinstance(record, dict) or set(record) != {
                "target",
                "before",
                "desired",
                "backup",
                "payload",
                "applied",
            }:
                raise RuntimeError("Durable transaction file record is invalid")
            target = self._target(record["target"])
            target_key = str(target).replace("\\", "/").casefold()
            if target_key in seen_targets:
                raise RuntimeError("Durable transaction journal contains duplicate target paths")
            seen_targets.add(target_key)
            for state_name in ("before", "desired"):
                state = record.get(state_name)
                if not isinstance(state, dict) or set(state) != {"exists", "size", "sha256"}:
                    raise RuntimeError("Durable transaction file state is invalid")
                if type(state.get("exists")) is not bool or type(state.get("size")) is not int:
                    raise RuntimeError("Durable transaction file state types are invalid")
                if state["exists"]:
                    if state["size"] < 0 or not isinstance(state.get("sha256"), str) or len(state["sha256"]) != 64:
                        raise RuntimeError("Durable transaction existing file state is invalid")
                elif state != {"exists": False, "size": 0, "sha256": None}:
                    raise RuntimeError("Durable transaction missing file state is invalid")
            if type(record.get("applied")) is not bool:
                raise RuntimeError("Durable transaction applied flag is invalid")
            expected_backup = f"backups/{index:04d}.bin" if record["before"].get("exists") else None
            expected_payload = f"payloads/{index:04d}.bin" if record["desired"].get("exists") else None
            if record.get("backup") != expected_backup or record.get("payload") != expected_payload:
                raise RuntimeError("Durable transaction payload path is invalid")
        return value

    def prepare(
        self,
        desired_files: Mapping[Path, Path | None],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.journal_path.exists():
            raise RuntimeError("Durable transaction is already prepared")
        self.transaction_dir.mkdir(parents=True, exist_ok=True)
        backups = self.transaction_dir / "backups"
        payloads = self.transaction_dir / "payloads"
        shutil.rmtree(backups, ignore_errors=True)
        shutil.rmtree(payloads, ignore_errors=True)
        backups.mkdir()
        payloads.mkdir()
        records: list[dict[str, Any]] = []
        ordered = sorted(desired_files.items(), key=lambda item: self._relative(item[0]))
        seen_targets: set[str] = set()
        for index, (target, source) in enumerate(ordered):
            relative = self._relative(target)
            resolved_target = self._target(relative)
            target_key = str(resolved_target).replace("\\", "/").casefold()
            if target_key in seen_targets:
                raise RuntimeError(f"Durable transaction contains duplicate target: {relative}")
            seen_targets.add(target_key)
            before = _file_state(resolved_target)
            backup_name: str | None = None
            if before["exists"]:
                backup_name = f"backups/{index:04d}.bin"
                _durable_atomic_copy(resolved_target, self.transaction_dir / backup_name)
                if _file_state(self.transaction_dir / backup_name) != before:
                    raise RuntimeError(f"Durable transaction backup verification failed: {relative}")
            payload_name: str | None = None
            if source is None:
                desired = {"exists": False, "size": 0, "sha256": None}
            else:
                if source.is_symlink():
                    raise RuntimeError(
                        f"Durable transaction source is a symbolic link: {source}"
                    )
                resolved_source = source.resolve(strict=True)
                if not resolved_source.is_file():
                    raise RuntimeError(f"Durable transaction source is not a regular file: {source}")
                payload_name = f"payloads/{index:04d}.bin"
                _durable_atomic_copy(resolved_source, self.transaction_dir / payload_name)
                desired = _file_state(self.transaction_dir / payload_name)
            records.append(
                {
                    "target": relative,
                    "before": before,
                    "desired": desired,
                    "backup": backup_name,
                    "payload": payload_name,
                    "applied": False,
                }
            )
        now = datetime.now().astimezone().isoformat(timespec="milliseconds")
        return self._write(
            {
                "schema_version": self.SCHEMA_VERSION,
                "transaction_id": self.transaction_id,
                "identity": self.identity,
                "status": "PREPARED",
                "created_at": now,
                "updated_at": now,
                "metadata": dict(metadata or {}),
                "files": records,
            }
        )

    def set_status(self, status: str) -> dict[str, Any]:
        if status not in self.ACTIVE_STATUSES | self.TERMINAL_STATUSES:
            raise RuntimeError(f"Unsupported durable transaction status: {status}")
        document = self.load()
        if document is None:
            raise RuntimeError("Durable transaction has no journal")
        if status not in self.STATUS_TRANSITIONS[document["status"]]:
            raise RuntimeError(
                f"Invalid durable transaction status transition: {document['status']} -> {status}"
            )
        document["status"] = status
        document["updated_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        return self._write(document)

    @staticmethod
    def _state_matches(actual: Mapping[str, object], expected: Mapping[str, object]) -> bool:
        return dict(actual) == dict(expected)

    def _verify_payloads(self, document: Mapping[str, Any]) -> None:
        for record in document["files"]:
            for field, state_name in (("backup", "before"), ("payload", "desired")):
                relative = record[field]
                if relative is None:
                    continue
                path = self.transaction_dir / relative
                if _file_state(path) != record[state_name]:
                    raise RuntimeError(
                        f"Durable transaction {field} hash is stale for {record['target']}"
                    )

    def apply_all(self) -> dict[str, Any]:
        document = self.load()
        if document is None:
            raise RuntimeError("Durable transaction has no journal")
        if document["transaction_id"] != self.transaction_id or document["identity"] != self.identity:
            raise RuntimeError("Durable transaction identity does not match this invocation")
        if document["status"] in {"FILES_COMMITTED", "FINALIZING", "FINALIZED"}:
            self.verify_committed(document)
            return document
        if document["status"] not in {"PREPARED", "APPLYING"}:
            raise RuntimeError(f"Cannot apply transaction in status {document['status']}")
        self._verify_payloads(document)
        if document["status"] != "APPLYING":
            document["status"] = "APPLYING"
            document["updated_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
            document = self._write(document)
        for record in document["files"]:
            target = self._target(record["target"])
            actual = _file_state(target)
            if self._state_matches(actual, record["desired"]):
                record["applied"] = True
            elif self._state_matches(actual, record["before"]):
                # Narrow the check-to-replace window after payload hashing and
                # before every mutation.  A cooperating writer is excluded by
                # the caller's delivery/catalog locks; a newly observed third
                # state always fails closed.
                latest = _file_state(target)
                if self._state_matches(latest, record["desired"]):
                    record["applied"] = True
                    document["updated_at"] = datetime.now().astimezone().isoformat(
                        timespec="milliseconds"
                    )
                    document = self._write(document)
                    continue
                if not self._state_matches(latest, record["before"]):
                    raise RuntimeError(
                        "Durable transaction detected external target drift immediately before overwrite: "
                        f"{record['target']}"
                    )
                if record["desired"]["exists"]:
                    assert record["payload"] is not None
                    _durable_atomic_copy(self.transaction_dir / record["payload"], target)
                else:
                    target.unlink(missing_ok=True)
                    _fsync_parent(target)
                if _file_state(target) != record["desired"]:
                    raise RuntimeError(
                        f"Durable transaction target verification failed: {record['target']}"
                    )
                record["applied"] = True
            else:
                raise RuntimeError(
                    "Durable transaction detected external target drift and will not overwrite it: "
                    f"{record['target']}"
                )
            document["updated_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
            document = self._write(document)
        document["status"] = "FILES_COMMITTED"
        document["updated_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        return self._write(document)

    def verify_committed(self, document: Mapping[str, Any] | None = None) -> None:
        value = document or self.load()
        if value is None:
            raise RuntimeError("Durable transaction has no journal")
        for record in value["files"]:
            if _file_state(self._target(record["target"])) != record["desired"]:
                raise RuntimeError(
                    "Durable transaction committed target drifted; recovery will not overwrite it: "
                    f"{record['target']}"
                )

    def rollback(self) -> dict[str, Any]:
        document = self.load()
        if document is None:
            raise RuntimeError("Durable transaction has no journal")
        if document["status"] == "FINALIZED":
            raise RuntimeError("Finalized durable transaction cannot be rolled back")
        if document["status"] == "ROLLED_BACK":
            self.cleanup_payloads()
            return document
        if document["status"] == "FINALIZING":
            raise RuntimeError("Finalizing durable transaction must recover forward")
        self._verify_payloads(document)
        states: list[tuple[dict[str, Any], dict[str, object]]] = []
        drifted: list[str] = []
        for record in document["files"]:
            actual = _file_state(self._target(record["target"]))
            if not (
                self._state_matches(actual, record["before"])
                or self._state_matches(actual, record["desired"])
            ):
                drifted.append(record["target"])
            states.append((record, actual))
        if drifted:
            raise RuntimeError(
                "Durable transaction rollback blocked by external target drift; no target was overwritten: "
                + ", ".join(drifted)
            )
        for record, actual in reversed(states):
            if self._state_matches(actual, record["before"]):
                continue
            target = self._target(record["target"])
            latest = _file_state(target)
            if self._state_matches(latest, record["before"]):
                continue
            if not self._state_matches(latest, record["desired"]):
                raise RuntimeError(
                    "Durable transaction rollback detected external target drift immediately before overwrite: "
                    f"{record['target']}"
                )
            if record["before"]["exists"]:
                assert record["backup"] is not None
                _durable_atomic_copy(self.transaction_dir / record["backup"], target)
            else:
                target.unlink(missing_ok=True)
                _fsync_parent(target)
            if _file_state(target) != record["before"]:
                raise RuntimeError(
                    f"Durable transaction rollback verification failed: {record['target']}"
                )
        document["status"] = "ROLLED_BACK"
        document["updated_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        document = self._write(document)
        self.cleanup_payloads()
        return document

    def cleanup_payloads(self) -> None:
        for name in ("backups", "payloads", "build"):
            shutil.rmtree(self.transaction_dir / name, ignore_errors=True)

    def reset_terminal(self) -> None:
        document = self.load()
        if document is not None and document["status"] not in self.TERMINAL_STATUSES:
            raise RuntimeError("Cannot reset an active durable transaction")
        shutil.rmtree(self.transaction_dir, ignore_errors=True)


def atomic_save_workbook(workbook, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_sibling(path)
    try:
        workbook.save(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def rollback_files_on_error(paths: list[Path]):
    unique_paths = list(dict.fromkeys(path.resolve() for path in paths if path))
    with tempfile.TemporaryDirectory(prefix="test-design-delivery-") as backup_dir_value:
        backup_dir = Path(backup_dir_value)
        snapshots: dict[Path, Path | None] = {}
        for index, path in enumerate(unique_paths):
            if path.exists():
                backup = backup_dir / f"{index:03d}{path.suffix}"
                shutil.copy2(path, backup)
                snapshots[path] = backup
            else:
                snapshots[path] = None
        try:
            yield
        except BaseException:
            for path, backup in snapshots.items():
                if backup is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_copy(backup, path)
            raise


@contextmanager
def exclusive_process_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    acquired = False
    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise RuntimeError(f"Another delivery process holds lock {lock_path}") from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()} created={datetime.now().isoformat(timespec='seconds')}\n".encode("utf-8"))
        lock_file.flush()
        yield
    finally:
        if acquired:
            try:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        lock_file.close()
