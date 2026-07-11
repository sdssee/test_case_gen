# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .io_utils import atomic_write_text


CACHE_VERSION = 1
CACHE_NAME = ".validation-cache.json"


def fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted({item.resolve() for item in paths}, key=str):
        digest.update(str(path).encode("utf-8"))
        if not path.exists():
            digest.update(b"<missing>")
            continue
        if path.is_dir():
            digest.update(b"<directory>")
            for child in sorted(item.name for item in path.iterdir() if item.name != CACHE_NAME):
                digest.update(child.encode("utf-8"))
            continue
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def cache_path(run_dir: Path) -> Path:
    return run_dir.resolve() / "artifacts" / CACHE_NAME


def cache_hit(run_dir: Path, phase: str, current_fingerprint: str) -> bool:
    path = cache_path(run_dir)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        data.get("version") == CACHE_VERSION
        and data.get("phases", {}).get(phase, {}).get("fingerprint") == current_fingerprint
        and data.get("phases", {}).get(phase, {}).get("status") == "passed"
    )


def record_success(run_dir: Path, phase: str, current_fingerprint: str, inputs: list[Path] | None = None) -> None:
    path = cache_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError:
        data = {}
    if data.get("version") != CACHE_VERSION:
        data = {"version": CACHE_VERSION, "phases": {}}
    input_report = []
    project_root = run_dir.resolve().parents[3] if len(run_dir.resolve().parents) > 3 else run_dir.resolve()
    for item in inputs or []:
        resolved = item.resolve()
        try:
            display_path = str(resolved.relative_to(project_root)).replace("\\", "/")
        except ValueError:
            display_path = f"<external>/{resolved.name}"
        input_report.append(
            {
                "path": display_path,
                "exists": resolved.exists(),
                "kind": "directory" if resolved.is_dir() else "file" if resolved.is_file() else "missing",
                "size": resolved.stat().st_size if resolved.is_file() else None,
            }
        )
    data.setdefault("phases", {})[phase] = {
        "fingerprint": current_fingerprint,
        "status": "passed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "input_count": len(input_report),
        "inputs": input_report,
    }
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
