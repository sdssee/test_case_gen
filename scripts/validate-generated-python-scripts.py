# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


MAX_FILE_BYTES = 256 * 1024
MAX_PYTHON_BYTES = 200 * 1024
MAX_JSON_BYTES = 256 * 1024
SCAN_EXTS = {".py", ".json", ".csv", ".md", ".txt"}

GENERIC_FILLER_PHRASES = {
    "确认操作完成后页面功能正常可用",
    "页面正常响应",
    "系统处理正确",
    "结果符合预期",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def iter_generated_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in SCAN_EXTS else []
    if not root.exists():
        fail(f"Path not found: {root}")
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SCAN_EXTS)


def validate_file_size(path: Path) -> None:
    suffix = path.suffix.lower()
    max_bytes = MAX_FILE_BYTES
    if suffix == ".py":
        max_bytes = MAX_PYTHON_BYTES
    elif suffix == ".json":
        max_bytes = MAX_JSON_BYTES

    size = path.stat().st_size
    if size > max_bytes:
        fail(
            f"{path} is {size} bytes, exceeding the generated intermediate file limit of {max_bytes} bytes. "
            "Do not write all test cases into one Python/JSON/text file. "
            "Keep the existing multiple-JSON design and split case bodies by functional block."
        )


def validate_compile(path: Path) -> None:
    fail(
        f"Task-specific Python producers are not allowed in a run directory: {path}. "
        "Use scripts/test_design_excel_tools.py compile-deliverables with JSON shards."
    )


def numbered_lines(value: str, label: str) -> None:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        fail(f"{label} must not be empty")
    for expected_number, line in enumerate(lines, start=1):
        match = re.match(r"^(\d+)\.\s*\S+", line)
        if not match or int(match.group(1)) != expected_number:
            fail(f"{label} must use consecutive numbered lines; got: {line}")


def function_case_rows(data: object) -> list[dict[str, object]]:
    if not isinstance(data, dict):
        return []
    section = data.get("功能测试用例")
    if isinstance(section, dict) and isinstance(section.get("rows"), list):
        return [row for row in section["rows"] if isinstance(row, dict)]
    if isinstance(section, list):
        return [row for row in section if isinstance(row, dict)]
    return []


def validate_json(path: Path) -> list[dict[str, object]]:
    try:
        with path.open("r", encoding="utf-8-sig") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as exc:
        fail(f"{path} failed JSON syntax validation: line {exc.lineno}, column {exc.colno}: {exc.msg}")
    rows = function_case_rows(data)
    for index, row in enumerate(rows, start=1):
        label = f"{path} function case {index}"
        required = ["用例 ID", "功能点", "用例标题", "操作步骤", "预期结果"]
        missing = [field for field in required if not str(row.get(field, "")).strip()]
        if missing:
            fail(f"{label} is missing required fields: {missing}")
        function_point = str(row["功能点"]).strip()
        title = str(row["用例标题"]).strip()
        if not title.startswith(f"{function_point}-"):
            fail(f"{label} title must start with 功能点-: {title}")
        steps = str(row["操作步骤"])
        expected = str(row["预期结果"])
        numbered_lines(steps, f"{label} 操作步骤")
        numbered_lines(expected, f"{label} 预期结果")
        first_steps = "\n".join(steps.splitlines()[:3])
        if not any(marker in first_steps for marker in ["登录", "打开系统", "访问系统", "进入系统", "打开平台", "访问平台", "进入平台", "URL"]):
            fail(f"{label} must start from the system/project entry")
        has_business_path = bool(re.search(r"进入.{1,80}[-—－>].+", first_steps))
        if not has_business_path and not any(marker in first_steps for marker in ["菜单", "模块", "导航", "路径", ">", "页面"]):
            fail(f"{label} must include the business navigation path before control operations")
        for phrase in GENERIC_FILLER_PHRASES:
            if phrase in steps or expected.strip() == phrase:
                fail(f"{label} contains generic filler text: {phrase}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated helper scripts and small data shards before execution.")
    parser.add_argument("--path", required=True, type=Path, help="Generated file or directory to scan recursively.")
    args = parser.parse_args()

    files = iter_generated_files(args.path)
    if not files:
        print(f"OK: no generated Python/JSON/text intermediate files found under {args.path}")
        return 0

    seen_ids: dict[str, Path] = {}
    seen_bodies: dict[tuple[str, str], tuple[str, Path]] = {}
    for path in files:
        validate_file_size(path)
        if path.suffix.lower() == ".py":
            validate_compile(path)
        elif path.suffix.lower() == ".json":
            for row in validate_json(path):
                case_id = str(row.get("用例 ID", "")).strip()
                if case_id in seen_ids:
                    fail(f"Duplicate case ID across JSON shards: {case_id} in {seen_ids[case_id]} and {path}")
                seen_ids[case_id] = path
                body = (str(row.get("操作步骤", "")).strip(), str(row.get("预期结果", "")).strip())
                if body in seen_bodies:
                    previous_id, previous_path = seen_bodies[body]
                    fail(f"Cases {previous_id} and {case_id} have identical steps and expected results: {previous_path}, {path}")
                seen_bodies[body] = (case_id, path)
    print(f"OK: validated {len(files)} generated intermediate file(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
