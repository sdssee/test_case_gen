from __future__ import annotations

from pathlib import Path


def module_names(module_path: str) -> list[str]:
    parts = [part.strip() for part in module_path.replace("/", ">").split(">") if part.strip()]
    return (parts + [""] * 5)[:5]


def canonical_module_parts(module_path: str, product_name: str | None = None) -> list[str]:
    parts = [part.strip() for part in module_path.replace("\\", ">").replace("/", ">").split(">") if part.strip()]
    if product_name and parts and parts[0] == product_name.strip():
        parts = parts[1:]
    return parts


def safe_filename(value: str) -> str:
    cleaned = value.replace("\\", ">").replace("/", ">")
    for char in '<>:"|?*':
        cleaned = cleaned.replace(char, "_")
    cleaned = "_".join(part.strip() for part in cleaned.split("_") if part.strip())
    cleaned = cleaned.replace(" ", "")
    return cleaned or "测试设计"


def deliverable_names(module_path: str, product_name: str | None = None) -> tuple[str, str, str]:
    parts = canonical_module_parts(module_path, product_name)
    stem = safe_filename(">".join(parts) if parts else module_path)
    return stem, f"{stem}_测试设计.xlsx", f"{stem}_导入用例.xlsx"


def module_leaf_name(module_path: str) -> str:
    parts = [part.strip() for part in module_path.replace("/", ">").split(">") if part.strip()]
    return parts[-1] if parts else module_path


def relative_project_path(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()
