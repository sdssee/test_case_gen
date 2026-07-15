# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Alignment

from .excel_utils import clear_data_rows, header_map, remove_workbook_tables_and_refresh_filters, write_mapped_row
from .io_utils import atomic_save_workbook, temporary_sibling
from .session_runtime import artifact_paths, load_cases, load_facts, load_plan, review_run


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


def _function_names(facts: dict[str, Any], plan: dict[str, Any]) -> dict[str, str]:
    result = {
        str(row.get("function_id") or row.get("fact_id")): str(row.get("name", ""))
        for row in facts.get("functions", [])
    }
    for row in plan.get("functions", []):
        if row.get("function_id") and row.get("name"):
            result[str(row["function_id"])] = str(row["name"])
    return result


def build_sheet_rows(run_dir: Path) -> dict[str, list[dict[str, str]]]:
    facts = load_facts(run_dir)
    plan = load_plan(run_dir)
    case_document = load_cases(run_dir)
    scope = facts.get("scope", {})
    functions = _function_names(facts, plan)
    pending = facts.get("pending", [])
    risks = facts.get("risks", []) + plan.get("risks", [])
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
        "准出条件": "事实、计划、用例双向追溯通过且交付文件校验通过",
        "待确认问题": "；".join(_text(row.get("description") or row.get("reason")) for row in pending),
    })
    for index, requirement in enumerate(facts.get("requirements", []), 1):
        rows["需求用户故事拆解"].append({
            "Story ID/需求 ID": _text(requirement.get("requirement_id") or requirement.get("fact_id") or f"REQ-{index:03d}"),
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
                "测试对象/页面元素": _text(case.get("fact_ids")),
                "输入数据/状态条件": _text(case.get("test_data")),
                "观察点": _text(case.get("observation")),
                "风险等级": _text(case.get("risk_level") or "中"),
                "优先级": _text(case.get("priority") or "P1"),
                "是否生成用例": "是",
                "备注": _text(case.get("notes")),
            })
    for case in case_document.get("cases", []):
        function_id = str(case.get("function_id", ""))
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
            "操作步骤": _numbered(case.get("steps")),
            "预期结果": _numbered(case.get("expected_results")),
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
            "编号": _text(risk.get("risk_id") or risk.get("fact_id") or f"RISK-{index:03d}"),
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
        for fact_id in case.get("fact_ids", []):
            case_by_fact.setdefault(str(fact_id), []).append(str(case.get("case_id")))
    for element in facts.get("elements", []):
        element_id = str(element.get("element_id") or element.get("fact_id"))
        rows["页面元素覆盖清单"].append({
            "元素 ID": element_id, "Story ID/需求 ID": _text(element.get("requirement_id")),
            "页面/入口": _text(element.get("page_name") or element.get("page_id")),
            "页面 URL/菜单路径": _text(element.get("menu_path")),
            "元素名称/文案": _text(element.get("name")), "元素类型": _text(element.get("type")),
            "交互方式": _text(element.get("interaction")), "适用DFX维度": _text(element.get("dfx_dimensions")),
            "适用DFX场景": _text(element.get("dfx_scenarios")), "前置状态/权限": _text(element.get("precondition")),
            "预期行为": _text(element.get("expected_behavior")), "业务依据/规则来源": _text(element.get("rule_source")),
            "覆盖用例 ID": "；".join(case_by_fact.get(str(element.get("fact_id")), []) + case_by_fact.get(element_id, [])),
            "覆盖状态": "已覆盖" if case_by_fact.get(str(element.get("fact_id"))) or case_by_fact.get(element_id) else "未覆盖",
            "发现方式": _text(element.get("discovery_method") or "DOM/可访问性树/页面状态"),
            "素材来源": _text(element.get("evidence")), "待确认问题/备注": _text(element.get("notes")),
        })
    return rows


def assemble_formal_workbook(run_dir: Path, template: Path, output: Path) -> dict[str, int]:
    review = review_run(run_dir)
    if review["status"] != "passed":
        raise ValueError("review failed; formal delivery is not allowed: " + " | ".join(review["errors"][:10]))
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
    counts: dict[str, int] = {}
    for sheet_name in SHEETS:
        worksheet = workbook[sheet_name]
        headers = header_map(worksheet)
        clear_data_rows(worksheet)
        if not rows_by_sheet[sheet_name] and worksheet.max_row >= 2:
            worksheet.delete_rows(2, worksheet.max_row - 1)
        for row_index, row in enumerate(rows_by_sheet[sheet_name], 2):
            unknown = set(row) - set(headers)
            if unknown:
                raise ValueError(f"{sheet_name} row uses unknown headers: {sorted(unknown)}")
            write_mapped_row(worksheet, headers, row_index, row)
            for header in MULTILINE_HEADERS:
                if header in headers:
                    cell = worksheet.cell(row_index, headers[header])
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
            worksheet.row_dimensions[row_index].height = max(worksheet.row_dimensions[row_index].height or 18, 36)
        counts[sheet_name] = len(rows_by_sheet[sheet_name])
    remove_workbook_tables_and_refresh_filters(workbook)
    atomic_save_workbook(workbook, temporary)
    os.replace(temporary, output)
    return counts


