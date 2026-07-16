# 产物契约

## 最小运行目录

新运行目录固定为 `docs/test-design/current/<run-id>/`；已存在 `events.jsonl` 或 `facts.json` 的历史运行可以原地恢复。

```text
run-dir/
├─ events.jsonl
├─ facts.json
├─ case-plan.json
├─ function-cases.json
├─ review.json
└─ deliverables/
   ├─ 正式测试设计.xlsx
   └─ 测试系统导入.xlsx
```

`diagnostics/` 只在工具自然产生 trace、用户明确要求或高风险排查时创建；后续阶段不得读取它。

## events.jsonl 与 facts.json

事件类型只有 `scope`、`page`、`function`、`element`、`transaction`、`test_object`、`open_item`。新事实只需提交 `kind` 和 `data`，运行时自动生成 `fact_id`；同批事件可声明 `local_ref`，并在后续字段中用 `@local_ref` 建立关系。跨批次使用调用方稳定生成的 `client_ref`，后续既可用相同 `client_ref` 合并更新，也可用 `@client_ref` 建立关系。`client_ref` 只做精确匹配，不做名称或文案模糊去重。更新既有事实也可使用运行时已返回的 `fact_id`。同一 `fact_id` 的最后有效事件形成当前事实。

页面事实必须记录实际观察到的 `menu_path` 数组和页面名称。元素类型统一为 `input/select/trigger/toggle/container`；运行时兼容常见页面模型别名、对象或字符串形式的输入类与选项，并从非空 `options` 推断有限选项。未知交互类型或未说明动态来源的空选择控件会被标记为登记不完整。输入元素可声明 `valid_input_classes`；运行时据此追加精简的 `exploration_requirements`，它是交互前清单，不是新的产物或义务队列。每个事务检查点必须记录 `result`、结构化 `result_anchor`、主验证 `element_ref` 和全部 `used_element_refs`；输入检查同时记录 `input_class`，具体动作可用 `action_tokens` 固化。稳定断言优先使用 `result_anchor.stable_tokens`，实际样本值可保留在事实中但不得误作稳定预期。已完成检查立即写入，尚未执行的既定分支在checkpoint汇总；缺少可观察锚点、声明但未使用的控件或触发动作时事务不写入。用例导航由页面事实生成。进程中断时只自动丢弃无法解析的最后一个未完整行，中间行损坏仍立即报错。

一个有限选项功能事务示例（仅说明契约，不代表预置功能）：

```json
{
  "kind": "transaction",
  "local_ref": "filter_transaction",
  "data": {
    "function_ref": "FN-FILTER",
    "element_refs": ["EL-SEVERITY"],
    "transaction_type": "selection",
    "checks": [
      {"element_ref": "EL-SEVERITY", "used_element_refs": ["EL-SEVERITY"], "action": "选择严重", "option_value": "严重", "result": "列表只显示严重级别告警", "result_anchor": {"assertion": "all_equal", "target": "告警列表", "field": "告警级别", "value": "严重"}},
      {"element_ref": "EL-SEVERITY", "used_element_refs": ["EL-SEVERITY"], "action": "选择警告", "option_value": "警告", "result": "列表只显示警告级别告警", "result_anchor": {"assertion": "all_equal", "value": "警告"}}
    ],
    "recovery_result": "恢复全部级别"
  }
}
```

`facts.json` 只有七类业务事实和一个非业务 `checkpoint` 摘要。正常记录事务不重放全部事件；页面结束或显式checkpoint时编译一次。事实按首次发现顺序输出，后续更新在原位置生效；scope更新合并字段并保留run_id和created_at。恢复发现facts落后于events时自动重建。运行时不按 `transaction_type` 内置专用 Case 模板；关系使用内部引用，对外用例不得出现。

## case-plan.json

