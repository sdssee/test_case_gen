# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path
from typing import Any, Iterable

from openpyxl import load_workbook
from openpyxl.formula.translate import Translator
from openpyxl.styles import Alignment

from .excel_utils import clear_data_rows, header_map, resize_workbook_structures, write_mapped_row
from .io_utils import atomic_save_workbook, temporary_sibling
from .session_runtime import artifact_digest, artifact_paths, load_cases, load_facts, load_plan


SHEETS = [
    "测试设计总览", "需求用户故事拆解", "测试场景矩阵", "功能测试用例",
    "性能测试设计", "风险与待确认问题", "自动化建议", "页面元素覆盖清单",
]
FUNCTION_SHEET = "功能测试用例"
MULTILINE_HEADERS = {"前置条件", "测试数据", "操作步骤", "预期结果", "备注", "描述", "影响范围", "建议处理方式"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "；".join(str(item) for item in value)
    return str(value)


def _numbered(values: Any) -> str:
    if not isinstance(values, list):
        values = [line for line in str(values or "").splitlines() if line.strip()]
    return "\n".join(f"{index}. {str(value).strip()}" for index, value in enumerate(values, 1))


def _paired_columns(steps: Any) -> tuple[str, str]:
    if not isinstance(steps, list):
        return "", ""
    actions = [str(step.get("action", "")).strip() for step in steps if isinstance(step, dict)]
    expected = [str(step.get("expected", "")).strip() for step in steps if isinstance(step, dict)]
    return _numbered(actions), _numbered(expected)


def _visual_line_count(value: Any, characters_per_line: int = 36) -> int:
    text = str(value or "")
    if not text:
        return 1
    return sum(max(1, math.ceil(len(line) / characters_per_line)) for line in text.splitlines())


def _row_height(values: Iterable[Any]) -> float:
    lines = max((_visual_line_count(value) for value in values), default=1)
    return float(max(36, min(180, 8 + lines * 16)))


def _workbook_structure(workbook) -> dict[str, Any]:
    """Capture template-owned structure while excluding expandable data ranges."""
    result: dict[str, Any] = {"sheetnames": list(workbook.sheetnames), "sheets": {}}
    for worksheet in workbook.worksheets:
        validations = sorted((
            validation.type, validation.operator, str(validation.formula1 or ""), str(validation.formula2 or ""),
            bool(validation.allow_blank), str(validation.errorTitle or ""), str(validation.error or ""),
            str(validation.promptTitle or ""), str(validation.prompt or ""),
        ) for validation in worksheet.data_validations.dataValidation)
        tables = sorted((
            table.name, table.displayName, str(getattr(table.tableStyleInfo, "name", "") or ""),
            tuple(column.name for column in table.tableColumns),
        ) for table in worksheet.tables.values())
        result["sheets"][worksheet.title] = {
            "state": worksheet.sheet_state,
            "headers": tuple(cell.value for cell in worksheet[1]),
            "widths": tuple((key, value.width, value.hidden) for key, value in sorted(worksheet.column_dimensions.items())),
            "header_hidden": bool(worksheet.row_dimensions[1].hidden),
            "freeze": str(worksheet.freeze_panes or ""),
            "merged": tuple(sorted(str(item) for item in worksheet.merged_cells.ranges)),
            "validations": validations,
            "tables": tables,
            "has_filter": bool(worksheet.auto_filter.ref),
            "print_area": str(worksheet.print_area or ""),
            "print_titles": str(worksheet.print_titles or ""),
            "orientation": str(worksheet.page_setup.orientation or ""),
        }
    return result


def _assert_structure_preserved(before: dict[str, Any], workbook, label: str) -> None:
    after = _workbook_structure(workbook)
    if before != after:
        changed = [name for name in before.get("sheets", {}) if before["sheets"].get(name) != after.get("sheets", {}).get(name)]
        raise ValueError(f"{label} template structure changed unexpectedly: {changed or 'sheet order'}")


def _sample_formulas(worksheet, row: int = 2) -> dict[int, str]:
    return {
        cell.column: str(cell.value)
        for cell in worksheet[row]
        if isinstance(cell.value, str) and cell.value.startswith("=")
    }


def _apply_sample_formulas(worksheet, formulas: dict[int, str], target_row: int) -> None:
    for column, formula in formulas.items():
        origin = worksheet.cell(2, column).coordinate
        destination = worksheet.cell(target_row, column).coordinate
        worksheet.cell(target_row, column).value = Translator(formula, origin=origin).translate_formula(destination)


def _function_names(facts: dict[str, Any], plan: dict[str, Any]) -> dict[str, str]:
    result = {
        str(row.get("fact_id")): str(row.get("name", ""))
        for row in facts.get("functions", [])
    }
    for row in plan.get("functions", []):
        if row.get("function_ref") and row.get("name"):
            result[str(row["function_ref"])] = str(row["name"])
    return result


def build_sheet_rows(run_dir: Path) -> dict[str, list[dict[str, str]]]:
    facts = load_facts(run_dir)
    plan = load_plan(run_dir)
    case_document = load_cases(run_dir)
    scope = facts.get("scope", {})
    functions = _function_names(facts, plan)
    fact_names = {
        str(row.get("fact_id")): str(row.get("name") or row.get("transaction_type") or "页面实探事实")
        for collection in ("pages", "functions", "elements", "transactions")
        for row in facts.get(collection, [])
    }
    open_items = facts.get("open_items", [])
    pending = [row for row in open_items if row.get("category") in {"external_question", "blocked_condition"}]
    risks = [row for row in open_items if row.get("category") == "observed_risk"] + plan.get("risks", [])
    rows: dict[str, list[dict[str, str]]] = {name: [] for name in SHEETS}
    rows["测试设计总览"].append({
        "项目/模块": _text(scope.get("module_path")),
        "需求名称": _text(scope.get("requirement_name") or scope.get("module_path")),
        "版本/迭代": _text(scope.get("version")),
        "测试负责人": _text(scope.get("owner")),
        "需求来源": _text(scope.get("source")),
        "测试范围": _text(scope.get("test_scope") or scope.get("module_path")),
        "不测范围": _text(scope.get("out_of_scope")),
        "测试类型": "功能测试；适用DFX扩展",
        "测试环境": _text(scope.get("environment")),
        "主要风险": "；".join(_text(row.get("description")) for row in risks if row.get("description")),
        "准入条件": "页面实探事实已固化，测试数据满足安全约束",
        "准出条件": "结构化用例完成一次跨产物审计，两个交付文件技术校验通过",
        "待确认问题": "；".join(_text(row.get("description") or row.get("reason")) for row in pending),
    })
    for index, requirement in enumerate(scope.get("requirements", []), 1):
        rows["需求用户故事拆解"].append({
            "Story ID/需求 ID": _text(requirement.get("requirement_id") or f"REQ-{index:03d}"),
            "用户故事/需求描述": _text(requirement.get("description")),
            "角色": _text(requirement.get("role")),
            "业务价值": _text(requirement.get("business_value")),
            "验收标准": _text(requirement.get("acceptance_criteria")),
            "业务规则": _text(requirement.get("business_rules")),
            "前置条件": _text(requirement.get("preconditions")),
            "后置影响": _text(requirement.get("postconditions")),
            "依赖系统": _text(requirement.get("dependencies")),
            "待确认问题": _text(requirement.get("unresolved")),
        })
    for function in plan.get("functions", []):
        for case in function.get("cases", []):
            rows["测试场景矩阵"].append({
                "场景 ID": _text(case.get("case_id")),
                "Story ID/需求 ID": _text(case.get("requirement_id")),
                "功能点": _text(function.get("name")),
                "测试维度": _text(case.get("strategy")),
                "DFX维度": _text(case.get("dfx_dimension")),
                "DFX场景": _text(case.get("dfx_scenario")),
                "测试对象/页面元素": "；".join(dict.fromkeys(
                    fact_names.get(str(ref), "") for ref in case.get("fact_refs", []) if fact_names.get(str(ref), "")
                )),
                "输入数据/状态条件": _text(case.get("test_data")),
                "观察点": _text(case.get("observation")),
                "风险等级": _text(case.get("risk_level") or "中"),
                "优先级": _text(case.get("priority") or "P1"),
                "是否生成用例": "是",
                "备注": _text(case.get("notes")),
            })
    for case in case_document.get("cases", []):
        function_id = str(case.get("function_ref", ""))
        action_text, expected_text = _paired_columns(case.get("steps"))
        rows[FUNCTION_SHEET].append({
            "用例 ID": _text(case.get("case_id")),
            "Story ID/需求 ID": _text(case.get("requirement_id")),
            "模块": _text(scope.get("module_path")),
            "功能点": functions.get(function_id, function_id),
            "用例标题": _text(case.get("title")),
            "优先级": _text(case.get("priority") or "P1"),
            "测试类型": _text(case.get("test_type") or "功能测试"),
            "DFX维度": _text(case.get("dfx_dimension")),
            "DFX场景": _text(case.get("dfx_scenario")),
            "前置条件": _numbered(case.get("preconditions")),
            "测试数据": _text(case.get("test_data")),
            "操作步骤": action_text,
            "预期结果": expected_text,
            "实际结果": "",
            "执行状态": "未执行",
            "是否适合自动化": "是" if case.get("automation") is True else ("否" if case.get("automation") is False else "待评估"),
            "关联风险": _text(case.get("risks")),
            "备注": _text(case.get("notes")),
        })
    for item in plan.get("performance_scenarios", []):
        mapping = {
            "性能场景 ID": "scenario_id", "Story ID/需求 ID": "requirement_id", "业务链路": "flow",
            "性能测试类型": "test_type", "DFX维度": "dfx_dimension", "DFX场景": "dfx_scenario",
            "目标用户量/并发数": "concurrency", "TPS/QPS 目标": "throughput", "响应时间目标": "response_time",
            "数据量级": "data_scale", "测试时长": "duration", "监控指标": "metrics", "通过标准": "pass_criteria",
            "造数策略": "data_strategy", "风险说明": "risk", "是否纳入本轮测试": "included",
        }
        rows["性能测试设计"].append({header: _text(item.get(key)) for header, key in mapping.items()})
    for index, risk in enumerate(risks + pending, 1):
        rows["风险与待确认问题"].append({
            "编号": _text(risk.get("risk_id") or f"RISK-{index:03d}"),
            "类型": _text(risk.get("type") or ("待确认" if risk in pending else "风险")),
            "关联DFX维度": _text(risk.get("dfx_dimension")),
            "关联DFX场景": _text(risk.get("dfx_scenario")),
            "描述": _text(risk.get("description") or risk.get("reason")),
            "影响范围": _text(risk.get("impact")),
            "风险等级": _text(risk.get("level") or "中"),
            "建议处理方式": _text(risk.get("recommendation")),
            "负责人": _text(risk.get("owner")),
            "状态": _text(risk.get("status") or "待确认"),
        })
    for case in case_document.get("cases", []):
        if case.get("automation") is not False:
            rows["自动化建议"].append({
                "用例 ID/场景 ID": _text(case.get("case_id")), "自动化层级": "UI",
                "自动化价值": _text(case.get("automation_value") or "回归验证"),
                "自动化优先级": _text(case.get("priority") or "P1"),
                "依赖数据": _text(case.get("test_data")), "Mock 需求": "无",
                "稳定性风险": _text(case.get("automation_risk")), "建议框架/工具": "按项目现有自动化框架",
                "备注": _text(case.get("notes")),
            })
    case_by_fact: dict[str, list[str]] = {}
    for case in case_document.get("cases", []):
        for fact_id in case.get("fact_refs", []):
            case_by_fact.setdefault(str(fact_id), []).append(str(case.get("case_id")))
    for element in facts.get("elements", []):
        element_id = str(element.get("fact_id"))
        element_label = "-".join(filter(None, [str(element.get("page_name", "")).strip(), str(element.get("name", "")).strip()]))
        covered_case_ids = list(dict.fromkeys(case_by_fact.get(element_id, [])))
        rows["页面元素覆盖清单"].append({
            "元素 ID": element_label or _text(element.get("name")), "Story ID/需求 ID": _text(element.get("requirement_id")),
            "页面/入口": _text(element.get("page_name") or element.get("page_id")),
            "页面 URL/菜单路径": _text(element.get("menu_path")),
            "元素名称/文案": _text(element.get("name")), "元素类型": _text(element.get("type")),
            "交互方式": _text(element.get("interaction")), "适用DFX维度": _text(element.get("dfx_dimensions")),
            "适用DFX场景": _text(element.get("dfx_scenarios")), "前置状态/权限": _text(element.get("precondition")),
            "预期行为": _text(element.get("expected_behavior")), "业务依据/规则来源": _text(element.get("rule_source")),
            "覆盖用例 ID": "；".join(covered_case_ids),
            "覆盖状态": "已覆盖" if covered_case_ids else "未覆盖",
            "发现方式": "页面实探",
            "素材来源": "", "待确认问题/备注": _text(element.get("notes")),
        })
    return rows


def assemble_formal_workbook(run_dir: Path, template: Path, output: Path) -> dict[str, int]:
    review_path = artifact_paths(run_dir)["review"]
    if not review_path.is_file():
        raise ValueError("review.json does not exist; run the single review first")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if review.get("status") not in {"ready", "ready_with_notes"}:
        raise ValueError(f"delivery requires a local repair recorded by review: {review.get('status')}")
    current_sources = {
        name: artifact_digest(artifact_paths(run_dir)[name])
        for name in ("facts", "plan", "cases")
    }
    if review.get("sources") != current_sources:
        raise ValueError("review.json is stale; run the single review once for the current artifacts")
    if not template.is_file():
        raise ValueError(f"formal workbook template not found: {template}")
    rows_by_sheet = build_sheet_rows(run_dir)
    if not rows_by_sheet[FUNCTION_SHEET]:
        raise ValueError("function-cases.json contains no cases")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_sibling(output)
    shutil.copy2(template, temporary)
    workbook = load_workbook(temporary)
    if workbook.sheetnames != SHEETS:
        raise ValueError(f"formal template must contain exactly the 8 standard sheets: {SHEETS}")
    structure = _workbook_structure(workbook)
    counts: dict[str, int] = {}
    for sheet_name in SHEETS:
        worksheet = workbook[sheet_name]
        headers = header_map(worksheet)
        formulas = _sample_formulas(worksheet)
        clear_data_rows(worksheet)
        if not rows_by_sheet[sheet_name] and worksheet.max_row >= 2:
            worksheet.delete_rows(2, worksheet.max_row - 1)
        for row_index, row in enumerate(rows_by_sheet[sheet_name], 2):
            unknown = set(row) - set(headers)
            if unknown:
                raise ValueError(f"{sheet_name} row uses unknown headers: {sorted(unknown)}")
            write_mapped_row(worksheet, headers, row_index, row)
            _apply_sample_formulas(worksheet, formulas, row_index)
            for header in MULTILINE_HEADERS:
                if header in headers:
                    cell = worksheet.cell(row_index, headers[header])
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
            multiline_values = [row.get(header, "") for header in MULTILINE_HEADERS if header in headers]
            worksheet.row_dimensions[row_index].height = _row_height(multiline_values)
        counts[sheet_name] = len(rows_by_sheet[sheet_name])
    resize_workbook_structures(workbook)
    _assert_structure_preserved(structure, workbook, "formal workbook")
    atomic_save_workbook(workbook, temporary)
    os.replace(temporary, output)
    return counts


def generate_import_workbook(run_dir: Path, template: Path, output: Path, module_path: str) -> int:
    """Generate the import workbook directly from function-cases.json."""
    if not template.is_file():
        raise ValueError("import template must exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_sibling(output)
    shutil.copy2(template, temporary)
    cases = load_cases(run_dir).get("cases", [])
    target_book = load_workbook(temporary)
    structure = _workbook_structure(target_book)
    target = target_book[target_book.sheetnames[0]]
    target_headers = header_map(target)
    formulas = _sample_formulas(target)
    clear_data_rows(target)
    modules = [part.strip() for part in re_split_module(module_path)][:5]
    modules += [""] * (5 - len(modules))
    count = 0
    for case in cases:
        case_id = str(case.get("case_id", "")).strip()
        title = str(case.get("title", "")).strip()
        if not str(case_id or "").strip() or not str(title or "").strip():
            raise ValueError("function-cases.json contains an empty case ID or title")
        count += 1
        actions, expected = _paired_columns(case.get("steps"))
        row = {
            "一级模块名称": modules[0], "二级模块名称": modules[1], "三级模块名称": modules[2],
            "四级模块名称": modules[3], "五级模块名称": modules[4], "测试用例序号": count,
            "测试用例名称": title,
            "测试步骤描述": actions,
            "测试步骤预期结果": expected,
            "测试类型": case.get("test_type") or "功能测试",
            "测试用例级别": case.get("priority") or "P1",
            "执行方式": "手工",
            "测试用例说明": title.split("-", 1)[0],
            "前置条件": _numbered(case.get("preconditions")),
            "标签": "；".join(filter(None, [
                str(case.get("dfx_dimension") or ""),
                str(case.get("dfx_scenario") or ""),
            ])),
            "备注": _text(case.get("notes")),
        }
        write_mapped_row(target, target_headers, count + 1, row)
        _apply_sample_formulas(target, formulas, count + 1)
        for header in ("测试步骤描述", "测试步骤预期结果", "测试用例说明", "前置条件", "备注"):
            if header in target_headers:
                target.cell(count + 1, target_headers[header]).alignment = Alignment(vertical="top", wrap_text=True)
        target.row_dimensions[count + 1].height = _row_height([
            row.get("测试步骤描述"), row.get("测试步骤预期结果"), row.get("前置条件"), row.get("测试用例说明"),
        ])
    if count == 0:
        raise ValueError("no function cases were available for the import workbook")
    resize_workbook_structures(target_book)
    _assert_structure_preserved(structure, target_book, "import workbook")
    atomic_save_workbook(target_book, temporary)
    os.replace(temporary, output)
    return count


def re_split_module(module_path: str) -> list[str]:
    import re
    return [value for value in re.split(r"\s*(?:>|/|\\|→)\s*", module_path) if value]


def _verify_generated_deliverables(
    run_dir: Path,
    formal_path: Path,
    import_path: Path,
    expected_count: int,
    formal_template_path: Path,
    import_template_path: Path,
) -> None:
    formal = load_workbook(formal_path, data_only=False)
    formal_template = load_workbook(formal_template_path, data_only=False)
    _assert_structure_preserved(_workbook_structure(formal_template), formal, "saved formal workbook")
    if formal.sheetnames != SHEETS:
        raise ValueError("generated formal workbook does not retain the exact 8-sheet contract")
    function_ws = formal[FUNCTION_SHEET]
    function_headers = header_map(function_ws)
    formal_rows = [
        row for row in range(2, function_ws.max_row + 1)
        if any(function_ws.cell(row, column).value not in (None, "") for column in range(1, function_ws.max_column + 1))
    ]
    if formal_rows != list(range(2, 2 + expected_count)):
        raise ValueError("generated formal workbook has a missing, blank, or extra function-case row")
    imported = load_workbook(import_path, data_only=False)
    import_template = load_workbook(import_template_path, data_only=False)
    _assert_structure_preserved(_workbook_structure(import_template), imported, "saved import workbook")
    import_ws = imported[imported.sheetnames[0]]
    import_headers = header_map(import_ws)
    import_rows = [
        row for row in range(2, import_ws.max_row + 1)
        if any(import_ws.cell(row, column).value not in (None, "") for column in range(1, import_ws.max_column + 1))
    ]
    if import_rows != list(range(2, 2 + expected_count)):
        raise ValueError("generated import workbook has a missing, blank, or extra case row")
    for name in SHEETS:
        generated_rows = [
            row for row in range(2, formal[name].max_row + 1)
            if any(formal[name].cell(row, column).value not in (None, "") for column in range(1, formal[name].max_column + 1))
        ]
        formulas = _sample_formulas(formal_template[name])
        for row in generated_rows:
            for column in formulas:
                value = formal[name].cell(row, column).value
                if not isinstance(value, str) or not value.startswith("=") or "#REF!" in value:
                    raise ValueError(f"generated formal workbook lost a template formula at {name}!{row},{column}")
    import_formulas = _sample_formulas(import_template[import_template.sheetnames[0]])
    for row in import_rows:
        for column in import_formulas:
            value = import_ws.cell(row, column).value
            if not isinstance(value, str) or not value.startswith("=") or "#REF!" in value:
                raise ValueError(f"generated import workbook lost a template formula at row {row}, column {column}")
    source_cases = load_cases(run_dir).get("cases", [])
    if len(source_cases) != expected_count:
        raise ValueError("generated workbook count differs from function-cases.json")
    mappings = {"用例标题": "测试用例名称", "操作步骤": "测试步骤描述", "预期结果": "测试步骤预期结果"}
    for formal_row, import_row in zip(formal_rows, import_rows):
        for formal_header, import_header in mappings.items():
            formal_value = str(function_ws.cell(formal_row, function_headers[formal_header]).value or "").strip()
            import_value = str(import_ws.cell(import_row, import_headers[import_header]).value or "").strip()
            if formal_value != import_value:
                raise ValueError(f"generated import {import_header} does not match formal case row {formal_row}")
    for index, case in enumerate(source_cases, 2):
        if str(function_ws.cell(index, function_headers["用例标题"]).value or "").strip() != str(case.get("title", "")).strip():
            raise ValueError(f"formal workbook row {index} differs from function-cases.json")


def complete_deliverables(run_dir: Path, project_root: Path) -> dict[str, Any]:
    paths = artifact_paths(run_dir)
    scope = load_facts(run_dir).get("scope", {})
    paths["delivery"].mkdir(parents=True, exist_ok=True)
    formal = paths["delivery"] / "正式测试设计.xlsx"
    import_file = paths["delivery"] / "测试系统导入.xlsx"
    formal_candidate = paths["delivery"] / ".正式测试设计.candidate.xlsx"
    import_candidate = paths["delivery"] / ".测试系统导入.candidate.xlsx"
    try:
        formal_template = project_root / "docs" / "test-design" / "codebuddy-test-design-template.xlsx"
        import_template = project_root / "docs" / "test-design" / "测试用例模板.xlsx"
        counts = assemble_formal_workbook(
            run_dir,
            formal_template,
            formal_candidate,
        )
        import_count = generate_import_workbook(
            run_dir,
            import_template,
            import_candidate,
            str(scope.get("module_path", "")),
        )
        _verify_generated_deliverables(
            run_dir, formal_candidate, import_candidate, import_count, formal_template, import_template,
        )
        os.replace(formal_candidate, formal)
        os.replace(import_candidate, import_file)
    finally:
        formal_candidate.unlink(missing_ok=True)
        import_candidate.unlink(missing_ok=True)
    return {
        "formal_workbook": formal.name,
        "import_workbook": import_file.name,
        "sheet_rows": counts,
        "import_cases": import_count,
    }
