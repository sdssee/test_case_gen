# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .io_utils import atomic_copy, atomic_write_text, temporary_sibling


def canonical_module_parts(module_path: str, product_name: str | None = None) -> list[str]:
    parts = [part.strip() for part in module_path.replace("\\", ">").replace("/", ">").split(">") if part.strip()]
    if product_name and parts and parts[0] == product_name.strip():
        parts = parts[1:]
    return parts


def split_module_parts(module_path: str, product_name: str | None = None) -> tuple[str, list[str]]:
    if product_name:
        return product_name, canonical_module_parts(module_path, product_name)
    parts = canonical_module_parts(module_path)
    if len(parts) >= 4:
        return parts[0], parts[1:]
    return (parts[0] if parts else "产品"), parts


def copy_template_if_missing(source: Path, target: Path) -> bool:
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_copy(source, target)
    return True


def write_single_csv_row(path: Path, values: dict[str, str]) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        headers = next(reader, [])
    if not headers:
        raise ValueError(f"CSV template has no header row: {path}")
    row = {header: "" for header in headers}
    for key, value in values.items():
        if key in row:
            row[key] = value
    temporary = temporary_sibling(path)
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=headers)
            writer.writeheader()
            writer.writerow(row)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


CSV_REQUIRED_FILES = {
    "batch-status.csv": "batch-status-template.csv",
    "page-discovery.csv": "page-discovery-template.csv",
    "element-case-plan.csv": "element-case-plan-template.csv",
    "test-data-lifecycle.csv": "test-data-lifecycle-template.csv",
}

FUNCTION_CASE_PART_RE = re.compile(r"^function_cases_part_\d{3}\.json$")
MAX_FUNCTION_CASES_PER_PART = 10
FUNCTION_CASE_MANIFEST = "function_cases_manifest.json"
SHEET_DATA_FILES = [
    "overview.json",
    "requirements.json",
    "scenarios.json",
    "performance.json",
    "risks.json",
    "automation.json",
    "page_elements.json",
]
FUNCTION_CASE_REQUIRED_FIELDS = [
    "用例 ID",
    "Story ID/需求 ID",
    "模块",
    "功能点",
    "用例标题",
    "优先级",
    "测试类型",
    "DFX维度",
    "DFX场景",
    "前置条件",
    "测试数据",
    "操作步骤",
    "预期结果",
    "实际结果",
    "执行状态",
    "是否适合自动化",
    "关联风险",
    "备注",
]
FUNCTION_CASE_FORBIDDEN_FIELDS = {
    "用例编号",
    "用侊 ID",
    "用侊标题",
    "场景类型",
    "正向/反向",
    "steps",
    "expected",
    "title",
    "case_id",
    "id",
}
PLACEHOLDER_CASE_IDS = {"", "TC-A2A-XXX", "TC-XXX", "TODO", "TBD"}
ENGLISH_TEMPLATE_MARKERS = [
    "Open browser",
    "navigate to",
    "Verify page",
    "Operate element",
    "Execute extended scenario",
    "Extended scenario",
    "passes",
    "behaves as expected",
]

INTERACTIVE_ELEMENT_MARKERS = [
    "按钮",
    "输入",
    "下拉",
    "选择",
    "单选",
    "复选",
    "开关",
    "分页",
    "页码",
    "弹窗",
    "表格",
    "链接",
    "上传",
    "编辑",
    "删除",
    "保存",
    "创建",
    "新增",
    "添加",
    "提交",
    "测试",
    "搜索",
    "筛选",
]


