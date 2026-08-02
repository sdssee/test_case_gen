# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

EXPECTED_SHEETS = [
    "测试设计总览",
    "需求用户故事拆解",
    "测试场景矩阵",
    "功能测试用例",
    "性能测试设计",
    "风险与待确认问题",
    "页面元素覆盖清单",
]

IMPORT_HEADERS = [
    "一级模块系统编号",
    "一级模块名称",
    "二级模块系统编号",
    "二级模块名称",
    "三级模块系统编号",
    "三级模块名称",
    "四级模块系统编号",
    "四级模块名称",
    "五级模块系统编号",
    "五级模块名称",
    "其他模块系统编号",
    "其他模块名称",
    "测试用例系统编号",
    "测试用例序号",
    "测试用例名称",
    "测试步骤描述",
    "测试步骤预期结果",
    "测试类型",
    "测试用例级别",
    "执行方式",
    "测试用例说明",
    "前置条件",
    "维护人",
    "标签",
    "备注",
    "作者",
]

REQUIRED_FILES = [
    "AGENTS.md",
    "CODEBUDDY.md",
    "README.md",
    "README_IMPORT.md",
    "deliverables/.gitkeep",
    ".codebuddy/skills/test-design/SKILL.md",
    ".codebuddy/.rules/test-design-rule.mdc",
    ".codebuddy/rules/test-design-rule.md",
    "docs/ARCHITECTURE.md",
    "docs/RULE_OWNERSHIP.md",
    "docs/test-design/excel-template-spec.md",
    "docs/test-design/rules/README.md",
    "docs/test-design/rules/case-design.md",
    "docs/test-design/rules/page-discovery.md",
    "docs/test-design/rules/pagination.md",
    "docs/test-design/rules/batch-run.md",
    "docs/test-design/rules/excel-deliverable.md",
    "docs/test-design/rules/import-template.md",
    "docs/test-design/rules/data-safety.md",
    "docs/test-design/rules/dfx-test-strategy.md",
    "scripts/test_design_excel_tools.py",
    "scripts/validate-test-design-deliverable.py",
]


def fail(message: str) -> None:
    raise AssertionError(message)


def workbook_sheets(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("xl/workbook.xml"))
    return [node.attrib["name"] for node in root.findall("x:sheets/x:sheet", NS)]


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.text or "" for node in item.findall(".//x:t", NS)) for item in root.findall("x:si", NS)]


def cell_text(cell: ET.Element, shared: list[str]) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", NS))
    value = cell.find("x:v", NS)
    if value is None or value.text is None:
        return ""
    if cell.attrib.get("t") == "s":
        return shared[int(value.text)]
    return value.text


