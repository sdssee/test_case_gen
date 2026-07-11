# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import py_compile
import re
import sys
from pathlib import Path

from test_design.contracts.function_cases import (
    ENGLISH_TEMPLATE_MARKERS,
    FUNCTION_CASE_FORBIDDEN_FIELDS,
    FUNCTION_CASE_PART_RE,
    FUNCTION_CASE_REQUIRED_FIELDS,
    MAX_FUNCTION_CASES_PER_PART,
)


MAX_FILE_BYTES = 256 * 1024
MAX_PYTHON_BYTES = 200 * 1024
MAX_JSON_BYTES = 256 * 1024
SCAN_EXTS = {".py", ".json", ".csv", ".md", ".txt"}

FORBIDDEN_QUOTE_CHARS = {
    "\u201c": "left double smart quote",
    "\u201d": "right double smart quote",
    "\u2018": "left single smart quote",
    "\u2019": "right single smart quote",
    "\u300c": "corner quote",
    "\u300d": "corner quote",
    "\u300e": "white corner quote",
    "\u300f": "white corner quote",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def iter_generated_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in SCAN_EXTS else []
    if not root.exists():
        fail(f"Path not found: {root}")
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SCAN_EXTS)


def validate_forbidden_quotes(path: Path) -> None:
    text = path.read_text(encoding="utf-8-sig")
    for line_number, line in enumerate(text.splitlines(), start=1):
        for char, label in FORBIDDEN_QUOTE_CHARS.items():
            column = line.find(char)
            if column >= 0:
                fail(
                    f"{path}:{line_number}:{column + 1} contains {label} U+{ord(char):04X}. "
                    "Generated Python scripts must serialize Chinese text with repr/json.dumps "
                    "or use plain ASCII quote delimiters with escaped content."
                )


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
            "Do not write a whole module, multiple leaf titles, or all test cases into one Python/JSON/text file. "
            "Split by the current leaf-title batch, keep case bodies in the formal Excel workbook, "
            "page-discovery.csv, and batch-status.csv, and make helper scripts load only small shard files."
        )


def validate_compile(path: Path) -> None:
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        fail(f"{path} failed Python syntax validation:\n{exc.msg}")


def validate_json(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8-sig") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as exc:
        fail(f"{path} failed JSON syntax validation: line {exc.lineno}, column {exc.colno}: {exc.msg}")
    if path.name.startswith("function_cases_part_"):
        if not FUNCTION_CASE_PART_RE.match(path.name):
            fail(f"{path} must use three-digit shard naming like function_cases_part_001.json")
        cases = data.get("cases") if isinstance(data, dict) else data
        if not isinstance(cases, list):
            fail(f"{path} must contain a list or an object with a cases list")
        if len(cases) > MAX_FUNCTION_CASES_PER_PART:
            fail(f"{path} contains {len(cases)} function cases; each function_cases_part_*.json must contain at most {MAX_FUNCTION_CASES_PER_PART}")
        for index, case in enumerate(cases, start=1):
            validate_function_case(case, f"{path.name} case {index}")


def numbered_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def validate_numbered(text: str, label: str, minimum: int) -> None:
    lines = numbered_lines(text)
    if len(lines) < minimum:
        fail(f"{label} must contain at least {minimum} numbered lines")
    expected = 1
    for line in lines:
        match = re.match(r"^(\d+)\.\s*\S+", line)
        if not match:
            fail(f"{label} must use numbered lines like '1. ...': {line}")
        number = int(match.group(1))
        if number != expected:
            fail(f"{label} numbering must be continuous; expected {expected}, got {number}: {line}")
        expected += 1


def validate_function_case(case: object, label: str) -> None:
    if not isinstance(case, dict):
        fail(f"{label} must be an object")
    keys = set(case)
    forbidden = sorted(keys & FUNCTION_CASE_FORBIDDEN_FIELDS)
    if forbidden:
        fail(f"{label} contains forbidden/deprecated fields: {forbidden}")
    missing = [field for field in FUNCTION_CASE_REQUIRED_FIELDS if field not in case]
    if missing:
        fail(f"{label} is missing required fields: {missing}")
    extra = sorted(keys - set(FUNCTION_CASE_REQUIRED_FIELDS))
    if extra:
        fail(f"{label} contains extra fields not allowed by the standard schema: {extra}")
    case_id = str(case.get("用例 ID", "") or "").strip()
    if not case_id or "XXX" in case_id or case_id in {"TODO", "TBD"}:
        fail(f"{label} must use a concrete 用例 ID, got: {case_id}")
    function_point = str(case.get("功能点", "") or "").strip()
    title = str(case.get("用例标题", "") or "").strip()
    if not title.startswith(f"{function_point}-"):
        fail(f"{label} 用例标题 must use 功能点-当前标题 format")
    if case.get("测试类型") == "性能规格测试" or case.get("DFX维度") == "DFP性能":
        fail(f"{label} must not put performance scenarios into function case shards")
    combined = "\n".join(str(case.get(field, "") or "") for field in ["前置条件", "操作步骤", "预期结果", "备注"])
    if any(marker in combined for marker in ENGLISH_TEMPLATE_MARKERS):
        fail(f"{label} contains English placeholder/template text")
    validate_numbered(str(case.get("前置条件", "") or ""), f"{label} 前置条件", 2)
    validate_numbered(str(case.get("操作步骤", "") or ""), f"{label} 操作步骤", 4)
    validate_numbered(str(case.get("预期结果", "") or ""), f"{label} 预期结果", 3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated helper scripts and small data shards before execution.")
    parser.add_argument("--path", required=True, type=Path, help="Generated file or directory to scan recursively.")
    args = parser.parse_args()

    files = iter_generated_files(args.path)
    if not files:
        print(f"OK: no generated Python/JSON/text intermediate files found under {args.path}")
        return 0

    for path in files:
        validate_file_size(path)
        if path.suffix.lower() == ".py":
            validate_forbidden_quotes(path)
            validate_compile(path)
        elif path.suffix.lower() == ".json":
            validate_json(path)
    print(f"OK: validated {len(files)} generated intermediate file(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