def generate_import_workbook(formal_workbook: Path, template: Path, output: Path, module_path: str) -> int:
    if not formal_workbook.is_file() or not template.is_file():
        raise ValueError("formal workbook and import template must exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_sibling(output)
    shutil.copy2(template, temporary)
    formal = load_workbook(formal_workbook, data_only=False)
    source = formal[FUNCTION_SHEET]
    source_headers = header_map(source)
    target_book = load_workbook(temporary)
    target = target_book[target_book.sheetnames[0]]
    target_headers = header_map(target)
    clear_data_rows(target)
    modules = [part.strip() for part in re_split_module(module_path)][:5]
    modules += [""] * (5 - len(modules))
    count = 0
    for source_row in range(2, source.max_row + 1):
        case_id = source.cell(source_row, source_headers["用例 ID"]).value
        title = source.cell(source_row, source_headers["用例标题"]).value
        if not str(case_id or "").strip() or not str(title or "").strip():
            continue
        count += 1
        row = {
            "一级模块名称": modules[0], "二级模块名称": modules[1], "三级模块名称": modules[2],
            "四级模块名称": modules[3], "五级模块名称": modules[4], "测试用例序号": count,
            "测试用例名称": title,
            "测试步骤描述": source.cell(source_row, source_headers["操作步骤"]).value,
            "测试步骤预期结果": source.cell(source_row, source_headers["预期结果"]).value,
            "测试类型": source.cell(source_row, source_headers["测试类型"]).value,
            "测试用例级别": source.cell(source_row, source_headers["优先级"]).value,
            "执行方式": "手工",
            "测试用例说明": source.cell(source_row, source_headers["功能点"]).value,
            "前置条件": source.cell(source_row, source_headers["前置条件"]).value,
            "标签": "；".join(filter(None, [
                str(source.cell(source_row, source_headers["DFX维度"]).value or ""),
                str(source.cell(source_row, source_headers["DFX场景"]).value or ""),
            ])),
            "备注": source.cell(source_row, source_headers["备注"]).value,
        }
        write_mapped_row(target, target_headers, count + 1, row)
        for header in ("测试步骤描述", "测试步骤预期结果", "测试用例说明", "前置条件", "备注"):
            if header in target_headers:
                target.cell(count + 1, target_headers[header]).alignment = Alignment(vertical="top", wrap_text=True)
        target.row_dimensions[count + 1].height = 60
    if count == 0:
        raise ValueError("no function cases were available for the import workbook")
    remove_workbook_tables_and_refresh_filters(target_book)
    atomic_save_workbook(target_book, temporary)
    os.replace(temporary, output)
    return count


def re_split_module(module_path: str) -> list[str]:
    import re
    return [value for value in re.split(r"\s*(?:>|/|\\|→)\s*", module_path) if value]


def _verify_generated_deliverables(formal_path: Path, import_path: Path, expected_count: int) -> None:
    formal = load_workbook(formal_path, data_only=False)
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
    import_ws = imported[imported.sheetnames[0]]
    import_headers = header_map(import_ws)
    import_rows = [
        row for row in range(2, import_ws.max_row + 1)
        if any(import_ws.cell(row, column).value not in (None, "") for column in range(1, import_ws.max_column + 1))
    ]
    if import_rows != list(range(2, 2 + expected_count)):
        raise ValueError("generated import workbook has a missing, blank, or extra case row")
    mappings = {"用例标题": "测试用例名称", "操作步骤": "测试步骤描述", "预期结果": "测试步骤预期结果"}
    for formal_row, import_row in zip(formal_rows, import_rows):
        for formal_header, import_header in mappings.items():
            formal_value = str(function_ws.cell(formal_row, function_headers[formal_header]).value or "").strip()
            import_value = str(import_ws.cell(import_row, import_headers[import_header]).value or "").strip()
            if formal_value != import_value:
                raise ValueError(f"generated import {import_header} does not match formal case row {formal_row}")


def complete_deliverables(run_dir: Path, project_root: Path) -> dict[str, Any]:
    paths = artifact_paths(run_dir)
    scope = json.loads(paths["scope"].read_text(encoding="utf-8"))
    safe_name = "-".join(part for part in re_split_module(str(scope.get("module_path", "模块"))) if part)
    safe_name = "".join(char if char not in '<>:"/\\|?*' else "_" for char in safe_name) or "测试设计"
    formal = paths["delivery"] / f"{safe_name}-测试设计.xlsx"
    import_file = paths["delivery"] / f"{safe_name}-测试系统导入.xlsx"
    counts = assemble_formal_workbook(
        run_dir,
        project_root / "docs" / "test-design" / "codebuddy-test-design-template.xlsx",
        formal,
    )
    import_count = generate_import_workbook(
        formal,
        project_root / "docs" / "test-design" / "测试用例模板.xlsx",
        import_file,
        str(scope.get("module_path", "")),
    )
    _verify_generated_deliverables(formal, import_file, import_count)
    receipt = {
        "schema_version": "1.0", "delivered_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "formal_workbook": formal.name, "import_workbook": import_file.name,
        "sheet_rows": counts, "import_cases": import_count,
    }
    paths["delivery"].mkdir(parents=True, exist_ok=True)
    (paths["delivery"] / "delivery-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt
