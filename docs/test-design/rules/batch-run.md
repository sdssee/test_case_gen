# 批次执行与恢复规则

## 适用范围

- 单页面或单一最小功能不强制生成批次计划。
- 多页面、多模块、全产品或明显超过一个最小标题路径时，建立精简`batch-plan.md`记录范围、顺序、依赖和完成状态。
- 所有页面实探运行保留`batch-status.csv`、`page-discovery.csv`和`artifacts/`，用于轻量checkpoint和断点恢复。

## 运行目录

首次写入页面事实时由工具自动建立run-dir，不向用户展示独立初始化阶段。

默认内容：

```text
run-dir/
├─ batch-status.csv
├─ page-discovery.csv
├─ artifacts/
│  ├─ 可选诊断证据
│  └─ scripts/
│     ├─ test-cases-01-<功能块>.json
│     └─ ...
└─ final-review.md   # 仅最终Review时创建
```

大范围任务额外包含`batch-plan.md`。不预创建空白Review，不强制五件套。

## 多JSON分片

- 保留按最小标题路径、页面域或功能块拆分的多个JSON，避免单次写入过大。
- JSON/CSV/Markdown/TXT建议小于256KB；run-dir不生成任务专用Python。
- 每个分片写入时完成JSON语法、必填字段、当前分片ID、编号和具体内容的轻量自检；本功能的故事、场景、用例、性能、风险和自动化建议一次写齐。
- 禁止生成`fix_*.py`或其他一次性补丁脚本；问题只修改所属源分片，不扫描无关分片，不直接修Excel。

## batch-status职责

`batch-status.csv`只记录当前路径、执行状态、页面实探状态、JSON分片状态、功能用例数、性能场景数、双Excel路径和更新时间。它用于恢复，不作为逐元素义务或语义门禁。

## Review

- 页面事实和JSON分片在生产时进行轻量结构自检，不形成Review报告。
- 全部产物完成后只创建一次`final-review.md`，审计页面事实真实性、baseline/DFX覆盖、CRUD、性能、8个Sheet、双Excel和路径。
- Review发现问题时只报告并定位相关事实或源分片；需要修正时由当前会话局部修改后人工决定是否重新编译，不启动第二轮完整语义Review和自动返工循环。

## 交付

- 使用`compile-deliverables`一次完成分片解析、8-Sheet编译、导入生成、校验和可回滚发布。
- 正式Excel与导入Excel来自同一次分片汇总；不得手工在多个目录反复复制。
- 工具返回的实际路径是最终路径，批次状态、Review和用户提示不得自行拼接。
