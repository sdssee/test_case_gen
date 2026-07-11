# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def replace_generated_block(text: str, begin: str, end: str, generated: str) -> str:
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError(f"Entry must contain exactly one generated block: {begin} ... {end}")
    begin_index = text.index(begin)
    end_index = text.index(end)
    if begin_index >= end_index:
        raise ValueError("Generated block markers are out of order")
    prefix, rest = text.split(begin, 1)
    _, suffix = rest.split(end, 1)
    return prefix + begin + "\n" + generated.rstrip() + "\n" + end + suffix


def validate_runtime_graphs(graphs: dict[str, list[str]], canonical: str, mirror: str) -> None:
    expected = {
        "codex": ["AGENTS.md", ".codebuddy/skills/test-design/SKILL.md", canonical],
        "codebuddy": ["CODEBUDDY.md", ".codebuddy/skills/test-design/SKILL.md", mirror],
    }
    if set(graphs) != set(expected):
        raise ValueError(f"runtime_graphs must contain exactly codex and codebuddy: {sorted(graphs)}")
    for runtime, required_graph in expected.items():
        graph = graphs[runtime]
        if graph != required_graph:
            raise ValueError(f"Runtime graph must use the required ordered entry chain: {runtime}: {required_graph}")
        if len(graph) != len(set(graph)):
            raise ValueError(f"Runtime graph contains a duplicate/cycle: {runtime}: {graph}")


def validate_frontmatter(text: str) -> None:
    lines = text.splitlines()
    if len(lines) < 5 or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("Skill YAML frontmatter is missing or malformed")
    end = lines[1:].index("---") + 1
    frontmatter = lines[1:end]
    for key in ["name", "description", "allowed-tools"]:
        matching = [line for line in frontmatter if line.startswith(f"{key}:")]
        if len(matching) != 1 or not matching[0].split(":", 1)[1].strip():
            raise ValueError(f"Skill frontmatter must contain exactly one non-empty {key}")
    if "name: test-design" not in frontmatter:
        raise ValueError("Skill frontmatter name must be test-design")


def validate_blocks(text: str, begin: str, end: str, local_begin: str, local_end: str, relative: str) -> None:
    for marker in [begin, end, local_begin, local_end]:
        if text.count(marker) != 1:
            raise ValueError(f"Entry must contain exactly one marker {marker}: {relative}")
    generated_start, generated_end = text.index(begin), text.index(end)
    local_start, local_end_index = text.index(local_begin), text.index(local_end)
    if not (generated_start < generated_end < local_start < local_end_index):
        raise ValueError(f"Generated and local override blocks must be ordered and non-nested: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or regenerate lightweight rule entrypoints from stable Gate IDs.")
    parser.add_argument("--write", action="store_true", help="Rewrite the Rule mirror and generated entry blocks.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    contract_path = repo_root / "docs" / "test-design" / "rules" / "entry-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("version") != 2:
        raise ValueError("entry-contract.json must use version 2")
    canonical_relative = contract["canonical_rule"]
    canonical_path = repo_root / canonical_relative
    canonical_text = canonical_path.read_text(encoding="utf-8")
    gate_lines = [line for line in canonical_text.splitlines() if line.startswith("- [TD-GATE-")]
    gate_ids = [line.split("]", 1)[0][3:] for line in gate_lines]
    if gate_ids != contract["required_gate_ids"] or len(gate_ids) != len(set(gate_ids)):
        raise ValueError(f"Canonical Rule Gate IDs changed, reordered, or duplicated: {gate_ids}")
    for gate_id, line in zip(gate_ids, gate_lines):
        missing = [marker for marker in contract["gate_semantics"].get(gate_id, []) if marker not in line]
        if missing:
            raise ValueError(f"Canonical Gate {gate_id} lost required semantics: {missing}")
    generated = "\n".join(gate_lines)

    mirrors = contract["rule_mirrors"]
    if len(mirrors) != 1:
        raise ValueError("Exactly one generated Rule mirror is supported")
    mirror_relative = mirrors[0]
    validate_runtime_graphs(contract["runtime_graphs"], canonical_relative, mirror_relative)
    for relative in contract["required_references"]:
        if not (repo_root / relative).exists():
            raise ValueError(f"Entry contract references a missing file: {relative}")

    begin = contract["generated_markers"]["begin"]
    end = contract["generated_markers"]["end"]
    local_begin = contract["local_override_markers"]["begin"]
    local_end = contract["local_override_markers"]["end"]
    proposed: dict[str, str] = {mirror_relative: canonical_text}
    originals: dict[str, str] = {}
    for relative in contract["generated_entries"]:
        path = repo_root / relative
        current = path.read_text(encoding="utf-8")
        originals[relative] = current
        validate_blocks(current, begin, end, local_begin, local_end, relative)
        proposed[relative] = replace_generated_block(current, begin, end, generated)
    originals[mirror_relative] = (repo_root / mirror_relative).read_text(encoding="utf-8")

    effective = {relative: proposed.get(relative, (repo_root / relative).read_text(encoding="utf-8")) for relative in contract["entry_budgets"]}
    for relative, text in effective.items():
        limit = int(contract["entry_budgets"][relative])
        if len(text) > limit:
            raise ValueError(f"Entry exceeds its character budget: {relative}: {len(text)} > {limit}")
    for relative, markers in contract["entry_required_markers"].items():
        missing = [marker for marker in markers if marker not in effective[relative]]
        if missing:
            raise ValueError(f"Entry is missing required route markers: {relative}: {missing}")
    validate_frontmatter(effective[".codebuddy/skills/test-design/SKILL.md"])
    graph_sizes = {
        runtime: sum(len(effective[relative]) for relative in graph)
        for runtime, graph in contract["runtime_graphs"].items()
    }
    for runtime, length in graph_sizes.items():
        if length > int(contract["runtime_budgets"][runtime]):
            raise ValueError(f"Runtime entry graph exceeds budget: {runtime}: {length}")

    if args.write:
        written: list[str] = []
        try:
            for relative, text in proposed.items():
                atomic_write(repo_root / relative, text)
                written.append(relative)
        except Exception:
            for relative in written:
                atomic_write(repo_root / relative, originals[relative])
            raise
    else:
        for relative, expected in proposed.items():
            if originals[relative] != expected:
                if relative == mirror_relative:
                    raise ValueError(f"Rule mirror drifted from canonical Rule: {relative}")
                raise ValueError(f"Generated Gate block drifted from canonical Rule: {relative}")

    print(f"OK: entry contract v2 aligned; runtime characters={graph_sizes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