def first_row_values(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        shared = shared_strings(zf)
        root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    row = root.find(".//x:sheetData/x:row[@r='1']", NS)
    if row is None:
        fail(f"{path} 第一张 Sheet 缺少表头行")
    return [cell_text(cell, shared) for cell in row.findall("x:c", NS)]


def validate_no_excel_tables(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        stale = [name for name in zf.namelist() if name.startswith("xl/tables/")]
        if stale:
            fail(f"{path} 不应包含 Excel Table 部件: {stale}")


def validate_import_left_alignment(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        styles = ET.fromstring(zf.read("xl/styles.xml"))
        cell_xfs = styles.find("x:cellXfs", NS)
        if cell_xfs is None:
            fail("导入模板缺少单元格样式")
        left_style_ids = {
            index
            for index, xf in enumerate(cell_xfs.findall("x:xf", NS))
            if (alignment := xf.find("x:alignment", NS)) is not None
            and alignment.attrib.get("horizontal") == "left"
        }
        sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
    cells = sheet.findall(".//x:sheetData/x:row[@r='2']/x:c", NS)
    wrong = [cell.attrib.get("r", "") for cell in cells if int(cell.attrib.get("s", "0")) not in left_style_ids]
    if wrong:
        fail(f"测试用例模板.xlsx 第 2 行必须全部左对齐，异常单元格: {wrong[:10]}")


def assert_contains(path: Path, markers: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for marker in markers:
        if marker not in text:
            fail(f"{path} 缺少必要规则: {marker}")


def assert_not_contains(path: Path, markers: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for marker in markers:
        if marker in text:
            fail(f"{path} 仍包含已废弃或冲突规则: {marker}")


def validate_discovery_gate(root: Path) -> None:
    validator_path = root / "scripts" / "validate-test-design-deliverable.py"
    spec = importlib.util.spec_from_file_location("test_design_deliverable_validator", validator_path)
    if spec is None or spec.loader is None:
        fail("无法加载交付校验器进行深探门禁自检")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    full_findings = module.format_findings("汇总", {"问题": [f"问题{i}" for i in range(25)]})
    if "问题24" not in full_findings or "已省略" in full_findings:
        fail("阻塞问题汇总仍会截断，可能导致分轮修正")
    cross_stage_findings: dict[str, list[str]] = {}
    for category, message in [
        ("正式工作簿", "样式问题"),
        ("深探映射", "映射问题"),
        ("导入文件", "导入问题"),
    ]:
        module.collect_validation_issue(
            cross_stage_findings,
            category,
            lambda message=message: module.fail(message),
        )
    cross_stage_report = module.format_findings("联合质检", cross_stage_findings)
    if not all(marker in cross_stage_report for marker in ["样式问题", "映射问题", "导入问题"]):
        fail("跨阶段联合质检没有一次汇总全部阶段问题")
    original_validate_workbook = module.validate_workbook
    original_validate_discovery_state = module.validate_discovery_state
    original_validate_import_workbook = module.validate_import_workbook
    try:
        def fake_validate_workbook(_workbook, _formal_template, findings):
            module.add_finding(findings, "正式工作簿", "样式问题")
            return {"requires_discovery_state": True}

        def fake_validate_discovery_state(_path, _workbook_data):
            module.fail("映射问题")

        def fake_validate_import_workbook(_path, _workbook_data, findings):
            module.add_finding(findings, "导入文件", "导入问题")
            return 0

        module.validate_workbook = fake_validate_workbook
        module.validate_discovery_state = fake_validate_discovery_state
        module.validate_import_workbook = fake_validate_import_workbook
        module.validate_delivery_bundle(Path("formal.xlsx"), Path("import.xlsx"), Path("template.xlsx"), Path("state.json"))
    except AssertionError as exc:
        message = str(exc)
        required_sections = ["样式问题", "映射问题", "导入问题"]
        if not all(section in message for section in required_sections):
            fail("联合质检没有在一次执行中返回正式工作簿、深探和导入文件问题")
    else:
        fail("联合质检回归样本错误地通过")
    finally:
        module.validate_workbook = original_validate_workbook
        module.validate_discovery_state = original_validate_discovery_state
        module.validate_import_workbook = original_validate_import_workbook
    for risk_rows, overview_rows, label in [
        ([{"编号": "R1", "类型": "待确认", "状态": ""}], [], "空风险状态"),
        ([{"编号": "R1", "类型": "风险", "状态": "已关闭"}], [{"待确认问题": "是否允许重名"}], "总览待确认问题"),
        ([{"编号": "R1", "类型": "风险", "状态": "已确认", "描述": "原因未知", "建议处理方式": "需确认发布计划"}], [], "伪已确认风险"),
    ]:
        try:
            module.validate_evidence_status_consistency(risk_rows, [], overview_rows, [])
        except AssertionError:
            pass
        else:
            fail(f"{label}错误地通过风险门禁")
    if not all(module.is_page_discovery_source(value) for value in ["浏览器实探", "computer use", "代码/DOM"]):
        fail("页面实探发现方式没有稳定触发深探状态文件")
    if any(module.is_page_discovery_source(value) for value in ["需求文档", "截图", "原型", "用户说明", ""]):
        fail("非实探发现方式错误地触发深探状态文件")
    module.assert_complete_operation_steps(
        "1. 进入一级菜单-二级菜单-目标页面\n2. 确认目标输入框默认值\n3. 点击执行按钮",
        "普通功能用例",
    )
    redundant_login_steps = (
        "1. 打开浏览器访问 http://example.test/login\n"
        "2. 在登录页输入用户名 root 和密码，点击登录按钮\n"
        "3. 进入一级菜单-二级菜单-目标页面\n4. 点击执行按钮"
    )
    if not module.has_session_setup_step(redundant_login_steps):
        fail("普通功能用例中的浏览器和主动登录步骤没有被识别")
    if module.case_requires_session_setup({"功能点": "目标功能", "用例标题": "目标功能-默认值"}):
        fail("普通功能用例被错误识别为登录或URL直达测试")
    if not module.case_requires_session_setup({"功能点": "用户登录", "用例标题": "用户登录-正确密码登录"}):
        fail("登录功能用例没有获得入口步骤例外")
    if not module.case_requires_session_setup({"功能点": "登录", "用例标题": "登录-正确账号密码"}):
        fail("单独命名为登录的功能用例没有获得入口步骤例外")
    ordinary_case = {
        "用例 ID": "TC-ENTRY-001",
        "功能点": "目标功能",
        "用例标题": "目标功能-默认值",
        "前置条件": "1. 用户已登录并具备目标功能权限",
        "操作步骤": redundant_login_steps,
        "预期结果": "1. 浏览器成功打开\n2. 用户成功登录\n3. 进入目标页面\n4. 目标功能执行并展示结果",
        "DFX维度": "DFT功能",
        "DFX场景": "正向流程",
        "是否适合自动化": "是",
    }
    try:
        module.validate_function_case_preflight([ordinary_case])
    except AssertionError as exc:
        if "普通功能用例不得重复打开浏览器" not in str(exc):
            fail(f"普通功能用例入口回归返回了非预期问题：{exc}")
    else:
        fail("普通功能用例错误地保留了浏览器和登录步骤")
    module.assert_complete_operation_steps(redundant_login_steps, "登录功能用例", True)
    matched_pagination_capabilities = [
        name for name, pattern in module.PAGINATION_CAPABILITY_RULES
        if pattern.search("分页-末页页码")
    ]
    if matched_pagination_capabilities != ["页码"]:
        fail("末页页码被错误识别为独立末页按钮")
    if module.case_generalization_issues({"操作步骤": "1. 确认当前为10条/页"}):
        fail("页容量数值被错误识别为当前环境固定数量")
    if "当前环境固定数量" not in module.case_generalization_issues(
        {"前置条件": "1. 当前测试环境仅有7条数据"}
    ):
        fail("真实的当前环境固定数量没有被识别")
    if "当前环境固定数量" not in module.case_generalization_issues(
        {"前置条件": "1. 当前仅有7条数据"}
    ):
        fail("省略环境字样的当前固定数据量没有被识别")
    valid_story = {
        "Story ID/需求 ID": "STORY-001",
        "用户故事/需求描述": "管理业务对象",
        "角色": "业务管理员",
        "业务价值": "完成对象管理",
        "验收标准": "可按权限完成管理",
    }
    story_ids = module.validate_story_rows([valid_story])
    module.validate_story_traceability(
        story_ids,
        [{"Story ID/需求 ID": "STORY-001"}],
        [{"Story ID/需求 ID": "STORY-001"}],
    )
    invalid_stories = [
        valid_story,
        dict(valid_story, **{"用户故事/需求描述": "查看业务对象"}),
        dict(valid_story, **{"Story ID/需求 ID": "STORY-002"}),
        {"Story ID/需求 ID": "STORY-003", "角色": "业务管理员"},
    ]
    try:
        module.validate_story_rows(invalid_stories)
    except AssertionError as exc:
        message = str(exc)
        for marker in ["Story ID/需求 ID 重复", "用户故事/需求描述 不能为空", "业务价值 不能为空", "验收标准 不能为空", "内容精确重复"]:
            if marker not in message:
                fail(f"需求用户故事拆解门禁没有一次汇总必要问题：{marker}")
    else:
        fail("需求用户故事拆解门禁错误地放行了不稳定故事结构")
    try:
        module.validate_story_traceability(
            {"STORY-001", "STORY-002"},
            [{"Story ID/需求 ID": "STORY-UNKNOWN"}],
            [{"Story ID/需求 ID": ""}],
        )
    except AssertionError as exc:
        message = str(exc)
        for marker in ["缺少 Story ID/需求 ID", "引用了不存在的 Story", "没有关联测试场景"]:
            if marker not in message:
                fail(f"Story 追踪门禁没有一次汇总必要问题：{marker}")
    else:
        fail("Story 追踪门禁错误地放行了断链结构")
    valid_state = {
        "version": 1,
        "scope": "一级菜单-二级菜单-目标页面",
        "baseline_complete": True,
        "closure_rescan_complete": True,
        "closure_rescan_new_targets": 0,
        "understanding_questions": [],
        "targets": [
            {
                "id": "TARGET-001",
                "page": "目标页面",
                "element": "创建按钮",
                "kind": "交互",
                "control_type": "按钮",
                "status": "已验证",
                "action": "点击创建按钮",
                "evidence_source": "浏览器实探",
                "evidence": "创建区域出现",
                "observation": "页面展示创建区域",
                "result": "进入创建状态",
                "disposition": "场景",
                "reference_ids": ["SCN-001"],
            }
        ],
        "scenario_case_mapping": [{"scenario_id": "SCN-001", "case_ids": ["TC-001"]}],
    }
    workbook_data = {
        "scenario_ids": {"SCN-001"},
        "generated_scenario_ids": {"SCN-001"},
        "case_ids": {"TC-001"},
        "risk_ids": set(),
        "performance_ids": set(),
        "scenario_rows_by_id": {
            "SCN-001": {
                "场景 ID": "SCN-001",
                "功能点": "创建",
                "测试对象/页面元素": "创建按钮",
                "输入数据/状态条件": "正常数据",
                "观察点": "创建状态出现",
            }
        },
        "case_rows_by_id": {
            "TC-001": {
                "用例 ID": "TC-001",
                "功能点": "创建",
                "用例标题": "创建-正常创建",
                "测试数据": "正常数据",
                "操作步骤": "1. 登录系统\n2. 点击创建按钮",
                "预期结果": "1. 创建状态出现",
            }
        },
        "coverage_rows": [
            {"页面/入口": "目标页面", "元素名称/文案": "创建按钮", "发现方式": "浏览器实探"}
        ],
    }
    with tempfile.TemporaryDirectory(prefix="test-design-discovery-") as temporary_dir:
        state_path = Path(temporary_dir) / "discovery-state.json"
        state_path.write_text(json.dumps(valid_state, ensure_ascii=False), encoding="utf-8")
        module.validate_discovery_state(state_path, workbook_data)
        mismatched_mapping_data = deepcopy(workbook_data)
        mismatched_mapping_data["case_rows_by_id"]["TC-001"]["功能点"] = "搜索"
        try:
            module.validate_discovery_state(state_path, mismatched_mapping_data)
        except AssertionError as exc:
            if "与用例 TC-001 的功能点“搜索”不一致" not in str(exc):
                fail("场景用例映射门禁没有识别功能点不一致")
        else:
            fail("场景用例映射门禁错误地放行了不相关用例")
        invalid_state = dict(valid_state)
        invalid_state["understanding_questions"] = ["是否允许重名"]
        invalid_state["targets"] = [
            dict(
                valid_state["targets"][0],
                status="待执行",
                branch_policy="逐项验证",
                discovered_values=["10", "20"],
            )
        ]
        invalid_state["scenario_case_mapping"] = []
        state_path.write_text(json.dumps(invalid_state, ensure_ascii=False), encoding="utf-8")
        try:
            module.validate_discovery_state(state_path, workbook_data)
        except AssertionError as exc:
            message = str(exc)
            for marker in ["仍存在待用户理解确认问题", "状态必须为已验证", "仍缺少分支目标", "没有对应功能用例映射"]:
                if marker not in message:
                    fail(f"深探门禁没有一次汇总必要问题：{marker}")
        else:
            fail("深探门禁错误地放行了未收口状态")

        pagination_state = json.loads(json.dumps(valid_state, ensure_ascii=False))
        pagination_state["targets"][0].update(element="分页组件", control_type="分页控件")
        state_path.write_text(json.dumps(pagination_state, ensure_ascii=False), encoding="utf-8")
        try:
            module.validate_discovery_state(state_path)
        except AssertionError as exc:
            if "必须按页面实际能力拆分分页目标" not in str(exc):
                fail("分页门禁没有识别只登记笼统分页组件的问题")
        else:
            fail("分页门禁错误地放行了笼统分页目标")

        pagination_evidence_state = json.loads(json.dumps(valid_state, ensure_ascii=False))
        pagination_evidence_state["targets"][0]["result"] = "查询后分页重置"
        state_path.write_text(json.dumps(pagination_evidence_state, ensure_ascii=False), encoding="utf-8")
        try:
            module.validate_discovery_state(state_path)
        except AssertionError as exc:
            if "已出现分页语义，但没有建立具体分页目标" not in str(exc):
                fail("分页门禁没有识别仅出现在结果描述中的分页证据")
        else:
            fail("分页门禁错误地放行了未建分页目标的证据")

        merged_pagination_state = json.loads(json.dumps(valid_state, ensure_ascii=False))
        merged_pagination_state["targets"][0].update(element="上一页/下一页", control_type="分页按钮")
        state_path.write_text(json.dumps(merged_pagination_state, ensure_ascii=False), encoding="utf-8")
        try:
            module.validate_discovery_state(state_path)
        except AssertionError as exc:
            if "在一个目标中合并了多项分页能力" not in str(exc):
                fail("分页门禁没有识别合并登记的上一页和下一页")
        else:
            fail("分页门禁错误地放行了合并分页能力")

        page_size_state = json.loads(json.dumps(valid_state, ensure_ascii=False))
        page_size_state["targets"][0].update(element="每页条数", control_type="下拉框")
        state_path.write_text(json.dumps(page_size_state, ensure_ascii=False), encoding="utf-8")
        try:
            module.validate_discovery_state(state_path)
        except AssertionError as exc:
            if "必须记录 branch_policy=逐项验证 和全部 discovered_values" not in str(exc):
                fail("分页门禁没有识别页容量逐项验证缺失")
        else:
            fail("分页门禁错误地放行了未记录分支的页容量目标")

        merged_page_size_state = deepcopy(valid_state)
        merged_page_size_state["targets"][0].update(
            element="每页条数",
            control_type="下拉框",
            branch_policy="逐项验证",
            discovered_values=["10条/页", "20条/页"],
            state_before="默认页容量已显示",
            state_after="页容量选项展开",
            terminal_action="选择页容量",
            recovery="列表刷新后保持在目标页面",
        )
        for index, value in enumerate(["10条/页", "20条/页"], start=1):
            merged_page_size_state["targets"].append(
                {
                    "id": f"PAGE-SIZE-{index}",
                    "parent_id": "TARGET-001",
                    "branch_value": value,
                    "page": "目标页面",
                    "element": "容量选项",
                    "kind": "交互",
                    "control_type": "选项",
                    "status": "已验证",
                    "action": f"选择{value}",
                    "evidence_source": "浏览器实探",
                    "evidence": f"选择{value}后列表刷新",
                    "observation": f"{value}已生效",
                    "result": f"{value}已生效",
                    "disposition": "场景",
                    "reference_ids": ["SCN-001"],
                }
            )
        page_size_child_state = deepcopy(merged_page_size_state)
        for child in page_size_child_state["targets"][1:]:
            child["element"] = f"每页条数-{child['branch_value']}"
            child["observation"] = f"选择{child['branch_value']}后浮层收起"
            child["result"] = f"列表按{child['branch_value']}重新加载"
            child["branch_policy"] = "用例逐项覆盖"
            child["discovered_values"] = [child["branch_value"]]
        state_path.write_text(json.dumps(page_size_child_state, ensure_ascii=False), encoding="utf-8")
        module.validate_discovery_state(state_path)
        merged_page_size_data = deepcopy(workbook_data)
        merged_page_size_data["coverage_rows"] = [
            {"页面/入口": "目标页面", "元素名称/文案": "每页条数", "发现方式": "浏览器实探"},
            {"页面/入口": "目标页面", "元素名称/文案": "容量选项", "发现方式": "浏览器实探"},
        ]
        merged_page_size_data["scenario_rows_by_id"]["SCN-001"].update(
            **{
                "功能点": "分页",
                "测试对象/页面元素": "每页条数",
                "输入数据/状态条件": "10条/页、20条/页",
                "观察点": "10条/页、20条/页均可生效",
            }
        )
        merged_page_size_data["case_rows_by_id"]["TC-001"].update(
            **{
                "功能点": "分页",
                "用例标题": "分页-遍历页容量",
                "测试数据": "10条/页、20条/页",
                "操作步骤": "1. 进入页面\n2. 依次选择10条/页、20条/页",
                "预期结果": "1. 10条/页、20条/页均可生效",
            }
        )
        state_path.write_text(json.dumps(merged_page_size_state, ensure_ascii=False), encoding="utf-8")
        try:
            module.validate_discovery_state(state_path, merged_page_size_data)
        except AssertionError as exc:
            message = str(exc)
            if "每个页容量必须独立形成场景" not in message or "每个页容量必须独立形成用例" not in message:
                fail("分页门禁没有同时识别页容量场景和用例合并")
        else:
            fail("分页门禁错误地允许多个页容量合并成一个场景和用例")

        valid_pagination_state = json.loads(json.dumps(valid_state, ensure_ascii=False))
        valid_pagination_state["targets"][0].update(element="下一页", control_type="分页按钮")
        state_path.write_text(json.dumps(valid_pagination_state, ensure_ascii=False), encoding="utf-8")
        module.validate_discovery_state(state_path)

        shared_pagination_mapping_state = deepcopy(valid_state)
        shared_pagination_mapping_state["targets"][0].update(element="上一页", control_type="分页按钮")
        shared_pagination_mapping_state["targets"].append(
            {
                **deepcopy(valid_state["targets"][0]),
                "id": "TARGET-NEXT",
                "element": "下一页",
                "control_type": "分页按钮",
            }
        )
        shared_pagination_mapping_data = deepcopy(workbook_data)
        shared_pagination_mapping_data["scenario_rows_by_id"]["SCN-001"]["功能点"] = "分页"
        shared_pagination_mapping_data["case_rows_by_id"]["TC-001"]["功能点"] = "分页"
        shared_pagination_mapping_data["coverage_rows"] = [
            {"页面/入口": "目标页面", "元素名称/文案": "上一页", "发现方式": "浏览器实探"},
            {"页面/入口": "目标页面", "元素名称/文案": "下一页", "发现方式": "浏览器实探"},
        ]
        state_path.write_text(json.dumps(shared_pagination_mapping_state, ensure_ascii=False), encoding="utf-8")
        try:
            module.validate_discovery_state(state_path, shared_pagination_mapping_data)
        except AssertionError as exc:
            message = str(exc)
            if "不同分页动作必须独立形成场景" not in message or "不同分页动作必须独立形成用例" not in message:
                fail("分页门禁没有同时识别不同分页动作复用场景和用例")
        else:
            fail("分页门禁错误地允许上一页和下一页复用同一场景和用例")

        pagination_risk_state = json.loads(json.dumps(valid_state, ensure_ascii=False))
        pagination_risk_state["targets"][0].update(
            element="下一页",
            control_type="分页按钮",
            disposition="风险",
            reference_ids=["R-001"],
        )
        pagination_risk_workbook = dict(workbook_data)
        pagination_risk_workbook["risk_ids"] = {"R-001"}
        pagination_risk_workbook["coverage_rows"] = [
            {"页面/入口": "目标页面", "元素名称/文案": "下一页", "发现方式": "浏览器实探"}
        ]
        state_path.write_text(json.dumps(pagination_risk_state, ensure_ascii=False), encoding="utf-8")
        try:
            module.validate_discovery_state(state_path, pagination_risk_workbook)
        except AssertionError as exc:
            if "数据不足只能标记实探受限" not in str(exc):
                fail("分页门禁没有阻止以数据不足为由删除分页用例")
        else:
            fail("分页门禁错误地允许实际分页交互只进入风险")

        branch_state = json.loads(json.dumps(valid_state, ensure_ascii=False))
        branch_state["targets"][0].update(
            element="变量类型",
            control_type="下拉框",
            branch_policy="用例逐项覆盖",
            discovered_values=["String", "Number"],
            state_before="下拉框收起",
            state_after="下拉框展开并显示选项",
            terminal_action="选择选项后提交",
            recovery="提交后返回列表",
        )
        branch_workbook_data = dict(workbook_data)
        branch_workbook_data["coverage_rows"] = [
            {"页面/入口": "目标页面", "元素名称/文案": "变量类型", "发现方式": "浏览器实探"}
        ]
        branch_workbook_data["scenario_rows_by_id"] = {
            "SCN-001": {
                "场景 ID": "SCN-001",
                "测试对象/页面元素": "变量类型",
                "输入数据/状态条件": "分别选择 String、Number",
                "观察点": "各类型均可保存",
            }
        }
        branch_workbook_data["case_rows_by_id"] = {
            "TC-001": {
                "用例 ID": "TC-001",
                "用例标题": "创建-遍历变量类型",
                "测试数据": "String\nNumber",
                "操作步骤": "1. 登录系统\n2. 分别选择类型并点击确定按钮",
                "预期结果": "1. String 保存成功\n2. Number 保存成功",
            }
        }
        state_path.write_text(json.dumps(branch_state, ensure_ascii=False), encoding="utf-8")
        module.validate_discovery_state(state_path, branch_workbook_data)

        branch_workbook_data["scenario_rows_by_id"]["SCN-001"]["输入数据/状态条件"] = "选择 String"
        branch_workbook_data["scenario_rows_by_id"]["SCN-001"]["观察点"] = "String 可保存"
        try:
            module.validate_discovery_state(state_path, branch_workbook_data)
        except AssertionError as exc:
            if "分支值“Number”未写入该目标关联的测试场景" not in str(exc):
                fail("逐项用例覆盖门禁没有识别未进入场景的分支值")
        else:
            fail("逐项用例覆盖门禁错误地放行了场景分支遗漏")
        branch_workbook_data["scenario_rows_by_id"]["SCN-001"]["输入数据/状态条件"] = "分别选择 String、Number"
        branch_workbook_data["scenario_rows_by_id"]["SCN-001"]["观察点"] = "各类型均可保存"

        branch_workbook_data["case_rows_by_id"]["TC-001"].update(
            测试数据="String",
            操作步骤="1. 登录系统\n2. 选择 String 并点击确定按钮",
            预期结果="1. String 保存成功",
        )
        try:
            module.validate_discovery_state(state_path, branch_workbook_data)
        except AssertionError as exc:
            if "分支值“Number”未写入关联场景映射的功能用例" not in str(exc):
                fail("逐项用例覆盖门禁没有识别未进入用例的分支值")
        else:
            fail("逐项用例覆盖门禁错误地放行了用例分支遗漏")

        branch_workbook_data["case_rows_by_id"]["TC-001"].update(
            测试数据="String、Number",
            操作步骤="1. 登录系统\n2. 分别选择类型后点击取消按钮",
            预期结果="1. String、Number 均可选择",
        )
        try:
            module.validate_discovery_state(state_path, branch_workbook_data)
        except AssertionError as exc:
            if "缺少逐值的保存、提交或等价持久化结果验证" not in str(exc):
                fail("逐项用例覆盖门禁没有识别只选择后取消的伪覆盖")
        else:
            fail("逐项用例覆盖门禁错误地放行了未提交分支")

        filter_state = deepcopy(branch_state)
        filter_state["targets"][0].update(
            page="列表页面",
            element="状态筛选下拉框",
            action="选择状态并观察列表刷新",
            result="列表按所选状态筛选",
            terminal_action="选择后浮层收起并刷新列表",
            recovery="切换回全部状态",
            discovered_values=["全部", "启用"],
        )
        filter_workbook_data = deepcopy(branch_workbook_data)
        filter_workbook_data["coverage_rows"] = [
            {"页面/入口": "列表页面", "元素名称/文案": "状态筛选下拉框", "发现方式": "浏览器实探"}
        ]
        filter_workbook_data["scenario_rows_by_id"]["SCN-001"].update(
            **{
                "测试对象/页面元素": "状态筛选下拉框",
                "输入数据/状态条件": "分别选择 全部、启用",
                "观察点": "列表按 全部、启用 分别刷新",
            }
        )
        filter_workbook_data["case_rows_by_id"]["TC-001"].update(
            测试数据="全部、启用",
            操作步骤="1. 分别选择 全部、启用",
            预期结果="1. 选择 全部 后列表刷新并显示全部状态数据\n2. 选择 启用 后列表刷新并筛选启用状态数据",
        )
        state_path.write_text(json.dumps(filter_state, ensure_ascii=False), encoding="utf-8")
        module.validate_discovery_state(state_path, filter_workbook_data)


def validate_delivery_workflow_guards(root: Path) -> None:
    tool_path = root / "scripts" / "test_design_excel_tools.py"
    spec = importlib.util.spec_from_file_location("test_design_excel_tools", tool_path)
    if spec is None or spec.loader is None:
        fail("无法加载交付工具进行单次交付防重试自检")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.canonical_module_parts("应用服务-智能体编排-记忆资产管理") != [
        "应用服务", "智能体编排", "记忆资产管理"
    ]:
        fail("一级菜单-二级菜单-目标页面未稳定解析为三级模块路径")

    with tempfile.TemporaryDirectory() as temporary_dir:
        temporary = Path(temporary_dir)
        discovery_state = temporary / "discovery-state.json"
        module.initialize_discovery_state("记忆资产管理", discovery_state)
        state = json.loads(discovery_state.read_text(encoding="utf-8"))
        if state.get("targets") != [] or state.get("closure_rescan_new_targets") is not None:
            fail("深探状态初始化未生成空动态队列或待复扫状态")
        try:
            module.initialize_discovery_state("记忆资产管理", discovery_state)
        except ValueError:
            pass
        else:
            fail("深探状态文件可被重复初始化，可能覆盖已记录证据")

        project_root = temporary / "project"
        template_dir = project_root / "docs" / "test-design"
        template_dir.mkdir(parents=True)
        (template_dir / "codebuddy-test-design-template.xlsx").write_bytes(b"formal-template")
        formal_workbook = temporary / "draft.xlsx"
        import_template = temporary / "import-template.xlsx"
        formal_workbook.write_bytes(b"draft")
        import_template.write_bytes(b"template")
        original_rebuild = module.rebuild_formal_workbook_from_template
        original_generate = module.generate_import_workbook
        original_runner = module.run_python_script

        def fake_rebuild(_source, _template, output):
            output.write_bytes(b"validated-formal")

        def fake_generate(_formal, _template, output, _module_path, _product_name=None):
            output.write_bytes(b"validated-import")

        module.rebuild_formal_workbook_from_template = fake_rebuild
        module.generate_import_workbook = fake_generate
        module.run_python_script = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("集中校验失败"))
        try:
            for _ in range(2):
                try:
                    module.complete_deliverables(
                        project_root,
                        formal_workbook,
                        import_template,
                        "应用服务-智能体编排-记忆资产管理",
                    )
                except AssertionError:
                    pass
                else:
                    fail("交付集中校验回归样本错误地通过")
            try:
                module.complete_deliverables(
                    project_root,
                    formal_workbook,
                    import_template,
                    "应用服务-智能体编排-记忆资产管理",
                )
            except ValueError:
                pass
            else:
                fail("两次集中校验失败后仍允许继续尝试，可能形成重试风暴")
        finally:
            module.rebuild_formal_workbook_from_template = original_rebuild
            module.generate_import_workbook = original_generate
            module.run_python_script = original_runner

        success_draft = temporary / "success-draft.xlsx"
        success_draft.write_bytes(b"draft-before-delivery")
        module.rebuild_formal_workbook_from_template = fake_rebuild
        module.generate_import_workbook = fake_generate
        module.run_python_script = lambda *_args, **_kwargs: None
        try:
            module.complete_deliverables(
                project_root,
                success_draft,
                import_template,
                "应用服务-智能体编排-记忆资产管理",
            )
        finally:
            module.rebuild_formal_workbook_from_template = original_rebuild
            module.generate_import_workbook = original_generate
            module.run_python_script = original_runner
        _, formal_name, import_name = module.deliverable_names(
            "应用服务-智能体编排-记忆资产管理"
        )
        if (project_root / "deliverables" / formal_name).read_bytes() != b"validated-formal":
            fail("集中校验通过后没有原子交付测试设计")
        if (project_root / "deliverables" / import_name).read_bytes() != b"validated-import":
            fail("集中校验通过后没有原子交付导入文件")
        try:
            module.complete_deliverables(
                project_root,
                success_draft,
                import_template,
                "应用服务-智能体编排-记忆资产管理",
            )
        except ValueError:
            pass
        else:
            fail("成功交付后仍允许第二次正式交付")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    for relative in REQUIRED_FILES:
        path = root / relative
        if not path.exists():
            fail(f"缺少项目文件: {relative}")

    formal_template = root / "docs" / "test-design" / "codebuddy-test-design-template.xlsx"
    import_template = root / "docs" / "test-design" / "测试用例模板.xlsx"
    for template in [formal_template, import_template]:
        if not template.exists():
            fail(f"缺少 Excel 模板: {template}")
        validate_no_excel_tables(template)

    if workbook_sheets(formal_template) != EXPECTED_SHEETS:
        fail("正式测试设计模板必须且只能包含 7 个标准 Sheet")
    if first_row_values(import_template) != IMPORT_HEADERS:
        fail("测试用例模板.xlsx 表头发生变化")
    validate_import_left_alignment(import_template)

    rule_a = (root / ".codebuddy" / ".rules" / "test-design-rule.mdc").read_text(encoding="utf-8")
    rule_b = (root / ".codebuddy" / "rules" / "test-design-rule.md").read_text(encoding="utf-8")
    if rule_a != rule_b:
        fail("两份 CodeBuddy Rule 镜像内容不一致")
    for relative in [
        "AGENTS.md",
        "CODEBUDDY.md",
        ".codebuddy/skills/test-design/SKILL.md",
        ".codebuddy/.rules/test-design-rule.mdc",
        ".codebuddy/rules/test-design-rule.md",
    ]:
        size = (root / relative).stat().st_size
        if size >= 10_000:
            fail(f"轻入口必须小于 10000 字节：{relative} 当前为 {size} 字节")
    assert_contains(
        root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        ["任务入口只读取本 Rule、Skill", "非敏捷或未声明敏捷时不设固定数量", "不新增字段"],
    )
    assert_not_contains(
        root / ".codebuddy" / ".rules" / "test-design-rule.mdc",
        ["所有任务：", "`选项取值/输入值` 与 `联动/依赖变化`"],
    )
    assert_not_contains(root / "README_IMPORT.md", ["所有任务都读取测试系统导入规则"])
    assert_not_contains(root / "README.md", ["--formal-workbook deliverables/"])
    assert_not_contains(root / "README_IMPORT.md", ["--formal-workbook deliverables/"])
    for relative in [
        "AGENTS.md",
        "CODEBUDDY.md",
        "README.md",
        "README_IMPORT.md",
        ".codebuddy/skills/test-design/SKILL.md",
        ".codebuddy/.rules/test-design-rule.mdc",
        "docs/ARCHITECTURE.md",
        "docs/test-design/excel-template-spec.md",
        "docs/test-design/rules/excel-deliverable.md",
        "docs/test-design/rules/import-template.md",
    ]:
        assert_not_contains(root / relative, ["preflight-deliverables"])

    tool = root / "scripts" / "test_design_excel_tools.py"
    assert_contains(
        tool,
        [
            "adjust_row_height",
            "rebuild_formal_workbook_from_template",
            "atomic_copy_workbook",
            "init-discovery",
            "complete-deliverables",
            "delivery_attempts",
            "complete_attempted",
            "generate-import",
            'project_root / "deliverables"',
        ],
    )
    if 'project_root / "docs" / "test-design" / "deliverables"' in tool.read_text(encoding="utf-8"):
        fail("统一交付工具仍包含旧的嵌套交付默认路径")
    assert_not_contains(
        tool,
        ["preflight-deliverables", "preflight_attempts", "prepared_workbook_paths", "finalize-deliverables"],
    )
    assert_contains(
        root / "AGENTS.md",
        [
            "每次正式交付",
            "不询问是否需要生成",
            "complete-deliverables",
            "唯一结构与样式基线",
            "手工降级生成",
            "正式测试设计默认使用中文",
            "集中执行一次中文自查",
            "不得把自动翻译写入 Excel 工具",
            "测试设计执行与交付流程不涉及 Git",
            "触发元素 → 状态出现 → 状态内操作 → 终态动作 → 页面/数据结果",
            "相似用例告警必须在交付前分类",
            "不得询问用户是否需要深探",
            "待确认理解问题非空时",
            "docs/test-design/rules/pagination.md",
            "深探事实必须先按测试对象、角色/状态、动作/输入、数据、观察点和恢复路径",
            "任务入口只读取并遵守",
            "写入或交付 Excel",
        ],
    )
    assert_contains(
        root / "docs" / "test-design" / "rules" / "page-discovery.md",
        [
            "待确认` 只能用于需求含义、业务规则、范围边界、角色职责或预期结果",
            "把问题写入风险表不等于用户已经确认",
            "追加读取 `pagination.md`",
            "供深探准出后的原子场景形成步骤逐项处理",
            "完成原子化后再为每个场景标记一个主",
            "branch_policy=用例逐项覆盖",
            "init-discovery",
            "已发现仅表示元素或选项存在",
            "筛选、分页和只读切换验证刷新或状态结果",
        ],
    )
    assert_contains(
        root / "docs" / "test-design" / "rules" / "pagination.md",
        [
            "分页区域不能只登记为一个笼统的“分页组件”",
            "数据不足只影响实探证据等级，不减少分页场景和用例",
            "每个实际可选页容量",
            "branch_policy=逐项验证",
            "末页页码”仍属于页码选择",
            "分页专项准出必须同时满足",
        ],
    )
    assert_contains(
        root / "docs" / "test-design" / "rules" / "case-design.md",
        [
            "分页场景按 `pagination.md` 的实际能力和状态转换设计",
            "数据不足只影响实探证据等级",
            "未完成事实核账不得开始 DFX",
            "DFX 只能标记或补充",
            "是否生成用例=是",
            "已认证后的一级菜单-二级菜单-目标页面",
            "未登录、无权限、断网或超时",
            "不得虚构“关闭弹窗”",
            "写入前集中核对",
            "禁止连续创建多个 `fix_*` 脚本",
            "数据变更流程覆盖",
            "只展开、选择后取消或关闭只能算交互覆盖",
            "需求用户故事拆解",
            "独立说明业务价值并独立验收",
            "以最小明确需求项作为默认 Story 边界",
            "页面元素、测试点或 DFX 再拆分或合并",
            "非敏捷或未声明敏捷时不设置固定用例数量",
        ],
    )
    assert_contains(
        root / "docs" / "test-design" / "rules" / "dfx-test-strategy.md",
        [
            "先按测试对象、角色/状态、动作/输入、数据、观察点和恢复路径形成并核账原子场景",
            "不得用维度归类、代表性抽样或去重删减",
        ],
    )
    assert_contains(
        root / "scripts" / "validate-test-design-deliverable.py",
        [
            "--discovery-state",
            "validate_discovery_state",
            "validate_atomic_scenario_rows",
            "warn_case_merge_candidates",
            "validate_evidence_status_consistency",
            "待实探风险未关闭",
            "风险状态缺失或非法",
            "DISCOVERY_SOURCE_ALLOWED_VALUES",
            "is_page_discovery_source",
            "ui_symbol_style_issues",
            "NAVIGATION_ACTION_PATTERN",
            "UNRESOLVED_COVERAGE_NOTE_PATTERN",
            "FINDING_DISPLAY_LIMIT",
            "collect_validation_issue",
            "validate_delivery_bundle",
            "测试设计、深探映射和导入文件联合质检未通过",
            "assert_formal_template_invariants",
            "validate_chinese_delivery_language",
            "CHINESE_DELIVERY_FIELDS",
            "TRANSIENT_ACTION_PATTERNS",
            "DROPDOWN_SELECTION_PATTERN",
            "validate_function_case_preflight",
            "未登录场景不得机械追加登录步骤",
            "has_session_setup_step",
            "普通功能用例不得重复打开浏览器",
            "assert_expected_result_consistency",
            "FORMAL_ALLOWED_VALUES",
            "用例逐项覆盖",
            "target_requires_persistent_commit",
            "validate_story_rows",
            "需求用户故事拆解检查未通过",
            "validate_story_traceability",
            "Story 到场景和用例追踪检查未通过",
            "scenario_count",
            '"--formal-template"',
        ],
    )
    validate_discovery_gate(root)
    validate_delivery_workflow_guards(root)
    assert_contains(
        root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        [
            "阶段规则加载",
            "进入首次深探或定向补探前重新读取 `page-discovery.md`",
            "在操作分页控件前读取一次 `pagination.md`",
            "不记录没有执行约束力的“已加载”标记",
            "阶段切换不新增用户确认、中间文件、生成轮次或自动重试",
            "工具硬限制最多两次校验尝试",
            "先按来源或独立业务价值与验收结果拆解并冻结 Story",
            "不得通过 `pip install`、`pip uninstall`",
            "正常流程在 `complete-deliverables` 成功后不重复校验",
            "每次任务入口只读取",
            "写入或交付 Excel",
        ],
    )
    assert_contains(
        root / "docs" / "test-design" / "rules" / "excel-deliverable.md",
        [
            "不得给 `Worksheet.max_row` 等只读属性赋值",
            "以命令输出的正式测试设计和导入文件实际路径为准",
            "最多两次校验尝试",
            "一次成功交付",
            "不重复执行同一份最终交付校验",
        ],
    )
    assert_contains(
        root / "docs" / "test-design" / "excel-template-spec.md",
        [
            "自动调整行高",
            "水平左对齐",
            "deliverables/",
            "功能点` 作为父场景",
            "每个场景行只允许一个主 `DFX维度`",
            "待实探：",
            "已覆盖` 不得与上述未解决前缀并存",
            "一级菜单-二级菜单-目标页面",
            "说明性字段默认使用中文",
            "每行表示一个具备独立业务价值和独立验收结果的业务闭环",
            "页面、字段、选项、测试数据、正常/异常、边界、权限状态和 DFX 维度只展开测试场景",
            "是否生成用例` 只能填写 `是` 或 `否",
            "是否适合自动化` 只能填写 `是`、`否` 或 `待评估",
            "不得虚构模板中不存在的列",
            "本文件只定义 Excel 字段、枚举、映射和样式契约",
        ],
    )
    assert_not_contains(
        root / "docs" / "test-design" / "excel-template-spec.md",
        ["`选项取值/输入值` 用于", "可以执行不可逆、高风险"],
    )
    assert_contains(
        root / "docs" / "test-design" / "rules" / "data-safety.md",
        ["测试标识只证明数据归属，不自动授权不可逆操作"],
    )
    assert_contains(
        root / "scripts" / "validate-generated-python-scripts.py",
        ["SMART_QUOTE_HINT_CHARS", "Generated intermediate validation found", "validate_compile", "validate_utf8"],
    )
    assert_contains(
        root / "scripts" / "validate-generated-python-scripts.ps1",
        ["codex-primary-runtime", "do not install or uninstall global dependencies"],
    )

    print("OK: test design templates and lightweight project structure are aligned.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
