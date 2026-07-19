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

事件类型只有 `scope`、`page`、`function`、`element`、`transaction`、`test_object`、`open_item`。新事实只需提交 `kind` 和 `data`，运行时自动生成 `fact_id`；同批事件可声明 `local_ref`，并在后续字段中用 `@local_ref` 建立关系。跨批次使用调用方稳定生成的 `client_ref`，后续既可用相同 `client_ref` 合并更新，也可用 `@client_ref` 建立关系。`client_ref` 只做精确匹配，不做名称或文案模糊去重。更新既有事实也可使用运行时已返回的 `fact_id`。误放在 `data` 内的 `fact_id/status/client_ref/local_ref` 在新负载写入前自动提升到事件外层；内外值冲突则整批不落盘。同一 `fact_id` 的最后有效事件形成当前事实。

页面事实必须记录实际观察到的 `menu_path` 数组和页面名称。元素类型统一为 `input/select/trigger/toggle/container`；运行时兼容常见页面模型别名、对象或字符串形式的输入类与选项，并从非空 `options` 推断有限选项。未知交互类型或未说明动态来源的空选择控件会被标记为登记不完整。输入元素可声明 `valid_input_classes`，有明确结构化格式时声明 `input_format/input_formats`；运行时据此追加精简的 `exploration_requirements`，它是交互前清单，不是新的产物或义务队列。每个事务检查点必须记录 `result`、结构化 `result_anchor`、主交互 `element_ref` 和全部 `used_element_refs`；输入检查同时记录 `input_class`。相关控件共同决定本次结果时用可选 `branch_bindings` 记录 `{element_ref, kind, value}`；唯一输入类和唯一有限选项由运行时自动归一，多个同类参与控件才需显式绑定。普通辅助控件不进入绑定，也不能完成独立分支。具体动作可用 `action_tokens` 固化。稳定断言优先使用 `result_anchor.stable_tokens`，实际样本值可保留在事实中但不得误作稳定预期。已完成检查立即写入，尚未执行的既定分支在checkpoint汇总；缺少可观察锚点、声明但未使用的控件、复用同一物理关系的独立分支或不完整触发动作时事务不写入。最后一次事务后再提交一次稳定页面扫描并明确 `unhandled_element_refs: []`；相同扫描幂等吸收，新事务会使旧扫描失效。用例导航由页面事实生成。进程中断时只自动丢弃无法解析的最后一个未完整行，中间行损坏仍立即报错。

一个受约束关联检查示例（名称和值仅说明契约，不预置业务）：

```json
{
  "element_ref": "EL-TARGET",
  "used_element_refs": ["EL-TARGET", "EL-MODE", "EL-RUN"],
  "trigger_element_ref": "EL-RUN",
  "input_class": "valid_type_a",
  "option_value": "模式一",
  "branch_bindings": [
    {"element_ref": "EL-TARGET", "kind": "input_class", "value": "valid_type_a"},
    {"element_ref": "EL-MODE", "kind": "option_value", "value": "模式一"}
  ],
  "action": "输入受控类型A数据，选择模式一并点击执行",
  "result": "显示模式一对应的处理结果",
  "result_anchor": {"assertion": "contains", "stable_tokens": ["处理结果"]}
}
```

一个有限选项功能事务示例（仅说明契约，不代表预置功能）：

