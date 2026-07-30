# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import zipfile
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
    "自动化建议",
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


def validate_discovery_gate(root: Path) -> None:
    validator_path = root / "scripts" / "validate-test-design-deliverable.py"
    spec = importlib.util.spec_from_file_location("test_design_deliverable_validator", validator_path)
    if spec is None or spec.loader is None:
        fail("无法加载交付校验器进行深探门禁自检")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    valid_story = {
        "Story ID/需求 ID": "STORY-001",
        "用户故事/需求描述": "管理业务对象",
        "角色": "租户管理员、系统管理员",
        "业务价值": "完成对象管理",
        "验收标准": "可按权限完成管理",
    }
    module.validate_story_role_rows([valid_story])
    invalid_stories = [
        dict(valid_story, 角色="租户管理员、租户管理员"),
        dict(valid_story, 角色="未登录用户,系统管理员"),
    ]
    try:
        module.validate_story_role_rows(invalid_stories)
    except AssertionError as exc:
        message = str(exc)
        for marker in ["Story ID/需求 ID 重复", "多角色只能使用中文顿号", "包含重复角色", "把测试状态写入业务角色", "除角色外内容一致"]:
            if marker not in message:
                fail(f"用户故事角色门禁没有一次汇总必要问题：{marker}")
    else:
        fail("用户故事角色门禁错误地放行了不稳定角色结构")
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
                "测试对象/页面元素": "创建按钮",
                "输入数据/状态条件": "正常数据",
                "观察点": "创建状态出现",
            }
        },
        "case_rows_by_id": {
            "TC-001": {
                "用例 ID": "TC-001",
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

        valid_pagination_state = json.loads(json.dumps(valid_state, ensure_ascii=False))
        valid_pagination_state["targets"][0].update(element="下一页", control_type="分页按钮")
        state_path.write_text(json.dumps(valid_pagination_state, ensure_ascii=False), encoding="utf-8")
        module.validate_discovery_state(state_path)

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
            if "只有选择或取消覆盖，缺少提交及最终结果中的逐值验证" not in str(exc):
                fail("逐项用例覆盖门禁没有识别只选择后取消的伪覆盖")
        else:
            fail("逐项用例覆盖门禁错误地放行了未提交分支")


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
        fail("正式测试设计模板必须且只能包含 8 个标准 Sheet")
    if first_row_values(import_template) != IMPORT_HEADERS:
        fail("测试用例模板.xlsx 表头发生变化")
    validate_import_left_alignment(import_template)

    rule_a = (root / ".codebuddy" / ".rules" / "test-design-rule.mdc").read_text(encoding="utf-8")
    rule_b = (root / ".codebuddy" / "rules" / "test-design-rule.md").read_text(encoding="utf-8")
    if rule_a != rule_b:
        fail("两份 CodeBuddy Rule 镜像内容不一致")

    tool = root / "scripts" / "test_design_excel_tools.py"
    assert_contains(
        tool,
        [
            "adjust_row_height",
            "rebuild_formal_workbook_from_template",
            "atomic_copy_workbook",
            "complete-deliverables",
            "generate-import",
            'project_root / "deliverables"',
        ],
    )
    if 'project_root / "docs" / "test-design" / "deliverables"' in tool.read_text(encoding="utf-8"):
        fail("统一交付工具仍包含旧的嵌套交付默认路径")
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
        ],
    )
    assert_contains(
        root / "docs" / "test-design" / "rules" / "pagination.md",
        [
            "分页区域不能只登记为一个笼统的“分页组件”",
            "数据不足只影响实探证据等级，不减少分页场景和用例",
            "每个实际可选页容量",
            "branch_policy=逐项验证",
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
            "系统/项目入口 → 一级菜单-二级菜单-目标页面",
            "未登录、退出登录、无痕访问、无权限角色、断网或超时",
            "不得虚构“关闭弹窗”",
            "写入前集中核对",
            "禁止连续创建多个 `fix_*` 脚本",
            "数据变更流程覆盖",
            "只展开、选择后取消或关闭只能算交互覆盖",
            "用户故事角色归一化",
            "具备该功能权限的业务用户",
            "DFX 和用例设计不得反向追加角色",
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
            "ui_symbol_style_issues",
            "NAVIGATION_ACTION_PATTERN",
            "UNRESOLVED_COVERAGE_NOTE_PATTERN",
            "FINDING_DISPLAY_LIMIT",
            "assert_formal_template_invariants",
            "validate_chinese_delivery_language",
            "CHINESE_DELIVERY_FIELDS",
            "TRANSIENT_ACTION_PATTERNS",
            "DROPDOWN_SELECTION_PATTERN",
            "validate_function_case_preflight",
            "未登录场景不得机械追加登录步骤",
            "assert_expected_result_consistency",
            "FORMAL_ALLOWED_VALUES",
            "用例逐项覆盖",
            "只有选择或取消覆盖",
            "validate_story_role_rows",
            "需求用户故事角色归一化检查未通过",
            "scenario_count",
            '"--formal-template"',
        ],
    )
    validate_discovery_gate(root)
    assert_contains(
        root / ".codebuddy" / "skills" / "test-design" / "SKILL.md",
        [
            "阶段规则加载",
            "进入首次深探或定向补探前重新读取 `page-discovery.md`",
            "在操作分页控件前读取一次 `pagination.md`",
            "不记录没有执行约束力的“已加载”标记",
            "阶段切换不新增用户确认、中间文件、生成轮次或自动重试",
            "先按证据归一化并冻结用户故事业务角色",
        ],
    )
    assert_contains(
        root / "docs" / "test-design" / "rules" / "excel-deliverable.md",
        [
            "不得给 `Worksheet.max_row` 等只读属性赋值",
            "以命令输出的正式测试设计和导入文件实际路径为准",
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
            "业务目标、业务价值、流程、业务规则和验收标准相同",
            "禁止使用当前账号名",
            "是否生成用例` 只能填写 `是` 或 `否",
            "是否适合自动化` 只能填写 `是`、`否` 或 `待评估",
        ],
    )
    assert_contains(
        root / "scripts" / "validate-generated-python-scripts.py",
        ["SMART_QUOTE_HINT_CHARS", "Generated intermediate validation found", "validate_compile", "validate_utf8"],
    )

    print("OK: test design templates and lightweight project structure are aligned.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