```json
{
  "schema_version": "2.0",
  "source": "facts.json",
  "functions": [{
    "function_ref": "FN-FILTER",
    "name": "告警级别筛选",
    "design_context": {
      "user_goal": "按级别查看目标告警",
      "role": "具备告警查看权限的用户",
      "business_value": "快速聚焦需要处理的告警",
      "acceptance_criteria": ["各级别选项分别得到对应列表结果"],
      "business_rules": ["有限选项分别验证"],
      "dependencies": ["存在各级别受控告警数据"],
      "postcondition": "列表保持在当前选择的级别",
      "basis": ["页面实探"]
    },
    "automation_profile": {"level": "UI", "dependency": "受控告警数据", "stability_risk": "列表异步刷新", "recommendation": "项目现有UI框架"},
    "cases": [{
      "case_id": "TC-FILTER-001",
      "page_ref": "PAGE-ALARM-LIST",
      "title": "严重级别筛选",
      "strategy": "baseline",
      "dfx_dimension": "DFT功能",
      "dfx_scenario": "正向流程"
    }, {
      "case_id": "TC-FILTER-002",
      "page_ref": "PAGE-ALARM-LIST",
      "title": "警告级别筛选",
      "strategy": "baseline",
      "dfx_dimension": "DFT功能",
      "dfx_scenario": "正向流程"
    }]
  }],
  "performance_scenarios": [],
  "performance_not_applicable_reason": "该筛选没有可单独定义的性能指标",
  "performance_basis_refs": ["FN-FILTER"],
  "risks": [],
  "risk_not_applicable_reason": "实探未发现需单独登记的风险",
  "risk_basis_refs": ["FN-FILTER"],
  "check_assignments": [
    {"transaction_ref": "TX-FILTER", "check_index": 1, "disposition": "case", "case_id": "TC-FILTER-001"},
    {"transaction_ref": "TX-FILTER", "check_index": 2, "disposition": "case", "case_id": "TC-FILTER-002"}
  ]
}
```

计划只写测试意图和唯一检查点分配账本；`fact_refs`、元素覆盖、功能覆盖和DFX关联由系统派生，不要求模型重复维护。每个有限选项和每个实测有效输入等价类分别对应独立 baseline Case；空值、无效格式、边界等已声明且实测的分支分别对应独立 DFX Case。不合并到一个 Case，也不默认做跨维度组合。性能或风险不适用时模型只提交真实原因，运行时自动补充并校验有效功能事实引用。
计划通过内部 `write-plan` 按功能 upsert；结构或映射错误在本次生成动作中局部修正，不把错误计划留给最终 Review。精确重复提交返回成功且不改写文件。

## function-cases.json

```json
{
  "schema_version": "2.0",
  "source_plan": "case-plan.json",
  "cases": [{
    "case_id": "TC-FILTER-001",
    "function_ref": "FN-FILTER",
    "title": "告警级别筛选-严重级别筛选",
    "preconditions": ["告警列表存在严重级别的可查看数据"],
    "test_data": "告警级别：严重",
    "automation_value": "高频筛选回归",
    "automation_priority": "P1",
    "steps": [
      {"action": "进入告警管理-告警列表", "expected": "显示告警查询区和告警列表"},
      {"action": "在告警级别中选择严重", "expected": "告警列表刷新，所有记录的告警级别均为严重", "source_check": {"transaction_ref": "TX-FILTER", "check_index": 1}}
    ],
    "fact_refs": ["FN-FILTER", "EL-SEVERITY", "TX-FILTER"]
  }]
}
```

模型提交步骤时只写 `action+expected`；写入器按 `check_assignments` 顺序自动注入一个内部 `source_check`，并从主验证和辅助使用控件派生 `fact_refs`。结构化 `result_anchor` 只校验明确的 `tokens` 或结果 `value`，允许目标、字段和观察原句使用更完整的等价表述。内部来源不导出Excel。
用例通过内部 `write-cases` 按功能 upsert；标题、菜单路径、配对步骤、具体数据、功能顺序和事实引用在生成时完成约束。同一功能再次提交时只替换该功能块，其他功能保持不变。

## review.json

模型只读取当前三份结构化产物，按功能提取设计上下文、计划意图、标题、数据、步骤/预期、DFX、专项结论和自动化字段形成内存中的紧凑投影，不生成Review中间文件。一次语义审计负载示例：

```json
{
  "reviewed_case_ids": ["TC-FILTER-001", "TC-FILTER-002"],
  "summary": "逐条复核当前用例，未发现需要局部修正的语义问题。",
  "issues": [],
  "local_fixes": []
}
```

运行时重新计算 discovery、plan、cases 和跨产物确定性检查，再与该语义判断合并写入 `review.json`；模型不能用自报通过字段覆盖确定性结果。只有两部分同时通过才可交付；发现页面事实确实缺失时明确标记阻塞，其他问题只指出单个功能或Case的局部修正，不自动循环。
