# -*- coding: utf-8 -*-
from __future__ import annotations

import re


MAX_FUNCTION_CASES_PER_PART = 10
FUNCTION_CASE_PART_RE = re.compile(r"^function_cases_part_\d{3}\.json$")
FUNCTION_CASE_REQUIRED_FIELDS = [
    "用例 ID", "Story ID/需求 ID", "模块", "功能点", "用例标题", "优先级", "测试类型", "DFX维度", "DFX场景",
    "前置条件", "测试数据", "操作步骤", "预期结果", "实际结果", "执行状态", "是否适合自动化", "关联风险", "备注",
]
FUNCTION_CASE_FORBIDDEN_FIELDS = {
    "用例编号", "用侊 ID", "用侊标题", "场景类型", "正向/反向", "steps", "expected", "title", "case_id", "id",
    "expected_result", "expectedResults", "preconditions", "test_data", "test_steps",
    "actual_result", "execution_status", "feature", "function_point", "name",
}
ENGLISH_TEMPLATE_MARKERS = [
    "Open browser", "navigate to", "Verify page", "Operate element", "Execute extended scenario", "Extended scenario",
    "passes", "behaves as expected",
]
