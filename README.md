# 页面深探与测试用例生成工具包

本项目帮助模型从真实页面行为出发，生成可执行、可追溯、可导入测试系统的测试用例。核心目标不是“多写用例”，而是先把页面功能和配置效果实探清楚，再将事实稳定地转换成测试计划和交付件。

## 最终架构

整个流程在一个会话和一个浏览器上下文中连续执行：

```text
页面扫描 + 事务操作
        ↓
events.jsonl（原始事实）
        ↓ 自动编译
facts.json（紧凑事实视图）
        ↓ 独立功能 + DFX 左移
case-plan.json（稳定用例意图）
        ↓
function-cases.json（可执行用例）
        ↓ 双向审查
review.json
        ↓
正式 8 Sheet Excel + 独立测试系统导入 Excel
```

没有逐点击任务队列、后台页面记录 Hook、多份发现 CSV、用例分片 manifest 或自动返工状态机。页面工具瞬时失败最多重试一次；页面真实返回直接记为事实；无法安全完成的项目统一进入最终缺口清单。

## 页面深探如何运行

- 首次进入页面、弹出结构复杂对话框、切换到新页面、完成事务时做全量扫描。
- 每次操作后只扫描受影响区域，并把新出现的控件加入当前事务或后续事务。
- 控件类型不是固定白名单；DOM、可访问性树和可见页面状态共同提供发现线索。
- 下拉框等有限集合必须逐项选择并观察真实变化，不能只展开。
- CRUD 必须真实创建、查询、编辑、验证生效、删除/恢复。
- 配置项只做单因素：默认值与每个可选配置分别提交，验证重开回显和实际效果；暂不做组合覆盖。
- 只有页面外部且模型仍无法理解的业务语义才询问用户。

一次分页事务可以连续完成“记录初始状态 → 选择各个实际存在的条数选项 → 观察列表和页数 → 操作页面实际存在的翻页控件 → 验证边界状态 → 恢复”。它是一条连续观察，不会机械展开为七个执行分支；最终形成几个用例由独立功能与 DFX 场景决定。

## 阶段隔离

| 阶段 | 只读输入 | 唯一写入 |
| --- | --- | --- |
| discovery | 页面、需求、产品资料 | `events.jsonl`、`facts.json`、`evidence/` |
| plan | `facts.json`、规则 | `case-plan.json` |
| cases | facts、plan | `function-cases.json` |
| review/delivery | facts、plan、cases | `review.json`、`deliverables/` |

阶段边界执行一次完整性检查。发现问题只修复受影响事实、计划项或用例，不从头重跑。

## 目录结构

```text
<run-dir>/
├─ scope.json
├─ artifacts/discovery/
│  ├─ events.jsonl
│  ├─ facts.json
│  └─ evidence/
├─ case-plan.json
├─ function-cases.json
├─ review.json
└─ deliverables/
   ├─ <模块>-测试设计.xlsx
   ├─ <模块>-测试系统导入.xlsx
   └─ delivery-receipt.json
```

## 快速开始

```powershell
scripts/run-test-design.ps1 init-run `
  --run-dir docs/test-assets/batch-runs/run-001 `
  --module-path "大数据平台>告警列表"

scripts/run-test-design.ps1 record-observation `
  --run-dir docs/test-assets/batch-runs/run-001 `
  --file observation.json

scripts/run-test-design.ps1 pipeline-status --run-dir docs/test-assets/batch-runs/run-001
scripts/run-test-design.ps1 validate-stage --run-dir docs/test-assets/batch-runs/run-001 --stage discovery
scripts/run-test-design.ps1 review-run --run-dir docs/test-assets/batch-runs/run-001
scripts/run-test-design.ps1 complete-deliverables --run-dir docs/test-assets/batch-runs/run-001 --project-root .
```

`record-observation` 接受单个事件或事件数组。它在一个有意义的事务观察点调用，不应包裹每次点击。

## Skill、规则与命令

- 执行 Skill：`.codebuddy/skills/test-design/SKILL.md`
- 硬规则：`.codebuddy/rules/test-design-rule.md`
- 专题规则索引：`docs/test-design/rules/README.md`
- 统一入口：`scripts/run-test-design.ps1`

快速和完整自检：

```powershell
scripts/validate-test-design.ps1 -Mode Fast
scripts/validate-test-design.ps1 -Mode Full
```
