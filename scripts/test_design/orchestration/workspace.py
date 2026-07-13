"""Isolated agent workspaces and transactional artifact promotion.

Agents write only below ``artifacts/agent-work/<role>/<task-id>/output``.  The
orchestrator validates those files and promotes an explicit mapping into a
formal run directory.  Every path is resolved against an allowed root, every
file is hashed, and a retained transaction receipt supports safe rollback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from uuid import uuid4


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorkspaceError(ValueError):
    """Raised for unsafe paths, invalid task IDs, or failed transactions."""


@dataclass(frozen=True)
class PromotionItem:
    """One output path relative to the task output and its formal destination."""

    source: str
    destination: str


@dataclass(frozen=True)
class PromotionReceipt:
    transaction_id: str
    agent_role: str
    task_id: str
    status: str
    target_root: str
    files: tuple[dict[str, object], ...]
    receipt_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "transaction_id": self.transaction_id,
            "agent_role": self.agent_role,
            "task_id": self.task_id,
            "status": self.status,
            "target_root": self.target_root,
            "files": [dict(item) for item in self.files],
            "receipt_path": self.receipt_path,
        }


def sha256_file(path: Path | str, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a regular file without loading it into memory."""

    path = Path(path)
    if not path.is_file():
        raise WorkspaceError(f"cannot hash missing or non-file path: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        if sha256_file(source) != sha256_file(temporary):
            raise WorkspaceError(f"copy hash mismatch for {source}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_component(value: str, label: str) -> str:
    value = str(value).strip()
    if not _SAFE_COMPONENT.fullmatch(value):
        raise WorkspaceError(
            f"{label} must match {_SAFE_COMPONENT.pattern} and cannot contain a path"
        )
    return value


def _relative_text(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


class WorkspaceManager:
    """Own agent work directories and all formal-file promotion operations."""

    def __init__(self, run_dir: Path | str) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.orchestration_dir = self.run_dir / "orchestration"
        self.agent_work_root = self.run_dir / "artifacts" / "agent-work"
        self.formal_data_root = self.run_dir / "artifacts" / "data"
        self.promotion_root = self.orchestration_dir / "promotions"

    def initialize(self) -> None:
        self.orchestration_dir.mkdir(parents=True, exist_ok=True)
        self.agent_work_root.mkdir(parents=True, exist_ok=True)
        self.formal_data_root.mkdir(parents=True, exist_ok=True)
        self.promotion_root.mkdir(parents=True, exist_ok=True)

    def _inside(self, root: Path, candidate: Path | str, label: str) -> Path:
        root = root.resolve()
        raw = Path(candidate)
        path = raw if raw.is_absolute() else root / raw
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError(f"{label} escapes allowed root {root}: {candidate}") from exc
        return resolved

    def task_workspace(self, agent_role: str, task_id: str) -> Path:
        role = _safe_component(agent_role, "agent_role")
        task = _safe_component(task_id, "task_id")
        return self._inside(self.agent_work_root, Path(role) / task, "task workspace")

    def create_task_workspace(
        self,
        agent_role: str,
        task_id: str,
        *,
        clean: bool = False,
    ) -> Path:
        """Create an isolated task root with input/output/meta subdirectories."""

        self.initialize()
        root = self.task_workspace(agent_role, task_id)
        if clean and root.exists():
            if root == self.agent_work_root or self.agent_work_root not in root.parents:
                raise WorkspaceError(f"refusing to clear unsafe task path: {root}")
            shutil.rmtree(root)
        for name in ("input", "output", "meta"):
            (root / name).mkdir(parents=True, exist_ok=True)
        return root

    def task_output_root(self, agent_role: str, task_id: str) -> Path:
        return self.task_workspace(agent_role, task_id) / "output"

    def resolve_task_output(
        self,
        agent_role: str,
        task_id: str,
        relative_path: Path | str,
    ) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute():
            raise WorkspaceError("task output path must be relative")
        return self._inside(
            self.task_output_root(agent_role, task_id), raw, "task output path"
        )

    def file_record(self, path: Path | str, *, relative_to: Path | None = None) -> dict[str, object]:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise WorkspaceError(f"expected a regular non-symlink file: {path}")
        resolved = path.resolve()
        label = (
            _relative_text(resolved, relative_to.resolve())
            if relative_to is not None
            else str(resolved)
        )
        return {
            "path": label,
            "size": resolved.stat().st_size,
            "sha256": sha256_file(resolved),
        }

    def output_manifest(self, agent_role: str, task_id: str) -> list[dict[str, object]]:
        output_root = self.task_output_root(agent_role, task_id)
        if not output_root.is_dir():
            raise WorkspaceError(f"task output directory does not exist: {output_root}")
        records = [
            self.file_record(path, relative_to=output_root)
            for path in output_root.rglob("*")
            if path.is_file()
        ]
        return sorted(records, key=lambda item: str(item["path"]))

    def fingerprint_outputs(self, agent_role: str, task_id: str) -> str:
        manifest = self.output_manifest(agent_role, task_id)
        canonical = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _normalize_items(
        self,
        items: Mapping[str, str] | Iterable[PromotionItem | tuple[str, str]],
    ) -> list[PromotionItem]:
        raw_items: Iterable[PromotionItem | tuple[str, str]]
        raw_items = items.items() if isinstance(items, Mapping) else items
        normalized: list[PromotionItem] = []
        for item in raw_items:
            if isinstance(item, PromotionItem):
                normalized.append(item)
            else:
                try:
                    source, destination = item
                except (TypeError, ValueError) as exc:
                    raise WorkspaceError(
                        "promotion items must contain source/destination pairs"
                    ) from exc
                normalized.append(PromotionItem(str(source), str(destination)))
        if not normalized:
            raise WorkspaceError("promotion requires at least one file")
        return normalized

    def _target_root(self, target_root: Path | str | None) -> Path:
        if target_root is None:
            resolved = self.formal_data_root.resolve()
        else:
            raw = Path(target_root)
            resolved = self._inside(self.run_dir, raw, "promotion target root")
        # Formal files must never be promoted back into a task workspace or into
        # orchestration metadata.
        for forbidden in (self.agent_work_root.resolve(), self.orchestration_dir.resolve()):
            if resolved == forbidden or forbidden in resolved.parents:
                raise WorkspaceError(f"promotion target root is reserved: {resolved}")
        return resolved

    def _assert_formal_target(self, target: Path) -> None:
        for forbidden in (
            self.agent_work_root.resolve(),
            self.orchestration_dir.resolve(),
        ):
            if target == forbidden or forbidden in target.parents:
                raise WorkspaceError(f"promotion destination is reserved: {target}")

    def atomic_promote(
        self,
        agent_role: str,
        task_id: str,
        items: Mapping[str, str] | Iterable[PromotionItem | tuple[str, str]],
        *,
        target_root: Path | str | None = None,
    ) -> PromotionReceipt:
        """Promote an explicit file set and retain enough state for rollback.

        All sources and destinations are validated and staged before the first
        formal file is replaced.  If any replacement or receipt write fails,
        every changed target is restored to its pre-transaction state.
        """

        self.initialize()
        role = _safe_component(agent_role, "agent_role")
        task = _safe_component(task_id, "task_id")
        output_root = self.task_output_root(role, task).resolve()
        if not output_root.is_dir():
            raise WorkspaceError(f"task output directory does not exist: {output_root}")
        destination_root = self._target_root(target_root)
        destination_root.mkdir(parents=True, exist_ok=True)
        normalized = self._normalize_items(items)

        transaction_id = uuid4().hex
        transaction_dir = self.promotion_root / transaction_id
        backup_dir = transaction_dir / "backups"
        staged_dir = transaction_dir / "staged"
        backup_dir.mkdir(parents=True, exist_ok=False)
        staged_dir.mkdir(parents=True, exist_ok=False)

        prepared: list[dict[str, object]] = []
        seen_targets: set[Path] = set()
        try:
            for index, item in enumerate(normalized):
                source_raw = Path(item.source)
                destination_raw = Path(item.destination)
                if source_raw.is_absolute() or destination_raw.is_absolute():
                    raise WorkspaceError("promotion source and destination must be relative")
                source = self._inside(output_root, source_raw, "promotion source")
                target = self._inside(destination_root, destination_raw, "promotion destination")
                # The second check protects custom target roots and symlinked parents.
                self._inside(self.run_dir, target, "formal promotion destination")
                self._assert_formal_target(target)
                if source.is_symlink() or not source.is_file():
                    raise WorkspaceError(f"promotion source is not a regular file: {source}")
                if source.stat().st_size == 0:
                    raise WorkspaceError(f"promotion source is empty: {source}")
                if target.is_symlink():
                    raise WorkspaceError(f"promotion destination cannot be a symlink: {target}")
                if target.exists() and not target.is_file():
                    raise WorkspaceError(f"promotion destination is not a file: {target}")
                if target in seen_targets:
                    raise WorkspaceError(f"duplicate promotion destination: {target}")
                seen_targets.add(target)

                source_hash = sha256_file(source)
                staged = staged_dir / f"{index:06d}.staged"
                shutil.copy2(source, staged)
                if sha256_file(staged) != source_hash:
                    raise WorkspaceError(f"staged hash mismatch for {source}")

                existed = target.is_file()
                backup = backup_dir / f"{index:06d}.backup"
                previous_hash: str | None = None
                if existed:
                    previous_hash = sha256_file(target)
                    shutil.copy2(target, backup)
                    if sha256_file(backup) != previous_hash:
                        raise WorkspaceError(f"backup hash mismatch for {target}")
                prepared.append(
                    {
                        "source": _relative_text(source, output_root),
                        "target": _relative_text(target, self.run_dir),
                        "source_sha256": source_hash,
                        "promoted_sha256": source_hash,
                        "size": source.stat().st_size,
                        "previous_existed": existed,
                        "previous_sha256": previous_hash,
                        "backup": (
                            _relative_text(backup, self.run_dir) if existed else None
                        ),
                        "_target": target,
                        "_staged": staged,
                    }
                )

            changed: list[dict[str, object]] = []
            try:
                for record in prepared:
                    target = record["_target"]
                    staged = record["_staged"]
                    assert isinstance(target, Path) and isinstance(staged, Path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged, target)
                    if sha256_file(target) != record["promoted_sha256"]:
                        raise WorkspaceError(f"promoted hash mismatch for {target}")
                    changed.append(record)

                public_records = tuple(
                    {
                        key: value
                        for key, value in record.items()
                        if not key.startswith("_")
                    }
                    for record in prepared
                )
                receipt_path = transaction_dir / "receipt.json"
                receipt = PromotionReceipt(
                    transaction_id=transaction_id,
                    agent_role=role,
                    task_id=task,
                    status="PROMOTED",
                    target_root=_relative_text(destination_root, self.run_dir),
                    files=public_records,
                    receipt_path=_relative_text(receipt_path, self.run_dir),
                )
                receipt_value = receipt.to_dict()
                receipt_value["created_at"] = datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ).replace("+00:00", "Z")
                _atomic_write_json(receipt_path, receipt_value)
                return receipt
            except BaseException:
                self._restore_pre_promotion(changed)
                raise
        except BaseException:
            # Retain backups for diagnosis/manual recovery if an exceptional
            # filesystem failure also prevents automatic restoration.  The
            # absent receipt ensures the incomplete transaction is never read
            # as successfully promoted.
            raise

    promote_files = atomic_promote

    def _restore_pre_promotion(self, records: Sequence[dict[str, object]]) -> None:
        errors: list[str] = []
        for record in reversed(records):
            target = record.get("_target")
            if not isinstance(target, Path):
                target = self._inside(
                    self.run_dir, str(record["target"]), "rollback target"
                )
            try:
                if record.get("previous_existed"):
                    raw_backup = record.get("backup")
                    if not raw_backup:
                        raise WorkspaceError(f"missing rollback backup for {target}")
                    backup = self._inside(
                        self.run_dir, str(raw_backup), "rollback backup"
                    )
                    if not backup.is_file() or sha256_file(backup) != record.get(
                        "previous_sha256"
                    ):
                        raise WorkspaceError(f"invalid rollback backup for {target}")
                    _atomic_copy(backup, target)
                else:
                    target.unlink(missing_ok=True)
            except Exception as exc:  # best effort all-file recovery
                errors.append(f"{target}: {exc}")
        if errors:
            raise WorkspaceError("promotion rollback failed: " + "; ".join(errors))

    def _load_receipt(
        self, receipt_or_transaction: PromotionReceipt | str
    ) -> tuple[Path, dict[str, object]]:
        transaction_id = (
            receipt_or_transaction.transaction_id
            if isinstance(receipt_or_transaction, PromotionReceipt)
            else _safe_component(str(receipt_or_transaction), "transaction_id")
        )
        transaction_dir = self._inside(
            self.promotion_root, transaction_id, "promotion transaction"
        )
        receipt_path = transaction_dir / "receipt.json"
        try:
            value = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(f"cannot read promotion receipt {receipt_path}: {exc}") from exc
        if not isinstance(value, dict) or value.get("transaction_id") != transaction_id:
            raise WorkspaceError(f"invalid promotion receipt: {receipt_path}")
        if not isinstance(value.get("files"), list) or not value["files"]:
            raise WorkspaceError(f"promotion receipt has no files: {receipt_path}")
        return receipt_path, value

    def rollback_promotion(
        self,
        receipt_or_transaction: PromotionReceipt | str,
        *,
        force: bool = False,
    ) -> dict[str, object]:
        """Restore the pre-promotion snapshot without clobbering newer changes."""

        receipt_path, receipt = self._load_receipt(receipt_or_transaction)
        if receipt.get("status") == "ROLLED_BACK":
            return receipt
        if receipt.get("status") != "PROMOTED":
            raise WorkspaceError(
                f"promotion cannot be rolled back from status {receipt.get('status')!r}"
            )
        records = receipt["files"]
        assert isinstance(records, list)

        # Refuse to overwrite a file changed by a newer transaction.
        for record in records:
            if not isinstance(record, dict):
                raise WorkspaceError("promotion receipt contains an invalid file record")
            target = self._inside(
                self.run_dir, str(record.get("target", "")), "rollback target"
            )
            if not force and (
                not target.is_file()
                or sha256_file(target) != record.get("promoted_sha256")
            ):
                raise WorkspaceError(
                    f"refusing rollback because promoted target has drifted: {target}"
                )

        rollback_snapshot = receipt_path.parent / "rollback-current"
        if rollback_snapshot.exists():
            shutil.rmtree(rollback_snapshot)
        rollback_snapshot.mkdir(parents=True)
        current_records: list[dict[str, object]] = []
        try:
            for index, record in enumerate(records):
                target = self._inside(
                    self.run_dir, str(record["target"]), "rollback target"
                )
                current = rollback_snapshot / f"{index:06d}.current"
                if target.is_file():
                    shutil.copy2(target, current)
                current_records.append(
                    {**record, "_target": target, "_current": current}
                )

            restored: list[dict[str, object]] = []
            try:
                for record in reversed(current_records):
                    target = record["_target"]
                    assert isinstance(target, Path)
                    if record.get("previous_existed"):
                        backup = self._inside(
                            self.run_dir,
                            str(record.get("backup", "")),
                            "rollback backup",
                        )
                        if not backup.is_file() or sha256_file(backup) != record.get(
                            "previous_sha256"
                        ):
                            raise WorkspaceError(f"rollback backup is invalid: {backup}")
                        _atomic_copy(backup, target)
                    else:
                        target.unlink(missing_ok=True)
                    restored.append(record)
            except BaseException:
                # Reapply the exact state observed before rollback began.
                for record in reversed(restored):
                    target = record["_target"]
                    current = record["_current"]
                    assert isinstance(target, Path) and isinstance(current, Path)
                    if current.is_file():
                        _atomic_copy(current, target)
                    else:
                        target.unlink(missing_ok=True)
                raise

            receipt["status"] = "ROLLED_BACK"
            receipt["rolled_back_at"] = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            _atomic_write_json(receipt_path, receipt)
            return receipt
        finally:
            shutil.rmtree(rollback_snapshot, ignore_errors=True)

    def finalize_promotion(
        self, receipt_or_transaction: PromotionReceipt | str
    ) -> dict[str, object]:
        """Discard rollback copies after a higher-level checkpoint is durable."""

        receipt_path, receipt = self._load_receipt(receipt_or_transaction)
        if receipt.get("status") != "PROMOTED":
            raise WorkspaceError(
                f"only a promoted transaction can be finalized: {receipt.get('status')!r}"
            )
        backup_dir = receipt_path.parent / "backups"
        shutil.rmtree(backup_dir, ignore_errors=True)
        receipt["status"] = "FINALIZED"
        receipt["finalized_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        _atomic_write_json(receipt_path, receipt)
        return receipt


AgentWorkspaceManager = WorkspaceManager


__all__ = [
    "AgentWorkspaceManager",
    "PromotionItem",
    "PromotionReceipt",
    "WorkspaceError",
    "WorkspaceManager",
    "sha256_file",
]
