# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import py_compile
import sys
from pathlib import Path


MAX_FILE_BYTES = 256 * 1024
MAX_PYTHON_BYTES = 200 * 1024
MAX_JSON_BYTES = 256 * 1024
MAX_FUNCTION_CASES_PER_PART = 10
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
        cases = data.get("cases") if isinstance(data, dict) else data
        if not isinstance(cases, list):
            fail(f"{path} must contain a list or an object with a cases list")
        if len(cases) > MAX_FUNCTION_CASES_PER_PART:
            fail(f"{path} contains {len(cases)} function cases; each function_cases_part_*.json must contain at most {MAX_FUNCTION_CASES_PER_PART}")


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