```json
{
  "kind": "transaction",
  "local_ref": "filter_transaction",
  "data": {
    "function_ref": "FN-FILTER",
    "element_refs": ["EL-OPTION"],
    "transaction_type": "selection",
    "checks": [
      {"element_ref": "EL-OPTION", "used_element_refs": ["EL-OPTION"], "action": "选择选项A", "option_value": "选项A", "result": "列表只显示选项A对应记录", "result_anchor": {"assertion": "all_equal", "target": "记录列表", "field": "状态", "value": "选项A"}},
      {"element_ref": "EL-OPTION", "used_element_refs": ["EL-OPTION"], "action": "选择选项B", "option_value": "选项B", "result": "列表只显示选项B对应记录", "result_anchor": {"assertion": "all_equal", "value": "选项B"}}
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
    "name": "有限选项筛选",
    "design_context": {
      "user_goal": "按选项查看对应记录",
      "role": "具备页面访问权限的用户",
      "business_value": "快速聚焦目标记录",
      "acceptance_criteria": ["各有限选项分别得到对应列表结果"],
      "business_rules": ["有限选项分别验证"],
      "dependencies": ["存在可区分各选项效果的受控数据"],
      "postcondition": "列表保持在当前选择的级别",
      "basis": ["页面实探"]
    },
    "automation_profile": {"level": "UI", "dependency": "受控页面数据", "stability_risk": "无已知稳定性风险", "recommendation": "项目现有UI框架"},
    "cases": [{
      "case_id": "TC-FILTER-001",
      "page_ref": "PAGE-LIST",
      "title": "选项A筛选",
      "verification_focus": "验证选择控件在选项A下只展示对应记录",
      "strategy": "baseline",
      "dfx_dimension": "DFT功能",
      "dfx_scenario": "正向流程"
    }, {
      "case_id": "TC-FILTER-002",
      "page_ref": "PAGE-LIST",
      "title": "选项B筛选",
      "verification_focus": "验证选择控件在选项B下只展示对应记录",
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

计划只写测试意图和唯一检查点分配账本；`fact_refs`、`scenario_signature`、Case级 `test_point`、元素覆盖、功能覆盖和DFX关联由系统派生，不要求模型重复维护。每个Case包含唯一 `verification_focus`；缺省时运行时根据同一实测检查的关联分支和观察结果派生事实化建议。每个实测有效关联分别对应独立 baseline Case；没有关联时每个有限选项和每个实测有效输入等价类仍分别对应独立 baseline Case。空值、无效格式、边界等已声明且实测的分支分别对应独立 DFX Case。一个关联Case只共同覆盖其明确绑定的分支，不默认做跨维度组合。页面自动化入口由运行时归一为UI；同类稳定性风险聚合为一个风险并保留受影响功能。按钮、提交或触发引用本身不是性能依据；只有实测加载、异步、长任务、超时、耗时或需求性能目标时才生成一条轻量响应场景，量化目标必须带 `target_basis` 和有效 `target_basis_refs`，不按Case重复。确无适用专项时保留真实不适用原因。
计划通过内部 `write-plan` 按功能 upsert；结构或映射错误在本次生成动作中局部修正，不把错误计划留给最终 Review。精确重复提交返回成功且不改写文件。

## function-cases.json

```json
{
  "schema_version": "2.0",
  "source_plan": "case-plan.json",
  "cases": [{
    "case_id": "TC-FILTER-001",
    "function_ref": "FN-FILTER",
    "title": "有限选项筛选-选项A筛选",
    "preconditions": ["列表存在可区分选项A效果的受控数据"],
    "test_data": "筛选值：选项A",
    "automation_value": "高频筛选回归",
    "automation_priority": "P1",
    "verification_focus": "验证选择控件在选项A下只展示对应记录",
    "steps": [
      {"action": "进入实际菜单路径对应的目标页面", "expected": "显示筛选区域和记录列表"},
      {"action": "在有限选项控件中选择选项A", "expected": "列表刷新，所有记录均符合选项A", "source_check": {"transaction_ref": "TX-FILTER", "check_index": 1}}
    ],
    "fact_refs": ["FN-FILTER", "EL-OPTION", "TX-FILTER"]
  }]
}
```

模型提交步骤时只写业务 `action+expected`；写入器根据计划统一补齐标题、Case级 `test_point`、测试类型和优先级，根据页面事实注入使用 `-` 连接的完整导航，再按 `check_assignments` 顺序注入一个内部 `source_check` 和计划中的 `verification_focus`，并从关联验证、主交互和辅助使用控件派生 `fact_refs`。结构化 `result_anchor` 优先校验稳定tokens；同时存在稳定tokens和带计数、百分比、耗时或进度的样本值时，样本值不得固化为预期。动作必须保留同一关联检查中的具体选项，预期不得借用其他关联的观察结果。命名测试数据引用采用 `TEST_*`，且前置条件必须说明同名受控来源。内部来源不导出Excel，Case级测试点和主验证目标用于场景矩阵、功能用例和一次性语义Review。
用例通过内部 `write-cases` 按功能 upsert；标题、菜单路径、配对步骤、具体数据、功能顺序和事实引用在生成时完成约束。同一功能再次提交时只替换该功能块，其他功能保持不变。

## review.json

模型只读取当前三份结构化产物，按功能提取设计上下文、计划意图、标题、数据、步骤/预期、DFX、专项结论和自动化字段形成内存中的紧凑投影，不生成Review中间文件。一次语义审计负载示例：

```json
{
  "reviewed_case_ids": ["TC-FILTER-001", "TC-FILTER-002"],
  "reviewed_sections": ["cases", "performance", "risks", "automation", "elements", "cross_sheet"],
  "summary": "逐条复核当前用例，未发现需要局部修正的语义问题。",
  "issues": [],
  "local_fixes": []
}
```

运行时重新计算 discovery、plan、cases 和跨产物确定性检查，再与该语义判断合并写入 `review.json`；模型不能用自报通过字段覆盖确定性结果。只有两部分同时通过才可交付；发现页面事实确实缺失时明确标记阻塞，其他问题只指出单个功能或Case的局部修正，不自动循环。
