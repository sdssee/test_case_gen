# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import py_compile
import sys
from pathlib import Path


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


def iter_python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() == ".py" else []
    if not root.exists():
        fail(f"Path not found: {root}")
    return sorted(path for path in root.rglob("*.py") if path.is_file())


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


def validate_compile(path: Path) -> None:
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        fail(f"{path} failed Python syntax validation:\n{exc.msg}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate generated Python helper scripts before execution.")
    parser.add_argument("--path", required=True, type=Path, help="Python file or directory to scan recursively.")
    args = parser.parse_args()

    files = iter_python_files(args.path)
    if not files:
        print(f"OK: no generated Python scripts found under {args.path}")
        return 0

    for path in files:
        validate_forbidden_quotes(path)
        validate_compile(path)
    print(f"OK: validated {len(files)} generated Python script(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