def read_csv_exact(path: Path, expected_headers: list[str], label: str) -> list[dict[str, str]]:
    if not path.exists():
        raise ValueError(f"{label} not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        try:
            headers = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{label} has no header row: {path}") from exc
        if headers != expected_headers:
            raise ValueError(f"{label} header must match the standard template exactly. Expected {expected_headers}, got {headers}")
        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue
            if len(row) != len(headers):
                raise ValueError(f"{label} row {index} column count mismatch: expected {len(headers)}, got {len(row)}")
            rows.append({header: row[col].strip() for col, header in enumerate(headers)})
    return rows


def template_headers(templates_dir: Path, template_name: str) -> list[str]:
    template_path = templates_dir / template_name
    if not template_path.exists():
        raise ValueError(f"Batch template not found: {template_path}")
    with template_path.open("r", encoding="utf-8-sig", newline="") as fp:
        return next(csv.reader(fp))


def split_plan_values(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、;；/\s]+", text or "") if item.strip()]


def parse_positive_int(value: str, label: str) -> int:
    try:
        number = int(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a positive integer, got: {value}") from exc
    if number <= 0:
        raise ValueError(f"{label} must be greater than 0, got: {value}")
    return number


def is_yes(value: str) -> bool:
    return (value or "").strip() in {"是", "Y", "Yes", "yes", "true", "True", "1"}


def contains_any(text: str, markers: list[str]) -> bool:
    return any(marker in (text or "") for marker in markers)


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def is_template_or_empty_row(row: dict[str, str], meaningful_fields: list[str]) -> bool:
    return not any((row.get(field) or "").strip() for field in meaningful_fields)


def is_interactive_discovery_row(row: dict[str, str]) -> bool:
    combined = "\n".join(
        [
            row.get("元素名称/文案", ""),
            row.get("元素类型", ""),
            row.get("交互方式", ""),
            row.get("完整点击路径", ""),
        ]
    )
    return contains_any(combined, INTERACTIVE_ELEMENT_MARKERS)


def dfx_pair_count(dimensions_text: str, scenarios_text: str) -> int:
    dimensions = split_plan_values(dimensions_text)
    scenarios = split_plan_values(scenarios_text)
    if not dimensions or not scenarios:
        return 0
    if len(dimensions) == len(scenarios):
        return len(list(zip(dimensions, scenarios)))
    return max(len(dimensions), len(scenarios))


def element_is_planned(discovery_name: str, plan_rows: list[dict[str, str]]) -> bool:
    name = normalized_text(discovery_name)
    if not name:
        return True
    for row in plan_rows:
        planned = normalized_text(
            "\n".join(
                [
                    row.get("元素名称/文案", ""),
                    row.get("功能点", ""),
                    row.get("交互方式", ""),
                    row.get("业务路径", ""),
                ]
            )
        )
        if name in planned or planned in name:
            return True
    return False


def minimum_cases_for_plan_row(row: dict[str, str]) -> int:
    element_type = row.get("元素类型", "")
    interaction = row.get("交互方式", "")
    function_point = row.get("功能点", "")
    direction = row.get("测试设计方向", "")
    element_name = row.get("元素名称/文案", "")
    dimensions = set(split_plan_values(row.get("适用DFX维度", "")))
    scenarios = set(split_plan_values(row.get("适用DFX场景", "")))
    combined = "\n".join([element_type, interaction, function_point, direction, element_name])

    minimum = 1
    if contains_any(combined, ["搜索", "筛选"]):
        minimum = max(minimum, 4)
    if contains_any(combined, ["输入", "文本框", "数字", "邮箱", "手机号", "URL", "地址"]):
        minimum = max(minimum, 3)
    if contains_any(combined, ["下拉", "选择", "单选", "复选", "级联"]):
        minimum = max(minimum, 4)
    if contains_any(combined, ["分页", "翻页", "页码", "每页", "跳页"]):
        minimum = max(minimum, 5)
    if contains_any(combined, ["弹窗", "抽屉", "对话框"]):
        minimum = max(minimum, 3)
    if contains_any(combined, ["新增", "创建", "添加", "接入", "保存", "提交"]):
        minimum = max(minimum, 5)
    if contains_any(combined, ["编辑", "修改"]):
        minimum = max(minimum, 4)
    if "删除" in combined:
        minimum = max(minimum, 3)
    if contains_any(combined, ["表格", "行操作", "批量"]):
        minimum = max(minimum, 3)
    if contains_any(combined, ["密钥", "Token", "密码", "鉴权", "权限"]):
        minimum = max(minimum, 4)
    if is_yes(row.get("是否涉及CRUD闭环", "")):
        minimum = max(minimum, 5)
    if is_yes(row.get("是否涉及配置生效", "")):
        minimum = max(minimum, 4)

    if {"边界值", "异常输入", "逆向操作"} & scenarios:
        minimum = max(minimum, 3)
    if dimensions & {"DFS安全", "DFR可靠", "DFU可用", "DFB业务"}:
        minimum += 1
    if scenarios & {"数据一致", "幂等性", "权限控制", "数据脱敏", "错误提示", "业务流程", "数据准确"}:
        minimum += 1
    if dimensions & {"DFP性能", "DFX极端"}:
        # 性能和极端场景不进入功能用例，但必须进入性能设计/风险/自动化建议。
        minimum = max(minimum, 1)

    return max(minimum, dfx_pair_count(row.get("适用DFX维度", ""), row.get("适用DFX场景", "")) or 1)


def planned_case_id_count(value: str) -> int:
    ids = split_plan_values(value)
    return len(ids)


def numbered_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def validate_numbered_sequence(text: str, label: str, minimum: int) -> None:
    lines = numbered_lines(text)
    if len(lines) < minimum:
        raise ValueError(f"{label} must contain at least {minimum} numbered lines")
    expected = 1
    for line in lines:
        match = re.match(r"^(\d+)\.\s*\S+", line)
        if not match:
            raise ValueError(f"{label} must use numbered lines like '1. ...': {line}")
        number = int(match.group(1))
        if number != expected:
            raise ValueError(f"{label} numbering must be continuous; expected {expected}, got {number}: {line}")
        expected += 1


def validate_case_steps_and_expected(case: dict[str, str], label: str) -> None:
    steps = str(case.get("操作步骤", "") or "")
    expected = str(case.get("预期结果", "") or "")
    precondition = str(case.get("前置条件", "") or "")
    combined = "\n".join([precondition, steps, expected, str(case.get("备注", "") or "")])

    if any(marker in combined for marker in ENGLISH_TEMPLATE_MARKERS):
        raise ValueError(f"{label} contains English placeholder/template text; generate concrete Chinese executable steps and expectations")

    validate_numbered_sequence(precondition, f"{label} 前置条件", 2)
    validate_numbered_sequence(steps, f"{label} 操作步骤", 4)
    validate_numbered_sequence(expected, f"{label} 预期结果", 3)

    first_steps = "\n".join(numbered_lines(steps)[:3])
    entry_markers = ["登录", "打开系统", "访问系统", "进入系统", "打开平台", "访问平台", "进入平台", "<product_login_url>"]
    navigation_markers = ["一级", "二级", "三级", "菜单", "模块", "导航", "路径", ">", "页面"]
    if not any(marker in first_steps for marker in entry_markers):
        raise ValueError(f"{label} 操作步骤 must start from system/project entry")
    if not any(marker in first_steps for marker in navigation_markers):
        raise ValueError(f"{label} 操作步骤 must include complete menu/module navigation")

    if re.search(r"\b点(搜索|保存|删除|确定|确认|取消)\b", steps):
        raise ValueError(f"{label} 操作步骤 contains overly terse wording like '点搜索'; write the full control name and action")
    if any(marker in steps for marker in ["操作元素", "扩展场景", "基本验证", "Extended"]):
        raise ValueError(f"{label} 操作步骤 contains generic generated wording; write concrete page operations")
    if any(marker in expected for marker in ["behaves as expected", "passes", "符合预期", "正常显示"]) and len(numbered_lines(expected)) <= 3:
        raise ValueError(f"{label} 预期结果 is too generic; write observable page/data/state outcomes")


def validate_function_case_schema(case: dict[str, object], label: str, planned_ids: set[str] | None = None) -> None:
    if not isinstance(case, dict):
        raise ValueError(f"{label} must be a JSON object")
    keys = set(case)
    forbidden = sorted(keys & FUNCTION_CASE_FORBIDDEN_FIELDS)
    if forbidden:
        raise ValueError(f"{label} contains forbidden/deprecated fields: {forbidden}; use the standard function case schema")
    missing = [field for field in FUNCTION_CASE_REQUIRED_FIELDS if field not in case]
    if missing:
        raise ValueError(f"{label} is missing required fields: {missing}")
    extra = sorted(keys - set(FUNCTION_CASE_REQUIRED_FIELDS))
    if extra:
        raise ValueError(f"{label} contains extra fields not allowed by the standard schema: {extra}")

    normalized = {field: "" if case.get(field) is None else str(case.get(field)).strip() for field in FUNCTION_CASE_REQUIRED_FIELDS}
    case_id = normalized["用例 ID"]
    if case_id in PLACEHOLDER_CASE_IDS or "XXX" in case_id:
        raise ValueError(f"{label} must use a concrete 用例 ID, got: {case_id}")
    if planned_ids is not None and planned_ids and case_id not in planned_ids:
        raise ValueError(f"{label} 用例 ID is not declared in element-case-plan.csv 计划用例ID: {case_id}")

    function_point = normalized["功能点"]
    title = normalized["用例标题"]
    if not function_point:
        raise ValueError(f"{label} must fill 功能点")
    if not title.startswith(f"{function_point}-"):
        raise ValueError(f"{label} 用例标题 must use 功能点-当前标题 format: {title}")
    for field in ["模块", "优先级", "测试类型", "DFX维度", "DFX场景", "测试数据"]:
        if not normalized[field]:
            raise ValueError(f"{label} must fill {field}")
    if normalized["测试类型"] == "性能规格测试" or normalized["DFX维度"] == "DFP性能":
        raise ValueError(f"{label} must not put performance scenarios into function case shards")

    validate_case_steps_and_expected(normalized, label)


def validate_function_case_part(path: Path, planned_ids: set[str] | None = None) -> int:
    if not FUNCTION_CASE_PART_RE.match(path.name):
        raise ValueError(f"{path} must use function_cases_part_001.json naming")
    with path.open("r", encoding="utf-8-sig") as fp:
        data = json.load(fp)
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError(f"{path} must contain a list or an object with a cases list")
    if len(cases) > MAX_FUNCTION_CASES_PER_PART:
        raise ValueError(f"{path} contains {len(cases)} cases; each function_cases_part_*.json must contain at most {MAX_FUNCTION_CASES_PER_PART}")
    seen_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        validate_function_case_schema(case, f"{path.name} case {index}", planned_ids)
        case_id = str(case.get("用例 ID", "")).strip()
        if case_id in seen_ids:
            raise ValueError(f"{path.name} has duplicate 用例 ID: {case_id}")
        seen_ids.add(case_id)
    return len(cases)


def planned_case_ids(plan_rows: list[dict[str, str]]) -> set[str]:
    ids: set[str] = set()
    for row in plan_rows:
        ids.update(split_plan_values(row.get("计划用例ID", "")))
    return ids


def manifest_parts(data_dir: Path) -> list[Path]:
    manifest = data_dir / FUNCTION_CASE_MANIFEST
    if not manifest.exists():
        raise ValueError(f"artifacts/data must contain {FUNCTION_CASE_MANIFEST}; Excel assembly must read the manifest, not glob stale shards")
    with manifest.open("r", encoding="utf-8-sig") as fp:
        data = json.load(fp)
    raw_parts = data.get("parts") if isinstance(data, dict) else data
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError(f"{manifest} must contain a non-empty parts list")
    result: list[Path] = []
    for item in raw_parts:
        name = str(item).strip()
        if not FUNCTION_CASE_PART_RE.match(name):
            raise ValueError(f"{manifest} contains invalid part name: {name}; use function_cases_part_001.json")
        path = data_dir / name
        if not path.exists():
            raise ValueError(f"{manifest} references missing function case shard: {name}")
        result.append(path)
    declared = {path.name for path in result}
    stale = sorted(path.name for path in data_dir.glob("function_cases_part_*.json") if path.name not in declared)
    if stale:
        raise ValueError(f"artifacts/data contains stale function case shards not listed in {FUNCTION_CASE_MANIFEST}: {stale[:10]}")
    return result


def prepare_function_case_generation(run_dir: Path) -> None:
    data_dir = run_dir.resolve() / "artifacts" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for pattern in ["function_cases_part_*.json", FUNCTION_CASE_MANIFEST]:
        for path in data_dir.glob(pattern):
            if path.is_file():
                removed.append(path.name)
                path.unlink()
    print(f"OK: prepared function case generation under {data_dir}; removed {len(removed)} stale file(s).")


def validate_batch_artifacts(run_dir: Path, phase: str = "cases") -> None:
    run_dir = run_dir.resolve()
    if not run_dir.exists():
        raise ValueError(f"Batch run directory not found: {run_dir}")
    templates_dir = run_dir.parent / "templates"
    expected_headers = {
        csv_name: template_headers(templates_dir, template_name)
        for csv_name, template_name in CSV_REQUIRED_FILES.items()
    }

    batch_rows = read_csv_exact(run_dir / "batch-status.csv", expected_headers["batch-status.csv"], "batch-status.csv")
    discovery_rows = read_csv_exact(run_dir / "page-discovery.csv", expected_headers["page-discovery.csv"], "page-discovery.csv")
    plan_rows = read_csv_exact(run_dir / "element-case-plan.csv", expected_headers["element-case-plan.csv"], "element-case-plan.csv")
    lifecycle_rows = read_csv_exact(run_dir / "test-data-lifecycle.csv", expected_headers["test-data-lifecycle.csv"], "test-data-lifecycle.csv")

    real_discovery_rows = [
        row for row in discovery_rows
        if not is_template_or_empty_row(row, ["页面/入口", "元素名称/文案", "元素类型", "交互方式"])
    ]
    if phase in {"discovery", "plan", "cases"} and not real_discovery_rows:
        raise ValueError("page-discovery.csv must contain real page elements before continuing")

    interactive_rows = [row for row in real_discovery_rows if is_interactive_discovery_row(row)]
    if phase in {"discovery", "plan", "cases"} and not interactive_rows:
        raise ValueError("page-discovery.csv must contain clickable/input/selectable/testable elements, not only static text")

    if phase in {"plan", "cases"}:
        real_plan_rows = [
            row for row in plan_rows
            if not is_template_or_empty_row(row, ["页面/入口", "功能点", "元素名称/文案", "元素类型", "测试设计方向", "计划用例ID"])
        ]
        if not real_plan_rows:
            raise ValueError("element-case-plan.csv must contain real element-driven case plans before writing cases")

        missing_plan = [
            row.get("元素名称/文案", "")
            for row in interactive_rows
            if row.get("元素名称/文案", "").strip() and not element_is_planned(row.get("元素名称/文案", ""), real_plan_rows)
        ]
        if missing_plan:
            preview = ", ".join(missing_plan[:10])
            raise ValueError(f"element-case-plan.csv is missing interactive page elements from page-discovery.csv: {preview}")

        computed_min_total = 0
        declared_total = 0
        for index, row in enumerate(real_plan_rows, start=2):
            declared = parse_positive_int(row.get("应生成用例数", ""), f"element-case-plan.csv row {index} 应生成用例数")
            minimum = minimum_cases_for_plan_row(row)
            if declared < minimum:
                raise ValueError(
                    f"element-case-plan.csv row {index} declares {declared} case(s), "
                    f"but element type + DFX minimum requires at least {minimum}: "
                    f"{row.get('元素名称/文案', '')} / {row.get('测试设计方向', '')}"
                )
            if planned_case_id_count(row.get("计划用例ID", "")) < declared:
                raise ValueError(f"element-case-plan.csv row {index} must provide at least {declared} planned case ID(s)")
            computed_min_total += minimum
            declared_total += declared

        if declared_total < len(interactive_rows):
            raise ValueError(
                f"element-case-plan.csv declares only {declared_total} cases for {len(interactive_rows)} interactive elements; "
                "DFX扩展后用例数不得低于可交互元素数"
            )

        has_crud_or_config = any(
            is_yes(row.get("是否涉及CRUD闭环", "")) or is_yes(row.get("是否涉及配置生效", ""))
            or contains_any(
                "\n".join([row.get("功能点", ""), row.get("元素名称/文案", ""), row.get("测试设计方向", "")]),
                ["新增", "创建", "添加", "保存", "提交", "编辑", "修改", "删除", "配置", "生效"],
            )
            for row in real_plan_rows
        )
        real_lifecycle_rows = [
            row for row in lifecycle_rows
            if not is_template_or_empty_row(row, ["测试数据ID/名称", "创建结果", "查看结果", "编辑结果", "删除确认结果", "清理状态"])
        ]
        if has_crud_or_config:
            if not real_lifecycle_rows:
                raise ValueError("test-data-lifecycle.csv must record AI_TEST/CODEX_TEST CRUD/config lifecycle before writing cases")
            for index, row in enumerate(real_lifecycle_rows, start=2):
                combined = "\n".join(row.values())
                if not contains_any(combined, ["AI_TEST", "CODEX_TEST", "用户提供测试数据"]):
                    raise ValueError(f"test-data-lifecycle.csv row {index} must bind to AI_TEST/CODEX_TEST or user-provided test data")

    artifacts_dir = run_dir / "artifacts"
    scripts_dir = artifacts_dir / "scripts"
    data_dir = artifacts_dir / "data"
    screenshots_dir = artifacts_dir / "screenshots"
    for required_dir in [artifacts_dir, scripts_dir, data_dir, screenshots_dir]:
        if phase in {"cases"} and not required_dir.exists():
            raise ValueError(f"Required batch artifact directory is missing: {required_dir}")

    misplaced_parts = sorted(artifacts_dir.glob("function_cases_part_*.json")) if artifacts_dir.exists() else []
    if misplaced_parts:
        raise ValueError("function_cases_part_*.json must be written under artifacts/data, not artifacts root")

    if phase == "cases":
        parts = manifest_parts(data_dir)
        planned_ids = planned_case_ids(plan_rows)
        case_count = sum(validate_function_case_part(path, planned_ids) for path in parts)
        missing_sheet_files = [name for name in SHEET_DATA_FILES if not (data_dir / name).exists()]
        if missing_sheet_files:
            raise ValueError(f"artifacts/data is missing sheet-split files: {missing_sheet_files}")
        if plan_rows:
            declared_total = sum(
                int(row.get("应生成用例数", "0"))
                for row in plan_rows
                if row.get("应生成用例数", "").isdigit()
            )
            if declared_total and case_count < declared_total:
                raise ValueError(f"function case shards contain {case_count} cases, fewer than element-case-plan declared {declared_total}")

    if batch_rows:
        first_status = batch_rows[0]
        if phase in {"plan", "cases"}:
            all_zero = all((first_status.get(field, "") or "0") == "0" for field in ["页面数", "元素总数", "功能用例数"])
            if first_status.get("状态") == "待开始" or all_zero:
                raise ValueError("batch-status.csv is still in the initial state; update page/element/case counts before continuing")

    print(f"OK: batch artifacts passed {phase} gate: {run_dir}")


def init_batch_run(
    project_root: Path,
    run_id: str,
    module_path: str,
    batch_id: str,
    product_name: str | None = None,
    resume: bool = False,
    force_reinitialize: bool = False,
) -> Path:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("run-id must be a single safe directory name without path separators")
    batch_runs_dir = (project_root / "docs" / "test-assets" / "batch-runs").resolve()
    run_dir = batch_runs_dir / run_id
    templates_dir = batch_runs_dir / "templates"
    required_templates = {
        "batch-plan.md": templates_dir / "batch-plan-template.md",
        "batch-status.csv": templates_dir / "batch-status-template.csv",
        "batch-review.md": templates_dir / "batch-review-template.md",
        "page-discovery.csv": templates_dir / "page-discovery-template.csv",
        "element-case-plan.csv": templates_dir / "element-case-plan-template.csv",
        "test-data-lifecycle.csv": templates_dir / "test-data-lifecycle-template.csv",
    }
    missing = [str(path) for path in required_templates.values() if not path.exists()]
    if missing:
        raise ValueError(f"Batch template files are missing: {missing}")

    if run_dir.exists():
        if resume:
            missing_run_files = [name for name in required_templates if not (run_dir / name).exists()]
            if missing_run_files:
                raise ValueError(f"Existing batch run is incomplete and cannot be resumed: {missing_run_files}")
            print(f"Resumed existing batch run without changing ledgers: {run_dir}")
            return run_dir
        if not force_reinitialize:
            raise ValueError(
                f"Batch run already exists: {run_dir}. Use --resume to keep existing data or "
                "--force-reinitialize to back it up and create a clean run."
            )
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_dir = batch_runs_dir / f"{run_id}_backup_{timestamp}"
        shutil.copytree(run_dir, backup_dir)
        shutil.rmtree(run_dir)
        print(f"Backed up existing batch run before reinitializing: {backup_dir}")

    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = run_dir / "artifacts"
    scripts_dir = artifacts_dir / "scripts"
    data_dir = artifacts_dir / "data"
    screenshots_dir = artifacts_dir / "screenshots"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    for target_name, template_path in required_templates.items():
        copy_template_if_missing(template_path, run_dir / target_name)

    product, modules = split_module_parts(module_path, product_name)
    level1 = modules[0] if len(modules) > 0 else ""
    level2 = modules[1] if len(modules) > 1 else ""
    level3 = modules[2] if len(modules) > 2 else ""
    leaf_path = ">".join(modules) or module_path

    write_single_csv_row(
        run_dir / "batch-status.csv",
        {
            "批次ID": batch_id,
            "一级模块": level1,
            "二级菜单": level2,
            "三级菜单/页面域": level3,
            "批次范围": leaf_path,
            "状态": "待开始",
            "页面数": "0",
            "元素总数": "0",
            "已覆盖元素数": "0",
            "待确认元素数": "0",
            "功能用例数": "0",
            "性能场景数": "0",
            "异常用例数": "0",
            "边界用例数": "0",
            "权限/状态用例数": "0",
            "数据一致性用例数": "0",
            "页面遍历完成": "否",
            "功能用例完成": "否",
            "性能设计完成": "否",
            "异常边界权限覆盖完成": "否",
            "页面元素覆盖完成": "否",
            "产品版图已更新": "否",
            "覆盖质量自检": "未通过",
            "导入文件已生成": "否",
            "最小标题路径": leaf_path,
            "下一步动作": "开始页面实探并补充 page-discovery.csv",
        },
    )
    write_single_csv_row(
        run_dir / "page-discovery.csv",
        {
            "批次ID": batch_id,
            "一级模块": level1,
            "二级菜单": level2,
            "三级菜单/页面域": level3,
            "最小标题路径": leaf_path,
            "菜单路径/URL": leaf_path,
            "发现方式": "浏览器实探/页面资料",
            "是否已生成用例": "否",
            "覆盖状态": "待确认",
            "备注": "按当前批次页面实探结果补充页面、元素、取值、联动和关联用例",
        },
    )

    init_note = (
        "\n\n## 批次初始化\n"
        f"- 产品/系统：{product}\n"
        f"- 模块路径：{leaf_path}\n"
        f"- 批次ID：{batch_id}\n"
        "- 执行要求：先补全 page-discovery.csv，再生成测试设计、导入文件和 batch-status.csv 覆盖数据。\n"
    )
    for markdown_name in ["batch-plan.md", "batch-review.md"]:
        markdown_path = run_dir / markdown_name
        text = markdown_path.read_text(encoding="utf-8-sig")
        if "## 批次初始化" not in text:
            atomic_write_text(markdown_path, text.rstrip() + init_note, encoding="utf-8")

    print(f"Initialized batch run: {run_dir}")
    return run_dir
