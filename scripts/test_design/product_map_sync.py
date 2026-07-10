# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from .batch import contains_any, split_module_parts, split_plan_values
from .excel_utils import FORMAL_FUNCTION_SHEET, header_map, non_empty_rows
from .fact_store import (
    PRODUCT_MAP_SHEETS,
    catalog_dir,
    module_document_name,
    project_catalog_to_workbook,
    save_module_document,
    validate_catalog,
)
from .io_utils import rollback_files_on_error
from .paths import safe_filename


def sync_product_map(
    product_map: Path,
    formal_workbook: Path,
    page_discovery: Path,
    module_path: str,
    archive_path: str,
    product_name: str | None = None,
) -> None:
    today = date.today().isoformat()
    product, modules = split_module_parts(module_path, product_name)
    module_label = ">".join(modules) or module_path
    level1 = modules[0] if len(modules) > 0 else ""
    level2 = modules[1] if len(modules) > 1 else ""
    level3 = modules[2] if len(modules) > 2 else ""

    formal_wb = load_workbook(formal_workbook, data_only=True)
    function_ws = formal_wb[FORMAL_FUNCTION_SHEET]
    function_rows = non_empty_rows(function_ws, header_map(function_ws))
    requirement_rows = non_empty_rows(
        formal_wb["需求用户故事拆解"], header_map(formal_wb["需求用户故事拆解"])
    )
    performance_rows = non_empty_rows(
        formal_wb["性能测试设计"], header_map(formal_wb["性能测试设计"])
    )

    with page_discovery.open("r", encoding="utf-8-sig", newline="") as fp:
        discovery_rows = [row for row in csv.DictReader(fp) if any((value or "").strip() for value in row.values())]

    facts_by_sheet: dict[str, list[dict[str, str]]] = {sheet_name: [] for sheet_name in PRODUCT_MAP_SHEETS}

    def add_fact(sheet_name: str, values: dict[str, str]) -> None:
        facts_by_sheet[sheet_name].append(values)

    pages = sorted({row.get("页面/入口", "") for row in discovery_rows if row.get("页面/入口")})
    for page in pages or [level3 or level2 or level1 or module_label]:
        add_fact(
            "产品模块地图",
            {
                "产品/系统": product,
                "一级模块": level1,
                "二级模块": level2,
                "三级模块": level3,
                "页面/入口": page,
                "菜单路径/URL": module_label,
                "模块功能摘要": f"{module_label} 页面功能、页面元素和测试资产已同步",
                "归档测试设计路径": archive_path,
                "覆盖状态": "已覆盖",
                "最后更新时间": today,
                "待确认问题": "无",
            },
        )

    create_markers = ["新增", "创建", "添加", "新建"]
    state_change_markers = ["编辑", "修改", "删除", "停用", "启用", "状态"]
    create_ids = [
        row.get("用例 ID", "")
        for row in function_rows
        if row.get("用例 ID")
        and contains_any(f"{row.get('功能点', '')}{row.get('用例标题', '')}", create_markers)
    ]
    state_change_ids = [
        row.get("用例 ID", "")
        for row in function_rows
        if row.get("用例 ID")
        and contains_any(f"{row.get('功能点', '')}{row.get('用例标题', '')}", state_change_markers)
    ]
    linked_create_ids = ";".join(create_ids[:3])
    add_fact(
        "业务对象地图",
        {
            "产品/系统": product,
            "业务对象": f"{level3 or level2 or module_label}（待确认）",
            "来源模块": module_label,
            "消费模块": "待确认",
            "关键字段": "待确认",
            "关键状态": "待确认",
            "状态生产者": "待确认",
            "状态消费者": "待确认",
            "创建用例ID": linked_create_ids,
            "状态变更用例ID": ";".join(state_change_ids),
            "归档测试设计路径": archive_path,
            "待确认问题": "业务对象、关键字段和关键状态需以需求或页面证据复核",
        },
    )
    business_flows = sorted({row.get("业务链路", "") for row in performance_rows if row.get("业务链路", "")})
    if not business_flows:
        business_flows = [f"{module_label}业务链路（待确认）"]
    for flow_index, business_flow in enumerate(business_flows, start=1):
        add_fact(
            "业务链路地图",
            {
                "链路ID": f"FLOW-{safe_filename(module_label)}-{flow_index:03d}",
                "链路名称": business_flow,
                "起始模块": module_label,
                "结束模块": module_label,
                "业务对象": level3 or level2 or module_label,
                "关键状态流转": "待从业务链路证据补充",
                "主流程用例ID": linked_create_ids,
                "依赖测试数据": "按正式测试设计中的测试数据准备",
                "风险点": "关键状态流转待确认",
                "归档测试设计路径": archive_path,
            },
        )

    for row in discovery_rows:
        add_fact(
            "页面元素地图",
            {
                "产品/系统": product,
                "模块": module_label,
                "页面/入口": row.get("页面/入口", ""),
                "菜单路径/URL": row.get("菜单路径/URL", "") or module_label,
                "元素名称/文案": row.get("元素名称/文案", ""),
                "元素类型": row.get("元素类型", ""),
                "交互方式": row.get("交互方式", ""),
                "前置状态/权限": row.get("角色/权限", ""),
                "关联用例ID": row.get("关联用例ID", ""),
                "覆盖状态": row.get("覆盖状态", ""),
                "发现来源": row.get("发现方式", ""),
                "最后更新时间": today,
                "备注": row.get("备注", ""),
            },
        )

    for row in function_rows:
        case_id = row.get("用例 ID", "")
        if not case_id:
            continue
        add_fact(
            "用例资产索引",
            {
                "产品/系统": product,
                "模块": module_label,
                "功能点": row.get("功能点", ""),
                "用例ID": case_id,
                "用例标题": row.get("用例标题", ""),
                "测试类型": row.get("测试类型", "功能测试") or "功能测试",
                "执行方式": "手动",
                "是否可复用为前置条件": "否",
                "是否跨模块": "否",
                "关联业务对象": "",
                "关联业务链路": business_flows[0] if business_flows else "",
                "归档测试设计路径": archive_path,
                "最后更新时间": today,
            },
        )

    for function_point in sorted({row.get("功能点", "") for row in function_rows if row.get("功能点")}):
        ids = ";".join(row.get("用例 ID", "") for row in function_rows if row.get("功能点") == function_point and row.get("用例 ID"))
        scenarios = ";".join(
            sorted(
                {
                    row.get("DFX场景", "")
                    for row in function_rows
                    if row.get("功能点") == function_point and row.get("DFX场景", "")
                }
            )
        )
        add_fact(
            "模块能力索引",
            {
                "产品/系统": product,
                "模块": module_label,
                "功能点": function_point,
                "能力/数据对象": level3 or level2 or module_label,
                "能力描述": f"{function_point} 已形成测试资产",
                "关键状态": scenarios or "待确认",
                "可复用前置条件": "按归档测试设计准备测试数据",
                "关联用例ID": ids,
                "归档测试设计路径": archive_path,
                "限制/待确认问题": "无",
                "最后更新时间": today,
            },
        )

    dependency_values: set[str] = set()
    for row in requirement_rows:
        dependency_values.update(split_plan_values(row.get("依赖系统", "")))
    for dependency in sorted(dependency_values):
        add_fact(
            "跨模块依赖关系",
            {
                "产品/系统": product,
                "当前模块": module_label,
                "依赖模块": dependency,
                "依赖业务对象": "待确认",
                "依赖功能点/能力": "由需求用户故事拆解中的依赖系统字段识别",
                "依赖类型": "系统依赖",
                "引用用例ID": "",
                "当前模块用例ID": linked_create_ids,
                "使用方式": "测试数据、权限或业务链路准备",
                "风险/待确认问题": "依赖能力、状态和失败降级路径需确认",
                "最后更新时间": today,
            },
        )
    if not dependency_values:
        add_fact(
            "跨模块依赖关系",
            {
                "产品/系统": product,
                "当前模块": module_label,
                "依赖模块": "待确认",
                "依赖业务对象": "待确认",
                "依赖功能点/能力": "正式测试设计未提供明确依赖系统证据",
                "依赖类型": "待确认",
                "引用用例ID": "",
                "当前模块用例ID": linked_create_ids,
                "使用方式": "补充需求或页面证据后更新",
                "风险/待确认问题": "需确认是否不存在跨模块依赖，或当前资料尚未覆盖",
                "最后更新时间": today,
            },
        )

    test_data_cases: dict[str, list[str]] = {}
    for row in function_rows:
        test_data = row.get("测试数据", "").strip()
        case_id = row.get("用例 ID", "").strip()
        if test_data:
            test_data_cases.setdefault(test_data, [])
            if case_id:
                test_data_cases[test_data].append(case_id)
    for data_index, (test_data, case_ids) in enumerate(sorted(test_data_cases.items()), start=1):
        add_fact(
            "可复用测试数据",
            {
                "产品/系统": product,
                "模块": module_label,
                "数据对象": level3 or level2 or module_label,
                "测试数据标识": f"DATA-{safe_filename(module_label)}-{data_index:03d}",
                "数据用途": test_data,
                "可执行敏感操作": "仅限本次创建且带测试标识的数据",
                "创建/维护方式": "按正式测试设计的前置条件和测试数据构造",
                "关联用例ID": ";".join(dict.fromkeys(case_ids)),
                "清理策略": "交付后按环境规则清理本次测试数据",
                "敏感信息处理": "仅使用占位符，不保存真实凭据",
                "最后更新时间": today,
            },
        )
    if not test_data_cases:
        add_fact(
            "可复用测试数据",
            {
                "产品/系统": product,
                "模块": module_label,
                "数据对象": level3 or level2 or module_label,
                "测试数据标识": "待补充",
                "数据用途": "正式测试设计未提供可复用测试数据证据",
                "可执行敏感操作": "否",
                "创建/维护方式": "补充测试数据生命周期证据后更新",
                "关联用例ID": "",
                "清理策略": "不适用",
                "敏感信息处理": "不得写入真实凭据",
                "最后更新时间": today,
            },
        )
    add_fact(
        "变更影响分析",
        {
            "产品/系统": product,
            "变更模块": module_label,
            "变更ID": f"CHANGE-{date.today().strftime('%Y%m%d')}-{safe_filename(module_label)}",
            "需求/任务": f"新增或更新 {module_label} 测试设计",
            "影响模块": module_label,
            "影响业务对象": level3 or level2 or module_label,
            "影响业务链路": ";".join(business_flows),
            "需复核历史用例ID": "",
            "需新增/修改用例": ";".join(row.get("用例 ID", "") for row in function_rows if row.get("用例 ID")),
            "风险等级": "中",
            "处理状态": "已同步",
            "分析日期": today,
            "备注": "影响范围依据本次正式测试设计和页面实探资产生成",
        },
    )
    add_fact(
        "变更记录",
        {
            "版本": date.today().strftime("%Y%m%d"),
            "日期": today,
            "变更人/来源": "AI测试设计流水线",
            "变更类型": "测试资产同步",
            "变更内容": f"同步 {module_label} 测试设计、页面元素和用例资产",
            "影响模块": module_label,
            "是否已同步产品版图": "是",
            "备注": f"归档路径：{archive_path}",
        },
    )
    module_key = module_label if module_label == product or module_label.startswith(f"{product}>") else f"{product}>{module_label}"
    catalog = catalog_dir(product_map)
    document_path = catalog / "modules" / module_document_name(module_key)
    catalog_paths = [
        product_map,
        catalog / "migration.json",
        catalog / "index.json",
        catalog / "modules" / "_legacy.json",
        document_path,
    ]
    with rollback_files_on_error(catalog_paths):
        save_module_document(
            product_map,
            module_key,
            product,
            module_label,
            archive_path,
            facts_by_sheet,
            {
                "type": "formal-design-and-page-discovery",
                "source": archive_path,
                "page_discovery": page_discovery.name,
                "generated_on": today,
            },
        )
        project_catalog_to_workbook(product_map)
        validate_catalog(product_map)
