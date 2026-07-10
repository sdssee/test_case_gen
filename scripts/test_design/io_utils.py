from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
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
    import json

    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
