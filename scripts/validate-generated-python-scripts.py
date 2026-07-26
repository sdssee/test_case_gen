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
SCAN_EXTS = {".py", ".json", ".csv", ".md", ".txt"}

SMART_QUOTE_HINT_CHARS = "“”‘’「」『』"


def fail(message: str) -> None:
    raise AssertionError(message)


def iter_generated_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in SCAN_EXTS else []
    if not root.exists():
        fail(f"Path not found: {root}")
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SCAN_EXTS)


def validate_utf8(path: Path) -> None:
    try:
        path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        fail(f"{path} is not valid UTF-8 text: {exc}")


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
            "small structured shard files, and make helper scripts load only the current shard."
        )


def validate_compile(path: Path) -> None:
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        syntax_error = getattr(exc, "exc_value", None)
        error_line = getattr(syntax_error, "text", "") or ""
        hint = ""
        if any(char in error_line for char in SMART_QUOTE_HINT_CHARS):
            hint = "\nHint: Chinese quote characters may appear in business text, but must not be used as Python string delimiters."
        fail(f"{path} failed Python syntax validation:\n{exc.msg}{hint}")


def validate_json(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8-sig") as fp:
            json.load(fp)
    except json.JSONDecodeError as exc:
        fail(f"{path} failed JSON syntax validation: line {exc.lineno}, column {exc.colno}: {exc.msg}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated helper scripts and small data shards before execution.")
    parser.add_argument("--path", required=True, type=Path, help="Generated file or directory to scan recursively.")
    args = parser.parse_args()

    files = iter_generated_files(args.path)
    if not files:
        print(f"OK: no generated Python/JSON/text intermediate files found under {args.path}")
        return 0

    errors: list[str] = []
    for path in files:
        validators = [validate_file_size, validate_utf8]
        if path.suffix.lower() == ".py":
            validators.append(validate_compile)
        elif path.suffix.lower() == ".json":
            validators.append(validate_json)
        for validator in validators:
            try:
                validator(path)
            except Exception as exc:
                errors.append(str(exc))
    if errors:
        fail(f"Generated intermediate validation found {len(errors)} issue(s):\n- " + "\n- ".join(errors))
    print(f"OK: validated {len(files)} generated intermediate file(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
